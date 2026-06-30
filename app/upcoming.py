"""Brawlers PRÓXIMAMENTE (anunciados, aún no en el juego).

Dataset curado y editable (`data/upcoming_brawlers.json`): los brawlers anunciados que todavía
NO existen en la API/catálogo de Brawl Stars, así que se mantienen aparte. No cuentan para la
colección, los filtros, los cálculos ni el meta. Se rellena a mano (o cuando hay datos oficiales
fiables) con lo que se sabe; los campos sin confirmar quedan como "Por confirmar".
"""
import os
import json

_PATH = os.path.join(os.path.dirname(__file__), "data", "upcoming_brawlers.json")
_cache = {"data": None, "mtime": 0.0}


def list_all() -> list:
    """Lista normalizada de brawlers próximos. Cacheada por mtime del JSON (recarga si se edita)."""
    try:
        mtime = os.path.getmtime(_PATH)
        if _cache["data"] is not None and _cache["mtime"] == mtime:
            return _cache["data"]
        with open(_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        out = []
        for e in (raw or []):
            if not isinstance(e, dict) or not e.get("name"):
                continue
            out.append({
                "name": str(e["name"]),
                "rarity": e.get("rarity") or "Por confirmar",
                "role": e.get("role") or "Por confirmar",
                "release": e.get("release") or "Próximamente",
                "description": e.get("description") or "",
                "image": e.get("image") or None,
                "image_full": e.get("image_full") or e.get("image") or None,
                "abilities": [{"name": str(a.get("name", "")), "note": str(a.get("note", ""))}
                              for a in (e.get("abilities") or [])
                              if isinstance(a, dict) and a.get("name")],
                "source": e.get("source") or "",
            })
        _cache["data"], _cache["mtime"] = out, mtime
        return out
    except Exception:  # noqa: BLE001
        return _cache["data"] or []
