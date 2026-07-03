# Vincular redes sociales — cómo obtener las claves (OAuth)

Todo el código de vinculación ya está montado. **Solo falta que registres una app en cada
plataforma y pongas sus claves como variables de entorno del servidor.** En cuanto una plataforma
tenga sus claves, dejará de aparecer como «sin configurar» y los usuarios podrán vincular su cuenta.

> ⚠️ Las claves (`*_SECRET`, `*_CLIENT_SECRET`) son **secretas**: van solo en el servidor (variables
> de entorno), **nunca** en el código del frontend ni en el repositorio.

---

## 1. La URL de retorno (Redirect URI)

Cada plataforma te pedirá una o varias **Redirect URI** (URL de retorno / callback). Usa esta,
sustituyendo el dominio por el tuyo:

```
https://TU-DOMINIO/api/social/<plataforma>/callback
```

Es decir:

| Plataforma | Redirect URI a registrar                                   |
|------------|------------------------------------------------------------|
| X          | `https://TU-DOMINIO/api/social/x/callback`                 |
| Reddit     | `https://TU-DOMINIO/api/social/reddit/callback`            |
| Facebook   | `https://TU-DOMINIO/api/social/facebook/callback`          |
| Instagram  | `https://TU-DOMINIO/api/social/instagram/callback`         |
| TikTok     | `https://TU-DOMINIO/api/social/tiktok/callback`            |

En local, para pruebas, puedes usar `http://localhost:8000/api/social/<plataforma>/callback`
(algunas plataformas exigen HTTPS; para esas usa un túnel tipo *ngrok*).

---

## 2. Variables de entorno por plataforma

Define estas variables en el servidor (fichero `.env`, panel del hosting, `export`, etc.). Solo
hace falta poner las de las plataformas que quieras ofrecer; el resto seguirán como «sin configurar».

```bash
# X (Twitter)
X_CLIENT_ID=...
X_CLIENT_SECRET=...

# Reddit
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...

# Facebook
FACEBOOK_APP_ID=...
FACEBOOK_APP_SECRET=...

# Instagram
INSTAGRAM_APP_ID=...
INSTAGRAM_APP_SECRET=...

# TikTok
TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
```

---

## 3. Dónde sacar cada clave

### X (Twitter)
1. Entra en el **X Developer Portal** → https://developer.x.com/ y crea un *Project* + *App*.
2. En la app, activa **User authentication settings** → OAuth **2.0**.
   - *Type of App*: Web App.
   - *Callback URI*: la de la tabla de arriba (`.../api/social/x/callback`).
3. Copia el **Client ID** y el **Client Secret** → `X_CLIENT_ID` / `X_CLIENT_SECRET`.
4. Scopes que usa la app: `tweet.read tweet.write users.read offline.access` (ya configurados).
   Para *publicar* tuits necesitas un plan con acceso de escritura.

### Reddit
1. Entra en https://www.reddit.com/prefs/apps → **create another app…**.
2. Tipo **web app**. En *redirect uri* pon `.../api/social/reddit/callback`.
3. El **client id** aparece bajo el nombre de la app; el **secret** es el campo *secret*
   → `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`.
4. Scopes: `identity submit` (ya configurados). Reddit exige una cabecera `User-Agent` (ya la enviamos).

### Facebook
1. Entra en **Meta for Developers** → https://developers.facebook.com/ y crea una app (tipo *Business*).
2. Añade el producto **Facebook Login** → *Settings* → *Valid OAuth Redirect URIs*:
   `.../api/social/facebook/callback`.
3. En *Settings → Basic* copia **App ID** y **App Secret** → `FACEBOOK_APP_ID` / `FACEBOOK_APP_SECRET`.
4. Para publicar en una página/perfil necesitarás permisos adicionales y **App Review** de Meta.

### Instagram
1. Instagram usa la plataforma de **Meta for Developers** (misma app que Facebook o una dedicada).
2. Producto **Instagram Basic Display** (o *Instagram Graph API* para publicar) → configura el
   *OAuth Redirect URI*: `.../api/social/instagram/callback`.
3. Copia el **App ID** y **App Secret** de Instagram → `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET`.
4. Publicar en Instagram requiere cuenta *Business/Creator* + **App Review**.

### TikTok
1. Entra en **TikTok for Developers** → https://developers.tiktok.com/ y crea una app.
2. Activa **Login Kit** y **Content Posting API**. Redirect URI: `.../api/social/tiktok/callback`.
3. Copia el **Client Key** y **Client Secret** → `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET`.
4. Scopes: `user.info.basic,video.publish` (ya configurados). Publicar requiere que TikTok
   apruebe la app.

---

## 4. Qué funciona con solo poner las claves (y qué requiere aprobación)

Con las claves, el **login OAuth y el guardado del token funcionan**, y la **publicación directa ya
está implementada** para:

- **X (Twitter)**: publica un tuit con el texto + enlace (`POST /2/tweets`). Requiere que tu plan de
  la API tenga acceso de escritura.
- **Reddit**: publica en el subreddit que elija el usuario (`POST /api/submit`).
- **Facebook**: publica en el feed (`/me/feed`). Publicar en nombre de usuarios exige permisos y
  **App Review** de Meta.

Cada publicación incluye un **enlace con Open Graph** (`/u/<id>` o `/e/<id>`), de modo que la vista
previa del enlace muestra la **imagen con la marca de agua del logo** y, al pulsarla, lleva al
**perfil público del autor** (o a la ficha del evento).

Queda pendiente (necesita el flujo de subida de medios + app aprobada por la plataforma):

- **Instagram** y **TikTok**: publicación basada en imagen/vídeo (crear contenedor → publicar). El
  andamiaje está listo; solo hay que añadir la llamada de subida cuando esas apps estén aprobadas.

Archivos implicados: `app/social.py` (config OAuth), `app/routers/social.py` (connect/callback/post
real), `app/routers/share.py` (imágenes con marca de agua + páginas Open Graph), `app/share_image.py`
(generación de la tarjeta), tabla `social_accounts` en `app/db.py`.
