"""Atlas FastAPI application."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import structlog
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.logging import setup_logging


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

    app.state.http_client = httpx.AsyncClient(timeout=30.0)

    yield

    # Shutdown
    log.info("shutting_down_atlas")
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
