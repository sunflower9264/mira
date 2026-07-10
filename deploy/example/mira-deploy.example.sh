#!/usr/bin/env sh
set -eu

PUBLIC_HOST="${PUBLIC_HOST:-127.0.0.1}"
PUBLIC_PORT="${PUBLIC_PORT:-19090}"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-19091}"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
DEPLOY_ROOT="${MIRA_DEPLOY_ROOT:-"$REPO_ROOT/.mira-deploy"}"

WEB_SRC="$REPO_ROOT/web"
BACKEND_SRC="$REPO_ROOT/backend"
WEB_DEPLOY="$DEPLOY_ROOT/web"
BACKEND_DEPLOY="$DEPLOY_ROOT/backend"
LOG_DIR="$DEPLOY_ROOT/logs"
RUN_DIR="$DEPLOY_ROOT/run"
NGINX_PREFIX="$DEPLOY_ROOT/nginx"
UV_CACHE_DIR="$DEPLOY_ROOT/.uv-cache"

BACKEND_PID="$RUN_DIR/backend.pid"
NGINX_CONF="$NGINX_PREFIX/conf/nginx.conf"

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[mira-deploy] missing command: $1" >&2
    exit 1
  fi
}

ensure_commands() {
  need_cmd rsync
  need_cmd uv
  need_cmd npm
  need_cmd nginx
  need_cmd curl
  need_cmd python3
  need_cmd setsid
}

mkdirs() {
  mkdir -p "$WEB_DEPLOY" "$BACKEND_DEPLOY" "$LOG_DIR" "$RUN_DIR" "$NGINX_PREFIX/conf" "$NGINX_PREFIX/client_body_temp" "$UV_CACHE_DIR"
}

rand_secret() {
  python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
}

fernet_secret() {
  python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'
}

env_value() {
  file="$1"
  key="$2"
  if [ ! -f "$file" ]; then
    return 0
  fi
  awk -v key="$key" '
    index($0, key "=") == 1 {
      sub("^[^=]*=", "")
      print
      exit
    }
  ' "$file"
}

write_env() {
  admin_username="${MIRA_DEPLOY_ADMIN_USERNAME:-admin}"
  admin_password="${MIRA_DEPLOY_ADMIN_PASSWORD:-}"
  if [ -z "$admin_password" ] || [ "$admin_password" = "change-me" ]; then
    echo "[mira-deploy] set MIRA_DEPLOY_ADMIN_PASSWORD to a real password" >&2
    exit 1
  fi

  mkdir -p "$BACKEND_DEPLOY/data" "$BACKEND_DEPLOY/runtime"
  existing_env="$BACKEND_DEPLOY/.env"
  jwt_secret=$(env_value "$existing_env" "JWT_SECRET")
  agent_secret=$(env_value "$existing_env" "AGENT_CONFIG_SECRET")
  if [ -z "$jwt_secret" ] || [ "$jwt_secret" = "__GENERATED_BY_INIT_ENV__" ]; then
    jwt_secret=$(rand_secret)
  fi
  if [ -z "$agent_secret" ] || [ "$agent_secret" = "__GENERATED_BY_INIT_ENV__" ]; then
    agent_secret=$(fernet_secret)
  fi

  cat >"$BACKEND_DEPLOY/.env" <<EOF
JWT_SECRET=$jwt_secret
AGENT_CONFIG_SECRET=$agent_secret
JWT_TTL_DAYS=30
ADMIN_USERNAME=$admin_username
ADMIN_PASSWORD=$admin_password
DATABASE_URL=sqlite+aiosqlite:///./data/mira.sqlite
DATA_DIR=./data
RUNTIME_DIR=./runtime
RUNTIME_SANDBOX_IMAGE=mira-agent-runtime:latest
RUNTIME_CALLBACK_BASE_URL=http://127.0.0.1:$BACKEND_PORT/api/internal/runtime
RUNTIME_DOCKER_NETWORK=host
RUNTIME_CONTAINER_MEMORY=2g
RUNTIME_CONTAINER_CPUS=2.0
RUNTIME_CONTAINER_PIDS_LIMIT=256
CORS_ORIGINS=["http://127.0.0.1:$PUBLIC_PORT","http://localhost:$PUBLIC_PORT"]
LOG_LEVEL=INFO
MAX_SKILL_SIZE_BYTES=10000000
MAX_UPLOAD_BYTES=20000000
MAX_INPUT_SIZE_BYTES=1000000
MAX_RESUME_TEXT_BYTES=8192
DISK_WARN_BYTES=5000000000
EOF
}

write_nginx_conf() {
  cat >"$NGINX_CONF" <<EOF
worker_processes 1;
pid $RUN_DIR/nginx.pid;
error_log $LOG_DIR/nginx-error.log;

events {
  worker_connections 1024;
}

http {
  include /etc/nginx/mime.types;
  default_type application/octet-stream;
  access_log $LOG_DIR/nginx-access.log;
  sendfile on;
  keepalive_timeout 65;
  client_max_body_size 25m;
  client_body_temp_path $NGINX_PREFIX/client_body_temp;

  server {
    listen $PUBLIC_HOST:$PUBLIC_PORT;
    server_name _;
    root $WEB_DEPLOY;
    index index.html;

    location /api/ {
      proxy_pass http://$BACKEND_HOST:$BACKEND_PORT/api/;
      proxy_http_version 1.1;
      proxy_set_header Host \$host;
      proxy_set_header X-Real-IP \$remote_addr;
      proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto \$scheme;
      proxy_buffering off;
      proxy_cache off;
      proxy_read_timeout 3600s;
      proxy_send_timeout 3600s;
    }

    location / {
      try_files \$uri \$uri/ /index.html;
    }
  }
}
EOF
}

build_frontend() {
  echo "[mira-deploy] building frontend"
  (
    cd "$WEB_SRC"
    if [ ! -d node_modules ]; then
      npm ci
    fi
    npm run build
  )
  rsync -a --delete "$WEB_SRC/dist/" "$WEB_DEPLOY/"
}

build_backend() {
  echo "[mira-deploy] syncing backend"
  rsync -a --delete \
    --exclude ".env" \
    --exclude ".venv" \
    --exclude ".pytest_cache" \
    --exclude "__pycache__" \
    --exclude "*.pyc" \
    --exclude "data" \
    --exclude "logs" \
    --exclude ".uv-cache" \
    --exclude "runtime/homes" \
    --exclude "runtime/workspaces" \
    --exclude "runtime/status_checks" \
    --exclude "runtime/bin/*/node_modules" \
    "$BACKEND_SRC/" "$BACKEND_DEPLOY/"
  write_env
  (
    cd "$BACKEND_DEPLOY"
    UV_CACHE_DIR="$UV_CACHE_DIR" uv sync --frozen
  )
}

build() {
  ensure_commands
  mkdirs
  build_frontend
  build_backend
  write_nginx_conf
}

is_running() {
  pid_file="$1"
  [ -f "$pid_file" ] || return 1
  pid=$(cat "$pid_file" 2>/dev/null || true)
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

stop_backend() {
  if is_running "$BACKEND_PID"; then
    pid=$(cat "$BACKEND_PID")
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$BACKEND_PID"
}

stop_nginx() {
  if [ -f "$NGINX_CONF" ]; then
    nginx -p "$NGINX_PREFIX/" -c conf/nginx.conf -s quit >/dev/null 2>&1 || true
  fi
  rm -f "$RUN_DIR/nginx.pid"
}

stop() {
  stop_nginx
  stop_backend
  echo "[mira-deploy] stopped"
}

wait_url() {
  url="$1"
  label="$2"
  i=0
  while [ "$i" -lt 40 ]; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "[mira-deploy] $label ready: $url"
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  echo "[mira-deploy] $label did not become ready: $url" >&2
  return 1
}

prepare_backend() {
  (
    cd "$BACKEND_DEPLOY"
    UV_CACHE_DIR="$UV_CACHE_DIR" uv run alembic upgrade head
    UV_CACHE_DIR="$UV_CACHE_DIR" uv run python scripts/init_admin.py
    UV_CACHE_DIR="$UV_CACHE_DIR" uv run python scripts/ensure_runtimes.py
  )
}

start_backend() {
  stop_backend
  setsid -f sh -c '
    cd "$1" || exit 1
    echo $$ > "$2"
    exec .venv/bin/python -m uvicorn app.main:app --host "$3" --port "$4" >"$5" 2>&1 </dev/null
  ' sh "$BACKEND_DEPLOY" "$BACKEND_PID" "$BACKEND_HOST" "$BACKEND_PORT" "$LOG_DIR/backend.log"
  wait_url "http://$BACKEND_HOST:$BACKEND_PORT/api/health" "backend"
}

start_nginx() {
  stop_nginx
  write_nginx_conf
  nginx -t -p "$NGINX_PREFIX/" -c conf/nginx.conf
  nginx -p "$NGINX_PREFIX/" -c conf/nginx.conf
  wait_url "http://127.0.0.1:$PUBLIC_PORT/api/health" "nginx"
}

start() {
  ensure_commands
  mkdirs
  prepare_backend
  stop
  start_backend
  start_nginx
}

status() {
  echo "frontend: http://127.0.0.1:$PUBLIC_PORT"
  curl -fsS "http://127.0.0.1:$PUBLIC_PORT/api/health" || true
}

case "${1:-deploy}" in
  build)
    build
    ;;
  start)
    start
    ;;
  stop)
    stop
    ;;
  status)
    status
    ;;
  deploy)
    build
    start
    ;;
  *)
    echo "usage: $0 [build|start|stop|status|deploy]" >&2
    exit 2
    ;;
esac
