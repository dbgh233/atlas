"""Atlas FastAPI application."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import structlog
from anthropic import AsyncAnthropic
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI, Request, Response
from slack_sdk.web.async_client import AsyncWebClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.clients.calendly import CalendlyClient
from app.core.clients.claude import ClaudeClient
from app.core.clients.ghl import GHLClient
from app.core.clients.google_search import GoogleSearchClient
from app.core.clients.ninjapear import NinjaPearClient
from app.core.clients.ocean import OceanClient
from app.core.clients.slack import SlackClient
from app.core.config import get_settings
from app.core.database import Database
from app.core.logging import setup_logging
from app.modules.admin.dlq_router import dlq_router
from app.modules.audit.router import router as audit_router
from app.modules.webhooks.router import admin_router as webhooks_admin_router
from app.modules.webhooks.router import router as webhooks_router
from app.modules.conversation.agent import ConversationAgent
from app.modules.health.checks import check_calendly_subscriptions, get_operational_status
from app.modules.meetings.router import router as meetings_router
from app.modules.precall.router import router as precall_router
from app.slack_app import set_agent, slack_handler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    # Startup
    settings = get_settings()
    app.state.settings = settings

    setup_logging(json_logs=settings.log_json_format, log_level=settings.log_level)

    log = structlog.get_logger()
    log.info(
        "starting_atlas",
        version=settings.app_version,
        log_format="json" if settings.log_json_format else "console",
    )

    # HTTP client (shared by API clients)
    app.state.http_client = httpx.AsyncClient(timeout=30.0)

    # Database
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    database = Database(settings.database_path)
    app.state.db = await database.connect()
    await database.run_migrations(app.state.db)
    log.info("database_ready", path=settings.database_path)

    # GHL client
    app.state.ghl_client = GHLClient(
        http_client=app.state.http_client,
        api_key=settings.ghl_api_key,
        location_id=settings.ghl_location_id,
        pipeline_id=settings.ghl_pipeline_id,
    )

    # Calendly client
    app.state.calendly_client = CalendlyClient(
        http_client=app.state.http_client,
        api_key=settings.calendly_api_key,
    )

    # Claude client
    anthropic_client = AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        timeout=60.0,
    )
    app.state.claude_client = ClaudeClient(client=anthropic_client)

    # Slack client
    app.state.slack_client = SlackClient(
        webhook_url=settings.slack_webhook_url,
        web_client=AsyncWebClient(token=settings.slack_bot_token) if settings.slack_bot_token else None,
    )

    # Google Custom Search client (optional enrichment)
    if settings.google_search_api_key and settings.google_search_engine_id:
        app.state.google_search_client = GoogleSearchClient(
            http_client=app.state.http_client,
            api_key=settings.google_search_api_key,
            search_engine_id=settings.google_search_engine_id,
        )
        log.info("google_search_client_ready")
    else:
        app.state.google_search_client = None
        log.info("google_search_client_skipped", reason="no_api_key")

    # Ocean.io client (optional enrichment)
    if settings.oceans_api_key:
        app.state.ocean_client = OceanClient(
            http_client=app.state.http_client,
            api_key=settings.oceans_api_key,
        )
        log.info("ocean_client_ready")
    else:
        app.state.ocean_client = None
        log.info("ocean_client_skipped", reason="no_api_key")

    # NinjaPear/Proxycurl client (optional LinkedIn enrichment)
    if settings.ninjapear_api_key:
        app.state.ninjapear_client = NinjaPearClient(
            http_client=app.state.http_client,
            api_key=settings.ninjapear_api_key,
        )
        log.info("ninjapear_client_ready")
    else:
        app.state.ninjapear_client = None
        log.info("ninjapear_client_skipped", reason="no_api_key")

    # Scheduler with daily audit at 8 AM EST (AUDIT-01)
    app.state.scheduler = AsyncIOScheduler(timezone="US/Eastern")

    async def _scheduled_audit():
        """Run daily pipeline audit with tagging, snapshot, auto-fixes, and Slack digest."""
        from app.modules.audit.digest import format_digest
        from app.modules.audit.engine import run_audit
        from app.modules.audit.tracker import (
            get_trend_comparison,
            save_snapshot,
            tag_findings,
        )
        from app.modules.audit.calendly_backfill import (
            format_backfill_digest,
            run_calendly_backfill,
        )
        from app.modules.autonomy.auto_fix import (
            format_auto_fix_digest,
            get_recent_auto_fixes,
            run_auto_fixes,
        )

        audit_log = structlog.get_logger()
        audit_log.info("scheduled_audit_start")
        try:
            result = await run_audit(app.state.ghl_client)
            tagged = await tag_findings(app.state.db, result)
            await save_snapshot(app.state.db, result, tagged, run_type="scheduled")
            trend = await get_trend_comparison(app.state.db)
            trend_summary = trend.get("summary") if trend.get("available") else None

            # Run auto-fixes for promoted fix types (CONV-07)
            findings_data = [
                {
                    "category": f.finding.category,
                    "opp_id": f.finding.opp_id,
                    "opp_name": f.finding.opp_name,
                    "field_name": f.finding.field_name,
                    "suggested_action": f.finding.suggested_action,
                }
                for f in tagged
            ]
            auto_fixed = await run_auto_fixes(
                app.state.db, app.state.ghl_client, findings_data
            )

            # Run Calendly backfill for missing fields
            try:
                backfill_result = await run_calendly_backfill(
                    app.state.ghl_client,
                    app.state.calendly_client,
                    app.state.db,
                    lookback_days=30,
                )
            except Exception as bf_err:
                audit_log.error("scheduled_backfill_error", error=str(bf_err))
                backfill_result = None

            # Build digest
            digest_text = format_digest(result, tagged=tagged, trend_summary=trend_summary)

            # Append auto-fix summary (CONV-08)
            if auto_fixed:
                auto_digest = format_auto_fix_digest(auto_fixed)
                if auto_digest:
                    digest_text += "\n\n" + auto_digest

            # Append backfill summary
            if backfill_result and backfill_result.actions:
                bf_digest = format_backfill_digest(backfill_result)
                if bf_digest:
                    digest_text += "\n\n" + bf_digest

            # Check commitment follow-through from meetings
            try:
                from app.modules.meetings.processor import (
                    check_commitment_followthrough,
                    format_commitment_digest,
                )
                from app.modules.meetings.repository import CommitmentRepository

                commitment_repo = CommitmentRepository(app.state.db)
                open_commitments = await commitment_repo.get_open()
                missed = await commitment_repo.get_missed()
                if open_commitments or missed:
                    commitment_digest = format_commitment_digest(
                        open_commitments, missed
                    )
                    if commitment_digest:
                        digest_text += "\n\n" + commitment_digest
            except Exception as cm_err:
                audit_log.error("scheduled_commitment_check_error", error=str(cm_err))

            # Run pattern detection (agenda gaps, recurring topics)
            try:
                from app.modules.meetings.patterns import (
                    detect_patterns,
                    format_pattern_digest,
                )

                pattern_results = await detect_patterns(
                    app.state.db, app.state.ghl_client
                )
                pattern_digest = format_pattern_digest(pattern_results)
                if pattern_digest:
                    digest_text += "\n\n" + pattern_digest
            except Exception as pd_err:
                audit_log.error("scheduled_pattern_detection_error", error=str(pd_err))

            await app.state.slack_client.send_message(digest_text)
            audit_log.info(
                "scheduled_audit_complete",
                total_opps=result.total_opportunities,
                total_issues=result.total_issues,
                auto_fixed=len(auto_fixed),
            )
        except Exception as e:
            audit_log.error("scheduled_audit_error", error=str(e), exc_info=True)
            try:
                await app.state.slack_client.send_message(
                    f":x: Atlas: Scheduled audit FAILED — {e}"
                )
            except Exception:
                pass

    app.state.scheduler.add_job(
        _scheduled_audit,
        "cron",
        hour=8,
        minute=0,
        day_of_week="mon-fri",
        id="daily_audit",
    )

    async def _subscription_health_check():
        """Periodic Calendly subscription health check (NOTIF-03)."""
        health_log = structlog.get_logger()
        try:
            callback_url = f"https://{settings.railway_domain}/webhooks/calendly"
            result = await check_calendly_subscriptions(
                app.state.calendly_client, app.state.slack_client, callback_url,
            )
            app.state.subscription_status = result
            health_log.info("periodic_subscription_check", healthy=result.get("healthy"))
        except Exception as e:
            health_log.error("periodic_subscription_check_error", error=str(e))

    app.state.scheduler.add_job(
        _subscription_health_check,
        "interval",
        hours=6,
        id="subscription_health_check",
    )

    async def _weekly_rollup():
        """Friday weekly commitment rollup."""
        from app.modules.meetings.processor import (
            auto_dismiss_fulfilled,
            generate_weekly_rollup,
        )

        rollup_log = structlog.get_logger()
        rollup_log.info("weekly_rollup_start")
        try:
            # Auto-dismiss fulfilled commitments first
            dismissed = await auto_dismiss_fulfilled(
                app.state.db, app.state.ghl_client
            )
            rollup_log.info("weekly_auto_dismiss", count=len(dismissed))

            # Generate rollup
            rollup_text = await generate_weekly_rollup(app.state.db)
            if rollup_text:
                await app.state.slack_client.send_message(rollup_text)
            rollup_log.info("weekly_rollup_complete")
        except Exception as e:
            rollup_log.error("weekly_rollup_error", error=str(e), exc_info=True)

    app.state.scheduler.add_job(
        _weekly_rollup,
        "cron",
        hour=16,
        minute=0,
        day_of_week="fri",
        id="weekly_rollup",
    )

    async def _auto_dismiss_check():
        """Daily auto-dismiss check for fulfilled commitments."""
        from app.modules.meetings.processor import auto_dismiss_fulfilled

        dismiss_log = structlog.get_logger()
        try:
            dismissed = await auto_dismiss_fulfilled(
                app.state.db, app.state.ghl_client
            )
            if dismissed:
                dismiss_log.info("daily_auto_dismiss", count=len(dismissed))
        except Exception as e:
            dismiss_log.error("daily_auto_dismiss_error", error=str(e))

    app.state.scheduler.add_job(
        _auto_dismiss_check,
        "cron",
        hour=7,
        minute=30,
        day_of_week="mon-fri",
        id="daily_auto_dismiss",
    )

    # Pre-call intelligence (Phase 10) — morning briefs before discovery calls
    async def _morning_precall_briefs():
        """Send pre-call intelligence DMs to reps with upcoming prospect calls."""
        from app.modules.precall.intelligence import run_morning_precall_briefs

        precall_log = structlog.get_logger()
        precall_log.info("precall_morning_start")
        try:
            result = await run_morning_precall_briefs(
                calendly_client=app.state.calendly_client,
                claude_client=app.state.claude_client,
                ghl_client=app.state.ghl_client,
                slack_client=app.state.slack_client,
                http_client=app.state.http_client,
                google_search_client=app.state.google_search_client,
                ocean_client=app.state.ocean_client,
                ninjapear_client=app.state.ninjapear_client,
            )
            precall_log.info(
                "precall_morning_complete",
                calls=result["calls_found"],
                briefs=result["briefs_sent"],
            )
        except Exception as e:
            precall_log.error("precall_morning_error", error=str(e), exc_info=True)

    app.state.scheduler.add_job(
        _morning_precall_briefs,
        "cron",
        hour=7,
        minute=30,
        day_of_week="mon-fri",
        id="morning_precall_briefs",
    )

    # Otter meeting sync (if API key configured)
    if settings.otter_api_key:
        from app.core.clients.otter import OtterClient
        app.state.otter_client = OtterClient(
            api_key=settings.otter_api_key,
            http_client=app.state.http_client,
        )
        log.info("otter_client_ready")

        async def _otter_sync():
            """Periodic Otter meeting sync."""
            from app.modules.meetings.otter_sync import sync_otter_meetings

            otter_log = structlog.get_logger()
            try:
                result = await sync_otter_meetings(
                    otter_client=app.state.otter_client,
                    db=app.state.db,
                    claude_client=app.state.claude_client,
                    ghl_client=app.state.ghl_client,
                    slack_client=app.state.slack_client,
                )
                otter_log.info("otter_sync_complete", **result)
            except Exception as e:
                otter_log.error("otter_sync_error", error=str(e))

        app.state.scheduler.add_job(
            _otter_sync,
            "interval",
            hours=2,
            id="otter_meeting_sync",
        )
    else:
        app.state.otter_client = None
        log.info("otter_client_skipped", reason="no_api_key")

    app.state.scheduler.start()
    jobs = ["daily_audit_8am_est", "subscription_check_6h", "weekly_rollup_fri_4pm", "daily_auto_dismiss_730am", "precall_briefs_730am"]
    if settings.otter_api_key:
        jobs.append("otter_sync_2h")
    log.info("scheduler_started", jobs=jobs)

    # Conversational agent (Phase 6)
    conversation_agent = ConversationAgent(
        anthropic_client=anthropic_client,
        ghl_client=app.state.ghl_client,
        db=app.state.db,
    )
    app.state.conversation_agent = conversation_agent
    set_agent(conversation_agent)
    log.info("conversation_agent_ready")

    # Startup subscription health check (INFRA-04)
    try:
        callback_url = f"https://{settings.railway_domain}/webhooks/calendly" if hasattr(settings, "railway_domain") and settings.railway_domain else ""
        if callback_url:
            sub_status = await check_calendly_subscriptions(
                app.state.calendly_client, app.state.slack_client, callback_url,
            )
            app.state.subscription_status = sub_status
            log.info("startup_subscription_check", healthy=sub_status.get("healthy"))
        else:
            app.state.subscription_status = None
            log.info("startup_subscription_check_skipped", reason="no_railway_domain")
    except Exception as e:
        app.state.subscription_status = None
        log.warning("startup_subscription_check_failed", error=str(e))

    log.info("atlas_ready", clients=["ghl", "calendly", "claude", "slack", "conversation"])

    yield

    # Shutdown
    log.info("shutting_down_atlas")
    app.state.scheduler.shutdown(wait=False)
    await app.state.db.close()
    await app.state.http_client.aclose()


app = FastAPI(lifespan=lifespan, title="Atlas", version="0.1.0")


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class StructlogMiddleware(BaseHTTPMiddleware):
    """Bind request context (request_id, path, method) to structlog contextvars."""

    async def dispatch(self, request: Request, call_next) -> Response:
        structlog.contextvars.clear_contextvars()

        request_id = request.headers.get("x-request-id", str(uuid4()))
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            path=request.url.path,
            method=request.method,
        )

        response = await call_next(request)
        return response


# Order matters: CorrelationId first (outermost), then Structlog
app.add_middleware(StructlogMiddleware)
app.add_middleware(CorrelationIdMiddleware)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health(request: Request):
    """Enhanced health endpoint with operational metrics (INFRA-05)."""
    settings = get_settings()
    try:
        ops = await get_operational_status(
            request.app.state.db,
            subscription_status=getattr(request.app.state, "subscription_status", None),
        )
        ops["service"] = settings.app_name
        ops["version"] = settings.app_version
        return ops
    except Exception:
        return {
            "status": "healthy",
            "service": settings.app_name,
            "version": settings.app_version,
            "timestamp": datetime.now(UTC).isoformat(),
        }


@app.post("/slack/events")
async def slack_events(request: Request):
    """Slack Events API endpoint (handled by slack-bolt)."""
    return await slack_handler.handle(request)


@app.get("/test/clients")
async def test_clients(request: Request):
    """Temporary endpoint to verify all API clients work against live services."""
    results = {}
    log = structlog.get_logger()

    # GHL
    try:
        opp = await request.app.state.ghl_client.get_opportunity("yUPOwC3GW7puTvGtgXPG")
        results["ghl"] = {"status": "ok", "opp_name": opp.get("name", "unknown")}
    except Exception as e:
        results["ghl"] = {"status": "error", "error": str(e)}
        log.error("test_ghl_failed", error=str(e))

    # Calendly
    try:
        user = await request.app.state.calendly_client.get_current_user()
        results["calendly"] = {"status": "ok", "user": user.get("resource", {}).get("name", "unknown")}
    except Exception as e:
        results["calendly"] = {"status": "error", "error": str(e)}
        log.error("test_calendly_failed", error=str(e))

    # Slack webhook
    try:
        await request.app.state.slack_client.send_message("Atlas test message from /test/clients")
        results["slack"] = {"status": "ok", "message": "sent"}
    except Exception as e:
        results["slack"] = {"status": "error", "error": str(e)}
        log.error("test_slack_failed", error=str(e))

    # Claude
    try:
        response = await request.app.state.claude_client.ask(
            "Respond with exactly: Atlas Claude client operational"
        )
        results["claude"] = {"status": "ok", "response": response}
    except Exception as e:
        results["claude"] = {"status": "error", "error": str(e)}
        log.error("test_claude_failed", error=str(e))

    return results


# ---------------------------------------------------------------------------
# Module routers
# ---------------------------------------------------------------------------

app.include_router(webhooks_router, prefix="/webhooks")
app.include_router(webhooks_admin_router, prefix="/admin")
app.include_router(dlq_router, prefix="/admin")
app.include_router(audit_router, prefix="/audit")
app.include_router(meetings_router, prefix="/meetings")
app.include_router(precall_router, prefix="/precall")
