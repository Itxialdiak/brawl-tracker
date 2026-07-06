"""Rutas de autenticación: login, registro, logout, sesión, contraseña, país.

Extraído de main.py; se incluye con app.include_router()."""
from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from .. import db, auth, rbac
from ..config import (REGISTRATION_OPEN, REGISTRATION_GATE_CODE,
                      REGISTRATION_REQUIRES_APPROVAL)

router = APIRouter()


# --------------------------- Autenticación ---------------------------

def _public_user(u: dict) -> dict:
    role = rbac.role_of(u)
    return {"id": u["id"], "username": u["username"], "country": u.get("country"),
            "is_admin": bool(u.get("is_admin")), "is_translator": bool(u.get("is_translator")),
            "role": role, "role_label": rbac.LABEL.get(role, "Usuario"),
            "status": u.get("status") or "active", "is_croker": bool(u.get("is_croker")),
            "permissions": rbac.permissions(u)}


@router.get("/api/auth/config")
def api_auth_config():
    """Lo lee el frontend para (des)grisar/mostrar los botones de acceso."""
    return {
        "registration_open": REGISTRATION_OPEN,
        # Hay una vía de creación (abierta del todo, o con verja de contraseña).
        "registration_available": bool(REGISTRATION_OPEN or REGISTRATION_GATE_CODE),
        "registration_gated": bool(not REGISTRATION_OPEN and REGISTRATION_GATE_CODE),
        "requires_approval": bool(REGISTRATION_REQUIRES_APPROVAL and not REGISTRATION_OPEN),
    }


@router.get("/api/auth/me")
def api_auth_me(request: Request):
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"error": "No has iniciado sesión."}, status_code=401)
    return _public_user(u)


@router.post("/api/auth/login")
def api_auth_login(request: Request, payload: dict = Body(...)):
    username = ((payload or {}).get("username") or "").strip()
    password = (payload or {}).get("password") or ""
    u = db.get_user_by_username(username) if username else None
    if not u or not auth.verify_password(password, u["password_hash"]):
        return JSONResponse({"error": "Usuario o contraseña incorrectos."}, status_code=401)
    status = u.get("status") or "active"
    if status == "pending":
        return JSONResponse(
            {"error": "Tu cuenta aún está pendiente de aprobación por un administrador."},
            status_code=403)
    if status == "disabled":
        return JSONResponse({"error": "Tu cuenta está deshabilitada."}, status_code=403)
    request.session["user_id"] = u["id"]
    return _public_user(u)


@router.post("/api/auth/logout")
def api_auth_logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.post("/api/auth/register")
def api_auth_register(request: Request, payload: dict = Body(...)):
    payload = payload or {}
    # Vías de registro:
    #  - REGISTRATION_OPEN True  -> registro libre, cuenta activa (autologin).
    #  - Verja (REGISTRATION_GATE_CODE) -> exige la contraseña de verja; cuenta PENDIENTE.
    #  - Ninguna -> cerrado.
    gated = bool(not REGISTRATION_OPEN and REGISTRATION_GATE_CODE)
    if not REGISTRATION_OPEN and not gated:
        return JSONResponse({"error": "El registro está cerrado durante la beta."}, status_code=403)
    if gated:
        code = (payload.get("code") or "").strip()
        if code != REGISTRATION_GATE_CODE:
            return JSONResponse(
                {"error": "Contraseña de acceso al registro incorrecta."}, status_code=403)
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    email = (payload.get("email") or "").strip() or None
    if not (3 <= len(username) <= 20):
        return JSONResponse({"error": "El usuario debe tener entre 3 y 20 caracteres."}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"error": "La contraseña debe tener al menos 6 caracteres."}, status_code=400)
    if email and ("@" not in email or "." not in email or len(email) > 120):
        return JSONResponse({"error": "El email de contacto no parece válido."}, status_code=400)
    needs_approval = bool(gated and REGISTRATION_REQUIRES_APPROVAL)
    uid = db.create_user(username, auth.hash_password(password), email=email,
                         status="pending" if needs_approval else "active")
    if uid is None:
        return JSONResponse({"error": "Ese nombre de usuario ya existe."}, status_code=409)
    if needs_approval:
        # No se inicia sesión: la cuenta espera aprobación de un administrador.
        return {"pending": True,
                "message": "Cuenta creada. Un administrador debe aprobarla antes de que puedas entrar."}
    request.session["user_id"] = uid
    return _public_user(db.get_user_by_id(uid))


@router.post("/api/auth/password")
def api_auth_password(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    current = (payload or {}).get("current") or ""
    new = (payload or {}).get("new") or ""
    if not auth.verify_password(current, user["password_hash"]):
        return JSONResponse({"error": "La contraseña actual no es correcta."}, status_code=403)
    if len(new) < 6:
        return JSONResponse({"error": "La nueva contraseña debe tener al menos 6 caracteres."}, status_code=400)
    db.set_user_password(user["id"], auth.hash_password(new))
    return {"ok": True}


@router.post("/api/auth/country")
def api_auth_country(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    country = ((payload or {}).get("country") or "").strip().lower()
    if country and (len(country) != 2 or not country.isalpha()):
        return JSONResponse({"error": "El país debe ser un código de 2 letras (p. ej. ES)."}, status_code=400)
    db.set_user_country(user["id"], country or None)
    return {"ok": True, "country": country or None}
