#!/usr/bin/env bash
# Register / switch to the docker-rt Docker context.
set -euo pipefail

SOCK="${DOCKER_RT_SOCK:-/tmp/docker-rt.sock}"
NAME="${DOCKER_RT_CONTEXT:-docker-rt}"
HOST="unix://${SOCK}"

if docker context inspect "$NAME" >/dev/null 2>&1; then
  echo "Context '$NAME' already exists; updating endpoint..."
  # docker context update is limited; recreate if host differs
  CURRENT="$(docker context inspect "$NAME" -f '{{.Endpoints.docker.Host}}' 2>/dev/null || true)"
  if [[ "$CURRENT" != "$HOST" ]]; then
    docker context rm -f "$NAME" >/dev/null
    docker context create "$NAME" --docker "host=${HOST}"
  fi
else
  docker context create "$NAME" --docker "host=${HOST}"
fi

docker context use "$NAME"
echo "Using context '$NAME' -> ${HOST}"
echo "Start the daemon first, e.g.:"
echo "  DOCKER_RT_SOCK=${SOCK} python server.py"
