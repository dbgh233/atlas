"""Meeting intelligence router — Otter webhook, commitment tracking, patterns."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.modules.meetings.processor import (
    check_commitment_followthrough,
    format_commitment_digest,
    process_transcript,
)
from app.modules.meetings.repository import (
    CommitmentRepository,
    MeetingRepository,
)

log = structlog.get_logger()

router = APIRouter(tags=["meetings"])


@router.post("/ingest")
async def ingest_transcript(request: Request) -> JSONResponse:
    """Ingest a meeting transcript for processing.

    Accepts JSON with meeting metadata and transcript text.
    Can be called by Otter webhook, Zapier, or manually.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid JSON body"},
        )

    # Required fields
    title = body.get("title")
    transcript = body.get("transcript") or body.get("transcript_text")
    speech_id = body.get("speech_id") or body.get("otter_speech_id") or body.get("id", "manual")
    start_time = body.get("start_time") or body.get("created_at", "")

    if not title or not transcript:
        return JSONResponse(
            status_code=400,
            content={"error": "Missing required fields: title, transcript"},
        )

    db = request.app.state.db
    claude_client = request.app.state.claude_client
    ghl_client = request.app.state.ghl_client

    try:
        result = await process_transcript(
            db=db,
            claude_client=claude_client,
            ghl_client=ghl_client,
            otter_speech_id=str(speech_id),
            title=title,
            start_time=start_time,
            transcript_text=transcript,
            organizer=body.get("organizer"),
            attendees=body.get("attendees"),
            end_time=body.get("end_time"),
            duration_minutes=body.get("duration_minutes"),
        )

        # Send Slack summary if commitments were extracted
        if result.commitments_extracted > 0:
            try:
                commitment_repo = CommitmentRepository(db)
                open_commitments = await commitment_repo.get_open()
                missed = await commitment_repo.get_missed()
                digest = format_commitment_digest(open_commitments, missed)
                if digest:
                    slack_client = request.app.state.slack_client
                    await slack_client.send_message(digest)
            except Exception as e:
                log.error("meeting_slack_failed", error=str(e))

        return JSONResponse(
            status_code=200,
            content={
                "status": "processed",
                "meeting_id": result.meeting_id,
                "meeting_type": result.meeting_type,
                "commitments_extracted": result.commitments_extracted,
                "merchants_found": result.merchants_found,
                "merchants_matched": result.merchants_matched_to_opps,
                "undiscussed_concerns": result.undiscussed_concerns,
                "errors": result.errors,
            },
        )

    except Exception as e:
        log.error("meeting_ingest_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.get("/commitments")
async def get_commitments(request: Request) -> JSONResponse:
    """Get open commitments, optionally filtered by assignee."""
    db = request.app.state.db
    user_filter = request.query_params.get("assignee")

    try:
        repo = CommitmentRepository(db)
        commitments = await repo.get_open(assignee_ghl_id=user_filter)
        missed = await repo.get_missed()

        return JSONResponse(
            status_code=200,
            content={
                "open": [dict(c) for c in commitments],
                "missed": [dict(c) for c in missed],
                "total_open": len(commitments),
                "total_missed": len(missed),
            },
        )
    except Exception as e:
        log.error("commitments_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.post("/check-followthrough")
async def check_followthrough(request: Request) -> JSONResponse:
    """Check if open commitments have been fulfilled based on GHL state."""
    db = request.app.state.db
    ghl_client = request.app.state.ghl_client

    try:
        results = await check_commitment_followthrough(db, ghl_client)
        return JSONResponse(
            status_code=200,
            content={
                "commitments_checked": len(results),
                "results": results,
            },
        )
    except Exception as e:
        log.error("followthrough_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.get("/recent")
async def get_recent_meetings(request: Request) -> JSONResponse:
    """Get recent processed meetings."""
    db = request.app.state.db
    limit = int(request.query_params.get("limit", "10"))

    try:
        repo = MeetingRepository(db)
        meetings = await repo.get_recent(limit=limit)
        return JSONResponse(status_code=200, content={"meetings": meetings})
    except Exception as e:
        log.error("meetings_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )
