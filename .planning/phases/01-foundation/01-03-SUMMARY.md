---
phase: 01-foundation
plan: 03
subsystem: storage
tags: [sqlite, aiosqlite, migrations, repository-pattern, wal-mode]
dependency-graph:
  requires: [01-01]
  provides: [database-manager, migration-runner, dlq-repository, audit-repository, interaction-repository, idempotency-repository]
  affects: [02-*, 03-*, 04-*]
tech-stack:
  added: [aiosqlite]
  patterns: [repository-pattern, migration-tracking, wal-mode]
key-files:
  created:
    - migrations/001_initial.sql
    - app/core/database.py
    - app/models/database.py
  modified: []
decisions:
  - id: D-0103-01
    summary: "WAL mode enabled both in migration SQL and at connection time for defense-in-depth"
  - id: D-0103-02
    summary: "Migration runner uses _migrations table with name-based tracking, sorted by filename"
  - id: D-0103-03
    summary: "Repositories return plain dicts (not Row objects) for JSON-serialization compatibility"
metrics:
  duration: ~1 min
  completed: 2026-03-05
---

# Phase 1 Plan 3: SQLite Persistent Storage Summary

**One-liner:** aiosqlite database with WAL mode, auto-migration runner, and typed repository classes for DLQ, audit, interaction, and idempotency tables.

## What Was Built

### migrations/001_initial.sql
Full DDL creating four tables with proper defaults and four indexes:
- `dead_letter_queue` -- failed webhook payloads with status tracking and retry count
- `audit_snapshots` -- daily audit results with JSON issues_by_type and full_results
- `interaction_log` -- every human interaction, keyed by opportunity_id
- `idempotency_keys` -- deduplication with TTL cleanup support

### app/core/database.py
Database manager class providing:
- `Database(db_path)` -- stores path, creates parent dir on connect
- `connect()` -- opens aiosqlite connection with WAL mode and Row factory
- `run_migrations(db)` -- classmethod that tracks applied migrations in `_migrations` table, scans `migrations/` dir, applies unapplied `.sql` files in sorted order
- `close(db)` -- static method to close connection
- `get_db(request)` -- FastAPI dependency pulling `request.app.state.db`

### app/models/database.py
Four repository classes, all accepting `aiosqlite.Connection`:
- **DLQRepository** -- add, get_pending, update_status, get_by_id
- **AuditRepository** -- add, get_latest, get_by_date
- **InteractionRepository** -- add, get_by_opportunity, get_recent
- **IdempotencyRepository** -- exists, add, cleanup_old

All use parameterized `?` queries. All return plain dicts via `dict(row)` conversion.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | e85c7d1 | Database manager, migration runner, initial schema DDL |
| 2 | 157e92b | Repository classes for all four tables |

## Deviations from Plan

None -- plan executed exactly as written. Note: `app/main.py` lifespan wiring was intentionally deferred to the orchestrator (parallel worktree coordination).

## Decisions Made

1. **D-0103-01: Dual WAL pragma** -- WAL is set both in 001_initial.sql and in Database.connect(). The migration SQL ensures WAL even if the DB is opened by other tools; the connect() call ensures WAL for the app connection regardless of migration state.

2. **D-0103-02: Filename-based migration tracking** -- Migrations tracked by filename in `_migrations` table. Simple, deterministic, no version numbers to manage.

3. **D-0103-03: Dict return type** -- Repositories convert `aiosqlite.Row` to plain `dict` before returning. This avoids serialization issues and keeps the interface clean for JSON responses.

## Next Phase Readiness

- Database manager and repositories are ready to be wired into `app/main.py` lifespan (orchestrator will handle this)
- All four tables available for Phase 2+ business logic (webhook DLQ, audit snapshots, interaction logging, idempotency)
- No blockers identified
