#!/usr/bin/env bash
# `make local` / `make local-stop`: the whole app on this Mac for a real run (docs/DEMO.md).
#
#   start: compose (postgres, valkey, s3) -> alembic upgrade head -> dev seed (QARAU_ENV=dev:
#          users, the demo site and its coverage)
#          -> API (127.0.0.1:LOCAL_API_PORT) -> S1 worker (native rq, this Mac's CPU)
#          -> web production build served by `vite preview` on LOCAL_WEB_PORT, /api proxied
#             (restarted if it crashes)
#   stop:  the three processes, then `docker compose down` (volumes kept)
#
# Settings come from .env.example, then .env (QARAU_LOCAL_ENV_FILE to use another file).
# Ports: API 8010 and web 4173 by default; clear of Kerege (Vite 5197-5199, its API and e2e on
# OS-picked free ports) and its dashboard (8765). LAN=1 serves the web app on 0.0.0.0 too, and
# never in open dev mode: an empty QARAU_API_TOKEN gets a generated one (lan_token).
# The three dev logins are generated once into data/local/dev-logins.env (data/ is gitignored).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
STATE=$ROOT/data/local
ENV_FILE=${QARAU_LOCAL_ENV_FILE:-$ROOT/.env}
API_PORT=${LOCAL_API_PORT:-8010}
WEB_PORT=${LOCAL_WEB_PORT:-4173}
DEMO_RUN=notre-dame-p3e # app/seed.py DEMO_RUN
PROCS=(api worker web)

say() { printf '%s\n' "$*"; }
die() { printf 'make local: %s\n' "$*" >&2; exit 1; }

load_env() {
  [ -f "$ENV_FILE" ] || die "no $ENV_FILE: cp .env.example .env"
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env.example"
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  # Always the compose services on this host, whatever URLs the env file has.
  export QARAU_DATABASE_URL="postgresql+psycopg://$POSTGRES_USER:$POSTGRES_PASSWORD@localhost:$POSTGRES_PORT/$POSTGRES_DB"
  export QARAU_S3_ENDPOINT="http://localhost:${S3_PORT:-7070}"
  export VALKEY_URL="redis://localhost:${VALKEY_PORT:-6379}/0"
  export QARAU_RUNS_DIR=${QARAU_RUNS_DIR:-../data/runs} # relative to backend/, as in .env.example
  export QARAU_PIPELINE_DIR=$ROOT/pipeline
}

compose() { docker compose --env-file "$ENV_FILE" -f "$ROOT/infra/docker-compose.yml" "$@"; }

listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1; }

alive() { [ -f "$STATE/$1.pid" ] && kill -0 "$(cat "$STATE/$1.pid")" 2>/dev/null; }

# Each process gets its own process group (set -m), so stop kills uv/pnpm and their children.
# stdin is /dev/null: a process reading the terminal (vite's "press h" prompt) dies with
# EIO "read" once that terminal is closed.
spawn() {
  local name=$1
  shift
  set -m
  (cd "$ROOT" && exec "$@") </dev/null >"$STATE/$name.log" 2>&1 &
  echo $! >"$STATE/$name.pid"
  set +m
}

wait_for() { # name, seconds, command...
  local name=$1 secs=$2
  shift 2
  for _ in $(seq "$secs"); do
    "$@" >/dev/null 2>&1 && return 0
    alive "$name" || { tail -20 "$STATE/$name.log" >&2; stop >/dev/null; die "$name exited, log above ($STATE/$name.log)"; }
    sleep 1
  done
  tail -20 "$STATE/$name.log" >&2
  stop >/dev/null
  die "$name not ready after ${secs}s, log above ($STATE/$name.log); everything stopped"
}

dev_logins() {
  local f=$STATE/dev-logins.env
  if [ ! -f "$f" ]; then
    (umask 077 && for r in ADMIN ENGINEER VIEWER; do
      echo "QARAU_DEV_${r}_TOKEN=$(openssl rand -hex 12)"
    done >"$f")
  fi
  set -a
  # shellcheck disable=SC1090
  . "$f"
  set +a
}

# LAN=1 with an empty QARAU_API_TOKEN: open dev mode (reads with any password) would reach the
# whole Wi-Fi, so the API gets a random shared token instead, kept as QARAU_LAN_API_TOKEN in
# dev-logins.env (not QARAU_API_TOKEN: without LAN=1 that file must not change the mode). Only
# the dev logins and event codes get in then; nobody is given the shared token.
lan_token() {
  [ "${LAN:-0}" = 1 ] && [ -z "${QARAU_API_TOKEN:-}" ] || return 0
  local f=$STATE/dev-logins.env
  if [ -z "${QARAU_LAN_API_TOKEN:-}" ]; then
    QARAU_LAN_API_TOKEN=$(openssl rand -hex 24)
    echo "QARAU_LAN_API_TOKEN=$QARAU_LAN_API_TOKEN" >>"$f"
  fi
  export QARAU_API_TOKEN=$QARAU_LAN_API_TOKEN
}

modal_state() {
  if [ -n "${MODAL_TOKEN_ID:-}" ] && [ -n "${MODAL_TOKEN_SECRET:-}" ]; then echo "set"; else echo "NOT set"; fi
}

workflow_dense() {
  (cd "$ROOT/pipeline" && uv run --no-sync python -c \
    'from qarau_pipeline.workflow import dense_enabled, load_config; print(dense_enabled(load_config()))')
}

start() {
  load_env
  mkdir -p "$STATE"
  for name in "${PROCS[@]}"; do alive "$name" && die "already running ($name); make local-stop first"; done
  for port in "$API_PORT" "$WEB_PORT"; do listening "$port" && die "port $port is taken; set LOCAL_API_PORT / LOCAL_WEB_PORT"; done
  local host=127.0.0.1
  [ "${LAN:-0}" = 1 ] && host=0.0.0.0

  say "== dependencies (backend, pipeline, web)"
  (cd "$ROOT/backend" && uv sync --frozen -q)
  (cd "$ROOT/pipeline" && uv sync --frozen -q)
  (cd "$ROOT/web" && pnpm install --frozen-lockfile --prefer-offline >/dev/null)

  say "== compose: postgres, valkey, s3 (waits for their health checks)"
  compose up -d --wait --quiet-pull postgres valkey s3

  say "== database: alembic upgrade head, dev seed"
  (cd "$ROOT/backend" && uv run --no-sync alembic upgrade head)
  dev_logins
  lan_token
  (cd "$ROOT/backend" && QARAU_ENV=dev uv run --no-sync python -m app.seed)

  say "== web: production build"
  (cd "$ROOT/web" && pnpm build >"$STATE/web-build.log" 2>&1) || { tail -20 "$STATE/web-build.log" >&2; die "web build failed"; }

  say "== starting API, worker, web"
  # WeasyPrint (PDF report) finds Homebrew's Pango through this path on Apple silicon.
  export DYLD_FALLBACK_LIBRARY_PATH=${DYLD_FALLBACK_LIBRARY_PATH:-/opt/homebrew/lib}
  spawn api sh -c "cd backend && exec uv run --no-sync uvicorn app.main:app --host 127.0.0.1 --port $API_PORT"
  # SimpleWorker: the job runs in the worker process itself (no fork, which macOS dislikes);
  # every pipeline step is still its own `qarau_pipeline step` child process.
  spawn worker sh -c "cd backend && exec uv run --no-sync rq worker --url $VALKEY_URL --worker-class rq.worker.SimpleWorker qarau"
  # The web restarts on a crash; `make local-stop` kills the whole loop (its process group).
  spawn web sh -c "cd web && while :; do QARAU_API_PROXY=http://127.0.0.1:$API_PORT pnpm exec vite preview --host $host --port $WEB_PORT --strictPort; echo \"web exited (\$?), restarting in 2 s\"; sleep 2; done"

  wait_for api 60 curl -fsS "http://127.0.0.1:$API_PORT/health"
  wait_for worker 60 grep -q "Listening on" "$STATE/worker.log"
  wait_for web 60 curl -fsS -o /dev/null "http://127.0.0.1:$WEB_PORT/"
  wait_for web 10 curl -fsS -o /dev/null -H "Authorization: Bearer $QARAU_DEV_VIEWER_TOKEN" "http://127.0.0.1:$WEB_PORT/api/runs"

  local insp url=http://localhost:$WEB_PORT
  insp=$(compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
    "select inspection_id from run where id = '$DEMO_RUN'" 2>/dev/null | tr -d '[:space:]')
  say ""
  say "Qarau is running (health checks passed: API db+storage, worker, web + /api proxy)."
  say "  Open:        $url"
  if [ "${LAN:-0}" = 1 ]; then
    local ip
    ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
    say "  Phone:       http://${ip:-<LAN address of this Mac>}:$WEB_PORT  (same Wi-Fi)"
    say "  Access:      closed: QARAU_API_TOKEN is set (data/local/dev-logins.env when .env has none);"
    say "               only the dev logins below and event codes get in, no open dev mode on the Wi-Fi"
  else
    say "  Phone:       off; run 'make local LAN=1' to serve on the Wi-Fi network"
  fi
  say "  Sign in:     name admin / engineer / viewer, password = its token:"
  say "                 admin     $QARAU_DEV_ADMIN_TOKEN"
  say "                 engineer  $QARAU_DEV_ENGINEER_TOKEN"
  say "                 viewer    $QARAU_DEV_VIEWER_TOKEN"
  say "               (dev logins, kept in data/local/dev-logins.env)"
  if [ -n "$insp" ]; then
    say "  Demo:        $url/inspections/$insp   3D map: $url/viewer/$DEMO_RUN"
  else
    say "  Demo:        no demo inspection: $DEMO_RUN is not under QARAU_RUNS_DIR ($QARAU_RUNS_DIR)"
  fi
  say ""
  say "Where the work runs:"
  say "  upload -> probe, frames, sparse (COLMAP), export, coverage: this Mac's CPU (data/local/worker.log)"
  say "  dense point cloud: NOT run by the app. An uploaded video gives the sparse map only."
  if [ "$(workflow_dense)" = True ]; then
    say "    WARNING: pipeline/config.yaml workflow.dense is true: the app adds FAKE dense points (marked fake)."
  fi
  say "    Dense on Modal (paid GPU, spends the Modal account's \$30 limit) is started by hand only (ADR 0019):"
  say "    cd pipeline && uv run python -m qarau_pipeline dense-remote --provider modal --allow-paid-gpu --frames ... --sparse ... --out ..."
  say "    Modal token (MODAL_TOKEN_ID, MODAL_TOKEN_SECRET) in this shell: $(modal_state)."
  say "    Without it that command stops with 'Modal token not set: export MODAL_TOKEN_ID and MODAL_TOKEN_SECRET ...';"
  say "    nothing is made and nothing fake is shown."
  say ""
  say "Logs: data/local/{api,worker,web}.log   Stop everything: make local-stop"
}

stop() {
  load_env
  mkdir -p "$STATE"
  for name in "${PROCS[@]}"; do
    if alive "$name"; then
      local pid
      pid=$(cat "$STATE/$name.pid")
      kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
      for _ in $(seq 15); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
      kill -KILL -- "-$pid" 2>/dev/null || true
      say "stopped $name"
    fi
    rm -f "$STATE/$name.pid"
  done
  compose down # containers removed, volumes (the data) kept
  local left=""
  for port in "$API_PORT" "$WEB_PORT"; do listening "$port" && left="$left $port"; done
  [ -z "$left" ] || die "still listening on:$left (not ours? check lsof -iTCP:<port>)"
  say "Qarau stopped: API, worker, web, postgres, valkey, s3. Data kept (docker volumes, data/runs)."
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  *) die "usage: infra/local.sh start|stop" ;;
esac
