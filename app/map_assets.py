"""Resolución robusta de la IMAGEN (layout cenital) de un mapa, con auto-hosting.

Problema: la imagen se sirve desde Brawlify, pero a veces el asset no existe (mapas muy
nuevos → 404) o el cliente bloquea dominios de terceros (Brave Shields). Solución: un proxy.
El frontend pide la imagen a NUESTRO dominio (`/api/map-image/{id}`); aquí resolvemos una
CADENA DE FALLBACK en el backend (CDN de Brawlify → espejo de GitHub, MISMO asset) y servimos
el PNG. Se CACHEA en disco si se puede; si no (carpeta de solo lectura), en MEMORIA, y se
sirven los bytes igualmente. Inmune a bloqueos del cliente (dominio propio) y resiliente.

IMPORTANTE (rendimiento y fiabilidad bajo ráfaga): una página pide ~40 imágenes a la vez. Para
que la ráfaga no provoque fallos, se usa UN cliente httpx compartido (pool de conexiones) y un
SEMÁFORO que limita las descargas simultáneas. Y la caché NEGATIVA solo se activa ante un 404
DEFINITIVO (el asset no existe en ninguna fuente), NUNCA ante un error de red transitorio: así
un fallo puntual se reintenta en la siguiente carga en vez de quedar 6 h en placeholder.

Los mapas se indexan por ID NUMÉRICO: la CDN sirve `/maps/regular/{id}.png` y su espejo en
GitHub (`Brawlify/CDN`, rama master) sirve el MISMO contenido.
"""

from __future__ import annotations

import os
import time
import threading
import httpx

_HEADERS = {"User-Agent": "BrawlTracker/1.0 (personal stats app)"}
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "map_cache")

# Un solo cliente reutilizable (pool de conexiones keep-alive) en vez de abrir una conexión
# nueva por imagen. El cliente sync de httpx es seguro entre hilos.
_client = httpx.Client(
    headers=_HEADERS, follow_redirects=True,
    timeout=httpx.Timeout(10.0, connect=4.0),
    limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
)
# Limita las descargas upstream simultáneas (la ráfaga de ~40 imágenes no abre 40 conexiones).
_sem = threading.Semaphore(8)

# Caché NEGATIVA en memoria: id -> instante hasta el que NO reintentamos. SOLO se activa ante un
# 404 definitivo (el render aún no existe); 6 h para auto-curar cuando Brawlify lo publique.
_neg: dict[int, float] = {}
_NEG_TTL = 6 * 3600

# Caché POSITIVA en memoria: fallback si el disco no es escribible (evita re-descargar).
_mem: dict[int, bytes] = {}
_MEM_MAX = 500
_disk_ok = True


def _candidates(map_id: int) -> list[str]:
    return [
        f"https://cdn.brawlify.com/maps/regular/{map_id}.png",
        f"https://raw.githubusercontent.com/Brawlify/CDN/master/maps/regular/{map_id}.png",
    ]


def _path(map_id: int) -> str:
    return os.path.join(_CACHE_DIR, f"{map_id}.png")


def _try_write_disk(map_id: int, content: bytes) -> bool:
    """Cachea en disco. Devuelve True si lo consiguió. Si no es escribible, lo avisa UNA vez."""
    global _disk_ok
    if not _disk_ok:
        return False
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        tmp = _path(map_id) + f".{os.getpid()}.tmp"   # tmp único: evita choques entre hilos
        with open(tmp, "wb") as f:
            f.write(content)
        os.replace(tmp, _path(map_id))
        return True
    except OSError as e:  # noqa: BLE001
        _disk_ok = False
        print(f"[map-image] aviso: caché en disco no disponible ({e}); uso memoria y sirvo igualmente.")
        return False


def _mem_put(map_id: int, content: bytes) -> None:
    if len(_mem) >= _MEM_MAX:
        _mem.pop(next(iter(_mem)), None)
    _mem[map_id] = content


def get_map_image(map_id: int):
    """Devuelve `('path', ruta)` o `('bytes', datos)` del PNG del mapa, o `None` si no hay imagen
    en ninguna fuente. Pensada para ejecutarse en un hilo (I/O síncrona)."""
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
    if _neg.get(map_id, 0) > time.time():   # 404 definitivo reciente: no reintentamos aún
        return None

    saw_404 = saw_error = False
    last = None
    with _sem:                              # limita la concurrencia upstream
        for url in _candidates(map_id):
            try:
                r = _client.get(url)
            except Exception as e:          # noqa: BLE001 (red/timeout transitorio)
                saw_error = True; last = f"{type(e).__name__} @ {url.split('/')[2]}"
                continue
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and r.content:
                if not _try_write_disk(map_id, r.content):
                    _mem_put(map_id, r.content)
                return ("bytes", r.content)
            if r.status_code == 404:
                saw_404 = True
            else:
                saw_error = True
            last = f"HTTP {r.status_code} @ {url.split('/')[2]}"

    # Solo marcamos "no existe" (y bloqueamos 6 h) si TODAS las fuentes dieron 404 SIN errores de
    # red. Ante cualquier fallo transitorio NO poisoneamos: se reintenta en la próxima carga.
    if saw_404 and not saw_error:
        _neg[map_id] = time.time() + _NEG_TTL
        print(f"[map-image] {map_id}: sin render en ninguna fuente (404).")
    else:
        print(f"[map-image] {map_id}: fallo transitorio, se reintentará ({last}).")
    return None
