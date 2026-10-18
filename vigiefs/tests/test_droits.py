import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vigiefs import acl, matrix as mx, pathcheck, sensitive  # noqa: E402
from vigiefs.api import create_app  # noqa: E402

PASSWORD = "Sup3r-Secret!"
HAS_ACL = subprocess.run(["which", "setfacl"], capture_output=True).returncode == 0
needs_acl = pytest.mark.skipif(not HAS_ACL, reason="setfacl indisponible")


def facl(*args):
    subprocess.run(["setfacl", *args], check=True)


def getfacl(path):
    return subprocess.run(["getfacl", "-p", path], capture_output=True, text=True).stdout


def wait_job(c, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.1)
    raise AssertionError("tâche trop longue")


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "partage"
    for d in ("Compta/2024", "RH/Paie", "Direction"):
        (root / d).mkdir(parents=True)
    (root / "Compta" / "bilan.txt").write_text("bilan")
    if HAS_ACL:
        facl("-m", "g:users:rwx", str(root / "Compta"))
        facl("-m", "u:nobody:rx", str(root / "RH"))
        os.chmod(root / "RH", 0o777)
    return root


@pytest.fixture
def env(tmp_path):
    app = create_app(str(tmp_path / "data"), start_engine=False)
    c = TestClient(app)
    c.headers.update({"X-VigieFS": "1"})
    c.post("/api/auth/setup", json={"username": "admin", "password": PASSWORD})
    c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    cid = c.post("/api/clients", json={"name": "ACME"}).json()["id"]
    return c, cid


def add_share(c, cid, path, **kw):
    body = {"client_id": cid, "name": kw.pop("name", "Partage"), "path": str(path), "scan_interval_min": 0,
            "monitor_enabled": False, "acl_interval_h": 0}
    body.update(kw)
    r = c.post("/api/shares", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ------------------------------------------------------------------ modèle NTFS (sortie PowerShell simulée)
WIN_DATA = {
    "nodes": [
        {"p": "D:\\Data", "d": 0, "o": "BUILTIN\\Administrators", "x": True, "a": [
            {"i": "BUILTIN\\Administrators", "m": 2032127, "t": "Allow", "h": False, "f": 3, "g": 0},
            {"i": "Everyone", "m": 1245631, "t": "Allow", "h": False, "f": 3, "g": 0},
            {"i": "CREATOR OWNER", "m": 268435456, "t": "Allow", "h": False, "f": 3, "g": 2}]},
        {"p": "D:\\Data\\Compta", "d": 1, "o": "S-1-5-21-1-2-3-1234", "x": False, "a": [
            {"i": "CORP\\jdupont", "m": 1179817, "t": "Allow", "h": False, "f": 3, "g": 0},
            {"i": "S-1-5-21-1-2-3-1234", "m": 1245631, "t": "Allow", "h": False, "f": 3, "g": 0},
            {"i": "CORP\\Stagiaires", "m": 278, "t": "Deny", "h": False, "f": 3, "g": 0},
            {"i": "BUILTIN\\Administrators", "m": 2032127, "t": "Allow", "h": True, "f": 3, "g": 0},
            {"i": "Everyone", "m": 1245631, "t": "Allow", "h": True, "f": 3, "g": 0}]},
        {"p": "D:\\Data\\Compta\\Paie\\2024", "d": 3, "err": "Accès refusé"},
    ],
    "principals": {
        "BUILTIN\\Administrators": {"sid": "S-1-5-32-544", "type": "group"},
        "Everyone": {"sid": "S-1-1-0", "type": "unknown"},
        "CREATOR OWNER": {"sid": "S-1-3-0", "type": "unknown"},
        "CORP\\jdupont": {"sid": "S-1-5-21-1-2-3-1001", "type": "user"},
        "CORP\\Stagiaires": {"sid": "S-1-5-21-1-2-3-1002", "type": "group"},
        "S-1-5-21-1-2-3-1234": {"sid": "S-1-5-21-1-2-3-1234", "type": "orphan"},
    },
}


def windows_snapshot():
    nodes, principals = acl.parse_windows(WIN_DATA, "D:\\Data")
    return {"platform": "windows", "root": "D:\\Data", "depth": 3, "created_at": time.time(),
            "nodes": nodes, "principals": principals}


def test_ntfs_masks_and_scopes():
    assert acl.level_from_mask(2032127) == "full"
    assert acl.level_from_mask(1245631) == "modify"
    assert acl.level_from_mask(1179817) == "read"
    assert acl.level_from_mask(-1610612736) == "read"  # GENERIC_READ|GENERIC_EXECUTE
    assert acl.level_from_mask(278) == "write"
    assert acl.scope_from_flags(3, 0) == "all" and acl.scope_from_flags(3, 2) == "sub_files_only"
    assert acl.scope_from_flags(0, 0) == "this" and acl.scope_from_flags(1, 0) == "this_sub"


def test_windows_audit_findings():
    snap = windows_snapshot()
    codes = {(f["code"], f["path"]) for f in acl.audit(snap)}
    assert ("open_write", "") in codes
    assert ("orphan", "Compta") in codes and ("owner_orphan", "Compta") in codes
    assert ("direct_user", "Compta") in codes and ("deny", "Compta") in codes
    assert ("no_system", "") in codes
    assert ("error", "Compta/Paie/2024") in codes
    assert ("no_admin", "") not in codes
    eff = acl.effective_levels(snap["nodes"][1])
    assert eff["CORP\\jdupont"] == "read" and eff["Everyone"] == "modify"
    summary = acl.summarize(snap, acl.audit(snap))
    assert summary["errors"] == 1 and summary["protected"] == 1 and 0 <= summary["score"] < 100


def test_windows_matrix_plan_and_script():
    snap = windows_snapshot()
    m = mx.propose(snap, 1, "ACME", "Data")
    names = [c["principal"] for c in m["columns"]]
    assert "BUILTIN\\Administrators" in names and "GDL_ACME_DATA_COMPTA_RW" in names
    rw = next(g for g in m["groups_plan"] if g["group"] == "GDL_ACME_DATA_COMPTA_RW")
    assert "Everyone" in rw["members"] and "Everyone" in rw["review"]
    assert mx.cell(m, "", "GDL_ACME_DATA_COMPTA_RO") == "list"  # parcours sur le parent
    p = mx.plan(m, snap)
    kinds = {(o["op"], o["path"], o.get("principal")) for o in p["ops"]}
    assert ("grant", "Compta", "GDL_ACME_DATA_COMPTA_RW") in kinds
    assert ("revoke", "", "Everyone") in kinds
    assert ("protect", "Compta", None) in kinds
    assert ("revoke", "Compta", "S-1-5-21-1-2-3-1234") in kinds
    script = mx.render_script(m, p, "D:\\Data", "Data", "admin")
    assert 'icacls D:\\Data\\Compta /grant:r "GDL_ACME_DATA_COMPTA_RW:(OI)(CI)(M)" /C' in script
    assert "icacls D:\\Data /remove:g Everyone /C" in script
    assert "*S-1-5-21-1-2-3-1234" in script and "/save" in script and "New-ADGroup" in script


def test_matrix_inheritance_conflict_forces_protection():
    snap = windows_snapshot()
    m = mx.build_current(snap, 1)
    m["rows"][1]["inherit"] = True
    m["cells"].pop(mx.key("Compta", "Everyone"), None)  # restreindre un droit hérité
    explicit = mx.desired_explicit(m)
    assert explicit["Compta"][1] is True and explicit["Compta"][2]


def test_matrix_csv_roundtrip_and_errors():
    m = mx.propose(windows_snapshot(), 1, "ACME", "Data")
    back = mx.import_csv(mx.export_csv(m), m)
    assert back["cells"] == m["cells"]
    edited = mx.export_csv(m).replace("Modification", "Contrôle total", 1)
    assert "full" in mx.import_csv(edited, m)["cells"].values()
    with pytest.raises(ValueError):
        mx.import_csv(mx.export_csv(m).replace("Lecture", "Peut-être", 1), m)


# ------------------------------------------------------------------ chemins et nommage
def test_path_rules():
    assert pathcheck.name_issues("rapport?.docx") == ["invalid_char"]
    assert "reserved" in pathcheck.name_issues("CON.txt")
    assert "edge_space" in pathcheck.name_issues("dossier ")
    assert "cloud_blocked" in pathcheck.name_issues("desktop.ini")
    assert pathcheck.name_issues("Facture 2024.pdf") == []
    pa = pathcheck.PathAnalyzer("\\\\SRV\\Partage", {"warn_len": 30, "crit_len": 40})
    pa.directory("")
    pa.directory("tres-long-dossier-de-projet")
    pa.file("tres-long-dossier-de-projet/fichier-au-nom-tres-long.docx", "fichier-au-nom-tres-long.docx", 10, 0)
    pa.listing("tres-long-dossier-de-projet", ["A.txt", "a.TXT"])
    r = pa.report()
    assert r["over_crit"] >= 1 and r["naming"]["counts"]["case_conflict"] == 1
    stats = {s["rel_path"]: s for s in pa.folder_stats()}
    assert stats[""]["files"] == 1 and stats["tres-long-dossier-de-projet"]["size"] == 10


def test_architecture_and_long_path_alert(env, tmp_path):
    c, cid = env
    root = tmp_path / "archi"
    deep = root / "Projets clients 2024" / ("Dossier au nom particulièrement long " * 2).strip()
    deep.mkdir(parents=True)
    (deep / ("Compte rendu de réunion de chantier numéro " * 3 + ".docx")).write_text("x")
    (root / "CON.txt").write_text("x")
    (root / "rapport final ").write_text("x")
    sid = add_share(c, cid, root, access_path="\\\\SERVEUR-FICHIERS-01\\Partage-Commun\\" + "x" * 40)
    c.app.state.engine.scan_share(c.app.state.engine.share(sid))
    a = c.get(f"/api/shares/{sid}/architecture").json()
    assert a["metrics"]["over_crit"] >= 1 and a["metrics"]["naming_issues"] >= 2
    assert any(r["title"] == "Chemins trop longs" for r in a["recommendations"])
    assert {t["rel_path"] for t in a["tree"]} >= {"", "Projets clients 2024"}
    alerts = c.get("/api/alerts?status=open").json()
    assert any(x["category"] == "paths" and x["severity"] == "critical" for x in alerts)
    assert "chemin long" in c.get(f"/api/shares/{sid}/paths.csv").text
    assert c.get("/api/architecture").json()[0]["grade"] in "ABCDE"


# ------------------------------------------------------------------ droits POSIX réels + API
@needs_acl
def test_posix_collect_and_audit(tree):
    snap = acl.collect(str(tree), 3)
    nodes = {n["path"]: n for n in snap["nodes"]}
    assert set(nodes) >= {"", "Compta", "RH", "Compta/2024", "RH/Paie"}
    assert any(a["principal"] == "group:users" and a["level"] == "modify" for a in nodes["Compta"]["aces"])
    codes = {(f["code"], f["path"]) for f in acl.audit(snap)}
    assert ("open_write", "RH") in codes and ("direct_user", "RH") in codes
    rows = acl.effective_access(snap, "user:nobody", [])
    assert any(r["path"] == "RH" for r in rows)


@needs_acl
def test_rights_api_matrix_apply_and_restore(env, tree):
    c, cid = env
    sid = add_share(c, cid, tree, acl_depth=3)
    assert c.get(f"/api/shares/{sid}/acl").json()["snapshot"] is None
    job = wait_job(c, c.post(f"/api/shares/{sid}/acl/scan").json()["job_id"])
    assert job["status"] == "done", job
    data = c.get(f"/api/shares/{sid}/acl").json()
    assert data["summary"]["folders"] >= 5 and data["findings"]
    node = c.get(f"/api/shares/{sid}/acl/node", params={"path": "RH"}).json()
    assert any(a["principal"] == "other" and a["broad"] for a in node["aces"])
    assert c.get(f"/api/shares/{sid}/acl/principal", params={"name": "user:nobody"}).json()["access"]
    assert "group:users" in c.get(f"/api/shares/{sid}/acl/export.csv").text or "users" in c.get(f"/api/shares/{sid}/acl/export.csv").text

    mid = c.post(f"/api/shares/{sid}/matrices", json={"kind": "current", "depth": 1}).json()["id"]
    m = c.get(f"/api/matrices/{mid}").json()
    assert m["plan"]["ops"] == []  # la matrice « actuelle » ne demande aucun changement
    d = m["data"]
    d["cells"][mx.key("RH", "group:users")] = "read"
    d["cells"].pop(mx.key("RH", "user:nobody"), None)
    d["cells"].pop(mx.key("RH", "other"), None)
    if not any(col["principal"] == "group:users" for col in d["columns"]):
        d["columns"].append({"principal": "group:users", "ptype": "group", "system": False, "broad": False})
    assert c.put(f"/api/matrices/{mid}", json={"data": d}).status_code == 200
    plan = c.get(f"/api/matrices/{mid}").json()["plan"]
    assert {o["op"] for o in plan["ops"]} == {"grant", "revoke"}
    assert "setfacl" in c.get(f"/api/matrices/{mid}/script").text
    assert c.get(f"/api/matrices/{mid}/export.xlsx").content[:2] == b"PK"
    assert c.post(f"/api/matrices/{mid}/apply", json={"confirm": False}).status_code == 400
    res = wait_job(c, c.post(f"/api/matrices/{mid}/apply", json={"confirm": True}).json()["job_id"])
    assert res["status"] == "done" and res["result"]["failed"] == 0, res
    out = getfacl(str(tree / "RH"))
    assert "group:users:r-x" in out and "user:nobody" not in out and "other::---" in out
    assert res["result"]["remaining_gaps"] == 0
    assert c.get(f"/api/shares/{sid}/matrices").json()[0]["status"] == "applied"
    diff = c.get(f"/api/shares/{sid}/acl/diff").json()
    assert any(ch["path"] == "RH" for ch in diff["changes"])

    # Dérive : modification manuelle après application → alerte
    facl("-m", "u:nobody:rwx", str(tree / "RH"))
    wait_job(c, c.post(f"/api/shares/{sid}/acl/scan").json()["job_id"])
    assert any(a["title"] == "Droits différents de la matrice" for a in c.get("/api/alerts?status=open").json())

    backups = c.get(f"/api/shares/{sid}/acl/backups").json()
    assert backups
    r = wait_job(c, c.post(f"/api/shares/{sid}/acl/restore", json={"name": backups[-1]["name"]}).json()["job_id"])
    assert r["status"] == "done", r
    assert "user:nobody:r-x" in getfacl(str(tree / "RH"))

    ops = wait_job(c, c.post(f"/api/shares/{sid}/acl/ops", json={"ops": [
        {"op": "grant", "path": "Direction", "principal": "group:users", "level": "read"}]}).json()["job_id"])
    assert ops["result"]["ok"] == 1 and "group:users:r-x" in getfacl(str(tree / "Direction"))


# ------------------------------------------------------------------ quotas stricts
@needs_acl
def test_hard_quota_lock_and_unlock(env, tmp_path):
    c, cid = env
    root = tmp_path / "q"
    (root / "Equipe").mkdir(parents=True)
    (root / "Equipe" / "gros.bin").write_bytes(b"x" * 300_000)
    sid = add_share(c, cid, root)
    r = c.post("/api/quotas", json={"share_id": sid, "rel_path": "Equipe", "limit_bytes": 200_000, "mode": "hard",
                                    "enforcement": "vigiefs"})
    assert r.status_code == 200, r.text
    state = r.json()["state"]
    assert state["level"] == "exceeded" and state["locked"]
    assert "mask::r-x" in getfacl(str(root / "Equipe"))
    q = c.get("/api/quotas").json()["quotas"][0]
    assert q["locks"] and q["effective_enforcement"] == "vigiefs"
    assert any("écriture bloquée" in a["title"] for a in c.get("/api/alerts?status=open").json())
    os.remove(root / "Equipe" / "gros.bin")
    state = c.post(f"/api/quotas/{q['id']}/check").json()
    assert not state["locked"] and state["level"] == "ok"
    assert "mask::rwx" in getfacl(str(root / "Equipe"))
    assert c.post("/api/quotas", json={"share_id": sid, "rel_path": "Absent", "limit_bytes": 10}).status_code == 400
    assert c.delete(f"/api/quotas/{q['id']}").status_code == 200


def test_auto_apply_quota_per_subfolder(env, tmp_path):
    c, cid = env
    root = tmp_path / "homes"
    for u, size in (("alice", 50_000), ("bob", 150_000)):
        (root / u).mkdir(parents=True)
        (root / u / "f.bin").write_bytes(b"x" * size)
    sid = add_share(c, cid, root)
    st = c.post("/api/quotas", json={"share_id": sid, "rel_path": "", "limit_bytes": 100_000, "mode": "soft",
                                     "auto_apply": True}).json()["state"]
    items = {i["path"]: i for i in st["items"]}
    assert items["alice"]["level"] == "ok" and items["bob"]["level"] == "exceeded" and not st["locked"]


# ------------------------------------------------------------------ données sensibles
def test_sensitive_validators():
    found, samples = sensitive.scan_text("NIR 2 91 01 75 018 002 68, faux 2 91 01 75 018 002 11 ; "
                                         "IBAN FR76 3000 6000 0112 3456 7890 189 ; CB 4012 8888 8888 1881 ; "
                                         "password = Azerty123")
    assert found == {"nir": 1, "iban": 1, "card": 1, "credentials": 1}
    assert "68" in samples["nir"][0] and "018" not in samples["nir"][0]


def test_sensitive_job_and_exposure(env, tmp_path):
    c, cid = env
    root = tmp_path / "s"
    (root / "Public").mkdir(parents=True)
    (root / "Public" / "salaries.csv").write_text("nom;nir\nDupont;2 91 01 75 018 002 68\n")
    (root / "Public" / "notes.txt").write_text("rien de sensible")
    os.chmod(root / "Public", 0o755)
    sid = add_share(c, cid, root, acl_depth=2)
    wait_job(c, c.post(f"/api/shares/{sid}/acl/scan").json()["job_id"])
    job = wait_job(c, c.post(f"/api/shares/{sid}/sensitive").json()["job_id"])
    res = job["result"]
    assert res["scanned"] == 2 and res["flagged"] == 1 and res["results"][0]["found"]["nir"] == 1
    assert res["exposed_high"] == 1
    assert any(a["category"] == "sensitive" and a["severity"] == "critical" for a in c.get("/api/alerts?status=open").json())
    assert c.get("/api/sensitive").json()["shares"][0]["high"] == 1


# ------------------------------------------------------------------ santé, SMB
def test_health_and_smb_endpoints(env, tmp_path):
    c, cid = env
    add_share(c, cid, tmp_path)
    h = c.get("/api/health/server").json()
    assert h["checks"] and any(x["group"] == "Dossiers supervisés" for x in h["checks"])
    smb = c.get("/api/smb/shares").json()
    assert "available" in smb
    opened = c.get("/api/smb/open-files").json()
    assert "files" in opened and "sessions" in opened
    assert c.post("/api/smb/open-files/12/close").status_code == 400  # Samba / non Windows
