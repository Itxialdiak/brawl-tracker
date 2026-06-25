"""
Capa de consejos (coaching) con Claude.

Reúne las estadísticas reales del jugador (o de un brawler concreto) y se las
pasa a Claude para que dé un análisis y consejos accionables, en castellano.

Necesita ANTHROPIC_API_KEY en el .env. Saca la tuya en https://console.anthropic.com
El modelo se puede cambiar con ANTHROPIC_MODEL (por defecto claude-sonnet-4-6).
"""

from __future__ import annotations

import os
from . import db

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

MIN_BATTLES = 3  # por debajo de esto no merece la pena analizar


def configured() -> bool:
    return bool(API_KEY)


SYSTEM = (
    "Eres un entrenador experto de Brawl Stars. Analizas las estadísticas REALES "
    "de un jugador y das consejos accionables, concretos y honestos, en castellano. "
    "Básate en los datos que te dan; no inventes cifras ni partidas. Puedes usar "
    "conocimiento general del juego (estilo de cada brawler, posicionamiento, control "
    "de mapa, gestión de rangos) pero conéctalo siempre con los patrones del jugador. "
    "Señala fortalezas, debilidades, malos emparejamientos, mapas y modos flojos, y qué "
    "brawlers potenciar o dejar de pickear. Estructura la respuesta en secciones cortas "
    "con un título en **negrita** cada una. No uses tablas. Si una muestra es pequeña, "
    "dilo en vez de sacar conclusiones tajantes."
)


def build_summary(player: str, brawler: str | None = None) -> dict:
    """Reúne las estadísticas relevantes en texto compacto para el prompt."""
    f = {"player": player, "brawler": brawler} if brawler else {"player": player}
    ov = db.overview(f)
    by_mode = db.winrate_by("mode", f)
    by_map = db.winrate_by("map", f)
    vs = db.winrate_vs(f)
    by_brawler = [] if brawler else db.winrate_by("brawler", {"player": player})

    L = []
    L.append(f"Ámbito: {'el brawler ' + brawler if brawler else 'la cuenta entera'}.")
    wr = ov["winrate"]
    L.append(
        f"Global: {ov['total']} partidas, win rate {wr if wr is not None else 's/d'}%, "
        f"{ov['wins']}V-{ov['losses']}D, balance de trofeos {ov['trophy_delta']:+d}, "
        f"jugador estrella {ov['star_rate'] if ov['star_rate'] is not None else 's/d'}%."
    )
    if ov.get("annotated"):
        L.append(
            f"Stats manuales (sobre {ov['annotated']} partidas anotadas a mano, muestra parcial): "
            f"media de asesinatos {ov['avg_kills']}, muertes {ov['avg_deaths']}, "
            f"daño {ov['avg_damage']}, curación {ov['avg_healing']}."
        )

    if by_brawler:
        top = sorted([r for r in by_brawler if r["total"] >= 2 and r["winrate"] is not None],
                     key=lambda r: -r["total"])[:12]
        if top:
            L.append("Por brawler (más jugados): " + "; ".join(
                f"{r['label']} {r['winrate']}% en {r['total']}p (estrella {r['star_rate'] if r['star_rate'] is not None else 's/d'}%)"
                for r in top))

    mok = [r for r in by_mode if r["winrate"] is not None]
    if mok:
        L.append("Por modo: " + "; ".join(f"{r['label']} {r['winrate']}% ({r['total']}p)" for r in mok))

    maps_ok = [r for r in by_map if r["winrate"] is not None and r["total"] >= 2]
    if maps_ok:
        best = sorted(maps_ok, key=lambda r: -r["winrate"])[:6]
        worst = sorted(maps_ok, key=lambda r: r["winrate"])[:6]
        L.append("Mejores mapas: " + "; ".join(f"{r['label']} {r['winrate']}%" for r in best))
        L.append("Peores mapas: " + "; ".join(f"{r['label']} {r['winrate']}%" for r in worst))

    vs_ok = [r for r in vs if r["winrate"] is not None and r["total"] >= 2]
    if vs_ok:
        worst_vs = sorted(vs_ok, key=lambda r: r["winrate"])[:8]
        best_vs = sorted(vs_ok, key=lambda r: -r["winrate"])[:6]

        def trophy_ctx(r):
            if r.get("avg_enemy_trophies") is None or r.get("avg_my_trophies") is None:
                return ""
            return f" [rival ~{r['avg_enemy_trophies']}🏆 vs tú ~{r['avg_my_trophies']}🏆]"

        L.append("Rivales que te ganan: " + "; ".join(
            f"vs {r['label']} {r['winrate']}% ({r['total']}p){trophy_ctx(r)}" for r in worst_vs))
        L.append("Rivales que dominas: " + "; ".join(
            f"vs {r['label']} {r['winrate']}%{trophy_ctx(r)}" for r in best_vs))
        L.append("(Nota: ten en cuenta las copas: perder contra un rival con muchas más copas que el jugador "
                 "es esperable; perder contra uno de copas similares o menores sí señala un problema de match-up.)")

    return {"total": ov["total"], "text": "\n".join(L)}


async def generate_advice(player: str, brawler: str | None = None) -> str:
    if not API_KEY:
        raise RuntimeError("Falta ANTHROPIC_API_KEY en el .env. Saca una en https://console.anthropic.com y reinicia.")

    summary = build_summary(player, brawler)
    if summary["total"] < MIN_BATTLES:
        return (f"Aún hay muy pocos datos ({summary['total']} partidas) para un análisis útil. "
                "Deja el tracker corriendo y juega unas cuantas partidas más antes de pedir consejos.")

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise RuntimeError("Falta el paquete 'anthropic'. Ejecuta: pip install -r requirements.txt")

    client = AsyncAnthropic(api_key=API_KEY)
    msg = await client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": "Estas son mis estadísticas en Brawl Stars. Dame un análisis y consejos para mejorar:\n\n"
                       + summary["text"],
        }],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
