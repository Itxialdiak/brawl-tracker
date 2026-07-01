#!/usr/bin/env python3
"""Genera app/data/map_names_es.json = { nombre_mapa_EN_minúsculas: "Nombre en español" }.

Los nombres de mapa oficiales solo llegan en inglés (API/battlelog/Brawlify); Brawl Stars los
muestra en español en el juego, pero no hay API que los dé. Aquí traducimos con IA los mapas
ACTIVOS (de BrawlAPI, los jugables en amistoso) a su nombre oficial en español, y se cachea.
El IDENTIFICADOR sigue siendo el inglés (para la detección); esto es solo para MOSTRAR.
Editable a mano para corregir. Reejecutar cuando roten mapas nuevos (los no traducidos se
muestran en inglés como respaldo).

  python scrape_map_names.py            # traduce los que falten (resume)
  python scrape_map_names.py --force    # retraduce todos
"""
import os
import sys
import json
import asyncio

from app import assets, buffs

OUT = os.path.join("app", "data", "map_names_es.json")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


async def _translate(names: list) -> dict:
    """{en: es} para una lista de nombres de mapa, vía IA (nombres OFICIALES de Brawl Stars ES)."""
    payload = "\n".join(names)
    data = await buffs._ai_json(
        ("Traduces nombres de MAPAS de Brawl Stars del inglés a su nombre OFICIAL en español "
         "(el que muestra el juego en español de España). Son nombres propios de mapas; usa la "
         "traducción oficial que conozcas. Si no la conoces con seguridad, tradúcelo de forma "
         "natural y fiel."),
        (payload + "\n\n---\nDevuelve SOLO JSON {\"m\": {\"<nombre EN>\": \"<nombre ES>\", …}} con "
         "una entrada por cada mapa de la lista, con su clave EN EXACTA como te la doy."),
        max(600, min(4000, len(payload))))
    return (data or {}).get("m") or {}


_SMALL = {"de", "la", "el", "los", "las", "y", "o", "en", "del", "al", "a", "un", "una"}


def _titlecase(s: str) -> str:
    """Estilo de Brawl Stars: cada palabra en mayúscula salvo conectores (menos la 1ª)."""
    words = s.split()
    out = []
    for i, w in enumerate(words):
        out.append(w if (i and w.lower() in _SMALL) else (w[:1].upper() + w[1:]))
    return " ".join(out)


async def main():
    force = "--force" in sys.argv
    a = await assets.get_assets()
    maps = sorted({mp for lst in (a.get("maps_by_mode") or {}).values() for mp in lst})
    store = {}
    if os.path.exists(OUT) and not force:
        try:
            with open(OUT, encoding="utf-8") as f:
                store = json.load(f)
        except Exception:  # noqa: BLE001
            store = {}
    pending = [m for m in maps if force or m.lower() not in store]
    print(f"Mapas activos: {len(maps)} | a traducir: {len(pending)}")
    for i in range(0, len(pending), 20):                       # lotes de 20 (menos fallos JSON)
        chunk = pending[i:i + 20]
        for attempt in range(2):                               # 1 reintento si falla el JSON
            tr = await _translate(chunk)
            if tr:
                break
        for en in chunk:
            es = tr.get(en) or tr.get(en.strip())
            if es:
                store[en.lower()] = _titlecase(str(es).strip())
        with open(OUT, "w", encoding="utf-8") as f:            # guardado incremental
            json.dump(store, f, ensure_ascii=False, indent=0, sort_keys=True)
        print(f"  {min(i + 20, len(pending))}/{len(pending)}…")
    # Re-normaliza mayúsculas de TODO (por si venían de una tanda anterior en minúsculas)
    store = {k: _titlecase(v) for k, v in store.items()}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=0, sort_keys=True)
    print(f"Hecho: {len(store)} mapas -> {OUT}")


if __name__ == "__main__":
    print("Traduciendo nombres de mapa al español (IA)…")
    asyncio.run(main())
