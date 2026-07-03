"""Fase 5 — Detección automática de resultados desde los battle logs amistosos.

Idea: se trae el battlelog (últimas 25 batallas) de cada participante, se filtran
las AMISTOSAS y se cruzan con las partidas pendientes del evento. Una partida se
resuelve si aparece una amistosa con esos jugadores en bandos opuestos, dentro del
rango de fechas del evento y (si se conoce) en el modo de la ronda.

El resultado es solo una propuesta: el organizador puede editarlo o borrarlo.
"""

from __future__ import annotations

import re
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


def _norm_map(s: str) -> str:
    """Normaliza un nombre de mapa (quita todo lo no alfanumérico) para casar el mapa del
    torneo con el de la partida, tolerando diferencias de espacios/apóstrofes/mayúsculas."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def mode_code(es_name: str):
    """Nombre castellano del modo -> código del battlelog ('gemgrab', etc.)."""
    return bs_maps.MODE_CODE.get((es_name or "").strip())


def _sides(battle: dict):
    """[[tags lado0],[tags lado1]] para batallas de 2 bandos (3v3, duelos). None si no."""
    teams = battle.get("teams")
    if isinstance(teams, list) and len(teams) == 2 and all(isinstance(t, list) for t in teams):
        return [[normalize_tag(p.get("tag")) for p in side if p.get("tag")] for side in teams]
    # Duelos (1 vs 1): la API lo da como lista PLANA de 2 jugadores, sin 'teams'.
    players = battle.get("players")
    if isinstance(players, list) and len(players) == 2 and all(isinstance(p, dict) for p in players):
        sides = [[normalize_tag(p.get("tag"))] if p.get("tag") else [] for p in players]
        return sides if all(sides) else None
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


def match_individual(pool, a_tag, b_tag, code, start, end, best_of=1, map_name=None):
    a, b = normalize_tag(a_tag), normalize_tag(b_tag)
    want_map = _norm_map(map_name) if map_name else None
    found = []
    for batt in pool:
        if not _in_window(batt["time"], start, end):
            continue
        sa, sb = _side_of(batt["sides"], a), _side_of(batt["sides"], b)
        if sa is None or sb is None or sa == sb:  # ambos, en bandos opuestos
            continue
        if code and batt["mode"] and batt["mode"] != code:  # modo (si se conoce)
            continue
        if want_map and _norm_map(batt.get("map")) != want_map:  # MAPA exacto del torneo
            continue
        w = "draw" if batt["winner_side"] is None else ("a" if batt["winner_side"] == sa else "b")
        found.append((batt, w))
    return _finalize(found, best_of)


# ===========================================================================
# Cruce por REGISTROS (nuestra BD primero, API si falta): dos jugadores que tienen
# una amistosa a la MISMA hora (battle_time, precisión de segundo => es la MISMA
# partida) con resultados OPUESTOS son ese enfrentamiento, aunque el modo del battlelog
# no coincida con el de la ronda. Es el método fiable cuando el registro de un jugador
# no incluye el tag del rival (p. ej. Duelos).
# ===========================================================================

def to_bs_time(dt):
    """datetime UTC -> formato battle_time ('YYYYMMDDTHHMMSS.000Z')."""
    return dt.strftime("%Y%m%dT%H%M%S.000Z") if dt else None


def _result_win(r):
    """'victory' -> True, 'defeat' -> False, resto (empate/desconocido) -> None."""
    r = (r or "").lower()
    return True if r == "victory" else (False if r == "defeat" else None)


def friendly_records_from_battlelog(items, since=None, until=None) -> list:
    """Registros {time, mode, map, result} de las AMISTOSAS de un battlelog crudo (para los
    jugadores que aún no están en nuestra BD). `since`/`until` en formato battle_time."""
    out = []
    for it in items or []:
        b = it.get("battle") or {}
        if _norm_mode(b.get("type")) != "friendly":
            continue
        t = it.get("battleTime")
        if not t or (since and t < since) or (until and t > until):
            continue
        ev = it.get("event") or {}
        out.append({"time": t, "mode": _norm_mode(b.get("mode") or ev.get("mode")),
                    "map": (ev.get("map") or "").strip(), "result": (b.get("result") or "").lower()})
    return out


def _finalize_records(found, best_of):
    if not found:
        return None
    found.sort(key=lambda x: x[0])
    if best_of <= 1:
        t, w, mp = found[-1]
        return {"winner": w, "score_a": 1 if w == "a" else 0, "score_b": 1 if w == "b" else 0,
                "evidence": t, "map": mp, "n": 1}
    wa = sum(1 for _, w, _ in found if w == "a")
    wb = sum(1 for _, w, _ in found if w == "b")
    winner = "a" if wa > wb else ("b" if wb > wa else "draw")
    return {"winner": winner, "score_a": wa, "score_b": wb,
            "evidence": found[-1][0], "map": found[-1][2], "n": len(found)}


def match_records(a_rec, b_rec, best_of=1, code=None, map_name=None) -> dict | None:
    """1v1: A y B con una amistosa a la MISMA hora (=> la MISMA partida) sirve para ENCONTRARLA,
    pero solo se valida si además coincide el MODO y el MAPA de la ronda y están en bandos
    opuestos. La ventana de fechas ya se aplicó al leer los registros. Modo o mapa distintos NO
    cuentan (evita falsos positivos). Devuelve el mapa REAL de la partida (para anotarlo)."""
    wm = _norm_map(map_name) if map_name else None
    b_by_time = {}
    for x in b_rec:
        b_by_time.setdefault(x["time"], x)   # una por hora basta (los segundos son únicos por partida)
    found = []
    for a in a_rec:
        b = b_by_time.get(a["time"])
        if not b:
            continue
        amap = _norm_map(a.get("map"))
        if amap != _norm_map(b.get("map")):
            continue                          # misma hora pero distinto mapa: no es la misma partida
        if wm and amap != wm:
            continue                          # debe ser en el MAPA de la ronda
        if code and _norm_mode(a.get("mode")) != code:
            continue                          # debe ser en el MODO de la ronda
        wa, wb = _result_win(a["result"]), _result_win(b["result"])
        if wa is True and wb is False:
            w = "a"
        elif wa is False and wb is True:
            w = "b"
        elif wa is None or wb is None:
            w = "draw"
        else:
            continue                          # ambos ganan o ambos pierden: incoherente
        found.append((a["time"], w, a.get("map") or b.get("map")))
    return _finalize_records(found, best_of)


def match_records_teams(rec_by_tag, roster_a, roster_b, best_of=1, code=None, map_name=None) -> dict | None:
    """Por equipos: en una misma hora, TODOS los del róster A ganan y TODOS los del róster B
    pierden (o al revés), con el modo y el mapa de la ronda. Si falta un jugador o hay cambio de
    equipo (alguien no está en su bando con el resultado esperado) NO cuenta."""
    ra = [normalize_tag(t) for t in (roster_a or []) if t]
    rb = [normalize_tag(t) for t in (roster_b or []) if t]
    if not ra or not rb:
        return None
    wm = _norm_map(map_name) if map_name else None
    times = {}
    for tag in set(ra) | set(rb):
        for x in rec_by_tag.get(tag, []):
            slot = times.setdefault(x["time"], {"w": {}, "maps": set(), "modes": set()})
            slot["w"][tag] = _result_win(x["result"])
            if x.get("map"):
                slot["maps"].add(_norm_map(x["map"]))
            if x.get("mode"):
                slot["modes"].add(_norm_mode(x["mode"]))
    found = []
    for t, slot in times.items():
        if len(slot["maps"]) > 1:             # distintos mapas a la misma hora: no es una partida
            continue
        mp = next(iter(slot["maps"]), None)
        if wm and mp != wm:
            continue                          # debe ser en el MAPA de la ronda
        if code and code not in slot["modes"]:
            continue                          # debe ser en el MODO de la ronda

        def side_all(roster, want_win):       # TODOS los del róster presentes y con ese resultado
            return roster and all(slot["w"].get(tg) is want_win for tg in roster)
        if side_all(ra, True) and side_all(rb, False):
            found.append((t, "a", mp))
        elif side_all(rb, True) and side_all(ra, False):
            found.append((t, "b", mp))
    return _finalize_records(found, best_of)


def match_teams(pool, roster_a, roster_b, code, start, end, best_of=1, map_name=None):
    ra = set(normalize_tag(t) for t in (roster_a or []) if t)
    rb = set(normalize_tag(t) for t in (roster_b or []) if t)
    if not ra or not rb:
        return None
    want_map = _norm_map(map_name) if map_name else None
    found = []
    for batt in pool:
        if not _in_window(batt["time"], start, end):
            continue
        if code and batt["mode"] and batt["mode"] != code:
            continue
        if want_map and _norm_map(batt.get("map")) != want_map:  # MAPA exacto del torneo
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
