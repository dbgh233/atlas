# Requirements: Atlas

**Defined:** 2026-03-04, updated 2026-03-05
**Core Value:** Calendly events automatically stamp the right fields on the right GHL opportunity so downstream automation workflows fire without manual intervention — and Atlas learns from every human interaction to progressively handle more autonomously.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Event Handler

- [ ] **EVNT-01**: Receive Calendly `invitee.canceled` and `invitee.no_show` webhooks via POST endpoint
- [ ] **EVNT-02**: Verify Calendly webhook signatures before processing
- [ ] **EVNT-03**: Parse event type, scheduled event URI, invitee email, and event name from payload
- [ ] **EVNT-04**: Filter events — only process if event name contains "Discovery" or "Onboarding"; log and ignore others
- [ ] **EVNT-05**: Match webhook to GHL opportunity — primary: Calendly Event ID custom field (exact match); fallback: contact email + Appointment Type + relevant stage
- [ ] **EVNT-06**: Verify appointment type — if GHL opp's Appointment Type conflicts with Calendly event name, trust GHL
- [ ] **EVNT-07**: Write correct field updates: Discovery No-Show (Discovery Outcome = "No Show", Appointment Status = "No-Show"), Onboarding No-Show (Appointment Status = "No-Show"), Discovery Cancel (Appointment Status = "Cancelled"), Onboarding Cancel (Appointment Status = "Cancelled")
- [ ] **EVNT-08**: Idempotent writes — duplicate webhooks produce no side effects
- [ ] **EVNT-09**: Always return 200 to Calendly regardless of outcome (log and alert, don't trigger retries)
- [ ] **EVNT-10**: Read-back verification — after writing GHL fields, read opportunity back and confirm fields persisted
- [ ] **EVNT-11**: Dead letter queue — store failed webhook payloads with full context (original payload, error, timestamp, retry count) for investigation and replay
- [ ] **EVNT-12**: Dry-run mode — process webhook and log intended writes without executing (via config flag or X-Atlas-Dry-Run header)

### Pipeline Audit

- [ ] **AUDIT-01**: Daily scheduled scan at 8 AM EST via APScheduler
- [ ] **AUDIT-02**: Manual trigger via POST /audit/run returning JSON results AND sending Slack digest
- [ ] **AUDIT-03**: Check 1 — Missing required fields per stage with inheritance (Discovery base fields + each subsequent stage adds its own)
- [ ] **AUDIT-04**: Check 2 — Stale deals past stage thresholds (Discovery: 7d, Committed: 5d, Onboarding Scheduled: 14d, MPA & Underwriting: 14d, Approved: 7d)
- [ ] **AUDIT-05**: Check 3 — Overdue tasks (completed=false, due date > 24h ago)
- [ ] **AUDIT-06**: Contact-level checks: Lead Source must be set, email must exist
- [ ] **AUDIT-07**: Opportunity name check: flag "New Merchant - Update Name" as missing real name
- [ ] **AUDIT-08**: Skip Close Lost, Declined, Churned opportunities in all checks
- [ ] **AUDIT-09**: "All clear" message when zero issues found, including total opps checked
- [ ] **AUDIT-10**: New vs recurring issue tagging — track previous audit results, tag issues as NEW or STILL OPEN (X days)
- [ ] **AUDIT-11**: Audit trend tracking — store daily audit snapshots, enable week-over-week comparison
- [ ] **AUDIT-12**: Each audit finding includes a `suggested_action` field (e.g., "Set Industry Type based on opp context") — report-only in v1, designed for future suggest+confirm flow

### Notifications

- [ ] **NOTIF-01**: Slack notification on every webhook outcome (success, match failure, API error)
- [ ] **NOTIF-02**: Audit digest — one Slack message with three sections (Missing Fields, Stale Deals, Overdue Actions) grouped by assigned user
- [ ] **NOTIF-03**: Slack alert when Calendly webhook subscriptions are missing or disabled
- [ ] **NOTIF-04**: Slack slash command /atlas status — returns system health summary (last webhook, last audit, success rate)

### Conversational Agent

- [ ] **CONV-01**: Atlas responds to @mentions in #sales-pipeline and DMs with natural language (powered by Claude Opus 4.6)
- [ ] **CONV-02**: Atlas can answer pipeline questions — "what's stale?", "show Henry's issues", "what's missing on [opp name]?"
- [ ] **CONV-03**: Audit findings presented as actionable suggestions with approve/reject conversational flow
- [ ] **CONV-04**: When user approves a suggested fix, Atlas writes the update to GHL and confirms
- [ ] **CONV-05**: Every human interaction logged with full context (suggestion, response, opp context, timestamp, who)
- [ ] **CONV-06**: Confidence scoring per fix type based on approval history
- [ ] **CONV-07**: Graduated autonomy — fix types auto-promote from suggest→auto-fix when approval rate >90% for 2+ weeks
- [ ] **CONV-08**: Auto-fixed issues reported in daily digest ("Atlas auto-fixed 3 issues overnight")
- [ ] **CONV-09**: Anomaly detection — if approval rate drops on auto-fix type, revert to suggest+confirm
- [ ] **CONV-10**: User can undo auto-fixes via conversation ("undo that" or "revert Industry Type on [opp]")

### Infrastructure

- [ ] **INFRA-01**: Structured JSON logging for all operations with correlation IDs
- [ ] **INFRA-02**: Create Calendly webhook subscriptions via API (invitee.canceled, invitee.no_show, organization-scoped)
- [ ] **INFRA-03**: Rate-limited GHL client with exponential backoff and retry (respect rate limit headers)
- [ ] **INFRA-04**: Subscription health check — verify Calendly webhook subscriptions are active on startup, Slack-alert if missing
- [ ] **INFRA-05**: Health check endpoint — GET /health returns last webhook received, last audit run, processing status
- [ ] **INFRA-06**: FastAPI application with modular architecture (core/ + modules/ pattern for future extensibility)
- [ ] **INFRA-07**: Railway deployment with environment variable configuration
- [ ] **INFRA-08**: Persistent storage for DLQ, audit snapshots, interaction log, and idempotency keys (Railway-compatible — SQLite)
- [ ] **INFRA-09**: Claude Opus 4.6 API client for conversational intelligence (Anthropic SDK)
- [ ] **INFRA-10**: Slack Events API integration for receiving @mentions and DMs (Agent/Assistant mode)

### Meeting Intelligence (Phase 9)

- [x] **MTG-01**: Calendly backfill — scan recent events, match to GHL opps (email + stage + type + timing), write missing Q&A fields, verify writes
- [x] **MTG-02**: Meeting transcript ingestion via POST /meetings/ingest — accepts Otter speech data, classifies meeting type
- [x] **MTG-03**: Commitment extraction — Claude analyzes transcripts, extracts action items with assignee, merchant, deadline, source quote
- [x] **MTG-04**: Merchant-to-opportunity matching — fuzzy match transcript merchant names to GHL opportunity IDs
- [x] **MTG-05**: Commitment tracking — open/fulfilled/missed/dismissed states, grouped by user with Slack @mentions
- [x] **MTG-06**: Auto-dismiss — daily check if linked GHL opps progressed since commitment, auto-mark fulfilled
- [x] **MTG-07**: Weekly Friday rollup — summarizes commitments fulfilled/missed/open per user for the week
- [x] **MTG-08**: Pattern detection — agenda gaps (deals behind SLA not discussed in triage), recurring topics (3+ meetings without stage movement)
- [x] **MTG-09**: Slack Block Kit interactive buttons — Dismiss/Create GHL Task/Mark Fulfilled overflow menus on commitment messages

### Pre-Call Intelligence (Phase 10)

- [ ] **PRECALL-01**: Morning scan of Calendly for today's discovery/partner calls, identify assigned AHG rep per event
- [ ] **PRECALL-02**: Prospect research — search LinkedIn profile, company website, industry databases for background
- [ ] **PRECALL-03**: Rapport matching — cross-reference prospect details with rep's LinkedIn/master prompt for shared connections (schools, locations, interests, associations)
- [ ] **PRECALL-04**: Deal scoring — score prospect against merchant ICP criteria (industry, volume, risk type, processor fit) before the call
- [ ] **PRECALL-05**: Pre-call brief generation — structured Slack DM to assigned rep 5-10 min before meeting with prospect background, rapport points, pain points, deal score, suggested openers
- [ ] **PRECALL-06**: Rep feedback loop — thumbs up/down reactions on pre-call briefs to train Atlas on what's useful
- [ ] **PRECALL-07**: Master prompt context — ingest each rep's selling style, strengths, and personal details to improve rapport point matching

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Future Modules

- **FUTURE-01**: Lead intake module — email referral parsing, contact creation, lead nurture enrollment
- **FUTURE-02**: Calendly/GHL reconciliation — nightly drift detection (first post-MVP addition, needs 1-2 weeks of live data)
- **FUTURE-03**: Zap absorption — move Discovery Booked and Onboarding Scheduled Zaps into Atlas
- **FUTURE-04**: Configurable audit rules without code deploy (admin config file or D1)
- **FUTURE-05**: Automated DLQ replay with rate limiting
- **FUTURE-06**: Deal scoring — rule-based then ML-based (needs 3-6 months historical data)
- **FUTURE-07**: MCP server — expose Atlas capabilities as tools for Claude (trigger audit, query pipeline, manage DLQ)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Stage transitions | Atlas NEVER moves opportunities between pipeline stages — only writes custom field values |
| Contact/opportunity creation | Atlas only reads contacts (for matching/audit) and updates existing opportunity fields |
| Full workflow engine | GHL owns cadences, sequences, SLA timers, notifications. Atlas feeds them data |
| Real-time per-event Slack pings for audit | Digest > firehose; noise fatigue kills adoption within a week |
| Admin UI | Config file sufficient for v1; CEO doesn't need a UI for rule changes |
| Bi-directional CRM sync | Atlas is one-way: external events → GHL field writes. No GHL → Calendly sync |
| Email notifications | Team lives in Slack; one channel is enough for v1 |
| AI-powered deal scoring (v1) | No training data yet; interaction log must accumulate 3-6 months first |
| Full chatbot personality / small talk | Atlas is a pipeline agent, not a general assistant. Responds only to pipeline-related queries |
| Multi-provider webhooks (v1) | Architecture supports it (adapter pattern) but only Calendly adapter in v1 |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| EVNT-01 | Phase 2 | Complete |
| EVNT-02 | Phase 2 | Complete |
| EVNT-03 | Phase 2 | Complete |
| EVNT-04 | Phase 2 | Complete |
| EVNT-05 | Phase 2 | Complete |
| EVNT-06 | Phase 2 | Complete |
| EVNT-07 | Phase 2 | Complete |
| EVNT-08 | Phase 2 | Complete |
| EVNT-09 | Phase 2 | Complete |
| EVNT-10 | Phase 3 | Complete |
| EVNT-11 | Phase 3 | Complete |
| EVNT-12 | Phase 3 | Complete |
| AUDIT-01 | Phase 4 | Complete |
| AUDIT-02 | Phase 4 | Complete |
| AUDIT-03 | Phase 4 | Complete |
| AUDIT-04 | Phase 4 | Complete |
| AUDIT-05 | Phase 4 | Complete |
| AUDIT-06 | Phase 4 | Complete |
| AUDIT-07 | Phase 4 | Complete |
| AUDIT-08 | Phase 4 | Complete |
| AUDIT-09 | Phase 4 | Complete |
| AUDIT-10 | Phase 5 | Complete |
| AUDIT-11 | Phase 5 | Complete |
| AUDIT-12 | Phase 5 | Complete |
| CONV-01 | Phase 6 | Complete |
| CONV-02 | Phase 6 | Complete |
| CONV-03 | Phase 6 | Complete |
| CONV-04 | Phase 6 | Complete |
| CONV-05 | Phase 6 | Complete |
| CONV-06 | Phase 7 | Complete |
| CONV-07 | Phase 7 | Complete |
| CONV-08 | Phase 7 | Complete |
| CONV-09 | Phase 7 | Complete |
| CONV-10 | Phase 7 | Complete |
| NOTIF-01 | Phase 2 | Complete |
| NOTIF-02 | Phase 4 | Complete |
| NOTIF-03 | Phase 8 | Complete |
| NOTIF-04 | Phase 6 | Complete |
| INFRA-01 | Phase 1 | Complete |
| INFRA-02 | Phase 2 | Complete |
| INFRA-03 | Phase 1 | Complete |
| INFRA-04 | Phase 8 | Complete |
| INFRA-05 | Phase 8 | Complete |
| INFRA-06 | Phase 1 | Complete |
| INFRA-07 | Phase 1 | Complete |
| INFRA-08 | Phase 1 | Complete |
| INFRA-09 | Phase 1 | Complete |
| INFRA-10 | Phase 1 | Complete |

**Coverage:**
- v1 requirements: 48 total
- Mapped to phases: 48
- Unmapped: 0

---
*Requirements defined: 2026-03-04*
*Last updated: 2026-03-05 after roadmap rebuild with conversational agent + graduated autonomy*
