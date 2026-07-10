# Integración con APIs externas (Supercell · Brawlify/BrawlAPI · Brawltime · RRSS)

Dictamen y plan de trabajo. Objetivo: mejorar la relación de nuestra API con fuentes externas,
igual que hicimos con la API oficial de Supercell, cubriendo los casos de uso que nos han ido
apareciendo (rotación/pool competitivo, datos globales por modo/mapa, imágenes, y compartir en RRSS).

## 1. Qué integramos HOY

| Fuente | Uso actual | Clave | Notas |
|---|---|---|---|
| **API oficial Supercell** (`api.brawlstars.com`) | Perfil, colección, battlelog | Sí (`BRAWL_API_TOKEN`, IP allow-list) | Battlelog = **solo las últimas 25 partidas** por jugador → de ahí que agreguemos en BD. |
| **BrawlAPI** (`api.brawlapi.com`) | Catálogo de brawlers, star powers/gadgets, mapas, modos, iconos | **No** | Estático (Cloudflare Pages). Sin límites. `/v1/events` va **siempre vacío**. |
| **CDN Brawlify** (`cdn.brawlify.com`) | Imágenes (retratos, iconos de modo, mapas, hipercargas) | No | Ya con proxy propio + fallback a espejo GitHub (`Brawlify/CDN`) en `map_assets.py`/`assets.py`. |

## 2. Casos de uso encontrados y su viabilidad

1. **Pool/rotación competitiva de mapas** (nos costó mucho; hoy lo inferimos del battlelog `soloRanked`/`teamRanked`).
   - Fuente ideal: **Brawlify LIVE** (`api.brawlify.com/v1/events`) da la rotación **activa y próxima** con `slot` (Ranked/Trofeos), `map` y `gameMode`. **Viable** — es justo lo que faltaba.
   - Caveat: `api.brawlify.com` responde **403 a IPs de datacenter** salvo con cabeceras de navegador; hay que **verificarlo en el VPS de producción** (allí ya servimos imágenes de su CDN). Fallback: la API oficial `/v1/events` (rotación oficial, sin metadatos ricos).

2. **Datos GLOBALES por brawler/modo/mapa** (para el switch *Comunitaria/Global* de "Mejores Modos" y para elegir mejor los 3 modos).
   - Fuente natural: **Brawltime.ninja**. **Problema:** su repo pasó a **privado** y **no publica una API pública estable**; sus win rates salen de un endpoint Cube interno **no documentado** → depender de él es **frágil y de dudosa legalidad (ToS)**.
   - **Recomendación honesta:** NO acoplarnos a brawltime. Nuestro mejor "global" es **nuestra propia agregación comunitaria**, que crece con cada jugador añadido (ya es la base de `brawler_scene`). El switch "Global" se puede:
     - (a) **posponer** hasta tener una fuente estable, o
     - (b) etiquetar "Global (beta)" alimentándolo de brawltime **con degradación total** si cambia/cae, dejándolo claramente como *best-effort*.

3. **Metadatos ricos de mapas/modos** (nombre, modo, environment, si está *en rotación*, link).
   - **Brawlify LIVE** `/v1/maps` y `/v1/gamemodes`. **Viable**; complementa a BrawlAPI (estático) con el flag de rotación.

4. **Tier list de comunidad** (ya tenemos `tierlist_community.json` curado).
   - Brawltime tiene tier list, misma pega de fuente inestable. Mantener el nuestro curado.

5. **Compartir en RRSS / login social** (fase social pendiente, ver [[social-plataforma]]).
   - Pista aparte: **OAuth** (X/Twitter, Discord) para *login social* + *compartir perfil/retos*. No es "consumir datos de juego" sino identidad + publicación. Se diseña en su propia fase; requiere registrar apps OAuth y guardar `client_id/secret` en `.env`.

## 3. Recomendación (orden de valor/esfuerzo)

1. **Brawlify LIVE (rotación + mapas + modos)** — alto valor, bajo esfuerzo. Resuelve el pool competitivo. **Hacer primero.**
2. **Capa `app/integrations/`** común (cliente JSON cacheado, cabeceras de navegador, degradación) — base para todo lo demás. **Preparada en este commit** (scaffold, sin cablear).
3. **Switch Global** — dejar en "pendiente de fuente"; si se activa, vía brawltime *best-effort* y claramente etiquetado.
4. **RRSS/OAuth** — fase social independiente.

## 4. Instrucciones de implementación

### 4.1 Base común (ya scaffolded)
- `app/integrations/_client.py`: cliente httpx compartido + caché memoria/disco + caché negativa solo en fallo real, cabeceras de navegador. Mismo patrón que `map_assets.py`.
- `app/integrations/brawlify_live.py`: `get_events()`, `get_maps()`, `get_gamemodes()` sobre `api.brawlify.com` (TTL configurable; 15 min eventos, 12 h catálogo).

### 4.2 Activar la rotación competitiva — YA CABLEADO (tras flag)
La rotación de Brawlify LIVE ya está **enganchada** a `modes._ranked_pool()` de forma **aditiva y
auto-degradante** (une la rotación real con la heurística de partidas; si Brawlify da 403/vacío, no
cambia nada). Solo falta encenderla una vez verificado el egress:

1. **Verificar egress DESDE EL SERVIDOR** (sin curl manual): como admin, abre
   `GET /api/admin/integrations/brawlify`. Reporta `egress.ok/status`, si el flag está activo y una
   muestra del pool. (Alternativa manual: `curl -A 'Mozilla/5.0' https://api.brawlify.com/v1/events`.)
2. **Si `egress.ok = true`** → pon `BRAWLIFY_LIVE_ROTATION=1` en el `.env` del servidor y reinicia. A
   partir de ahí `_ranked_pool()` incorpora los mapas Ranked reales (con caché de 15 min) además de la
   heurística. Los mapas nuevos que aún no están en el battlelog de nadie aparecerán al instante.
3. **Si da 403/timeout** (p. ej. Cloudflare bloquea la IP) → deja el flag apagado; seguimos con la
   heurística + API oficial. Sin pérdida: el diagnóstico lo deja claro en `hint`.

> Config nueva: `BRAWLIFY_LIVE_ROTATION` (0/1, por defecto 0) en el `.env` del servidor.

### 4.3 Switch Global de "Mejores Modos" (si se decide activar)
- Rellenar `scope='global'` en `brawler_scene`/insight desde la fuente elegida (hoy scope solo `community`). El endpoint y la tabla `brawler_insight` ya contemplan `scope`.
- Si la fuente es brawltime: envolver TODO en try/except con degradación a "community" y un badge "beta".

### 4.4 RRSS/OAuth (fase social)
- Registrar apps OAuth (X, Discord). Guardar `X_CLIENT_ID/SECRET`, `DISCORD_CLIENT_ID/SECRET` en `.env` (gitignored).
- Endpoints `/auth/oauth/{provider}` (redirect) + callback; enlazar con la cuenta existente. Compartir = generar tarjeta (imagen) del perfil/reto + publicar por la API del proveedor.

## 5. Cumplimiento / legal
- Supercell y Brawlify: mantener la atribución y respetar la Fan Content Policy (ver [[terceros-legal]]).
- **Brawltime**: al no tener API pública, cualquier consumo es *best-effort* y debe poder desconectarse; no presentarlo como dato propio ni cachearlo agresivamente.

## 6. Estado del repositorio
- **Hecho (este commit):** paquete `app/integrations/` (`_client.py` + `brawlify_live.py`), rotación
  de Brawlify LIVE **cableada** a `modes._ranked_pool()` de forma aditiva y auto-degradante **tras el
  flag `BRAWLIFY_LIVE_ROTATION`** (por defecto OFF → sin cambio de comportamiento), y endpoint admin
  de diagnóstico de egress `GET /api/admin/integrations/brawlify`.
- **Acción pendiente del usuario:** abrir el endpoint de diagnóstico en producción; si el egress va,
  poner `BRAWLIFY_LIVE_ROTATION=1` y reiniciar. Nada más que tocar.
