"""API : moteur RepareDocs (réparation en masse d'un dossier, réparation individuelle, résultats, rapport)."""

import html
import mimetypes
import os
from typing import Dict, Optional

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .reparedocs import STATUTS, RepareDocs, RepareDocsError

# Pages produites par le moteur (rapport, listes) : affichées isolées de l'interface VigieFS.
SANDBOX_CSP = ("sandbox allow-downloads allow-popups allow-popups-to-escape-sandbox; default-src 'none'; "
               "style-src 'unsafe-inline'; img-src 'self' data:; font-src data:")


class RunIn(BaseModel):
    share_id: Optional[int] = None
    path: str = ""                       # sous-dossier du dossier supervisé
    source: Optional[str] = None         # chemin libre (administrateur)
    output: Optional[str] = None         # dossier de sortie (administrateur) ; sinon dossier par défaut
    options: Dict = Field(default_factory=dict)


class ShareFileIn(BaseModel):
    path: str


class ApplyIn(BaseModel):
    mode: str = Field("copy", pattern="^(copy|replace)$")


class ConfirmIn(BaseModel):
    confirm: str = ""


def register(app, c):
    db, engine, viewer, operator, admin = c["db"], c["engine"], c["viewer"], c["operator"], c["admin"]
    get_share, resolve, data_dir, has_role = c["get_share"], c["resolve"], c["data_dir"], c["has_role"]
    rd = RepareDocs(db, data_dir)
    engine.reparedocs = rd

    def run_or_404(run_id):
        run = rd.run(run_id)
        if not run:
            raise HTTPException(404, "Traitement RepareDocs introuvable")
        return run

    def fail(exc):
        raise HTTPException(400, str(exc))

    @app.get("/api/reparedocs/info")
    def rd_info(refresh: bool = False, user=Depends(viewer)):
        info = rd.info(refresh)
        busy = rd.busy()
        return {**info, "busy": busy["id"] if busy else None, "statuses": STATUTS,
                "defaults": {**(info.get("defaults") or {})}}

    @app.post("/api/reparedocs/check")
    def rd_check(body: RunIn, user=Depends(operator)):
        source, output = _paths(body, user)
        try:
            return {**rd.check_paths(source, output), "source": source, "output": output}
        except RepareDocsError as exc:
            fail(exc)

    def _paths(body, user):
        share = None
        if body.share_id:
            share = get_share(body.share_id)
            source = resolve(share, body.path or "")
        elif body.source:
            if not has_role(user["role"], "admin"):
                raise HTTPException(403, "Chemin libre réservé aux administrateurs")
            source = body.source.strip().strip('"')
        else:
            raise HTTPException(400, "Choisissez un dossier supervisé ou un dossier à analyser")
        if body.output and not has_role(user["role"], "admin"):
            raise HTTPException(403, "Dossier de sortie personnalisé réservé aux administrateurs")
        label = (share["name"] + (" - " + body.path.replace("/", " - ") if body.path else "")) if share else \
            os.path.basename(source.rstrip("\\/")) or "analyse"
        output = body.output.strip().strip('"') if body.output else rd.default_output(label, source)
        return source, output

    @app.get("/api/reparedocs/runs")
    def rd_runs(share_id: Optional[int] = None, limit: int = Query(50, le=500), user=Depends(viewer)):
        return [{k: v for k, v in r.items() if k not in ("logs",)} for r in rd.runs(limit, share_id)]

    @app.post("/api/reparedocs/runs")
    def rd_start(body: RunIn, user=Depends(operator)):
        source, output = _paths(body, user)
        share = get_share(body.share_id) if body.share_id else None
        try:
            run_id = rd.start("masse", source, output, body.options, user["username"], share=share,
                              origin={"path": body.path} if share else {}, skip_roots=engine.skip_roots())
        except RepareDocsError as exc:
            fail(exc)
        return {"run_id": run_id}

    @app.get("/api/reparedocs/runs/{run_id}")
    def rd_state(run_id: int, since: int = 0, user=Depends(viewer)):
        st = rd.state(run_or_404(run_id))
        st["logs"] = [l for l in st["logs"] if isinstance(l, dict) and (l.get("id") or 0) > since]
        return st

    @app.post("/api/reparedocs/runs/{run_id}/stop")
    def rd_stop(run_id: int, user=Depends(operator)):
        rd.stop(run_or_404(run_id))
        db.audit(user["username"], "RepareDocs : arrêt demandé", str(run_id))
        return {"ok": True}

    @app.get("/api/reparedocs/runs/{run_id}/results")
    def rd_results(run_id: int, status: str = "", q: str = "", offset: int = Query(0, ge=0),
                   limit: int = Query(50, ge=1, le=200), user=Depends(viewer)):
        try:
            return rd.results(run_or_404(run_id), [s for s in status.split(",") if s], q.strip(), offset, limit)
        except RepareDocsError as exc:
            fail(exc)

    @app.post("/api/reparedocs/runs/{run_id}/restore")
    def rd_restore(run_id: int, body: ConfirmIn, user=Depends(admin)):
        run = rd.state(run_or_404(run_id))
        if run["kind"] != "masse" or run["status"] not in ("done", "stopped"):
            raise HTTPException(409, "Remise en place possible après un traitement en masse terminé")
        if body.confirm.strip().upper() not in ("REMETTRE", "REMETTRE EN PLACE"):
            raise HTTPException(400, "Confirmation requise : saisissez REMETTRE")
        try:
            new_id = rd.start("restaurer", run["source"], run["output"], run["options"], user["username"],
                              share={"id": run["share_id"]} if run.get("share_id") else None,
                              origin={"from_run": run_id})
        except RepareDocsError as exc:
            fail(exc)
        return {"run_id": new_id}

    # ------------------------------------------------------------ réparation individuelle
    @app.post("/api/reparedocs/upload")
    async def rd_upload(request: Request, filename: str, user=Depends(operator)):
        content = await request.body()
        if not content:
            raise HTTPException(400, "Fichier vide")
        from starlette.concurrency import run_in_threadpool
        try:
            run_id = await run_in_threadpool(rd.start_individual, filename, content, user["username"])
        except RepareDocsError as exc:
            fail(exc)
        return {"run_id": run_id}

    @app.post("/api/shares/{share_id}/reparedocs/file")
    def rd_share_file(share_id: int, body: ShareFileIn, user=Depends(operator)):
        share = get_share(share_id)
        target = resolve(share, body.path)
        if not os.path.isfile(target):
            raise HTTPException(404, "Fichier introuvable")
        try:
            run_id = rd.start_individual(os.path.basename(target), target, user["username"], share=share,
                                         rel=body.path)
        except RepareDocsError as exc:
            fail(exc)
        return {"run_id": run_id}

    @app.post("/api/reparedocs/runs/{run_id}/apply")
    def rd_apply(run_id: int, body: ApplyIn, user=Depends(operator)):
        run = rd.state(run_or_404(run_id))
        share_id = (run.get("origin") or {}).get("share_id")
        if run["kind"] != "individuel" or not share_id or run["status"] != "done":
            raise HTTPException(409, "Dépôt possible pour une réparation individuelle terminée d'un fichier de partage")
        try:
            return rd.apply_individual(run, body.mode, engine.recycle, get_share(share_id), user["username"])
        except RepareDocsError as exc:
            fail(exc)

    # ------------------------------------------------------------ rapport et fichiers produits
    @app.get("/api/reparedocs/runs/{run_id}/sortie/{rel:path}", include_in_schema=False)
    @app.get("/api/reparedocs/runs/{run_id}/sortie/", include_in_schema=False)
    def rd_file(run_id: int, rel: str = "", user=Depends(viewer)):
        run = run_or_404(run_id)
        target = rd.resolve_file(run, rel)
        if not target:
            raise HTTPException(404, "Introuvable (le fichier n'est peut-être pas encore produit)")
        if os.path.isdir(target):
            items = sorted(os.listdir(target), key=str.lower)
            rows = "".join(
                f'<li><a href="{html.escape(n)}{"/" if os.path.isdir(os.path.join(target, n)) else ""}">'
                f'{html.escape(n)}{"/" if os.path.isdir(os.path.join(target, n)) else ""}</a></li>'
                for n in items if not n.endswith(".tmp"))
            body = (f"<!doctype html><meta charset='utf-8'><title>{html.escape(rel or 'Résultats')}</title>"
                    "<style>body{font:14px system-ui,sans-serif;margin:24px}li{margin:4px 0}</style>"
                    f"<h2>{html.escape('/' + rel)}</h2><p><a href='../'>Dossier parent</a></p><ul>{rows}</ul>")
            return HTMLResponse(body, headers={"Content-Security-Policy": SANDBOX_CSP})
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        headers = {"Content-Security-Policy": SANDBOX_CSP}
        if ctype in ("text/html", "application/pdf", "text/plain") or ctype.startswith("image/"):
            return FileResponse(target, media_type=ctype, headers=headers,
                                content_disposition_type="inline", filename=os.path.basename(target))
        return FileResponse(target, media_type=ctype, filename=os.path.basename(target), headers=headers)

    return rd
