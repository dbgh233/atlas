# Atlas Pipeline Reference
## How AHG's Pipeline Works -- For Agent Context

**Purpose:** This document gives Atlas the operational context to make intelligent audit decisions. It combines pipeline architecture, SLA targets, what's automated vs manual, and field relationships into one reference.

**Last Updated:** March 6, 2026

---

## Pipeline Stages and Progression

| Stage | ID | What It Means |
|-------|-----|---------------|
| Discovery | 16634e86-5f37-4bda-85a0-336ad5c744d8 | Discovery call booked or completed. Opp created here by Calendly Zap. |
| Committed | 81519450-74be-4514-a718-24916aec33d1 | Merchant said yes but hasn't booked onboarding yet. NOT all opps go here -- skipping to Onboarding Scheduled is ideal. |
| Onboarding Scheduled | 96f0eb52-c557-45c8-b467-d2cce611ffb2 | Onboarding call booked via Calendly. Zap updates opp. |
| MPA & Underwriting | 3d89d46a-064b-4da0-8126-fd4685b84955 | Application submitted to processor bank. Waiting on approval. |
| Approved | 49522dbe-98b8-4f9e-8eee-06ae6d153955 | Processor approved. Need to set up gateway and go live. |
| Live | fdbd8d76-3cb9-481a-8bed-dc8d9b75cb0a | Merchant processing transactions. This is the revenue event. |
| Close Lost | a8b7e67f-6b14-490d-846d-8748812d052b | Deal dead. Close Lost Reason required. |
| Declined | 7270b22e-858c-497b-aebf-54cf82051b73 | Processor declined the application. |
| Churned | 7a6d180e-6826-4bbd-a180-3653781f005f | Was live, now inactive. |

### Valid Stage Progressions

Opps only move FORWARD. No backward movement without CEO approval.

Common paths:
- Discovery -> Committed -> Onboarding Scheduled -> MPA -> Approved -> Live
- Discovery -> Onboarding Scheduled -> MPA -> Approved -> Live (skips Committed -- this is the IDEAL outcome from a Discovery call where BAMFAM works)
- Onboarding Scheduled -> MPA -> Approved -> Live (existing clients who book onboarding directly without Discovery -- no Discovery data will exist and that's normal)
- Any stage -> Close Lost (deal dies at any point)
- MPA -> Declined (processor rejects)
- Live -> Churned (merchant stops processing)

---

## SLA Targets

| Transition | SLA | Owner | Enforcement |
|-----------|-----|-------|-------------|
| Discovery -> Committed or Onboarding | Decision on call (BAMFAM). If maybe, follow-up within 48 hours. | Sales (Henry) | WF1: Manual Action at 48 hours in Committed |
| Committed -> Onboarding Scheduled | 48 hours | Sales (Henry) | WF1: Manual Action fires |
| Onboarding call -> MPA submitted | 48 hours after onboarding call | Onboarding (Hannah) | WF2: Tasks at 48hr + 72hr |
| MPA submitted -> Approved | Bank-dependent. Follow up every business day x5. | Onboarding (Hannah) | WF3: Tasks daily x5 business days |
| Approved -> Live | 168 hours (7 days). Book integration call within 48 hours. | Sales (Henry) owns integration booking. | WF4: Manual Actions at 48hr, 4 days, 7 days |

### Critical Rules
- BAMFAM: Never end a call without the next step booked.
- No partial MPA submissions. All docs collected before submitting.
- Same-day response to processor notes/stipulations.
- No stage skipping (except Discovery->Onboarding Scheduled which is acceptable).
- Forward-only movement. No dragging opps backward.

---

## What's Automated vs What's Manual

### Automated (System Handles -- Fields Should Exist If Stage Reached)

| What | When It Fires | What It Does |
|------|--------------|-------------|
| Opp created in Discovery | Calendly Discovery booking | Zap creates opp with all form data: industry, volume, high ticket, website, appointment date, Calendly Event ID, appointment type = "Discovery", appointment status = "Scheduled" |
| Discovery Outcome = Closed Won | Calendly Onboarding booking | Onboarding Zap stamps this when merchant books onboarding |
| Opp moved to Onboarding Scheduled | Calendly Onboarding booking | Onboarding Zap moves opp, overwrites appointment type to "Onboarding", updates appointment date |
| Onboarding Completed Date stamped | Appointment Status set to "Completed" | GHL workflow WF0 stamps date. Only writes if field is empty. |
| Submitted Date stamped | Opp moves to MPA & Underwriting | GHL workflow stamps date. Only writes if field is empty. |
| Approval Date stamped | Opp moves to Approved | GHL workflow WF5 stamps date. Only writes if field is empty. |
| Discovery Outcome = Closed Lost | Opp moves to Close Lost or Declined | GHL workflow stamps this and sets status to Lost. |
| No-show/cancellation field updates | Calendly event fires | Atlas Event Handler writes Appointment Status and Discovery Outcome. |
| SLA follow-up actions fire | Opp sits past SLA window | Manual Actions (Sales) or Tasks (Onboarding) created automatically by GHL workflows. |

### Manual (Human Must Do -- If Missing, It's a Human Gap)

| Action | Who | When | Impact If Missing |
|--------|-----|------|------------------|
| Set Appointment Status = "Completed" after onboarding | Onboarding (Hannah) | Same day as onboarding call | Onboarding Completed Date never stamps. SLA timer to Submitted never starts. |
| Move opp to MPA & Underwriting | Onboarding (Hannah) | When MPA submitted to processor | Submitted Date never stamps. WF2/WF3 tasks never fire. |
| Move opp to Approved | Onboarding (Hannah) | When full approval received | Approval Date never stamps. WF4 Manual Actions never fire. |
| Move opp to Live | CS (Ism) | When first live transaction processes | Won status never triggers. Dashboard doesn't count it. |
| Move opp to Close Lost + select reason | Sales (Henry) | When prospect is dead | Distorts close rate. Dead opp clutters pipeline. |
| Set Processor field | Sales/Onboarding | During onboarding or before MPA | Hub app can't route correctly. Commission matching may fail. |

---

## Field Architecture

### Fields Set By Zaps at Booking (Should Exist From Opp Creation)

| Field | ID | Set By |
|-------|-----|--------|
| Calendly Event ID | U3dnzBS8MNAh8Gl6oj07 | Discovery Zap + Onboarding Zap |
| Appointment Type | g92GpfXFMxW9HmYbGIt0 | Discovery Zap (= "Discovery"), Onboarding Zap overwrites to "Onboarding" |
| Appointment Status | wEHbXwLTwbmHbLru1vC8 | Discovery Zap (= "Scheduled"), updated by Atlas or manual |
| Appointment Date | duqOLqU4YFdIsluC3NO1 | Discovery Zap, Onboarding Zap overwrites |
| Industry Type | iT881KYvOCWyTSXzqFEe | Discovery Zap (from Calendly form) |
| Monthly Volume | 6I29W6gfVhfdClb9uZA3 | Discovery Zap (parsed from range) |
| High Ticket | z8d4gF6TnVDBXS40g05g | Discovery Zap (from Calendly form) |
| Website | nJ4FZEwhuFzzzGlDB7WO | Discovery Zap (validated URL) |
| Discovery Scheduled Date | xAqJTd2AZJFmPIn3JuNc | Discovery Zap |

### Fields Set By GHL Workflows (Should Exist If Stage Reached)

| Field | ID | Trigger |
|-------|-----|---------|
| Discovery Outcome | uQpcrxwjsZ5kqnCe4pVj | Onboarding Zap sets "Closed Won", Close Lost workflow sets "Closed Lost", Atlas sets "No Show" |
| Onboarding Completed Date | wxaW6hw3bdhaUDJfzSNm | WF0 fires when Appointment Status = Completed |
| Submitted Date | 8XG9HFRJQSFsuu7eMveT | Workflow fires when opp moves to MPA stage |
| Approval Date | GmxvoOCpSCJ3ZWfaICsp | WF5 fires when opp moves to Approved |

### Fields Set Manually

| Field | ID | Set By |
|-------|-----|--------|
| Processor | hhQbzTtgTFsFT1ngiHCt | Sales or Onboarding |
| Close Lost Reason | (standard GHL field) | Sales |
| Live Date | XGdqLFLfHZo2Xd1DxjHs | CS when merchant goes live |

### Contact-Level Fields (Checked During Audit)

| Field | ID | Set By |
|-------|-----|--------|
| Lead Source (Contact) | ZCZS5FYR8bKBIySe94Wq | June during enrollment |
| Lead Referral Partner | KlqPOKN5BTg9NzEHjjW8 | June during enrollment |
| Email | (standard) | Calendly or manual |
| Phone | (standard) | Calendly (optional) or manual |

---

## Team and Ownership

| Name | GHL User ID | Role | Opp Stages They Own |
|------|-------------|------|-------------------|
| Henry Mashburn | OcuxaptjbljS6L2SnKbb | Sales | Discovery, Committed. Also owns Approved->Live integration booking (WF4). |
| Hannah Ness | MxNzXKj1RhdGMshfp9E5 | Onboarding/CSM | Onboarding Scheduled, MPA & Underwriting. Sometimes clears stipulations in Approved. |
| Ism Shovan | pEGvWEXTparQBFwZpLAB | CS/Approvals | Approved (primary), Live |
| Drew Brasiel | 8oVYzIxdHG8TGVpXc3Ma | CEO | Oversight. Pipeline triage. Exception approvals. |
| June Babael | MK5s94o3X9NASajdbX2j | EA | Lead enrollment, CRM hygiene |

**Note:** Opportunity owner stays as the sales rep (Henry) for reporting and attribution. Contact owner changes by stage for call routing. These are separate fields serving separate purposes.

---

## Accountability Workflows (What's Already Firing)

| Workflow | Trigger | Action | Assigned To |
|----------|---------|--------|-------------|
| WF1: Committed 48hr | Opp in Committed 48 hours | Manual Action: call to book onboarding | Sales |
| WF2: Onboarding to Submitted 48hr + 72hr | Onboarding Completed Date stamped | Tasks at 48hr and 72hr | Onboarding |
| WF3: Submitted to Approved daily x5 | Opp moves to MPA | Task daily for 5 business days | Onboarding |
| WF4: Approved to Live 48hr/4d/7d | Opp moves to Approved | 3 Manual Actions: book integration | Sales |
| Discovery Cancellation Handler | Appointment Status = "Cancelled" + Type = "Discovery" | Manual Action: rebook Discovery | Sales |
| Onboarding Cancellation Handler | Appointment Status = "Cancelled" + Type = "Onboarding" | Manual Action: rebook Onboarding | Sales |
| Onboarding No-Show Handler | Appointment Status = "No-Show" + Type = "Onboarding" | Manual Action: recovery call (30 min due) | Sales |
| No-Show Recovery Cadence | Discovery Outcome = "No Show" | 3 Manual Actions + SMS/email | Sales |

**Manual Actions have 3 states only:** Pending, Completed, Missed. No rescheduling. This was a deliberate design choice to prevent gaming.

---

*This document is Atlas's operational context. When pipeline rules change, update this file and re-paste to Claude Code.*
