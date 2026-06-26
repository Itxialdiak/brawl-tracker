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
import re
import time
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Query, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import db, brawl_api, coach, assets, auth

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
    yield
    if task:
        task.cancel()


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
            events.append({"mode": evt.get("mode") or it.get("mode"), "map": map_,
                           "startTime": it.get("startTime"), "endTime": it.get("endTime")})
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
        prof = await brawl_api.get_player(tag)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"No se pudo leer el perfil: {e}"}, status_code=502)
    brawlers = sorted(
        [{"id": b.get("id"), "name": b.get("name"), "trophies": b.get("trophies") or 0,
          "power": b.get("power"), "rank": b.get("rank")} for b in (prof.get("brawlers") or [])],
        key=lambda b: b["trophies"], reverse=True)
    club = prof.get("club") or {}
    return {
        "tag": tag, "name": prof.get("name"), "trophies": prof.get("trophies"),
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


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
