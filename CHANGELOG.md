# Changelog

All notable changes of `csp-infra-rabbitmq` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Each change is recorded in the pull request that introduces it.

## [Unreleased]

### Added

- The single RabbitMQ instance of the platform for the domain events (ADR-036): `compose.yml` with the pinned image
  `rabbitmq:3.13-management-alpine`, a named volume, no published AMQP port and the `platform` network;
  `compose.dev.yml`, which publishes the management UI to `127.0.0.1` only in dev.
- `rabbitmq/definitions.json.template` (the exchanges `cine.events` and `cine.dlx`, the logins `rabbit_admin`,
  `worker_relay`, `ticketing_fulfillment` and `concessions` with their permissions, and the `cine-limits` policy) and
  `rabbitmq/rabbitmq.conf` (`max_message_size`, `disk_free_limit`, `vm_memory_high_watermark`). The definitions are
  rendered at every start with password hashes only.
- The env examples of `dev`, `qa` and `main`; `scripts/up.sh` and `scripts/down.sh`.
- `.github/workflows/ci.yml`, `tests/instance.sh` and `tests/amqp_checks.py`: they start the instance and check the
  exchanges, the logins, the permissions against a real broker, the dead-letter path, the limits, the ports and the restart.
- The pull request template of the annex, `.gitignore` and `.gitattributes`.
- This changelog.

### Changed

- `README.md` describes the instance, its variables, its logins, its permission patterns and the naming of the queues,
  and cites ADR-036.

### Fixed

- The queue `cine.ticketing_fulfillment.ticket-issued` has no consumer (it exists only so that the relay can route `TicketIssued`), but the policy `cine-limits` made a full one reject the relay (`reject-publish` after 10000 events). It now has its own policy `cine-ticket-issued-sink` (priority 10, `max-length` 10000, `message-ttl` 7 days, `overflow` `drop-head`): the oldest event is dropped and dead-lettered, and a publish is always accepted. `tests/instance.sh` checks the policy and `tests/amqp_checks.py` publishes past `max-length` against a real broker (`csp-infra-rabbitmq#13`, ADR-070).
- The broker no longer stops at start with `eacces` on `/var/lib/rabbitmq/.erlang.cookie` (csp-infra-rabbitmq#10): the
  health check runs as the `rabbitmq` user, so the first check can no longer create the cookie as root while the server
  boots. `tests/erlang_cookie.sh` checks the owner of the cookie and 10 starts from an empty volume, and the CI runs it.

- The `RELAY_AMQP_URL`, `TICKETING_AMQP_URL` and `CONCESSIONS_AMQP_URL` of the env examples and the README end in `/%2F`
  (the default vhost `/` URL-encoded) instead of a bare `/`, which the Java amqp-client and Spring AMQP read as an empty
  vhost ("vhost not found"). `tests/env_urls.py` parses every URL of the examples and the README, and `tests/instance.sh`
  checks that the vhost `/` exists on the broker (HU-AUTH-004).
