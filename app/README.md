# Backend — estructura

API **FastAPI** + **SQLite**. Los endpoints están troceados por área en `routers/`, y el
sondeo (poller) vive en su propio módulo, así que `main.py` queda mínimo: solo crea la app,
el ciclo de vida y las tareas de fondo, incluye los routers y sirve la web.

```
app/
├── main.py            # Creación de la app, SessionMiddleware, ciclo de vida (lifespan) y
│                      #   tareas de fondo (arranca el poller de poller.py, el notificador de
│                      #   eventos, el refresco diario de wiki y el auto-detector de resultados).
│                      #   Incluye TODOS los routers, sirve index.html en "/" (con cache-busting)
│                      #   y monta /static. Conserva un par de endpoints públicos de metadatos
│                      #   (/api/changelog, /api/meta-global) que no dependen del poller.
├── poller.py          # Poller de jugadores: sondea el battlelog y ACUMULA partidas en SQLite.
│                      #   Mantiene el estado vivo del sondeo (_last_poll, _last_profile_refresh)
│                      #   y expone _poll_player/_poll_all/_poller. Lo arranca main.lifespan.
├── config.py          # Interruptores de configuración / beta compartidos (REGISTRATION_OPEN,
│                      #   REGISTRATION_GATE_CODE, cuota de informes, POLL_INTERVAL).
├── api_common.py      # Helpers compartidos por varios routers: _require_follow, _filters y la
│                      #   caché corta de la API (perfil, battlelog, alta de nombres).
├── routers/           # Un APIRouter por área (se incluyen en main con include_router):
│   ├── auth.py            # login, registro (con verja), logout, sesión, contraseña, país
│   ├── players.py         # jugadores seguidos: listar, añadir, dejar de seguir, jugador principal
│   ├── analytics.py       # Analíticas: overview, win rate, enfrentamientos, roles, filtros
│   ├── modes.py           # Modos de Juego: rotación, Hub de Modos, detalle de mapa, roles
│   ├── rankings.py        # rankings (global/nacional/comunitario) + liguillas + páginas de club
│   ├── brawlers.py        # apartado Brawlers: rejilla, rating de cuenta, ficha, tier lists, buffs
│   ├── battles.py         # historial de batallas (ver, edición manual)
│   ├── coach.py           # informes del Sensei (Claude, en 2º plano) — multi-modelo
│   ├── wiki.py            # Guía de Estrategia (árbol, nodos, propuestas, imágenes)
│   ├── admin.py           # administración: propuestas, usuarios, roles (RBAC), aprobación, métricas
│   ├── catalog.py         # catálogo visual (recursos de Brawlify, modos/mapas) — público
│   ├── notifications.py   # notificaciones (listar, no leídas, marcar, borrar)
│   ├── i18n.py            # traducciones de la UI (público: /api/i18n; admin/traductor: Rosetta)
│   ├── events.py          # ligas y torneos (el más grande): partidas, equipos, clasificación
│   ├── retos.py           # retos sociales: tablón, crear, apuntarse, progreso automático
│   ├── friends.py         # amigos + perfil público de un usuario
│   ├── messages.py        # mensajería entre usuarios
│   ├── social.py          # vincular redes sociales (OAuth) y publicar
│   ├── public.py          # endpoints PÚBLICOS sin cuenta (comunidad, perfil, lookup por tag)
│   ├── share.py           # imágenes para compartir (tarjetas PNG) + páginas Open Graph
│   └── status.py          # /api/status, /api/server-status, /api/poll (leen el estado del poller)
└── (módulos de servicio/dominio)
    ├── db.py              # toda la capa SQLite (queries, migraciones, upserts)
    ├── auth.py            # hashing, sesiones, dependencias require_user/require_admin/require_perm
    ├── rbac.py            # roles y permisos (root/admin/collaborator/translator/user + Croker)
    ├── brawl_api.py       # cliente de la API oficial de Brawl Stars (vía proxy)
    ├── assets.py          # imágenes/catálogo de Brawlify (retratos, iconos, mapas)
    ├── bs_maps.py         # rotación de mapas y modos
    ├── brawler_extra.py   # dataset curado (hipercargas, stats, builds, roles)
    ├── skins.py           # catálogo persistente de imágenes de skins
    ├── coach.py           # llamadas a Claude (informes y resúmenes) + multi-modelo + tokens
    ├── share_image.py     # generación de tarjetas PNG (Pillow, OPCIONAL)
    ├── detect.py          # detección/parseo de partidas
    └── retos.py           # motor de retos: métricas medibles + progreso/dificultad (tracking)
```

## Retos: condiciones medibles (nunca datos manuales)

Un reto guarda una lista de **condiciones** como JSON. Cada condición usa una métrica del
catálogo `retos.METRICS` (victorias, partidas, win rate, racha, brawlers distintos, copas,
estrella del partido), con un objetivo y un ámbito opcional (brawler/modo/mapa/rol). Todo se
calcula en `db.reto_metric()` **desde la tabla `battles`**, sobre las partidas del jugador
**desde que se apuntó** (`joined_at`). Así el seguimiento es automático: el progreso y el
"completado" salen de los datos que el poller ya guarda, sin que el usuario actualice nada.

## Cómo se conecta

`main.py` crea `app`, añade el `SessionMiddleware` y luego **incluye** cada router con
`app.include_router(...)`. Cada router importa lo que necesita de `..` (módulos de servicio),
de `..config` (constantes) y de `..api_common` (helpers). Los routers **no** importan `main`
(evita ciclos). El estado del poller lo comparten `routers/status.py` (lo lee) y
`players.py`/`public.py` (usan `poller._poll_player` para el sondeo inmediato al añadir/consultar
un jugador) importando desde `..poller`, que es un módulo hoja (no importa routers).

## Pendiente (opcional, no urgente)

- **Poller extraído (HECHO)**: el poller de jugadores vive en `poller.py` y `/api/status`,
  `/api/server-status` y `/api/poll` están en `routers/status.py`. `main.py` quedó mínimo.
- **Trocear `db.py`** (~2000 líneas): sigue siendo grande pero está **organizado por secciones**
  y es navegable, así que se mantiene tal cual. Si algún día se hace inmanejable, la vía de
  menor riesgo es convertirlo en un paquete `db/` con submódulos por dominio (usuarios, jugadores,
  batallas/analíticas, retos, eventos, clubs…) y un `db/__init__.py` que los **re-exporte**
  (fachada), para no tocar los cientos de llamadas `db.func()` existentes.
