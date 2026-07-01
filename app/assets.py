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

import os
import json
import time
import httpx

BASE = "https://api.brawlapi.com/v1"
HEADERS = {"User-Agent": "BrawlTracker/1.0 (personal stats app)"}
CACHE_TTL = 60 * 60 * 12  # 12 horas

_cache = {"data": None, "at": 0.0}
_MAPES_PATH = os.path.join(os.path.dirname(__file__), "data", "map_names_es.json")
_mapes_cache = {"data": None, "mtime": 0.0}


def _load_map_names_es() -> dict:
    """{nombre_mapa_EN_minúsculas: 'Nombre ES'} (de scrape_map_names.py). Cache por mtime."""
    try:
        mt = os.path.getmtime(_MAPES_PATH)
    except OSError:
        return _mapes_cache["data"] or {}
    if _mapes_cache["data"] is None or mt != _mapes_cache["mtime"]:
        try:
            with open(_MAPES_PATH, encoding="utf-8") as f:
                _mapes_cache["data"] = json.load(f)
            _mapes_cache["mtime"] = mt
        except Exception:  # noqa: BLE001
            _mapes_cache["data"] = _mapes_cache["data"] or {}
    return _mapes_cache["data"] or {}


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
    # "Siege" (nombre interno antiguo con el que llegan las partidas) = la nueva "Brawl
    # Arena" (Arena en español): reutiliza su icono oficial.
    if "brawl arena" in mmap:
        mmap.setdefault("siege", mmap["brawl arena"])

    pmap = {}
    maps_by_mode = {}
    for m in maps:
        name, url = m.get("name"), m.get("imageUrl")
        if name and url:
            pmap[name.lower()] = {"id": m.get("id"), "image": url}
        # Mapas JUGABLES EN AMISTOSO hoy = los NO deshabilitados, agrupados por modo.
        if name and not m.get("disabled"):
            gm = (m.get("gameMode") or {}).get("name")
            if gm:
                maps_by_mode.setdefault(gm.lower(), []).append(name)
    for gm in maps_by_mode:
        maps_by_mode[gm] = sorted(dict.fromkeys(maps_by_mode[gm]))  # dedup + orden alfabético

    return {"brawlers": bmap, "modes": mmap, "maps": pmap, "maps_by_mode": maps_by_mode}


async def get_assets() -> dict:
    now = time.time()
    if _cache["data"] is None or (now - _cache["at"]) >= CACHE_TTL:
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
                _cache["data"] = {"brawlers": {}, "modes": {}, "maps": {}, "maps_by_mode": {}}
    _cache["data"]["map_names_es"] = _load_map_names_es()  # nombres ES para mostrar (barato, por mtime)
    return _cache["data"]


# --- Catálogo de mapas por modo (para el Hub de Modos) -------------------------

_map_catalog_cache = {"data": None, "at": 0.0}


def norm_mode(s: str | None) -> str:
    """Clave de modo normalizada para casar el modo canónico de la BD ('gemGrab')
    con el de BrawlAPI ('Gem Grab'): minúsculas sin espacios/guiones."""
    return (s or "").lower().replace(" ", "").replace("-", "").replace("_", "")


async def get_map_catalog() -> dict:
    """Todos los mapas de Brawlify agrupados por modo (clave normalizada), cada uno
    con imagen, si está activo (no 'disabled') y el color oficial del modo. Es la
    fuente de 'todos los mapas del modo' del Hub. Cacheado 12 h; ante fallo
    devuelve lo último o vacío."""
    now = time.time()
    if _map_catalog_cache["data"] is not None and (now - _map_catalog_cache["at"]) < CACHE_TTL:
        return _map_catalog_cache["data"]
    try:
        async with httpx.AsyncClient() as client:
            maps = await _fetch(client, "maps")
    except Exception as e:  # noqa: BLE001
        print(f"[assets] no se pudo cargar el catálogo de mapas: {e}")
        return _map_catalog_cache["data"] or {"by_mode": {}, "by_name": {}}
    by_mode, by_name = {}, {}
    for m in maps:
        name = m.get("name")
        gm = m.get("gameMode") or {}
        mode_name = gm.get("name")
        if not name or not mode_name:
            continue
        entry = {"name": name, "image": m.get("imageUrl"),
                 "active": not m.get("disabled", False),
                 "mode": mode_name, "mode_color": gm.get("color"),
                 "last_active": m.get("lastActive") or 0}
        by_mode.setdefault(norm_mode(mode_name), []).append(entry)
        by_name[name.lower()] = entry
    # los más recientes primero dentro de cada modo
    for lst in by_mode.values():
        lst.sort(key=lambda e: (e["active"], e["last_active"]), reverse=True)
    _map_catalog_cache["data"] = {"by_mode": by_mode, "by_name": by_name}
    _map_catalog_cache["at"] = now
    return _map_catalog_cache["data"]


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
