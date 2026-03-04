# Atlas — AHG Pipeline Intelligence Agent

## What This Is

Atlas is the pipeline intelligence agent for Alternative Horizons Group (AHG). It's a Python service deployed on Railway that does two things: handles Calendly webhook events (no-shows and cancellations) by writing the correct field updates to GoHighLevel opportunities, and runs a daily pipeline audit that scans all active deals for missing fields, stale deals, and overdue actions — sending one consolidated Slack digest grouped by team member.

## Core Value

Calendly events automatically stamp the right fields on the right GHL opportunity so downstream automation workflows fire without manual intervention. If nothing else works, this must.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Receive Calendly `invitee.canceled` and `invitee.no_show` webhooks
- [ ] Parse event type, scheduled event URI, invitee email, event name
- [ ] Filter: only process events containing "Discovery" or "Onboarding" in event name
- [ ] Match webhook to GHL opportunity — primary: Calendly Event ID field; fallback: contact email + Appointment Type + relevant stage
- [ ] Write correct field updates per event type (Discovery No-Show, Onboarding No-Show, Discovery Cancel, Onboarding Cancel)
- [ ] Idempotent writes — duplicate webhooks produce no side effects
- [ ] Always return 200 to Calendly regardless of outcome
- [ ] Slack notification on every webhook outcome (success, match failure, API error)
- [ ] Daily pipeline audit at 8 AM EST (also manually triggerable via POST /audit/run)
- [ ] Audit Check 1: Missing required fields per stage (Discovery through Live, with inheritance)
- [ ] Audit Check 2: Stale deals past stage thresholds (7/5/14/14/7 days)
- [ ] Audit Check 3: Overdue tasks (completed=false, due > 24h ago)
- [ ] Audit output: One Slack message, three sections, grouped by assigned user
- [ ] Skip Close Lost, Declined, Churned opportunities in audit
- [ ] "All clear" message when zero issues found
- [ ] Structured JSON logging for all operations
- [ ] Calendly webhook subscriptions created via API (invitee.canceled, invitee.no_show, org-scoped)
- [ ] Contact-level audit checks: Lead Source required, email must exist
- [ ] Opportunity name check: flag "New Merchant - Update Name" as missing real name

### Out of Scope

- Stage transitions — Atlas NEVER moves opportunities between pipeline stages
- Contact/opportunity creation — Atlas only reads (audit) or updates existing fields (event handler)
- Auto-fixing data — audit is report-only for MVP
- Lead intake / email parsing — future module, design for pluggability
- Calendly vs GHL drift reconciliation — future module
- Absorbing Discovery Booked / Onboarding Scheduled Zaps — future module
- Manual Action status checking — include if GHL API supports it, defer if not
- AHG Hub interaction — Hub reads from GHL independently; Atlas doesn't touch Hub

## Context

**System landscape:**
- **Calendly** fires webhooks on cancellations and no-shows for "AHG Payments Discovery" (30min) and Onboarding (60min) events
- **GoHighLevel (GHL)** is the CRM — pipeline with 9 stages, custom fields, automation workflows. Atlas reads and writes via REST API
- **AHG Hub** is the internal onboarding web app. Reads GHL via "Refresh from GHL" button. Atlas doesn't interact with Hub directly — Hub sees Atlas's field writes on next refresh
- **Slack** receives all notifications and the daily digest in #sales-pipeline
- **GHL Workflows** own all cadences, Manual Actions, SLA timers, and notifications. Atlas feeds them accurate data

**Pipeline stages (in order):** Discovery → Committed → Onboarding Scheduled → MPA & Underwriting → Approved → Live | Close Lost, Declined, Churned (terminal)

**Team:**
- Henry Mashburn (Sales) — GHL ID: OcuxaptjbljS6L2SnKbb
- Drew Brasiel (CEO) — GHL ID: 8oVYzIxdHG8TGVpXc3Ma

**Existing integrations built for AHG:** GHL full CRUD, IRIS REST client, OneDrive integration — all in the AHG Onboarding Hub (separate project, different stack)

**Key API details:**
- GHL Location ID: l39XXt9HcdLTsuqTind6
- GHL Pipeline ID: V6mwUqamI0tGUm1GDvKD
- All field IDs documented in technical reference (see .planning/TECHNICAL_REFERENCE.md)

## Constraints

- **Deployment**: Railway (existing account, Python)
- **Framework**: FastAPI + APScheduler (async webhooks, auto-docs, cron scheduling)
- **GHL API Key**: New dedicated PIT to be created (scoped to Atlas)
- **Calendly API Key**: To be created during setup
- **Slack Webhook**: To be created during setup (incoming webhook for #sales-pipeline)
- **GHL API Rate Limits**: Respect rate limits, implement retry with backoff
- **Webhook Verification**: Validate Calendly webhook signatures before processing
- **No Destructive Writes**: Only custom field updates — never stage moves, never create/delete records

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| FastAPI + APScheduler | Async for webhooks, built-in OpenAPI docs, APScheduler for cron — clean fit | — Pending |
| Railway deployment | Existing account, supports Python, persistent process for scheduler | — Pending |
| Dedicated GHL PIT | Isolate Atlas's API access from other projects for security/auditability | — Pending |
| Primary match on Calendly Event ID, fallback on email | Event ID is deterministic; email fallback handles cases where Event ID wasn't stamped yet | — Pending |
| Report-only audit (no auto-fix) | MVP keeps humans in the loop; auto-fix is a future decision after trust is established | — Pending |
| Modular codebase for future plugins | Lead intake, reconciliation, Zap absorption designed as pluggable modules | — Pending |

---
*Last updated: 2026-03-04 after initialization*
