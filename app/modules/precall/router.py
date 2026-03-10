"""Pre-call intelligence router — trigger briefs manually or check status."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.modules.precall.intelligence import (
    get_todays_calls,
    run_morning_precall_briefs,
)

log = structlog.get_logger()

router = APIRouter(tags=["precall"])


@router.post("/run")
async def trigger_precall_briefs(request: Request) -> JSONResponse:
    """Manually trigger pre-call intelligence briefs for today's calls."""
    try:
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
        # Simplify invitee data for JSON response
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
