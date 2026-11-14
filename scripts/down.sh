#!/usr/bin/env sh
# Stops the RabbitMQ instance of an environment. The named volume (and the durable queues in it) is kept.
# Usage: ./scripts/down.sh dev|qa|main
set -eu
cd "$(dirname "$0")/.."
env_name="${1:?usage: down.sh dev|qa|main}"
[ "$env_name" = dev ] && COMPOSE_FILE=compose.yml:compose.dev.yml || COMPOSE_FILE=compose.yml
export COMPOSE_FILE COMPOSE_PATH_SEPARATOR=:
docker compose --env-file "env/$env_name.env" down
