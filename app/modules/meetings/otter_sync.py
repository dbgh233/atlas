"""Otter.ai automatic meeting sync — polls for new transcripts and ingests them.

Runs on a schedule to check Otter for new pipeline-related meetings,
download transcripts, and feed them through the commitment extraction pipeline.
"""

from __future__ import annotations

import structlog

import aiosqlite

from app.core.clients.claude import ClaudeClient
from app.core.clients.ghl import GHLClient
from app.core.clients.otter import OtterClient
from app.core.clients.slack import SlackClient
from app.modules.meetings.processor import (
    build_commitment_blocks,
    classify_meeting_type,
    format_commitment_digest,
    process_transcript,
)
from app.modules.meetings.repository import CommitmentRepository, MeetingRepository

log = structlog.get_logger()

# Meeting titles that indicate pipeline-relevant meetings
RELEVANT_KEYWORDS = [
    "pipeline triage",
    "pipeline review",
    "onboarding review",
    "pipeline / onboarding",
    "pipeline/onboarding",
    "call review",
    "triage",
]


def _is_relevant_meeting(title: str) -> bool:
    """Check if a meeting title indicates a pipeline-relevant meeting."""
    lower = title.lower()
    return any(kw in lower for kw in RELEVANT_KEYWORDS)


async def sync_otter_meetings(
    otter_client: OtterClient,
    db: aiosqlite.Connection,
    claude_client: ClaudeClient,
    ghl_client: GHLClient,
    slack_client: SlackClient | None = None,
) -> dict:
    """Poll Otter for new meetings and ingest relevant ones.

    Returns summary of what was processed.
    """
    result = {
        "speeches_checked": 0,
        "relevant_found": 0,
        "already_ingested": 0,
        "newly_ingested": 0,
        "errors": [],
    }

    meeting_repo = MeetingRepository(db)

    try:
        speeches = await otter_client.list_speeches(page_size=20)
    except Exception as e:
        result["errors"].append(f"Failed to list speeches: {e}")
        log.error("otter_sync_list_failed", error=str(e))
        return result

    result["speeches_checked"] = len(speeches)

    for speech in speeches:
        speech_id = speech.get("id") or speech.get("speech_id") or speech.get("otid", "")
        title = speech.get("title", "")

        if not speech_id:
            continue

        # Check if relevant
        if not _is_relevant_meeting(title):
            continue

        result["relevant_found"] += 1

        # Check if already ingested
        existing = await meeting_repo.get_by_otter_id(str(speech_id))
        if existing:
            result["already_ingested"] += 1
            continue

        # Fetch full transcript
        try:
            full_speech = await otter_client.get_speech(str(speech_id))
        except Exception as e:
            result["errors"].append(f"Failed to fetch {speech_id}: {e}")
            continue

        # Extract transcript text
        transcript = full_speech.get("text") or full_speech.get("transcript", "")
        if not transcript:
            # Try speech.transcripts or speech.summary
            transcript = full_speech.get("summary", "")

        if not transcript or len(transcript) < 100:
            log.info("otter_sync_skip_short", speech_id=speech_id, length=len(transcript or ""))
            continue

        # Extract metadata
        start_time = (
            speech.get("start_time")
            or speech.get("created_at")
            or full_speech.get("start_time", "")
        )
        duration = speech.get("duration") or speech.get("duration_seconds")
        duration_minutes = int(duration / 60) if isinstance(duration, (int, float)) and duration > 0 else None

        attendees = []
        participants = speech.get("participants") or full_speech.get("participants", [])
        for p in participants:
            if isinstance(p, str):
                attendees.append(p)
            elif isinstance(p, dict):
                attendees.append(p.get("name") or p.get("email", "Unknown"))

        # Process transcript
        try:
            processed = await process_transcript(
                db=db,
                claude_client=claude_client,
                ghl_client=ghl_client,
                otter_speech_id=str(speech_id),
                title=title,
                start_time=str(start_time),
                transcript_text=transcript,
                attendees=attendees if attendees else None,
                duration_minutes=duration_minutes,
            )

            result["newly_ingested"] += 1
            log.info(
                "otter_sync_ingested",
                speech_id=speech_id,
                title=title,
                commitments=processed.commitments_extracted,
            )

            # Send Slack notification for new meeting ingestion
            if slack_client and processed.commitments_extracted > 0:
                try:
                    commitment_repo = CommitmentRepository(db)
                    open_commitments = await commitment_repo.get_open()
                    missed = await commitment_repo.get_missed()

                    if slack_client.web_client:
                        blocks = build_commitment_blocks(open_commitments, missed)
                        if blocks:
                            await slack_client.send_rich_message(
                                channel="C08RBFA977B",
                                blocks=blocks,
                                text=f"Meeting ingested: {processed.commitments_extracted} commitments from {title}",
                            )
                    else:
                        digest = format_commitment_digest(open_commitments, missed)
                        if digest:
                            await slack_client.send_message(
                                f":memo: *New meeting ingested:* _{title}_\n"
                                f"{processed.commitments_extracted} commitments extracted\n\n"
                                + digest
                            )
                except Exception as e:
                    log.error("otter_sync_slack_failed", error=str(e))

        except Exception as e:
            result["errors"].append(f"Failed to process {speech_id}: {e}")
            log.error("otter_sync_process_failed", speech_id=speech_id, error=str(e))

    log.info(
        "otter_sync_complete",
        checked=result["speeches_checked"],
        relevant=result["relevant_found"],
        new=result["newly_ingested"],
        errors=len(result["errors"]),
    )
    return result
