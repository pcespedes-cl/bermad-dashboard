# BERMAD Cloud · Gestión de flujo y razón de lixiviación

Dashboard interactivo que consulta la API REST de BERMAD Cloud y presenta el
caudal del medidor junto con la **razón de lixiviación** del plan operativo.

- **Local:** <http://localhost:8765/>
- **Versión anterior (solo navegador):** `monitor_flujo_24h.html`

## Configuración

La API key y los IDs se leen de variables de entorno (o del archivo `.env`):

```bash
BERMAD_API_KEY=tu-api-key
BERMAD_PROJECT_ID=f09f3f01c034ad82ac311fdb3e47e391
BERMAD_UNIT_ID=dcc38574-e6b8-454e-b3f3-36b1c3924264
BERMAD_TZ=America/Santiago
PORT=8765
HOST=127.0.0.1
```

Copia `.env.example` a `.env` y completa `BERMAD_API_KEY`. Para exponer en la red local usa `HOST=0.0.0.0`.

## Qué muestra

- **Gestión de flujos**: caudal actual con fecha y hora de la lectura, caudal
  recomendado para alcanzar la razón de lixiviación esperada y brecha entre ambos.
- **Razón de lixiviación**: **RL real** (volumen medido ÷ toneladas) frente a
  **RL ejecutada** (caudal de diseño = área de riego × tasa de riego), en
  m³/t·d y L/t·d, normalizadas a 24 h.
- **Volumen** integrado del periodo.
- **Voltaje del equipo Omega**, con estimación
  dinámica de autonomía de la batería.
- **Presión** (psi) y **Temperatura** (°C): tarjetas reservadas para
  integración futura.
- **Plan de lixiviación** editable: toneladas cargadas, duración del ciclo,
  razón objetivo, área de riego y tasa de riego. El cálculo se actualiza en
  tiempo real y se guarda en `localStorage`.
- Gráfico con dos vistas: **caudal** instantáneo o **volumen acumulado**, con
  línea de meta, tooltip, detección de huecos y exportación CSV.
- Dispositivo, medidor, alertas y últimas lecturas crudas.

## Cómo se calcula la razón de lixiviación

Con los parámetros del plan (`toneladas`, `días`, `razón objetivo`, `área de
riego`, `tasa de riego`):

```text
volumen objetivo      = toneladas × razón objetivo          [m³]
meta diaria           = volumen objetivo ÷ días             [m³/día]
meta de caudal        = meta diaria ÷ 24                    [m³/h]
razón diaria objetivo = razón objetivo ÷ días               [m³/t·d]

volumen diario        = volumen del periodo × 24 ÷ horas     [m³/día]
razón real            = volumen diario ÷ toneladas           [m³/t·d]

caudal de diseño      = área de riego × tasa de riego ÷ 1000 [m³/h]
volumen ejecutado     = caudal de diseño × 24                [m³/día]
razón ejecutada       = volumen ejecutado ÷ toneladas        [m³/t·d]
cumplimiento RL       = razón real ÷ razón ejecutada
```

- Área de riego en **m²** (por defecto `25.000`).
- Tasa de riego en **L/m²·h** (por defecto `2,7`).
- La razón se expresa en **m³/t·d** y también en **L/t·d** (×1000), que es la
  unidad cómoda para valores operativos como `10,67 L/t·d`.

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

## Ejecutar en local

```bash
cd "/Users/pedrocespedes/Documents/Proyecto predeterminado"
python3 serve.py
```

Abrir <http://localhost:8765/>. Para detenerlo: `Ctrl+C`.

Dependencia opcional recomendada: `pip install certifi` (para el bundle de
certificados TLS).

## Variables de entorno

| Variable | Valor por defecto |
| --- | --- |
| `PORT` | `8765` |
| `BERMAD_API_BASE` | `https://api-bermad.com` |
| `BERMAD_API_KEY` | definida en `serve.py` |
| `BERMAD_PROJECT_ID` | `f09f3f01c034ad82ac311fdb3e47e391` |
| `BERMAD_UNIT_ID` | `dcc38574-e6b8-454e-b3f3-36b1c3924264` |
| `BERMAD_TZ` | `America/Santiago` |

## Seguridad

La `apiKey` vive **solo en el servidor**. Para publicar el dashboard hace falta
un backend o función serverless que haga de proxy; subir `index.html` solo a un
hosting estático dejaría el panel sin datos.
