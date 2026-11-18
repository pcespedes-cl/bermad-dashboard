# Prompt para recrear el dashboard en Gemini Canvas

Copia el bloque siguiente en Gemini Canvas (o en cualquier generador de apps de
un solo archivo). El resultado debe ser equivalente a
`bermad-dashboard-gemini.html`.

---

Crea una aplicación web **en un solo archivo HTML** (HTML + CSS + JavaScript
vanilla, sin dependencias externas) para monitorear un medidor de flujo BERMAD
en la operación de lixiviación de **Codelco - División El Salvador**.

## Contexto y objetivo

El dashboard debe mostrar en tiempo real el caudal, el volumen, la presión, la
temperatura, el voltaje del equipo y la **razón de lixiviación** del ciclo de
riego. Debe funcionar de dos formas:

1. **API**: consumiendo un backend propio (`/api/dashboard`) que guarda la API
   key. El navegador nunca debe ver credenciales.
2. **Simulador**: generando datos de ejemplo en el navegador, sin red.

La aplicación se abrirá también desde `file://` o embebida en un iframe, así que
todo debe estar incluido en el archivo (incluido el logo).

## Identidad visual

- Título visible: **Codelco - División El Salvador**.
- Logo corporativo en la cabecera, completamente visible, sobre fondo claro u
  oscuro. Embébelo como `data:image/png;base64,...` (no uses rutas relativas).
- Estética tipo **macOS**: fondo gris muy claro (`#f5f5f7`), tarjetas blancas,
  bordes suaves, radios de 16 px, sombras discretas, tipografía del sistema
  (`-apple-system, BlinkMacSystemFont, "SF Pro Text", ...`).
- Tema claro y oscuro con un botón; respeta `prefers-color-scheme` y guarda la
  preferencia.
- Colores de marca: azul `#007aff`, verde `#34c759`, ámbar `#ff9500`, rojo
  `#ff3b30`, violeta `#af52de`.
- No muestres nombres de proyecto ni de dispositivo de demostración: usa
  etiquetas neutras como «Medidor principal» y «Equipo Omega».

## Cabecera y controles

- Cabecera con logo, nombre del producto, estado de conexión, selector de rango
  (**6 h / 12 h / 24 h / 48 h**), botón de tema y botón **Actualizar**.
- Selector **API / Simulador** visible. Recuerda la elección en `localStorage`.
- Botón de ajustes (⚙) junto al selector: permite configurar la **URL base del
  backend** (por ejemplo `https://mi-backend.onrender.com`). Guárdala en
  `localStorage`. Si está vacía, usa el mismo origen (`/api`). Valida que
  empiece con `http://` o `https://`.
- Al cambiar de modo o de rango, vuelve a cargar los datos.

## Tarjetas KPI (grid uniforme)

1. **Gestión de flujos**: caudal actual en m³/h, fecha/hora de la lectura,
   caudal recomendado y barra de avance con la brecha.
2. **Razón de lixiviación** (destacada): dos columnas, **RL real** y **RL
   ejecutada**, en m³/t con 2 decimales y una línea inferior en L/t. Barra que
   compara la RL real con la ejecutada y porcentaje de cumplimiento.
3. **Volumen del periodo** en m³, con el número de lecturas.
4. **Presión** en psi, con promedio y número de lecturas.
5. **Temperatura** en °C, con promedio y número de lecturas.
6. **Cumplimiento del plan** en %, con la meta diaria como subtítulo.
7. **Voltaje del equipo** en V, estado de batería (Cargando / Descargando /
   Estable), porcentaje y autonomía estimada.

Formato numérico **es-CL**: coma decimal y punto de miles. Valores generales con
**1 decimal**; las razones con **2 decimales**.

## Panel de parámetros del plan

Barra horizontal sobre las tarjetas con estos campos editables:

| Campo | Unidad | Valor por defecto |
| --- | --- | --- |
| Toneladas cargadas | t | 145000 |
| Duración del ciclo | días | 75 |
| Fecha de inicio | fecha | hoy |
| Razón objetivo | m³/t | 0,8 |
| Área de riego | m² | 25000 |
| Tasa de riego | L/m²·h | 2,7 |

- Muestra a la derecha la **meta de caudal** en m³/h y el caudal de diseño.
- Cada cambio recalcula inmediatamente las tarjetas, el resumen del plan y el
  gráfico.
- Guarda todos los parámetros (incluida la fecha de inicio) en `localStorage`
  bajo `bermad-leaching-plan`.
- La fecha de inicio debe usarse en los cálculos de avance. Si no hay valor,
  usa la fecha de hoy en la zona `America/Santiago`.

## Cálculos obligatorios

Usa el **total de días del ciclo**, no valores diarios aislados:

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

Fechas:

```text
fin del ciclo         = inicio + días
día del ciclo         = floor(hoy − inicio) + 1   (acotado a 1…días)
avance                = (hoy − inicio) ÷ días
días restantes        = días − avance
meta a la fecha       = meta diaria × avance
proyectado a la fecha = volumen diario × avance
```

Evita los desfases de zona horaria al parsear fechas `YYYY-MM-DD`: trata la
fecha como fecha pura (UTC) y formatea con `timeZone: 'UTC'`.

## Tarjeta «Plan de lixiviación»

Resumen con:

- Avance vs meta del ciclo (%) y barra.
- Razón actual (m³/t) y meta del ciclo (m³/t).
- Volumen del periodo y número de lecturas.
- Brecha de caudal (m³/h sobre o bajo la meta).
- Proyección del ciclo / volumen objetivo (m³).
- **Inicio del ciclo**: `01 oct 2026 → 15 dic 2026`.
- **Avance del ciclo**: `64,0 % · día 49 de 75`.
- **Días restantes**.
- Badge de estado: Bajo la meta / Cerca de la meta / En meta / Sobre la meta.
- Nota que explique las fórmulas en una línea.

## Gráfico

- SVG generado a mano (sin librerías), responsive, con `ResizeObserver`.
- Cuatro botones: **Caudal**, **Volumen**, **Temperatura**, **Presión**.
- Caudal: serie instantánea en m³/h con línea de meta operativa.
- Volumen: integral acumulada del caudal en m³.
- Temperatura: °C; Presión: psi. En estos dos casos no dibujes línea de meta y
  ajusta el eje a un rango legible (múltiplos de 10).
- Tooltip al pasar el cursor con hora y valor, estadísticas **Mín. / Prom. /
  Máx. / Lecturas**, y un mensaje claro si no hay datos.

## Modo Simulador

Genera la cantidad de puntos necesaria para el rango elegido con resolución de
**2 minutos** (mínimo 24 puntos):

- Caudal con curva diaria suave, bajada nocturna y cortes ocasionales; el último
  punto debe ser cercano a la meta.
- **Presión**: serie con promedio exacto de **45 psi**.
- **Temperatura**: serie con promedio exacto de **50 °C**.
- Volumen integrado a partir del caudal.
- Voltaje de batería, alertas, últimas lecturas y estado del dispositivo
  coherentes con los datos generados.
- No uses `Math.random()` sin control: usa funciones deterministas (senos y
  ruido suave) para que la demo sea estable y reproducible.

## Modo API

- Llama a `GET {base}/api/dashboard?hours=24` (y `&force=1` al actualizar).
- Espera un JSON con esta forma aproximada:
  `{ ok, window, stats, series, device, meter, alerts, readings }`.
- Si la respuesta falla, muestra un banner de error con una pista útil y
  permite reintentar.
- Refresca automáticamente cada 60 s solo si la pestaña está visible.
- Cuando el backend no entregue presión o temperatura, muestra «Próximamente»
  en esas tarjetas y «sin datos» en el gráfico, sin romper el resto.
- Si el modo es Simulador, no muestres errores de red ni de API.

## Exportar CSV

- Botón **Exportar CSV** en la tabla de últimas lecturas.
- En modo API usa `fetch` + `Blob` para descargar un archivo real (funciona con
  un backend en otro origen).
- En modo Simulador genera el CSV localmente.
- Usa separador `;`, BOM UTF-8 y nombre
  `bermad_ultimas_lecturas_AAAAMMDD_HHMM.csv`.

## Requisitos técnicos

- Un solo archivo, sin CDN, sin frameworks y sin claves de API.
- Accesibilidad básica: `aria-label`, foco visible, contraste correcto.
- Sin errores en consola.
- Todo el estado persistente debe usar `localStorage` con manejo seguro de
  excepciones (modo privado, iframe restringido).
- El diseño debe verse bien entre 900 px y 1600 px de ancho; en pantallas
  menores, apila las columnas.

Entrega el archivo completo y funcional, listo para abrir en el navegador.
