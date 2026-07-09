"""
Cliente de la API oficial de Brawl Stars (a través del proxy de RoyaleAPI).

Cómo funciona el proxy:
- En la KEY das de alta la IP del proxy (45.79.218.79), no la tuya.
- Pones BRAWL_API_BASE=https://bsproxy.royaleapi.dev/v1 en el .env.
- El proxy reenvía tu petición (con tu token) a Supercell desde su IP fija,
  así tu IP pública puede ser dinámica sin romper nada.

Verifica la IP vigente del proxy en https://docs.royaleapi.com/proxy
"""

from __future__ import annotations

import os
import httpx
from urllib.parse import quote

from .db import normalize_tag

BASE = os.environ.get("BRAWL_API_BASE", "https://api.brawlstars.com/v1").rstrip("/")
TOKEN = os.environ.get("BRAWL_API_TOKEN", "")


class NotFound(RuntimeError):
    """La API devuelve 404: el jugador/recurso no existe (tag mal escrito o cuenta
    borrada/renombrada). Se trata aparte de los errores reales (red, token, IP)."""


def using_proxy() -> bool:
    return "royaleapi.dev" in BASE


def _headers():
    return {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}


def _tag_path(tag: str) -> str:
    # '#ABC' -> '%23ABC'
    return quote(normalize_tag(tag), safe="")


async def _get(path: str) -> dict:
    """GET genérico que, ante un error, expone el motivo que da la API."""
    url = f"{BASE}{path}"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers=_headers())
    if r.status_code >= 400:
        # La API de Supercell devuelve {"reason": "...", "message": "..."} con
        # el motivo (IP no autorizada, token inválido, jugador no encontrado…).
        detail = r.text
        try:
            j = r.json()
            detail = j.get("message") or j.get("reason") or r.text
        except Exception:
            pass
        msg = f"HTTP {r.status_code} ({BASE}): {detail}"
        raise NotFound(msg) if r.status_code == 404 else RuntimeError(msg)
    return r.json()


async def get_battlelog(tag: str) -> list[dict]:
    data = await _get(f"/players/{_tag_path(tag)}/battlelog")
    return data.get("items", [])


async def get_player(tag: str) -> dict:
    """Perfil del jugador (trofeos, brawlers, niveles…). Útil más adelante."""
    return await _get(f"/players/{_tag_path(tag)}")


async def get_brawlers() -> list[dict]:
    """Lista OFICIAL de brawlers DISPONIBLES (lanzados) de Supercell. Es autoritativa para saber
    qué brawlers YA existen en el juego: los anunciados/no lanzados no aparecen aquí."""
    data = await _get("/brawlers")
    return data.get("items", []) if isinstance(data, dict) else (data or [])


async def get_events_rotation() -> list[dict]:
    """Eventos en rotación ahora mismo: cada uno con su modo y mapa."""
    data = await _get("/events/rotation")
    return data if isinstance(data, list) else data.get("items", data)


async def get_rankings(kind: str, country: str = "global", brawler_id=None, limit: int = 200) -> list[dict]:
    """Top de jugadores/clubs/brawler. country = 'global' o código de país de 2 letras."""
    country = (country or "global").lower()
    if kind == "brawlers":
        path = f"/rankings/{country}/brawlers/{brawler_id}"
    elif kind == "clubs":
        path = f"/rankings/{country}/clubs"
    else:
        path = f"/rankings/{country}/players"
    data = await _get(f"{path}?limit={limit}")
    items = data.get("items", data) if isinstance(data, dict) else data
    return items or []


async def get_club(tag: str) -> dict:
    return await _get(f"/clubs/{_tag_path(tag)}")


async def get_club_members(tag: str) -> list[dict]:
    data = await _get(f"/clubs/{_tag_path(tag)}/members")
    return data.get("items", []) if isinstance(data, dict) else (data or [])
