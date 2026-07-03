"""Mensajería privada entre usuarios (fase E).

Todo va por el usuario en sesión (auth.require_user): cada quien solo ve y modifica SUS
conversaciones (aislamiento estricto por cuenta). El JS de cliente es visible: aquí no van secretos."""
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from .. import db, auth

router = APIRouter()

MAX_LEN = 4000


@router.get("/api/messages")
def api_messages_inbox(user: dict = Depends(auth.require_user)):
    """Bandeja: lista de conversaciones (último mensaje + no leídos) y total sin leer."""
    return {"conversations": db.list_conversations(user["id"]),
            "unread": db.count_unread_messages(user["id"])}


@router.get("/api/messages/count")
def api_messages_count(user: dict = Depends(auth.require_user)):
    """Nº de mensajes sin leer (para la insignia del menú)."""
    return {"unread": db.count_unread_messages(user["id"])}


@router.get("/api/messages/{other_id}")
def api_messages_thread(other_id: int, user: dict = Depends(auth.require_user)):
    """Conversación con otro usuario. Al abrirla se marcan como leídos los recibidos."""
    other = db.get_user_by_id(other_id)
    if not other:
        return JSONResponse({"error": "No existe ese usuario."}, status_code=404)
    db.mark_conversation_read(user["id"], other_id)
    return {"other": {"id": other["id"], "username": other["username"]},
            "messages": db.get_conversation(user["id"], other_id)}


@router.post("/api/messages")
def api_messages_send(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    """Envía un mensaje a otro usuario (por id). No puedes escribirte a ti mismo."""
    b = payload or {}
    body = (b.get("body") or "").strip()
    if not body:
        return JSONResponse({"error": "El mensaje está vacío."}, status_code=400)
    if len(body) > MAX_LEN:
        body = body[:MAX_LEN]
    target = db.get_user_by_id(b.get("to_user"))
    if not target:
        return JSONResponse({"error": "No existe ese usuario."}, status_code=404)
    if target["id"] == user["id"]:
        return JSONResponse({"error": "No puedes escribirte a ti mismo."}, status_code=400)
    db.send_message(user["id"], target["id"], body)
    try:
        db.notify_many([target["id"]], "message", "Nuevo mensaje",
                       f"@{user['username']} te ha enviado un mensaje.")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "messages": db.get_conversation(user["id"], target["id"])}


@router.post("/api/messages/read-all")
def api_messages_read_all(user: dict = Depends(auth.require_user)):
    """Marca TODAS las conversaciones como leídas."""
    db.mark_all_messages_read(user["id"])
    return {"ok": True, "unread": 0}


@router.post("/api/messages/{other_id}/read")
def api_messages_mark_read(other_id: int, user: dict = Depends(auth.require_user)):
    db.mark_conversation_read(user["id"], other_id)
    return {"ok": True, "unread": db.count_unread_messages(user["id"])}


@router.post("/api/messages/{other_id}/unread")
def api_messages_mark_unread(other_id: int, user: dict = Depends(auth.require_user)):
    """Vuelve a marcar la conversación como NO leída."""
    db.mark_conversation_unread(user["id"], other_id)
    return {"ok": True, "unread": db.count_unread_messages(user["id"])}


@router.delete("/api/messages/{other_id}")
def api_messages_delete(other_id: int, user: dict = Depends(auth.require_user)):
    """Borra (oculta) la conversación solo para el usuario en sesión."""
    db.delete_conversation(user["id"], other_id)
    return {"ok": True}
