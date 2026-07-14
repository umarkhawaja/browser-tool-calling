#!/usr/bin/env bash
# Run the backend API and the frontend dev server together.
# Ctrl-C stops both. Backend -> :8000, frontend -> :5173.
set -u
cd "$(dirname "$0")"

cleanup() { kill 0 2>/dev/null; }
trap cleanup EXIT INT TERM

echo "▶ backend  http://localhost:8000"
( cd backend && ./.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload ) &

echo "▶ frontend http://localhost:5173"
( cd frontend && npm run dev ) &

wait
