"""Servidor local para el dashboard BERMAD Cloud.

- Sirve los archivos estáticos del proyecto.
- Expone /api/dashboard, que consulta la API REST de BERMAD y entrega
  los datos ya normalizados al navegador.
- Mantiene la API key en el servidor: el navegador nunca la recibe.
"""

from __future__ import annotations

import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import certifi

    CA_FILE = certifi.where()
except Exception:  # pragma: no cover - certifi suele estar disponible
    CA_FILE = None

BASE_DIR = Path(__file__).resolve().parent
# Carga opcional de variables desde .env (solo stdlib)
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _value = _line.split("=", 1)
        _key = _key.strip()
        _value = _value.strip().strip('"').strip("'")
        if _key and _key not in os.environ:
            os.environ[_key] = _value

PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "127.0.0.1")

API_BASE = os.environ.get("BERMAD_API_BASE", "https://api-bermad.com").rstrip("/")
API_KEY = os.environ.get("BERMAD_API_KEY", "")
if not API_KEY:
    raise SystemExit(
        "Falta BERMAD_API_KEY. Define la variable de entorno o crea un archivo .env "
        "con BERMAD_API_KEY=<tu-api-key>."
    )
PROJECT_ID = os.environ.get("BERMAD_PROJECT_ID", "f09f3f01c034ad82ac311fdb3e47e391")
UNIT_ID = os.environ.get("BERMAD_UNIT_ID", "dcc38574-e6b8-454e-b3f3-36b1c3924264")
DEFAULT_TZ = os.environ.get("BERMAD_TZ", "America/Santiago")

PAGE_SIZE = 200
MAX_RECORDS = 5000
CACHE_TTL = 20  # segundos
CACHE: dict[str, tuple[float, dict]] = {}
CACHE_LOCK = threading.Lock()


class ApiError(RuntimeError):
    pass


def _ssl_context() -> ssl.SSLContext:
    if CA_FILE:
        return ssl.create_default_context(cafile=CA_FILE)
    return ssl.create_default_context()


RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0


def api_get(
    path: str,
    params: dict | None = None,
    timeout: int = 30,
    attempts: int = RETRY_ATTEMPTS,
) -> dict:
    """GET a la API de BERMAD con la API key en la cabecera.

    Reintenta errores transitorios (timeout, red, 5xx, 429) con backoff
    exponencial. La API key nunca se registra en los mensajes de error.
    """
    query = urllib.parse.urlencode(params or {}, doseq=True)
    url = f"{API_BASE}{path}"
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

    attempts = max(1, int(attempts))
    last_error: str = "error desconocido"

    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
                body = response.read().decode("utf-8", "replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            last_error = f"BERMAD respondió HTTP {exc.code}: {detail}"
            if exc.code in RETRY_STATUS and attempt < attempts:
                time.sleep(RETRY_BASE_DELAY * (2 ** (attempt - 1)))
                continue
            raise ApiError(last_error) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            last_error = f"No se pudo conectar con BERMAD: {reason}"
            if attempt < attempts:
                time.sleep(RETRY_BASE_DELAY * (2 ** (attempt - 1)))
                continue
            raise ApiError(last_error) from exc
        except json.JSONDecodeError as exc:
            last_error = "BERMAD devolvió una respuesta que no es JSON."
            if attempt < attempts:
                time.sleep(RETRY_BASE_DELAY * (2 ** (attempt - 1)))
                continue
            raise ApiError(last_error) from exc

    raise ApiError(last_error)


def first_item(payload: dict) -> dict:
    data = payload.get("data") or []
    return data[0] if data else {}


def fetch_all_pages(path: str, params: dict, max_records: int = MAX_RECORDS) -> tuple[list[dict], int, int]:
    """Descarga todas las páginas en paralelo. Tolerante a fallos parciales."""
    first = api_get(path, {**params, "offset": 0, "limit": PAGE_SIZE})
    records = list(first.get("data") or [])
    total = int((first.get("meta") or {}).get("total") or len(records))
    wanted = min(total, max_records)
    offsets = list(range(PAGE_SIZE, wanted, PAGE_SIZE))
    requests_made = 1

    if offsets:
        def load_page(offset: int) -> tuple[int, list[dict]]:
            page = api_get(path, {**params, "offset": offset, "limit": PAGE_SIZE})
            return offset, list(page.get("data") or [])

        pages: dict[int, list[dict]] = {}
        with ThreadPoolExecutor(max_workers=min(6, len(offsets))) as executor:
            futures = {executor.submit(load_page, offset): offset for offset in offsets}
            for future in futures:
                try:
                    offset, page_records = future.result()
                except ApiError:
                    continue
                pages[offset] = page_records
        for offset in offsets:
            records.extend(pages.get(offset, []))
        requests_made += len(offsets)

    return records, total, requests_made


def battery_percent(voltage: float, cutoff: float, full: float) -> float:
    if full <= cutoff:
        return 0.0
    return max(0.0, min(100.0, (voltage - cutoff) / (full - cutoff) * 100))


def linear_slope_per_day(points: list[tuple[datetime, float]]) -> float | None:
    if len(points) < 4:
        return None
    origin = points[-1][0]
    xs = [(stamp - origin).total_seconds() / 86400 for stamp, _ in points]
    ys = [value for _, value in points]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((value - mean_x) ** 2 for value in xs)
    if denominator <= 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator


def resolve_timezone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TZ)


def fmt_api_dt(value: datetime) -> str:
    # La API interpreta "from"/"to" como hora local del controlador,
    # sin sufijo Z ni offset.
    return value.strftime("%Y-%m-%d %H:%M:%S")


BATTERY_HOURS = 48


def api_stamp_to_utc(value: object, tz: ZoneInfo) -> str | None:
    """Reinterpreta la marca horaria local del controlador (etiquetada Z) como hora real del controlador y la convierte a UTC."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1]
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        # Si ya trae offset, se respeta; si no, se asume tz del controlador.
        pass
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def build_battery(power_rows: list[dict], tz: ZoneInfo) -> dict:
    points: list[tuple[datetime, float]] = []
    for row in power_rows:
        stamp = api_stamp_to_utc(row.get("dateTime"), tz)
        value = row.get("value")
        if not stamp or value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        points.append((parse_iso(stamp), numeric))

    points = sorted((stamp, value) for stamp, value in points if stamp is not None)
    if not points:
        return {"available": False}

    # Un cambio brusco de tensión suele indicar cambio de fuente (red/batería).
    # La estimación se hace solo con el régimen actual.
    regime_start = 0
    for index in range(1, len(points)):
        if abs(points[index][1] - points[index - 1][1]) >= 0.9:
            regime_start = index
    regime = points[regime_start:]
    if len(regime) < 4:
        regime = points

    values = [value for _, value in regime]
    current = values[-1]
    is_12v = current > 9
    cutoff = 11.0 if is_12v else 5.4
    full = 12.9 if is_12v else 6.45
    slope = linear_slope_per_day(regime)
    discharge = -slope if slope is not None else 0.0

    if current <= cutoff:
        estimated_days: float | None = 0.0
    elif discharge > 0.03:
        estimated_days = (current - cutoff) / discharge
    else:
        estimated_days = None

    if slope is None:
        trend = "unknown"
    elif slope > 0.05:
        trend = "charging"
    elif slope < -0.03:
        trend = "discharging"
    else:
        trend = "stable"

    first_stamp = regime[0][0]
    last_stamp = regime[-1][0]
    span_hours = max(0.0, (last_stamp - first_stamp).total_seconds() / 3600)

    return {
        "available": True,
        "voltage": round(current, 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "avg": round(sum(values) / len(values), 2),
        "percent": round(battery_percent(current, cutoff, full)),
        "full": full,
        "trend": trend,
        "slopeVPerDay": round(slope, 4) if slope is not None else None,
        "cutoff": cutoff,
        "estimatedDays": round(estimated_days, 2) if estimated_days is not None else None,
        "estimatedHours": round(estimated_days * 24, 1) if estimated_days is not None else None,
        "samples": len(regime),
        "spanHours": round(span_hours, 1),
        "lastReading": last_stamp.isoformat().replace("+00:00", "Z"),
        "regimeStart": first_stamp.isoformat().replace("+00:00", "Z"),
        "stepDetected": regime_start > 0,
        "windowHours": BATTERY_HOURS,
    }


CACHE_LOCK = threading.Lock()
CACHE_FETCH_LOCKS: dict[str, threading.Lock] = {}


def _cached_payload(
    cache_key: str,
    allow_stale: bool = False,
    min_timestamp: float | None = None,
) -> dict | None:
    now_monotonic = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get(cache_key)
    if not cached:
        return None
    if min_timestamp is not None and cached[0] < min_timestamp:
        return None
    age = now_monotonic - cached[0]
    if age >= CACHE_TTL and not allow_stale:
        return None
    payload = dict(cached[1])
    payload["cached"] = True
    payload["cacheAgeSeconds"] = round(age, 1)
    if age >= CACHE_TTL:
        payload["stale"] = True
    return payload


def _cache_fetch_lock(cache_key: str) -> threading.Lock:
    """Evita que varias peticiones simultáneas disparen la misma consulta lenta."""
    with CACHE_LOCK:
        return CACHE_FETCH_LOCKS.setdefault(cache_key, threading.Lock())


def build_dashboard(hours: int = 24, force: bool = False, start: str = None, end: str = None) -> dict:
    if start and end:
        try:
            _s = datetime.fromisoformat(start.replace("Z", "+00:00"))
            _e = datetime.fromisoformat(end.replace("Z", "+00:00"))
            if _s.tzinfo is None:
                _s = _s.replace(tzinfo=timezone.utc)
            if _e.tzinfo is None:
                _e = _e.replace(tzinfo=timezone.utc)
            cache_key = f"{PROJECT_ID}:{UNIT_ID}:range:{_s.isoformat()}:{_e.isoformat()}"
            hours = None
        except ValueError:
            start = end = None
            hours = max(1, min(48, int(hours)))
            cache_key = f"{PROJECT_ID}:{UNIT_ID}:{hours}"
    else:
        hours = max(1, min(48, int(hours)))
        cache_key = f"{PROJECT_ID}:{UNIT_ID}:{hours}"
    request_started = time.monotonic()

    fetch_lock = _cache_fetch_lock(cache_key)

    if not force:
        cached = _cached_payload(cache_key)
        if cached:
            return cached
        if fetch_lock.locked():
            # Ya hay una consulta en curso: no bloqueamos el panel, servimos
            # la última copia buena aunque esté vencida.
            stale = _cached_payload(cache_key, allow_stale=True)
            if stale:
                return stale

    with fetch_lock:
        if not force:
            cached = _cached_payload(cache_key)
            if cached:
                return cached
        else:
            # Otra petición forzada pudo terminar mientras esperábamos el turno:
            # reutilizamos su resultado en vez de repetir la consulta.
            cached = _cached_payload(cache_key, min_timestamp=request_started)
            if cached:
                return cached
        try:
            return _build_dashboard(cache_key, hours, start, end)
        except ApiError:
            # Si BERMAD no responde, se entrega la última copia buena en vez
            # de romper el panel.
            stale = _cached_payload(cache_key, allow_stale=True)
            if stale:
                return stale
            raise


def _build_dashboard(cache_key: str, hours: int, start: str = None, end: str = None) -> dict:
    started_at = time.monotonic()

    try:
        omega_payload = api_get(
            f"/project/{PROJECT_ID}/omega",
            {"offset": 0, "limit": 1, "id": UNIT_ID},
        )
        omega = first_item(omega_payload)
    except ApiError:
        # El endpoint de Omega puede estar temporalmente no disponible:
        # no debe tumbar el panel; se usa una zona horaria por defecto.
        omega = {}
    tz = resolve_timezone(omega.get("timezone"))
    if start and end:
        try:
            _s = datetime.fromisoformat(start.replace("Z", "+00:00"))
            _e = datetime.fromisoformat(end.replace("Z", "+00:00"))
            if _s.tzinfo is None:
                _s = _s.replace(tzinfo=tz)
            else:
                _s = _s.astimezone(tz)
            if _e.tzinfo is None:
                _e = _e.replace(tzinfo=tz)
            else:
                _e = _e.astimezone(tz)
            start_local = _s.replace(microsecond=0, tzinfo=None).replace(tzinfo=tz)
            end_local = _e.replace(microsecond=0, tzinfo=None).replace(tzinfo=tz)
        except ValueError:
            end_local = datetime.now(tz).replace(microsecond=0)
            start_local = end_local - timedelta(hours=hours or 24)
    else:
        end_local = datetime.now(tz).replace(microsecond=0)
        start_local = end_local - timedelta(hours=hours or 24)
    from_value = fmt_api_dt(start_local)
    to_value = fmt_api_dt(end_local)
    alert_from = start_local.strftime("%d/%m/%Y")
    alert_to = end_local.strftime("%d/%m/%Y")
    battery_from = fmt_api_dt(end_local - timedelta(hours=BATTERY_HOURS))

    with ThreadPoolExecutor(max_workers=5) as executor:
        meter_future = executor.submit(
            api_get,
            f"/project/{PROJECT_ID}/watermeter",
            {"offset": 0, "limit": 50, "unitId": UNIT_ID},
        )
        alerts_future = executor.submit(
            api_get,
            f"/project/{PROJECT_ID}/alert",
            {"to": alert_to, "offset": 0, "limit": 100},
        )
        flow_future = executor.submit(
            fetch_all_pages,
            f"/project/{PROJECT_ID}/log/omega/watermeter-flow",
            {
                "from": from_value,
                "to": to_value,
                "unitId": UNIT_ID,
            },
        )
        power_future = executor.submit(
            fetch_all_pages,
            f"/project/{PROJECT_ID}/log/power",
            {
                "from": battery_from,
                "to": to_value,
                "unitId": UNIT_ID,
            },
            600,
        )

        meters: list[dict] = []
        meter: dict = {}
        flow_error: str | None = None
        try:
            meter_payload = meter_future.result()
            meters = meter_payload.get("data") or []
            meter = next((item for item in meters if item.get("unitId") == UNIT_ID), meters[0] if meters else {})
        except ApiError:
            meter = {}
        alerts = []

        try:
            flow_rows, flow_total, flow_pages = flow_future.result()
        except ApiError as exc:
            flow_rows, flow_total, flow_pages = [], 0, 0
            flow_error = str(exc)

        try:
            alerts_payload = alerts_future.result()
            alerts = alerts_payload.get("data") or []
        except ApiError:
            alerts = []

        try:
            power_rows, _, _ = power_future.result()
        except ApiError:
            power_rows = []

    if flow_error and not flow_rows:
        # Sin lecturas de caudal el panel no tiene sentido: mejor informar el
        # fallo para que el frontend reintente o use la última copia en caché.
        raise ApiError(flow_error)

    # Agrupa por timestamp: si hay varios medidores reportando a la misma hora,
    # el caudal mostrado es la suma del proyecto.
    by_timestamp: dict[str, float] = {}
    entities: set[str] = set()
    recent_raw: list[dict] = []
    seen_recent: set[tuple[str, str]] = set()

    for row in flow_rows:
        stamp = api_stamp_to_utc(row.get("dateTime"), tz)
        value = row.get("value")
        if not stamp or value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        by_timestamp[stamp] = by_timestamp.get(stamp, 0.0) + numeric
        if row.get("entityId"):
            entities.add(str(row["entityId"]))

    for row in sorted(flow_rows, key=lambda item: item.get("dateTime") or "", reverse=True):
        stamp = api_stamp_to_utc(row.get("dateTime"), tz)
        if not stamp:
            continue
        key = (stamp, str(row.get("entityId") or ""))
        if key in seen_recent:
            continue
        seen_recent.add(key)
        recent_raw.append(
            {
                "t": stamp,
                "v": float(row.get("value") or 0),
                "entity": row.get("entityName") or meter.get("name") or "Medidor",
                "entityId": row.get("entityId"),
            }
        )
        if len(recent_raw) >= 40:
            break

    series = [
        {"t": stamp, "v": round(value, 4)}
        for stamp, value in sorted(by_timestamp.items())
    ]

    try:
        interval_minutes = float(meter.get("logInterval") or 2)
    except (TypeError, ValueError):
        interval_minutes = 2.0
    interval_minutes = max(0.5, min(60.0, interval_minutes))

    values = [point["v"] for point in series]
    current = values[-1] if values else 0.0
    previous = values[-2] if len(values) > 1 else None
    volume = sum(values) * (interval_minutes / 60.0)
    expected = max(1, round((hours or 24) * 60 / interval_minutes))
    last_reading = series[-1]["t"] if series else None
    last_dt = parse_iso(last_reading)
    stale_minutes = None
    if last_dt:
        stale_minutes = max(0, round((datetime.now(timezone.utc) - last_dt).total_seconds() / 60))

    stats = {
        "current": round(current, 3),
        "previous": round(previous, 3) if previous is not None else None,
        "delta": round(current - previous, 3) if previous is not None else None,
        "deltaPct": round(((current - previous) / previous) * 100, 1) if previous else None,
        "avg": round(sum(values) / len(values), 3) if values else 0.0,
        "max": round(max(values), 3) if values else 0.0,
        "min": round(min(values), 3) if values else 0.0,
        "volume": round(volume, 3),
        "points": len(series),
        "expected": expected,
        "coverage": round(min(100.0, len(series) / expected * 100), 1),
        "lastReading": last_reading,
        "staleMinutes": stale_minutes,
        "nonzero": sum(1 for value in values if value > 0),
        "entityCount": len(entities),
        "intervalMinutes": interval_minutes,
    }

    device = {
        "id": omega.get("id"),
        "name": omega.get("name"),
        "serial": omega.get("serial"),
        "status": omega.get("status"),
        "timezone": omega.get("timezone") or DEFAULT_TZ,
        "lat": omega.get("lat"),
        "long": omega.get("long"),
        "model": (omega.get("device") or {}).get("model", {}).get("sub_type"),
        "firmware": (omega.get("device") or {}).get("firmware"),
        "communicationType": (omega.get("device") or {}).get("communication_type"),
        "isOnline": bool((omega.get("device") or {}).get("is_online")),
        "lowPower": omega.get("lowPower"),
        "created": (omega.get("time") or {}).get("created"),
        "updated": (omega.get("time") or {}).get("updated"),
        "synced": (omega.get("time") or {}).get("synced"),
    }

    project = {"id": PROJECT_ID, "name": "Proyecto BERMAD", "description": None}

    payload = {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "cached": False,
        "project": {
            "id": project.get("id", PROJECT_ID),
            "name": project.get("name", "Proyecto BERMAD"),
            "description": project.get("description"),
        },
        "window": {
            "hours": int((end_local - start_local).total_seconds() / 3600) if (end_local - start_local).total_seconds() > 0 else (hours or 24),
            "timezone": device["timezone"],
            "fromLocal": start_local.isoformat(),
            "toLocal": end_local.isoformat(),
            "fromUTC": start_local.astimezone(timezone.utc).isoformat(),
            "toUTC": end_local.astimezone(timezone.utc).isoformat(),
        },
        "device": device,
        "meter": {
            "id": meter.get("id"),
            "name": meter.get("name"),
            "kind": meter.get("kind"),
            "role": meter.get("role"),
            "unitId": meter.get("unitId"),
            "logInterval": meter.get("logInterval"),
            "readInterval": meter.get("readInterval"),
            "flowUnits": meter.get("flowUnits"),
            "volUnits": meter.get("volUnits"),
            "slaveId": meter.get("slaveId"),
            "baudRate": meter.get("baudRate"),
            "valueType": meter.get("valueType"),
            "flowRegister": meter.get("flowRegister"),
            "volRegister": meter.get("volRegister"),
            "leakDetectionTimeFrame": meter.get("leakDetectionTimeFrame"),
            "noWaterDelay": meter.get("noWaterDelay"),
            "synced": (meter.get("time") or {}).get("synced"),
            "updated": (meter.get("time") or {}).get("updated"),
        },
        "meters": meters,
        "alerts": alerts,
        "series": series,
        "recentRaw": recent_raw,
        "stats": stats,
        "battery": build_battery(power_rows, tz),
        "api": {
            "base": API_BASE,
            "projectId": PROJECT_ID,
            "unitId": UNIT_ID,
            "latencyMs": round((time.monotonic() - started_at) * 1000),
            "flowPages": flow_pages,
            "flowTotal": flow_total,
            "flowTruncated": flow_total > len(flow_rows),
            "requests": flow_pages + 5,
            "errors": [flow_error] if flow_error else [],
        },
    }

    with CACHE_LOCK:
        CACHE[cache_key] = (time.monotonic(), payload)
    return payload


class Handler(SimpleHTTPRequestHandler):
    BLOCKED_SUFFIXES = (".log", ".pyc", ".sqlite", ".sqlite3", ".db", ".pem", ".key")
    BLOCKED_NAMES = {"nohup.out"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Security-Policy", "frame-ancestors *")
        super().end_headers()

    def send_json(self, status: int, payload: dict) -> None:
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def is_blocked_path(self, path: str) -> bool:
        """Impide servir archivos sensibles (.env, .git, logs, claves)."""
        rel = urllib.parse.unquote(path).lstrip("/")
        parts = [part for part in rel.split("/") if part]
        if not parts:
            return False
        if any(part.startswith(".") for part in parts):
            return True
        name = parts[-1].lower()
        if name in self.BLOCKED_NAMES:
            return True
        return name.endswith(self.BLOCKED_SUFFIXES)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/health":
            query = urllib.parse.parse_qs(parsed.query)
            deep = (query.get("deep") or ["0"])[0] in {"1", "true", "yes"}
            payload = {
                "ok": True,
                "projectId": PROJECT_ID,
                "unitId": UNIT_ID,
                "apiBase": API_BASE,
                "cached": len(CACHE),
                "serverTime": datetime.now(timezone.utc).isoformat(),
            }
            if deep:
                started = time.monotonic()
                try:
                    api_get(
                        f"/project/{PROJECT_ID}/omega",
                        {"offset": 0, "limit": 1, "id": UNIT_ID},
                        timeout=20,
                        attempts=1,
                    )
                    payload["api"] = {
                        "ok": True,
                        "latencyMs": round((time.monotonic() - started) * 1000),
                    }
                except ApiError as exc:
                    payload["ok"] = False
                    payload["api"] = {"ok": False, "error": str(exc)}
            self.send_json(200 if payload["ok"] else 503, payload)
            return

        if self.is_blocked_path(parsed.path):
            self.send_json(404, {"ok": False, "error": "Recurso no disponible."})
            return

        if parsed.path == "/api/dashboard":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                hours = int((query.get("hours") or ["24"])[0])
            except ValueError:
                hours = 24
            force = (query.get("force") or ["0"])[0] in {"1", "true", "yes"}
            start = (query.get("from") or [None])[0]
            end = (query.get("to") or [None])[0]
            try:
                payload = build_dashboard(hours=hours, force=force, start=start, end=end)
            except ApiError as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
            except Exception as exc:  # pragma: no cover - salvaguarda
                self.send_json(500, {"ok": False, "error": f"Error interno: {exc}"})
            else:
                self.send_json(200, payload)
            return

        if parsed.path == "/api/export":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                hours = int((query.get("hours") or ["24"])[0])
            except ValueError:
                hours = 24
            start = (query.get("from") or [None])[0]
            end = (query.get("to") or [None])[0]
            try:
                payload = build_dashboard(hours=hours, force=False, start=start, end=end)
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            readings = payload.get("recentRaw") or []
            tz_name = (payload.get("device") or {}).get("timezone") or "UTC"
            try:
                tz = ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                tz = timezone.utc
            lines = []
            lines.append(["Proyecto", "Smart - Lix"])
            lines.append(["Dispositivo", "Medidor principal"])
            lines.append(
                [
                    "Ventana",
                    f"{payload.get('window', {}).get('fromUTC', '')} / {payload.get('window', {}).get('toUTC', '')}",
                ]
            )
            lines.append(["Lecturas exportadas", len(readings)])
            lines.append([])
            lines.append(["Fecha UTC", "Fecha local", "Caudal (m3/h)", "Medidor"])

            def local_stamp(value: str) -> str:
                try:
                    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                    return dt.astimezone(tz).strftime("%d-%b-%Y %H:%M")
                except Exception:
                    return str(value or "")

            for row in readings:
                value = row.get("v", 0)
                try:
                    value_text = str(value).replace(".", ",")
                except Exception:
                    value_text = str(value)
                lines.append(
                    [
                        row.get("t") or "",
                        local_stamp(row.get("t") or ""),
                        value_text,
                        "MUT7000",
                    ]
                )

            def escape_csv(value: object) -> str:
                text = "" if value is None else str(value)
                return '"' + text.replace('"', '""') + '"'

            csv_text = "\uFEFF" + "\r\n".join(
                ";".join(escape_csv(item) for item in row) for row in lines
            )
            data = csv_text.encode("utf-8")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"bermad_ultimas_lecturas_{stamp}.csv"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        super().do_GET()

    def log_message(self, fmt: str, *args) -> None:
        if self.path.startswith("/api/"):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    os.chdir(BASE_DIR)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Dashboard BERMAD en http://{HOST}:{PORT}/")
    print(f"API: {API_BASE} · proyecto {PROJECT_ID} · unidad {UNIT_ID}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDetenido.")
    finally:
        server.server_close()
