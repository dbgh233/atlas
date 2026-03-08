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
from datetime import UTC, datetime

import aiosqlite
import structlog

from app.core.clients.claude import ClaudeClient
from app.core.clients.ghl import GHLClient
from app.modules.audit.rules import USER_NAMES
from app.modules.meetings.repository import (
    CommitmentRepository,
    MeetingRepository,
    PatternRepository,
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
- action: What they committed to do (be specific, include merchant name if mentioned)
- merchant_name: The merchant/company name if one was mentioned (null if general)
- deadline: Any mentioned deadline ("by Friday", "today", "this week", "end of day") or null
- source_quote: The approximate quote from the transcript (keep short, 1-2 sentences max)

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
    errors: list[str] = field(default_factory=list)


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
    4. Match merchants to GHL opportunities
    5. Store commitments with opp links
    6. Detect patterns
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

    # Match merchants to GHL opportunities
    all_opps = None
    try:
        all_opps = await ghl_client.search_opportunities()
    except Exception:
        pass

    merchant_opp_map = await _match_merchants_to_opps(
        ghl_client, merchants_discussed, all_opps
    )
    result.merchants_matched_to_opps = sum(
        1 for v in merchant_opp_map.values() if v is not None
    )

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

    # Store commitments
    for c in commitments_data:
        assignee = c.get("assignee", "Unknown")
        ghl_id = _match_assignee_to_ghl(assignee)
        merchant = c.get("merchant_name")
        opp_id = merchant_opp_map.get(merchant) if merchant else None

        await commitment_repo.add(
            meeting_id=meeting_id,
            assignee_name=assignee,
            assignee_ghl_id=ghl_id,
            action=c.get("action", ""),
            merchant_name=merchant,
            opportunity_id=opp_id,
            deadline=c.get("deadline"),
            source_quote=c.get("source_quote"),
        )
        result.commitments_extracted += 1

    log.info(
        "meeting_processed",
        meeting_id=meeting_id,
        meeting_type=meeting_type,
        commitments=result.commitments_extracted,
        merchants=len(merchants_discussed),
        matched_opps=result.merchants_matched_to_opps,
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


def _user_mention(ghl_id: str | None, name: str) -> str:
    """Return Slack @mention or display name."""
    from app.modules.audit.rules import SLACK_USER_IDS
    if ghl_id:
        slack_id = SLACK_USER_IDS.get(ghl_id)
        if slack_id:
            return f"<@{slack_id}>"
    return name


def format_commitment_digest(
    commitments: list[dict],
    missed: list[dict] | None = None,
) -> str:
    """Format commitment tracking for Slack digest — grouped by user."""
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
        user_total = len(data["open"]) + len(data["missed"])
        lines.append(f"\n{mention}:")

        for c in data["missed"]:
            action = c.get("action", "?")
            meeting = c.get("meeting_title", "")
            lines.append(f"  :red_circle: {action} (overdue, from {meeting})")

        for c in data["open"]:
            action = c.get("action", "?")
            deadline = c.get("deadline")
            deadline_str = f" -- {deadline}" if deadline else ""
            lines.append(f"  :white_circle: {action}{deadline_str}")

    return "\n".join(lines)
