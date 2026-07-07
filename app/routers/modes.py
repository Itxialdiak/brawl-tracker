"""Rutas de Modos de Juego: rotación (Copas/Competitivo), Hub de Modos y detalle de mapa.

Extraído de main.py; se incluye con app.include_router()."""
import os
import re
import json
import time
import asyncio
from datetime import datetime
from fastapi import APIRouter, Query, Depends
from fastapi.responses import JSONResponse
from .. import db, brawl_api, assets, brawler_extra, auth
from ..api_common import _require_follow

router = APIRouter()


_RANKED_MODES = {"gemGrab", "brawlBall", "heist", "knockout", "hotZone", "bounty"}


def _mnorm(s: str) -> str:
    """Clave de modo/mapa normalizada: minúsculas, solo alfanumérico."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _slot_hours(start: str | None, end: str | None) -> float:
    try:
        f = "%Y%m%dT%H%M%S"
        return (datetime.strptime(end[:15], f) - datetime.strptime(start[:15], f)).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        return 0.0


_rotation_cache = {"at": 0.0, "data": None}
_ranked_pool_cache = {"data": None, "mtime": None}


def _ranked_pool() -> dict:
    """Pool COMPETITIVO curado (data/ranked_maps.json): {modo_norm: {mapa_norm: 'Mapa EN'}}.
    Editable a mano; cacheado por mtime. Si está vacío, el llamador cae al feed en vivo."""
    path = os.path.join(os.path.dirname(__file__), "..", "data", "ranked_maps.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _ranked_pool_cache["data"] is None or mtime != _ranked_pool_cache["mtime"]:
        pool = {}
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            for mode, maps in raw.items():
                if mode.startswith("_") or not isinstance(maps, list):
                    continue
                for mp in maps:
                    if mp:
                        pool.setdefault(_mnorm(mode), {})[_mnorm(mp)] = mp
        except Exception:  # noqa: BLE001
            pool = _ranked_pool_cache["data"] or {}
        _ranked_pool_cache["data"] = pool
        _ranked_pool_cache["mtime"] = mtime
    return _ranked_pool_cache["data"] or {}
_mode_guide_cache = {"data": None, "mtime": None}


def _mode_guide() -> dict:
    """Guía estática por modo (data/mode_guide.json): intro, objetivo, consejos."""
    path = os.path.join(os.path.dirname(__file__), "data", "mode_guide.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _mode_guide_cache["data"] is None or mtime != _mode_guide_cache["mtime"]:
        try:
            with open(path, encoding="utf-8") as f:
                _mode_guide_cache["data"] = json.load(f)
            _mode_guide_cache["mtime"] = mtime
        except Exception:  # noqa: BLE001
            return _mode_guide_cache["data"] or {}
    return _mode_guide_cache["data"] or {}


def _brawler_title(name: str | None) -> str:
    return (name or "").title()


def _role_insight(your_roles: list, comm_roles: list, mode_es: str):
    """Frase de desviación por ROL: compara tu rendimiento/uso en el rol que mejor le
    funciona a la comunidad en este modo."""
    if not comm_roles or not your_roles:
        return None
    cr = sorted([r for r in comm_roles if (r.get("total") or 0) >= 5 and r.get("winrate") is not None],
                key=lambda r: r["winrate"], reverse=True)
    if not cr:
        return None
    top = cr[0]
    mine = {r["label"]: r for r in your_roles}.get(top["label"])
    if not mine or (mine.get("total") or 0) < 3:
        return (f"La comunidad rinde muy bien con el rol {top['label']} en {mode_es} "
                f"({top['winrate']}% WR) y tú apenas lo juegas.")
    yw = mine.get("winrate")
    if yw is not None:
        d = round(yw - top["winrate"], 1)
        if d <= -5:
            return (f"Con el rol {top['label']} en {mode_es} rindes {abs(d)} puntos por debajo de "
                    f"la comunidad ({yw}% vs {top['winrate']}%).")
        if d >= 5:
            return (f"Con el rol {top['label']} en {mode_es} rindes {d} puntos por encima de "
                    f"la comunidad ({yw}% vs {top['winrate']}%).")
    return None


def _mode_insights(your_ov: dict, your_brawlers: list, comm: dict, mode_es: str,
                   your_roles: list = None, comm_roles: list = None) -> list:
    """Frases de desviación frente a la media de BrawlSensei (lo que engancha)."""
    out = []
    yw, cw, yt = your_ov.get("winrate"), comm.get("winrate"), your_ov.get("total") or 0
    if yw is not None and cw is not None and yt >= 3:
        diff = round(yw - cw, 1)
        if abs(diff) >= 2:
            ud = "por encima" if diff > 0 else "por debajo"
            out.append(f"Tu win rate en {mode_es} ({yw}%) está {abs(diff)} puntos {ud} de la media de BrawlSensei ({cw}%).")
        else:
            out.append(f"Tu win rate en {mode_es} ({yw}%) va parejo a la media de BrawlSensei ({cw}%).")
    your_names = {b["label"].upper() for b in your_brawlers}
    comm_brawlers = comm.get("brawlers", [])
    for cb in comm_brawlers[:8]:
        if cb["pick_rate"] >= 6 and cb["brawler"].upper() not in your_names:
            out.append(f"La comunidad pickea mucho a {_brawler_title(cb['brawler'])} en {mode_es} (pick {cb['pick_rate']}%) y tú no lo tocas.")
            break
    comm_by = {b["brawler"].upper(): b for b in comm_brawlers}
    best = None
    for yb in your_brawlers:
        if (yb.get("total") or 0) >= 3 and yb.get("winrate") is not None:
            cb = comm_by.get(yb["label"].upper())
            if cb and cb["winrate"] is not None and cb["games"] >= 5:
                d = round(yb["winrate"] - cb["winrate"], 1)
                if d >= 8 and (best is None or d > best[1]):
                    best = (yb, d, cb)
    if best:
        yb, d, cb = best
        out.append(f"Con {_brawler_title(yb['label'])} rindes {d} puntos sobre la media ({yb['winrate']}% vs {cb['winrate']}%): es de tus armas en {mode_es}.")
    ri = _role_insight(your_roles, comm_roles, mode_es)
    if ri:
        out.append(ri)
    return out[:4]


def _draft_helper(your_brawlers: list, comm: dict) -> list:
    """Mejores brawlers para un mapa cruzando el meta comunitario con tu win rate."""
    yb = {b["label"].upper(): b for b in your_brawlers}
    out = []
    for cb in comm.get("brawlers", []):
        if cb["games"] < 3:
            continue
        comm_wr = cb["winrate"] if cb["winrate"] is not None else 50.0
        you = yb.get(cb["brawler"].upper())
        your_wr = you["winrate"] if (you and you.get("winrate") is not None and (you.get("total") or 0) >= 2) else None
        score = comm_wr + cb["pick_rate"] * 0.25
        if your_wr is not None:
            score = score * 0.6 + your_wr * 0.4
        out.append({"brawler": cb["brawler"], "community_winrate": comm_wr,
                    "your_winrate": your_wr, "pick_rate": cb["pick_rate"], "_s": score})
    out.sort(key=lambda x: x["_s"], reverse=True)
    for o in out:
        o.pop("_s", None)
    return out[:6]


def _map_tips(draft: list, guide: dict) -> list:
    from collections import Counter
    tips, roles = [], []
    for d in draft[:4]:
        roles += brawler_extra.roles_of(d["brawler"])
    top = [r for r, _ in Counter(roles).most_common(3)]
    if top:
        tips.append(f"Composición recomendada: prioriza {', '.join(top)} — es lo que mejor combina aquí según el meta y tu historial.")
    if guide.get("map_tip"):
        tips.append(guide["map_tip"])
    return tips


async def _get_rotation() -> list:
    """Rotación actual cacheada (10 min) con categoría copas/competitivo por evento.

    El pool COMPETITIVO se decide así (estable):
      1) Si `data/ranked_maps.json` tiene mapas (pool curado), MANDA: un evento es 'ranked'
         solo si su (modo, mapa) está en el pool. Además se inyectan los mapas del pool que
         hoy NO están en la rotación de trofeos, para mostrar el pool completo.
      2) Si el pool está vacío, se cae al FEED en vivo: 'ranked' = modo competitivo con un
         slot claramente largo (heurística de respaldo)."""
    now = time.time()
    if _rotation_cache["data"] is None or now - _rotation_cache["at"] > 600:
        raw = await brawl_api.get_events_rotation()
        pool = _ranked_pool()
        events, seen_ranked = [], set()
        for it in (raw or []):
            evt = it.get("event") or {}
            map_ = evt.get("map") or it.get("map")
            if not map_:
                continue
            mode = evt.get("mode") or it.get("mode")
            start, end = it.get("startTime"), it.get("endTime")
            if pool:  # pool curado manda
                ranked = _mnorm(map_) in pool.get(_mnorm(mode), {})
            else:      # respaldo: heurística del feed en vivo
                ranked = mode in _RANKED_MODES and _slot_hours(start, end) >= 36
            if ranked:
                seen_ranked.add((_mnorm(mode), _mnorm(map_)))
            events.append({"mode": mode, "map": map_, "startTime": start, "endTime": end,
                           "category": "ranked" if ranked else "trophy"})
        # Inyecta el resto del pool curado que hoy no da trofeos (para ver el pool completo).
        for mode_norm, maps in pool.items():
            for map_norm, map_en in maps.items():
                if (mode_norm, map_norm) not in seen_ranked:
                    events.append({"mode": mode_norm, "map": map_en, "startTime": None,
                                   "endTime": None, "category": "ranked"})
        _rotation_cache.update(at=now, data=events)
    return _rotation_cache["data"]


@router.get("/api/rotation")
async def api_rotation(player: str = Query(None), user: dict = Depends(auth.require_user)):
    """'Qué jugar ahora': rotación actual cruzada con tu win rate por mapa y tus mejores brawlers."""
    tag = _require_follow(user, player)
    if not brawl_api.TOKEN:
        return JSONResponse({"error": "Falta BRAWL_API_TOKEN en .env"}, status_code=400)
    try:
        events = await _get_rotation()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"No se pudo leer la rotación: {e}"}, status_code=502)
    analysis = await asyncio.to_thread(db.rotation_analysis, tag, events)
    return {"events": analysis}


@router.get("/api/mode-hub")
async def api_mode_hub(player: str = Query(None), mode: str = Query(...),
                       user: dict = Depends(auth.require_user)):
    """Hub de un modo: tus stats, meta comunitario (BrawlSensei), tu desviación
    frente a la media y los mapas del modo (en rotación + otros)."""
    tag = _require_follow(user, player)
    f = {"player": tag, "mode": mode}
    your_ov = await asyncio.to_thread(db.overview, f)
    your_brawlers = await asyncio.to_thread(db.winrate_by, "brawler", f)
    your_maps = await asyncio.to_thread(db.winrate_by, "map", f)
    your_series = await asyncio.to_thread(db.trophy_series, f)
    comm = await asyncio.to_thread(db.community_meta, mode)
    # Roles: tuyos y de la comunidad para este modo (agregados sobre TODOS los mapas del modo),
    # y el mejor rol por mapa (para ponerlo en cada tarjeta).
    your_roles = await asyncio.to_thread(db.winrate_by_role, f)
    comm_roles = await asyncio.to_thread(db.winrate_by_role, {"mode": mode})
    roles_by_map = await asyncio.to_thread(db.role_winrates_by_map, mode)
    guide = _mode_guide().get(mode) or {}
    mode_es = guide.get("name_es") or mode
    insights = _mode_insights(your_ov, your_brawlers, comm, mode_es, your_roles, comm_roles)

    cat = await assets.get_map_catalog()
    mode_maps = cat["by_mode"].get(assets.norm_mode(mode), [])
    try:
        rotation = await _get_rotation()
    except Exception:  # noqa: BLE001
        rotation = []
    rot_cat = {ev["map"].lower(): ev["category"] for ev in rotation
               if assets.norm_mode(ev["mode"]) == assets.norm_mode(mode)}
    your_by_map = {m["label"].lower(): m for m in your_maps}

    def card(name, image, active, category):
        ym = your_by_map.get(name.lower())
        rm = roles_by_map.get(name.lower(), [])
        return {"name": name, "image": image, "active": active, "category": category,
                "your_winrate": ym["winrate"] if ym else None,
                "your_games": ym["total"] if ym else 0,
                "top_role": rm[0] if rm else None}  # mejor rol comunitario en ESTE mapa

    in_rotation, others, seen = [], [], set()
    for e in mode_maps:
        seen.add(e["name"].lower())
        c = rot_cat.get(e["name"].lower())
        (in_rotation if c else others).append(card(e["name"], e["image"], e["active"], c))
    for mname, c in rot_cat.items():            # en rotación pero no en el catálogo
        if mname not in seen:
            ym = your_by_map.get(mname)
            in_rotation.append(card(ym["label"] if ym else mname, None, True, c))

    by_pick = comm["brawlers"][:8]
    by_wr = sorted([b for b in comm["brawlers"] if b["games"] >= 3],
                   key=lambda x: (x["winrate"] or 0), reverse=True)[:8]
    return {
        "mode": mode, "mode_es": mode_es,
        "your": {"winrate": your_ov.get("winrate"), "total": your_ov.get("total"),
                 "trophy_delta": your_ov.get("trophy_delta"),
                 "trophy_series": your_series, "best_brawlers": your_brawlers[:6]},
        "community": {"winrate": comm["winrate"], "total": comm["total"],
                      "by_pick": by_pick, "by_winrate": by_wr},
        "roles": {"community": _slim_roles(comm_roles), "your": _slim_roles(your_roles)},
        "insights": insights, "guide": guide,
        "maps": {"rotation": in_rotation, "others": others},
    }


def _slim_roles(roles: list) -> list:
    """Top 5 roles por win rate (con >=3 partidas para fiabilidad), con su uso."""
    rel = [r for r in roles if (r.get("total") or 0) >= 3]
    rel.sort(key=lambda r: ((r["winrate"] if r.get("winrate") is not None else -1), r["total"]), reverse=True)
    return [{"role": r["label"], "winrate": r["winrate"], "usage_pct": r.get("usage_pct"),
             "total": r["total"]} for r in rel[:5]]


@router.get("/api/map-detail")
async def api_map_detail(player: str = Query(None), map: str = Query(...),
                         mode: str = Query(None), user: dict = Depends(auth.require_user)):
    """Ficha de un mapa: tu win rate, mejores brawlers/aliados, peores rivales,
    ayudante de draft (tus datos + meta comunitario) y consejos."""
    tag = _require_follow(user, player)
    f = {"player": tag, "map": map}
    if mode:
        f["mode"] = mode
    your_ov = await asyncio.to_thread(db.overview, f)
    your_brawlers = await asyncio.to_thread(db.winrate_by, "brawler", f)
    your_allies = await asyncio.to_thread(db.winrate_with_allies, f)
    your_enemies = await asyncio.to_thread(db.winrate_vs, f)
    comm = await asyncio.to_thread(db.community_meta, mode, map)
    # Roles ESPECÍFICOS DE ESTE MAPA: tuyos y de la comunidad (no del modo en general).
    comm_role_f = {"map": map}
    if mode:
        comm_role_f["mode"] = mode
    your_roles = await asyncio.to_thread(db.winrate_by_role, f)
    comm_roles = await asyncio.to_thread(db.winrate_by_role, comm_role_f)
    cat = await assets.get_map_catalog()
    entry = cat["by_name"].get(map.lower()) or {}
    draft = _draft_helper(your_brawlers, comm)
    guide = _mode_guide().get(mode) or {}
    worst = sorted([e for e in your_enemies if e.get("winrate") is not None],
                   key=lambda x: x["winrate"])[:5]
    return {
        "map": map, "mode": mode, "image": entry.get("image"), "active": entry.get("active"),
        "your": {"winrate": your_ov.get("winrate"), "total": your_ov.get("total"),
                 "best_brawlers": your_brawlers[:6],
                 "best_allies": [a for a in your_allies if (a.get("total") or 0) >= 1][:5],
                 "worst_enemies": worst},
        "community": {"by_pick": comm["brawlers"][:6],
                      "by_winrate": sorted([b for b in comm["brawlers"] if b["games"] >= 3],
                                           key=lambda x: (x["winrate"] or 0), reverse=True)[:6]},
        "roles": {"community": _slim_roles(comm_roles), "your": _slim_roles(your_roles)},
        "draft": draft, "tips": _map_tips(draft, guide),
    }
