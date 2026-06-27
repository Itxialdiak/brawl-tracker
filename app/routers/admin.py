"""Rutas de administración: propuestas de la wiki, usuarios, jugadores, métricas, historial.

Extraído de main.py; se incluye con app.include_router()."""
import re
from fastapi import APIRouter, Body, Query, Depends
from fastapi.responses import JSONResponse
from .. import db, auth

router = APIRouter()


# --------------------------- Administración ---------------------------

@router.get("/api/admin/proposals")
def api_admin_proposals(status: str = Query("pending"), admin: dict = Depends(auth.require_admin)):
    st = status if status in ("pending", "approved", "rejected", "all") else "pending"
    return {"proposals": db.list_proposals(None if st == "all" else st)}


@router.get("/api/admin/proposals/{pid}")
def api_admin_proposal_detail(pid: int, admin: dict = Depends(auth.require_admin)):
    p = db.get_proposal(pid)
    if not p:
        return JSONResponse({"error": "No encontrado."}, status_code=404)
    current = db.get_wiki_node(p["node_id"]) if p.get("node_id") else None
    parent = None
    if p["kind"] == "create_subsection" and p["payload"].get("parent_id"):
        parent = db.get_wiki_node(p["payload"]["parent_id"])
    return {"proposal": p, "current": current, "parent": parent}


@router.post("/api/admin/proposals/{pid}/approve")
def api_admin_approve(pid: int, admin: dict = Depends(auth.require_admin)):
    ok = db.apply_proposal(pid, admin["id"])
    return {"ok": ok}


@router.post("/api/admin/proposals/{pid}/reject")
def api_admin_reject(pid: int, admin: dict = Depends(auth.require_admin)):
    p = db.get_proposal(pid)
    if not p or p["status"] != "pending":
        return JSONResponse({"error": "No disponible."}, status_code=400)
    db.set_proposal_status(pid, "rejected", admin["id"])
    return {"ok": True}


@router.post("/api/admin/proposals/approve-all")
def api_admin_approve_all(admin: dict = Depends(auth.require_admin)):
    pend = db.list_proposals("pending")
    # aplicar de más antigua a más nueva
    n = 0
    for p in sorted(pend, key=lambda x: x["id"]):
        if db.apply_proposal(p["id"], admin["id"]):
            n += 1
    return {"ok": True, "approved": n}


@router.get("/api/admin/users")
def api_admin_users(admin: dict = Depends(auth.require_admin)):
    return {"users": db.list_users()}


@router.post("/api/admin/users")
def api_admin_user_create(payload: dict = Body(...), admin: dict = Depends(auth.require_admin)):
    username = ((payload or {}).get("username") or "").strip()
    password = (payload or {}).get("password") or ""
    if not username or len(username) < 3:
        return JSONResponse({"error": "Usuario inválido (mínimo 3 caracteres)."}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"error": "La contraseña debe tener al menos 6 caracteres."}, status_code=400)
    uid = db.create_user(username, auth.hash_password(password))
    if uid is None:
        return JSONResponse({"error": "Ese usuario ya existe."}, status_code=409)
    if (payload or {}).get("is_admin"):
        db.set_user_admin(uid, True)
    return {"ok": True, "id": uid}


@router.delete("/api/admin/users/{uid}")
def api_admin_user_delete(uid: int, admin: dict = Depends(auth.require_admin)):
    if uid == admin["id"]:
        return JSONResponse({"error": "No puedes borrarte a ti mismo."}, status_code=400)
    db.delete_user(uid)
    return {"ok": True}


@router.post("/api/admin/users/{uid}/admin")
def api_admin_user_setadmin(uid: int, payload: dict = Body(...), admin: dict = Depends(auth.require_admin)):
    val = bool((payload or {}).get("is_admin"))
    if uid == admin["id"] and not val:
        return JSONResponse({"error": "No puedes quitarte tus propios permisos."}, status_code=400)
    db.set_user_admin(uid, val)
    return {"ok": True}


@router.post("/api/admin/users/{uid}/password")
def api_admin_user_password(uid: int, payload: dict = Body(...), admin: dict = Depends(auth.require_admin)):
    pw = (payload or {}).get("password") or ""
    if len(pw) < 6:
        return JSONResponse({"error": "Mínimo 6 caracteres."}, status_code=400)
    db.set_user_password(uid, auth.hash_password(pw))
    return {"ok": True}


@router.get("/api/admin/history")
def api_admin_history(admin: dict = Depends(auth.require_admin)):
    return {"history": db.list_wiki_history()}


@router.post("/api/admin/history/{hid}/revert")
def api_admin_history_revert(hid: int, admin: dict = Depends(auth.require_admin)):
    ok = db.revert_wiki_version(hid, admin["id"])
    return {"ok": ok}


@router.get("/api/admin/players")
def api_admin_players(admin: dict = Depends(auth.require_admin)):
    """Todos los jugadores trackeados, con nº de partidas y de seguidores (huérfanos
    = sin ningún usuario que los siga)."""
    return {"players": db.list_players_admin()}


@router.post("/api/admin/players")
def api_admin_players_add(payload: dict = Body(...), admin: dict = Depends(auth.require_admin)):
    """Añade jugador(es) al trackeo aunque no los siga ningún usuario. `tags` admite
    una lista o un string con player IDs separados por comas/saltos de línea."""
    raw = payload.get("tags") or payload.get("tag") or ""
    parts = re.split(r"[,\n;]+", raw) if isinstance(raw, str) else list(raw)
    seen, added, skipped = set(), [], []
    for p in parts:
        t = db.normalize_tag(str(p).strip())
        if len(t) < 4 or t in seen:
            continue
        seen.add(t)
        (added if db.add_player(t) else skipped).append(t)
    return {"added": added, "skipped": skipped}


@router.delete("/api/admin/players/{tag}")
def api_admin_player_delete(tag: str, delete_battles: bool = Query(False),
                            admin: dict = Depends(auth.require_admin)):
    """Deja de trackear al jugador. Por defecto conserva su historial; con
    delete_battles=true borra también sus partidas."""
    db.admin_remove_player(tag, delete_battles)
    return {"removed": db.normalize_tag(tag), "battles_deleted": delete_battles}


@router.get("/api/admin/metrics")
def api_admin_metrics(admin: dict = Depends(auth.require_admin)):
    """Usuarios, jugadores, partidas, informes y consumo de tokens de IA."""
    return db.admin_metrics()
