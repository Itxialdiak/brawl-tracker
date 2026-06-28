"""Tier Lists del meta de Brawl Stars.

- COMUNITARIA: se genera con los datos de TODOS los jugadores de BrawlSensei
  (rendimiento + uso) y los clasifica en S/A/B/C/D/F. NUNCA debe estar vacía: si hoy
  no hay datos suficientes se muestra la última guardada (de un día anterior) y, si no
  hubiera ninguna, una de referencia. Se persiste en disco y se cachea en memoria.
- GLOBAL: el meta según fuentes externas (scraping). Llega aparte.
"""
import os
import re
import json
import time
import unicodedata
from datetime import datetime, timezone

from . import db, brawler_extra


def _norm(s: str) -> str:
    """Normaliza un nombre de brawler para comparar (sin acentos, sin signos ni
    conectores Y/AND): 'Larry & Lawrie' y 'Larry Y Lawrie' -> 'LARRYLAWRIE'."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().upper()
    words = [w for w in re.findall(r"[A-Z0-9]+", s) if w not in ("Y", "AND")]
    return "".join(words)


async def _catalog_names() -> dict:
    """{nombre_normalizado -> NOMBRE CANÓNICO en mayúsculas} del catálogo."""
    try:
        from . import assets
        cat = await assets.get_brawler_catalog()
        out = {}
        for c in (cat.get("by_id") or {}).values():
            n = c.get("name")
            if n and not brawler_extra.is_temporary(c.get("id"), n):   # temporales fuera del meta
                out[_norm(n)] = n.upper()
        return out
    except Exception:  # noqa: BLE001
        return {}

TIERS = ["S", "A", "B", "C", "D", "F"]
# Reparto acumulado por tiers (S pocos, centro ancho, F pocos): % de brawlers hasta ese tier.
_CUTS = [("S", 0.07), ("A", 0.22), ("B", 0.44), ("C", 0.67), ("D", 0.87), ("F", 1.0)]
_MIN_GAMES = 2          # partidas mínimas (en toda la comunidad) para entrar en la lista
_MIN_BRAWLERS = 5       # por debajo de esto la muestra es demasiado pobre -> usar la guardada
_TTL = 6 * 3600         # recalcular como mucho cada 6 h
_STORE = os.path.join(os.path.dirname(__file__), "data", "tierlist_community.json")
_cache: dict = {}       # kind -> (timestamp, data)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _band(ranked: list) -> dict:
    """Asigna cada brawler (ya ordenado de mejor a peor) a un tier por percentil. Garantiza
    que el mejor caiga en S aunque la muestra sea pequeña."""
    n = len(ranked)
    tiers = {t: [] for t in TIERS}
    for i, b in enumerate(ranked):
        pct = (i + 1) / n
        tier = "S" if i == 0 else next(t for t, cut in _CUTS if pct <= cut)
        tiers[tier].append({"name": b["brawler"], "winrate": b["winrate"],
                            "pick_rate": b["pick_rate"], "games": b["games"], "score": b["score"]})
    return tiers


def _compute() -> dict | None:
    """Tier list desde los datos de la comunidad, o None si la muestra es muy pobre."""
    meta = db.community_meta()
    brs = [b for b in meta.get("brawlers", [])
           if (b.get("games") or 0) >= _MIN_GAMES and b.get("winrate") is not None
           and not brawler_extra.is_temporary(name=b.get("brawler"))]
    if len(brs) < _MIN_BRAWLERS:
        return None
    maxpick = max((b["pick_rate"] for b in brs), default=1) or 1
    for b in brs:
        usage = 100.0 * b["pick_rate"] / maxpick
        b["score"] = round(0.65 * b["winrate"] + 0.35 * usage, 2)
    brs.sort(key=lambda b: -b["score"])
    return {"kind": "community", "tiers": _band(brs), "sample": meta.get("total", 0),
            "updated": _now_iso(),
            "criteria": "Generada con los datos de la comunidad: 65% win rate medio + 35% uso."}


# --- Respaldo de referencia (solo si no hay datos NI lista guardada) ---
_BASELINE = {
    "S": ["SHELLY", "SURGE", "SPIKE", "KENJI", "MELODIE", "JUJU"],
    "A": ["COLT", "EDGAR", "CROW", "LEON", "GENE", "TARA", "SANDY", "CORDELIUS", "ANGELO"],
    "B": ["NITA", "BULL", "BROCK", "DYNAMIKE", "BO", "BARLEY", "POCO", "RICO", "MORTIS", "STU", "MAX", "GRAY"],
    "C": ["TICK", "EMZ", "8-BIT", "PIPER", "PAM", "FRANK", "BIBI", "JACKY", "PENNY", "CARL", "ROSA", "BEA", "NANI", "JESSIE"],
    "D": ["DARRYL", "EL PRIMO", "SPROUT", "GALE", "COLETTE", "BELLE", "SQUEAK", "GROM", "BUZZ", "GRIFF", "ASH", "LOU"],
    "F": ["MEG", "BYRON", "MR. P", "GUS", "FANG", "EVE", "JANET", "OTIS", "SAM", "BONNIE", "CHESTER", "AMBER"],
}


def _baseline() -> dict:
    return {"kind": "community", "sample": 0, "updated": None,
            "tiers": {t: [{"name": n, "winrate": None, "pick_rate": None, "games": 0} for n in _BASELINE[t]]
                      for t in TIERS},
            "note": "Tier list de referencia mientras se acumulan datos de la comunidad."}


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


def _community() -> dict:
    fresh = _compute()
    if fresh:
        _save_store(fresh)        # guarda la última buena para días sin datos
        return fresh
    return _load_store() or _baseline()   # la de ayer, o la de referencia: NUNCA vacía


# --- Global: consenso del meta externo. Lo genera el modelo analizando las tier lists y
# fuentes fiables más recientes (refresco diario, persistido). Sin clave de IA usa una de
# referencia para que tampoco quede vacía. ---

_GLOBAL_STORE = os.path.join(os.path.dirname(__file__), "data", "tierlist_global.json")
_GLOBAL_TTL = 24 * 3600


async def _global_compute(names: dict) -> dict | None:
    from . import coach
    if not coach.configured():
        return None
    try:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=coach.API_KEY)
        msg = await client.messages.create(
            model=coach.MODEL, max_tokens=1500,
            system=("Eres un analista del meta de Brawl Stars. Conoces el consenso de las tier "
                    "lists y fuentes fiables más recientes de la comunidad competitiva."),
            messages=[{"role": "user", "content": (
                "Genera la tier list GLOBAL actual del meta de Brawl Stars (3v3 ranked, nivel "
                "competitivo), promediando el consenso de las fuentes fiables más recientes. "
                "Clasifica los brawlers relevantes en S/A/B/C/D/F. Responde SOLO con un JSON: "
                '{"S":["NOMBRE",...],"A":[...],"B":[...],"C":[...],"D":[...],"F":[...]} '
                "con los nombres de brawler en MAYÚSCULAS y sin texto adicional.")}],
        )
        try:
            db.log_ai_usage("tierlist_global", msg.usage.input_tokens, msg.usage.output_tokens)
        except Exception:  # noqa: BLE001
            pass
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
        i, j = text.find("{"), text.rfind("}")
        raw = json.loads(text[i:j + 1])
        tiers = {}
        for t in TIERS:
            seen, lst = set(), []
            for n in (raw.get(t) or []):
                canon = names.get(_norm(n)) if names else str(n).upper()
                if canon and canon not in seen:   # solo brawlers reales del catálogo (evita letras rotas)
                    seen.add(canon)
                    lst.append({"name": canon, "winrate": None, "pick_rate": None, "games": 0})
            tiers[t] = lst
        if any(tiers[t] for t in TIERS):
            return {"kind": "global", "tiers": tiers, "updated": _now_iso(),
                    "criteria": "Consenso del meta global según fuentes fiables (se actualiza a diario)."}
    except Exception as e:  # noqa: BLE001
        print(f"[tierlist global] {e}")
    return None


def _global_baseline() -> dict:
    b = _baseline()
    b["kind"] = "global"
    b["note"] = "Tier list global de referencia (se actualizará automáticamente con el meta)."
    return b


def _load_global_store() -> dict | None:
    try:
        with open(_GLOBAL_STORE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _save_global_store(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_GLOBAL_STORE), exist_ok=True)
        with open(_GLOBAL_STORE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def _age_seconds(data: dict) -> float:
    """Antigüedad (s) de una tier list guardada según su campo 'updated' (ISO)."""
    try:
        up = (data or {}).get("updated")
        return (time.time() - datetime.fromisoformat(up).timestamp()) if up else 1e12
    except Exception:  # noqa: BLE001
        return 1e12


_refreshing = {"global": False}


async def _refresh_global() -> None:
    """Regenera la tier list global con la IA y la guarda. Corre en segundo plano."""
    try:
        data = await _global_compute(await _catalog_names())
        if data:
            _save_global_store(data)
            _cache["global"] = (time.time(), data)
    except Exception as e:  # noqa: BLE001
        print(f"[tierlist global refresh] {e}")
    finally:
        _refreshing["global"] = False


def _schedule_global_refresh() -> None:
    """Lanza la regeneración en segundo plano si no hay otra en curso (no bloquea)."""
    import asyncio
    if _refreshing["global"]:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _refreshing["global"] = True
    loop.create_task(_refresh_global())


async def global_tierlist() -> dict:
    """Responde SIEMPRE rápido: usa la versión en memoria o la guardada en disco. Si está
    caducada (>24 h) o no existe, devuelve igualmente lo mejor disponible (o la de
    referencia) y dispara la regeneración con IA EN SEGUNDO PLANO, sin bloquear la petición."""
    now = time.time()
    c = _cache.get("global")
    if c and now - c[0] < 600:                  # cache de memoria (10 min) -> instantáneo
        return c[1]
    stored = _load_global_store()
    if stored and _age_seconds(stored) < _GLOBAL_TTL:
        _cache["global"] = (now, stored)        # guardada y fresca -> instantáneo, sin IA
        return stored
    data = stored or _global_baseline()         # caducada/ausente: responde ya y refresca detrás
    _cache["global"] = (now, data)
    _schedule_global_refresh()
    return data


def get(kind: str) -> dict:
    """Versión síncrona: la Comunitaria. La Global es async (usa global_tierlist())."""
    now = time.time()
    c = _cache.get("community")
    if c and now - c[0] < _TTL:
        return c[1]
    data = _community()
    _cache["community"] = (now, data)
    return data
