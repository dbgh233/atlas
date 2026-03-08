"""Audit issue tracking — new vs recurring tagging and trend snapshots.

Compares current audit findings with previous snapshot to determine which
issues are NEW vs STILL OPEN. Stores snapshots for week-over-week trends.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite
import structlog

from app.models.database import AuditRepository
from app.modules.audit.engine import AuditFinding, AuditResult

log = structlog.get_logger()


@dataclass
class TaggedFinding:
    """An audit finding with new/recurring status."""

    finding: AuditFinding
    tag: str  # "NEW" or "STILL OPEN (Xd)"
    days_open: int | None = None  # None for NEW findings


def _finding_key(f: AuditFinding) -> str:
    """Generate a stable key for matching findings across audits."""
    return f"{f.opp_id}:{f.category}:{f.field_name or f.description}"


async def tag_findings(
    db: aiosqlite.Connection,
    result: AuditResult,
) -> list[TaggedFinding]:
    """Compare current findings with previous audit to tag NEW vs STILL OPEN."""
    repo = AuditRepository(db)
    previous_snapshots = await repo.get_latest(limit=1)

    # Build set of previous finding keys
    previous_keys: dict[str, str] = {}  # key -> first_seen date
    if previous_snapshots:
        prev = previous_snapshots[0]
        try:
            prev_results = json.loads(prev.get("full_results", "{}"))
            prev_findings = prev_results.get("findings", [])
            prev_first_seen = prev_results.get("first_seen", {})

            for pf in prev_findings:
                key = f"{pf.get('opp_id')}:{pf.get('category')}:{pf.get('field_name') or pf.get('description')}"
                # Inherit first_seen from previous tracking, or use the previous run date
                previous_keys[key] = prev_first_seen.get(key, prev.get("run_date", ""))
        except (json.JSONDecodeError, AttributeError):
            log.warning("audit_tracker_parse_previous_failed")

    now = datetime.now(UTC)
    tagged: list[TaggedFinding] = []

    for finding in result.findings:
        key = _finding_key(finding)
        if key in previous_keys:
            # Recurring issue — calculate days open
            first_seen = previous_keys[key]
            try:
                first_dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
                days = (now - first_dt).days
            except (ValueError, TypeError):
                days = 1
            tagged.append(TaggedFinding(
                finding=finding,
                tag=f"STILL OPEN ({days}d)",
                days_open=days,
            ))
        else:
            tagged.append(TaggedFinding(
                finding=finding,
                tag="NEW",
                days_open=None,
            ))

    return tagged


async def save_snapshot(
    db: aiosqlite.Connection,
    result: AuditResult,
    tagged: list[TaggedFinding],
    run_type: str = "scheduled",
) -> int:
    """Store audit snapshot with full results and first_seen tracking."""
    repo = AuditRepository(db)

    # Build first_seen map: inherit from previous + add NEW findings
    previous_snapshots = await repo.get_latest(limit=1)
    first_seen: dict[str, str] = {}

    if previous_snapshots:
        try:
            prev_results = json.loads(previous_snapshots[0].get("full_results", "{}"))
            first_seen = prev_results.get("first_seen", {})
        except (json.JSONDecodeError, AttributeError):
            pass

    now_str = datetime.now(UTC).isoformat()
    for tf in tagged:
        key = _finding_key(tf.finding)
        if key not in first_seen:
            first_seen[key] = now_str

    # Build full results JSON
    findings_json = [
        {
            "category": f.finding.category,
            "opp_id": f.finding.opp_id,
            "opp_name": f.finding.opp_name,
            "stage": f.finding.stage,
            "assigned_to": f.finding.assigned_to,
            "description": f.finding.description,
            "field_name": f.finding.field_name,
            "suggested_action": f.finding.suggested_action,
            "severity": f.finding.severity,
            "suggested_value": f.finding.suggested_value,
            "owner_hint": f.finding.owner_hint,
            "tag": f.tag,
        }
        for f in tagged
    ]

    full_results = json.dumps({
        "findings": findings_json,
        "first_seen": first_seen,
    })

    issues_by_type = json.dumps({
        "missing_fields": len(result.missing_fields),
        "stale_deals": len(result.stale_deals),
        "overdue_tasks": sum(result.overdue_task_counts.values()) if result.overdue_task_counts else 0,
        "overdue_task_counts": result.overdue_task_counts,
        "close_lost_missing_reason": result.close_lost_missing_reason,
    })

    run_date = datetime.now(UTC).strftime("%Y-%m-%d")

    snapshot_id = await repo.add(
        run_date=run_date,
        run_type=run_type,
        total_opps=result.total_opportunities,
        total_issues=result.total_issues,
        issues_by_type=issues_by_type,
        full_results=full_results,
    )

    log.info("audit_snapshot_saved", snapshot_id=snapshot_id, run_date=run_date)
    return snapshot_id


async def get_trend_comparison(db: aiosqlite.Connection) -> dict:
    """Get week-over-week comparison from stored snapshots."""
    repo = AuditRepository(db)
    snapshots = await repo.get_latest(limit=14)  # 2 weeks of data

    if not snapshots:
        return {"available": False, "message": "No audit snapshots yet"}

    latest = snapshots[0]
    latest_issues = latest.get("total_issues", 0)
    latest_opps = latest.get("total_opportunities", 0)

    # Find snapshot from ~7 days ago
    week_ago = None
    for s in snapshots:
        try:
            s_date = datetime.strptime(s.get("run_date", ""), "%Y-%m-%d")
            l_date = datetime.strptime(latest.get("run_date", ""), "%Y-%m-%d")
            if (l_date - s_date).days >= 6:
                week_ago = s
                break
        except ValueError:
            continue

    if week_ago:
        prev_issues = week_ago.get("total_issues", 0)
        change = latest_issues - prev_issues
        direction = "down" if change < 0 else "up" if change > 0 else "flat"
        return {
            "available": True,
            "current": {
                "date": latest.get("run_date"),
                "issues": latest_issues,
                "opportunities": latest_opps,
            },
            "previous_week": {
                "date": week_ago.get("run_date"),
                "issues": prev_issues,
                "opportunities": week_ago.get("total_opportunities", 0),
            },
            "change": change,
            "direction": direction,
            "summary": f"{latest_issues} issues this week, {'down' if change < 0 else 'up'} from {prev_issues}",
        }

    return {
        "available": True,
        "current": {
            "date": latest.get("run_date"),
            "issues": latest_issues,
            "opportunities": latest_opps,
        },
        "previous_week": None,
        "summary": f"{latest_issues} issues (no previous week data for comparison)",
    }
