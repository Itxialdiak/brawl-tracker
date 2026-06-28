"""Tier Lists del meta de Brawl Stars.

- COMUNITARIA: se genera con los datos de TODOS los jugadores de BrawlSensei
  (rendimiento + uso) y los clasifica en S/A/B/C/D/F. NUNCA debe estar vacía: si hoy
  no hay datos suficientes se muestra la última guardada (de un día anterior) y, si no
  hubiera ninguna, una de referencia. Se persiste en disco y se cachea en memoria.
- GLOBAL: el meta según fuentes externas (scraping). Llega aparte.
"""
import os
import json
import time
from datetime import datetime, timezone

from . import db

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
           if (b.get("games") or 0) >= _MIN_GAMES and b.get("winrate") is not None]
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


def _global_pending() -> dict:
    return {"kind": "global", "tiers": {t: [] for t in TIERS},
            "note": "La Tier List Global (meta externo, por scraping de fuentes) llegará en breve."}


def get(kind: str) -> dict:
    kind = kind if kind in ("community", "global") else "community"
    now = time.time()
    c = _cache.get(kind)
    if c and now - c[0] < _TTL:
        return c[1]
    data = _community() if kind == "community" else _global_pending()
    _cache[kind] = (now, data)
    return data
