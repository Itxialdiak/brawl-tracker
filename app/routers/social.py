"""Rutas para vincular redes sociales y compartir (fase F).

Flujo de vinculación (OAuth2 código→token), listo para funcionar en cuanto se pongan las claves:
  · GET  /api/social/config                 → estado de plataformas + cuáles tiene vinculadas el usuario
  · GET  /api/social/{platform}/connect      → devuelve la URL de login OAuth de la red (guarda state en sesión)
  · GET  /api/social/{platform}/callback     → la red vuelve aquí; canjeamos el código por el token y lo guardamos
  · DELETE /api/social/{platform}            → desvincula
  · POST /api/social/{platform}/post         → publicar (requiere la app aprobada + implementación por red)

Las claves van SOLO en el servidor (variables de entorno). Sin claves, la plataforma sale "no configurada"."""
import secrets

import httpx
from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import auth, db, social

router = APIRouter()


def _redirect_uri(request: Request, pid: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/social/{pid}/callback"


@router.get("/api/social/config")
def api_social_config(user: dict = Depends(auth.require_user)):
    return {"platforms": social.platforms_status(),
            "linked": [a["platform"] for a in db.list_social_accounts(user["id"])]}


@router.get("/api/social/accounts")
def api_social_accounts(user: dict = Depends(auth.require_user)):
    return {"accounts": db.list_social_accounts(user["id"])}


@router.get("/api/social/{platform}/connect")
def api_social_connect(platform: str, request: Request, user: dict = Depends(auth.require_user)):
    """Devuelve la URL de autorización OAuth; el cliente redirige al usuario allí."""
    p = social.get_platform(platform)
    if not p:
        return JSONResponse({"error": "Plataforma desconocida."}, status_code=404)
    if not social.is_configured(p):
        return JSONResponse(
            {"error": f"{p['name']} no está configurada en el servidor todavía (faltan las claves de la app)."},
            status_code=400)
    state = secrets.token_urlsafe(24)
    url, verifier = social.build_authorize_url(p, _redirect_uri(request, platform), state)
    request.session[f"oauth_{platform}"] = {"state": state, "verifier": verifier, "uid": user["id"]}
    return {"url": url}


@router.get("/api/social/{platform}/callback")
async def api_social_callback(platform: str, request: Request, code: str = None,
                              state: str = None, error: str = None):
    """La red social vuelve aquí tras el login. Canjea el código por el token y lo guarda."""
    p = social.get_platform(platform)
    sess = request.session.get(f"oauth_{platform}") or {}
    request.session.pop(f"oauth_{platform}", None)

    def back(status: str):
        return RedirectResponse(url=f"/?social={status}", status_code=302)

    if error or not code or not p or not sess or sess.get("state") != state:
        return back("error")
    uid, cid, csec = sess.get("uid"), *social.creds(p)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(request, platform),
        p.get("client_id_param", "client_id"): cid,
    }
    if sess.get("verifier"):
        data["code_verifier"] = sess["verifier"]
    # X y Reddit autentican la app con HTTP Basic (id:secret); el resto lo mandan en el cuerpo.
    auth_basic = (cid, csec) if platform in ("x", "reddit") else None
    if auth_basic is None:
        data["client_secret"] = csec
    try:
        async with httpx.AsyncClient(timeout=15) as cx:
            r = await cx.post(p["token_url"], data=data, auth=auth_basic,
                              headers={"Accept": "application/json", "User-Agent": "BrawlSensei/1.0"})
        tok = r.json() if "json" in (r.headers.get("content-type") or "") else {}
        access = tok.get("access_token")
        if not access:
            return back("error")
        db.link_social_account(
            uid, platform,
            external_id=str(tok.get("open_id") or tok.get("user_id") or ""),
            access_token=access, refresh_token=tok.get("refresh_token"))
        return back("connected")
    except Exception:  # noqa: BLE001
        return back("error")


@router.delete("/api/social/{platform}")
def api_social_disconnect(platform: str, user: dict = Depends(auth.require_user)):
    db.unlink_social_account(user["id"], platform)
    return {"ok": True, "accounts": db.list_social_accounts(user["id"])}


def _api_error(name: str, r: httpx.Response) -> str:
    try:
        j = r.json()
        msg = (j.get("detail") or j.get("error") or j.get("message")
               or (j.get("errors") and j["errors"][0].get("detail")) or "")
    except Exception:  # noqa: BLE001
        msg = (r.text or "")[:180]
    return f"{name} rechazó la publicación ({r.status_code}). {msg}".strip()


@router.post("/api/social/{platform}/post")
async def api_social_post(platform: str, payload: dict = Body(default={}), user: dict = Depends(auth.require_user)):
    """Publicación DIRECTA en nombre del usuario con su token OAuth guardado.
    X/Reddit/Facebook publican texto + enlace (el enlace lleva Open Graph → vista previa con imagen).
    Instagram/TikTok necesitan el flujo de subida de medios y app aprobada (pendiente)."""
    p = social.get_platform(platform)
    if not p:
        return JSONResponse({"error": "Plataforma desconocida."}, status_code=404)
    if not social.is_configured(p):
        return JSONResponse({"error": f"{p['name']} no está configurada en el servidor."}, status_code=400)
    tok = db.get_social_token(user["id"], platform)
    if not tok or not tok.get("access_token"):
        return JSONResponse({"error": f"No tienes vinculada tu cuenta de {p['name']}."}, status_code=400)
    access = tok["access_token"]
    b = payload or {}
    title, text, url = (b.get("title") or "").strip(), (b.get("text") or "").strip(), (b.get("url") or "").strip()
    body_text = " ".join(x for x in (title, text) if x).strip()
    full = (body_text + (("\n" + url) if url else "")).strip()
    try:
        async with httpx.AsyncClient(timeout=20) as cx:
            if platform == "x":
                r = await cx.post("https://api.twitter.com/2/tweets",
                                  headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
                                  json={"text": full[:280]})
                if r.status_code in (200, 201):
                    return {"ok": True}
                return JSONResponse({"error": _api_error("X", r)}, status_code=502)

            if platform == "reddit":
                sr = (b.get("subreddit") or "").strip().lstrip("r/").strip("/")
                if not sr:
                    return JSONResponse({"error": "Indica el subreddit donde publicar (p. ej. «BrawlStars»)."}, status_code=400)
                data = {"sr": sr, "api_type": "json", "title": (title or "Brawl Sensei")[:300],
                        "kind": "link" if url else "self"}
                if url:
                    data["url"] = url
                else:
                    data["text"] = body_text
                r = await cx.post("https://oauth.reddit.com/api/submit", data=data,
                                  headers={"Authorization": f"Bearer {access}", "User-Agent": "BrawlSensei/1.0"})
                errs = (r.json().get("json", {}).get("errors") if r.status_code == 200 else None)
                if r.status_code == 200 and not errs:
                    return {"ok": True}
                return JSONResponse({"error": _api_error("Reddit", r)}, status_code=502)

            if platform == "facebook":
                data = {"message": full, "access_token": access}
                if url:
                    data["link"] = url
                r = await cx.post("https://graph.facebook.com/v19.0/me/feed", data=data)
                if r.status_code == 200:
                    return {"ok": True}
                return JSONResponse({"error": _api_error("Facebook", r)}, status_code=502)

            # Instagram y TikTok: publicación basada en medios (crear contenedor + publicar), con app
            # aprobada. El código base está listo para añadirse cuando esas apps estén aprobadas.
            return JSONResponse(
                {"error": f"La publicación en {p['name']} requiere el flujo de subida de medios y que la "
                          f"app esté aprobada por la plataforma. Está documentado y pendiente de activar."},
                status_code=501)
    except httpx.HTTPError:
        return JSONResponse({"error": f"No se pudo contactar con la API de {p['name']}."}, status_code=502)
