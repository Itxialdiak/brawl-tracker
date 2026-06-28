"""Buffs y nerfs por brawler, sacados de las notas de parche.

Igual de robusto y NO bloqueante que la tier list global: lo genera el modelo a partir
de las notas de parche, se canonicalizan los nombres contra el catálogo, se cachea, se
persiste y se refresca en segundo plano. Se distingue entre cambios VIGENTES (ya en el
juego) y PRÓXIMOS confirmados. Cada entrada lleva además QUÉ se toca (target): ataque,
súper, gadget, estelar, hipercarga o características.
"""
import os
import json
import time
from datetime import datetime, timezone

from . import db
from .tierlist import _norm, _catalog_names   # reutiliza normalización y nombres del catálogo

_STORE = os.path.join(os.path.dirname(__file__), "data", "buffs.json")
_TTL = 24 * 3600
_cache = {"data": None, "at": 0.0}
_refreshing = {"on": False}
KINDS = {"buff", "nerf", "rework"}
TARGETS = {"attack", "super", "gadget", "starpower", "hypercharge", "stats"}
_MAX = 18


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(lst, names) -> list:
    out = []
    for e in (lst or []):
        if not isinstance(e, dict):
            continue
        canon = names.get(_norm(e.get("brawler", ""))) if names else str(e.get("brawler", "")).upper()
        kind = str(e.get("kind", "")).lower()
        if not canon or kind not in KINDS:
            continue
        target = str(e.get("target", "")).lower()
        if target not in TARGETS:
            target = "stats"
        out.append({"brawler": canon, "kind": kind, "target": target,
                    "note": str(e.get("note") or "")[:160], "date": str(e.get("date") or "")})
    return out[:_MAX]


async def _compute(names: dict) -> dict | None:
    from . import coach
    if not coach.configured():
        return None
    try:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=coach.API_KEY)
        msg = await client.messages.create(
            model=coach.MODEL, max_tokens=2600,
            system=("Eres un analista del meta de Brawl Stars. Conoces al detalle las notas de "
                    "parche (balance changes) recientes y las anunciadas para próximas versiones."),
            messages=[{"role": "user", "content": (
                "Dame los cambios de balance de Brawl Stars en DOS grupos: los VIGENTES ahora "
                "mismo (ya aplicados en el juego en las últimas semanas) y los PRÓXIMOS "
                "confirmados (anunciados oficialmente pero aún sin aplicar). Para cada cambio "
                "indica el brawler, si es buff/nerf/rework, QUÉ se toca (attack, super, gadget, "
                "starpower, hypercharge o stats) y un resumen muy breve en español. Responde SOLO "
                'con un JSON sin texto adicional: {"current":[{"brawler":"NOMBRE","kind":"buff|'
                'nerf|rework","target":"attack|super|gadget|starpower|hypercharge|stats","note":'
                '"...","date":"versión o fecha"}],"upcoming":[...]}. Nombres en MAYÚSCULAS. Si no '
                'hay próximos confirmados, devuelve "upcoming":[].')}],
        )
        try:
            db.log_ai_usage("buffs", msg.usage.input_tokens, msg.usage.output_tokens)
        except Exception:  # noqa: BLE001
            pass
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
        i, j = text.find("{"), text.rfind("}")
        raw = json.loads(text[i:j + 1])
        current, upcoming = _clean(raw.get("current"), names), _clean(raw.get("upcoming"), names)
        if current or upcoming:
            return {"current": current, "upcoming": upcoming, "updated": _now_iso()}
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
    """{current:[...], upcoming:[...], updated}. NUNCA bloquea: sirve memoria/disco al
    instante y, si está caducado o ausente, refresca con IA en segundo plano."""
    now = time.time()
    if _cache["data"] is not None and now - _cache["at"] < 600:
        return _cache["data"]
    stored = _load_store()
    if stored and _age(stored) < _TTL:
        _cache["data"], _cache["at"] = stored, now
        return stored
    data = stored or {"current": [], "upcoming": [], "updated": None,
                      "note": "Recopilando los cambios de balance recientes…"}
    _cache["data"], _cache["at"] = data, now
    _schedule_refresh()
    return data


def changes_map() -> dict:
    """{NOMBRE: {kind,target,note,date}} con el primer cambio VIGENTE de cada brawler (para
    los badges en las tarjetas/ficha y para las recomendaciones). Síncrono, sin IA."""
    d = _cache["data"] or _load_store() or {}
    m = {}
    for e in (d.get("current") or []):
        m.setdefault(e["brawler"], e)
    return m
