"""Rutas PÚBLICAS (sin necesidad de cuenta) para el modo invitado.

Un visitante sin cuenta puede ver la comunidad: la lista de usuarios (ordenada por relevancia) y sus
perfiles públicos de solo lectura (datos agregados; nunca privados). Solo lectura, sin escritura."""
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from .. import db

router = APIRouter()


@router.get("/api/public/users")
def api_public_users(q: str = Query(None)):
    """Comunidad: usuarios ordenados por relevancia (contribución). Filtro opcional por nombre."""
    return {"users": db.public_users(60, q)}


@router.get("/api/public/users/{uid}/profile")
def api_public_profile(uid: int):
    """Perfil público de un usuario (sus jugadores, agregado). Accesible sin cuenta."""
    target = db.get_user_by_id(uid)
    if not target:
        return JSONResponse({"error": "No existe ese usuario."}, status_code=404)
    players = [{"tag": p["tag"], "name": p["name"], "icon_id": p.get("icon_id"),
                "club_name": p.get("club_name"), "battles": p.get("battles") or 0}
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
            "roles": db.winrate_by_role(f)}
