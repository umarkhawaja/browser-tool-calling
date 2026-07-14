#!/usr/bin/env bash
# Run the backend (pytest) and frontend (vitest) test suites concurrently.
# Exits non-zero if either suite fails.
set -u
cd "$(dirname "$0")"

BACK_LOG="$(mktemp)"
FRONT_LOG="$(mktemp)"

echo "▶ backend  (pytest)  …"
( cd backend && ./.venv/bin/python -m pytest -q ) > "$BACK_LOG" 2>&1 &
BACK_PID=$!

echo "▶ frontend (vitest)  …"
( cd frontend && npm run test --silent ) > "$FRONT_LOG" 2>&1 &
FRONT_PID=$!

wait "$BACK_PID"; BACK_STATUS=$?
wait "$FRONT_PID"; FRONT_STATUS=$?

echo
echo "================= backend ================="
cat "$BACK_LOG"
echo "================= frontend ================"
cat "$FRONT_LOG"
rm -f "$BACK_LOG" "$FRONT_LOG"

echo
[ "$BACK_STATUS"  -eq 0 ] && echo "✅ backend  passed" || echo "❌ backend  failed"
[ "$FRONT_STATUS" -eq 0 ] && echo "✅ frontend passed" || echo "❌ frontend failed"

[ "$BACK_STATUS" -eq 0 ] && [ "$FRONT_STATUS" -eq 0 ]
