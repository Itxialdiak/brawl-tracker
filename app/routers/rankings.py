"""Rutas de Rankings oficiales (país/club) y personalizados (liguillas: crear, compartir, importar, ordenar).

Extraído de main.py; se incluye con app.include_router()."""
import time
import asyncio
from fastapi import APIRouter, Body, Query, Depends
from fastapi.responses import JSONResponse
from .. import db, brawl_api, auth
from ..api_common import _require_follow, _get_player_cached, _parse_player_tags

router = APIRouter()


# --------------------------- Rankings ---------------------------

def _ranking_country(user: dict, scope: str) -> str:
    """'global' o el código de país del usuario, según el scope pedido."""
    if scope == "national" and user.get("country"):
        return user["country"].lower()
    return "global"


@router.get("/api/player-profile")
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


@router.get("/api/rankings")
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


@router.get("/api/club")
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


@router.get("/api/custom-rankings")
def api_cr_list(user: dict = Depends(auth.require_user)):
    return {"rankings": [_cr_public(r) for r in db.list_custom_rankings_for_user(user["id"])]}


@router.post("/api/custom-rankings")
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


@router.get("/api/custom-rankings/{rid}")
def api_cr_get(rid: int, user: dict = Depends(auth.require_user)):
    if not db.user_can_view_ranking(user["id"], rid):
        return JSONResponse({"error": "No encontrado."}, status_code=404)
    cr = db.get_custom_ranking(rid)
    if not cr:
        return JSONResponse({"error": "No encontrado."}, status_code=404)
    return {"id": cr["id"], "name": cr["name"], "share_token": cr["share_token"],
            "players": cr["player_tags"], "owned": cr["owner_user_id"] == user["id"]}


@router.put("/api/custom-rankings/{rid}")
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


@router.delete("/api/custom-rankings/{rid}")
def api_cr_delete(rid: int, user: dict = Depends(auth.require_user)):
    return {"ok": True, "result": db.delete_or_unsubscribe_ranking(user["id"], rid)}


@router.get("/api/custom-rankings/{rid}/standings")
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


@router.get("/api/shared-ranking")
def api_shared(token: str = Query(None), user: dict = Depends(auth.require_user)):
    if not token:
        return JSONResponse({"error": "Falta el enlace."}, status_code=400)
    cr = db.get_custom_ranking_by_token(token)
    if not cr:
        return JSONResponse({"error": "Enlace no válido o liguilla borrada."}, status_code=404)
    return {"id": cr["id"], "name": cr["name"], "count": len(cr["player_tags"]),
            "owned": cr["owner_user_id"] == user["id"],
            "already": db.user_can_view_ranking(user["id"], cr["id"])}


@router.post("/api/custom-rankings/import")
def api_cr_import(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    token = ((payload or {}).get("token") or "").strip()
    cr = db.get_custom_ranking_by_token(token)
    if not cr:
        return JSONResponse({"error": "Enlace no válido."}, status_code=404)
    if cr["owner_user_id"] == user["id"]:
        return {"ok": True, "id": cr["id"], "name": cr["name"], "self": True}
    db.subscribe_ranking(user["id"], cr["id"])
    return {"ok": True, "id": cr["id"], "name": cr["name"]}


@router.get("/api/rankings-order")
def api_order_get(user: dict = Depends(auth.require_user)):
    return {"order": db.get_rankings_order(user["id"])}


@router.post("/api/rankings-order")
def api_order_set(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    order = (payload or {}).get("order")
    if not isinstance(order, list):
        return JSONResponse({"error": "Orden no válido."}, status_code=400)
    db.set_rankings_order(user["id"], [str(x) for x in order][:300])
    return {"ok": True}
