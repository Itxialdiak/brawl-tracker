# Frontend — estructura

La interfaz es una **SPA** (una sola página; las pestañas se cambian en cliente con
JS, sin recargar). Tras el saneamiento, está separada en tres capas:

```
frontend/
├── index.html            # SOLO markup: cabecera, navegación y el contenedor de cada
│                         #   sección/pestaña. Carga el CSS y los scripts.
├── styles.css            # TODO el CSS (antes estaba incrustado en index.html).
├── scripts/              # TODO el JS, en módulos por área (antes incrustado).
│   ├── 01-core-analytics.js
│   ├── 02-rankings-leagues.js
│   ├── 03-brawlers.js
│   ├── 04-wiki-guide.js
│   ├── 05-admin.js
│   ├── 06-modes-coach.js
│   ├── 07-auth-notifs.js
│   ├── 08-tournaments.js
│   └── 09-init.js
└── media/                # imágenes (banner, logo, subidas de eventos…)
```

FastAPI sirve `index.html` en `/` y monta esta carpeta en `/static`
(ver `app/main.py`), así que `styles.css` → `/static/styles.css` y los scripts →
`/static/scripts/NN-*.js`.

## Los módulos JS y su orden

Son **scripts clásicos** (no módulos ES) y se cargan **en el orden numerado**. Eso
importa: comparten el ámbito global (variables y funciones se ven entre archivos) y
algún listener de nivel superior se registra al cargar. Reglas que se respetan:

1. **`01-core-analytics.js` primero**: define `$`, helpers (`esc`, `getJSON`…), los
   recursos visuales y el estado global. Todo lo demás depende de esto.
2. **`09-init.js` el último**: arranca la app (`bootApp`, login, sondeo). Debe ir al
   final para que todas las funciones ya estén definidas.
3. Entre medias, un módulo por área. El orden se conserva tal cual estaba en el
   `index.html` original (es seguro respecto al *hoisting*).

| Módulo | Contiene |
|---|---|
| `01-core-analytics.js` | núcleo + pestaña **Analíticas** (overview, paneles, filtros multi-select, gráficas, historial, jugador, rotación, cuenta) |
| `02-rankings-leagues.js` | **Rankings** + liguillas (personalizados, drag&drop, compartir/importar) + navegación de secciones/pestañas |
| `03-brawlers.js` | apartado **Brawlers** (rejilla, contadores, rating, Top 13, ficha) |
| `04-wiki-guide.js` | **Guía de Estrategia** (wiki: árbol, nodos, editor, revisión) |
| `05-admin.js` | **Administración** (cambios, usuarios, jugadores, métricas, historial) |
| `06-modes-coach.js` | manejadores de la app + **Modos de Juego** (heatmap, Hub de Modos, modal de mapa) + **Consejos** (informes IA) |
| `07-auth-notifs.js` | **autenticación** y notificaciones |
| `08-tournaments.js` | **Ligas y Torneos** (eventos, equipos, rondas, clasificación, partidas) |
| `09-init.js` | **arranque** de la app |

## Convenciones

- Dentro de cada módulo, las áreas se separan con comentarios `/* ---- ... ---- */`
  (secciones menores) y `/* ==== ... ==== */` (secciones mayores).
- En el HTML, cada pestaña se marca con un comentario `<!-- NOMBRE -->`.

## ⚠️ Nota de seguridad

El JS de cliente **siempre es visible** desde el navegador (DevTools → Sources, o
pidiendo el `.js` directo). Separarlo en archivos mejora el **mantenimiento y la
caché**, pero **no lo oculta**. Nunca pongas secretos (claves, tokens) en el
frontend: van en el backend (`.env`).
