"""Recomendaciones de brawlers: cruzan TUS datos (uso y win rate por brawler) con la
posición de cada brawler en el meta (tier list + meta comunitario o global).

Cinco subsecciones, 5 brawlers cada una:
  1. in_form      — los dominas y además están fuertes en el meta -> aprovéchalos.
  2. dont_overuse — los dominas pero están en horas bajas -> no abuses, altérnalos.
  3. hidden_gems  — apenas los juegas y están en buen momento -> dales una oportunidad.
  4. against_meta — el meta dice que van flojos pero TÚ los haces rendir -> tu sello.
  5. to_max       — sin nivel máximo y muy bien valorados -> los que más conviene maxear.

Todo se respalda en datos: tier de cada brawler, tus partidas y tu win rate frente a la
media (comunitaria si la hay, o el win rate esperado por tier en la versión global).
"""
from . import brawler_extra

_TIER_RANK = {"S": 6, "A": 5, "B": 4, "C": 3, "D": 2, "F": 1}
# Win rate esperado por tier (para estimar el sobrerrendimiento en la versión global,
# donde no tenemos win rate por brawler, solo la tier).
_TIER_BASELINE = {"S": 56, "A": 53, "B": 51, "C": 49, "D": 47, "F": 44}
MIN_PLAY = 4        # partidas mínimas para considerar que "lo usas" (y fiar de tu win rate)
GOOD_META = 4       # rank >= B se considera buen momento
OVERPERF = 6        # % sobre la media para entrar en "a contracorriente"
N = 5               # brawlers por subsección


def _meta_map(tiers: dict, community: dict | None) -> dict:
    """NOMBRE_MAYUS -> {tier, rank, mwr}. mwr = win rate de la comunidad (o None)."""
    m = {}
    for t, lst in (tiers or {}).items():
        for b in (lst or []):
            nm = (b.get("name") or "").upper()
            if nm:
                m[nm] = {"tier": t, "rank": _TIER_RANK.get(t, 0), "mwr": None}
    for b in ((community or {}).get("brawlers") or []):
        nm = (b.get("brawler") or "").upper()
        if not nm:
            continue
        m.setdefault(nm, {"tier": None, "rank": 0, "mwr": None})["mwr"] = b.get("winrate")
    return m


def build(kind: str, catalog: dict, tl: dict, community: dict | None,
          collection: list, wr_rows: list) -> dict:
    tiers = (tl or {}).get("tiers") or {}
    meta = _meta_map(tiers, community)
    by_id = (catalog or {}).get("by_id") or {}
    coll = {c["brawler_id"]: c for c in (collection or [])}
    wr = {(r.get("label") or "").upper(): r for r in (wr_rows or [])}

    cands = []
    for bid, cat in by_id.items():
        name = cat.get("name")
        if not name or brawler_extra.is_temporary(bid, name):
            continue
        c = coll.get(bid)
        if not c:                       # solo brawlers que posees
            continue
        nm = name.upper()
        mm = meta.get(nm) or {"tier": None, "rank": 0, "mwr": None}
        w = wr.get(nm) or {}
        bat = w.get("total") or 0
        ywr = w.get("winrate") if bat >= MIN_PLAY else None
        base = mm["mwr"] if mm["mwr"] is not None else _TIER_BASELINE.get(mm["tier"], 50)
        over = round(ywr - base, 1) if ywr is not None else None
        cands.append({"id": bid, "name": name, "portrait": cat.get("portrait"),
                      "tier": mm["tier"], "rank": mm["rank"], "winrate": ywr,
                      "battles": bat, "power": c.get("power"), "over": over})

    def good_meta(x): return x["rank"] >= GOOD_META
    def bad_meta(x): return 0 < x["rank"] < GOOD_META
    def played(x): return x["battles"] >= MIN_PLAY
    def good_perf(x): return x["winrate"] is not None and x["winrate"] >= 50

    g1 = sorted([x for x in cands if good_meta(x) and played(x) and good_perf(x)],
                key=lambda x: (x["winrate"], x["rank"]), reverse=True)[:N]
    g4 = sorted([x for x in cands if bad_meta(x) and played(x)
                 and x["over"] is not None and x["over"] >= OVERPERF],
                key=lambda x: x["over"], reverse=True)[:N]
    used4 = {x["id"] for x in g4}
    g2 = sorted([x for x in cands if bad_meta(x) and played(x) and good_perf(x)
                 and x["id"] not in used4],
                key=lambda x: (x["battles"], -x["rank"]), reverse=True)[:N]
    g3 = sorted([x for x in cands if good_meta(x) and not played(x)],
                key=lambda x: (x["rank"], x["mwr"] if (x.get("mwr") is not None) else 0),
                reverse=True)[:N]
    g5 = sorted([x for x in cands if good_meta(x) and (x["power"] or 0) < 11],
                key=lambda x: (x["rank"], x["power"] or 0), reverse=True)[:N]

    def entry(x, note):
        return {"id": x["id"], "name": x["name"], "portrait": x["portrait"],
                "tier": x["tier"], "winrate": x["winrate"], "battles": x["battles"],
                "power": x["power"], "note": note}

    groups = [
        {"key": "in_form", "title": "En forma · aprovéchalos",
         "subtitle": "De los brawlers que dominas, los que además pegan fuerte en el meta ahora mismo.",
         "brawlers": [entry(x, f"{x['winrate']}% en {x['battles']} · tier {x['tier']}") for x in g1]},
        {"key": "dont_overuse", "title": "No abuses · mal momento",
         "subtitle": "Los manejas bien, pero están en horas bajas en el meta: altérnalos para no estancarte.",
         "brawlers": [entry(x, f"Lo usas mucho ({x['battles']}) · tier {x['tier']}") for x in g2]},
        {"key": "hidden_gems", "title": "Joyas olvidadas · dales una oportunidad",
         "subtitle": "Apenas los tocas y están en un gran momento: ideal para aprenderlos ahora.",
         "brawlers": [entry(x, f"Tier {x['tier']} y casi sin jugar") for x in g3]},
        {"key": "against_meta", "title": "A contracorriente · tu sello",
         "subtitle": "El meta dice que van flojos, pero tú les sacas un rendimiento por encima de lo esperado.",
         "brawlers": [entry(x, f"+{x['over']}% sobre lo esperado · tier {x['tier']}") for x in g4]},
        {"key": "to_max", "title": "Conviene maxear",
         "subtitle": "Sin nivel máximo y muy bien valorados en el meta: donde más rinde tu inversión.",
         "brawlers": [entry(x, f"Nivel {x['power']} · tier {x['tier']}") for x in g5]},
    ]
    return {"kind": kind, "groups": groups,
            "source": (tl or {}).get("note") or (tl or {}).get("criteria") or ""}
