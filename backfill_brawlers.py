#!/usr/bin/env python3
"""
Rellena la tabla `brawler_collection` para los jugadores que ya estaban dados de
alta antes de existir el snapshot de colección. Lee el perfil de cada jugador de
la API oficial de Brawl Stars y guarda su colección de brawlers (nivel, rank,
trofeos, star powers/gadgets/gears poseídos).

A partir de ahora, la colección se actualiza sola en cada sondeo del poller; este
script solo hace falta una vez para poner al día el histórico.

Ejecútalo desde la raíz del proyecto, con el venv activado y BRAWL_API_TOKEN en
el .env (igual que para arrancar la app):
    python backfill_brawlers.py
"""

from __future__ import annotations

import asyncio
import sys

from app import db, brawl_api

try:  # la consola de Windows (cp1252) no admite algunos símbolos; forzamos UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


async def main() -> None:
    db.init_db()
    if not brawl_api.TOKEN:
        print("Falta BRAWL_API_TOKEN en el .env; no se puede consultar la API.")
        return

    tags = [p["tag"] for p in db.list_players()]
    if not tags:
        print("No hay jugadores en la base de datos.")
        return

    print(f"Actualizando la colección de {len(tags)} jugador(es) desde la API…")
    ok, fail = 0, 0
    for tag in tags:
        try:
            prof = await brawl_api.get_player(tag)
            n = db.snapshot_brawlers(tag, prof.get("brawlers"))
            ok += 1
            print(f"  [OK]  {tag}: {n} brawlers guardados")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"  [ERR] {tag}: {e}")

    print(f"Hecho. {ok} actualizados, {fail} con error.")


if __name__ == "__main__":
    asyncio.run(main())
