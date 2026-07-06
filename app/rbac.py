"""Control de accesos basado en roles (RBAC) — fuente única de verdad.

Roles de USUARIO (jerárquicos, de menor a mayor autoridad):

    user  <  translator  <  collaborator  <  admin  <  root

- **root**  — control total. Único. Es el dueño de la plataforma. Puede cambiar el
  rol de cualquiera, incluidos administradores y otros root.
- **admin** — como root SALVO que NO puede tocar los privilegios de root ni de otros
  administradores (solo root reparte/quita el rol admin y root).
- **collaborator** (Colaborador) — panel de administración RESTRINGIDO: ve métricas de
  página pero NO las de consumo; puede ver y colaborar en traducciones y revisar
  cambios de la app. En suscripciones: el tramo más alto gratis (con límites).
- **translator** (Traductor) — usuario normal + panel de traducción. En suscripciones:
  el tramo más bajo gratis + descuento en los de pago.
- **user** (Usuario) — privilegios normales; en el futuro con límites por plan.

Aparte existe un rol de JUGADOR (no de usuario): **Croker** — miembro del club Crokers.
No otorga permisos de plataforma; solo un bono de límites algo superior al de otros
jugadores (compensación por el apoyo del club). Se marca en la cuenta (`is_croker`).

Diseño: `role` en la tabla `users` es la fuente de verdad. Las columnas antiguas
`is_admin`/`is_translator` se mantienen como ESPEJOS derivados de `role` para no
romper el código que aún las lee; se sincronizan al asignar rol (ver db.set_user_role).
"""

from __future__ import annotations

# --- Roles ------------------------------------------------------------------

ROOT = "root"
ADMIN = "admin"
COLLABORATOR = "collaborator"
TRANSLATOR = "translator"
USER = "user"

ROLES = [ROOT, ADMIN, COLLABORATOR, TRANSLATOR, USER]

# Nivel jerárquico: a mayor número, más autoridad. Se usa para decidir quién puede
# gestionar a quién y qué rol puede otorgar cada uno.
LEVEL = {USER: 0, TRANSLATOR: 1, COLLABORATOR: 2, ADMIN: 3, ROOT: 4}

# Etiquetas para la interfaz (es).
LABEL = {
    ROOT: "Root",
    ADMIN: "Administrador",
    COLLABORATOR: "Colaborador",
    TRANSLATOR: "Traductor",
    USER: "Usuario",
}
LABEL_PLURAL = {
    ROOT: "Root",
    ADMIN: "Administradores",
    COLLABORATOR: "Colaboradores",
    TRANSLATOR: "Traductores",
    USER: "Usuarios",
}

# Usuario que será root por defecto (configurable por entorno). Coincide con el admin
# histórico sembrado en db.init_db.
import os
ROOT_USERNAME = os.environ.get("ROOT_USERNAME", "itxialdiak")


def normalize(role) -> str:
    """Devuelve un rol válido; cualquier valor desconocido cae a 'user'."""
    return role if role in LEVEL else USER


def role_of(user) -> str:
    """Rol efectivo de un dict de usuario, tolerante con cuentas antiguas que aún no
    tienen la columna `role` poblada (se deriva de is_admin/is_translator)."""
    if not user:
        return USER
    r = user.get("role")
    if r in LEVEL:
        return r
    # Compatibilidad hacia atrás: derivar de los flags antiguos.
    if user.get("username") == ROOT_USERNAME:
        return ROOT
    if user.get("is_admin"):
        return ADMIN
    if user.get("is_translator"):
        return TRANSLATOR
    return USER


def level_of(user) -> int:
    return LEVEL[role_of(user)]


# --- Permisos ----------------------------------------------------------------
# Constantes de permiso (strings estables; no cambiar sin migrar comprobaciones).

MANAGE_USERS = "manage_users"            # crear / borrar / editar cuentas
ASSIGN_ROLES = "assign_roles"            # cambiar el rol de otros (matizado por jerarquía)
APPROVE_ACCOUNTS = "approve_accounts"    # aprobar registros pendientes
VIEW_PAGE_METRICS = "view_page_metrics"  # métricas de tráfico / uso de páginas
VIEW_CONSUMPTION = "view_consumption"    # métricas de consumo (coste IA, cuotas) — sensible
TRANSLATE = "translate"                  # panel de traducción (Rosetta)
REVIEW_CHANGES = "review_changes"        # revisar cambios de la app / contenido
MANAGE_CONTENT = "manage_content"        # editar wiki, tier lists oficiales, etc.
ADMIN_PANEL = "admin_panel"              # acceso a ALGÚN panel de administración

# Conjunto de permisos por rol. root se resuelve aparte (lo tiene TODO).
_PERMS = {
    ADMIN: {
        MANAGE_USERS, ASSIGN_ROLES, APPROVE_ACCOUNTS, VIEW_PAGE_METRICS,
        VIEW_CONSUMPTION, TRANSLATE, REVIEW_CHANGES, MANAGE_CONTENT, ADMIN_PANEL,
    },
    COLLABORATOR: {
        VIEW_PAGE_METRICS, TRANSLATE, REVIEW_CHANGES, ADMIN_PANEL,
    },
    TRANSLATOR: {
        TRANSLATE,
    },
    USER: set(),
}


def has_perm(user, perm: str) -> bool:
    """¿Tiene el usuario ese permiso? root siempre; el resto según su conjunto."""
    role = role_of(user)
    if role == ROOT:
        return True
    return perm in _PERMS.get(role, set())


def permissions(user) -> list[str]:
    """Lista de permisos efectivos (para exponer al frontend)."""
    role = role_of(user)
    if role == ROOT:
        return sorted({p for s in _PERMS.values() for p in s} | {
            MANAGE_USERS, ASSIGN_ROLES, APPROVE_ACCOUNTS, VIEW_PAGE_METRICS,
            VIEW_CONSUMPTION, TRANSLATE, REVIEW_CHANGES, MANAGE_CONTENT, ADMIN_PANEL,
        })
    return sorted(_PERMS.get(role, set()))


# --- Reglas de gestión entre usuarios ---------------------------------------

def can_manage_user(actor, target) -> bool:
    """¿Puede `actor` gestionar (editar/borrar/cambiar rol) a `target`?

    Regla: hace falta autoridad ESTRICTAMENTE superior. Así un admin gestiona a
    colaboradores/traductores/usuarios pero NO a otros admins ni a root; y root
    gestiona a cualquiera (incluido otro admin)."""
    if not has_perm(actor, MANAGE_USERS):
        return False
    if actor.get("id") and target.get("id") and actor["id"] == target["id"]:
        return False  # nadie se autogestiona el rol (evita autobloqueos)
    return level_of(actor) > level_of(target)


def can_assign_role(actor, target, new_role: str) -> bool:
    """¿Puede `actor` asignar `new_role` a `target`?

    Además de poder gestionar al objetivo, el actor no puede otorgar un rol de
    autoridad >= a la suya (salvo root, que puede todo, incluido crear otro root)."""
    new_role = normalize(new_role)
    if not has_perm(actor, ASSIGN_ROLES):
        return False
    if role_of(actor) == ROOT:
        return True
    if not can_manage_user(actor, target):
        return False
    # No puede otorgar un rol de nivel igual o superior al suyo.
    return LEVEL[new_role] < level_of(actor)


def assignable_roles(actor) -> list[str]:
    """Roles que `actor` puede otorgar (para poblar la UI de gestión de roles)."""
    if role_of(actor) == ROOT:
        return ROLES[:]  # todos, incluido root
    if not has_perm(actor, ASSIGN_ROLES):
        return []
    return [r for r in ROLES if LEVEL[r] < level_of(actor)]


# --- Sistema de "Pergaminos" (tokens de consulta al Sensei) ------------------
# Consultar al Sensei (IA) gasta 1 Pergamino. NO son acumulables: cada rol/plan
# tiene un tope y se recargan mensualmente HASTA ese tope (si tienes 5, gastas 2 y
# se rellenan los 2 gastados hasta 5, no 5+2). root y admin no tienen límite.
#
# DESACTIVADO por ahora (a la espera de definir los planes de suscripción). Los
# topes por rol son provisionales; la base queda lista para activarlo cambiando
# TOKENS_ENABLED y ajustando TOKEN_LIMITS/por-plan.

TOKENS_ENABLED = False
TOKEN_NAME = "Pergaminos"       # nombre temático (plural)
TOKEN_NAME_SINGULAR = "Pergamino"
UNLIMITED = None                # None = sin límite

# Tope mensual base por rol (provisional; se afinará con los planes). None = ilimitado.
TOKEN_LIMITS = {
    ROOT: UNLIMITED,
    ADMIN: UNLIMITED,
    COLLABORATOR: 200,   # tramo más alto gratis
    TRANSLATOR: 50,      # tramo más bajo gratis
    USER: 20,            # provisional para el plan gratuito
}
# Bono adicional para jugadores Croker (se suma al tope de su plan).
CROKER_BONUS = 10


def token_limit(user) -> int | None:
    """Tope mensual de Pergaminos del usuario (None = ilimitado). Suma el bono Croker."""
    base = TOKEN_LIMITS.get(role_of(user), TOKEN_LIMITS[USER])
    if base is UNLIMITED:
        return UNLIMITED
    if user and user.get("is_croker"):
        return base + CROKER_BONUS
    return base


def is_unlimited(user) -> bool:
    return token_limit(user) is UNLIMITED
