"""
App FastAPI del tracker de Brawl Stars (multi-jugador).

Sigue varios tags a la vez. El poller en segundo plano recorre cada
POLL_INTERVAL_SECONDS todos los jugadores dados de alta y guarda sus partidas
nuevas. Los jugadores se añaden desde la web (o se siembra uno con
BRAWL_PLAYER_TAG en el .env).

Arrancar:  uvicorn app.main:app --reload --port 8000
Luego abre http://localhost:8000
"""

from __future__ import annotations

import os
import json
import random
import re
import time
import base64
import uuid
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Query, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import db, brawl_api, coach, assets, auth, bs_maps, detect, brawler_extra
from .api_common import (_require_follow, _filters, _get_player_cached,
                         _get_battlelog_cached, _ensure_player_profiles, _parse_player_tags)

SEED_TAG = os.environ.get("BRAWL_PLAYER_TAG", "").strip()

# Cuenta personal (la tuya), con tu hash ya existente. Se configura en el .env:
#   PERSONAL_USER=itxialdiak
#   PERSONAL_PASSWORD_HASH='$2a$14$...'   (entre comillas simples por los $)
PERSONAL_USER = os.environ.get("PERSONAL_USER", "").strip()
PERSONAL_PASSWORD_HASH = os.environ.get("PERSONAL_PASSWORD_HASH", "").strip()

# Interruptores de configuración / beta (compartidos con los routers): ver config.py
from .config import REGISTRATION_OPEN, REPORT_QUOTA_ENABLED, MONTHLY_REPORT_LIMIT, POLL_INTERVAL

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

_last_poll = {"new": None, "players": None, "error": None, "at": None}


_last_profile_refresh: dict = {}  # tag -> timestamp del último refresco de perfil


async def _poll_player(tag: str) -> int:
    """Sondea un jugador y guarda sus partidas nuevas. Devuelve cuántas."""
    # Refresca el perfil (nombre + icono + club) si falta el icono o cada hora,
    # para que el club aparezca también en jugadores añadidos antes de esta función.
    need = await asyncio.to_thread(db.player_needs_profile, tag)
    stale = (time.time() - _last_profile_refresh.get(tag, 0)) > 3600
    if need or stale:
        try:
            prof = await brawl_api.get_player(tag)
            await asyncio.to_thread(db.update_player_profile, tag,
                                    prof.get("name"), (prof.get("icon") or {}).get("id"),
                                    (prof.get("club") or {}).get("name"))
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
    total_new, errors = 0, []
    for tag in tags:
        try:
            total_new += await _poll_player(tag)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{tag}: {e}")
    _last_poll.update(new=total_new, players=len(tags),
                      error="; ".join(errors) if errors else None,
                      at=datetime.now(timezone.utc).isoformat())
    msg = f"[poll] {len(tags)} jugador(es), {total_new} partidas nuevas"
    if errors:
        msg += f" | errores: {'; '.join(errors)}"
    print(msg)
    return _last_poll


async def _poller():
    while True:
        try:
            await _poll_all()
        except Exception as e:  # noqa: BLE001
            _last_poll["error"] = str(e)
            _last_poll["at"] = datetime.now(timezone.utc).isoformat()
            print(f"[poll] error: {e}")
        await asyncio.sleep(POLL_INTERVAL)


EVENT_NOTIFY_INTERVAL = 300  # cada 5 min
SOON_HOURS = 24             # "empieza pronto" dentro de esta ventana


def _check_event_starts():
    """Avisa a seguidores y apuntados de la cercanía y el inicio de cada evento (idempotente)."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    for eid in db.event_ids_with_start():
        e = db.get_event(eid)
        if not e:
            continue
        start = detect.parse_event_date(e.get("date_start"))
        if not start:
            continue
        s = e.get("settings") or {}
        recipients = db.event_follower_ids(eid) + db.event_participant_user_ids(eid)
        changed = False
        if now >= start and not s.get("notified_started"):
            db.notify_many(recipients, "event_start", f"¡Empieza «{e.get('name')}»!",
                           "El evento que sigues ha comenzado. ¡Mucha suerte!", event_id=eid)
            s["notified_started"] = True; s["notified_soon"] = True; changed = True
        elif now < start and (start - now) <= timedelta(hours=SOON_HOURS) and not s.get("notified_soon"):
            db.notify_many(recipients, "event_soon", f"«{e.get('name')}» empieza pronto",
                           "El evento que sigues está a punto de empezar.", event_id=eid)
            s["notified_soon"] = True; changed = True
        if changed:
            db.update_event(eid, {"settings": s})


async def _event_notifier():
    while True:
        try:
            await asyncio.to_thread(_check_event_starts)
        except Exception as e:  # noqa: BLE001
            print(f"[notify] error: {e}")
        await asyncio.sleep(EVENT_NOTIFY_INTERVAL)


WIKI_UPDATE_INTERVAL = 24 * 3600  # una vez al día


async def _rebuild_roles_index():
    """Reconstruye data/roles_index.json (NOMBRE->[roles]) desde el catálogo + el
    dataset recién scrapeado, para que el filtro/agregación por rol no se
    desincronice de los roles que muestra la pestaña Brawlers."""
    try:
        cat = (await assets.get_brawler_catalog()).get("by_id") or {}
        index = {}
        for bid, c in cat.items():
            name = c.get("name")
            if not name:
                continue
            primary = brawler_extra.get(bid).get("role") or brawler_extra.role_primary_fallback(name) or c.get("role")
            secondary = brawler_extra.role_secondary(name)
            roles = ([primary] if primary else []) + ([secondary] if secondary and secondary != primary else [])
            if roles:
                index[name.upper()] = roles
        path = os.path.join(os.path.dirname(__file__), "data", "roles_index.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        print(f"[roles_index] no se pudo regenerar: {e}")


async def _wiki_updater():
    """Revisa a diario la wiki de Brawl Stars y Brawl Time Ninja, y regenera el
    dataset de brawlers (stats, súper, hipercarga, descripción y builds) si tiene
    más de ~20 h, para no re-scrapear en cada reinicio."""
    from . import wiki
    while True:
        try:
            fresh = os.path.exists(wiki.OUT_PATH) and \
                (time.time() - os.path.getmtime(wiki.OUT_PATH)) < 20 * 3600
            if not fresh:
                res = await wiki.refresh()
                await _rebuild_roles_index()
                print(f"[wiki] dataset de brawlers actualizado (wiki + builds): {res}")
            try:  # precachea imágenes a cuerpo entero de las skins equipadas por cualquier jugador
                from . import skins
                eq = await asyncio.to_thread(db.all_equipped_skins)
                if eq:
                    print(f"[skins] precache de imágenes: {await skins.refresh_missing(eq)}")
            except Exception as se:  # noqa: BLE001
                print(f"[skins] error precacheando skins: {se}")
        except Exception as e:  # noqa: BLE001
            print(f"[wiki] error actualizando el dataset: {e}")
        await asyncio.sleep(WIKI_UPDATE_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()

    # tester / betatest: cuenta VACÍA, la que se reparte a los testers.
    if not db.get_user_by_username("tester"):
        db.create_user("tester", auth.hash_password("betatest"))
        print("Cuenta beta creada: tester / betatest")
    tester_id = db.get_user_by_username("tester")["id"]

    # Tu cuenta personal con tu hash existente (desde el .env). No se borra nunca.
    personal_id = None
    if PERSONAL_USER and PERSONAL_PASSWORD_HASH:
        existing = db.get_user_by_username(PERSONAL_USER)
        if existing:
            personal_id = existing["id"]
        else:
            personal_id = db.create_user(PERSONAL_USER, PERSONAL_PASSWORD_HASH)
            print(f"Cuenta personal creada: {PERSONAL_USER}")
            if personal_id:
                # Migración única: tus jugadores de prueba -> a tu cuenta; tester vacío.
                db.reassign_players_for_personal_account(personal_id, tester_id)

    if SEED_TAG:  # opcional: siembra un jugador inicial desde el .env
        db.add_player(SEED_TAG)
        if personal_id:
            db.follow_player(personal_id, SEED_TAG)

    # Cualquier jugador sin dueño -> tu cuenta (o tester si no hay cuenta personal).
    db.link_orphan_players_to(personal_id or tester_id)

    via = "proxy RoyaleAPI" if brawl_api.using_proxy() else "API oficial"
    print(f"Endpoint de la API: {brawl_api.BASE}  ({via})")
    task = None
    if brawl_api.TOKEN:
        task = asyncio.create_task(_poller())
        print(f"Poller activo cada {POLL_INTERVAL}s. Jugadores: {db.active_player_tags() or 'ninguno (añádelos en la web)'}")
    else:
        print("⚠️  Falta BRAWL_API_TOKEN en .env; el poller está parado.")
    notifier = asyncio.create_task(_event_notifier())  # avisos de cercanía/inicio (Fase 6)
    wiki_task = asyncio.create_task(_wiki_updater())   # refresco diario de datos de brawlers
    yield
    if task:
        task.cancel()
    notifier.cancel()
    wiki_task.cancel()


app = FastAPI(title="Brawl Stars Tracker", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware, secret_key=auth.session_secret(),
    same_site="lax", https_only=False, max_age=60 * 60 * 24 * 30,
)


# --- Routers por área (ver app/routers/) ---
from .routers.auth import router as _r_auth
from .routers.wiki import router as _r_wiki
from .routers.admin import router as _r_admin
from .routers.catalog import router as _r_catalog
from .routers.notifications import router as _r_notifications
for _r in (_r_auth, _r_wiki, _r_admin, _r_catalog, _r_notifications):
    app.include_router(_r)

from .routers.players import router as _r_players
from .routers.analytics import router as _r_analytics
from .routers.modes import router as _r_modes
from .routers.rankings import router as _r_rankings
from .routers.brawlers import router as _r_brawlers
from .routers.battles import router as _r_battles
from .routers.coach import router as _r_coach
from .routers.events import router as _r_events
for _r in (_r_players, _r_analytics, _r_modes, _r_rankings, _r_brawlers, _r_battles, _r_coach, _r_events):
    app.include_router(_r)


# --------------------------- Sondeo (estado y disparo manual) ---------------------------
# Se quedan en main: dependen del estado vivo del poller (_last_poll y _poll_player).

@app.get("/api/status")
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


@app.post("/api/poll")
async def api_poll(player: str = Query(None), user: dict = Depends(auth.require_user)):
    if not brawl_api.TOKEN:
        return JSONResponse({"error": "Falta BRAWL_API_TOKEN en .env"}, status_code=400)
    tag = _require_follow(user, player) if player else None
    try:
        if tag:
            new = await _poll_player(tag)
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
        _last_poll["error"] = str(e)
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
