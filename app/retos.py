"""Motor de Retos: el modelo de CONDICIONES medibles y el cálculo de progreso.

Regla de oro: un reto solo mide cosas verificables con datos que la app ya recoge
(las partidas de `battles`): victorias, partidas, win rate, copas (trophy_change),
brawlers distintos, rachas y estrella del partido. NUNCA datos manuales (no "haz X
de daño con Y").

El progreso se mide sobre las partidas del jugador DESDE que se apuntó (joined_at) y,
si el reto tiene límite de tiempo, hasta su vencimiento. Las consultas viven en
db.reto_metric(); aquí va la lógica (validación, descripción, progreso, dificultad)."""
import itertools
import math
from datetime import datetime, timezone, timedelta

from . import db

# Catálogo de métricas medibles. Es la fuente de verdad para el formulario de crear
# reto y para el "manual de qué es medible".
#   scope     -> admite acotar por brawler / modo / mapa / rol.
#   min_games -> objetivo de mínimo de partidas (solo tiene sentido en win rate).
# Cada métrica declara:
#   scope     -> admite acotar por brawler / modo / mapa / rol.
#   min_games -> pide (o exige) un mínimo de partidas para que el dato sea fiable.
#   per_ok    -> admite el modificador "por cada" (exigir el objetivo por CADA brawler/
#                modo/mapa seleccionado). Las métricas de "distintos" no lo admiten.
#   cap       -> es un TOPE (menos es mejor): el objetivo es un máximo a no superar.
METRICS = {
    "wins":              {"label": "Ganar partidas",                "unit": "victorias", "scope": True,  "min_games": False, "per_ok": True,
                          "help": "Cuenta tus victorias. Puedes acotarlo a un brawler, modo, mapa o rol."},
    "games":             {"label": "Jugar partidas",                "unit": "partidas",  "scope": True,  "min_games": False, "per_ok": True,
                          "help": "Cuenta las partidas jugadas (ganes o pierdas). Con 'por cada' logras 'juega X con cada brawler/modo'."},
    "winrate":           {"label": "Alcanzar un win rate",          "unit": "%",         "scope": True,  "min_games": True,  "per_ok": True,
                          "help": "Mantén un % de victorias sobre un mínimo de partidas, para que sea fiable."},
    "performance":       {"label": "Alcanzar un rendimiento",       "unit": "pts",       "scope": True,  "min_games": True,  "per_ok": True,
                          "help": "Alcanza cierto rendimiento AJUSTADO (win rate encogido por nº de partidas + dificultad por copas) sobre un mínimo de partidas."},
    "win_streak":        {"label": "Racha de victorias",            "unit": "seguidas",  "scope": True,  "min_games": False, "per_ok": True,
                          "help": "Encadena victorias seguidas (una derrota corta la racha)."},
    "distinct_brawlers": {"label": "Ganar con brawlers distintos",  "unit": "brawlers",  "scope": True,  "min_games": False, "per_ok": False,
                          "help": "Gana al menos una vez con cierto número de brawlers diferentes."},
    "distinct_played":   {"label": "Jugar con brawlers distintos",  "unit": "brawlers",  "scope": True,  "min_games": False, "per_ok": False,
                          "help": "Juega al menos una partida con cierto número de brawlers diferentes."},
    "trophies":          {"label": "Sumar copas",                   "unit": "copas",     "scope": True,  "min_games": False, "per_ok": True,
                          "help": "Suma copas netas (lo que ganas menos lo que pierdes) durante el reto."},
    "star_player":       {"label": "Ser jugador estelar",           "unit": "veces",     "scope": True,  "min_games": False, "per_ok": True,
                          "help": "Te nombran jugador estelar el número de veces indicado."},
    "active_days":       {"label": "Días jugados (constancia)",     "unit": "días",      "scope": True,  "min_games": False, "per_ok": False,
                          "help": "Juega al menos una partida en ese número de días distintos (constancia)."},
    "max_losses":        {"label": "No pasar de X derrotas",        "unit": "derrotas máx.", "scope": True, "min_games": True, "per_ok": True, "cap": True,
                          "help": "Reto de disciplina: sobre un mínimo de partidas, no acumules más de X derrotas. Va bien con un plazo."},
}

# Dimensiones válidas del modificador "por cada".
PER_DIMS = ("brawler", "mode", "map")

MAX_CONDITIONS = 8


def validate_conditions(conditions):
    """Devuelve (ok, error|None). Cada condición: {metric, target, scope?, min_games?, per?}."""
    if not isinstance(conditions, list) or not conditions:
        return False, "El reto necesita al menos una condición medible."
    if len(conditions) > MAX_CONDITIONS:
        return False, f"Demasiadas condiciones (máximo {MAX_CONDITIONS})."
    for c in conditions:
        if not isinstance(c, dict) or c.get("metric") not in METRICS:
            return False, "Hay una condición con una métrica no válida."
        meta = METRICS[c["metric"]]
        try:
            if float(c.get("target")) <= 0:
                return False, "El objetivo de cada condición debe ser mayor que cero."
        except (TypeError, ValueError):
            return False, "Cada condición necesita un objetivo numérico."
        if meta.get("cap") and not int(c.get("min_games") or 0):
            return False, "El tope de derrotas necesita un mínimo de partidas (sobre cuántas se cuenta)."
        per = c.get("per")
        if per:
            dims = [per] if isinstance(per, str) else per
            if not isinstance(dims, list) or not dims or not all(d in PER_DIMS for d in dims) or not meta.get("per_ok"):
                return False, "Esa métrica no admite el modo «por cada»."
            scope = c.get("scope") or {}
            for d in dims:
                items = scope.get(d)
                items = items if isinstance(items, list) else ([items] if items else [])
                if not items:
                    return False, "Para «por cada» tienes que seleccionar al menos un brawler/modo/mapa en ese ámbito."
    return True, None


# --------------------------- descripción legible ---------------------------

def _scope_text(scope):
    if not scope:
        return ""
    bits = []
    for key, lab in (("brawler", "con"), ("mode", "en"), ("map", "en"), ("role", "como rol")):
        v = scope.get(key)
        if v:
            v = ", ".join(v) if isinstance(v, list) else v
            bits.append(f"{lab} {v}")
    return (" " + " ".join(bits)) if bits else ""


_PER_LABEL = {"brawler": "brawler", "mode": "modo", "map": "mapa"}


def _per_dims(c) -> list:
    """Dimensiones normalizadas del modificador «por cada» (0, 1 o varias -> producto)."""
    per = c.get("per")
    if not per:
        return []
    dims = [per] if isinstance(per, str) else list(per)
    return [d for d in dims if d in PER_DIMS]


def _per_text(c) -> str:
    """Sufijo legible del modificador «por cada» (una o varias dimensiones combinadas)."""
    dims = _per_dims(c)
    if not dims:
        return ""
    scope = c.get("scope") or {}
    parts = []
    for d in dims:
        items = scope.get(d)
        items = items if isinstance(items, list) else ([items] if items else [])
        if not items:
            continue
        shown = ", ".join(map(str, items[:5])) + (f"… (+{len(items) - 5})" if len(items) > 5 else "")
        parts.append(f"cada {_PER_LABEL.get(d, d)} ({shown})")
    return (", por " + " y ".join(parts)) if parts else ""


def describe_condition(c) -> str:
    metric = c.get("metric")
    per_dims = _per_dims(c)
    # Las dimensiones "por cada" se describen aparte, no como filtro conjunto del ámbito.
    scope = _scope_text({k: v for k, v in (c.get("scope") or {}).items() if k not in per_dims})
    per_s = _per_text(c)
    t = c.get("target")
    ti = int(float(t)) if t is not None else 0
    if metric == "winrate":
        mg = int(c.get("min_games") or 0)
        return f"Mantén un {ti}% de victorias{scope}{per_s}" + (f" (mínimo {mg} partidas)" if mg else "")
    if metric == "performance":
        mg = int(c.get("min_games") or 0)
        return f"Alcanza {ti} de rendimiento ajustado{scope}{per_s}" + (f" (mínimo {mg} partidas)" if mg else "")
    if metric == "max_losses":
        mg = int(c.get("min_games") or 0)
        return f"No pases de {ti} derrotas{scope}{per_s}" + (f" en al menos {mg} partidas" if mg else "")
    return {
        "wins": f"Gana {ti} partidas{scope}{per_s}",
        "games": f"Juega {ti} partidas{scope}{per_s}",
        "win_streak": f"Encadena {ti} victorias seguidas{scope}{per_s}",
        "distinct_brawlers": f"Gana con {ti} brawlers distintos{scope}",
        "distinct_played": f"Juega con {ti} brawlers distintos{scope}",
        "trophies": f"Suma {ti} copas{scope}{per_s}",
        "star_player": f"Sé jugador estelar {ti} veces{scope}{per_s}",
        "active_days": f"Juega en {ti} días distintos{scope}",
    }.get(metric, f"{METRICS.get(metric, {}).get('label', '?')}: {t}")


# --------------------------- progreso ---------------------------

def _to_bs_time(iso):
    """ISO (joined_at / ahora) -> formato de battle_time ('YYYYMMDDTHHMMSS.000Z')."""
    try:
        return datetime.fromisoformat(iso).strftime("%Y%m%dT%H%M%S.000Z")
    except Exception:
        return None


def _deadline(participant, reto):
    days = reto.get("time_limit_days")
    if not days or not participant.get("joined_at"):
        return None
    try:
        start = datetime.fromisoformat(participant["joined_at"])
    except Exception:
        return None
    return (start + timedelta(days=int(days))).strftime("%Y%m%dT%H%M%S.000Z")


def condition_progress(player_tag, joined_at, deadline, c):
    metric, target = c["metric"], float(c["target"])
    scope = c.get("scope") or {}
    per_dims = _per_dims(c)

    # --- Modificador "por cada": el objetivo debe cumplirse para CADA combinación de los
    # valores seleccionados en las dimensiones elegidas (producto cartesiano). ---
    if per_dims:
        dim_lists = []
        for d in per_dims:
            items = scope.get(d)
            items = items if isinstance(items, list) else ([items] if items else [])
            dim_lists.append((d, items))
        combos = list(itertools.product(*[lst for _, lst in dim_lists]))
        subs, breakdown = [], []
        for combo in combos:
            sub = dict(c); sub.pop("per", None)
            sub_scope = dict(scope)
            for (d, _), val in zip(dim_lists, combo):
                sub_scope[d] = val
            sub["scope"] = sub_scope
            s = condition_progress(player_tag, joined_at, deadline, sub)
            subs.append(s)
            # desglose por sub-objetivo (cada combinación con su avance individual)
            breakdown.append({"combo": list(combo), "current": s["current"], "target": s["target"],
                              "done": s["done"], "pct": s["pct"], "note": s.get("note")})
        total = len(subs)
        met = sum(1 for s in subs if s["done"])
        done = total > 0 and met == total
        pct = 100.0 if done else (round(100.0 * met / total, 1) if total else 0.0)
        lbl = " y ".join(_PER_LABEL.get(d, d) for d in per_dims)
        return {"metric": metric, "target": target, "current": met, "per": per_dims, "per_total": total,
                "done": done, "pct": pct, "text": describe_condition(c),
                "note": f"{met}/{total} ({lbl})", "breakdown": breakdown}

    since = _to_bs_time(joined_at) if joined_at else None
    current = db.reto_metric(player_tag, since, deadline, metric, scope)
    note = None

    # --- Métricas con umbral mínimo de partidas (win rate / rendimiento). ---
    if metric in ("winrate", "performance"):
        mg = int(c.get("min_games") or 0)
        games = db.reto_metric(player_tag, since, deadline, "games", scope)
        done = (games >= mg) and (current >= target)
        if games < mg:
            note = f"{int(games)}/{mg} partidas mínimas"
        pct = 100.0 if done else (max(0.0, min(99.0, round(100.0 * current / target, 1))) if target else 0.0)
    # --- Tope (menos es mejor): no superar 'target' derrotas sobre un mínimo de partidas. ---
    elif metric == "max_losses":
        mg = int(c.get("min_games") or 0)
        games = db.reto_metric(player_tag, since, deadline, "games", scope)
        over = current > target
        done = (games >= mg) and not over
        if over:
            note = f"te pasaste: {int(current)} derrotas (máx {int(target)})"
        else:
            note = f"{int(current)}/{int(target)} derrotas · {int(games)}/{mg} partidas"
        pct = 100.0 if done else (0.0 if over else (max(0.0, min(99.0, round(100.0 * games / mg, 1))) if mg else 0.0))
    # --- Métricas normales (más es mejor). ---
    else:
        done = current >= target
        pct = 100.0 if done else (max(0.0, min(99.0, round(100.0 * current / target, 1))) if target else 0.0)

    return {"metric": metric, "target": target, "current": current, "done": done,
            "pct": pct, "text": describe_condition(c), "note": note}


def reto_progress(reto, participant):
    """Progreso de un participante: por condición + global. `expired` si venció el plazo."""
    if not participant or not participant.get("player_tag"):
        return {"conditions": [], "done": False, "pct": 0.0, "expired": False}
    deadline = _deadline(participant, reto)
    tag, joined = participant["player_tag"], participant.get("joined_at")
    progs = [condition_progress(tag, joined, deadline, c) for c in (reto.get("conditions") or [])]
    done = bool(progs) and all(p["done"] for p in progs)
    pct = round(sum(p["pct"] for p in progs) / len(progs), 1) if progs else 0.0
    now = _to_bs_time(datetime.now(timezone.utc).isoformat())
    expired = bool(deadline) and now is not None and now > deadline and not done
    return {"conditions": progs, "done": done, "pct": 100.0 if done else pct, "expired": expired}


# --------------------------- dificultad asignada (v1) ---------------------------
# La declara el creador (1..5). El sistema la RECALIBRA según lo lejos que estén los
# objetivos del ritmo/nivel reciente del jugador que lo ve. Los retos del Sensei NO se
# recalibran (ya son personalizados). Heurística v1, pensada para refinarse.

def recalibrate_difficulty(reto, player_tag, lookback=60):
    declared = int(reto.get("difficulty_declared") or 3)
    if reto.get("source") == "sensei" or not player_tag:
        return declared
    conds = reto.get("conditions") or []
    if not conds:
        return declared
    effort = sum(_condition_effort(c, player_tag) for c in conds) / len(conds)
    raw = min(5.0, max(1.0, 1.0 + 4.0 * effort))        # esfuerzo medio (0..1) -> 1..5
    blended = round(0.6 * raw + 0.4 * declared)          # no ignorar al creador
    return int(min(5, max(1, blended)))


def _condition_effort(c, player_tag):
    """Esfuerzo aproximado (0..1) de una condición para este jugador, mirando su
    histórico reciente en ese ámbito."""
    metric, target = c["metric"], float(c["target"])
    scope = c.get("scope") or {}
    base_effort = _base_effort(metric, target, scope, player_tag)
    per_dims = _per_dims(c)
    if per_dims:
        n = 1
        for d in per_dims:
            items = scope.get(d)
            items = items if isinstance(items, list) else ([items] if items else [])
            n *= max(1, len(items))
        # exigir el objetivo por CADA combinación sube el esfuerzo (saturando, no lineal)
        base_effort = min(1.0, base_effort * (1.0 + math.log10(max(1, n))))
    return base_effort


def _base_effort(metric, target, scope, player_tag):
    if metric in ("wins", "games", "trophies", "star_player", "distinct_brawlers", "distinct_played", "active_days"):
        base = "games" if metric in ("trophies", "active_days") else metric
        recent = db.reto_metric(player_tag, None, None, base, scope)
        pace = max(5.0, recent / 4.0)                     # ~un cuarto de su histórico como "ritmo"
        return min(1.0, target / pace / 4.0)
    if metric in ("winrate", "performance"):
        cur = db.reto_metric(player_tag, None, None, metric, scope)
        return min(1.0, max(0.0, (target - cur) / 40.0))  # 40 puntos por encima = máximo
    if metric == "max_losses":                            # tope: pocas derrotas permitidas = más difícil
        return min(1.0, max(0.1, 1.0 - target / 20.0))
    if metric == "win_streak":
        wr = db.reto_metric(player_tag, None, None, "winrate", scope) or 40.0
        p = max(0.2, min(0.9, wr / 100.0))
        return min(1.0, -math.log10(max(1e-6, p ** target)) / 6.0)
    return 0.5


# --------------------------- Gating del Sensei ---------------------------
# Tras un informe, no se puede pedir otro hasta haber cumplido casi todos sus retos
# (quedan <= SENSEI_GATE_REMAINING activos), o hasta que pasen SENSEI_GATE_DAYS días, o si
# eres admin. "Resetear entrenamiento" (abandonar los activos) requiere los días o ser admin.

SENSEI_GATE_REMAINING = 4
SENSEI_GATE_DAYS = 10


def sensei_gate(user_id, is_admin=False):
    """¿Puede el usuario pedir un nuevo informe al Sensei? Devuelve el estado del candado."""
    active = db.count_active_sensei_retos(user_id)
    last_at = db.last_sensei_reto_at(user_id)
    days = None
    if last_at:
        try:
            days = (datetime.now(timezone.utc) - datetime.fromisoformat(last_at)).days
        except Exception:  # noqa: BLE001
            days = None
    fresh = last_at is None
    can = bool(is_admin or fresh or active <= SENSEI_GATE_REMAINING or (days is not None and days >= SENSEI_GATE_DAYS))
    can_reset = bool(is_admin or (days is not None and days >= SENSEI_GATE_DAYS))
    return {"can_generate": can, "active": active, "threshold": SENSEI_GATE_REMAINING,
            "days_since": days, "gate_days": SENSEI_GATE_DAYS, "can_reset": can_reset, "is_admin": bool(is_admin)}
