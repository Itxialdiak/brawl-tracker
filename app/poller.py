"""Poller de jugadores: sondea el `battlelog` de cada jugador seguido y ACUMULA sus partidas
en SQLite (la API oficial solo guarda las 25 últimas de cada jugador).

Extraído de `main.py` para dejarlo mínimo. Mantiene el ESTADO VIVO del sondeo
(`_last_poll`, `_last_profile_refresh`), que consultan los endpoints `/api/status` y
`/api/poll` (en `routers/status.py`). Además, `players.py`/`public.py` usan `_poll_player`
para el sondeo inmediato al añadir o consultar un jugador por tag.

`main.py` arranca el bucle `_poller()` como tarea de fondo en su `lifespan`.
"""
import asyncio
import time
from datetime import datetime, timezone

from . import db, brawl_api
from .config import POLL_INTERVAL

# Resumen del último sondeo global (lo lee /api/status; lo mutan el poller y /api/poll).
_last_poll = {"new": None, "players": None, "error": None, "not_found": None,
              "maintenance": None, "at": None}

_last_profile_refresh: dict = {}  # tag -> timestamp del último refresco de perfil


def _is_maintenance(exc) -> bool:
    # 503 "API is currently in maintenance": estado de Supercell, NO un error nuestro.
    return "maintenance" in str(exc).lower()


async def _poll_player(tag: str) -> int:
    """Sondea un jugador y guarda sus partidas nuevas. Devuelve cuántas."""
    # Refresca el perfil (nombre + icono + club) si falta el icono o cada hora,
    # para que el club aparezca también en jugadores añadidos antes de esta función.
    need = await asyncio.to_thread(db.player_needs_profile, tag)
    stale = (time.time() - _last_profile_refresh.get(tag, 0)) > 3600
    if need or stale:
        try:
            prof = await brawl_api.get_player(tag)
            _club = prof.get("club") or {}
            await asyncio.to_thread(db.update_player_profile, tag,
                                    prof.get("name"), (prof.get("icon") or {}).get("id"),
                                    _club.get("name"), _club.get("tag"))
            await asyncio.to_thread(db.snapshot_brawlers, tag, prof.get("brawlers"))
            _last_profile_refresh[tag] = time.time()
        except Exception as e:  # noqa: BLE001
            print(f"[perfil] no se pudo refrescar {tag}: {e}")
    items = await brawl_api.get_battlelog(tag)
    new = await asyncio.to_thread(db.ingest_battles, items, tag)
    await asyncio.to_thread(db.mark_polled, tag)
    return new


async def _poll_all() -> dict:
    tags = await asyncio.to_thread(db.active_player_tags)
    total_new, errors, dead = 0, [], []
    any_ok = maint = False
    for tag in tags:
        try:
            total_new += await _poll_player(tag)
            await asyncio.to_thread(db.clear_player_error, tag)
            any_ok = True
        except brawl_api.NotFound:
            dead.append(tag)  # tag inexistente en la API: se omite, no es un error real
            await asyncio.to_thread(db.set_player_error, tag, "El tag no existe en la API de Brawl Stars (404).")
        except Exception as e:  # noqa: BLE001
            if _is_maintenance(e):      # 503 mantenimiento: estado de Supercell, no error nuestro
                maint = True
            else:
                errors.append(f"{tag}: {e}")
    now = datetime.now(timezone.utc).isoformat()
    if maint:
        state = "maintenance"
    elif any_ok or (dead and not errors):     # 200, o solo 404 (el servidor responde) = online
        state = "online"
    elif errors:                              # ninguna 200 y errores no-mantenimiento = caído
        state = "down"
    else:
        state = None                          # sin jugadores que sondear: nada que afirmar
    if state:
        await asyncio.to_thread(db.set_server_state, state, now)
    cur = await asyncio.to_thread(db.current_incident)
    _last_poll.update(new=total_new, players=len(tags),
                      error="; ".join(errors) if errors else None,
                      not_found=dead or None,
                      maintenance=(cur["started_at"] if cur and cur["kind"] == "maintenance" else None),
                      at=now)
    msg = f"[poll] {len(tags)} jugador(es), {total_new} partidas nuevas"
    if cur:
        msg += f" | servidor Supercell: {cur['kind']} desde {cur['started_at']}"
    if dead:
        msg += f" | tags inexistentes (omitidos): {', '.join(dead)}"
    if errors:
        msg += f" | errores: {'; '.join(errors)}"
    print(msg)
    return _last_poll


async def _poller():
    """Bucle de fondo: sondea a todos cada POLL_INTERVAL segundos. Lo arranca main.lifespan."""
    while True:
        try:
            await _poll_all()
        except Exception as e:  # noqa: BLE001
            _last_poll["error"] = str(e)
            _last_poll["at"] = datetime.now(timezone.utc).isoformat()
            print(f"[poll] error: {e}")
        await asyncio.sleep(POLL_INTERVAL)
