"""Pattern detection engine — surfaces recurring themes and accountability gaps.

Runs during daily audit to detect:
1. Recurring topics — same merchant discussed in 3+ consecutive triage meetings
   with no stage movement
2. Commitment misses — commitments not fulfilled by deadline
3. Agenda gaps — active pipeline deals behind SLA not discussed in recent triage
4. Stall patterns — deals stuck at same stage for extended periods
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import aiosqlite
import structlog

from app.core.clients.ghl import GHLClient
from app.modules.audit.rules import (
    STAGE_NAMES,
    STALE_THRESHOLDS,
    SKIP_STAGES,
    USER_NAMES,
    SLACK_USER_IDS,
    SKIP_OPP_NAMES,
)
from app.modules.meetings.repository import (
    CommitmentRepository,
    MeetingRepository,
    PatternRepository,
)

log = structlog.get_logger()


async def detect_patterns(
    db: aiosqlite.Connection,
    ghl_client: GHLClient,
) -> dict:
    """Run all pattern detections. Returns summary for digest.

    Called during daily audit flow.
    """
    results = {
        "recurring_topics": [],
        "missed_commitments": [],
        "agenda_gaps": [],
        "patterns_stored": 0,
    }

    try:
        missed = await _check_missed_commitments(db)
        results["missed_commitments"] = missed
    except Exception as e:
        log.error("pattern_missed_commitments_error", error=str(e))

    try:
        recurring = await _detect_recurring_topics(db)
        results["recurring_topics"] = recurring
    except Exception as e:
        log.error("pattern_recurring_topics_error", error=str(e))

    try:
        gaps = await _detect_agenda_gaps(db, ghl_client)
        results["agenda_gaps"] = gaps
    except Exception as e:
        log.error("pattern_agenda_gaps_error", error=str(e))

    # Store patterns
    pattern_repo = PatternRepository(db)
    stored = 0

    for topic in results["recurring_topics"]:
        await pattern_repo.upsert(
            pattern_type="recurring_topic",
            pattern_key=f"recurring:{topic['merchant_name']}",
            description=(
                f"{topic['merchant_name']} discussed in {topic['meeting_count']} "
                f"consecutive meetings with no stage movement"
            ),
            evidence=topic.get("meetings", []),
            entity_type="opportunity",
            entity_id=topic.get("opp_id"),
            confidence=min(0.3 * topic["meeting_count"], 1.0),
            actionable=topic["meeting_count"] >= 3,
        )
        stored += 1

    for gap in results["agenda_gaps"]:
        await pattern_repo.upsert(
            pattern_type="agenda_gap",
            pattern_key=f"agendagap:{gap['opp_id']}:{gap.get('last_triage_date', 'none')}",
            description=(
                f"{gap['opp_name']} is {gap['days_in_stage']}d in "
                f"{gap['stage_name']} but was not discussed in recent triage"
            ),
            evidence=[gap],
            entity_type="opportunity",
            entity_id=gap["opp_id"],
            confidence=0.8,
            actionable=True,
        )
        stored += 1

    results["patterns_stored"] = stored
    log.info(
        "patterns_detected",
        recurring=len(results["recurring_topics"]),
        missed=len(results["missed_commitments"]),
        gaps=len(results["agenda_gaps"]),
        stored=stored,
    )
    return results


async def _check_missed_commitments(db: aiosqlite.Connection) -> list[dict]:
    """Find commitments past their deadline that are still open."""
    repo = CommitmentRepository(db)
    missed = await repo.get_missed()
    return [
        {
            "assignee_name": c.get("assignee_name"),
            "assignee_ghl_id": c.get("assignee_ghl_id"),
            "action": c.get("action"),
            "merchant_name": c.get("merchant_name"),
            "deadline": c.get("deadline"),
            "meeting_title": c.get("meeting_title"),
            "meeting_date": c.get("meeting_date"),
            "commitment_id": c.get("id"),
        }
        for c in missed
    ]


async def _detect_recurring_topics(db: aiosqlite.Connection) -> list[dict]:
    """Find merchants discussed in 3+ recent triage meetings without progress."""
    meeting_repo = MeetingRepository(db)
    recent_meetings = await meeting_repo.get_recent(limit=10)

    # Only look at triage meetings
    triage_meetings = [
        m for m in recent_meetings
        if m.get("meeting_type") == "pipeline_triage"
    ]

    if len(triage_meetings) < 2:
        return []

    # Count how many consecutive meetings each merchant appears in
    merchant_counts: dict[str, list[dict]] = defaultdict(list)
    for meeting in triage_meetings:
        merchants_json = meeting.get("merchants_mentioned")
        if not merchants_json:
            continue
        try:
            merchants = json.loads(merchants_json) if isinstance(merchants_json, str) else merchants_json
        except (json.JSONDecodeError, TypeError):
            continue

        for merchant in merchants:
            merchant_counts[merchant.lower()].append({
                "meeting_id": meeting.get("id"),
                "title": meeting.get("title"),
                "date": meeting.get("start_time"),
            })

    recurring = []
    for merchant, meetings in merchant_counts.items():
        if len(meetings) >= 3:
            recurring.append({
                "merchant_name": merchant,
                "meeting_count": len(meetings),
                "meetings": meetings,
                "opp_id": None,  # Could be enriched with GHL lookup
            })

    return recurring


async def _detect_agenda_gaps(
    db: aiosqlite.Connection,
    ghl_client: GHLClient,
) -> list[dict]:
    """Find active deals behind SLA that weren't discussed in recent triage."""
    meeting_repo = MeetingRepository(db)
    recent_meetings = await meeting_repo.get_recent(limit=5)

    # Get merchants discussed in recent triage meetings
    discussed_merchants: set[str] = set()
    last_triage_date = None
    for m in recent_meetings:
        if m.get("meeting_type") != "pipeline_triage":
            continue
        if not last_triage_date:
            last_triage_date = m.get("start_time")
        merchants_json = m.get("merchants_mentioned")
        if merchants_json:
            try:
                merchants = json.loads(merchants_json) if isinstance(merchants_json, str) else merchants_json
                discussed_merchants.update(name.lower() for name in merchants)
            except (json.JSONDecodeError, TypeError):
                pass

    if not discussed_merchants and not last_triage_date:
        return []  # No triage meetings yet — can't detect gaps

    # Get all active opps and find ones behind SLA but not discussed
    try:
        all_opps = await ghl_client.search_opportunities()
    except Exception:
        return []

    now = datetime.now(UTC)
    gaps: list[dict] = []

    for opp in all_opps:
        opp_name = opp.get("name", "")
        if opp_name in SKIP_OPP_NAMES:
            continue

        stage_id = opp.get("pipelineStageId", "")
        if stage_id in SKIP_STAGES:
            continue

        # Check if behind SLA
        threshold = STALE_THRESHOLDS.get(stage_id)
        if not threshold:
            continue

        last_activity = opp.get("updatedAt") or opp.get("createdAt", "")
        if not last_activity:
            continue

        try:
            last_dt = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
            days_in_stage = (now - last_dt).days
        except (ValueError, TypeError):
            continue

        if days_in_stage < threshold:
            continue  # Not behind SLA

        # Behind SLA — was it discussed?
        opp_name_lower = opp_name.lower().strip()
        if opp_name_lower in discussed_merchants:
            continue  # Discussed — no gap

        # Also check partial name matches
        was_discussed = any(
            opp_name_lower in m or m in opp_name_lower
            for m in discussed_merchants
        )
        if was_discussed:
            continue

        stage_name = STAGE_NAMES.get(stage_id, stage_id)
        assigned = opp.get("assignedTo") or "Unassigned"

        gaps.append({
            "opp_id": opp.get("id", ""),
            "opp_name": opp_name,
            "stage_name": stage_name,
            "days_in_stage": days_in_stage,
            "assigned_to": assigned,
            "assigned_name": USER_NAMES.get(assigned, assigned),
            "last_triage_date": last_triage_date,
        })

    # Sort by days in stage descending (worst first)
    gaps.sort(key=lambda g: g["days_in_stage"], reverse=True)
    return gaps[:10]  # Cap at 10 to avoid noise


def format_pattern_digest(patterns: dict) -> str:
    """Format pattern detection results for the Slack digest."""
    lines: list[str] = []

    gaps = patterns.get("agenda_gaps", [])
    if gaps:
        lines.append(
            f":eyes: *{len(gaps)} deal(s) behind SLA not discussed in triage:*"
        )
        for g in gaps[:5]:
            mention = ""
            ghl_id = g.get("assigned_to")
            if ghl_id and ghl_id != "Unassigned":
                slack_id = SLACK_USER_IDS.get(ghl_id)
                if slack_id:
                    mention = f" (<@{slack_id}>)"
            lines.append(
                f"  :warning: {g['opp_name']} -- {g['days_in_stage']}d "
                f"in {g['stage_name']}{mention}"
            )

    recurring = patterns.get("recurring_topics", [])
    if recurring:
        for r in recurring:
            lines.append(
                f":repeat: {r['merchant_name']} discussed {r['meeting_count']}x "
                f"in a row with no movement"
            )

    return "\n".join(lines)
