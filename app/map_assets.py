"""Resolución robusta de la IMAGEN (layout cenital) de un mapa, con auto-hosting.

Problema: la imagen se sirve desde Brawlify, pero a veces el asset no existe (mapas muy
nuevos → 404) o el cliente bloquea dominios de terceros (Brave Shields). Solución: un proxy
con CACHÉ EN DISCO. El frontend pide la imagen a NUESTRO dominio (`/api/map-image/{id}`); aquí
resolvemos una CADENA DE FALLBACK en el backend, descargamos el PNG la primera vez y lo
servimos ya local. Ventajas: inmune a bloqueos del cliente (dominio propio), resiliente a
caídas de la CDN (espejo), y se AUTO-CURA cuando el asset aparece (caché negativa corta).

Los mapas se indexan por ID NUMÉRICO del juego (no por nombre): la CDN de Brawlify sirve
`/maps/regular/{id}.png`, y su espejo en GitHub (`Brawlify/CDN`) sirve el MISMO contenido.
"""

from __future__ import annotations

import os
import time
import httpx

_HEADERS = {"User-Agent": "BrawlTracker/1.0 (personal stats app)"}
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "map_cache")
_TIMEOUT = 8.0

# Caché NEGATIVA en memoria: id -> instante hasta el que NO reintentamos. Corta (6 h) para que,
# en cuanto Brawlify publique el render de un mapa nuevo, se auto-cure sin tocar código.
_neg: dict[int, float] = {}
_NEG_TTL = 6 * 3600


def _candidates(map_id: int) -> list[str]:
    """URLs candidatas EN ORDEN. La CDN (Cloudflare) primero; el espejo de GitHub como
    respaldo (mismo asset) si la CDN falla. Ambas son fuentes gratuitas sin clave."""
    return [
        f"https://cdn.brawlify.com/maps/regular/{map_id}.png",
        f"https://raw.githubusercontent.com/Brawlify/CDN/master/maps/regular/{map_id}.png",
    ]


def _path(map_id: int) -> str:
    return os.path.join(_CACHE_DIR, f"{map_id}.png")


def get_local_image(map_id: int) -> str | None:
    """Ruta local del PNG del mapa, descargándolo la primera vez desde la cadena de fallback.
    Devuelve None si el asset no existe en ninguna fuente (el llamador sirve un placeholder).
    Pensada para ejecutarse en un hilo (I/O de red y disco síncronos)."""
    if not map_id or map_id <= 0:
        return None
    p = _path(map_id)
    try:
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p
    except OSError:
        pass
    if _neg.get(map_id, 0) > time.time():   # aún en caché negativa: no martilleamos upstream
        return None
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
    except OSError:
        return None
    for url in _candidates(map_id):
        try:
            r = httpx.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
        except Exception:  # noqa: BLE001 (red: pasa a la siguiente candidata)
            continue
        ctype = r.headers.get("content-type", "")
        if r.status_code == 200 and ctype.startswith("image") and r.content:
            tmp = p + ".tmp"
            try:
                with open(tmp, "wb") as f:
                    f.write(r.content)
                os.replace(tmp, p)   # escritura atómica (evita ficheros a medias)
                return p
            except OSError:
                return None
    _neg[map_id] = time.time() + _NEG_TTL   # no está en ninguna fuente: reintentar en 6 h
    return None
