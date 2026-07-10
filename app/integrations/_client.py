"""Cliente JSON común para integraciones externas.

Un solo cliente httpx reutilizable (pool keep-alive) + caché en memoria con TTL por URL. Cabeceras
de navegador porque algunas fuentes (p. ej. `api.brawlify.com`) devuelven 403 a peticiones "de bot"
o a IPs de datacenter. Toda llamada degrada con elegancia: ante cualquier fallo devuelve el último
valor cacheado (aunque esté caducado) o `None`, nunca lanza hacia el llamante.
"""

from __future__ import annotations

import time
import threading

import httpx

# Cabeceras de navegador: sube la probabilidad de pasar el escudo anti-bot de Cloudflare.
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"),
    "Accept": "application/json",
}

_client = httpx.Client(
    headers=_HEADERS, follow_redirects=True,
    timeout=httpx.Timeout(12.0, connect=5.0),
    limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
)
_sem = threading.Semaphore(6)

# Caché positiva en memoria: url -> (expira_en, payload). Guardamos también payloads caducados para
# poder servirlos como fallback si la fuente cae.
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def get_json(url: str, ttl: float = 900.0, force: bool = False):
    """GET JSON con caché de `ttl` segundos. Devuelve el objeto parseado o `None`.

    - Si hay valor fresco en caché y no `force`, lo devuelve sin red.
    - Ante error/timeout/403, devuelve el último valor cacheado (aunque esté caducado) o `None`.
    """
    now = time.time()
    with _lock:
        hit = _cache.get(url)
    if hit and not force and hit[0] > now:
        return hit[1]
    try:
        with _sem:
            r = _client.get(url)
        r.raise_for_status()
        data = r.json()
        with _lock:
            _cache[url] = (now + ttl, data)
        return data
    except Exception:  # noqa: BLE001 — degradación: nunca romper por una fuente externa
        return hit[1] if hit else None


def cached(url: str):
    """Último valor cacheado para una URL (sin tocar la red), o `None`."""
    with _lock:
        hit = _cache.get(url)
    return hit[1] if hit else None


def probe(url: str) -> dict:
    """Diagnóstico de egress: hace un GET REAL (sin caché) y reporta el resultado con detalle, sin
    lanzar. `{ok, status, error, keys}` — para el panel de admin que verifica si la fuente responde
    desde el propio servidor (evita tener que hacer curl a mano en el VPS)."""
    try:
        with _sem:
            r = _client.get(url)
        keys = None
        try:
            body = r.json()
            keys = list(body.keys())[:12] if isinstance(body, dict) else f"[{len(body)} items]"
        except Exception:  # noqa: BLE001
            pass
        return {"ok": r.status_code == 200, "status": r.status_code, "error": None, "keys": keys}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "error": f"{type(e).__name__}: {e}", "keys": None}
