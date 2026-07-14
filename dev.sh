#!/usr/bin/env bash
# Run the backend API and the frontend dev server together.
# Ctrl-C stops both. Backend -> :8000, frontend -> :5173.
set -u
cd "$(dirname "$0")"

cleanup() { kill 0 2>/dev/null; }
trap cleanup EXIT INT TERM

BACKEND_PORT="${BACKEND_PORT:-8008}"

echo "▶ backend  http://localhost:${BACKEND_PORT}"
( cd backend && ./.venv/bin/uvicorn main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload ) &

echo "▶ frontend http://localhost:5173"
( cd frontend && npm run dev ) &

wait
