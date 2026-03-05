"""Audit rules — stage-required fields, stale thresholds, skip stages.

All GHL field IDs and stage IDs from TECHNICAL_REFERENCE.md.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

STAGE_DISCOVERY = "16634e86-5f37-4bda-85a0-336ad5c744d8"
STAGE_COMMITTED = "81519450-74be-4514-a718-24916aec33d1"
STAGE_ONBOARDING_SCHEDULED = "96f0eb52-c557-45c8-b467-d2cce611ffb2"
STAGE_MPA_UNDERWRITING = "3d89d46a-064b-4da0-8126-fd4685b84955"
STAGE_APPROVED = "49522dbe-98b8-4f9e-8eee-06ae6d153955"
STAGE_LIVE = "fdbd8d76-3cb9-481a-8bed-dc8d9b75cb0a"
STAGE_CLOSE_LOST = "a8b7e67f-6b14-490d-846d-8748812d052b"
STAGE_DECLINED = "7270b22e-858c-497b-aebf-54cf82051b73"
STAGE_CHURNED = "7a6d180e-6826-4bbd-a180-3653781f005f"

SKIP_STAGES = {STAGE_CLOSE_LOST, STAGE_DECLINED, STAGE_CHURNED}

STAGE_NAMES: dict[str, str] = {
    STAGE_DISCOVERY: "Discovery",
    STAGE_COMMITTED: "Committed",
    STAGE_ONBOARDING_SCHEDULED: "Onboarding Scheduled",
    STAGE_MPA_UNDERWRITING: "MPA & Underwriting",
    STAGE_APPROVED: "Approved",
    STAGE_LIVE: "Live",
    STAGE_CLOSE_LOST: "Close Lost",
    STAGE_DECLINED: "Declined",
    STAGE_CHURNED: "Churned",
}

# ---------------------------------------------------------------------------
# Stale deal thresholds (days)
# ---------------------------------------------------------------------------

STALE_THRESHOLDS: dict[str, int] = {
    STAGE_DISCOVERY: 7,
    STAGE_COMMITTED: 5,
    STAGE_ONBOARDING_SCHEDULED: 14,
    STAGE_MPA_UNDERWRITING: 14,
    STAGE_APPROVED: 7,
}

# ---------------------------------------------------------------------------
# GHL custom field IDs
# ---------------------------------------------------------------------------

FIELD_APPOINTMENT_TYPE = "g92GpfXFMxW9HmYbGIt0"
FIELD_APPOINTMENT_STATUS = "wEHbXwLTwbmHbLru1vC8"
FIELD_APPOINTMENT_DATE = "duqOLqU4YFdIsluC3NO1"
FIELD_INDUSTRY_TYPE = "iT881KYvOCWyTSXzqFEe"
FIELD_MONTHLY_VOLUME = "6I29W6gfVhfdClb9uZA3"
FIELD_HIGH_TICKET = "z8d4gF6TnVDBXS40g05g"
FIELD_CALENDLY_EVENT_ID = "U3dnzBS8MNAh8Gl6oj07"
FIELD_WEBSITE = "nJ4FZEwhuFzzzGlDB7WO"
FIELD_DISCOVERY_OUTCOME = "uQpcrxwjsZ5kqnCe4pVj"
FIELD_SUBMITTED_DATE = "8XG9HFRJQSFsuu7eMveT"
FIELD_APPROVAL_DATE = "GmxvoOCpSCJ3ZWfaICsp"
FIELD_LIVE_DATE = "XGdqLFLfHZo2Xd1DxjHs"

# Contact-level
FIELD_LEAD_SOURCE = "ZCZS5FYR8bKBIySe94Wq"

# ---------------------------------------------------------------------------
# Field display names (for audit messages)
# ---------------------------------------------------------------------------

FIELD_NAMES: dict[str, str] = {
    FIELD_APPOINTMENT_TYPE: "Appointment Type",
    FIELD_APPOINTMENT_STATUS: "Appointment Status",
    FIELD_APPOINTMENT_DATE: "Appointment Date",
    FIELD_INDUSTRY_TYPE: "Industry Type",
    FIELD_MONTHLY_VOLUME: "Monthly Volume",
    FIELD_HIGH_TICKET: "High Ticket",
    FIELD_CALENDLY_EVENT_ID: "Calendly Event ID",
    FIELD_WEBSITE: "Website",
    FIELD_DISCOVERY_OUTCOME: "Discovery Outcome",
    FIELD_SUBMITTED_DATE: "Submitted Date",
    FIELD_APPROVAL_DATE: "Approval Date",
    FIELD_LIVE_DATE: "Live Date",
    FIELD_LEAD_SOURCE: "Lead Source",
}

# ---------------------------------------------------------------------------
# Stage-required fields matrix (inheritance model)
# Discovery base + each subsequent stage adds its own
# ---------------------------------------------------------------------------

# Base fields required at every active stage
_BASE_FIELDS = [
    FIELD_APPOINTMENT_TYPE,
    FIELD_APPOINTMENT_STATUS,
    FIELD_APPOINTMENT_DATE,
    FIELD_INDUSTRY_TYPE,
    FIELD_MONTHLY_VOLUME,
    FIELD_HIGH_TICKET,
    FIELD_CALENDLY_EVENT_ID,
    FIELD_WEBSITE,
]

STAGE_REQUIRED_FIELDS: dict[str, list[str]] = {
    STAGE_DISCOVERY: _BASE_FIELDS.copy(),
    STAGE_COMMITTED: _BASE_FIELDS + [FIELD_DISCOVERY_OUTCOME],
    STAGE_ONBOARDING_SCHEDULED: _BASE_FIELDS + [FIELD_DISCOVERY_OUTCOME],
    STAGE_MPA_UNDERWRITING: _BASE_FIELDS + [FIELD_DISCOVERY_OUTCOME, FIELD_SUBMITTED_DATE],
    STAGE_APPROVED: _BASE_FIELDS + [FIELD_DISCOVERY_OUTCOME, FIELD_SUBMITTED_DATE, FIELD_APPROVAL_DATE],
    STAGE_LIVE: _BASE_FIELDS + [FIELD_DISCOVERY_OUTCOME, FIELD_SUBMITTED_DATE, FIELD_APPROVAL_DATE, FIELD_LIVE_DATE],
}

# Placeholder opportunity name to flag
PLACEHOLDER_OPP_NAME = "New Merchant - Update Name"
