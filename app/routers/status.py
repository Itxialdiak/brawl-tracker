"""Estado del sondeo y disparo manual: /api/status, /api/server-status, /api/poll.

Extraído de main.py. Dependen del ESTADO VIVO del poller (`app.poller`): `_last_poll`,
`_poll_player`, `_is_maintenance`."""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Depends
from fastapi.responses import JSONResponse

from .. import db, brawl_api, auth, coach
from ..config import POLL_INTERVAL
from ..poller import _last_poll, _poll_player, _is_maintenance
from ..api_common import _require_follow

router = APIRouter()


@router.get("/api/status")
def api_status(user: dict = Depends(auth.require_user)):
    return {
        "configured": bool(brawl_api.TOKEN),
        "players": [p["tag"] for p in db.list_players_for_user(user["id"])],
        "poll_interval": POLL_INTERVAL,
        "api_base": brawl_api.BASE,
        "via_proxy": brawl_api.using_proxy(),
        "coach_configured": coach.configured(),
        "last_poll": _last_poll,
    }


@router.get("/api/server-status")
def api_server_status(user: dict = Depends(auth.require_user)):
    """Estado actual del servidor de Supercell + histórico de incidencias (con duración)."""
    cur = db.current_incident()
    return {"status": cur["kind"] if cur else "online",
            "since": cur["started_at"] if cur else None,
            "history": db.incident_history(60)}


@router.post("/api/poll")
async def api_poll(player: str = Query(None), user: dict = Depends(auth.require_user)):
    if not brawl_api.TOKEN:
        return JSONResponse({"error": "Falta BRAWL_API_TOKEN en .env"}, status_code=400)
    tag = _require_follow(user, player) if player else None
    try:
        if tag:
            new = await _poll_player(tag)
            await asyncio.to_thread(db.set_server_state, "online",
                                    datetime.now(timezone.utc).isoformat())  # 200: cierra incidencia
            _last_poll["maintenance"] = None
            return {"new": new, "players": 1, "at": datetime.now(timezone.utc).isoformat()}
        # Sin jugador: sondea solo los jugadores de este usuario.
        tags = [p["tag"] for p in await asyncio.to_thread(db.list_players_for_user, user["id"])]
        total = 0
        for t in tags:
            try:
                total += await _poll_player(t)
            except Exception as e:  # noqa: BLE001
                print(f"[poll usuario {user['id']}] {t}: {e}")
        return {"new": total, "players": len(tags), "at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:  # noqa: BLE001
        if _is_maintenance(e):     # mantenimiento de Supercell: estado, no error nuestro
            now = datetime.now(timezone.utc).isoformat()
            await asyncio.to_thread(db.set_server_state, "maintenance", now)
            cur = await asyncio.to_thread(db.current_incident)
            since = cur["started_at"] if cur else now
            _last_poll["maintenance"] = since
            return JSONResponse({"error": "El servidor de Supercell está en mantenimiento. "
                                          "El tracker reanudará el sondeo cuando termine.",
                                 "maintenance": since}, status_code=503)
        _last_poll["error"] = str(e)
        return JSONResponse({"error": str(e)}, status_code=502)
