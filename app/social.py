"""Compartir en redes (fase F): infraestructura de vinculación OAuth + publicación.

CÓMO ACTIVARLO (resumen; guía completa en docs/REDES_SOCIALES_OAUTH.md):
  1. Registra una app de desarrollador en cada plataforma que quieras ofrecer.
  2. Pon como "Redirect URI" (URL de retorno):  https://TU-DOMINIO/api/social/<plataforma>/callback
  3. Define en el SERVIDOR las variables de entorno con sus claves (ver `env` de cada plataforma).
     Nunca pongas estas claves en el JS de cliente (es visible en el navegador).

Cada usuario vincula SUS cuentas voluntariamente: al pulsar "Vincular", va al login OAuth de la red,
da permisos, y guardamos su token para poder publicar en su nombre. Si no vincula ninguna red, no se
le muestran los iconos de compartir. Mientras falten las claves de una plataforma, sale "no configurada".
"""
import base64
import hashlib
import os
import secrets

# `flow`:  oauth2  -> flujo estándar código→token.  El resto de campos son la config OAuth de cada red.
PLATFORMS = [
    {"id": "x", "name": "X (Twitter)", "icon": "𝕏", "intent": True,
     "env": ["X_CLIENT_ID", "X_CLIENT_SECRET"],
     "auth_url": "https://twitter.com/i/oauth2/authorize",
     "token_url": "https://api.twitter.com/2/oauth2/token",
     "scope": "tweet.read tweet.write users.read offline.access", "pkce": True},
    {"id": "reddit", "name": "Reddit", "icon": "R", "intent": True,
     "env": ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"],
     "auth_url": "https://www.reddit.com/api/v1/authorize",
     "token_url": "https://www.reddit.com/api/v1/access_token",
     "scope": "identity submit", "extra_auth": {"duration": "permanent"}},
    {"id": "facebook", "name": "Facebook", "icon": "f", "intent": True,
     "env": ["FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET"],
     "auth_url": "https://www.facebook.com/v19.0/dialog/oauth",
     "token_url": "https://graph.facebook.com/v19.0/oauth/access_token",
     "scope": "public_profile"},
    {"id": "instagram", "name": "Instagram", "icon": "IG", "intent": False,
     "env": ["INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET"],
     "auth_url": "https://api.instagram.com/oauth/authorize",
     "token_url": "https://api.instagram.com/oauth/access_token",
     "scope": "user_profile,user_media"},
    {"id": "tiktok", "name": "TikTok", "icon": "TT", "intent": False,
     "env": ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"],
     "auth_url": "https://www.tiktok.com/v2/auth/authorize/",
     "token_url": "https://open.tiktokapis.com/v2/oauth/token/",
     "scope": "user.info.basic,video.publish", "client_id_param": "client_key"},
]


def get_platform(pid: str) -> dict | None:
    return next((p for p in PLATFORMS if p["id"] == pid), None)


def creds(p: dict) -> tuple:
    """(client_id, client_secret) desde el entorno; (None, None) si faltan."""
    env = p.get("env", [])
    cid = os.environ.get(env[0]) if len(env) > 0 else None
    csec = os.environ.get(env[1]) if len(env) > 1 else None
    return cid, csec


def is_configured(p: dict) -> bool:
    cid, csec = creds(p)
    return bool(cid and csec)


def platforms_status() -> list[dict]:
    """Estado para el cliente (sin claves): id, nombre, si permite enlace de intención y si la
    publicación directa (OAuth) está configurada en el servidor."""
    return [{"id": p["id"], "name": p["name"], "icon": p["icon"],
             "intent": p["intent"], "configured": is_configured(p)} for p in PLATFORMS]


def _pkce_pair() -> tuple:
    """(code_verifier, code_challenge) para PKCE (S256), que usa X."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def build_authorize_url(p: dict, redirect_uri: str, state: str) -> tuple:
    """Construye la URL de autorización OAuth de la plataforma. Devuelve (url, code_verifier|None)."""
    from urllib.parse import urlencode
    cid, _ = creds(p)
    params = {
        p.get("client_id_param", "client_id"): cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": p.get("scope", ""),
        "state": state,
    }
    params.update(p.get("extra_auth", {}))
    verifier = None
    if p.get("pkce"):
        verifier, challenge = _pkce_pair()
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    return f"{p['auth_url']}?{urlencode(params)}", verifier
