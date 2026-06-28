"""Rutas de la sección Retos: tablón comunitario, crear/editar/borrar, apuntarse/seguir,
"tus retos", contadores y progreso. El progreso y la dificultad asignada se calculan
en app/retos.py a partir de las partidas (tracking automático, sin datos manuales)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Query, Depends, HTTPException
from fastapi.responses import JSONResponse

from .. import db, auth, retos
from ..api_common import _require_follow

router = APIRouter()


def _user_main_tag(user: dict):
    """Tag del primer jugador que sigue el usuario (para recalibrar dificultad cuando
    aún no se ha apuntado con uno concreto)."""
    players = db.list_players_for_user(user["id"])
    return players[0]["tag"] if players else None


def _sync_completion(reto: dict, user_id: int, part: dict) -> dict:
    """Calcula el progreso y, si está cumplido y aún activo, lo marca como completado
    (tracking automático). Devuelve el progreso."""
    prog = retos.reto_progress(reto, part)
    if prog["done"] and part and part.get("status") == "active":
        db.set_reto_status(reto["id"], user_id, "completed",
                           datetime.now(timezone.utc).isoformat())
        part["status"] = "completed"
        try:
            db.notify_many([user_id], "reto_done", f"¡Reto cumplido! · {reto.get('name')}",
                           "Lo verificó el seguimiento automático de tus partidas. 🎉")
        except Exception:  # noqa: BLE001
            pass
    return prog


# --------------------------- catálogo y contadores ---------------------------

@router.get("/api/retos/meta")
def api_retos_meta(user: dict = Depends(auth.require_user)):
    """Catálogo de métricas medibles (para el formulario de crear reto y el manual)."""
    return {"metrics": retos.METRICS, "max_conditions": retos.MAX_CONDITIONS}


@router.get("/api/retos/counters")
def api_retos_counters(user: dict = Depends(auth.require_user)):
    return {"sensei": db.count_completed_retos(user["id"], "sensei"),
            "community": db.count_completed_retos(user["id"], "user")}


@router.get("/api/retos/completed")
def api_retos_completed(source: str = Query(""), user: dict = Depends(auth.require_user)):
    return {"retos": db.list_completed_retos(user["id"], source or None)}


# --------------------------- tus retos / tablón ---------------------------

@router.get("/api/retos/mine")
def api_retos_mine(user: dict = Depends(auth.require_user)):
    groups = db.list_my_retos(user["id"])
    for key in ("sensei", "joined", "created"):
        for r in groups.get(key, []):
            part = db.reto_participant(r["id"], user["id"])
            if part:
                r["my_progress"] = _sync_completion(r, user["id"], part)
                r["my_status"] = part.get("status")
    return groups


@router.get("/api/retos/board")
def api_retos_board(status: str = Query(""), theme: str = Query(""),
                    user: dict = Depends(auth.require_user)):
    main_tag = _user_main_tag(user)
    rows = db.list_board_retos(user["id"], status or None, theme or None)
    for r in rows:
        r["assigned_difficulty"] = retos.recalibrate_difficulty(r, main_tag)
    return {"retos": rows}


# --------------------------- CRUD ---------------------------

@router.post("/api/retos")
def api_reto_create(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    b = payload or {}
    if not (b.get("name") or "").strip():
        return JSONResponse({"error": "Ponle un nombre al reto."}, status_code=400)
    ok, err = retos.validate_conditions(b.get("conditions"))
    if not ok:
        return JSONResponse({"error": err}, status_code=400)
    rid = db.create_reto(
        user["id"], b.get("name"), b.get("theme"), b.get("description"),
        b.get("conditions"), int(b.get("difficulty") or 3),
        b.get("visibility") or "public", b.get("time_limit_days"))
    return {"ok": True, "id": rid}


@router.get("/api/retos/{rid}")
def api_reto_detail(rid: int, user: dict = Depends(auth.require_user)):
    r = db.get_reto(rid)
    if not r:
        return JSONResponse({"error": "No existe ese reto."}, status_code=404)
    part = db.reto_participant(rid, user["id"])
    r["my"] = part
    r["participants"] = db.list_reto_participants(rid)
    r["conditions_text"] = [retos.describe_condition(c) for c in r.get("conditions", [])]
    if part and part.get("role") == "participant":
        r["my_progress"] = _sync_completion(r, user["id"], part)
    r["assigned_difficulty"] = retos.recalibrate_difficulty(
        r, (part or {}).get("player_tag") or _user_main_tag(user))
    return r


@router.put("/api/retos/{rid}")
def api_reto_update(rid: int, payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    if db.reto_creator(rid) != user["id"]:
        raise HTTPException(status_code=403, detail="No es tu reto.")
    b = payload or {}
    if "conditions" in b:
        ok, err = retos.validate_conditions(b["conditions"])
        if not ok:
            return JSONResponse({"error": err}, status_code=400)
    db.update_reto(rid, b)
    return {"ok": True}


@router.delete("/api/retos/{rid}")
def api_reto_delete(rid: int, user: dict = Depends(auth.require_user)):
    """Borrar tu propio reto. Si participa más gente, solo te desapunta a ti (el reto
    sigue vivo para los demás). Si no eres el dueño, simplemente te quitas."""
    if db.reto_creator(rid) != user["id"]:
        db.leave_reto(rid, user["id"])
        return {"ok": True, "left": True}
    others = [p for p in db.list_reto_participants(rid) if p["user_id"] != user["id"]]
    if others:
        db.leave_reto(rid, user["id"])
        return {"ok": True, "left": True, "kept": True}
    db.delete_reto(rid)
    return {"ok": True, "deleted": True}


# --------------------------- participación ---------------------------

@router.post("/api/retos/{rid}/join")
def api_reto_join(rid: int, payload: dict = Body(default={}), user: dict = Depends(auth.require_user)):
    r = db.get_reto(rid)
    if not r:
        return JSONResponse({"error": "No existe ese reto."}, status_code=404)
    tag = _require_follow(user, (payload or {}).get("player"))  # te apuntas con un jugador que sigues
    diff = retos.recalibrate_difficulty(r, tag)
    db.join_reto(rid, user["id"], tag, role="participant", assigned_difficulty=diff)
    return {"ok": True, "assigned_difficulty": diff}


@router.post("/api/retos/{rid}/follow")
def api_reto_follow(rid: int, user: dict = Depends(auth.require_user)):
    if not db.get_reto(rid):
        return JSONResponse({"error": "No existe ese reto."}, status_code=404)
    db.join_reto(rid, user["id"], None, role="follower")
    return {"ok": True}


@router.delete("/api/retos/{rid}/join")
def api_reto_leave(rid: int, user: dict = Depends(auth.require_user)):
    db.leave_reto(rid, user["id"])
    return {"ok": True}
