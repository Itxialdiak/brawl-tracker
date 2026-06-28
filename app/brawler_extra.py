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


# --- Brawlers temporales (colaboraciones limitadas) ---------------------------
# No forman parte de la colección: no cuentan en el total, no se filtran, no se usan en
# cálculos ni en las tier lists. Solo se muestran aparte, como recuerdo de eventos pasados.
TEMPORARY_IDS = {16000088}                 # Buzz Lightyear
TEMPORARY_NAMES = {"BUZZ LIGHTYEAR"}


def is_temporary(brawler_id=None, name=None) -> bool:
    """¿Es un brawler temporal (colab limitada) que no entra en la colección?"""
    try:
        if brawler_id is not None and int(brawler_id) in TEMPORARY_IDS:
            return True
    except (TypeError, ValueError):
        pass
    return bool(name) and str(name).upper() in TEMPORARY_NAMES


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


# --- Índice de roles por brawler (data/roles_index.json) ---------------------
# { NOMBRE_MAYUS: [rol_primario, rol_secundario] }. Generado por gen_roles_index.py.

_RINDEX_PATH = os.path.join(os.path.dirname(__file__), "data", "roles_index.json")
_rindex_cache: dict = {"data": None, "mtime": None}


def _load_roles_index() -> dict:
    try:
        mtime = os.path.getmtime(_RINDEX_PATH)
    except OSError:
        return {}
    if _rindex_cache["data"] is None or mtime != _rindex_cache["mtime"]:
        try:
            with open(_RINDEX_PATH, encoding="utf-8") as f:
                _rindex_cache["data"] = json.load(f)
            _rindex_cache["mtime"] = mtime
        except Exception:  # noqa: BLE001
            return _rindex_cache["data"] or {}
    return _rindex_cache["data"] or {}


def roles_of(brawler_name: str) -> list:
    """Roles (primario + secundario) de un brawler por su nombre (case-insensitive)."""
    return list(_load_roles_index().get((brawler_name or "").upper()) or [])


def brawlers_with_role(role: str) -> list:
    """Nombres (MAYÚS) de brawlers con ese rol como primario o secundario."""
    return [name for name, rs in _load_roles_index().items() if role in rs]


def all_roles() -> list:
    """Todos los roles distintos del índice (orden alfabético)."""
    return sorted({r for rs in _load_roles_index().values() for r in rs})
