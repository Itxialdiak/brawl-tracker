#!/usr/bin/env python3
"""
Regenera app/data/brawler_extra.json desde la wiki de Fandom (ES) + las builds
recomendadas de Brawl Time Ninja. La lógica vive en app/wiki.py, que comparte el
poll diario del servidor.

Uso (desde la raíz, con el venv):
    python scrape_wiki.py                 # todos los brawlers
    python scrape_wiki.py Charlie Gigi    # solo esos (para probar)
"""

from __future__ import annotations

import asyncio
import sys

from app import wiki

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


if __name__ == "__main__":
    only = set(sys.argv[1:]) or None
    print("Actualizando datos de brawlers desde la wiki y Brawl Time Ninja…")
    result = asyncio.run(wiki.refresh(only=only))
    print("Hecho:", result)
