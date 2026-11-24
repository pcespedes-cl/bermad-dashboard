#!/usr/bin/env bash
# Publica el dashboard en Render: commit (si hay cambios), push y deploy por API.
#
# Uso:
#   ./deploy.sh                  -> mensaje de commit automático
#   ./deploy.sh "mi mensaje"     -> mensaje de commit personalizado
#
# Requiere el archivo .render-key (ignorado por git) con la API key de Render.
# Solo incluye archivos ya trackeados: los archivos nuevos sin seguimiento
# no se agregan automáticamente.
set -euo pipefail

cd "$(dirname "$0")"

SERVICE_ID="srv-dc24egl9fdbs73cd4p0g"
BRANCH="main"
KEY_FILE=".render-key"
API="https://api.render.com/v1"
SITE="https://bermad-dashboard.onrender.com"

msg="${1:-deploy: $(date '+%Y-%m-%d %H:%M')}"

if [[ ! -f "$KEY_FILE" ]]; then
  echo "ERROR: falta $KEY_FILE con la API key de Render." >&2
  exit 1
fi

# 1) Commit de archivos trackeados modificados o eliminados.
if ! git diff --quiet || ! git diff --cached --quiet; then
  git add -u
  git commit -m "$msg"
else
  echo "Sin cambios locales que commitear."
fi

# 2) Push solo si hay commits pendientes.
if git rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
  if [[ "$(git rev-parse HEAD)" != "$(git rev-parse '@{u}')" ]]; then
    git push origin "$BRANCH"
  else
    echo "GitHub ya está al día."
  fi
else
  git push -u origin "$BRANCH"
fi

SHA="$(git rev-parse HEAD)"
KEY="$(tr -d '\r\n' < "$KEY_FILE")"

# 3) Disparar el deploy del commit actual.
echo "Disparando deploy de ${SHA:0:7} en Render..."
RESP="$(curl -sS -m 60 -X POST \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d "{\"commitId\":\"$SHA\"}" \
  "$API/services/$SERVICE_ID/deploys")"

DEPLOY_ID="$(printf '%s' "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("id",""))')"

if [[ -z "$DEPLOY_ID" ]]; then
  echo "ERROR: Render no devolvió un deploy válido:" >&2
  printf '%s\n' "$RESP" >&2
  exit 1
fi

echo "Deploy: $DEPLOY_ID"

# 4) Esperar el resultado (máx. ~7 minutos).
for _ in $(seq 1 40); do
  STATUS="$(curl -sS -m 30 -H "Authorization: Bearer $KEY" \
    "$API/services/$SERVICE_ID/deploys/$DEPLOY_ID" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("status",""))')"
  echo "  $(date '+%H:%M:%S') estado: $STATUS"
  case "$STATUS" in
    live)
      echo "OK: dashboard publicado en $SITE"
      exit 0
      ;;
    build_failed|canceled|deactivated)
      echo "ERROR: el deploy terminó en estado $STATUS." >&2
      exit 1
      ;;
  esac
  sleep 10
done

echo "ERROR: tiempo de espera agotado. Revisa el deploy en Render." >&2
exit 1
