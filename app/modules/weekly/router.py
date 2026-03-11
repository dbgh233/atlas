"""Weekly report endpoints -- manual trigger for show rate report."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request

router = APIRouter()
log = structlog.get_logger()


@router.post("/run")
async def run_weekly_report_endpoint(request: Request):
    """Manually trigger the weekly show rate report.

    POST /weekly/run
    POST /weekly/run?dry_run=true  (returns report text without posting to Slack)
    """
    from app.modules.weekly.report import (
        calculate_show_rates,
        format_weekly_report,
        get_commitment_scorecard,
        get_pipeline_movement,
        run_weekly_report,
    )
    from datetime import UTC, datetime, timedelta

    dry_run = request.query_params.get("dry_run", "").lower() in ("true", "1", "yes")

    if dry_run:
        now = datetime.now(UTC)
        days_since_monday = now.weekday()
        week_start = (now - timedelta(days=days_since_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        week_end = now

        show_rates = await calculate_show_rates(
            request.app.state.calendly_client, week_start, week_end
        )
        pipeline_movement = await get_pipeline_movement(
            request.app.state.ghl_client, week_start, week_end
        )
        commitments = await get_commitment_scorecard(
            request.app.state.db, week_start, week_end
        )
        report_text = format_weekly_report(
            show_rates, pipeline_movement, commitments, week_start, week_end
        )
        return {
            "dry_run": True,
            "report_text": report_text,
            "show_rates": show_rates,
            "pipeline_movement": pipeline_movement,
            "commitments": commitments,
        }

    result = await run_weekly_report(
        calendly_client=request.app.state.calendly_client,
        ghl_client=request.app.state.ghl_client,
        slack_client=request.app.state.slack_client,
        db=request.app.state.db,
    )
    return {"posted": True, **result}
