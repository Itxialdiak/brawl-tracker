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


def scope_label_from(filters: dict) -> str:
    parts = []
    if filters.get("brawler"): parts.append(filters["brawler"])
    if filters.get("mode"): parts.append(f"modo {filters['mode']}")
    if filters.get("map"): parts.append(f"mapa {filters['map']}")
    return " · ".join(parts) if parts else "Cuenta entera"


def build_summary(player: str, brawler=None, mode=None, map=None) -> dict:
    """Reúne las estadísticas relevantes en texto compacto para el prompt."""
    f = {"player": player}
    if brawler: f["brawler"] = brawler
    if mode: f["mode"] = mode
    if map: f["map"] = map
    ov = db.overview(f)
    by_mode = db.winrate_by("mode", f)
    by_map = db.winrate_by("map", f)
    vs = db.winrate_vs(f)
    by_brawler = [] if brawler else db.winrate_by("brawler", f)

    L = []
    bits = []
    if brawler: bits.append(f"el brawler {brawler}")
    if mode: bits.append(f"el modo {mode}")
    if map: bits.append(f"el mapa {map}")
    L.append("Ámbito: " + (", ".join(bits) if bits else "la cuenta entera") + ".")
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


async def generate_report(player: str, filters: dict) -> tuple[str, str]:
    """Genera (nombre, contenido) de un informe. El nombre lo decide Claude según el ámbito."""
    label = scope_label_from(filters)
    if not API_KEY:
        raise RuntimeError("Falta ANTHROPIC_API_KEY en el .env. Saca una en https://console.anthropic.com y reinicia.")

    summary = build_summary(player, filters.get("brawler"), filters.get("mode"), filters.get("map"))
    if summary["total"] < MIN_BATTLES:
        content = (f"Aún hay muy pocos datos ({summary['total']} partidas) en este ámbito ({label}) "
                   "para un análisis útil. Deja el tracker corriendo y juega más partidas, o amplía los filtros.")
        return f"Informe · {label}", content

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise RuntimeError("Falta el paquete 'anthropic'. Ejecuta: pip install -r requirements.txt")

    client = AsyncAnthropic(api_key=API_KEY)
    msg = await client.messages.create(
        model=MODEL, max_tokens=1600, system=SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Ámbito del informe: {label}.\n"
                "En la PRIMERA línea escribe exactamente 'TÍTULO: ' seguido de un nombre corto y "
                "descriptivo para este informe según el ámbito (por ejemplo 'Informe general', "
                "'Análisis de Shelly', 'Rendimiento en Atrapagemas', 'Shelly en Mina Rocosa'). "
                "Desde la segunda línea en adelante, el análisis y los consejos.\n\n"
                "Estas son mis estadísticas en Brawl Stars:\n\n" + summary["text"]
            ),
        }],
    )
    try:
        db.log_ai_usage("report", msg.usage.input_tokens, msg.usage.output_tokens)
    except Exception:  # noqa: BLE001
        pass
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    return _split_title(text, label)


def _split_title(text: str, fallback: str) -> tuple[str, str]:
    lines = text.split("\n")
    if lines and lines[0].strip().upper().startswith("TÍTULO:"):
        name = lines[0].split(":", 1)[1].strip()
        body = "\n".join(lines[1:]).strip()
        return (name or f"Informe · {fallback}"), body
    return f"Informe · {fallback}", text


# --------------------------- Fase 6: resumen de evento para seguidores ---------------------------

async def generate_event_summary(ctx: str) -> str:
    """Una sola llamada a Claude: un resumen breve (para notificar a los seguidores)
    de cómo va el evento tras la última ronda/jornada. `ctx` es texto ya preparado."""
    if not API_KEY:
        raise RuntimeError("Falta ANTHROPIC_API_KEY en el .env.")
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise RuntimeError("Falta el paquete 'anthropic'. Ejecuta: pip install -r requirements.txt")
    client = AsyncAnthropic(api_key=API_KEY)
    msg = await client.messages.create(
        model=MODEL, max_tokens=350,
        system=("Eres el cronista de un torneo de Brawl Stars. Escribe en castellano un resumen "
                "BREVE (2-4 frases, máximo ~90 palabras) y ameno para avisar a los seguidores de "
                "cómo va el evento: menciona los resultados más destacados de la última ronda y cómo "
                "queda la clasificación (líder y poco más). Tono cercano, sin markdown, sin títulos, "
                "sin listas, solo el texto del aviso. No te inventes datos que no estén en el contexto."),
        messages=[{"role": "user", "content": ctx}],
    )
    try:
        db.log_ai_usage("event_summary", msg.usage.input_tokens, msg.usage.output_tokens)
    except Exception:  # noqa: BLE001
        pass
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
