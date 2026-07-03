"""Rutas para compartir en redes (fase F).

`/api/social/config` dice al cliente qué plataformas ofrecen enlace de intención y cuáles tienen la
publicación directa (OAuth) configurada. La publicación directa real requiere claves por plataforma
(ver app/social.py); mientras no estén, se devuelve un aviso claro y el cliente usa la vía de enlace."""
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from .. import auth, social

router = APIRouter()


@router.get("/api/social/config")
def api_social_config(user: dict = Depends(auth.require_user)):
    return {"platforms": social.platforms_status()}


@router.post("/api/social/{platform}/post")
def api_social_post(platform: str, payload: dict = Body(default={}), user: dict = Depends(auth.require_user)):
    """Publicación DIRECTA (OAuth). Requiere app registrada + claves en el entorno del servidor.
    Sin ellas, responde con un aviso para que el cliente ofrezca la vía de enlace/imagen."""
    p = social.get_platform(platform)
    if not p:
        return JSONResponse({"error": "Plataforma desconocida."}, status_code=404)
    if not social.is_configured(p):
        return JSONResponse(
            {"error": f"La publicación directa en {p['name']} aún no está configurada "
                      f"(faltan las claves de la app en el servidor). Usa el botón de compartir por enlace."},
            status_code=400)
    # Andamiaje: aquí irá el flujo OAuth (autorización + token) y la llamada de publicación de cada
    # API cuando existan las claves y la app aprobada. De momento no está implementado por plataforma.
    return JSONResponse(
        {"error": f"La publicación directa en {p['name']} está configurada pero el flujo aún no "
                  f"está implementado. Próximamente."},
        status_code=501)
