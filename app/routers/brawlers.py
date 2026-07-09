"""Rutas de apartado Brawlers: rejilla, rating de cuenta y ficha de detalle.

Extraído de main.py; se incluye con app.include_router()."""
import asyncio
import re
import time
from fastapi import APIRouter, Query, Body, Depends
from fastapi.responses import JSONResponse
from .. import db, assets, brawler_extra, auth, buffs, changes, upcoming
from ..api_common import _require_follow, _get_player_cached

router = APIRouter()

_avail_cache = {"ids": None, "at": 0.0}


async def available_brawler_ids() -> set:
    """IDs de brawlers YA DISPONIBLES (lanzados y conseguibles en el juego). OJO: ni el catálogo
    de BrawlAPI ni la lista OFICIAL de Supercell sirven para esto — ambos añaden los brawlers
    ANUNCIADOS antes de su lanzamiento (p. ej. Wendy ya aparece en la API oficial aunque sale el
    mes que viene). El único signo fiable de "ya se puede conseguir" es que algún jugador trackeado
    lo POSEA (está en alguna colección) — más el override manual del admin. Cacheado 10 min."""
    now = time.time()
    if _avail_cache["ids"] is not None and now - _avail_cache["at"] < 600:
        return _avail_cache["ids"]
    ids = set()
    try:
        ids |= await asyncio.to_thread(db.owned_brawler_ids)
    except Exception:  # noqa: BLE001
        pass
    try:
        ids |= await asyncio.to_thread(db.brawler_available_overrides)
    except Exception:  # noqa: BLE001
        pass
    _avail_cache["ids"] = ids
    _avail_cache["at"] = now
    return ids


def _invalidate_available_cache() -> None:
    _avail_cache["ids"] = None


@router.post("/api/admin/brawler-available")
async def api_mark_brawler_available(payload: dict = Body(...), user: dict = Depends(auth.require_admin)):
    """ADMIN: marca un brawler como DISPONIBLE (lo saca de 'Próximos' y lo mete en la lista común
    con los demás, para que cuente en colección/analíticas), por si la app no lo detectó sola.
    Acepta {id} o {name}. Primero conviene dejar que la detección automática lo intente."""
    p = payload or {}
    bid = p.get("id")
    if bid is None and p.get("name"):
        cat = await assets.get_brawler_catalog()
        nm = str(p["name"]).upper()
        bid = next((k for k, v in (cat.get("by_id") or {}).items()
                    if (v.get("name") or "").upper() == nm), None)
    if bid is None:
        return JSONResponse({"error": "Ese brawler aún no está en el catálogo (ni en BrawlAPI)."},
                            status_code=404)
    await asyncio.to_thread(db.set_brawler_available, int(bid), True)
    _invalidate_available_cache()
    return {"ok": True, "id": int(bid)}


# --------------------------- Brawlers (apartado tipo Brawlify) ---------------------------

def _rank_band(trophies) -> str:
    """Banda de rango por trofeos del brawler (icono en el frontend)."""
    t = trophies or 0
    if t >= 3000: return "p3"
    if t >= 2000: return "p2"
    if t >= 1000: return "p1"
    if t >= 750:  return "gold"
    if t >= 500:  return "silver"
    if t >= 250:  return "bronze"
    return "wood"


_NO_HYPERCHARGE = {"STARR NOVA", "BOLT"}  # los únicos brawlers sin hipercarga ahora mismo


def _dedup_by_id(items):
    """Quita duplicados por id (BrawlAPI lista a veces el mismo gadget/star power dos veces)."""
    seen, out = set(), []
    for it in (items or []):
        if it.get("id") in seen:
            continue
        seen.add(it.get("id")); out.append(it)
    return out


def _norm_name(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _clean_abilities(cat_items, es_list):
    """BrawlAPI lista a veces gadgets/star powers VIEJOS de brawlers reworkeados (p. ej.
    Bolt, con Rocket Laces/Fuel ya retirados) junto a los actuales. Si la lista curada
    (es_list) fija los `id` de los gadgets/star powers actuales, mostramos SOLO esos en su
    orden; si no, quitamos posibles duplicados traducidos y devolvemos el catálogo dedup."""
    cat = _dedup_by_id(cat_items)
    es_list = es_list or []
    if not es_list:
        return cat
    ids = [e.get("id") for e in es_list if e.get("id")]
    if ids and len(ids) == len(es_list):           # el dataset fija los actuales por id
        by_id = {it.get("id"): it for it in cat}
        picked = [by_id[i] for i in ids if i in by_id]
        if len(picked) == len(ids):
            return picked
    # Sin ids fiables: si el catálogo trae MÁS que la lista curada, son gadgets/estelares
    # RETIRADOS de un rework (BrawlAPI los lista primero, por id más bajo). Nos quedamos con
    # los ÚLTIMOS N (los actuales), que es lo que la wiki recoge en `es_list`.
    if len(cat) > len(es_list):
        return cat[-len(es_list):]
    return cat


@router.get("/api/brawlers")
async def api_brawlers(player: str = Query(None), user: dict = Depends(auth.require_user)):
    """Rejilla del apartado Brawlers: contadores, rating y todos los brawlers con tu
    colección fusionada (nivel, rank, loadout poseído y tu win rate)."""
    tag = _require_follow(user, player)
    catalog = await assets.get_brawler_catalog()
    by_id = catalog.get("by_id") or {}
    totals = catalog.get("totals") or {}
    # Perfil (cacheado 120 s): stats de cuenta + refresco de la colección al abrir.
    account = {}
    try:
        prof = await _get_player_cached(tag)
        if prof.get("brawlers"):
            await asyncio.to_thread(db.snapshot_brawlers, tag, prof.get("brawlers"))
        account = {"trophies": prof.get("trophies"), "highest_trophies": prof.get("highestTrophies"),
                   "victories_3v3": prof.get("3vs3Victories"), "victories_solo": prof.get("soloVictories"),
                   "victories_duo": prof.get("duoVictories"), "exp_level": prof.get("expLevel")}
    except Exception as e:  # noqa: BLE001
        print(f"[brawlers] no se pudo leer el perfil de {tag}: {e}")
    collection = await asyncio.to_thread(db.get_collection, tag)
    coll_by_id = {c["brawler_id"]: c for c in collection}
    wr = await asyncio.to_thread(db.winrate_by, "brawler", {"player": tag})
    wr_by_name = {(r["label"] or "").upper(): r for r in wr}
    hc_ids = brawler_extra.hypercharge_ids()
    await buffs.get_buffs()                                     # calienta la caché (no bloquea)
    bchanges = buffs.changes_map()                              # cambios vigentes por brawler

    # Disponibilidad: BrawlAPI ya lista brawlers ANUNCIADOS (Nori, Wendy) antes de salir. Los que
    # están en el dataset de "próximos" y aún NO se han lanzado (no disponibles) se EXCLUYEN de la
    # rejilla/colección/meta; en cuanto se detectan disponibles, entran como uno normal.
    avail = await available_brawler_ids()
    upcoming_all = upcoming.list_all()
    up_names = {str(e["name"]).upper() for e in upcoming_all}

    items = []
    temporary = []
    n_unreleased = 0
    for bid, cat in by_id.items():
        c = coll_by_id.get(bid)
        name = cat.get("name")
        if (name or "").upper() in up_names and bid not in avail:
            n_unreleased += 1
            continue                                           # próximo aún NO lanzado: fuera de la lista

        w = wr_by_name.get((name or "").upper())
        owned_sp = set(c["star_power_ids"]) if c else set()
        owned_gd = set(c["gadget_ids"]) if c else set()
        ex = brawler_extra.get(bid)
        sps = _clean_abilities(cat.get("star_powers"), ex.get("star_powers_es"))
        gds = _clean_abilities(cat.get("gadgets"), ex.get("gadgets_es"))
        role = brawler_extra.norm_role(ex.get("role") or brawler_extra.role_primary_fallback(name) or cat.get("role"))
        item = {
            "id": bid, "name": name, "role": role,
            "role_secondary": brawler_extra.role_secondary(name),
            "hypercharge_icon": (ex.get("hypercharge") or {}).get("icon"),
            "rarity": cat.get("rarity"),
            "portrait": cat.get("portrait"),
            "owned": c is not None,
            "power": c["power"] if c else None,
            "rank": c["rank"] if c else None,
            "trophies": c["trophies"] if c else None,
            "rank_band": _rank_band(c["trophies"]) if c else None,
            "prestige": c.get("prestige_level") if c else None,
            "star_powers": [{"icon": s.get("icon"), "owned": s.get("id") in owned_sp} for s in sps],
            "gadgets": [{"icon": g.get("icon"), "owned": g.get("id") in owned_gd} for g in gds],
            "owned_star_powers": len(owned_sp),
            "total_star_powers": len(sps),
            "owned_gadgets": len(owned_gd),
            "total_gadgets": len(gds),
            "has_hypercharge": (name or "").upper() not in _NO_HYPERCHARGE,
            "owns_hypercharge": bool(c and c.get("hypercharge_ids")),
            "your_winrate": w["winrate"] if w else None,
            "your_adj": w["adj_score"] if w else None,
            "your_reliability": w["reliability"] if w else None,
            "your_battles": w["total"] if w else 0,
            "change": bchanges.get((name or "").upper()),
        }
        if brawler_extra.is_temporary(bid, name):    # colab temporal: a su apartado aparte
            item["temporary"] = True
            temporary.append(item)
        else:
            items.append(item)

    # Top 13 por trofeos; los 3 del podio con imagen a cuerpo entero de la skin equipada.
    # Se reutiliza para el Top 13 general Y para el top de cada rol (filtro del podio).
    from .. import skins

    async def _full_image(it):
        """Imagen de cuerpo entero de la ficha: skin equipada si la hay, o cuerpo entero."""
        ex = brawler_extra.get(it["id"])
        image_full = ex.get("body_image") or (by_id.get(it["id"]) or {}).get("image_full")
        c = coll_by_id.get(it["id"])
        if c and c.get("skin_id") and c.get("skin_name"):
            try:
                skin_url = skins.get_image(c["skin_id"]) or await skins.resolve_and_cache(c["skin_id"], it["name"], c["skin_name"])
                if skin_url:
                    image_full = skin_url
            except Exception:  # noqa: BLE001
                pass
        return image_full

    async def _podium(subset):
        subset = sorted(subset, key=lambda x: x["trophies"], reverse=True)[:13]
        out = []
        for pos, it in enumerate(subset):
            tb = {"id": it["id"], "name": it["name"], "trophies": it["trophies"],
                  "portrait": it["portrait"], "rarity": it["rarity"], "rank_band": it["rank_band"],
                  "your_winrate": it["your_winrate"], "your_battles": it["your_battles"],
                  "your_adj": it["your_adj"], "your_reliability": it["your_reliability"]}
            if pos < 3:  # solo el podio necesita la imagen grande (evita resolver skins de más)
                tb["image_full"] = await _full_image(it)
            out.append(tb)
        return out

    owned_items = [it for it in items if it["owned"] and it["trophies"] is not None]
    top_brawlers = await _podium(owned_items)
    # Top 13 por ROL (primario o secundario) para el filtro del podio.
    roles_present = sorted({r for it in owned_items for r in (it["role"], it.get("role_secondary")) if r})
    top_by_role = {}
    for role in roles_present:
        subset = [it for it in owned_items if it["role"] == role or it.get("role_secondary") == role]
        top_by_role[role] = await _podium(subset)

    counts = await asyncio.to_thread(db.collection_counts, tag)
    n_temp = len(temporary)                        # los temporales no cuentan en la colección
    owned_temp = sum(1 for t in temporary if t["owned"])
    # El total de la colección excluye temporales Y próximos no lanzados (no se pueden conseguir).
    total_brawlers = max(0, (totals.get("brawlers") or len(by_id)) - n_temp - n_unreleased)
    rating = await asyncio.to_thread(
        db.account_rating, tag,
        {**totals, "brawlers": total_brawlers, "hypercharges": brawler_extra.hypercharge_total()})
    counters = {
        "brawlers": {"owned": max(0, counts["brawlers"] - owned_temp), "total": total_brawlers},
        "star_powers": {"owned": counts["star_powers_owned"], "total": totals.get("star_powers") or 0},
        "gadgets": {"owned": counts["gadgets_owned"], "total": totals.get("gadgets") or 0},
        "hypercharges": {"owned": counts["hypercharges_owned"],
                         "total": max(0, total_brawlers - len(_NO_HYPERCHARGE))},
    }
    # "Próximos": solo los que aún NO están disponibles; los ya lanzados salen de aquí (pasan a la lista).
    name_to_id = {(c.get("name") or "").upper(): bid for bid, c in by_id.items()}
    upcoming_shown = [e for e in upcoming_all
                      if name_to_id.get(str(e["name"]).upper()) not in avail]
    return {"counters": counters, "rating": rating, "account": account,
            "brawlers": items, "temporary": temporary, "top_brawlers": top_brawlers,
            "top_by_role": top_by_role, "upcoming": upcoming_shown}


async def _brawler_podium_payload(tag: str) -> dict:
    """Top 13 brawlers de un jugador (general + por rol), versión LIGERA (sin contadores, rating
    ni loadouts): solo lo que el podio necesita. Reutilizable en el perfil PÚBLICO (sin cuenta)."""
    from .. import skins
    catalog = await assets.get_brawler_catalog()
    by_id = catalog.get("by_id") or {}
    collection = await asyncio.to_thread(db.get_collection, tag)
    coll_by_id = {c["brawler_id"]: c for c in collection}
    wr = await asyncio.to_thread(db.winrate_by, "brawler", {"player": tag})
    wr_by_name = {(r["label"] or "").upper(): r for r in wr}
    items = []
    for bid, cat in by_id.items():
        c = coll_by_id.get(bid)
        if not c or c.get("trophies") is None:
            continue                                   # solo brawlers de la colección
        name = cat.get("name")
        if brawler_extra.is_temporary(bid, name):
            continue
        ex = brawler_extra.get(bid)
        w = wr_by_name.get((name or "").upper())
        role = brawler_extra.norm_role(ex.get("role") or brawler_extra.role_primary_fallback(name) or cat.get("role"))
        items.append({
            "id": bid, "name": name, "role": role,
            "role_secondary": brawler_extra.role_secondary(name),
            "trophies": c["trophies"], "portrait": cat.get("portrait"),
            "rarity": cat.get("rarity"), "rank_band": _rank_band(c["trophies"]),
            "your_winrate": w["winrate"] if w else None,
            "your_adj": w["adj_score"] if w else None,
            "your_reliability": w["reliability"] if w else None,
            "your_battles": w["total"] if w else 0,
            "_full": ex.get("body_image") or cat.get("image_full"),
            "_skin": (c.get("skin_id"), c.get("skin_name")),
        })

    async def podium(subset):
        subset = sorted(subset, key=lambda x: x["trophies"], reverse=True)[:13]
        out = []
        for pos, it in enumerate(subset):
            tb = {k: it[k] for k in ("id", "name", "trophies", "portrait", "rarity", "rank_band",
                                     "your_winrate", "your_battles", "your_adj", "your_reliability")}
            if pos < 3:                                # solo el podio necesita cuerpo entero
                img = it["_full"]
                sid, _sn = it["_skin"]
                if sid:
                    try:
                        img = skins.get_image(sid) or img
                    except Exception:  # noqa: BLE001
                        pass
                tb["image_full"] = img
            out.append(tb)
        return out

    top_brawlers = await podium(items)
    roles_present = sorted({r for it in items for r in (it["role"], it.get("role_secondary")) if r})
    top_by_role = {role: await podium([it for it in items if it["role"] == role or it.get("role_secondary") == role])
                   for role in roles_present}
    return {"top_brawlers": top_brawlers, "top_by_role": top_by_role}


@router.get("/api/public/players/{tag}/brawlers-top")
async def api_public_brawlers_top(tag: str):
    """Top 13 brawlers (podio) de un jugador, PÚBLICO (sin cuenta): para el perfil público."""
    return await _brawler_podium_payload(db.normalize_tag(tag))


@router.get("/api/versatile")
async def api_versatile(player: str = Query(None), mode: str = Query(None), map: str = Query(None),
                        brawler: str = Query(None), role: str = Query(None),
                        user: dict = Depends(auth.require_user)):
    """Top 13 brawlers más VERSÁTILES: mayor win rate medio entre los modos jugados (el
    mismo orden que la tabla Brawler × Modo), con retrato y cuerpo entero para el podio."""
    tag = _require_follow(user, player)
    f = {"player": tag, "mode": mode, "map": map, "brawler": brawler, "role": role}
    vers = await asyncio.to_thread(db.versatile_brawlers, f, 13)
    catalog = await assets.get_brawler_catalog()
    by_id = catalog.get("by_id") or {}
    by_name = {(c.get("name") or "").upper(): bid for bid, c in by_id.items()}
    coll_by_id = {c["brawler_id"]: c for c in await asyncio.to_thread(db.get_collection, tag)}
    from .. import skins
    out = []
    for pos, v in enumerate(vers):
        bid = by_name.get((v["name"] or "").upper())
        cat = by_id.get(bid) or {}
        tb = {"id": bid, "name": cat.get("name") or v["name"], "avg_winrate": v["avg_winrate"],
              "modes_played": v["modes_played"], "total": v["total"], "portrait": cat.get("portrait")}
        if pos < 3 and bid:
            ex = brawler_extra.get(bid)
            image_full = ex.get("body_image") or cat.get("image_full")
            c = coll_by_id.get(bid)
            if c and c.get("skin_id") and c.get("skin_name"):
                try:
                    skin_url = skins.get_image(c["skin_id"]) or await skins.resolve_and_cache(c["skin_id"], cat.get("name"), c["skin_name"])
                    if skin_url:
                        image_full = skin_url
                except Exception:  # noqa: BLE001
                    pass
            tb["image_full"] = image_full
        out.append(tb)
    return {"versatile": out}


@router.get("/api/tierlist")
async def api_tierlist(kind: str = Query("community"), user: dict = Depends(auth.optional_user)):
    """Tier List del meta: 'community' (generada con los datos de BrawlSensei) o
    'global' (consenso del meta externo, vía IA con respaldo). PÚBLICO (solo lectura)."""
    from .. import tierlist
    if kind == "global":
        return await tierlist.global_tierlist()
    return tierlist.get("community")


@router.get("/api/recommendations")
async def api_recommendations(player: str = Query(None), kind: str = Query("community"),
                              user: dict = Depends(auth.require_user)):
    """Recomendaciones de brawlers: cruza tu colección y tus win rates con el meta
    (comunitario o global). Devuelve 5 subsecciones de hasta 5 brawlers cada una."""
    tag = _require_follow(user, player)
    from .. import tierlist, recommendations
    catalog = await assets.get_brawler_catalog()
    if kind == "global":
        tl = await tierlist.global_tierlist()
    else:
        tl = await asyncio.to_thread(tierlist.get, "community")
    collection = await asyncio.to_thread(db.get_collection, tag)
    wr_rows = await asyncio.to_thread(db.winrate_by, "brawler", {"player": tag})
    await buffs.get_buffs()
    changes = buffs.changes_map()
    return recommendations.build(kind, catalog, tl, collection, wr_rows, changes)


@router.get("/api/public/players/{tag}/recommendations")
async def api_public_recommendations(tag: str, kind: str = Query("community")):
    """Recomendaciones de brawlers de un jugador, PÚBLICO (sin cuenta): para la consulta de
    invitado. Mismo cálculo que la sección Brawlers (colección + win rates × meta)."""
    from .. import tierlist, recommendations
    ntag = db.normalize_tag(tag)
    catalog = await assets.get_brawler_catalog()
    if kind == "global":
        tl = await tierlist.global_tierlist()
    else:
        tl = await asyncio.to_thread(tierlist.get, "community")
    collection = await asyncio.to_thread(db.get_collection, ntag)
    wr_rows = await asyncio.to_thread(db.winrate_by, "brawler", {"player": ntag})
    await buffs.get_buffs()
    changes = buffs.changes_map()
    return recommendations.build(kind, catalog, tl, collection, wr_rows, changes)


@router.get("/api/buffs")
async def api_buffs(user: dict = Depends(auth.optional_user)):
    """Buffs/nerfs recientes por brawler (de las notas de parche, vía IA, no bloqueante).
    PÚBLICO (solo lectura)."""
    return await buffs.get_buffs()


@router.get("/api/brawler/{brawler_id}/changes")
async def api_brawler_changes(brawler_id: int, user: dict = Depends(auth.require_user)):
    """Histórico COMPLETO de cambios (buffs/nerfs/reworks) de un brawler, desde la wiki
    (dataset `brawler_changes.json`, traducido). Lectura instantánea, sin IA en la petición."""
    catalog = await assets.get_brawler_catalog()
    cat = (catalog.get("by_id") or {}).get(brawler_id)
    if not cat:
        return JSONResponse({"error": "Brawler no encontrado."}, status_code=404)
    name = cat.get("name")
    return {"name": name, "history": changes.history_for(name), "summary": changes.summary_for(name)}


@router.get("/api/account-rating")
async def api_account_rating(player: str = Query(None), user: dict = Depends(auth.require_user)):
    """Solo el rating de cuenta (para mostrarlo también en Estadísticas, sin cargar
    toda la rejilla de brawlers)."""
    tag = _require_follow(user, player)
    catalog = await assets.get_brawler_catalog()
    try:
        prof = await _get_player_cached(tag)
        if prof.get("brawlers"):
            await asyncio.to_thread(db.snapshot_brawlers, tag, prof.get("brawlers"))
    except Exception as e:  # noqa: BLE001
        print(f"[rating] no se pudo refrescar {tag}: {e}")
    rating = await asyncio.to_thread(db.account_rating, tag,
                                     {**(catalog.get("totals") or {}), "hypercharges": brawler_extra.hypercharge_total()})
    return {"rating": rating}


@router.get("/api/brawler/{brawler_id}")
async def api_brawler_detail(brawler_id: int, player: str = Query(None),
                             user: dict = Depends(auth.require_user)):
    """Ficha de un brawler: catálogo + lo que posees + dataset curado (hipercarga,
    stats por nivel, builds) + tu win rate con él, global y por modo."""
    tag = _require_follow(user, player)
    catalog = await assets.get_brawler_catalog()
    cat = (catalog.get("by_id") or {}).get(brawler_id)
    if not cat:
        return JSONResponse({"error": "Brawler no encontrado en el catálogo."}, status_code=404)
    collection = await asyncio.to_thread(db.get_collection, tag)
    c = next((x for x in collection if x["brawler_id"] == brawler_id), None)
    owned_sp = set(c["star_power_ids"]) if c else set()
    owned_gd = set(c["gadget_ids"]) if c else set()

    extra = brawler_extra.get(brawler_id)

    def merge_abilities(cat_items, owned, es_list):
        """Funde el catálogo (icono + lo poseído) con el nombre/efecto en español
        de la wiki (emparejado por orden)."""
        es_list = es_list or []
        out = []
        for i, it in enumerate(_clean_abilities(cat_items, es_list)):
            es = es_list[i] if i < len(es_list) else {}
            out.append({"id": it.get("id"), "name": es.get("name") or it.get("name"),
                        "icon": it.get("icon"),
                        "description": es.get("description") or it.get("description"),
                        "owned": it.get("id") in owned})
        return out

    name = cat.get("name")
    bname = ((c["brawler_name"] if c else name) or name or "").upper()
    filt = {"player": tag, "brawler": bname}
    ov = await asyncio.to_thread(db.overview, filt)
    by_mode = await asyncio.to_thread(db.winrate_by, "mode", filt)
    wr_all = await asyncio.to_thread(db.winrate_by, "brawler", {"player": tag})
    wr_me = next((r for r in wr_all if (r.get("label") or "").upper() == bname), None)
    skin = {"id": c.get("skin_id"), "name": c.get("skin_name")} if (c and c.get("skin_id")) else None
    image_full = extra.get("body_image") or cat.get("image_full")
    if skin and skin.get("id") and skin.get("name"):
        from .. import skins as skin_cat
        skin_url = skin_cat.get_image(skin["id"]) or await skin_cat.resolve_and_cache(skin["id"], name, skin["name"])
        if skin_url:
            image_full = skin_url       # muestra la skin equipada si la encontramos
            skin["image"] = skin_url

    return {
        "id": brawler_id, "name": name,
        "description": extra.get("description_es") or cat.get("description"),
        "role": brawler_extra.norm_role(extra.get("role") or brawler_extra.role_primary_fallback(name) or cat.get("role")),
        "role_secondary": brawler_extra.role_secondary(name),
        "rarity": cat.get("rarity"),
        "image_full": image_full, "portrait": cat.get("portrait"),
        "attack": extra.get("attack"),
        "passive": extra.get("passive"),
        "super": extra.get("super"),
        "star_powers": merge_abilities(cat.get("star_powers"), owned_sp, extra.get("star_powers_es")),
        "gadgets": merge_abilities(cat.get("gadgets"), owned_gd, extra.get("gadgets_es")),
        "owned": c is not None,
        "power": c["power"] if c else None,
        "rank": c["rank"] if c else None,
        "trophies": c["trophies"] if c else None,
        "highest_trophies": c["highest_trophies"] if c else None,
        "rank_band": _rank_band(c["trophies"]) if c else None,
        "prestige_level": c.get("prestige_level") if c else None,
        "skin": skin,
        "gears_owned": len(c["gear_ids"]) if c else 0,
        "has_hypercharge": (name or "").upper() not in _NO_HYPERCHARGE,
        "change": buffs.changes_map().get((name or "").upper()),
        "owns_hypercharge": bool(c and c.get("hypercharge_ids")),
        "hypercharge": extra.get("hypercharge"),
        "stats_by_level": extra.get("stats_by_level"),
        "builds": extra.get("builds") or [],
        "your": {
            "winrate": ov.get("winrate"), "battles": ov.get("total"),
            "adj_score": (wr_me or {}).get("adj_score"), "level": (wr_me or {}).get("avg_trophies"),
            "reliability": (wr_me or {}).get("reliability"),
            "by_mode": [{"mode": m["label"], "winrate": m["winrate"], "battles": m["total"]}
                        for m in by_mode],
        },
    }
