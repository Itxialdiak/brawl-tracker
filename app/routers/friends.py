"""Rutas de la base social: amigos y solicitudes de amistad.

Todas las operaciones van por el usuario en sesión (auth.require_user), de modo que un
usuario solo ve y modifica SUS amistades/solicitudes (aislamiento de cuenta). El JS de
cliente es visible en el navegador: aquí no van secretos."""
from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from .. import db, auth

router = APIRouter()


@router.get("/api/friends")
def api_friends(user: dict = Depends(auth.require_user)):
    """Lista de amigos + solicitudes entrantes y salientes del usuario en sesión."""
    return {
        "friends": db.list_friends(user["id"]),
        "incoming": db.list_incoming_requests(user["id"]),
        "outgoing": db.list_outgoing_requests(user["id"]),
    }


@router.get("/api/friends/count")
def api_friends_count(user: dict = Depends(auth.require_user)):
    """Nº de solicitudes de amistad pendientes (para el punto rojo del menú)."""
    return {"incoming": db.count_incoming_requests(user["id"])}


@router.get("/api/friends/search")
def api_friends_search(q: str = Query(""), user: dict = Depends(auth.require_user)):
    """Busca usuarios por nombre para enviarles solicitud. Marca su relación actual contigo."""
    out = []
    for u in db.search_users(q, user["id"]):
        rel = "none"
        if db.are_friends(user["id"], u["id"]):
            rel = "friend"
        elif db.friend_request_status(user["id"], u["id"]) == "pending":
            rel = "outgoing"
        elif db.friend_request_status(u["id"], user["id"]) == "pending":
            rel = "incoming"
        out.append({**u, "relation": rel})
    return {"users": out}


@router.post("/api/friends/request")
def api_friend_request(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    """Envía una solicitud de amistad a otro usuario (por id o por nombre de usuario)."""
    b = payload or {}
    target = db.get_user_by_id(b["user_id"]) if b.get("user_id") else (
        db.get_user_by_username((b.get("username") or "").strip()) if b.get("username") else None)
    if not target:
        return JSONResponse({"error": "No existe ese usuario."}, status_code=404)
    res = db.send_friend_request(user["id"], target["id"])
    if res["status"] == "self":
        return JSONResponse({"error": "No puedes añadirte a ti mismo."}, status_code=400)
    if res["status"] == "pending":
        try:
            db.notify_many([target["id"]], "friend_request", "Nueva solicitud de amistad",
                           f"@{user['username']} quiere ser tu amigo.")
        except Exception:  # noqa: BLE001
            pass
    elif res["status"] == "friends":
        try:  # había solicitud inversa: ahora sois amigos, avisa al otro
            db.notify_many([target["id"]], "friend_accepted", "Solicitud de amistad aceptada",
                           f"Ya sois amigos con @{user['username']}.")
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "status": res["status"]}


@router.post("/api/friends/requests/{req_id}/accept")
def api_friend_accept(req_id: int, user: dict = Depends(auth.require_user)):
    if not db.accept_friend_request(req_id, user["id"]):
        return JSONResponse({"error": "No se pudo aceptar la solicitud."}, status_code=400)
    return {"ok": True}


@router.post("/api/friends/requests/{req_id}/reject")
def api_friend_reject(req_id: int, user: dict = Depends(auth.require_user)):
    if not db.reject_friend_request(req_id, user["id"]):
        return JSONResponse({"error": "No se pudo rechazar/cancelar la solicitud."}, status_code=400)
    return {"ok": True}


@router.delete("/api/friends/{friend_id}")
def api_friend_remove(friend_id: int, user: dict = Depends(auth.require_user)):
    db.remove_friend(user["id"], friend_id)
    return {"ok": True}
