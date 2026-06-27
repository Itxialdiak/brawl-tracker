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

SEED_TAG = os.environ.get("BRAWL_PLAYER_TAG", "").strip()
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "180"))

# Cuenta personal (la tuya), con tu hash ya existente. Se configura en el .env:
#   PERSONAL_USER=itxialdiak
#   PERSONAL_PASSWORD_HASH='$2a$14$...'   (entre comillas simples por los $)
PERSONAL_USER = os.environ.get("PERSONAL_USER", "").strip()
PERSONAL_PASSWORD_HASH = os.environ.get("PERSONAL_PASSWORD_HASH", "").strip()

# --- Interruptores de la beta -------------------------------------------------
# Para abrir el registro libre: pon REGISTRATION_OPEN = True (el endpoint se
# activa y el botón "Crear cuenta" deja de estar gris, todo desde aquí).
REGISTRATION_OPEN = False
# Para limitar el gasto de informes por usuario cuando abras la beta:
REPORT_QUOTA_ENABLED = False
MONTHLY_REPORT_LIMIT = 12  # informes por usuario y mes (cuando la cuota esté activa)
# ------------------------------------------------------------------------------

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
                print(f"[wiki] dataset de brawlers actualizado (wiki + builds): {res}")
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


# --------------------------- Autenticación ---------------------------

def _public_user(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"], "country": u.get("country"),
            "is_admin": bool(u.get("is_admin"))}


@app.get("/api/auth/config")
def api_auth_config():
    """Lo lee el frontend para (des)grisar el botón de registro."""
    return {"registration_open": REGISTRATION_OPEN}


@app.get("/api/auth/me")
def api_auth_me(request: Request):
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"error": "No has iniciado sesión."}, status_code=401)
    return _public_user(u)


@app.post("/api/auth/login")
def api_auth_login(request: Request, payload: dict = Body(...)):
    username = ((payload or {}).get("username") or "").strip()
    password = (payload or {}).get("password") or ""
    u = db.get_user_by_username(username) if username else None
    if not u or not auth.verify_password(password, u["password_hash"]):
        return JSONResponse({"error": "Usuario o contraseña incorrectos."}, status_code=401)
    request.session["user_id"] = u["id"]
    return _public_user(u)


@app.post("/api/auth/logout")
def api_auth_logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.post("/api/auth/register")
def api_auth_register(request: Request, payload: dict = Body(...)):
    # Interruptor: durante la beta REGISTRATION_OPEN = False -> el registro está cerrado.
    if not REGISTRATION_OPEN:
        return JSONResponse({"error": "El registro está cerrado durante la beta."}, status_code=403)
    username = ((payload or {}).get("username") or "").strip()
    password = (payload or {}).get("password") or ""
    if not (3 <= len(username) <= 20):
        return JSONResponse({"error": "El usuario debe tener entre 3 y 20 caracteres."}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"error": "La contraseña debe tener al menos 6 caracteres."}, status_code=400)
    uid = db.create_user(username, auth.hash_password(password))
    if uid is None:
        return JSONResponse({"error": "Ese nombre de usuario ya existe."}, status_code=409)
    request.session["user_id"] = uid
    return _public_user(db.get_user_by_id(uid))


@app.post("/api/auth/password")
def api_auth_password(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    current = (payload or {}).get("current") or ""
    new = (payload or {}).get("new") or ""
    if not auth.verify_password(current, user["password_hash"]):
        return JSONResponse({"error": "La contraseña actual no es correcta."}, status_code=403)
    if len(new) < 6:
        return JSONResponse({"error": "La nueva contraseña debe tener al menos 6 caracteres."}, status_code=400)
    db.set_user_password(user["id"], auth.hash_password(new))
    return {"ok": True}


@app.post("/api/auth/country")
def api_auth_country(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    country = ((payload or {}).get("country") or "").strip().lower()
    if country and (len(country) != 2 or not country.isalpha()):
        return JSONResponse({"error": "El país debe ser un código de 2 letras (p. ej. ES)."}, status_code=400)
    db.set_user_country(user["id"], country or None)
    return {"ok": True, "country": country or None}


def _require_follow(user: dict, player: str) -> str:
    """Valida que el usuario sigue a ese jugador; devuelve el tag normalizado."""
    if not player:
        raise HTTPException(status_code=400, detail="Falta el jugador.")
    tag = db.normalize_tag(player)
    if not db.user_follows(user["id"], tag):
        raise HTTPException(status_code=403, detail="No sigues a ese jugador.")
    return tag


# --------------------------- Jugadores ---------------------------

ICON_CDN = "https://cdn.brawlify.com/profile-icons/regular/{id}.png"


def _with_icon(p: dict) -> dict:
    p = dict(p)
    p["icon_url"] = ICON_CDN.format(id=p["icon_id"]) if p.get("icon_id") else None
    return p


@app.get("/api/players")
def api_players(user: dict = Depends(auth.require_user)):
    return [_with_icon(p) for p in db.list_players_for_user(user["id"])]


@app.post("/api/players")
async def api_add_player(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    raw = (payload or {}).get("tag", "")
    if not raw or not raw.strip():
        return JSONResponse({"error": "Falta el tag."}, status_code=400)
    if not brawl_api.TOKEN:
        return JSONResponse({"error": "Falta BRAWL_API_TOKEN en .env"}, status_code=400)
    tag = db.normalize_tag(raw)
    # Validamos contra la API que el jugador existe y de paso cogemos nombre + icono.
    try:
        profile = await brawl_api.get_player(tag)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "404" in msg or "notFound" in msg.lower():
            return JSONResponse({"error": f"No existe ningún jugador con el tag {tag}."}, status_code=404)
        return JSONResponse({"error": f"No se pudo validar el tag: {msg}"}, status_code=502)
    name = profile.get("name")
    icon_id = (profile.get("icon") or {}).get("id")
    club_name = (profile.get("club") or {}).get("name")
    is_new = await asyncio.to_thread(db.add_player, tag, name, icon_id, club_name)
    await asyncio.to_thread(db.follow_player, user["id"], tag)  # lo asocia a este usuario
    await asyncio.to_thread(db.snapshot_brawlers, tag, profile.get("brawlers"))  # colección inicial
    # Sondeo inmediato para que aparezcan datos al momento.
    try:
        await _poll_player(tag)
    except Exception as e:  # noqa: BLE001
        print(f"[add] aviso: sondeo inicial de {tag} falló: {e}")
    return {"tag": tag, "name": name, "is_new": is_new}


@app.delete("/api/players/{tag}")
def api_remove_player(tag: str, user: dict = Depends(auth.require_user)):
    # Solo lo desvincula de este usuario; si no lo sigue nadie más, db lo limpia.
    db.unfollow_player(user["id"], tag)
    return {"removed": db.normalize_tag(tag)}


# --------------------------- Estadísticas ---------------------------

def _filters(player, mode, map_, brawler, vs):
    return {"player": player, "mode": mode, "map": map_, "brawler": brawler, "vs": vs}


@app.get("/api/overview")
def api_overview(player: str = Query(None), mode: str = Query(None), map: str = Query(None),
                 brawler: str = Query(None), vs: str = Query(None),
                 user: dict = Depends(auth.require_user)):
    _require_follow(user, player)
    return db.overview(_filters(player, mode, map, brawler, vs))


@app.get("/api/winrate")
def api_winrate(by: str = Query("brawler"), player: str = Query(None), mode: str = Query(None),
                map: str = Query(None), brawler: str = Query(None), vs: str = Query(None),
                user: dict = Depends(auth.require_user)):
    _require_follow(user, player)
    try:
        return db.winrate_by(by, _filters(player, mode, map, brawler, vs))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/vs")
def api_vs(player: str = Query(None), mode: str = Query(None), map: str = Query(None),
           brawler: str = Query(None), user: dict = Depends(auth.require_user)):
    _require_follow(user, player)
    return db.winrate_vs(_filters(player, mode, map, brawler, None))


@app.get("/api/report")
def api_report(player: str = Query(None), mode: str = Query(None), map: str = Query(None),
               brawler: str = Query(None), user: dict = Depends(auth.require_user)):
    """Cálculos derivados para el Informe (destacados, datos cruzados, serie de trofeos)."""
    _require_follow(user, player)
    return db.report_analytics(_filters(player, mode, map, brawler, None))


@app.get("/api/filters")
def api_filters(player: str = Query(None), user: dict = Depends(auth.require_user)):
    _require_follow(user, player)
    return db.distinct_values(player)


# La rotación oficial mezcla eventos de Copas y de Competitivo (Ranked). Los de
# Competitivo van en un bloque sincronizado de varios días (los de Copas rotan a
# diario) y son de uno de estos modos; así los separamos para mostrarlos aparte.
_RANKED_MODES = {"gemGrab", "brawlBall", "heist", "knockout", "hotZone", "bounty"}


def _slot_hours(start: str | None, end: str | None) -> float:
    try:
        f = "%Y%m%dT%H%M%S"
        return (datetime.strptime(end[:15], f) - datetime.strptime(start[:15], f)).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        return 0.0


_rotation_cache = {"at": 0.0, "data": None}


@app.get("/api/rotation")
async def api_rotation(player: str = Query(None), user: dict = Depends(auth.require_user)):
    """'Qué jugar ahora': rotación actual cruzada con tu win rate por mapa y tus mejores brawlers."""
    tag = _require_follow(user, player)
    if not brawl_api.TOKEN:
        return JSONResponse({"error": "Falta BRAWL_API_TOKEN en .env"}, status_code=400)
    now = time.time()
    if _rotation_cache["data"] is None or now - _rotation_cache["at"] > 600:  # 10 min
        try:
            raw = await brawl_api.get_events_rotation()
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": f"No se pudo leer la rotación: {e}"}, status_code=502)
        events = []
        for it in (raw or []):
            evt = it.get("event") or {}
            map_ = evt.get("map") or it.get("map")
            if not map_:
                continue
            mode = evt.get("mode") or it.get("mode")
            start, end = it.get("startTime"), it.get("endTime")
            ranked = mode in _RANKED_MODES and _slot_hours(start, end) >= 36
            events.append({"mode": mode, "map": map_,
                           "startTime": start, "endTime": end,
                           "category": "ranked" if ranked else "trophy"})
        _rotation_cache.update(at=now, data=events)
    analysis = await asyncio.to_thread(db.rotation_analysis, tag, _rotation_cache["data"])
    return {"events": analysis}


# --------------------------- Rankings ---------------------------

def _ranking_country(user: dict, scope: str) -> str:
    """'global' o el código de país del usuario, según el scope pedido."""
    if scope == "national" and user.get("country"):
        return user["country"].lower()
    return "global"


@app.get("/api/player-profile")
async def api_player_profile(player: str = Query(None), user: dict = Depends(auth.require_user)):
    """Perfil del jugador: club y colección de brawlers (para el selector de rankings)."""
    tag = _require_follow(user, player)
    if not brawl_api.TOKEN:
        return JSONResponse({"error": "Falta BRAWL_API_TOKEN en .env"}, status_code=400)
    try:
        prof = await _get_player_cached(tag)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"No se pudo leer el perfil: {e}"}, status_code=502)
    brawlers = sorted(
        [{"id": b.get("id"), "name": b.get("name"), "trophies": b.get("trophies") or 0,
          "power": b.get("power"), "rank": b.get("rank")} for b in (prof.get("brawlers") or [])],
        key=lambda b: b["trophies"], reverse=True)
    club = prof.get("club") or {}
    return {
        "tag": tag, "name": prof.get("name"), "trophies": prof.get("trophies"),
        "highest_trophies": prof.get("highestTrophies"),
        "victories_3v3": prof.get("3vs3Victories"),
        "victories_solo": prof.get("soloVictories"),
        "victories_duo": prof.get("duoVictories"),
        "club": {"tag": club.get("tag"), "name": club.get("name")} if club.get("tag") else None,
        "brawlers": brawlers,
    }


@app.get("/api/rankings")
async def api_rankings(kind: str = Query("players"), scope: str = Query("global"),
                       brawler_id: int = Query(None), user: dict = Depends(auth.require_user)):
    if not brawl_api.TOKEN:
        return JSONResponse({"error": "Falta BRAWL_API_TOKEN en .env"}, status_code=400)
    if kind not in ("players", "clubs", "brawlers"):
        return JSONResponse({"error": "Tipo de ranking no válido."}, status_code=400)
    if kind == "brawlers" and not brawler_id:
        return JSONResponse({"error": "Falta el brawler."}, status_code=400)
    country = _ranking_country(user, scope)
    try:
        items = await brawl_api.get_rankings(kind, country=country, brawler_id=brawler_id)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"No se pudo leer el ranking: {e}"}, status_code=502)
    return {"kind": kind, "scope": "national" if country != "global" else "global",
            "country": None if country == "global" else country, "items": items}


@app.get("/api/club")
async def api_club(tag: str = Query(None), user: dict = Depends(auth.require_user)):
    """Datos del club + miembros ordenados por trofeos (ranking interno)."""
    if not brawl_api.TOKEN:
        return JSONResponse({"error": "Falta BRAWL_API_TOKEN en .env"}, status_code=400)
    if not tag:
        return JSONResponse({"error": "Falta el club."}, status_code=400)
    try:
        club = await brawl_api.get_club(tag)
        members = await brawl_api.get_club_members(tag)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"No se pudo leer el club: {e}"}, status_code=502)
    return {
        "tag": club.get("tag"), "name": club.get("name"), "trophies": club.get("trophies"),
        "required_trophies": club.get("requiredTrophies"), "type": club.get("type"),
        "member_count": len(members),
        "members": [{"tag": m.get("tag"), "name": m.get("name"), "role": m.get("role"),
                     "trophies": m.get("trophies"), "icon_id": (m.get("icon") or {}).get("id")}
                    for m in members],
    }


# --------------------------- Rankings personalizados (liguillas) ---------------------------

_profile_cache: dict = {}  # tag -> (timestamp, data)
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


def _cr_public(cr: dict) -> dict:
    return {"id": cr["id"], "name": cr["name"], "share_token": cr["share_token"],
            "count": len(cr.get("player_tags") or []), "owned": cr.get("owned", True)}


async def _fetch_one_profile(tag: str) -> dict:
    now = time.time()
    cached = _profile_cache.get(tag)
    if cached and now - cached[0] < 300:
        return cached[1]
    try:
        p = await brawl_api.get_player(tag)
        data = {"tag": tag, "name": p.get("name"), "trophies": p.get("trophies"),
                "icon_id": (p.get("icon") or {}).get("id"),
                "club": (p.get("club") or {}).get("name")}
    except Exception:  # noqa: BLE001
        data = {"tag": tag, "name": None, "trophies": None, "icon_id": None, "club": None, "error": True}
    _profile_cache[tag] = (now, data)
    return data


async def _fetch_standings(tags: list) -> dict:
    results = await asyncio.gather(*[_fetch_one_profile(t) for t in tags]) if tags else []
    found = [r for r in results if not r.get("error") and r.get("trophies") is not None]
    missing = [r["tag"] for r in results if r.get("error") or r.get("trophies") is None]
    found.sort(key=lambda r: r["trophies"], reverse=True)
    for i, r in enumerate(found):
        r["rank"] = i + 1
    return {"players": found, "missing": missing}


@app.get("/api/custom-rankings")
def api_cr_list(user: dict = Depends(auth.require_user)):
    return {"rankings": [_cr_public(r) for r in db.list_custom_rankings_for_user(user["id"])]}


@app.post("/api/custom-rankings")
def api_cr_create(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    name = ((payload or {}).get("name") or "").strip()
    tags = _parse_player_tags((payload or {}).get("players"))
    if not name:
        return JSONResponse({"error": "Ponle un nombre a la liguilla."}, status_code=400)
    if not tags:
        return JSONResponse({"error": "Añade al menos un player ID."}, status_code=400)
    if len(tags) > 100:
        return JSONResponse({"error": "Máximo 100 jugadores por liguilla."}, status_code=400)
    rid = db.create_custom_ranking(user["id"], name, tags)
    cr = db.get_custom_ranking(rid); cr["owned"] = True
    return _cr_public(cr)


@app.get("/api/custom-rankings/{rid}")
def api_cr_get(rid: int, user: dict = Depends(auth.require_user)):
    if not db.user_can_view_ranking(user["id"], rid):
        return JSONResponse({"error": "No encontrado."}, status_code=404)
    cr = db.get_custom_ranking(rid)
    if not cr:
        return JSONResponse({"error": "No encontrado."}, status_code=404)
    return {"id": cr["id"], "name": cr["name"], "share_token": cr["share_token"],
            "players": cr["player_tags"], "owned": cr["owner_user_id"] == user["id"]}


@app.put("/api/custom-rankings/{rid}")
def api_cr_update(rid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    name = ((payload or {}).get("name") or "").strip()
    tags = _parse_player_tags((payload or {}).get("players"))
    if not name:
        return JSONResponse({"error": "Ponle un nombre."}, status_code=400)
    if not tags:
        return JSONResponse({"error": "Añade al menos un player ID."}, status_code=400)
    if len(tags) > 100:
        return JSONResponse({"error": "Máximo 100 jugadores."}, status_code=400)
    if not db.update_custom_ranking(rid, user["id"], name, tags):
        return JSONResponse({"error": "Solo el dueño puede editar esta liguilla."}, status_code=403)
    cr = db.get_custom_ranking(rid); cr["owned"] = True
    return _cr_public(cr)


@app.delete("/api/custom-rankings/{rid}")
def api_cr_delete(rid: int, user: dict = Depends(auth.require_user)):
    return {"ok": True, "result": db.delete_or_unsubscribe_ranking(user["id"], rid)}


@app.get("/api/custom-rankings/{rid}/standings")
async def api_cr_standings(rid: int, user: dict = Depends(auth.require_user)):
    if not db.user_can_view_ranking(user["id"], rid):
        return JSONResponse({"error": "No encontrado."}, status_code=404)
    cr = db.get_custom_ranking(rid)
    if not cr:
        return JSONResponse({"error": "No encontrado."}, status_code=404)
    if not brawl_api.TOKEN:
        return JSONResponse({"error": "Falta BRAWL_API_TOKEN en .env"}, status_code=400)
    data = await _fetch_standings(cr["player_tags"])
    return {"id": cr["id"], "name": cr["name"], **data}


@app.get("/api/shared-ranking")
def api_shared(token: str = Query(None), user: dict = Depends(auth.require_user)):
    if not token:
        return JSONResponse({"error": "Falta el enlace."}, status_code=400)
    cr = db.get_custom_ranking_by_token(token)
    if not cr:
        return JSONResponse({"error": "Enlace no válido o liguilla borrada."}, status_code=404)
    return {"id": cr["id"], "name": cr["name"], "count": len(cr["player_tags"]),
            "owned": cr["owner_user_id"] == user["id"],
            "already": db.user_can_view_ranking(user["id"], cr["id"])}


@app.post("/api/custom-rankings/import")
def api_cr_import(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    token = ((payload or {}).get("token") or "").strip()
    cr = db.get_custom_ranking_by_token(token)
    if not cr:
        return JSONResponse({"error": "Enlace no válido."}, status_code=404)
    if cr["owner_user_id"] == user["id"]:
        return {"ok": True, "id": cr["id"], "name": cr["name"], "self": True}
    db.subscribe_ranking(user["id"], cr["id"])
    return {"ok": True, "id": cr["id"], "name": cr["name"]}


@app.get("/api/rankings-order")
def api_order_get(user: dict = Depends(auth.require_user)):
    return {"order": db.get_rankings_order(user["id"])}


@app.post("/api/rankings-order")
def api_order_set(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    order = (payload or {}).get("order")
    if not isinstance(order, list):
        return JSONResponse({"error": "Orden no válido."}, status_code=400)
    db.set_rankings_order(user["id"], [str(x) for x in order][:300])
    return {"ok": True}


# --------------------------- Wiki / Guía de estrategia ---------------------------

_PROP_KINDS = {"edit", "create_section", "create_subsection", "create_separator", "delete", "reorder"}


@app.get("/api/wiki/tree")
def api_wiki_tree(user: dict = Depends(auth.require_user)):
    return {"tree": db.get_wiki_tree(), "is_admin": bool(user.get("is_admin")),
            "pending": db.count_pending_proposals() if user.get("is_admin") else 0}


@app.get("/api/wiki/node/{nid}")
def api_wiki_node(nid: int, user: dict = Depends(auth.require_user)):
    node = db.get_wiki_node(nid)
    if not node:
        return JSONResponse({"error": "No encontrado."}, status_code=404)
    return {"id": node["id"], "type": node["type"], "title": node["title"],
            "body": node.get("body"), "parent_id": node.get("parent_id")}


@app.post("/api/wiki/proposals")
def api_wiki_propose(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    p = payload or {}
    kind = (p.get("kind") or "").strip()
    if kind not in _PROP_KINDS:
        return JSONResponse({"error": "Tipo de cambio no válido."}, status_code=400)
    summary = (p.get("summary") or "").strip()
    justification = (p.get("justification") or "").strip()
    if not summary:
        return JSONResponse({"error": "Describe brevemente el cambio."}, status_code=400)
    if not justification:
        return JSONResponse({"error": "Justifica el cambio."}, status_code=400)
    node_id = p.get("node_id")
    data = p.get("data") or {}
    # Validaciones mínimas por tipo
    if kind in ("edit", "delete") and not node_id:
        return JSONResponse({"error": "Falta el nodo objetivo."}, status_code=400)
    if kind == "edit" and not (data.get("title") or "").strip():
        return JSONResponse({"error": "El título no puede quedar vacío."}, status_code=400)
    if kind in ("create_section", "create_separator") and not (data.get("title") or "").strip():
        return JSONResponse({"error": "Ponle un título."}, status_code=400)
    if kind == "create_subsection":
        if not data.get("parent_id"):
            return JSONResponse({"error": "Indica a qué sección pertenece."}, status_code=400)
        if not (data.get("title") or "").strip():
            return JSONResponse({"error": "Ponle un título."}, status_code=400)
    pid = db.create_proposal(user["id"], kind, node_id, data, summary, justification)
    return {"ok": True, "id": pid}


_WIKI_MEDIA_DIR = os.path.join(FRONTEND_DIR, "media", "wiki")
_IMG_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}


@app.post("/api/wiki/upload-image")
def api_wiki_upload(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    data = (payload or {}).get("data") or ""
    mime = ((payload or {}).get("mime") or "").lower()
    if data.startswith("data:"):
        head, _, b64 = data.partition(",")
        if not mime and ":" in head and ";" in head:
            mime = head[head.index(":") + 1:head.index(";")].lower()
        data = b64
    ext = _IMG_EXT.get(mime)
    if not ext:
        return JSONResponse({"error": "Formato no admitido (usa PNG, JPG, GIF o WEBP)."}, status_code=400)
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "Imagen no válida."}, status_code=400)
    if not raw:
        return JSONResponse({"error": "Imagen vacía."}, status_code=400)
    if len(raw) > 6 * 1024 * 1024:
        return JSONResponse({"error": "La imagen supera los 6 MB."}, status_code=400)
    os.makedirs(_WIKI_MEDIA_DIR, exist_ok=True)
    name = uuid.uuid4().hex + ext
    with open(os.path.join(_WIKI_MEDIA_DIR, name), "wb") as f:
        f.write(raw)
    return {"ok": True, "url": "/static/media/wiki/" + name}


# --------------------------- Administración ---------------------------

@app.get("/api/admin/proposals")
def api_admin_proposals(status: str = Query("pending"), admin: dict = Depends(auth.require_admin)):
    st = status if status in ("pending", "approved", "rejected", "all") else "pending"
    return {"proposals": db.list_proposals(None if st == "all" else st)}


@app.get("/api/admin/proposals/{pid}")
def api_admin_proposal_detail(pid: int, admin: dict = Depends(auth.require_admin)):
    p = db.get_proposal(pid)
    if not p:
        return JSONResponse({"error": "No encontrado."}, status_code=404)
    current = db.get_wiki_node(p["node_id"]) if p.get("node_id") else None
    parent = None
    if p["kind"] == "create_subsection" and p["payload"].get("parent_id"):
        parent = db.get_wiki_node(p["payload"]["parent_id"])
    return {"proposal": p, "current": current, "parent": parent}


@app.post("/api/admin/proposals/{pid}/approve")
def api_admin_approve(pid: int, admin: dict = Depends(auth.require_admin)):
    ok = db.apply_proposal(pid, admin["id"])
    return {"ok": ok}


@app.post("/api/admin/proposals/{pid}/reject")
def api_admin_reject(pid: int, admin: dict = Depends(auth.require_admin)):
    p = db.get_proposal(pid)
    if not p or p["status"] != "pending":
        return JSONResponse({"error": "No disponible."}, status_code=400)
    db.set_proposal_status(pid, "rejected", admin["id"])
    return {"ok": True}


@app.post("/api/admin/proposals/approve-all")
def api_admin_approve_all(admin: dict = Depends(auth.require_admin)):
    pend = db.list_proposals("pending")
    # aplicar de más antigua a más nueva
    n = 0
    for p in sorted(pend, key=lambda x: x["id"]):
        if db.apply_proposal(p["id"], admin["id"]):
            n += 1
    return {"ok": True, "approved": n}


@app.get("/api/admin/users")
def api_admin_users(admin: dict = Depends(auth.require_admin)):
    return {"users": db.list_users()}


@app.post("/api/admin/users")
def api_admin_user_create(payload: dict = Body(...), admin: dict = Depends(auth.require_admin)):
    username = ((payload or {}).get("username") or "").strip()
    password = (payload or {}).get("password") or ""
    if not username or len(username) < 3:
        return JSONResponse({"error": "Usuario inválido (mínimo 3 caracteres)."}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"error": "La contraseña debe tener al menos 6 caracteres."}, status_code=400)
    uid = db.create_user(username, auth.hash_password(password))
    if uid is None:
        return JSONResponse({"error": "Ese usuario ya existe."}, status_code=409)
    if (payload or {}).get("is_admin"):
        db.set_user_admin(uid, True)
    return {"ok": True, "id": uid}


@app.delete("/api/admin/users/{uid}")
def api_admin_user_delete(uid: int, admin: dict = Depends(auth.require_admin)):
    if uid == admin["id"]:
        return JSONResponse({"error": "No puedes borrarte a ti mismo."}, status_code=400)
    db.delete_user(uid)
    return {"ok": True}


@app.post("/api/admin/users/{uid}/admin")
def api_admin_user_setadmin(uid: int, payload: dict = Body(...), admin: dict = Depends(auth.require_admin)):
    val = bool((payload or {}).get("is_admin"))
    if uid == admin["id"] and not val:
        return JSONResponse({"error": "No puedes quitarte tus propios permisos."}, status_code=400)
    db.set_user_admin(uid, val)
    return {"ok": True}


@app.post("/api/admin/users/{uid}/password")
def api_admin_user_password(uid: int, payload: dict = Body(...), admin: dict = Depends(auth.require_admin)):
    pw = (payload or {}).get("password") or ""
    if len(pw) < 6:
        return JSONResponse({"error": "Mínimo 6 caracteres."}, status_code=400)
    db.set_user_password(uid, auth.hash_password(pw))
    return {"ok": True}


@app.get("/api/admin/history")
def api_admin_history(admin: dict = Depends(auth.require_admin)):
    return {"history": db.list_wiki_history()}


@app.post("/api/admin/history/{hid}/revert")
def api_admin_history_revert(hid: int, admin: dict = Depends(auth.require_admin)):
    ok = db.revert_wiki_version(hid, admin["id"])
    return {"ok": ok}


@app.get("/api/assets")
async def api_assets(user: dict = Depends(auth.require_user)):
    """Retratos de brawlers, iconos de modo (con color) e imágenes de mapas (Brawlify)."""
    return await assets.get_assets()


@app.get("/api/bs/modes-maps")
async def api_bs_modes_maps(user: dict = Depends(auth.require_user)):
    """Catálogo de modos y mapas de Brawl Stars con el icono real de cada modo."""
    data = await assets.get_assets()
    mmap = data.get("modes") or {}

    def icon_for(es_name):
        en = (bs_maps.EN.get(es_name) or es_name).lower()
        info = mmap.get(en)
        if not info and "showdown" in en:
            info = mmap.get("solo showdown") or mmap.get("showdown")
        return (info or {}).get("icon")
    modes = [{**m, "icon": icon_for(m["name"])} for m in bs_maps.catalog()]
    return {"modes": modes}


# --------------------------- Brawlers (apartado tipo Brawlify) ---------------------------

def _rank_band(trophies) -> str:
    """Banda de rango por trofeos del brawler (icono en el frontend)."""
    t = trophies or 0
    if t >= 3000: return "p3"
    if t >= 2000: return "p2"
    if t >= 1000: return "p1"
    if t >= 750:  return "gold"
    if t >= 500:  return "silver"
    if t >= 250:  return "bronze"
    return "wood"


@app.get("/api/brawlers")
async def api_brawlers(player: str = Query(None), user: dict = Depends(auth.require_user)):
    """Rejilla del apartado Brawlers: contadores, rating y todos los brawlers con tu
    colección fusionada (nivel, rank, loadout poseído y tu win rate)."""
    tag = _require_follow(user, player)
    catalog = await assets.get_brawler_catalog()
    by_id = catalog.get("by_id") or {}
    totals = catalog.get("totals") or {}
    # Perfil (cacheado 120 s): stats de cuenta + refresco de la colección al abrir.
    account = {}
    try:
        prof = await _get_player_cached(tag)
        if prof.get("brawlers"):
            await asyncio.to_thread(db.snapshot_brawlers, tag, prof.get("brawlers"))
        account = {"trophies": prof.get("trophies"), "highest_trophies": prof.get("highestTrophies"),
                   "victories_3v3": prof.get("3vs3Victories"), "victories_solo": prof.get("soloVictories"),
                   "victories_duo": prof.get("duoVictories"), "exp_level": prof.get("expLevel")}
    except Exception as e:  # noqa: BLE001
        print(f"[brawlers] no se pudo leer el perfil de {tag}: {e}")
    collection = await asyncio.to_thread(db.get_collection, tag)
    coll_by_id = {c["brawler_id"]: c for c in collection}
    wr = await asyncio.to_thread(db.winrate_by, "brawler", {"player": tag})
    wr_by_name = {(r["label"] or "").upper(): r for r in wr}
    hc_ids = brawler_extra.hypercharge_ids()

    items = []
    for bid, cat in by_id.items():
        c = coll_by_id.get(bid)
        name = cat.get("name")
        w = wr_by_name.get((name or "").upper())
        owned_sp = set(c["star_power_ids"]) if c else set()
        owned_gd = set(c["gadget_ids"]) if c else set()
        ex = brawler_extra.get(bid)
        role = ex.get("role") or brawler_extra.role_primary_fallback(name) or cat.get("role")
        items.append({
            "id": bid, "name": name, "role": role,
            "role_secondary": brawler_extra.role_secondary(name),
            "hypercharge_icon": (ex.get("hypercharge") or {}).get("icon"),
            "rarity": cat.get("rarity"),
            "portrait": cat.get("portrait"),
            "owned": c is not None,
            "power": c["power"] if c else None,
            "rank": c["rank"] if c else None,
            "trophies": c["trophies"] if c else None,
            "rank_band": _rank_band(c["trophies"]) if c else None,
            "prestige": c.get("prestige_level") if c else None,
            "star_powers": [{"icon": s.get("icon"), "owned": s.get("id") in owned_sp}
                            for s in (cat.get("star_powers") or [])],
            "gadgets": [{"icon": g.get("icon"), "owned": g.get("id") in owned_gd}
                        for g in (cat.get("gadgets") or [])],
            "owned_star_powers": len(owned_sp),
            "total_star_powers": len(cat.get("star_powers") or []),
            "owned_gadgets": len(owned_gd),
            "total_gadgets": len(cat.get("gadgets") or []),
            "has_hypercharge": bid in hc_ids,
            "owns_hypercharge": bool(c and c.get("hypercharge_ids")),
            "your_winrate": w["winrate"] if w else None,
            "your_battles": w["total"] if w else 0,
        })

    counts = await asyncio.to_thread(db.collection_counts, tag)
    rating = await asyncio.to_thread(db.account_rating, tag,
                                     {**totals, "hypercharges": brawler_extra.hypercharge_total()})
    counters = {
        "brawlers": {"owned": counts["brawlers"], "total": totals.get("brawlers") or len(by_id)},
        "star_powers": {"owned": counts["star_powers_owned"], "total": totals.get("star_powers") or 0},
        "gadgets": {"owned": counts["gadgets_owned"], "total": totals.get("gadgets") or 0},
        "hypercharges": {"owned": counts["hypercharges_owned"], "total": brawler_extra.hypercharge_total()},
    }
    return {"counters": counters, "rating": rating, "account": account, "brawlers": items}


@app.get("/api/account-rating")
async def api_account_rating(player: str = Query(None), user: dict = Depends(auth.require_user)):
    """Solo el rating de cuenta (para mostrarlo también en Estadísticas, sin cargar
    toda la rejilla de brawlers)."""
    tag = _require_follow(user, player)
    catalog = await assets.get_brawler_catalog()
    try:
        prof = await _get_player_cached(tag)
        if prof.get("brawlers"):
            await asyncio.to_thread(db.snapshot_brawlers, tag, prof.get("brawlers"))
    except Exception as e:  # noqa: BLE001
        print(f"[rating] no se pudo refrescar {tag}: {e}")
    rating = await asyncio.to_thread(db.account_rating, tag,
                                     {**(catalog.get("totals") or {}), "hypercharges": brawler_extra.hypercharge_total()})
    return {"rating": rating}


@app.get("/api/brawler/{brawler_id}")
async def api_brawler_detail(brawler_id: int, player: str = Query(None),
                             user: dict = Depends(auth.require_user)):
    """Ficha de un brawler: catálogo + lo que posees + dataset curado (hipercarga,
    stats por nivel, builds) + tu win rate con él, global y por modo."""
    tag = _require_follow(user, player)
    catalog = await assets.get_brawler_catalog()
    cat = (catalog.get("by_id") or {}).get(brawler_id)
    if not cat:
        return JSONResponse({"error": "Brawler no encontrado en el catálogo."}, status_code=404)
    collection = await asyncio.to_thread(db.get_collection, tag)
    c = next((x for x in collection if x["brawler_id"] == brawler_id), None)
    owned_sp = set(c["star_power_ids"]) if c else set()
    owned_gd = set(c["gadget_ids"]) if c else set()

    extra = brawler_extra.get(brawler_id)

    def merge_abilities(cat_items, owned, es_list):
        """Funde el catálogo (icono + lo poseído) con el nombre/efecto en español
        de la wiki (emparejado por orden)."""
        es_list = es_list or []
        out = []
        for i, it in enumerate(cat_items or []):
            es = es_list[i] if i < len(es_list) else {}
            out.append({"id": it.get("id"), "name": es.get("name") or it.get("name"),
                        "icon": it.get("icon"),
                        "description": es.get("description") or it.get("description"),
                        "owned": it.get("id") in owned})
        return out

    name = cat.get("name")
    bname = ((c["brawler_name"] if c else name) or name or "").upper()
    filt = {"player": tag, "brawler": bname}
    ov = await asyncio.to_thread(db.overview, filt)
    by_mode = await asyncio.to_thread(db.winrate_by, "mode", filt)
    skin = {"id": c.get("skin_id"), "name": c.get("skin_name")} if (c and c.get("skin_id")) else None
    image_full = extra.get("body_image") or cat.get("image_full")
    if skin and skin.get("name"):
        from . import wiki
        skin_url = await wiki.resolve_skin_image(name, skin["name"])
        if skin_url:
            image_full = skin_url       # muestra la skin equipada si la encontramos
            skin["image"] = skin_url

    return {
        "id": brawler_id, "name": name,
        "description": extra.get("description_es") or cat.get("description"),
        "role": extra.get("role") or brawler_extra.role_primary_fallback(name) or cat.get("role"),
        "role_secondary": brawler_extra.role_secondary(name),
        "rarity": cat.get("rarity"),
        "image_full": image_full, "portrait": cat.get("portrait"),
        "attack": extra.get("attack"),
        "passive": extra.get("passive"),
        "super": extra.get("super"),
        "star_powers": merge_abilities(cat.get("star_powers"), owned_sp, extra.get("star_powers_es")),
        "gadgets": merge_abilities(cat.get("gadgets"), owned_gd, extra.get("gadgets_es")),
        "owned": c is not None,
        "power": c["power"] if c else None,
        "rank": c["rank"] if c else None,
        "trophies": c["trophies"] if c else None,
        "highest_trophies": c["highest_trophies"] if c else None,
        "rank_band": _rank_band(c["trophies"]) if c else None,
        "prestige_level": c.get("prestige_level") if c else None,
        "skin": skin,
        "gears_owned": len(c["gear_ids"]) if c else 0,
        "has_hypercharge": brawler_id in brawler_extra.hypercharge_ids(),
        "owns_hypercharge": bool(c and c.get("hypercharge_ids")),
        "hypercharge": extra.get("hypercharge"),
        "stats_by_level": extra.get("stats_by_level"),
        "builds": extra.get("builds") or [],
        "your": {
            "winrate": ov.get("winrate"), "battles": ov.get("total"),
            "by_mode": [{"mode": m["label"], "winrate": m["winrate"], "battles": m["total"]}
                        for m in by_mode],
        },
    }


@app.get("/api/battles")
def api_battles(player: str = Query(None), mode: str = Query(None), map: str = Query(None),
                brawler: str = Query(None), vs: str = Query(None),
                limit: int = Query(25), offset: int = Query(0),
                user: dict = Depends(auth.require_user)):
    _require_follow(user, player)
    limit = max(1, min(limit, 100))
    return db.list_battles(_filters(player, mode, map, brawler, vs), limit=limit, offset=offset)


@app.put("/api/battles/{battle_id}/manual")
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


@app.post("/api/reports")
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


@app.get("/api/reports")
async def api_list_reports(player: str = Query(...), user: dict = Depends(auth.require_user)):
    _require_follow(user, player)
    rows = await asyncio.to_thread(db.list_reports, player)
    return [_public_report(r, with_content=False) for r in rows]


@app.get("/api/reports/{report_id}")
async def api_get_report(report_id: int, user: dict = Depends(auth.require_user)):
    rep = await asyncio.to_thread(db.get_report, report_id)
    if not rep:
        return JSONResponse({"error": "No existe ese informe."}, status_code=404)
    if not db.user_follows(user["id"], rep["player_tag"]):
        raise HTTPException(status_code=403, detail="No sigues a ese jugador.")
    return _public_report(rep, with_content=True)


# ============================ EVENTOS (LIGAS Y TORNEOS) ============================

_EVENT_MEDIA_DIR = os.path.join(FRONTEND_DIR, "media", "events")
_EV_KINDS = {"league", "tournament"}
_EV_MODES = {"individual", "teams"}
_EV_VIS = {"public", "acceptance", "private"}
_EV_MATCH = {"bo1", "bo3", "bo5"}


def _event_public(e: dict) -> dict:
    """Vista del evento sin el hash de la contraseña."""
    return {
        "id": e["id"], "owner_user_id": e["owner_user_id"], "name": e["name"],
        "kind": e["kind"], "mode": e["mode"], "visibility": e["visibility"],
        "language": e.get("language"), "max_participants": e.get("max_participants"),
        "format": e.get("format"), "match_type": e.get("match_type"),
        "date_start": e.get("date_start"), "date_end": e.get("date_end"),
        "description": e.get("description"), "poster_url": e.get("poster_url"),
        "has_password": e.get("has_password", False),
        "require_confirmation": e.get("require_confirmation"),
        "hidden": e.get("hidden"),
        "settings": e.get("settings") or {}, "status": e.get("status"),
        "participants": e.get("participants"), "followers": e.get("followers"),
        "relation": e.get("relation"),
    }


def _can_view_event(e: dict, user_id: int) -> bool:
    # La visibilidad controla CÓMO se entra (contraseña/validación) y si el evento
    # aparece en el tablón ('hidden'), no quién puede ver su ficha. Cualquiera con el
    # enlace puede consultarla (los privados ocultos se comparten por enlace directo).
    return True


def _round_robin(ids: list):
    """Calendario todos-contra-todos (método del círculo). Devuelve lista de jornadas,
    cada una con pares (a, b)."""
    ids = list(ids)
    if len(ids) % 2:
        ids.append(None)  # descanso si son impares
    n = len(ids)
    arr = ids[:]
    rounds = []
    for _ in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = arr[i], arr[n - 1 - i]
            if a is not None and b is not None:
                pairs.append((a, b))
        rounds.append(pairs)
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]  # rota dejando fijo el primero
    return rounds


def _seed_order(n: int) -> list:
    """Orden de siembra estándar de un cuadro de tamaño n (potencia de 2).
    Coloca el 1 y el 2 en extremos opuestos, etc. Devuelve semillas 1..n."""
    if n <= 1:
        return [1]
    prev = _seed_order(n // 2)
    out = []
    for s in prev:
        out.append(s); out.append(n + 1 - s)
    return out


def _advance_winner(eid: int, m: dict, teams_mode: bool) -> None:
    """Lleva al ganador de un partido del cuadro a su hueco de la ronda siguiente."""
    pos = m.get("bracket_pos")
    if pos is None:
        return
    w = m.get("winner")
    if w not in ("a", "b"):
        return
    win_id = (m.get("a_team") if w == "a" else m.get("b_team")) if teams_mode \
        else (m.get("a_tag") if w == "a" else m.get("b_tag"))
    r = (m.get("round") or 1)
    nxt = next((x for x in db.list_matches(eid)
                if (x.get("round") == r + 1 and x.get("bracket_pos") == pos // 2)), None)
    if not nxt:
        return
    slot = "a" if pos % 2 == 0 else "b"
    field = (slot + "_team") if teams_mode else (slot + "_tag")
    db.update_match(nxt["id"], {field: win_id})


def _unadvance(eid: int, m: dict, teams_mode: bool) -> None:
    """Quita la aportación de un partido del hueco siguiente (al borrar su resultado)."""
    pos = m.get("bracket_pos")
    if pos is None:
        return
    r = (m.get("round") or 1)
    nxt = next((x for x in db.list_matches(eid)
                if (x.get("round") == r + 1 and x.get("bracket_pos") == pos // 2)), None)
    if not nxt:
        return
    slot = "a" if pos % 2 == 0 else "b"
    field = (slot + "_team") if teams_mode else (slot + "_tag")
    db.update_match(nxt["id"], {field: None})


def _gen_single_elim(eid: int, ids: list, teams_mode: bool, round_mm) -> int:
    """Genera el cuadro completo de eliminación directa: ronda 1 con byes para las
    primeras cabezas de serie, rondas siguientes vacías, y resuelve los byes."""
    import math
    n = len(ids)
    rounds_count = max(1, math.ceil(math.log2(n)))
    size = 1 << rounds_count
    order = _seed_order(size)
    seq = [ids[s - 1] if s <= n else None for s in order]  # None = bye
    pairs = [(seq[i], seq[i + 1]) for i in range(0, size, 2)]
    created = 0
    mode, mp = round_mm(1)
    for pos, (a, b) in enumerate(pairs):  # ronda 1
        if teams_mode:
            db.create_match(eid, 1, a_team=a, b_team=b, mode=mode, map=mp, bracket_pos=pos)
        else:
            db.create_match(eid, 1, a_tag=a, b_tag=b, mode=mode, map=mp, bracket_pos=pos)
        created += 1
    for r in range(2, rounds_count + 1):  # rondas siguientes vacías
        rm, rmap = round_mm(r)
        for pos in range(size >> r):
            db.create_match(eid, r, mode=rm, map=rmap, bracket_pos=pos)
            created += 1
    for m in db.list_matches(eid):  # resolver byes de la ronda 1 y avanzar
        if (m.get("round") or 1) != 1:
            continue
        a = m.get("a_team") if teams_mode else m.get("a_tag")
        b = m.get("b_team") if teams_mode else m.get("b_tag")
        if bool(a) != bool(b):  # exactamente uno presente → bye
            db.update_match(m["id"], {"winner": "a" if a else "b", "status": "played"})
            _advance_winner(eid, db.get_match(m["id"]), teams_mode)
    return created


# --------------------------- Suizo / McMahon (Fase 3) ---------------------------

def _mcmahon_initial(participants: list, pw: int) -> dict:
    """Puntos iniciales de McMahon por bandas de copas (snapshot). Solo individual.
    Reparte a los jugadores en hasta 4 bandas por copas; cada banda por encima
    de la inferior parte con el equivalente a una victoria más."""
    seeded = [(p["player_tag"], p.get("seed_cups") or 0) for p in participants]
    if not seeded:
        return {}
    order = sorted(seeded, key=lambda x: x[1])  # ascendente por copas
    n = len(order)
    bands = min(4, max(1, n // 2))
    out = {}
    for i, (tag, _c) in enumerate(order):
        band = (i * bands) // n  # 0 = menos copas … bands-1 = más copas
        out[tag] = band * pw
    return out


def _played_pairs(matches: list, teams_mode: bool) -> set:
    s = set()
    for m in matches:
        a = m.get("a_team") if teams_mode else m.get("a_tag")
        b = m.get("b_team") if teams_mode else m.get("b_tag")
        if a and b:
            s.add(frozenset((a, b)))
    return s


def _had_bye(matches: list, key, teams_mode: bool) -> bool:
    for m in matches:
        a = m.get("a_team") if teams_mode else m.get("a_tag")
        b = m.get("b_team") if teams_mode else m.get("b_tag")
        if (a == key and not b) or (b == key and not a):
            return True
    return False


def _pair_swiss(order: list, played: set):
    """Emparejamiento sin repetir rival (backtracking). Devuelve lista de (a,b) o None."""
    if not order:
        return []
    a = order[0]
    for i in range(1, len(order)):
        b = order[i]
        if frozenset((a, b)) in played:
            continue
        sub = _pair_swiss(order[1:i] + order[i + 1:], played)
        if sub is not None:
            return [(a, b)] + sub
    return None


def _pair_greedy_avoid(order: list, played: set) -> list:
    """Plan B: empareja al primero con el primer rival no repetido (o repite si no hay)."""
    order = list(order)
    pairs = []
    while order:
        a = order.pop(0)
        idx = next((i for i, b in enumerate(order) if frozenset((a, b)) not in played), None)
        if idx is None:
            idx = 0
        pairs.append((a, order.pop(idx)))
    return pairs


async def _snapshot_cups(eid: int, parts: list) -> None:
    """Guarda el snapshot de copas totales de cada cuenta (semilla suizo/McMahon)."""
    async def one(tag):
        try:
            p = await _get_player_cached(tag)
            return tag, p.get("trophies")
        except Exception:  # noqa: BLE001
            return tag, None
    results = await asyncio.gather(*[one(p["player_tag"]) for p in parts]) if parts else []
    for tag, cups in results:
        if cups is not None:
            await asyncio.to_thread(db.set_participant_seed_cups, eid, tag, cups)


def _gen_swiss_round(eid: int, e: dict, teams_mode: bool, round_mm) -> dict:
    """Genera la SIGUIENTE ronda suiza/McMahon emparejando por puntuación sin repetir rival."""
    matches = db.list_matches(eid)
    if matches and any(m.get("status") != "played" for m in matches):
        return {"error": "Termina la ronda actual (todos los resultados) antes de emparejar la siguiente."}
    parts = db.list_participants(eid)
    teams = db.list_teams(eid)
    keys = [t["id"] for t in teams] if teams_mode else [p["player_tag"] for p in parts]
    if len(keys) < 2:
        return {"error": "Hacen falta al menos 2 participantes."}
    next_round = (max((m.get("round") or 1) for m in matches) + 1) if matches else 1
    mode, mp = round_mm(next_round)
    standings = _compute_standings(e, matches, parts, teams)
    score = {r["key"]: r["pts"] for r in standings}
    name_of = {r["key"]: str(r["name"]).lower() for r in standings}
    seed = {t["id"]: 0 for t in teams} if teams_mode else {p["player_tag"]: (p.get("seed_cups") or 0) for p in parts}
    played = _played_pairs(matches, teams_mode)
    order = sorted(keys, key=lambda k: (-score.get(k, 0), -seed.get(k, 0), name_of.get(k, "")))
    bye_key = None
    if len(order) % 2 == 1:
        for k in reversed(order):  # bye al de menos puntos que no lo haya tenido
            if not _had_bye(matches, k, teams_mode):
                bye_key = k
                break
        bye_key = bye_key if bye_key is not None else order[-1]
        order = [k for k in order if k != bye_key]
    pairs = (_pair_swiss(order, played) if len(order) <= 16 else None)
    if pairs is None:
        pairs = _pair_greedy_avoid(order, played)
    created = 0
    for (a, b) in pairs:
        if teams_mode:
            db.create_match(eid, next_round, a_team=a, b_team=b, mode=mode, map=mp)
        else:
            db.create_match(eid, next_round, a_tag=a, b_tag=b, mode=mode, map=mp)
        created += 1
    if bye_key is not None:  # el bye = partido jugado de un solo lado (victoria)
        mid = (db.create_match(eid, next_round, a_team=bye_key, mode=mode, map=mp) if teams_mode
               else db.create_match(eid, next_round, a_tag=bye_key, mode=mode, map=mp))
        db.update_match(mid, {"winner": "a", "status": "played"})
        created += 1
    return {"created": created, "round": next_round, "bye": bye_key is not None}


def _gen_random_round(eid: int, parts: list, team_size: int, round_mm) -> dict:
    """Genera una ronda de EQUIPOS ALEATORIOS (eventos individuales): baraja a los
    participantes, forma equipos de `team_size` y los empareja. Cada partido guarda
    su roster; la clasificación es individual (cada jugador suma según su equipo)."""
    matches = db.list_matches(eid)
    if matches and any(m.get("status") != "played" for m in matches):
        return {"error": "Termina la ronda actual (todos los resultados) antes de generar la siguiente."}
    k = max(1, int(team_size or 3))
    tags = [p["player_tag"] for p in parts]
    if len(tags) < 2 * k:
        return {"error": f"Hacen falta al menos {2 * k} jugadores para equipos de {k}."}
    next_round = (max((m.get("round") or 1) for m in matches) + 1) if matches else 1
    mode, mp = round_mm(next_round)
    random.shuffle(tags)
    teams = [tags[i:i + k] for i in range(0, len(tags), k)]
    teams = [t for t in teams if len(t) == k]  # descarta equipo incompleto
    created = 0
    i = 0
    while i + 1 < len(teams):
        db.create_match(eid, next_round, mode=mode, map=mp, roster_a=teams[i], roster_b=teams[i + 1])
        created += 1
        i += 2
    benched = len(tags) - created * 2 * k
    return {"created": created, "round": next_round, "benched": benched, "team_size": k}


def _compute_standings(e: dict, matches: list, participants: list, teams: list) -> list:
    """Clasificación a partir de los enfrentamientos jugados, con los puntos del evento.
    En McMahon suma los puntos iniciales por copas; en suizo/McMahon desempata por copas."""
    pts = (e.get("settings") or {}).get("points") or {}
    pw, pdr, pl = pts.get("win", 3), pts.get("draw", 1), pts.get("loss", 0)
    teams_mode = e["mode"] == "teams"
    fmt = e.get("format") or ""
    rows = {}
    if teams_mode:
        for t in teams:
            rows[t["id"]] = {"key": t["id"], "name": t.get("name") or "Equipo", "logo": t.get("logo_url"),
                             "seed_cups": None, "pj": 0, "g": 0, "e": 0, "p": 0, "sf": 0, "sa": 0, "pts": 0}
    else:
        for p in participants:
            rows[p["player_tag"]] = {"key": p["player_tag"], "name": p.get("player_name") or p["player_tag"],
                                     "tag": p["player_tag"], "icon_id": p.get("icon_id"), "seed_cups": p.get("seed_cups"),
                                     "pj": 0, "g": 0, "e": 0, "p": 0, "sf": 0, "sa": 0, "pts": 0}
    if fmt == "mcmahon" and not teams_mode:  # ventaja inicial por copas
        for tag, ini in _mcmahon_initial(participants, pw).items():
            if tag in rows:
                rows[tag]["pts"] += ini
                rows[tag]["mcmahon"] = ini
    if fmt == "random_teams" and not teams_mode:  # equipos aleatorios → puntuación individual por roster
        for m in matches:
            if m.get("status") != "played" or m.get("winner") == "void":
                continue
            ros_a, ros_b = (m.get("roster_a") or []), (m.get("roster_b") or [])
            w = m.get("winner")
            for tag in ros_a:
                if tag in rows:
                    r = rows[tag]; r["pj"] += 1
                    if w == "a": r["g"] += 1; r["pts"] += pw
                    elif w == "b": r["p"] += 1; r["pts"] += pl
                    else: r["e"] += 1; r["pts"] += pdr
            for tag in ros_b:
                if tag in rows:
                    r = rows[tag]; r["pj"] += 1
                    if w == "b": r["g"] += 1; r["pts"] += pw
                    elif w == "a": r["p"] += 1; r["pts"] += pl
                    else: r["e"] += 1; r["pts"] += pdr
    else:
        for m in matches:
            if m.get("status") != "played" or m.get("winner") == "void":
                continue
            ka, kb = (m.get("a_team"), m.get("b_team")) if teams_mode else (m.get("a_tag"), m.get("b_tag"))
            if (ka in rows) and not kb:  # bye → victoria sin rival
                r = rows[ka]; r["pj"] += 1; r["g"] += 1; r["pts"] += pw; continue
            if (kb in rows) and not ka:
                r = rows[kb]; r["pj"] += 1; r["g"] += 1; r["pts"] += pw; continue
            if ka not in rows or kb not in rows:
                continue
            sa, sb = m.get("score_a") or 0, m.get("score_b") or 0
            ra, rb = rows[ka], rows[kb]
            ra["pj"] += 1; rb["pj"] += 1
            ra["sf"] += sa; ra["sa"] += sb; rb["sf"] += sb; rb["sa"] += sa
            w = m.get("winner")
            if w == "a":
                ra["g"] += 1; rb["p"] += 1; ra["pts"] += pw; rb["pts"] += pl
            elif w == "b":
                rb["g"] += 1; ra["p"] += 1; rb["pts"] += pw; ra["pts"] += pl
            else:
                ra["e"] += 1; rb["e"] += 1; ra["pts"] += pdr; rb["pts"] += pdr
    out = list(rows.values())
    for r in out:
        r["dif"] = r["sf"] - r["sa"]
    if fmt == "random_teams":  # individual: por puntos y victorias
        out.sort(key=lambda r: (-r["pts"], -r["g"], str(r["name"]).lower()))
    elif fmt in ("swiss", "mcmahon"):  # desempate final por copas (snapshot)
        out.sort(key=lambda r: (-r["pts"], -r["dif"], -r["sf"], -(r.get("seed_cups") or 0), str(r["name"]).lower()))
    else:
        out.sort(key=lambda r: (-r["pts"], -r["dif"], -r["sf"], str(r["name"]).lower()))
    return out


@app.post("/api/events")
def api_event_create(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    body = payload or {}
    name = (body.get("name") or "").strip()
    kind, mode, vis = body.get("kind"), body.get("mode"), body.get("visibility")
    lang = body.get("language") or None
    if not name:
        return JSONResponse({"error": "Pon un nombre al evento."}, status_code=400)
    if kind not in _EV_KINDS or mode not in _EV_MODES or vis not in _EV_VIS:
        return JSONResponse({"error": "Datos de evento no válidos."}, status_code=400)
    eid = db.create_event(user["id"], name, kind, mode, vis, lang)
    return {"ok": True, "id": eid}


@app.get("/api/events/mine")
def api_events_mine(user: dict = Depends(auth.require_user)):
    return {"events": db.list_my_events(user["id"])}


@app.get("/api/events/board")
def api_events_board(types: str = Query(""), langs: str = Query(""),
                     acceptance: str = Query(""), user: dict = Depends(auth.require_user)):
    t = [x for x in types.split(",") if x] or None
    lg = [x for x in langs.split(",") if x] or None
    a = [x for x in acceptance.split(",") if x] or None
    return {"events": db.list_board_events(user["id"], t, lg, a)}


@app.get("/api/events/{eid}")
async def api_event_detail(eid: int, user: dict = Depends(auth.require_user)):
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    if not _can_view_event(e, user["id"]):
        raise HTTPException(status_code=403, detail="Evento privado.")
    counts = db.event_counts(eid)
    e["participants"], e["followers"] = counts["participants"], counts["followers"]
    is_owner = e["owner_user_id"] == user["id"]
    parts = db.list_participants(eid)
    missing = [p["player_tag"] for p in parts if not p.get("player_name")]
    if missing:  # rellena nombres desde la API (solo la 1.ª vez; luego quedan en players)
        await _ensure_player_profiles(missing)
        parts = db.list_participants(eid)
    e["relation"] = "owner" if is_owner else (
        "participant" if any(p.get("user_id") == user["id"] for p in parts) else (
            "follower" if db.is_following_event(eid, user["id"]) else "none"))
    pub = _event_public(e)
    pub["participants_list"] = parts
    pub["teams"] = db.list_teams(eid)
    pub["is_owner"] = is_owner
    pub["is_following"] = db.is_following_event(eid, user["id"])
    pub["my_request"] = db.user_pending_request(eid, user["id"])
    pub["my_tags"] = db.list_players_for_user(user["id"])
    pub["matches"] = db.list_matches(eid)
    pub["standings"] = _compute_standings(e, pub["matches"], parts, pub["teams"])
    if is_owner:
        pub["requests"] = db.list_requests(eid, "pending")
    return pub


@app.put("/api/events/{eid}")
def api_event_update(eid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    body = payload or {}
    fields = {}
    for c in ("name", "kind", "mode", "visibility", "language", "format",
              "match_type", "date_start", "date_end", "description", "poster_url", "status"):
        if c in body:
            fields[c] = body[c]
    if "max_participants" in body:
        try:
            fields["max_participants"] = max(2, min(512, int(body["max_participants"])))
        except Exception:  # noqa: BLE001
            pass
    if "require_confirmation" in body:
        fields["require_confirmation"] = 1 if body["require_confirmation"] else 0
    if "hidden" in body:
        fields["hidden"] = 1 if body["hidden"] else 0
    if "settings" in body and isinstance(body["settings"], dict):
        fields["settings"] = body["settings"]
    if "password" in body:
        pw = (body["password"] or "").strip()
        fields["password_hash"] = auth.hash_password(pw) if pw else None
    if fields.get("kind") and fields["kind"] not in _EV_KINDS:
        return JSONResponse({"error": "Tipo no válido."}, status_code=400)
    if fields.get("visibility") and fields["visibility"] not in _EV_VIS:
        return JSONResponse({"error": "Visibilidad no válida."}, status_code=400)
    if fields.get("match_type") and fields["match_type"] not in _EV_MATCH:
        return JSONResponse({"error": "Enfrentamiento no válido."}, status_code=400)
    db.update_event(eid, fields)
    return {"ok": True}


@app.delete("/api/events/{eid}")
def api_event_delete(eid: int, user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    db.delete_event(eid)
    return {"ok": True}


@app.post("/api/events/{eid}/follow")
def api_event_follow(eid: int, user: dict = Depends(auth.require_user)):
    if not db.get_event(eid):
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    db.follow_event(eid, user["id"])
    return {"ok": True, "followers": db.event_counts(eid)["followers"]}


@app.delete("/api/events/{eid}/follow")
def api_event_unfollow(eid: int, user: dict = Depends(auth.require_user)):
    db.unfollow_event(eid, user["id"])
    return {"ok": True, "followers": db.event_counts(eid)["followers"]}


@app.post("/api/events/{eid}/join")
def api_event_join(eid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    body = payload or {}
    tag = (body.get("player_tag") or "").strip()
    if not tag:
        return JSONResponse({"error": "Elige con qué ID te apuntas."}, status_code=400)
    tag = db.normalize_tag(tag)
    if not db.user_follows(user["id"], tag):
        return JSONResponse({"error": "Ese ID no está en tu cuenta."}, status_code=400)
    if db.tag_in_event(eid, tag):
        return JSONResponse({"error": "Ese ID ya está inscrito en el evento."}, status_code=409)
    if db.participant_count(eid) >= (e.get("max_participants") or 12):
        return JSONResponse({"error": "El evento está completo."}, status_code=409)
    team_name = (body.get("team_name") or "").strip() or None
    team_logo = (body.get("team_logo_url") or "").strip() or None
    vis = e["visibility"]

    def _do_join():
        team_id = None
        if e["mode"] == "teams" and team_name:
            team_id = db.create_team(eid, team_name, team_logo, user["id"])
        db.add_participant(eid, user["id"], tag, team_id, 0)

    if vis == "public":
        _do_join()
        return {"ok": True, "joined": True}
    if vis == "acceptance":
        db.create_request(eid, user["id"], tag, team_name, body.get("message"))
        return {"ok": True, "requested": True}
    # privado
    ph = db.get_event_password_hash(eid)
    if ph and not auth.verify_password(body.get("password") or "", ph):
        return JSONResponse({"error": "Contraseña incorrecta."}, status_code=403)
    if e.get("require_confirmation"):
        db.create_request(eid, user["id"], tag, team_name, body.get("message"))
        return {"ok": True, "requested": True}
    _do_join()
    return {"ok": True, "joined": True}


@app.post("/api/events/{eid}/requests/{rid}/accept")
def api_event_req_accept(eid: int, rid: int, user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    req = db.get_request(rid)
    if not req or req["event_id"] != eid:
        return JSONResponse({"error": "Solicitud no encontrada."}, status_code=404)
    if req["status"] != "pending":
        return JSONResponse({"error": "Esa solicitud ya está resuelta."}, status_code=409)
    e = db.get_event(eid)
    if db.participant_count(eid) >= (e.get("max_participants") or 12):
        return JSONResponse({"error": "No quedan plazas."}, status_code=409)
    if not db.tag_in_event(eid, req["player_tag"]):
        team_id = None
        if e["mode"] == "teams" and req.get("team_name"):
            team_id = db.create_team(eid, req["team_name"], None, req["user_id"])
        db.add_participant(eid, req["user_id"], req["player_tag"], team_id, 0)
    db.set_request_status(rid, "accepted")
    return {"ok": True}


@app.post("/api/events/{eid}/requests/{rid}/reject")
def api_event_req_reject(eid: int, rid: int, user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    req = db.get_request(rid)
    if not req or req["event_id"] != eid:
        return JSONResponse({"error": "Solicitud no encontrada."}, status_code=404)
    db.set_request_status(rid, "rejected")
    return {"ok": True}


def _notify_followers_player_added(eid: int, e: dict, tags: list) -> None:
    """Avisa a quienes SIGUEN a un jugador recién apuntado al evento (Fase 6)."""
    if not tags:
        return
    ev_name = e.get("name") or "un evento"
    owner = e.get("owner_user_id")
    for tag in tags:
        followers = db.users_following_player(tag)
        if not followers:
            continue
        pname = db.get_player_name(tag) or tag
        db.notify_many(
            followers, "player_in_event",
            f"{pname} participa en un evento",
            f"Sigues a {pname}, que se ha apuntado a «{ev_name}». ¿Quieres seguir el evento para no perderte sus partidas?",
            event_id=eid, data={"player_tag": tag}, exclude=[owner])


@app.post("/api/events/{eid}/invite")
async def api_event_invite(eid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    e = db.get_event(eid)
    tag = ((payload or {}).get("player_tag") or "").strip()
    if not tag:
        return JSONResponse({"error": "Indica el tag del jugador."}, status_code=400)
    tag = db.normalize_tag(tag)
    if db.tag_in_event(eid, tag):
        return JSONResponse({"error": "Ese ID ya está inscrito."}, status_code=409)
    if db.participant_count(eid) >= (e.get("max_participants") or 12):
        return JSONResponse({"error": "No quedan plazas."}, status_code=409)
    team_name = ((payload or {}).get("team_name") or "").strip() or None
    team_id = db.create_team(eid, team_name, None, None) if (e["mode"] == "teams" and team_name) else None
    db.add_participant(eid, None, tag, team_id, 1)
    await _ensure_player_profiles([tag])  # guarda su nombre para mostrarlo
    _notify_followers_player_added(eid, e, [tag])
    return {"ok": True}


@app.post("/api/events/{eid}/participants/bulk")
async def api_event_invite_bulk(eid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    e = db.get_event(eid)
    tags = _parse_player_tags((payload or {}).get("player_tags"))
    seen, norm = set(), []
    for t in tags:
        nt = db.normalize_tag(t)
        if nt and nt not in seen:
            seen.add(nt); norm.append(nt)
    if not norm:
        return JSONResponse({"error": "Pega al menos un ID de jugador."}, status_code=400)
    cap = e.get("max_participants") or 12
    team_name = ((payload or {}).get("team_name") or "").strip() or None
    team_id = db.create_team(eid, team_name, None, None) if (e["mode"] == "teams" and team_name) else None
    added, dup, no_space = [], 0, 0
    for nt in norm:
        if db.tag_in_event(eid, nt):
            dup += 1; continue
        if db.participant_count(eid) >= cap:
            no_space += 1; continue
        db.add_participant(eid, None, nt, team_id, 1)
        added.append(nt)
    await _ensure_player_profiles(added)  # guarda sus nombres para mostrarlos
    _notify_followers_player_added(eid, e, added)
    return {"ok": True, "added": len(added), "duplicates": dup, "no_space": no_space, "total": len(norm)}


@app.delete("/api/events/{eid}/participants/{pid}")
def api_event_remove_participant(eid: int, pid: int, user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    db.remove_participant(eid, pid)
    return {"ok": True}


# --------------------------- Notificaciones (Fase 6) ---------------------------

@app.get("/api/notifications")
def api_notifications_list(user: dict = Depends(auth.require_user)):
    items = db.list_notifications(user["id"])
    return {"items": items, "unread": db.count_unread_notifications(user["id"])}


@app.get("/api/notifications/unread-count")
def api_notifications_unread(user: dict = Depends(auth.require_user)):
    return {"unread": db.count_unread_notifications(user["id"])}


@app.post("/api/notifications/{nid}/read")
def api_notification_read(nid: int, user: dict = Depends(auth.require_user)):
    db.mark_notification_read(user["id"], nid)
    return {"ok": True, "unread": db.count_unread_notifications(user["id"])}


@app.post("/api/notifications/read-all")
def api_notifications_read_all(user: dict = Depends(auth.require_user)):
    n = db.mark_all_notifications_read(user["id"])
    return {"ok": True, "marked": n}


@app.delete("/api/notifications/{nid}")
def api_notification_delete(nid: int, user: dict = Depends(auth.require_user)):
    db.delete_notification(user["id"], nid)
    return {"ok": True}


@app.delete("/api/notifications")
def api_notifications_delete_all(user: dict = Depends(auth.require_user)):
    n = db.delete_all_notifications(user["id"])
    return {"ok": True, "deleted": n}


def _match_names(m, teams_mode, parts):
    """Nombres de los dos lados de una partida, para el resumen."""
    def nm(tag):
        p = next((x for x in parts if x["player_tag"] == tag), None)
        return (p.get("player_name") if p else None) or tag
    if m.get("roster_a") or m.get("roster_b"):
        return ("Equipo A", "Equipo B")
    if teams_mode:
        return (m.get("a_team_name") or "Equipo A", m.get("b_team_name") or "Equipo B")
    return (m.get("a_name") or m.get("a_tag") or "?", m.get("b_name") or m.get("b_tag") or "?")


def _summary_context(e, matches, standings, parts):
    teams_mode = e.get("mode") == "teams"
    _fmt = {"swiss": "suizo", "mcmahon": "McMahon", "roundrobin": "todos contra todos",
            "single_elim": "eliminación directa", "random_teams": "equipos aleatorios", "free": "libre"}
    L = [f"Evento: «{e.get('name')}» · formato {_fmt.get(e.get('format'), e.get('format'))}."]
    played = [m for m in matches if m.get("status") == "played"]
    if played:
        last = max((m.get("round") or 1) for m in played)
        lr = [m for m in played if (m.get("round") or 1) == last]
        L.append(f"\nResultados de la ronda {last}:")
        for m in lr:
            a, b = _match_names(m, teams_mode, parts)
            if m.get("winner") == "void":
                L.append(f"- {a} vs {b}: no jugado")
            else:
                L.append(f"- {a} {m.get('score_a')}–{m.get('score_b')} {b}")
    if standings:
        L.append("\nClasificación:")
        for i, r in enumerate(standings[:6], 1):
            L.append(f"{i}. {r['name']} — {r['pts']} pts (PJ {r['pj']}, G {r['g']}, E {r['e']}, P {r['p']})")
    return "\n".join(L)


@app.post("/api/events/{eid}/summary")
async def api_event_summary(eid: int, payload: dict = Body(default={}), user: dict = Depends(auth.require_user)):
    """El organizador genera un resumen (una sola llamada a Claude) y se envía como
    notificación idéntica a seguidores, apuntados y a sí mismo (Fase 6)."""
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    if not coach.configured():
        return JSONResponse({"error": "Falta ANTHROPIC_API_KEY para generar el resumen."}, status_code=400)
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    matches = db.list_matches(eid)
    parts = db.list_participants(eid)
    teams = db.list_teams(eid)
    standings = _compute_standings(e, matches, parts, teams)
    if not any(m.get("status") == "played" for m in matches):
        return JSONResponse({"error": "Aún no hay resultados que resumir."}, status_code=400)
    try:
        text = await coach.generate_event_summary(_summary_context(e, matches, standings, parts))
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"error": f"No se pudo generar el resumen: {ex}"}, status_code=502)
    if not text:
        return JSONResponse({"error": "El resumen salió vacío; inténtalo de nuevo."}, status_code=502)
    # destinatarios: seguidores + apuntados + organizador (deduplicados)
    recipients = db.event_follower_ids(eid) + db.event_participant_user_ids(eid) + [user["id"]]
    n = db.notify_many(recipients, "event_summary", f"Resumen · {e.get('name')}", text, event_id=eid)
    return {"ok": True, "sent": n, "text": text}


# --------------------------- Equipos / plantillas (Fase 4) ---------------------------

@app.post("/api/events/{eid}/teams")
def api_team_create(eid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    name = ((payload or {}).get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "Pon un nombre al equipo."}, status_code=400)
    logo = ((payload or {}).get("logo_url") or "").strip() or None
    tid = db.create_team(eid, name, logo, None)
    return {"ok": True, "id": tid}


@app.patch("/api/events/{eid}/teams/{tid}")
def api_team_update(eid: int, tid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    body = payload or {}
    name = (body.get("name") or "").strip() or None
    logo = body.get("logo_url")
    logo = logo.strip() if isinstance(logo, str) and logo.strip() else None
    db.update_team(eid, tid, name, logo)
    return {"ok": True}


@app.delete("/api/events/{eid}/teams/{tid}")
def api_team_delete(eid: int, tid: int, user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    db.delete_team(eid, tid)
    return {"ok": True}


@app.post("/api/events/{eid}/participants/{pid}/team")
def api_participant_set_team(eid: int, pid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    tid = (payload or {}).get("team_id")
    db.set_participant_team(eid, pid, int(tid) if tid else None)
    return {"ok": True}


@app.post("/api/events/upload-image")
def api_event_upload(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    data = (payload or {}).get("data") or ""
    mime = ((payload or {}).get("mime") or "").lower()
    if data.startswith("data:"):
        head, _, b64 = data.partition(",")
        if not mime and ":" in head and ";" in head:
            mime = head[head.index(":") + 1:head.index(";")].lower()
        data = b64
    ext = _IMG_EXT.get(mime)
    if not ext:
        return JSONResponse({"error": "Formato no admitido (usa PNG, JPG, GIF o WEBP)."}, status_code=400)
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "Imagen no válida."}, status_code=400)
    if not raw or len(raw) > 6 * 1024 * 1024:
        return JSONResponse({"error": "Imagen vacía o supera los 6 MB."}, status_code=400)
    os.makedirs(_EVENT_MEDIA_DIR, exist_ok=True)
    name = uuid.uuid4().hex + ext
    with open(os.path.join(_EVENT_MEDIA_DIR, name), "wb") as f:
        f.write(raw)
    return {"ok": True, "url": "/static/media/events/" + name}


@app.post("/api/events/{eid}/matches")
def api_match_create(eid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    body = payload or {}
    try:
        rnd = max(1, int(body.get("round") or 1))
    except Exception:  # noqa: BLE001
        rnd = 1
    mode = (body.get("mode") or "").strip() or None
    mp = (body.get("map") or "").strip() or None
    if e["mode"] == "teams":
        a, b = body.get("a_team"), body.get("b_team")
        if not a or not b or str(a) == str(b):
            return JSONResponse({"error": "Elige dos equipos distintos."}, status_code=400)
        mid = db.create_match(eid, rnd, a_team=int(a), b_team=int(b), mode=mode, map=mp)
    else:
        a = db.normalize_tag(body["a_tag"]) if body.get("a_tag") else None
        b = db.normalize_tag(body["b_tag"]) if body.get("b_tag") else None
        if not a or not b or a == b:
            return JSONResponse({"error": "Elige dos jugadores distintos."}, status_code=400)
        mid = db.create_match(eid, rnd, a_tag=a, b_tag=b, mode=mode, map=mp)
    return {"ok": True, "id": mid}


@app.put("/api/events/{eid}/matches/{mid}")
def api_match_update(eid: int, mid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    m = db.get_match(mid)
    if not m or m["event_id"] != eid:
        return JSONResponse({"error": "Enfrentamiento no encontrado."}, status_code=404)
    body = payload or {}
    fields = {}
    if "round" in body:
        try:
            fields["round"] = max(1, int(body["round"]))
        except Exception:  # noqa: BLE001
            pass
    for c in ("mode", "map"):
        if c in body:
            fields[c] = (body[c] or "").strip() or None
    if body.get("clear_result"):
        fields.update({"status": "pending", "winner": None, "score_a": None, "score_b": None, "evidence_battle_id": None})
    elif "winner" in body or "score_a" in body or "score_b" in body:
        def _toint(x):
            try:
                return int(x) if x not in (None, "") else None
            except Exception:  # noqa: BLE001
                return None
        sa, sb, w = _toint(body.get("score_a")), _toint(body.get("score_b")), body.get("winner")
        if sa is not None and sb is not None and not w:
            w = "a" if sa > sb else ("b" if sb > sa else "draw")
        elif w and sa is None and sb is None:
            sa, sb = (1, 0) if w == "a" else ((0, 1) if w == "b" else (0, 0))
        if w in ("a", "b", "draw"):
            fields.update({"winner": w, "score_a": sa, "score_b": sb, "status": "played"})
        elif w == "void":  # anulado / no jugado → 0 puntos para ambos
            fields.update({"winner": "void", "score_a": 0, "score_b": 0, "status": "played"})
    db.update_match(mid, fields)
    # Eliminación directa: propagar (o retirar) el ganador al hueco siguiente del cuadro
    ev = db.get_event(eid) or {}
    if ev.get("format") == "single_elim":
        teams_mode = ev.get("mode") == "teams"
        m2 = db.get_match(mid)
        if m2 and m2.get("status") == "played" and m2.get("winner") in ("a", "b"):
            _advance_winner(eid, m2, teams_mode)
        else:
            _unadvance(eid, m2 or m, teams_mode)
    return {"ok": True}


@app.post("/api/events/{eid}/matches/close-pending")
def api_close_pending(eid: int, payload: dict = Body(default={}), user: dict = Depends(auth.require_user)):
    """Cierra la ronda: marca como ANULADOS (nulos, 0 puntos) los enfrentamientos pendientes."""
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    n = 0
    for m in db.list_matches(eid):
        if m.get("status") != "played":
            db.update_match(m["id"], {"winner": "void", "score_a": 0, "score_b": 0, "status": "played"})
            n += 1
    return {"ok": True, "voided": n}


@app.post("/api/events/{eid}/matches/detect")
async def api_match_detect(eid: int, payload: dict = Body(default={}), user: dict = Depends(auth.require_user)):
    """Fase 5: cruza las partidas pendientes con los battlelogs amistosos de los
    participantes y propone resultados (el organizador puede editarlos o borrarlos)."""
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    if not brawl_api.TOKEN:
        return JSONResponse({"error": "No hay token de la API de Brawl Stars configurado."}, status_code=400)
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    body = payload or {}
    force = bool(body.get("force"))  # re-detectar también las ya jugadas
    only_mid = body.get("match_id")
    teams_mode = e.get("mode") == "teams"
    best_of = {"bo1": 1, "bo3": 3, "bo5": 5}.get(e.get("match_type") or "bo1", 1)
    start = detect.parse_event_date(e.get("date_start"))
    end = detect.parse_event_date(e.get("date_end"), end=True)

    matches = db.list_matches(eid)
    if only_mid:
        matches = [m for m in matches if m["id"] == only_mid]
    pend = [m for m in matches if (force or m.get("status") != "played")]
    # un evento de eliminación solo tiene cruce cuando ambos lados están definidos
    pend = [m for m in pend if (teams_mode and (m.get("a_team") or m.get("roster_a")) and (m.get("b_team") or m.get("roster_b")))
            or (not teams_mode and m.get("a_tag") and m.get("b_tag"))]
    if not pend:
        return {"ok": True, "detected": 0, "checked": 0, "not_found": 0, "no_token": False}

    # rosters de cada equipo (para eventos por equipos)
    team_players = {}
    if teams_mode:
        for p in db.list_participants(eid):
            if p.get("team_id"):
                team_players.setdefault(p["team_id"], []).append(p["player_tag"])

    def roster_of(m, side):
        ros = m.get("roster_a" if side == "a" else "roster_b")
        if ros:
            return ros
        tid = m.get("a_team" if side == "a" else "b_team")
        return team_players.get(tid, [])

    # tags a consultar (una vez cada uno)
    tags = set()
    for m in pend:
        if teams_mode:
            tags.update(roster_of(m, "a")); tags.update(roster_of(m, "b"))
        else:
            tags.add(m["a_tag"]); tags.add(m["b_tag"])
    tags = [t for t in tags if t]

    async def fetch(tag):
        try:
            return (tag, await _get_battlelog_cached(tag))
        except Exception:  # noqa: BLE001
            return (tag, [])
    logs = await asyncio.gather(*[fetch(t) for t in tags]) if tags else []
    pool = detect.build_pool(logs)

    detected = 0
    for m in pend:
        code = detect.mode_code(m.get("mode"))
        if teams_mode:
            res = detect.match_teams(pool, roster_of(m, "a"), roster_of(m, "b"), code, start, end, best_of)
        else:
            res = detect.match_individual(pool, m["a_tag"], m["b_tag"], code, start, end, best_of)
        if not res:
            continue
        sa, sb, w = res["score_a"], res["score_b"], res["winner"]
        await asyncio.to_thread(db.update_match, m["id"], {
            "winner": w, "score_a": sa, "score_b": sb, "status": "played",
            "evidence_battle_id": res["evidence"],
        })
        detected += 1
        if e.get("format") == "single_elim" and w in ("a", "b"):
            m2 = db.get_match(m["id"])
            if m2:
                _advance_winner(eid, m2, teams_mode)

    return {"ok": True, "detected": detected, "checked": len(pend),
            "not_found": len(pend) - detected, "players": len(tags)}


@app.delete("/api/events/{eid}/matches/{mid}")
def api_match_delete(eid: int, mid: int, user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    db.delete_match(eid, mid)
    return {"ok": True}


@app.post("/api/events/{eid}/matches/generate")
async def api_match_generate(eid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    body = payload or {}
    fmt = e.get("format") or ""
    if fmt not in ("", "roundrobin", "free", "single_elim", "swiss", "mcmahon", "random_teams"):
        return JSONResponse({"error": "El emparejamiento automático para este formato aún no está disponible. Puedes añadir los enfrentamientos a mano."}, status_code=400)
    if body.get("replace"):
        db.clear_matches(eid)
    settings = e.get("settings") or {}
    policy = settings.get("map_policy")
    fm = settings.get("fixed_mode") or None
    fmap = settings.get("fixed_map") or None
    round_maps = settings.get("round_maps") or {}
    ev_mode = e["mode"]
    showdown = settings.get("showdown") or "exclude"
    _mm = {}

    def round_mm(rn):  # modo/mapa de la ronda rn
        manual = round_maps.get(str(rn)) or round_maps.get(rn)  # selección manual por ronda
        if manual and (manual.get("mode") or manual.get("map")):
            return (manual.get("mode") or None, manual.get("map") or None)
        if policy == "fixed":
            return (fm, fmap)
        if policy == "random":
            if rn not in _mm:
                _mm[rn] = bs_maps.random_mode_map(ev_mode, showdown)
            return _mm[rn]
        return (None, None)
    teams_mode = e["mode"] == "teams"
    ids = [t["id"] for t in db.list_teams(eid)] if teams_mode else \
          [p["player_tag"] for p in db.list_participants(eid)]
    if len(ids) < 2:
        return JSONResponse({"error": "Hacen falta al menos 2 participantes."}, status_code=400)
    max_rounds = settings.get("rounds")  # límite de rondas (suizo/McMahon/aleatorios)
    if fmt in ("swiss", "mcmahon", "random_teams") and max_rounds:
        existing = db.list_matches(eid)
        nextr = (max((m.get("round") or 1) for m in existing) + 1) if existing else 1
        if nextr > int(max_rounds):
            return JSONResponse({"error": f"El torneo está configurado a {int(max_rounds)} rondas; ya están todas generadas."}, status_code=400)
    if fmt == "single_elim":
        created = _gen_single_elim(eid, ids, teams_mode, round_mm)
        return {"ok": True, "created": created, "format": "single_elim"}
    if fmt in ("swiss", "mcmahon"):
        if not db.list_matches(eid) and not teams_mode:  # ronda 1: snapshot de copas
            await _snapshot_cups(eid, db.list_participants(eid))
        res = _gen_swiss_round(eid, e, teams_mode, round_mm)
        if "error" in res:
            return JSONResponse({"error": res["error"]}, status_code=400)
        return {"ok": True, "format": fmt, **res}
    if fmt == "random_teams":
        ts = body.get("team_size") or settings.get("team_size") or 3
        res = _gen_random_round(eid, db.list_participants(eid), ts, round_mm)
        if "error" in res:
            return JSONResponse({"error": res["error"]}, status_code=400)
        return {"ok": True, "format": "random_teams", **res}
    try:
        legs = max(1, min(20, int(body.get("legs") or settings.get("rounds") or 1)))
    except Exception:  # noqa: BLE001
        legs = 1
    schedule = _round_robin(ids)
    created, round_no = 0, 0
    for leg in range(legs):
        for jornada in schedule:
            round_no += 1
            rm, rmap = round_mm(round_no)
            for (a, b) in jornada:
                if leg % 2 == 1:
                    a, b = b, a
                if teams_mode:
                    db.create_match(eid, round_no, a_team=a, b_team=b, mode=rm, map=rmap)
                else:
                    db.create_match(eid, round_no, a_tag=a, b_tag=b, mode=rm, map=rmap)
                created += 1
    return {"ok": True, "created": created}


@app.post("/api/events/{eid}/matches/close-round")
def api_match_close_round(eid: int, payload: dict = Body(None), user: dict = Depends(auth.require_user)):
    """Cierra la ronda actual: marca como NO JUGADOS (nulos, 0 pts) los pendientes."""
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    matches = db.list_matches(eid)
    if not matches:
        return {"ok": True, "closed": 0}
    last = max((m.get("round") or 1) for m in matches)
    closed = 0
    for m in matches:
        if (m.get("round") or 1) == last and m.get("status") != "played":
            db.update_match(m["id"], {"winner": "void", "score_a": 0, "score_b": 0, "status": "played"})
            closed += 1
    return {"ok": True, "closed": closed, "round": last}


@app.put("/api/events/{eid}/rounds/{rn}/mode-map")
def api_round_mode_map(eid: int, rn: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    """Fija el modo y el mapa de una ronda; se aplica a TODAS sus partidas y se guarda
    para las rondas aún no generadas."""
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    body = payload or {}
    mode = (body.get("mode") or "").strip() or None
    mp = (body.get("map") or "").strip() or None
    settings = e.get("settings") or {}
    rmaps = settings.get("round_maps") or {}
    rmaps[str(rn)] = {"mode": mode, "map": mp}
    settings["round_maps"] = rmaps
    db.update_event(eid, {"settings": settings})
    updated = 0
    for m in db.list_matches(eid):  # aplicar a las partidas ya generadas de esa ronda
        if (m.get("round") or 1) == rn:
            db.update_match(m["id"], {"mode": mode, "map": mp})
            updated += 1
    return {"ok": True, "round": rn, "updated": updated}


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
