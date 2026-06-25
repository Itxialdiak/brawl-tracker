"""
Autenticación ligera: hash de contraseñas (solo librería estándar, sin
dependencias compiladas) y utilidades de sesión.

No guardamos datos personales: solo usuario y contraseña, y la contraseña
nunca en claro (PBKDF2-HMAC-SHA256 con sal aleatoria por usuario).
"""

from __future__ import annotations

import os
import hashlib
import hmac
import secrets

from fastapi import Request, HTTPException

from . import db

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    # Hashes bcrypt (p. ej. los de `caddy hash-password`): $2a$ / $2b$ / $2y$
    if stored.startswith("$2"):
        try:
            import bcrypt
            return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
        except Exception:  # noqa: BLE001
            return False
    # Formato propio PBKDF2
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:  # noqa: BLE001
        return False


def session_secret() -> str:
    """Secreto para firmar la cookie de sesión. Del .env si existe; si no,
    se genera y se persiste junto a la BD para que sobreviva a reinicios."""
    env = os.environ.get("SESSION_SECRET")
    if env:
        return env
    db_path = os.environ.get(
        "DB_PATH", os.path.join(os.path.dirname(__file__), "..", "brawl_stats.db"))
    secret_path = os.path.join(os.path.dirname(os.path.abspath(db_path)), ".session_secret")
    try:
        if os.path.exists(secret_path):
            with open(secret_path) as f:
                s = f.read().strip()
                if s:
                    return s
        s = secrets.token_hex(32)
        with open(secret_path, "w") as f:
            f.write(s)
        os.chmod(secret_path, 0o600)
        return s
    except Exception:  # noqa: BLE001
        # Último recurso: efímero (cerraría sesiones al reiniciar, pero no rompe).
        return secrets.token_hex(32)


def current_user(request: Request) -> dict | None:
    uid = request.session.get("user_id")
    if not uid:
        return None
    return db.get_user_by_id(uid)


def require_user(request: Request) -> dict:
    """Dependencia: exige sesión válida; si no, 401."""
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="No has iniciado sesión.")
    return user
