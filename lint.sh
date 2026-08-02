#!/usr/bin/env bash
# Lint both halves of the project. Pass --fix to apply what can be fixed.
# Exits non-zero if either linter complains.
set -u
cd "$(dirname "$0")"

FIX=""
[ "${1:-}" = "--fix" ] && FIX="--fix"

echo "▸ backend  (ruff)"
( cd backend && ./.venv/bin/ruff check $FIX . && ./.venv/bin/ruff format --check . )
BACK=$?

echo
echo "▸ frontend (eslint)"
( cd frontend && npx eslint . $FIX )
FRONT=$?

echo
[ "$BACK"  -eq 0 ] && echo "✅ backend  clean" || echo "❌ backend  has findings"
[ "$FRONT" -eq 0 ] && echo "✅ frontend clean" || echo "❌ frontend has findings"

[ "$BACK" -eq 0 ] && [ "$FRONT" -eq 0 ]
