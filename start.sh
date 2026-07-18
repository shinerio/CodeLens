#!/usr/bin/env bash

set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
BACKEND_PID=""
FRONTEND_PID=""

fail() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

command -v uv >/dev/null 2>&1 || fail "uv is required: https://docs.astral.sh/uv/"
command -v pnpm >/dev/null 2>&1 || fail "pnpm is required: https://pnpm.io/installation"

cleanup() {
  trap - EXIT INT TERM
  if [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
  if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
  if [ -n "$FRONTEND_PID" ]; then
    wait "$FRONTEND_PID" 2>/dev/null || true
  fi
  if [ -n "$BACKEND_PID" ]; then
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
}

trap cleanup EXIT
trap 'exit 130' INT TERM

cd "$SCRIPT_DIR"

printf '\n[1/3] Installing backend dependencies...\n'
uv sync --project backend || fail "backend dependency installation failed"

printf '\n[2/3] Installing frontend dependencies...\n'
pnpm --dir frontend install || fail "frontend dependency installation failed"

printf '\n[3/3] Starting CodeLens...\n'
uv run --project backend codelens-review start &
BACKEND_PID=$!
pnpm --dir frontend dev --host 127.0.0.1 --strictPort &
FRONTEND_PID=$!

sleep 1
if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
  wait "$BACKEND_PID" 2>/dev/null || true
  fail "backend failed to start; make sure port 8765 is available"
fi
if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
  wait "$FRONTEND_PID" 2>/dev/null || true
  fail "frontend failed to start; make sure port 5173 is available"
fi

printf '\nCodeLens is starting. Open these addresses:\n'
printf '  Frontend:  http://127.0.0.1:5173\n'
printf '  Backend:   http://127.0.0.1:8765\n'
printf '  OpenAPI:   http://127.0.0.1:8765/docs\n'
printf '\nAll locally accessible Git repositories are allowed by default.\n'
printf 'Choose a repository and configure model gateways in the Web UI.\n'
printf 'Press Ctrl+C to stop both services.\n\n'

EXIT_CODE=0
while :; do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    wait "$BACKEND_PID" || EXIT_CODE=$?
    printf 'Backend process stopped.\n' >&2
    break
  fi
  if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    wait "$FRONTEND_PID" || EXIT_CODE=$?
    printf 'Frontend process stopped.\n' >&2
    break
  fi
  sleep 1
done

exit "$EXIT_CODE"
