# Desplegar Smart - Lix en Render

El dashboard necesita un backend (no puede ser solo HTML estático) porque la
**API key de BERMAD debe permanecer en el servidor**. `serve.py` sirve el panel y
hace de proxy hacia BERMAD. Render ejecuta ese proceso y le asigna una URL HTTPS.

## Requisitos

- Cuenta gratuita de [GitHub](https://github.com).
- Cuenta gratuita de [Render](https://render.com).
- El archivo `.env` **no se sube** al repositorio (ya está en `.gitignore`).

## 1. Subir el proyecto a GitHub

Crea un repositorio **vacío** en <https://github.com/new> (sin README, sin
`.gitignore`, sin licencia) llamado, por ejemplo, `bermad-dashboard`.

Luego, desde esta carpeta:

```bash
cd "/Users/pedrocespedes/Documents/Proyecto predeterminado"
git remote add origin https://github.com/USUARIO/bermad-dashboard.git
git branch -M main
git push -u origin main
```

> Reemplaza `USUARIO` por tu usuario de GitHub. Si el repositorio ya tiene un
> README, usa `git pull --rebase origin main` antes de `git push`.

Verifica que `.env` no se haya subido: en GitHub no debe aparecer ese archivo.

## 2. Crear el servicio en Render

1. En Render entra a **Dashboard → New + → Blueprint**.
2. Conecta tu cuenta de GitHub y elige el repositorio `bermad-dashboard`.
3. Render detecta `render.yaml` y muestra el servicio `bermad-dashboard`.
4. Cuando pida **`BERMAD_API_KEY`**, pega la clave real (la misma del `.env`).
   Los IDs de proyecto y unidad ya vienen en `render.yaml`.
5. Pulsa **Apply / Deploy**.

Render instalará `requirements.txt`, iniciará `python serve.py` y publicará una
URL parecida a:

```text
https://bermad-dashboard-xxxx.onrender.com
```

## 3. Verificar

```bash
curl https://TU-URL.onrender.com/api/health
curl "https://TU-URL.onrender.com/api/health?deep=1"
```

- `"ok": true` y `"api": {"ok": true, ...}` indican que el backend y la conexión
  con BERMAD están bien.
- Abre la URL en el navegador. El modo **API** funciona en el mismo origen, sin
  configurar nada más.

## 4. Usar la URL desde el artifact de Gemini

En `gemini-artifact.html` (o en el artifact publicado), abre **⚙** y escribe la
URL de Render, sin `/` final:

```text
https://bermad-dashboard-xxxx.onrender.com
```

Así el artifact consulta la API a través de tu backend y la clave sigue oculta.

## Notas de operación

- **Plan gratuito:** el servicio se suspende tras ~15 min sin tráfico. La
  primera petición después de eso tarda 30–60 s; luego responde normal.
- **Caché:** el backend guarda 20 s por ventana y reutiliza la última copia
  buena si BERMAD no responde (`"stale": true`).
- **Logs:** en Render, pestaña **Logs**. Si ves `Falta BERMAD_API_KEY`, revisa la
  variable de entorno.
- **Puerto:** no definas `PORT`; Render lo inyecta y `serve.py` lo respeta.
  `HOST=0.0.0.0` ya está configurado.
- **TLS:** `requirements.txt` incluye `certifi` para validar el certificado de
  `api-bermad.com`.

## Actualizar el despliegue

Cada `git push` a `main` vuelve a desplegar automáticamente:

```bash
git add -A
git commit -m "cambios"
git push
```

## Alternativa sin GitHub

Si prefieres no usar GitHub, se puede publicar con un túnel temporal
(Cloudflare Tunnel) o migrar el servicio a otro proveedor. En ese caso la URL
puede cambiar o requerir que el equipo que ejecuta el backend permanezca
encendido.
