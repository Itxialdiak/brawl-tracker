"""Rutas de Analíticas: overview, win rate por dimensión, enfrentamientos, informe rápido, roles, filtros.

Extraído de main.py; se incluye con app.include_router()."""
from fastapi import APIRouter, Query, Depends
from fastapi.responses import JSONResponse
from .. import db, auth
from ..api_common import _require_follow, _filters

router = APIRouter()


# --------------------------- Estadísticas ---------------------------


@router.get("/api/overview")
def api_overview(player: str = Query(None), mode: str = Query(None), map: str = Query(None),
                 brawler: str = Query(None), vs: str = Query(None), role: str = Query(None),
                 user: dict = Depends(auth.require_user)):
    _require_follow(user, player)
    return db.overview(_filters(player, mode, map, brawler, vs, role))


@router.get("/api/winrate")
def api_winrate(by: str = Query("brawler"), player: str = Query(None), mode: str = Query(None),
                map: str = Query(None), brawler: str = Query(None), vs: str = Query(None),
                role: str = Query(None), user: dict = Depends(auth.require_user)):
    _require_follow(user, player)
    try:
        return db.winrate_by(by, _filters(player, mode, map, brawler, vs, role))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/api/vs")
def api_vs(player: str = Query(None), mode: str = Query(None), map: str = Query(None),
           brawler: str = Query(None), role: str = Query(None),
           user: dict = Depends(auth.require_user)):
    _require_follow(user, player)
    return db.winrate_vs(_filters(player, mode, map, brawler, None, role))


@router.get("/api/allies")
def api_allies(player: str = Query(None), mode: str = Query(None), map: str = Query(None),
               brawler: str = Query(None), role: str = Query(None),
               user: dict = Depends(auth.require_user)):
    """Win rate con cada brawler aliado (cuando van en tu equipo). Para el modal de
    'Mejores aliados' de Analíticas."""
    _require_follow(user, player)
    return db.winrate_with_allies(_filters(player, mode, map, brawler, None, role))


@router.get("/api/report")
def api_report(player: str = Query(None), mode: str = Query(None), map: str = Query(None),
               brawler: str = Query(None), role: str = Query(None),
               user: dict = Depends(auth.require_user)):
    """Cálculos derivados para el Informe (destacados, datos cruzados, serie de trofeos)."""
    _require_follow(user, player)
    return db.report_analytics(_filters(player, mode, map, brawler, None, role))


@router.get("/api/roles")
def api_roles(player: str = Query(None), mode: str = Query(None), map: str = Query(None),
              brawler: str = Query(None), user: dict = Depends(auth.require_user)):
    """Win rate y uso por ROL (cada brawler cuenta en su rol primario y secundario).
    No aplica el filtro de rol: siempre devuelve el desglose completo, para los
    radares de 'Preferencia de Rol'/'Estilo de Juego' y el panel 'Por rol'."""
    _require_follow(user, player)
    return db.winrate_by_role(_filters(player, mode, map, brawler, None, None))


@router.get("/api/filters")
def api_filters(player: str = Query(None), user: dict = Depends(auth.require_user)):
    _require_follow(user, player)
    return db.distinct_values(player)


# La rotación oficial mezcla eventos de Copas y de Competitivo (Ranked). Los de
# Competitivo van en un bloque sincronizado de varios días (los de Copas rotan a
# diario) y son de uno de estos modos; así los separamos para mostrarlos aparte.
