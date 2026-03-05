"""Typed repository classes for Atlas SQLite tables.

All repositories accept an ``aiosqlite.Connection`` and use parameterized
queries with ``?`` placeholders.  Rows are returned as plain dicts
(converted from ``aiosqlite.Row``).
"""

from __future__ import annotations

from typing import Optional

import aiosqlite


def _row_to_dict(row: aiosqlite.Row | None) -> dict | None:
    """Convert an aiosqlite.Row to a plain dict, or return None."""
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows: list[aiosqlite.Row]) -> list[dict]:
    """Convert a list of aiosqlite.Row objects to plain dicts."""
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Dead Letter Queue
# ---------------------------------------------------------------------------


class DLQRepository:
    """CRUD helpers for the ``dead_letter_queue`` table."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db

    async def add(
        self,
        event_type: str,
        payload: str,
        error_message: str,
        error_context: Optional[str] = None,
    ) -> int:
        """Insert a failed event and return its id."""
        cursor = await self.db.execute(
            "INSERT INTO dead_letter_queue "
            "(event_type, payload, error_message, error_context) "
            "VALUES (?, ?, ?, ?)",
            (event_type, payload, error_message, error_context),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def get_pending(self, limit: int = 50) -> list[dict]:
        """Return pending DLQ entries ordered by creation time."""
        cursor = await self.db.execute(
            "SELECT * FROM dead_letter_queue "
            "WHERE status = 'pending' "
            "ORDER BY created_at LIMIT ?",
            (limit,),
        )
        return _rows_to_dicts(await cursor.fetchall())

    async def update_status(self, id: int, status: str) -> None:
        """Update the status (and updated_at) of a DLQ entry."""
        await self.db.execute(
            "UPDATE dead_letter_queue "
            "SET status = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (status, id),
        )
        await self.db.commit()

    async def get_by_id(self, id: int) -> dict | None:
        """Return a single DLQ entry by id, or None."""
        cursor = await self.db.execute(
            "SELECT * FROM dead_letter_queue WHERE id = ?", (id,)
        )
        return _row_to_dict(await cursor.fetchone())

    async def get_all(self, limit: int = 50, status: str | None = None) -> list[dict]:
        """Return DLQ entries optionally filtered by status, ordered by created_at DESC."""
        if status:
            cursor = await self.db.execute(
                "SELECT * FROM dead_letter_queue WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM dead_letter_queue ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return _rows_to_dicts(await cursor.fetchall())

    async def retry_entry(self, id: int) -> dict | None:
        """Increment retry_count and set status to 'retrying'."""
        await self.db.execute(
            "UPDATE dead_letter_queue SET status = 'retrying', "
            "retry_count = retry_count + 1, updated_at = datetime('now') "
            "WHERE id = ?",
            (id,),
        )
        await self.db.commit()
        return await self.get_by_id(id)


# ---------------------------------------------------------------------------
# Audit Snapshots
# ---------------------------------------------------------------------------


class AuditRepository:
    """CRUD helpers for the ``audit_snapshots`` table."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db

    async def add(
        self,
        run_date: str,
        run_type: str,
        total_opps: int,
        total_issues: int,
        issues_by_type: str,
        full_results: str,
    ) -> int:
        """Insert an audit snapshot and return its id."""
        cursor = await self.db.execute(
            "INSERT INTO audit_snapshots "
            "(run_date, run_type, total_opportunities, total_issues, "
            "issues_by_type, full_results) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_date, run_type, total_opps, total_issues, issues_by_type, full_results),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def get_latest(self, limit: int = 7) -> list[dict]:
        """Return the most recent audit snapshots."""
        cursor = await self.db.execute(
            "SELECT * FROM audit_snapshots ORDER BY run_date DESC LIMIT ?",
            (limit,),
        )
        return _rows_to_dicts(await cursor.fetchall())

    async def get_by_date(self, run_date: str) -> dict | None:
        """Return an audit snapshot for a specific date, or None."""
        cursor = await self.db.execute(
            "SELECT * FROM audit_snapshots WHERE run_date = ?", (run_date,)
        )
        return _row_to_dict(await cursor.fetchone())


# ---------------------------------------------------------------------------
# Interaction Log
# ---------------------------------------------------------------------------


class InteractionRepository:
    """CRUD helpers for the ``interaction_log`` table."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db

    async def add(
        self,
        interaction_type: str,
        user_id: str,
        channel_id: Optional[str] = None,
        opportunity_id: Optional[str] = None,
        field_name: Optional[str] = None,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        context: Optional[str] = None,
    ) -> int:
        """Insert an interaction record and return its id."""
        cursor = await self.db.execute(
            "INSERT INTO interaction_log "
            "(interaction_type, user_id, channel_id, opportunity_id, "
            "field_name, old_value, new_value, context) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                interaction_type,
                user_id,
                channel_id,
                opportunity_id,
                field_name,
                old_value,
                new_value,
                context,
            ),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def get_by_opportunity(
        self, opp_id: str, limit: int = 50
    ) -> list[dict]:
        """Return interactions for a specific opportunity."""
        cursor = await self.db.execute(
            "SELECT * FROM interaction_log "
            "WHERE opportunity_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (opp_id, limit),
        )
        return _rows_to_dicts(await cursor.fetchall())

    async def get_recent(self, limit: int = 100) -> list[dict]:
        """Return the most recent interactions across all opportunities."""
        cursor = await self.db.execute(
            "SELECT * FROM interaction_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return _rows_to_dicts(await cursor.fetchall())


# ---------------------------------------------------------------------------
# Idempotency Keys
# ---------------------------------------------------------------------------


class IdempotencyRepository:
    """CRUD helpers for the ``idempotency_keys`` table."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db

    async def exists(self, key: str) -> bool:
        """Check whether an idempotency key has already been processed."""
        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM idempotency_keys WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return bool(row and row[0] > 0)

    async def add(
        self, key: str, event_type: str, result: str = "success"
    ) -> None:
        """Record a processed idempotency key (ignores duplicates)."""
        await self.db.execute(
            "INSERT OR IGNORE INTO idempotency_keys (key, event_type, result) "
            "VALUES (?, ?, ?)",
            (key, event_type, result),
        )
        await self.db.commit()

    async def cleanup_old(self, days: int = 30) -> int:
        """Delete idempotency keys older than *days* and return count deleted."""
        cursor = await self.db.execute(
            "DELETE FROM idempotency_keys "
            "WHERE processed_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        await self.db.commit()
        return cursor.rowcount
