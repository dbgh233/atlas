"""Pipeline reporting API endpoints — manual triggers for daily/weekly/monthly."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request

from app.modules.pipeline_reporting.data import pull_pipeline_data
from app.modules.pipeline_reporting.report import (
    format_daily_pulse,
    format_monthly_cohort,
    format_weekly_scorecard,
)

log = structlog.get_logger()
router = APIRouter()

CEO_SLACK_ID = "U07LUAX5T89"


async def _pull_data(request: Request) -> dict:
    """Pull pipeline data using app-level clients."""
    return await pull_pipeline_data(
        http_client=request.app.state.http_client,
        ghl_client=request.app.state.ghl_client,
    )


@router.post("/daily-pulse")
async def send_daily_pulse(request: Request):
    """Manually trigger the daily pipeline pulse DM to CEO."""
    data = await _pull_data(request)
    text = format_daily_pulse(data)
    await request.app.state.slack_client.send_dm_by_user_id(CEO_SLACK_ID, text)
    log.info("pipeline_daily_pulse_sent")
    return {"status": "sent", "type": "daily_pulse"}


@router.post("/weekly-scorecard")
async def send_weekly_scorecard(request: Request):
    """Manually trigger the weekly pipeline scorecard DM to CEO."""
    data = await _pull_data(request)
    text = format_weekly_scorecard(data)
    await request.app.state.slack_client.send_dm_by_user_id(CEO_SLACK_ID, text)
    log.info("pipeline_weekly_scorecard_sent")
    return {"status": "sent", "type": "weekly_scorecard"}


@router.post("/monthly-cohort")
async def send_monthly_cohort(request: Request):
    """Manually trigger the monthly cohort analysis DM to CEO."""
    data = await _pull_data(request)
    text = format_monthly_cohort(data)
    await request.app.state.slack_client.send_dm_by_user_id(CEO_SLACK_ID, text)
    log.info("pipeline_monthly_cohort_sent")
    return {"status": "sent", "type": "monthly_cohort"}


@router.get("/data")
async def get_pipeline_data(request: Request):
    """Return raw pipeline data as JSON (for debugging / Claude cloud project)."""
    data = await _pull_data(request)
    return data


@router.post("/run")
async def run_all_reports(request: Request):
    """Run all three reports and send to CEO. Used for testing."""
    data = await _pull_data(request)

    pulse = format_daily_pulse(data)
    scorecard = format_weekly_scorecard(data)

    slack = request.app.state.slack_client
    await slack.send_dm_by_user_id(CEO_SLACK_ID, pulse)
    await slack.send_dm_by_user_id(CEO_SLACK_ID, scorecard)

    log.info("pipeline_all_reports_sent")
    return {
        "status": "sent",
        "types": ["daily_pulse", "weekly_scorecard"],
        "data_summary": {
            "current_month_approvals": data["current_month"]["approvals"],
            "current_month_went_live": data["current_month"]["went_live"],
            "quarter_approvals": data["quarter"]["approvals"],
            "quarter_went_live": data["quarter"]["went_live"],
            "pipeline_open": data["pipeline"]["total_open"],
            "active_merchants": data.get("active_merchants", 0),
        },
    }
