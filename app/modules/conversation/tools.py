"""Pipeline query tools — functions Claude can call via tool_use.

These tools give the conversational agent access to live pipeline data:
stale deals, missing fields, per-user filtering, specific opportunity lookup.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import aiosqlite
import structlog

from app.core.clients.ghl import GHLClient
from app.models.database import AuditRepository
from app.modules.audit.engine import AuditFinding, AuditResult, run_audit
from app.modules.audit.rules import FIELD_NAMES, STAGE_NAMES, USER_NAMES
from app.modules.audit.tracker import TaggedFinding, tag_findings

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Tool definitions for Claude tool_use
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "get_stale_deals",
        "description": "Get all stale deals from the latest pipeline audit. Returns deals that have been in their current stage longer than the threshold.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_filter": {
                    "type": "string",
                    "description": "Optional: filter by assigned user name (e.g. 'Henry', 'Drew'). Leave empty for all users.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_missing_fields",
        "description": "Get all opportunities with missing required fields from the latest pipeline audit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_filter": {
                    "type": "string",
                    "description": "Optional: filter by assigned user name (e.g. 'Henry', 'Drew'). Leave empty for all users.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_overdue_tasks",
        "description": "Get all overdue tasks from the latest pipeline audit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_filter": {
                    "type": "string",
                    "description": "Optional: filter by assigned user name.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_audit_summary",
        "description": "Get a summary of the latest pipeline audit including total opportunities, total issues, and breakdown by category.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_opportunity_issues",
        "description": "Get all audit issues for a specific opportunity by name (partial match supported).",
        "input_schema": {
            "type": "object",
            "properties": {
                "opp_name": {
                    "type": "string",
                    "description": "The opportunity/merchant name to look up (partial match).",
                }
            },
            "required": ["opp_name"],
        },
    },
    {
        "name": "get_system_status",
        "description": "Get Atlas system health status including last webhook, last audit, and success rate.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_trend",
        "description": "Get week-over-week audit trend comparison.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "suggest_fix",
        "description": "Suggest a field fix for an opportunity. Returns the suggestion details for user confirmation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "opp_id": {
                    "type": "string",
                    "description": "The GHL opportunity ID to fix.",
                },
                "field_name": {
                    "type": "string",
                    "description": "The display name of the field to update (e.g. 'Industry Type', 'Appointment Status').",
                },
                "new_value": {
                    "type": "string",
                    "description": "The suggested new value for the field.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this fix is being suggested.",
                },
            },
            "required": ["opp_id", "field_name", "new_value", "reason"],
        },
    },
    {
        "name": "undo_auto_fix",
        "description": "Undo the most recent auto-fix. Optionally filter by opportunity name or field name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "opp_name": {
                    "type": "string",
                    "description": "Optional: filter undo to a specific opportunity name (partial match).",
                },
                "field_name": {
                    "type": "string",
                    "description": "Optional: filter undo to a specific field name (e.g. 'Industry Type').",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_confidence_scores",
        "description": "Get confidence scores and auto-promotion status for all fix types.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

# Reverse lookup: user display name -> user ID
_NAME_TO_ID: dict[str, str] = {}
for uid, name in USER_NAMES.items():
    _NAME_TO_ID[name.lower()] = uid
    # Also index first names
    first = name.split()[0].lower()
    _NAME_TO_ID[first] = uid


def _match_user(user_filter: str | None) -> str | None:
    """Resolve a user filter string to a GHL user ID."""
    if not user_filter:
        return None
    lower = user_filter.strip().lower()
    return _NAME_TO_ID.get(lower)


def _filter_findings(
    findings: list[dict], user_id: str | None
) -> list[dict]:
    """Filter finding dicts by assigned_to if user_id given."""
    if not user_id:
        return findings
    return [f for f in findings if f.get("assigned_to") == user_id]


def _finding_to_text(f: dict) -> str:
    """Format a single finding dict as readable text."""
    tag = f.get("tag", "")
    tag_str = f" [{tag}]" if tag else ""
    action = f.get("suggested_action", "")
    action_str = f" — Suggested: {action}" if action else ""
    return (
        f"• {f.get('opp_name', '?')} ({f.get('stage', '?')}): "
        f"{f.get('description', '?')}{tag_str}{action_str}"
    )


async def _get_latest_audit_data(db: aiosqlite.Connection) -> dict | None:
    """Load the latest audit snapshot's full_results."""
    repo = AuditRepository(db)
    snapshots = await repo.get_latest(limit=1)
    if not snapshots:
        return None
    try:
        return json.loads(snapshots[0].get("full_results", "{}"))
    except (json.JSONDecodeError, AttributeError):
        return None


async def execute_tool(
    tool_name: str,
    tool_input: dict,
    ghl_client: GHLClient,
    db: aiosqlite.Connection,
) -> str:
    """Execute a pipeline query tool and return text result."""
    try:
        if tool_name == "get_stale_deals":
            return await _tool_get_stale_deals(db, tool_input)
        elif tool_name == "get_missing_fields":
            return await _tool_get_missing_fields(db, tool_input)
        elif tool_name == "get_overdue_tasks":
            return await _tool_get_overdue_tasks(db, tool_input)
        elif tool_name == "get_audit_summary":
            return await _tool_get_audit_summary(db)
        elif tool_name == "get_opportunity_issues":
            return await _tool_get_opportunity_issues(db, tool_input)
        elif tool_name == "get_system_status":
            return await _tool_get_system_status(db)
        elif tool_name == "get_trend":
            return await _tool_get_trend(db)
        elif tool_name == "suggest_fix":
            return await _tool_suggest_fix(tool_input, ghl_client)
        elif tool_name == "undo_auto_fix":
            return await _tool_undo_auto_fix(db, ghl_client, tool_input)
        elif tool_name == "get_confidence_scores":
            return await _tool_get_confidence_scores(db)
        else:
            return f"Unknown tool: {tool_name}"
    except Exception as e:
        log.error("tool_execution_error", tool=tool_name, error=str(e))
        return f"Error executing {tool_name}: {e}"


async def _tool_get_stale_deals(db: aiosqlite.Connection, inp: dict) -> str:
    data = await _get_latest_audit_data(db)
    if not data:
        return "No audit data available. Run an audit first with POST /audit/run."
    findings = [f for f in data.get("findings", []) if f.get("category") == "stale_deal"]
    user_id = _match_user(inp.get("user_filter"))
    findings = _filter_findings(findings, user_id)
    if not findings:
        user_str = f" for {inp.get('user_filter')}" if inp.get("user_filter") else ""
        return f"No stale deals found{user_str}."
    lines = [f"Found {len(findings)} stale deal(s):"]
    for f in findings:
        lines.append(_finding_to_text(f))
    return "\n".join(lines)


async def _tool_get_missing_fields(db: aiosqlite.Connection, inp: dict) -> str:
    data = await _get_latest_audit_data(db)
    if not data:
        return "No audit data available. Run an audit first."
    findings = [f for f in data.get("findings", []) if f.get("category") in ("missing_field", "contact_issue", "name_issue")]
    user_id = _match_user(inp.get("user_filter"))
    findings = _filter_findings(findings, user_id)
    if not findings:
        user_str = f" for {inp.get('user_filter')}" if inp.get("user_filter") else ""
        return f"No missing field issues found{user_str}."
    lines = [f"Found {len(findings)} missing field issue(s):"]
    for f in findings:
        lines.append(_finding_to_text(f))
    return "\n".join(lines)


async def _tool_get_overdue_tasks(db: aiosqlite.Connection, inp: dict) -> str:
    data = await _get_latest_audit_data(db)
    if not data:
        return "No audit data available. Run an audit first."
    findings = [f for f in data.get("findings", []) if f.get("category") == "overdue_task"]
    user_id = _match_user(inp.get("user_filter"))
    findings = _filter_findings(findings, user_id)
    if not findings:
        user_str = f" for {inp.get('user_filter')}" if inp.get("user_filter") else ""
        return f"No overdue tasks found{user_str}."
    lines = [f"Found {len(findings)} overdue task(s):"]
    for f in findings:
        lines.append(_finding_to_text(f))
    return "\n".join(lines)


async def _tool_get_audit_summary(db: aiosqlite.Connection) -> str:
    repo = AuditRepository(db)
    snapshots = await repo.get_latest(limit=1)
    if not snapshots:
        return "No audit data available. Run an audit first."
    s = snapshots[0]
    try:
        by_type = json.loads(s.get("issues_by_type", "{}"))
    except json.JSONDecodeError:
        by_type = {}
    return (
        f"Latest audit ({s.get('run_date', '?')}, {s.get('run_type', '?')}):\n"
        f"• Total opportunities checked: {s.get('total_opportunities', '?')}\n"
        f"• Total issues: {s.get('total_issues', '?')}\n"
        f"  - Missing fields: {by_type.get('missing_fields', '?')}\n"
        f"  - Stale deals: {by_type.get('stale_deals', '?')}\n"
        f"  - Overdue tasks: {by_type.get('overdue_tasks', '?')}"
    )


async def _tool_get_opportunity_issues(db: aiosqlite.Connection, inp: dict) -> str:
    data = await _get_latest_audit_data(db)
    if not data:
        return "No audit data available. Run an audit first."
    opp_name = inp.get("opp_name", "").lower()
    findings = [
        f for f in data.get("findings", [])
        if opp_name in f.get("opp_name", "").lower()
    ]
    if not findings:
        return f"No issues found for opportunity matching '{inp.get('opp_name')}'."
    # Get the actual opp name from first finding
    actual_name = findings[0].get("opp_name", "?")
    lines = [f"Issues for {actual_name} ({len(findings)} total):"]
    for f in findings:
        lines.append(_finding_to_text(f))
    return "\n".join(lines)


async def _tool_get_system_status(db: aiosqlite.Connection) -> str:
    repo = AuditRepository(db)
    snapshots = await repo.get_latest(limit=1)
    last_audit = "Never"
    if snapshots:
        last_audit = f"{snapshots[0].get('run_date', '?')} ({snapshots[0].get('run_type', '?')})"

    # Check DLQ for recent webhook activity
    from app.models.database import DLQRepository, IdempotencyRepository
    idem_repo = IdempotencyRepository(db)
    dlq_repo = DLQRepository(db)

    cursor = await db.execute(
        "SELECT MAX(processed_at) FROM idempotency_keys"
    )
    row = await cursor.fetchone()
    last_webhook = row[0] if row and row[0] else "No webhooks processed"

    dlq_entries = await dlq_repo.get_all(limit=5, status="pending")
    dlq_count = len(dlq_entries)

    cursor2 = await db.execute("SELECT COUNT(*) FROM idempotency_keys")
    row2 = await cursor2.fetchone()
    total_processed = row2[0] if row2 else 0

    return (
        f"Atlas System Status:\n"
        f"• Last webhook processed: {last_webhook}\n"
        f"• Last audit: {last_audit}\n"
        f"• Total webhooks processed: {total_processed}\n"
        f"• Pending DLQ entries: {dlq_count}\n"
        f"• Status: Healthy"
    )


async def _tool_get_trend(db: aiosqlite.Connection) -> str:
    from app.modules.audit.tracker import get_trend_comparison
    trend = await get_trend_comparison(db)
    if not trend.get("available"):
        return trend.get("message", "No trend data available.")
    summary = trend.get("summary", "")
    current = trend.get("current", {})
    previous = trend.get("previous_week")
    lines = [f"Audit Trend:\n{summary}"]
    lines.append(f"\nCurrent: {current.get('issues', '?')} issues across {current.get('opportunities', '?')} opportunities ({current.get('date', '?')})")
    if previous:
        lines.append(f"Previous week: {previous.get('issues', '?')} issues across {previous.get('opportunities', '?')} opportunities ({previous.get('date', '?')})")
        change = trend.get("change", 0)
        direction = "fewer" if change < 0 else "more" if change > 0 else "same"
        lines.append(f"Change: {abs(change)} {direction} issues")
    return "\n".join(lines)


async def _tool_suggest_fix(inp: dict, ghl_client: GHLClient) -> str:
    """Prepare a fix suggestion — does NOT execute, returns details for user confirmation."""
    opp_id = inp.get("opp_id", "")
    field_name = inp.get("field_name", "")
    new_value = inp.get("new_value", "")
    reason = inp.get("reason", "")

    # Verify the opportunity exists
    try:
        opp = await ghl_client.get_opportunity(opp_id)
        opp_name = opp.get("name", "Unknown")
    except Exception as e:
        return f"Could not find opportunity {opp_id}: {e}"

    # Look up field ID from display name
    field_id = None
    for fid, fname in FIELD_NAMES.items():
        if fname.lower() == field_name.lower():
            field_id = fid
            break

    if not field_id:
        return f"Unknown field name: {field_name}. Valid fields: {', '.join(FIELD_NAMES.values())}"

    return (
        f"PENDING_FIX:{opp_id}:{field_id}:{new_value}\n"
        f"Suggested fix for {opp_name}:\n"
        f"• Field: {field_name}\n"
        f"• New value: {new_value}\n"
        f"• Reason: {reason}\n\n"
        f"Reply 'yes' to apply this fix, or 'no' to skip."
    )


async def _tool_undo_auto_fix(db: aiosqlite.Connection, ghl_client: GHLClient, inp: dict) -> str:
    from app.modules.autonomy.auto_fix import undo_last_auto_fix
    return await undo_last_auto_fix(
        db=db,
        ghl_client=ghl_client,
        user_id="conversation",
        opp_name_filter=inp.get("opp_name"),
        field_filter=inp.get("field_name"),
    )


async def _tool_get_confidence_scores(db: aiosqlite.Connection) -> str:
    from app.modules.autonomy.confidence import get_all_confidence
    scores = await get_all_confidence(db)
    if not scores:
        return "No confidence data yet. Fix types will accumulate scores as users approve or reject suggestions."
    lines = ["Fix Type Confidence Scores:"]
    for s in scores:
        status = s.get("status", "suggest")
        rate = s.get("approval_rate", 0)
        total = s.get("total_suggestions", 0)
        approvals = s.get("total_approvals", 0)
        rejections = s.get("total_rejections", 0)
        status_str = "AUTO-FIX" if status == "auto_fix" else "suggest"
        lines.append(
            f"• {s.get('fix_type', '?')}: {rate:.0%} approval "
            f"({approvals}/{total} approved, {rejections} rejected) [{status_str}]"
        )
    return "\n".join(lines)
