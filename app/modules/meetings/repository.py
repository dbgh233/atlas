"""Meeting intelligence repository — CRUD for meetings, commitments, and patterns."""

from __future__ import annotations

import json
from typing import Optional

import aiosqlite
import structlog

log = structlog.get_logger()


class MeetingRepository:
    """Database operations for meeting transcripts."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db

    async def upsert_meeting(
        self,
        otter_speech_id: str,
        title: str,
        start_time: str,
        meeting_type: Optional[str] = None,
        organizer: Optional[str] = None,
        end_time: Optional[str] = None,
        duration_minutes: Optional[int] = None,
        attendees: Optional[list[str]] = None,
        summary: Optional[str] = None,
        transcript_text: Optional[str] = None,
        merchants_mentioned: Optional[list[str]] = None,
    ) -> int:
        """Insert or update a meeting record. Returns the meeting ID."""
        cursor = await self.db.execute(
            """INSERT INTO meetings
               (otter_speech_id, title, meeting_type, organizer, start_time,
                end_time, duration_minutes, attendees, summary, transcript_text,
                merchants_mentioned)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(otter_speech_id) DO UPDATE SET
                 title = excluded.title,
                 meeting_type = excluded.meeting_type,
                 summary = excluded.summary,
                 transcript_text = excluded.transcript_text,
                 merchants_mentioned = excluded.merchants_mentioned,
                 processed_at = datetime('now')""",
            (
                otter_speech_id,
                title,
                meeting_type,
                organizer,
                start_time,
                end_time,
                duration_minutes,
                json.dumps(attendees) if attendees else None,
                summary,
                transcript_text,
                json.dumps(merchants_mentioned) if merchants_mentioned else None,
            ),
        )
        await self.db.commit()
        # Get the ID (either new or existing)
        id_cursor = await self.db.execute(
            "SELECT id FROM meetings WHERE otter_speech_id = ?",
            (otter_speech_id,),
        )
        row = await id_cursor.fetchone()
        return row[0] if row else cursor.lastrowid

    async def get_recent(self, limit: int = 10) -> list[dict]:
        """Get recent meetings."""
        cursor = await self.db.execute(
            "SELECT * FROM meetings ORDER BY start_time DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_by_id(self, meeting_id: int) -> Optional[dict]:
        """Get a meeting by ID."""
        cursor = await self.db.execute(
            "SELECT * FROM meetings WHERE id = ?", (meeting_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


class CommitmentRepository:
    """Database operations for meeting commitments."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db

    async def add(
        self,
        meeting_id: int,
        assignee_name: str,
        action: str,
        assignee_ghl_id: Optional[str] = None,
        merchant_name: Optional[str] = None,
        opportunity_id: Optional[str] = None,
        deadline: Optional[str] = None,
        source_quote: Optional[str] = None,
    ) -> int:
        """Insert a commitment and return its ID."""
        cursor = await self.db.execute(
            """INSERT INTO commitments
               (meeting_id, assignee_name, action, assignee_ghl_id,
                merchant_name, opportunity_id, deadline, source_quote)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                meeting_id,
                assignee_name,
                action,
                assignee_ghl_id,
                merchant_name,
                opportunity_id,
                deadline,
                source_quote,
            ),
        )
        await self.db.commit()
        return cursor.lastrowid

    async def get_open(self, assignee_ghl_id: Optional[str] = None) -> list[dict]:
        """Get open commitments, optionally filtered by assignee."""
        if assignee_ghl_id:
            cursor = await self.db.execute(
                "SELECT c.*, m.title as meeting_title, m.start_time as meeting_date "
                "FROM commitments c JOIN meetings m ON c.meeting_id = m.id "
                "WHERE c.status = 'open' AND c.assignee_ghl_id = ? "
                "ORDER BY c.created_at DESC",
                (assignee_ghl_id,),
            )
        else:
            cursor = await self.db.execute(
                "SELECT c.*, m.title as meeting_title, m.start_time as meeting_date "
                "FROM commitments c JOIN meetings m ON c.meeting_id = m.id "
                "WHERE c.status = 'open' "
                "ORDER BY c.created_at DESC",
            )
        return [dict(r) for r in await cursor.fetchall()]

    async def update_status(
        self,
        commitment_id: int,
        status: str,
        evidence: Optional[str] = None,
    ) -> None:
        """Update a commitment's status."""
        if status == "fulfilled":
            await self.db.execute(
                """UPDATE commitments
                   SET status = ?, fulfilled_at = datetime('now'),
                       fulfilled_evidence = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (status, evidence, commitment_id),
            )
        else:
            await self.db.execute(
                """UPDATE commitments
                   SET status = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (status, commitment_id),
            )
        await self.db.commit()

    async def get_missed(self) -> list[dict]:
        """Get commitments that are past deadline and still open."""
        cursor = await self.db.execute(
            "SELECT c.*, m.title as meeting_title, m.start_time as meeting_date "
            "FROM commitments c JOIN meetings m ON c.meeting_id = m.id "
            "WHERE c.status = 'open' AND c.deadline IS NOT NULL "
            "AND c.deadline < datetime('now') "
            "ORDER BY c.deadline ASC",
        )
        return [dict(r) for r in await cursor.fetchall()]


class PatternRepository:
    """Database operations for pipeline patterns."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db

    async def upsert(
        self,
        pattern_type: str,
        pattern_key: str,
        description: str,
        evidence: list[dict],
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        confidence: float = 0.5,
        actionable: bool = False,
    ) -> int:
        """Insert or update a pattern. Returns the pattern ID."""
        evidence_json = json.dumps(evidence)
        cursor = await self.db.execute(
            """INSERT INTO pipeline_patterns
               (pattern_type, pattern_key, description, evidence,
                entity_type, entity_id, confidence, actionable)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(pattern_key) DO UPDATE SET
                 description = excluded.description,
                 evidence = excluded.evidence,
                 confidence = excluded.confidence,
                 occurrences = occurrences + 1,
                 last_seen = datetime('now'),
                 actionable = excluded.actionable""",
            (
                pattern_type,
                pattern_key,
                description,
                evidence_json,
                entity_type,
                entity_id,
                confidence,
                1 if actionable else 0,
            ),
        )
        await self.db.commit()
        # Need to handle both insert and update cases for RETURNING
        if cursor.lastrowid:
            return cursor.lastrowid
        id_cursor = await self.db.execute(
            "SELECT id FROM pipeline_patterns WHERE pattern_key = ?",
            (pattern_key,),
        )
        row = await id_cursor.fetchone()
        return row[0] if row else 0

    async def get_actionable(self) -> list[dict]:
        """Get patterns flagged as actionable."""
        cursor = await self.db.execute(
            "SELECT * FROM pipeline_patterns WHERE actionable = 1 "
            "ORDER BY confidence DESC, occurrences DESC",
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_by_entity(
        self, entity_type: str, entity_id: str
    ) -> list[dict]:
        """Get patterns for a specific entity."""
        cursor = await self.db.execute(
            "SELECT * FROM pipeline_patterns "
            "WHERE entity_type = ? AND entity_id = ? "
            "ORDER BY last_seen DESC",
            (entity_type, entity_id),
        )
        return [dict(r) for r in await cursor.fetchall()]
