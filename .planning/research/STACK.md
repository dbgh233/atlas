# Technology Stack

**Project:** Atlas - Pipeline Intelligence Agent
**Researched:** 2026-03-04
**Overall Confidence:** HIGH

## Recommended Stack

### Core Framework

| Technology | Version | Purpose | Confidence | Why |
|------------|---------|---------|------------|-----|
| **FastAPI** | `0.135.1` | Web framework (webhooks + API) | HIGH | Industry standard for Python async APIs. Native Pydantic integration for request/response validation. Built-in OpenAPI docs. SSE support added recently. Verified: latest release Mar 1, 2026. |
| **Uvicorn** | `0.41.0` | ASGI server | HIGH | Default production server for FastAPI. Lightweight, fast, well-maintained. Use with `--host 0.0.0.0 --port $PORT` on Railway. Verified: released Feb 16, 2026. |
| **Python** | `3.12` | Runtime | HIGH | Stable, well-supported on Railway via Nixpacks. 3.13 is available but 3.12 has broader library compatibility. 3.14 support landing in FastAPI/Pydantic but too early for production. |

### Data Validation & Configuration

| Technology | Version | Purpose | Confidence | Why |
|------------|---------|---------|------------|-----|
| **Pydantic** | `2.12.5` | Data models + validation | HIGH | Ships with FastAPI. Validates webhook payloads, API responses, config. V2 is mature and fast (Rust core). 2.13 in beta -- stay on 2.12.x stable. |
| **pydantic-settings** | `2.13.1` | Environment config | HIGH | Type-safe env var loading with `BaseSettings`. Validates all config at startup (fail fast). Supports `.env` files, prefixes, nested models. FastAPI's recommended approach. |

### HTTP Client

| Technology | Version | Purpose | Confidence | Why |
|------------|---------|---------|------------|-----|
| **httpx** | `0.28.1` | Async HTTP client (GHL, Calendly, Slack APIs) | HIGH | Native async/await support -- matches FastAPI's async model. HTTP/2 support. Connection pooling via `AsyncClient`. Drop-in familiar API (requests-like). Up to 7x faster than `requests` for concurrent calls. |

### Scheduling

| Technology | Version | Purpose | Confidence | Why |
|------------|---------|---------|------------|-----|
| **APScheduler** | `3.11.0` | Cron jobs (daily audit) | HIGH | Use **3.x stable**, NOT 4.x alpha. 3.11.0 released Dec 2025, production-proven. `AsyncIOScheduler` integrates with FastAPI's event loop. `CronTrigger` for "8 AM EST daily". Simple API, no external broker needed. |

### Retry & Resilience

| Technology | Version | Purpose | Confidence | Why |
|------------|---------|---------|------------|-----|
| **tenacity** | `9.1.4` | Retry with backoff (API calls) | HIGH | De facto standard for Python retry logic. Decorator-based (`@retry`). Supports exponential backoff, jitter, per-exception retry. Works with async functions natively. Released Feb 7, 2026. |

### Logging

| Technology | Version | Purpose | Confidence | Why |
|------------|---------|---------|------------|-----|
| **structlog** | `25.5.0` | Structured JSON logging | HIGH | Produces JSON logs that Railway's log viewer parses natively. Processor pipeline architecture (add context, timestamps, format). Dev mode: pretty console. Prod mode: JSON with `orjson`. Bind context per-request (merchant_id, deal_id). |
| **orjson** | `3.x` | Fast JSON serialization (for structlog) | MEDIUM | ~10x faster than stdlib `json`. structlog's `JSONRenderer` uses it when available. Not strictly required but recommended for production logging perf. |

### Testing

| Technology | Version | Purpose | Confidence | Why |
|------------|---------|---------|------------|-----|
| **pytest** | `8.x` | Test runner | HIGH | Standard Python test framework. Rich plugin ecosystem. |
| **pytest-asyncio** | `0.24.x` | Async test support | HIGH | Required for testing async FastAPI endpoints and httpx calls. |
| **httpx** (test client) | `0.28.1` | Integration tests | HIGH | FastAPI's recommended test client via `httpx.AsyncClient(transport=ASGITransport(app))`. Same library used for production HTTP calls -- one less dependency. |
| **respx** | `0.22.x` | HTTP mocking | MEDIUM | Mock httpx requests in tests. Cleaner than `unittest.mock` for HTTP. Alternative: `pytest-httpx`. |
| **time-machine** | `2.x` | Time mocking | MEDIUM | For testing scheduled jobs -- freeze time to 8 AM EST, verify audit runs. Better API than `freezegun`. |

### Security

| Technology | Version | Purpose | Confidence | Why |
|------------|---------|---------|------------|-----|
| **Python stdlib `hmac`** | (built-in) | Calendly webhook signature verification | HIGH | HMAC-SHA256 with `hmac.compare_digest()` for timing-attack-safe comparison. No external dependency needed. Calendly signs payloads with SHA256. |

### Infrastructure

| Technology | Version | Purpose | Confidence | Why |
|------------|---------|---------|------------|-----|
| **Railway** | N/A | Hosting platform | HIGH | Auto-deploys from GitHub. Nixpacks auto-detects Python + requirements. Env var management built in. Supports cron-like scheduling. $5/mo hobby plan sufficient for this workload. |
| **Docker** (optional) | N/A | Containerization | MEDIUM | Railway uses Nixpacks by default (zero-config). Only add Dockerfile if you need precise control (e.g., multi-stage build, system deps). Start without it. |
| **GitHub Actions** | N/A | CI (lint + test) | MEDIUM | Run `pytest` + `ruff` on PRs. Railway handles CD. Keep CI simple -- no need for complex pipelines on a single-service project. |

### Code Quality

| Technology | Version | Purpose | Confidence | Why |
|------------|---------|---------|------------|-----|
| **ruff** | `0.9.x` | Linter + formatter | HIGH | Replaces flake8, isort, black in a single tool. 10-100x faster (Rust). Industry standard for new Python projects in 2025/2026. |
| **mypy** | `1.x` | Type checking | MEDIUM | Catches type errors statically. Pairs well with Pydantic models. Optional but recommended -- start strict, stay strict. |

## Supporting Libraries (As-Needed)

| Library | Version | Purpose | When to Add |
|---------|---------|---------|-------------|
| **slack-sdk** | `3.x` | Slack API (notifications) | Phase 1 -- for posting daily audit digests. Use `WebClient.chat_postMessage`. Async support via `AsyncWebClient`. |
| **python-dotenv** | `1.x` | Local `.env` loading | Development only. pydantic-settings handles `.env` natively, but dotenv is useful if you want explicit control. |
| **sentry-sdk** | `2.x` | Error tracking | Post-MVP. Add when you want alerting on unhandled exceptions in production. FastAPI integration built in. |

## Installation

```bash
# Core dependencies
pip install "fastapi[standard]" uvicorn httpx pydantic-settings apscheduler tenacity structlog orjson

# Slack integration
pip install slack-sdk

# Dev dependencies
pip install pytest pytest-asyncio respx time-machine ruff mypy
```

Or with a `pyproject.toml` (preferred over `requirements.txt` for new projects):

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
    "slack-sdk>=3.33.0",
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

## Railway Deployment

```toml
# railway.toml
[build]
builder = "NIXPACKS"

[deploy]
startCommand = "uvicorn app.main:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/health"
healthcheckTimeout = 10
```

Bind to `$PORT` (Railway injects this). No Dockerfile needed for initial deployment -- Nixpacks handles Python auto-detection.

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| **Framework** | FastAPI | Flask | Flask lacks native async, no built-in validation, no auto-docs. FastAPI is the clear winner for async webhook services. |
| **Framework** | FastAPI | Litestar | Litestar is capable but smaller ecosystem, fewer tutorials, less hiring familiarity. FastAPI has 5x the adoption. |
| **HTTP Client** | httpx | requests | `requests` is sync-only. Mixing sync HTTP in an async FastAPI app blocks the event loop. httpx does both. |
| **HTTP Client** | httpx | aiohttp | aiohttp is faster at extreme concurrency (10k+ concurrent) but has a less intuitive API. httpx is sufficient for API-to-API calls (tens, not thousands, of concurrent requests). |
| **Scheduler** | APScheduler 3.x | APScheduler 4.x | 4.x is alpha (4.0.0a6, Apr 2025). Complete rewrite with breaking API changes. Not production-ready. Use 3.11.x. |
| **Scheduler** | APScheduler | Celery Beat | Celery requires Redis/RabbitMQ broker. Massive overkill for one daily cron job. APScheduler runs in-process, zero infrastructure. |
| **Scheduler** | APScheduler | `schedule` | `schedule` is single-threaded, no async support, no persistence. Fine for scripts, not for a production service. |
| **Logging** | structlog | stdlib `logging` | stdlib logging produces flat strings. Structured JSON logs are essential for Railway's log viewer and any observability platform. structlog wraps stdlib cleanly. |
| **Logging** | structlog | loguru | loguru is popular but opinionated about replacing stdlib. structlog composes with stdlib, which matters when libraries (uvicorn, httpx) use stdlib logging. |
| **Retry** | tenacity | backoff | `backoff` is simpler but less flexible. tenacity supports per-exception strategies, async natively, and custom callbacks. Industry standard. |
| **Formatter** | ruff | black + flake8 + isort | Three tools doing what one tool does faster. ruff replaced all three in the Python ecosystem during 2024-2025. |
| **Settings** | pydantic-settings | python-decouple | pydantic-settings validates types at startup and integrates directly with FastAPI's dependency injection. python-decouple just reads strings. |
| **Container** | Nixpacks (Railway default) | Dockerfile | Start with Nixpacks for zero config. Only add Dockerfile if you hit Nixpacks limitations (custom system deps, multi-stage builds). |

## Anti-Recommendations (What NOT to Use)

| Technology | Why Not | What Instead |
|------------|---------|--------------|
| **Django** | Full MVC framework with ORM, admin, templates -- massive overkill for a webhook handler + cron service. No ORM needed (no database). | FastAPI |
| **Celery** | Requires Redis/RabbitMQ broker infrastructure. You have ONE scheduled job and webhook handlers. Celery is for distributed task queues at scale. | APScheduler (in-process) |
| **APScheduler 4.x** | Still in alpha (4.0.0a6). API completely rewritten. No migration path from 3.x job stores. Will break. | APScheduler 3.11.x |
| **requests** | Sync-only. Blocks FastAPI's async event loop. Forces `run_in_executor` hacks. | httpx |
| **aiohttp (as framework)** | Lower-level than FastAPI, no built-in validation, no OpenAPI generation. More boilerplate for the same result. | FastAPI |
| **SQLAlchemy / any ORM** | Atlas has no database. It reads/writes to GHL and Calendly APIs. Don't add a database unless you need state persistence (and even then, consider Railway's Redis or a simple JSON file first). | httpx to external APIs |
| **python-dotenv standalone** | pydantic-settings reads `.env` files natively. Adding python-dotenv is redundant unless you have a specific edge case. | pydantic-settings |
| **gunicorn** | Added complexity for multi-worker management. Uvicorn handles async workers natively. Only add gunicorn if you need pre-fork worker management at scale (you don't for this service). | Uvicorn standalone |

## Architecture Notes for Roadmap

### Key Technical Decisions

1. **No database.** Atlas is a stateless event processor. State lives in GHL (CRM fields) and Calendly (event data). If idempotency tracking is needed, use Railway's Redis add-on with TTL keys -- don't add a full database.

2. **Single process.** APScheduler runs in-process alongside FastAPI. No separate worker process needed. This simplifies deployment (one Railway service, one start command).

3. **httpx connection pooling.** Create a single `httpx.AsyncClient` at startup, reuse across requests. Don't create/destroy clients per-request (leaks connections, slow).

4. **Structured logging from day one.** Configure structlog before writing any business logic. Bind `merchant_id`, `deal_id`, `event_type` to every log line. This makes debugging in Railway's log viewer trivial.

5. **Webhook signature verification is non-negotiable.** Calendly signs with HMAC-SHA256. Verify before processing. Use `hmac.compare_digest()` (timing-safe). Raw request body for signature computation (not parsed JSON).

## Sources

- FastAPI v0.135.1: [PyPI](https://pypi.org/project/fastapi/) (verified Mar 4, 2026)
- Uvicorn v0.41.0: [PyPI](https://pypi.org/project/uvicorn/) (released Feb 16, 2026)
- Pydantic v2.12.5: [PyPI](https://pypi.org/project/pydantic/) (released Nov 26, 2025)
- pydantic-settings v2.13.1: [PyPI](https://pypi.org/project/pydantic-settings/) (released Feb 19, 2026)
- httpx v0.28.1: [PyPI](https://pypi.org/project/httpx/)
- APScheduler v3.11.2 stable: [PyPI](https://pypi.org/project/APScheduler/) (released Dec 22, 2025)
- APScheduler 4.0 alpha status: [GitHub Issue #465](https://github.com/agronholm/apscheduler/issues/465)
- tenacity v9.1.4: [PyPI](https://pypi.org/project/tenacity/) (released Feb 7, 2026)
- structlog v25.5.0: [PyPI](https://pypi.org/project/structlog/) (released Oct 27, 2025)
- Railway FastAPI guide: [Railway Docs](https://docs.railway.com/guides/fastapi)
- Calendly webhook signatures: [Calendly Developer Docs](https://developer.calendly.com/api-docs/4c305798a61d3-webhook-signatures)
- httpx vs requests comparison: [Oxylabs](https://oxylabs.io/blog/httpx-vs-requests-vs-aiohttp), [Speakeasy](https://www.speakeasy.com/blog/python-http-clients-requests-vs-httpx-vs-aiohttp)
- APScheduler vs alternatives: [Leapcell](https://leapcell.io/blog/scheduling-tasks-in-python-apscheduler-versus-schedule)
- FastAPI best practices 2026: [FastLaunchAPI](https://fastlaunchapi.dev/blog/fastapi-best-practices-production-2026)
- FastAPI webhook patterns: [OneUptime](https://oneuptime.com/blog/post/2026-01-25-webhook-handlers-python/view)
- structlog best practices: [structlog docs](https://www.structlog.org/en/stable/logging-best-practices.html)
