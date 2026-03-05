# Roadmap: Atlas

**Created:** 2026-03-04
**Updated:** 2026-03-05
**Depth:** Standard
**Phases:** 8
**Requirements:** 48 mapped

## Overview

Atlas delivers pipeline intelligence in three layers: Calendly webhook event handling (the primary value), daily pipeline auditing (the CEO's daily tool), and a conversational Slack agent powered by Claude Opus 4.6 that turns audit findings into actionable suggestions and learns from every human interaction to progressively handle more autonomously. The build follows the dependency chain: shared infrastructure and ALL API clients first (GHL, Calendly, Slack Events, Claude), then the webhook handler (hardest matching problem), then audit (depends on proven GHL client), then audit intelligence (suggested actions needed before conversational layer), then conversational agent (needs pipeline data to reason about), then graduated autonomy (needs interaction history to score confidence), and finally operational readiness.

## Phases

- [x] **Phase 1: Foundation** - App scaffold, ALL API clients (GHL, Calendly, Slack Events, Claude), persistent storage, structured logging, Railway deployment
- [x] **Phase 2: Webhook Event Handler** - Calendly webhooks matched to GHL opportunities with correct field writes and Slack notifications
- [x] **Phase 3: Webhook Hardening** - Read-back verification, dead letter queue, dry-run mode
- [x] **Phase 4: Pipeline Audit** - Daily scheduled audit with Slack digest grouped by owner
- [ ] **Phase 5: Audit Intelligence** - New vs recurring issue tracking, trend snapshots, suggested actions per finding
- [ ] **Phase 6: Conversational Agent** - Atlas responds to natural language in Slack, answers pipeline questions, and presents suggest+confirm flow for fixes
- [ ] **Phase 7: Graduated Autonomy** - Confidence scoring, auto-promotion of fix types, anomaly detection, undo capability
- [ ] **Phase 8: Operational Readiness** - Health checks, subscription monitoring, Slack alerts for degraded state

## Phase Details

### Phase 1: Foundation
**Goal:** A running FastAPI service deployed on Railway with working GHL, Calendly, Slack (incoming webhooks + Events API), and Claude Opus 4.6 API clients, SQLite persistent storage, structured logging, and a health endpoint -- ready to receive business logic.
**Depends on:** Nothing (first phase)
**Requirements:** INFRA-01, INFRA-03, INFRA-06, INFRA-07, INFRA-08, INFRA-09, INFRA-10
**Success Criteria** (what must be TRUE):
  1. GET /health on the Railway production URL returns a 200 response with service status
  2. GHL client can fetch an opportunity by ID from the live AHG pipeline and return its custom fields
  3. Slack client can post a test message to #sales-pipeline AND receive an @mention event via Events API
  4. Claude client can send a prompt to Opus 4.6 and receive a response
  5. All log output is structured JSON with correlation IDs visible in Railway's log viewer
**Plans:** 3 plans in 2 waves

Plans:
- [ ] 01-01-PLAN.md -- FastAPI scaffold, config, structured logging, health endpoint, Railway deploy
- [ ] 01-02-PLAN.md -- GHL, Calendly, Slack, and Claude API clients with retry and dependency injection
- [ ] 01-03-PLAN.md -- SQLite persistent storage (4 tables, migration system, repository classes)

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
**Requirements:** EVNT-10, EVNT-11, EVNT-12
**Success Criteria** (what must be TRUE):
  1. After every GHL field write, Atlas reads the opportunity back and confirms the fields persisted -- Slack alert if verification fails
  2. When a webhook fails processing, the full payload, error context, and retry count are stored in the dead letter queue and retrievable via API
  3. Sending a webhook with X-Atlas-Dry-Run header logs the intended GHL writes but makes no API calls to GHL
**Plans:** TBD

Plans:
- [ ] 03-01: Read-back verification and dry-run mode
- [ ] 03-02: Dead letter queue storage and retrieval API

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
**Goal:** The daily audit digest distinguishes new issues from recurring ones, tracks trends over time, and includes actionable suggested fixes for each finding -- laying the foundation for conversational suggest+confirm.
**Depends on:** Phase 3 (persistent storage proven), Phase 4
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

### Phase 6: Conversational Agent
**Goal:** Atlas responds to @mentions and DMs in Slack with natural language, answers pipeline questions using live audit data, presents audit findings as actionable suggestions with approve/reject flow, writes approved fixes to GHL, and logs every interaction.
**Depends on:** Phase 5 (suggested actions needed for suggest+confirm flow)
**Requirements:** CONV-01, CONV-02, CONV-03, CONV-04, CONV-05, NOTIF-04
**Success Criteria** (what must be TRUE):
  1. @Atlas in #sales-pipeline with "what's stale?" returns a natural language summary of stale deals from the latest audit
  2. @Atlas with "show Henry's issues" returns findings filtered to Henry Mashburn's assigned opportunities
  3. When Atlas suggests a fix (e.g., "Set Industry Type to Hemp on [opp]?") and user replies "yes", the field is written to GHL and Atlas confirms the update
  4. Every suggestion, approval, and rejection is stored in the interaction log with full context (who, what opp, what field, timestamp)
  5. /atlas status in Slack returns system health summary including last webhook, last audit, and success rate
**Plans:** TBD

Plans:
- [ ] 06-01: Slack Events API handler for @mentions and DMs, Claude-powered response generation
- [ ] 06-02: Pipeline query tools (stale, missing, per-user filtering) wired to Claude tool use
- [ ] 06-03: Suggest+confirm conversational flow with GHL write-back and interaction logging

---

### Phase 7: Graduated Autonomy
**Goal:** Atlas tracks approval rates per fix type, auto-promotes high-confidence fixes from suggest to auto-fix, reports auto-fixed issues in the daily digest, detects anomalies that should trigger reversion to suggest+confirm, and supports undo via conversation.
**Depends on:** Phase 6 (needs interaction history to compute confidence)
**Requirements:** CONV-06, CONV-07, CONV-08, CONV-09, CONV-10
**Success Criteria** (what must be TRUE):
  1. Each fix type (e.g., "Set Industry Type", "Set Appointment Status") has a confidence score computed from its approval/rejection history
  2. A fix type with >90% approval rate sustained for 2+ weeks is auto-promoted -- Atlas applies the fix without asking and logs it
  3. Auto-fixed issues appear in the daily digest as "Atlas auto-fixed 3 issues overnight" with details
  4. If a previously auto-promoted fix type's approval rate drops (e.g., user undoes multiple auto-fixes), Atlas reverts that type to suggest+confirm
  5. User can say "undo that" or "revert Industry Type on [opp]" and Atlas reverses the last auto-fix
**Plans:** TBD

Plans:
- [ ] 07-01: Confidence scoring engine and approval rate tracking per fix type
- [ ] 07-02: Auto-promotion rules, daily digest integration for auto-fixes
- [ ] 07-03: Anomaly detection (reversion trigger) and conversational undo

---

### Phase 8: Operational Readiness
**Goal:** Atlas monitors its own health -- verifying Calendly subscriptions are active, exposing system status via health endpoint, and alerting when something is wrong.
**Depends on:** Phase 2, Phase 4
**Requirements:** INFRA-04, INFRA-05, NOTIF-03
**Success Criteria** (what must be TRUE):
  1. On every startup, Atlas verifies Calendly webhook subscriptions are active and posts a Slack alert if any are missing or disabled
  2. GET /health returns last webhook received timestamp, last audit run timestamp, and current processing status
  3. If Calendly webhook subscriptions become disabled, a Slack alert fires within the next health check cycle
**Plans:** TBD

Plans:
- [ ] 08-01: Subscription health check on startup and periodic verification
- [ ] 08-02: Enhanced health endpoint with operational metrics

---

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8
Note: Phase 4 depends on Phase 1 (not Phase 3), so Phases 3 and 4 could execute in parallel.

| Phase | Plans Complete | Status | Completed |
|-------|---------------|--------|-----------|
| 1. Foundation | 0/3 | Not started | - |
| 2. Webhook Event Handler | 0/3 | Not started | - |
| 3. Webhook Hardening | 0/2 | Not started | - |
| 4. Pipeline Audit | 0/3 | Not started | - |
| 5. Audit Intelligence | 0/2 | Not started | - |
| 6. Conversational Agent | 0/3 | Not started | - |
| 7. Graduated Autonomy | 0/3 | Not started | - |
| 8. Operational Readiness | 0/2 | Not started | - |
