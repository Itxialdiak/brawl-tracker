"""Rutas de jugadores seguidos: listar, añadir (alta + snapshot de brawlers), dejar de seguir.

Extraído de main.py; se incluye con app.include_router()."""
import asyncio
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from .. import db, brawl_api, auth

router = APIRouter()


# --------------------------- Jugadores ---------------------------

ICON_CDN = "https://cdn.brawlify.com/profile-icons/regular/{id}.png"


def _with_icon(p: dict) -> dict:
    p = dict(p)
    p["icon_url"] = ICON_CDN.format(id=p["icon_id"]) if p.get("icon_id") else None
    return p


@router.get("/api/players")
def api_players(user: dict = Depends(auth.require_user)):
    return [_with_icon(p) for p in db.list_players_for_user(user["id"])]


@router.post("/api/players")
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
    # Sondeo inmediato para que aparezcan datos al momento. El poller vive en main
    # (depende de su estado vivo); import perezoso para no crear un ciclo de imports.
    try:
        from ..main import _poll_player
        await _poll_player(tag)
    except Exception as e:  # noqa: BLE001
        print(f"[add] aviso: sondeo inicial de {tag} falló: {e}")
    return {"tag": tag, "name": name, "is_new": is_new}


@router.post("/api/players/{tag}/main")
def api_set_main_player(tag: str, user: dict = Depends(auth.require_user)):
    """Declara el jugador PRINCIPAL de la cuenta (identidad; def. del perfil público). Debe
    ser un jugador que sigues. Recalcula el rol Croker según el club de ese jugador."""
    if not db.set_main_player(user["id"], tag):
        return JSONResponse({"error": "Ese jugador no está en tu cuenta."}, status_code=400)
    return {"ok": True, "main": db.normalize_tag(tag)}


@router.delete("/api/players/{tag}")
def api_remove_player(tag: str, user: dict = Depends(auth.require_user)):
    """Deja de seguir a un jugador. No permite quitar el PRINCIPAL (es tu identidad; primero
    cambia el principal a otro)."""
    ntag = db.normalize_tag(tag)
    if db.get_main_player(user["id"]) == ntag:
        return JSONResponse(
            {"error": "Es tu jugador principal. Marca otro como principal antes de quitarlo."},
            status_code=400)
    # Solo lo desvincula de este usuario; si no lo sigue nadie más, db lo limpia.
    db.unfollow_player(user["id"], tag)
    return {"removed": ntag}
