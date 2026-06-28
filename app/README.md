# Backend — estructura

API **FastAPI** + **SQLite**. Tras el saneamiento, los endpoints están troceados por
área en `routers/`, en vez de vivir todos en un `main.py` gigante.

```
app/
├── main.py            # Creación de la app, middleware de sesión, ciclo de vida
│                      #   (lifespan) y tareas de fondo (poller, notificador, refresco
│                      #   de wiki). Incluye los routers, sirve index.html en "/" y
│                      #   monta /static. Conserva solo los endpoints acoplados al
│                      #   poller (/api/status, /api/poll); el resto son routers.
├── config.py          # Interruptores de configuración / beta compartidos
│                      #   (REGISTRATION_OPEN, cuota de informes, POLL_INTERVAL).
├── api_common.py      # Helpers compartidos por varios routers: _require_follow,
│                      #   _filters y la caché corta de la API (perfil, battlelog,
│                      #   alta de nombres). Aparte de main.py para evitar ciclos.
├── routers/           # Un APIRouter por área (se incluyen en main con include_router):
│   ├── auth.py            # login, registro, logout, sesión, contraseña, país
│   ├── players.py         # jugadores seguidos: listar, añadir, dejar de seguir
│   ├── analytics.py       # Analíticas: overview, win rate, enfrentamientos, roles, filtros
│   ├── modes.py           # Modos de Juego: rotación, Hub de Modos, detalle de mapa
│   ├── rankings.py        # rankings oficiales + personalizados (liguillas)
│   ├── brawlers.py        # apartado Brawlers: rejilla, rating de cuenta, ficha
│   ├── battles.py         # historial de batallas (ver, edición manual)
│   ├── coach.py           # informes de Claude (creación en 2º plano, listar, ver)
│   ├── wiki.py            # Guía de Estrategia (árbol, nodos, propuestas, imágenes)
│   ├── admin.py           # administración (propuestas, usuarios, jugadores, métricas)
│   ├── catalog.py         # catálogo visual (recursos de Brawlify, modos/mapas)
│   ├── notifications.py   # notificaciones (listar, no leídas, marcar, borrar)
│   ├── events.py          # ligas y torneos (el más grande): partidas, equipos, clasificación
│   └── retos.py           # retos sociales: tablón, crear, apuntarse, progreso automático
└── (módulos de servicio/dominio)
    ├── db.py              # toda la capa SQLite (queries, migraciones, upserts)
    ├── auth.py            # hashing, sesiones, dependencias require_user/require_admin
    ├── brawl_api.py       # cliente de la API oficial de Brawl Stars (vía proxy)
    ├── assets.py          # imágenes/catálogo de BrawlAPI (retratos, iconos, mapas)
    ├── bs_maps.py         # rotación de mapas y modos
    ├── brawler_extra.py   # dataset curado (hipercargas, stats, builds)
    ├── skins.py           # catálogo persistente de imágenes de skins
    ├── coach.py           # llamadas a Claude (informes y resúmenes) + conteo de tokens
    ├── detect.py          # detección/parseo de partidas
    └── retos.py           # motor de retos: métricas medibles + progreso/dificultad (tracking)
```

## Retos: condiciones medibles (nunca datos manuales)

Un reto guarda una lista de **condiciones** como JSON. Cada condición usa una métrica
del catálogo `retos.METRICS` (victorias, partidas, win rate, racha, brawlers
distintos, copas, estrella del partido), con un objetivo y un ámbito opcional
(brawler/modo/mapa/rol). Todo se calcula en `db.reto_metric()` **desde la tabla
`battles`**, sobre las partidas del jugador **desde que se apuntó** (`joined_at`). Así
el seguimiento es automático: el progreso y el "completado" salen de los datos que el
poller ya guarda, sin que el usuario actualice nada. La dificultad la declara el
creador y `retos.recalibrate_difficulty()` la ajusta al nivel del jugador que la ve.

## Cómo se conecta

`main.py` crea `app`, añade el `SessionMiddleware` y luego **incluye** cada router con
`app.include_router(...)`. Cada router importa lo que necesita de `..` (módulos de
servicio), de `..config` (constantes) y de `..api_common` (helpers compartidos). Los
routers **no** importan `main` (evita ciclos); la única excepción es un import
**perezoso** en `players.py` para el sondeo inicial, porque el poller vive en `main`.

## Por qué algunos endpoints siguen en main.py

`/api/status` y `/api/poll` dependen del **estado vivo del poller** (`_last_poll`,
`_poll_player`), que se ejecuta como tarea de fondo en `main.py`. Moverlos rompería
ese acoplamiento, así que se quedan junto al poller. (Extraer el poller a su propio
módulo `poller.py` queda como mejora futura; entonces podrían ir a un router.)

## Pendiente (opcional, no urgente)

- Trocear `db.py` (~2000 líneas de capa SQLite) por dominios si se hace inmanejable.
- Extraer el poller a un `poller.py` propio; entonces `/api/status` y `/api/poll`
  podrían irse también a un router y `main.py` quedaría mínimo del todo.
