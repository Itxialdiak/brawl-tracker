"""Rutas de Informes de Claude: creación en segundo plano, listado y consulta.

Extraído de main.py; se incluye con app.include_router()."""
import json
import asyncio
from fastapi import APIRouter, Body, Query, Depends, HTTPException
from fastapi.responses import JSONResponse
from .. import db, coach, auth
from ..config import REPORT_QUOTA_ENABLED, MONTHLY_REPORT_LIMIT
from ..api_common import _require_follow

router = APIRouter()


# --------------------------- Informes de Claude (en segundo plano) ---------------------------

_report_tasks: set = set()


def _public_report(r: dict, with_content: bool) -> dict:
    out = {"id": r["id"], "name": r.get("name"), "status": r.get("status"),
           "scope_label": r.get("scope_label"), "created_at": r.get("created_at"),
           "completed_at": r.get("completed_at"), "error": r.get("error")}
    if with_content:
        out["content"] = r.get("content")
    return out


async def _run_report(report_id: int, player: str, filters: dict):
    """Genera el informe llamando a Claude y lo guarda. Corre por su cuenta en segundo plano."""
    try:
        name, content = await coach.generate_report(player, filters)
        await asyncio.to_thread(db.set_report_result, report_id, name, content)
    except Exception as e:  # noqa: BLE001
        await asyncio.to_thread(db.set_report_error, report_id, str(e))


@router.post("/api/reports")
async def api_create_report(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    player = (payload or {}).get("player")
    if not player:
        return JSONResponse({"error": "Falta el jugador."}, status_code=400)
    _require_follow(user, player)
    if not coach.configured():
        return JSONResponse({"error": "Falta ANTHROPIC_API_KEY en el .env para generar informes."}, status_code=400)
    if await asyncio.to_thread(db.has_generating_report, player):
        return JSONResponse({"error": "Ya hay un informe generándose para este jugador. Espera a que termine."}, status_code=409)
    # Cuota mensual de informes: activa solo si REPORT_QUOTA_ENABLED (apagada en beta).
    if REPORT_QUOTA_ENABLED:
        if not await asyncio.to_thread(db.consume_report_credit, user["id"], MONTHLY_REPORT_LIMIT):
            return JSONResponse(
                {"error": f"Has agotado tus {MONTHLY_REPORT_LIMIT} informes de este mes. Se renuevan el mes que viene."},
                status_code=429)
    filters = {
        "player": player,
        "brawler": (payload or {}).get("brawler") or None,
        "mode": (payload or {}).get("mode") or None,
        "map": (payload or {}).get("map") or None,
    }
    label = coach.scope_label_from(filters)
    rid = await asyncio.to_thread(db.create_report, player, json.dumps(filters), label)
    task = asyncio.create_task(_run_report(rid, player, filters))
    _report_tasks.add(task)
    task.add_done_callback(_report_tasks.discard)
    return _public_report(await asyncio.to_thread(db.get_report, rid), with_content=False)


@router.get("/api/reports")
async def api_list_reports(player: str = Query(...), user: dict = Depends(auth.require_user)):
    _require_follow(user, player)
    rows = await asyncio.to_thread(db.list_reports, player)
    return [_public_report(r, with_content=False) for r in rows]


@router.get("/api/reports/{report_id}")
async def api_get_report(report_id: int, user: dict = Depends(auth.require_user)):
    rep = await asyncio.to_thread(db.get_report, report_id)
    if not rep:
        return JSONResponse({"error": "No existe ese informe."}, status_code=404)
    if not db.user_follows(user["id"], rep["player_tag"]):
        raise HTTPException(status_code=403, detail="No sigues a ese jugador.")
    return _public_report(rep, with_content=True)
