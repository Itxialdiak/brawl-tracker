"""Rutas de EVENTOS (ligas y torneos): crear/editar, seguir, apuntarse, equipos,
partidas (crear, editar, detectar, generar por formato), clasificación y resumen IA.

Es el router más grande; se extrajo entero de main.py con sus helpers (_gen_*,
_compute_standings, _match_names…). Se incluye con app.include_router()."""
import os
import random
import base64
import uuid
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Body, Query, Depends, HTTPException
from fastapi.responses import JSONResponse
from .. import db, brawl_api, coach, bs_maps, detect, auth
from ..api_common import _get_player_cached, _get_battlelog_cached, _ensure_player_profiles, _parse_player_tags

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")

router = APIRouter()


# ============================ EVENTOS (LIGAS Y TORNEOS) ============================

_EVENT_MEDIA_DIR = os.path.join(FRONTEND_DIR, "media", "events")
_IMG_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
_EV_KINDS = {"league", "tournament"}
_EV_MODES = {"individual", "teams"}
_EV_VIS = {"public", "acceptance", "private"}
_EV_MATCH = {"bo1", "bo3", "bo5"}


def _event_public(e: dict) -> dict:
    """Vista del evento sin el hash de la contraseña."""
    return {
        "id": e["id"], "owner_user_id": e["owner_user_id"], "name": e["name"],
        "kind": e["kind"], "mode": e["mode"], "visibility": e["visibility"],
        "language": e.get("language"), "max_participants": e.get("max_participants"),
        "format": e.get("format"), "match_type": e.get("match_type"),
        "date_start": e.get("date_start"), "date_end": e.get("date_end"),
        "description": e.get("description"), "poster_url": e.get("poster_url"),
        "has_password": e.get("has_password", False),
        "require_confirmation": e.get("require_confirmation"),
        "hidden": e.get("hidden"),
        "settings": e.get("settings") or {}, "status": e.get("status"),
        "participants": e.get("participants"), "followers": e.get("followers"),
        "relation": e.get("relation"),
    }


def _can_view_event(e: dict, user_id: int) -> bool:
    # La visibilidad controla CÓMO se entra (contraseña/validación) y si el evento
    # aparece en el tablón ('hidden'), no quién puede ver su ficha. Cualquiera con el
    # enlace puede consultarla (los privados ocultos se comparten por enlace directo).
    return True


def _round_robin(ids: list):
    """Calendario todos-contra-todos (método del círculo). Devuelve lista de jornadas,
    cada una con pares (a, b)."""
    ids = list(ids)
    if len(ids) % 2:
        ids.append(None)  # descanso si son impares
    n = len(ids)
    arr = ids[:]
    rounds = []
    for _ in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = arr[i], arr[n - 1 - i]
            if a is not None and b is not None:
                pairs.append((a, b))
        rounds.append(pairs)
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]  # rota dejando fijo el primero
    return rounds


def _seed_order(n: int) -> list:
    """Orden de siembra estándar de un cuadro de tamaño n (potencia de 2).
    Coloca el 1 y el 2 en extremos opuestos, etc. Devuelve semillas 1..n."""
    if n <= 1:
        return [1]
    prev = _seed_order(n // 2)
    out = []
    for s in prev:
        out.append(s); out.append(n + 1 - s)
    return out


def _advance_winner(eid: int, m: dict, teams_mode: bool) -> None:
    """Lleva al ganador de un partido del cuadro a su hueco de la ronda siguiente."""
    pos = m.get("bracket_pos")
    if pos is None:
        return
    w = m.get("winner")
    if w not in ("a", "b"):
        return
    win_id = (m.get("a_team") if w == "a" else m.get("b_team")) if teams_mode \
        else (m.get("a_tag") if w == "a" else m.get("b_tag"))
    r = (m.get("round") or 1)
    nxt = next((x for x in db.list_matches(eid)
                if (x.get("round") == r + 1 and x.get("bracket_pos") == pos // 2)), None)
    if not nxt:
        return
    slot = "a" if pos % 2 == 0 else "b"
    field = (slot + "_team") if teams_mode else (slot + "_tag")
    db.update_match(nxt["id"], {field: win_id})


def _unadvance(eid: int, m: dict, teams_mode: bool) -> None:
    """Quita la aportación de un partido del hueco siguiente (al borrar su resultado)."""
    pos = m.get("bracket_pos")
    if pos is None:
        return
    r = (m.get("round") or 1)
    nxt = next((x for x in db.list_matches(eid)
                if (x.get("round") == r + 1 and x.get("bracket_pos") == pos // 2)), None)
    if not nxt:
        return
    slot = "a" if pos % 2 == 0 else "b"
    field = (slot + "_team") if teams_mode else (slot + "_tag")
    db.update_match(nxt["id"], {field: None})


def _gen_single_elim(eid: int, ids: list, teams_mode: bool, round_mm) -> int:
    """Genera el cuadro completo de eliminación directa: ronda 1 con byes para las
    primeras cabezas de serie, rondas siguientes vacías, y resuelve los byes."""
    import math
    n = len(ids)
    rounds_count = max(1, math.ceil(math.log2(n)))
    size = 1 << rounds_count
    order = _seed_order(size)
    seq = [ids[s - 1] if s <= n else None for s in order]  # None = bye
    pairs = [(seq[i], seq[i + 1]) for i in range(0, size, 2)]
    created = 0
    mode, mp = round_mm(1)
    for pos, (a, b) in enumerate(pairs):  # ronda 1
        if teams_mode:
            db.create_match(eid, 1, a_team=a, b_team=b, mode=mode, map=mp, bracket_pos=pos)
        else:
            db.create_match(eid, 1, a_tag=a, b_tag=b, mode=mode, map=mp, bracket_pos=pos)
        created += 1
    for r in range(2, rounds_count + 1):  # rondas siguientes vacías
        rm, rmap = round_mm(r)
        for pos in range(size >> r):
            db.create_match(eid, r, mode=rm, map=rmap, bracket_pos=pos)
            created += 1
    for m in db.list_matches(eid):  # resolver byes de la ronda 1 y avanzar
        if (m.get("round") or 1) != 1:
            continue
        a = m.get("a_team") if teams_mode else m.get("a_tag")
        b = m.get("b_team") if teams_mode else m.get("b_tag")
        if bool(a) != bool(b):  # exactamente uno presente → bye
            db.update_match(m["id"], {"winner": "a" if a else "b", "status": "played"})
            _advance_winner(eid, db.get_match(m["id"]), teams_mode)
    return created


# --------------------------- Suizo / McMahon (Fase 3) ---------------------------

def _mcmahon_initial(participants: list, pw: int) -> dict:
    """Puntos iniciales de McMahon por bandas de copas (snapshot). Solo individual.
    Reparte a los jugadores en hasta 4 bandas por copas; cada banda por encima
    de la inferior parte con el equivalente a una victoria más."""
    seeded = [(p["player_tag"], p.get("seed_cups") or 0) for p in participants]
    if not seeded:
        return {}
    order = sorted(seeded, key=lambda x: x[1])  # ascendente por copas
    n = len(order)
    bands = min(4, max(1, n // 2))
    out = {}
    for i, (tag, _c) in enumerate(order):
        band = (i * bands) // n  # 0 = menos copas … bands-1 = más copas
        out[tag] = band * pw
    return out


def _played_pairs(matches: list, teams_mode: bool) -> set:
    s = set()
    for m in matches:
        a = m.get("a_team") if teams_mode else m.get("a_tag")
        b = m.get("b_team") if teams_mode else m.get("b_tag")
        if a and b:
            s.add(frozenset((a, b)))
    return s


def _had_bye(matches: list, key, teams_mode: bool) -> bool:
    for m in matches:
        a = m.get("a_team") if teams_mode else m.get("a_tag")
        b = m.get("b_team") if teams_mode else m.get("b_tag")
        if (a == key and not b) or (b == key and not a):
            return True
    return False


def _pair_swiss(order: list, played: set):
    """Emparejamiento sin repetir rival (backtracking). Devuelve lista de (a,b) o None."""
    if not order:
        return []
    a = order[0]
    for i in range(1, len(order)):
        b = order[i]
        if frozenset((a, b)) in played:
            continue
        sub = _pair_swiss(order[1:i] + order[i + 1:], played)
        if sub is not None:
            return [(a, b)] + sub
    return None


def _pair_greedy_avoid(order: list, played: set) -> list:
    """Plan B: empareja al primero con el primer rival no repetido (o repite si no hay)."""
    order = list(order)
    pairs = []
    while order:
        a = order.pop(0)
        idx = next((i for i, b in enumerate(order) if frozenset((a, b)) not in played), None)
        if idx is None:
            idx = 0
        pairs.append((a, order.pop(idx)))
    return pairs


async def _snapshot_cups(eid: int, parts: list) -> None:
    """Guarda el snapshot de copas totales de cada cuenta (semilla suizo/McMahon)."""
    async def one(tag):
        try:
            p = await _get_player_cached(tag)
            return tag, p.get("trophies")
        except Exception:  # noqa: BLE001
            return tag, None
    results = await asyncio.gather(*[one(p["player_tag"]) for p in parts]) if parts else []
    for tag, cups in results:
        if cups is not None:
            await asyncio.to_thread(db.set_participant_seed_cups, eid, tag, cups)


def _gen_swiss_round(eid: int, e: dict, teams_mode: bool, round_mm) -> dict:
    """Genera la SIGUIENTE ronda suiza/McMahon emparejando por puntuación sin repetir rival."""
    matches = db.list_matches(eid)
    if matches and any(m.get("status") != "played" for m in matches):
        return {"error": "Termina la ronda actual (todos los resultados) antes de emparejar la siguiente."}
    parts = db.list_participants(eid)
    teams = db.list_teams(eid)
    keys = [t["id"] for t in teams] if teams_mode else [p["player_tag"] for p in parts]
    if len(keys) < 2:
        return {"error": "Hacen falta al menos 2 participantes."}
    next_round = (max((m.get("round") or 1) for m in matches) + 1) if matches else 1
    mode, mp = round_mm(next_round)
    # Evento INDIVIDUAL con un modo de equipo (3v3, dúo, trío): esa ronda no se juega 1 vs 1,
    # se forman equipos aleatorios del tamaño del modo (como en "equipos aleatorios").
    if not teams_mode and bs_maps.team_size_for_mode(mode) > 1:
        return _gen_random_round(eid, parts, None, round_mm)
    standings = _compute_standings(e, matches, parts, teams)
    score = {r["key"]: r["pts"] for r in standings}
    name_of = {r["key"]: str(r["name"]).lower() for r in standings}
    seed = {t["id"]: 0 for t in teams} if teams_mode else {p["player_tag"]: (p.get("seed_cups") or 0) for p in parts}
    played = _played_pairs(matches, teams_mode)
    order = sorted(keys, key=lambda k: (-score.get(k, 0), -seed.get(k, 0), name_of.get(k, "")))
    bye_key = None
    if len(order) % 2 == 1:
        for k in reversed(order):  # bye al de menos puntos que no lo haya tenido
            if not _had_bye(matches, k, teams_mode):
                bye_key = k
                break
        bye_key = bye_key if bye_key is not None else order[-1]
        order = [k for k in order if k != bye_key]
    pairs = (_pair_swiss(order, played) if len(order) <= 16 else None)
    if pairs is None:
        pairs = _pair_greedy_avoid(order, played)
    created = 0
    for (a, b) in pairs:
        if teams_mode:
            db.create_match(eid, next_round, a_team=a, b_team=b, mode=mode, map=mp)
        else:
            db.create_match(eid, next_round, a_tag=a, b_tag=b, mode=mode, map=mp)
        created += 1
    if bye_key is not None:  # el bye = partido jugado de un solo lado (victoria)
        mid = (db.create_match(eid, next_round, a_team=bye_key, mode=mode, map=mp) if teams_mode
               else db.create_match(eid, next_round, a_tag=bye_key, mode=mode, map=mp))
        db.update_match(mid, {"winner": "a", "status": "played"})
        created += 1
    return {"created": created, "round": next_round, "bye": bye_key is not None}


def _gen_random_round(eid: int, parts: list, team_size, round_mm) -> dict:
    """Genera una ronda de EQUIPOS ALEATORIOS (eventos individuales): baraja a los
    participantes, forma equipos de `team_size` y los empareja. Cada partido guarda
    su roster; la clasificación es individual (cada jugador suma según su equipo).
    El tamaño de equipo se toma del MODO de la ronda (3 en 3v3, 2 en dúo, 1 en Duelos);
    `team_size` explícito solo se usa como respaldo si el modo no lo determina."""
    matches = db.list_matches(eid)
    if matches and any(m.get("status") != "played" for m in matches):
        return {"error": "Termina la ronda actual (todos los resultados) antes de generar la siguiente."}
    next_round = (max((m.get("round") or 1) for m in matches) + 1) if matches else 1
    mode, mp = round_mm(next_round)
    k = bs_maps.team_size_for_mode(mode) or int(team_size or 0) or 3  # según el modo de la ronda
    k = max(1, k)
    tags = [p["player_tag"] for p in parts]
    if len(tags) < 2 * k:
        return {"error": f"Hacen falta al menos {2 * k} jugadores para equipos de {k} (modo {mode or '3v3'})."}
    random.shuffle(tags)
    teams = [tags[i:i + k] for i in range(0, len(tags), k)]
    teams = [t for t in teams if len(t) == k]  # descarta equipo incompleto
    created = 0
    i = 0
    while i + 1 < len(teams):
        db.create_match(eid, next_round, mode=mode, map=mp, roster_a=teams[i], roster_b=teams[i + 1])
        created += 1
        i += 2
    benched = len(tags) - created * 2 * k
    return {"created": created, "round": next_round, "benched": benched, "team_size": k}


def _compute_standings(e: dict, matches: list, participants: list, teams: list) -> list:
    """Clasificación a partir de los enfrentamientos jugados, con los puntos del evento.
    En McMahon suma los puntos iniciales por copas; en suizo/McMahon desempata por copas."""
    pts = (e.get("settings") or {}).get("points") or {}
    pw, pdr, pl = pts.get("win", 3), pts.get("draw", 1), pts.get("loss", 0)
    teams_mode = e["mode"] == "teams"
    fmt = e.get("format") or ""
    rows = {}
    if teams_mode:
        for t in teams:
            rows[t["id"]] = {"key": t["id"], "name": t.get("name") or "Equipo", "logo": t.get("logo_url"),
                             "seed_cups": None, "pj": 0, "g": 0, "e": 0, "p": 0, "sf": 0, "sa": 0, "pts": 0}
    else:
        for p in participants:
            rows[p["player_tag"]] = {"key": p["player_tag"], "name": p.get("player_name") or p["player_tag"],
                                     "tag": p["player_tag"], "icon_id": p.get("icon_id"), "seed_cups": p.get("seed_cups"),
                                     "pj": 0, "g": 0, "e": 0, "p": 0, "sf": 0, "sa": 0, "pts": 0}
    if fmt == "mcmahon" and not teams_mode:  # ventaja inicial por copas
        for tag, ini in _mcmahon_initial(participants, pw).items():
            if tag in rows:
                rows[tag]["pts"] += ini
                rows[tag]["mcmahon"] = ini
    for m in matches:
        if m.get("status") != "played" or m.get("winner") == "void":
            continue
        w = m.get("winner")
        ros_a, ros_b = (m.get("roster_a") or []), (m.get("roster_b") or [])
        if ros_a or ros_b:  # partido de equipos aleatorios → puntuación individual por roster
            for tag in ros_a:
                if tag in rows:
                    r = rows[tag]; r["pj"] += 1
                    if w == "a": r["g"] += 1; r["pts"] += pw
                    elif w == "b": r["p"] += 1; r["pts"] += pl
                    else: r["e"] += 1; r["pts"] += pdr
            for tag in ros_b:
                if tag in rows:
                    r = rows[tag]; r["pj"] += 1
                    if w == "b": r["g"] += 1; r["pts"] += pw
                    elif w == "a": r["p"] += 1; r["pts"] += pl
                    else: r["e"] += 1; r["pts"] += pdr
            continue
        ka, kb = (m.get("a_team"), m.get("b_team")) if teams_mode else (m.get("a_tag"), m.get("b_tag"))
        if (ka in rows) and not kb:  # bye → victoria sin rival
            r = rows[ka]; r["pj"] += 1; r["g"] += 1; r["pts"] += pw; continue
        if (kb in rows) and not ka:
            r = rows[kb]; r["pj"] += 1; r["g"] += 1; r["pts"] += pw; continue
        if ka not in rows or kb not in rows:
            continue
        sa, sb = m.get("score_a") or 0, m.get("score_b") or 0
        ra, rb = rows[ka], rows[kb]
        ra["pj"] += 1; rb["pj"] += 1
        ra["sf"] += sa; ra["sa"] += sb; rb["sf"] += sb; rb["sa"] += sa
        if w == "a":
            ra["g"] += 1; rb["p"] += 1; ra["pts"] += pw; rb["pts"] += pl
        elif w == "b":
            rb["g"] += 1; ra["p"] += 1; rb["pts"] += pw; ra["pts"] += pl
        else:
            ra["e"] += 1; rb["e"] += 1; ra["pts"] += pdr; rb["pts"] += pdr
    out = list(rows.values())
    for r in out:
        r["dif"] = r["sf"] - r["sa"]
    if fmt == "random_teams":  # individual: por puntos y victorias
        out.sort(key=lambda r: (-r["pts"], -r["g"], str(r["name"]).lower()))
    elif fmt in ("swiss", "mcmahon"):  # desempate final por copas (snapshot)
        out.sort(key=lambda r: (-r["pts"], -r["dif"], -r["sf"], -(r.get("seed_cups") or 0), str(r["name"]).lower()))
    else:
        out.sort(key=lambda r: (-r["pts"], -r["dif"], -r["sf"], str(r["name"]).lower()))
    return out


@router.post("/api/events")
def api_event_create(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    body = payload or {}
    name = (body.get("name") or "").strip()
    kind, mode, vis = body.get("kind"), body.get("mode"), body.get("visibility")
    lang = body.get("language") or None
    if not name:
        return JSONResponse({"error": "Pon un nombre al evento."}, status_code=400)
    if kind not in _EV_KINDS or mode not in _EV_MODES or vis not in _EV_VIS:
        return JSONResponse({"error": "Datos de evento no válidos."}, status_code=400)
    eid = db.create_event(user["id"], name, kind, mode, vis, lang)
    return {"ok": True, "id": eid}


@router.get("/api/events/mine")
def api_events_mine(user: dict = Depends(auth.require_user)):
    return {"events": db.list_my_events(user["id"])}


@router.get("/api/events/board")
def api_events_board(types: str = Query(""), langs: str = Query(""),
                     acceptance: str = Query(""), user: dict = Depends(auth.require_user)):
    t = [x for x in types.split(",") if x] or None
    lg = [x for x in langs.split(",") if x] or None
    a = [x for x in acceptance.split(",") if x] or None
    return {"events": db.list_board_events(user["id"], t, lg, a)}


@router.get("/api/events/{eid}")
async def api_event_detail(eid: int, user: dict = Depends(auth.require_user)):
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    if not _can_view_event(e, user["id"]):
        raise HTTPException(status_code=403, detail="Evento privado.")
    _apply_auto_approvals(eid, e)   # confirma propuestas de modo/mapa con +24 h sin respuesta
    counts = db.event_counts(eid)
    e["participants"], e["followers"] = counts["participants"], counts["followers"]
    is_owner = e["owner_user_id"] == user["id"]
    parts = db.list_participants(eid)
    missing = [p["player_tag"] for p in parts if not p.get("player_name")]
    if missing:  # rellena nombres desde la API (solo la 1.ª vez; luego quedan en players)
        await _ensure_player_profiles(missing)
        parts = db.list_participants(eid)
    e["relation"] = "owner" if is_owner else (
        "participant" if any(p.get("user_id") == user["id"] for p in parts) else (
            "follower" if db.is_following_event(eid, user["id"]) else "none"))
    pub = _event_public(e)
    pub["participants_list"] = parts
    pub["teams"] = db.list_teams(eid)
    pub["is_owner"] = is_owner
    pub["is_following"] = db.is_following_event(eid, user["id"])
    pub["my_request"] = db.user_pending_request(eid, user["id"])
    pub["my_tags"] = db.list_players_for_user(user["id"])
    pub["matches"] = db.list_matches(eid)
    pub["standings"] = _compute_standings(e, pub["matches"], parts, pub["teams"])
    if is_owner:
        pub["requests"] = db.list_requests(eid, "pending")
    return pub


@router.put("/api/events/{eid}")
def api_event_update(eid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    body = payload or {}
    fields = {}
    for c in ("name", "kind", "mode", "visibility", "language", "format",
              "match_type", "date_start", "date_end", "description", "poster_url", "status"):
        if c in body:
            fields[c] = body[c]
    if "max_participants" in body:
        try:
            fields["max_participants"] = max(2, min(512, int(body["max_participants"])))
        except Exception:  # noqa: BLE001
            pass
    if "require_confirmation" in body:
        fields["require_confirmation"] = 1 if body["require_confirmation"] else 0
    if "hidden" in body:
        fields["hidden"] = 1 if body["hidden"] else 0
    if "settings" in body and isinstance(body["settings"], dict):
        fields["settings"] = body["settings"]
    if "password" in body:
        pw = (body["password"] or "").strip()
        fields["password_hash"] = auth.hash_password(pw) if pw else None
    if fields.get("kind") and fields["kind"] not in _EV_KINDS:
        return JSONResponse({"error": "Tipo no válido."}, status_code=400)
    if fields.get("visibility") and fields["visibility"] not in _EV_VIS:
        return JSONResponse({"error": "Visibilidad no válida."}, status_code=400)
    if fields.get("match_type") and fields["match_type"] not in _EV_MATCH:
        return JSONResponse({"error": "Enfrentamiento no válido."}, status_code=400)
    # La modalidad (individual/equipos) solo se puede cambiar si NO hay nadie apuntado: los
    # inscritos se apuntaron con unos requisitos; para cambiarla hay que quitarlos antes.
    if "mode" in fields:
        e = db.get_event(eid)
        if e and fields["mode"] != e["mode"] and db.participant_count(eid) > 0:
            return JSONResponse({"error": "No puedes cambiar la modalidad con jugadores apuntados. "
                                          "Quítalos primero (indicando el motivo) y que se vuelvan a apuntar."},
                                status_code=400)
    db.update_event(eid, fields)
    return {"ok": True}


@router.delete("/api/events/{eid}")
def api_event_delete(eid: int, user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    db.delete_event(eid)
    return {"ok": True}


@router.post("/api/events/{eid}/follow")
def api_event_follow(eid: int, user: dict = Depends(auth.require_user)):
    if not db.get_event(eid):
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    db.follow_event(eid, user["id"])
    return {"ok": True, "followers": db.event_counts(eid)["followers"]}


@router.delete("/api/events/{eid}/follow")
def api_event_unfollow(eid: int, user: dict = Depends(auth.require_user)):
    db.unfollow_event(eid, user["id"])
    return {"ok": True, "followers": db.event_counts(eid)["followers"]}


@router.post("/api/events/{eid}/join")
def api_event_join(eid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    body = payload or {}
    tag = (body.get("player_tag") or "").strip()
    if not tag:
        return JSONResponse({"error": "Elige con qué ID te apuntas."}, status_code=400)
    tag = db.normalize_tag(tag)
    if not db.user_follows(user["id"], tag):
        return JSONResponse({"error": "Ese ID no está en tu cuenta."}, status_code=400)
    if db.tag_in_event(eid, tag):
        return JSONResponse({"error": "Ese ID ya está inscrito en el evento."}, status_code=409)
    if db.participant_count(eid) >= (e.get("max_participants") or 12):
        return JSONResponse({"error": "El evento está completo."}, status_code=409)
    team_name = (body.get("team_name") or "").strip() or None
    team_logo = (body.get("team_logo_url") or "").strip() or None
    vis = e["visibility"]

    def _do_join():
        team_id = None
        if e["mode"] == "teams" and team_name:
            team_id = db.create_team(eid, team_name, team_logo, user["id"])
        db.add_participant(eid, user["id"], tag, team_id, 0)

    if vis == "public":
        _do_join()
        return {"ok": True, "joined": True}
    if vis == "acceptance":
        db.create_request(eid, user["id"], tag, team_name, body.get("message"))
        return {"ok": True, "requested": True}
    # privado
    ph = db.get_event_password_hash(eid)
    if ph and not auth.verify_password(body.get("password") or "", ph):
        return JSONResponse({"error": "Contraseña incorrecta."}, status_code=403)
    if e.get("require_confirmation"):
        db.create_request(eid, user["id"], tag, team_name, body.get("message"))
        return {"ok": True, "requested": True}
    _do_join()
    return {"ok": True, "joined": True}


@router.post("/api/events/{eid}/requests/{rid}/accept")
def api_event_req_accept(eid: int, rid: int, user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    req = db.get_request(rid)
    if not req or req["event_id"] != eid:
        return JSONResponse({"error": "Solicitud no encontrada."}, status_code=404)
    if req["status"] != "pending":
        return JSONResponse({"error": "Esa solicitud ya está resuelta."}, status_code=409)
    e = db.get_event(eid)
    if db.participant_count(eid) >= (e.get("max_participants") or 12):
        return JSONResponse({"error": "No quedan plazas."}, status_code=409)
    if not db.tag_in_event(eid, req["player_tag"]):
        team_id = None
        if e["mode"] == "teams" and req.get("team_name"):
            team_id = db.create_team(eid, req["team_name"], None, req["user_id"])
        db.add_participant(eid, req["user_id"], req["player_tag"], team_id, 0)
    db.set_request_status(rid, "accepted")
    return {"ok": True}


@router.post("/api/events/{eid}/requests/{rid}/reject")
def api_event_req_reject(eid: int, rid: int, user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    req = db.get_request(rid)
    if not req or req["event_id"] != eid:
        return JSONResponse({"error": "Solicitud no encontrada."}, status_code=404)
    db.set_request_status(rid, "rejected")
    return {"ok": True}


def _notify_followers_player_added(eid: int, e: dict, tags: list) -> None:
    """Avisa a quienes SIGUEN a un jugador recién apuntado al evento (Fase 6)."""
    if not tags:
        return
    ev_name = e.get("name") or "un evento"
    owner = e.get("owner_user_id")
    for tag in tags:
        followers = db.users_following_player(tag)
        if not followers:
            continue
        pname = db.get_player_name(tag) or tag
        db.notify_many(
            followers, "player_in_event",
            f"{pname} participa en un evento",
            f"Sigues a {pname}, que se ha apuntado a «{ev_name}». ¿Quieres seguir el evento para no perderte sus partidas?",
            event_id=eid, data={"player_tag": tag}, exclude=[owner])


@router.post("/api/events/{eid}/invite")
async def api_event_invite(eid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    e = db.get_event(eid)
    tag = ((payload or {}).get("player_tag") or "").strip()
    if not tag:
        return JSONResponse({"error": "Indica el tag del jugador."}, status_code=400)
    tag = db.normalize_tag(tag)
    if db.tag_in_event(eid, tag):
        return JSONResponse({"error": "Ese ID ya está inscrito."}, status_code=409)
    if db.participant_count(eid) >= (e.get("max_participants") or 12):
        return JSONResponse({"error": "No quedan plazas."}, status_code=409)
    team_name = ((payload or {}).get("team_name") or "").strip() or None
    team_id = db.create_team(eid, team_name, None, None) if (e["mode"] == "teams" and team_name) else None
    db.add_participant(eid, None, tag, team_id, 1)
    await _ensure_player_profiles([tag])  # guarda su nombre para mostrarlo
    _notify_followers_player_added(eid, e, [tag])
    return {"ok": True}


@router.post("/api/events/{eid}/participants/bulk")
async def api_event_invite_bulk(eid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    e = db.get_event(eid)
    tags = _parse_player_tags((payload or {}).get("player_tags"))
    seen, norm = set(), []
    for t in tags:
        nt = db.normalize_tag(t)
        if nt and nt not in seen:
            seen.add(nt); norm.append(nt)
    if not norm:
        return JSONResponse({"error": "Pega al menos un ID de jugador."}, status_code=400)
    cap = e.get("max_participants") or 12
    team_name = ((payload or {}).get("team_name") or "").strip() or None
    team_id = db.create_team(eid, team_name, None, None) if (e["mode"] == "teams" and team_name) else None
    added, dup, no_space = [], 0, 0
    for nt in norm:
        if db.tag_in_event(eid, nt):
            dup += 1; continue
        if db.participant_count(eid) >= cap:
            no_space += 1; continue
        db.add_participant(eid, None, nt, team_id, 1)
        added.append(nt)
    await _ensure_player_profiles(added)  # guarda sus nombres para mostrarlos
    _notify_followers_player_added(eid, e, added)
    return {"ok": True, "added": len(added), "duplicates": dup, "no_space": no_space, "total": len(norm)}


@router.delete("/api/events/{eid}/participants/{pid}")
def api_event_remove_participant(eid: int, pid: int, reason: str = Query(""), user: dict = Depends(auth.require_user)):
    """Quita a un jugador. El organizador DEBE justificarlo; al jugador le llega una
    notificación con el motivo."""
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    reason = (reason or "").strip()
    if not reason:
        return JSONResponse({"error": "Indica el motivo por el que quitas al jugador (se le notificará)."},
                            status_code=400)
    e = db.get_event(eid)
    part = next((p for p in db.list_participants(eid) if p["id"] == pid), None)
    db.remove_participant(eid, pid)
    if part and part.get("user_id"):
        db.notify_many([part["user_id"]], "event_removed",
                       f"Te han quitado de {e.get('name') if e else 'un evento'}",
                       f"El organizador te ha quitado del evento. Motivo: {reason}", event_id=eid)
    return {"ok": True}


def _match_names(m, teams_mode, parts):
    """Nombres de los dos lados de una partida, para el resumen."""
    def nm(tag):
        p = next((x for x in parts if x["player_tag"] == tag), None)
        return (p.get("player_name") if p else None) or tag
    if m.get("roster_a") or m.get("roster_b"):
        return ("Equipo A", "Equipo B")
    if teams_mode:
        return (m.get("a_team_name") or "Equipo A", m.get("b_team_name") or "Equipo B")
    return (m.get("a_name") or m.get("a_tag") or "?", m.get("b_name") or m.get("b_tag") or "?")


def _summary_context(e, matches, standings, parts):
    teams_mode = e.get("mode") == "teams"
    _fmt = {"swiss": "suizo", "mcmahon": "McMahon", "roundrobin": "todos contra todos",
            "single_elim": "eliminación directa", "random_teams": "equipos aleatorios", "free": "libre"}
    L = [f"Evento: «{e.get('name')}» · formato {_fmt.get(e.get('format'), e.get('format'))}."]
    played = [m for m in matches if m.get("status") == "played"]
    if played:
        last = max((m.get("round") or 1) for m in played)
        lr = [m for m in played if (m.get("round") or 1) == last]
        L.append(f"\nResultados de la ronda {last}:")
        for m in lr:
            a, b = _match_names(m, teams_mode, parts)
            if m.get("winner") == "void":
                L.append(f"- {a} vs {b}: no jugado")
            else:
                L.append(f"- {a} {m.get('score_a')}–{m.get('score_b')} {b}")
    if standings:
        L.append("\nClasificación:")
        for i, r in enumerate(standings[:6], 1):
            L.append(f"{i}. {r['name']} — {r['pts']} pts (PJ {r['pj']}, G {r['g']}, E {r['e']}, P {r['p']})")
    return "\n".join(L)


@router.post("/api/events/{eid}/summary")
async def api_event_summary(eid: int, payload: dict = Body(default={}), user: dict = Depends(auth.require_user)):
    """El organizador genera un resumen (una sola llamada a Claude) y se envía como
    notificación idéntica a seguidores, apuntados y a sí mismo (Fase 6)."""
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    if not coach.configured():
        return JSONResponse({"error": "Falta ANTHROPIC_API_KEY para generar el resumen."}, status_code=400)
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    matches = db.list_matches(eid)
    parts = db.list_participants(eid)
    teams = db.list_teams(eid)
    standings = _compute_standings(e, matches, parts, teams)
    if not any(m.get("status") == "played" for m in matches):
        return JSONResponse({"error": "Aún no hay resultados que resumir."}, status_code=400)
    try:
        text = await coach.generate_event_summary(_summary_context(e, matches, standings, parts))
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"error": f"No se pudo generar el resumen: {ex}"}, status_code=502)
    if not text:
        return JSONResponse({"error": "El resumen salió vacío; inténtalo de nuevo."}, status_code=502)
    # destinatarios: seguidores + apuntados + organizador (deduplicados)
    recipients = db.event_follower_ids(eid) + db.event_participant_user_ids(eid) + [user["id"]]
    n = db.notify_many(recipients, "event_summary", f"Resumen · {e.get('name')}", text, event_id=eid)
    return {"ok": True, "sent": n, "text": text}


# --------------------------- Equipos / plantillas (Fase 4) ---------------------------

@router.post("/api/events/{eid}/teams")
def api_team_create(eid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    name = ((payload or {}).get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "Pon un nombre al equipo."}, status_code=400)
    logo = ((payload or {}).get("logo_url") or "").strip() or None
    tid = db.create_team(eid, name, logo, None)
    return {"ok": True, "id": tid}


@router.patch("/api/events/{eid}/teams/{tid}")
def api_team_update(eid: int, tid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    body = payload or {}
    name = (body.get("name") or "").strip() or None
    logo = body.get("logo_url")
    logo = logo.strip() if isinstance(logo, str) and logo.strip() else None
    db.update_team(eid, tid, name, logo)
    return {"ok": True}


@router.delete("/api/events/{eid}/teams/{tid}")
def api_team_delete(eid: int, tid: int, user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    db.delete_team(eid, tid)
    return {"ok": True}


@router.post("/api/events/{eid}/participants/{pid}/team")
def api_participant_set_team(eid: int, pid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    tid = (payload or {}).get("team_id")
    db.set_participant_team(eid, pid, int(tid) if tid else None)
    return {"ok": True}


@router.post("/api/events/upload-image")
def api_event_upload(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    data = (payload or {}).get("data") or ""
    mime = ((payload or {}).get("mime") or "").lower()
    if data.startswith("data:"):
        head, _, b64 = data.partition(",")
        if not mime and ":" in head and ";" in head:
            mime = head[head.index(":") + 1:head.index(";")].lower()
        data = b64
    ext = _IMG_EXT.get(mime)
    if not ext:
        return JSONResponse({"error": "Formato no admitido (usa PNG, JPG, GIF o WEBP)."}, status_code=400)
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "Imagen no válida."}, status_code=400)
    if not raw or len(raw) > 6 * 1024 * 1024:
        return JSONResponse({"error": "Imagen vacía o supera los 6 MB."}, status_code=400)
    os.makedirs(_EVENT_MEDIA_DIR, exist_ok=True)
    name = uuid.uuid4().hex + ext
    with open(os.path.join(_EVENT_MEDIA_DIR, name), "wb") as f:
        f.write(raw)
    return {"ok": True, "url": "/static/media/events/" + name}


@router.post("/api/events/{eid}/matches")
def api_match_create(eid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    body = payload or {}
    try:
        rnd = max(1, int(body.get("round") or 1))
    except Exception:  # noqa: BLE001
        rnd = 1
    mode = (body.get("mode") or "").strip() or None
    mp = (body.get("map") or "").strip() or None
    if e["mode"] == "teams":
        a, b = body.get("a_team"), body.get("b_team")
        if not a or not b or str(a) == str(b):
            return JSONResponse({"error": "Elige dos equipos distintos."}, status_code=400)
        mid = db.create_match(eid, rnd, a_team=int(a), b_team=int(b), mode=mode, map=mp)
    else:
        a = db.normalize_tag(body["a_tag"]) if body.get("a_tag") else None
        b = db.normalize_tag(body["b_tag"]) if body.get("b_tag") else None
        if not a or not b or a == b:
            return JSONResponse({"error": "Elige dos jugadores distintos."}, status_code=400)
        mid = db.create_match(eid, rnd, a_tag=a, b_tag=b, mode=mode, map=mp)
    return {"ok": True, "id": mid}


@router.put("/api/events/{eid}/matches/{mid}")
def api_match_update(eid: int, mid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    m = db.get_match(mid)
    if not m or m["event_id"] != eid:
        return JSONResponse({"error": "Enfrentamiento no encontrado."}, status_code=404)
    body = payload or {}
    fields = {}
    if "round" in body:
        try:
            fields["round"] = max(1, int(body["round"]))
        except Exception:  # noqa: BLE001
            pass
    for c in ("mode", "map"):
        if c in body:
            fields[c] = (body[c] or "").strip() or None
    if body.get("clear_result"):
        fields.update({"status": "pending", "winner": None, "score_a": None, "score_b": None, "evidence_battle_id": None})
    elif "winner" in body or "score_a" in body or "score_b" in body:
        def _toint(x):
            try:
                return int(x) if x not in (None, "") else None
            except Exception:  # noqa: BLE001
                return None
        sa, sb, w = _toint(body.get("score_a")), _toint(body.get("score_b")), body.get("winner")
        if sa is not None and sb is not None and not w:
            w = "a" if sa > sb else ("b" if sb > sa else "draw")
        elif w and sa is None and sb is None:
            sa, sb = (1, 0) if w == "a" else ((0, 1) if w == "b" else (0, 0))
        if w in ("a", "b", "draw"):
            fields.update({"winner": w, "score_a": sa, "score_b": sb, "status": "played"})
        elif w == "void":  # anulado / no jugado → 0 puntos para ambos
            fields.update({"winner": "void", "score_a": 0, "score_b": 0, "status": "played"})
    db.update_match(mid, fields)
    # Eliminación directa: propagar (o retirar) el ganador al hueco siguiente del cuadro
    ev = db.get_event(eid) or {}
    if ev.get("format") == "single_elim":
        teams_mode = ev.get("mode") == "teams"
        m2 = db.get_match(mid)
        if m2 and m2.get("status") == "played" and m2.get("winner") in ("a", "b"):
            _advance_winner(eid, m2, teams_mode)
        else:
            _unadvance(eid, m2 or m, teams_mode)
    return {"ok": True}


@router.post("/api/events/{eid}/matches/close-pending")
def api_close_pending(eid: int, payload: dict = Body(default={}), user: dict = Depends(auth.require_user)):
    """Cierra la ronda: marca como ANULADOS (nulos, 0 puntos) los enfrentamientos pendientes."""
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    n = 0
    for m in db.list_matches(eid):
        if m.get("status") != "played":
            db.update_match(m["id"], {"winner": "void", "score_a": 0, "score_b": 0, "status": "played"})
            n += 1
    return {"ok": True, "voided": n}


@router.post("/api/events/{eid}/matches/detect")
async def api_match_detect(eid: int, payload: dict = Body(default={}), user: dict = Depends(auth.require_user)):
    """Fase 5: cruza las partidas pendientes con los battlelogs amistosos de los
    participantes y propone resultados (el organizador puede editarlos o borrarlos)."""
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    if not brawl_api.TOKEN:
        return JSONResponse({"error": "No hay token de la API de Brawl Stars configurado."}, status_code=400)
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    body = payload or {}
    force = bool(body.get("force"))  # re-detectar también las ya jugadas
    only_mid = body.get("match_id")
    teams_mode = e.get("mode") == "teams"
    best_of = {"bo1": 1, "bo3": 3, "bo5": 5}.get(e.get("match_type") or "bo1", 1)
    start = detect.parse_event_date(e.get("date_start"))
    end = detect.parse_event_date(e.get("date_end"), end=True)

    matches = db.list_matches(eid)
    if only_mid:
        matches = [m for m in matches if m["id"] == only_mid]
    pend = [m for m in matches if (force or m.get("status") != "played")]
    # un evento de eliminación solo tiene cruce cuando ambos lados están definidos
    pend = [m for m in pend if (teams_mode and (m.get("a_team") or m.get("roster_a")) and (m.get("b_team") or m.get("roster_b")))
            or (not teams_mode and m.get("a_tag") and m.get("b_tag"))]
    if not pend:
        return {"ok": True, "detected": 0, "checked": 0, "not_found": 0, "no_token": False}

    # rosters de cada equipo (para eventos por equipos)
    team_players = {}
    if teams_mode:
        for p in db.list_participants(eid):
            if p.get("team_id"):
                team_players.setdefault(p["team_id"], []).append(p["player_tag"])

    def roster_of(m, side):
        ros = m.get("roster_a" if side == "a" else "roster_b")
        if ros:
            return ros
        tid = m.get("a_team" if side == "a" else "b_team")
        return team_players.get(tid, [])

    # tags a consultar (una vez cada uno)
    tags = set()
    for m in pend:
        if teams_mode:
            tags.update(roster_of(m, "a")); tags.update(roster_of(m, "b"))
        else:
            tags.add(m["a_tag"]); tags.add(m["b_tag"])
    tags = [t for t in tags if t]

    async def fetch(tag):
        try:
            return (tag, await _get_battlelog_cached(tag))
        except Exception:  # noqa: BLE001
            return (tag, [])
    logs = await asyncio.gather(*[fetch(t) for t in tags]) if tags else []
    pool = detect.build_pool(logs)

    detected = 0
    for m in pend:
        code = detect.mode_code(m.get("mode"))
        mp = m.get("map")  # mapa concreto del torneo: si está fijado, la partida debe ser en ÉL
        if teams_mode:
            res = detect.match_teams(pool, roster_of(m, "a"), roster_of(m, "b"), code, start, end, best_of, mp)
        else:
            res = detect.match_individual(pool, m["a_tag"], m["b_tag"], code, start, end, best_of, mp)
        if not res:
            continue
        sa, sb, w = res["score_a"], res["score_b"], res["winner"]
        await asyncio.to_thread(db.update_match, m["id"], {
            "winner": w, "score_a": sa, "score_b": sb, "status": "played",
            "evidence_battle_id": res["evidence"],
        })
        detected += 1
        if e.get("format") == "single_elim" and w in ("a", "b"):
            m2 = db.get_match(m["id"])
            if m2:
                _advance_winner(eid, m2, teams_mode)

    return {"ok": True, "detected": detected, "checked": len(pend),
            "not_found": len(pend) - detected, "players": len(tags)}


@router.delete("/api/events/{eid}/matches/{mid}")
def api_match_delete(eid: int, mid: int, user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    db.delete_match(eid, mid)
    return {"ok": True}


def _resolve_round_mm(modes: list, maps: list, ev_mode: str, showdown: str, catalog: list) -> tuple:
    """Elige un (modo, mapa) al azar entre los PERMITIDOS de la ronda. `modes`/`maps` son las
    listas marcadas por el organizador (vacías = cualquiera de las del evento). Se usa cuando el
    modo/mapa no está determinado (0 o varios elegidos) → la selección queda como PROPUESTA.
    Si solo se eligieron MAPAS (sin modo), el MODO lo determina el mapa elegido."""
    allowed = bs_maps.allowed_modes(ev_mode, showdown)
    modes, maps = (modes or []), (maps or [])
    if modes:                                   # el organizador acotó los modos (se respetan tal cual,
        mode = random.choice(modes)             # incluidos Supervivencia dúo/trío)
    elif maps:                                  # solo mapas → el modo lo determina el mapa
        mp0 = random.choice(maps)
        mode = bs_maps.mode_for_map(mp0)
        if mode not in allowed:
            mode = random.choice(allowed) if allowed else None
    else:
        mode = random.choice(allowed) if allowed else None
    mode_maps = next((c["maps"] for c in (catalog or []) if c["name"] == mode), [])
    if maps:
        inter = [x for x in maps if x in mode_maps]
        mp = random.choice(inter) if inter else (random.choice(mode_maps) if mode_maps else None)
    else:
        mp = random.choice(mode_maps) if mode_maps else None
    return (mode, mp)


def _apply_auto_approvals(eid: int, e: dict) -> None:
    """Aprueba automáticamente las propuestas de modo/mapa con más de 24 h sin respuesta.
    Muta `e['settings']` y, si hay cambios, los guarda."""
    settings = e.get("settings") or {}
    rmaps = settings.get("round_maps") or {}
    changed = False
    now = datetime.now(timezone.utc)
    for rn, rk in rmaps.items():
        if not isinstance(rk, dict) or rk.get("status") != "proposed":
            continue
        ts = rk.get("proposed_at")
        if not ts:
            continue
        try:
            when = datetime.fromisoformat(ts)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            continue
        if (now - when).total_seconds() >= 24 * 3600:
            rk["status"] = "confirmed"
            rk["auto_approved"] = True
            changed = True
    if changed:
        settings["round_maps"] = rmaps
        e["settings"] = settings
        db.update_event(eid, {"settings": settings})


def _persist_round_proposals(eid: int):
    """Tras generar, fija en los ajustes el modo/mapa que ha quedado en cada ronda nueva y su
    estado: 'confirmed' si el organizador lo había determinado (1 modo + 1 mapa, o modo fijo),
    o 'proposed' si se eligió al azar (queda pendiente de que el organizador lo confirme)."""
    e = db.get_event(eid)
    if not e:
        return
    settings = e.get("settings") or {}
    rmaps = settings.get("round_maps") or {}
    fixed = settings.get("map_policy") == "fixed"
    by_round: dict = {}
    for m in db.list_matches(eid):
        by_round.setdefault(m.get("round") or 1, []).append(m)
    changed = False
    for rn, ms in by_round.items():
        rk = rmaps.get(str(rn)) or {}
        if rk.get("status"):          # ya resuelto (propuesto o confirmado)
            continue
        mode = next((m.get("mode") for m in ms if m.get("mode")), None)
        mp = next((m.get("map") for m in ms if m.get("map")), None)
        if not mode and not mp:
            continue
        determined = fixed or (len(rk.get("modes") or []) == 1 and len(rk.get("maps") or []) == 1)
        rk["mode"], rk["map"] = mode, mp
        rk["status"] = "confirmed" if determined else "proposed"
        if not determined:  # marca cuándo se propuso, para la auto-aprobación a las 24 h
            rk["proposed_at"] = datetime.now(timezone.utc).isoformat()
        rmaps[str(rn)] = rk
        changed = True
    if changed:
        settings["round_maps"] = rmaps
        db.update_event(eid, {"settings": settings})


@router.post("/api/events/{eid}/rounds/{rn}/confirm")
def api_round_confirm(eid: int, rn: int, user: dict = Depends(auth.require_user)):
    """El organizador confirma la propuesta de modo/mapa de una ronda → queda cerrada."""
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    settings = e.get("settings") or {}
    rmaps = settings.get("round_maps") or {}
    rk = rmaps.get(str(rn)) or {}
    if rk.get("status") != "proposed":
        return JSONResponse({"error": "Esa ronda no tiene una propuesta por confirmar."}, status_code=400)
    rk["status"] = "confirmed"
    rmaps[str(rn)] = rk
    settings["round_maps"] = rmaps
    db.update_event(eid, {"settings": settings})
    return {"ok": True, "round": rn}


@router.post("/api/events/{eid}/rounds/add")
def api_round_add(eid: int, user: dict = Depends(auth.require_user)):
    """Crea una ronda VACÍA (solo aumenta el nº de rondas del torneo) para que el organizador
    elija sus modos/mapas antes de generar los emparejamientos. No genera cruces ni propone nada."""
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    settings = e.get("settings") or {}
    matches = db.list_matches(eid)
    gen = max((m.get("round") or 1) for m in matches) if matches else 0
    count = int(settings.get("round_count") or 0)
    settings["round_count"] = max(count, gen) + 1
    db.update_event(eid, {"settings": settings})
    return {"ok": True, "round_count": settings["round_count"]}


@router.post("/api/events/{eid}/rounds/{rn}/change-request")
def api_round_change_request(eid: int, rn: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    """El organizador pide cambiar el modo/mapa de una ronda YA CONFIRMADA. Debe justificarlo;
    se notifica a los participantes, que pueden aceptar u oponerse (una sola oposición lo bloquea)."""
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    body = payload or {}
    new_mode = (body.get("mode") or "").strip() or None
    new_map = (body.get("map") or "").strip() or None
    reason = (body.get("reason") or "").strip()
    if not new_mode and not new_map:
        return JSONResponse({"error": "Indica el nuevo modo o mapa."}, status_code=400)
    if not reason:
        return JSONResponse({"error": "Justifica el cambio: los jugadores verán el motivo."}, status_code=400)
    settings = e.get("settings") or {}
    rmaps = settings.get("round_maps") or {}
    rk = rmaps.get(str(rn)) or {}
    if rk.get("status") != "confirmed":
        return JSONResponse({"error": "Solo se puede pedir un cambio de una ronda ya confirmada."}, status_code=400)
    rk["change_request"] = {"new_mode": new_mode, "new_map": new_map, "reason": reason,
                            "votes": {}, "status": "pending"}
    rmaps[str(rn)] = rk
    settings["round_maps"] = rmaps
    db.update_event(eid, {"settings": settings})
    voters = [uid for uid in db.event_participant_user_ids(eid) if uid != user["id"]]
    mm = " · ".join([x for x in (new_mode, new_map) if x])
    db.notify_many(voters, "event_map_change", f"Cambio de mapa propuesto · {e.get('name')}",
                   f"Ronda {rn}: se propone {mm}. Motivo: {reason}. Acéptalo u oponte en la ficha del evento.",
                   event_id=eid)
    return {"ok": True, "round": rn, "voters": len(voters)}


@router.post("/api/events/{eid}/rounds/{rn}/change-vote")
def api_round_change_vote(eid: int, rn: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    """Un participante acepta u se opone al cambio propuesto. Una sola oposición lo bloquea;
    si todos aceptan, se aplica el nuevo modo/mapa a la ronda y sus partidas."""
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    vote = (payload or {}).get("vote")
    if vote not in ("accept", "object"):
        return JSONResponse({"error": "Voto no válido."}, status_code=400)
    part_users = set(db.event_participant_user_ids(eid))
    if user["id"] not in part_users:
        return JSONResponse({"error": "Solo los participantes pueden votar."}, status_code=403)
    settings = e.get("settings") or {}
    rmaps = settings.get("round_maps") or {}
    rk = rmaps.get(str(rn)) or {}
    cr = rk.get("change_request")
    if not cr or cr.get("status") != "pending":
        return JSONResponse({"error": "No hay un cambio pendiente en esa ronda."}, status_code=400)
    owner = db.event_owner(eid)
    cr.setdefault("votes", {})[str(user["id"])] = vote
    if vote == "object":
        cr["status"] = "blocked"
        rk["change_request"] = cr; rmaps[str(rn)] = rk; settings["round_maps"] = rmaps
        db.update_event(eid, {"settings": settings})
        db.notify_many([owner], "event_map_change", f"Cambio rechazado · {e.get('name')}",
                       f"Ronda {rn}: un participante se opuso; el modo y el mapa se mantienen.", event_id=eid)
        return {"ok": True, "status": "blocked"}
    voters = [uid for uid in part_users if uid != owner]
    accepted = {int(k) for k, v in cr["votes"].items() if v == "accept"}
    if all(uid in accepted for uid in voters):     # todos han aceptado → aplicar
        rk["mode"] = cr.get("new_mode") or rk.get("mode")
        rk["map"] = cr.get("new_map") or rk.get("map")
        cr["status"] = "applied"
        rk["change_request"] = cr; rmaps[str(rn)] = rk; settings["round_maps"] = rmaps
        db.update_event(eid, {"settings": settings})
        for m in db.list_matches(eid):
            if (m.get("round") or 1) == rn:
                db.update_match(m["id"], {"mode": rk.get("mode"), "map": rk.get("map")})
        db.notify_many(list(part_users), "event_map_change", f"Modo/mapa actualizado · {e.get('name')}",
                       f"Ronda {rn}: ahora se juega {rk.get('mode') or '—'} · {rk.get('map') or '—'}.", event_id=eid)
        return {"ok": True, "status": "applied"}
    rk["change_request"] = cr; rmaps[str(rn)] = rk; settings["round_maps"] = rmaps
    db.update_event(eid, {"settings": settings})
    return {"ok": True, "status": "pending", "accepted": len(accepted), "needed": len(voters)}


@router.post("/api/events/{eid}/matches/generate")
async def api_match_generate(eid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    body = payload or {}
    fmt = e.get("format") or ""
    if fmt not in ("", "roundrobin", "free", "single_elim", "swiss", "mcmahon", "random_teams"):
        return JSONResponse({"error": "El emparejamiento automático para este formato aún no está disponible. Puedes añadir los enfrentamientos a mano."}, status_code=400)
    if body.get("replace"):
        db.clear_matches(eid)
    settings = e.get("settings") or {}
    policy = settings.get("map_policy")
    fm = settings.get("fixed_mode") or None
    fmap = settings.get("fixed_map") or None
    round_maps = settings.get("round_maps") or {}
    ev_mode = e["mode"]
    showdown = settings.get("showdown") or "exclude"
    _mm = {}
    from .. import assets
    _live_cat = bs_maps.catalog_with_live((await assets.get_assets()).get("maps_by_mode"))

    def round_mm(rn):  # modo/mapa de la ronda rn
        rk = round_maps.get(str(rn)) or round_maps.get(rn) or {}
        if rk.get("mode") or rk.get("map"):        # ya determinado/propuesto/confirmado
            return (rk.get("mode") or None, rk.get("map") or None)
        if policy == "fixed" and (fm or fmap):
            return (fm, fmap)
        # No determinado (0 o varios modos/mapas elegidos) → se elige al azar entre los permitidos.
        if rn not in _mm:
            _mm[rn] = _resolve_round_mm(rk.get("modes") or [], rk.get("maps") or [], ev_mode, showdown, _live_cat)
        return _mm[rn]
    teams_mode = e["mode"] == "teams"
    ids = [t["id"] for t in db.list_teams(eid)] if teams_mode else \
          [p["player_tag"] for p in db.list_participants(eid)]
    if len(ids) < 2:
        return JSONResponse({"error": "Hacen falta al menos 2 participantes."}, status_code=400)
    # límite de rondas: en torneos lo marca el nº de rondas creadas con «+» (round_count);
    # en ligas, las vueltas (rounds).
    max_rounds = settings.get("round_count") or settings.get("rounds")
    if fmt in ("swiss", "mcmahon", "random_teams") and max_rounds:
        existing = db.list_matches(eid)
        nextr = (max((m.get("round") or 1) for m in existing) + 1) if existing else 1
        if nextr > int(max_rounds):
            msg = (f"Ya has generado las {int(max_rounds)} rondas creadas. Crea una ronda nueva con «+» "
                   f"(en «Modos y mapas por ronda») para poder generar la siguiente."
                   if settings.get("round_count") else
                   f"El torneo está configurado a {int(max_rounds)} rondas; ya están todas generadas.")
            return JSONResponse({"error": msg}, status_code=400)
    if fmt == "single_elim":
        created = _gen_single_elim(eid, ids, teams_mode, round_mm)
        _persist_round_proposals(eid)
        return {"ok": True, "created": created, "format": "single_elim"}
    if fmt in ("swiss", "mcmahon"):
        if not db.list_matches(eid) and not teams_mode:  # ronda 1: snapshot de copas
            await _snapshot_cups(eid, db.list_participants(eid))
        res = _gen_swiss_round(eid, e, teams_mode, round_mm)
        if "error" in res:
            return JSONResponse({"error": res["error"]}, status_code=400)
        _persist_round_proposals(eid)
        return {"ok": True, "format": fmt, **res}
    if fmt == "random_teams":
        ts = body.get("team_size") or settings.get("team_size") or 3
        res = _gen_random_round(eid, db.list_participants(eid), ts, round_mm)
        if "error" in res:
            return JSONResponse({"error": res["error"]}, status_code=400)
        _persist_round_proposals(eid)
        return {"ok": True, "format": "random_teams", **res}
    try:
        legs = max(1, min(20, int(body.get("legs") or settings.get("rounds") or 1)))
    except Exception:  # noqa: BLE001
        legs = 1
    schedule = _round_robin(ids)
    created, round_no = 0, 0
    for leg in range(legs):
        for jornada in schedule:
            round_no += 1
            rm, rmap = round_mm(round_no)
            for (a, b) in jornada:
                if leg % 2 == 1:
                    a, b = b, a
                if teams_mode:
                    db.create_match(eid, round_no, a_team=a, b_team=b, mode=rm, map=rmap)
                else:
                    db.create_match(eid, round_no, a_tag=a, b_tag=b, mode=rm, map=rmap)
                created += 1
    _persist_round_proposals(eid)
    return {"ok": True, "created": created}


@router.post("/api/events/{eid}/matches/close-round")
def api_match_close_round(eid: int, payload: dict = Body(None), user: dict = Depends(auth.require_user)):
    """Cierra la ronda actual: marca como NO JUGADOS (nulos, 0 pts) los pendientes."""
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    matches = db.list_matches(eid)
    if not matches:
        return {"ok": True, "closed": 0}
    last = max((m.get("round") or 1) for m in matches)
    closed = 0
    for m in matches:
        if (m.get("round") or 1) == last and m.get("status") != "played":
            db.update_match(m["id"], {"winner": "void", "score_a": 0, "score_b": 0, "status": "played"})
            closed += 1
    return {"ok": True, "closed": closed, "round": last}


@router.put("/api/events/{eid}/rounds/{rn}/mode-map")
def api_round_mode_map(eid: int, rn: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    """Fija el modo y el mapa de una ronda; se aplica a TODAS sus partidas y se guarda
    para las rondas aún no generadas."""
    if db.event_owner(eid) != user["id"]:
        raise HTTPException(status_code=403, detail="No eres el organizador.")
    e = db.get_event(eid)
    if not e:
        return JSONResponse({"error": "No existe ese evento."}, status_code=404)
    body = payload or {}
    settings = e.get("settings") or {}
    rmaps = settings.get("round_maps") or {}
    rk = rmaps.get(str(rn)) or {}
    round_matches = [m for m in db.list_matches(eid) if (m.get("round") or 1) == rn]
    # Una ronda ya generada tiene la selección CERRADA (solo se cambia con confirmación/petición).
    if round_matches and rk.get("status") in ("proposed", "confirmed"):
        return JSONResponse({"error": "La ronda ya está generada; su modo y mapa están cerrados."},
                            status_code=400)
    # Listas de PERMITIDOS (checkbox) para cuando se genere la ronda.
    modes = [str(x).strip() for x in (body.get("modes") or []) if str(x).strip()]
    maps = [str(x).strip() for x in (body.get("maps") or []) if str(x).strip()]
    rk["modes"], rk["maps"] = modes, maps
    # Si el organizador determina EXACTAMENTE un modo y un mapa, queda fijado (confirmado).
    mode = (body.get("mode") or (modes[0] if len(modes) == 1 else "") or "").strip() or None
    mp = (body.get("map") or (maps[0] if len(maps) == 1 else "") or "").strip() or None
    if len(modes) == 1 and len(maps) == 1:
        rk["mode"], rk["map"], rk["status"] = mode, mp, "confirmed"
    else:
        rk.pop("mode", None); rk.pop("map", None); rk.pop("status", None)
    rmaps[str(rn)] = rk
    settings["round_maps"] = rmaps
    db.update_event(eid, {"settings": settings})
    updated = 0
    for m in round_matches:  # aplicar a las partidas ya generadas de esa ronda (si las hay)
        db.update_match(m["id"], {"mode": rk.get("mode"), "map": rk.get("map")})
        updated += 1
    return {"ok": True, "round": rn, "updated": updated}
