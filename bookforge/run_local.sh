#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# BookForge — single-node production launcher (no Docker).
#
# Starts the FastAPI backend (in-process pipeline, local Playwright render) and
# a PRODUCTION build of the Next.js UI. Both read config from bookforge/.env.
#
#   ./run_local.sh              # build UI (if needed) + start API :8000 + UI :3000
#   SKIP_UI_BUILD=1 ./run_local.sh
#   API_PORT=8000 UI_PORT=3000 ./run_local.sh
#
# Requires: bookforge/.venv (pip install -r requirements.txt; playwright install
# chromium) and Node. If Node isn't on PATH, a local build under ~/.local/node
# is picked up automatically.
# ---------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-3000}"
API_URL="${API_URL:-http://localhost:${API_PORT}}"

# --- Node on PATH (fall back to a local install) ---------------------------
if ! command -v node >/dev/null 2>&1; then
  if [ -x "$HOME/.local/node/bin/node" ]; then
    export PATH="$HOME/.local/node/bin:$PATH"
  else
    echo "ERROR: node not found. Install Node 20+ or place a build at ~/.local/node" >&2
    exit 1
  fi
fi

VENV_PY="$HERE/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "ERROR: $VENV_PY not found. Create the venv and install requirements.txt." >&2
  exit 1
fi

# --- Load .env -------------------------------------------------------------
if [ -f "$HERE/.env" ]; then
  set -a; . "$HERE/.env"; set +a
fi
export RENDER_URL="${RENDER_URL:-}"          # empty => local Playwright render
export API_URL                                # consumed by the UI /api/config route

echo "▶ BookForge launching (API :$API_PORT, UI :$UI_PORT, render: ${RENDER_URL:-playwright})"

# --- Build the UI ----------------------------------------------------------
cd "$HERE/app"
if [ "${SKIP_UI_BUILD:-0}" != "1" ]; then
  echo "▶ Building UI (npm ci + next build)…"
  npm ci
  npm run build
fi
cd "$HERE"

# --- Start services --------------------------------------------------------
pids=()
cleanup() { echo; echo "▶ Stopping…"; for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup INT TERM EXIT

echo "▶ Starting API…"
"$HERE/.venv/bin/uvicorn" bookforge.api.main:app --host "$API_HOST" --port "$API_PORT" &
pids+=($!)

echo "▶ Starting UI…"
( cd "$HERE/app" && npm run start -- --port "$UI_PORT" ) &
pids+=($!)

echo "✓ Up. UI: http://localhost:${UI_PORT}   API: ${API_URL}/health"
wait
