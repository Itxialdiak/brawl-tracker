"""Rutas del historial de batallas: ver y edición manual de estadísticas.

Extraído de main.py; se incluye con app.include_router()."""
from fastapi import APIRouter, Body, Query, Depends, HTTPException
from .. import db, auth
from ..api_common import _require_follow, _filters

router = APIRouter()


@router.get("/api/battles")
def api_battles(player: str = Query(None), mode: str = Query(None), map: str = Query(None),
                brawler: str = Query(None), vs: str = Query(None),
                limit: int = Query(25), offset: int = Query(0),
                user: dict = Depends(auth.require_user)):
    _require_follow(user, player)
    limit = max(1, min(limit, 100))
    return db.list_battles(_filters(player, mode, map, brawler, vs), limit=limit, offset=offset)


@router.put("/api/battles/{battle_id}/manual")
def api_set_manual(battle_id: str, payload: dict = Body(...),
                   user: dict = Depends(auth.require_user)):
    owner = db.battle_player_tag(battle_id)
    if owner and not db.user_follows(user["id"], owner):
        raise HTTPException(status_code=403, detail="No sigues a ese jugador.")
    def num(v):
        try:
            return int(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None
    db.set_manual_stats(
        battle_id,
        kills=num(payload.get("kills")), deaths=num(payload.get("deaths")),
        damage=num(payload.get("damage")), healing=num(payload.get("healing")),
        notes=(payload.get("notes") or None),
    )
    return {"ok": True, "battle_id": battle_id}
