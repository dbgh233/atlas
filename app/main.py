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
from app.core.clients.slack import SlackClient
from app.core.config import get_settings
from app.core.database import Database
from app.core.logging import setup_logging
from app.modules.admin.dlq_router import dlq_router
from app.modules.webhooks.router import admin_router as webhooks_admin_router
from app.modules.webhooks.router import router as webhooks_router
from app.slack_app import slack_handler


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

    # Scheduler
    app.state.scheduler = AsyncIOScheduler()
    app.state.scheduler.start()
    log.info("scheduler_started")

    log.info("atlas_ready", clients=["ghl", "calendly", "claude", "slack"])

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
async def health():
    """Health check endpoint for Railway and monitoring."""
    settings = get_settings()
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
