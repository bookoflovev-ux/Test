"""Analyse comportementale des utilisateurs : lignes de base, détecteurs, score de risque, réponses.

Chaque détecteur examine une période (la dernière heure en continu, toute la période d'un journal importé) et
produit des constats datés. Un constat a une empreinte (détecteur, utilisateur, tranche de temps) : relancer
l'analyse sur la même période ne le duplique pas. Les constats récents lèvent une alerte ; tous alimentent le
score de risque de l'utilisateur (somme pondérée, demi-vie réglable, plafonnée à 100).

Lignes de base : activité horaire de l'utilisateur sur les N derniers jours (95e centile des heures actives,
part de l'activité hors horaires, postes et dossiers habituels). Un utilisateur « surveillé » (départ,
compte sensible) a des seuils divisés par deux et des constats plus graves.
"""

import json
import logging
import os
import re
import time
from collections import Counter, defaultdict

from .config import SEVERITY_ORDER, merged_setting
from .rules import ransomware_matchers

log = logging.getLogger("vigiefs.ueba")

DETECTOR_LABEL = {
    "ransomware": "Chiffrement suspect (rançongiciel)",
    "mass_delete": "Suppressions massives",
    "mass_read": "Lectures massives (copie, exfiltration)",
    "volume": "Volume d'activité inhabituel",
    "off_hours": "Activité hors horaires",
    "denied": "Accès refusés répétés",
    "new_device": "Nouveau poste",
    "new_folder": "Dossier inhabituel",
    "perm": "Modifications de droits",
    "audit": "Atteinte à l'audit de sécurité",
    "brute_force": "Échecs de connexion répétés",
    "spray": "Attaque de mots de passe (pulvérisation)",
    "lockout": "Compte verrouillé",
    "dormant": "Compte dormant réactivé",
    "admin_group": "Ajout à un groupe d'administration",
    "account": "Compte créé ou supprimé",
    "storage_growth": "Croissance du volume stocké",
    "capacity": "Capacité de stockage dépassée",
}
SECURITY_DETECTORS = {"audit", "brute_force", "spray", "lockout", "admin_group", "account"}
ADMIN_GROUPS = re.compile(r"admin|domain admins|admins du domaine|enterprise admins|schema admins|backup operators|"
                          r"opérateurs de sauvegarde|operateurs de sauvegarde|account operators|opérateurs de compte|"
                          r"server operators|opérateurs de serveur", re.I)
SEV_UP = {"info": "warning", "warning": "critical", "critical": "critical"}
# Un même épisode ne donne qu'un constat, même s'il chevauche deux tranches de temps ou deux analyses.
EPISODE_SEC = {"mass_delete": 900, "ransomware": 900, "mass_read": 3600, "denied": 3600, "brute_force": 1800,
               "volume": 3600, "perm": 3600}
FILE_BURSTS = {"mass_delete", "ransomware", "mass_read"}


def risk_level(score):
    return "critical" if score >= 70 else "high" if score >= 40 else "medium" if score >= 15 else "low"


def _p95(values):
    if not values:
        return 0
    v = sorted(values)
    return v[min(len(v) - 1, int(round(0.95 * (len(v) - 1))))]


def _fmt_h(ts):
    return time.strftime("%d/%m %H:%M", time.localtime(ts))


def _top_folders(paths, n=5):
    c = Counter((p or "").split("/", 1)[0] if "/" in (p or "") else "(racine)" for p in paths)
    return [{"folder": k, "count": v} for k, v in c.most_common(n)]


class Analyzer:
    def __init__(self, engine, store):
        self.engine = engine
        self.db = engine.db
        self.alerts = engine.alerts
        self.store = store
        self.last_end = None
        self._baselines = {}

    # ------------------------------------------------------------ cycle
    def run(self, start=None, end=None):
        """Analyse [start, end[ (par défaut : depuis la fin de la dernière analyse, avec recouvrement)."""
        cfg = merged_setting(self.db, "security")
        now = time.time()
        live = start is None
        end = end or now
        if start is None:
            # Reprise là où la dernière analyse s'est arrêtée (signet persistant) : l'historique repris au premier
            # démarrage (journal Sécurité des derniers jours) est analysé lui aussi.
            last = self.last_end or ((self.store.source("analysis") or {}).get("bookmark") or {}).get("until")
            if last is None:
                last = now - float(cfg.get("initial_days", 7)) * 86400
            start = max(min(last - 900, end - 3600), now - 30 * 86400)
        self._baselines = {}
        self.cfg, self.thr = cfg, cfg.get("thresholds", {})
        self.profiles = {r["user"]: r for r in self.db.query("SELECT * FROM user_profiles")}
        findings = []
        for det in (self.d_ransomware, self.d_mass_delete, self.d_mass_read, self.d_volume, self.d_off_hours,
                    self.d_denied, self.d_new_device, self.d_new_folder, self.d_perm, self.d_audit, self.d_logons,
                    self.d_dormant, self.d_groups):
            try:
                findings += det(start, end)
            except Exception:  # noqa: BLE001
                log.exception("Détecteur %s en échec", det.__name__)
        bursts = {(f["user"], int(f["ts"] // 3600)) for f in findings if f["detector"] in FILE_BURSTS}
        findings = [f for f in findings if not (f["detector"] in ("volume", "off_hours")
                                                and (f["user"], int(f["ts"] // 3600)) in bursts)]
        new = self.record(findings, now)
        self.update_risk(cfg, now)
        if live:
            self.last_end = end
            self.store.source_update("analysis", True, len(new), {"until": end})
        return new

    def t(self, user, key, default):
        value = float(self.thr.get(key, default))
        return max(1, value * (0.5 if self.watched(user) else 1))

    def watched(self, user):
        return bool((self.profiles.get(user) or {}).get("watch"))

    def sev(self, user, base):
        return SEV_UP[base] if self.watched(user) else base

    def events(self, start, end, actions, extra="", params=()):
        marks = ",".join("?" * len(actions))
        return self.db.query(f"SELECT * FROM user_events WHERE ts >= ? AND ts < ? AND action IN ({marks}) {extra} "
                             "ORDER BY ts", (start, end, *actions, *params))

    def baseline(self, user, before):
        """Ligne de base de l'utilisateur avant `before` (hors dernière heure)."""
        key = (user, int(before // 3600))
        if key in self._baselines:
            return self._baselines[key]
        days = int(self.cfg.get("baseline_days", 28))
        h1 = int(before // 3600) - 1
        rows = self.db.query("SELECT hour, SUM(total) AS t, SUM(reads) AS r FROM user_activity WHERE user=? AND hour>=? "
                             "AND hour<? GROUP BY hour", (user, h1 - days * 24, h1))
        totals = [r["t"] for r in rows if r["t"]]
        off = sum(r["t"] for r in rows if not self.business(r["hour"] * 3600 + 1800))
        b = {"hours": len(totals), "p95": _p95(totals), "reads_p95": _p95([r["r"] for r in rows if r["r"]]),
             "off_share": off / sum(totals) if totals else 0.0}
        self._baselines[key] = b
        return b

    def business(self, ts):
        bh = self.cfg.get("business_hours") or {}
        lt = time.localtime(ts)
        return lt.tm_wday in bh.get("days", [0, 1, 2, 3, 4]) and int(bh.get("start", 7)) <= lt.tm_hour < int(bh.get("end", 20))

    @staticmethod
    def finding(user, detector, bucket, severity, score, title, message, details=None, share_id=None, ts=None):
        return {"user": user, "detector": detector, "fp": f"{detector}:{user}:{bucket}", "severity": severity,
                "score": score, "title": title, "message": message, "details": details or {}, "share_id": share_id,
                "ts": ts or time.time()}

    # ------------------------------------------------------------ fichiers
    def d_ransomware(self, start, end):
        rs_ext, rs_notes = ransomware_matchers(merged_setting(self.db, "ransomware"))
        groups = defaultdict(list)
        for e in self.events(start, end, ("write", "rename", "create")):
            groups[(e["user"], int(e["ts"] // 600))].append(e)
        out = []
        for (user, b), evs in groups.items():
            names = [os.path.basename((e["dest_path"] or e["path"] or "").replace("\\", "/")) for e in evs]
            marked = [n for n in names if n and (rs_ext.match(n) or rs_notes.match(n))]
            thr = self.t(user, "ransomware", 150)
            renames = sum(1 for e in evs if e["action"] == "rename")
            if len(evs) < thr and not (marked and len(evs) >= max(5, thr / 10)):
                continue
            if not marked and renames < thr / 2:
                continue                    # gros enregistrement (copie, synchronisation) sans renommage massif
            clients = Counter(e["client"] for e in evs if e["client"])
            client = clients.most_common(1)[0][0] if clients else None
            sev = "critical" if marked else self.sev(user, "warning")
            out.append(self.finding(
                user, "ransomware", b, sev, 90 if marked else 60,
                "Chiffrement de fichiers en cours ?" if marked else "Renommages massifs de fichiers",
                f"{user} a modifié ou renommé {len(evs)} fichier(s) en 10 min ({_fmt_h(evs[0]['ts'])})"
                + (f" depuis {client}" if client else "")
                + (f", dont {len(marked)} portant une extension ou un nom de note de rançon connus" if marked else "")
                + ". Isolez immédiatement ce poste, fermez les sessions de l'utilisateur et vérifiez les sauvegardes.",
                {"events": len(evs), "renames": renames, "marked": marked[:20], "client": client,
                 "folders": _top_folders([e["path"] for e in evs]), "samples": [e["path"] for e in evs[:20]]},
                evs[0]["share_id"], evs[0]["ts"]))
        return out

    def d_mass_delete(self, start, end):
        groups = defaultdict(list)
        for e in self.events(start, end, ("delete",), "AND result='ok'"):
            groups[(e["user"], int(e["ts"] // 600))].append(e)
        out = []
        for (user, b), evs in groups.items():
            thr = self.t(user, "mass_delete", 100)
            if len(evs) < thr:
                continue
            clients = Counter(e["client"] for e in evs if e["client"])
            sev = "critical" if len(evs) >= 5 * thr else self.sev(user, "warning")
            out.append(self.finding(
                user, "mass_delete", b, sev, 70 if sev == "critical" else 45, "Suppressions massives",
                f"{user} a supprimé {len(evs)} élément(s) en 10 min ({_fmt_h(evs[0]['ts'])})"
                + (f" depuis {clients.most_common(1)[0][0]}" if clients else "")
                + ". Vérifiez auprès de l'utilisateur ; les fichiers se restaurent depuis la sauvegarde Datto ou les "
                  "versions précédentes.",
                {"deleted": len(evs), "folders": _top_folders([e["path"] for e in evs]),
                 "samples": [e["path"] for e in evs[:25]], "client": clients.most_common(1)[0][0] if clients else None},
                evs[0]["share_id"], evs[0]["ts"]))
        return out

    def sensitive_folders(self):
        out = set()
        for s in self.engine.shares():
            job = self.engine.jobs.latest("sensitive", s["id"])
            for r in ((job or {}).get("result") or {}).get("results", []):
                if r.get("level") in ("high", "medium"):
                    rel = r["path"].replace("\\", "/")
                    out.add((s["id"], rel.split("/", 1)[0] if "/" in rel else ""))
        return out

    def d_mass_read(self, start, end):
        groups = defaultdict(list)
        for e in self.events(start, end, ("read", "open"), "AND result='ok'"):
            groups[(e["user"], int(e["ts"] // 3600))].append(e)
        out = []
        sensitive = None
        for (user, h), evs in groups.items():
            files = {(e["share_id"], e["path"]) for e in evs}
            base = self.baseline(user, h * 3600)
            thr = self.t(user, "mass_read", 1000)
            factor = float(self.thr.get("anomaly_factor", 4))
            if base["hours"] >= 20 and base["reads_p95"]:
                thr = min(thr, max(float(self.thr.get("anomaly_min", 300)), factor * base["reads_p95"]))
            if len(files) < thr:
                continue
            if sensitive is None:
                sensitive = self.sensitive_folders()
            touched = {(s, (p or "").split("/", 1)[0] if "/" in (p or "") else "") for s, p in files}
            sens = sorted(f"{f}" for s, f in touched & sensitive)
            off = not self.business(h * 3600 + 1800)
            prof = self.profiles.get(user) or {}
            clients = Counter(e["client"] for e in evs if e["client"])
            client = clients.most_common(1)[0][0] if clients else None
            known = json.loads(prof.get("clients") or "{}").get(client) if client else None
            new_device = bool(known and known[0] >= h * 3600 and (prof.get("first_seen") or 0) < h * 3600 - 7 * 86400)
            sev = "critical" if (off or new_device or sens) else self.sev(user, "warning")
            why = [w for w, c in (("hors des horaires habituels", off), (f"depuis un poste nouveau ({client})", new_device),
                                  ("dans des dossiers contenant des données personnelles", sens)) if c]
            out.append(self.finding(
                user, "mass_read", h, sev, 75 if sev == "critical" else 45, "Lecture massive de fichiers",
                f"{user} a ouvert {len(files)} fichier(s) différents entre {_fmt_h(h * 3600)} et "
                f"{time.strftime('%H:%M', time.localtime(h * 3600 + 3600))}"
                + (f" ({', '.join(why)})" if why else "")
                + (f" — habituellement au plus {int(base['reads_p95'])} par heure" if base["reads_p95"] else "")
                + ". Possible copie massive ou exfiltration de données : vérifiez auprès de l'utilisateur.",
                {"files": len(files), "baseline_p95": base["reads_p95"], "client": client, "off_hours": off,
                 "new_device": new_device, "sensitive_folders": sens[:10], "folders": _top_folders([p for _, p in files]),
                 "samples": [p for _, p in list(files)[:25]]},
                evs[0]["share_id"], evs[0]["ts"]))
        return out

    # ------------------------------------------------------------ comportement horaire
    def hourly(self, start, end):
        return self.db.query("SELECT user, hour, SUM(total) AS total, SUM(reads) AS reads, SUM(writes) AS writes, "
                             "SUM(deletes) AS deletes, SUM(renames) AS renames FROM user_activity WHERE hour >= ? AND "
                             "hour < ? GROUP BY user, hour", (int(start // 3600), int((end + 3599) // 3600)))

    def d_volume(self, start, end):
        out = []
        for r in self.hourly(start, end):
            user, h = r["user"], r["hour"]
            base = self.baseline(user, h * 3600)
            if base["hours"] < 20:
                continue                    # pas encore de ligne de base fiable
            limit = max(self.t(user, "anomaly_min", 300), float(self.thr.get("anomaly_factor", 4)) * base["p95"])
            if r["total"] < limit:
                continue
            mix = {k: r[k] for k in ("reads", "writes", "deletes", "renames") if r[k]}
            out.append(self.finding(
                user, "volume", h, self.sev(user, "warning"), 25, "Activité inhabituellement élevée",
                f"{user} : {r['total']} opération(s) entre {_fmt_h(h * 3600)} et "
                f"{time.strftime('%H:%M', time.localtime(h * 3600 + 3600))}, soit {r['total'] / max(1, base['p95']):.0f} "
                f"fois son heure la plus chargée habituelle ({int(base['p95'])}).",
                {"total": r["total"], "baseline_p95": base["p95"], "mix": mix}, ts=h * 3600))
        return out

    def d_off_hours(self, start, end):
        out = []
        for r in self.hourly(start, end):
            user, h = r["user"], r["hour"]
            if self.business(h * 3600 + 1800) or r["total"] < self.t(user, "offhours_min", 50):
                continue
            base = self.baseline(user, h * 3600)
            if base["hours"] >= 20 and base["off_share"] > 0.05 and not self.watched(user):
                continue                    # travaille régulièrement hors horaires
            day = time.strftime("%Y%m%d", time.localtime(h * 3600))
            out.append(self.finding(
                user, "off_hours", day, self.sev(user, "warning"), 20, "Activité hors horaires",
                f"{user} a été actif hors des horaires de travail ({_fmt_h(h * 3600)} : {r['total']} opération(s)), "
                "ce qui ne lui arrive habituellement pas.",
                {"total": r["total"], "hour": h * 3600, "usual_off_share": round(base["off_share"], 3)}, ts=h * 3600))
        return out

    def d_denied(self, start, end):
        groups = defaultdict(list)
        for e in self.events(start, end, ("denied",)):
            groups[(e["user"], int(e["ts"] // 3600))].append(e)
        out = []
        for (user, h), evs in groups.items():
            if len(evs) < self.t(user, "denied", 25):
                continue
            out.append(self.finding(
                user, "denied", h, self.sev(user, "warning"), 30, "Accès refusés répétés",
                f"{user} a essuyé {len(evs)} refus d'accès en une heure ({_fmt_h(h * 3600)}) : recherche de dossiers "
                "auxquels il n'a pas droit, ou droits mal attribués.",
                {"denied": len(evs), "folders": _top_folders([e["path"] for e in evs]),
                 "samples": sorted({e["path"] or "" for e in evs})[:25]}, evs[0]["share_id"], evs[0]["ts"]))
        return out

    def d_new_device(self, start, end):
        out = []
        for user, prof in self.profiles.items():
            if (prof.get("first_seen") or end) > start - 7 * 86400:
                continue                    # utilisateur trop récent : tous ses postes sont « nouveaux »
            clients = json.loads(prof.get("clients") or "{}")
            for client, (first, _last) in clients.items():
                if start <= first < end and len(clients) > 1:
                    out.append(self.finding(
                        user, "new_device", f"{client}", self.sev(user, "info"), 10, "Connexion depuis un nouveau poste",
                        f"{user} utilise pour la première fois le poste ou l'adresse {client} ({_fmt_h(first)}).",
                        {"client": client, "known": sorted(c for c in clients if c != client)[:10]}, ts=first))
        return out

    def d_new_folder(self, start, end):
        out = []
        sensitive = None
        for user, prof in self.profiles.items():
            if (prof.get("first_seen") or end) > start - 14 * 86400:
                continue
            found = []
            for key, (first, _last) in json.loads(prof.get("folders") or "{}").items():
                if not (start <= first < end):
                    continue
                sid, folder = key.split(":", 1)
                n = self.db.query_one(
                    "SELECT COUNT(*) AS n FROM user_events WHERE user=? AND share_id=? AND ts>=? AND ts<? AND "
                    "(path = ? OR path LIKE ?)", (user, int(sid), start, end, folder, folder + "/%"))["n"]
                if n < 20:
                    continue
                if sensitive is None:
                    sensitive = self.sensitive_folders()
                share = self.engine.share(int(sid)) or {}
                found.append({"folder": folder or "(racine)", "share": share.get("name"), "share_id": int(sid),
                              "count": n, "sensitive": (int(sid), folder) in sensitive, "first": first})
            if not found:
                continue
            found.sort(key=lambda f: f["first"])
            sens = any(f["sensitive"] for f in found)
            names = ", ".join(f"« {f['folder']} » ({f['count']})" for f in found[:6])
            day = time.strftime("%Y%m%d", time.localtime(found[0]["first"]))
            out.append(self.finding(
                user, "new_folder", day, self.sev(user, "warning" if sens else "info"), 30 if sens else 10,
                "Accès à des dossiers inhabituels" if len(found) > 1 else "Accès à un dossier inhabituel",
                f"{user} accède pour la première fois à {names}"
                + (" — dont des dossiers contenant des données personnelles" if sens else "") + ".",
                {"folders": [{"folder": f["folder"], "count": f["count"]} for f in found], "sensitive": sens},
                found[0]["share_id"], found[0]["first"]))
        return out

    def d_perm(self, start, end):
        groups = defaultdict(list)
        for e in self.events(start, end, ("perm", "owner")):
            groups[(e["user"], int(e["ts"] // 3600))].append(e)
        out = []
        for (user, h), evs in groups.items():
            many = len(evs) >= self.t(user, "perm_changes", 10)
            out.append(self.finding(
                user, "perm", h, self.sev(user, "warning" if many else "info"), 20 if many else 5,
                "Droits d'accès modifiés",
                f"{user} a modifié les droits ou le propriétaire de {len(evs)} élément(s) ({_fmt_h(evs[0]['ts'])}).",
                {"count": len(evs), "samples": sorted({e["path"] or "" for e in evs})[:25]}, evs[0]["share_id"],
                evs[0]["ts"]))
        return out

    # ------------------------------------------------------------ sécurité du serveur et des comptes
    def d_audit(self, start, end):
        out = []
        for e in self.events(start, end, ("audit_cleared", "policy")):
            cleared = e["action"] == "audit_cleared"
            d = json.loads(e["details"] or "{}")
            out.append(self.finding(
                e["user"], "audit", f"{e['id']}", "critical" if cleared else "warning", 80 if cleared else 40,
                "Journal de sécurité effacé" if cleared else "Audit de sécurité modifié",
                (f"{e['user']} a effacé le journal de sécurité du serveur ({_fmt_h(e['ts'])}). C'est souvent la trace "
                 "d'une intrusion cherchant à effacer ses traces." if cleared else
                 f"{e['user']} a modifié l'audit ({d.get('objet', 'stratégie')}) le {_fmt_h(e['ts'])}"
                 + (f" : {e['path']}" if e["path"] else "") + ". Vérifiez que la traçabilité n'a pas été désactivée."),
                {"event_id": e["event_id"], **d}, e["share_id"], e["ts"]))
        return out

    def d_logons(self, start, end):
        out = []
        fails = defaultdict(list)
        by_client = defaultdict(set)
        for e in self.events(start, end, ("logon_fail", "lockout")):
            b = int(e["ts"] // 900)
            if e["action"] == "lockout":
                out.append(self.finding(
                    e["user"], "lockout", f"{b}", self.sev(e["user"], "warning"), 15, "Compte verrouillé",
                    f"Le compte {e['user']} a été verrouillé après des échecs de mot de passe ({_fmt_h(e['ts'])})"
                    + (f", depuis {e['client']}" if e["client"] else "") + ".", {"client": e["client"]}, ts=e["ts"]))
                continue
            fails[(e["user"], b)].append(e)
            if e["client"]:
                by_client[(e["client"], b)].add(e["user"])
        for (user, b), evs in fails.items():
            if len(evs) < self.t(user, "logon_fail", 10):
                continue
            clients = Counter(e["client"] for e in evs if e["client"])
            reasons = Counter(json.loads(e["details"] or "{}").get("motif") for e in evs)
            out.append(self.finding(
                user, "brute_force", f"{b}", self.sev(user, "warning"), 35, "Échecs de connexion répétés",
                f"{len(evs)} échecs de connexion pour {user} en 15 min ({_fmt_h(evs[0]['ts'])})"
                + (f", depuis {', '.join(c for c, _ in clients.most_common(3))}" if clients else "")
                + ". Mot de passe oublié sur un appareil, ou tentative de deviner le mot de passe.",
                {"fails": len(evs), "clients": dict(clients.most_common(5)), "reasons": dict(reasons)}, ts=evs[0]["ts"]))
        spray = int(self.thr.get("spray_users", 5))
        for (client, b), users in by_client.items():
            if len(users) >= spray:
                out.append(self.finding(
                    f"poste:{client.lower()}", "spray", f"{b}", "critical", 70, "Attaque de mots de passe",
                    f"Le poste {client} a échoué à se connecter avec {len(users)} comptes différents en 15 min "
                    f"({_fmt_h(b * 900)}) : tentative typique de pulvérisation de mots de passe. Isolez ce poste.",
                    {"client": client, "users": sorted(users)[:30]}, ts=b * 900))
        return out

    def d_dormant(self, start, end):
        out = []
        days = float(self.thr.get("dormant_days", 45))
        h0 = int(start // 3600)
        active = self.db.query("SELECT user, MIN(hour) AS h FROM user_activity WHERE hour >= ? AND hour < ? GROUP BY user",
                               (h0, int((end + 3599) // 3600)))
        for r in active:
            prev = self.db.query_one("SELECT MAX(hour) AS h FROM user_activity WHERE user=? AND hour < ?", (r["user"], r["h"]))
            if prev and prev["h"] is not None and (r["h"] - prev["h"]) * 3600 >= days * 86400:
                idle = (r["h"] - prev["h"]) / 24
                out.append(self.finding(
                    r["user"], "dormant", f"{r['h']}", self.sev(r["user"], "warning"), 30, "Compte dormant réactivé",
                    f"{r['user']} n'avait rien fait depuis {idle:.0f} jours et redevient actif ({_fmt_h(r['h'] * 3600)}). "
                    "Vérifiez qu'il s'agit bien de la personne (ancien salarié, prestataire, compte partagé ?).",
                    {"idle_days": round(idle), "previous": prev["h"] * 3600}, ts=r["h"] * 3600))
        return out

    def d_groups(self, start, end):
        out = []
        for e in self.events(start, end, ("group_add", "account")):
            d = json.loads(e["details"] or "{}")
            if e["action"] == "group_add":
                admin = bool(ADMIN_GROUPS.search(str(d.get("groupe") or "")))
                out.append(self.finding(
                    e["user"], "admin_group" if admin else "account", f"{e['id']}", "critical" if admin else "info",
                    70 if admin else 5, "Ajout à un groupe d'administration" if admin else "Ajout à un groupe",
                    f"{e['user']} a ajouté {d.get('membre')} au groupe {d.get('groupe')} ({_fmt_h(e['ts'])})."
                    + (" Élévation de privilèges : vérifiez que c'est voulu." if admin else ""), d, ts=e["ts"]))
            else:
                out.append(self.finding(
                    e["user"], "account", f"{e['id']}", "info", 5, f"Compte {d.get('operation', 'modifié')}",
                    f"{e['user']} : {d.get('operation', 'opération')} du compte {d.get('compte')} ({_fmt_h(e['ts'])}).",
                    d, ts=e["ts"]))
        return out

    # ------------------------------------------------------------ enregistrement, alertes, score
    def record(self, findings, now=None):
        now = now or time.time()
        new = []
        for f in findings:
            window = EPISODE_SEC.get(f["detector"])
            if window and self.db.query_one(
                    "SELECT 1 AS x FROM user_findings WHERE detector=? AND user=? AND ts > ? AND ts < ? LIMIT 1",
                    (f["detector"], f["user"], f["ts"] - window, f["ts"] + window)):
                continue
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO user_findings(user, ts, detector, severity, score, title, message, details, "
                "share_id, fingerprint, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (f["user"], f["ts"], f["detector"], f["severity"], f["score"], f["title"], f["message"],
                 json.dumps(f["details"], ensure_ascii=False, default=str), f["share_id"], f["fp"], now))
            if not inserted:
                continue
            new.append(f)
            if now - f["ts"] > 86400:
                continue                    # constat ancien (journal importé) : visible dans l'analyse, sans alerte
            share = self.engine.share(f["share_id"]) if f["share_id"] else None
            category = "security" if f["detector"] in SECURITY_DETECTORS or f["user"].startswith("poste:") else "behavior"
            alert_id, _ = self.alerts.raise_alert(
                f"ueba:{f['fp']}", f["severity"], category, f"{f['title']} — {f['user']}", f["message"], share=share,
                details={"user": f["user"], "detector": f["detector"], **f["details"]},
                notify=SEVERITY_ORDER[f["severity"]] >= SEVERITY_ORDER["warning"])
            self.db.execute("UPDATE user_findings SET alert_id=? WHERE fingerprint=?", (alert_id, f["fp"]))
            if f["detector"] == "ransomware" and f["severity"] == "critical":
                self.respond(f)
        return new

    def respond(self, f):
        """Réponse automatique à un chiffrement attribué (désactivée par défaut, voir Paramètres)."""
        auto = self.cfg.get("auto_response") or {}
        if not auto.get("ransomware"):
            return
        from . import response
        done = []
        try:
            if auto.get("close_sessions", True):
                done.append(f"{response.close_sessions(f['user'])} session(s) fermée(s)")
            if auto.get("block_share") and f["share_id"]:
                share = self.engine.share(f["share_id"])
                done.append("accès bloqué : " + ", ".join(response.block(f["user"], [share])))
        except Exception as exc:  # noqa: BLE001
            done.append(f"échec : {exc}")
        self.db.audit("réponse automatique", "rançongiciel : " + " ; ".join(done), f["user"])
        details = {**f["details"], "response": done}
        self.db.execute("UPDATE user_findings SET details=? WHERE fingerprint=?",
                        (json.dumps(details, ensure_ascii=False, default=str), f["fp"]))

    def update_risk(self, cfg, now=None):
        now = now or time.time()
        half = float(cfg.get("risk_half_life_days", 7)) * 86400
        # Cumul « au moins un incident réel » : 1 − Π(1 − score × décroissance). Deux constats à 50 donnent 75,
        # pas 100 : le score reste discriminant même avec beaucoup de petits constats.
        keep = defaultdict(lambda: 1.0)
        for r in self.db.query("SELECT user, ts, score FROM user_findings WHERE ts > ?", (now - 6 * half,)):
            p = min(0.99, r["score"] / 100.0) * 0.5 ** (max(0.0, now - r["ts"]) / half)
            keep[r["user"]] *= 1 - p
        scores = {u: round(100 * (1 - k), 1) for u, k in keep.items()}
        self.db.execute("UPDATE user_profiles SET risk=0, risk_updated=? WHERE risk > 0", (now,))
        self.db.executemany("UPDATE user_profiles SET risk=?, risk_updated=? WHERE user=?",
                            [(v, now, u) for u, v in scores.items()])
        return scores
