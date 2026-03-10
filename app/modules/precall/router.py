"""Pre-call intelligence router — trigger briefs manually or check status."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.modules.precall.intelligence import (
    get_todays_calls,
    run_morning_precall_briefs,
    run_precall_dry_run,
)
from app.modules.precall.rep_profiles import get_all_reps

log = structlog.get_logger()

router = APIRouter(tags=["precall"])


@router.post("/run")
async def trigger_precall_briefs(request: Request) -> JSONResponse:
    """Trigger pre-call intelligence briefs for today's calls.

    Query params:
        dry_run=true — generate briefs but return them as JSON instead of sending to Slack.
    """
    dry_run = request.query_params.get("dry_run", "").lower() in ("true", "1", "yes")

    try:
        if dry_run:
            result = await run_precall_dry_run(
                calendly_client=request.app.state.calendly_client,
                claude_client=request.app.state.claude_client,
                ghl_client=request.app.state.ghl_client,
                http_client=request.app.state.http_client,
            )
        else:
            result = await run_morning_precall_briefs(
                calendly_client=request.app.state.calendly_client,
                claude_client=request.app.state.claude_client,
                ghl_client=request.app.state.ghl_client,
                slack_client=request.app.state.slack_client,
                http_client=request.app.state.http_client,
            )
        return JSONResponse(status_code=200, content=result)
    except Exception as e:
        log.error("precall_trigger_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.get("/today")
async def get_todays_upcoming_calls(request: Request) -> JSONResponse:
    """List today's upcoming prospect calls from Calendly."""
    try:
        calls = await get_todays_calls(request.app.state.calendly_client)
        simplified = []
        for call in calls:
            invitee_names = [
                inv.get("name", inv.get("email", "unknown"))
                for inv in call.get("invitees", [])
            ]
            simplified.append({
                "event_name": call["event_name"],
                "start_time": call["start_time"],
                "host": call["host_name"],
                "host_email": call["host_email"],
                "prospects": invitee_names,
            })
        return JSONResponse(
            status_code=200,
            content={"calls": simplified, "total": len(simplified)},
        )
    except Exception as e:
        log.error("precall_today_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.get("/reps")
async def list_reps(request: Request) -> JSONResponse:
    """List configured sales rep profiles."""
    reps = get_all_reps()
    return JSONResponse(
        status_code=200,
        content={
            "reps": [
                {
                    "name": r["name"],
                    "calendly_email": r["calendly_email"],
                    "slack_user_id": r["slack_user_id"],
                    "linkedin_url": r.get("linkedin_url", ""),
                    "role": r.get("role", ""),
                }
                for r in reps
            ],
            "total": len(reps),
        },
    )
