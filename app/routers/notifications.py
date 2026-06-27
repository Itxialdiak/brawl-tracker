"""Rutas de notificaciones: listar, no leídas, marcar, borrar.

Extraído de main.py; se incluye con app.include_router()."""
from fastapi import APIRouter, Depends
from .. import db, auth

router = APIRouter()


# --------------------------- Notificaciones (Fase 6) ---------------------------

@router.get("/api/notifications")
def api_notifications_list(user: dict = Depends(auth.require_user)):
    items = db.list_notifications(user["id"])
    return {"items": items, "unread": db.count_unread_notifications(user["id"])}


@router.get("/api/notifications/unread-count")
def api_notifications_unread(user: dict = Depends(auth.require_user)):
    return {"unread": db.count_unread_notifications(user["id"])}


@router.post("/api/notifications/{nid}/read")
def api_notification_read(nid: int, user: dict = Depends(auth.require_user)):
    db.mark_notification_read(user["id"], nid)
    return {"ok": True, "unread": db.count_unread_notifications(user["id"])}


@router.post("/api/notifications/read-all")
def api_notifications_read_all(user: dict = Depends(auth.require_user)):
    n = db.mark_all_notifications_read(user["id"])
    return {"ok": True, "marked": n}


@router.delete("/api/notifications/{nid}")
def api_notification_delete(nid: int, user: dict = Depends(auth.require_user)):
    db.delete_notification(user["id"], nid)
    return {"ok": True}


@router.delete("/api/notifications")
def api_notifications_delete_all(user: dict = Depends(auth.require_user)):
    n = db.delete_all_notifications(user["id"])
    return {"ok": True, "deleted": n}
