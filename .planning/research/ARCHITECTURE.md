# Architecture Patterns

**Domain:** CRM Pipeline Intelligence Agent (webhook handler + scheduled audit + notifications)
**Researched:** 2026-03-04
**Overall confidence:** HIGH

## Recommended Architecture

Atlas is a single-process Python/FastAPI service deployed on Railway. It follows a **modular monolith** pattern: a shared core provides API clients, configuration, and logging, while each business capability (event handling, auditing, future modules) lives in its own self-contained module with routes, services, and schemas.

```
                 ┌────────────────────────────────────────────────┐
                 │                   Railway                      │
                 │  ┌──────────────────────────────────────────┐  │
                 │  │           FastAPI Application             │  │
                 │  │                                           │  │
  Calendly ──────┼──┤  POST /webhooks/calendly ──► EventHandler │  │
  webhooks       │  │                              module       │  │
                 │  │                                │          │  │
                 │  │  APScheduler (8AM EST) ──► AuditRunner    │  │
                 │  │  POST /audit/run ────────► module         │  │
                 │  │                              │            │  │
                 │  │              ┌────────────────┘            │  │
                 │  │              ▼                             │  │
                 │  │  ┌─────────────────────┐                  │  │
                 │  │  │     Core Layer       │                  │  │
                 │  │  │  ┌───────────────┐   │                  │  │
                 │  │  │  │  GHL Client   │   │                  │  │
                 │  │  │  │  Slack Client  │───┼──► Slack        │  │
                 │  │  │  │  Calendly Cl.  │   │  #sales-pipeline│  │
                 │  │  │  │  Config / Env  │   │                  │  │
                 │  │  │  │  Logging       │   │                  │  │
                 │  │  │  └───────────────┘   │                  │  │
                 │  │  └─────────────────────┘                  │  │
                 │  └──────────────────────────────────────────┘  │
                 └────────────────────────────────────────────────┘
                                       │
                                       ▼
                              GoHighLevel REST API
```

**Single-process rationale:** Railway runs one container instance. APScheduler uses `AsyncIOScheduler` sharing FastAPI's event loop. No multi-worker complexity, no duplicate scheduler instances, no need for distributed job storage. This is the simplest correct architecture for this workload.

### Component Boundaries

| Component | Responsibility | Communicates With |
|-----------|---------------|-------------------|
| **FastAPI app (main.py)** | Lifespan management, router mounting, scheduler startup | All modules (imports routers), Core (config) |
| **Core: GHL Client** | All GoHighLevel API calls (search, get, update opps/contacts/tasks), rate limiting, retry | Event Handler, Audit Runner |
| **Core: Slack Client** | Format and send Slack messages (webhook outcomes, audit digest, errors) | Event Handler, Audit Runner |
| **Core: Calendly Client** | Webhook signature verification, subscription management | Event Handler |
| **Core: Config** | Load env vars, expose typed settings (Pydantic Settings) | Everything |
| **Core: Logging** | Structured JSON logging (structlog) | Everything |
| **Module: Event Handler** | Parse Calendly webhooks, match to GHL opp, write field updates, notify Slack | Core (GHL, Slack, Calendly, Config) |
| **Module: Audit Runner** | Scan active opps, check field/stale/task rules, build Slack digest | Core (GHL, Slack, Config) |

### Data Flow

**Webhook Flow (Event Handler):**
```
Calendly POST /webhooks/calendly
  │
  ▼
1. Verify webhook signature (Core: Calendly Client)
  │
  ▼
2. Parse payload → extract event_type, event_name, invitee_email, event_uri
  │
  ▼
3. Filter: only "Discovery" or "Onboarding" events → else discard + log
  │
  ▼
4. Match to GHL opportunity:
   a. Primary: search by Calendly Event ID custom field (Core: GHL Client)
   b. Fallback: search contacts by email → find opp by contact + stage + type
  │
  ▼
5. Determine field updates (Discovery No-Show, Onboarding Cancel, etc.)
  │
  ▼
6. Write custom fields to GHL opportunity (Core: GHL Client)
  │  (idempotent — same values = no-op)
  ▼
7. Send Slack notification with outcome (Core: Slack Client)
  │
  ▼
8. Return 200 to Calendly (always, regardless of outcome)
```

**Audit Flow (Audit Runner):**
```
APScheduler cron trigger (8 AM EST) OR POST /audit/run
  │
  ▼
1. Fetch all opportunities in pipeline (paginated, Core: GHL Client)
  │
  ▼
2. Filter out terminal stages (Close Lost, Declined, Churned)
  │
  ▼
3. For each opportunity:
   a. Check required fields for current stage (field matrix lookup)
   b. Check staleness (days in stage vs. threshold)
   c. Fetch contact → check Lead Source, email
   d. Fetch tasks → check overdue (completed=false, due > 24h ago)
  │
  ▼
4. Group issues by assigned user
  │
  ▼
5. Build Slack digest: 3 sections (Missing Fields, Stale Deals, Overdue Tasks)
   OR "All clear" if zero issues
  │
  ▼
6. Send to #sales-pipeline (Core: Slack Client)
```

## Package/Module Structure

```
atlas/
├── main.py                     # FastAPI app, lifespan, mount routers, start scheduler
├── core/
│   ├── __init__.py
│   ├── config.py               # Pydantic Settings (env vars, typed config)
│   ├── logging.py              # structlog setup, JSON formatter
│   └── clients/
│       ├── __init__.py
│       ├── ghl.py              # GHLClient: search/get/update opps, contacts, tasks
│       ├── slack.py            # SlackClient: send webhook messages
│       └── calendly.py         # CalendlyClient: verify signatures, manage subscriptions
├── modules/
│   ├── __init__.py
│   ├── events/                 # Calendly webhook event handler
│   │   ├── __init__.py
│   │   ├── router.py           # POST /webhooks/calendly
│   │   ├── service.py          # parse, match, update, notify logic
│   │   ├── schemas.py          # Pydantic models for Calendly payloads
│   │   └── matching.py         # Opp-matching strategies (Event ID, email fallback)
│   └── audit/                  # Daily pipeline audit
│       ├── __init__.py
│       ├── router.py           # POST /audit/run (manual trigger)
│       ├── service.py          # scan, check rules, group, build digest
│       ├── schemas.py          # Pydantic models for audit results
│       ├── rules.py            # Stage-required fields matrix, stale thresholds
│       └── formatters.py       # Slack message formatting for digest
├── tests/
│   ├── conftest.py             # Shared fixtures (mock GHL, mock Slack)
│   ├── test_events/
│   │   ├── test_router.py
│   │   ├── test_service.py
│   │   └── test_matching.py
│   └── test_audit/
│       ├── test_service.py
│       └── test_rules.py
├── pyproject.toml              # Dependencies, project metadata
├── Dockerfile                  # Railway deployment
├── .env.example                # Template for env vars
└── railway.json                # Railway config (if needed)
```

### Why This Structure

**`core/` is stable infrastructure.** API clients, config, and logging change rarely. They have no business logic — just "call this API" and "send this message." Every module imports from core; core imports from nothing else.

**`modules/` are pluggable business capabilities.** Each module has its own router (HTTP endpoints), service (business logic), and schemas (data shapes). Modules depend on core but never on each other. This is the key extensibility principle.

**Future modules slot in cleanly:**
```
modules/
├── events/          # ✅ Phase 1: Calendly webhook handler
├── audit/           # ✅ Phase 1: Daily pipeline audit
├── intake/          # 🔮 Future: Email parsing → contact creation → nurture enrollment
├── reconciliation/  # 🔮 Future: Calendly ↔ GHL drift detection
└── zap_absorber/    # 🔮 Future: Replace Zapier automations with native handlers
```

Each future module follows the same pattern: `router.py`, `service.py`, `schemas.py`, plus any module-specific files. Registration is one line in `main.py`:

```python
# main.py
from modules.events.router import router as events_router
from modules.audit.router import router as audit_router
# Future: from modules.intake.router import router as intake_router

app.include_router(events_router, prefix="/webhooks", tags=["events"])
app.include_router(audit_router, prefix="/audit", tags=["audit"])
# Future: app.include_router(intake_router, prefix="/intake", tags=["intake"])
```

## Patterns to Follow

### Pattern 1: Service Layer (Business Logic Separated from Routes)

**What:** Routes are thin — they parse HTTP requests and call service functions. All business logic lives in `service.py`.

**When:** Always. Every module follows this pattern.

**Why:** Routes become trivially testable (just HTTP in/out). Services are testable with mocked clients (no HTTP layer). Business logic changes don't touch routing code.

**Example:**
```python
# modules/events/router.py
from fastapi import APIRouter, Request, Depends
from core.clients.ghl import GHLClient
from core.clients.slack import SlackClient
from .service import handle_calendly_event

router = APIRouter()

@router.post("/calendly")
async def calendly_webhook(request: Request, ghl: GHLClient = Depends(), slack: SlackClient = Depends()):
    payload = await request.json()
    await handle_calendly_event(payload, ghl=ghl, slack=slack)
    return {"status": "ok"}  # Always 200 to Calendly

# modules/events/service.py
async def handle_calendly_event(payload: dict, *, ghl: GHLClient, slack: SlackClient):
    event = parse_calendly_payload(payload)
    if not is_relevant_event(event):
        logger.info("skipping_irrelevant_event", event_name=event.name)
        return
    opp = await match_opportunity(event, ghl=ghl)
    if not opp:
        await slack.send_match_failure(event)
        return
    updates = determine_field_updates(event)
    await ghl.update_opportunity(opp.id, custom_fields=updates)
    await slack.send_event_success(event, opp)
```

### Pattern 2: Dependency Injection for Clients

**What:** Use FastAPI's `Depends()` to inject API clients into route handlers, which pass them to services.

**When:** All client usage across all modules.

**Why:** Tests swap real clients for mocks via dependency overrides. No global state. Each client can manage its own connection lifecycle.

**Example:**
```python
# core/clients/ghl.py
from core.config import Settings

class GHLClient:
    def __init__(self, settings: Settings = Depends(get_settings)):
        self.api_key = settings.ghl_api_key
        self.base_url = "https://services.leadconnectorhq.com"
        self.session: httpx.AsyncClient | None = None

    async def search_opportunities(self, **filters) -> list[dict]:
        # Rate-limited, retried API call
        ...
```

### Pattern 3: APScheduler via Lifespan

**What:** Start APScheduler in FastAPI's lifespan context manager. Register cron jobs at startup, shut down cleanly on exit.

**When:** Audit runner and any future scheduled modules.

**Why:** Single-process Railway deployment means AsyncIOScheduler shares FastAPI's event loop. No separate worker process needed. Lifespan ensures clean startup/shutdown.

**Example:**
```python
# main.py
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_daily_audit,
        CronTrigger(hour=8, minute=0, timezone="US/Eastern"),
        id="daily_audit",
        replace_existing=True,
    )
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)
```

### Pattern 4: Idempotent Webhook Processing

**What:** Webhook handlers must produce the same result when called multiple times with the same event.

**When:** All webhook endpoints (Calendly sends retries on timeout).

**Why:** Calendly retries on non-200 responses and may occasionally double-deliver. Writing the same field values is a no-op in GHL, but Slack notifications should not duplicate.

**Implementation:** Log the Calendly Event URI as a processed key. On duplicate, log and skip notification but still return 200.

### Pattern 5: Always-200 Webhook Response

**What:** Return HTTP 200 to Calendly regardless of internal outcome (match failure, GHL API error, etc.).

**When:** All webhook endpoints receiving third-party callbacks.

**Why:** Returning errors causes Calendly to retry, compounding the problem. Internal failures are reported via Slack notifications and structured logging, not HTTP status codes.

## Anti-Patterns to Avoid

### Anti-Pattern 1: Business Logic in Routes

**What:** Putting matching logic, field update calculations, or audit rules directly in route handler functions.

**Why bad:** Routes become untestable without spinning up full HTTP stack. Logic is locked behind the HTTP layer. Impossible to reuse (e.g., audit service calling from cron vs. manual trigger).

**Instead:** Routes call service functions. Services are plain async functions that accept typed parameters.

### Anti-Pattern 2: Cross-Module Imports

**What:** Audit module importing from Events module, or future Intake module importing from Audit.

**Why bad:** Creates coupling that prevents independent evolution. Changing Events could break Audit. Modules become entangled, defeating the pluggable architecture.

**Instead:** Modules only import from `core/`. If two modules need shared logic, extract it to `core/`. If modules need to communicate, use events or a shared core service.

### Anti-Pattern 3: Global Mutable State for Clients

**What:** Creating a single global `ghl_client = GHLClient()` at module level and importing it everywhere.

**Why bad:** Impossible to mock in tests without monkeypatching. Config must be available at import time. Client lifecycle not tied to app lifecycle.

**Instead:** FastAPI dependency injection. Clients are instantiated per-request (or per-app via lifespan + `app.state`).

### Anti-Pattern 4: Multi-Worker APScheduler

**What:** Running Gunicorn with multiple Uvicorn workers while using APScheduler's AsyncIOScheduler.

**Why bad:** Each worker starts its own scheduler instance. The daily audit runs N times (once per worker), sending N Slack digests.

**Instead:** Railway single-process deployment with `uvicorn main:app`. One process = one scheduler. If scaling later, extract scheduler to a separate service or use a lock mechanism.

### Anti-Pattern 5: Synchronous HTTP Calls

**What:** Using `requests` library for GHL/Slack/Calendly API calls.

**Why bad:** Blocks the event loop. A slow GHL API response blocks all other requests (including incoming webhooks).

**Instead:** Use `httpx.AsyncClient` for all external API calls. FastAPI is async-first; honor that.

## Suggested Build Order

Build order follows dependency chains. Each phase produces a deployable, testable increment.

### Phase 1: Core Infrastructure (build first — everything depends on this)

1. **`core/config.py`** — Pydantic Settings loading env vars
2. **`core/logging.py`** — structlog with JSON output
3. **`main.py`** — bare FastAPI app with health check endpoint (`GET /health`)
4. **`Dockerfile` + `railway.json`** — deploy empty app to Railway, confirm it runs

*Deliverable: A running Railway service that responds to `GET /health`.*

### Phase 2: API Clients (build second — modules consume these)

5. **`core/clients/ghl.py`** — GHLClient with search, get, update opps/contacts/tasks. Rate limiting + retry with exponential backoff.
6. **`core/clients/slack.py`** — SlackClient wrapping incoming webhook. Message formatting helpers.
7. **`core/clients/calendly.py`** — CalendlyClient for webhook signature verification + subscription management.

*Deliverable: Clients are independently testable against real APIs.*

### Phase 3: Event Handler Module (build third — the primary value)

8. **`modules/events/schemas.py`** — Pydantic models for Calendly `invitee.canceled` and `invitee.no_show` payloads
9. **`modules/events/matching.py`** — Opportunity matching (Event ID primary, email fallback)
10. **`modules/events/service.py`** — Parse → filter → match → update → notify pipeline
11. **`modules/events/router.py`** — `POST /webhooks/calendly` endpoint
12. **Calendly webhook subscription** — Create org-scoped subscriptions via API
13. **End-to-end test** with real Calendly test event

*Deliverable: Calendly webhook fires, GHL fields update, Slack notification sent.*

### Phase 4: Audit Module (build fourth — depends on GHL client maturity)

14. **`modules/audit/rules.py`** — Stage-required fields matrix, stale thresholds (from TECHNICAL_REFERENCE.md)
15. **`modules/audit/service.py`** — Scan all opps, apply rules, group by user
16. **`modules/audit/formatters.py`** — Slack digest formatting (3 sections, grouped by user)
17. **`modules/audit/router.py`** — `POST /audit/run` manual trigger
18. **APScheduler integration** — Wire cron job in `main.py` lifespan
19. **End-to-end test** — trigger audit, verify Slack digest

*Deliverable: Daily 8 AM audit runs, Slack digest lands in #sales-pipeline.*

### Phase 5: Hardening

20. Structured logging review (every operation has context)
21. Error handling audit (no unhandled exceptions crash the process)
22. Idempotency verification (duplicate webhooks produce no side effects)
23. Rate limit testing (GHL API limits honored under load)

### Build Order Rationale

- **Core before modules:** Modules import from core. Building core first means modules have stable dependencies from day one.
- **Event handler before audit:** Event handler is the primary value ("if nothing else works, this must"). Audit is valuable but secondary. Event handler also exercises the GHL client in write mode, validating the client before audit reads from it.
- **Clients before modules:** Each client is independently testable. Verify GHL API access works before building logic that depends on it.
- **Hardening last:** Polish after correctness. Structured logging and error handling are important but don't block core functionality.

## Scalability Considerations

| Concern | Current (2 users, ~5 deals) | Growth (10 users, 50 deals) | At Scale (50 users, 500 deals) |
|---------|----------------------------|-----------------------------|---------------------------------|
| Webhook throughput | Single process handles easily | Still fine — webhooks are infrequent | Still fine — max ~10 events/day |
| Audit scan duration | Seconds | 10-30 seconds (API pagination) | Minutes — add batching, caching |
| GHL rate limits | No concern | Monitor rate limit headers | Implement request queuing |
| Scheduler | Single AsyncIOScheduler | Same — one cron job | Same — audit batching handles scale |
| Railway deployment | Single instance | Single instance | May need dedicated scheduler process |

Atlas's workload is fundamentally low-throughput: a handful of webhook events per day and one daily audit scan. The single-process architecture is not a compromise — it is the right choice for this scale. The modular structure means if a future module (lead intake) brings higher throughput, it can be extracted to its own service without touching the others.

## Sources

- [FastAPI Modular Monolith Starter Kit](https://github.com/arctikant/fastapi-modular-monolith-starter-kit) — Module boundary pattern, gateway/event architecture (HIGH confidence)
- [APScheduler Documentation](https://apscheduler.readthedocs.io/en/3.x/userguide.html) — AsyncIOScheduler, CronTrigger, job storage (HIGH confidence)
- [Sentry: Schedule Tasks with FastAPI](https://sentry.io/answers/schedule-tasks-with-fastapi/) — Lifespan integration pattern (HIGH confidence)
- [FastAPI Best Practices for Production 2026](https://fastlaunchapi.dev/blog/fastapi-best-practices-production-2026) — General production patterns (MEDIUM confidence)
- [Layered Architecture & DI in FastAPI](https://dev.to/markoulis/layered-architecture-dependency-injection-a-recipe-for-clean-and-testable-fastapi-code-3ioo) — Service layer pattern (MEDIUM confidence)
- [Building Production-Ready FastAPI with Service Layer Architecture](https://medium.com/@abhinav.dobhal/building-production-ready-fastapi-applications-with-service-layer-architecture-in-2025-f3af8a6ac563) — Service layer rationale (MEDIUM confidence)
- [APScheduler Single Instance Discussion](https://github.com/agronholm/apscheduler/discussions/913) — Multi-worker scheduler pitfalls (HIGH confidence)
