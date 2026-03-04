# Atlas Technical Reference — GHL API, Field IDs, Pipeline Structure

This file contains all technical reference data Atlas needs. Use for lookups when building API calls and field checks.

---

## GHL API Configuration

Base URL: https://services.leadconnectorhq.com
Location ID: l39XXt9HcdLTsuqTind6
Pipeline ID: V6mwUqamI0tGUm1GDvKD

Headers:
  Authorization: Bearer {GHL_API_KEY}
  Version: 2021-07-28
  Content-Type: application/json

## GHL API Endpoints

| Action | Method | Endpoint |
|--------|--------|----------|
| Search Opportunities | GET | /opportunities/search?location_id={id}&pipeline_id={id}&limit=100 |
| Get Opportunity | GET | /opportunities/{opportunity_id} |
| Update Opportunity | PUT | /opportunities/{opportunity_id} |
| Search Contacts | GET | /contacts/?locationId={id}&query={email_or_phone} |
| Get Contact | GET | /contacts/{contact_id} |
| Get Contact Tasks | GET | /contacts/{contact_id}/tasks |

Opportunity search pagination: use startAfter + startAfterId from last opp's sort[] array. Max 5 pages.

Custom field update format:
```json
{
  "customFields": [
    {"id": "field_id_here", "field_value": "value_here"}
  ]
}
```

Note: Use "field_value" not "value" in the API payload.

## Pipeline Stage IDs

| Stage | ID | SLA |
|-------|----|-----|
| Discovery | 16634e86-5f37-4bda-85a0-336ad5c744d8 | Stale: 7 days |
| Committed | 81519450-74be-4514-a718-24916aec33d1 | Stale: 5 days |
| Onboarding Scheduled | 96f0eb52-c557-45c8-b467-d2cce611ffb2 | Stale: 14 days |
| MPA & Underwriting | 3d89d46a-064b-4da0-8126-fd4685b84955 | Stale: 14 days |
| Approved | 49522dbe-98b8-4f9e-8eee-06ae6d153955 | Stale: 7 days |
| Live | fdbd8d76-3cb9-481a-8bed-dc8d9b75cb0a | N/A |
| Close Lost | a8b7e67f-6b14-490d-846d-8748812d052b | Skip in audit |
| Declined | 7270b22e-858c-497b-aebf-54cf82051b73 | Skip in audit |
| Churned | 7a6d180e-6826-4bbd-a180-3653781f005f | Skip in audit |

## GHL User IDs

| User | ID | Role |
|------|-----|------|
| Henry Mashburn | OcuxaptjbljS6L2SnKbb | Sales |
| Drew Brasiel | 8oVYzIxdHG8TGVpXc3Ma | CEO |

## Opportunity Custom Fields

| Field | ID | Type | Audit Rules |
|-------|----|------|-------------|
| Appointment Status | wEHbXwLTwbmHbLru1vC8 | Dropdown | Required all stages. Values: Scheduled, Completed, Cancelled, No-Show |
| Discovery Outcome | uQpcrxwjsZ5kqnCe4pVj | Dropdown | Required Committed+. Values: Closed Won, Closed Lost, No Show |
| Appointment Type | g92GpfXFMxW9HmYbGIt0 | Dropdown | Required all stages. Values: Discovery, Onboarding |
| Calendly Event ID | U3dnzBS8MNAh8Gl6oj07 | Text | Required all stages. Full Calendly event URI. Primary matching key. |
| Appointment Date | duqOLqU4YFdIsluC3NO1 | Date | Required all stages. YYYY-MM-DD |
| Industry Type | iT881KYvOCWyTSXzqFEe | Dropdown | Required all stages. Values: Hemp, Kratom/Kava, Mushroom, Nutraceuticals, Telehealth, Service-Based, High-Ticket, RD Peptides, Other |
| Monthly Volume | 6I29W6gfVhfdClb9uZA3 | Number | Required all stages. Must be > 0 |
| High Ticket | z8d4gF6TnVDBXS40g05g | Number | Required all stages. Must be > 0 |
| Website | nJ4FZEwhuFzzzGlDB7WO | Text | Required all stages. URL |
| Submitted Date | 8XG9HFRJQSFsuu7eMveT | Date | Required MPA+ |
| Approval Date | GmxvoOCpSCJ3ZWfaICsp | Date | Required Approved+ |
| Live Date | XGdqLFLfHZo2Xd1DxjHs | Date | Required Live |
| Booking Lead Time | eQPTewuZwkSoFl98DiXo | Number | Informational only, not audited |
| Discovery Scheduled Date | xAqJTd2AZJFmPIn3JuNc | Date | Informational only |
| Referral Partner (Opp) | RDjDNWwmidjPRgdTwbLT | Multi-Select | Informational only |
| Event Start | 3omkDJIZkxY1MiTDcbzr | Text | Informational only |
| Event End | mEx8ulPqTsa75dqKIpx5 | Text | Informational only |
| Event URI | hgK7YuL0C5gAoLCe0l76 | Text | Informational only |

## Contact Custom Fields

| Field | ID | Audit Rules |
|-------|----|-------------|
| Lead Source (Contact) | ZCZS5FYR8bKBIySe94Wq | Required. Values: Website Inbound, Direct Inbound, Referral Partner, Merchant Referral, Outbound, Trade Show/Event, Association, Other |
| Lead Referral Partner | KlqPOKN5BTg9NzEHjjW8 | Optional (depends on lead source) |

## Calendly Reference

Discovery event type name: "AHG Payments Discovery"
Discovery scheduling URL: https://calendly.com/hmashburn-althorizonsg/ahg-payments-discovery
Onboarding event type name: Contains "Onboarding"

Webhook subscription API: POST https://api.calendly.com/webhook_subscriptions
Events needed: invitee.canceled, invitee.no_show (organization scope)

## Environment Variables

| Var | Purpose |
|-----|---------|
| GHL_API_KEY | GHL API bearer token (pit-XXXXX format) |
| GHL_LOCATION_ID | l39XXt9HcdLTsuqTind6 |
| CALENDLY_API_KEY | Calendly personal access token |
| SLACK_WEBHOOK_URL | Slack incoming webhook for #sales-pipeline |
| CALENDLY_WEBHOOK_SECRET | Signing secret for webhook verification |

## Field Update Rules (Event Handler)

| Event | Field Updates |
|-------|--------------|
| Discovery No-Show | Discovery Outcome = "No Show", Appointment Status = "No-Show" |
| Onboarding No-Show | Appointment Status = "No-Show" |
| Discovery Cancellation | Appointment Status = "Cancelled" |
| Onboarding Cancellation | Appointment Status = "Cancelled" |

## Stage-Required Fields Matrix

| Field | Discovery | Committed | Onboarding Sched | MPA & UW | Approved | Live |
|-------|-----------|-----------|-------------------|----------|----------|------|
| Appointment Type | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Appointment Status | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Appointment Date | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Industry Type | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Monthly Volume | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| High Ticket | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Calendly Event ID | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Website | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Real Opp Name | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Discovery Outcome | | ✓ (Closed Won) | ✓ | ✓ | ✓ | ✓ |
| Submitted Date | | | | ✓ | ✓ | ✓ |
| Approval Date | | | | | ✓ | ✓ |
| Live Date | | | | | | ✓ |
| Lead Source (contact) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Email (contact) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

## Stale Deal Thresholds

| Stage | Days |
|-------|------|
| Discovery | 7 |
| Committed | 5 |
| Onboarding Scheduled | 14 |
| MPA & Underwriting | 14 |
| Approved | 7 |
