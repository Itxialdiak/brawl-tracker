"""Rutas de Guía de Estrategia (wiki): árbol, nodos, propuestas de cambios, subida de imágenes.

Extraído de main.py; se incluye con app.include_router()."""
import os, base64, uuid
from fastapi import APIRouter, Body, Query, Depends
from fastapi.responses import JSONResponse
from .. import db, auth

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")

router = APIRouter()


# --------------------------- Wiki / Guía de estrategia ---------------------------

_PROP_KINDS = {"edit", "create_section", "create_subsection", "create_separator", "delete", "reorder"}


@router.get("/api/wiki/tree")
def api_wiki_tree(user: dict = Depends(auth.require_user)):
    return {"tree": db.get_wiki_tree(), "is_admin": bool(user.get("is_admin")),
            "pending": db.count_pending_proposals() if user.get("is_admin") else 0}


@router.get("/api/wiki/node/{nid}")
def api_wiki_node(nid: int, user: dict = Depends(auth.require_user)):
    node = db.get_wiki_node(nid)
    if not node:
        return JSONResponse({"error": "No encontrado."}, status_code=404)
    return {"id": node["id"], "type": node["type"], "title": node["title"],
            "body": node.get("body"), "parent_id": node.get("parent_id")}


@router.post("/api/wiki/proposals")
def api_wiki_propose(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    p = payload or {}
    kind = (p.get("kind") or "").strip()
    if kind not in _PROP_KINDS:
        return JSONResponse({"error": "Tipo de cambio no válido."}, status_code=400)
    summary = (p.get("summary") or "").strip()
    justification = (p.get("justification") or "").strip()
    if not summary:
        return JSONResponse({"error": "Describe brevemente el cambio."}, status_code=400)
    if not justification:
        return JSONResponse({"error": "Justifica el cambio."}, status_code=400)
    node_id = p.get("node_id")
    data = p.get("data") or {}
    # Validaciones mínimas por tipo
    if kind in ("edit", "delete") and not node_id:
        return JSONResponse({"error": "Falta el nodo objetivo."}, status_code=400)
    if kind == "edit" and not (data.get("title") or "").strip():
        return JSONResponse({"error": "El título no puede quedar vacío."}, status_code=400)
    if kind in ("create_section", "create_separator") and not (data.get("title") or "").strip():
        return JSONResponse({"error": "Ponle un título."}, status_code=400)
    if kind == "create_subsection":
        if not data.get("parent_id"):
            return JSONResponse({"error": "Indica a qué sección pertenece."}, status_code=400)
        if not (data.get("title") or "").strip():
            return JSONResponse({"error": "Ponle un título."}, status_code=400)
    pid = db.create_proposal(user["id"], kind, node_id, data, summary, justification)
    return {"ok": True, "id": pid}


_WIKI_MEDIA_DIR = os.path.join(FRONTEND_DIR, "media", "wiki")
_IMG_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}


@router.post("/api/wiki/upload-image")
def api_wiki_upload(payload: dict = Body(...), user: dict = Depends(auth.require_user)):
    data = (payload or {}).get("data") or ""
    mime = ((payload or {}).get("mime") or "").lower()
    if data.startswith("data:"):
        head, _, b64 = data.partition(",")
        if not mime and ":" in head and ";" in head:
            mime = head[head.index(":") + 1:head.index(";")].lower()
        data = b64
    ext = _IMG_EXT.get(mime)
    if not ext:
        return JSONResponse({"error": "Formato no admitido (usa PNG, JPG, GIF o WEBP)."}, status_code=400)
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "Imagen no válida."}, status_code=400)
    if not raw:
        return JSONResponse({"error": "Imagen vacía."}, status_code=400)
    if len(raw) > 6 * 1024 * 1024:
        return JSONResponse({"error": "La imagen supera los 6 MB."}, status_code=400)
    os.makedirs(_WIKI_MEDIA_DIR, exist_ok=True)
    name = uuid.uuid4().hex + ext
    with open(os.path.join(_WIKI_MEDIA_DIR, name), "wb") as f:
        f.write(raw)
    return {"ok": True, "url": "/static/media/wiki/" + name}
