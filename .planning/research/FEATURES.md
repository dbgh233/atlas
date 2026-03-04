# Feature Landscape

**Domain:** CRM Pipeline Intelligence Agent (webhook event handling + pipeline data quality audit + team notifications)
**Researched:** 2026-03-04
**Overall confidence:** MEDIUM-HIGH (domain well-established; Atlas-specific patterns verified against multiple sources)

---

## Table Stakes

Features the service MUST have or it fundamentally does not work.

| Feature | Why Expected | Complexity | Dependencies | Notes |
|---------|--------------|------------|--------------|-------|
| **Webhook ingestion with fast ACK** | Calendly (and future providers) expect 2xx within seconds or they retry/drop. Queue-first pattern: verify signature, enqueue, return 200. | Low | Queue infrastructure | Never do processing inline. Verify -> enqueue -> ACK. |
| **Idempotent event processing** | Webhooks deliver at-least-once. Without deduplication, a single Calendly booking creates duplicate GHL updates. Store `event_id` with TTL >= provider retry window. | Med | Durable key-value store (D1 or KV) | Calendly retries for up to 72h; keep keys at least that long. |
| **Event-to-opportunity matching** | The entire value proposition. Calendly event arrives -> identify which GHL opportunity it belongs to -> write fields. Without reliable matching, nothing downstream works. | High | GHL contact/opp search API | Match on email, phone, contact ID. Handle no-match and multi-match cases explicitly. |
| **Field mapping and write-back** | Map Calendly event fields (date, type, attendee info) to GHL opportunity custom fields. This is the core data transformation. | Med | GHL custom field definitions | Must handle field type coercion (dates, selects, text). Must validate before writing. |
| **Pipeline audit scan** | Daily scan of active deals for missing required fields, stale deals (no activity in N days), overdue tasks. Without this, the CEO is still manually chasing. | Med | GHL opportunities list API, field rules config | Must be configurable: which fields are required at which stage, what counts as "stale." |
| **Slack digest delivery** | Audit results delivered as Slack messages grouped by team member. This is how the CEO stops chasing -- the digest does it for them. | Low-Med | Slack Bot API, audit scan output | Must be scannable at a glance: who owes what, sorted by urgency. |
| **Error handling and dead letter queue** | Failed webhook processing must not silently drop events. Failed events go to DLQ with full context for investigation and replay. | Med | DLQ storage (D1 table or queue) | Include original payload, error message, timestamp, retry count. |
| **Structured logging** | Every event processed must be traceable: received -> matched -> fields written -> downstream triggered. Without this, debugging is guesswork. | Low | Logging infrastructure | Log correlation IDs linking webhook receipt to all downstream actions. |
| **Webhook signature verification** | Verify Calendly webhook signatures to prevent spoofed events from writing to GHL. Basic security requirement. | Low | Calendly signing secret | Reject unsigned/invalid payloads before enqueueing. |
| **Rate limit awareness** | GHL API has rate limits. Batch writes and respect throttling or the service gets blocked. | Low-Med | GHL rate limit headers | Implement exponential backoff. Track rate limit headers. Queue writes if near limit. |

## Differentiators

Features that provide competitive advantage or high leverage for AHG specifically. Not expected in a basic webhook handler, but valuable.

| Feature | Value Proposition | Complexity | Dependencies | Notes |
|---------|-------------------|------------|--------------|-------|
| **Stage-aware field validation rules** | Different pipeline stages require different fields. "Appointment Set" needs meeting date; "Docs Submitted" needs doc count. The audit knows what's missing based on WHERE the deal is, not a flat checklist. | Med | Stage definitions config, pipeline audit | This is what makes the audit actionable vs. noisy. A flat "missing fields" report is useless if half the fields aren't relevant yet. |
| **Slack digest grouped by owner with action items** | Not just "these deals have problems" but "Drew: 3 deals need close dates. Sarah: 2 deals stale >14 days." Each person sees their own action list. Reduces CEO-to-rep friction to zero. | Med | Slack Block Kit, audit grouping logic | Use Block Kit for rich formatting. Consider DM vs. channel strategy. |
| **Audit trend tracking** | Track audit scores over time. "Pipeline hygiene improved 15% this month" or "Drew's data quality dropped this week." Turns one-time audit into accountability system. | Med | Historical audit storage (D1) | Store daily audit snapshots. Simple week-over-week comparison is enough for v1. |
| **Configurable audit rules (no code deploy)** | Rules for what counts as "missing" or "stale" stored in config (D1 or JSON), not hardcoded. CEO can adjust thresholds without a developer. | Med | Admin config storage, rules engine | Start with JSON config file. Evolve to admin UI later. Keep rule evaluation simple (field presence, date comparisons, enum checks). |
| **Webhook event replay** | Replay failed or DLQ'd events after fixing the issue. Don't lose data just because processing failed once. | Med | DLQ with full payload storage | Replay with rate limiting to avoid thundering herd. Mark replayed events to distinguish from originals. |
| **Calendly-GHL reconciliation report** | Periodic check: "These 5 Calendly events have no matching GHL opportunity" and "These 3 GHL opportunities claim appointments but Calendly has no record." Catches sync drift before it becomes a problem. | Med-High | Calendly events API, GHL opps API | Run weekly. Surfaces integration failures that individual event processing might miss. |
| **Dry-run / preview mode** | Process a webhook event and show what WOULD be written to GHL without actually writing. Essential for testing new field mappings and debugging. | Low-Med | Event processing pipeline | Flag on the processing pipeline. Log intended writes instead of executing them. |
| **Multi-provider webhook support** | Architecture that handles Calendly today but can accept webhooks from other providers (form submissions, payment events) without rewriting the core. Provider-specific adapters normalize to a common event schema. | Med | Adapter pattern, common event schema | Design for this from day 1. The adapter cost is low; retrofitting is expensive. |
| **Downstream workflow triggering** | After writing GHL fields, trigger specific GHL workflows (e.g., "send confirmation sequence"). Atlas ensures the data is right, then kicks off the workflow that depends on it. | Low-Med | GHL workflow trigger API | Atlas does NOT own the cadence -- it just ensures the data is ready and fires the trigger. Clear separation of concerns. |
| **Health check dashboard** | Simple status page: last webhook received, last audit run, processing success rate, queue depth. CEO glances at it and knows the system is working. | Low-Med | Metrics storage, simple UI or Slack command | Can start as a Slack slash command (`/atlas status`) before building a UI. |

## Anti-Features

Features to deliberately NOT build. Common mistakes in this domain that would waste time or create maintenance burden.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Full workflow engine / cadence builder** | GHL already has workflows. Building a parallel workflow engine creates confusion about which system owns what, doubles maintenance, and fights the tool the team already uses. Atlas's job is DATA QUALITY, not orchestration. | Write accurate data to GHL fields. Trigger existing GHL workflows. Let GHL own sequences, cadences, and multi-step automations. |
| **Real-time Slack notifications per event** | Every Calendly booking pinging Slack creates noise fatigue. The team ignores it within a week. The whole point is a DIGEST, not a firehose. | Daily (or twice-daily) Slack digest. Only real-time alert for genuine errors (processing failures, match failures). |
| **AI-powered deal scoring or forecasting** | Massive complexity, requires training data AHG doesn't have, and solves a problem the CEO hasn't asked for. Classic scope creep dressed up as innovation. | Simple rule-based audit (field presence, date math, activity recency). If the CEO wants scoring later, it's a separate initiative with its own data requirements. |
| **User-facing admin UI (v1)** | Building a full admin panel before the core pipeline works is premature. Config changes are infrequent; a config file or D1 row is fine. | JSON/TOML config file for audit rules. Direct D1 edits for one-off changes. Admin UI is a Phase 3+ consideration after core is proven. |
| **Bi-directional CRM sync** | Syncing GHL changes BACK to Calendly or other systems adds exponential complexity (conflict resolution, infinite loops, schema mismatches). Atlas is a one-way data quality layer. | One-way: external events -> GHL field writes. If GHL data needs to flow elsewhere, that's a separate integration with its own sync logic. |
| **Generic webhook relay / Zapier clone** | Tempting to generalize into "handle any webhook and route it anywhere." This turns Atlas into an infrastructure product instead of a business-logic product. Competing with n8n/Zapier/Make is a losing game. | Purpose-built handlers for specific AHG business events. Use adapters for extensibility, but each adapter encodes specific business logic, not generic routing. |
| **Complex retry strategies with circuit breakers** | Over-engineering for a system processing dozens of events per day, not thousands. Circuit breakers, bulkheads, and saga patterns are for high-scale distributed systems. | Simple exponential backoff with max 3 retries. DLQ for failures. Manual replay capability. This covers 99.9% of AHG's volume. |
| **Email notifications (duplicating Slack)** | Adding email as a notification channel alongside Slack fragments attention. The team lives in Slack. | Slack only for v1. If specific people need email digests, consider it as a targeted addition later, not a parallel notification system. |

## Feature Dependencies

```
Webhook Signature Verification
  |
  v
Webhook Ingestion (queue-first ACK)
  |
  v
Idempotent Event Processing (dedup store)
  |
  +---> Event-to-Opportunity Matching
  |       |
  |       v
  |     Field Mapping & Write-back ---> Downstream Workflow Triggering
  |       |
  |       v
  |     Structured Logging
  |
  v
Error Handling & DLQ ---> Webhook Event Replay

---

Pipeline Audit Scan (independent of webhook path)
  |
  +---> Stage-aware Field Validation Rules
  |
  +---> Audit Trend Tracking (requires historical storage)
  |
  v
Slack Digest Delivery (grouped by owner)

---

Calendly-GHL Reconciliation (requires both webhook log + Calendly API)
  depends on: Structured Logging + Calendly events API

Multi-provider Webhook Support (architectural, not a feature to "ship")
  depends on: Webhook Ingestion pattern being adapter-based from day 1

Health Check Dashboard
  depends on: Structured Logging + Metrics collection

Configurable Audit Rules
  depends on: Pipeline Audit Scan (must exist before making it configurable)
```

### Critical path for MVP:
```
Webhook Ingestion -> Dedup -> Matching -> Field Write -> Logging
                                                            |
Pipeline Audit Scan -> Slack Digest                         |
                                                            |
Error Handling / DLQ <--------------------------------------+
```

These two paths (event handling + audit) are independent and can be built in parallel. They share GHL API access but have no code dependencies on each other.

## MVP Recommendation

**For MVP, prioritize (in build order):**

1. **Webhook ingestion with signature verification and fast ACK** -- the entry point for all event data
2. **Idempotent event processing** -- without this, every retry corrupts data
3. **Event-to-opportunity matching** -- the hardest table-stakes problem; validate matching accuracy early
4. **Field mapping and write-back** -- the core value delivery
5. **Structured logging** -- needed to debug everything above
6. **Error handling and DLQ** -- catch failures before they become silent data loss
7. **Pipeline audit scan** -- independent workstream, can build in parallel with 1-6
8. **Slack digest delivery** -- the CEO's primary interface with the system

**One differentiator for MVP:**
- **Stage-aware field validation rules** -- low incremental cost over flat validation, dramatically improves audit signal-to-noise ratio

**Defer to post-MVP:**
- Audit trend tracking: needs historical data that doesn't exist yet; revisit after 2-4 weeks of audit runs
- Calendly-GHL reconciliation: valuable but not blocking core value delivery
- Configurable audit rules: hardcode rules for v1; make configurable when rules actually need to change
- Health check dashboard: Slack command (`/atlas status`) is sufficient for v1
- Webhook event replay: DLQ captures failures; manual replay via script is fine before building automated replay
- Multi-provider support: design the adapter pattern into the architecture but only implement the Calendly adapter

## Sources

- [Hookdeck: Webhooks at Scale Best Practices](https://hookdeck.com/blog/webhooks-at-scale) -- queue-first architecture, idempotency patterns (MEDIUM confidence)
- [Momentum AI: Slack Notifications for Sales Teams](https://www.momentum.io/notifications) -- digest patterns, pipeline notification features (MEDIUM confidence)
- [Pipeline Hygiene 2026 Guide](https://resources.rework.com/libraries/pipeline-management/pipeline-hygiene) -- audit cadence, field requirements by stage (MEDIUM confidence)
- [Nexuscale: Pipeline Hygiene Automation Rules](https://www.nexuscale.ai/blogs/pipeline-hygiene-automation-rules-that-keep-forecasts-honest) -- stage-aware validation, agentic governance patterns (LOW-MEDIUM confidence)
- [AskElephant: Pipeline Hygiene](https://www.askelephant.ai/blog/what-is-pipeline-hygiene) -- stale deal detection, required field enforcement (MEDIUM confidence)
- [Scratchpad: 10 Slack Automations for RevOps](https://www.scratchpad.com/blog/revops-slack-automations) -- real-world Slack automation patterns for sales teams (MEDIUM confidence)
- [HighLevel: Calendly Integration](https://help.gohighlevel.com/support/solutions/articles/155000002373-integrate-calendly-with-highlevel-calendars) -- GHL-Calendly sync limitations, one-way sync (HIGH confidence, official docs)
- [Latenode: Webhook Deduplication Checklist](https://latenode.com/blog/integration-api-management/webhook-setup-configuration/webhook-deduplication-checklist-for-developers) -- idempotency key management, TTL strategy (MEDIUM confidence)
- [Outreach: Sales Pipeline Management Best Practices 2026](https://www.outreach.io/resources/blog/sales-pipeline-management-best-practices) -- pipeline audit scope, data quality standards (MEDIUM confidence)
