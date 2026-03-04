# Project Research Summary

**Project:** Atlas - Pipeline Intelligence Agent
**Domain:** CRM Pipeline Intelligence (webhook event processing + scheduled audit + Slack notifications)
**Researched:** 2026-03-04
**Confidence:** HIGH

## Executive Summary

Atlas is a lightweight, stateless event-processing service with two core jobs: (1) receive Calendly webhooks and write the right data to the right GHL opportunity, and (2) run a daily audit of the pipeline and deliver an actionable Slack digest. Experts in this domain build such services as modular monoliths on async Python frameworks (FastAPI + APScheduler), deployed on a single-process PaaS host (Railway), with no database — state lives entirely in the external APIs (GHL, Calendly). The architecture is intentionally simple; the value is in the business logic, not the infrastructure.

The recommended approach is: Python 3.12 + FastAPI + APScheduler (3.x stable) + httpx + structlog, deployed on Railway via Nixpacks. No database, no message broker, no separate worker process. Core infrastructure (config, logging, API clients) is built first and shared via dependency injection; business capability modules (events, audit) plug in without coupling to each other. This structure is proven, well-documented, and extensible to future modules without architectural rewrites.

The dominant risk is webhook-to-opportunity matching reliability — a race condition between Calendly firing and GHL having the Event ID field populated, compounded by non-unique email matching when a contact has multiple opportunities. This must be treated as the hardest engineering problem in Phase 1, not an afterthought. Secondary risks are silent data loss (incomplete audit due to GHL rate limits, or Calendly disabling a subscription after a prolonged outage) and notification fatigue if the audit digest degrades into recurring noise. All three are preventable with specific patterns documented in the research.

## Key Findings

### Recommended Stack

Atlas requires no database, no message broker, and no distributed infrastructure. The entire service is a single Python process. The stack is well-established for async webhook services in 2025-2026: FastAPI as the web framework (native async, Pydantic validation, OpenAPI docs out of the box), httpx for all outbound API calls (async, connection pooling, HTTP/2), APScheduler 3.x for the daily cron job (runs in-process alongside FastAPI's event loop), and structlog for JSON-structured logging that Railway's log viewer parses natively. Tenacity handles API retry logic with exponential backoff. pydantic-settings validates all environment variables at startup — fail fast if config is wrong.

**Core technologies:**
- **Python 3.12 + FastAPI 0.135.1**: Web framework — async-first, Pydantic native, OpenAPI auto-generated
- **APScheduler 3.11.x** (NOT 4.x alpha): Cron jobs — in-process, AsyncIOScheduler shares FastAPI event loop
- **httpx 0.28.1**: All outbound API calls — async, connection pooling, drop-in requests API
- **pydantic-settings 2.13.1**: Env var config — type-safe, validates at startup
- **tenacity 9.1.4**: Retry logic — exponential backoff, per-exception strategies, native async
- **structlog 25.5.0**: JSON logging — Railway-native, bind context per request
- **Railway + Nixpacks**: Hosting — zero-config Python auto-detection, $5/mo hobby plan sufficient
- **slack-sdk 3.x**: Slack notifications — AsyncWebClient for non-blocking sends

See `STACK.md` for full version pins, alternatives considered, and anti-recommendations.

### Expected Features

The MVP has two independent workstreams that share only the GHL API client. The webhook path (Calendly event → GHL field update) is the primary value; the audit path (daily scan → Slack digest) is the CEO's primary interface. Both must work for Atlas to deliver.

**Must have (table stakes):**
- Webhook ingestion with fast ACK (verify signature → return 200 immediately, never inline-process)
- Idempotent event processing (deduplication via event ID; Calendly retries for up to 72 hours)
- Event-to-opportunity matching (primary: Calendly Event ID custom field; fallback: email + appointment type + stage)
- Field mapping and write-back to GHL opportunity custom fields (`field_value` key, not `value`)
- Pipeline audit scan (active deals checked for missing required fields, stale deals, overdue tasks)
- Slack digest delivery (grouped by owner, 3 sections: missing fields / stale / overdue tasks)
- Error handling with dead letter queue (failed events captured with full context for replay)
- Structured logging (every event traceable from receipt through GHL write)
- Webhook signature verification (HMAC-SHA256 over raw request bytes)
- GHL API rate limit awareness (exponential backoff, 429 handling)

**Should have (differentiators):**
- Stage-aware field validation rules (what counts as "missing" depends on pipeline stage, not a flat checklist)
- Slack digest grouped by owner with action items (not "deals have problems" but "Drew: 3 deals need close dates")
- Webhook subscription health check on boot (detect and auto-recreate disabled Calendly subscriptions)
- Audit coverage tracking (report "47/52 opportunities audited" — never report "all clear" on partial data)
- Manual audit trigger endpoint (POST /audit/run — allows CEO to trigger on demand as fallback)
- Dry-run/preview mode (show intended GHL writes without executing — essential for testing)

**Defer to post-MVP (v2+):**
- Audit trend tracking (needs 2-4 weeks of historical data before meaningful; requires D1 storage)
- Configurable audit rules (hardcode rules for v1; make configurable when rules actually need to change)
- Calendly-GHL reconciliation report (valuable but not blocking core value delivery)
- Health check dashboard (Slack slash command `/atlas status` is sufficient for v1)
- Webhook event replay UI (DLQ captures failures; manual replay via script is fine initially)
- Audit "new vs. recurring" issue separation (requires persisting previous audit results)

**Never build:**
- Full workflow engine (GHL already has workflows; Atlas owns data quality, not orchestration)
- Real-time Slack notification per event (noise fatigue; digest only, real-time only for errors)
- AI-powered deal scoring (scope creep; rule-based audit solves the actual problem)
- Bi-directional CRM sync (Atlas is a one-way data quality layer)

See `FEATURES.md` for full dependency graph and MVP build order.

### Architecture Approach

Atlas is a single-process modular monolith. A `core/` layer provides stable, shared infrastructure (API clients, config, logging) that all modules consume but never import from each other. Business capabilities live in self-contained `modules/` (events, audit), each with its own router, service, and schemas. New modules slot in with one line in `main.py`. APScheduler runs via FastAPI's lifespan context manager, sharing the event loop — no separate worker process, no broker, no distributed job storage. FastAPI's dependency injection system wires clients into routes; tests swap real clients for mocks via override without monkeypatching.

**Major components:**
1. **Core: GHL Client** — all GoHighLevel API calls with rate limiting, retry, and cursor-based pagination
2. **Core: Slack Client** — message formatting and send; Block Kit digest builder
3. **Core: Calendly Client** — webhook signature verification (raw bytes, HMAC-SHA256)
4. **Core: Config + Logging** — pydantic-settings env validation at startup; structlog JSON output
5. **Module: Event Handler** — parse Calendly payload → match GHL opportunity → write fields → notify Slack
6. **Module: Audit Runner** — paginated GHL scan → stage-aware rule check → group by user → Slack digest
7. **APScheduler (via lifespan)** — `CronTrigger(hour=8, timezone="US/Eastern")` for daily audit

**Build order (dependency chain):**
1. Core infrastructure (config, logging, bare FastAPI + health endpoint, Railway deploy)
2. API clients (GHL, Slack, Calendly — independently testable)
3. Event Handler module (primary value; also stress-tests GHL client in write mode)
4. Audit Runner module (depends on mature GHL client from Phase 3)
5. Hardening (logging review, error handling audit, idempotency verification)

See `ARCHITECTURE.md` for full diagrams, code patterns, and scalability considerations.

### Critical Pitfalls

1. **Webhook-to-opportunity matching produces false matches or silent misses** — Race condition between Calendly firing and GHL having the Event ID field. Prevention: retry once after 30s before falling back to email match; email fallback requires BOTH email AND appointment type AND stage relevance; send Slack alert on zero-match (never silently drop); log every match attempt with full context.

2. **Calendly disables webhook subscription after 24 hours of errors** — Returning non-200 triggers exponential retry; after 24h, subscription is silently disabled (no notification). Prevention: always return 200 regardless of internal outcome; catch ALL exceptions in the webhook handler; health-check subscription on every boot and auto-recreate if disabled.

3. **GHL rate limits cause silent incomplete audits** — Burst of 150-600 API calls during audit hits undocumented rate limits (community: ~100 req/min for PITs). Prevention: token bucket throttling targeting 60 req/min; track "opportunities audited vs. attempted"; never report "all clear" unless coverage is 100%.

4. **GHL custom field write uses wrong key (`value` vs `field_value`)** — GHL returns 200 but silently does not write. Prevention: always use `{"id": "field_id", "field_value": "value"}` format via a single centralized `update_opportunity_fields()` function; read-back verify after every write.

5. **Slack notification fatigue kills digest effectiveness** — Same stale deals appear every day; CEO stops reading. Prevention: separate "new issues since last audit" from recurring; keep thresholds calibrated against actual deal velocity; include trend context ("3 new issues, down from 7"); cap Slack Block Kit at ~20 items per section (50-block limit).

See `PITFALLS.md` for 13 pitfalls total including moderate (APScheduler duplicate jobs, pagination silent truncation, idempotency race condition) and minor (timezone bugs, Railway cold start, env var misconfiguration).

## Implications for Roadmap

Based on research, recommended 4-phase structure:

### Phase 1: Core Infrastructure + Webhook Handler
**Rationale:** The webhook handler is the primary value proposition and must work before anything else. Core infrastructure (config, logging, API clients) has no business logic and is a prerequisite for all modules. Building and deploying the bare service first validates Railway deployment before there is any business logic to debug.
**Delivers:** Running Railway service; Calendly webhook → GHL field update → Slack notification. The CEO can see Atlas working on the first real Calendly event.
**Addresses:** Webhook ingestion, signature verification, idempotent processing, event-to-opportunity matching, field write-back, error handling, structured logging (all table stakes)
**Avoids:** Pitfall 1 (matching), Pitfall 2 (subscription disabling), Pitfall 4 (wrong field key), Pitfall 7 (signature verification on raw bytes), Pitfall 9 (idempotency race condition)
**Stack:** FastAPI + pydantic-settings + structlog + httpx (GHL/Calendly clients) + tenacity + Railway/Nixpacks

### Phase 2: Pipeline Audit Runner
**Rationale:** Audit depends on a mature GHL client (exercised in Phase 1) and adds no webhook dependencies. It is an independent workstream. The daily digest is the CEO's primary tool for eliminating manual pipeline chasing — it is the second half of the value proposition.
**Delivers:** Daily 8 AM Slack digest grouped by owner with 3 sections (missing fields / stale deals / overdue tasks); manual trigger endpoint; audit coverage reporting.
**Addresses:** Pipeline audit scan, Slack digest delivery, stage-aware field validation rules (one differentiator worth including)
**Avoids:** Pitfall 3 (rate limits / incomplete audit), Pitfall 4 (notification fatigue), Pitfall 5 (APScheduler duplicate jobs), Pitfall 8 (pagination truncation), Pitfall 10 (timezone bugs), Pitfall 11 (Slack Block Kit truncation)
**Stack:** APScheduler 3.x (CronTrigger via lifespan), slack-sdk AsyncWebClient, GHL pagination iterator

### Phase 3: Hardening + Operational Readiness
**Rationale:** Core functionality is proven; now make it production-grade. This is cheaper to do as a dedicated phase than to retrofit later, and prevents operational surprises after go-live.
**Delivers:** Subscription health check on boot; startup env var + API connectivity validation; heartbeat monitoring for missed audits; read-back verification for GHL writes; dry-run/preview mode; comprehensive test coverage.
**Addresses:** Operational pitfalls (Pitfall 12: Railway cold start, Pitfall 13: env var misconfiguration); production confidence for CEO
**Stack:** No new dependencies; test coverage via pytest + pytest-asyncio + respx + time-machine

### Phase 4: Post-MVP Enhancements (after 2-4 weeks of live data)
**Rationale:** These features require historical data or proven demand. Audit trend tracking needs baseline data. Configurable rules need real evidence that rules should change. Reconciliation needs the event log from Phase 1.
**Delivers:** Audit trend tracking; configurable rules (no-code-deploy changes); Calendly-GHL reconciliation report; "new vs. recurring" issue separation in digest
**Addresses:** Differentiator features deferred from MVP; organizational maturity features
**Requires:** D1 or Railway Redis for persistent storage (first time Atlas needs external state)

### Phase Ordering Rationale

- Webhook handler before audit because it is the harder engineering problem (matching) and validates the GHL client in write mode before audit reads from it.
- API clients as a shared phase 1 prerequisite because every module imports from core; building clients first gives stable, independently tested foundations.
- Hardening as a dedicated phase because operational reliability (subscription monitoring, heartbeat, startup validation) is high-leverage but can be retrofitted without major refactoring — unlike the matching logic which must be right from day one.
- Post-MVP enhancements require live data; the features that need historical baselines cannot ship meaningfully before the system has been running for weeks.

### Research Flags

**Phases needing deeper research during planning:**
- **Phase 1 (Opportunity Matching):** The primary/fallback matching strategy should be reviewed against actual AHG GHL data — specifically, how reliably is the Calendly Event ID custom field populated on GHL opportunities, and what is the actual lag between Calendly firing and the field being set? This affects whether the 30s retry window is sufficient.
- **Phase 2 (GHL Pagination + Rate Limits):** Exact GHL rate limit behavior for the AHG PIT key should be tested early. Community reports suggest ~100 req/min but AHG may differ. Audit scan volume (number of active opportunities) should be confirmed before assuming the token bucket target.
- **Phase 4 (Persistent Storage):** If D1 is chosen for audit snapshots, Railway D1 connectivity needs verification. If Railway Redis, cost and connection pooling patterns need research.

**Phases with standard patterns (skip additional research):**
- **Phase 1 (Core Infrastructure):** FastAPI + Railway + Nixpacks deployment is extremely well-documented. The patterns in ARCHITECTURE.md are directly implementable.
- **Phase 3 (Hardening):** pytest + respx + time-machine patterns for testing async FastAPI services are standard. No research needed.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All versions verified against PyPI as of Mar 4, 2026. Railway deployment patterns verified against official Railway FastAPI guide. APScheduler 4.x alpha warning verified against GitHub issues. |
| Features | MEDIUM-HIGH | Table stakes derived from established webhook/CRM integration patterns (Hookdeck, GHL docs). Differentiators inferred from AHG-specific context and pipeline hygiene research. MVP boundary is opinionated but defensible. |
| Architecture | HIGH | Modular monolith pattern well-documented (FastAPI starter kits, APScheduler official docs). Component boundaries and build order derived from dependency analysis. Single-process rationale confirmed by APScheduler multi-worker issue tracker. |
| Pitfalls | HIGH | Top pitfalls 1-4 sourced from official Calendly docs, GHL API docs, and first-party AHG Onboarding Hub experience. Pitfall 4 (field_value key) confirmed in GHL community reports and AHG Hub codebase directly. |

**Overall confidence: HIGH**

### Gaps to Address

- **Calendly Event ID custom field population timing:** Research confirms the race condition exists but cannot quantify the typical lag for AHG. Validate during Phase 1 by logging the time between Calendly firing and the field being present in GHL. Adjust the retry delay (currently 30s) based on real data.
- **GHL rate limit numbers for AHG PIT key:** Undocumented by GHL; community reports suggest ~100 req/min. Validate early in Phase 2 by running a test audit scan with rate limit header logging. Adjust token bucket target accordingly.
- **Active opportunity count:** Architecture assumes 50-200 active deals. Confirm actual pipeline size with the CEO before finalizing audit scan timeout and pagination cap (currently 500 opportunities = 5 pages).
- **Calendly event types in scope:** Architecture assumes "Discovery" and "Onboarding" event types. Confirm the exact GHL appointment type names and Calendly event names that should trigger field updates before building the filter logic in Phase 1.

## Sources

### Primary (HIGH confidence)
- FastAPI v0.135.1, Uvicorn v0.41.0, APScheduler v3.11.x — PyPI (verified Mar 4, 2026)
- Calendly Webhook Signatures — [official Calendly developer docs](https://developer.calendly.com/api-docs/4c305798a61d3-webhook-signatures)
- Calendly Webhook Errors — [official Calendly developer docs](https://developer.calendly.com/api-docs/ZG9jOjM2MzE2MDM5-webhook-errors)
- GHL Search Opportunity API — [official GHL marketplace docs](https://marketplace.gohighlevel.com/docs/ghl/opportunities/search-opportunity/index.html)
- APScheduler Documentation — [official APScheduler 3.x docs](https://apscheduler.readthedocs.io/en/3.x/userguide.html)
- Railway FastAPI guide — [Railway official docs](https://docs.railway.com/guides/fastapi)
- FastAPI modular monolith pattern — [GitHub starter kit](https://github.com/arctikant/fastapi-modular-monolith-starter-kit)
- AHG Onboarding Hub codebase — first-party experience (field_value key, IRIS/GHL integration patterns)

### Secondary (MEDIUM confidence)
- Hookdeck: Webhooks at Scale Best Practices — queue-first architecture, idempotency patterns
- APScheduler multi-worker issue — [GitHub discussions](https://github.com/agronholm/apscheduler/discussions/913)
- GHL custom fields `value` vs `field_value` — [Make community report](https://community.make.com/t/ghl-custom-fields-api-problem/79683)
- Pipeline hygiene research — Nexuscale, AskElephant, Outreach (stage-aware validation patterns)
- Monte Carlo: Alert Fatigue — notification fatigue patterns and mitigation

### Tertiary (LOW confidence)
- GHL rate limit numbers (~100 req/min) — community reports only; GHL does not publish official limits; validate empirically

---
*Research completed: 2026-03-04*
*Ready for roadmap: yes*
