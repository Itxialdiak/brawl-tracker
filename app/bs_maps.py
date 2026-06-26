"""Catálogo de modos y mapas de Brawl Stars (para partidas amistosas).

Nombres en castellano. Los iconos de modo NO se hardcodean aquí: se obtienen en
caliente de Brawlify (vía app.assets, emparejando por nombre en inglés con el
mapa EN de abajo), para que siempre correspondan al modo real.

Cada modo tiene un `kind`:
  core    -> modo 3v3 estándar (vale para individual y equipos)
  duel    -> Duelos (1v1, solo eventos individuales)
  sd_solo / sd_duo -> Supervivencia en eventos individuales
  sd_trio -> Supervivencia en eventos de equipos (3v3)
"""

from __future__ import annotations

import random

# Mapas de Supervivencia (compartidos por solo/dúo/trío)
_SD = ["Choque de Cactus", "Anillo Tóxico", "Cueva Resbaladiza", "Refugio Acogedor",
       "Estanque Falso", "Plantas Rodadoras", "Caída Mortal", "Cráneo de la Suerte"]

MODES = [
    {"name": "Atrapagemas", "kind": "core", "maps": [
        "Mina Dura", "Cueva Cristalina", "Mina Abierta", "Última Parada",
        "Doble Riel", "Reserva Sembrada", "Cráter de Gemas", "Cerrojo Recién Hecho"]},
    {"name": "Balón Brawl", "kind": "core", "maps": [
        "Súper Playa", "Centro Comercial", "Pinball Soñado", "Campo Despejado",
        "Patio Trasero", "Cancha Encajonada", "Penalti", "Trampa Triple"]},
    {"name": "Atraco", "kind": "core", "maps": [
        "Zona Segura", "Botín", "Excavación", "Puente Demasiado Lejos",
        "Bandidos al Acecho", "Tubería Caliente"]},
    {"name": "Caza Estelar", "kind": "core", "maps": [
        "Cañón Escondido", "Estrella Solitaria", "Hoyo Caliente",
        "Cementerio Central", "Tierra de Nadie", "Cala Resbaladiza"]},
    {"name": "Zona Restringida", "kind": "core", "maps": [
        "Anillo de Fuego", "Rejilla", "Hojas Caídas", "Parque Central",
        "Cancha Dividida", "Frenos en Llamas"]},
    {"name": "Noqueo", "kind": "core", "maps": [
        "Belleza Oculta", "Fin de la Línea", "Jardín Flotante",
        "Nuevos Horizontes", "Cráneo Árido", "Cizaña"]},
    {"name": "Duelos", "kind": "duel", "maps": [
        "Choque de Cactus", "Anillo Tóxico", "Refugio Acogedor", "Doble Problema"]},
    {"name": "Supervivencia solo", "kind": "sd_solo", "maps": list(_SD)},
    {"name": "Supervivencia dúo", "kind": "sd_duo", "maps": list(_SD)},
    {"name": "Supervivencia trío", "kind": "sd_trio", "maps": list(_SD)},
]

# nombre castellano -> nombre en inglés (para emparejar el icono de Brawlify)
EN = {
    "Atrapagemas": "Gem Grab", "Balón Brawl": "Brawl Ball", "Atraco": "Heist",
    "Caza Estelar": "Bounty", "Zona Restringida": "Hot Zone", "Noqueo": "Knockout",
    "Duelos": "Duels", "Supervivencia solo": "Showdown",
    "Supervivencia dúo": "Showdown", "Supervivencia trío": "Showdown",
}

# nombre castellano -> código de modo del battlelog (para la detección automática)
MODE_CODE = {
    "Atrapagemas": "gemgrab", "Balón Brawl": "brawlball", "Atraco": "heist",
    "Caza Estelar": "bounty", "Zona Restringida": "hotzone", "Noqueo": "knockout",
    "Duelos": "duels", "Supervivencia solo": "soloshowdown",
    "Supervivencia dúo": "duoshowdown", "Supervivencia trío": "trioshowdown",
}

_MAP_TO_MODE = {}
for _m in MODES:
    for _mp in _m["maps"]:
        _MAP_TO_MODE.setdefault(_mp, _m["name"])


def catalog() -> list:
    """Lista completa de modos con sus mapas y su `kind` (sin icono)."""
    return [{"name": m["name"], "kind": m["kind"], "maps": list(m["maps"])} for m in MODES]


def allowed_modes(event_mode: str, showdown: str) -> list:
    """Modos permitidos según el tipo de evento y la política de supervivencia.
    showdown: 'include' | 'exclude' | 'only'."""
    teams = event_mode == "teams"
    core = [m for m in MODES if m["kind"] == "core"]
    if not teams:  # Duelos solo en eventos individuales (1v1)
        core += [m for m in MODES if m["kind"] == "duel"]
    sd_kinds = {"sd_trio"} if teams else {"sd_solo", "sd_duo"}
    sd = [m for m in MODES if m["kind"] in sd_kinds]
    if showdown == "only":
        chosen = sd
    elif showdown == "exclude":
        chosen = core
    else:  # include
        chosen = core + sd
    return [m["name"] for m in chosen]


def mode_for_map(map_name: str) -> str | None:
    return _MAP_TO_MODE.get((map_name or "").strip())


def random_mode_map(event_mode: str = "individual", showdown: str = "exclude") -> tuple:
    """(modo, mapa) al azar dentro de los modos permitidos para el evento."""
    names = set(allowed_modes(event_mode, showdown))
    pool = [(m["name"], mp) for m in MODES if m["name"] in names for mp in m["maps"]]
    return random.choice(pool) if pool else (None, None)
