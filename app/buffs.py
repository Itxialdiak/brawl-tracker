"""Buffs y nerfs recientes por brawler, sacados de las notas de parche.

Mismo enfoque robusto y NO bloqueante que la tier list global: lo genera el modelo a
partir de las notas de parche más recientes, se canonicalizan los nombres contra el
catálogo, se cachea en memoria, se persiste en disco y se refresca en segundo plano (la
petición nunca espera a la IA). Cada entrada: {kind: buff|nerf|rework, note, date}.
"""
import os
import json
import time
from datetime import datetime, timezone

from . import db
from .tierlist import _norm, _catalog_names   # reutiliza la normalización y los nombres del catálogo

_STORE = os.path.join(os.path.dirname(__file__), "data", "buffs.json")
_TTL = 24 * 3600
_cache = {"data": None, "at": 0.0}
_refreshing = {"on": False}
KINDS = {"buff", "nerf", "rework"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _compute(names: dict) -> dict | None:
    from . import coach
    if not coach.configured():
        return None
    try:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=coach.API_KEY)
        msg = await client.messages.create(
            model=coach.MODEL, max_tokens=2200,
            system=("Eres un analista del meta de Brawl Stars. Conoces las notas de parche "
                    "(balance changes) más recientes del juego al detalle."),
            messages=[{"role": "user", "content": (
                "Lista los cambios de balance (buffs, nerfs y reworks) más RECIENTES de Brawl "
                "Stars brawler por brawler, según las últimas notas de parche (último mes o dos). "
                "Responde SOLO con un JSON, sin texto adicional, con la forma: "
                '{"NOMBRE":{"kind":"buff|nerf|rework","note":"resumen muy breve del cambio en '
                'español","date":"AAAA-MM o número de versión"}}. El nombre del brawler en '
                "MAYÚSCULAS. Incluye solo brawlers con cambios recientes reales.")}],
        )
        try:
            db.log_ai_usage("buffs", msg.usage.input_tokens, msg.usage.output_tokens)
        except Exception:  # noqa: BLE001
            pass
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
        i, j = text.find("{"), text.rfind("}")
        raw = json.loads(text[i:j + 1])
        out = {}
        for nm, info in raw.items():
            if not isinstance(info, dict):
                continue
            canon = names.get(_norm(nm)) if names else str(nm).upper()
            kind = str(info.get("kind", "")).lower()
            if not canon or kind not in KINDS:
                continue
            out[canon] = {"kind": kind, "note": str(info.get("note") or "")[:160],
                          "date": str(info.get("date") or "")}
        if out:
            return {"changes": out, "updated": _now_iso(),
                    "note": "Cambios de balance recientes según las notas de parche."}
    except Exception as e:  # noqa: BLE001
        print(f"[buffs] {e}")
    return None


def _load_store() -> dict | None:
    try:
        with open(_STORE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _save_store(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_STORE), exist_ok=True)
        with open(_STORE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def _age(data: dict) -> float:
    try:
        up = (data or {}).get("updated")
        return (time.time() - datetime.fromisoformat(up).timestamp()) if up else 1e12
    except Exception:  # noqa: BLE001
        return 1e12


async def _refresh() -> None:
    try:
        data = await _compute(await _catalog_names())
        if data:
            _save_store(data)
            _cache["data"], _cache["at"] = data, time.time()
    except Exception as e:  # noqa: BLE001
        print(f"[buffs refresh] {e}")
    finally:
        _refreshing["on"] = False


def _schedule_refresh() -> None:
    import asyncio
    if _refreshing["on"]:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _refreshing["on"] = True
    loop.create_task(_refresh())


async def get_buffs() -> dict:
    """Mapa de cambios recientes por brawler. NUNCA bloquea: sirve memoria/disco al
    instante y, si está caducado o ausente, refresca con IA en segundo plano."""
    now = time.time()
    if _cache["data"] is not None and now - _cache["at"] < 600:
        return _cache["data"]
    stored = _load_store()
    if stored and _age(stored) < _TTL:
        _cache["data"], _cache["at"] = stored, now
        return stored
    data = stored or {"changes": {}, "updated": None,
                      "note": "Recopilando los cambios de parche recientes…"}
    _cache["data"], _cache["at"] = data, now
    _schedule_refresh()
    return data


def changes_map() -> dict:
    """Versión SÍNCRONA (para cálculos como las recomendaciones): {NOMBRE: {kind,note,date}}
    de lo que haya en memoria o disco, sin disparar la IA."""
    d = _cache["data"] or _load_store() or {}
    return d.get("changes") or {}
