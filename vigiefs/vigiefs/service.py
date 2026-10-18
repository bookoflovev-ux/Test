"""Fonctionnement 24 h/24, 7 j/7 : superviseur de processus, arrêt volontaire, journal de disponibilité.

Architecture :

    service Windows (WinSW / NSSM) · unité systemd · tâche planifiée
      └─ superviseur        python -m vigiefs supervise          relance, surveille, journalise
           └─ processus     python -m vigiefs serve --supervised  interface web + moteur

Le processus de travail écrit un battement de cœur toutes les 10 s. Le superviseur le relance s'il
s'arrête (plantage), s'il ne bat plus (blocage) ou si l'interface ne répond plus, avec un délai
progressif. VigieFS ne s'arrête que sur décision : commande « vigiefs stop », bouton de l'interface
ou arrêt du service par le système. Cet arrêt volontaire est journalisé et n'est jamais relancé.
"""

import json
import logging
import os
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

log = logging.getLogger("vigiefs.service")

BACKOFF = (1, 2, 5, 10, 30, 60)   # délais de relance successifs (s), le dernier se répète indéfiniment
STABLE_AFTER = 600                # un processus resté 10 min en service remet le délai à zéro
JOURNAL_MAX = 2 * 1024 * 1024
CONSOLE_MAX = 5 * 1024 * 1024

STATE, JOURNAL, HEARTBEAT, COMMAND, STOPPED, LOCK, CONSOLE = (
    "etat.json", "journal.jsonl", "battement.json", "commande.json", "arret-volontaire.json",
    "superviseur.lock", "console.log")

EVENT_LABEL = {
    "superviseur_demarre": "Démarrage du service",
    "superviseur_arrete": "Arrêt du service",
    "demarrage": "Lancement du moteur",
    "pret": "VigieFS opérationnel",
    "incident": "Incident",
    "relance": "Relance automatique",
    "redemarrage": "Redémarrage demandé",
    "arret": "Arrêt volontaire",
    "interruption": "Interruption non contrôlée",
    "reparation": "Auto-réparation",
    "maintenance": "Mode maintenance",
}
INCIDENT_LABEL = {
    "plantage": "arrêt inattendu du processus",
    "blocage": "processus bloqué (plus de battement)",
    "sans_reponse": "interface web sans réponse",
}


# ================================================================ fichiers de contrôle
def run_dir(data_dir):
    path = os.path.join(os.path.abspath(data_dir), "run")
    os.makedirs(path, exist_ok=True)
    return path


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    """Écriture atomique (fichier temporaire puis remplacement) ; tolère un lecteur concurrent sous Windows."""
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, default=str)
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    try:
        os.remove(tmp)
    except OSError:
        pass


def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def journal(rdir, event, **fields):
    """Ajoute une ligne au journal de disponibilité (partagé par le superviseur et le moteur)."""
    path = os.path.join(rdir, JOURNAL)
    try:
        if os.path.getsize(path) > JOURNAL_MAX:
            os.replace(path, path + ".1")
    except OSError:
        pass
    line = json.dumps({"ts": time.time(), "event": event, **fields}, ensure_ascii=False, default=str)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        log.warning("Journal de disponibilité inaccessible : %s", path)


def read_journal(rdir, since=0, limit=None):
    events = []
    for name in (JOURNAL + ".1", JOURNAL):
        try:
            with open(os.path.join(rdir, name), encoding="utf-8") as fh:
                for line in fh:
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("ts", 0) >= since:
                        events.append(ev)
        except OSError:
            continue
    events.sort(key=lambda e: e.get("ts", 0))
    return events[-limit:] if limit else events


def send_command(rdir, action, actor, reason=""):
    """Dépose une demande (stop / restart) que le superviseur traite dans la seconde."""
    write_json(os.path.join(rdir, COMMAND), {"action": action, "actor": actor, "reason": reason, "at": time.time()})


def take_command(rdir):
    path = os.path.join(rdir, COMMAND)
    cmd = read_json(path)
    if cmd is not None:
        remove(path)
    return cmd if isinstance(cmd, dict) and cmd.get("action") in ("stop", "restart") else None


def tail(path, size=4096):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - size))
            return fh.read().decode("utf-8", "replace").splitlines()[-25:]
    except OSError:
        return []


# ================================================================ instance unique
class InstanceLock:
    """Verrou exclusif sur un fichier : empêche deux superviseurs sur le même dossier de données."""

    def __init__(self, path):
        self.path = path
        self.fd = None

    def acquire(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self.fd = fd
        if os.name != "nt":
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
        return True

    def release(self):
        if self.fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(self.fd)
        self.fd = None


def supervisor_running(rdir):
    lock = InstanceLock(os.path.join(rdir, LOCK))
    if lock.acquire():
        lock.release()
        return False
    return True


# ================================================================ systemd
def sd_notify(message):
    """Protocole sd_notify (Type=notify, WatchdogSec) sans dépendance ; sans effet hors systemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr or os.name == "nt":
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(message.encode())
        return True
    except OSError:
        return False


def http_probe(url, timeout=15):
    """Sonde de vivacité de l'interface (certificat non vérifié : sonde locale uniquement)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:  # noqa: S310
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def probe_url(host, port, tls=False):
    host = {"0.0.0.0": "127.0.0.1", "::": "::1", "": "127.0.0.1"}.get(host, host)
    if ":" in host:
        host = f"[{host}]"
    return f"{'https' if tls else 'http'}://{host}:{port}/api/health/live"


# ================================================================ superviseur
class Supervisor:
    """Lance le processus de travail et le maintient en service jusqu'à un arrêt volontaire."""

    def __init__(self, cmd, data_dir, probe=None, mode="console", hang_timeout=300, startup_grace=180,
                 probe_interval=30, probe_failures=4, stop_timeout=45, poll=1.0, backoff=BACKOFF,
                 stable_after=STABLE_AFTER, env=None, cwd=None):
        self.cmd = list(cmd)
        self.data_dir = os.path.abspath(data_dir)
        self.rdir = run_dir(self.data_dir)
        self.probe = probe                       # url (str) ou fonction () -> bool
        self.mode = mode
        self.hang_timeout = hang_timeout
        self.startup_grace = startup_grace
        self.probe_interval = probe_interval
        self.probe_failures = probe_failures
        self.stop_timeout = stop_timeout
        self.poll = poll
        self.backoff = tuple(backoff) or (1,)
        self.stable_after = stable_after
        self.env = env
        self.cwd = cwd
        self.lock = InstanceLock(os.path.join(self.rdir, LOCK))
        self._wake = threading.Event()
        self._stop = None                        # {"actor", "reason"} une fois l'arrêt décidé
        self._last_state_write = 0
        self._down_since = None
        self.state = {}

    # --- signaux du gestionnaire de services (Ctrl+C, SIGTERM, arrêt du service Windows) ---
    def install_signal_handlers(self):
        def handler(signum, _frame):
            name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
            self.request_stop("gestionnaire de services", f"signal {name} (arrêt du service ou du système)")
        for sig in ("SIGINT", "SIGTERM", "SIGBREAK", "SIGHUP"):
            if hasattr(signal, sig):
                try:
                    signal.signal(getattr(signal, sig), handler)
                except (OSError, ValueError):
                    pass

    def request_stop(self, actor, reason=""):
        if not self._stop:
            self._stop = {"actor": actor, "reason": reason}
        self._wake.set()

    # --- état publié pour « vigiefs status » et l'interface ---
    def _save_state(self, force=False, **values):
        self.state.update(values)
        now = time.monotonic()
        if force or now - self._last_state_write >= 5:
            self._last_state_write = now
            self.state["updated_at"] = time.time()
            write_json(os.path.join(self.rdir, STATE), self.state)

    def _spawn(self, last_incident=None):
        console = os.path.join(self.rdir, CONSOLE)
        try:
            if os.path.getsize(console) > CONSOLE_MAX:
                os.replace(console, console + ".1")
        except OSError:
            pass
        env = dict(self.env if self.env is not None else os.environ)
        env["VIGIEFS_SUPERVISED"] = "1"
        env["VIGIEFS_SERVICE_MODE"] = self.mode
        env["PYTHONUNBUFFERED"] = "1"
        # Identifiant de lancement : sous Windows, le python.exe d'un venv est un lanceur dont le PID
        # diffère de celui de l'interpréteur ; le battement de cœur est donc rattaché à cet identifiant.
        run_id = uuid.uuid4().hex
        env["VIGIEFS_RUN_ID"] = run_id
        if last_incident:
            env["VIGIEFS_LAST_INCIDENT"] = json.dumps(last_incident, ensure_ascii=False, default=str)
        else:
            env.pop("VIGIEFS_LAST_INCIDENT", None)
        out = open(console, "ab")  # noqa: SIM115 — fermé à la fin du processus
        kwargs = {"stdin": subprocess.PIPE, "stdout": out, "stderr": subprocess.STDOUT, "env": env, "cwd": self.cwd}
        if os.name == "nt":
            # Groupe distinct : le Ctrl+C du service est traité par le superviseur, qui arrête proprement.
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        child = subprocess.Popen(self.cmd, **kwargs)  # noqa: S603
        child._vigiefs_out = out
        child._vigiefs_run = run_id
        return child

    def _read_heartbeat(self):
        return read_json(os.path.join(self.rdir, HEARTBEAT), {}) or {}

    def _probe_ok(self):
        if self.probe is None:
            return True
        if callable(self.probe):
            try:
                return bool(self.probe())
            except Exception:  # noqa: BLE001
                return False
        return http_probe(self.probe)

    def _terminate(self, child, timeout=None):
        """Arrêt propre (ordre sur l'entrée standard), puis forcé si le délai est dépassé."""
        timeout = self.stop_timeout if timeout is None else timeout
        if child.poll() is None:
            try:
                child.stdin.write(b"stop\n")
                child.stdin.flush()
                child.stdin.close()
            except OSError:
                pass
            try:
                child.wait(timeout)
            except subprocess.TimeoutExpired:
                log.warning("Arrêt propre non obtenu en %s s : arrêt forcé du processus %s", timeout, child.pid)
                child.terminate()
                try:
                    child.wait(10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(10)
        self._close(child)
        return child.returncode

    @staticmethod
    def _close(child):
        for fh in (child.stdin, getattr(child, "_vigiefs_out", None)):
            try:
                if fh:
                    fh.close()
            except OSError:
                pass

    def _dump_stacks(self, child):
        """Avant de tuer un processus bloqué : pile de chaque thread dans run/blocages.log (Linux)."""
        if os.name != "nt" and hasattr(signal, "SIGUSR1"):
            try:
                os.kill(child.pid, signal.SIGUSR1)
                time.sleep(1)
            except OSError:
                pass

    def _watch(self, child):
        """Surveille le processus ; renvoie (motif, code retour, détail)."""
        started = time.monotonic()
        last_seq, last_change = None, started
        last_probe, probe_fail, ready = 0.0, 0, False
        while True:
            rc = child.poll()
            if rc is not None:
                self._close(child)
                return "plantage", rc, f"code de sortie {rc}"
            cmd = take_command(self.rdir)
            if cmd and cmd["action"] == "stop":
                self.request_stop(cmd.get("actor") or "inconnu", cmd.get("reason") or "")
            if self._stop:
                self._save_state(force=True, status="arrêt en cours")
                return "arret", self._terminate(child), None
            if cmd and cmd["action"] == "restart":
                self._save_state(force=True, status="redémarrage")
                return "redemarrage", self._terminate(child), cmd
            now = time.monotonic()
            hb = self._read_heartbeat()
            if hb.get("run") == child._vigiefs_run and hb.get("seq") != last_seq:
                last_seq, last_change = hb.get("seq"), now
                if not ready:
                    ready = True
                    journal(self.rdir, "pret", pid=hb.get("pid"), startup_sec=round(now - started, 1))
                    self._save_state(force=True, status="en service", ready_at=time.time())
                    sd_notify("READY=1\nSTATUS=VigieFS en service")
            silent = now - last_change
            limit = self.hang_timeout if ready else max(self.hang_timeout, self.startup_grace)
            if silent > limit:
                # Processus figé : l'arrêt propre n'aboutirait pas, arrêt forcé rapide après capture des piles.
                self._down_since = time.time() - silent
                self._save_state(force=True, status="blocage détecté — relance")
                self._dump_stacks(child)
                self._terminate(child, timeout=5)
                return "blocage", child.returncode, f"aucun battement depuis {int(silent)} s"
            if (ready or now - started > self.startup_grace) and now - last_probe >= self.probe_interval:
                last_probe = now
                if self._probe_ok():
                    probe_fail = 0
                else:
                    probe_fail += 1
                    log.warning("Interface sans réponse (%s/%s)", probe_fail, self.probe_failures)
                    if probe_fail >= self.probe_failures:
                        self._down_since = time.time() - self.probe_interval * (probe_fail - 1)
                        self._save_state(force=True, status="interface sans réponse — relance")
                        self._terminate(child, timeout=10)
                        return "sans_reponse", child.returncode, f"{probe_fail} sondes consécutives sans réponse"
                self._save_state(probe={"ok": probe_fail == 0, "failures": probe_fail, "at": time.time()})
            sd_notify("WATCHDOG=1")
            self._save_state(heartbeat_age=round(silent, 1),
                             status="en service" if ready and probe_fail == 0 else
                             "démarrage" if not ready else "dégradé")
            self._wake.wait(self.poll)
            self._wake.clear()

    def _wait_backoff(self, delay):
        """Attente avant relance, interrompue par un arrêt ou une demande de redémarrage immédiat."""
        end = time.monotonic() + delay
        while time.monotonic() < end and not self._stop:
            cmd = take_command(self.rdir)
            if cmd:
                if cmd["action"] == "stop":
                    self.request_stop(cmd.get("actor") or "inconnu", cmd.get("reason") or "")
                    return
                return
            sd_notify("WATCHDOG=1")
            self._wake.wait(min(self.poll, max(0.05, end - time.monotonic())))
            self._wake.clear()

    def run(self):
        if not self.lock.acquire():
            log.error("Un superviseur VigieFS est déjà actif pour %s", self.data_dir)
            return 2
        try:
            return self._run()
        finally:
            self.lock.release()

    def _run(self):
        previous = read_json(os.path.join(self.rdir, STATE), {}) or {}
        remove(os.path.join(self.rdir, STOPPED))
        remove(os.path.join(self.rdir, COMMAND))
        last_incident = None
        if previous.get("status") not in (None, "arrêté") and previous.get("updated_at"):
            # Le superviseur précédent n'a pas pu s'arrêter proprement : coupure, arrêt forcé, plantage OS.
            last_incident = {"kind": "interruption", "at": previous["updated_at"],
                             "detail": "Arrêt brutal du service ou du serveur (coupure, redémarrage forcé)"}
            journal(self.rdir, "interruption", since=previous["updated_at"], detail=last_incident["detail"])
        journal(self.rdir, "superviseur_demarre", pid=os.getpid(), mode=self.mode)
        self.state = {"supervisor_pid": os.getpid(), "supervisor_started_at": time.time(), "mode": self.mode,
                      "status": "démarrage", "restarts": 0, "incidents": 0, "consecutive_failures": 0,
                      "data_dir": self.data_dir}
        self._save_state(force=True)
        attempt = 0
        while not self._stop:
            try:
                child = self._spawn(last_incident)
            except OSError as exc:
                log.error("Lancement impossible : %s", exc)
                child = None
                kind, rc, detail, ran = "plantage", None, f"lancement impossible : {exc}", 0
            if child is not None:
                spawned = time.monotonic()
                journal(self.rdir, "demarrage", pid=child.pid)
                self._save_state(force=True, child_pid=child.pid, child_started_at=time.time(),
                                 status="démarrage", next_restart_at=None)
                kind, rc, detail = self._watch(child)
                ran = time.monotonic() - spawned
            if kind == "arret":
                break
            if kind == "redemarrage":
                journal(self.rdir, "redemarrage", actor=detail.get("actor"), reason=detail.get("reason"), code=rc)
                self.state["restarts"] += 1
                last_incident, attempt = None, 0
                continue
            # Incident : relance avec délai progressif, sans limite de tentatives.
            if ran >= self.stable_after:
                attempt = 0
            delay = self.backoff[min(attempt, len(self.backoff) - 1)]
            attempt += 1
            lines = tail(os.path.join(self.rdir, CONSOLE))
            since, self._down_since = self._down_since, None
            last_incident = {"kind": kind, "code": rc, "detail": detail, "at": time.time(),
                             "ran_sec": round(ran, 1), "log": lines[-10:]}
            journal(self.rdir, "incident", kind=kind, code=rc, detail=detail, ran_sec=round(ran, 1), log=lines[-10:],
                    **({"since": since} if since else {}))
            log.error("Incident VigieFS (%s, %s) : relance dans %s s", INCIDENT_LABEL.get(kind, kind), detail, delay)
            self.state["incidents"] += 1
            self.state["restarts"] += 1
            self._save_state(force=True, status="relance en attente", last_incident=last_incident,
                             consecutive_failures=attempt, next_restart_at=time.time() + delay, child_pid=None)
            sd_notify(f"STATUS=Incident ({INCIDENT_LABEL.get(kind, kind)}), relance dans {delay} s")
            self._wait_backoff(delay)
            if not self._stop:
                journal(self.rdir, "relance", attempt=attempt, delay=delay)
        stop = self._stop or {"actor": "inconnu", "reason": ""}
        sd_notify("STOPPING=1")
        journal(self.rdir, "arret", actor=stop["actor"], reason=stop["reason"])
        write_json(os.path.join(self.rdir, STOPPED), {**stop, "at": time.time()})
        journal(self.rdir, "superviseur_arrete", pid=os.getpid())
        self._save_state(force=True, status="arrêté", child_pid=None, stopped_at=time.time(),
                         stopped_by=stop["actor"], stop_reason=stop["reason"])
        return 0


# ================================================================ lecture de l'état
def status(data_dir):
    rdir = run_dir(data_dir)
    state = read_json(os.path.join(rdir, STATE), {}) or {}
    stopped = read_json(os.path.join(rdir, STOPPED))
    running = supervisor_running(rdir)
    hb = read_json(os.path.join(rdir, HEARTBEAT), {}) or {}
    if running:
        overall = state.get("status") or "en service"
    elif stopped:
        overall = "arrêté volontairement"
    elif state.get("status") not in (None, "arrêté"):
        overall = "interrompu"
    else:
        overall = "arrêté"
    return {"running": running, "status": overall, "state": state, "voluntary_stop": stopped, "heartbeat": hb}


def availability(events, start, end=None):
    """Disponibilité sur [start, end] : temps en service, indisponibilité non planifiée, arrêts volontaires.

    Les arrêts volontaires et redémarrages demandés ne comptent pas comme indisponibilité.
    """
    end = end or time.time()
    up = unplanned = planned = 0.0
    incidents = 0
    state, since = None, start
    for ev in sorted(events, key=lambda e: e.get("ts", 0)):
        ts = min(max(ev.get("ts", 0), start), end)
        kind = ev.get("event")
        if kind == "interruption" or (kind == "incident" and ev.get("since")):
            # Rétroactif : la période depuis le dernier état connu était une indisponibilité subie.
            cut = min(max(ev.get("since") or ts, start), end)
            if state == "up":
                up += max(0, cut - since)
                unplanned += max(0, ts - cut)
            elif state == "down":
                unplanned += max(0, ts - since)
            elif state == "planned":
                planned += max(0, cut - since)
                unplanned += max(0, ts - cut)
            if ev.get("ts", 0) >= start:
                incidents += 1
            state, since = "down", ts
            continue
        new = {"pret": "up", "incident": "down", "redemarrage": "planned", "arret": "planned",
               "superviseur_arrete": "planned"}.get(kind)
        if kind == "superviseur_demarre":
            new = "planned" if state in (None, "planned") else "down"
        if new is None:
            continue
        if state == "up":
            up += ts - since
        elif state == "down":
            unplanned += ts - since
        elif state == "planned":
            planned += ts - since
        if kind == "incident" and ev.get("ts", 0) >= start:
            incidents += 1
        state, since = new, ts
    if state == "up":
        up += end - since
    elif state == "down":
        unplanned += end - since
    elif state == "planned":
        planned += end - since
    measured = up + unplanned
    return {"pct": round(100.0 * up / measured, 3) if measured else None, "up_sec": round(up),
            "unplanned_sec": round(unplanned), "planned_sec": round(planned), "incidents": incidents}


def worker_command(args):
    """Commande du processus de travail à partir des options du superviseur."""
    cmd = [sys.executable, "-m", "vigiefs", "serve", "--supervised", "--host", args.host, "--port", str(args.port),
           "--data", os.path.abspath(args.data_dir)]
    if args.ssl_certfile:
        cmd += ["--ssl-certfile", args.ssl_certfile]
    if args.ssl_keyfile:
        cmd += ["--ssl-keyfile", args.ssl_keyfile]
    return cmd


# ================================================================ contrôle depuis le processus de travail
class ServiceControl:
    """Arrêt / redémarrage demandés depuis l'interface ou la garde.

    Sous superviseur, la demande lui est transmise (fichier de commande) : il arrête proprement le
    processus, puis le relance ou s'arrête. En mode console, seul l'arrêt est possible.
    """

    def __init__(self, data_dir, supervised=None):
        self.rdir = run_dir(data_dir)
        self.supervised = os.environ.get("VIGIEFS_SUPERVISED") == "1" if supervised is None else supervised
        self.mode = os.environ.get("VIGIEFS_SERVICE_MODE", "supervisé" if self.supervised else "console")
        self.server = None
        self.pending = None

    def bind(self, server):
        self.server = server

    def can_restart(self):
        return self.supervised

    def request(self, action, actor, reason=""):
        if action not in ("stop", "restart"):
            raise ValueError(action)
        self.pending = {"action": action, "actor": actor, "reason": reason, "at": time.time()}
        if self.supervised:
            send_command(self.rdir, action, actor, reason)
            return True
        if action == "restart" or self.server is None:
            return False
        journal(self.rdir, "arret", actor=actor, reason=reason)
        write_json(os.path.join(self.rdir, STOPPED), {"actor": actor, "reason": reason, "at": time.time()})
        # Laisse partir la réponse HTTP avant d'arrêter le serveur.
        threading.Timer(0.5, lambda: setattr(self.server, "should_exit", True)).start()
        return True


def watch_stdin(server):
    """Processus supervisé : « stop » ou fermeture de l'entrée standard (superviseur disparu) = arrêt propre."""
    def loop():
        try:
            for line in sys.stdin:
                if line.strip() == "stop":
                    break
        except (OSError, ValueError):
            pass
        server.should_exit = True
    threading.Thread(target=loop, name="vigiefs-controle", daemon=True).start()
