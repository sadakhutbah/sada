#!/usr/bin/env bash
# Sadā preprod on this machine (WSL). See docs/preprod-wsl.md.
#
#   preprod.sh up        build the image if missing, start everything, seed the test mosque (idempotent)
#   preprod.sh down      stop and remove the containers (data kept)
#   preprod.sh stop|start  pause / resume without removing
#   preprod.sh reset     down + erase every volume (database, recordings), then up
#   preprod.sh status    containers, health, URLs
#   preprod.sh logs [service]
#   preprod.sh build     rebuild the image from the current checkout (then `up`)
#   preprod.sh env       print the values a client needs (stream key, tailscale address)
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
ENV_FILE="$HERE/.env"
COMPOSE=(docker compose -f "$HERE/compose.yaml" --env-file "$ENV_FILE")
# Local test password of the two test accounts (never a production value).
export PREPROD_PASSWORD=${PREPROD_PASSWORD:-Preprod-Sada-2026}
PORT=${PREPROD_CONSOLE_PORT:-8090}

touch "$ENV_FILE"; chmod 600 "$ENV_FILE"
getenv() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }
setenv() { { grep -v -E "^$1=" "$ENV_FILE" || true; echo "$1=$2"; } > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"; chmod 600 "$ENV_FILE"; }

tailscale_ip() { command -v tailscale >/dev/null && tailscale ip -4 2>/dev/null | head -1 || true; }

build() {
  [ -f "$ROOT/web/dist/index.html" ] || npm --prefix "$ROOT/web" ci --silent && npm --prefix "$ROOT/web" run build --silent
  docker build -t sada:preprod --build-arg SADA_VERSION="preprod-$(git -C "$ROOT" rev-parse --short HEAD)" "$ROOT"
}

wait_health() {
  for _ in $(seq 1 90); do
    curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && return 0
    sleep 2
  done
  echo "back office not healthy after 3 min" >&2; return 1
}

seed() {
  local flags=()
  [ -n "$(getenv SADA_AGENT_TOKEN)" ] || flags+=(--issue-agent)
  [ -n "$(getenv PREPROD_OBS_KEY)" ] || flags+=(--issue-key)
  local line
  line=$("${COMPOSE[@]}" exec -T -e PREPROD_PASSWORD backoffice python - "${flags[@]}" < "$HERE/seed.py" | grep '^SEED ')
  python3 - "$line" "$ENV_FILE" <<'PY'
import json, sys
data = json.loads(sys.argv[1][5:])
lines = [l for l in open(sys.argv[2]).read().splitlines() if l]
def put(k, v):
    global lines
    lines = [l for l in lines if not l.startswith(k + "=")] + [f"{k}={v}"]
if "agent_token" in data: put("SADA_AGENT_TOKEN", data["agent_token"])
if "publish_key" in data: put("PREPROD_OBS_KEY", data["publish_key"])
open(sys.argv[2], "w").write("\n".join(lines) + "\n")
PY
}

up() {
  docker image inspect sada:preprod >/dev/null 2>&1 || build
  local ts; ts=$(tailscale_ip); [ -z "$ts" ] || setenv PREPROD_TAILSCALE_IP "$ts"
  "${COMPOSE[@]}" up -d postgres backoffice
  wait_health
  seed
  "${COMPOSE[@]}" up -d
  echo; status
}

status() {
  "${COMPOSE[@]}" ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
  echo
  echo "Console   http://127.0.0.1:$PORT   (comptes : preprod-admin, preprod-responsable ; mot de passe : $PREPROD_PASSWORD)"
  echo "Courrier  http://127.0.0.1:${PREPROD_MAIL_UI_PORT:-8025}   (Mailpit : e-mails de contrôle)"
  echo "OBS       rtmp://127.0.0.1:${PREPROD_RTMP_PORT:-1935}/live/preprod?user=preprod&pass=$(getenv PREPROD_OBS_KEY)"
  echo "Faux YouTube  rtmp://127.0.0.1:1936/live/youtube-preprod   (reçu : curl -s 127.0.0.1:9998/v3/paths/list)"
  local ts; ts=$(getenv PREPROD_TAILSCALE_IP); [ -z "$ts" ] || echo "Tailscale http://$ts:$PORT"
}

case "${1:-status}" in
  up) up ;;
  build) build ;;
  down) "${COMPOSE[@]}" down ;;
  stop) "${COMPOSE[@]}" stop ;;
  start) "${COMPOSE[@]}" start ;;
  reset)
    "${COMPOSE[@]}" down -v
    setenv SADA_AGENT_TOKEN ""; setenv PREPROD_OBS_KEY ""
    up ;;
  status) status ;;
  logs) shift; "${COMPOSE[@]}" logs --tail=100 -f "$@" ;;
  env) cat "$ENV_FILE" ;;
  *) sed -n 2,13p "$0"; exit 2 ;;
esac
