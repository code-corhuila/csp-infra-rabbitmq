# csp-infra-rabbitmq

> The single RabbitMQ instance of the platform: the exchanges of the domain events and one login per domain (ADR-036)

Part of the **Cinesync Platform** distributed system, team `cinesync-platform`, Group 1.
Governance and documentation live in [`csp-docs`](https://github.com/code-corhuila/csp-docs).
The decision is recorded in ADR-036 (`ADR-036-rabbitmq-instance-for-the-domain-events`, in `05-architecture/decisions/` of `csp-docs`).

## Purpose

Each domain publishes its events through its outbox and the relay of `csp-worker`; the consumers read them from their own
queues. This repository **defines the only RabbitMQ broker** of each environment: the two shared exchanges, the logins
and the limits. It creates **no queue**: each consuming service declares its own queues and bindings at start-up.

```
compose.yml                           the rabbitmq service: pinned image, volume, definitions rendered at start, health check
compose.dev.yml                       dev only: the management UI published to 127.0.0.1
rabbitmq/definitions.json.template    exchanges, logins, permissions and limits policy, without any secret
rabbitmq/rabbitmq.conf                message size, disk and memory limits
env/<env>.env.example                 one file per environment: dev, qa, main (git-ignored without .example)
scripts/up.sh, down.sh                bring an environment up (waits until healthy) and stop it
tests/instance.sh, amqp_checks.py     the checks of the CI, also runnable by hand
.github/workflows/ci.yml              starts the instance as dev and as qa/main and runs the checks
```

## Environments and variables

Each environment has its own env file, its own `COMPOSE_PROJECT_NAME` (`csp-dev`, `csp-qa`, `csp-main`) and therefore its
own container and volume (J.5.1). Only the `.example` file is versioned. In `qa` and `main` the real values come from the
secret of the environment, never from the repository.

| Variable | Meaning |
|---|---|
| `COMPOSE_PROJECT_NAME` | Compose project of the environment |
| `RABBITMQ_MANAGEMENT_PORT` | dev only: host port of the management UI (default `15672`, bound to `127.0.0.1`) |
| `RABBIT_ADMIN_PASSWORD` | password of `rabbit_admin` |
| `WORKER_RELAY_PASSWORD` | password of `worker_relay` |
| `TICKETING_FULFILLMENT_PASSWORD`, `CONCESSIONS_PASSWORD` | passwords of the consumer logins |
| `RELAY_AMQP_URL`, `TICKETING_AMQP_URL`, `CONCESSIONS_AMQP_URL` | what the services read: `amqp://<login>:<password>@rabbitmq:5672/` |

Passwords go inside the URLs, so they must be URL-safe: `openssl rand -hex 24`.

## How to run

```bash
cp env/dev.env.example env/dev.env      # then set the four passwords
./scripts/up.sh dev                     # creates the platform network, starts RabbitMQ and waits until it is healthy
./scripts/down.sh dev                   # stops it; the volume and its durable queues are kept
./scripts/up.sh qa                      # the environment argument is mandatory
```

The image is `rabbitmq:3.13-management-alpine`, pinned by tag. Services reach the broker by the name `rabbitmq` on the
`platform` network, never by `localhost`. The AMQP port (5672) is never published to the host. The management UI (15672)
is published to `127.0.0.1` only in `dev`; in `qa` and `main` it is only exposed to the network and the broker is operated
with `rabbitmqctl` inside the container:

```bash
docker compose --env-file env/qa.env exec rabbitmq rabbitmqctl list_queues name messages
```

By hand, outside the scripts, `dev` needs both files: `COMPOSE_FILE=compose.yml:compose.dev.yml COMPOSE_PATH_SEPARATOR=:`.

## Topology

| Object | Value |
|---|---|
| Exchange `cine.events` | topic, durable; the relay publishes here with routing keys `cine.<producer>.<event>.v1` |
| Exchange `cine.dlx` | topic, durable; dead-letter exchange |
| Queues | none created here. A consumer declares `cine.<service>.<event-type>` and `cine.<service>.<event-type>.dlq` |

A consumer declares, at start-up: the DLQ, bound to `cine.dlx` with the **queue name** as routing key; the queue with
`x-dead-letter-exchange=cine.dlx` and `x-dead-letter-routing-key=<queue name>`; and the queue bound to `cine.events` by
the routing key of the event. The dead-letter routing key must be the queue name: with the default (the original
routing key) the DLQs of two consumers of the same event would both receive each other's dead letters.

## Logins

The definitions file is rebuilt at every start from the template and the passwords, and kept inside the container with
mode `400`. Only salted SHA-256 hashes are written to it. The file is never versioned. The user `guest` does not exist.

| Login | Configure | Write | Read | Used by |
|---|---|---|---|---|
| `rabbit_admin` | all | all | all | operation and the container health check; no service |
| `worker_relay` | nothing | `cine.events` | nothing | `csp-worker` (the relay only publishes) |
| `ticketing_fulfillment` | `cine.ticketing_fulfillment.*` | its queues and `cine.dlx` | its queues, `cine.events`, `cine.dlx` | `csp-ticketing-api` |
| `concessions` | `cine.concessions.*` | its queues and `cine.dlx` | its queues, `cine.events`, `cine.dlx` | `csp-concessions-api` |

The consumers need **read** on the two exchanges because RabbitMQ requires read access to the source exchange to create
a binding, and **write** on `cine.dlx` because it requires write access to the dead-letter exchange to declare a queue with
`x-dead-letter-exchange`. Neither lets them publish to `cine.events` (no write) or touch another service's queues.
The tests assert each of these rules against a real broker. A new consuming domain gets its login with a small change of
the template, the variable and the tests.

## Limits (Norma 5.3.10)

- Policy `cine-limits` over the queues `^cine[.]`: `max-length` 10000, `overflow` `reject-publish`, `message-ttl` 7 days.
  A full queue rejects the publishes of the relay, so the outbox rows wait in `PENDING`.
- `max_message_size` 131072 bytes; `disk_free_limit` 1 GB and `vm_memory_high_watermark` 0.4, in `rabbitmq/rabbitmq.conf`.

## Rules

- **No secret is versioned.** `env/*.env.example` carries names and placeholders only.
- **Single node, named volume.** Durable queues and persistent messages survive a restart; a lost volume loses the queues
  (the events stay in the outbox).
- The definitions are imported at every start; a login or a permission removed from the template is not removed from an
  existing volume (use `rabbitmqctl`, or drop the volume in dev).

## Tests

`tests/instance.sh dev` is the check the CI runs after starting the instance. It verifies the exchanges (type and
durability), that no queue exists, the logins and permissions, the policy and the limits, that no clear password is in the
rendered definitions, the ports and the restart. It also runs `tests/amqp_checks.py` in a throwaway Python container on
the `platform` network: guest and wrong passwords refused, the relay publishes and cannot declare or read, each consumer
declares its queue and DLQ, binds, consumes, dead-letters with a `nack` and cannot publish to `cine.events` or touch
another service's queues, an oversized message is rejected and a full queue rejects the publish. `tests/instance.sh qa`
checks that nothing is published. Both print their totals and fail if no check ran. By hand, set the four passwords,
`COMPOSE_PROJECT_NAME` and the compose files, and run `sh tests/instance.sh dev`.

## Branching

Three permanent branches. **None of them accepts a direct commit** — you enter through a child
branch and leave through a Pull Request.

```
develop  <--PR--  feat/... fix/... chore/...
qa       <--PR--  qa/...
main     <--PR--  release/...  hotfix/...
```

Promotion happens **by re-application** (`git cherry-pick -x`), never by merging one permanent
branch into another: `merge develop -> qa` and `merge qa -> main` do not exist in this model.

`main` requires **1 approval from `ariel5253`**. On `develop` and `qa` the team sets its own review
rule.

Full policy: `00-governance/branching-policy.md` in `csp-docs`.
