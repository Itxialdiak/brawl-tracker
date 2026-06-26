"""
Recursos visuales desde BrawlAPI / Brawlify (api.brawlapi.com).

Gratis y sin clave; solo pide cabecera User-Agent. Construimos tres mapas:
- brawlers: NOMBRE_MAYUS -> url del retrato
- modes:    schash_minus -> {icon, color}
- maps:     nombre_minus -> {id, image}

Se cachean en memoria y se refrescan cada cierto tiempo. Si la API falla,
devolvemos lo último que tengamos (o vacío) sin romper nada: el frontend
simplemente muestra los nombres sin imagen.
"""

from __future__ import annotations

import time
import httpx

BASE = "https://api.brawlapi.com/v1"
HEADERS = {"User-Agent": "BrawlTracker/1.0 (personal stats app)"}
CACHE_TTL = 60 * 60 * 12  # 12 horas

_cache = {"data": None, "at": 0.0}


def _extract_list(data):
    if isinstance(data, dict):
        return data.get("list") or data.get("items") or []
    return data if isinstance(data, list) else []


async def _fetch(client: httpx.AsyncClient, path: str):
    r = await client.get(f"{BASE}/{path}", headers=HEADERS, timeout=20)
    r.raise_for_status()
    return _extract_list(r.json())


def _build(brawlers, gamemodes, maps) -> dict:
    bmap = {}
    for b in brawlers:
        name, url = b.get("name"), b.get("imageUrl")
        if name and url:
            bmap[name.upper()] = url

    mmap = {}
    for g in gamemodes:
        url = g.get("imageUrl")
        if not url:
            continue
        info = {"icon": url, "color": g.get("color")}
        for k in (g.get("scHash"), g.get("hash"), g.get("name")):  # también por nombre
            if k:
                mmap[str(k).lower()] = info
    # Alias para showdown solo/dúo si la API solo trae "showdown".
    if "showdown" in mmap:
        for k in ("soloshowdown", "duoshowdown"):
            mmap.setdefault(k, mmap["showdown"])

    pmap = {}
    for m in maps:
        name, url = m.get("name"), m.get("imageUrl")
        if name and url:
            pmap[name.lower()] = {"id": m.get("id"), "image": url}

    return {"brawlers": bmap, "modes": mmap, "maps": pmap}


async def get_assets() -> dict:
    now = time.time()
    if _cache["data"] is not None and (now - _cache["at"]) < CACHE_TTL:
        return _cache["data"]
    try:
        async with httpx.AsyncClient() as client:
            brawlers = await _fetch(client, "brawlers")
            gamemodes = await _fetch(client, "gamemodes")
            maps = await _fetch(client, "maps")
        _cache["data"] = _build(brawlers, gamemodes, maps)
        _cache["at"] = now
    except Exception as e:  # noqa: BLE001
        print(f"[assets] no se pudieron cargar recursos de Brawlify: {e}")
        if _cache["data"] is None:
            _cache["data"] = {"brawlers": {}, "modes": {}, "maps": {}}
    return _cache["data"]
