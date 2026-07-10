#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKEND_PORT=8000
FRONTEND_PORT=5173
HOST=0.0.0.0
BACKEND_LOG_DIR="$ROOT/backend/logs"
WEB_LOG_DIR="$ROOT/web/logs"
UV_CACHE_DIR="$ROOT/backend/.uv-cache"

kill_port() {
  port="$1"
  pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
  elif command -v fuser >/dev/null 2>&1; then
    pids=$(fuser "$port"/tcp 2>/dev/null || true)
  fi

  if [ -n "$pids" ]; then
    echo "[Mira] Killing processes on port $port: $pids"
    kill $pids 2>/dev/null || true
    sleep 1
    kill -9 $pids 2>/dev/null || true
  fi
}

echo "[Mira] Stopping processes on ports $BACKEND_PORT and $FRONTEND_PORT..."
kill_port "$BACKEND_PORT"
kill_port "$FRONTEND_PORT"

mkdir -p "$BACKEND_LOG_DIR" "$WEB_LOG_DIR" "$UV_CACHE_DIR"

echo "[Mira] Starting backend on http://$HOST:$BACKEND_PORT"
(
  cd "$ROOT/backend"
  UV_CACHE_DIR="$UV_CACHE_DIR" uv run python scripts/dev.py
) >"$BACKEND_LOG_DIR/backend-dev.log" 2>&1 &

echo "[Mira] Starting frontend on http://$HOST:$FRONTEND_PORT"
(
  cd "$ROOT/web"
  npm run dev -- --host "$HOST" --port "$FRONTEND_PORT"
) >"$WEB_LOG_DIR/frontend-dev.log" 2>&1 &

echo "[Mira] Started. Backend: http://$HOST:$BACKEND_PORT  Frontend: http://$HOST:$FRONTEND_PORT"
echo "[Mira] Logs: $BACKEND_LOG_DIR/backend-dev.log  $WEB_LOG_DIR/frontend-dev.log"
