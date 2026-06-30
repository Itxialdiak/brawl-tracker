"""Histórico COMPLETO de cambios de balance por brawler, desde la wiki de Fandom (EN).

Cada página de brawler en la wiki EN tiene una sección ==History== con TODOS los cambios
datados desde 2017, ya clasificados con plantillas {{Balance|Buff|…}} / {{Balance|Nerf|…}} /
{{Balance|Rework|…}} / {{Balance|Neutral|…}}. Parseamos eso (sin IA, la clasificación viene
dada) y traducimos el texto al español con IA en lotes (uno por brawler). El resultado se
guarda en `data/brawler_changes.json` y lo sirve `changes.py` en la ficha.

Uso vía `scrape_changes.py` (offline, como `scrape_wiki.py`). En el servidor solo se LEE el JSON.
"""
import re
import json
import asyncio

import httpx

_EN_API = "https://brawlstars.fandom.com/api.php"
_UA = "Mozilla/5.0 (compatible; BrawlSensei/1.0)"
_KIND = {"buff": "buff", "nerf": "nerf", "rework": "rework", "neutral": "neutral",
         "new": "neutral", "addition": "neutral"}
_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")


# ------------------------------- descarga -------------------------------
async def _wikitext(client: httpx.AsyncClient, page: str) -> str:
    try:
        r = await client.get(_EN_API, params={"action": "parse", "page": page,
                                               "prop": "wikitext", "format": "json"})
        return r.json().get("parse", {}).get("wikitext", {}).get("*", "") or ""
    except Exception as e:  # noqa: BLE001
        print(f"[wiki_changes] {page}: {e}")
        return ""


def _history_section(wt: str) -> str:
    """Extrae el cuerpo de la sección ==History== (hasta la siguiente sección de nivel 2)."""
    m = re.search(r"(?im)^==\s*History\s*==\s*$", wt)
    if not m:
        return ""
    rest = wt[m.end():]
    nxt = re.search(r"(?m)^==[^=].*?==\s*$", rest)
    return rest[:nxt.start()] if nxt else rest


# ------------------------------- parseo -------------------------------
_DATE_BULLET = re.compile(r"(?m)^\*+\s*'*\(?\s*(\d{1,2}/\d{1,2}/\d{2,4})")
_PARAM = re.compile(r"\s*(Change|Content|Type|Text|Stat)\s*=\s*(.*)$", re.S | re.I)
_COSMETIC = re.compile(r"\b(skin|skins|remodel|remodell?ed|was featured|pin|spray|"
                       r"profile icon|cosmetic|emote|reaction)\b", re.I)
_KINDWORD = re.compile(r"^(change\s*=\s*)?(buff|nerf|rework|neutral)\.?$", re.I)


def _split_pipes(s: str) -> list:
    """Parte por '|' de primer nivel, respetando anidamiento [[...]] y {{...}}."""
    out, buf, i, br, brc = [], "", 0, 0, 0
    while i < len(s):
        two = s[i:i + 2]
        if two == "[[":
            br += 1; buf += two; i += 2; continue
        if two == "]]":
            br -= 1; buf += two; i += 2; continue
        if two == "{{":
            brc += 1; buf += two; i += 2; continue
        if two == "}}":
            brc -= 1; buf += two; i += 2; continue
        if s[i] == "|" and br <= 0 and brc <= 0:
            out.append(buf); buf = ""; i += 1; continue
        buf += s[i]; i += 1
    out.append(buf)
    return out


def _iter_balance(sec: str):
    """(pos, kind_raw, texto_crudo) de cada {{Balance…}} en la sección (multilínea)."""
    i = 0
    while True:
        s = sec.find("{{Balance", i)
        if s < 0:
            break
        depth, j = 0, s
        while j < len(sec):
            if sec[j:j + 2] == "{{":
                depth += 1; j += 2; continue
            if sec[j:j + 2] == "}}":
                depth -= 1; j += 2
                if depth == 0:
                    break
                continue
            j += 1
        parts = _split_pipes(sec[s + 2:j - 2])    # ["Balance", ...]
        kind, text, positional = None, None, []
        for p in parts[1:]:
            m = _PARAM.match(p)
            if m:
                key, val = m.group(1).lower(), m.group(2)
                if key in ("change", "type"):
                    kind = val.strip().lower()
                elif key in ("content", "text"):
                    text = val
            else:
                positional.append(p)
        if kind is None and positional:
            kind = positional.pop(0).strip().lower()
        if text is None:
            text = positional[0] if positional else ""
        yield s, (kind or ""), text
        i = j


def _clean(desc: str) -> str:
    desc = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", desc)   # [[a|b]] -> b
    desc = re.sub(r"\[\[([^\]]*)\]\]", r"\1", desc)            # [[a]] -> a
    desc = re.sub(r"\{\{[^}]*\}\}", "", desc)                  # {{...}} fuera
    desc = desc.replace("'''", "").replace("''", "")
    desc = re.sub(r"<[^>]+>", "", desc)
    return re.sub(r"\s+", " ", desc).strip()


def _iso(d: str, m: str, y: str) -> str:
    y = ("20" + y) if len(y) == 2 else y
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def _kind_of(raw: str) -> str:
    raw = (raw or "").lower()
    for k in ("buff", "nerf", "rework", "neutral"):
        if k in raw:
            return k
    return "neutral"


def parse_history(wt: str) -> list:
    """[{date, iso, kind, note_en}] de una página (más reciente primero). Filtra ruido
    cosmético (aspectos, pins…) y entradas vacías o que solo repiten el tipo."""
    sec = _history_section(wt)
    if not sec:
        return []
    dates = [(m.start(), m.group(1)) for m in _DATE_BULLET.finditer(sec)]
    out = []
    for pos, kind_raw, desc in _iter_balance(sec):
        note = _clean(desc)
        if not note or _KINDWORD.match(note):
            continue
        kind = _kind_of(kind_raw)
        if kind == "neutral" and _COSMETIC.search(note):
            continue                                   # aspectos/pins: no es balance
        dd = ""
        for dpos, dval in dates:                       # última fecha-bullet antes del template
            if dpos <= pos:
                dd = dval
            else:
                break
        m = _DATE.search(dd)
        out.append({"date": (f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)[-2:]}"
                             if m else ""),
                    "iso": _iso(m.group(1), m.group(2), m.group(3)) if m else "",
                    "kind": kind, "note_en": note})
    out.sort(key=lambda e: e["iso"], reverse=True)
    return out


# ------------------------------- traducción (IA, por brawler) -------------------------------
async def _translate(notes: list) -> list:
    """Traduce una lista de notas EN -> ES manteniendo el orden y la longitud. Sin IA o si
    falla, devuelve las originales (degrada con elegancia)."""
    from . import buffs
    if not notes:
        return notes
    payload = "\n".join(f"{i+1}. {n}" for i, n in enumerate(notes))
    data = await buffs._ai_json(
        "Traduces notas de cambios de balance de Brawl Stars al español, de forma concisa y "
        "natural (términos del juego: brawler, súper, gadget, habilidad estelar, hipercarga, "
        "daño, alcance, recarga, cadencia). Mantén números y unidades.",
        (payload + "\n\n---\nDevuelve SOLO JSON {\"t\": [\"…\", …]} con la traducción de CADA "
         f"línea en el MISMO orden ({len(notes)} elementos), sin numeración."),
        max(600, min(4000, len(payload) // 2 + 400)))
    out = (data or {}).get("t")
    if isinstance(out, list) and len(out) == len(notes):
        return [str(x) for x in out]
    return notes


async def brawler_history(client: httpx.AsyncClient, page: str, translate: bool = True) -> list:
    wt = await _wikitext(client, page)
    entries = parse_history(wt)
    if entries and translate:
        es = await _translate([e["note_en"] for e in entries])
        for e, t in zip(entries, es):
            e["note"] = t
    else:
        for e in entries:
            e["note"] = e.get("note_en", "")
    for e in entries:
        e.pop("note_en", None)
    return entries
