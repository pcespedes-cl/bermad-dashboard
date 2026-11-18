# Smart - Lix · Gestión de flujo y razón de lixiviación

Dashboard interactivo para el medidor BERMAD: caudal, volumen, presión,
temperatura, voltaje y **razón de lixiviación** calculada sobre el ciclo de
riego completo.

- **Local (con backend):** <http://localhost:8765/>
- **Versión de un solo archivo (Gemini Canvas / standalone):**
  `bermad-dashboard-gemini.html`
- **Artifact listo para Gemini:** `gemini-artifact.html`
- **Guía para recrearlo en Gemini:** `gemini-prompt.md`
- **Versión anterior:** `monitor_flujo_24h.html`

## Modos de datos: API y Simulador

El selector de la esquina superior derecha alterna entre dos fuentes:

| Modo | Qué hace |
| --- | --- |
| **API** | Consulta el backend (`/api/dashboard`). La API key permanece en el servidor. |
| **Simulador** | Genera 24 h de datos en el navegador, sin red ni credenciales. |

- El modo elegido se recuerda en `localStorage` (`bermad-source`).
- En `localhost` el modo inicial es **API**; si el archivo se abre directamente
  (`file://`) el modo inicial es **Simulador**, porque no existe un backend en el
  origen del archivo.
- Si desde `file://` se elige **API** sin URL configurada, el panel muestra una
  instrucción para abrir <http://127.0.0.1:8765/> o configurar el backend con ⚙.
- El botón de ajustes (⚙) permite indicar la **URL pública del backend** cuando
  el dashboard no se sirve desde el mismo origen. Se guarda en
  `bermad-api-base`. Vacío = mismo origen (`/api`).
- Los datos simulados usan 2 min de resolución e incluyen presión con promedio
  **45 psi** y temperatura con promedio **50 °C**; caudal, volumen integrado,
  voltaje de batería y alertas también son sintéticos.

## Configuración del backend

La API key y los IDs se leen de variables de entorno (o del archivo `.env`):

```bash
BERMAD_API_KEY=tu-api-key
BERMAD_PROJECT_ID=f09f3f01c034ad82ac311fdb3e47e391
BERMAD_UNIT_ID=dcc38574-e6b8-454e-b3f3-36b1c3924264
BERMAD_TZ=America/Santiago
PORT=8765
HOST=127.0.0.1
```

Copia `.env.example` a `.env` y completa `BERMAD_API_KEY`. Para exponer en la red
local usa `HOST=0.0.0.0`.

## Qué muestra

- **Gestión de flujos**: caudal actual con fecha y hora de la lectura, caudal
  recomendado para alcanzar la razón de lixiviación esperada y brecha entre
  ambos.
- **Razón de lixiviación**: **RL real** (volumen proyectado al ciclo ÷
  toneladas) frente a **RL ejecutada** (caudal de diseño × 24 h × días del
  ciclo ÷ toneladas), en **m³/t** con una línea secundaria en **L/t**. La barra
  compara la RL real con la ejecutada.
- **Volumen** integrado del periodo medido.
- **Presión** (psi) y **Temperatura** (°C): en modo Simulador muestran la serie
  completa y su promedio; en modo API quedan en *Próximamente* hasta que el
  medidor reporte esas variables.
- **Cumplimiento del plan**, **Voltaje del equipo Omega** y estimación dinámica
  de autonomía de la batería.
- **Plan de lixiviación** editable: toneladas cargadas, duración del ciclo,
  **fecha de inicio**, razón objetivo, área de riego y tasa de riego. Cada
  cambio recalcula las tarjetas, el plan y el gráfico al instante y se guarda en
  `localStorage` (`bermad-leaching-plan`).
- **Plan de lixiviación** (resumen): avance del ciclo, día actual, días
  restantes, razón actual, meta del ciclo, volumen del periodo, brecha de
  caudal, proyección del ciclo e intervalo inicio → fin.
- Gráfico con cuatro vistas: **Caudal**, **Volumen**, **Temperatura** y
  **Presión**, con línea de meta cuando corresponde, tooltip, detección de
  huecos y estadísticas mín./prom./máx.
- Dispositivo, medidor, alertas y últimas lecturas crudas.
- **Exportar CSV** de las últimas lecturas (descarga real de archivo, también
  con un backend remoto).

Los valores generales usan **1 decimal**; las razones usan **2 decimales** para
seguir siendo legibles.

## Cómo se calcula la razón de lixiviación

Los cálculos usan los parámetros del plan y el **total de días del ciclo**, no
valores diarios aislados:

```text
volumen objetivo      = toneladas × razón objetivo             [m³]
meta diaria           = volumen objetivo ÷ días                [m³/día]
meta de caudal        = meta diaria ÷ 24                       [m³/h]

volumen diario        = volumen del periodo × 24 ÷ horas        [m³/día]
volumen del ciclo     = volumen diario × días                  [m³]
RL real               = volumen del ciclo ÷ toneladas          [m³/t]

caudal de diseño      = área de riego × tasa de riego ÷ 1000   [m³/h]
volumen ejecutado     = caudal de diseño × 24 × días           [m³]
RL ejecutada          = volumen ejecutado ÷ toneladas          [m³/t]

cumplimiento RL       = RL real ÷ RL ejecutada
```

- Área de riego en **m²** (por defecto `25.000`).
- Tasa de riego en **L/m²·h** (por defecto `2,7`).
- La razón se expresa en **m³/t** y también en **L/t** (×1000).
- La **fecha de inicio** por defecto es hoy y se guarda con el plan.

### Fechas y avance del ciclo

```text
fin del ciclo        = inicio + días
día del ciclo        = floor(hoy − inicio) + 1   (acotado a 1…días)
avance               = (hoy − inicio) ÷ días
días restantes       = días − avance
meta a la fecha      = meta diaria × avance
proyectado a la fecha= volumen diario × avance
```

Ejemplo: inicio `01-10-2026`, 75 días, hoy `18-11-2026` → día 49 de 75,
64,0 % de avance, 27 días restantes, fin `15-12-2026`.

## Voltaje y autonomía de la batería

La tarjeta **Voltaje del equipo** usa los registros de `battery_voltage` de los
últimos 48 h. La estimación se recalcula en cada actualización:

```text
1. Se detecta el último cambio brusco de tensión (≥ 0,9 V), que indica un
   cambio de fuente (red/solar → batería). Se analiza solo ese régimen.
2. Se ajusta una regresión lineal tensión vs tiempo sobre las muestras del
   régimen actual y se obtiene la pendiente en V/día.
3. Duración estimada = (tensión actual − corte) ÷ velocidad de descarga
```

- Corte de referencia: **5,4 V** para batería de 6 V y **11,0 V** para 12 V.
- Si la pendiente es plana o positiva, la tarjeta muestra *Estable* o
  *Cargando* en lugar de una cuenta regresiva.
- El porcentaje de carga se normaliza entre el corte y la tensión de plena
  carga (6,45 V o 12,9 V).
- Al pasar el cursor por la tarjeta se muestran las muestras usadas, el inicio
  del régimen y el voltaje de corte.

## Arquitectura y conexión con la API

El navegador **nunca** recibe la API key. `serve.py` actúa de proxy:

```text
Navegador → http://localhost:8765/api/dashboard
                 ↓
           serve.py (API key en el servidor)
                 ↓
           https://api-bermad.com  (cabecera X-API-Key)
```

Endpoints propios del backend:

| Endpoint | Uso |
| --- | --- |
| `GET /api/dashboard?hours=24` | Datos consolidados del dashboard |
| `GET /api/export?hours=24` | CSV de lecturas (descarga) |
| `GET /api/health` | Estado del proxy y de la configuración |

Conexión base (código real de `serve.py`):

```python
import json
import os
import ssl
import urllib.parse
import urllib.request

import certifi

API_BASE = os.environ.get("BERMAD_API_BASE", "https://api-bermad.com").rstrip("/")
API_KEY = os.environ.get("BERMAD_API_KEY", "<tu-api-key>")

PROJECT_ID = os.environ.get("BERMAD_PROJECT_ID", "f09f3f01c034ad82ac311fdb3e47e391")
UNIT_ID = os.environ.get("BERMAD_UNIT_ID", "dcc38574-e6b8-454e-b3f3-36b1c3924264")

def api_get(path, params=None, timeout=40):
    """GET a la API de BERMAD con la API key en la cabecera."""
    url = f"{API_BASE}{path}"
    query = urllib.parse.urlencode(params or {}, doseq=True)
    if query:
        url = f"{url}?{query}"

    request = urllib.request.Request(
        url,
        headers={
            "X-API-Key": API_KEY,
            "Accept": "application/json",
            "User-Agent": "bermad-dashboard/1.0",
        },
        method="GET",
    )

    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        body = response.read().decode("utf-8", "replace")
        return json.loads(body) if body else {}
```

### Endpoints utilizados

| Endpoint | Uso |
| --- | --- |
| `GET /project` | Nombre y datos del proyecto |
| `GET /project/{pid}/omega` | Zona horaria y estado del equipo |
| `GET /project/{pid}/watermeter` | Medidor Modbus (intervalos, unidades, registros) |
| `GET /project/{pid}/log/omega/watermeter-flow` | Serie de caudal (paginada, 200 por página) |
| `GET /project/{pid}/log/power` | Voltaje de batería (`battery_voltage`, cada ~10 min) |
| `GET /project/{pid}/alert` | Alertas del periodo (`from`/`to` en `DD/MM/YYYY`) |

### Detalles relevantes de la API

- Las fechas de `watermeter-flow` se piden en **hora local del controlador**
  (`YYYY-MM-DD HH:mm:ss`) y la API las devuelve con sufijo `Z` aunque **no son
  UTC**. `serve.py` las reinterpreta en la zona del equipo y las convierte a UTC
  real antes de enviarlas al navegador.
- `watermeter-flow` acepta como máximo **200 registros por página**; el proxy
  pagina en paralelo.
- Las alertas requieren `from`/`to` en formato **`DD/MM/YYYY`**.
- El volumen es una **integración del caudal** (`caudal × intervalo`), porque el
  totalizador del medidor no viene en la respuesta.
- El proxy envía `Access-Control-Allow-Origin: *` y `X-Frame-Options: ALLOWALL`
  para permitir el uso desde Gemini Canvas o Google Sites.

## Usar el archivo de un solo archivo (Gemini Canvas)

`bermad-dashboard-gemini.html` incluye estilos, lógica, logo embebido y modo
Simulador. No necesita el backend para funcionar:

1. Abre el archivo en el navegador o súbelo a Gemini Canvas / Google Sites.
2. Para usar datos reales, pulsa ⚙ y escribe la URL pública del backend
   (por ejemplo, el deploy de `serve.py` en Render).
3. Elige **API** o **Simulador** en el selector superior.
4. **Exportar CSV** descarga el archivo en ambos modos.

### Artifact para Gemini

`gemini-artifact.html` es la variante preparada para **Gemini Canvas / Gemini
Artifacts**:

- Un único archivo autocontenido (sin CDN, sin módulos, sin API keys).
- Logo embebido y estilos listos para el iframe de Gemini.
- **Abre directamente en modo Simulador**, así que funciona sin backend ni
  permisos de red.
- Incluye el selector **API / Simulador**, la configuración de URL del backend
  (⚙), las cuatro vistas del gráfico, la fecha de inicio del ciclo y la
  exportación CSV.
- Es tolerante a `localStorage` bloqueado y a la navegación en iframe.

Para usarlo en Gemini: adjunta o pega el contenido de `gemini-artifact.html` en
Canvas y pídele que lo ejecute como artifact. Si Gemini pide regenerarlo, usa
`gemini-prompt.md` como especificación.

`bermad-dashboard-standalone.html` es la misma aplicación autocontenida, pensada
para abrirse directamente o alojarse en un sitio estático.

## Ejecutar en local

```bash
cd "/Users/pedrocespedes/Documents/Proyecto predeterminado"
python3 serve.py
```

Abrir <http://localhost:8765/>. Para detenerlo: `Ctrl+C`. También se puede usar
`./start.sh` (y `HOST=0.0.0.0 ./start.sh` para la red local).

Dependencia opcional recomendada: `pip install certifi` (para el bundle de
certificados TLS).

## Variables de entorno

| Variable | Valor por defecto |
| --- | --- |
| `PORT` | `8765` |
| `HOST` | `127.0.0.1` |
| `BERMAD_API_BASE` | `https://api-bermad.com` |
| `BERMAD_API_KEY` | definida en `serve.py` |
| `BERMAD_PROJECT_ID` | `f09f3f01c034ad82ac311fdb3e47e391` |
| `BERMAD_UNIT_ID` | `dcc38574-e6b8-454e-b3f3-36b1c3924264` |
| `BERMAD_TZ` | `America/Santiago` |

## Seguridad

La `apiKey` vive **solo en el servidor**. Para publicar el dashboard con datos
reales hace falta un backend o función serverless que haga de proxy; subir solo
un HTML a un hosting estático deja el panel en modo Simulador (o sin datos si se
fuerza API).
