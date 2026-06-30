"""Buffs y nerfs por brawler, a partir de fuentes EN VIVO (no del conocimiento del modelo,
que está desfasado). La spider (`app/spider.py`) reúne el texto; aquí la IA lo estructura.

- VIGENTES: de las NOTAS OFICIALES de Supercell (autoridad).
- ANUNCIADOS: de YouTube de creadores (Spiuk, Godeik, Soba...) y redes -> cada uno con estado
  "announced" (solo publicado, puede variar) o "confirmed" (con fecha, va en la próxima).

NUNCA bloquea: sirve memoria/disco al instante y refresca en segundo plano (la red va en un
hilo). Los datos quedan FIJOS hasta que cambia la FIRMA de las fuentes (nueva nota o vídeo
nuevo relevante): así no se gasta IA ni se pierde estabilidad. Si la red falla del todo, cae
al conocimiento del modelo para no quedar vacío.
"""
import os
import json
import time
import asyncio
from datetime import datetime, timezone

from . import db
from . import spider
from .tierlist import _norm, _catalog_names   # normalización + nombres del catálogo

_STORE = os.path.join(os.path.dirname(__file__), "data", "buffs.json")
_TTL = 24 * 3600
_cache = {"data": None, "at": 0.0}
_refreshing = {"on": False}
KINDS = {"buff", "nerf", "rework"}
TARGETS = {"attack", "super", "gadget", "starpower", "hypercharge", "stats"}
STATUS = {"announced", "confirmed"}
_MAX = 24


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------- extracción con IA -----------------------------
async def _ai_json(system: str, user: str, max_tokens: int) -> dict | None:
    from . import coach
    if not coach.configured():
        return None
    try:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=coach.API_KEY)
        msg = await client.messages.create(
            model=coach.MODEL, max_tokens=max_tokens,
            system=system, messages=[{"role": "user", "content": user}])
        try:
            db.log_ai_usage("buffs", msg.usage.input_tokens, msg.usage.output_tokens)
        except Exception:  # noqa: BLE001
            pass
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
        i, j = text.find("{"), text.rfind("}")
        return json.loads(text[i:j + 1])
    except Exception as e:  # noqa: BLE001
        print(f"[buffs ai] {e}")
        return None


async def _extract_live(notes: list, news: list) -> dict | None:
    """Una sola llamada: extrae current (de las notas oficiales) y upcoming (de las noticias)."""
    blocks = []
    for n in notes:
        blocks.append(f"=== NOTAS OFICIALES — {n['url']} ===\n{n['text']}")
    for n in news:
        blocks.append(f"=== {n['source']} — {n['url']} ===\n{n['text']}")
    if not blocks:
        return None
    body = "\n\n".join(blocks)[:28000]
    return await _ai_json(
        ("Extraes cambios de balance de Brawl Stars SOLO del texto dado (no de memoria). Las "
         "'NOTAS OFICIALES' son la autoridad de lo ya aplicado; las secciones de YouTube/redes "
         "son lo anunciado/previsto."),
        (body + "\n\n---\nDevuelve, SOLO del texto dado:\n"
         "- current: cambios de balance YA APLICADOS (SOLO de las NOTAS OFICIALES).\n"
         "- upcoming: cambios de balance ANUNCIADOS o previstos (de YouTube/redes), sin aplicar.\n"
         "  current/upcoming: brawler, kind (buff|nerf|rework), target (attack|super|gadget|"
         "starpower|hypercharge|stats), note (breve, español), date. En upcoming añade status "
         "(\"confirmed\" con fecha si va en la próxima, o \"announced\" si solo se menciona).\n"
         "- brawlers: PRÓXIMOS brawlers por salir (name=nombre, note=rol/fecha/habilidades).\n"
         "- modes: nuevos modos de juego o eventos (name, note=en qué consisten).\n"
         "- other: otros cambios y ajustes del juego (name=título corto, note=detalle).\n"
         "Ignora promos, sorteos y enlaces. Responde SOLO JSON: {\"current\":[...],\"upcoming\":"
         "[...],\"brawlers\":[...],\"modes\":[...],\"other\":[...]}. Nombres de brawler en "
         "MAYÚSCULAS. Listas vacías si no hay nada de ese tipo."),
        3800)


async def _extract_from_knowledge() -> dict | None:
    """Fallback si la red falla del todo: conocimiento del modelo (puede estar desfasado)."""
    return await _ai_json(
        "Eres un analista del meta de Brawl Stars y conoces las notas de parche recientes.",
        ("Cambios de balance de Brawl Stars en dos grupos: current (vigentes ya en el juego) y "
         "upcoming (anunciados, aún sin aplicar). Para cada uno: brawler, kind (buff|nerf|rework), "
         "target (attack|super|gadget|starpower|hypercharge|stats), note (breve, español), date. "
         'En upcoming añade status (announced|confirmed). Responde SOLO JSON {"current":[...],'
         '"upcoming":[...]} con nombres de brawler en MAYÚSCULAS.'),
        2600)


# ----------------------------- normalización -----------------------------
def _clean(lst, names, upcoming: bool = False) -> list:
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
        item = {"brawler": canon, "kind": kind, "target": target,
                "note": str(e.get("note") or "")[:160], "date": str(e.get("date") or "")}
        if upcoming:
            st = str(e.get("status", "")).lower()
            item["status"] = st if st in STATUS else "announced"
        out.append(item)
    return out


def _dedup(lst) -> list:
    """Quita repeticiones exactas pero conserva cambios distintos del mismo brawler."""
    seen, out = set(), []
    for e in lst:
        k = (e["brawler"], e["target"], e["kind"], e["note"][:40].lower())
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out[:_MAX]


def _clean_items(lst) -> list:
    """Normaliza bloques de texto libre (próximos brawlers / modos / otros cambios) a {name,note}."""
    out = []
    for e in (lst or []):
        if isinstance(e, dict):
            name = str(e.get("name") or e.get("title") or e.get("brawler") or "").strip()
            note = str(e.get("note") or e.get("desc") or "").strip()
        else:
            name, note = str(e).strip(), ""
        if name:
            out.append({"name": name[:90], "note": note[:280]})
    return out[:14]


async def _compute(names: dict) -> dict | None:
    g = await asyncio.to_thread(spider.gather)          # red en un hilo (no bloquea el bucle)
    data = await _extract_live(g["notes"], g["news"])
    current = _clean((data or {}).get("current"), names)
    upcoming = _clean((data or {}).get("upcoming"), names, upcoming=True)
    brawlers = _clean_items((data or {}).get("brawlers"))
    modes = _clean_items((data or {}).get("modes"))
    other = _clean_items((data or {}).get("other"))
    if not current and not upcoming:                    # red caída -> conocimiento del modelo
        kn = await _extract_from_knowledge()
        if kn:
            current = _clean(kn.get("current"), names)
            upcoming = _clean(kn.get("upcoming"), names, upcoming=True)
    if not (current or upcoming or brawlers or modes or other):
        return None
    now = _now_iso()
    return {"current": _dedup(current), "upcoming": _dedup(upcoming),
            "brawlers": brawlers, "modes": modes, "other": other,
            "updated": now, "checked": now,
            "sources": [n["url"] for n in g["notes"]] + [n["url"] for n in g["news"]],
            "source_sig": g["signature"]}


# ----------------------------- almacenamiento -----------------------------
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
    """Antigüedad desde la última REVISIÓN (no desde el último cambio): con las fuentes sin
    novedades, no se recalcula en bucle, solo se revisa la firma cada _TTL."""
    try:
        up = (data or {}).get("checked") or (data or {}).get("updated")
        return (time.time() - datetime.fromisoformat(up).timestamp()) if up else 1e12
    except Exception:  # noqa: BLE001
        return 1e12


async def _refresh() -> None:
    try:
        names = await _catalog_names()
        sig = await asyncio.to_thread(spider.signature)     # firma barata (notas + RSS)
        stored = _load_store()
        # FIJO hasta que cambie la firma de fuentes: ni se gasta IA ni descargas pesadas.
        if stored and stored.get("source_sig") == sig and (stored.get("current") or stored.get("upcoming")):
            stored["checked"] = _now_iso()
            _save_store(stored)
            _cache["data"], _cache["at"] = stored, time.time()
            return
        data = await _compute(names)
        if data:
            _save_store(data)
            _cache["data"], _cache["at"] = data, time.time()
    except Exception as e:  # noqa: BLE001
        print(f"[buffs refresh] {e}")
    finally:
        _refreshing["on"] = False


def _schedule_refresh() -> None:
    if _refreshing["on"]:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _refreshing["on"] = True
    loop.create_task(_refresh())


async def get_buffs() -> dict:
    """{current:[...], upcoming:[...], updated, ...}. NUNCA bloquea: sirve memoria/disco al
    instante y, si está caducado o ausente, refresca con la spider + IA en segundo plano."""
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
    """{NOMBRE: {kind,target,note,date}} con el primer cambio VIGENTE de cada brawler (para los
    badges en las tarjetas/ficha y para las recomendaciones). Síncrono, sin IA."""
    d = _cache["data"] or _load_store() or {}
    m = {}
    for e in (d.get("current") or []):
        m.setdefault(e["brawler"], e)
    return m
