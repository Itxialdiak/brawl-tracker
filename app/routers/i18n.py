"""Rutas del sistema de traducción de la interfaz (Rosetta).

- GET  /api/i18n/{lang}       público: overrides de la comunidad para fusionar sobre el .json
                              estático del cliente. Devuelve {exact:{src:tgt}, patterns:[[re,sub]]}.
- GET  /api/admin/i18n?lang=  admin/traductor: {source: {kind, target}} de ese idioma (editor).
- POST /api/admin/i18n        admin/traductor: guarda una traducción {lang, source, kind, target}.

Origen SIEMPRE en español; el editor deja elegir un idioma de referencia (se piden dos GET).
"""
from fastapi import APIRouter, Body, Query, Depends
from fastapi.responses import JSONResponse

from .. import db, auth, i18n_tools

router = APIRouter()


@router.get("/api/i18n/{lang}")
def api_i18n(lang: str):
    """Overrides de la comunidad para un idioma (público; se fusionan sobre el .json base)."""
    exact, patterns = {}, []
    for r in db.ui_translations_rows(lang):
        tgt = r.get("target")
        if tgt is None or tgt == "":
            continue
        if r.get("kind") == "pattern":
            rule = i18n_tools.compile_pattern(r["source"], tgt)
            if rule:
                patterns.append(rule)
        else:
            exact[r["source"]] = tgt
    return {"lang": lang, "exact": exact, "patterns": patterns}


@router.get("/api/admin/i18n")
def api_admin_i18n(lang: str = Query(...), user: dict = Depends(auth.require_translator)):
    """Mapa {source: {kind, target}} de un idioma, para prefijar el editor (destino o referencia)."""
    out = {r["source"]: {"kind": r.get("kind") or "exact", "target": r.get("target") or ""}
           for r in db.ui_translations_rows(lang)}
    return {"lang": lang, "map": out, "is_admin": bool(user.get("is_admin"))}


@router.post("/api/admin/i18n")
def api_admin_i18n_save(payload: dict = Body(...), user: dict = Depends(auth.require_translator)):
    p = payload or {}
    lang = (p.get("lang") or "").strip()
    source = p.get("source")
    kind = (p.get("kind") or "exact").strip()
    if not lang or lang == "es" or not source:
        return JSONResponse({"error": "Idioma o cadena no válidos."}, status_code=400)
    if kind not in ("exact", "pattern"):
        kind = "exact"
    target = (p.get("target") or "").strip()
    # Validación de patrón: los tokens {n}/{s} del destino deben cuadrar con el origen.
    if kind == "pattern" and target and not i18n_tools.compile_pattern(source, target):
        return JSONResponse({"error": "La traducción del patrón debe conservar los mismos {n}/{s} que el origen."},
                            status_code=400)
    db.ui_upsert_translation(lang, source, kind, target, user["id"])
    return {"ok": True}
