"""Audit router — manual trigger, results, and trend endpoint."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.modules.audit.calendly_backfill import format_backfill_digest, run_calendly_backfill
from app.modules.audit.digest import format_digest, format_digest_blocks
from app.modules.audit.engine import run_audit
from app.modules.audit.tracker import get_trend_comparison, save_snapshot, tag_findings

log = structlog.get_logger()

router = APIRouter(tags=["audit"])


@router.post("/run")
async def trigger_audit(request: Request) -> JSONResponse:
    """Run a full pipeline audit, tag findings, save snapshot, send Slack digest."""
    ghl_client = request.app.state.ghl_client
    slack_client = request.app.state.slack_client
    db = request.app.state.db

    try:
        result = await run_audit(ghl_client)

        # Tag findings as NEW or STILL OPEN
        tagged = await tag_findings(db, result)

        # Save snapshot for trend tracking
        await save_snapshot(db, result, tagged, run_type="manual")

        # Get trend summary
        trend = await get_trend_comparison(db)
        trend_summary = trend.get("summary") if trend.get("available") else None

        # Run Calendly backfill
        calendly_client = request.app.state.calendly_client
        backfill_result = None
        try:
            backfill_result = await run_calendly_backfill(
                ghl_client, calendly_client, db, lookback_days=30
            )
        except Exception as e:
            log.error("audit_backfill_failed", error=str(e))

        # Send Slack digest with tags
        digest_text = format_digest(result, tagged=tagged, trend_summary=trend_summary)

        if backfill_result and backfill_result.actions:
            bf_digest = format_backfill_digest(backfill_result)
            if bf_digest:
                digest_text += "\n\n" + bf_digest

        try:
            await slack_client.send_message(digest_text)
        except Exception as e:
            log.error("audit_slack_digest_failed", error=str(e))

        # Send interactive Block Kit buttons if audit channel is configured
        settings = get_settings()
        audit_channel = settings.slack_audit_channel
        if audit_channel and slack_client.web_client:
            try:
                blocks = format_digest_blocks(result, tagged=tagged)
                if blocks:
                    await slack_client.send_rich_message(
                        channel=audit_channel,
                        blocks=blocks,
                        text="Atlas audit -- quick actions",
                    )
                    log.info("audit_buttons_sent", channel=audit_channel)
            except Exception as btn_err:
                log.error("audit_buttons_error", error=str(btn_err))

        # Build JSON response
        findings_json = [
            {
                "category": tf.finding.category,
                "opp_id": tf.finding.opp_id,
                "opp_name": tf.finding.opp_name,
                "stage": tf.finding.stage,
                "assigned_to": tf.finding.assigned_to,
                "description": tf.finding.description,
                "field_name": tf.finding.field_name,
                "suggested_action": tf.finding.suggested_action,
                "severity": tf.finding.severity,
                "suggested_value": tf.finding.suggested_value,
                "owner_hint": tf.finding.owner_hint,
                "tag": tf.tag,
                "days_open": tf.days_open,
            }
            for tf in tagged
        ]

        return JSONResponse(
            status_code=200,
            content={
                "status": "complete",
                "total_opportunities": result.total_opportunities,
                "total_issues": result.total_issues,
                "findings": findings_json,
                "summary": {
                    "missing_fields": len(result.missing_fields),
                    "stale_deals": len(result.stale_deals),
                    "overdue_tasks": sum(result.overdue_task_counts.values()) if result.overdue_task_counts else 0,
                    "overdue_task_counts": result.overdue_task_counts,
                    "close_lost_missing_reason": result.close_lost_missing_reason,
                    "new_issues": sum(1 for tf in tagged if tf.tag == "NEW"),
                    "recurring_issues": sum(1 for tf in tagged if tf.tag != "NEW"),
                },
                "trend": trend if trend.get("available") else None,
                "backfill": {
                    "events_checked": backfill_result.events_checked,
                    "events_matched": backfill_result.events_matched,
                    "fields_written": backfill_result.fields_written,
                    "fields_verified": backfill_result.fields_verified,
                    "skipped_multi_match": backfill_result.skipped_multi_match,
                    "errors": len(backfill_result.errors),
                } if backfill_result else None,
            },
        )

    except Exception as e:
        log.error("audit_run_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.post("/backfill")
async def trigger_backfill(request: Request) -> JSONResponse:
    """Run Calendly backfill only (for testing)."""
    try:
        result = await run_calendly_backfill(
            request.app.state.ghl_client,
            request.app.state.calendly_client,
            request.app.state.db,
            lookback_days=30,
        )
        return JSONResponse(
            status_code=200,
            content={
                "status": "complete",
                "events_checked": result.events_checked,
                "events_matched": result.events_matched,
                "fields_written": result.fields_written,
                "fields_verified": result.fields_verified,
                "skipped_multi_match": result.skipped_multi_match,
                "skipped_no_match": result.skipped_no_match,
                "skipped_already_populated": result.skipped_already_populated,
                "errors": result.errors,
                "actions": [
                    {
                        "opp_id": a.opp_id,
                        "opp_name": a.opp_name,
                        "field_name": a.field_name,
                        "value": a.value,
                        "verified": a.verified,
                    }
                    for a in result.actions
                ],
            },
        )
    except Exception as e:
        log.error("backfill_test_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.get("/trend")
async def get_audit_trend(request: Request) -> JSONResponse:
    """Get week-over-week audit trend comparison."""
    db = request.app.state.db
    try:
        trend = await get_trend_comparison(db)
        return JSONResponse(status_code=200, content=trend)
    except Exception as e:
        log.error("audit_trend_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.get("/accountability")
async def get_accountability(request: Request) -> JSONResponse:
    """Return current accountability items per user for debugging/review."""
    from app.models.database import AccountabilityRepository
    from app.modules.audit.rules import USER_NAMES

    db = request.app.state.db
    try:
        repo = AccountabilityRepository(db)
        result = {}
        for ghl_id, name in USER_NAMES.items():
            if ghl_id == "Unassigned":
                continue
            items = await repo.get_open_for_user(ghl_id)
            result[name] = {
                "open_count": len(items),
                "items": [
                    {
                        "opp_name": i["opp_name"],
                        "description": i["description"],
                        "status": i["status"],
                        "first_seen_at": i["first_seen_at"],
                    }
                    for i in items[:10]
                ],
            }
        return JSONResponse(status_code=200, content=result)
    except Exception as e:
        log.error("accountability_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.post("/scorecard")
async def trigger_scorecard(request: Request) -> JSONResponse:
    """Manually trigger the weekly accountability scorecard."""
    from app.modules.audit.scorecard import generate_weekly_scorecard, send_weekly_scorecard

    db = request.app.state.db
    slack_client = request.app.state.slack_client
    try:
        text = await generate_weekly_scorecard(db)
        await send_weekly_scorecard(db, slack_client)
        return JSONResponse(
            status_code=200,
            content={"status": "sent", "scorecard_text": text},
        )
    except Exception as e:
        log.error("scorecard_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )
