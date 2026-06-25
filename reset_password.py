#!/usr/bin/env python3
"""
Crea o actualiza la contraseña de un usuario directamente en la base de datos.

Uso:
    python reset_password.py <usuario> <contraseña>

Usa el MISMO hashing que la app pero en formato PBKDF2 (solo librería estándar),
así que funciona aunque `bcrypt` no esté instalado. Pensado para resetear
contraseñas en producción (VPS) sin tener que tocar el `.env` ni depender de
que la cuenta se cree al arrancar.

Ejecútalo desde la raíz del proyecto y con el venv activado:
    source venv/bin/activate        # Windows: venv\\Scripts\\activate
    python reset_password.py itxialdiak 'mi contraseña'
"""

from __future__ import annotations

import sys

from app import db, auth


def main() -> None:
    if len(sys.argv) != 3:
        print("Uso: python reset_password.py <usuario> <contraseña>")
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]
    if len(password) < 6:
        print("La contraseña debe tener al menos 6 caracteres.")
        sys.exit(1)

    db.init_db()
    password_hash = auth.hash_password(password)  # PBKDF2: no necesita bcrypt

    existing = db.get_user_by_username(username)
    if existing:
        conn = db.get_conn()
        conn.execute("UPDATE users SET password_hash=? WHERE username=?",
                     (password_hash, username))
        conn.commit()
        conn.close()
        print(f"OK: contraseña actualizada para '{username}' (id={existing['id']}).")
    else:
        uid = db.create_user(username, password_hash)
        print(f"OK: usuario '{username}' creado (id={uid}).")

    # Comprobación de cordura: la nueva contraseña valida contra el hash guardado.
    u = db.get_user_by_username(username)
    ok = auth.verify_password(password, u["password_hash"])
    print("Verificación de login:", "CORRECTA" if ok else "FALLIDA")


if __name__ == "__main__":
    main()
