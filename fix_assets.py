#!/usr/bin/env python3
"""Saneo puntual de app/data/brawler_extra.json (sin re-scrape completo):
- Quita las variantes "Buffie" (skins) de gadgets_es / star_powers_es.
- Reemplaza iconos de hipercarga que sean del Buffie por el icono BASE de la wiki (o null=genérico).

Las causas raíz ya están corregidas en app/wiki.py para futuros scrapes; esto limpia lo ya generado.
  python fix_assets.py
"""
import re
import sys
import json
import asyncio

import httpx

from app import wiki, assets

PATH = "app/data/brawler_extra.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; BrawlSensei/1.0)"}

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


_EN_API = "https://brawlstars.fandom.com/api.php"


async def _file_url(client, api, fname):
    j = await client.get(api, params={"action": "query", "titles": "File:" + fname.strip(),
                                      "prop": "imageinfo", "iiprop": "url", "format": "json"})
    for p in j.json().get("query", {}).get("pages", {}).values():
        ii = p.get("imageinfo")
        if ii:
            return ii[0]["url"]
    return None


async def base_hc_icon(client, name):
    """Icono BASE (no Buffie) de la hipercarga. Convención canónica de la wiki EN
    'File:{Nombre}-Hypercharge.png'; si falla, la primera imagen no-Buffie de la sección."""
    try:
        url = await _file_url(client, _EN_API, f"{name}-Hypercharge.png")
        if url:
            return url
    except Exception as e:  # noqa: BLE001
        print(f"  ! {name}: {e}")
    for api, sec in ((wiki.WIKI_API, "Hipercarga"), (_EN_API, "Hypercharge")):
        try:
            r = await client.get(api, params={"action": "parse", "page": name,
                                              "prop": "wikitext", "format": "json"})
            body = wiki.section(r.json().get("parse", {}).get("wikitext", {}).get("*", ""), sec)
            for cand in re.findall(r"Img[^|}]*\|\s*([^|}\]]+\.png)", body or ""):
                if "buffie" not in cand.lower():
                    url = await _file_url(client, api, cand)
                    if url:
                        return url
        except Exception:  # noqa: BLE001
            pass
    return None


async def main():
    with open(PATH, encoding="utf-8") as f:
        d = json.load(f)
    catalog = await assets.get_brawler_catalog()
    names = {int(bid): c.get("name") for bid, c in (catalog.get("by_id") or {}).items()}
    removed = fixed = 0
    async with httpx.AsyncClient(timeout=25, headers=UA, follow_redirects=True) as client:
        for k, v in d.items():
            if k.startswith("_") or not isinstance(v, dict):
                continue
            for key in ("gadgets_es", "star_powers_es"):
                lst = v.get(key) or []
                clean = [x for x in lst if "buffie" not in str(x.get("name", "")).lower()]
                if len(clean) != len(lst):
                    removed += len(lst) - len(clean)
                    v[key] = clean
            # Re-fetchea el icono canónico para TODA hipercarga (uniforme, completo, sin Buffie).
            hc = v.get("hypercharge")
            if hc and hc.get("name"):
                nm = names.get(int(k))
                new = await base_hc_icon(client, nm) if nm else None
                if new and "buffie" not in new.lower():
                    if new != hc.get("icon"):
                        fixed += 1
                    hc["icon"] = new
                elif "buffie" in str(hc.get("icon") or "").lower():
                    hc["icon"] = None              # sin base: quita el Buffie (genérico ⚡)
                    print(f"  hipercarga {nm}: NULL (icono genérico ⚡)")
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    print(f"\nHecho: 'Buffie' eliminados de loadout: {removed} | iconos de hipercarga corregidos: {fixed}")


if __name__ == "__main__":
    print("Saneando brawler_extra.json (Buffie + iconos de hipercarga)…")
    asyncio.run(main())
