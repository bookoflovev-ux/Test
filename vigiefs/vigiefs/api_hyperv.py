"""API : virtualisation Hyper-V (inventaire, constats, données des VM, contenu des VM éteintes, compactage)."""

import json
import time
from typing import Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from . import hyperv, system
from .optimize import find_duplicates


class LinkIn(BaseModel):
    share_id: int


class ConfirmIn(BaseModel):
    confirm: str = ""


class DupIn(BaseModel):
    min_size_mb: float = Field(1.0, ge=0)


def register(app, c):
    db, engine, jobs = c["db"], c["engine"], c["jobs"]
    viewer, operator, admin = c["viewer"], c["operator"], c["admin"]
    hv = engine.hyperv

    def latest_or_404():
        snap = hv.latest()
        if not snap:
            raise HTTPException(404, "Aucun inventaire Hyper-V : lancez « Actualiser l'inventaire » sur l'hôte.")
        return snap

    def vm_or_404(vm_id):
        snap = latest_or_404()
        vm = next((v for v in snap["data"]["vms"] if v["id"] == vm_id), None)
        if not vm:
            raise HTTPException(404, "Machine virtuelle inconnue")
        return snap, vm

    def demo_guard(action):
        if hv.settings().get("demo"):
            raise HTTPException(400, f"Inventaire de démonstration : {action} est disponible sur un vrai hôte Hyper-V.")

    def vm_shares(snap):
        shares = engine.shares()
        return hyperv.links(snap["data"], shares, hv.manual_links()), {s["id"]: s for s in shares}

    @app.get("/api/hyperv")
    def overview(user=Depends(viewer)):
        state = db.get_setting("hyperv_state", {}) or {}
        snap = hv.latest()
        running = db.query_one("SELECT id FROM jobs WHERE type='hyperv_refresh' AND status IN ('queued','running') "
                               "ORDER BY id DESC LIMIT 1")
        out = {"state": state, "demo": bool(hv.settings().get("demo")), "platform": "windows" if system.IS_WINDOWS else "posix",
               "job": running["id"] if running else None, "inventory": None}
        if not snap:
            return out
        links, shares = vm_shares(snap)
        guests = hv.guests()
        week_ago = time.time() - 7 * 86400
        old = db.query_one("SELECT data FROM hyperv_snapshots WHERE created_at <= ? ORDER BY id DESC LIMIT 1", (week_ago,)) \
            or db.query_one("SELECT data FROM hyperv_snapshots ORDER BY id LIMIT 1")
        before = {v["id"]: v["file_size"] for v in json.loads(old["data"])["vms"]} if old else {}
        for vm in snap["data"]["vms"]:
            g = guests.get(vm["id"]) or {}
            vm["guest"] = g
            vm["growth_7d"] = vm["file_size"] - before[vm["id"]] if vm["id"] in before else None
            vm["shares"] = [{"id": sid, "name": shares[sid]["name"], "path": shares[sid]["path"],
                             "size": shares[sid].get("last_size"), "client_id": shares[sid]["client_id"]}
                            for sid in links.get(vm["id"], []) if sid in shares]
            vm["data_size"] = sum((v.get("size") or 0) - (v.get("free") or 0) for v in g.get("volumes") or [])
        out.update(inventory=snap["data"], findings=snap["findings"], collected_at=snap["created_at"],
                   protected=db.get_setting("vm_protected_paths", []) or [])
        return out

    @app.post("/api/hyperv/refresh")
    def refresh(user=Depends(operator)):
        demo_guard("l'actualisation")
        if not system.IS_WINDOWS:
            raise HTTPException(400, "Hyper-V : installez VigieFS sur l'hôte Windows Hyper-V.")
        return {"job_id": jobs.submit("hyperv_refresh", lambda ctx: hv.refresh(ctx=ctx), user["username"])}

    @app.post("/api/hyperv/vms/{vm_id}/discover")
    def discover(vm_id: str, user=Depends(operator)):
        demo_guard("la découverte des données")
        snap, vm = vm_or_404(vm_id)
        if not vm["running"]:
            raise HTTPException(400, "La VM est éteinte : analysez plutôt le contenu de ses disques.")
        res = hyperv.discover(vm)
        hv.save_guest(vm_id, vm["name"], res)
        hv.store(snap["data"])
        return res

    @app.post("/api/hyperv/vms/{vm_id}/links")
    def add_link(vm_id: str, body: LinkIn, user=Depends(operator)):
        vm_or_404(vm_id)
        if not engine.share(body.share_id):
            raise HTTPException(404, "Dossier inconnu")
        db.execute("INSERT OR IGNORE INTO hyperv_links(vm_id, share_id) VALUES(?,?)", (vm_id, body.share_id))
        db.audit(user["username"], "lien VM ↔ dossier", vm_id, str(body.share_id))
        snap = hv.latest()
        hv.store(snap["data"])
        return {"ok": True}

    @app.delete("/api/hyperv/vms/{vm_id}/links/{share_id}")
    def del_link(vm_id: str, share_id: int, user=Depends(operator)):
        db.execute("DELETE FROM hyperv_links WHERE vm_id=? AND share_id=?", (vm_id, share_id))
        return {"ok": True}

    @app.post("/api/hyperv/vms/{vm_id}/scan")
    def scan(vm_id: str, body: ConfirmIn, user=Depends(admin)):
        demo_guard("l'analyse du contenu")
        snap, vm = vm_or_404(vm_id)
        if vm["running"]:
            raise HTTPException(400, "La VM est en marche : ses disques ne peuvent pas être montés. Ses données se voient "
                                     "par ses partages (découverte) ou en supervisant ses dossiers.")

        def run(ctx):
            res = hyperv.scan_offline(vm, ctx, engine.skip_roots())
            g = hv.guests().get(vm_id) or {}
            hv.save_guest(vm_id, vm["name"], {**g, "offline": res})
            return res
        return {"job_id": jobs.submit("hyperv_scan", run, user["username"], params={"vm": vm["name"]},
                                      title=f"Analyse du contenu — {vm['name']}")}

    @app.post("/api/hyperv/vms/{vm_id}/compact")
    def compact(vm_id: str, body: ConfirmIn, user=Depends(admin)):
        demo_guard("le compactage")
        if body.confirm.strip().upper() != "COMPACTER":
            raise HTTPException(400, "Saisissez COMPACTER pour confirmer")
        snap, vm = vm_or_404(vm_id)
        if (vm["state"] or "").lower() != "off":
            raise HTTPException(400, "La VM doit être arrêtée pour compacter ses disques.")
        db.audit(user["username"], "compactage des disques", vm["name"])
        return {"job_id": jobs.submit("hyperv_compact", lambda ctx: hyperv.compact(vm, ctx), user["username"],
                                      params={"vm": vm["name"]}, title=f"Compactage — {vm['name']}")}

    @app.post("/api/hyperv/duplicates")
    def duplicates(body: DupIn, user=Depends(operator)):
        snap = latest_or_404()
        links, shares = vm_shares(snap)
        ids = sorted({sid for lst in links.values() for sid in lst if sid in shares})
        if len(ids) < 2:
            raise HTTPException(400, "Il faut au moins deux dossiers supervisés rattachés à des VM.")
        chosen = [shares[i] for i in ids]
        min_size = int(body.min_size_mb * 1024 * 1024)
        return {"job_id": jobs.submit(
            "duplicates", lambda ctx: find_duplicates(chosen, min_size, ctx, engine.skip_roots() + engine.protected_roots()),
            user["username"], title="Recherche de doublons entre les VM",
            params={"min_size_mb": body.min_size_mb, "share_ids": ids, "scope": "hyperv"})}

    @app.get("/api/hyperv/history")
    def history(vm_id: Optional[str] = None, user=Depends(viewer)):
        rows = db.query("SELECT created_at, data FROM hyperv_snapshots ORDER BY id")
        out = []
        for r in rows:
            vms = json.loads(r["data"])["vms"]
            if vm_id:
                v = next((x for x in vms if x["id"] == vm_id), None)
                if v:
                    out.append({"ts": r["created_at"], "size": v["file_size"]})
            else:
                out.append({"ts": r["created_at"], "size": sum(v["file_size"] for v in vms)})
        return out
