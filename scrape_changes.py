#!/usr/bin/env python3
"""Regenera app/data/brawler_changes.json: histórico COMPLETO de balance por brawler,
desde la wiki de Fandom (EN), traducido al español. Análogo a scrape_wiki.py.

  python scrape_changes.py                  # todos (resume: salta los ya hechos)
  python scrape_changes.py --force          # todos, rehaciéndolos
  python scrape_changes.py --no-translate   # sin IA (deja el texto en inglés)
  python scrape_changes.py Shelly Charlie   # solo esos brawlers

El servidor solo LEE el JSON (app/changes.py). Reejecutar tras parches del juego.
"""
import os
import sys
import json
import asyncio

import httpx

from app import wiki_changes as wc
from app import assets

OUT = os.path.join("app", "data", "brawler_changes.json")

# Nombre del catálogo -> título de la página en la wiki EN, cuando difieren.
PAGE_OVERRIDES = {
    "8-BIT": "8-Bit", "MR. P": "Mr. P", "LARRY & LAWRIE": "Larry & Lawrie",
    "JAE-YONG": "Jae-yong",
}

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


async def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    force = "--force" in sys.argv
    translate = "--no-translate" not in sys.argv
    only = {a.lower() for a in args} or None

    catalog = await assets.get_brawler_catalog()
    names = [c.get("name") for c in (catalog.get("by_id") or {}).values() if c.get("name")]

    store = {}
    if os.path.exists(OUT) and not force:
        try:
            with open(OUT, encoding="utf-8") as f:
                store = json.load(f)
        except Exception:  # noqa: BLE001
            store = {}

    done = skipped = 0
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": wc._UA},
                                 follow_redirects=True) as client:
        for name in names:
            key = name.upper()
            if only and name.lower() not in only:
                continue
            if not only and not force and key in store and store[key].get("entries"):
                skipped += 1
                continue
            page = PAGE_OVERRIDES.get(key, name)
            try:
                ents = await wc.brawler_history(client, page, translate=translate)
            except Exception as e:  # noqa: BLE001
                print(f"  ! {name}: {e}")
                continue
            store[key] = {"entries": ents}
            done += 1
            print(f"  + {name}: {len(ents)} cambios")
            with open(OUT, "w", encoding="utf-8") as f:        # guardado incremental (resume)
                json.dump(store, f, ensure_ascii=False)
    print(f"\nHecho: {done} procesados, {skipped} ya estaban -> {OUT} ({len(store)} brawlers)")


if __name__ == "__main__":
    print("Generando histórico de cambios por brawler desde la wiki (EN) + traducción…")
    asyncio.run(main())
