import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vigiefs.alerts import AlertManager  # noqa: E402
from vigiefs.api import create_app  # noqa: E402
from vigiefs.db import Database  # noqa: E402
from vigiefs.engine import Engine  # noqa: E402
from vigiefs.rules import Matcher, growth_per_day, normalize_extensions, quota_state, safe_join  # noqa: E402
from vigiefs.optimize import find_duplicates  # noqa: E402
from vigiefs.scanner import scan_tree  # noqa: E402

PASSWORD = "Sup3r-Secret!"


def write(path, size=10, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    if mtime:
        os.utime(path, (mtime, mtime))


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "partage"
    write(str(root / "compta" / "bilan.xlsx"), 1000)
    write(str(root / "compta" / "vieux.pdf"), 5000, mtime=time.time() - 800 * 86400)
    write(str(root / "rh" / "setup.exe"), 300)
    write(str(root / "racine.txt"), 50)
    return root


def wait_for(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        res = fn()
        if res:
            return res
        time.sleep(0.2)
    return None


# ------------------------------------------------------------------ règles
def test_normalize_extensions():
    assert normalize_extensions("exe, .BAT;msi\n.exe") == [".exe", ".bat", ".msi"]


def test_matcher_extensions_and_patterns():
    m = Matcher([".locky"], ["*how_to_decrypt*"])
    assert m.match("facture.pdf.LOCKY")
    assert m.match("HOW_TO_DECRYPT.html")
    assert not m.match("facture.pdf")


def test_quota_state():
    share = {"quota_bytes": 1000, "quota_warn_pct": 80, "quota_crit_pct": 95}
    assert quota_state(share, 500) == (None, 50.0)
    assert quota_state(share, 850)[0] == "warning"
    assert quota_state(share, 990)[0] == "critical"
    assert quota_state({"quota_bytes": 0}, 10) == (None, None)


def test_growth_per_day():
    day = 86400
    pts = [(0, 100), (day, 200), (2 * day, 300)]
    assert round(growth_per_day(pts)) == 100
    assert growth_per_day([(0, 1)]) is None


def test_safe_join_blocks_traversal(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    assert safe_join(str(root), "a/b").startswith(str(root.resolve()))
    with pytest.raises(ValueError):
        safe_join(str(root), "../../etc")


# ------------------------------------------------------------------ analyse
def test_scan_tree_report(tree):
    report = scan_tree(str(tree), stale_days=365, screening=Matcher([".exe"]))
    assert report["file_count"] == 4
    assert report["dir_count"] == 2
    assert report["total_size"] == 6350
    assert report["stale_count"] == 1 and report["stale_size"] == 5000
    assert [f["path"] for f in report["screened"]] == ["rh/setup.exe"]
    assert report["largest"][0]["path"] == "compta/vieux.pdf"
    folders = {f["name"]: f["size"] for f in report["top_folders"]}
    assert folders["compta"] == 6000 and folders["(racine)"] == 50
    assert sum(a["count"] for a in report["ages"]) == 4


def test_find_duplicates(tmp_path):
    for name in ("a.bin", "sous/b.bin"):
        write(str(tmp_path / name), 2048)
    write(str(tmp_path / "c.bin"), 4096)
    res = find_duplicates([{"id": 1, "name": "P", "path": str(tmp_path)}], min_size=1024)
    assert len(res["groups"]) == 1
    assert res["wasted_total"] == 2048
    assert sorted(f["path"] for f in res["groups"][0]["files"]) == ["a.bin", "sous/b.bin"]


# ------------------------------------------------------------------ alertes & moteur
@pytest.fixture
def core(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    alerts = AlertManager(db)
    engine = Engine(db, alerts)
    cid = db.execute("INSERT INTO clients(name, created_at) VALUES('ACME', ?)", (time.time(),))
    return db, alerts, engine, cid


def add_share(db, cid, path, **kw):
    cols = {"client_id": cid, "name": kw.pop("name", "Partage"), "path": str(path), "created_at": time.time(),
            "scan_interval_min": 0, "monitor_enabled": 0}
    cols.update(kw)
    sid = db.execute(f"INSERT INTO shares({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                     tuple(cols.values()))
    return db.query_one("SELECT * FROM shares WHERE id=?", (sid,))


def test_alert_dedup_escalation_and_resolve(core):
    db, alerts, _, _ = core
    a1, created = alerts.raise_alert("fp", "warning", "quota", "t", "m", notify=False)
    assert created
    alerts.acknowledge(a1, "tech")
    a2, created = alerts.raise_alert("fp", "warning", "quota", "t", "m", notify=False)
    assert a1 == a2 and not created
    row = db.query_one("SELECT * FROM alerts WHERE id=?", (a1,))
    assert row["count"] == 2 and row["status"] == "acknowledged"
    alerts.raise_alert("fp", "critical", "quota", "t", "m", notify=False)
    assert db.query_one("SELECT status, severity FROM alerts WHERE id=?", (a1,)) == \
        {"status": "open", "severity": "critical"}
    alerts.resolve("fp")
    assert db.query_one("SELECT status FROM alerts WHERE id=?", (a1,))["status"] == "resolved"


def test_engine_scan_raises_quota_screening_and_ransomware(core, tree):
    db, alerts, engine, cid = core
    write(str(tree / "compta" / "bilan.xlsx.locky"), 10)
    share = add_share(db, cid, tree, quota_bytes=6000, blocked_extensions="")
    engine.scan_share(share)
    cats = {a["category"]: a for a in db.query("SELECT * FROM alerts WHERE status='open'")}
    assert cats["quota"]["severity"] == "critical"
    assert cats["screening"]["severity"] == "warning"
    assert cats["ransomware"]["severity"] == "critical"
    updated = engine.share(share["id"])
    assert updated["last_file_count"] == 5 and updated["last_scan_status"] == "ok"

    # Correction des causes → résolution automatique à l'analyse suivante.
    os.remove(tree / "rh" / "setup.exe")
    os.remove(tree / "compta" / "bilan.xlsx.locky")
    db.execute("UPDATE shares SET quota_bytes=? WHERE id=?", (10 ** 9, share["id"]))
    engine.scan_share(engine.share(share["id"]))
    assert db.query("SELECT * FROM alerts WHERE status='open' AND category IN ('quota','screening','ransomware')") == []


def test_engine_quarantine(core, tree, tmp_path):
    db, _, engine, cid = core
    qdir = tmp_path / "quarantaine"
    db.set_setting("screening", {"quarantine_dir": str(qdir)})
    share = add_share(db, cid, tree, screening_action="quarantine", blocked_extensions=".exe")
    engine.scan_share(share)
    assert not (tree / "rh" / "setup.exe").exists()
    moved = list(qdir.rglob("setup.exe"))
    assert len(moved) == 1


def test_missing_path_alert(core, tmp_path):
    db, _, engine, cid = core
    share = add_share(db, cid, tmp_path / "absent")
    assert engine.check_availability(share) is False
    assert db.query_one("SELECT category, severity FROM alerts") == {"category": "path", "severity": "critical"}
    (tmp_path / "absent").mkdir()
    assert engine.check_availability(share) is True
    assert db.query_one("SELECT status FROM alerts")["status"] == "resolved"


def test_monitor_burst_detection(core, tree):
    db, _, engine, cid = core
    db.set_setting("ransomware", {"burst_threshold": 50, "burst_window_sec": 60})
    share = add_share(db, cid, tree)
    engine.monitor._shares[share["id"]] = share
    now = time.time()
    batch = [(share["id"], now, "modified", str(tree / f"f{i}.docx"), None, False) for i in range(60)]
    engine.monitor.process_batch(batch)
    assert db.query_one("SELECT COUNT(*) AS n FROM events")["n"] == 60
    alert = db.query_one("SELECT * FROM alerts WHERE fingerprint=?", (f"burst:{share['id']}",))
    assert alert and alert["severity"] == "critical"


# ------------------------------------------------------------------ API
@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "data"), start_engine=True)
    with TestClient(app) as c:
        c.headers.update({"X-VigieFS": "1"})
        yield c


def login(c):
    assert c.get("/api/auth/status").json()["setup_required"] is True
    assert c.post("/api/auth/setup", json={"username": "admin", "password": "faible"}).status_code == 400
    assert c.post("/api/auth/setup", json={"username": "admin", "password": PASSWORD}).status_code == 200
    assert c.post("/api/auth/setup", json={"username": "autre", "password": PASSWORD}).status_code == 409
    r = c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert r.status_code == 200


def test_api_requires_auth(client):
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/v1/status", headers={"X-API-Key": "faux"}).status_code == 401


def test_api_full_flow(client, tree):
    login(client)
    cid = client.post("/api/clients", json={"name": "ACME"}).json()["id"]
    assert client.post("/api/clients", json={"name": "ACME"}).status_code == 409

    r = client.post("/api/shares", json={"client_id": cid, "name": "Data", "path": "relatif"})
    assert r.status_code == 400
    sid = client.post("/api/shares", json={"client_id": cid, "name": "Data", "path": str(tree),
                                           "quota_bytes": 10 ** 6}).json()["id"]

    share = wait_for(lambda: (lambda s: s if s["last_scan_status"] == "ok" else None)(
        client.get(f"/api/shares/{sid}").json()))
    assert share and share["last_file_count"] == 4
    assert share["monitor_status"] == "active"

    report = client.get(f"/api/shares/{sid}/report").json()["scan"]["report"]
    assert report["file_count"] == 4

    listing = client.get(f"/api/shares/{sid}/browse", params={"path": "compta"}).json()
    assert {e["name"] for e in listing["entries"]} == {"bilan.xlsx", "vieux.pdf"}
    assert client.get(f"/api/shares/{sid}/browse", params={"path": "../.."}).status_code == 400
    assert client.get(f"/api/shares/{sid}/permissions", params={"path": "compta"}).json()["entries"]

    assert client.post(f"/api/shares/{sid}/mkdir", json={"path": "", "name": "a/b"}).status_code == 400
    assert client.post(f"/api/shares/{sid}/mkdir", json={"path": "", "name": "Nouveau"}).status_code == 200
    assert client.post(f"/api/shares/{sid}/rename", json={"path": "Nouveau", "new_name": "Archive"}).status_code == 200
    assert (tree / "Archive").is_dir()
    assert client.post(f"/api/shares/{sid}/delete", json={"path": ""}).status_code == 400
    assert client.post(f"/api/shares/{sid}/delete", json={"path": "Archive"}).status_code == 200

    # Surveillance temps réel : un fichier « chiffré » déclenche une alerte critique.
    write(str(tree / "rh" / "paie.xlsx.lockbit"), 20)
    alert = wait_for(lambda: [a for a in client.get("/api/alerts?status=open").json()
                              if a["fingerprint"] == f"ransomware:{sid}"])
    assert alert and alert[0]["severity"] == "critical"
    events = wait_for(lambda: client.get(f"/api/events?share_id={sid}").json())
    assert any(e["path"].endswith(".lockbit") for e in events)

    aid = alert[0]["id"]
    assert client.post(f"/api/alerts/{aid}/ack").status_code == 200
    assert client.get("/api/alerts?status=acknowledged").json()[0]["ack_by"] == "admin"

    dash = client.get("/api/dashboard").json()
    assert dash["counts"]["shares"] == 1 and len(dash["activity"]) == 24

    assert "ACME" in client.get("/api/reports/usage.csv").text

    # Clé d'API lecture seule pour le centre de supervision.
    key = client.post("/api/apikeys", json={"name": "rmm", "role": "viewer"}).json()["key"]
    anon = TestClient(client.app)
    st = anon.get("/api/v1/status", headers={"X-API-Key": key}).json()
    assert st["status"] in ("warning", "critical") and st["shares"][0]["name"] == "Data"
    assert "vigiefs_share_size_bytes" in anon.get("/metrics", headers={"Authorization": f"Bearer {key}"}).text
    assert anon.post("/api/clients", json={"name": "X"}, headers={"X-API-Key": key}).status_code == 403


def test_csrf_header_required(client):
    login(client)
    r = client.post("/api/clients", json={"name": "B"}, headers={"X-VigieFS": ""})
    assert r.status_code == 403


def test_roles_and_last_admin(client):
    login(client)
    assert client.post("/api/users", json={"username": "lecteur", "password": PASSWORD, "role": "viewer"}).status_code == 200
    me = client.get("/api/users").json()
    admin_id = [u for u in me if u["username"] == "admin"][0]["id"]
    assert client.put(f"/api/users/{admin_id}", json={"username": "admin", "role": "viewer"}).status_code == 400

    other = TestClient(client.app)
    other.headers.update({"X-VigieFS": "1"})
    assert other.post("/api/auth/login", json={"username": "lecteur", "password": PASSWORD}).status_code == 200
    assert other.get("/api/clients").status_code == 200
    assert other.post("/api/clients", json={"name": "Z"}).status_code == 403
    assert other.get("/api/settings").status_code == 403


def test_login_throttle(client):
    login(client)
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "admin", "password": "mauvais"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 429


def test_settings_mask_secrets(client):
    login(client)
    client.put("/api/settings/smtp", json={"host": "smtp.x", "password": "p@ss"})
    s = client.get("/api/settings").json()["smtp"]
    assert s["password"] == "********" and s["host"] == "smtp.x"
    client.put("/api/settings/smtp", json={"password": "********", "port": 465})
    assert client.app.state.db.get_setting("smtp")["password"] == "p@ss"
