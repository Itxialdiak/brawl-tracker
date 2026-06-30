"""Histórico de cambios de balance por brawler (lectura).

La fuente es el dataset COMPLETO `data/brawler_changes.json`, generado offline por
`scrape_changes.py` desde la wiki de Fandom (EN) y traducido al español (ver
`app/wiki_changes.py`). Aquí solo se LEE y se sirve por brawler. Síncrono y sin red.
"""
import os
import json

from .tierlist import _norm

_STORE = os.path.join(os.path.dirname(__file__), "data", "brawler_changes.json")
_cache = {"data": None, "mtime": 0.0}


def _load() -> dict:
    """Carga el dataset cacheado por mtime (se regenera con scrape_changes.py)."""
    try:
        mt = os.path.getmtime(_STORE)
    except OSError:
        return _cache["data"] or {}
    if _cache["data"] is None or mt != _cache["mtime"]:
        try:
            with open(_STORE, encoding="utf-8") as f:
                _cache["data"] = json.load(f)
            _cache["mtime"] = mt
        except Exception:  # noqa: BLE001
            _cache["data"] = _cache["data"] or {}
    return _cache["data"]


def history_for(name: str) -> list:
    """[{date, iso, kind, note}] de un brawler, más reciente primero. Síncrono."""
    target = _norm(name)
    store = _load()
    for key, rec in store.items():
        if _norm(key) == target:
            ents = [{"date": e.get("date"), "iso": e.get("iso"), "kind": e.get("kind"),
                     "note": e.get("note") or e.get("note_en") or ""}
                    for e in rec.get("entries", [])]
            ents.sort(key=lambda e: e.get("iso") or "", reverse=True)
            return ents
    return []


def summary_for(name: str) -> dict:
    """Conteo {buff, nerf, rework, neutral, total} para resúmenes en la ficha."""
    out = {"buff": 0, "nerf": 0, "rework": 0, "neutral": 0, "total": 0}
    for e in history_for(name):
        out[e["kind"]] = out.get(e["kind"], 0) + 1
        out["total"] += 1
    return out


def timeline() -> list:
    """Historial GENERAL: agrupa TODOS los cambios por fecha (de todos los brawlers), más
    reciente primero. [{iso, date, buff, nerf, rework, neutral, changes:[{brawler,kind,note}]}].
    Es el changelog completo del juego, derivado del dataset de la wiki (ya traducido)."""
    store = _load()
    by = {}
    for key, rec in store.items():
        for e in rec.get("entries", []):
            iso = e.get("iso") or "0000-00-00"
            g = by.get(iso)
            if g is None:
                g = by[iso] = {"iso": iso, "date": e.get("date"), "changes": [],
                               "buff": 0, "nerf": 0, "rework": 0, "neutral": 0}
            kind = e.get("kind") or "neutral"
            g["changes"].append({"brawler": key, "kind": kind, "note": e.get("note") or ""})
            g[kind] = g.get(kind, 0) + 1
    out = sorted(by.values(), key=lambda x: x["iso"], reverse=True)
    for g in out:                                   # cambios de cada día: buffs, luego nerfs, luego resto
        order = {"buff": 0, "nerf": 1, "rework": 2, "neutral": 3}
        g["changes"].sort(key=lambda c: (order.get(c["kind"], 4), c["brawler"]))
    return out


def latest_changes() -> list:
    """Cambios de la actualización MÁS RECIENTE (para 'vigentes' en Actualizaciones)."""
    tl = timeline()
    return tl[0]["changes"] if tl else []
