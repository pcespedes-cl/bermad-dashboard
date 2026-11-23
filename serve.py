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


def api_get(path: str, params: dict | None = None, timeout: int = 120) -> dict:
    """GET a la API de BERMAD con la API key en la cabecera."""
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

    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            body = response.read().decode("utf-8", "replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:600]
        raise ApiError(f"BERMAD respondió HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"No se pudo conectar con BERMAD: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ApiError("BERMAD devolvió una respuesta que no es JSON.") from exc


def first_item(payload: dict) -> dict:
    data = payload.get("data") or []
    return data[0] if data else {}


def fetch_all_pages(path: str, params: dict, max_records: int = MAX_RECORDS) -> tuple[list[dict], int, int]:
    """Descarga todas las páginas necesarias. Tolerante a fallos parciales."""
    first = api_get(path, {**params, "offset": 0, "limit": PAGE_SIZE})
    records = list(first.get("data") or [])
    total = int((first.get("meta") or {}).get("total") or len(records))
    wanted = min(total, max_records)
    requests_made = 1

    offsets = list(range(PAGE_SIZE, wanted, PAGE_SIZE))
    for offset in offsets:
        try:
            page = api_get(path, {**params, "offset": offset, "limit": PAGE_SIZE})
            records.extend(list(page.get("data") or []))
        except ApiError:
            pass
        requests_made += 1

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


def build_dashboard(hours: int = 24, force: bool = False) -> dict:
    hours = max(1, min(48, int(hours)))
    cache_key = f"{PROJECT_ID}:{UNIT_ID}:{hours}"
    now_monotonic = time.monotonic()

    if not force:
        with CACHE_LOCK:
            cached = CACHE.get(cache_key)
        if cached and now_monotonic - cached[0] < CACHE_TTL:
            payload = dict(cached[1])
            payload["cached"] = True
            payload["cacheAgeSeconds"] = round(now_monotonic - cached[0], 1)
            return payload

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
    end_local = datetime.now(tz).replace(microsecond=0)
    start_local = end_local - timedelta(hours=hours)
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

        try:
            meter_payload = meter_future.result()
            meters = meter_payload.get("data") or []
            meter = next((item for item in meters if item.get("unitId") == UNIT_ID), meters[0] if meters else {})
        except ApiError:
            meter = {}

        alerts = []

        try:
            flow_rows, flow_total, flow_pages = flow_future.result()
        except ApiError:
            flow_rows, flow_total, flow_pages = [], 0, 0

        try:
            alerts_payload = alerts_future.result()
            alerts = alerts_payload.get("data") or []
        except ApiError:
            alerts = []

        try:
            power_rows, _, _ = power_future.result()
        except ApiError:
            power_rows = []

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
    expected = max(1, round(hours * 60 / interval_minutes))
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
            "hours": hours,
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
        },
    }

    with CACHE_LOCK:
        CACHE[cache_key] = (time.monotonic(), payload)
    return payload


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Frame-Options", "ALLOWALL")
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

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/health":
            self.send_json(
                200,
                {
                    "ok": True,
                    "projectId": PROJECT_ID,
                    "unitId": UNIT_ID,
                    "apiBase": API_BASE,
                    "serverTime": datetime.now(timezone.utc).isoformat(),
                },
            )
            return

        if parsed.path == "/api/dashboard":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                hours = int((query.get("hours") or ["24"])[0])
            except ValueError:
                hours = 24
            force = (query.get("force") or ["0"])[0] in {"1", "true", "yes"}
            try:
                payload = build_dashboard(hours=hours, force=force)
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
            try:
                payload = build_dashboard(hours=hours, force=False)
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
                        row.get("entity") or "Medidor",
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
            api_path = parsed.path[len("/api") :]
            params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            flat_params = {key: values[-1] for key, values in params.items()}
            try:
                payload = api_get(api_path, flat_params)
            except ApiError as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
            else:
                self.send_json(200, {"ok": True, "data": payload})
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
