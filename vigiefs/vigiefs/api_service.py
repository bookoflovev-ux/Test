"""API : fonctionnement 24 h/24 — vivacité, composants, disponibilité, arrêt / redémarrage, maintenance."""

import os
import time
import unicodedata

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import service
from .guard import COMPONENT_LABEL


class ServiceActionIn(BaseModel):
    action: str = Field(pattern="^(stop|restart)$")
    reason: str = Field("", max_length=300)
    confirm: str = ""


class MaintenanceIn(BaseModel):
    minutes: int = Field(60, ge=5, le=7 * 24 * 60)
    reason: str = Field("", max_length=300)


def _plain(text):
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().strip().upper()


def service_summary(engine, control, data_dir, journal_days=30):
    rdir = service.run_dir(data_dir)
    sup = service.status(data_dir)
    now = time.time()
    events = service.read_journal(rdir, since=now - journal_days * 86400)
    av30 = service.availability(events, now - 30 * 86400, now)
    av7 = service.availability(events, now - 7 * 86400, now)
    guard = engine.guard.snapshot()
    state = sup["state"] if control.supervised else {}
    return {
        "status": guard["status"],
        "mode": control.mode,
        "supervised": control.supervised,
        "can_restart": control.can_restart(),
        "pid": os.getpid(),
        "uptime_sec": round(now - engine.started_at),
        "started_at": engine.started_at,
        "supervisor": {
            "running": sup["running"], "status": sup["status"], "pid": state.get("supervisor_pid"),
            "started_at": state.get("supervisor_started_at"), "restarts": state.get("restarts", 0),
            "incidents": state.get("incidents", 0), "last_incident": state.get("last_incident"),
            "probe": state.get("probe"),
        } if control.supervised else None,
        "components": guard["components"],
        "checked_at": guard["checked_at"],
        "availability": {"7d": av7, "30d": av30},
        "maintenance": engine.maintenance(),
        "journal": [{**e, "label": service.EVENT_LABEL.get(e.get("event"), e.get("event"))}
                    for e in reversed(events[-200:])],
        "pending": control.pending,
    }


def register(app, c):
    db, engine, viewer, admin = c["db"], c["engine"], c["viewer"], c["admin"]
    control, data_dir = c["control"], c["data_dir"]

    # Sonde de vivacité : asynchrone (boucle d'événements uniquement), sans authentification ni accès disque.
    @app.get("/api/health/live", include_in_schema=False)
    async def health_live():
        return {"status": "ok", "ts": time.time()}

    @app.get("/api/health/ready")
    def health_ready():
        """Prêt à servir : planificateur et garde actifs, aucun composant en erreur (503 sinon)."""
        snap = engine.guard.snapshot()
        ready = engine.scheduler_alive() and engine.guard.alive() and snap["status"] != "error"
        body = {"status": "ready" if ready else "degraded", "components": {k["name"]: k["status"] for k in snap["components"]},
                "maintenance": bool(engine.maintenance())}
        return JSONResponse(body, status_code=200 if ready else 503)

    @app.get("/api/service")
    def service_state(user=Depends(viewer)):
        return service_summary(engine, control, data_dir)

    @app.post("/api/service/action")
    def service_action(body: ServiceActionIn, user=Depends(admin)):
        actor = f"{user['username']} (interface)"
        if body.action == "stop":
            if _plain(body.confirm) != "ARRETER":
                raise HTTPException(400, "Confirmation requise : saisissez ARRÊTER")
        elif not control.can_restart():
            raise HTTPException(409, "Redémarrage indisponible : VigieFS n'est pas lancé en mode service "
                                     "(python -m vigiefs supervise, service Windows ou systemd).")
        if not control.request(body.action, actor, body.reason):
            raise HTTPException(409, "Action impossible dans ce mode de lancement")
        db.audit(user["username"], "arrêt du service" if body.action == "stop" else "redémarrage du service",
                 "VigieFS", body.reason)
        return {"ok": True, "action": body.action, "supervised": control.supervised}

    @app.post("/api/service/maintenance")
    def start_maintenance(body: MaintenanceIn, user=Depends(admin)):
        m = {"until": time.time() + body.minutes * 60, "reason": body.reason, "by": user["username"],
             "since": time.time()}
        db.set_setting("maintenance", m)
        db.audit(user["username"], "mode maintenance", f"{body.minutes} min", body.reason)
        service.journal(service.run_dir(data_dir), "maintenance", actor=user["username"], minutes=body.minutes,
                        reason=body.reason)
        return m

    @app.delete("/api/service/maintenance")
    def stop_maintenance(user=Depends(admin)):
        db.set_setting("maintenance", {})
        db.audit(user["username"], "fin du mode maintenance", "")
        return {"ok": True}

    @app.post("/api/service/db-backup")
    def db_backup(user=Depends(admin)):
        info = engine.guard.maintain_database()
        db.audit(user["username"], "sauvegarde de la base", info.get("backup_file") or "")
        if info.get("integrity") not in (None, "ok"):
            raise HTTPException(500, f"Contrôle d'intégrité en échec : {info['integrity']}")
        return info

    return {"labels": COMPONENT_LABEL}
