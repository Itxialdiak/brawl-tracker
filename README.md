# Brawl Tracker

Tracker personal de estadísticas de Brawl Stars, **multi-jugador**. Sigue a
varios tags a la vez (el tuyo, amigos, pareja, gente del club, cuentas
secundarias…). Consulta el `battlelog` de cada uno cada pocos minutos y
**acumula** las partidas en SQLite, porque la API oficial solo guarda las 25
últimas de cada jugador. Cuanto más tiempo corra, más historial tendrás.

Por cada jugador muestra win rate por brawler, por modo, por mapa y **contra
cada brawler enemigo**, con filtros.

> **Configuración actual: vía proxy de RoyaleAPI**, para no depender de tu IP
> pública. En la key de Brawl Stars das de alta la **IP del proxy**, no la tuya.

## 1. La API key (apuntando al proxy)

Ya está hecha y el token está puesto en `.env`. Si necesitas crear otra:
1. **developer.brawlstars.com → My Account → Create New Key.**
2. En **Allowed IP Addresses** pon **`45.79.218.79`** (la IP del proxy, *no* la
   tuya). Verifica que sigue vigente en https://docs.royaleapi.com/proxy
3. Copia el token a `BRAWL_API_TOKEN` en el `.env`.

## 2. Arrancar

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Abre **http://localhost:8000**.

### En Visual Studio Code

1. Descomprime y abre la carpeta: **File → Open Folder → `brawl-tracker`**.
2. Crea el entorno virtual y elige el intérprete: paleta de comandos
   (`Ctrl/Cmd+Shift+P`) → **Python: Create Environment** (o a mano con
   `python -m venv venv`) y luego **Python: Select Interpreter** → el del `venv`.
3. En la terminal integrada: `pip install -r requirements.txt`.
4. Arranca: pulsa **F5** (configuración "Brawl Tracker (uvicorn)" ya incluida en
   `.vscode/launch.json`) o ejecuta `uvicorn app.main:app --reload --port 8000`.

Requiere **Python 3.9 o superior** (recomendado 3.11+).

## 3. Añadir jugadores

No hace falta configurar ningún tag en el `.env`. En la web, en la barra
**"Añadir"**, escribe el tag de un jugador (ej. `#2P0LYQQRJ`) y pulsa **+ Seguir**:
- Se valida contra la API que ese jugador existe (si no, te avisa).
- Se da de alta y se hace un sondeo inmediato, así ves datos al momento.
- A partir de ahí, el poller lo actualiza solo cada 3 minutos.

Con el desplegable **"Jugador"** cambias de uno a otro. **"Quitar"** deja de
seguirlo y borra sus datos.

> Opcional: si pones `BRAWL_PLAYER_TAG=#TUTAG` en el `.env`, ese jugador se da
> de alta solo al arrancar. Es solo una comodidad; puedes dejarlo vacío.

**Deja la app corriendo mientras se juega** para no perder partidas.


## Notas

- **Supervivencia:** la API no da victoria/derrota, solo posición. Se cuenta
  como victoria el top-4 en solo y el top-2 en dúo (heurístico, en
  `app/db.py` → `_derive_is_win`).
- **Empates:** no cuentan en el win rate.
- La base de datos es `brawl_stats.db` (se crea sola). Bórrala para empezar de
  cero.

## Estructura

```
app/
  main.py       FastAPI: poller multi-jugador + endpoints + sirve la web
  brawl_api.py  cliente de la API (vía proxy), con errores explicativos
  db.py         SQLite: jugadores, parseo de batallas, estadísticas
frontend/
  index.html    panel (HTML/CSS/JS sin dependencias)
```

## Endpoints

| Método | Ruta | Qué hace |
|--------|------|----------|
| GET    | `/api/players` | Lista de jugadores seguidos (con nº de partidas). |
| POST   | `/api/players` | Añade un jugador. Body: `{"tag": "#..."}`. Valida y sondea. |
| DELETE | `/api/players/{tag}` | Deja de seguir y borra sus datos. |
| GET    | `/api/overview?player=#...` | Resumen del jugador (acepta filtros). |
| GET    | `/api/winrate?by=brawler\|mode\|map&player=#...` | Win rate por dimensión. |
| GET    | `/api/vs?player=#...` | Win rate contra cada brawler enemigo. |
| GET    | `/api/filters?player=#...` | Modos/mapas/brawlers de ese jugador. |
| GET    | `/api/status` | Estado del poller y endpoint en uso. |
| POST   | `/api/poll?player=#...` | Fuerza un sondeo (de uno o de todos). |

Filtros en las rutas de estadísticas: `player`, `mode`, `map`, `brawler`, `vs`.

## Siguiente paso

La capa de **coaching con Claude** (consejos por brawler y detección de
patrones: matchups malos, mapas flojos, picks a evitar) se monta encima de
estos endpoints, por jugador.

## Propiedad de terceros, marcas y atribución

Brawl Sensei / Brawl Tracker es **contenido de fans no oficial**. No está afiliado ni
avalado por Supercell.

> This material is unofficial and is not endorsed by Supercell. For more information see
> Supercell's Fan Content Policy: www.supercell.com/fan-content-policy.

- **Brawl Stars, Supercell y sus marcas, logotipos, personajes (brawlers), imágenes,
  nombres y datos del juego** son propiedad de **Supercell Oy** y se usan según los
  [Términos de la API de Brawl Stars](https://developer.brawlstars.com/) y la
  [Fan Content Policy de Supercell](https://supercell.com/en/fan-content-policy/).
- Los **datos del juego** se obtienen de la **API oficial de Supercell**
  (developer.brawlstars.com), servida a través del **proxy de RoyaleAPI**
  (https://docs.royaleapi.com/proxy), sujeto a sus términos. El token de la API es
  **secreto y vive solo en el servidor** (`.env`), nunca en el cliente.
- Las **imágenes** (retratos de brawlers, iconos de modo, mapas, iconos de perfil/club…)
  provienen de **Brawlify** (`cdn.brawlify.com`) y **BrawlAPI** (`brawlapi.com`), de uso
  libre y sin necesidad de crédito, pero igualmente **sujetas a los términos de Supercell**.
  No se modifican los recursos de Supercell.
- El **asistente de IA ("el Sensei")** usa modelos de **Anthropic (Claude)** vía su API,
  sujeto a los términos de Anthropic.

Toda esa propiedad intelectual pertenece a sus respectivos titulares. Este proyecto solo
la referencia/consume con fines de análisis para fans, sin reclamar derecho alguno sobre ella.

### Cumplimiento (revisar antes de abrir al público o monetizar)

- **Aviso obligatorio**: el descargo de Supercell (recuadro de arriba, texto literal) debe
  mostrarse de forma **legible** allá donde se presente el contenido. Ya está en el **pie de
  la web** y en los **Términos de uso** de la app.
- **Monetización** (Fan Content Policy): permite ingresos por **publicidad**, **donaciones
  puras** y **coaching**, pero **prohíbe cobrar tarifas de cualquier tipo sin aprobación
  expresa de Supercell**. ⚠️ El **modelo de suscripción** previsto para limitar/cobrar el uso
  de la IA encaja en "cobrar tarifas": antes de activarlo hay que **obtener aprobación de
  Supercell** o replantearlo (p. ej. donaciones sin funciones de pago).
- **Prohibido**: NFT/blockchain/cripto, cheats/hacks/mods e **información filtrada o no
  publicada**. ⚠️ La sección de **"próximos brawlers"** debe alimentarse solo de **anuncios
  oficiales**, nunca de filtraciones.
- **Nombre/marca**: no usar marcas de Supercell (p. ej. "Brawl Stars") en dominios o handles.
  "Brawl Sensei" usa "Brawl" (palabra común): zona gris de bajo riesgo; revísalo si se comercializa.
- **Datos personales**: solo se guarda nombre de usuario, email de contacto y hash de
  contraseña; los datos de juego son públicos vía la API oficial.

Las políticas cambian: **conviene releer la Fan Content Policy y los términos de la API tal y
como estén vigentes** antes de cada apertura/cambio de modelo de negocio.
