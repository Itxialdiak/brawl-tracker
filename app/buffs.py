"""Buffs y nerfs por brawler.

VIGENTES: se extraen de las NOTAS OFICIALES de Supercell (release notes). Se descubre la
actualización más reciente desde el índice del blog, se descarga la página, se limpia el
HTML y un modelo extrae los cambios estructurados USANDO SOLO ese texto (no de memoria,
que estaría desfasada). Los hot fixes se SUMAN a los cambios de la actualización (no la
sustituyen). Los cambios vigentes quedan FIJOS —no se recalculan ni se gasta IA— hasta que
se detecta una actualización nueva (cambia la firma de fuentes); solo se revisa el índice.

PRÓXIMOS (anunciados): cambios publicados pero aún sin aplicar. Cada uno lleva estado:
  - "announced": solo se ha publicado QUÉ cambia (todavía puede variar).
  - "confirmed": confirmado para la próxima actualización (lleva fecha).

NUNCA bloquea: sirve memoria/disco al instante y refresca en segundo plano. Si la descarga
de las notas falla, cae con elegancia al conocimiento del modelo para no quedarse vacío.

Configuración por entorno (todo opcional):
  BUFFS_INDEX_URL   índice de release notes a rastrear (por defecto, el oficial ES).
  BUFFS_NOTES_URL   fija una URL de notas concreta (desactiva el autodescubrimiento).
  BUFFS_HOTFIX_URLS URLs de hot fix a SUMAR a la actualización vigente (separadas por comas).
"""
import os
import re
import html
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
STATUS = {"announced", "confirmed"}
_MAX = 24

_INDEX_URL = os.environ.get(
    "BUFFS_INDEX_URL",
    "https://supercell.com/en/games/brawlstars/es/blog/release-notes/")
_NOTES_URL = os.environ.get("BUFFS_NOTES_URL", "").strip()
_HOTFIX_URLS = [u.strip() for u in os.environ.get("BUFFS_HOTFIX_URLS", "").split(",") if u.strip()]
_UA = {"User-Agent": "Mozilla/5.0 (compatible; BrawlSensei/1.0)"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------- descarga + limpieza -----------------------------
def _html_to_text(raw: str) -> str:
    """HTML -> texto plano legible (suficiente para que el modelo lea las notas)."""
    raw = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?is)</(p|div|li|h[1-6]|tr|section)>", "\n", raw)
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\r\f]+", " ", raw)
    raw = re.sub(r"\n[ \t]*\n+", "\n", raw)
    return raw.strip()


async def _fetch(url: str) -> str:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=_UA) as cli:
            r = await cli.get(url)
            r.raise_for_status()
            return r.text
    except Exception as e:  # noqa: BLE001
        print(f"[buffs fetch] {url}: {e}")
        return ""


async def _discover_notes_url() -> str:
    """URL de la actualización más reciente desde el índice. Respeta BUFFS_NOTES_URL si está
    fijada. Devuelve '' si no logra descubrir nada."""
    if _NOTES_URL:
        return _NOTES_URL
    raw = await _fetch(_INDEX_URL)
    if not raw:
        return ""
    links = re.findall(r'href="([^"#?]*release-notes/[^"#?]+)"', raw)
    seen, cand = set(), []
    for l in links:
        slug = l.rstrip("/").rsplit("/", 1)[-1]
        if not slug or slug == "release-notes" or l in seen:
            continue
        seen.add(l)
        cand.append(l if l.startswith("http") else "https://supercell.com" + l)
    return cand[0] if cand else ""   # el índice lista lo más reciente primero


def _date_hint(url: str) -> str:
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return slug.replace("-", " ").replace("notas de la actualizacion de", "").strip() or slug


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


async def _extract(text: str, date_hint: str) -> dict | None:
    """Extrae los cambios del TEXTO de unas notas oficiales (solo a partir del texto)."""
    if not text:
        return None
    return await _ai_json(
        ("Extraes cambios de balance de las notas OFICIALES de Brawl Stars. Usa SOLO la "
         "información del texto dado; no inventes ni añadas cambios de memoria."),
        ("Texto de las notas oficiales de Brawl Stars:\n\n" + text[:14000] +
         "\n\n---\nExtrae los cambios de balance por brawler distinguiendo:\n"
         "- current: cambios YA aplicados en esta actualización.\n"
         "- upcoming: cambios solo ANUNCIADOS o previstos para una versión futura.\n"
         "Para cada cambio: brawler, kind (buff|nerf|rework), target (attack|super|gadget|"
         "starpower|hypercharge|stats), note (resumen muy breve en español), date (versión/"
         f'fecha; usa "{date_hint}" si no hay otra). En upcoming añade status (announced|'
         'confirmed). Responde SOLO JSON: {"current":[...],"upcoming":[...]}. Nombres de '
         "brawler en MAYÚSCULAS. Listas vacías si no hay cambios de ese tipo."),
        3000)


async def _extract_from_knowledge() -> dict | None:
    """Fallback si no se pudo descargar/parsear ninguna nota: conocimiento del modelo (puede
    estar algo desfasado) para no dejar la sección vacía."""
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
    """Quita repeticiones exactas pero CONSERVA cambios distintos del mismo brawler (un hot fix
    sobre un brawler ya tocado se suma, no sustituye)."""
    seen, out = set(), []
    for e in lst:
        k = (e["brawler"], e["target"], e["kind"], e["note"][:40].lower())
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out[:_MAX]


def _sources_sig(sources: list) -> str:
    return "|".join(sources)


async def _compute(names: dict, sources: list) -> dict | None:
    """Descarga y parsea cada fuente (actualización + hot fixes) y combina los cambios."""
    current, upcoming = [], []
    for url in sources:
        raw = await _fetch(url)
        if not raw:
            continue
        data = await _extract(_html_to_text(raw), _date_hint(url))
        if not data:
            continue
        current += _clean(data.get("current"), names)
        upcoming += _clean(data.get("upcoming"), names, upcoming=True)
    if not current:                       # notas no descargables/parseables -> conocimiento
        kn = await _extract_from_knowledge()
        if kn:
            current = _clean(kn.get("current"), names)
            if not upcoming:
                upcoming = _clean(kn.get("upcoming"), names, upcoming=True)
    if not (current or upcoming):
        return None
    now = _now_iso()
    return {"current": _dedup(current), "upcoming": _dedup(upcoming),
            "updated": now, "checked": now,
            "sources": sources, "source_sig": _sources_sig(sources)}


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
    """Antigüedad desde la última REVISIÓN (no desde el último cambio): así, con las fuentes
    sin novedades, no se recalcula en bucle, solo se revisa el índice cada _TTL."""
    try:
        up = (data or {}).get("checked") or (data or {}).get("updated")
        return (time.time() - datetime.fromisoformat(up).timestamp()) if up else 1e12
    except Exception:  # noqa: BLE001
        return 1e12


async def _refresh() -> None:
    try:
        names = await _catalog_names()
        main = await _discover_notes_url()
        sources = ([main] if main else []) + _HOTFIX_URLS
        sig = _sources_sig(sources)
        stored = _load_store()
        # Vigentes FIJOS: si la firma de fuentes no cambió y ya hay datos, NO se recalcula
        # (ni se gasta IA); solo se marca que se revisó para no caducar en bucle.
        if stored and stored.get("source_sig") == sig and (stored.get("current") or stored.get("upcoming")):
            stored["checked"] = _now_iso()
            _save_store(stored)
            _cache["data"], _cache["at"] = stored, time.time()
            return
        data = await _compute(names, sources)
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
    """{current:[...], upcoming:[...], updated, ...}. NUNCA bloquea: sirve memoria/disco al
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
