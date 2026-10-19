"""Fonctionnement 24 h/24 : superviseur, arrêt volontaire, auto-réparation, sondes, maintenance, API."""

import os
import sys
import textwrap
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vigiefs import service  # noqa: E402
from vigiefs.alerts import AlertManager  # noqa: E402
from vigiefs.api import create_app  # noqa: E402
from vigiefs.db import Database  # noqa: E402
from vigiefs.engine import Engine  # noqa: E402

PASSWORD = "Sup3r-Secret!"

# Faux processus de travail : bat comme la garde, s'arrête sur « stop » ; le comportement du
# premier lancement est piloté par FIRST (crash = code 3, hang = cesse de battre).
FAKE_WORKER = textwrap.dedent("""
    import json, os, sys, threading, time
    rdir, first = sys.argv[1], sys.argv[2]
    flag = os.path.join(rdir, "deja-lance")
    initial = not os.path.exists(flag)
    open(flag, "a").close()
    stop = threading.Event()
    def ctl():
        for line in sys.stdin:
            if line.strip() == "stop":
                break
        stop.set()
    threading.Thread(target=ctl, daemon=True).start()
    seq = 0
    while not stop.is_set():
        if initial and first == "crash" and seq >= 2:
            sys.exit(3)
        if not (initial and first == "hang" and seq >= 2):
            seq += 1
            tmp = os.path.join(rdir, "battement.json.tmp")
            with open(tmp, "w") as fh:
                json.dump({"pid": os.getpid(), "run": os.environ["VIGIEFS_RUN_ID"], "seq": seq}, fh)
            os.replace(tmp, os.path.join(rdir, "battement.json"))
        elif initial and first == "hang":
            time.sleep(60)  # bloqué : ignore même l'ordre d'arrêt
        time.sleep(0.05)
""")


def make_supervisor(tmp_path, first):
    script = tmp_path / "faux_moteur.py"
    script.write_text(FAKE_WORKER)
    data = tmp_path / "data"
    rdir = service.run_dir(str(data))
    sup = service.Supervisor([sys.executable, str(script), rdir, first], str(data), probe=None, hang_timeout=1,
                             startup_grace=5, stop_timeout=5, poll=0.05, backoff=(0.1,))
    return sup, rdir


def run_in_thread(sup):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("rc", sup.run()), daemon=True)
    t.start()
    return t, out


def wait_for(cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    raise AssertionError("condition non atteinte")


def kinds(rdir):
    return [e["event"] for e in service.read_journal(rdir)]


def test_supervisor_restarts_after_crash_and_stops_on_demand(tmp_path):
    sup, rdir = make_supervisor(tmp_path, "crash")
    t, out = run_in_thread(sup)
    wait_for(lambda: kinds(rdir).count("pret") >= 2)
    inc = [e for e in service.read_journal(rdir) if e["event"] == "incident"][0]
    assert inc["kind"] == "plantage" and inc["code"] == 3
    assert service.supervisor_running(rdir)
    service.send_command(rdir, "stop", "test", "fin du test")
    t.join(15)
    assert out["rc"] == 0 and not service.supervisor_running(rdir)
    ev = kinds(rdir)
    assert ev[0] == "superviseur_demarre" and ev[-2:] == ["arret", "superviseur_arrete"]
    assert "relance" in ev
    st = service.status(str(tmp_path / "data"))
    assert st["status"] == "arrêté volontairement" and st["voluntary_stop"]["actor"] == "test"
    assert st["state"]["incidents"] == 1 and st["state"]["restarts"] == 1


def test_supervisor_detects_hang(tmp_path):
    sup, rdir = make_supervisor(tmp_path, "hang")
    t, out = run_in_thread(sup)
    wait_for(lambda: kinds(rdir).count("pret") >= 2)
    inc = [e for e in service.read_journal(rdir) if e["event"] == "incident"][0]
    assert inc["kind"] == "blocage" and inc.get("since")
    sup.request_stop("test", "")
    t.join(15)
    assert out["rc"] == 0


def test_supervisor_restart_on_request_is_not_an_incident(tmp_path):
    sup, rdir = make_supervisor(tmp_path, "ok")
    t, out = run_in_thread(sup)
    wait_for(lambda: kinds(rdir).count("pret") >= 1)
    service.send_command(rdir, "restart", "admin", "mise à jour")
    wait_for(lambda: kinds(rdir).count("pret") >= 2)
    assert "incident" not in kinds(rdir) and "redemarrage" in kinds(rdir)
    service.send_command(rdir, "stop", "admin")
    t.join(15)
    av = service.availability(service.read_journal(rdir), time.time() - 3600)
    assert av["incidents"] == 0 and av["unplanned_sec"] == 0


def test_instance_lock_prevents_second_supervisor(tmp_path):
    rdir = service.run_dir(str(tmp_path))
    lock = service.InstanceLock(os.path.join(rdir, service.LOCK))
    assert lock.acquire()
    assert service.supervisor_running(rdir)
    sup = service.Supervisor([sys.executable, "-c", "pass"], str(tmp_path), poll=0.05)
    assert sup.run() == 2
    lock.release()
    assert not service.supervisor_running(rdir)


def test_availability_counts_only_unplanned_downtime():
    ev = [{"ts": 0, "event": "superviseur_demarre"}, {"ts": 10, "event": "pret"},
          {"ts": 1000, "event": "incident", "kind": "plantage"}, {"ts": 1060, "event": "pret"},
          {"ts": 2000, "event": "arret"}, {"ts": 2001, "event": "superviseur_arrete"},
          {"ts": 3000, "event": "superviseur_demarre"}, {"ts": 3005, "event": "pret"}]
    av = service.availability(ev, 0, 4000)
    assert av["up_sec"] == 990 + 940 + 995 and av["unplanned_sec"] == 60 and av["incidents"] == 1
    assert av["planned_sec"] == 10 + 1005
    assert av["pct"] == round(100 * 2925 / 2985, 3)
    # Interruption brutale : indisponibilité rétroactive depuis le dernier signe de vie.
    ev2 = [{"ts": 0, "event": "pret"}, {"ts": 500, "event": "interruption", "since": 300},
           {"ts": 520, "event": "superviseur_demarre"}, {"ts": 530, "event": "pret"}]
    av2 = service.availability(ev2, 0, 1000)
    assert av2["up_sec"] == 300 + 470 and av2["unplanned_sec"] == 230 and av2["incidents"] == 1


# ------------------------------------------------------------------ garde interne
@pytest.fixture
def eng(tmp_path):
    db = Database(str(tmp_path / "data" / "vigiefs.db"))
    alerts = AlertManager(db)
    engine = Engine(db, alerts)
    cid = db.execute("INSERT INTO clients(name, created_at) VALUES('ACME', ?)", (time.time(),))
    yield db, alerts, engine, cid
    engine.stop()


def add_share(db, cid, path, **kw):
    cols = {"client_id": cid, "name": "Partage", "path": str(path), "created_at": time.time(),
            "scan_interval_min": 0, "monitor_enabled": 1, **kw}
    sid = db.execute(f"INSERT INTO shares({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                     tuple(cols.values()))
    return db.query_one("SELECT * FROM shares WHERE id=?", (sid,))


def test_guard_repairs_scheduler_observer_and_watches(eng, tmp_path):
    db, alerts, engine, cid = eng
    (tmp_path / "p").mkdir()
    share = add_share(db, cid, tmp_path / "p")
    engine.start()
    wait_for(lambda: engine.monitor.is_active(share["id"]))
    # Planificateur arrêté (exception fatale simulée) → relancé.
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    engine._thread = dead
    # Observateur de fichiers arrêté → recréé, surveillances reprogrammées par le planificateur.
    engine.monitor._observer.stop()
    engine.monitor._observer.join(5)
    engine.guard.check()
    assert engine.scheduler_alive()
    assert engine.monitor._observer.is_alive()
    comps = {c["name"]: c for c in engine.guard.snapshot()["components"]}
    assert comps["scheduler"]["repairs"] == 1 and comps["monitor"]["repairs"] >= 1
    assert alerts.db.query_one("SELECT status FROM alerts WHERE fingerprint='service:repair:scheduler'")["status"] == "open"
    # Le planificateur relancé reprogramme aussitôt la surveillance sur le nouvel observateur.
    wait_for(lambda: engine.monitor.is_active(share["id"]))
    assert any(em.is_alive() for em in engine.monitor._observer.emitters)
    events = [e["event"] for e in service.read_journal(engine.guard.rdir)]
    assert events.count("reparation") >= 2
    hb = service.read_json(os.path.join(engine.guard.rdir, service.HEARTBEAT))
    assert hb["seq"] >= 1 and hb["pid"] == os.getpid()


def test_unresponsive_share_does_not_block_scheduler(eng, tmp_path):
    db, _, engine, cid = eng
    share = add_share(db, cid, tmp_path)
    db.set_setting("service", {"probe_timeout_sec": 0.3})
    engine._probe_path = lambda path: (time.sleep(3), (True, None))[1]
    started = time.monotonic()
    assert engine.check_availability(share) is False
    assert time.monotonic() - started < 2
    alert = db.query_one("SELECT message FROM alerts WHERE fingerprint=?", (f"path:{share['id']}",))
    assert "ne répond pas" in alert["message"]
    started = time.monotonic()
    assert engine.check_availability(share) is False   # sonde toujours bloquée : pas de nouvelle attente
    assert time.monotonic() - started < 1


def test_maintenance_suspends_scheduled_actions(eng, tmp_path):
    db, _, engine, cid = eng
    add_share(db, cid, tmp_path, scan_interval_min=1, monitor_enabled=0)
    db.set_setting("maintenance", {"until": time.time() + 600, "reason": "migration", "by": "admin"})
    engine.tick()
    assert not engine.running and engine.maintenance()["reason"] == "migration"
    db.set_setting("maintenance", {})
    engine.tick()
    assert engine.maintenance() is None
    wait_for(lambda: db.query_one("SELECT COUNT(*) AS n FROM scans")["n"] >= 1)


def test_database_backup_integrity_and_interrupted_jobs(tmp_path):
    path = str(tmp_path / "data" / "vigiefs.db")
    db = Database(path)
    db.execute("INSERT INTO jobs(type, title, status, params, actor, created_at) "
               "VALUES('matrix_apply', 'Application', 'running', '{}', 'admin', ?)", (time.time(),))
    db2 = Database(path)
    assert [j["type"] for j in db2.interrupted_jobs] == ["matrix_apply"]
    assert db2.query_one("SELECT status FROM jobs")["status"] == "interrupted"
    engine = Engine(db2, AlertManager(db2))
    os.environ["VIGIEFS_LAST_INCIDENT"] = '{"kind": "plantage", "detail": "code de sortie 1"}'
    try:
        engine.guard._report_startup()
    finally:
        del os.environ["VIGIEFS_LAST_INCIDENT"]
    fps = {a["fingerprint"]: a for a in db2.query("SELECT fingerprint, severity FROM alerts")}
    assert fps["service:jobs"]["severity"] == "warning" and "service:incident" in fps
    info = engine.guard.maintain_database()
    assert info["integrity"] == "ok" and os.path.isfile(os.path.join(tmp_path, "data", "sauvegardes-base", info["backup_file"]))
    for _ in range(3):
        db2.backup_to(str(tmp_path / "bk"), keep=2)
        time.sleep(1.01)
    assert len(os.listdir(tmp_path / "bk")) == 2
    db2.checkpoint()
    assert db2.quick_check() == "ok"


def test_guard_requests_restart_on_memory_only_when_supervised(eng):
    _, _, engine, _ = eng
    calls = []
    engine.guard.control = SimpleNamespace(supervised=True, request=lambda *a: calls.append(a))
    engine.db.set_setting("service", {"max_memory_mb": 1})
    engine.guard.check()
    assert calls and calls[0][0] == "restart" and "mémoire" in calls[0][2]
    comps = {c["name"]: c for c in engine.guard.snapshot()["components"]}
    assert comps["memory"]["status"] == "degraded"


# ------------------------------------------------------------------ API
@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "data"), start_engine=False)
    c = TestClient(app)
    c.headers.update({"X-VigieFS": "1"})
    c.post("/api/auth/setup", json={"username": "admin", "password": PASSWORD})
    c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    return c, app


def test_service_api(client, tmp_path):
    c, app = client
    anon = TestClient(app)
    assert anon.get("/api/health/live").json()["status"] == "ok"
    assert anon.get("/api/health/ready").status_code == 503       # moteur non démarré
    assert anon.get("/api/service").status_code == 401
    app.state.engine.guard.check()
    s = c.get("/api/service").json()
    assert s["supervised"] is False and not s["can_restart"]
    assert {x["name"] for x in s["components"]} >= {"database", "disk", "memory", "events"}
    assert c.post("/api/service/action", json={"action": "restart"}).status_code == 409
    assert c.post("/api/service/action", json={"action": "stop", "confirm": "non"}).status_code == 400
    server = SimpleNamespace(should_exit=False)
    app.state.control.bind(server)
    assert c.post("/api/service/action", json={"action": "stop", "confirm": "arrêter", "reason": "test"}).json()["ok"]
    wait_for(lambda: server.should_exit, 5)
    marker = service.read_json(os.path.join(tmp_path, "data", "run", service.STOPPED))
    assert marker["actor"] == "admin (interface)" and marker["reason"] == "test"
    m = c.post("/api/service/maintenance", json={"minutes": 30, "reason": "migration"}).json()
    assert m["by"] == "admin"
    st = c.get("/api/v1/status").json()
    assert st["service"]["maintenance"]["reason"] == "migration" and st["service"]["supervised"] is False
    assert c.delete("/api/service/maintenance").json()["ok"]
    assert c.get("/api/service").json()["maintenance"] is None
    r = c.post("/api/service/db-backup").json()
    assert r["integrity"] == "ok" and r["backup_file"].startswith("vigiefs-")
    assert "vigiefs_component_ok" in c.get("/metrics").text


def test_supervised_api_forwards_commands(client, tmp_path):
    c, app = client
    app.state.control.supervised = True
    assert c.post("/api/service/action", json={"action": "restart", "reason": "mise à jour"}).json()["ok"]
    cmd = service.take_command(os.path.join(tmp_path, "data", "run"))
    assert cmd["action"] == "restart" and cmd["actor"] == "admin (interface)"


def test_windows_extended_paths(monkeypatch):
    r"""Chemins > 260 caractères sous Windows : préfixe \\?\ (local et UNC), chemins relatifs inchangés."""
    import ntpath
    from vigiefs import rules
    monkeypatch.setattr(rules, "os", SimpleNamespace(name="nt", path=ntpath))
    assert rules.long_path("C:\\Partages\\Commun") == "\\\\?\\C:\\Partages\\Commun"
    assert rules.long_path("\\\\SRV\\Commun\\RH") == "\\\\?\\UNC\\SRV\\Commun\\RH"
    assert rules.long_path("\\\\?\\C:\\x") == "\\\\?\\C:\\x"
    assert rules.strip_long("\\\\?\\UNC\\SRV\\Commun") == "\\\\SRV\\Commun"
    deep = "\\\\?\\C:\\Partages\\Commun\\" + "\\".join(["Dossier très long"] * 20) + "\\fichier.docx"
    assert rules.relpath("C:\\Partages\\Commun", deep) == "/".join(["Dossier très long"] * 20) + "/fichier.docx"
    assert rules.relpath("C:\\Partages\\Commun", "\\\\?\\C:\\Partages\\Commun") == ""


def test_demo_dataset(tmp_path):
    from vigiefs import demo, pathcheck
    data = tmp_path / "data-demo"
    res = demo.create(str(data))
    assert res["created"] and len(res["shares"]) == 2 and all(s["files"] > 30 for s in res["shares"])
    assert demo.create(str(data))["created"] is False          # idempotent
    db = Database(str(data / "vigiefs.db"))
    share = db.query_one("SELECT * FROM shares WHERE name='Commun'")
    longest = max(pathcheck.user_path_length(share["access_path"], os.path.relpath(os.path.join(dp, f), share["path"]))
                  for dp, _, files in os.walk(share["path"]) for f in files)
    assert longest > 260                                          # alerte « chemin trop long » garantie
    assert max(len(os.path.join(dp, f)) for dp, _, files in os.walk(share["path"]) for f in files) < 400
    assert db.query_one("SELECT COUNT(*) AS n FROM quotas")["n"] == 1
    assert db.get_setting("archive")["root"].endswith("demo-archives")


def test_start_raises_no_false_repair(eng):
    db, _, engine, _ = eng
    engine.start()
    engine.guard.check()
    assert db.query_one("SELECT COUNT(*) AS n FROM alerts WHERE fingerprint LIKE 'service:repair:%'")["n"] == 0
    assert {c["name"]: c for c in engine.guard.snapshot()["components"]}["scheduler"]["repairs"] == 0
