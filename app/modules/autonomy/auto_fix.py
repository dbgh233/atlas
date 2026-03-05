"""Auto-fix engine — applies fixes automatically for promoted fix types.

CONV-07: Auto-promotion of fix types with >90% approval rate
CONV-08: Auto-fixed issues reported in daily digest
CONV-10: User can undo auto-fixes via conversation
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import aiosqlite
import structlog

from app.core.clients.ghl import GHLClient
from app.models.database import InteractionRepository
from app.modules.audit.rules import FIELD_NAMES
from app.modules.autonomy.confidence import get_all_confidence, normalize_fix_type

log = structlog.get_logger()


async def run_auto_fixes(
    db: aiosqlite.Connection,
    ghl_client: GHLClient,
    audit_findings: list[dict],
) -> list[dict]:
    """Apply auto-fixes for promoted fix types from latest audit findings.

    Returns list of applied fixes for digest reporting.
    """
    # Get all auto-promoted fix types
    all_confidence = await get_all_confidence(db)
    auto_types = {
        c["fix_type"] for c in all_confidence if c["status"] == "auto_fix"
    }

    if not auto_types:
        return []

    applied: list[dict] = []

    for finding in audit_findings:
        if finding.get("category") != "missing_field":
            continue

        field_name = finding.get("field_name")
        if not field_name:
            continue

        fix_type = normalize_fix_type(field_name)
        if fix_type not in auto_types:
            continue

        suggested_action = finding.get("suggested_action", "")
        if not suggested_action:
            continue

        opp_id = finding.get("opp_id")
        opp_name = finding.get("opp_name", "Unknown")

        # Look up field ID
        field_id = None
        for fid, fname in FIELD_NAMES.items():
            if normalize_fix_type(fname) == fix_type:
                field_id = fid
                break

        if not field_id:
            continue

        # For auto-fix, we need a concrete value.
        # In v1, auto-fix only works for fix types where the suggested_action
        # includes a specific value. This is a safety measure.
        # Future: Claude can infer values from context.
        new_value = _extract_value_from_suggestion(suggested_action)
        if not new_value:
            log.debug(
                "auto_fix_skipped_no_value",
                opp_id=opp_id,
                field=field_name,
                suggestion=suggested_action,
            )
            continue

        try:
            # Read current value
            opp = await ghl_client.get_opportunity(opp_id)
            old_value = None
            custom_fields = opp.get("customFields")
            if isinstance(custom_fields, list):
                for cf in custom_fields:
                    if isinstance(cf, dict) and cf.get("id") == field_id:
                        old_value = cf.get("value")

            # Apply fix
            await ghl_client.update_opportunity(
                opp_id,
                {"customFields": [{"id": field_id, "field_value": new_value}]},
            )

            # Log to auto_fix_log
            await db.execute(
                """INSERT INTO auto_fix_log
                   (fix_type, opportunity_id, opportunity_name, field_id, field_name, old_value, new_value)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (fix_type, opp_id, opp_name, field_id, field_name,
                 str(old_value) if old_value else None, new_value),
            )
            await db.commit()

            # Log to interaction_log
            interaction_repo = InteractionRepository(db)
            await interaction_repo.add(
                interaction_type="auto_fix",
                user_id="atlas",
                opportunity_id=opp_id,
                field_name=field_name,
                old_value=str(old_value) if old_value else None,
                new_value=new_value,
                context=json.dumps({
                    "fix_type": fix_type,
                    "opp_name": opp_name,
                }),
            )

            applied.append({
                "opp_id": opp_id,
                "opp_name": opp_name,
                "field_name": field_name,
                "old_value": old_value,
                "new_value": new_value,
            })

            log.info(
                "auto_fix_applied",
                opp_id=opp_id,
                opp_name=opp_name,
                field=field_name,
                new_value=new_value,
            )

        except Exception as e:
            log.error(
                "auto_fix_error",
                opp_id=opp_id,
                field=field_name,
                error=str(e),
            )

    return applied


def _extract_value_from_suggestion(suggestion: str) -> str | None:
    """Try to extract a concrete value from a suggested action.

    Example: "Set Industry Type to Hemp on this opportunity" -> "Hemp"
    Returns None if no concrete value can be extracted.
    """
    # Pattern: "Set X to Y on..."
    lower = suggestion.lower()
    if " to " in lower and " on " in lower:
        start = suggestion.index(" to ") + 4
        end = suggestion.index(" on ", start) if " on " in suggestion[start:] else len(suggestion)
        value = suggestion[start:start + (end - start)].strip()
        if value and len(value) < 100:
            return value
    return None


async def undo_last_auto_fix(
    db: aiosqlite.Connection,
    ghl_client: GHLClient,
    user_id: str,
    opp_name_filter: str | None = None,
    field_filter: str | None = None,
) -> str:
    """Undo the most recent auto-fix, optionally filtered by opp name or field.

    CONV-10: User can undo auto-fixes via conversation.
    """
    query = "SELECT * FROM auto_fix_log WHERE undone = 0"
    params: list = []

    if opp_name_filter:
        query += " AND LOWER(opportunity_name) LIKE ?"
        params.append(f"%{opp_name_filter.lower()}%")
    if field_filter:
        query += " AND LOWER(field_name) LIKE ?"
        params.append(f"%{field_filter.lower()}%")

    query += " ORDER BY created_at DESC LIMIT 1"

    cursor = await db.execute(query, params)
    row = await cursor.fetchone()

    if not row:
        return "No auto-fixes found to undo."

    fix = dict(row)
    opp_id = fix["opportunity_id"]
    field_id = fix["field_id"]
    old_value = fix["old_value"]
    field_name = fix["field_name"]
    opp_name = fix.get("opportunity_name", "Unknown")

    if not old_value:
        return f"Cannot undo — no previous value recorded for {field_name} on {opp_name}."

    try:
        # Revert the field
        await ghl_client.update_opportunity(
            opp_id,
            {"customFields": [{"id": field_id, "field_value": old_value}]},
        )

        # Mark as undone
        await db.execute(
            """UPDATE auto_fix_log
               SET undone = 1, undone_at = datetime('now'), undone_by = ?
               WHERE id = ?""",
            (user_id, fix["id"]),
        )
        await db.commit()

        # Log interaction
        interaction_repo = InteractionRepository(db)
        await interaction_repo.add(
            interaction_type="auto_fix_undo",
            user_id=user_id,
            opportunity_id=opp_id,
            field_name=field_name,
            old_value=fix["new_value"],
            new_value=old_value,
            context=json.dumps({
                "auto_fix_id": fix["id"],
                "opp_name": opp_name,
            }),
        )

        log.info(
            "auto_fix_undone",
            fix_id=fix["id"],
            opp_id=opp_id,
            field=field_name,
            user=user_id,
        )

        return (
            f"Undone! Reverted {field_name} on {opp_name} "
            f"from \"{fix['new_value']}\" back to \"{old_value}\"."
        )

    except Exception as e:
        log.error("auto_fix_undo_error", fix_id=fix["id"], error=str(e))
        return f"Failed to undo: {e}"


async def get_recent_auto_fixes(
    db: aiosqlite.Connection, limit: int = 10
) -> list[dict]:
    """Get recent auto-fixes for digest reporting."""
    cursor = await db.execute(
        "SELECT * FROM auto_fix_log ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in await cursor.fetchall()]


def format_auto_fix_digest(fixes: list[dict]) -> str:
    """Format auto-fixes for the daily Slack digest.

    CONV-08: Auto-fixed issues reported in daily digest.
    """
    if not fixes:
        return ""

    lines = [f":robot_face: *Atlas auto-fixed {len(fixes)} issue(s) since last audit:*"]
    for fix in fixes:
        undone = " (undone)" if fix.get("undone") else ""
        lines.append(
            f"  • {fix.get('opportunity_name', '?')}: "
            f"Set {fix.get('field_name', '?')} to \"{fix.get('new_value', '?')}\"{undone}"
        )
    return "\n".join(lines)
