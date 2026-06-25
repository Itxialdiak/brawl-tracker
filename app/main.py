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
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Query, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, brawl_api, coach, assets

SEED_TAG = os.environ.get("BRAWL_PLAYER_TAG", "").strip()
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "180"))

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

_last_poll = {"new": None, "players": None, "error": None, "at": None}


async def _poll_player(tag: str) -> int:
    """Sondea un jugador y guarda sus partidas nuevas. Devuelve cuántas."""
    # Backfill puntual del perfil (nombre + icono) si aún no lo tenemos.
    if await asyncio.to_thread(db.player_needs_profile, tag):
        try:
            prof = await brawl_api.get_player(tag)
            await asyncio.to_thread(db.update_player_profile, tag,
                                    prof.get("name"), (prof.get("icon") or {}).get("id"))
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
    if SEED_TAG:  # opcional: siembra un jugador inicial desde el .env
        db.add_player(SEED_TAG)
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


# --------------------------- Jugadores ---------------------------

ICON_CDN = "https://cdn.brawlify.com/profile-icons/regular/{id}.png"


def _with_icon(p: dict) -> dict:
    p = dict(p)
    p["icon_url"] = ICON_CDN.format(id=p["icon_id"]) if p.get("icon_id") else None
    return p


@app.get("/api/players")
def api_players():
    return [_with_icon(p) for p in db.list_players()]


@app.post("/api/players")
async def api_add_player(payload: dict = Body(...)):
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
    is_new = await asyncio.to_thread(db.add_player, tag, name, icon_id)
    # Sondeo inmediato para que aparezcan datos al momento.
    try:
        await _poll_player(tag)
    except Exception as e:  # noqa: BLE001
        print(f"[add] aviso: sondeo inicial de {tag} falló: {e}")
    return {"tag": tag, "name": name, "is_new": is_new}


@app.delete("/api/players/{tag}")
def api_remove_player(tag: str):
    db.remove_player(tag)
    return {"removed": db.normalize_tag(tag)}


# --------------------------- Estadísticas ---------------------------

def _filters(player, mode, map_, brawler, vs):
    return {"player": player, "mode": mode, "map": map_, "brawler": brawler, "vs": vs}


@app.get("/api/overview")
def api_overview(player: str = Query(None), mode: str = Query(None), map: str = Query(None),
                 brawler: str = Query(None), vs: str = Query(None)):
    return db.overview(_filters(player, mode, map, brawler, vs))


@app.get("/api/winrate")
def api_winrate(by: str = Query("brawler"), player: str = Query(None), mode: str = Query(None),
                map: str = Query(None), brawler: str = Query(None), vs: str = Query(None)):
    try:
        return db.winrate_by(by, _filters(player, mode, map, brawler, vs))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/vs")
def api_vs(player: str = Query(None), mode: str = Query(None), map: str = Query(None),
           brawler: str = Query(None)):
    return db.winrate_vs(_filters(player, mode, map, brawler, None))


@app.get("/api/filters")
def api_filters(player: str = Query(None)):
    return db.distinct_values(player)


@app.get("/api/assets")
async def api_assets():
    """Retratos de brawlers, iconos de modo (con color) e imágenes de mapas (Brawlify)."""
    return await assets.get_assets()


@app.get("/api/battles")
def api_battles(player: str = Query(None), mode: str = Query(None), map: str = Query(None),
                brawler: str = Query(None), vs: str = Query(None),
                limit: int = Query(25), offset: int = Query(0)):
    limit = max(1, min(limit, 100))
    return db.list_battles(_filters(player, mode, map, brawler, vs), limit=limit, offset=offset)


@app.put("/api/battles/{battle_id}/manual")
def api_set_manual(battle_id: str, payload: dict = Body(...)):
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
def api_status():
    return {
        "configured": bool(brawl_api.TOKEN),
        "players": db.active_player_tags(),
        "poll_interval": POLL_INTERVAL,
        "api_base": brawl_api.BASE,
        "via_proxy": brawl_api.using_proxy(),
        "coach_configured": coach.configured(),
        "last_poll": _last_poll,
    }


@app.post("/api/poll")
async def api_poll(player: str = Query(None)):
    if not brawl_api.TOKEN:
        return JSONResponse({"error": "Falta BRAWL_API_TOKEN en .env"}, status_code=400)
    try:
        if player:
            new = await _poll_player(player)
            return {"new": new, "players": 1, "at": datetime.now(timezone.utc).isoformat()}
        return await _poll_all()
    except Exception as e:  # noqa: BLE001
        _last_poll["error"] = str(e)
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/coach")
async def api_coach(payload: dict = Body(...)):
    player = (payload or {}).get("player")
    brawler = (payload or {}).get("brawler") or None
    if not player:
        return JSONResponse({"error": "Falta el jugador."}, status_code=400)
    try:
        advice = await coach.generate_advice(player, brawler)
        return {"advice": advice}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
