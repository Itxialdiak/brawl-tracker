"""
Catálogo persistente de imágenes a cuerpo entero de skins, indexado por id de skin
de Supercell (estable). Se rellena bajo demanda (al ver una skin en una ficha o en
el podio) y se completa periódicamente con las skins que cualquier jugador tenga
equipadas (es decir, con el tiempo, todas las del juego que alguien usa).

Fuente: wiki EN de Brawl Stars, cuyos nombres de fichero ("Shelly Skin-Witch.png")
casan con los nombres en inglés que da la API —la wiki ES los traduce y no casan—.

data/skins.json = { "<skin_id>": {"image": url|null, "brawler": ..., "skin": ...} }
Las entradas con image=null se reintentan en cada refresco (por si la wiki la añade
o el nombre de fichero cambia).
"""
from __future__ import annotations

import asyncio
import json
import os
import re

import httpx

from .wiki import UA

EN_API = "https://brawlstars.fandom.com/api.php"
_PATH = os.path.join(os.path.dirname(__file__), "data", "skins.json")
_cache: dict = {"data": None, "mtime": None}
_lock = asyncio.Lock()


def _load() -> dict:
    try:
        mtime = os.path.getmtime(_PATH)
    except OSError:
        return _cache["data"] if _cache["data"] is not None else {}
    if _cache["data"] is None or mtime != _cache["mtime"]:
        try:
            with open(_PATH, encoding="utf-8") as f:
                _cache["data"] = json.load(f)
            _cache["mtime"] = mtime
        except Exception:  # noqa: BLE001
            _cache["data"] = _cache["data"] or {}
    return _cache["data"]


def _save(data: dict) -> None:
    try:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=0)
        _cache["data"] = data
        _cache["mtime"] = os.path.getmtime(_PATH)
    except Exception as e:  # noqa: BLE001
        print(f"[skins] no se pudo guardar {_PATH}: {e}")


def get_image(skin_id) -> str | None:
    """URL cacheada de la skin (o None si no está aún o no se pudo resolver)."""
    if not skin_id:
        return None
    return (_load().get(str(skin_id)) or {}).get("image")


def _is_default(brawler: str, skin_name: str) -> bool:
    sn = (skin_name or "").upper().strip()
    return not sn or sn == (brawler or "").upper() or sn.endswith("DEFAULT")


def _candidates(brawler: str, skin_name: str) -> list:
    """Convenciones de nombre de fichero en la wiki EN (la que casa con la API)."""
    sk = " ".join((skin_name or "").split())
    rest = re.sub(rf"\b{re.escape(brawler)}\b", "", sk, flags=re.I).strip(" :-")
    out = []
    if rest:
        rt = rest.title()
        out += [f"{brawler} Skin-{rt}.png", f"{brawler}_Skin-{rt}.png",
                f"{brawler} {rt}.png", f"{rt} {brawler}.png"]
    out += [f"{brawler} Skin-{sk.title()}.png", f"{sk.title()}.png"]
    return out


async def _fetch_image_url(client: httpx.AsyncClient, filename: str, width: int = 500) -> str | None:
    try:
        r = await client.get(EN_API, params={"action": "query", "titles": "File:" + filename,
                                              "prop": "imageinfo", "iiprop": "url",
                                              "iiurlwidth": str(width), "format": "json", "redirects": "1"})
        for p in ((r.json().get("query") or {}).get("pages") or {}).values():
            if "missing" in p:
                continue
            ii = (p.get("imageinfo") or [{}])[0]
            return ii.get("thumburl") or ii.get("url")
    except Exception:  # noqa: BLE001
        return None
    return None


async def _resolve(client: httpx.AsyncClient, brawler: str, skin_name: str) -> str | None:
    for cand in _candidates(brawler, skin_name):
        url = await _fetch_image_url(client, cand)
        if url:
            return url
    return None


async def resolve_and_cache(skin_id, brawler: str, skin_name: str) -> str | None:
    """Imagen de la skin: de la caché si está; si no, la resuelve en la wiki EN y la
    cachea (incl. fallos como null, que se reintentan en el refresco periódico)."""
    if not skin_id or _is_default(brawler, skin_name):
        return None
    key = str(skin_id)
    cached = _load().get(key)
    if cached and cached.get("image"):
        return cached["image"]
    url = None
    try:
        async with httpx.AsyncClient(headers=UA, timeout=20, follow_redirects=True) as c:
            url = await _resolve(c, (brawler or "").title(), skin_name)
    except Exception:  # noqa: BLE001
        url = None
    async with _lock:
        data = dict(_load())
        data[key] = {"image": url, "brawler": brawler, "skin": skin_name}
        _save(data)
    return url


async def refresh_missing(skins_list: list, limit: int = 60) -> dict:
    """skins_list: [(skin_id, brawler, skin_name)]. Resuelve y cachea las que falten
    o sigan sin imagen. `limit` por pasada para no saturar la wiki (el resto cae en
    la siguiente pasada o al verlas en la web)."""
    data = _load()
    pending = []
    for sid, brawler, sname in skins_list:
        if not sid or _is_default(brawler, sname):
            continue
        e = data.get(str(sid))
        if e is None or not e.get("image"):
            pending.append((sid, brawler, sname))
    results, done = {}, 0
    try:
        async with httpx.AsyncClient(headers=UA, timeout=20, follow_redirects=True) as c:
            for sid, brawler, sname in pending[:limit]:
                url = await _resolve(c, (brawler or "").title(), sname)
                results[str(sid)] = {"image": url, "brawler": brawler, "skin": sname}
                if url:
                    done += 1
    finally:
        if results:
            async with _lock:
                merged = dict(_load())
                merged.update(results)
                _save(merged)
    return {"checked": len(results), "resolved": done, "pending": max(0, len(pending) - limit)}
