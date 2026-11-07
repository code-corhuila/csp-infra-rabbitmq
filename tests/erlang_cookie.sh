#!/usr/bin/env sh
# The Erlang cookie must belong to the rabbitmq user whoever creates it first (csp-infra-rabbitmq#10). The health check
# is the command that can create it before the server does, so the check runs it exactly as the compose file defines it.
# Usage: sh tests/erlang_cookie.sh [starts]     (default 10 starts from an empty volume)
# It uses the current Compose project (COMPOSE_PROJECT_NAME, COMPOSE_FILE, the four passwords), removes its volume and
# leaves the instance stopped. Every check is counted: the last line prints the totals and the exit code is non-zero if
# one failed or none ran.
set -u
# Git Bash on Windows would rewrite the container paths below; on Linux the variable has no effect.
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
starts="${1:-10}"
cookie=/var/lib/rabbitmq/.erlang.cookie
total=0
failures=0

pass() { total=$((total + 1)); echo "ok   $1"; }
fail() { total=$((total + 1)); failures=$((failures + 1)); echo "FAIL $1"; }

# the health check command, as the composition defines it (after the interpolation of $$)
health_cmd=$(docker compose config --format json | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["services"]["rabbitmq"]["healthcheck"]["test"][1])')
[ -n "$health_cmd" ] || { echo "FAIL could not read the health check of the composition"; exit 1; }

docker compose down -v >/dev/null 2>&1

# 1. Deterministic: the health check runs while no server has written the cookie yet (the slow start of a loaded host).
owner=$(docker compose run --rm --no-deps --entrypoint sh rabbitmq -c "$health_cmd >/dev/null 2>&1; stat -c %U $cookie" 2>&1 | tail -n 1)
if [ "$owner" = rabbitmq ]; then
  pass "the cookie created by the health check before the server belongs to rabbitmq"
else
  fail "the cookie created by the health check before the server belongs to rabbitmq: owner is '$owner'"
fi

# 2. Real starts from an empty volume: the health check is run as soon as the container runs, then the broker must be healthy.
i=1
while [ "$i" -le "$starts" ]; do
  docker compose down -v >/dev/null 2>&1
  docker compose up -d rabbitmq >/dev/null 2>&1
  docker compose exec -T rabbitmq sh -c "$health_cmd" >/dev/null 2>&1
  status=starting
  tries=0
  while [ "$tries" -lt 60 ]; do
    status=$(docker compose ps --format '{{.State}}/{{.Health}}' rabbitmq 2>/dev/null)
    case "$status" in running/healthy | exited/* ) break ;; esac
    tries=$((tries + 1))
    sleep 3
  done
  if [ "$status" = running/healthy ]; then
    pass "start $i of $starts from an empty volume ends healthy"
  else
    fail "start $i of $starts from an empty volume ends healthy: state is '$status'"
    docker compose logs rabbitmq 2>&1 | grep -i 'eacces' | head -n 2
  fi
  i=$((i + 1))
done

docker compose down -v >/dev/null 2>&1
echo "total: $total checks, $failures failed"
[ "$failures" -eq 0 ] && [ "$total" -gt 0 ]
