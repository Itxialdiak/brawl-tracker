"""Fase 5 — Detección automática de resultados desde los battle logs amistosos.

Idea: se trae el battlelog (últimas 25 batallas) de cada participante, se filtran
las AMISTOSAS y se cruzan con las partidas pendientes del evento. Una partida se
resuelve si aparece una amistosa con esos jugadores en bandos opuestos, dentro del
rango de fechas del evento y (si se conoce) en el modo de la ronda.

El resultado es solo una propuesta: el organizador puede editarlo o borrarlo.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from . import bs_maps
from .db import normalize_tag


def parse_battle_time(bt: str):
    """'20250626T180000.000Z' -> datetime con zona UTC (o None)."""
    if not bt:
        return None
    for fmt in ("%Y%m%dT%H%M%S.%fZ", "%Y%m%dT%H%M%SZ"):
        try:
            return datetime.strptime(bt, fmt).replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            pass
    return None


def parse_event_date(s: str, end: bool = False):
    """date_start / date_end (ISO o 'YYYY-MM-DD') -> datetime UTC. `end` suma 1 día."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        pass
    try:
        d = datetime.strptime(str(s)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return d + timedelta(days=1) if end else d
    except Exception:  # noqa: BLE001
        return None


def _norm_mode(s: str) -> str:
    return (s or "").replace(" ", "").replace("-", "").replace("_", "").lower()


def mode_code(es_name: str):
    """Nombre castellano del modo -> código del battlelog ('gemgrab', etc.)."""
    return bs_maps.MODE_CODE.get((es_name or "").strip())


def _sides(battle: dict):
    """[[tags lado0],[tags lado1]] para batallas de 2 bandos (3v3, duelos). None si no."""
    teams = battle.get("teams")
    if isinstance(teams, list) and len(teams) == 2 and all(isinstance(t, list) for t in teams):
        return [[normalize_tag(p.get("tag")) for p in side if p.get("tag")] for side in teams]
    return None


def build_pool(battlelogs) -> list:
    """battlelogs: lista de (owner_tag, items). Devuelve amistosas normalizadas y deduplicadas."""
    pool = {}
    for owner, items in battlelogs:
        owner = normalize_tag(owner)
        for it in items or []:
            battle = it.get("battle") or {}
            if _norm_mode(battle.get("type")) != "friendly":  # solo amistosas
                continue
            sides = _sides(battle)
            if not sides:
                continue
            bt = it.get("battleTime")
            res = (battle.get("result") or "").lower()
            owner_side = next((i for i, s in enumerate(sides) if owner in s), None)
            winner_side = None
            if owner_side is not None and res in ("victory", "defeat"):
                winner_side = owner_side if res == "victory" else (1 - owner_side)
            ev = it.get("event") or {}
            entry = {
                "time": parse_battle_time(bt), "raw_time": bt, "sides": sides,
                "winner_side": winner_side,
                "mode": _norm_mode(battle.get("mode") or ev.get("mode")),
                "map": (ev.get("map") or "").strip(),
            }
            cur = pool.get(bt)
            if cur is None:
                pool[bt] = entry
            elif cur["winner_side"] is None and winner_side is not None:
                cur["winner_side"] = winner_side  # completa el ganador desde otra perspectiva
    return list(pool.values())


def _in_window(t, start, end) -> bool:
    if t is None:
        return True
    if start and t < start:
        return False
    if end and t > end:
        return False
    return True


def _side_of(sides, tag):
    tag = normalize_tag(tag)
    for i, s in enumerate(sides):
        if tag in s:
            return i
    return None


def _finalize(found, best_of):
    """found: lista de (batalla, 'a'/'b'/'draw'). Devuelve resultado propuesto."""
    if not found:
        return None
    found.sort(key=lambda x: x[0]["time"] or datetime.min.replace(tzinfo=timezone.utc))
    if best_of <= 1:
        batt, w = found[-1]  # la más reciente
        return {"winner": w, "score_a": 1 if w == "a" else 0, "score_b": 1 if w == "b" else 0,
                "evidence": batt["raw_time"], "map": batt["map"], "n": 1}
    wa = sum(1 for _, w in found if w == "a")
    wb = sum(1 for _, w in found if w == "b")
    winner = "a" if wa > wb else ("b" if wb > wa else "draw")
    return {"winner": winner, "score_a": wa, "score_b": wb,
            "evidence": found[-1][0]["raw_time"], "map": found[-1][0]["map"], "n": len(found)}


def match_individual(pool, a_tag, b_tag, code, start, end, best_of=1):
    a, b = normalize_tag(a_tag), normalize_tag(b_tag)
    found = []
    for batt in pool:
        if not _in_window(batt["time"], start, end):
            continue
        sa, sb = _side_of(batt["sides"], a), _side_of(batt["sides"], b)
        if sa is None or sb is None or sa == sb:  # ambos, en bandos opuestos
            continue
        if code and batt["mode"] and batt["mode"] != code:  # modo (si se conoce)
            continue
        w = "draw" if batt["winner_side"] is None else ("a" if batt["winner_side"] == sa else "b")
        found.append((batt, w))
    return _finalize(found, best_of)


def match_teams(pool, roster_a, roster_b, code, start, end, best_of=1):
    ra = set(normalize_tag(t) for t in (roster_a or []) if t)
    rb = set(normalize_tag(t) for t in (roster_b or []) if t)
    if not ra or not rb:
        return None
    found = []
    for batt in pool:
        if not _in_window(batt["time"], start, end):
            continue
        if code and batt["mode"] and batt["mode"] != code:
            continue
        s0, s1 = set(batt["sides"][0]), set(batt["sides"][1])
        if len(s0 & ra) >= 2 and len(s1 & rb) >= 2:
            a_side = 0
        elif len(s0 & rb) >= 2 and len(s1 & ra) >= 2:
            a_side = 1
        else:
            continue
        w = "draw" if batt["winner_side"] is None else ("a" if batt["winner_side"] == a_side else "b")
        found.append((batt, w))
    return _finalize(found, best_of)
