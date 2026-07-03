"""Imágenes para compartir en redes (fase F): tarjetas PNG con la marca de agua del logo.

Son públicas (las lee el usuario y los rastreadores de las redes al previsualizar el enlace). La
publicación enlaza al perfil público del autor mediante `?user=<id>` (lo maneja el frontend)."""
import html as _html
import json as _json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .. import db, share_image

router = APIRouter()

_PNG_HEADERS = {"Cache-Control": "public, max-age=300"}


def _og_page(title: str, desc: str, image: str, url: str, redirect: str) -> HTMLResponse:
    """Página mínima con Open Graph/Twitter Card (para que la vista previa del enlace muestre la
    imagen con marca de agua) que redirige a la SPA (los humanos van al perfil; los rastreadores
    leen las meta). Los rastreadores no ejecutan JS, por eso las meta van en el HTML del servidor."""
    t, dsc = _html.escape(title), _html.escape(desc)
    img, u = _html.escape(image), _html.escape(url)
    return HTMLResponse(f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t}</title>
<meta name="description" content="{dsc}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Brawl Sensei">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{dsc}">
<meta property="og:image" content="{img}">
<meta property="og:url" content="{u}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{t}">
<meta name="twitter:description" content="{dsc}">
<meta name="twitter:image" content="{img}">
<meta http-equiv="refresh" content="0; url={_html.escape(redirect)}">
</head><body style="background:#0a0a1f;color:#ece9ff;font-family:system-ui,sans-serif;text-align:center;padding:48px">
<p>Abriendo Brawl Sensei…</p>
<p><a href="{_html.escape(redirect)}" style="color:#3fe1ff">Continuar</a></p>
<script>location.replace({_json.dumps(redirect)})</script>
</body></html>""")


@router.get("/u/{uid}")
def share_landing_user(uid: int, request: Request):
    """Enlace de una publicación de PERFIL → vista previa con imagen + lleva al perfil público."""
    u = db.get_user_by_id(uid)
    if not u:
        return RedirectResponse("/")
    base = str(request.base_url).rstrip("/")
    n_players, n_battles = db.user_contribution(uid)
    return _og_page(
        f"@{u['username']} · Brawl Sensei",
        f"Perfil de @{u['username']} — {n_players} jugadores, {n_battles} partidas aportadas.",
        f"{base}/api/share/user/{uid}.png", f"{base}/u/{uid}", f"/?user={uid}")


@router.get("/e/{eid}")
def share_landing_event(eid: int, request: Request):
    """Enlace de una publicación de EVENTO → vista previa con imagen + lleva a la ficha del evento."""
    e = db.get_event(eid)
    if not e:
        return RedirectResponse("/")
    base = str(request.base_url).rstrip("/")
    kind = "Liga" if e.get("kind") == "league" else "Torneo"
    return _og_page(
        f"{e.get('name') or 'Evento'} · Brawl Sensei",
        f"{kind} en Brawl Sensei. ¡Únete y compite!",
        f"{base}/api/share/event/{eid}.png", f"{base}/e/{eid}", f"/?event={eid}")


@router.get("/api/share/user/{uid}.png")
def api_share_user_image(uid: int):
    """Tarjeta del perfil de un jugador (@nombre + contribución) con marca de agua."""
    u = db.get_user_by_id(uid)
    if not u:
        return JSONResponse({"error": "No existe ese usuario."}, status_code=404)
    n_players, n_battles = db.user_contribution(uid)
    stats = [("Jugadores en seguimiento", n_players), ("Partidas aportadas", n_battles)]
    png = share_image.render_card(
        eyebrow="Jugador de la comunidad", title="@" + (u["username"] or ""),
        subtitle=(u.get("country") or "") and f"País: {u['country'].upper()}", stats=stats)
    return Response(content=png, media_type="image/png", headers=_PNG_HEADERS)


@router.get("/api/share/event/{eid}.png")
def api_share_event_image(eid: int):
    """Tarjeta de un evento (nombre + tipo/formato + participantes) con marca de agua."""
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    counts = db.event_counts(eid)
    kind = "Liga" if e.get("kind") == "league" else "Torneo"
    stats = [("Participantes", f"{counts.get('participants', 0)} / {e.get('max_participants') or 12}"),
             ("Seguidores", counts.get("followers", 0))]
    png = share_image.render_card(
        eyebrow=kind, title=e.get("name") or "Evento", subtitle="En Brawl Sensei",
        stats=stats, accent=share_image._GOLD)
    return Response(content=png, media_type="image/png", headers=_PNG_HEADERS)
