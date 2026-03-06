# GoHighLevel Field ID Reference

**Alternative Horizons Group - Backend Configuration**
*Last Updated: 3/6/2026 (imported from CEO reference doc)*

---

## Account Configuration

| Parameter | Value |
| --- | --- |
| Location ID | `l39XXt9HcdLTsuqTind6` |
| API Version Header | `Version: 2021-07-28` |
| Base URL | `https://services.leadconnectorhq.com` |

---

## Pipelines

### AHG Pipeline

**Pipeline ID:** `V6mwUqamI0tGUm1GDvKD`

| Stage | Stage ID | Position |
| --- | --- | --- |
| Discovery - 30% | `16634e86-5f37-4bda-85a0-336ad5c744d8` | 0 |
| Committed - 60% | `81519450-74be-4514-a718-24916aec33d1` | 1 |
| Pre-Application - 70% | `c8a3dcea-c549-446e-8dfa-9be1f5deea3f` | 2 |
| Onboarding Scheduled - 70% | `96f0eb52-c557-45c8-b467-d2cce611ffb2` | 3 |
| MPA & Underwriting - 80% | `3d89d46a-064b-4da0-8126-fd4685b84955` | 4 |
| Approved - 90% | `49522dbe-98b8-4f9e-8eee-06ae6d153955` | 5 |
| Live - 100% | `fdbd8d76-3cb9-481a-8bed-dc8d9b75cb0a` | 6 |
| Close Lost - 0% | `a8b7e67f-6b14-490d-846d-8748812d052b` | 7 |
| Declined - 0% | `7270b22e-858c-497b-aebf-54cf82051b73` | 8 |
| Churned | `7a6d180e-6826-4bbd-a180-3653781f005f` | 9 |

**Note:** Pre-Application (position 2) exists in GHL but is not actively used in the current pipeline flow.

---

## Team Member IDs

| Name | User ID | Role | Email | Slack ID |
| --- | --- | --- | --- | --- |
| Drew Brasiel | `8oVYzIxdHG8TGVpXc3Ma` | CEO | dbrasiel@althorizonsg.com | -- |
| Henry Mashburn | `OcuxaptjbljS6L2SnKbb` | CSO/Sales | hmashburn@althorizonsg.com | `U08H642F692` |
| Hannah Ness | `MxNzXKj1RhdGMshfp9E5` | Merchant Onboarding | hness@althorizonsg.com | -- |
| Ism Shovan | `pEGvWEXTparQBFwZpLAB` | CSM | ishovan@althorizonsg.com | -- |
| June Babael | `MK5s94o3X9NASajdbX2j` | EA | june@althorizonsg.com | -- |

---

## Slack Channels

| Channel | Channel ID |
| --- | --- |
| #sales-pipeline | `C08RBFA977B` |

---

## Calendar URLs

| Calendar | Owner | URL |
| --- | --- | --- |
| AHG Payments Discovery | Henry | `https://calendly.com/hmashburn-althorizonsg/ahg-payments-discovery` |
| AHG Payments Onboarding | Hannah | `https://calendly.com/hness-althorizonsg/ahg-payments-onboarding` |

---

## Custom Fields - Opportunity Level

### Calendly Integration

| Field Name | Field ID | Data Type | Purpose |
| --- | --- | --- | --- |
| Calendly Event ID | `U3dnzBS8MNAh8Gl6oj07` | Text | Unique event identifier |
| Calendar Event URI | `hgK7YuL0C5gAoLCe0l76` | Text | Calendly API URI |
| Scheduled Event Start Time | `3omkDJIZkxY1MiTDcbzr` | Text (ISO 8601) | Start timestamp |
| Scheduled Event End Time | `mEx8ulPqTsa75dqKIpx5` | Text (ISO 8601) | End timestamp |

### Appointment Tracking (Active/Current)

| Field Name | Field ID | Data Type | Example Values |
| --- | --- | --- | --- |
| Appointment Type | `g92GpfXFMxW9HmYbGIt0` | Text | Onboarding, Discovery, Follow-up |
| Appointment Date | `duqOLqU4YFdIsluC3NO1` | Text (YYYY-MM-DD) | 2026-01-05 |
| Appointment Status | `wEHbXwLTwbmHbLru1vC8` | Text | Scheduled, Completed, Cancelled, No-Show |
| Booking Lead Time | `eQPTewuZwkSoFl98DiXo` | Number | Hours from booking to appt |

### Sales Fields

| Field Name | Field ID | Data Type | Options |
| --- | --- | --- | --- |
| Lead Source | `5pobkX4Md3Fkc9ZZTwPL` | Dropdown | Website Inbound, Direct Inbound, Referral Partner, Merchant Referral, Outbound, Trade Show/Event, Association, Other |
| Industry Type | `iT881KYvOCWyTSXzqFEe` | Dropdown | Hemp, Kratom/Kava, Mushroom, Nutraceuticals, Telehealth, Service-Based, High-Ticket, Other |
| Discovery Scheduled Date | `xAqJTd2AZJFmPIn3JuNc` | Date | When discovery call was booked (persists forever) |
| Discovery Outcome | `uQpcrxwjsZ5kqnCe4pVj` | Dropdown | Closed Won, Closed Lost, No Show |
| Lead Created Date | `jiL8nmKX3NnjTbSR59lp` | Date/Time | Timestamp when form submitted |

### Key Dates (Historical - Never Overwrite)

| Field Name | Field ID | Data Type | Purpose |
| --- | --- | --- | --- |
| Submitted Date | `8XG9HFRJQSFsuu7eMveT` | Date | When MPA submitted to processor |
| Onboarding Completed Date | `wxaW6hw3bdhaUDJfzSNm` | Date/Time | Day/time Merchant Onboarding call takes place |
| Approval Date | `GmxvoOCpSCJ3ZWfaICsp` | Text (YYYY-MM-DD) | Processor approval date |
| Live Date | `XGdqLFLfHZo2Xd1DxjHs` | Text (YYYY-MM-DD) | Date merchant went live |

### Processor & Gateway

| Field Name | Field ID | Data Type | Example Values |
| --- | --- | --- | --- |
| Processor | `hhQbzTtgTFsFT1ngiHCt` | Text | West Town, Argyle, North |
| Equipment | `qpUiYKhJdO4vE8NS8xRB` | Multi-Select | NMI, Authorize.net, etc. |
| MID | `21Q10aVMgeKuBXMd6nSJ` | Text | 55555 |
| MCC | `3cOKKYWWrDcJTgVeBgsU` | Text | 5499 |

### Volume & Pricing

| Field Name | Field ID | Data Type |
| --- | --- | --- |
| Monthly Volume | `6I29W6gfVhfdClb9uZA3` | Number |
| High Ticket Limit | `z8d4gF6TnVDBXS40g05g` | Number |

### Referral Partner

| Field Name | Field ID | Data Type |
| --- | --- | --- |
| Referral Partner | `RDjDNWwmidjPRgdTwbLT` | Multi-Select |
| Paid? | `dwZeG0A3ycnMjvksnNvb` | Checkbox |

### Documentation

| Field Name | Field ID | Data Type |
| --- | --- | --- |
| Required Documents | `w2dBcgRrvGb4L81pvlk2` | Multi-Select |
| Processor Docs | `slgIGEm7AhNOMkaryJHm` | Multi-Select |
| Internal Notes | `b5QSNw1t19KTlGGBjwJA` | Text |

### CS POC (Contact Group Field)

| Field Name | Field ID |
| --- | --- |
| CS POC | `gbdC9KvPX1FCFekL3oNh` |
| CS POC > Name | `f4ae49ad-4c79-45f3-8540-6f9fda720c42` |
| CS POC > Email | `5fb22992-5a08-4b6c-9f22-5e4e6514e93f` |
| CS POC > Phone | `9e16e547-bad1-460b-a32e-2756fb268412` |

---

## Custom Fields - Contact Level

| Field Name | Field ID | Data Type |
| --- | --- | --- |
| Calendar Owner | `DmsZQ2NpWxtNRb3fvOSi` | Single Select |
| Lead Referral Partner | `KlqPOKN5BTg9NzEHjjW8` | Single Select |
| Lead Source | `ZCZS5FYR8bKBIySe94Wq` | Single Select |

---

## Field Architecture Notes

### Fields That Overwrite (Operational - Current State)

| Field | Purpose |
| --- | --- |
| Appointment Type | What's the NEXT appointment? |
| Appointment Date | When is the NEXT appointment? |
| Appointment Status | Status of CURRENT appointment |

### Fields That Persist (Historical - Never Overwrite)

| Field | Purpose |
| --- | --- |
| Discovery Scheduled Date | When discovery was booked |
| Discovery Outcome | Result of discovery call |
| Lead Created Date | When lead first came in |
| Submitted Date | When MPA submitted |
| Approval Date | When processor approved |
| Live Date | When first transaction processed |

---

## API Quick Reference

### Custom Field Update Format

```json
{
  "customFields": [
    {
      "id": "field_id_here",
      "field_value": "value_here"
    }
  ]
}
```

**Note:** Use `field_value` not `value` in the API payload.

---

*Maintained by: Drew Brasiel, CEO*
