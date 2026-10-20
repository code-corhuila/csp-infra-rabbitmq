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
