#!/usr/bin/env bash
# Inicia el dashboard BERMAD en localhost.
# Uso: ./start.sh            -> http://127.0.0.1:8765/
#      HOST=0.0.0.0 ./start.sh -> accesible desde la red local
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
HOST="${HOST:-127.0.0.1}"
export PORT HOST
echo "Iniciando dashboard BERMAD en http://${HOST}:${PORT}/"
exec python3 serve.py