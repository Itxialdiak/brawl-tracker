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


# --- Catálogo completo de brawlers (contenido para el apartado Brawlers) -------

_catalog_cache = {"data": None, "at": 0.0}
_EMPTY_CATALOG = {"by_id": {}, "totals": {"brawlers": 0, "star_powers": 0, "gadgets": 0}}


def _ability(x: dict) -> dict:
    """Star power o gadget -> {id, name, icon, description}."""
    return {"id": x.get("id"), "name": x.get("name"),
            "icon": x.get("imageUrl"), "description": x.get("description")}


async def get_brawler_catalog() -> dict:
    """Catálogo de brawlers de Brawlify indexado por id: descripción, rol, rareza,
    imagen a cuerpo entero (imageUrl2), retrato, y star powers/gadgets con sus
    iconos. Incluye `totals` (denominadores para los contadores y el rating).
    Cacheado como get_assets; ante fallo devuelve lo último o vacío."""
    now = time.time()
    if _catalog_cache["data"] is not None and (now - _catalog_cache["at"]) < CACHE_TTL:
        return _catalog_cache["data"]
    try:
        async with httpx.AsyncClient() as client:
            brawlers = await _fetch(client, "brawlers")
    except Exception as e:  # noqa: BLE001
        print(f"[assets] no se pudo cargar el catálogo de brawlers: {e}")
        return _catalog_cache["data"] or _EMPTY_CATALOG

    by_id, total_sp, total_gd = {}, 0, 0
    for b in brawlers:
        bid = b.get("id")
        if bid is None:
            continue
        sps = [_ability(x) for x in (b.get("starPowers") or [])]
        gds = [_ability(x) for x in (b.get("gadgets") or [])]
        total_sp += len(sps); total_gd += len(gds)
        rarity = b.get("rarity") or {}
        cls = b.get("class") or {}
        by_id[bid] = {
            "id": bid, "name": b.get("name"), "description": b.get("description"),
            "role": cls.get("name"),
            "rarity": {"name": rarity.get("name"), "color": rarity.get("color")},
            "image_full": b.get("imageUrl2"), "portrait": b.get("imageUrl"),
            "star_powers": sps, "gadgets": gds,
        }
    data = {"by_id": by_id,
            "totals": {"brawlers": len(by_id), "star_powers": total_sp, "gadgets": total_gd}}
    _catalog_cache["data"] = data
    _catalog_cache["at"] = now
    return data
