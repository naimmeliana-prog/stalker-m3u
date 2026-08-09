# Lista M3U de series (portal Stalker/MAG) actualizada con GitHub Actions

Genera diariamente un M3U con las URLs de reproduccion ya resueltas y lo
publica en este repo, para cargarlo en TiviPlayer (u otra app IPTV) **sin
necesidad de tener tu PC encendido**.

## Como funciona (Multi-Portal)

Ahora el proyecto soporta **múltiples portales** usando una sola base de código y un único Worker de Cloudflare.

1. Cada portal tiene su propia configuración en `portals/<portal_id>/config.json`.
2. Las listas generadas (M3Us y JSONs para Xtream) se guardan en `portals/<portal_id>/`.
3. El **Worker de Cloudflare** enruta las peticiones de forma dinámica usando el **Username (Usuario)** del cliente IPTV:
   - URL del Servidor en TiviPlayer: `https://tu-worker.workers.dev`
   - Usuario: `<portal_id>` (por ejemplo, `greatott` para cargar la configuración de `portals/greatott/`)
   - Contraseña: `cualquiera` (TiviPlayer la exige, pero el Worker la ignora).

De esta manera, no necesitas desplegar múltiples Workers en Cloudflare, uno solo sirve para todos tus portales.

## Filtrado Estricto de Región e Idioma

El proyecto cuenta con un filtro estricto automático para evitar canales/películas no deseados:
- **TV Directo (ITV)**: Solo se importan canales en **ES** (Español), **FR** (Francés) y **EN/UK** (Inglés).
- **VOD (Películas y Series)**: Solo se importan contenidos en **ES** (Español) y **FR** (Francés).
- **Regiones Excluidas**: Se descarta automáticamente cualquier canal, película o serie que contenga palabras como `LATINO`, `QUEBEC`, `SUISSE`/`SUIZA`, `BELGIQUE`/`BELGICA` o `CANADA`/`CANADIAN`, garantizando únicamente Español de España y Francés de Francia.

## Cómo añadir un nuevo Portal

1. Crea una subcarpeta bajo `portals/` con el nombre de tu portal (por ejemplo, `portals/mi_portal/`).
2. Copia el archivo de configuración de ejemplo [config.json](file:///c:/Users/USUARIO/Downloads/OPENCODE_MAC_M3U/stalker-m3u-main/portals/ejemplo_portal/config.json) en tu nueva carpeta.
3. Configura la URL del portal y la MAC en ese `config.json`.
4. Ejecuta la generación localmente con:
   ```bash
   python generate.py portals/mi_portal/config.json
   python combine.py portals/mi_portal/config.json
   ```
5. En GitHub Actions, puedes configurar tus workflows para que iteren sobre todas las carpetas en `portals/*` o definir secretos específicos.

## Aviso de privacidad

El repo es **publico** (necesario para que TiviPlayer descargue el M3U sin
autenticacion y para usar minutos ilimitados de Actions). El M3U contiene la MAC y las URLs del portal. No lo uses con una cuenta que no quieras exponer.
