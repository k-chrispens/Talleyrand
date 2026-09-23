#!/usr/bin/env bash
# Start a local Talleyrand session: MongoDB in Docker, the backend and frontend
# on this machine, and every model call on your own signed-in Claude Code
# subscription. No accounts, no API keys. Ctrl-C stops the session.
#
# One-time setup: Docker, pnpm, uv (or pdm), and `claude auth login`.
set -euo pipefail
cd "$(dirname "$0")"

die() {
  echo "start-local: $*" >&2
  exit 1
}

# --- preflight ---------------------------------------------------------------
docker info >/dev/null 2>&1 || die "Docker is not running. Start your Docker runtime (Docker Desktop, OrbStack, ...) and try again."
command -v pnpm >/dev/null || die "pnpm is not installed."
command -v claude >/dev/null || die "Claude Code is not installed (https://claude.com/claude-code)."
if command -v pdm >/dev/null; then
  PDM="pdm"
elif command -v uvx >/dev/null; then
  PDM="uvx pdm@2.29.0" # the version dev.Dockerfile pins
else
  die "Neither pdm nor uv is installed."
fi
for port in 8000 3000; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    die "Port $port is already in use. A docker compose backend? Stop it with: docker compose down"
  fi
done
if ! claude auth status --json 2>/dev/null | grep -Eq '"authMethod": *"claude.ai"'; then
  echo "start-local: warning: Claude Code is not signed in with a Claude subscription." >&2
  echo "start-local: answers will fail until you run: claude auth login" >&2
fi

# The backend never passes these to `claude` anyway; this is a second guard
# against a run landing on API billing instead of the subscription.
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL OPENAI_API_KEY

# --- MongoDB -----------------------------------------------------------------
# Never a bare `docker compose up`: that also starts the containerized backend.
docker compose up -d mongodb >/dev/null
echo "start-local: waiting for MongoDB..."
# A port check, not `docker compose exec`: exec refuses to run without
# backend/.env, which a local session doesn't need. The port only answers once
# the real server is up (first-run initialization listens inside the container).
for i in $(seq 60); do
  nc -z localhost 27017 >/dev/null 2>&1 && break
  [ "$i" = 60 ] && die "MongoDB did not come up. See: docker compose logs mongodb"
  sleep 1
done

# --- dependencies (first run only) -------------------------------------------
[ -x backend/.venv/bin/uvicorn ] || (cd backend && $PDM install)
[ -x frontend/node_modules/.bin/vite ] || (cd frontend && pnpm install --frozen-lockfile)

# --- backend and frontend ----------------------------------------------------
BACKEND_PID=""
FRONTEND_PID=""
cleanup() {
  trap - EXIT INT TERM
  echo
  echo "start-local: stopping..."
  # The backend's shutdown kills any Claude Code run still in flight.
  [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
  wait 2>/dev/null
  echo "start-local: stopped. MongoDB keeps running; stop it with: docker compose stop mongodb"
}
trap cleanup EXIT INT TERM

# Loopback only: local mode has no sign-in, so the network is the boundary.
(
  cd backend
  export LOCAL_MODE=true AGENT_BACKEND=claude_code COOKIE_SECURE=false
  export MONGODB_URL=mongodb://admin:admin@localhost:27017
  exec .venv/bin/uvicorn talleyrand.main:app --host 127.0.0.1 --port 8000 \
    --timeout-graceful-shutdown 10
) &
BACKEND_PID=$!

echo "start-local: waiting for the backend..."
until curl -sf http://127.0.0.1:8000/backend/health >/dev/null; do
  kill -0 "$BACKEND_PID" 2>/dev/null || die "The backend failed to start (see the output above)."
  sleep 0.5
done

(
  cd frontend
  export VITE_BACKEND_URL=localhost:8000 VITE_IS_SECURE=false VITE_AGENT_BACKEND=claude_code
  exec node_modules/.bin/vite --host 127.0.0.1 --port 3000 --strictPort
) &
FRONTEND_PID=$!

until curl -sf http://127.0.0.1:3000 >/dev/null; do
  kill -0 "$FRONTEND_PID" 2>/dev/null || die "The frontend failed to start (see the output above)."
  sleep 0.5
done

echo
echo "start-local: Talleyrand is running at http://localhost:3000 (Ctrl-C to stop)"
open http://localhost:3000/research 2>/dev/null || true

# Stop the whole session as soon as either half exits.
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done
