"""Sécurité et utilisateurs : journaux Windows / Samba, rattachement, lignes de base, détecteurs, occupation, API."""

import json
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vigiefs import activity, smb  # noqa: E402
from vigiefs.activity import ShareMapper, parse_samba_line, parse_windows, parse_windows_xml  # noqa: E402
from vigiefs.api import create_app  # noqa: E402

PASSWORD = "Sup3r-Secret!"
WIN_SHARE = {"id": 1, "name": "Commun", "path": "C:\\Partages\\Commun", "access_path": "\\\\SRV-FS\\Commun"}
DAY = 86400


def rec(i, d, t=None, fail=False):
    return {"i": i, "r": 1, "t": t or time.time(), "f": fail, "d": d}


def subj(name="alice", dom="CABINET", **kw):
    return {"SubjectUserName": name, "SubjectDomainName": dom, **kw}


# ------------------------------------------------------------------ analyse des journaux
def test_parse_windows_file_events():
    e = parse_windows(rec(4663, subj(ObjectType="File", ObjectName="C:\\Partages\\Commun\\RH\\paie.xlsx",
                                     AccessMask="0x2", ProcessName="C:\\Program Files\\Excel\\EXCEL.EXE")))
    assert e["action"] == "write" and e["user"] == "CABINET\\alice" and e["details"]["processus"] == "EXCEL.EXE"
    assert parse_windows(rec(4663, subj(ObjectType="File", ObjectName="C:\\x\\a.pdf", AccessMask="0x10000")))["action"] == "delete"
    assert parse_windows(rec(4663, subj(ObjectType="File", ObjectName="C:\\x\\a.pdf", AccessMask="0x1")))["action"] == "read"
    assert parse_windows(rec(4663, subj(ObjectType="File", ObjectName="C:\\x\\dossier", AccessMask="0x1"))) is None
    assert parse_windows(rec(4663, subj(ObjectType="File", ObjectName="C:\\x", AccessMask="0x40000")))["action"] == "perm"
    assert parse_windows(rec(4663, subj(ObjectType="Key", ObjectName="HKLM\\x", AccessMask="0x2"))) is None
    # 4656 : seuls les refus ; 5145 : refus avec l'adresse, succès = poste seulement
    assert parse_windows(rec(4656, subj(ObjectType="File", ObjectName="C:\\x\\a.pdf", AccessMask="0x2"))) is None
    d = parse_windows(rec(4656, subj(ObjectType="File", ObjectName="C:\\x\\a.pdf", AccessMask="0x2"), fail=True))
    assert d["action"] == "denied" and d["result"] == "fail"
    d = parse_windows(rec(5145, subj(ShareName="\\\\*\\Commun", ShareLocalPath="\\??\\C:\\Partages\\Commun",
                                     RelativeTargetName="Direction\\budget.xlsx", AccessMask="0x1", IpAddress="10.0.0.8"),
                          fail=True))
    assert d["action"] == "denied" and d["abs"] == "C:\\Partages\\Commun\\Direction\\budget.xlsx" and d["client"] == "10.0.0.8"
    ok = parse_windows(rec(5145, subj(ShareLocalPath="\\??\\C:\\Partages\\Commun", RelativeTargetName="a.txt",
                                      IpAddress="10.0.0.8")))
    assert ok["action"] is None and ok["client"] == "10.0.0.8"


def test_parse_windows_account_events():
    e = parse_windows(rec(4625, {"TargetUserName": "bob", "TargetDomainName": "CABINET", "SubStatus": "0xC000006A",
                                 "Status": "0xC000006D", "LogonType": "3", "IpAddress": "10.0.5.20",
                                 "WorkstationName": "-"}))
    assert e["action"] == "logon_fail" and e["details"]["motif"] == "mot de passe incorrect" and e["client"] == "10.0.5.20"
    e = parse_windows(rec(4624, {"TargetUserName": "bob", "TargetDomainName": "CABINET", "LogonType": "10",
                                 "WorkstationName": "PC-BOB", "IpAddress": "10.0.0.3"}))
    assert e["action"] == "logon" and e["client"] == "PC-BOB" and e["details"]["type"] == "bureau à distance"
    assert parse_windows(rec(4624, {"TargetUserName": "bob", "LogonType": "5"})) is None   # service
    e = parse_windows(rec(4740, {"TargetUserName": "bob", "TargetDomainName": "PC-INCONNU", "SubjectUserName": "DC$"}))
    assert e["action"] == "lockout" and e["user"] == "bob" and e["client"] == "PC-INCONNU"
    assert parse_windows(rec(1102, {"SubjectUserName": "admin", "SubjectDomainName": "CABINET"}))["action"] == "audit_cleared"
    e = parse_windows(rec(4728, subj(MemberName="CN=Bob Leroy,OU=Users,DC=cabinet,DC=local", TargetUserName="Admins du domaine")))
    assert e["action"] == "group_add" and e["details"] == {"membre": "Bob Leroy", "groupe": "Admins du domaine"}


XML_EXPORT = """<?xml version="1.0" encoding="UTF-8"?>
<Events>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="Microsoft-Windows-Security-Auditing"/>
<EventID>4663</EventID><Keywords>0x8020000000000000</Keywords><TimeCreated SystemTime="2026-10-12T08:15:02.1234567Z"/>
<EventRecordID>9001</EventRecordID></System><EventData><Data Name="SubjectUserName">alice</Data>
<Data Name="SubjectDomainName">CABINET</Data><Data Name="ObjectType">File</Data>
<Data Name="ObjectName">C:\\Partages\\Commun\\Contrats\\bail.docx</Data><Data Name="AccessMask">0x10000</Data></EventData></Event>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><EventID>1102</EventID>
<Keywords>0x4020000000000000</Keywords><TimeCreated SystemTime="2026-10-12T23:59:00Z"/><EventRecordID>9002</EventRecordID></System>
<UserData><LogFileCleared xmlns="http://manifests.microsoft.com/win/2004/08/windows/eventlog"><SubjectUserName>intrus</SubjectUserName>
<SubjectDomainName>CABINET</SubjectDomainName></LogFileCleared></UserData></Event>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><EventID>5145</EventID>
<Keywords>0x8010000000000000</Keywords><TimeCreated SystemTime="2026-10-12T09:00:00Z"/><EventRecordID>9003</EventRecordID></System>
<EventData><Data Name="SubjectUserName">stagiaire</Data><Data Name="SubjectDomainName">CABINET</Data>
<Data Name="IpAddress">10.0.0.44</Data><Data Name="ShareLocalPath">\\??\\C:\\Partages\\Commun</Data>
<Data Name="RelativeTargetName">RH\\Paie</Data><Data Name="AccessMask">0x1</Data></EventData></Event>
</Events>"""


def test_parse_windows_xml_export():
    recs = parse_windows_xml(XML_EXPORT)
    assert [r["i"] for r in recs] == [4663, 1102, 5145] and recs[0]["r"] == 9001
    assert recs[0]["t"] == pytest.approx(1791792902.1234567, abs=0.01) and not recs[0]["f"] and recs[2]["f"]
    events = [parse_windows(r) for r in recs]
    assert events[0]["action"] == "delete" and events[1]["action"] == "audit_cleared"
    assert events[1]["user"] == "CABINET\\intrus" and events[2]["action"] == "denied"


def test_parse_samba_lines():
    now = time.time()
    e = parse_samba_line("Oct 20 10:15:01 nas smbd_audit: alice|10.0.0.5|pc-alice|Commun|openat|ok|w|Compta/bilan.xlsx", now)
    assert e["action"] == "write" and e["share_name"] == "Commun" and e["rel"] == "Compta/bilan.xlsx" and e["client"] == "pc-alice"
    e = parse_samba_line("2026-10-20T10:15:01.000000+02:00 nas smbd_audit: bob|10.0.0.6|pc-bob|Commun|renameat|ok|a.docx|b.docx.lockbit")
    assert e["action"] == "rename" and e["rel_dest"] == "b.docx.lockbit" and e["ts"] == pytest.approx(1792484101, abs=1)
    e = parse_samba_line("x smbd_audit: carole|10.0.0.7|pc-c|RH|unlinkat|fail (Permission denied)|Paie/x.csv", now)
    assert e["action"] == "denied" and e["result"] == "fail" and e["details"]["motif"] == "Permission denied"
    assert parse_samba_line("Oct 20 10:15:01 nas sshd[12]: Accepted password for root", now) is None
    assert parse_samba_line("x smbd_audit: alice|10.0.0.5|pc|Commun|connect|ok|Commun", now)["action"] == "share"


def test_share_mapper():
    m = ShareMapper([WIN_SHARE, {"id": 2, "name": "RH", "path": "C:\\Partages\\Commun\\RH"},
                     {"id": 3, "name": "NAS", "path": "/srv/nas"}])
    s, rel = m.map("C:\\partages\\commun\\Contrats\\Bail.docx")
    assert s["id"] == 1 and rel == "Contrats/Bail.docx"
    assert m.map("\\??\\C:\\Partages\\Commun\\RH\\Paie\\x.csv")[0]["id"] == 2      # le plus précis gagne
    assert m.map("\\\\SRV-FS\\Commun\\a.txt") == (WIN_SHARE, "a.txt")
    assert m.map("/srv/nas/x/y") [1] == "x/y" and m.map("/srv/nasbis/x") == (None, None)
    assert m.map("C:\\Partages\\Commun") == (WIN_SHARE, "")


# ------------------------------------------------------------------ ingestion, lignes de base, détecteurs
@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(smb, "sessions", lambda: [])
    monkeypatch.setattr(smb, "open_files", lambda: [])
    app = create_app(str(tmp_path / "data"), start_engine=False)
    c = TestClient(app)
    c.headers.update({"X-VigieFS": "1"})
    c.post("/api/auth/setup", json={"username": "admin", "password": PASSWORD})
    c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    root = tmp_path / "partage"
    for rel in ("Contrats/bail.docx", "RH/Paie/salaires.xlsx", "Utilisateurs/alice/note.docx",
                "Utilisateurs/bob/film.mp4", "Utilisateurs/bob/projet/plan.pdf"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * (5000 if "film" in rel else 300))
    cid = c.post("/api/clients", json={"name": "Cabinet"}).json()["id"]
    sid = c.post("/api/shares", json={"client_id": cid, "name": "Commun", "path": str(root), "scan_interval_min": 0,
                                      "monitor_enabled": False, "home_roots": "Utilisateurs"}).json()["id"]
    engine = app.state.engine
    return c, app, engine, sid, root


def ev(ts, user, action, path=None, client=None, **kw):
    return {"ts": ts, "user": user, "action": action, "abs": path, "client": client, "source": "test", **kw}


def test_ingest_dedupes_attributes_and_aggregates(env):
    c, app, engine, sid, root = env
    store, mapper = engine.security.store, engine.security.mapper()
    t = time.time() - 600
    n = store.ingest([
        {"ts": t, "user": "CABINET\\Alice", "action": None, "client": "10.0.0.8", "source": "windows"},   # 5145 succès
        ev(t + 1, "CABINET\\Alice", "write", str(root / "Contrats" / "bail.docx")),
        ev(t + 2, "CABINET\\Alice", "write", str(root / "Contrats" / "bail.docx")),                        # doublon
        ev(t + 3, "CABINET\\alice", "delete", str(root / "Contrats" / "bail.docx")),
        ev(t + 4, "CABINET\\SRV-FS$", "write", str(root / "x")),                                            # machine
        ev(t + 5, "NT AUTHORITY\\SYSTEM", "write", str(root / "x")),
        ev(t + 6, "CABINET\\svc_backup", "read", str(root / "x.pdf")),
        {"ts": t + 7, "user": "alice", "action": "lockout", "client": "PC-X", "source": "windows"},          # identifiant seul
    ], mapper)
    assert n == 3
    rows = engine.db.query("SELECT * FROM user_events ORDER BY ts")
    assert {r["user"] for r in rows} == {"cabinet\\alice"}
    assert rows[0]["client"] == "10.0.0.8" and rows[0]["share_id"] == sid and rows[0]["path"] == "Contrats/bail.docx"
    agg = engine.db.query_one("SELECT SUM(writes) AS w, SUM(deletes) AS d, SUM(logon_fails) AS f, SUM(total) AS t "
                              "FROM user_activity WHERE user='cabinet\\alice'")
    assert (agg["w"], agg["d"], agg["f"], agg["t"]) == (1, 1, 1, 3)
    prof = engine.db.query_one("SELECT * FROM user_profiles WHERE user='cabinet\\alice'")
    assert set(json.loads(prof["clients"])) == {"10.0.0.8", "PC-X"} and f"{sid}:Contrats" in json.loads(prof["folders"])


def seed_baseline(store, mapper, root, user, days=30, per_hour=40, client="PC-ALICE", end=None):
    """Activité de bureau régulière : 9 h–17 h, du lundi au vendredi."""
    end = end or time.time() - 2 * DAY
    events = []
    for d in range(days, 0, -1):
        day = end - d * DAY
        lt = time.localtime(day)
        if lt.tm_wday >= 5:
            continue
        base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 9, 0, 0, 0, 0, -1))
        for h in range(8):
            for k in range(per_hour):
                events.append(ev(base + h * 3600 + k * 70, user, "read" if k % 3 else "write",
                                 str(root / "Contrats" / f"doc{k % 25}.docx"), client))
    store.ingest(events, mapper)


def night(days_ago=0, hour=2):
    lt = time.localtime(time.time() - days_ago * DAY)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, 0, 0, 0, 0, -1))


def test_detectors_find_anomalies(env):
    c, app, engine, sid, root = env
    sec = engine.security
    store, mapper = sec.store, sec.mapper()
    seed_baseline(store, mapper, root, "CABINET\\alice")
    seed_baseline(store, mapper, root, "CABINET\\carole", client="PC-CAROLE")
    seed_baseline(store, mapper, root, "CABINET\\ancien", days=120, per_hour=5, client="PC-ANCIEN",
                  end=time.time() - 80 * DAY)
    t0 = night(0, 2)
    burst = []
    # alice : 1 500 fichiers lus à 2 h du matin depuis un poste inconnu, dont la paie (données sensibles)
    burst += [ev(t0 + k, "CABINET\\alice", "read", str(root / ("RH/Paie" if k % 2 else "Contrats") / f"f{k}.pdf"),
                 "PC-INCONNU-07") for k in range(1500)]
    # carole : 300 suppressions en 5 min
    t1 = t0 + 3600 + 600
    burst += [ev(t1 + k, "CABINET\\carole", "delete", str(root / "Contrats" / f"x{k}.docx"), "PC-CAROLE")
              for k in range(300)]
    # stagiaire : 40 refus ; bob : renommages en .lockbit ; force brute ; pulvérisation ; journal effacé
    burst += [ev(t0 + 120 + k, "CABINET\\stagiaire", "denied", str(root / "RH" / f"p{k}.xlsx"), "PC-STAGE",
                 result="fail") for k in range(40)]
    burst += [ev(t1 + k, "CABINET\\bob", "rename", str(root / "Contrats" / f"c{k}.docx"), "PC-BOB",
                 abs_dest=str(root / "Contrats" / f"c{k}.docx.lockbit")) for k in range(60)]
    burst += [ev(t0 + 60 + k, "CABINET\\direction", "logon_fail", client="10.0.5.20", result="fail",
                 details={"motif": "mot de passe incorrect"}) for k in range(15)]
    burst += [ev(t0 + 300 + k, f"CABINET\\user{k}", "logon_fail", client="10.6.6.6", result="fail") for k in range(6)]
    burst += [ev(t0 + 900, "CABINET\\intrus", "audit_cleared"),
              ev(t0 + 950, "CABINET\\intrus", "group_add", details={"membre": "intrus", "groupe": "Admins du domaine"}),
              ev(t0 + 400, "CABINET\\ancien", "read", str(root / "Contrats" / "bail.docx"), "PC-ANCIEN")]
    store.ingest(burst, mapper)
    found = sec.analyzer.run(t0 - 3600, time.time()) if sec.analyzer else None
    if found is None:
        from vigiefs.ueba import Analyzer
        sec.analyzer = Analyzer(engine, store)
        found = sec.analyzer.run(t0 - 3600, time.time())
    by = {(f["detector"], f["user"]) for f in found}
    assert ("mass_read", "cabinet\\alice") in by
    mr = next(f for f in found if f["detector"] == "mass_read")
    assert mr["severity"] == "critical" and mr["details"]["off_hours"] and mr["details"]["new_device"]
    assert ("new_device", "cabinet\\alice") in by and ("new_folder", "cabinet\\alice") in by
    # Hors horaires et volume inhabituel de la même heure : portés par le constat de lecture massive, pas en double.
    assert ("off_hours", "cabinet\\alice") not in by and ("volume", "cabinet\\alice") not in by
    assert ("mass_delete", "cabinet\\carole") in by
    assert ("denied", "cabinet\\stagiaire") in by
    rw = next(f for f in found if f["detector"] == "ransomware")
    assert rw["user"] == "cabinet\\bob" and rw["severity"] == "critical" and rw["details"]["marked"]
    assert ("brute_force", "cabinet\\direction") in by and ("spray", "poste:10.6.6.6") in by
    assert ("audit", "cabinet\\intrus") in by and ("admin_group", "cabinet\\intrus") in by
    assert ("dormant", "cabinet\\ancien") in by
    assert not any(f["user"] == "cabinet\\carole" and f["detector"] in ("off_hours", "volume") for f in found
                   if f["detector"] != "mass_delete" and f["ts"] < t1 - 3600)
    # Relancer l'analyse sur la même période ne duplique rien ; les alertes sont levées une fois.
    again = sec.analyzer.run(t0 - 3600, time.time())
    assert again == []
    alerts = engine.db.query("SELECT * FROM alerts WHERE fingerprint LIKE 'ueba:%'")
    assert len(alerts) == len(found) and {a["category"] for a in alerts} == {"behavior", "security"}
    risk = {r["user"]: r["risk"] for r in engine.db.query("SELECT user, risk FROM user_profiles")}
    assert risk["cabinet\\alice"] > risk["cabinet\\carole"] > 0 and risk["cabinet\\bob"] >= 60
    # Un utilisateur « surveillé » déclenche dès la moitié des seuils.
    engine.db.execute("UPDATE user_profiles SET watch=1 WHERE user='cabinet\\carole'")
    t2 = t1 + 3 * 3600
    store.ingest([ev(t2 + k, "CABINET\\carole", "delete", str(root / "RH" / f"z{k}.docx"), "PC-CAROLE")
                  for k in range(60)], mapper)
    assert any(f["detector"] == "mass_delete" for f in sec.analyzer.run(t2 - 60, t2 + 600))


def test_owner_storage_capacity_and_api(env, monkeypatch):
    c, app, engine, sid, root = env
    engine.scan_share(engine.share(sid))
    rows = {r["user"]: r for r in engine.db.query("SELECT * FROM user_storage WHERE share_id=?", (sid,))}
    assert rows["bob"]["size"] == 5300 and rows["bob"]["via"] == "home" and rows["alice"]["files"] == 1
    me = next(k for k in rows if k not in ("alice", "bob"))
    assert rows[me]["via"] == "owner" and rows[me]["files"] == 2
    assert json.loads(rows["bob"]["folders"])[0]["name"] == "Utilisateurs"
    # Capacité allouée par utilisateur → alerte ; occupation visible dans la liste et la fiche.
    assert c.put("/api/security/user?u=bob", json={"capacity_gb": 0.000004, "watch": True}).json()["ok"]
    alert = engine.db.query_one("SELECT * FROM alerts WHERE fingerprint='ucap:bob'")
    assert alert and alert["severity"] == "critical" and alert["category"] == "quota"
    users = {u["user"]: u for u in c.get("/api/security/users").json()}
    assert users["bob"]["size"] == 5300 and users["bob"]["capacity_pct"] > 100 and users["bob"]["watch"]
    d = c.get("/api/security/user", params={"u": "bob"}).json()
    assert d["storage_total"] == 5300 and d["storage"][0]["share_name"] == "Commun" and len(d["heatmap"]) == 7
    assert len(d["week"]) >= 167 and d["capacity"] and d["profile"]["watch"] == 1
    assert c.get("/api/security/user", params={"u": "personne"}).status_code == 404
    # Croissance brutale entre deux analyses
    engine.db.set_setting("security", {"thresholds": {**engine.db.get_setting("security", {}).get("thresholds", {}),
                                                      "storage_growth_gb": 0.000001}})
    from vigiefs.ueba import Analyzer
    engine.security.analyzer = Analyzer(engine, engine.security.store)
    (root / "Utilisateurs" / "bob" / "gros.iso").write_bytes(b"0" * 9000)
    engine.scan_share(engine.share(sid))
    assert engine.db.query_one("SELECT * FROM user_findings WHERE detector='storage_growth' AND user='bob'")


def test_api_logs_import_analysis_and_actions(env, monkeypatch):
    c, app, engine, sid, root = env
    monkeypatch.setattr(smb, "samba_shares", lambda: [{"name": "Commun", "path": str(root)}])
    lines = [f"Oct 12 02:{m:02d}:{s:02d} nas smbd_audit: dupont|10.0.0.9|pc-dupont|Commun|unlinkat|ok|Contrats/f{m}{s}.docx"
             for m in range(3) for s in range(0, 60, 1)]
    r = c.post("/api/security/import?filename=audit.log", content="\n".join(lines).encode())
    job = r.json()["job_id"]
    for _ in range(100):
        j = c.get(f"/api/jobs/{job}").json()
        if j["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert j["status"] == "done", j
    assert j["result"]["kept"] == 180 and j["result"]["format"] == "Journal d'audit Samba" and j["result"]["findings"] >= 1
    f = c.get("/api/security/findings", params={"u": "dupont"}).json()
    md = next(x for x in f if x["detector"] == "mass_delete")
    assert md["label"] == "Suppressions massives" and [x["detector"] for x in f] == ["mass_delete"]
    # Constat ancien (journal importé) : pas d'alerte, mais visible dans l'analyse.
    assert not engine.db.query("SELECT 1 FROM alerts WHERE fingerprint LIKE 'ueba:mass_delete:dupont%'")
    ev = c.get("/api/security/events", params={"u": "dupont", "action": "delete", "limit": 5}).json()
    assert ev["total"] == 180 and ev["rows"][0]["share_name"] == "Commun" and ev["rows"][0]["client"] == "pc-dupont"
    st = c.get("/api/security/stats", params={"u": "dupont"}).json()
    assert st["total"] == 180 and st["by_action"][0] == {"k": "delete", "n": 180} and st["histogram"]
    csv_text = c.get("/api/security/events.csv", params={"u": "dupont"}).content.decode("utf-8-sig")
    assert csv_text.count("\n") == 181 and "Suppression" in csv_text
    r = c.post("/api/security/import?filename=Securite.xml", content=XML_EXPORT.encode())
    for _ in range(100):
        j = c.get(f"/api/jobs/{r.json()['job_id']}").json()
        if j["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert j["status"] == "done" and j["result"]["format"] == "Journal Windows (XML)" and j["result"]["kept"] == 3
    if os.name != "nt":
        r = c.post("/api/security/import?filename=x.evtx", content=b"ElfFile")
        for _ in range(100):
            j = c.get(f"/api/jobs/{r.json()['job_id']}").json()
            if j["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        assert j["status"] == "failed" and "XML" in j["error"]
    ov = c.get("/api/security/overview").json()
    assert ov["events_total"] >= 183 and ov["findings"] and len(ov["timeline"]) == 48
    # Réponses : fermeture des sessions (simulées) ; blocage réservé aux administrateurs, confirmation exigée.
    closed = []
    monkeypatch.setattr(smb, "sessions", lambda: [{"id": "7", "user": "NAS\\dupont", "client": "pc-dupont"},
                                                  {"id": "8", "user": "NAS\\autre"}])
    monkeypatch.setattr(smb, "close_session", lambda sid_: closed.append(sid_))
    r = c.post("/api/security/user/action?u=dupont", json={"action": "close_sessions"}).json()
    assert r["message"] == "1 session(s) fermée(s)" and closed == ["7"]
    assert c.post("/api/security/user/action?u=dupont", json={"action": "disable"}).status_code == 400
    assert c.put("/api/security/user?u=dupont", json={"service": True}).json()["ok"]
    assert engine.security.store.exclusions()("dupont")
    # Préparation de l'audit : état et script
    audit = c.get("/api/security/audit").json()
    assert audit["items"] and audit["platform"] in ("posix", "windows")
    script = c.get("/api/security/audit/script.ps1").content.decode("utf-8-sig")
    assert "auditpol /set" in script and str(root) in script


def test_service_collects_samba_log(env, tmp_path, monkeypatch):
    c, app, engine, sid, root = env
    monkeypatch.setattr(activity.system, "IS_WINDOWS", False)
    monkeypatch.setattr(smb, "samba_shares", lambda: [{"name": "Commun", "path": str(root)}])
    log = tmp_path / "audit.log"
    log.write_text("")
    engine.db.set_setting("security", {"samba_log": str(log), "poll_sec": 60})
    sec = engine.security
    sec.run_once()
    assert sec.store.source("samba")["last_count"] == 0
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(log, "a") as fh:
        fh.write(f"{stamp} nas smbd_audit: alice|10.0.0.5|pc-alice|Commun|openat|ok|w|Contrats/bail.docx\n")
        fh.write(f"{stamp} nas smbd_audit: alice|10.0.0.5|pc-alice|Commun|unlinkat|ok|Contrats/bail.docx\n")
    sec.run_once()
    rows = engine.db.query("SELECT * FROM user_events WHERE source='samba'")
    assert [r["action"] for r in rows] == ["write", "delete"] and rows[0]["share_id"] == sid
    sec.run_once()                                  # signet : rien n'est relu
    assert len(engine.db.query("SELECT * FROM user_events WHERE source='samba'")) == 2


def test_demo_activity_is_detected(tmp_path):
    """Le jeu de démonstration contient des incidents que les vrais détecteurs repèrent (et rien du poste lui-même)."""
    from vigiefs import demo
    from vigiefs.ueba import Analyzer
    data = tmp_path / "data-demo"
    demo.create(str(data))
    app = create_app(str(data), start_engine=False)
    engine = app.state.engine
    cfg = engine.db.get_setting("security")
    assert cfg["collect_windows"] is False and cfg["smb_sampling"] is False
    assert engine.db.query_one("SELECT COUNT(DISTINCT user) AS n FROM user_events")["n"] >= 15
    engine.security.analyzer = Analyzer(engine, engine.security.store)
    found = {(f["detector"], f["user"]) for f in engine.security.analyzer.run(time.time() - 2 * DAY, time.time())}
    for expected in (("mass_read", "cabinet\\bob"), ("mass_delete", "cabinet\\carole"), ("denied", "cabinet\\stagiaire"),
                     ("brute_force", "cabinet\\direction"), ("spray", "poste:10.0.6.66"),
                     ("dormant", "cabinet\\ancien.associe"), ("ransomware", "leroy\\paul"),
                     ("audit", "leroy\\admin.tmp"), ("admin_group", "leroy\\admin.tmp")):
        assert expected in found, expected
    assert not any(u in ("cabinet\\alice", "leroy\\julie") for _, u in found)      # activité normale : rien
    top = engine.db.query("SELECT user FROM user_profiles ORDER BY risk DESC LIMIT 3")
    assert {r["user"] for r in top} == {"leroy\\admin.tmp", "leroy\\paul", "cabinet\\bob"}
