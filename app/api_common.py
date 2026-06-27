"""Helpers compartidos por varios routers: validación de seguimiento, construcción de
filtros y caché corta de llamadas a la API (perfil de jugador, battlelog, alta de
nombres). Vive aparte de main.py para que los routers lo importen sin ciclos."""
import re
import time
import asyncio

from fastapi import HTTPException

from . import db, brawl_api


def _require_follow(user: dict, player: str) -> str:
    """Valida que el usuario sigue a ese jugador; devuelve el tag normalizado."""
    if not player:
        raise HTTPException(status_code=400, detail="Falta el jugador.")
    tag = db.normalize_tag(player)
    if not db.user_follows(user["id"], tag):
        raise HTTPException(status_code=403, detail="No sigues a ese jugador.")
    return tag


def _filters(player, mode, map_, brawler, vs, role=None):
    return {"player": player, "mode": mode, "map": map_, "brawler": brawler, "vs": vs, "role": role}


_player_obj_cache: dict = {}  # tag -> (timestamp, objeto crudo de la API)


async def _get_player_cached(tag: str) -> dict:
    """get_player con caché corta para no repetir llamadas (cabecera + rankings)."""
    now = time.time()
    c = _player_obj_cache.get(tag)
    if c and now - c[0] < 120:
        return c[1]
    p = await brawl_api.get_player(tag)
    _player_obj_cache[tag] = (now, p)
    return p


_battlelog_cache: dict = {}  # tag -> (timestamp, items)


async def _get_battlelog_cached(tag: str) -> list:
    """Battlelog con caché corta (90 s) para no repetir llamadas en la detección."""
    now = time.time()
    c = _battlelog_cache.get(tag)
    if c and now - c[0] < 90:
        return c[1]
    items = await brawl_api.get_battlelog(tag)
    _battlelog_cache[tag] = (now, items)
    return items


async def _ensure_player_profiles(tags: list) -> None:
    """Obtiene de la API el nombre (e icono/club) de los tags indicados y los guarda
    en `players`, para que los participantes se muestren con su nombre real."""
    if not tags or not brawl_api.TOKEN:
        return

    async def one(tag):
        try:
            p = await _get_player_cached(tag)
            name = p.get("name")
            if name:
                await asyncio.to_thread(db.add_player, tag, name,
                                        (p.get("icon") or {}).get("id"),
                                        (p.get("club") or {}).get("name"))
        except Exception:  # noqa: BLE001
            pass
    await asyncio.gather(*[one(t) for t in tags])


def _parse_player_tags(value) -> list:
    if isinstance(value, list):
        return [str(t) for t in value]
    return [t for t in re.split(r"[\s,;]+", str(value or "")) if t.strip()]
