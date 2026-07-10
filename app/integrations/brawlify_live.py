"""Cliente de Brawlify LIVE (`api.brawlify.com`) — rotación de eventos, mapas y modos EN VIVO.

A diferencia de BrawlAPI (`api.brawlapi.com`, estático, cuyo `/v1/events` va vacío), la API LIVE de
Brawlify sí da la ROTACIÓN activa y próxima, con el `slot` (Trofeos/Ranked), el mapa y el modo. Es la
pieza que faltaba para el POOL COMPETITIVO de mapas (ver docs/integraciones-apis.md, caso de uso 1).

OJO: `api.brawlify.com` puede responder 403 a IPs de datacenter pese a las cabeceras de navegador.
Verificar en el VPS de producción antes de cablearlo (`curl -A 'Mozilla/5.0' .../v1/events`). Si allí
también da 403, usar la API oficial de Supercell `/v1/events` como fuente de rotación.

Estado: SCAFFOLD. Funciones listas, pero NADA de esto se llama todavía desde la app.
"""

from __future__ import annotations

from . import _client

_BASE = "https://api.brawlify.com/v1"

_TTL_EVENTS = 15 * 60      # la rotación cambia cada pocas horas; 15 min sobra
_TTL_CATALOG = 12 * 3600   # mapas/modos cambian raramente


def get_events(force: bool = False) -> dict:
    """Rotación en vivo: `{active: [...], upcoming: [...]}`. `{}` si la fuente no responde.

    Cada entrada trae `slot` (p. ej. Ranked), `startTime`/`endTime`, `map` y `map.gameMode`."""
    return _client.get_json(f"{_BASE}/events", ttl=_TTL_EVENTS, force=force) or {}


def get_maps(force: bool = False) -> list:
    """Catálogo de mapas con metadatos ricos y flag de rotación (`disabled`). `[]` si falla."""
    data = _client.get_json(f"{_BASE}/maps", ttl=_TTL_CATALOG, force=force) or {}
    return data.get("list") or []


def get_gamemodes(force: bool = False) -> list:
    """Catálogo de modos (nombre, color, icono, si está activo). `[]` si falla."""
    data = _client.get_json(f"{_BASE}/gamemodes", ttl=_TTL_CATALOG, force=force) or {}
    return data.get("list") or []


def ranked_pool(force: bool = False) -> list[dict]:
    """Pares `{map, mode}` actualmente en el POOL COMPETITIVO (slots tipo Ranked/Competitivo), de la
    rotación activa+próxima. Vacío si la fuente no responde o el slot no encaja — el llamante debe
    tener su propio fallback (agregación de `soloRanked`/`teamRanked` del battlelog). Ver [[modos-mapas]].

    Conservador a propósito: solo cuenta slots cuyo nombre contiene 'ranked' o 'competit', para no
    colar mapas de la rotación de TROFEOS en el pool competitivo si el formato cambia."""
    ev = get_events(force=force)
    out: list[dict] = []
    seen: set = set()
    for e in (ev.get("active") or []) + (ev.get("upcoming") or []):
        slot = ((e.get("slot") or {}).get("name") or "").lower()
        if "ranked" not in slot and "competit" not in slot:
            continue
        mp = e.get("map") or {}
        name = mp.get("name")
        mode = (mp.get("gameMode") or {}).get("name")
        if name and (name, mode) not in seen:
            seen.add((name, mode))
            out.append({"map": name, "mode": mode})
    return out


def ranked_map_names(force: bool = False) -> list[str]:
    """Solo los nombres de mapa del pool competitivo (compat)."""
    return [x["map"] for x in ranked_pool(force=force)]


def probe() -> dict:
    """Verifica el egress a Brawlify LIVE desde el servidor (GET real a /v1/events). No lanza."""
    return _client.probe(f"{_BASE}/events")
