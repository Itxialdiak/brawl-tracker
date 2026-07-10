"""Integraciones con APIs externas de datos de Brawl Stars.

Capa común para consumir fuentes de terceros (Brawlify LIVE, y en el futuro otras) con el mismo
patrón robusto que `map_assets.py`: cliente httpx compartido, caché en memoria con TTL, cabeceras
de navegador y DEGRADACIÓN elegante (si la fuente falla, devolvemos lo cacheado o vacío, nunca
rompemos la petición del usuario).

Ver `docs/integraciones-apis.md` para el dictamen y el plan. NADA de esto está cableado aún a la
app: es andamiaje preparado para activarlo cuando se verifique el egress en producción.
"""

from . import brawlify_live  # noqa: F401
