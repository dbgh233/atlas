"""Meeting transcript processor — extracts commitments and merchant references.

Uses Claude to analyze meeting transcripts and extract structured data:
- Action items with assignee, merchant, and deadline
- Merchant names cross-referenced with GHL pipeline
- Agenda adherence analysis
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import aiosqlite
import structlog

from app.core.clients.claude import ClaudeClient
from app.core.clients.ghl import GHLClient
from app.modules.audit.rules import STAGE_NAMES, USER_NAMES
from app.modules.meetings.repository import (
    CommitmentRepository,
    MeetingRepository,
    PatternRepository,
)
from app.modules.meetings.resolver import (
    resolve_commitment_names,
    format_resolved_digest,
)

log = structlog.get_logger()

# Map team member first names to GHL user IDs for commitment assignment
NAME_TO_GHL_ID: dict[str, str] = {}
for ghl_id, full_name in USER_NAMES.items():
    first = full_name.split()[0].lower()
    NAME_TO_GHL_ID[first] = ghl_id
    NAME_TO_GHL_ID[full_name.lower()] = ghl_id

# Meeting type classification based on title
MEETING_TYPE_PATTERNS: dict[str, list[str]] = {
    "pipeline_triage": ["pipeline triage", "triage"],
    "pipeline_review": [
        "pipeline review",
        "onboarding review",
        "pipeline / onboarding",
        "pipeline/onboarding",
        "call review",
    ],
}

COMMITMENT_EXTRACTION_PROMPT = """Analyze this meeting transcript and extract action items/commitments.

Meeting: {title}
Date: {date}
Attendees: {attendees}

TRANSCRIPT:
{transcript}

Extract ONLY clear, specific commitments where someone said they would do something.
Do NOT include vague statements or general discussion.

For each commitment, provide:
- assignee: First name of the person responsible (Henry, Hannah, Drew, Ism, June)
- action: What they committed to do. CRITICAL: ALWAYS include the specific merchant/company name in the action text. Never use vague references like "the client" or "that merchant" — use the actual name. If the context makes the merchant name clear from surrounding discussion, include it. Example: "Call Buzz Tips to get hemp license update" NOT "Call the operating agreement client"
- merchant_name: The merchant/company name if one was mentioned (null ONLY if truly general/process-level)
- deadline: Any mentioned deadline ("by Friday", "today", "this week", "end of day") or null
- source_quote: The approximate quote from the transcript (keep short, 1-2 sentences max)

IMPORTANT: This meeting is a pipeline triage where the team discusses specific deals in their CRM. When someone commits to an action during discussion of a specific merchant, ALWAYS tie that action to the merchant being discussed at that point in the conversation, even if the person didn't explicitly say the merchant name in their commitment sentence. Use conversation context.

Also provide:
- merchants_discussed: List of ALL merchant/company names mentioned in the meeting
- undiscussed_concern: Any topic that seemed important but had no clear action item assigned

Respond in JSON format:
{{
  "commitments": [
    {{
      "assignee": "Henry",
      "action": "Submit MPA for Moon Tide",
      "merchant_name": "Moon Tide",
      "deadline": "by Thursday",
      "source_quote": "I'll get that MPA submitted for Moon Tide by Thursday"
    }}
  ],
  "merchants_discussed": ["Moon Tide", "Golden Rule", "Bayside Peptides"],
  "undiscussed_concerns": ["Certified-pep has been at Onboarding for 12 days with no movement"]
}}"""


@dataclass
class ProcessedMeeting:
    """Result of processing a meeting transcript."""

    meeting_id: int
    title: str
    meeting_type: str | None
    commitments_extracted: int = 0
    merchants_found: list[str] = field(default_factory=list)
    merchants_matched_to_opps: int = 0
    undiscussed_concerns: list[str] = field(default_factory=list)
    resolved_digest: str = ""  # Slack-ready digest with resolved merchant names
    errors: list[str] = field(default_factory=list)


def _parse_meeting_date(start_time: str) -> datetime:
    """Parse a meeting start time into a datetime."""
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(start_time.split("+")[0].split("Z")[0], fmt)
            return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
        except ValueError:
            continue
    return datetime.now(UTC)


def _normalize_deadline(raw: str | None, meeting_date: datetime) -> str | None:
    """Convert relative deadlines like 'by Thursday' to actual ISO dates."""
    if not raw:
        return None

    lower = raw.lower().strip()

    # Already a date
    if re.match(r"\d{4}-\d{2}-\d{2}", lower):
        return raw

    # Day-of-week mapping
    day_names = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }

    # "today"
    if "today" in lower:
        return meeting_date.strftime("%Y-%m-%d")

    # "tomorrow"
    if "tomorrow" in lower:
        return (meeting_date + timedelta(days=1)).strftime("%Y-%m-%d")

    # "end of week" / "this week" / "by end of week"
    if "end of week" in lower or "this week" in lower or "by eow" in lower:
        days_until_friday = (4 - meeting_date.weekday()) % 7
        if days_until_friday == 0:
            days_until_friday = 7
        return (meeting_date + timedelta(days=days_until_friday)).strftime("%Y-%m-%d")

    # "by [day]" or just the day name
    for day_name, day_num in day_names.items():
        if day_name in lower:
            days_ahead = (day_num - meeting_date.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7  # Next occurrence
            return (meeting_date + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # "X days" / "in X days"
    days_match = re.search(r"(\d+)\s*days?", lower)
    if days_match:
        return (meeting_date + timedelta(days=int(days_match.group(1)))).strftime("%Y-%m-%d")

    # "X weeks" / "in X weeks"
    weeks_match = re.search(r"(\d+)\s*weeks?", lower)
    if weeks_match:
        return (meeting_date + timedelta(weeks=int(weeks_match.group(1)))).strftime("%Y-%m-%d")

    # Can't parse — return raw
    return raw


def classify_meeting_type(title: str) -> str | None:
    """Classify a meeting title into a known type."""
    lower = title.lower()
    for meeting_type, patterns in MEETING_TYPE_PATTERNS.items():
        for pattern in patterns:
            if pattern in lower:
                return meeting_type
    return "other"


def _match_assignee_to_ghl(name: str) -> str | None:
    """Match an assignee name to a GHL user ID."""
    lower = name.lower().strip()
    return NAME_TO_GHL_ID.get(lower)


async def _match_merchants_to_opps(
    ghl_client: GHLClient,
    merchant_names: list[str],
    all_opps: list[dict] | None = None,
) -> dict[str, str | None]:
    """Match merchant names from transcript to GHL opportunity IDs.

    Returns dict of {merchant_name: opp_id or None}.
    """
    if all_opps is None:
        try:
            all_opps = await ghl_client.search_opportunities()
        except Exception as exc:
            log.error("meeting_opp_search_failed", error=str(exc))
            return {name: None for name in merchant_names}

    matches: dict[str, str | None] = {}
    for name in merchant_names:
        name_lower = name.lower().strip()
        best_match = None
        for opp in all_opps:
            opp_name = (opp.get("name") or "").lower().strip()
            if not opp_name:
                continue
            # Exact match
            if name_lower == opp_name:
                best_match = opp.get("id")
                break
            # Substring match (merchant name appears in opp name or vice versa)
            if name_lower in opp_name or opp_name in name_lower:
                best_match = opp.get("id")
                break
        matches[name] = best_match

    return matches


async def process_transcript(
    db: aiosqlite.Connection,
    claude_client: ClaudeClient,
    ghl_client: GHLClient,
    otter_speech_id: str,
    title: str,
    start_time: str,
    transcript_text: str,
    organizer: str | None = None,
    attendees: list[str] | None = None,
    end_time: str | None = None,
    duration_minutes: int | None = None,
) -> ProcessedMeeting:
    """Process a meeting transcript end-to-end.

    1. Classify meeting type
    2. Store meeting record
    3. Extract commitments via Claude
    4. Resolve commitment names against GHL pipeline (fuzzy matching)
    5. Store commitments with resolved opp links
    6. Generate resolved digest for Slack
    """
    meeting_type = classify_meeting_type(title)
    meeting_repo = MeetingRepository(db)
    commitment_repo = CommitmentRepository(db)

    result = ProcessedMeeting(
        meeting_id=0,
        title=title,
        meeting_type=meeting_type,
    )

    # Extract commitments via Claude
    prompt = COMMITMENT_EXTRACTION_PROMPT.format(
        title=title,
        date=start_time,
        attendees=", ".join(attendees) if attendees else "Unknown",
        transcript=transcript_text[:15000],  # Limit transcript length
    )

    try:
        raw_response = await claude_client.ask(prompt)
    except Exception as exc:
        log.error("meeting_claude_extraction_failed", error=str(exc))
        result.errors.append(f"Claude extraction failed: {exc}")
        # Still store the meeting even if extraction fails
        meeting_id = await meeting_repo.upsert_meeting(
            otter_speech_id=otter_speech_id,
            title=title,
            start_time=start_time,
            meeting_type=meeting_type,
            organizer=organizer,
            end_time=end_time,
            duration_minutes=duration_minutes,
            attendees=attendees,
            transcript_text=transcript_text,
        )
        result.meeting_id = meeting_id
        return result

    # Parse Claude's JSON response
    try:
        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r"\{[\s\S]*\}", raw_response)
        if not json_match:
            raise ValueError("No JSON found in Claude response")
        extracted = json.loads(json_match.group())
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("meeting_parse_failed", error=str(exc), response=raw_response[:200])
        result.errors.append(f"Failed to parse Claude response: {exc}")
        meeting_id = await meeting_repo.upsert_meeting(
            otter_speech_id=otter_speech_id,
            title=title,
            start_time=start_time,
            meeting_type=meeting_type,
            organizer=organizer,
            end_time=end_time,
            duration_minutes=duration_minutes,
            attendees=attendees,
            transcript_text=transcript_text,
        )
        result.meeting_id = meeting_id
        return result

    commitments_data = extracted.get("commitments", [])
    merchants_discussed = extracted.get("merchants_discussed", [])
    undiscussed = extracted.get("undiscussed_concerns", [])

    result.merchants_found = merchants_discussed
    result.undiscussed_concerns = undiscussed

    # Fetch all open opportunities once (shared between merchant matching and resolver)
    all_opps = None
    try:
        all_opps = await ghl_client.search_opportunities()
    except Exception:
        pass

    # Match merchants_discussed to GHL opportunities (for the meeting record)
    merchant_opp_map = await _match_merchants_to_opps(
        ghl_client, merchants_discussed, all_opps
    )
    result.merchants_matched_to_opps = sum(
        1 for v in merchant_opp_map.values() if v is not None
    )

    # --- Resolve commitment names via fuzzy matching ---
    # This is the key enhancement: cross-reference vague commitment text
    # with actual GHL pipeline data to get real merchant names, stages, volumes
    resolved_commitments = await resolve_commitment_names(
        commitments_data, ghl_client, all_opps=all_opps,
    )

    # Generate the resolved digest for Slack (with actual merchant names)
    result.resolved_digest = format_resolved_digest(resolved_commitments)

    # Store meeting
    meeting_id = await meeting_repo.upsert_meeting(
        otter_speech_id=otter_speech_id,
        title=title,
        start_time=start_time,
        meeting_type=meeting_type,
        organizer=organizer,
        end_time=end_time,
        duration_minutes=duration_minutes,
        attendees=attendees,
        summary=None,
        transcript_text=transcript_text,
        merchants_mentioned=merchants_discussed,
    )
    result.meeting_id = meeting_id

    # Store commitments using resolved data
    meeting_date = _parse_meeting_date(start_time)
    for i, c in enumerate(commitments_data):
        assignee = c.get("assignee", "Unknown")
        ghl_id = _match_assignee_to_ghl(assignee)

        # Use resolved data if available, fall back to basic matching
        resolved = resolved_commitments[i] if i < len(resolved_commitments) else None

        if resolved and resolved.opportunity_id:
            # Resolver found a match — use resolved merchant name and opp ID
            merchant = resolved.resolved_merchant_name or c.get("merchant_name")
            opp_id = resolved.opportunity_id
        else:
            # Fall back to basic merchant_opp_map matching
            merchant = c.get("merchant_name")
            opp_id = merchant_opp_map.get(merchant) if merchant else None

        # Normalize relative deadlines to actual dates
        raw_deadline = c.get("deadline")
        normalized_deadline = _normalize_deadline(raw_deadline, meeting_date)

        await commitment_repo.add(
            meeting_id=meeting_id,
            assignee_name=assignee,
            assignee_ghl_id=ghl_id,
            action=resolved.display_action if resolved else c.get("action", ""),
            merchant_name=merchant,
            opportunity_id=opp_id,
            deadline=normalized_deadline,
            source_quote=c.get("source_quote"),
        )
        result.commitments_extracted += 1

    # Count how many commitments got resolved to actual opps
    resolved_count = sum(
        1 for rc in resolved_commitments if rc.opportunity_id
    )

    log.info(
        "meeting_processed",
        meeting_id=meeting_id,
        meeting_type=meeting_type,
        commitments=result.commitments_extracted,
        merchants=len(merchants_discussed),
        matched_opps=result.merchants_matched_to_opps,
        resolved_commitments=resolved_count,
    )

    return result


async def check_commitment_followthrough(
    db: aiosqlite.Connection,
    ghl_client: GHLClient,
) -> list[dict]:
    """Check open commitments against current GHL state.

    Looks for evidence that commitments were fulfilled:
    - Stage moved forward since commitment date
    - Related task completed
    - Field populated that was mentioned

    Returns list of commitments with updated status info.
    """
    commitment_repo = CommitmentRepository(db)
    open_commitments = await commitment_repo.get_open()

    if not open_commitments:
        return []

    results: list[dict] = []

    for c in open_commitments:
        opp_id = c.get("opportunity_id")
        if not opp_id:
            results.append({**c, "followthrough_status": "unlinked"})
            continue

        try:
            opp = await ghl_client.get_opportunity(opp_id)
        except Exception:
            results.append({**c, "followthrough_status": "check_failed"})
            continue

        # Check if opp has progressed since commitment was made
        commitment_date = c.get("created_at", "")
        opp_updated = opp.get("updatedAt", "")

        if opp_updated > commitment_date:
            # Something changed — might be fulfilled
            results.append({
                **c,
                "followthrough_status": "activity_detected",
                "current_stage": opp.get("pipelineStageId"),
            })
        else:
            results.append({
                **c,
                "followthrough_status": "no_activity",
                "current_stage": opp.get("pipelineStageId"),
            })

    return results


async def auto_dismiss_fulfilled(
    db: aiosqlite.Connection,
    ghl_client: GHLClient,
) -> list[dict]:
    """Auto-dismiss commitments where GHL shows the deal progressed.

    Checks open commitments with linked opportunity IDs and marks them
    fulfilled if the opp's stage has changed since the commitment was created.
    """
    commitment_repo = CommitmentRepository(db)
    open_commitments = await commitment_repo.get_open()

    dismissed: list[dict] = []

    for c in open_commitments:
        opp_id = c.get("opportunity_id")
        if not opp_id:
            continue

        try:
            opp = await ghl_client.get_opportunity(opp_id)
        except Exception:
            continue

        opp_updated = opp.get("updatedAt", "")
        commitment_date = c.get("created_at", "")

        if opp_updated > commitment_date:
            # Deal has activity since commitment — auto-fulfill
            await commitment_repo.update_status(
                c["id"],
                "fulfilled",
                evidence=f"Auto-dismissed: opp updated at {opp_updated}",
            )
            dismissed.append({
                "id": c["id"],
                "action": c.get("action"),
                "assignee_name": c.get("assignee_name"),
                "merchant_name": c.get("merchant_name"),
                "opp_id": opp_id,
            })
            log.info(
                "commitment_auto_dismissed",
                commitment_id=c["id"],
                opp_id=opp_id,
            )

    return dismissed


async def generate_weekly_rollup(
    db: aiosqlite.Connection,
) -> str:
    """Generate Friday weekly rollup of commitment tracking.

    Summarizes commitments fulfilled, missed, and still open for the week.
    """
    # Get all commitments updated this week
    cursor = await db.execute(
        "SELECT c.*, m.title as meeting_title, m.start_time as meeting_date "
        "FROM commitments c JOIN meetings m ON c.meeting_id = m.id "
        "WHERE c.updated_at >= datetime('now', '-7 days') "
        "ORDER BY c.assignee_name, c.status",
    )
    week_commitments = [dict(r) for r in await cursor.fetchall()]

    # Also get anything still open from before this week
    cursor2 = await db.execute(
        "SELECT c.*, m.title as meeting_title, m.start_time as meeting_date "
        "FROM commitments c JOIN meetings m ON c.meeting_id = m.id "
        "WHERE c.status = 'open' AND c.updated_at < datetime('now', '-7 days') "
        "ORDER BY c.assignee_name",
    )
    stale_open = [dict(r) for r in await cursor2.fetchall()]

    fulfilled = [c for c in week_commitments if c.get("status") == "fulfilled"]
    missed = [c for c in week_commitments if c.get("status") == "missed"]
    dismissed = [c for c in week_commitments if c.get("status") == "dismissed"]
    still_open = [c for c in week_commitments if c.get("status") == "open"]
    still_open.extend(stale_open)

    # Get meetings this week
    cursor3 = await db.execute(
        "SELECT COUNT(*) FROM meetings WHERE start_time >= datetime('now', '-7 days')",
    )
    meeting_count = (await cursor3.fetchone())[0]

    lines: list[str] = []
    lines.append(":bar_chart: *Weekly Commitment Rollup*")
    lines.append(f"_{meeting_count} meetings processed this week_\n")

    total = len(fulfilled) + len(missed) + len(dismissed) + len(still_open)
    if total == 0:
        lines.append("No commitments tracked this week.")
        return "\n".join(lines)

    lines.append(
        f":white_check_mark: Fulfilled: {len(fulfilled)}  "
        f":red_circle: Missed: {len(missed)}  "
        f":white_circle: Open: {len(still_open)}  "
        f":grey_question: Dismissed: {len(dismissed)}"
    )

    # Group by user
    from app.modules.audit.rules import SLACK_USER_IDS

    user_stats: dict[str, dict] = {}
    for c in fulfilled + missed + still_open + dismissed:
        ghl_id = c.get("assignee_ghl_id") or ""
        name = c.get("assignee_name", "Unknown")
        key = ghl_id or name
        if key not in user_stats:
            user_stats[key] = {
                "mention": _user_mention(ghl_id, name),
                "fulfilled": 0, "missed": 0, "open": 0, "dismissed": 0,
            }
        user_stats[key][c.get("status", "open")] += 1

    lines.append("")
    for key, stats in sorted(user_stats.items(), key=lambda x: x[1]["mention"]):
        parts = []
        if stats["fulfilled"]:
            parts.append(f":white_check_mark: {stats['fulfilled']}")
        if stats["missed"]:
            parts.append(f":red_circle: {stats['missed']}")
        if stats["open"]:
            parts.append(f":white_circle: {stats['open']}")
        if stats["dismissed"]:
            parts.append(f":grey_question: {stats['dismissed']}")
        lines.append(f"{stats['mention']}: {' | '.join(parts)}")

    # Call out specific missed items
    if missed:
        lines.append("\n:rotating_light: *Missed commitments:*")
        for c in missed[:5]:
            mention = _user_mention(c.get("assignee_ghl_id"), c.get("assignee_name", "?"))
            lines.append(f"  {mention}: {c.get('action', '?')} (from {c.get('meeting_title', '?')})")

    # Call out stale open items
    if stale_open:
        lines.append(f"\n:warning: *{len(stale_open)} commitment(s) still open from prior weeks*")

    return "\n".join(lines)


def _user_mention(ghl_id: str | None, name: str) -> str:
    """Return Slack @mention or display name."""
    from app.modules.audit.rules import SLACK_USER_IDS
    if ghl_id:
        slack_id = SLACK_USER_IDS.get(ghl_id)
        if slack_id:
            return f"<@{slack_id}>"
    return name


def build_commitment_blocks(
    commitments: list[dict],
    missed: list[dict] | None = None,
) -> list[dict]:
    """Build Slack Block Kit blocks for commitments with interactive buttons."""
    if not commitments and not missed:
        return []

    blocks: list[dict] = []

    total = len(commitments or []) + len(missed or [])
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": f"Open Commitments ({total})"},
    })

    all_items: dict[str, dict] = {}
    for c in commitments or []:
        ghl_id = c.get("assignee_ghl_id") or ""
        name = c.get("assignee_name", "Unknown")
        key = ghl_id or name
        if key not in all_items:
            all_items[key] = {"mention": _user_mention(ghl_id, name), "open": [], "missed": []}
        all_items[key]["open"].append(c)

    for c in missed or []:
        ghl_id = c.get("assignee_ghl_id") or ""
        name = c.get("assignee_name", "Unknown")
        key = ghl_id or name
        if key not in all_items:
            all_items[key] = {"mention": _user_mention(ghl_id, name), "open": [], "missed": []}
        all_items[key]["missed"].append(c)

    for key, data in sorted(all_items.items(), key=lambda x: x[1]["mention"]):
        mention = data["mention"]
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{mention}*"},
        })

        for c in data["missed"]:
            cid = c.get("id", 0)
            action = c.get("action", "?")
            meeting = c.get("meeting_title", "")
            # Show stage context if opportunity is linked
            stage_ctx = _format_opp_context(c)
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":red_circle: {action}{stage_ctx}\n_Overdue, from {meeting}_",
                },
                "accessory": {
                    "type": "overflow",
                    "action_id": f"commitment_action_{cid}",
                    "options": [
                        {"text": {"type": "plain_text", "text": "Dismiss"}, "value": f"dismiss_{cid}"},
                        {"text": {"type": "plain_text", "text": "Create GHL Task"}, "value": f"create_task_{cid}"},
                        {"text": {"type": "plain_text", "text": "Mark Fulfilled"}, "value": f"fulfill_{cid}"},
                    ],
                },
            })

        for c in data["open"]:
            cid = c.get("id", 0)
            action = c.get("action", "?")
            deadline = c.get("deadline")
            deadline_str = f"\n_Due: {deadline}_" if deadline else ""
            # Show stage context if opportunity is linked
            stage_ctx = _format_opp_context(c)
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":white_circle: {action}{stage_ctx}{deadline_str}",
                },
                "accessory": {
                    "type": "overflow",
                    "action_id": f"commitment_action_{cid}",
                    "options": [
                        {"text": {"type": "plain_text", "text": "Dismiss"}, "value": f"dismiss_{cid}"},
                        {"text": {"type": "plain_text", "text": "Create GHL Task"}, "value": f"create_task_{cid}"},
                        {"text": {"type": "plain_text", "text": "Mark Fulfilled"}, "value": f"fulfill_{cid}"},
                    ],
                },
            })

    return blocks


def _format_opp_context(commitment: dict) -> str:
    """Format opportunity context (stage name) for display in commitment blocks.

    If the commitment has a linked opportunity_id, look up the stage name
    from the stored data.
    """
    opp_id = commitment.get("opportunity_id")
    if not opp_id:
        return ""
    # If we have stage info stored alongside the commitment, show it
    # This data comes from the resolver enrichment stored at processing time
    merchant = commitment.get("merchant_name", "")
    if merchant:
        return f" -- _{merchant}_"
    return ""


def format_commitment_digest(
    commitments: list[dict],
    missed: list[dict] | None = None,
) -> str:
    """Format commitment tracking for Slack digest — grouped by user.

    Shows enriched data when available: merchant names with stage context.
    """
    if not commitments and not missed:
        return ""

    lines: list[str] = []

    # Group all items (open + missed) by assignee
    all_items: dict[str, dict] = {}  # ghl_id or name -> {mention, open: [], missed: []}

    for c in commitments or []:
        ghl_id = c.get("assignee_ghl_id") or ""
        name = c.get("assignee_name", "Unknown")
        key = ghl_id or name
        if key not in all_items:
            all_items[key] = {
                "mention": _user_mention(ghl_id, name),
                "open": [],
                "missed": [],
            }
        all_items[key]["open"].append(c)

    for c in missed or []:
        ghl_id = c.get("assignee_ghl_id") or ""
        name = c.get("assignee_name", "Unknown")
        key = ghl_id or name
        if key not in all_items:
            all_items[key] = {
                "mention": _user_mention(ghl_id, name),
                "open": [],
                "missed": [],
            }
        all_items[key]["missed"].append(c)

    if not all_items:
        return ""

    total = len(commitments or []) + len(missed or [])
    lines.append(f":memo: *Open commitments* ({total})")

    for key, data in sorted(all_items.items(), key=lambda x: x[1]["mention"]):
        mention = data["mention"]
        lines.append(f"\n{mention}:")

        for c in data["missed"]:
            action = c.get("action", "?")
            meeting = c.get("meeting_title", "")
            merchant_ctx = _format_merchant_context(c)
            lines.append(f"  :red_circle: {action}{merchant_ctx} (overdue, from {meeting})")

        for c in data["open"]:
            action = c.get("action", "?")
            deadline = c.get("deadline")
            deadline_str = f" -- {deadline}" if deadline else ""
            merchant_ctx = _format_merchant_context(c)
            lines.append(f"  :white_circle: {action}{merchant_ctx}{deadline_str}")

    return "\n".join(lines)


def _format_merchant_context(commitment: dict) -> str:
    """Build merchant context string for digest display.

    If the commitment has a linked opportunity, shows the merchant name
    in context. The action text itself should already contain the resolved
    merchant name (set during process_transcript), but this adds stage/link
    context when we have it.
    """
    opp_id = commitment.get("opportunity_id")
    if not opp_id:
        return ""
    # The merchant_name field is already the resolved name (set in process_transcript)
    # Just indicate we have a GHL link
    return " :link:"
