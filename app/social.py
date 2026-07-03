"""Compartir en redes (fase F).

Dos vías de compartir:
  1) SIN claves (funciona ya): Web Share nativo, copiar enlace/texto y enlaces de "intención" de
     X, Reddit y Facebook (abren la ventana de compartir de cada red con el texto prerelleno).
  2) Publicación DIRECTA vía OAuth (Instagram, TikTok, X API, Reddit API, Facebook Graph): requiere
     registrar una app por plataforma y poner sus claves en variables de entorno DEL SERVIDOR. Nunca
     van en el JS de cliente (es visible en el navegador). Mientras falten las claves, la plataforma
     aparece como "no configurada" y solo se ofrece la vía 1.

Para activar la publicación directa de una plataforma, define sus variables de entorno (ver `env`).
"""
import os

PLATFORMS = [
    {"id": "x", "name": "X (Twitter)", "icon": "𝕏", "intent": True,
     "env": ["X_CLIENT_ID", "X_CLIENT_SECRET"]},
    {"id": "reddit", "name": "Reddit", "icon": "R", "intent": True,
     "env": ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"]},
    {"id": "facebook", "name": "Facebook", "icon": "f", "intent": True,
     "env": ["FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET"]},
    {"id": "instagram", "name": "Instagram", "icon": "IG", "intent": False,
     "env": ["INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET"]},
    {"id": "tiktok", "name": "TikTok", "icon": "TT", "intent": False,
     "env": ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"]},
]


def is_configured(p: dict) -> bool:
    """True si la app de esa plataforma tiene TODAS sus claves en el entorno del servidor."""
    return all(os.environ.get(e) for e in p.get("env", []))


def get_platform(pid: str) -> dict | None:
    return next((p for p in PLATFORMS if p["id"] == pid), None)


def platforms_status() -> list[dict]:
    """Estado para el cliente: qué plataformas permiten enlace de intención y cuáles tienen la
    publicación directa (OAuth) configurada. No expone ninguna clave."""
    return [{"id": p["id"], "name": p["name"], "icon": p["icon"],
             "intent": p["intent"], "configured": is_configured(p)} for p in PLATFORMS]
