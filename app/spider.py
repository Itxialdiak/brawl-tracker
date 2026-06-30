"""Spider de noticias de balance de Brawl Stars.

Reúne TEXTO de fuentes EN VIVO (no del conocimiento del modelo, que está desfasado: su corte
es anterior a las últimas actualizaciones) para que `buffs.py` extraiga los cambios con IA.

Fuentes:
- Notas OFICIALES de Supercell (release notes): autoridad para los cambios VIGENTES.
- YouTube de creadores (Spiuk, Godeik, Soba...): título + descripción de sus vídeos recientes
  relevantes (Brawl Talk, balance, buffs/nerfs) -> cambios ANUNCIADOS. (Las transcripciones de
  YouTube están bloqueadas salvo con token PoT; la descripción es accesible y suele resumirlos.)
- Redes/otras URLs opcionales (`BUFFS_SOCIAL_URLS`).

Funciones SÍNCRONAS (httpx.get); `buffs.py` las llama en un hilo para no bloquear el bucle.
Configurable por entorno:
  BUFFS_NOTES_URLS    URLs de notas oficiales (coma). Por defecto, la última conocida.
  BUFFS_YT_CREATORS   handles de YouTube (coma). Por defecto @Godeik,@SobaBS,@Spiuk.
  BUFFS_SOCIAL_URLS   URLs extra de redes/noticias (coma).
"""
import os
import re
import html
import json

import httpx

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_CK = {"CONSENT": "YES+cb", "SOCS": "CAI"}     # evita el muro de consentimiento de YouTube

_RELEASE_BASE = os.environ.get(
    "BUFFS_RELEASE_BASE", "https://supercell.com/en/games/brawlstars/blog/release-notes/")
NOTES_URLS = [u.strip() for u in os.environ.get(
    "BUFFS_NOTES_URLS", _RELEASE_BASE + "release-notes-june-2026/").split(",") if u.strip()]
CREATORS = [h.strip() for h in os.environ.get(
    "BUFFS_YT_CREATORS", "@SpiukYT,@Godeik,@SobaBS").split(",") if h.strip()]
SOCIAL_URLS = [u.strip() for u in os.environ.get("BUFFS_SOCIAL_URLS", "").split(",") if u.strip()]

# Solo nos interesan vídeos cuyo TÍTULO huela a noticia de balance.
_KW = re.compile(r"buff|nerf|rework|balance|cambio|ajuste|actualiza|brawl ?talk|parche|"
                 r"hipercarga|hypercharge|mejora|recorte|nuev[oa]s? poder", re.I)
_MAX_VIDEOS = 5        # vídeos relevantes por creador
_RSS_SCAN = 15         # cuántos vídeos recientes mirar por canal


def _get(url: str, headers: dict = None, timeout: float = 12.0) -> str:
    try:
        h = {"User-Agent": _UA}
        if headers:
            h.update(headers)
        r = httpx.get(url, timeout=timeout, follow_redirects=True, headers=h, cookies=_CK)
        r.raise_for_status()
        return r.text
    except Exception as e:  # noqa: BLE001
        print(f"[spider] GET {url[:70]}: {e}")
        return ""


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?is)</(p|div|li|h[1-6]|tr|section)>", "\n", raw)
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\r\f]+", " ", raw)
    raw = re.sub(r"\n[ \t]*\n+", "\n", raw)
    return raw.strip()


# ------------------------------- YouTube -------------------------------
def resolve_channel_id(handle: str) -> str:
    """Handle (@Godeik) -> channelId (UC...). '' si no se logra (handle inexistente, etc.)."""
    h = handle.lstrip("@")
    raw = _get(f"https://www.youtube.com/@{h}/videos?hl=es&gl=US")
    if not raw:
        return ""
    m = (re.search(r'"(?:externalId|channelId)":"(UC[\w-]{20,})"', raw)
         or re.search(r"/channel/(UC[\w-]{20,})", raw))
    return m.group(1) if m else ""


def recent_videos(channel_id: str) -> list:
    """Vídeos recientes del canal (RSS): [{id, title, date}], más nuevos primero."""
    rss = _get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    if not rss:
        return []
    out = []
    for entry in re.findall(r"(?s)<entry>(.*?)</entry>", rss):
        vid = re.search(r"<yt:videoId>([\w-]+)</yt:videoId>", entry)
        tit = re.search(r"<title>(.*?)</title>", entry)
        pub = re.search(r"<published>(.*?)</published>", entry)
        if vid and tit:
            out.append({"id": vid.group(1), "title": html.unescape(tit.group(1)),
                        "date": (pub.group(1)[:10] if pub else "")})
        if len(out) >= _RSS_SCAN:
            break
    return out


def video_description(vid: str) -> str:
    """Descripción del vídeo (desde la watch page; no necesita token, a diferencia de los subs)."""
    raw = _get(f"https://www.youtube.com/watch?v={vid}&hl=es")
    if not raw:
        return ""
    m = re.search(r'"shortDescription":"((?:\\.|[^"\\])*)"', raw)
    if not m:
        return ""
    try:
        return json.loads('"' + m.group(1) + '"')
    except Exception:  # noqa: BLE001
        return ""


def _relevant_videos(channel_id: str) -> list:
    return [v for v in recent_videos(channel_id) if _KW.search(v["title"])][:_MAX_VIDEOS]


# ------------------------------- agregación -------------------------------
def official_notes() -> list:
    items = []
    for url in NOTES_URLS:
        raw = _get(url)
        if raw:
            items.append({"source": "Notas oficiales Supercell", "url": url,
                          "text": html_to_text(raw)[:14000]})
    return items


def social() -> list:
    items = []
    for url in SOCIAL_URLS:
        raw = _get(url)
        if raw:
            items.append({"source": "Redes/noticias", "url": url,
                          "text": html_to_text(raw)[:6000]})
    return items


def youtube_news() -> list:
    items = []
    for handle in CREATORS:
        cid = resolve_channel_id(handle)
        if not cid:
            print(f"[spider] sin channelId para {handle}")
            continue
        for v in _relevant_videos(cid):
            desc = video_description(v["id"])
            items.append({"source": f"YouTube {handle} ({v['date']})",
                          "url": f"https://youtu.be/{v['id']}",
                          "text": (v["title"] + "\n" + desc).strip()[:2000]})
    return items


def signature() -> str:
    """Firma BARATA de las fuentes (URLs de notas + IDs de vídeos relevantes recientes), para
    detectar novedades SIN descargar descripciones ni llamar a la IA. Solo notas + RSS."""
    parts = list(NOTES_URLS) + list(SOCIAL_URLS)
    for handle in CREATORS:
        cid = resolve_channel_id(handle)
        if cid:
            parts += [v["id"] for v in _relevant_videos(cid)]
    return "|".join(parts)


def gather() -> dict:
    """Reúne TODO el texto en vivo. {notes:[...], news:[...], signature}. Costoso (descarga
    descripciones); usar solo cuando la firma cambió."""
    notes = official_notes()
    news = youtube_news() + social()
    sig_parts = list(NOTES_URLS) + list(SOCIAL_URLS) + [n["url"].rsplit("/", 1)[-1] for n in news
                                                        if "youtu.be" in n["url"]]
    return {"notes": notes, "news": news, "signature": "|".join(sig_parts)}


# ----------------------------- historial de cambios (release notes) -----------------------------
_SLUG_RE = re.compile(r"^[a-z0-9-]{3,80}$")
_MONTHS = {"january": "enero", "february": "febrero", "march": "marzo", "april": "abril",
           "may": "mayo", "june": "junio", "july": "julio", "august": "agosto",
           "september": "septiembre", "october": "octubre", "november": "noviembre",
           "december": "diciembre", "enero": "enero", "febrero": "febrero", "marzo": "marzo",
           "abril": "abril", "mayo": "mayo", "junio": "junio", "julio": "julio", "agosto": "agosto",
           "septiembre": "septiembre", "octubre": "octubre", "noviembre": "noviembre",
           "diciembre": "diciembre"}
_index_cache = {"at": 0.0, "data": []}


def _slug_meta(slug: str) -> tuple:
    """De un slug de nota saca (título legible, fecha 'mes año' si se detecta)."""
    words = slug.replace("release-notes", "").replace("notas-de-la-actualizacion-de", "")
    words = words.replace("notas-actualizacion", "").replace("-", " ").strip()
    m = re.search(r"\b(" + "|".join(_MONTHS) + r")\b[ ]*(?:de )?[ ]*(\d{4})", words, re.I)
    date = f"{_MONTHS.get(m.group(1).lower(), m.group(1)).capitalize()} {m.group(2)}" if m else ""
    title = ("Notas · " + date) if date else ("Notas · " + (words.title() or slug))
    return title, date


_BLOG_BASE = os.environ.get("BUFFS_BLOG_BASE", "https://supercell.com/en/games/brawlstars/blog/")


def release_index(ttl: float = 1800.0) -> list:
    """Lista de actualizaciones desde el blog oficial (con paginación): [{slug, url, title, date}]
    (más reciente primero). Cacheada en memoria (la red va en un hilo desde el endpoint)."""
    import time
    if _index_cache["data"] and (time.time() - _index_cache["at"]) < ttl:
        return _index_cache["data"]
    out, seen = [], set()
    for page in [_BLOG_BASE] + [_BLOG_BASE + f"page/{n}/" for n in range(2, 6)]:
        raw = _get(page)
        if not raw:
            continue
        for slug in re.findall(r"/games/brawlstars/blog/release-notes/([a-z0-9-]+)", raw):
            if not _SLUG_RE.match(slug) or slug in seen or slug == "release-notes":
                continue
            seen.add(slug)
            title, date = _slug_meta(slug)
            out.append({"slug": slug, "url": _RELEASE_BASE + slug + "/", "title": title, "date": date})
    if out:
        _index_cache["data"], _index_cache["at"] = out, time.time()
    return out


def update_text(slug: str) -> str:
    """Texto limpio de una actualización concreta. Valida el slug (evita SSRF/URLs arbitrarias):
    solo se permite construir la URL desde la base oficial."""
    if not _SLUG_RE.match(slug or ""):
        return ""
    raw = _get(_RELEASE_BASE + slug + "/")
    return html_to_text(raw)[:16000] if raw else ""
