#!/usr/bin/env sh
# Checks the running instance of the current Compose project (ADR-036). The CI runs it after
# `docker compose up -d --wait rabbitmq`; it can be run by hand with the four passwords set.
# Usage: sh tests/instance.sh dev|qa
#   dev: the management UI is published to 127.0.0.1 only (compose.dev.yml) and the full set of checks runs.
#   qa:  nothing is published; it checks the instance, the definitions and the restart (qa and main compose the same).
# Every check is counted: the last line prints the totals and the exit code is non-zero if one failed or none ran.
set -u
# Git Bash on Windows would rewrite the container paths below; on Linux the variable has no effect.
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
mode="${1:?usage: instance.sh dev|qa}"
admin_password="${RABBIT_ADMIN_PASSWORD:?set RABBIT_ADMIN_PASSWORD}"
relay_password="${WORKER_RELAY_PASSWORD:?set WORKER_RELAY_PASSWORD}"
ticketing_password="${TICKETING_FULFILLMENT_PASSWORD:?set TICKETING_FULFILLMENT_PASSWORD}"
concessions_password="${CONCESSIONS_PASSWORD:?set CONCESSIONS_PASSWORD}"
total=0
failures=0

pass() { total=$((total + 1)); echo "ok   $1"; }
fail() { total=$((total + 1)); failures=$((failures + 1)); echo "FAIL $1"; }
# expect <description> <expected text> <actual output>
expect() {
  case "$3" in
    *"$2"*) pass "$1" ;;
    *) fail "$1: expected '$2', got '$3'" ;;
  esac
}
# expect_not <description> <text that must not appear> <actual output>
expect_not() {
  case "$3" in
    *"$2"*) fail "$1: found '$2' in '$3'" ;;
    *) pass "$1" ;;
  esac
}
ctl() { docker compose exec -T rabbitmq rabbitmqctl --quiet "$@" 2>&1; }
login() { ctl authenticate_user "$1" "$2"; }

# ---- exchanges, queues, users, permissions, policy ----
exchanges=$(ctl list_exchanges name type durable)
expect "cine.events is a durable topic exchange" "$(printf 'cine.events\ttopic\ttrue')" "$exchanges"
expect "cine.dlx is a durable topic exchange" "$(printf 'cine.dlx\ttopic\ttrue')" "$exchanges"
queues=$(ctl list_queues name)
[ -z "$queues" ] && pass "the instance creates no queue" || fail "the instance created queues: $queues"

users=$(ctl list_users)
expect_not "the guest user is removed" guest "$users"
for user in rabbit_admin worker_relay ticketing_fulfillment concessions; do expect "the login $user exists" "$user" "$users"; done
expect "only rabbit_admin has a tag (administrator)" "$(printf 'rabbit_admin\t[administrator]')" "$users"

permissions=$(ctl list_permissions)
expect "worker_relay: configure and read nothing, write cine.events only" \
  "$(printf 'worker_relay\t^$\t^cine[.]events$\t^$')" "$permissions"
for service in ticketing_fulfillment concessions; do
  expect "$service: configure only its own queues" "$(printf '%s\t^cine[.]%s[.]\t' "$service" "$service")" "$permissions"
done

policies=$(ctl list_policies)
expect "the limits policy covers the queues cine.*" "$(printf 'cine-limits\t^cine[.]\tqueues')" "$policies"
for setting in '"max-length":10000' '"overflow":"reject-publish"' '"message-ttl":604800000'; do
  expect "the limits policy sets $setting" "$setting" "$policies"
done

config=$(docker compose exec -T rabbitmq cat /etc/rabbitmq/conf.d/20-csp.conf)
expect "max_message_size is 131072" "max_message_size = 131072" "$config"
expect "disk_free_limit is explicit" "disk_free_limit.absolute" "$config"
expect "vm_memory_high_watermark is explicit" "vm_memory_high_watermark.relative" "$config"
effective=$(docker compose exec -T rabbitmq rabbitmq-diagnostics --quiet environment 2>&1)
expect "the broker runs with max_message_size 131072" "{max_message_size,131072}" "$effective"

# ---- secrets: the rendered definitions hold hashes only ----
rendered=$(docker compose exec -T rabbitmq cat /etc/rabbitmq/definitions.json 2>&1)
for password in "$admin_password" "$relay_password" "$ticketing_password" "$concessions_password"; do
  expect_not "a clear password is not in the rendered definitions" "\"$password\"" "$rendered"
done
expect_not "no placeholder is left in the rendered definitions" "@" "$rendered"
expect "the definitions carry salted SHA-256 hashes" rabbit_password_hashing_sha256 "$rendered"

# ---- ports ----
amqp_port=$(docker compose port rabbitmq 5672 2>&1)
# an unpublished port is reported empty or as ":0"; any other host port would mean the broker is open to the host
if echo "$amqp_port" | grep -Eq ':[1-9][0-9]*$'; then fail "port 5672 is published: $amqp_port"; else pass "port 5672 (AMQP) is not published"; fi
management_port=$(docker compose port rabbitmq 15672 2>&1)
if [ "$mode" = dev ]; then
  expect "the management UI is published to 127.0.0.1 only" "127.0.0.1:" "$management_port"
  expect_not "the management UI is not published to every interface" "0.0.0.0" "$management_port"
else
  if echo "$management_port" | grep -Eq ':[1-9][0-9]*$'; then fail "port 15672 is published: $management_port"; else pass "port 15672 (management) is not published"; fi
fi

# ---- logins and permissions on a real broker ----
if [ "$mode" = dev ]; then
  broker=$(docker inspect -f '{{.Name}}' "$(docker compose ps -q rabbitmq)" | tr -d '/')
  amqp_output=$(docker run --rm -i --network platform \
    -e BROKER_HOST="$broker" -e RABBIT_ADMIN_PASSWORD="$admin_password" -e WORKER_RELAY_PASSWORD="$relay_password" \
    -e TICKETING_FULFILLMENT_PASSWORD="$ticketing_password" -e CONCESSIONS_PASSWORD="$concessions_password" \
    python:3.12-alpine sh -c 'pip install -q --disable-pip-version-check pika==1.3.2 >/dev/null 2>&1 && python -' < tests/amqp_checks.py 2>&1)
  echo "$amqp_output"
  amqp_line=$(echo "$amqp_output" | grep '^amqp checks:' | tail -n 1)
  amqp_run=$(echo "$amqp_line" | sed -n 's/^amqp checks: \([0-9]*\) run.*/\1/p')
  amqp_failed=$(echo "$amqp_line" | sed -n 's/.* \([0-9]*\) failed$/\1/p')
  if [ -n "$amqp_run" ] && [ "$amqp_run" -gt 0 ]; then
    total=$((total + amqp_run)); failures=$((failures + amqp_failed))
  else
    fail "the AMQP checks did not report their totals"
  fi
  queues=$(ctl list_queues name)
  [ -z "$queues" ] && pass "the AMQP checks left no queue behind" || fail "queues left behind: $queues"
fi

# ---- restart: the definitions are rendered again and everything still works ----
docker compose restart rabbitmq >/dev/null 2>&1
docker compose up -d --wait rabbitmq >/dev/null 2>&1 && pass "the instance is healthy after a restart" || fail "the instance is not healthy after a restart"
expect "the exchange cine.events survives a restart" "cine.events" "$(ctl list_exchanges name)"
expect "worker_relay authenticates after a restart" "Success" "$(login worker_relay "$relay_password")"
expect "ticketing_fulfillment authenticates after a restart" "Success" "$(login ticketing_fulfillment "$ticketing_password")"
expect "concessions authenticates after a restart" "Success" "$(login concessions "$concessions_password")"
expect "a wrong password is refused" "invalid credentials" "$(login worker_relay wrong)"
expect_not "the guest user stays removed after a restart" guest "$(ctl list_users)"

echo "checks: $total run, $((total - failures)) passed, $failures failed"
[ "$failures" -eq 0 ] && [ "$total" -gt 0 ]
