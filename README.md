# PullPanel

Panel web para ZimaOS que reemplaza el `sudo docker pull …` + recrear a mano.
Elegís qué contenedores controla y desde el navegador:

- **Chequear**: compara tu imagen local con la del registro (ghcr.io, Docker Hub) sin descargar nada.
- **Actualizar**: hace el pull y recrea los contenedores con la imagen nueva, conservando
  puertos, volúmenes, variables, redes y política de reinicio. Si el contenedor nuevo no arranca,
  vuelve solo al anterior.
- **Solo pull**: descarga la imagen sin tocar los contenedores.
- **Automático** (opcional, por imagen): cada N minutos/horas/días chequea y, solo si hay versión
  nueva, actualiza.
- **Limpieza**: lista de imágenes del equipo, marca las que no usa nadie (las versiones viejas
  quedan “sin tag”) y las borrás desde ahí, de a una o seleccionando varias.

Cada imagen se maneja por separado: su propio botón, su propio automático y su propio registro.

## 1. Publicar la imagen en GitHub (una sola vez)

1. En GitHub creá un repo **público** llamado `pullpanel` (así la imagen y el ícono quedan públicos).
2. **Add file → Upload files** y arrastrá el contenido de esta carpeta.
   La carpeta `.github` suele estar oculta en el explorador: si no se sube, entrá a la pestaña
   **Actions → set up a workflow yourself**, nombrá el archivo `publish.yml` y pegá el contenido de
   `.github/workflows/publish.yml`.
3. En **Actions** esperá a que el workflow quede en verde. Publica `ghcr.io/thedanthes/pullpanel:latest`.
4. En tu perfil → **Packages → pullpanel → Package settings**, confirmá que la visibilidad sea
   **Public**. Si quedó privada, ZimaOS no la puede bajar sin login.

## 2. Instalar en ZimaOS (sin terminal)

1. App Store → **+** (arriba a la derecha) → **Instalar una app personalizada** → **Importar**.
2. Pegá `pullpanel-zimaos.yaml`.
3. Cambiá `PULLPANEL_PASSWORD` por tu contraseña → **Instalar**.
4. Abrí PullPanel desde el dashboard (puerto 8790) e ingresá la contraseña.

## 3. Agregar jsdr-api

**+ Agregar** → tildá el contenedor `jsdr-api` (la imagen se completa sola) → Guardar.
Desde ahí, cada vez que publiques en GitHub: **Actualizar**. Listo.

Si `jsdr-api` es privada: **Ajustes → + Agregar registro** → `ghcr.io`, tu usuario de GitHub y un
token *classic* con permiso `read:packages`.

## Notas

- Necesita `/var/run/docker.sock`, que le da control total de Docker. Por eso la contraseña es
  obligatoria y no conviene exponer el puerto 8790 a internet.
- Datos en `/DATA/AppData/pullpanel/data` (`config.json`, incluye los tokens guardados).
- PullPanel puede actualizarse a sí mismo: agregalo como una entrada más. Lanza un contenedor
  auxiliar temporal que lo recrea; recargá la página en ~30 s.
- Solo recrea los contenedores que elegiste. No toca el resto.
- Solo actualiza referencias por tag (`repo:tag`), no por digest.

## Desarrollo

```bash
pip install -r requirements.txt pytest
PULLPANEL_DATA=./data PULLPANEL_PASSWORD=dev uvicorn app.main:app --port 8790 --reload
pytest -q
```
