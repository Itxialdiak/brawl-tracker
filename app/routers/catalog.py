"""Rutas de catálogo visual: recursos de Brawlify y modos/mapas de Brawl Stars.

Extraído de main.py; se incluye con app.include_router()."""
from fastapi import APIRouter, Depends
from .. import auth, assets, bs_maps

router = APIRouter()


@router.get("/api/assets")
async def api_assets():
    """Retratos de brawlers, iconos de modo (con color) e imágenes de mapas (Brawlify).
    PÚBLICO: son solo URLs de imágenes (dato no sensible), así los invitados también ven
    los retratos en el perfil público. Cacheado en servidor."""
    return await assets.get_assets()


@router.get("/api/bs/modes-maps")
async def api_bs_modes_maps(user: dict = Depends(auth.require_user)):
    """Catálogo de modos y mapas de Brawl Stars con el icono real de cada modo."""
    data = await assets.get_assets()
    mmap = data.get("modes") or {}

    def icon_for(es_name):
        en = (bs_maps.EN.get(es_name) or es_name).lower()
        info = mmap.get(en)
        if not info and "showdown" in en:
            info = mmap.get("solo showdown") or mmap.get("showdown")
        return (info or {}).get("icon")
    # Mapas ACTIVOS reales (jugables en amistoso hoy), de BrawlAPI; respaldo hardcodeado.
    live = bs_maps.catalog_with_live(data.get("maps_by_mode"))
    modes = [{**m, "icon": icon_for(m["name"])} for m in live]
    return {"modes": modes}
