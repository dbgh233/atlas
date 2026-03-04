# Roadmap: Atlas

**Created:** 2026-03-04
**Depth:** Standard
**Phases:** 6
**Requirements:** 36 mapped

## Overview

Atlas delivers pipeline intelligence in two independent capabilities: Calendly webhook event handling (the primary value) and daily pipeline auditing (the CEO's daily tool). The build order follows the dependency chain: shared infrastructure first, then the webhook handler (hardest engineering problem: opportunity matching), then audit (depends on a proven GHL client), then operational hardening. Each phase delivers a verifiable, running capability on Railway.

## Phases

- [ ] **Phase 1: Foundation** - App scaffold, API clients, structured logging, Railway deployment
- [ ] **Phase 2: Webhook Event Handler** - Calendly webhooks matched to GHL opportunities with correct field writes
- [ ] **Phase 3: Webhook Hardening** - Read-back verification, dead letter queue, dry-run mode, persistent storage
- [ ] **Phase 4: Pipeline Audit** - Daily scheduled audit with Slack digest grouped by owner
- [ ] **Phase 5: Audit Intelligence** - New vs recurring issue tracking, trend snapshots, suggested actions
- [ ] **Phase 6: Operational Readiness** - Health checks, subscription monitoring, Slack slash command

## Phase Details

### Phase 1: Foundation
**Goal:** A running FastAPI service deployed on Railway with working GHL, Calendly, and Slack API clients, structured logging, and a health endpoint -- ready to receive business logic.
**Depends on:** Nothing (first phase)
**Requirements:** INFRA-01, INFRA-03, INFRA-06, INFRA-07
**Success Criteria** (what must be TRUE):
  1. GET /health on the Railway production URL returns a 200 response with service status
  2. GHL client can fetch an opportunity by ID from the live AHG pipeline and return its custom fields
  3. Slack client can post a test message to #sales-pipeline
  4. All log output is structured JSON with correlation IDs visible in Railway's log viewer
  5. Application fails fast on startup if any required environment variable is missing
**Plans:** TBD

Plans:
- [ ] 01-01: FastAPI scaffold, config, logging, Railway deploy
- [ ] 01-02: GHL, Calendly, and Slack API clients with rate limiting and retry

---

### Phase 2: Webhook Event Handler
**Goal:** When a Calendly cancellation or no-show webhook fires for a Discovery or Onboarding event, Atlas matches it to the correct GHL opportunity and writes the correct field updates -- with Slack notification on every outcome.
**Depends on:** Phase 1
**Requirements:** EVNT-01, EVNT-02, EVNT-03, EVNT-04, EVNT-05, EVNT-06, EVNT-07, EVNT-08, EVNT-09, INFRA-02, NOTIF-01
**Success Criteria** (what must be TRUE):
  1. POST /webhooks/calendly with a valid signed payload for a Discovery No-Show results in the correct GHL opportunity having Discovery Outcome = "No Show" and Appointment Status = "No-Show"
  2. A webhook for a non-Discovery/non-Onboarding event is logged and ignored -- no GHL writes occur
  3. Sending the same webhook payload twice produces no duplicate field writes (idempotent)
  4. A webhook with an invalid signature returns 200 but is rejected with a Slack alert
  5. Every webhook outcome (success, match failure, API error) produces a Slack notification in #sales-pipeline
**Plans:** TBD

Plans:
- [ ] 02-01: Webhook endpoint, signature verification, payload parsing, event filtering
- [ ] 02-02: Opportunity matching (Calendly Event ID primary, email+type+stage fallback)
- [ ] 02-03: Field writes, idempotency, Calendly subscription setup, Slack notifications

---

### Phase 3: Webhook Hardening
**Goal:** Failed webhooks are captured with full context for investigation and replay, GHL writes are verified after execution, and the entire pipeline can be tested in dry-run mode without touching GHL.
**Depends on:** Phase 2
**Requirements:** EVNT-10, EVNT-11, EVNT-12, INFRA-08
**Success Criteria** (what must be TRUE):
  1. After every GHL field write, Atlas reads the opportunity back and confirms the fields persisted -- Slack alert if verification fails
  2. When a webhook fails processing, the full payload, error context, and retry count are stored in the dead letter queue and retrievable via API
  3. Sending a webhook with X-Atlas-Dry-Run header logs the intended GHL writes but makes no API calls to GHL
  4. Persistent storage (SQLite) holds DLQ entries, idempotency keys, and audit snapshots across Railway restarts
**Plans:** TBD

Plans:
- [ ] 03-01: Read-back verification and dry-run mode
- [ ] 03-02: Dead letter queue with persistent SQLite storage

---

### Phase 4: Pipeline Audit
**Goal:** Every weekday at 8 AM EST, Atlas scans all active GHL opportunities and delivers one Slack digest with missing fields, stale deals, and overdue tasks -- grouped by assigned team member.
**Depends on:** Phase 1
**Requirements:** AUDIT-01, AUDIT-02, AUDIT-03, AUDIT-04, AUDIT-05, AUDIT-06, AUDIT-07, AUDIT-08, AUDIT-09, NOTIF-02
**Success Criteria** (what must be TRUE):
  1. At 8 AM EST, a Slack message appears in #sales-pipeline with three sections (Missing Fields, Stale Deals, Overdue Tasks) grouped by assigned user
  2. POST /audit/run triggers an immediate audit and returns JSON results with the same findings sent to Slack
  3. An opportunity in "Close Lost" stage with missing fields does NOT appear in the audit
  4. An opportunity named "New Merchant - Update Name" is flagged as missing a real name
  5. When zero issues are found, the digest says "All clear" with total opportunities checked
**Plans:** TBD

Plans:
- [ ] 04-01: GHL pipeline scan with pagination, stage filtering, and contact-level checks
- [ ] 04-02: Stage-aware field validation rules and stale deal detection
- [ ] 04-03: Slack digest formatting, APScheduler cron, manual trigger endpoint

---

### Phase 5: Audit Intelligence
**Goal:** The daily audit digest distinguishes new issues from recurring ones, tracks trends over time, and includes actionable suggested fixes for each finding.
**Depends on:** Phase 3 (persistent storage), Phase 4
**Requirements:** AUDIT-10, AUDIT-11, AUDIT-12
**Success Criteria** (what must be TRUE):
  1. Each audit finding in the Slack digest is tagged as NEW or STILL OPEN (X days) based on comparison with previous audit results
  2. Audit snapshots are stored daily and a week-over-week comparison is available via API (e.g., "12 issues this week, down from 18")
  3. Every audit finding includes a suggested_action field describing what should be done (e.g., "Set Industry Type based on opportunity context")
**Plans:** TBD

Plans:
- [ ] 05-01: Audit snapshot persistence, new vs recurring tagging, trend tracking
- [ ] 05-02: Suggested action generation per finding type

---

### Phase 6: Operational Readiness
**Goal:** Atlas monitors its own health -- verifying Calendly subscriptions are active, exposing system status via health endpoint and Slack slash command, and alerting when something is wrong.
**Depends on:** Phase 2, Phase 4
**Requirements:** INFRA-04, INFRA-05, NOTIF-03, NOTIF-04
**Success Criteria** (what must be TRUE):
  1. On every startup, Atlas verifies Calendly webhook subscriptions are active and posts a Slack alert if any are missing or disabled
  2. GET /health returns last webhook received timestamp, last audit run timestamp, and current processing status
  3. If Calendly webhook subscriptions become disabled, a Slack alert fires within the next health check cycle
  4. /atlas status in Slack returns a system health summary including last webhook time, last audit time, and recent success rate
**Plans:** TBD

Plans:
- [ ] 06-01: Subscription health check on startup and periodic verification
- [ ] 06-02: Enhanced health endpoint and Slack slash command

---

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6
Note: Phase 4 depends on Phase 1 (not Phase 3), so Phases 3 and 4 could execute in parallel.

| Phase | Plans Complete | Status | Completed |
|-------|---------------|--------|-----------|
| 1. Foundation | 0/2 | Not started | - |
| 2. Webhook Event Handler | 0/3 | Not started | - |
| 3. Webhook Hardening | 0/2 | Not started | - |
| 4. Pipeline Audit | 0/3 | Not started | - |
| 5. Audit Intelligence | 0/2 | Not started | - |
| 6. Operational Readiness | 0/2 | Not started | - |
