"""Connecteur RepareDocs : protocole --web, traitements en masse et individuels, résultats, sécurité."""

import os
import shutil
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vigiefs.api import create_app  # noqa: E402
from vigiefs.reparedocs import RepareDocs, clean_options  # noqa: E402

PASSWORD = "Sup3r-Secret!"
FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_reparedocs")


def wait_run(c, run_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        st = c.get(f"/api/reparedocs/runs/{run_id}").json()
        if st["status"] != "running":
            return st
        time.sleep(0.1)
    raise AssertionError("traitement RepareDocs trop long")


@pytest.fixture
def env(tmp_path):
    app = create_app(str(tmp_path / "data"), start_engine=False)
    c = TestClient(app)
    c.headers.update({"X-VigieFS": "1"})
    c.post("/api/auth/setup", json={"username": "admin", "password": PASSWORD})
    c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    c.put("/api/settings/repair", json={"reparedocs_dir": FAKE})
    root = tmp_path / "partage"
    (root / "Contrats").mkdir(parents=True)
    (root / "Contrats" / "contrat abime.docx").write_bytes(b"PK abime")
    (root / "Contrats" / "rapport.docx").write_bytes(b"PK sain")
    (root / "vieux perdu.pdf").write_bytes(b"%PDF perdu")
    (root / "note.txt").write_text("ignoré")
    cid = c.post("/api/clients", json={"name": "ACME"}).json()["id"]
    sid = c.post("/api/shares", json={"client_id": cid, "name": "Commun", "path": str(root),
                                      "scan_interval_min": 0, "monitor_enabled": False}).json()["id"]
    return c, app, sid, root


def test_options_are_validated():
    o = clean_options({"workers": 999, "kinds": ["pdf", "exe"], "use_disk": "oui", "inconnue": 1})
    assert o["workers"] == 32 and o["kinds"] == ["pdf"] and o["use_disk"] is True and "inconnue" not in o
    assert clean_options({"use_disk": True}, individual=True)["use_disk"] is False


def test_engine_missing(tmp_path):
    app = create_app(str(tmp_path / "d"), start_engine=False)
    c = TestClient(app)
    c.headers.update({"X-VigieFS": "1"})
    c.post("/api/auth/setup", json={"username": "admin", "password": PASSWORD})
    c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert c.get("/api/reparedocs/info").json()["available"] is False
    r = c.post("/api/reparedocs/upload?filename=x.docx", content=b"PK")
    assert r.status_code == 400 and "introuvable" in r.json()["detail"]
    # Copie partielle (lanceur seul, sans app\repare_docs) : signalée précisément, rien n'est lancé.
    partial = tmp_path / "RepareDocs"
    (partial / "app").mkdir(parents=True)
    shutil.copy(os.path.join(FAKE, "app", "RepareDocs.py"), partial / "app" / "RepareDocs.py")
    c.put("/api/settings/repair", json={"reparedocs_dir": str(partial)})
    info = c.get("/api/reparedocs/info").json()
    assert info["available"] is False and info["dir"] == str(partial) and "incomplet" in info["error"]
    assert "repare_docs" in info["error"]


def test_mass_run_results_report_and_restore(env):
    c, app, sid, root = env
    info = c.get("/api/reparedocs/info").json()
    assert info["available"] and info["version"] == "2.5-test" and info["phases"][0] == "Recensement"
    assert info["defaults"]["workers"] == 3 and info["tools"]["office"] == {"word": False, "excel": False,
                                                                            "powerpoint": False}
    run_id = c.post("/api/reparedocs/runs", json={"share_id": sid, "options": {"workers": 2}}).json()["run_id"]
    st = wait_run(c, run_id)
    assert st["status"] == "done" and st["summary"] == {"OK": 1, "REPARE": 1, "IRRECUPERABLE": 1}
    assert st["logs"] and st["done"]["summary"]["REPARE"] == 1
    res = c.get(f"/api/reparedocs/runs/{run_id}/results").json()
    assert res["total"] == 3 and res["counts"]["REPARE"] == 1
    fixed = next(r for r in res["rows"] if r["status"] == "REPARE")
    assert fixed["output_rel"] == "1-REPARES/Contrats/contrat abime.docx"
    only = c.get(f"/api/reparedocs/runs/{run_id}/results?status=IRRECUPERABLE").json()
    assert only["total"] == 1 and only["rows"][0]["rel"] == "vieux perdu.pdf"
    f = c.get(f"/api/reparedocs/runs/{run_id}/sortie/{fixed['output_rel']}")
    assert f.status_code == 200 and f.content == b"REPARE:PK abime"
    rep = c.get(f"/api/reparedocs/runs/{run_id}/sortie/RAPPORT.html")
    assert rep.status_code == 200 and rep.headers["content-security-policy"].startswith("sandbox")
    listing = c.get(f"/api/reparedocs/runs/{run_id}/sortie/1-REPARES/")
    assert "Contrats/" in listing.text and "sandbox" in listing.headers["content-security-policy"]
    assert c.get(f"/api/reparedocs/runs/{run_id}/sortie/../../vigiefs.db").status_code in (404, 422)
    assert c.get(f"/api/reparedocs/runs/{run_id}/sortie/1-REPARES/..%2F..%2Fsecret").status_code == 404
    # Remise en place : confirmation exigée, originaux conservés par le moteur.
    assert c.post(f"/api/reparedocs/runs/{run_id}/restore", json={"confirm": "oui"}).status_code == 400
    new_id = c.post(f"/api/reparedocs/runs/{run_id}/restore", json={"confirm": "REMETTRE"}).json()["run_id"]
    st = wait_run(c, new_id)
    # Comme le vrai moteur, la remise en place ne publie que son journal : le bilan en est extrait.
    assert st["kind"] == "restaurer" and st["status"] == "done" and st["error"] is None
    assert st["summary"] == {"remis": 1, "ignores": 0, "erreurs": 0}
    assert (root / "Contrats" / "contrat abime.docx").read_bytes() == b"REPARE:PK abime"
    out = st["output"]
    assert open(os.path.join(out, "3-ORIGINAUX_ENDOMMAGES", "Contrats", "contrat abime.docx"), "rb").read() == b"PK abime"
    runs = c.get(f"/api/reparedocs/runs?share_id={sid}").json()
    assert [r["kind"] for r in runs] == ["restaurer", "masse"]


def test_stop_and_single_mass_run(env, monkeypatch):
    c, app, sid, root = env
    monkeypatch.setenv("FAKE_RD_DELAY", "1.5")
    run_id = c.post("/api/reparedocs/runs", json={"share_id": sid}).json()["run_id"]
    time.sleep(0.5)
    busy = c.post("/api/reparedocs/runs", json={"share_id": sid})
    assert busy.status_code == 400 and "déjà en cours" in busy.json()["detail"]
    assert c.post(f"/api/reparedocs/runs/{run_id}/stop").json()["ok"]
    st = wait_run(c, run_id)
    assert st["status"] == "stopped"


def test_output_rules(env, tmp_path, monkeypatch):
    c, app, sid, root = env
    r = c.post("/api/reparedocs/runs", json={"source": str(root), "output": str(root / "sortie")})
    assert r.status_code == 400 and "ne doit pas se trouver dans le dossier analysé" in r.json()["detail"]
    # Sortie par défaut : celle que RepareDocs propose sur un autre disque, stable pour un même dossier (reprise).
    monkeypatch.setenv("FAKE_RD_SUGGEST_ROOT", str(tmp_path / "autre-disque"))
    chk = c.post("/api/reparedocs/check", json={"share_id": sid, "path": "Contrats"}).json()
    expected = str(tmp_path / "autre-disque" / "RepareDocs-sorties" / "Commun - Contrats")
    assert chk["output"] == expected and chk["source_ok"] and chk["same_volume"] is False
    run_id = c.post("/api/reparedocs/runs", json={"share_id": sid, "path": "Contrats"}).json()["run_id"]
    st = wait_run(c, run_id)
    assert st["status"] == "done" and st["output"] == expected
    assert c.post("/api/reparedocs/check", json={"share_id": sid, "path": "Contrats"}).json()["previous"] is True


def test_same_volume_refused_unless_allowed(env, monkeypatch):
    c, app, sid, root = env
    monkeypatch.setenv("FAKE_RD_SAME_VOLUME", "1")
    assert c.post("/api/reparedocs/check", json={"share_id": sid}).json()["same_volume"] is True
    st = wait_run(c, c.post("/api/reparedocs/runs", json={"share_id": sid}).json()["run_id"])
    assert st["status"] == "failed" and "même disque" in st["error"]
    st = wait_run(c, c.post("/api/reparedocs/runs", json={"share_id": sid, "options": {"allow_same_volume": True}})
                  .json()["run_id"])
    assert st["status"] == "done" and st["summary"]["REPARE"] == 1
    # La réparation individuelle travaille sur une copie dans les données de VigieFS : toujours autorisée.
    st = wait_run(c, c.post("/api/reparedocs/upload?filename=lettre abime.docx", content=b"PK").json()["run_id"])
    assert st["status"] == "done" and st["options"]["allow_same_volume"] is True


def test_engine_crash_reports_console(tmp_path):
    # Moteur arrêté avant d'avoir publié son état (composant manquant…) : l'erreur cite sa dernière ligne de console.
    (tmp_path / "console.log").write_text("=== lancement\nTraceback (most recent call last):\n"
                                          "ModuleNotFoundError: No module named 'fitz'\n", encoding="utf-8")
    assert RepareDocs._console_tail(str(tmp_path)).endswith("No module named 'fitz'")
    assert RepareDocs._console_tail(str(tmp_path / "absent")) == ""


def test_individual_upload_and_share_file(env):
    c, app, sid, root = env
    run_id = c.post("/api/reparedocs/upload?filename=lettre abime.docx", content=b"PK lettre").json()["run_id"]
    st = wait_run(c, run_id)
    assert st["kind"] == "individuel" and st["status"] == "done" and st["options"]["use_disk"] is False
    row = c.get(f"/api/reparedocs/runs/{run_id}/results").json()["rows"][0]
    assert row["status"] == "REPARE"
    assert c.get(f"/api/reparedocs/runs/{run_id}/sortie/{row['output_rel']}").content == b"REPARE:PK lettre"
    # Fichier d'un partage : copie « (réparé) » puis remplacement (original vers la corbeille VigieFS).
    run_id = c.post(f"/api/shares/{sid}/reparedocs/file", json={"path": "Contrats/contrat abime.docx"}).json()["run_id"]
    assert wait_run(c, run_id)["status"] == "done"
    r = c.post(f"/api/reparedocs/runs/{run_id}/apply", json={"mode": "copy"}).json()
    assert r["path"] == "Contrats/contrat abime (réparé).docx"
    assert (root / "Contrats" / "contrat abime (réparé).docx").read_bytes() == b"REPARE:PK abime"
    assert (root / "Contrats" / "contrat abime.docx").read_bytes() == b"PK abime"
    r = c.post(f"/api/reparedocs/runs/{run_id}/apply", json={"mode": "replace"}).json()
    assert r["path"] == "Contrats/contrat abime.docx"
    assert (root / "Contrats" / "contrat abime.docx").read_bytes() == b"REPARE:PK abime"
    assert any(i["rel_path"].endswith("contrat abime.docx") for i in c.get("/api/recycle").json()["items"])
    assert c.post(f"/api/shares/{sid}/reparedocs/file", json={"path": "../x"}).status_code in (400, 403, 404)
