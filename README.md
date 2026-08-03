# Lista M3U de series (portal Stalker/MAG) actualizada con GitHub Actions

Genera diariamente un M3U con las URLs de reproduccion ya resueltas y lo
publica en este repo, para cargarlo en TiviPlayer (u otra app IPTV) **sin
necesidad de tener tu PC encendido**.

## Como funciona

1. **GitHub Actions** ejecuta `generate.py` cada dia (cron `05:30 UTC`) o
   manualmente desde la pestana "Actions".
2. `stalker_series_m3u.py` se autentica en el portal con la MAC (secret
   `MAG_MAC`), resuelve la URL de cada episodio y escribe `series.m3u`.
3. Guarda progreso en `checkpoint.json`: si el job se corta (limite de 6 h),
   la siguiente ejecucion **reanuda** donde se quedo en vez de empezar de cero.
4. Cada 2 min hace push parcial de `series.m3u` + `checkpoint.json`, de modo
   que el CDN siempre sirve la lista mas reciente posible.
5. TiviPlayer carga el M3U desde el CDN gratuito de **jsDelivr**:
   `https://cdn.jsdelivr.net/gh/naimmeliana-prog/stalker-m3u@main/series.m3u`

## Configuracion

- `config.json`: portal, categorias, numero de hilos, agrupacion. Editalo en
  el repo y haz push (el workflow se relanza si usas `workflow_dispatch`).
   - `categories`: lista de IDs. Para listar los IDs: `python stalker_series_m3u.py <PORTAL> --mac <MAC> --list-categories`.
   - Cuantos mas episodios, mas tarda: el job dura hasta **6 h** y se reanuda
     desde el checkpoint si se corta. Ejemplo medido: categoria 949 (Espana) =
     ~7000 episodios ~40-60 min.
   - `checkpoint`: archivo de progreso (default `checkpoint.json`).
   - `push_interval`: segundos entre pushes parciales (default 120).
- **Secret `MAG_MAC`**: la MAC del dispositivo. No la pongas en `config.json`
  (aparece igualmente dentro de `series.m3u`).

## Tiempo y caducidad

- Los `play_token` de las URLs caducan. La regeneracion diaria renueva la
  lista; si el portal las caduca antes de 24 h, sube la frecuencia del cron.
- Cron de GitHub Actions: **se desactiva si el repo lleva 60 dias sin
  actividad** (haz un push o ejecuta `workflow_dispatch` para reactivarlo).

## Aviso de privacidad

El repo es **publico** (necesario para que TiviPlayer descargue el M3U sin
autenticacion y para usar minutos ilimitados de Actions). `series.m3u`
contiene la MAC y las URLs del portal, visibles para cualquiera. No lo uses
con una cuenta que no quieras exponer.
