"""Rutas de Informes de Claude: creación en segundo plano, listado y consulta.

Extraído de main.py; se incluye con app.include_router()."""
import json
import asyncio
from fastapi import APIRouter, Body, Query, Depends, HTTPException
from fastapi.responses import JSONResponse
from .. import db, coach, auth, retos
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


async def _run_report(report_id: int, player: str, filters: dict, user_id: int,
                      model_key: str = None, is_admin: bool = False):
    """Genera el informe llamando a Claude, lo guarda y crea los retos del Sensei que
    propone. Corre por su cuenta en segundo plano."""
    try:
        name, content, new_retos = await coach.generate_report(player, filters, model_key, is_admin)
        await asyncio.to_thread(db.set_report_result, report_id, name, content)
        await asyncio.to_thread(_assign_sensei_retos, user_id, player, report_id, new_retos)
    except Exception as e:  # noqa: BLE001
        await asyncio.to_thread(db.set_report_error, report_id, str(e))


def _assign_sensei_retos(user_id: int, player: str, report_id: int, new_retos: list) -> None:
    """Inserta los retos propuestos por el informe como retos del Sensei y apunta al
    usuario (su progreso se medirá desde ahora, automáticamente)."""
    for r in (new_retos or []):
        ok, _ = retos.validate_conditions(r.get("conditions"))
        if not ok:
            continue
        diff = int(r.get("difficulty") or 3)
        rid = db.create_reto(user_id, r.get("name"), r.get("theme"), r.get("description"),
                             r.get("conditions"), diff, visibility="invite",
                             source="sensei", report_id=report_id, target_user_id=user_id)
        db.join_reto(rid, user_id, player, role="participant", assigned_difficulty=diff)


@router.post("/api/reports")
async def api_create_report(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    player = (payload or {}).get("player")
    if not player:
        return JSONResponse({"error": "Falta el jugador."}, status_code=400)
    _require_follow(user, player)
    if not coach.configured():
        return JSONResponse({"error": "Falta ANTHROPIC_API_KEY en el .env para generar informes."}, status_code=400)
    gate = retos.sensei_gate(user["id"], bool(user.get("is_admin")))
    if not gate["can_generate"]:
        return JSONResponse({"error": f"Aún tienes {gate['active']} retos del Sensei pendientes. Cúmplelos "
                                      f"(o resetea el entrenamiento si han pasado {gate['gate_days']} días) "
                                      "antes de pedir otro informe.", "gate": gate}, status_code=403)
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
        "role": (payload or {}).get("role") or None,
        "lang": (payload or {}).get("lang") or "es",  # el Sensei responde en el idioma de la app
    }
    label = coach.scope_label_from(filters)
    model_key = (payload or {}).get("model")   # premium (Opus) solo para admin; el resto cae a estándar
    is_admin = bool(user.get("is_admin"))
    rid = await asyncio.to_thread(db.create_report, player, json.dumps(filters), label)
    task = asyncio.create_task(_run_report(rid, player, filters, user["id"], model_key, is_admin))
    _report_tasks.add(task)
    task.add_done_callback(_report_tasks.discard)
    return _public_report(await asyncio.to_thread(db.get_report, rid), with_content=False)


@router.get("/api/reports")
async def api_list_reports(player: str = Query(...), user: dict = Depends(auth.require_user)):
    _require_follow(user, player)
    # Auto-saneado: un informe que lleve >15 min "generándose" está atascado (cuelgue o
    # reinicio); se marca como error para que no bloquee y el usuario pueda reintentar.
    await asyncio.to_thread(db.fail_stale_reports, player, 15,
                            "El informe tardó demasiado y se canceló. Vuelve a pedirlo.")
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


@router.delete("/api/reports/{report_id}")
async def api_delete_report(report_id: int, user: dict = Depends(auth.require_user)):
    rep = await asyncio.to_thread(db.get_report, report_id)
    if not rep:
        return JSONResponse({"error": "No existe ese informe."}, status_code=404)
    if not db.user_follows(user["id"], rep["player_tag"]):
        raise HTTPException(status_code=403, detail="No sigues a ese jugador.")
    await asyncio.to_thread(db.delete_report, report_id)
    return {"ok": True}


# --------------------------- Estado del Sensei (candado de retos) ---------------------------

@router.get("/api/sensei/status")
def api_sensei_status(user: dict = Depends(auth.require_user)):
    """Estado del candado del Sensei (para el botón Consultar), si la IA está lista y los
    modelos disponibles (los premium/Opus solo aparecen para administradores)."""
    is_admin = bool(user.get("is_admin"))
    return {"configured": coach.configured(),
            "gate": retos.sensei_gate(user["id"], is_admin),
            "models": coach.models_for(is_admin)}


@router.post("/api/sensei/reset")
def api_sensei_reset(user: dict = Depends(auth.require_user)):
    """Resetea el entrenamiento: abandona los retos del Sensei activos (solo si han
    pasado los días del candado o eres admin)."""
    gate = retos.sensei_gate(user["id"], bool(user.get("is_admin")))
    if not gate["can_reset"]:
        return JSONResponse({"error": f"Solo puedes resetear el entrenamiento tras {gate['gate_days']} días "
                                      "desde el último informe (o siendo admin)."}, status_code=403)
    n = db.reset_sensei_training(user["id"])
    return {"ok": True, "abandoned": n}
