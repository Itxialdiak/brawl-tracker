#!/usr/bin/env python3
"""
Corrige partidas históricas de Brawl Hockey mal etiquetadas como Balón Brawl.

La API marca Brawl Hockey como 'brawlBall' en battle.mode (antes guardábamos ese
valor); ahora se guarda 'brawlHockey'. Este script arregla lo ya almacenado:
busca en Brawlify los mapas de Brawl Hockey y reescribe esas partidas. Es seguro
y se puede ejecutar varias veces (idempotente).

Ejecútalo una vez desde la raíz del proyecto, con el venv:
    python fix_modes.py
"""

from __future__ import annotations

import asyncio
import sys

import httpx

from app import db

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


async def hockey_map_names() -> set[str]:
    """Nombres (en minúsculas) de mapas cuyo modo en Brawlify es Brawl Hockey."""
    async with httpx.AsyncClient() as c:
        r = await c.get("https://api.brawlapi.com/v1/maps",
                        headers={"User-Agent": "BrawlTracker/1.0"}, timeout=20)
        r.raise_for_status()
    out = set()
    for m in r.json().get("list", []):
        gm = ((m.get("gameMode") or {}).get("name") or "").lower()
        if gm == "brawl hockey" and m.get("name"):
            out.add(m["name"].strip().lower())
    return out


async def main() -> None:
    db.init_db()
    maps = await hockey_map_names()
    print(f"{len(maps)} mapas de Brawl Hockey conocidos en Brawlify.")
    conn = db.get_conn()
    cur = conn.cursor()
    rows = cur.execute("SELECT DISTINCT map FROM battles WHERE mode='brawlBall'").fetchall()
    fixed = 0
    for (mp,) in rows:
        if mp and mp.strip().lower() in maps:
            cur.execute("UPDATE battles SET mode='brawlHockey' WHERE mode='brawlBall' AND map=?", (mp,))
            print(f"  {mp}: {cur.rowcount} partidas -> brawlHockey")
            fixed += cur.rowcount
    conn.commit()
    conn.close()
    print(f"Hecho. {fixed} partidas corregidas.")


if __name__ == "__main__":
    asyncio.run(main())
