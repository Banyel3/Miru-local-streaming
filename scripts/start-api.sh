#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -e "apps/api[dev]"

set -a; [ -f .env ] && . ./.env; set +a
exec .venv/bin/uvicorn miru.main:app --host 0.0.0.0 --port 8000 --app-dir apps/api "$@"
