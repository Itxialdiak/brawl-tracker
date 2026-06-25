"""
Cliente de la API oficial de Brawl Stars (a través del proxy de RoyaleAPI).

Cómo funciona el proxy:
- En la KEY das de alta la IP del proxy (45.79.218.79), no la tuya.
- Pones BRAWL_API_BASE=https://proxy.royaleapi.dev/v1 en el .env.
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


def using_proxy() -> bool:
    return "proxy.royaleapi.dev" in BASE


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
        raise RuntimeError(f"HTTP {r.status_code} ({BASE}): {detail}")
    return r.json()


async def get_battlelog(tag: str) -> list[dict]:
    data = await _get(f"/players/{_tag_path(tag)}/battlelog")
    return data.get("items", [])


async def get_player(tag: str) -> dict:
    """Perfil del jugador (trofeos, brawlers, niveles…). Útil más adelante."""
    return await _get(f"/players/{_tag_path(tag)}")
