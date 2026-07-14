#!/usr/bin/env bash
# Start the backend API + agent (uses the local venv).
set -e
cd "$(dirname "$0")"
exec ./.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
