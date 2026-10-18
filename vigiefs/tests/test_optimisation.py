import io
import os
import sys
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vigiefs import docrepair  # noqa: E402
from vigiefs.api import create_app  # noqa: E402
from vigiefs.optimize import JunkClassifier  # noqa: E402
from vigiefs.scanner import scan_tree  # noqa: E402

PASSWORD = "Sup3r-Secret!"
OLD = time.time() - 1000 * 86400


def write(path, data=b"x", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode())
    if mtime:
        os.utime(path, (mtime, mtime))


def wait_job(c, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.1)
    raise AssertionError("tâche trop longue")


@pytest.fixture
def env(tmp_path):
    app = create_app(str(tmp_path / "data"), start_engine=False)
    c = TestClient(app)
    c.headers.update({"X-VigieFS": "1"})
    c.post("/api/auth/setup", json={"username": "admin", "password": PASSWORD})
    c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    cid = c.post("/api/clients", json={"name": "ACME"}).json()["id"]
    return c, cid, tmp_path


def add_share(c, cid, path, name="Data", **kw):
    body = {"client_id": cid, "name": name, "path": str(path), "scan_interval_min": 0, "monitor_enabled": False}
    body.update(kw)
    r = c.post("/api/shares", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ------------------------------------------------------------------ fichiers inutiles
def test_junk_classifier():
    j = JunkClassifier({"temp_days": 2, "backup_days": 30, "logs_days": 90, "empty_days": 7})
    assert j.classify("~$rapport.docx", 162, OLD) == "temp"
    assert j.classify("~$rapport.docx", 162, time.time()) is None  # document peut-être ouvert
    assert j.classify("Thumbs.db", 5000, time.time()) == "system"
    assert j.classify("budget.xlsx.bak", 10, OLD) == "backup"
    assert j.classify("vide.txt", 0, OLD) == "empty"
    assert j.classify("desktop.ini", 0, OLD) is None
    assert j.classify("contrat.docx", 100, OLD) is None


def test_scan_reports_junk_and_empty_dirs(tmp_path):
    write(str(tmp_path / "a" / "~$doc.docx"), b"x" * 162, OLD)
    write(str(tmp_path / "a" / "Thumbs.db"), b"x" * 50)
    write(str(tmp_path / "a" / "vrai.docx"), b"x" * 50)
    os.makedirs(tmp_path / "vide")
    j = JunkClassifier({"temp_days": 2, "backup_days": 30, "logs_days": 90, "empty_days": 7})
    r = scan_tree(str(tmp_path), junk=j)
    assert r["junk"]["temp"]["count"] == 1 and r["junk"]["system"]["size"] == 50
    assert r["junk_total"]["count"] == 2
    assert r["empty_dirs"]["count"] == 1


def test_cleanup_to_recycle_and_restore(env):
    c, cid, tmp = env
    root = tmp / "partage"
    write(str(root / "compta" / "~$facture.xlsx"), b"x" * 165, OLD)
    write(str(root / "compta" / "facture.xlsx.bak"), b"x" * 1000, OLD)
    write(str(root / "compta" / "facture.xlsx"), b"x" * 1000, OLD)
    os.makedirs(root / "ancien" / "vide")
    os.utime(root / "ancien" / "vide", (OLD, OLD))
    os.utime(root / "ancien", (OLD, OLD))
    sid = add_share(c, cid, root)
    job = wait_job(c, c.post(f"/api/shares/{sid}/cleanup", json={"categories": ["temp", "backup", "empty_dirs"]}).json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["moved"] >= 3 and job["result"]["freed"] == 1165
    assert (root / "compta" / "facture.xlsx").exists()
    assert not (root / "compta" / "facture.xlsx.bak").exists()
    assert not (root / "ancien" / "vide").exists()

    items = c.get("/api/recycle").json()["items"]
    bak = [i for i in items if i["rel_path"].endswith(".bak")][0]
    assert c.post("/api/recycle/restore", json={"ids": [bak["id"]]}).json()["restored"] == 1
    assert (root / "compta" / "facture.xlsx.bak").exists()
    tmpf = [i for i in items if i["rel_path"].endswith("~$facture.xlsx")][0]
    assert c.post("/api/recycle/purge", json={"ids": [tmpf["id"]]}).json()["purged"] == 1
    assert c.post("/api/recycle/restore", json={"ids": [tmpf["id"]]}).json()["errors"]


def test_explorer_delete_goes_to_recycle(env):
    c, cid, tmp = env
    write(str(tmp / "p" / "doc.txt"), "bonjour")
    sid = add_share(c, cid, tmp / "p")
    assert c.post(f"/api/shares/{sid}/delete", json={"path": "doc.txt"}).status_code == 200
    assert not (tmp / "p" / "doc.txt").exists()
    item = c.get("/api/recycle").json()["items"][0]
    assert item["reason"] == "suppression manuelle"
    c.post("/api/recycle/restore", json={"ids": [item["id"]]})
    assert (tmp / "p" / "doc.txt").read_text() == "bonjour"


# ------------------------------------------------------------------ doublons
def test_duplicates_across_client_shares_and_resolve(env):
    c, cid, tmp = env
    blob = os.urandom(300_000)
    write(str(tmp / "s1" / "original.pdf"), blob, OLD)
    write(str(tmp / "s2" / "copie" / "original - Copie.pdf"), blob)
    write(str(tmp / "s2" / "autre.pdf"), os.urandom(300_000))
    s1 = add_share(c, cid, tmp / "s1", "S1")
    add_share(c, cid, tmp / "s2", "S2")
    job = wait_job(c, c.post(f"/api/clients/{cid}/duplicates", json={"min_size_mb": 0.1}).json()["job_id"])
    res = job["result"]
    assert res["group_count"] == 1 and res["wasted_total"] == 300_000
    assert {f["share_id"] for f in res["groups"][0]["files"]} == {s1, s1 + 1}

    r = wait_job(c, c.post(f"/api/jobs/{job['id']}/resolve-duplicates", json={"strategy": "oldest"}).json()["job_id"])
    assert r["result"]["removed"] == 1 and r["result"]["freed"] == 300_000
    assert (tmp / "s1" / "original.pdf").exists()
    assert not (tmp / "s2" / "copie" / "original - Copie.pdf").exists()


def test_resolve_skips_modified_copy(env):
    c, cid, tmp = env
    blob = os.urandom(200_000)
    write(str(tmp / "s" / "a.bin"), blob, OLD)
    write(str(tmp / "s" / "b.bin"), blob)
    sid = add_share(c, cid, tmp / "s")
    job = wait_job(c, c.post(f"/api/shares/{sid}/duplicates", json={"min_size_mb": 0.1}).json()["job_id"])
    write(str(tmp / "s" / "b.bin"), os.urandom(200_000))  # modifié entre-temps
    r = wait_job(c, c.post(f"/api/jobs/{job['id']}/resolve-duplicates", json={}).json()["job_id"])
    assert r["result"]["removed"] == 0 and r["result"]["skipped_count"] == 1
    assert (tmp / "s" / "b.bin").exists()


# ------------------------------------------------------------------ archivage à froid
def test_archive_simulate_run_restore(env):
    c, cid, tmp = env
    root = tmp / "p"
    write(str(root / "2019" / "bilan.pdf"), b"a" * 5000, OLD)
    write(str(root / "2019" / "photo.jpg"), b"b" * 3000, OLD)
    write(str(root / "courant.docx"), b"c" * 100)
    sid = add_share(c, cid, root, archive_age_days=365)
    sim = wait_job(c, c.post(f"/api/shares/{sid}/archive/simulate", json={}).json()["job_id"])
    assert sim["result"]["count"] == 2 and sim["result"]["size"] == 8000
    sim2 = wait_job(c, c.post(f"/api/shares/{sid}/archive/simulate", json={"extensions": ".pdf"}).json()["job_id"])
    assert sim2["result"]["count"] == 1

    assert c.post(f"/api/shares/{sid}/archive/run", json={}).status_code == 400  # pas de stockage configuré
    c.put("/api/settings/archive", json={"root": str(tmp / "froid")})
    run = wait_job(c, c.post(f"/api/shares/{sid}/archive/run", json={}).json()["job_id"])
    assert run["result"]["archived"] == 2, run
    archived = tmp / "froid" / "ACME" / "Data" / "2019" / "bilan.pdf"
    assert archived.read_bytes() == b"a" * 5000
    assert abs(archived.stat().st_mtime - OLD) < 2
    assert not (root / "2019" / "bilan.pdf").exists()
    stub = root / "2019" / "bilan.pdf [archivé].url"
    assert "URL=file:" in stub.read_text()

    items = c.get(f"/api/archive/items?share_id={sid}").json()
    rest = wait_job(c, c.post("/api/archive/restore", json={"ids": [i["id"] for i in items]}).json()["job_id"])
    assert rest["result"]["restored"] == 2
    assert (root / "2019" / "bilan.pdf").read_bytes() == b"a" * 5000
    assert not stub.exists() and not archived.exists()


def test_archive_root_inside_share_refused(env):
    c, cid, tmp = env
    write(str(tmp / "p" / "f.txt"), "x", OLD)
    sid = add_share(c, cid, tmp / "p", archive_age_days=1)
    c.put("/api/settings/archive", json={"root": str(tmp / "p" / "archives")})
    job = wait_job(c, c.post(f"/api/shares/{sid}/archive/run", json={}).json()["job_id"])
    assert job["status"] == "failed" and "ne doit pas" in job["error"]


# ------------------------------------------------------------------ réparation de documents
def make_docx(path, n=30):
    docrepair.write_minimal_docx(str(path), [f"Paragraphe {i} — réparation éàç" for i in range(n)])


def rezip(src, dst, mutate):
    zin = zipfile.ZipFile(src)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zo:
        for n in zin.namelist():
            d = mutate(n, zin.read(n))
            if d is not None:
                zo.writestr(n, d)
    open(dst, "wb").write(buf.getvalue())


def test_docx_invalid_char_repaired(tmp_path):
    make_docx(tmp_path / "ok.docx")
    rezip(tmp_path / "ok.docx", tmp_path / "ko.docx",
          lambda n, d: d.replace(b"Paragraphe 3", b"Paragraphe \x0b3") if n == "word/document.xml" else d)
    assert docrepair.diagnose(str(tmp_path / "ok.docx"))["status"] == "ok"
    assert docrepair.diagnose(str(tmp_path / "ko.docx"))["status"] == "corrupt"
    r = docrepair.repair(str(tmp_path / "ko.docx"), str(tmp_path / "out"), libreoffice=None)
    assert r["status"] == "repaired" and r["after"]["status"] == "ok"
    text = zipfile.ZipFile(r["output"]).read("word/document.xml").decode()
    assert "Paragraphe 3" in text and "Paragraphe 29" in text


def test_truncated_docx_rebuilt(tmp_path):
    make_docx(tmp_path / "ok.docx", 400)
    data = (tmp_path / "ok.docx").read_bytes()
    (tmp_path / "tronque.docx").write_bytes(data[: int(len(data) * 0.85)])
    r = docrepair.repair(str(tmp_path / "tronque.docx"), str(tmp_path / "out"), libreoffice=None)
    assert r["status"] in ("repaired", "text_salvaged"), r
    assert r["after"]["status"] == "ok"


def test_missing_content_types_and_broken_link(tmp_path):
    docrepair.write_minimal_xlsx(str(tmp_path / "ok.xlsx"), {"Feuille": [["Client", 12], ["B", 3.5]]})
    rezip(tmp_path / "ok.xlsx", tmp_path / "ko.xlsx", lambda n, d: None if n == "[Content_Types].xml" else d)
    r = docrepair.repair(str(tmp_path / "ko.xlsx"), str(tmp_path / "out"), libreoffice=None)
    assert r["status"] == "repaired"
    assert "[Content_Types].xml" in zipfile.ZipFile(r["output"]).namelist()


def test_zero_filled_and_encrypted_are_irrecoverable(tmp_path):
    (tmp_path / "zeros.docx").write_bytes(b"\0" * 5000)
    (tmp_path / "chiffre.xlsx").write_bytes(os.urandom(40000))
    for name in ("zeros.docx", "chiffre.xlsx"):
        d = docrepair.diagnose(str(tmp_path / name))
        assert d["status"] == "irrecoverable"
        r = docrepair.repair(str(tmp_path / name), str(tmp_path / "out"), libreoffice=None)
        assert r["status"] == "failed" and "Datto" in r["message"]
    assert "rançongiciel" in docrepair.diagnose(str(tmp_path / "chiffre.xlsx"))["recommendation"] + \
        " ".join(i["message"] for i in docrepair.diagnose(str(tmp_path / "chiffre.xlsx"))["issues"])


def make_pdf(path, pages=3):
    import pikepdf
    pdf = pikepdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica))
    for i in range(pages):
        page = pikepdf.Page(pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 595, 842],
                                               Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))))
        page.obj.Contents = pdf.make_stream(f"BT /F1 24 Tf 72 700 Td (Page {i + 1}) Tj ET".encode())
        pdf.pages.append(page)
    pdf.save(str(path), object_stream_mode=__import__("pikepdf").ObjectStreamMode.disable)


def test_pdf_repairs(tmp_path):
    import pikepdf
    make_pdf(tmp_path / "ok.pdf")
    data = (tmp_path / "ok.pdf").read_bytes()
    assert docrepair.diagnose(str(tmp_path / "ok.pdf"))["status"] == "ok"
    # Données parasites + xref détruite
    i = data.rfind(b"xref")
    (tmp_path / "xref.pdf").write_bytes(b"JUNK" * 10 + data[:i] + b"xref\ngarbage\n" + data[i + 40:])
    # Fichier tronqué : catalogue et trailer perdus
    lost = data.replace(b"/Type /Catalog", b"/Type /Abimee!")
    (tmp_path / "tronque.pdf").write_bytes(lost[: lost.rfind(b"xref")])
    for name in ("xref.pdf", "tronque.pdf"):
        assert docrepair.diagnose(str(tmp_path / name))["status"] == "corrupt"
        r = docrepair.repair(str(tmp_path / name), str(tmp_path / "out"), libreoffice=None)
        assert r["status"] == "repaired", (name, r)
        with pikepdf.open(r["output"]) as pdf:
            assert len(pdf.pages) == 3


def test_integrity_scan_and_repair_api(env):
    c, cid, tmp = env
    root = tmp / "docs"
    os.makedirs(root)
    make_docx(root / "sain.docx")
    rezip(root / "sain.docx", root / "abime.docx",
          lambda n, d: d.replace(b"Paragraphe 1", b"Paragraphe \x0c1") if n == "word/document.xml" else d)
    (root / "vide.pdf").write_bytes(b"\0" * 300)
    sid = add_share(c, cid, root)
    job = wait_job(c, c.post(f"/api/shares/{sid}/integrity").json()["job_id"])
    res = job["result"]
    assert res["checked"] == 3 and res["counts"]["corrupt"] == 1 and res["counts"]["irrecoverable"] == 1
    assert c.get(f"/api/shares/{sid}/integrity").json()["last"]["id"] == job["id"]

    r = c.post(f"/api/shares/{sid}/repair", json={"path": "abime.docx", "mode": "copy"}).json()
    assert r["status"] == "repaired" and r["output_rel"] == "abime (réparé).docx"
    assert (root / "abime.docx").exists() and (root / "abime (réparé).docx").exists()

    r = c.post(f"/api/shares/{sid}/repair", json={"path": "abime.docx", "mode": "replace"}).json()
    assert r["status"] == "repaired" and r["output_rel"] == "abime.docx"
    assert docrepair.diagnose(str(root / "abime.docx"))["status"] == "ok"
    assert any(i["reason"] == "remplacé par la version réparée" for i in c.get("/api/recycle").json()["items"])

    batch = wait_job(c, c.post(f"/api/shares/{sid}/repair-batch", json={"paths": ["vide.pdf"]}).json()["job_id"])
    assert batch["result"]["failed"] == 1


def test_repair_upload_workshop(env):
    c, _, tmp = env
    make_docx(tmp / "ok.docx")
    rezip(tmp / "ok.docx", tmp / "ko.docx", lambda n, d: None if n == "_rels/.rels" else d)
    r = c.post("/api/repair/upload?filename=Contrat%20client.docx", content=(tmp / "ko.docx").read_bytes()).json()
    assert r["status"] == "repaired", r
    hist = c.get("/api/repairs").json()
    assert hist[0]["original_name"] == "Contrat client.docx" and hist[0]["downloadable"]
    dl = c.get(f"/api/repairs/{hist[0]['id']}/download")
    assert dl.status_code == 200 and dl.content[:2] == b"PK"
    caps = c.get("/api/repair/capabilities").json()
    assert caps["pdf"] and caps["xml"] and "docx" in caps["formats"]


def test_optimization_overview(env):
    c, cid, tmp = env
    write(str(tmp / "p" / "x.tmp"), b"x" * 100, OLD)
    sid = add_share(c, cid, tmp / "p")
    c.app.state.engine.scan_share(c.app.state.engine.share(sid))
    ov = c.get("/api/optimization").json()
    assert ov["totals"]["junk_size"] == 100 and ov["shares"][0]["junk_count"] == 1
    cats = c.get(f"/api/shares/{sid}/junk").json()["categories"]
    assert [x for x in cats if x["key"] == "temp"][0]["count"] == 1
