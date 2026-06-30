"""
Datos de brawler que ninguna API da, desde dos fuentes públicas:

- **Wiki de Fandom (ES)** vía su API de MediaWiki: estadísticas por nivel, súper,
  hipercarga, efectos de estelares/gadgets y descripción, todo en castellano.
- **Brawl Time Ninja**: la build recomendada por la comunidad (star power, gadget
  y gear con mayor win rate), parseada del JSON embebido en su página.

`refresh()` recorre todos los brawlers y reescribe `data/brawler_extra.json`. Lo
usa el script `scrape_wiki.py` (a mano) y un poll diario del servidor (main.py).
Requiere User-Agent de navegador (Fandom y brawltime bloquean fetchers genéricos).
"""

from __future__ import annotations

import asyncio
import json
import os
import re

import httpx

WIKI_API = "https://brawlstars.fandom.com/es/api.php"
BT_BASE = "https://brawltime.ninja/tier-list/brawler"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
OUT_PATH = os.path.join(os.path.dirname(__file__), "data", "brawler_extra.json")

# Nombre del catálogo (inglés) -> título de la página en la wiki ES, cuando difiere.
NAME_OVERRIDE = {"Larry & Lawrie": "Larry y Lawrie"}

# Escalado de poder: stat al nivel L (1..11) = base * (1 + 0.1*(L-1)); a P11 = base*2.
SCALE = [round(1 + 0.1 * (L - 1), 1) for L in range(1, 12)]

MIN_PICKS = 500  # umbral de partidas para fiarnos de un win rate de brawltime


# --------------------------- limpieza de wikitext ---------------------------

def _expr(m: re.Match) -> str:
    return re.sub(r"\|\s*\d+(\.\d+)?\s*$", "", m.group(1))  # quita "|20" de recarga final


def clean(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"\{\{CI\|([^{}|]*)\|?[^{}]*?\}\}", r"\1", s)
    s = re.sub(r"\{\{Calidad\|([^{}]*)\}\}", r"\1", s)
    s = re.sub(r"\{\{Img[^{}]*\}\}", "", s)
    s = re.sub(r"\{\{Expresión\|([^{}]*)\}\}", _expr, s)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", s).strip()


def _num(s: str | None):
    if not s:
        return None
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


# --------------------------- parseo del infobox ---------------------------

def split_top_pipes(block: str) -> list[str]:
    parts, buf, dc, db = [], [], 0, 0
    i = 0
    while i < len(block):
        two = block[i:i + 2]
        if two == "{{":
            dc += 1; buf.append(two); i += 2; continue
        if two == "}}":
            dc -= 1; buf.append(two); i += 2; continue
        if two == "[[":
            db += 1; buf.append(two); i += 2; continue
        if two == "]]":
            db -= 1; buf.append(two); i += 2; continue
        ch = block[i]
        if ch == "|" and dc == 0 and db == 0:
            parts.append("".join(buf)); buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def extract_infobox(wt: str) -> dict:
    m = re.search(r"\{\{Brawler[_ ]Infobox", wt)  # la wiki la escribe con espacio o con _
    if not m:
        return {}
    i, depth, j = m.start(), 0, m.start()
    while j < len(wt):
        if wt[j:j + 2] == "{{":
            depth += 1; j += 2
        elif wt[j:j + 2] == "}}":
            depth -= 1; j += 2
            if depth == 0:
                break
        else:
            j += 1
    out = {}
    for p in split_top_pipes(wt[i + 2:j - 2])[1:]:  # sin {{ }} ni "Brawler_Infobox"
        if "=" in p:
            k, _, v = p.partition("=")
            out[k.strip()] = v.strip()
    return out


def section(wt: str, title: str) -> str:
    m = re.search(r"\n==\s*" + re.escape(title) + r"\s*==\s*\n(.*?)(?=\n==[^=]|\Z)", wt, re.S)
    return m.group(1) if m else ""


def sentence_cut(s: str, limit: int) -> str:
    if not s or len(s) <= limit:
        return s
    cut = s[:limit]
    dot = cut.rfind(". ")
    return (cut[:dot + 1] if dot > limit * 0.5 else cut).strip()


def first_subsection(body: str) -> tuple[str, str]:
    m = re.search(r"===\s*(.*?)\s*===\s*\n(.*?)(?=\n===|\Z)", body, re.S)
    if not m:
        return "", sentence_cut(clean(body), 500)
    return clean(m.group(1)), sentence_cut(clean(m.group(2)), 500)


def all_subsections(body: str) -> list[dict]:
    out = []
    for m in re.finditer(r"===\s*(.*?)\s*===\s*\n(.*?)(?=\n===|\Z)", body, re.S):
        name = clean(m.group(1))
        if name:
            out.append({"name": name, "description": sentence_cut(clean(m.group(2)), 360) or None})
    return out


def lead_paragraph(wt: str) -> str:
    m = re.search(r"\}\}\s*(?:\{\{Expresión\|[^{}]*\}\})?\s*(.*?)(?=\n==[^=])", wt, re.S)
    return clean(m.group(1)) if m else ""


def by_level(base):
    return [round(base * f) for f in SCALE] if base else None


def build_entry(wt: str) -> dict:
    ib = extract_infobox(wt)
    stats = {}
    for key, base in (("health", _num(ib.get("Salud"))), ("damage", _num(ib.get("Ataque"))),
                      ("super", _num(ib.get("Super")))):
        if base:
            stats[key] = by_level(base)
    spd = _num(ib.get("VelocidadMovimiento"))
    if spd:
        stats["speed"] = spd
    if ib.get("RangoAtaque"):
        stats["range"] = clean(ib["RangoAtaque"])
    if ib.get("EnfriamientoAtaque"):
        stats["reload"] = clean(ib["EnfriamientoAtaque"])

    s_name, s_desc = first_subsection(section(wt, "Súper") or section(wt, "Super"))
    a_name, a_desc = first_subsection(section(wt, "Ataque"))
    role = clean(ib.get("Clase"))  # rol oficial en español (Control, Asesino, Tanque…)
    passive_body = section(wt, "Atributo") or section(wt, "Atributos")
    hc_mult = ib.get("Multiplicador de hipercarga") or ib.get("MultiplicadorHipercarga")
    hc_body = section(wt, "Hipercarga")
    hc_name, hc_desc = first_subsection(hc_body) if hc_body else ("", "")
    has_hc = bool(hc_mult)  # "lanzada" = el infobox trae el multiplicador
    hc_img = None
    if hc_body:
        # Salta las variantes "Buffie" (skin): coge la PRIMERA imagen que NO sea Buffie.
        for cand in re.findall(r"Img[^|}]*\|\s*([^|}\]]+\.png)", hc_body):
            if "buffie" not in cand.lower():
                hc_img = cand.strip()
                break

    entry = {}
    desc = sentence_cut(lead_paragraph(wt), 900)
    if desc:
        entry["description_es"] = desc
    if stats:
        entry["stats_by_level"] = stats
    if role:
        entry["role"] = role
    if a_name or a_desc:
        entry["attack"] = {"name": a_name or None, "description": a_desc or None}
    if s_name or s_desc:
        entry["super"] = {"name": s_name or None, "description": s_desc or None}
    if passive_body:
        pv = sentence_cut(clean(passive_body), 400)
        if pv:
            entry["passive"] = pv
    # Las "Buffie" son variantes de skin (otro tema): NO son gadgets/estelares reales.
    def _no_buffie(items):
        return [x for x in (items or []) if "buffie" not in str(x.get("name", "")).lower()]
    sps = _no_buffie(all_subsections(section(wt, "Habilidades Estelares")))
    gds = _no_buffie(all_subsections(section(wt, "Gadgets")))
    if sps:
        entry["star_powers_es"] = sps
    if gds:
        entry["gadgets_es"] = gds
    if has_hc:
        entry["hypercharge"] = {"name": hc_name or None, "description": hc_desc or None,
                                "multiplier": clean(hc_mult) if hc_mult else None}
    entry["_has_hypercharge"] = has_hc
    entry["_image_file"] = ib.get("image")  # se resuelve a URL en refresh()
    entry["_hc_image_file"] = hc_img        # icono real de la hipercarga
    return entry


async def fetch_wikitext(client: httpx.AsyncClient, title: str) -> str | None:
    try:
        r = await client.get(WIKI_API, params={"action": "parse", "page": title, "prop": "wikitext",
                                                "format": "json", "redirects": "1"})
        if r.status_code != 200:
            return None
        data = r.json()
        return None if "error" in data else (data.get("parse") or {}).get("wikitext", {}).get("*")
    except Exception:  # noqa: BLE001
        return None


async def fetch_image_url(client: httpx.AsyncClient, filename: str | None, width: int = 500) -> str | None:
    """Resuelve un fichero de la wiki (p. ej. 'Charlie.png') a su URL real (imagen
    a cuerpo entero), escalada a `width` px para no servir el original enorme."""
    if not filename:
        return None
    try:
        r = await client.get(WIKI_API, params={"action": "query", "titles": "File:" + filename,
                                               "prop": "imageinfo", "iiprop": "url",
                                               "iiurlwidth": str(width), "format": "json"})
        for p in ((r.json().get("query") or {}).get("pages") or {}).values():
            if "missing" in p:
                continue
            ii = (p.get("imageinfo") or [{}])[0]
            if ii.get("thumburl") or ii.get("url"):
                return ii.get("thumburl") or ii.get("url")
    except Exception:  # noqa: BLE001
        return None
    return None


def _img_candidates(name: str, infobox_image: str | None) -> list[str]:
    """Nombres de fichero a probar: el del infobox (brawlers antiguos) y los
    convencionales (los nuevos no lo ponen en el infobox; suele ser '{Nombre}.png')."""
    cands = [infobox_image] if infobox_image else []
    for base in (name, name.replace(" ", "_")):
        cands += [f"{base}.png", f"{base} Skin-Default.png", f"{base}_Skin-Default.png"]
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c); out.append(c)
    return out


async def resolve_body_image(client: httpx.AsyncClient, name: str, infobox_image: str | None) -> str | None:
    for fn in _img_candidates(name, infobox_image):
        url = await fetch_image_url(client, fn)
        if url:
            return url
    return None


_skin_img_cache: dict = {}  # (brawler, skin_name) -> url|None  (cachea también los fallos)


async def resolve_skin_image(brawler: str, skin_name: str) -> str | None:
    """Best-effort: imagen a cuerpo entero de una skin equipada, desde la wiki ES.
    Los nombres de fichero de skin son irregulares (la API da "VIRUS CHARLIE",
    la wiki lo guarda como "Charlie Virus.png"), así que probamos varias
    convenciones y cacheamos el resultado para no repetir peticiones."""
    if not brawler or not skin_name:
        return None
    key = (brawler, skin_name)
    if key in _skin_img_cache:
        return _skin_img_cache[key]
    sk = " ".join(skin_name.split())
    rest = re.sub(rf"\b{re.escape(brawler)}\b", "", sk, flags=re.I).strip()
    rest_tc = rest.title()
    cands = [
        f"{brawler} {rest_tc}.png" if rest else None,   # Charlie Virus.png
        f"{sk.title()}.png",                            # Virus Charlie.png
        f"{brawler} Skin-{rest_tc}.png" if rest else None,
        f"{brawler}-{rest_tc}.png" if rest else None,
    ]
    url = None
    try:
        async with httpx.AsyncClient(headers=UA, timeout=20, follow_redirects=True) as client:
            for cand in [c for c in cands if c]:
                url = await fetch_image_url(client, cand, 500)
                if url:
                    break
    except Exception:  # noqa: BLE001
        url = None
    _skin_img_cache[key] = url
    return url


# --------------------------- builds de Brawl Time Ninja ---------------------------

def _bt_walk(obj, rows: list) -> None:
    if isinstance(obj, dict):
        mr, dr = obj.get("metricsRaw"), obj.get("dimensionsRaw")
        if isinstance(mr, dict) and "winRate" in mr and isinstance(dr, dict):
            for kind in ("starpower", "gadget", "gear"):
                d = dr.get(kind)
                if isinstance(d, dict) and (d.get(kind + "Name") or d.get("name")):
                    rows.append({"kind": kind, "name": d.get(kind + "Name") or d.get("name"),
                                 "id": d.get(kind), "winRate": mr["winRate"], "picks": mr.get("picks", 0)})
        for v in obj.values():
            _bt_walk(v, rows)
    elif isinstance(obj, list):
        for v in obj:
            _bt_walk(v, rows)


def _bt_slug(name: str) -> str:
    # brawltime: minúsculas y cada carácter no alfanumérico (espacio, ".", "&") -> "_";
    # los guiones que ya van en el nombre se mantienen (8-bit, r-t).
    return re.sub(r"[^a-z0-9-]", "_", name.lower())


async def fetch_build(client: httpx.AsyncClient, name: str) -> dict | None:
    """Mejor star power/gadget/gear por win rate (comunidad) desde brawltime."""
    slug = _bt_slug(name)
    try:
        r = await client.get(f"{BT_BASE}/{slug}")
        if r.status_code != 200:
            return None
        m = re.search(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', r.text, re.S)
        if not m:
            return None
        rows = []
        _bt_walk(json.loads(m.group(1)), rows)
    except Exception:  # noqa: BLE001
        return None
    best = {}
    for row in rows:
        if row["picks"] and row["picks"] < MIN_PICKS:
            continue
        cur = best.get(row["kind"])
        if not cur or row["winRate"] > cur["winRate"]:
            best[row["kind"]] = row
    sp, gd, gr = best.get("starpower"), best.get("gadget"), best.get("gear")
    if not sp and not gd:
        return None

    def _id(x):
        return int(x["id"]) if x and str(x.get("id")).isdigit() else None

    build = {"name": "Mayor win rate (comunidad)", "source": f"{BT_BASE}/{slug}"}
    if _id(sp):
        build["star_power_id"] = _id(sp)
    if _id(gd):
        build["gadget_id"] = _id(gd)
    if gr:
        build["gear"] = gr["name"].title()
    wr = (sp or gd or {}).get("winRate")
    if wr:
        build["win_rate"] = round(wr * 100, 1)
    return build


# --------------------------- orquestación ---------------------------

async def refresh(only: set | None = None) -> dict:
    """Reescribe data/brawler_extra.json con la wiki + las builds de brawltime."""
    from . import assets  # tardío: evita import circular al cargar el módulo
    catalog = await assets.get_brawler_catalog()
    by_id = catalog.get("by_id") or {}
    if not by_id:
        return {"error": "sin catálogo de brawlers"}

    out = {}
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                out = json.load(f)
        except Exception:  # noqa: BLE001
            out = {}
    meta = dict(out.get("_meta") or {})

    hc_total = ok = miss = builds_ok = 0
    async with httpx.AsyncClient(headers=UA, timeout=30, follow_redirects=True) as client:
        for bid, b in by_id.items():
            name = b.get("name") or ""
            if only and name not in only:
                continue
            await asyncio.sleep(0.25)  # cortesía
            wt = await fetch_wikitext(client, NAME_OVERRIDE.get(name, name))
            if not wt:
                miss += 1
                continue
            entry = build_entry(wt)
            img_file = entry.pop("_image_file", None)
            hc_img_file = entry.pop("_hc_image_file", None)
            if entry.pop("_has_hypercharge", False):
                hc_total += 1
            url = await resolve_body_image(client, name, img_file)
            if url:
                entry["body_image"] = url
            if hc_img_file and entry.get("hypercharge"):
                hc_url = await fetch_image_url(client, hc_img_file, 120)
                if hc_url:
                    entry["hypercharge"]["icon"] = hc_url
            if entry.get("hypercharge") and not entry["hypercharge"].get("icon"):
                base = name.replace(" ", "").replace("&", "").replace(".", "").replace("-", "")
                for cand in (f"{name} Hipercarga.png", f"{base}Hypercharge.png"):
                    hc_url = await fetch_image_url(client, cand, 120)
                    if hc_url:
                        entry["hypercharge"]["icon"] = hc_url
                        break
            prev = out.get(str(bid)) or {}
            build = await fetch_build(client, name)
            if build:
                entry["builds"] = [build]
                builds_ok += 1
            elif prev.get("builds"):
                entry["builds"] = prev["builds"]
            if prev.get("hypercharge", {}).get("icon") and entry.get("hypercharge"):
                entry["hypercharge"]["icon"] = prev["hypercharge"]["icon"]
            out[str(bid)] = entry
            ok += 1

    if not only:
        meta["hypercharges_in_game"] = hc_total
    meta["note"] = ("Wiki Fandom ES (stats/súper/hipercarga/descripción) + builds de "
                    "Brawl Time Ninja (win rate). Se actualiza solo a diario; o con scrape_wiki.py.")
    out["_meta"] = meta
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return {"ok": ok, "miss": miss, "builds": builds_ok,
            "hypercharges": hc_total if not only else None}
