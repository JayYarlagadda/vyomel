# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows milestones (`m0`, `m1`, …) rather than semver until v1.

## [Unreleased] — M0: Foundation

### Added
- Complete design documentation set in `docs/` (overview, requirements, architecture,
  data model, API, tools, security, execution engine, memory/RAG, model serving,
  observability, evaluation, roadmap, environment, resume traceability, risks, workflow, ADRs).
- Core primitives: settings with hard-ceiling enforcement, error hierarchy with
  declared retryability, ULID identifiers, deterministic idempotency keys,
  injectable clock, structured logging with mandatory secret redaction.
- Capability lattice (`L0`–`L4`) with saturating, monotonic escalation.
- Persistence: async SQLAlchemy engine, unit-of-work session scope, `tasks` table,
  Alembic migration `0001` creating extensions and shared enums.
- FastAPI application with `/healthz`, `/readyz`, `/version`, `/metrics`, and
  `POST|GET /v1/tasks`.
- Typer CLI: `serve`, `doctor`, `db upgrade|downgrade|current|check`.
- Local infrastructure: Postgres 17 + pgvector and Redis 7 via Docker Compose in WSL.
- CI: ruff, mypy strict, layering guard, traceability guard, gitleaks, pip-audit,
  pytest against real Postgres and Redis, migration round-trip check.
- Permission policy baseline in `config/policy.yaml`.
