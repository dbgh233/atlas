"""Confidence scoring engine — tracks approval rates per fix type.

Fix types are normalized strings like "set_industry_type", "set_appointment_status".
Each fix type accumulates approval/rejection counts from user interactions.

CONV-06: Confidence scoring per fix type based on approval history
CONV-07: Graduated autonomy — auto-promote when approval rate >90% for 2+ weeks
CONV-09: Anomaly detection — revert to suggest+confirm if approval rate drops
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite
import structlog

log = structlog.get_logger()

PROMOTION_THRESHOLD = 0.90  # 90% approval rate
PROMOTION_MIN_SAMPLES = 10  # need at least 10 suggestions before promoting
PROMOTION_MIN_DAYS = 14     # must sustain for 2 weeks
REVERSION_THRESHOLD = 0.75  # revert if drops below 75%
REVERSION_WINDOW = 5        # check last 5 interactions for anomaly


def normalize_fix_type(field_name: str) -> str:
    """Convert a field display name to a fix type key."""
    return field_name.lower().strip().replace(" ", "_")


async def record_suggestion(db: aiosqlite.Connection, fix_type: str) -> None:
    """Record that a fix was suggested (before user response)."""
    fix_type = normalize_fix_type(fix_type)
    await db.execute(
        """INSERT INTO fix_type_confidence (fix_type, total_suggestions)
           VALUES (?, 1)
           ON CONFLICT(fix_type) DO UPDATE SET
             total_suggestions = total_suggestions + 1,
             updated_at = datetime('now')""",
        (fix_type,),
    )
    await db.commit()


async def record_approval(db: aiosqlite.Connection, fix_type: str) -> None:
    """Record that a user approved a suggested fix."""
    fix_type = normalize_fix_type(fix_type)
    await db.execute(
        """INSERT INTO fix_type_confidence (fix_type, total_suggestions, total_approvals)
           VALUES (?, 1, 1)
           ON CONFLICT(fix_type) DO UPDATE SET
             total_approvals = total_approvals + 1,
             approval_rate = CAST(total_approvals + 1 AS REAL) / MAX(total_suggestions, 1),
             updated_at = datetime('now')""",
        (fix_type,),
    )
    await db.commit()
    log.info("confidence_approval_recorded", fix_type=fix_type)

    # Check for auto-promotion
    await _check_promotion(db, fix_type)


async def record_rejection(db: aiosqlite.Connection, fix_type: str) -> None:
    """Record that a user rejected a suggested fix."""
    fix_type = normalize_fix_type(fix_type)
    await db.execute(
        """INSERT INTO fix_type_confidence (fix_type, total_suggestions, total_rejections)
           VALUES (?, 1, 0)
           ON CONFLICT(fix_type) DO UPDATE SET
             total_rejections = total_rejections + 1,
             approval_rate = CAST(total_approvals AS REAL) / MAX(total_suggestions, 1),
             updated_at = datetime('now')""",
        (fix_type,),
    )
    await db.commit()
    log.info("confidence_rejection_recorded", fix_type=fix_type)

    # Check for anomaly / reversion
    await _check_reversion(db, fix_type)


async def get_confidence(db: aiosqlite.Connection, fix_type: str) -> dict | None:
    """Get confidence data for a fix type."""
    fix_type = normalize_fix_type(fix_type)
    cursor = await db.execute(
        "SELECT * FROM fix_type_confidence WHERE fix_type = ?",
        (fix_type,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_all_confidence(db: aiosqlite.Connection) -> list[dict]:
    """Get confidence data for all fix types."""
    cursor = await db.execute(
        "SELECT * FROM fix_type_confidence ORDER BY approval_rate DESC"
    )
    return [dict(r) for r in await cursor.fetchall()]


async def is_auto_fix(db: aiosqlite.Connection, fix_type: str) -> bool:
    """Check if a fix type has been auto-promoted."""
    fix_type = normalize_fix_type(fix_type)
    cursor = await db.execute(
        "SELECT status FROM fix_type_confidence WHERE fix_type = ?",
        (fix_type,),
    )
    row = await cursor.fetchone()
    return row is not None and row[0] == "auto_fix"


async def _check_promotion(db: aiosqlite.Connection, fix_type: str) -> None:
    """Check if a fix type should be auto-promoted."""
    data = await get_confidence(db, fix_type)
    if not data:
        return

    if data["status"] == "auto_fix":
        return  # already promoted

    # Check criteria
    if data["total_suggestions"] < PROMOTION_MIN_SAMPLES:
        return
    if data["approval_rate"] < PROMOTION_THRESHOLD:
        return

    # Check if rate has been sustained (created_at to now >= 14 days)
    created = data.get("created_at", "")
    if created:
        try:
            created_dt = datetime.fromisoformat(created)
            if (datetime.now(UTC) - created_dt).days < PROMOTION_MIN_DAYS:
                return
        except ValueError:
            return

    # Promote!
    await db.execute(
        """UPDATE fix_type_confidence
           SET status = 'auto_fix', promoted_at = datetime('now'), updated_at = datetime('now')
           WHERE fix_type = ?""",
        (fix_type,),
    )
    await db.commit()
    log.info(
        "fix_type_auto_promoted",
        fix_type=fix_type,
        approval_rate=data["approval_rate"],
        total_suggestions=data["total_suggestions"],
    )


async def _check_reversion(db: aiosqlite.Connection, fix_type: str) -> None:
    """Check if an auto-promoted fix type should revert to suggest+confirm."""
    data = await get_confidence(db, fix_type)
    if not data or data["status"] != "auto_fix":
        return

    if data["approval_rate"] < REVERSION_THRESHOLD:
        await db.execute(
            """UPDATE fix_type_confidence
               SET status = 'suggest', reverted_at = datetime('now'), updated_at = datetime('now')
               WHERE fix_type = ?""",
            (fix_type,),
        )
        await db.commit()
        log.warning(
            "fix_type_reverted_to_suggest",
            fix_type=fix_type,
            approval_rate=data["approval_rate"],
        )
