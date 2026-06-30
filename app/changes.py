"""Histórico de cambios de balance por brawler (de las notas oficiales / changelog).

Para CADA actualización del índice de release-notes, la IA extrae UNA vez los cambios de
balance por brawler y se cachea en disco por slug: las notas viejas no cambian, así que nunca
se reprocesan ni se vuelve a gastar IA. La ficha de un brawler muestra todos sus
buffs/nerfs/reworks a lo largo del tiempo (más reciente primero).

No bloquea: `schedule_build()` lanza la construcción en segundo plano; `history_for()` es
síncrono y sirve lo que haya cacheado (vacío hasta que termine la primera construcción).
"""
import os
import json
import asyncio

from . import spider
from . import buffs
from .tierlist import _norm, _catalog_names

_STORE = os.path.join(os.path.dirname(__file__), "data", "changelog_history.json")
_MONTH_NUM = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
              "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
              "diciembre": 12}
_cache = {"data": None}
_lock = asyncio.Lock()
_building = {"on": False}


def _load() -> dict:
    if _cache["data"] is None:
        try:
            with open(_STORE, encoding="utf-8") as f:
                _cache["data"] = json.load(f)
        except Exception:  # noqa: BLE001
            _cache["data"] = {}
    return _cache["data"]


def _save(d: dict) -> None:
    _cache["data"] = d
    try:
        os.makedirs(os.path.dirname(_STORE), exist_ok=True)
        with open(_STORE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


async def _extract(text: str) -> dict | None:
    return await buffs._ai_json(
        ("Extraes cambios de balance de Brawl Stars SOLO del texto de una nota de actualización "
         "oficial (no de memoria)."),
        (text[:15000] + "\n\n---\nDevuelve SOLO los cambios de balance de ESTA nota, por brawler: "
         "lista 'changes' con brawler, kind (buff|nerf|rework), target (attack|super|gadget|"
         "starpower|hypercharge|stats), note (breve, en español). Ignora brawlers nuevos, modos, "
         "mapas, promos y enlaces. Responde SOLO JSON {\"changes\":[...]} con nombres de brawler "
         "en MAYÚSCULAS. Lista vacía si la nota no trae cambios de balance."),
        3000)


def _clean(lst, names) -> list:
    out = []
    for e in (lst or []):
        if not isinstance(e, dict):
            continue
        canon = names.get(_norm(e.get("brawler", ""))) if names else str(e.get("brawler", "")).upper()
        kind = str(e.get("kind", "")).lower()
        if not canon or kind not in buffs.KINDS:
            continue
        target = str(e.get("target", "")).lower()
        if target not in buffs.TARGETS:
            target = "stats"
        out.append({"brawler": canon, "kind": kind, "target": target,
                    "note": str(e.get("note") or "")[:160]})
    return out


def _date_key(date: str) -> tuple:
    """'Junio 2026' -> (2026, 6) para ordenar; lo no reconocido va al final."""
    parts = str(date or "").lower().split()
    if len(parts) == 2 and parts[0] in _MONTH_NUM and parts[1].isdigit():
        return (int(parts[1]), _MONTH_NUM[parts[0]])
    return (0, 0)


async def ensure_built() -> dict:
    """Procesa (una vez por slug) cada actualización del índice y devuelve el store completo.
    La primera vez gasta IA por cada nota; después solo carga del disco."""
    async with _lock:
        store = _load()
        try:
            idx = await asyncio.to_thread(spider.release_index)
        except Exception:  # noqa: BLE001
            idx = []
        names = await _catalog_names()
        changed = False
        for u in idx:
            slug = u.get("slug")
            if not slug or slug in store:
                continue
            text = await asyncio.to_thread(spider.update_text, slug)
            if not text:
                continue
            data = await _extract(text)
            store[slug] = {"title": u.get("title"), "date": u.get("date"),
                           "key": _date_key(u.get("date")),
                           "changes": _clean((data or {}).get("changes"), names)}
            changed = True
        if changed:
            _save(store)
        return store


async def _build_task() -> None:
    try:
        await ensure_built()
    except Exception as e:  # noqa: BLE001
        print(f"[changes build] {e}")
    finally:
        _building["on"] = False


def schedule_build() -> None:
    """Lanza la construcción en segundo plano si hay notas sin procesar (no bloquea)."""
    if _building["on"]:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _building["on"] = True
    loop.create_task(_build_task())


def history_for(name: str) -> list:
    """[{title, date, kind, target, note}] de un brawler, más reciente primero. Síncrono."""
    target = _norm(name)
    store = _load()
    out = []
    for u in store.values():
        key = tuple(u.get("key") or _date_key(u.get("date")))
        for c in u.get("changes", []):
            if _norm(c["brawler"]) == target:
                out.append({"title": u.get("title"), "date": u.get("date"), "_k": key,
                            "kind": c["kind"], "target": c["target"], "note": c["note"]})
    out.sort(key=lambda x: x["_k"], reverse=True)
    for e in out:
        e.pop("_k", None)
    return out
