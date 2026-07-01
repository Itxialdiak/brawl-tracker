#!/usr/bin/env python3
"""Siembra la wiki con su versión en INGLÉS.

Traduce con IA el título + cuerpo (HTML) de cada artículo actual (español) y lo guarda:
  1) en `app/data/wiki_translations_seed.json` (git-tracked, reproducible: se carga en el
     init casando por el TÍTULO original, porque los ids de nodo no son estables entre BDs);
  2) directamente en la BD local (para uso inmediato).
La comunidad puede mejorar la traducción después (pasa por revisión). Resume: solo traduce
lo que aún no esté en el seed.

  python scrape_wiki_en.py            # traduce lo que falte
  python scrape_wiki_en.py --force    # retraduce todo
"""
import os
import re
import sys
import json
import asyncio

from app import db, coach

OUT = os.path.join("app", "data", "wiki_translations_seed.json")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

SYS_TITLE = (
    "Traduce del español al INGLÉS el TÍTULO de un artículo de una wiki de estrategia de "
    "Brawl Stars. Mantén los nombres propios del juego (brawler, Star Power, Gadget, "
    "Hypercharge, nombres de brawler/modo/mapa). Devuelve SOLO el título traducido, sin "
    "comillas, sin punto final y sin nada más.")
SYS_BODY = (
    "Traduces del español al INGLÉS el cuerpo (en HTML) de un artículo de una wiki de "
    "estrategia de Brawl Stars. Traduce SOLO el texto visible y CONSERVA EXACTAMENTE todas "
    "las etiquetas, atributos y clases HTML (p, h3, ul/ol, li, table/thead/tbody/tr/th/td con "
    "su class, blockquote, img con su src, strong…). Mantén los nombres propios del juego "
    "(brawler, Star Power, Gadget, Hypercharge, nombres de brawler/modo/mapa). Devuelve SOLO "
    "el HTML traducido, sin ``` ni explicaciones.")


async def _ai_text(system: str, user: str, max_tokens: int) -> str:
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=coach.API_KEY)
    msg = await client.messages.create(model=coach.MODEL, max_tokens=max_tokens,
                                        system=system, messages=[{"role": "user", "content": user}])
    try:
        db.log_ai_usage("wiki_en", msg.usage.input_tokens, msg.usage.output_tokens)
    except Exception:  # noqa: BLE001
        pass
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


async def main():
    force = "--force" in sys.argv
    if not coach.configured():
        print("Falta ANTHROPIC_API_KEY en el .env. Sácala en https://console.anthropic.com y reinicia.")
        return
    db.init_db()
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id,type,title,body FROM wiki_nodes ORDER BY sort_order,id").fetchall()]
    conn.close()

    store, seen = [], set()
    if os.path.exists(OUT) and not force:
        try:
            store = json.load(open(OUT, encoding="utf-8"))
            seen = {(e.get("orig_title"), e.get("lang")) for e in store}
        except Exception:  # noqa: BLE001
            store = []
    print(f"Nodos: {len(rows)} | ya traducidos: {len(seen)}")

    for r in rows:
        if (r["title"], "en") in seen:
            continue
        title_en = await _ai_text(SYS_TITLE, r["title"], 300)
        body_en = None
        if r["type"] != "separator" and (r["body"] or "").strip():
            body_en = await _ai_text(SYS_BODY, r["body"], max(1000, min(8000, len(r["body"]) + 400)))
        entry = {"orig_title": r["title"], "lang": "en", "title": title_en or r["title"], "body": body_en}
        store.append(entry); seen.add((r["title"], "en"))
        db.wiki_upsert_translation(r["id"], "en", entry["title"], body_en, None)   # uso inmediato
        with open(OUT, "w", encoding="utf-8") as f:                                # guardado incremental
            json.dump(store, f, ensure_ascii=False, indent=1)
        print(f"  ✓ #{r['id']} {r['title'][:38]} → {(title_en or '')[:38]}")
    print(f"Hecho: {len(store)} traducciones EN → {OUT}")


if __name__ == "__main__":
    print("Traduciendo la wiki al inglés (IA)…")
    asyncio.run(main())
