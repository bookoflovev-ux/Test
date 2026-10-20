"""Hyper-V : inventaire, constats, rattachement des données, protections des fichiers de VM, API."""

import json
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vigiefs import hyperv, system  # noqa: E402
from vigiefs import restructure as rs  # noqa: E402
from vigiefs.api import create_app  # noqa: E402
from vigiefs.demo import HYPERV_GUESTS, hyperv_raw  # noqa: E402
from vigiefs.optimize import JunkClassifier, find_duplicates  # noqa: E402
from vigiefs.storage import iter_archive_candidates, share_policy  # noqa: E402

PASSWORD = "Sup3r-Secret!"
GB = 1024 ** 3
FILES_VM = "6f1c2a10-0001-4d1e-9a01-000000000001"


def test_normalize_and_analyze():
    now = time.time()
    inv = hyperv.normalize(hyperv_raw(now), now)
    vms = {v["name"]: v for v in inv["vms"]}
    assert [v["name"] for v in inv["vms"][:3]] == ["SRV-AD01", "SRV-FICHIERS-CABINET", "SRV-TRANSPORTS"]   # en marche d'abord
    f = vms["SRV-FICHIERS-CABINET"]
    assert f["file_size"] == (41 + 74 + 612) * GB and f["max_size"] == (80 + 1024) * GB
    assert f["targets"][:2] == ["srv-fichiers.cabinet.local", "SRV-FICHIERS-CABINET"] and "192.168.10.20" in f["targets"]
    d = next(v for v in inv["volumes"] if v["root"] == "D:\\")
    assert d["vm_files"] > 1000 * GB and d["potential_growth"] > d["free"]
    found = {x["key"].split(":")[0]: x for x in hyperv.analyze(inv, {}, HYPERV_GUESTS, {}, "D:\\Archives", now)}
    assert found["checkpoint"]["severity"] == "critical" and found["checkpoint"]["details"]["age_days"] == 41
    assert {"hostfree", "overcommit", "orphans", "inactive", "unsupervised", "reclaim", "archive-volume"} <= set(found)
    assert found["orphans"]["details"]["size"] == 305 * GB
    # Les dossiers des VM deviennent des racines protégées, jamais un volume entier.
    prot = hyperv.protected_paths(inv)
    assert "D:\\Hyper-V\\Virtual Hard Disks" in prot and "D:\\Hyper-V\\VM-ERP-2016" in prot and "D:\\" not in prot


def test_links_and_share_discovery(monkeypatch):
    now = time.time()
    inv = hyperv.normalize(hyperv_raw(now), now)
    shares = [{"id": 1, "path": "\\\\srv-fichiers.cabinet.local\\Commun"}, {"id": 2, "path": "\\\\SRV-TRANSPORTS\\Compta"},
              {"id": 3, "path": "\\\\192.168.10.10\\x"}, {"id": 4, "path": "D:\\Local"}]
    links = hyperv.links(inv, shares, manual=[("6f1c2a10-0004-4d1e-9a01-000000000004", 4)])
    assert links[FILES_VM] == [1] and links["6f1c2a10-0002-4d1e-9a01-000000000002"] == [2]
    assert links["6f1c2a10-0003-4d1e-9a01-000000000003"] == [3] and links["6f1c2a10-0004-4d1e-9a01-000000000004"] == [4]
    text = ("Ressources partagées de \\\\SRV-TRANSPORTS\n\nNom du partage  Type    Utilisé comme  Commentaire\n"
            "-------------------------------------------------------------------\nADMIN$          Disque                 Administration à distance\n"
            "Compta          Disque                 Comptabilité\nExploitation    Disque\nNETLOGON        Disque\n"
            "imprimante     Impression\nLa commande s'est terminée correctement.\n")
    assert [s["name"] for s in hyperv.parse_net_view(text)] == ["Compta", "Exploitation"]
    calls = []

    def fake_ps(script, timeout=0, as_json=True):
        calls.append(script)
        if "srv-fichiers.cabinet.local" in script:
            raise system.CommandError("WinRM refusé")
        return {"error": "Accès refusé", "netview": text}
    monkeypatch.setattr(system, "powershell", fake_ps)
    vm = next(v for v in inv["vms"] if v["name"] == "SRV-FICHIERS-CABINET")
    res = hyperv.discover(vm)
    assert res["method"] == "net view" and res["target"] == "SRV-FICHIERS-CABINET" and len(calls) == 2


def test_vm_files_are_never_touched(tmp_path):
    """Archivage, nettoyage, doublons, réorganisation : un disque virtuel ne bouge jamais."""
    root = tmp_path / "srv"
    old = time.time() - 4000 * 86400
    files = {"Hyper-V/Virtual Hard Disks/srv.vhdx": b"V" * 4096, "Hyper-V/Virtual Hard Disks/copie.vhdx": b"V" * 4096,
             "Hyper-V/SRV/config.vmcx": b"c", "Modeles/W2019.vhdx": b"M" * 4096, "Docs/vieux.pdf": b"%PDF" * 1024,
             "Docs/copie.pdf": b"%PDF" * 1024}
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        os.utime(p, (old, old))
    share = {"id": 1, "name": "srv", "path": str(root), "archive_age_days": 30}
    stats = {}
    cands = [c[0] for c in iter_archive_candidates(share, share_policy(share), stats=stats)]
    assert sorted(cands) == ["Docs/copie.pdf", "Docs/vieux.pdf"] and stats["vm_protected"] == 4
    assert JunkClassifier({}).classify("vide.vhdx", 0, old) is None
    dups = find_duplicates([share], min_size=1)
    assert [sorted(f["path"] for f in g["files"]) for g in dups["groups"]] == [["Docs/copie.pdf", "Docs/vieux.pdf"]]
    plan, _ = rs.propose(share, {"cold_years": 1}, {})
    moved = {o["src"] for o in plan["ops"] if o["type"] in rs.DIR_OPS}
    assert not any(src.startswith(("Hyper-V", "Modeles")) or src == "" for src in moved)
    assert plan["protected"]["dirs"] >= 1                     # « Modeles » (froid) laissé en place
    assert "Docs" in moved                                    # le reste est bien proposé


@pytest.fixture
def env(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "data"), start_engine=False)
    c = TestClient(app)
    c.headers.update({"X-VigieFS": "1"})
    c.post("/api/auth/setup", json={"username": "admin", "password": PASSWORD})
    c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    return c, app.state.engine, tmp_path


def test_refresh_store_alerts_and_api(env, monkeypatch):
    c, engine, tmp_path = env
    assert c.get("/api/hyperv").json()["inventory"] is None
    cid = c.post("/api/clients", json={"name": "Cabinet"}).json()["id"]
    a = tmp_path / "a"
    b = tmp_path / "b"
    for d in (a, b):
        d.mkdir()
        (d / "contrat.pdf").write_bytes(b"%PDF" * 400000)
    s1 = c.post("/api/shares", json={"client_id": cid, "name": "Commun", "path": "\\\\srv-fichiers.cabinet.local\\Commun",
                                     "scan_interval_min": 0, "monitor_enabled": False}).json()["id"]
    s2 = c.post("/api/shares", json={"client_id": cid, "name": "Local A", "path": str(a), "scan_interval_min": 0,
                                     "monitor_enabled": False}).json()["id"]
    s3 = c.post("/api/shares", json={"client_id": cid, "name": "Local B", "path": str(b), "scan_interval_min": 0,
                                     "monitor_enabled": False}).json()["id"]
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    now = time.time()
    res = engine.hyperv.refresh(inventory_fn=lambda: hyperv_raw(now), discover_fn=lambda vm: HYPERV_GUESTS.get(vm["id"], {
        "shares": [], "volumes": [], "method": "cim", "target": vm["name"]}))
    assert res["available"] and res["vms"] == 5 and res["findings"] >= 7
    alerts = {a["fingerprint"]: a for a in engine.db.query("SELECT * FROM alerts WHERE category='virtualization'")}
    assert f"hyperv:checkpoint:{FILES_VM}" in alerts and alerts[f"hyperv:checkpoint:{FILES_VM}"]["severity"] == "critical"
    assert "D:\\Hyper-V\\Virtual Hard Disks" in engine.protected_roots()
    d = c.get("/api/hyperv").json()
    vm = next(v for v in d["inventory"]["vms"] if v["id"] == FILES_VM)
    assert [s["id"] for s in vm["shares"]] == [s1] and vm["guest"]["shares"][0]["name"] == "Commun"
    assert vm["data_size"] == (41 + 462) * GB
    # Lien manuel (VigieFS dans la VM / chemin local) puis doublons entre les VM
    vm2, vm3 = "6f1c2a10-0002-4d1e-9a01-000000000002", "6f1c2a10-0003-4d1e-9a01-000000000003"
    assert c.post(f"/api/hyperv/vms/{vm2}/links", json={"share_id": s2}).json()["ok"]
    assert c.post(f"/api/hyperv/vms/{vm3}/links", json={"share_id": s3}).json()["ok"]
    job = c.post("/api/hyperv/duplicates", json={"min_size_mb": 0.5}).json()["job_id"]
    for _ in range(100):
        j = c.get(f"/api/jobs/{job}").json()
        if j["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert j["status"] == "done" and j["result"]["group_count"] == 1
    assert [x["id"] for x in c.get("/api/jobs?type=duplicates&scope=hyperv").json()] == [job]
    # Un nouvel inventaire sans point de contrôle résout l'alerte correspondante.
    raw = hyperv_raw(now)
    raw["vms"][0]["snapshots"] = []
    engine.hyperv.refresh(inventory_fn=lambda: raw, with_guests=False)
    st = engine.db.query_one("SELECT status FROM alerts WHERE fingerprint=?", (f"hyperv:checkpoint:{FILES_VM}",))
    assert st["status"] == "resolved"
    # Garde-fous : VM en marche (analyse, compactage), confirmation, explorateur
    assert c.post(f"/api/hyperv/vms/{FILES_VM}/scan", json={}).status_code == 400
    off = "6f1c2a10-0004-4d1e-9a01-000000000004"
    assert c.post(f"/api/hyperv/vms/{off}/compact", json={"confirm": "oui"}).status_code == 400
    assert c.post(f"/api/hyperv/vms/{FILES_VM}/compact", json={"confirm": "COMPACTER"}).status_code == 400
    (a / "VM").mkdir()
    (a / "VM" / "disque.vhdx").write_bytes(b"x")
    r = c.post(f"/api/shares/{s2}/delete", json={"path": "VM"})
    assert r.status_code == 409 and "machine virtuelle" in r.json()["detail"]
    assert c.post(f"/api/shares/{s2}/rename", json={"path": "VM/disque.vhdx", "new_name": "x.vhdx"}).status_code == 409
    assert c.get("/api/hyperv/history").json()


def test_demo_has_hyperv_inventory(tmp_path):
    from vigiefs import demo
    demo.create(str(tmp_path / "d"))
    app = create_app(str(tmp_path / "d"), start_engine=False)
    c = TestClient(app)
    c.headers.update({"X-VigieFS": "1"})
    c.post("/api/auth/setup", json={"username": "admin", "password": PASSWORD})
    c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    d = c.get("/api/hyperv").json()
    assert d["demo"] and len(d["inventory"]["vms"]) == 5 and d["findings"]
    files = next(v for v in d["inventory"]["vms"] if v["name"] == "SRV-FICHIERS-CABINET")
    assert files["shares"] and files["growth_7d"] > 0
    assert c.post("/api/hyperv/refresh").status_code == 400                # démo : rien à actualiser
    assert app.state.engine.protected_roots() == []                        # chemins fictifs : rien de protégé sur le poste
    assert json.loads(app.state.engine.db.query_one("SELECT findings FROM hyperv_snapshots ORDER BY id DESC")["findings"])
