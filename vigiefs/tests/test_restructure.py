"""Réorganisation d'arborescence : proposition, 2 étapes sans perte de fichier, conflits, retour arrière."""

import hashlib
import os
import shutil
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vigiefs import restructure as rs  # noqa: E402
from vigiefs.api import create_app  # noqa: E402

PASSWORD = "Sup3r-Secret!"
LONG_PREFIX = "\\\\SRV-FICHIERS-CABINET-MARTIN\\Commun-Documents-partages-de-tout-le-cabinet"


def snapshot(root):
    """{chemin relatif: empreinte} de tous les fichiers."""
    out = {}
    for dp, _, files in os.walk(root):
        for f in files:
            p = os.path.join(dp, f)
            out[os.path.relpath(p, root).replace(os.sep, "/")] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


def write(root, rel, data=None, age_days=0):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data if data is not None else rel.encode() * 3)
    t = time.time() - age_days * 86400
    os.utime(p, (t, t))


def build(root):
    write(root, "Comptabilité/2024/Facture?.pdf")
    write(root, "Comptabilité/2024/Facture.pdf")
    write(root, "Projets/Projets/plan.docx")
    write(root, "Projets/Projets/Sous/x.txt")
    deep = ("Dossiers clients/SCI Les Tilleuls/Procédure prud'homale 2024 devant le conseil de Paris section "
            "commerce/Pièces communiquées par la partie adverse lors de l'audience")
    write(root, deep + "/Annexe 12 - Attestation de témoin rédigée par le gestionnaire.pdf")
    os.makedirs(os.path.join(root, "Vide", "Encore vide"))
    write(root, "Archives 2015/vieux.doc", age_days=3000)
    write(root, "Archives 2015/Sous/autre.xls", age_days=2900)
    write(root, "Direction/CR.docx")
    write(root, "Direction/cr.DOCX")
    write(root, "Direction/CON.txt")
    write(root, "lettre.docx")
    for i in range(12):
        write(root, f"Scans/scan{i}.pdf", age_days=400 * (i % 3))


@pytest.fixture
def env(tmp_path):
    app = create_app(str(tmp_path / "data"), start_engine=False)
    c = TestClient(app)
    c.headers.update({"X-VigieFS": "1"})
    c.post("/api/auth/setup", json={"username": "admin", "password": PASSWORD})
    c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    c.put("/api/settings/paths", json={"crowded_items": 10})
    c.put("/api/settings/restructure", json={"vss_snapshot": False})
    root = tmp_path / "Commun"
    build(str(root))
    cid = c.post("/api/clients", json={"name": "ACME"}).json()["id"]
    sid = c.post("/api/shares", json={"client_id": cid, "name": "Commun", "path": str(root),
                                      "access_path": LONG_PREFIX, "scan_interval_min": 0,
                                      "monitor_enabled": False}).json()["id"]
    return c, app, sid, str(root)


def wait_job(c, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError("tâche trop longue")


def propose(c, sid):
    j = wait_job(c, c.post(f"/api/shares/{sid}/restructure/propose").json()["job_id"])
    assert j["status"] == "done", j
    return j["result"]["plan_id"]


def test_names_and_shortening():
    assert rs.sanitize("Facture: mars?.pdf", False) == "Facture - mars.pdf"
    assert rs.sanitize("notes ", True) == "notes" and rs.sanitize("CON.txt", False) == "CON_.txt"
    assert rs.sanitize("fin.", True) == "fin"
    s = rs.shorten("Pièces communiquées par la partie adverse lors de l'audience", 30)
    assert len(s) <= 30 and s.startswith("Pièces")
    assert rs.shorten("Rapport annuel de la direction générale 2024.docx", 30, is_dir=False).endswith(".docx")


def test_proposal_covers_rules(env):
    c, app, sid, root = env
    pid = propose(c, sid)
    p = c.get(f"/api/restructure/{pid}").json()
    ops = p["data"]["ops"]
    kinds = {(o["rule"], o["type"]) for o in ops}
    assert {("empty", "remove_empty"), ("intermediate", "merge_up"), ("naming", "rename_file"),
            ("length", "rename_dir"), ("crowded", "move_files"), ("root", "move_files"),
            ("cold", "move_dir")} <= kinds
    names = {o["src"]: o.get("name") for o in ops if o["type"] in ("rename_file", "rename_dir")}
    assert names["Comptabilité/2024/Facture?.pdf"] == "Facture (2).pdf"      # jamais de doublon
    assert names["Direction/cr.DOCX"] == "cr (2).DOCX" and names["Direction/CON.txt"] == "CON_.txt"
    m = p["data"]["metrics"]
    assert m["before"]["long_paths"] >= 1 and m["after"]["long_paths"] == 0
    assert p["status"] == "draft" and not [x for x in p["data"]["problems"] if x["level"] == "error"]


def test_two_steps_without_loss_and_rollback(env):
    c, app, sid, root = env
    original = snapshot(root)
    pid = propose(c, sid)
    # Tout sélectionner (modèle de premier niveau compris) puis un nom choisi à la main.
    plan = c.put(f"/api/restructure/{pid}", json={"rules": {r: True for r in rs.RULES}}).json()
    rename = next(o for o in plan["data"]["ops"] if o["src"] == "Direction/CON.txt")
    plan = c.put(f"/api/restructure/{pid}", json={"ops": [{"id": rename["id"], "name": "CON-note.txt"}]}).json()
    assert not [x for x in plan["data"]["problems"] if x["level"] == "error"], plan["data"]["problems"]

    # Étape 2 impossible avant l'étape 1 ; étape 1 réservée aux administrateurs.
    assert c.post(f"/api/restructure/{pid}/execute", json={"confirm": "DÉPLACER"}).status_code == 409
    j = wait_job(c, c.post(f"/api/restructure/{pid}/prepare").json()["job_id"])
    assert j["status"] == "done", j
    assert snapshot(root) == original                         # étape 1 : aucun fichier déplacé
    assert os.path.isdir(os.path.join(root, "_Archives", "Archives 2015"))
    assert os.path.isdir(os.path.join(root, "06 - Projets", "Sous"))
    assert c.get(f"/api/restructure/{pid}").json()["status"] == "prepared"

    assert c.post(f"/api/restructure/{pid}/execute", json={"confirm": "oui"}).status_code == 400
    j = wait_job(c, c.post(f"/api/restructure/{pid}/execute", json={"confirm": "DÉPLACER"}).json()["job_id"])
    assert j["status"] == "done", j
    assert j["result"]["inventory_ok"] and j["result"]["kept"] == 0
    after = snapshot(root)
    # Même contenu, aucun fichier perdu ni dupliqué : les empreintes sont identiques.
    assert sorted(after.values()) == sorted(original.values())
    plan = c.get(f"/api/restructure/{pid}").json()
    assert plan["status"] == "done"
    m = rs.Mapping(plan["data"])
    for rel, digest in original.items():
        assert after.get(m.file(rel)) == digest, rel
    assert "06 - Projets/plan.docx" in after and "Projets" not in os.listdir(root)
    assert after["_Archives/Archives 2015/Sous/autre.xls"] == original["Archives 2015/Sous/autre.xls"]
    assert "Direction/CON-note.txt" not in after and "01 - Direction/CON-note.txt" in after
    assert not os.path.exists(os.path.join(root, "Vide"))
    assert any(k.startswith("_A classer/") for k in after)
    assert any(k.startswith("90 - Echanges/Scans/20") for k in after)
    assert os.path.isfile(plan["result"]["execute"]["manifest"])
    assert c.delete(f"/api/restructure/{pid}").status_code == 409     # conservé pour le retour arrière

    # Retour arrière : la structure d'origine revient exactement.
    assert c.post(f"/api/restructure/{pid}/rollback", json={"confirm": "non"}).status_code == 400
    j = wait_job(c, c.post(f"/api/restructure/{pid}/rollback", json={"confirm": "ANNULER"}).json()["job_id"])
    assert j["status"] == "done", j
    assert snapshot(root) == original
    assert c.get(f"/api/restructure/{pid}").json()["status"] == "rolled_back"


def test_conflicts_are_never_overwritten(env):
    c, app, sid, root = env
    pid = propose(c, sid)
    c.put(f"/api/restructure/{pid}", json={"rules": {"cold": True}})
    assert wait_job(c, c.post(f"/api/restructure/{pid}/prepare").json()["job_id"])["status"] == "done"
    # Un fichier différent apparaît à la destination entre l'étape 1 et l'étape 2.
    write(root, "_Archives/Archives 2015/vieux.doc", b"AUTRE CONTENU")
    j = wait_job(c, c.post(f"/api/restructure/{pid}/execute", json={"confirm": "DEPLACER"}).json()["job_id"])
    assert j["status"] == "done" and j["result"]["kept"] == 1 and j["result"]["status"] == "partial"
    assert open(os.path.join(root, "_Archives/Archives 2015/vieux.doc"), "rb").read() == b"AUTRE CONTENU"
    assert open(os.path.join(root, "Archives 2015/vieux.doc"), "rb").read() == b"Archives 2015/vieux.doc" * 3
    plan = c.get(f"/api/restructure/{pid}").json()
    kept = [k for r in plan["result"]["execute"]["ops"].values() for k in r.get("kept", [])]
    assert kept and "conflit" in kept[0]["reason"]


def test_selection_change_requires_new_step1(env):
    c, app, sid, root = env
    pid = propose(c, sid)
    assert wait_job(c, c.post(f"/api/restructure/{pid}/prepare").json()["job_id"])["status"] == "done"
    c.put(f"/api/restructure/{pid}", json={"rules": {"root": True}})
    assert c.get(f"/api/restructure/{pid}").json()["status"] == "draft"
    assert c.post(f"/api/restructure/{pid}/execute", json={"confirm": "DÉPLACER"}).status_code == 409
    bad = next(o for o in c.get(f"/api/restructure/{pid}").json()["data"]["ops"] if o["type"] == "rename_file")
    plan = c.put(f"/api/restructure/{pid}", json={"ops": [{"id": bad["id"], "name": "mauvais?.pdf"}]}).json()
    assert any(p["level"] == "error" for p in plan["data"]["problems"])
    j = wait_job(c, c.post(f"/api/restructure/{pid}/prepare").json()["job_id"])
    assert j["status"] == "failed" and "bloquant" in j["error"]


def test_tree_and_script(env):
    c, app, sid, root = env
    pid = propose(c, sid)
    c.put(f"/api/restructure/{pid}", json={"rules": {"template": True}})
    t = c.get(f"/api/restructure/{pid}/tree").json()
    assert any(n["path"] == "06 - Projets" and n["changed"] for n in t["after"])
    assert any(n["path"] == "Projets" for n in t["before"])
    before = set(os.listdir(os.getcwd()))
    s = c.get(f"/api/restructure/{pid}/script")
    text = s.content.decode("utf-8-sig")
    assert set(os.listdir(os.getcwd())) == before          # générer le script n'écrit rien
    assert "New-Item -ItemType Directory" in text and "robocopy" in text
    assert "/XC /XN /XO" in text and "/MOV" not in text and "/PURGE" not in text and "/MIR" not in text
    pwsh = shutil.which("pwsh")
    if pwsh:
        path = os.path.join(root, "..", "script.ps1")
        open(path, "w", encoding="utf-8-sig").write(text)
        r = subprocess.run([pwsh, "-NoProfile", "-Command",
                            f"$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('{path}',"
                            "[ref]$null,[ref]$e);$e.Count"], capture_output=True, text=True)
        assert r.stdout.strip() == "0"


def test_robocopy_command_is_safe(tmp_path):
    mv = rs.Mover(str(tmp_path), str(tmp_path / "logs"), {"threads": 16}, use_robocopy=True)
    cmd = mv.robocopy_cmd("D:\\Partages\\A", "D:\\Partages\\B", exclude_dirs=["D:\\Partages\\A\\X"])
    assert cmd[:3] == ["robocopy", "D:\\Partages\\A", "D:\\Partages\\B"]
    for flag in ("/E", "/COPY:DATS", "/DCOPY:DAT", "/XC", "/XN", "/XO", "/XJ", "/MT:16"):
        assert flag in cmd
    assert not {"/MOV", "/MOVE", "/MIR", "/PURGE"} & set(cmd)
    assert cmd[cmd.index("/XD") + 1] == "D:\\Partages\\A\\X"
    assert rs.Mover(str(tmp_path), str(tmp_path), {}, use_robocopy=True).robocopy_cmd("D:\\", "E:\\x")[1] == "D:\\."


def test_monitor_quiet_suppresses_burst(env):
    c, app, sid, root = env
    engine = app.state.engine
    db = app.state.db
    db.set_setting("ransomware", {"burst_threshold": 5, "burst_window_sec": 60})
    share = db.query_one("SELECT * FROM shares WHERE id=?", (sid,))
    engine.monitor._shares[sid] = share
    engine.monitor.quiet.add(sid)
    batch = [(sid, time.time(), "moved", os.path.join(root, f"f{i}.pdf"), os.path.join(root, f"g{i}.pdf"), False)
             for i in range(20)]
    engine.monitor.process_batch(batch)
    assert not db.query_one("SELECT id FROM alerts WHERE fingerprint=?", (f"burst:{sid}",))
    engine.monitor.quiet.discard(sid)
    engine.monitor.process_batch(batch)
    assert db.query_one("SELECT id FROM alerts WHERE fingerprint=?", (f"burst:{sid}",))
