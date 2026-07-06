"""
Capa de consejos (coaching) con Claude.

Reúne las estadísticas reales del jugador (o de un brawler concreto) y se las
pasa a Claude para que dé un análisis y consejos accionables, en castellano.

Necesita ANTHROPIC_API_KEY en el .env. Saca la tuya en https://console.anthropic.com
El modelo se puede cambiar con ANTHROPIC_MODEL (por defecto claude-sonnet-4-6).
"""

from __future__ import annotations

import os
from . import db, retos

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

MIN_BATTLES = 3  # por debajo de esto no merece la pena analizar

# Catálogo de modelos del Sensei. El "standard" es el configurado por entorno (Sonnet, barato).
# Los premium (Opus) hacen un análisis MÁS PROFUNDO, cuestan más "Pergaminos" (cuando el sistema
# esté activo) y de momento están RESTRINGIDOS a administradores. `tokens_cost` = coste en Pergaminos.
MODELS = {
    "standard": {"id": MODEL, "label": "Sensei", "sub": "análisis estándar",
                 "tokens_cost": 1, "admin_only": False, "max_tokens": 3200},
    "opus-4-6": {"id": "claude-opus-4-6", "label": "Gran Sensei · Opus 4.6", "sub": "análisis profundo",
                 "tokens_cost": 5, "admin_only": True, "max_tokens": 5000},
    "opus-4-7": {"id": "claude-opus-4-7", "label": "Gran Sensei · Opus 4.7", "sub": "análisis profundo",
                 "tokens_cost": 6, "admin_only": True, "max_tokens": 5000},
    "opus-4-8": {"id": "claude-opus-4-8", "label": "Gran Sensei · Opus 4.8", "sub": "análisis profundo (máximo)",
                 "tokens_cost": 7, "admin_only": True, "max_tokens": 5500},
}
DEFAULT_MODEL_KEY = "standard"


def resolve_model(key: str | None, is_admin: bool) -> dict:
    """Devuelve la config del modelo pedido si el usuario puede usarlo; si no, el estándar."""
    m = MODELS.get(key or "")
    if not m or (m.get("admin_only") and not is_admin):
        return MODELS[DEFAULT_MODEL_KEY]
    return m


def models_for(is_admin: bool) -> list:
    """Modelos que puede elegir el usuario (los premium solo si es admin)."""
    return [{"key": k, "label": m["label"], "sub": m["sub"], "tokens_cost": m["tokens_cost"],
             "premium": bool(m["admin_only"]), "default": k == DEFAULT_MODEL_KEY}
            for k, m in MODELS.items() if (is_admin or not m["admin_only"])]


def configured() -> bool:
    return bool(API_KEY)


SYSTEM = (
    "Eres un Sensei (maestro entrenador) de Brawl Stars: cercano, motivador, honesto y RIGUROSO. "
    "Analizas las estadísticas REALES de un alumno —win rate, RENDIMIENTO AJUSTADO (win rate "
    "encogido por nº de partidas y corregido por la dificultad de copas de los rivales), la FIABILIDAD "
    "de cada dato, los ROLES que juega, su FLEXIBILIDAD/variabilidad, el rating de cuenta y el meta "
    "comunitario— y devuelves un plan de mejora en castellano. Básate SOLO en los datos que te dan; "
    "no inventes cifras ni partidas. Puedes usar conocimiento del juego (estilo de cada brawler, "
    "posicionamiento, control de mapa, gestión de rangos) pero conéctalo siempre con los patrones del "
    "alumno y, cuando te lo den, con el meta comunitario.\n"
    "REGLAS DE RIGOR: (a) prioriza el RENDIMIENTO AJUSTADO y la FIABILIDAD sobre el win rate crudo; "
    "con muestras pequeñas (fiabilidad baja) sé prudente y DILO, no saques conclusiones tajantes; "
    "(b) señala el posible SESGO cuando los datos son escasos o están concentrados en pocos brawlers/roles; "
    "(c) analiza su perfil de ROLES y su FLEXIBILIDAD (¿especialista o versátil?, ¿en qué roles rinde y en "
    "cuáles no?).\n"
    "ESTRUCTURA DEL INFORME (cada apartado con su título en **negrita**): "
    "(1) **Aprecio** — méritos y logros reales según los datos, sé específico y motiva; "
    "(2) **Roles y flexibilidad** — qué roles juegas y dominas, tu versatilidad y qué implica para tu juego; "
    "(3) **Áreas de mejora** — debilidades, malos emparejamientos, mapas/modos/brawlers flojos, con el porqué; "
    "(4) **Fiabilidad y sesgo** — qué conclusiones son sólidas y cuáles provisionales por poca muestra o sesgo; "
    "(5) **Qué practicar** — pasos accionables y medibles; "
    "(6) **Análisis final** — una síntesis clara con la prioridad nº1 y el rumbo a seguir. "
    "No uses tablas. Extiéndete donde los datos lo justifiquen y sé breve donde no.\n"
    "Entre los retos, incluye SIEMPRE alguna MISIÓN DE CALIDAD DE DATOS (métrica \"games\": "
    "'juega N partidas más con <brawler> o en <modo>') allí donde la muestra sea escasa o la fiabilidad "
    "baja, para que los próximos análisis sean más fiables."
)


_LANG_NAMES = {"en": "English", "fr": "French", "de": "German", "zh": "Chinese (Simplified)",
               "ko": "Korean", "ja": "Japanese", "eu": "Basque (Euskera)", "ca": "Catalan"}


def _system_for(lang: str | None) -> str:
    """SYSTEM con override de idioma: el Sensei responde en el idioma de la app."""
    lang = (lang or "es").lower()
    if lang != "es" and lang in _LANG_NAMES:
        return SYSTEM + (
            f" MUY IMPORTANTE: aunque estas instrucciones estén en castellano, escribe TODO el informe, "
            f"el título y los retos (name, theme, description) en {_LANG_NAMES[lang]}, NUNCA en castellano. "
            "Mantén EXACTAMENTE los marcadores literales 'TÍTULO:' y '<<<RETOS>>>' sin traducir ni cambiar, "
            "y las CLAVES del JSON en inglés (name, theme, description, difficulty, conditions, metric, "
            "target, scope, min_games)."
        )
    return SYSTEM


def scope_label_from(filters: dict) -> str:
    parts = []
    if filters.get("brawler"): parts.append(filters["brawler"])
    if filters.get("mode"): parts.append(f"modo {filters['mode']}")
    if filters.get("map"): parts.append(f"mapa {filters['map']}")
    if filters.get("role"): parts.append(f"rol {filters['role']}")
    return " · ".join(parts) if parts else "Cuenta entera"


def build_summary(player: str, brawler=None, mode=None, map=None, role=None) -> dict:
    """Reúne las estadísticas relevantes en texto compacto para el prompt."""
    f = {"player": player}
    if brawler: f["brawler"] = brawler
    if mode: f["mode"] = mode
    if map: f["map"] = map
    if role: f["role"] = role
    ov = db.overview(f)
    by_mode = db.winrate_by("mode", f)
    by_map = db.winrate_by("map", f)
    vs = db.winrate_vs(f)
    by_brawler = [] if brawler else db.winrate_by("brawler", f)
    by_role = db.winrate_by_role(f)
    account_wide = not any([brawler, mode, map, role])

    L = []
    bits = []
    if brawler: bits.append(f"el brawler {brawler}")
    if mode: bits.append(f"el modo {mode}")
    if map: bits.append(f"el mapa {map}")
    L.append("Ámbito: " + (", ".join(bits) if bits else "la cuenta entera") + ".")
    L.append("NOTA DE LECTURA: 'rend' = rendimiento ajustado (win rate encogido por nº de partidas + "
             "dificultad por copas; más fiable que el win rate crudo). 'fiab' = fiabilidad del dato en % "
             "(sube con el nº de partidas). Prioriza 'rend' y 'fiab' sobre el win rate crudo.")
    wr = ov["winrate"]
    L.append(
        f"Global: {ov['total']} partidas, win rate {wr if wr is not None else 's/d'}%, "
        f"{ov['wins']}V-{ov['losses']}D, balance de trofeos {ov['trophy_delta']:+d}, "
        f"jugador estelar {ov['star_rate'] if ov['star_rate'] is not None else 's/d'}%."
    )
    if account_wide:
        try:
            rt = db.account_rating(player)
            if rt and rt.get("overall") is not None:
                L.append(
                    f"Rating de cuenta (BrawlSensei): {round(rt['overall'])}/100 ({rt.get('tier', '')}). "
                    f"Sub-scores — Colección {round(rt.get('collection') or 0)}, Maestría {round(rt.get('mastery') or 0)}, "
                    f"Eficiencia {round(rt.get('efficiency') or 0)}, Pushing {round(rt.get('pushing') or 0)}.")
        except Exception:  # noqa: BLE001
            pass
    if ov.get("annotated"):
        L.append(
            f"Stats manuales (sobre {ov['annotated']} partidas anotadas a mano, muestra parcial): "
            f"media de asesinatos {ov['avg_kills']}, muertes {ov['avg_deaths']}, "
            f"daño {ov['avg_damage']}, curación {ov['avg_healing']}."
        )

    # Roles y flexibilidad
    rok = [r for r in by_role if (r.get("total") or 0) >= 3 and r.get("winrate") is not None]
    if rok:
        rok = sorted(rok, key=lambda r: -(r.get("usage_pct") or 0))
        L.append("Roles que juegas (uso%, win rate, rend, fiab): " + "; ".join(
            f"{r['label']} — uso {r.get('usage_pct')}%, WR {r['winrate']}%, rend {r.get('shrunk_score')}, fiab {r.get('reliability')}%"
            for r in rok))
        n_relevant = len([r for r in rok if (r.get("usage_pct") or 0) >= 10])
        n_brawlers = len([r for r in by_brawler if (r.get("total") or 0) >= 3]) if by_brawler else 0
        top_role = rok[0]
        conc = round(top_role.get("usage_pct") or 0)
        L.append(
            f"Flexibilidad: {n_relevant} rol(es) con peso relevante; {n_brawlers} brawlers con muestra suficiente; "
            f"tu rol más jugado ({top_role['label']}) concentra el {conc}% del uso. "
            "(Concentración alta = especialista, más predecible pero datos más sólidos por rol; "
            "reparto amplio = versátil y adaptable, pero datos más diluidos por rol.)")

    if by_brawler:
        top = sorted([r for r in by_brawler if r["total"] >= 2 and r["winrate"] is not None],
                     key=lambda r: -r["total"])[:12]
        if top:
            L.append("Por brawler (más jugados; WR, rend, fiab, estelar): " + "; ".join(
                f"{r['label']} {r['winrate']}% en {r['total']}p (rend {r.get('shrunk_score')}, fiab {r.get('reliability')}%, "
                f"estelar {r['star_rate'] if r['star_rate'] is not None else 's/d'}%)"
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

    # Meta comunitario (BrawlSensei) como contexto para el Sensei.
    try:
        cm = db.community_meta(mode) if mode else db.community_meta()
        cmb = [b for b in cm.get("brawlers", []) if (b.get("games") or 0) >= 5 and b.get("winrate") is not None]
        if cmb:
            top = sorted(cmb, key=lambda b: -b["winrate"])[:8]
            L.append(f"Meta comunitario ({'en ' + mode if mode else 'general'}, win rate medio {cm.get('winrate', 's/d')}%): "
                     + "; ".join(f"{b['brawler']} {b['winrate']}% (uso {b['pick_rate']}%)" for b in top))
    except Exception:  # noqa: BLE001
        pass

    # Fiabilidad y sesgo de la muestra (para que el Sensei module su confianza y proponga misiones de datos).
    low_brawlers = [r for r in (by_brawler or []) if 0 < (r.get("total") or 0) < 6]
    thin_modes = [r for r in by_mode if 0 < (r.get("total") or 0) < 6]
    notes = [f"muestra total {ov['total']} partidas"]
    if low_brawlers:
        notes.append(f"{len(low_brawlers)} brawlers con <6 partidas (WR poco fiable): "
                     + ", ".join(r["label"] for r in low_brawlers[:8]))
    if thin_modes:
        notes.append(f"{len(thin_modes)} modos con <6 partidas")
    notes.append("donde la fiabilidad sea baja, propón misiones de calidad de datos (jugar más) en vez de "
                 "conclusiones tajantes")
    L.append("Fiabilidad y sesgo: " + "; ".join(notes) + ".")

    return {"total": ov["total"], "text": "\n".join(L)}


async def generate_report(player: str, filters: dict, model_key: str | None = None,
                          is_admin: bool = False) -> tuple[str, str, list]:
    """Genera (nombre, contenido, retos) de un informe del Sensei. El nombre lo decide
    Claude; los retos son misiones medibles (validadas contra el catálogo de métricas).
    Si Claude no devuelve un JSON de retos válido, se usan retos deterministas.
    `model_key` elige el modelo (los premium/Opus solo si `is_admin`)."""
    label = scope_label_from(filters)
    model = resolve_model(model_key, is_admin)
    if not API_KEY:
        raise RuntimeError("Falta ANTHROPIC_API_KEY en el .env. Saca una en https://console.anthropic.com y reinicia.")

    summary = build_summary(player, filters.get("brawler"), filters.get("mode"), filters.get("map"), filters.get("role"))
    if summary["total"] < MIN_BATTLES:
        content = (f"Aún hay muy pocos datos ({summary['total']} partidas) en este ámbito ({label}) "
                   "para un análisis útil. Deja el tracker corriendo y juega más partidas, o amplía los filtros.")
        return f"Informe · {label}", content, []

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise RuntimeError("Falta el paquete 'anthropic'. Ejecuta: pip install -r requirements.txt")

    client = AsyncAnthropic(api_key=API_KEY)
    msg = await client.messages.create(
        model=model["id"], max_tokens=model["max_tokens"], system=_system_for(filters.get("lang")),
        messages=[{
            "role": "user",
            "content": (
                f"Ámbito del informe: {label}.\n"
                "En la PRIMERA línea escribe exactamente 'TÍTULO: ' seguido de un nombre corto, temático y "
                "evocador (NO uses 'Informe general'; por ejemplo 'La senda de Shelly', 'Maestría en "
                "Atrapagemas', 'El muro de los rivales veloces'). Desde la segunda línea, el informe "
                "(apreciación, áreas de mejora y qué practicar).\n\n"
                "Al final del todo, en una línea aparte, escribe exactamente '<<<RETOS>>>' y debajo SOLO un "
                "JSON válido (sin markdown, sin ```): un array de 8 a 20 retos personalizados (más cuantas más "
                "cosas haya que mejorar), repartidos EXACTAMENTE mitad y mitad: una mitad de MEJORA (corregir lo "
                "que el alumno hace mal) y la otra mitad POTENCIATIVOS (asentar y reforzar lo que ya hace bien). "
                "Objetivos realistas y alcanzables jugando, basados en mis datos reales, y orientados a mejorar "
                "justo las métricas de las que habla el informe. Cada reto: {\"name\": str, "
                "\"theme\": \"1-2 palabras\", \"description\": str, \"difficulty\": 1-5, \"conditions\": [...]}.\n"
                + _retos_spec() + "\n\n"
                "Estas son mis estadísticas en Brawl Stars:\n\n" + summary["text"]
            ),
        }],
    )
    try:
        db.log_ai_usage("report", msg.usage.input_tokens, msg.usage.output_tokens, model["id"])
    except Exception:  # noqa: BLE001
        pass
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()

    report_text, retos_json = text, ""
    if "<<<RETOS>>>" in text:
        report_text, retos_json = text.split("<<<RETOS>>>", 1)
    name, body = _split_title(report_text.strip(), label)
    new_retos = _parse_retos(retos_json)
    if len(new_retos) < 3:
        new_retos = fallback_retos(player, filters)
    return name, body, new_retos[:20]


def _split_title(text: str, fallback: str) -> tuple[str, str]:
    lines = text.split("\n")
    if lines and lines[0].strip().upper().startswith("TÍTULO:"):
        name = lines[0].split(":", 1)[1].strip()
        body = "\n".join(lines[1:]).strip()
        return (name or f"Informe · {fallback}"), body
    return f"Informe · {fallback}", text


# --------------------------- Retos generados por el informe ---------------------------

def _retos_spec() -> str:
    """Texto para el prompt: qué métricas puede usar Claude (las únicas verificables)."""
    lines = ["Métricas permitidas para las condiciones (USA SOLO ESTAS; son las únicas que la app puede "
             "verificar desde las partidas, NUNCA datos manuales como daño infligido):"]
    for key, m in retos.METRICS.items():
        extra = ' — admite "min_games"' if m.get("min_games") else ""
        lines.append(f'- "{key}": {m["label"]}{extra}.')
    lines.append('Cada condición es un objeto: {"metric": <clave de arriba>, "target": <número>, '
                 '"scope": {"brawler"?: "NOMBRE", "mode"?: "gemGrab", "map"?: "Nombre"}, "min_games"?: <entero>}. '
                 'El "scope" es opcional; usa los nombres EXACTOS de brawler/modo/mapa que aparecen en mis datos.')
    return "\n".join(lines)


def _parse_retos(raw: str) -> list:
    """Extrae y valida la lista de retos del JSON de Claude. Descarta lo que no cuadre
    con el catálogo de métricas."""
    import json as _json
    raw = (raw or "").strip()
    i, j = raw.find("["), raw.rfind("]")
    if i == -1 or j == -1 or j < i:
        return []
    try:
        items = _json.loads(raw[i:j + 1])
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        ok, _ = retos.validate_conditions(it.get("conditions"))
        if not ok:
            continue
        try:
            diff = int(it.get("difficulty") or 3)
        except (TypeError, ValueError):
            diff = 3
        out.append({"name": str(it.get("name") or "Reto del Sensei")[:80],
                    "theme": str(it.get("theme") or "Sensei")[:30],
                    "description": str(it.get("description") or ""),
                    "difficulty": min(5, max(1, diff)),
                    "conditions": it["conditions"]})
    return out


def fallback_retos(player: str, filters: dict) -> list:
    """Retos deterministas desde los datos del jugador (red de seguridad si Claude no
    devuelve un JSON válido). Mitad para potenciar fortalezas, mitad para corregir debilidades."""
    f = {"player": player}
    for k in ("brawler", "mode", "map"):
        if filters.get(k):
            f[k] = filters[k]
    out = []
    played = [r for r in db.winrate_by("brawler", f) if (r.get("total") or 0) >= 4 and r.get("winrate") is not None]
    strong = sorted(played, key=lambda r: -r["winrate"])[:3]
    weak = sorted(played, key=lambda r: r["winrate"])[:3]
    for r in strong:
        out.append({"name": f"Domina con {r['label']}", "theme": "Fortaleza",
                    "description": f"Sigues ganando con {r['label']} ({r['winrate']}%). Mantén el nivel.",
                    "difficulty": 2, "conditions": [{"metric": "wins", "target": 10, "scope": {"brawler": r["label"]}}]})
    for r in weak:
        out.append({"name": f"Mejora con {r['label']}", "theme": "Debilidad",
                    "description": f"Tu win rate con {r['label']} es {r['winrate']}%. Súbelo con práctica.",
                    "difficulty": 4, "conditions": [{"metric": "winrate", "target": min(99, int(r["winrate"]) + 10),
                                                     "min_games": 15, "scope": {"brawler": r["label"]}}]})
    for r in sorted([r for r in db.winrate_by("mode", f) if r.get("winrate") is not None and (r.get("total") or 0) >= 4],
                    key=lambda r: r["winrate"])[:2]:
        out.append({"name": f"Remonta en {r['label']}", "theme": "Modo",
                    "description": f"En {r['label']} vas al {r['winrate']}%. Gana partidas para mejorarlo.",
                    "difficulty": 3, "conditions": [{"metric": "wins", "target": 8, "scope": {"mode": r["label"]}}]})
    out.append({"name": "Constancia", "theme": "Hábito",
                "description": "Juega para mantener datos frescos y medir tu progreso.",
                "difficulty": 1, "conditions": [{"metric": "games", "target": 25}]})
    out.append({"name": "Caza de estrellas", "theme": "Impacto",
                "description": "Sé decisivo y conviértete en el jugador estelar.",
                "difficulty": 3, "conditions": [{"metric": "star_player", "target": 10}]})
    return out


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
