"""Genera app/data/roles_index.json = { NOMBRE_BRAWLER_MAYUS: [rol_primario, rol_secundario] }.

Lo usa la BD para (1) filtrar partidas por rol (primario o secundario) y (2) agregar
el win rate por rol, sin depender en tiempo de ejecución del catálogo (async).
Las claves van en MAYÚSCULAS para casar con `battles.my_brawler` (la API los da así).

Reejecuta este script (o se regenera en el scrape de la wiki) si cambian los roles.
"""
import asyncio
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import assets, brawler_extra as bx


async def main():
    cat = (await assets.get_brawler_catalog())["by_id"]
    index = {}
    for bid, c in cat.items():
        name = c.get("name")
        if not name:
            continue
        primary = bx.get(bid).get("role") or bx.role_primary_fallback(name) or c.get("role")
        secondary = bx.role_secondary(name)
        roles = []
        if primary:
            roles.append(primary)
        if secondary and secondary != primary:
            roles.append(secondary)
        if roles:
            index[name.upper()] = roles
    path = os.path.join("app", "data", "roles_index.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=0)
    roles_set = sorted({r for rs in index.values() for r in rs})
    print(f"Brawlers indexados: {len(index)}")
    print(f"Roles distintos ({len(roles_set)}): {', '.join(roles_set)}")
    print("Muestra:", {k: index[k] for k in list(index)[:5]})


asyncio.run(main())
