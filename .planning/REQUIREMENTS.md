# Requirements: Atlas

**Defined:** 2026-03-04
**Core Value:** Calendly events automatically stamp the right fields on the right GHL opportunity so downstream automation workflows fire without manual intervention.

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

### Infrastructure

- [ ] **INFRA-01**: Structured JSON logging for all operations with correlation IDs
- [ ] **INFRA-02**: Create Calendly webhook subscriptions via API (invitee.canceled, invitee.no_show, organization-scoped)
- [ ] **INFRA-03**: Rate-limited GHL client with exponential backoff and retry (respect rate limit headers)
- [ ] **INFRA-04**: Subscription health check — verify Calendly webhook subscriptions are active on startup, Slack-alert if missing
- [ ] **INFRA-05**: Health check endpoint — GET /health returns last webhook received, last audit run, processing status
- [ ] **INFRA-06**: FastAPI application with modular architecture (core/ + modules/ pattern for future extensibility)
- [ ] **INFRA-07**: Railway deployment with environment variable configuration
- [ ] **INFRA-08**: Persistent storage for DLQ, audit snapshots, and idempotency keys (Railway-compatible — SQLite or Redis)

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Suggest + Confirm (Human-in-the-Loop Fix Flow)

- **FIX-01**: Audit findings include interactive Slack buttons (Approve Fix / Dismiss)
- **FIX-02**: When approved, Atlas writes the suggested fix to GHL
- **FIX-03**: Track approval/rejection rates per fix type for auto-fix transition readiness
- **FIX-04**: When fix type approval rate >95% for 2+ weeks, flag for auto-fix promotion

### Auto-Fix (Fully Agentic)

- **AUTOFIX-01**: Graduated autonomy — auto-fix types individually promoted based on approval rate data
- **AUTOFIX-02**: Auto-fixed issues logged and summarized in daily digest (what Atlas fixed overnight)
- **AUTOFIX-03**: Revert capability — human can undo auto-fixes from Slack

### Future Modules

- **FUTURE-01**: Lead intake module — email referral parsing, contact creation, lead nurture enrollment
- **FUTURE-02**: Calendly/GHL reconciliation — nightly drift detection (first post-MVP addition, needs 1-2 weeks of live data)
- **FUTURE-03**: Zap absorption — move Discovery Booked and Onboarding Scheduled Zaps into Atlas
- **FUTURE-04**: Configurable audit rules without code deploy (admin config file or D1)
- **FUTURE-05**: Automated DLQ replay with rate limiting
- **FUTURE-06**: Deal scoring — rule-based then ML-based (needs 3-6 months historical data)

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
| AI-powered deal scoring (v1) | No training data yet; rule-based audit must run 3-6 months first |
| Multi-provider webhooks (v1) | Architecture supports it (adapter pattern) but only Calendly adapter in v1 |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| EVNT-01 | TBD | Pending |
| EVNT-02 | TBD | Pending |
| EVNT-03 | TBD | Pending |
| EVNT-04 | TBD | Pending |
| EVNT-05 | TBD | Pending |
| EVNT-06 | TBD | Pending |
| EVNT-07 | TBD | Pending |
| EVNT-08 | TBD | Pending |
| EVNT-09 | TBD | Pending |
| EVNT-10 | TBD | Pending |
| EVNT-11 | TBD | Pending |
| EVNT-12 | TBD | Pending |
| AUDIT-01 | TBD | Pending |
| AUDIT-02 | TBD | Pending |
| AUDIT-03 | TBD | Pending |
| AUDIT-04 | TBD | Pending |
| AUDIT-05 | TBD | Pending |
| AUDIT-06 | TBD | Pending |
| AUDIT-07 | TBD | Pending |
| AUDIT-08 | TBD | Pending |
| AUDIT-09 | TBD | Pending |
| AUDIT-10 | TBD | Pending |
| AUDIT-11 | TBD | Pending |
| AUDIT-12 | TBD | Pending |
| NOTIF-01 | TBD | Pending |
| NOTIF-02 | TBD | Pending |
| NOTIF-03 | TBD | Pending |
| NOTIF-04 | TBD | Pending |
| INFRA-01 | TBD | Pending |
| INFRA-02 | TBD | Pending |
| INFRA-03 | TBD | Pending |
| INFRA-04 | TBD | Pending |
| INFRA-05 | TBD | Pending |
| INFRA-06 | TBD | Pending |
| INFRA-07 | TBD | Pending |
| INFRA-08 | TBD | Pending |

**Coverage:**
- v1 requirements: 32 total
- Mapped to phases: 0
- Unmapped: 32 ⚠️

---
*Requirements defined: 2026-03-04*
*Last updated: 2026-03-04 after initial definition*
