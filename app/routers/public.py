"""Rutas PÚBLICAS (sin necesidad de cuenta) para el modo invitado.

Un visitante sin cuenta puede ver la comunidad: la lista de usuarios (ordenada por relevancia) y sus
perfiles públicos de solo lectura (datos agregados; nunca privados). Solo lectura, sin escritura."""
import asyncio
import time

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from .. import db, brawl_api

router = APIRouter()

_ICON_CDN = "https://cdn.brawlify.com/profile-icons/regular/{id}.png"

# Rate-limit sencillo en memoria para la búsqueda pública por tag (evita inundar la tabla
# de jugadores y la cuota de la API de Supercell desde una vía anónima).
_LOOKUP_HITS: dict = {}
_LOOKUP_MAX = 8            # búsquedas
_LOOKUP_WINDOW = 60.0      # por minuto y por IP


def _lookup_rate_ok(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _LOOKUP_HITS.get(ip or "?", []) if now - t < _LOOKUP_WINDOW]
    if len(hits) >= _LOOKUP_MAX:
        _LOOKUP_HITS[ip or "?"] = hits
        return False
    hits.append(now)
    _LOOKUP_HITS[ip or "?"] = hits
    return True


@router.get("/api/public/player/{tag}")
async def api_public_player_lookup(tag: str, request: Request):
    """Búsqueda PÚBLICA por tag (para invitados sin cuenta): da de alta al jugador en el
    tracking (huérfano, sin dueño), lo sondea y devuelve un RESUMEN público. Al reconsultar,
    los datos se actualizan (el poller ya lo tiene fichado). No requiere cuenta."""
    if not brawl_api.TOKEN:
        return JSONResponse({"error": "La búsqueda no está disponible ahora mismo."}, status_code=503)
    ip = request.client.host if request.client else "?"
    if not _lookup_rate_ok(ip):
        return JSONResponse({"error": "Demasiadas búsquedas seguidas. Espera un minuto."}, status_code=429)
    ntag = db.normalize_tag(tag)
    if len(ntag) < 4:
        return JSONResponse({"error": "Tag inválido. Ejemplo: #2P0LYQQRJ"}, status_code=400)
    try:
        profile = await brawl_api.get_player(ntag)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "404" in msg or "notfound" in msg.lower():
            return JSONResponse({"error": f"No existe ningún jugador con el tag {ntag}."}, status_code=404)
        return JSONResponse({"error": "No se pudo validar el tag ahora mismo."}, status_code=502)
    name = profile.get("name")
    icon_id = (profile.get("icon") or {}).get("id")
    _club = profile.get("club") or {}
    club_name, club_tag = _club.get("name"), _club.get("tag")
    is_new = await asyncio.to_thread(db.add_player, ntag, name, icon_id, club_name, club_tag)  # alta huérfana
    await asyncio.to_thread(db.snapshot_brawlers, ntag, profile.get("brawlers"))
    if is_new:  # solo sondeamos al ALTA; en reconsultas ya lo actualiza el poller de fondo
        try:
            from ..main import _poll_player
            await _poll_player(ntag)
        except Exception as e:  # noqa: BLE001
            print(f"[public lookup] sondeo de {ntag} falló: {e}")
    f = {"player": ntag}
    report = await asyncio.to_thread(db.report_analytics, f)
    return {
        "tag": ntag, "name": name, "icon_id": icon_id, "club_name": club_name,
        "icon_url": (_ICON_CDN.format(id=icon_id) if icon_id else None),
        "report": report,
        "rating": await asyncio.to_thread(db.account_rating, ntag),
        "roles": await asyncio.to_thread(db.winrate_by_role, f),
        "brawlers": await asyncio.to_thread(db.winrate_by, "brawler", f),
        "guest": True,
    }


@router.get("/api/public/users")
def api_public_users(q: str = Query(None)):
    """Comunidad: usuarios ordenados por relevancia (contribución). Filtro opcional por nombre."""
    return {"users": db.public_users(60, q)}


@router.get("/api/public/users/{uid}/profile")
def api_public_profile(uid: int):
    """Perfil público de un usuario (sus jugadores, agregado). Accesible sin cuenta."""
    target = db.get_user_by_id(uid)
    if not target or target.get("hidden"):
        # Cuentas de sistema (tester) no son visibles públicamente.
        return JSONResponse({"error": "No existe ese usuario."}, status_code=404)
    players = [{"tag": p["tag"], "name": p["name"], "icon_id": p.get("icon_id"),
                "club_name": p.get("club_name"), "battles": p.get("battles") or 0,
                "is_main": bool(p.get("is_main"))}
               for p in db.list_players_for_user(uid)]
    return {"id": target["id"], "username": target["username"], "country": target.get("country"),
            "relation": "none", "players": players, "guest": True}


@router.get("/api/public/users/{uid}/players/{tag}/summary")
def api_public_summary(uid: int, tag: str):
    """Resumen de analíticas de un jugador del perfil público (3 líneas + gráficas). Sin cuenta.
    El tag DEBE pertenecer a ese usuario (no se permiten consultas de tags arbitrarios)."""
    owned = {db.normalize_tag(p["tag"]) for p in db.list_players_for_user(uid)}
    ntag = db.normalize_tag(tag)
    if ntag not in owned:
        return JSONResponse({"error": "Ese jugador no pertenece a este usuario."}, status_code=404)
    f = {"player": ntag}
    return {"report": db.report_analytics(f), "rating": db.account_rating(ntag),
            "roles": db.winrate_by_role(f), "brawlers": db.winrate_by("brawler", f)}
