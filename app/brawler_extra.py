"""
Dataset curado a mano con lo que NINGUNA API expone: hipercargas (nombre + icono
+ qué brawler la tiene), estadísticas por nivel y builds recomendadas por la
comunidad. Se rellena poco a poco en `data/brawler_extra.json`; la app degrada con
elegancia si falta una entrada (muestra solo lo que sí da la API).

El JSON se recarga solo cuando cambia en disco (mira su mtime), así puedes editarlo
sin reiniciar el servidor.

Formato de data/brawler_extra.json (claves = id de brawler como string):
    {
      "_meta": {"note": "...", "hypercharges_in_game": null},
      "16000000": {
        "hypercharge": {"name": "...", "icon": "https://.../x.png"},
        "stats_by_level": {"health": [11 valores 1..11], "damage": [...], "speed": "..."},
        "builds": [{"name": "...", "star_power_id": 0, "gadget_id": 0, "gears": [], "source": "url"}]
      }
    }
"""

from __future__ import annotations

import json
import os

_PATH = os.path.join(os.path.dirname(__file__), "data", "brawler_extra.json")
_cache: dict = {"data": None, "mtime": None}


def _load() -> dict:
    try:
        mtime = os.path.getmtime(_PATH)
    except OSError:
        return {}
    if _cache["data"] is None or mtime != _cache["mtime"]:
        try:
            with open(_PATH, encoding="utf-8") as f:
                _cache["data"] = json.load(f)
            _cache["mtime"] = mtime
        except Exception as e:  # noqa: BLE001
            print(f"[brawler_extra] no se pudo leer {_PATH}: {e}")
            return _cache["data"] or {}
    return _cache["data"] or {}


def get(brawler_id) -> dict:
    """Datos curados de un brawler (o {} si no hay entrada)."""
    return _load().get(str(brawler_id)) or {}


def meta() -> dict:
    return _load().get("_meta") or {}


def hypercharge_ids() -> set:
    """Ids de brawler que tienen una entrada de hipercarga en el dataset."""
    return {int(k) for k, v in _load().items()
            if k != "_meta" and isinstance(v, dict) and v.get("hypercharge")}


def hypercharge_total() -> int:
    """Total de hipercargas en el juego. Usa el número explícito de `_meta`
    (`hypercharges_in_game`) si está puesto; si no, cuenta las registradas."""
    explicit = meta().get("hypercharges_in_game")
    return explicit if isinstance(explicit, int) else len(hypercharge_ids())


# --- Roles secundarios curados (data/roles_secondary.json) -------------------

_ROLES_PATH = os.path.join(os.path.dirname(__file__), "data", "roles_secondary.json")
_roles_cache: dict = {"data": None, "mtime": None}


def _load_roles() -> dict:
    try:
        mtime = os.path.getmtime(_ROLES_PATH)
    except OSError:
        return {}
    if _roles_cache["data"] is None or mtime != _roles_cache["mtime"]:
        try:
            with open(_ROLES_PATH, encoding="utf-8") as f:
                _roles_cache["data"] = json.load(f)
            _roles_cache["mtime"] = mtime
        except Exception:  # noqa: BLE001
            return _roles_cache["data"] or {}
    return _roles_cache["data"] or {}


def role_secondary(name: str) -> str | None:
    return (_load_roles().get("secondary") or {}).get(name)


def role_primary_fallback(name: str) -> str | None:
    """Rol primario para brawlers que la wiki aún no clasifica (Clase=None)."""
    return (_load_roles().get("primary_fallback") or {}).get(name)
