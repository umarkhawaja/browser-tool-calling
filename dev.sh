#!/usr/bin/env bash
# Run the backend API and the frontend dev server together.
# Both ports are freed first, so a previous run that was backgrounded or killed
# without its trap firing never blocks a restart.
# Ctrl-C stops both. Backend -> :8008, frontend -> :5173.
set -u
cd "$(dirname "$0")"

cleanup() { kill 0 2>/dev/null; }
trap cleanup EXIT INT TERM

BACKEND_PORT="${BACKEND_PORT:-8008}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

# Kill a process and everything it spawned, children first.
#
# The backend's headless Chromium is a grandchild of the port holder (uvicorn
# --reload runs a reloader plus a worker), and killing only the listener leaves
# that browser running and holding memory. Walking the tree keeps the blast
# radius to the run we are actually replacing — a broader sweep for Playwright
# browsers would also take out any other project's.
kill_tree() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null); do
    kill_tree "$child"
  done
  kill "$pid" 2>/dev/null || true
}

listeners_on() { lsof -ti "tcp:$1" -sTCP:LISTEN 2>/dev/null || true; }

# Free a TCP port, naming whatever was holding it rather than killing silently.
free_port() {
  local port="$1" label="$2" pids attempt
  command -v lsof >/dev/null 2>&1 || return 0

  pids="$(listeners_on "$port")"
  [ -n "$pids" ] || return 0

  # Show the command line, not just the binary: "Python" tells you nothing,
  # "uvicorn app.main:app --port 8008" tells you whether it was yours. argv[0]
  # is reduced to its basename first, or the interpreter's absolute path eats
  # the whole line before the interesting part.
  for pid in $pids; do
    local what
    what="$(ps -o args= -p "$pid" 2>/dev/null |
            awk 'NR==1 { n = split($1, a, "/"); $1 = a[n]; print }' | cut -c1-70)"
    echo "  ✕ ${label} :${port} — ${what:-pid $pid} (${pid})"
    kill_tree "$pid"
  done

  # Let them close the socket before escalating.
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    sleep 0.2
    pids="$(listeners_on "$port")"
    [ -n "$pids" ] || return 0
  done

  kill -9 $pids 2>/dev/null || true
  sleep 0.3

  # Still held usually means it belongs to another user, which we cannot fix.
  # (Uppercasing via tr, not ${var^^} — macOS ships bash 3.2.)
  if [ -n "$(listeners_on "$port")" ]; then
    local var
    var="$(printf '%s' "$label" | tr '[:lower:]' '[:upper:]')_PORT"
    echo "  ! :${port} is still in use — set ${var} to something else" >&2
  fi
}

echo "▸ freeing ports"
free_port "$BACKEND_PORT" "backend"
free_port "$FRONTEND_PORT" "frontend"

echo "▶ backend  http://localhost:${BACKEND_PORT}"
( cd backend && ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload ) &

# --strictPort so Vite fails loudly instead of quietly moving to 5174, where it
# would still be pointing VITE_WS_URL at the port printed above.
echo "▶ frontend http://localhost:${FRONTEND_PORT}"
( cd frontend && npm run dev -- --port "$FRONTEND_PORT" --strictPort ) &

wait
