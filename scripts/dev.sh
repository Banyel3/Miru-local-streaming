#!/usr/bin/env bash
# Both halves, one terminal. Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -d apps/web/node_modules ] || (cd apps/web && npm install)

./scripts/start-api.sh --reload &
API=$!
trap 'kill $API 2>/dev/null || true' EXIT

cd apps/web && npm run dev
