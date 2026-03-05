# Phase 1: Foundation - Research

**Researched:** 2026-03-05
**Domain:** FastAPI service scaffold, API client integration, persistent storage, Railway deployment
**Confidence:** HIGH

## Summary

Phase 1 delivers a running FastAPI service on Railway with five working API clients (GHL, Calendly, Slack incoming webhooks, Slack Events API, Claude Opus 4.6), SQLite persistent storage via Railway volumes, structured JSON logging with correlation IDs, and a health endpoint. This is pure infrastructure -- no business logic.

The standard approach is a modular monolith using FastAPI's lifespan for resource management, httpx AsyncClient for all outbound HTTP, slack-bolt's AsyncApp for Slack Events API handling, the anthropic SDK's AsyncAnthropic for Claude, aiosqlite for async SQLite access on a Railway-mounted volume, and structlog with contextvars for request-scoped structured logging.

**Primary recommendation:** Use FastAPI lifespan to manage all client lifecycles (httpx, aiosqlite, anthropic, APScheduler). Store the SQLite database on a Railway volume at `/app/data/atlas.db`. Use slack-bolt's AsyncApp with FastAPI adapter for Slack Events API -- it handles URL verification, request signing, and event dispatch automatically.

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | `>=0.135.0` | Web framework | Industry standard async Python API framework. Native Pydantic, OpenAPI, dependency injection. |
| Uvicorn | `>=0.41.0` | ASGI server | Default FastAPI production server. Single worker for Railway. |
| Python | `3.12` | Runtime | Stable, broad library compat. Railway Nixpacks auto-detects. |
| httpx | `>=0.28.0` | Async HTTP client | GHL + Calendly API calls. Connection pooling via AsyncClient. Async-native. |
| anthropic | `>=0.84.0` | Claude API | Official SDK. AsyncAnthropic for async usage. Built-in retries, rate limit handling. Model: `claude-opus-4-6`. |
| slack-bolt | `>=1.27.0` | Slack Events API | Official Slack framework. AsyncApp + FastAPI adapter. Handles URL verification, request signing, @mention events, DMs. |
| slack-sdk | `>=3.33.0` | Slack Web API | Ships with slack-bolt. AsyncWebClient for chat.postMessage (incoming webhook alternative for richer messages). |
| aiosqlite | `>=0.20.0` | Async SQLite | Non-blocking SQLite on asyncio event loop. Wraps stdlib sqlite3. |
| pydantic-settings | `>=2.13.0` | Environment config | Type-safe env var loading with BaseSettings. Validates at startup. |
| structlog | `>=25.5.0` | Structured logging | JSON logs for Railway. Contextvars for correlation IDs. Dev console + prod JSON modes. |
| APScheduler | `>=3.11.0,<4.0` | Cron scheduling | AsyncIOScheduler in FastAPI lifespan. Daily audit at 8 AM EST. Do NOT use 4.x alpha. |
| tenacity | `>=9.1.0` | Retry with backoff | Decorator-based retry for API calls. Exponential backoff, jitter, async-native. |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| orjson | `>=3.10.0` | Fast JSON serialization | structlog's JSONRenderer uses it when available. ~10x faster than stdlib json. |
| asgi-correlation-id | `>=4.0.0` | Correlation ID middleware | Generates/propagates X-Request-ID header. Integrates with structlog contextvars. |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| slack-bolt | Raw slack-sdk + manual event handling | slack-bolt handles URL verification, request signing, middleware chain automatically. Raw sdk = reimplementing all of that. Use slack-bolt. |
| aiosqlite | SQLAlchemy async + aiosqlite | ORM is overkill for 4 simple tables. Raw SQL with aiosqlite is clearer and faster to build. Revisit if schema complexity grows. |
| anthropic SDK | httpx direct to API | SDK handles retries, rate limits, streaming, types. No reason to hand-roll. |
| Railway volumes + SQLite | Railway PostgreSQL addon | SQLite is simpler, cheaper ($0 vs $5+/mo), sufficient for DLQ + audit snapshots + idempotency. PostgreSQL warranted only at scale. |

**Installation:**
```bash
pip install "fastapi[standard]" uvicorn httpx pydantic-settings \
    "apscheduler>=3.11.0,<4.0" tenacity structlog orjson \
    slack-bolt anthropic aiosqlite asgi-correlation-id
```

**pyproject.toml:**
```toml
[project]
name = "atlas"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi[standard]>=0.135.0",
    "uvicorn>=0.41.0",
    "httpx>=0.28.0",
    "pydantic-settings>=2.13.0",
    "apscheduler>=3.11.0,<4.0",
    "tenacity>=9.1.0",
    "structlog>=25.5.0",
    "orjson>=3.10.0",
    "slack-bolt>=1.27.0",
    "anthropic>=0.84.0",
    "aiosqlite>=0.20.0",
    "asgi-correlation-id>=4.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24.0",
    "respx>=0.22.0",
    "time-machine>=2.15.0",
    "ruff>=0.9.0",
    "mypy>=1.13.0",
]
```

## Architecture Patterns

### Recommended Project Structure

```
atlas/
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI app, lifespan, mount routers
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py               # Pydantic Settings (all env vars)
│   │   ├── logging.py              # structlog setup (JSON + console modes)
│   │   ├── database.py             # aiosqlite connection manager + migrations
│   │   └── clients/
│   │       ├── __init__.py
│   │       ├── ghl.py              # GHLClient: httpx-based, rate-limited, retried
│   │       ├── calendly.py         # CalendlyClient: signature verification, subscriptions
│   │       ├── slack.py            # SlackClient: incoming webhook + WebClient wrapper
│   │       └── claude.py           # ClaudeClient: AsyncAnthropic wrapper
│   ├── slack_app.py                # slack-bolt AsyncApp (event handlers)
│   ├── modules/
│   │   ├── __init__.py
│   │   ├── events/                 # Phase 2: Calendly webhook handler
│   │   │   ├── __init__.py
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   └── schemas.py
│   │   └── audit/                  # Phase 4: Daily pipeline audit
│   │       ├── __init__.py
│   │       ├── router.py
│   │       ├── service.py
│   │       ├── rules.py
│   │       └── schemas.py
│   └── models/
│       ├── __init__.py
│       └── database.py             # SQLite table definitions (DDL + query helpers)
├── tests/
│   ├── conftest.py
│   ├── test_core/
│   │   ├── test_config.py
│   │   ├── test_ghl_client.py
│   │   ├── test_slack_client.py
│   │   └── test_claude_client.py
│   └── test_api/
│       └── test_health.py
├── migrations/
│   └── 001_initial.sql             # Initial schema DDL
├── pyproject.toml
├── railway.toml                    # Railway deployment config
├── .env.example                    # Template for local development
└── .gitignore
```

**Key structural decisions:**
- `app/` package (not `atlas/`) -- matches Railway's Nixpacks expectations for `/app` working directory
- `app/slack_app.py` is a dedicated file for the slack-bolt AsyncApp instance, separate from FastAPI routes. The FastAPI app mounts the slack-bolt handler at `/slack/events`.
- `app/models/database.py` contains raw SQL table definitions and typed query helpers (not an ORM). Keeps SQL centralized.
- `migrations/` holds numbered SQL files applied on startup by `app/core/database.py`.

### Pattern 1: Lifespan Resource Management

**What:** Create all long-lived resources (httpx client, aiosqlite connection, anthropic client, APScheduler) in FastAPI's lifespan context manager. Store on `app.state`. Close on shutdown.

**When to use:** Always. This is the only correct way to manage async resources in FastAPI.

**Example:**
```python
# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
import httpx
import aiosqlite
from anthropic import AsyncAnthropic
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import get_settings
from app.core.logging import setup_logging

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(json_logs=settings.log_json_format)

    # Initialize all clients
    app.state.http_client = httpx.AsyncClient(timeout=30.0)
    app.state.db = await aiosqlite.connect(settings.database_path)
    app.state.db.row_factory = aiosqlite.Row
    await run_migrations(app.state.db)

    app.state.anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key)

    # APScheduler
    scheduler = AsyncIOScheduler()
    app.state.scheduler = scheduler
    scheduler.start()

    yield

    # Shutdown
    scheduler.shutdown()
    await app.state.db.close()
    await app.state.http_client.aclose()

app = FastAPI(lifespan=lifespan)
```
Source: [FastAPI Lifespan Events](https://fastapi.tiangolo.com/advanced/events/), [FastAPI Lifespan Handler GitHub](https://github.com/trondhindenes/fastapi-lifespan-handler)

### Pattern 2: Dependency Injection from app.state

**What:** Use FastAPI dependencies that pull clients from `request.app.state`. Services receive typed clients, not raw state.

**When to use:** All route handlers and service functions.

**Example:**
```python
# app/core/clients/ghl.py
from fastapi import Request, Depends
import httpx

class GHLClient:
    def __init__(self, http_client: httpx.AsyncClient, api_key: str, location_id: str):
        self.http = http_client
        self.api_key = api_key
        self.location_id = location_id
        self.base_url = "https://services.leadconnectorhq.com"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Version": "2021-07-28",
            "Content-Type": "application/json",
        }

    async def get_opportunity(self, opp_id: str) -> dict:
        resp = await self.http.get(
            f"{self.base_url}/opportunities/{opp_id}",
            headers=self.headers,
        )
        resp.raise_for_status()
        return resp.json()

def get_ghl_client(request: Request) -> GHLClient:
    settings = request.app.state.settings
    return GHLClient(
        http_client=request.app.state.http_client,
        api_key=settings.ghl_api_key,
        location_id=settings.ghl_location_id,
    )
```
Source: [FastAPI Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/)

### Pattern 3: slack-bolt AsyncApp Mounted in FastAPI

**What:** Create a slack-bolt AsyncApp for handling Slack events (app_mention, message, etc.). Mount it on a FastAPI route using AsyncSlackRequestHandler.

**When to use:** Slack Events API integration. This handles URL verification, request signature validation, and event dispatch.

**Example:**
```python
# app/slack_app.py
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

slack_app = AsyncApp(
    token="xoxb-...",       # SLACK_BOT_TOKEN
    signing_secret="...",   # SLACK_SIGNING_SECRET
)

@slack_app.event("app_mention")
async def handle_mention(event, say, logger):
    logger.info(f"Received mention from {event['user']}")
    await say(f"Hello <@{event['user']}>! Atlas is online.")

@slack_app.event("message")
async def handle_dm(event, say, logger):
    # Only fires for DMs (im channel type)
    logger.info(f"DM from {event['user']}: {event.get('text', '')}")
    await say("I received your message. Atlas conversational features coming soon.")

handler = AsyncSlackRequestHandler(slack_app)

# app/main.py (mounting)
from fastapi import Request
from app.slack_app import handler as slack_handler

@app.post("/slack/events")
async def slack_events(request: Request):
    return await slack_handler.handle(request)
```
Source: [slack-bolt FastAPI async example](https://github.com/slackapi/bolt-python/blob/main/examples/fastapi/async_app.py)

### Pattern 4: Structured Logging with Correlation IDs

**What:** Configure structlog for JSON output in production, console in development. Use ASGI middleware to generate correlation IDs that flow through all log lines for a given request.

**When to use:** Always. Set up before writing any business logic.

**Example:**
```python
# app/core/logging.py
import logging
import structlog

def setup_logging(json_logs: bool = False, log_level: str = "INFO"):
    timestamper = structlog.processors.TimeStamper(fmt="iso")

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.stdlib.ExtraAdder(),
        timestamper,
        structlog.processors.StackInfoRenderer(),
    ]

    if json_logs:
        shared_processors.append(structlog.processors.format_exc_info)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    log_renderer = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer()
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            log_renderer,
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level.upper())

    # Capture uvicorn logs through structlog
    for name in ["uvicorn", "uvicorn.error"]:
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True

    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False
```

**Middleware for correlation IDs:**
```python
# In app/main.py
from asgi_correlation_id import CorrelationIdMiddleware
import structlog
import uuid
from starlette.middleware.base import BaseHTTPMiddleware

class StructlogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request.headers.get("x-request-id", str(uuid.uuid4())),
            path=request.url.path,
            method=request.method,
        )
        response = await call_next(request)
        return response

# Order matters: CorrelationIdMiddleware first, then StructlogMiddleware
app.add_middleware(StructlogMiddleware)
app.add_middleware(CorrelationIdMiddleware)
```
Source: [structlog contextvars docs](https://www.structlog.org/en/stable/contextvars.html), [FastAPI + structlog integration guide](https://wazaari.dev/blog/fastapi-structlog-integration)

### Pattern 5: SQLite Schema with Raw SQL + aiosqlite

**What:** Define tables as SQL DDL, apply on startup, query with parameterized SQL through aiosqlite. No ORM.

**When to use:** Atlas's 4 simple tables (DLQ, audit_snapshots, interaction_log, idempotency_keys). ORM overhead not warranted.

**Example:**
```python
# app/core/database.py
import aiosqlite
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"

async def run_migrations(db: aiosqlite.Connection):
    """Apply numbered SQL migrations in order."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id INTEGER PRIMARY KEY,
            filename TEXT NOT NULL UNIQUE,
            applied_at TEXT DEFAULT (datetime('now'))
        )
    """)
    await db.commit()

    applied = set()
    async with db.execute("SELECT filename FROM _migrations") as cursor:
        async for row in cursor:
            applied.add(row[0])

    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if sql_file.name not in applied:
            sql = sql_file.read_text()
            await db.executescript(sql)
            await db.execute(
                "INSERT INTO _migrations (filename) VALUES (?)",
                (sql_file.name,),
            )
            await db.commit()
```
Source: [aiosqlite GitHub](https://github.com/omnilib/aiosqlite)

### Anti-Patterns to Avoid

- **Creating httpx.AsyncClient per-request:** Leaks connections, slow. Create once in lifespan, reuse via app.state.
- **Using `requests` library anywhere:** Blocks the async event loop. Use httpx exclusively.
- **Global module-level clients:** `ghl = GHLClient()` at import time -- untestable, config not available at import. Use dependency injection.
- **Handling Slack Events API manually:** Reimplementing URL verification, request signing, event dispatch. Use slack-bolt which does all of this correctly.
- **APScheduler with multiple workers:** Each worker spawns its own scheduler. Use `--workers 1` in Uvicorn start command.
- **SQLite writes without WAL mode:** Default journal mode blocks readers during writes. Enable WAL: `PRAGMA journal_mode=WAL;`

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Slack URL verification | Manual challenge response handler | slack-bolt AsyncApp | Handles verification, signing, event routing. 5 lines vs 50. |
| Slack request signature verification | Manual HMAC computation | slack-bolt's built-in verification | Timing-safe comparison, header parsing, timestamp validation included. |
| Claude API interaction | Raw httpx to Anthropic API | anthropic SDK (AsyncAnthropic) | Built-in retries, rate limiting, streaming, typed responses, error hierarchy. |
| HTTP retry with backoff | Custom while/sleep loops | tenacity decorators | Exponential backoff, jitter, per-exception strategies, async support. |
| Correlation ID propagation | Manual header parsing + threading.local | asgi-correlation-id + structlog contextvars | Thread-safe, async-safe, automatic propagation across entire request lifecycle. |
| SQLite migration system | Manual schema versioning | Simple numbered SQL files + _migrations table (see Pattern 5) | Lightweight, zero dependencies, sufficient for 4 tables. Don't add Alembic for this. |
| Environment variable loading | os.environ with manual validation | pydantic-settings BaseSettings | Type coercion, validation, .env file support, fail-fast on missing vars. |

**Key insight:** Phase 1 is 100% integration code. Every component has an official SDK or established library. Hand-rolling any client or infrastructure layer wastes time and introduces bugs that libraries have already solved.

## Common Pitfalls

### Pitfall 1: Railway Volume Not Mounted at Runtime

**What goes wrong:** You write SQLite to a path like `./data/atlas.db` but forget to mount a Railway volume to that path. Data persists during a single deployment but is lost on every redeploy (ephemeral filesystem). Or, you write during build time (in a Dockerfile RUN step) and the volume isn't mounted yet.
**Why it happens:** Railway volumes mount at container start time, not build time. Nixpacks puts code in `/app`, so your relative paths resolve to `/app/data/`.
**How to avoid:**
1. Mount a Railway volume to `/app/data`
2. Set `DATABASE_PATH=/app/data/atlas.db` as an environment variable
3. Never write to the database path during build
4. Test by redeploying and verifying data persists
**Warning signs:** Data disappears after every deploy. SQLite file size resets to 0.

### Pitfall 2: Slack Events API URL Verification Fails

**What goes wrong:** When configuring the Request URL in Slack app settings, Slack sends a challenge POST. If your endpoint doesn't respond correctly within 3 seconds, Slack rejects the URL and you can't save the configuration. Teams waste hours debugging this.
**Why it happens:** The endpoint must return `{"challenge": "..."}` with a 200 status. If slack-bolt isn't properly mounted, the request falls through to a 404 or FastAPI's default handler.
**How to avoid:**
1. Use slack-bolt's AsyncSlackRequestHandler -- it handles URL verification automatically
2. Deploy the `/slack/events` endpoint to Railway BEFORE configuring the Slack app Request URL
3. Verify with `curl -X POST https://your-app.railway.app/slack/events -H "Content-Type: application/json" -d '{"type":"url_verification","challenge":"test123"}'`
**Warning signs:** Slack app settings page shows "URL didn't respond correctly."

### Pitfall 3: structlog Contextvars Isolation Between Sync/Async

**What goes wrong:** Context variables set in a synchronous middleware don't appear in async route handlers, and vice versa. Logs from certain code paths are missing correlation IDs.
**Why it happens:** Python's contextvars module isolates context between sync and async execution contexts. FastAPI/Starlette's `BaseHTTPMiddleware` runs the `dispatch` method in a way that may cross context boundaries.
**How to avoid:**
1. Use async middleware consistently (all middleware async)
2. Alternatively, use a raw ASGI middleware instead of Starlette's `BaseHTTPMiddleware`
3. Test by logging in the middleware AND in the route handler and verifying the same request_id appears in both
**Warning signs:** Some log lines have request_id, others don't.

### Pitfall 4: Anthropic SDK Default Timeout Is 10 Minutes

**What goes wrong:** A Claude API call with a large max_tokens hangs for up to 10 minutes before timing out. During this time, the request that triggered it is also blocked.
**Why it happens:** The SDK defaults to 600-second timeout. For Phase 1 (client validation), this is fine. But in Phase 6 (conversational agent), a hung Claude call blocks the Slack response.
**How to avoid:**
1. Set explicit timeout: `AsyncAnthropic(timeout=60.0)` (60 seconds is plenty for pipeline queries)
2. For Phase 1 testing, the default is acceptable
**Warning signs:** Slack shows "Atlas didn't respond in time" when Claude is slow.

### Pitfall 5: slack-bolt AsyncApp Needs SLACK_SIGNING_SECRET, Not SLACK_BOT_TOKEN Alone

**What goes wrong:** You initialize slack-bolt with the bot token but forget the signing secret. The app starts but rejects all incoming events because it can't verify request signatures.
**Why it happens:** slack-bolt requires `signing_secret` for request verification (separate from the bot token used for API calls). These are from different places in the Slack app configuration.
**How to avoid:**
1. Set both `SLACK_BOT_TOKEN` and `SLACK_SIGNING_SECRET` in Railway env vars
2. Signing secret is found in Slack app settings > Basic Information > App Credentials
3. pydantic-settings validates both are present at startup
**Warning signs:** All Slack events return 401 or are silently dropped.

### Pitfall 6: httpx AsyncClient Timeout Defaults May Be Too Short

**What goes wrong:** httpx.AsyncClient defaults to a 5-second timeout. GHL API calls sometimes take 3-8 seconds under load. Timeout errors during audit scans.
**Why it happens:** httpx uses conservative defaults. GHL is not the fastest API.
**How to avoid:** Set `httpx.AsyncClient(timeout=30.0)` explicitly in lifespan.
**Warning signs:** Intermittent `httpx.ReadTimeout` errors in logs.

## Code Examples

### Health Endpoint

```python
# app/main.py
from datetime import datetime, timezone

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "atlas",
        "version": "0.1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
```

### GHL Client with Retry (tenacity)

```python
# app/core/clients/ghl.py
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx
import structlog

logger = structlog.get_logger()

class GHLClient:
    def __init__(self, http_client: httpx.AsyncClient, api_key: str, location_id: str):
        self.http = http_client
        self.api_key = api_key
        self.location_id = location_id
        self.base_url = "https://services.leadconnectorhq.com"

    @property
    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Version": "2021-07-28",
            "Content-Type": "application/json",
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
        before_sleep=lambda retry_state: logger.warning(
            "ghl_api_retry",
            attempt=retry_state.attempt_number,
            wait=retry_state.next_action.sleep,
        ),
    )
    async def get_opportunity(self, opp_id: str) -> dict:
        resp = await self.http.get(
            f"{self.base_url}/opportunities/{opp_id}",
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json().get("opportunity", resp.json())
```
Source: [tenacity docs](https://tenacity.readthedocs.io/), [GHL API docs](https://marketplace.gohighlevel.com/docs)

### Claude Client (AsyncAnthropic)

```python
# app/core/clients/claude.py
from anthropic import AsyncAnthropic
import structlog

logger = structlog.get_logger()

class ClaudeClient:
    def __init__(self, client: AsyncAnthropic):
        self.client = client
        self.model = "claude-opus-4-6"

    async def ask(self, prompt: str, system: str | None = None, max_tokens: int = 1024) -> str:
        """Send a prompt to Claude and return the text response."""
        messages = [{"role": "user", "content": prompt}]
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        logger.info("claude_request", model=self.model, prompt_length=len(prompt))
        response = await self.client.messages.create(**kwargs)
        text = response.content[0].text
        logger.info(
            "claude_response",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        return text
```
Source: [Anthropic Python SDK docs](https://platform.claude.com/docs/en/api/sdks/python)

### Pydantic Settings Configuration

```python
# app/core/config.py
from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    # App
    app_name: str = "atlas"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    log_json_format: bool = True  # False for local dev

    # Database
    database_path: str = "/app/data/atlas.db"  # Railway volume mount

    # GHL
    ghl_api_key: str
    ghl_location_id: str = "l39XXt9HcdLTsuqTind6"
    ghl_pipeline_id: str = "V6mwUqamI0tGUm1GDvKD"

    # Calendly
    calendly_api_key: str
    calendly_webhook_secret: str = ""  # Optional until Phase 2

    # Slack
    slack_bot_token: str
    slack_signing_secret: str
    slack_webhook_url: str = ""  # Incoming webhook for simple notifications

    # Anthropic
    anthropic_api_key: str

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

@lru_cache
def get_settings() -> Settings:
    return Settings()
```
Source: [pydantic-settings docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)

### SQLite Initial Schema

```sql
-- migrations/001_initial.sql

-- Enable WAL mode for concurrent read/write
PRAGMA journal_mode=WAL;

-- Dead Letter Queue: failed webhook payloads for investigation and replay
CREATE TABLE IF NOT EXISTS dead_letter_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,           -- 'calendly.invitee.canceled', etc.
    payload TEXT NOT NULL,              -- Full JSON payload
    error_message TEXT NOT NULL,        -- What went wrong
    error_context TEXT,                 -- Additional context (stack trace, etc.)
    retry_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',      -- pending, retrying, resolved, abandoned
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Audit Snapshots: daily audit results for trend tracking
CREATE TABLE IF NOT EXISTS audit_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,             -- YYYY-MM-DD
    run_type TEXT DEFAULT 'scheduled',  -- scheduled, manual
    total_opportunities INTEGER NOT NULL,
    total_issues INTEGER NOT NULL,
    issues_by_type TEXT NOT NULL,       -- JSON: {"missing_fields": 5, "stale": 3, "overdue": 1}
    full_results TEXT NOT NULL,         -- Full JSON audit results for comparison
    created_at TEXT DEFAULT (datetime('now'))
);

-- Interaction Log: every human interaction with Atlas
CREATE TABLE IF NOT EXISTS interaction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    interaction_type TEXT NOT NULL,     -- suggestion, approval, rejection, query, auto_fix
    user_id TEXT NOT NULL,              -- Slack user ID
    channel_id TEXT,                    -- Slack channel
    opportunity_id TEXT,                -- GHL opportunity ID (if applicable)
    field_name TEXT,                    -- Which field was affected
    old_value TEXT,                     -- Before value
    new_value TEXT,                     -- After value
    context TEXT,                       -- Full context JSON
    created_at TEXT DEFAULT (datetime('now'))
);

-- Idempotency Keys: prevent duplicate webhook processing
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,               -- Calendly event URI or delivery ID
    event_type TEXT NOT NULL,
    processed_at TEXT DEFAULT (datetime('now')),
    result TEXT                         -- 'success', 'ignored', 'error'
);

-- Index for DLQ status queries
CREATE INDEX IF NOT EXISTS idx_dlq_status ON dead_letter_queue(status);

-- Index for audit date range queries
CREATE INDEX IF NOT EXISTS idx_audit_date ON audit_snapshots(run_date);

-- Index for interaction lookups by opportunity
CREATE INDEX IF NOT EXISTS idx_interaction_opp ON interaction_log(opportunity_id);

-- Index for idempotency TTL cleanup
CREATE INDEX IF NOT EXISTS idx_idempotency_time ON idempotency_keys(processed_at);
```

### Railway Deployment Configuration

```toml
# railway.toml
[build]
builder = "NIXPACKS"

[deploy]
startCommand = "python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1"
healthcheckPath = "/health"
healthcheckTimeout = 10
```

**Railway volume setup (via Railway dashboard or CLI):**
- Mount path: `/app/data`
- This persists SQLite across deployments

**Required Railway environment variables:**
```
GHL_API_KEY=pit-c104e9dd-ab1c-40a6-9b8f-0b04c62f0948
GHL_LOCATION_ID=l39XXt9HcdLTsuqTind6
CALENDLY_API_KEY=<calendly-pat>
SLACK_BOT_TOKEN=xoxb-7689063398230-10645725563489-Y0PJEctG35fHuog76U6ALH4F
SLACK_SIGNING_SECRET=<from-slack-app-basic-info>
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/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
ANTHROPIC_API_KEY=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
DATABASE_PATH=/app/data/atlas.db
LOG_JSON_FORMAT=true
LOG_LEVEL=INFO
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `@app.on_event("startup")` | `lifespan` context manager | FastAPI 0.109+ (late 2023) | Must use lifespan for startup/shutdown. on_event is deprecated. |
| APScheduler 3.x | APScheduler 3.11.x (3.x line maintained) | Ongoing | 4.x is still alpha (4.0.0a6). Stay on 3.x. |
| slack-sdk + manual Events API | slack-bolt | 2020+ | slack-bolt is the official recommended framework. Handles verification, middleware, event dispatch. |
| `requests` for HTTP | httpx (async) | 2023+ | httpx is the standard async HTTP client for FastAPI apps. |
| `logging.basicConfig` | structlog + stdlib integration | Mature | structlog wraps stdlib, captures uvicorn/httpx logs, produces JSON. |
| Dockerfile mandatory on Railway | Nixpacks auto-detection | 2023+ | Nixpacks detects Python, installs deps, sets start command. Dockerfile optional. |
| Railway PostgreSQL only | Railway volumes for SQLite | 2024+ | Volumes allow persistent file storage. SQLite viable for lightweight use cases. |

**Deprecated/outdated:**
- `@app.on_event("startup")`/`@app.on_event("shutdown")`: Use `lifespan` parameter instead
- APScheduler 4.x: Still alpha, do not use in production
- `slackeventsapi` (python-slack-events-api): Deprecated in favor of slack-bolt. Flask-only, no async.

## Open Questions

1. **SLACK_SIGNING_SECRET location**
   - What we know: slack-bolt requires it for request verification. It's in Slack app settings > Basic Information > App Credentials.
   - What's unclear: The user provided SLACK_BOT_TOKEN and SLACK_WEBHOOK_URL but not the signing secret. Need to look it up.
   - Recommendation: During Plan 01-01, include a task to retrieve the signing secret from Slack app settings and add it to Railway env vars.

2. **Slack Events API Request URL timing**
   - What we know: Slack sends a URL verification challenge when you set the Request URL. The endpoint must be live and responding.
   - What's unclear: Whether to configure the Request URL during Phase 1 or wait until Phase 6 (conversational agent).
   - Recommendation: Configure it in Phase 1. The /slack/events endpoint with basic `app_mention` and `message` handlers proves the Events API works (success criterion 3). Full conversational logic comes in Phase 6.

3. **Railway volume size for SQLite**
   - What we know: Hobby plan gets 5GB. Atlas's SQLite will be tiny (< 100MB even with months of data).
   - What's unclear: Whether the free/trial plan has volume support.
   - Recommendation: Hobby plan is sufficient. Volume cost is minimal (billed per GB used).

4. **Calendly API key scope**
   - What we know: CALENDLY_API_KEY is a Personal Access Token. It's listed in the additional_context.
   - What's unclear: Whether the existing PAT has organization scope (needed for webhook subscriptions in Phase 2).
   - Recommendation: Verify PAT scope during Phase 1 client testing. If insufficient, create a new PAT with org scope.

## Sources

### Primary (HIGH confidence)
- [FastAPI Lifespan Events - official docs](https://fastapi.tiangolo.com/advanced/events/) - Lifespan pattern, resource management
- [Anthropic Python SDK - official docs](https://platform.claude.com/docs/en/api/sdks/python) - AsyncAnthropic, retries, timeouts, tool use
- [slack-bolt Python FastAPI example - official repo](https://github.com/slackapi/bolt-python/blob/main/examples/fastapi/async_app.py) - AsyncApp + FastAPI adapter
- [Slack Events API url_verification - official docs](https://docs.slack.dev/reference/events/url_verification/) - Challenge/response protocol
- [structlog contextvars - official docs](https://www.structlog.org/en/stable/contextvars.html) - Request-scoped context, merge_contextvars processor
- [Railway Volumes reference - official docs](https://docs.railway.com/reference/volumes) - Mount paths, pricing, limitations
- [Railway FastAPI guide - official docs](https://docs.railway.com/guides/fastapi) - Deployment config, start command
- [Railway SQLite with volumes - community Q&A](https://station.railway.com/questions/how-do-i-use-volumes-to-make-a-sqlite-da-34ea0372) - Mount to /app/data, Nixpacks /app root
- [aiosqlite - official GitHub](https://github.com/omnilib/aiosqlite) - Async wrapper API, connection management

### Secondary (MEDIUM confidence)
- [FastAPI + structlog integration guide](https://wazaari.dev/blog/fastapi-structlog-integration) - Full middleware setup, dual logger pattern
- [asgi-correlation-id - GitHub](https://github.com/snok/asgi-correlation-id) - Correlation ID middleware for ASGI
- [FastAPI Lifespan Explained - Medium (Jan 2026)](https://medium.com/algomart/fastapi-lifespan-explained-the-right-way-to-handle-startup-and-shutdown-logic-f825f38dd304) - httpx in lifespan
- [FastAPI + structlog logging gist](https://gist.github.com/nymous/f138c7f06062b7c43c060bf03759c29e) - Complete logging setup reference

### Tertiary (LOW confidence)
- Railway volume persistence across deployments -- verified via community Q&A but not tested firsthand

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All libraries are official SDKs or ecosystem standards, verified via PyPI and official docs
- Architecture: HIGH - Patterns from FastAPI official docs, prior ARCHITECTURE.md research, and verified examples
- Pitfalls: HIGH - Railway volume behavior from official docs + community Q&A; structlog contextvars from official docs; slack-bolt requirements from official repo
- SQLite schema: MEDIUM - Schema design is reasonable but may need adjustment during implementation
- Anthropic SDK version: MEDIUM - Could not pin exact latest; >=0.84.0 is a reasonable floor

**Research date:** 2026-03-05
**Valid until:** 2026-04-05 (30 days -- stack is stable, no expected breaking changes)
