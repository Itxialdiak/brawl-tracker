"""Rutas de administración: propuestas de la wiki, usuarios, jugadores, métricas, historial.

Extraído de main.py; se incluye con app.include_router()."""
import re
from fastapi import APIRouter, Body, Query, Depends
from fastapi.responses import JSONResponse
from .. import db, auth, rbac

router = APIRouter()


# --------------------------- Administración ---------------------------

@router.get("/api/admin/proposals")
def api_admin_proposals(status: str = Query("pending"), admin: dict = Depends(auth.require_perm(rbac.REVIEW_CHANGES))):
    st = status if status in ("pending", "approved", "rejected", "all") else "pending"
    return {"proposals": db.list_proposals(None if st == "all" else st)}


@router.get("/api/admin/proposals/{pid}")
def api_admin_proposal_detail(pid: int, admin: dict = Depends(auth.require_perm(rbac.REVIEW_CHANGES))):
    p = db.get_proposal(pid)
    if not p:
        return JSONResponse({"error": "No encontrado."}, status_code=404)
    current = db.get_wiki_node(p["node_id"]) if p.get("node_id") else None
    parent = None
    if p["kind"] == "create_subsection" and p["payload"].get("parent_id"):
        parent = db.get_wiki_node(p["payload"]["parent_id"])
    current_tr = None
    if p["kind"] == "translate" and p.get("node_id") and p["payload"].get("lang"):
        current_tr = db.get_wiki_translation(p["node_id"], p["payload"]["lang"])
    return {"proposal": p, "current": current, "parent": parent, "current_translation": current_tr}


@router.post("/api/admin/proposals/{pid}/approve")
def api_admin_approve(pid: int, admin: dict = Depends(auth.require_perm(rbac.REVIEW_CHANGES))):
    ok = db.apply_proposal(pid, admin["id"])
    return {"ok": ok}


@router.post("/api/admin/proposals/{pid}/reject")
def api_admin_reject(pid: int, admin: dict = Depends(auth.require_perm(rbac.REVIEW_CHANGES))):
    p = db.get_proposal(pid)
    if not p or p["status"] != "pending":
        return JSONResponse({"error": "No disponible."}, status_code=400)
    db.set_proposal_status(pid, "rejected", admin["id"])
    return {"ok": True}


@router.post("/api/admin/proposals/approve-all")
def api_admin_approve_all(admin: dict = Depends(auth.require_perm(rbac.REVIEW_CHANGES))):
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
    # Asignar rol admin al crear solo si el actor tiene autoridad para otorgarlo (solo root).
    if (payload or {}).get("is_admin"):
        target = db.get_user_by_id(uid)
        if rbac.can_assign_role(admin, target, "admin"):
            db.set_user_role(uid, "admin")
        else:
            return {"ok": True, "id": uid,
                    "warning": "Cuenta creada como usuario normal: no tienes autoridad para crear administradores."}
    return {"ok": True, "id": uid}


@router.delete("/api/admin/users/{uid}")
def api_admin_user_delete(uid: int, delete_players: bool = Query(False), admin: dict = Depends(auth.require_admin)):
    """Borra la cuenta. Por defecto conserva los jugadores asociados en el tracking; con
    `?delete_players=true` elimina también los que queden huérfanos (sin otro usuario que los siga)."""
    if uid == admin["id"]:
        return JSONResponse({"error": "No puedes borrarte a ti mismo."}, status_code=400)
    target = db.get_user_by_id(uid)
    if not target:
        return JSONResponse({"error": "No existe ese usuario."}, status_code=404)
    if not rbac.can_manage_user(admin, target):
        return JSONResponse(
            {"error": "No tienes autoridad sobre ese usuario (no puedes borrar a root ni a otros administradores)."},
            status_code=403)
    db.delete_user(uid, delete_players=delete_players)
    return {"ok": True}


@router.post("/api/admin/users/{uid}/admin")
def api_admin_user_setadmin(uid: int, payload: dict = Body(...), admin: dict = Depends(auth.require_admin)):
    """Compat: alterna admin. MISMA autorización estricta que /role (solo root reparte admin;
    nadie degrada a root ni a otros admins)."""
    val = bool((payload or {}).get("is_admin"))
    if uid == admin["id"] and not val:
        return JSONResponse({"error": "No puedes quitarte tus propios permisos."}, status_code=400)
    target = db.get_user_by_id(uid)
    if not target:
        return JSONResponse({"error": "No existe ese usuario."}, status_code=404)
    new_role = "admin" if val else "user"
    if not rbac.can_assign_role(admin, target, new_role):
        return JSONResponse({"error": "No tienes autoridad para esta acción."}, status_code=403)
    db.set_user_role(uid, new_role)
    return {"ok": True}


@router.post("/api/admin/users/{uid}/translator")
def api_admin_user_settranslator(uid: int, payload: dict = Body(...), admin: dict = Depends(auth.require_admin)):
    """Compat: alterna traductor. Exige autoridad sobre el usuario (no toca a root/otros admins)."""
    target = db.get_user_by_id(uid)
    if not target:
        return JSONResponse({"error": "No existe ese usuario."}, status_code=404)
    if uid == admin["id"] or not rbac.can_manage_user(admin, target):
        return JSONResponse({"error": "No tienes autoridad sobre ese usuario."}, status_code=403)
    db.set_user_translator(uid, bool((payload or {}).get("is_translator")))
    return {"ok": True}


@router.get("/api/admin/rbac")
def api_admin_rbac(admin: dict = Depends(auth.require_admin)):
    """Catálogo de roles + los que ESTE administrador puede asignar (para la pestaña de
    gestión de roles: arrastrar un usuario de una lista a otra)."""
    return {
        "roles": [
            {"id": r, "label": rbac.LABEL[r], "label_plural": rbac.LABEL_PLURAL[r],
             "level": rbac.LEVEL[r]}
            for r in rbac.ROLES
        ],
        "assignable": rbac.assignable_roles(admin),
        "my_role": rbac.role_of(admin),
    }


@router.post("/api/admin/users/{uid}/role")
def api_admin_user_setrole(uid: int, payload: dict = Body(...), admin: dict = Depends(auth.require_admin)):
    """Asigna un rol RBAC. Autorización estricta: un admin no puede tocar a root ni a
    otros admins, ni otorgar un rol de autoridad >= a la suya. Solo root reparte admin/root."""
    new_role = rbac.normalize((payload or {}).get("role"))
    target = db.get_user_by_id(uid)
    if not target:
        return JSONResponse({"error": "No existe ese usuario."}, status_code=404)
    if uid == admin["id"]:
        return JSONResponse({"error": "No puedes cambiar tu propio rol."}, status_code=400)
    if not rbac.can_assign_role(admin, target, new_role):
        return JSONResponse(
            {"error": "No tienes autoridad para asignar ese rol a ese usuario."}, status_code=403)
    db.set_user_role(uid, new_role)
    return {"ok": True, "role": new_role, "role_label": rbac.LABEL.get(new_role, "Usuario")}


@router.post("/api/admin/users/{uid}/approve")
def api_admin_user_approve(uid: int, admin: dict = Depends(auth.require_admin)):
    """Aprueba una cuenta pendiente (registro con verja) -> queda activa."""
    if not rbac.has_perm(admin, rbac.APPROVE_ACCOUNTS):
        return JSONResponse({"error": "No puedes aprobar cuentas."}, status_code=403)
    target = db.get_user_by_id(uid)
    if not target:
        return JSONResponse({"error": "No existe ese usuario."}, status_code=404)
    if not rbac.can_manage_user(admin, target):
        return JSONResponse({"error": "No tienes autoridad sobre esa cuenta."}, status_code=403)
    db.set_user_status(uid, "active")
    return {"ok": True, "status": "active"}


@router.post("/api/admin/users/{uid}/status")
def api_admin_user_status(uid: int, payload: dict = Body(...), admin: dict = Depends(auth.require_admin)):
    """Cambia el estado de la cuenta: active / pending / disabled. Exige autoridad sobre el
    usuario SIEMPRE (no hay atajo por estado pendiente)."""
    if not rbac.has_perm(admin, rbac.APPROVE_ACCOUNTS):
        return JSONResponse({"error": "No puedes cambiar el estado de cuentas."}, status_code=403)
    status = (payload or {}).get("status")
    if status not in ("active", "pending", "disabled"):
        return JSONResponse({"error": "Estado no válido."}, status_code=400)
    target = db.get_user_by_id(uid)
    if not target:
        return JSONResponse({"error": "No existe ese usuario."}, status_code=404)
    if uid == admin["id"]:
        return JSONResponse({"error": "No puedes cambiar tu propio estado."}, status_code=400)
    if not rbac.can_manage_user(admin, target):
        return JSONResponse({"error": "No tienes autoridad sobre ese usuario."}, status_code=403)
    db.set_user_status(uid, status)
    return {"ok": True, "status": status}


# El rol Croker es AUTOMÁTICO (según el club del jugador principal, ver db.recompute_croker);
# no hay endpoint manual para alternarlo.


@router.post("/api/admin/users/{uid}/password")
def api_admin_user_password(uid: int, payload: dict = Body(...), admin: dict = Depends(auth.require_admin)):
    pw = (payload or {}).get("password") or ""
    if len(pw) < 6:
        return JSONResponse({"error": "Mínimo 6 caracteres."}, status_code=400)
    target = db.get_user_by_id(uid)
    if not target:
        return JSONResponse({"error": "No existe ese usuario."}, status_code=404)
    # Un admin no puede resetear la contraseña de root ni de otros admins (solo root, o uno mismo).
    if uid != admin["id"] and not rbac.can_manage_user(admin, target):
        return JSONResponse({"error": "No tienes autoridad sobre ese usuario."}, status_code=403)
    db.set_user_password(uid, auth.hash_password(pw))
    return {"ok": True}


@router.get("/api/admin/history")
def api_admin_history(admin: dict = Depends(auth.require_perm(rbac.REVIEW_CHANGES))):
    return {"history": db.list_wiki_history()}


@router.post("/api/admin/history/{hid}/revert")
def api_admin_history_revert(hid: int, admin: dict = Depends(auth.require_perm(rbac.REVIEW_CHANGES))):
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
def api_admin_metrics(user: dict = Depends(auth.require_admin_panel)):
    """Usuarios, jugadores, partidas, informes y consumo de IA (tokens + coste en €).
    Los COLABORADORES ven las métricas de página pero NO las de consumo (se redacta `ai`)."""
    from .. import coach
    m = db.admin_metrics(coach.MODEL)
    if not rbac.has_perm(user, rbac.VIEW_CONSUMPTION):
        m.pop("ai", None)
        m["consumption_hidden"] = True
    return m
