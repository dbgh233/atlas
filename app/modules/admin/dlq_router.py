"""Admin API for dead letter queue inspection and retry management."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.models.database import DLQRepository

log = structlog.get_logger()

dlq_router = APIRouter(prefix="/dlq", tags=["admin-dlq"])


@dlq_router.get("")
async def list_dlq_entries(
    request: Request, status: str | None = None, limit: int = 50
) -> JSONResponse:
    """List DLQ entries, optionally filtered by status."""
    db = request.app.state.db
    entries = await DLQRepository(db).get_all(limit=limit, status=status)
    return JSONResponse(status_code=200, content={"entries": entries, "count": len(entries)})


@dlq_router.get("/{entry_id}")
async def get_dlq_entry(request: Request, entry_id: int) -> JSONResponse:
    """Get a single DLQ entry by ID."""
    db = request.app.state.db
    entry = await DLQRepository(db).get_by_id(entry_id)
    if entry is None:
        return JSONResponse(status_code=404, content={"error": "DLQ entry not found"})
    return JSONResponse(status_code=200, content=entry)


@dlq_router.post("/{entry_id}/retry")
async def retry_dlq_entry(request: Request, entry_id: int) -> JSONResponse:
    """Mark a DLQ entry for retry (increments retry_count, sets status='retrying')."""
    db = request.app.state.db
    repo = DLQRepository(db)
    entry = await repo.get_by_id(entry_id)
    if entry is None:
        return JSONResponse(status_code=404, content={"error": "DLQ entry not found"})
    updated = await repo.retry_entry(entry_id)
    return JSONResponse(status_code=200, content={"status": "retrying", "entry": updated})
