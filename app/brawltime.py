"""Meta global de brawltime.ninja (solo el top por win rate).

brawltime expone los datos completos por brawler a través de un Cube.js con TOKEN de
autorización (403 sin él), así que NO se puede consultar el dataset entero. Sus páginas SSR
solo renderizan el TOP (~10) del ranking. Aquí parseamos ese top del dashboard (win rate
ajustado + use rate) como "meta global" complementario a nuestras tier lists. Degrada con
elegancia (lista vacía) si la web cambia o no responde. Síncrono; llamar en un hilo.
"""
import os
import re
import html
import time

import httpx

_UA = "Mozilla/5.0 (compatible; BrawlSensei/1.0)"
_URL = os.environ.get(
    "BRAWLTIME_URL",
    "https://brawltime.ninja/es/dashboard?cube=map&dimension=brawler"
    "&metric=winRateAdj&metric=useRate&sort=winRateAdj")
_cache = {"at": 0.0, "data": []}
_ROW = re.compile(r"^(\d+)\s+(.+?)\s+([\d.,]+)%\s+([\d.,]+)%$")


def top_brawlers(ttl: float = 3600.0) -> list:
    """Top brawlers del meta global: [{rank, name, win_rate, use_rate}]. Cacheado (1 h)."""
    if _cache["data"] and (time.time() - _cache["at"]) < ttl:
        return _cache["data"]
    try:
        r = httpx.get(_URL, timeout=15.0, follow_redirects=True, headers={"User-Agent": _UA})
        r.raise_for_status()
        out = []
        for tr in re.findall(r"(?s)<tr[^>]*>(.*?)</tr>", r.text):
            txt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", tr))).strip()
            m = _ROW.match(txt)
            if m:
                out.append({"rank": int(m.group(1)), "name": m.group(2).strip(),
                            "win_rate": m.group(3).replace(",", "."),
                            "use_rate": m.group(4).replace(",", ".")})
        if out:
            _cache["data"], _cache["at"] = out, time.time()
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[brawltime] {e}")
        return _cache["data"] or []
