"""Novedades NO de balance de la última actualización (modos, eventos, otros).

Dataset curado a mano (`data/updates_extra.json`) porque la IA de `buffs.py` no extrae
fiablemente esta parte de las notas (NanoPowers, eventos, etc.). Editar al salir cada
actualización. Cacheado por mtime; el servidor solo LEE.
"""
import os
import json

_PATH = os.path.join(os.path.dirname(__file__), "data", "updates_extra.json")
_cache = {"data": None, "mtime": 0.0}


def get() -> dict:
    """{update, modes:[{name,note}], other:[{name,note}]}. Degrada a vacío si falta o falla."""
    try:
        mtime = os.path.getmtime(_PATH)
        if _cache["data"] is not None and _cache["mtime"] == mtime:
            return _cache["data"]
        with open(_PATH, encoding="utf-8") as f:
            raw = json.load(f)

        def _items(lst):
            return [{"name": str(e.get("name", "")), "note": str(e.get("note", ""))}
                    for e in (lst or []) if isinstance(e, dict) and e.get("name")]

        out = {"update": raw.get("update") or "",
               "modes": _items(raw.get("modes")), "other": _items(raw.get("other"))}
        _cache["data"], _cache["mtime"] = out, mtime
        return out
    except Exception:  # noqa: BLE001
        return _cache["data"] or {"update": "", "modes": [], "other": []}
