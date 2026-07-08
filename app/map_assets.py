"""Resolución robusta de la IMAGEN (layout cenital) de un mapa, con auto-hosting.

Problema: la imagen se sirve desde Brawlify, pero a veces el asset no existe (mapas muy
nuevos → 404) o el cliente bloquea dominios de terceros (Brave Shields). Solución: un proxy.
El frontend pide la imagen a NUESTRO dominio (`/api/map-image/{id}`); aquí resolvemos una
CADENA DE FALLBACK en el backend (CDN de Brawlify → espejo de GitHub, MISMO asset) y servimos
el PNG. Se CACHEA en disco si se puede; si el disco no es escribible (p. ej. la carpeta de la
app es de solo lectura en la VPS), se cachea en MEMORIA y se sirven los bytes igualmente, para
que NUNCA dependa de poder escribir en disco. Ventajas: inmune a bloqueos del cliente (dominio
propio), resiliente a caídas de la CDN (espejo) y se AUTO-CURA (caché negativa corta).

Los mapas se indexan por ID NUMÉRICO del juego: la CDN de Brawlify sirve `/maps/regular/{id}.png`
y su espejo en GitHub (`Brawlify/CDN`, rama master) sirve el MISMO contenido.
"""

from __future__ import annotations

import os
import time
import httpx

_HEADERS = {"User-Agent": "BrawlTracker/1.0 (personal stats app)"}
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "map_cache")
_TIMEOUT = httpx.Timeout(8.0, connect=3.0)   # connect corto: si un host está bloqueado, cae rápido al siguiente

# Caché NEGATIVA en memoria: id -> instante hasta el que NO reintentamos (6 h, para auto-curar
# mapas nuevos cuando Brawlify publique su render sin tocar código).
_neg: dict[int, float] = {}
_NEG_TTL = 6 * 3600

# Caché POSITIVA en memoria: fallback si el disco no es escribible (evita re-descargar cada vez).
_mem: dict[int, bytes] = {}
_MEM_MAX = 500
_disk_ok = True   # se pone a False (una vez) si detectamos que no se puede escribir en disco


def _candidates(map_id: int) -> list[str]:
    """URLs candidatas EN ORDEN. La CDN (Cloudflare) primero; el espejo de GitHub como respaldo
    (mismo asset) si la CDN falla o está bloqueada por egress. Ambas gratis y sin clave."""
    return [
        f"https://cdn.brawlify.com/maps/regular/{map_id}.png",
        f"https://raw.githubusercontent.com/Brawlify/CDN/master/maps/regular/{map_id}.png",
    ]


def _path(map_id: int) -> str:
    return os.path.join(_CACHE_DIR, f"{map_id}.png")


def _try_write_disk(map_id: int, content: bytes) -> bool:
    """Intenta cachear en disco. Devuelve True si lo consiguió. Si el disco no es escribible,
    lo avisa UNA vez y a partir de ahí no vuelve a intentarlo (se usará la memoria)."""
    global _disk_ok
    if not _disk_ok:
        return False
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        tmp = _path(map_id) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(content)
        os.replace(tmp, _path(map_id))   # escritura atómica
        return True
    except OSError as e:  # noqa: BLE001
        _disk_ok = False
        print(f"[map-image] aviso: caché en disco no disponible ({e}); usaré memoria y serviré igualmente.")
        return False


def _mem_put(map_id: int, content: bytes) -> None:
    if len(_mem) >= _MEM_MAX:
        _mem.pop(next(iter(_mem)), None)   # descarta el más antiguo (FIFO simple)
    _mem[map_id] = content


def get_map_image(map_id: int):
    """Devuelve `('path', ruta)` o `('bytes', datos)` del PNG del mapa, o `None` si no hay
    imagen en ninguna fuente (el llamador sirve un placeholder). Descarga de la cadena de
    fallback y cachea (disco si puede, si no memoria). Pensada para un hilo (I/O síncrona)."""
    if not map_id or map_id <= 0:
        return None
    p = _path(map_id)
    try:
        if _disk_ok and os.path.exists(p) and os.path.getsize(p) > 0:
            return ("path", p)
    except OSError:
        pass
    if map_id in _mem:
        return ("bytes", _mem[map_id])
    if _neg.get(map_id, 0) > time.time():   # aún en caché negativa: no martilleamos upstream
        return None
    last = None
    for url in _candidates(map_id):
        try:
            r = httpx.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
        except Exception as e:  # noqa: BLE001 (red bloqueada/caída: probamos la siguiente)
            last = f"{type(e).__name__} @ {url.split('/')[2]}"
            continue
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and r.content:
            if not _try_write_disk(map_id, r.content):
                _mem_put(map_id, r.content)   # disco no disponible: guarda en memoria
            return ("bytes", r.content)
        last = f"HTTP {r.status_code} @ {url.split('/')[2]}"
    _neg[map_id] = time.time() + _NEG_TTL   # no está en ninguna fuente / red bloqueada
    print(f"[map-image] {map_id}: sin imagen ({last})")
    return None
