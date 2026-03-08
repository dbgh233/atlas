"""Context-aware audit engine — scans GHL pipeline with pipeline intelligence.

Instead of checking "is this field empty?", asks "SHOULD this field be populated
based on what has already happened?" Uses appointment dates, stage timestamps,
and automation rules to eliminate false positives and classify findings by severity.

Severity levels:
  - system_failure: A Zap or GHL workflow should have set this. Broken automation.
  - human_gap: A human should have done this and the triggering event has occurred.
  - info: Heads-up — this will be needed soon but isn't blocking yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog

from app.core.clients.ghl import GHLClient
from app.modules.audit.rules import (
    AUDIT_CUTOFF_DATE,
    FIELD_APPOINTMENT_DATE,
    FIELD_APPOINTMENT_STATUS,
    FIELD_APPOINTMENT_TYPE,
    FIELD_APPROVAL_DATE,
    FIELD_CALENDLY_EVENT_ID,
    FIELD_DISCOVERY_OUTCOME,
    FIELD_DISCOVERY_SCHEDULED_DATE,
    FIELD_LEAD_SOURCE,
    FIELD_LIVE_DATE,
    FIELD_NAMES,
    FIELD_ONBOARDING_COMPLETED_DATE,
    FIELD_PROCESSOR,
    FIELD_SUBMITTED_DATE,
    PLACEHOLDER_OPP_NAME,
    SKIP_OPP_NAMES,
    SKIP_STAGES,
    STAGE_APPROVED,
    STAGE_CLOSE_LOST,
    STAGE_COMMITTED,
    STAGE_DISCOVERY,
    STAGE_LIVE,
    STAGE_MPA_UNDERWRITING,
    STAGE_NAMES,
    STAGE_ONBOARDING_SCHEDULED,
    ZAP_DISCOVERY_FIELDS,
    stage_at_or_past,
)

log = structlog.get_logger()


@dataclass
class AuditFinding:
    """A single audit issue with context-aware severity."""

    category: str  # "missing_field", "stale_deal", "overdue_task", "contact_issue", "name_issue", "close_lost_issue"
    opp_id: str
    opp_name: str
    stage: str
    assigned_to: str  # GHL user ID or "Unassigned"
    description: str
    field_name: str | None = None
    suggested_action: str | None = None
    severity: str = "human_gap"  # "system_failure", "human_gap", "info"
    suggested_value: str | None = None  # concrete value Atlas can suggest
    owner_hint: str | None = None  # who should fix this


@dataclass
class AuditResult:
    """Complete audit run output."""

    total_opportunities: int = 0
    total_issues: int = 0
    findings: list[AuditFinding] = field(default_factory=list)
    missing_fields: list[AuditFinding] = field(default_factory=list)
    stale_deals: list[AuditFinding] = field(default_factory=list)  # kept for backward compat, always empty
    overdue_tasks: list[AuditFinding] = field(default_factory=list)  # kept for backward compat, always empty
    overdue_task_counts: dict[str, int] = field(default_factory=dict)  # user_id -> count
    close_lost_missing_reason: int = 0  # count of Close Lost deals missing reason
    run_timestamp: str = ""


def _get_custom_field_value(opp: dict, field_id: str) -> str | None:
    """Extract a custom field value from a GHL opportunity."""
    custom_fields = opp.get("customFields")
    if not custom_fields or not isinstance(custom_fields, (list, dict)):
        return None
    if isinstance(custom_fields, list):
        for cf in custom_fields:
            if not isinstance(cf, dict):
                continue
            if cf.get("id") == field_id:
                # GHL uses "value" in search results and "fieldValue" in direct gets
                val = cf.get("value") or cf.get("fieldValue") or cf.get("field_value", "")
                # Debug: log what we found for Calendly Event ID field
                if field_id == "U3dnzBS8MNAh8Gl6oj07":
                    log.info("debug_custom_field", field_id=field_id, cf_keys=list(cf.keys()), cf_raw=cf, val=val, opp_name=opp.get("name"))
                if val and str(val).strip():
                    return str(val).strip()
                return None
    elif isinstance(custom_fields, dict):
        val = custom_fields.get(field_id, "")
        if val and str(val).strip():
            return str(val).strip()
    return None


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse a date string into a datetime, handling various formats."""
    if not date_str:
        return None
    try:
        if "T" in date_str:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        # YYYY-MM-DD format
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _get_assigned_user(opp: dict) -> str:
    """Extract assigned user ID from opportunity."""
    return opp.get("assignedTo", "") or "Unassigned"


def _has_discovery_data(opp: dict) -> bool:
    """Check if this opp went through Discovery (has Discovery Zap data).

    Opps booked directly into Onboarding (existing clients) won't have
    Discovery Scheduled Date or Discovery Zap fields — that's normal.
    """
    return _get_custom_field_value(opp, FIELD_DISCOVERY_SCHEDULED_DATE) is not None


def _appointment_date_passed(opp: dict, now: datetime) -> bool:
    """Check if the current appointment date has passed."""
    appt_date_str = _get_custom_field_value(opp, FIELD_APPOINTMENT_DATE)
    appt_dt = _parse_date(appt_date_str)
    if not appt_dt:
        return False
    return appt_dt < now


async def run_audit(ghl_client: GHLClient) -> AuditResult:
    """Execute full pipeline audit with context-aware field checks."""
    now = datetime.now(UTC)
    result = AuditResult(run_timestamp=now.isoformat())

    log.info("audit_start")

    opportunities = await ghl_client.search_opportunities(status="open")
    result.total_opportunities = len(opportunities)
    log.info("audit_fetched_opportunities", count=len(opportunities))

    for opp in opportunities:
        opp_id = opp.get("id", "")
        opp_name = opp.get("name", "Unknown")
        stage_id = opp.get("pipelineStageId", "")
        stage_name = STAGE_NAMES.get(stage_id, stage_id)
        assigned_to = _get_assigned_user(opp)

        # Skip excluded opp names (e.g. E2E test merchant)
        if opp_name in SKIP_OPP_NAMES:
            continue

        # Skip terminal/excluded stages (Pre-Application, Declined, Churned)
        if stage_id in SKIP_STAGES:
            continue

        # Close Lost: only check for close lost reason, skip all other checks
        if stage_id == STAGE_CLOSE_LOST:
            _check_close_lost_reason(opp, result)
            continue

        # Grandfather cutoff — skip field checks for old deals
        opp_created_at = opp.get("createdAt", "")
        is_grandfathered = False
        if AUDIT_CUTOFF_DATE and opp_created_at:
            try:
                if isinstance(opp_created_at, str):
                    created_dt = datetime.fromisoformat(opp_created_at.replace("Z", "+00:00"))
                else:
                    created_dt = datetime.fromtimestamp(opp_created_at / 1000, tz=UTC)
                is_grandfathered = created_dt < AUDIT_CUTOFF_DATE
            except (ValueError, TypeError):
                pass

        # Placeholder name check
        if opp_name == PLACEHOLDER_OPP_NAME:
            finding = AuditFinding(
                category="name_issue",
                opp_id=opp_id,
                opp_name=opp_name,
                stage=stage_name,
                assigned_to=assigned_to,
                description="Opportunity still has placeholder name",
                suggested_action="Set real merchant name on this opportunity",
                severity="human_gap",
            )
            result.findings.append(finding)
            result.missing_fields.append(finding)

        # Context-aware field checks (skip for grandfathered deals)
        if not is_grandfathered:
            _check_fields_contextual(opp, opp_id, opp_name, stage_id, stage_name, assigned_to, now, result)
            _check_contact_fields(opp, opp_id, opp_name, stage_name, assigned_to, result)

        # Stale deal detection removed — tracked via GHL tasks, noise in digest

    # Overdue tasks — count per user, no individual findings
    await _check_overdue_tasks(ghl_client, opportunities, result, now)

    result.total_issues = len(result.findings)
    log.info(
        "audit_complete",
        total_opps=result.total_opportunities,
        total_issues=result.total_issues,
        missing_fields=len(result.missing_fields),
        stale_deals=len(result.stale_deals),
        overdue_task_counts=result.overdue_task_counts,
        close_lost_missing_reason=result.close_lost_missing_reason,
    )

    return result


def _check_close_lost_reason(
    opp: dict,
    result: AuditResult,
) -> None:
    """Check if a Close Lost deal has a close reason recorded.

    GHL stores lost reason via opp.get('lostReasonId'). If missing,
    increment the counter. The exact field may need verification.
    """
    lost_reason_id = opp.get("lostReasonId")
    if not lost_reason_id:
        result.close_lost_missing_reason += 1


def _check_fields_contextual(
    opp: dict,
    opp_id: str,
    opp_name: str,
    stage_id: str,
    stage_name: str,
    assigned_to: str,
    now: datetime,
    result: AuditResult,
) -> None:
    """Run context-aware field checks based on pipeline logic.

    Asks: "Has the event that populates this field already occurred?"
    """
    has_discovery = _has_discovery_data(opp)
    appt_date_str = _get_custom_field_value(opp, FIELD_APPOINTMENT_DATE)
    appt_passed = _appointment_date_passed(opp, now)
    _get_custom_field_value(opp, FIELD_APPOINTMENT_TYPE)  # noqa: F841 — read but unused currently

    # -----------------------------------------------------------------------
    # 1. Zap-populated fields (Industry, Volume, High Ticket, Website, etc.)
    # These should exist from opp creation IF the opp came through a Zap booking.
    # -----------------------------------------------------------------------
    if has_discovery:
        # Opp came through Discovery Zap — all Zap fields should exist
        for field_id in ZAP_DISCOVERY_FIELDS:
            value = _get_custom_field_value(opp, field_id)
            if not value:
                display_name = FIELD_NAMES.get(field_id, field_id)
                finding = AuditFinding(
                    category="missing_field",
                    opp_id=opp_id,
                    opp_name=opp_name,
                    stage=stage_name,
                    assigned_to=assigned_to,
                    description=f"Missing {display_name} — Discovery Zap should have set this at booking",
                    field_name=display_name,
                    suggested_action=f"Check Calendly booking data for this merchant and set {display_name}. Possible Zap failure.",
                    severity="system_failure",
                )
                result.findings.append(finding)
                result.missing_fields.append(finding)
    elif stage_at_or_past(stage_id, STAGE_ONBOARDING_SCHEDULED):
        # Direct Onboarding booking (no Discovery) — Onboarding Zap should have set core fields
        for field_id in [FIELD_APPOINTMENT_TYPE, FIELD_APPOINTMENT_STATUS, FIELD_APPOINTMENT_DATE, FIELD_CALENDLY_EVENT_ID]:
            value = _get_custom_field_value(opp, field_id)
            if not value:
                display_name = FIELD_NAMES.get(field_id, field_id)
                finding = AuditFinding(
                    category="missing_field",
                    opp_id=opp_id,
                    opp_name=opp_name,
                    stage=stage_name,
                    assigned_to=assigned_to,
                    description=f"Missing {display_name} — Onboarding Zap should have set this",
                    field_name=display_name,
                    suggested_action="Check Calendly booking. Onboarding Zap may have failed.",
                    severity="system_failure",
                )
                result.findings.append(finding)
                result.missing_fields.append(finding)

    # -----------------------------------------------------------------------
    # 2. Discovery Outcome
    # -----------------------------------------------------------------------
    discovery_outcome = _get_custom_field_value(opp, FIELD_DISCOVERY_OUTCOME)
    if not discovery_outcome:
        if not has_discovery:
            # Direct Onboarding path — no Discovery data expected, SKIP
            pass
        elif stage_id == STAGE_DISCOVERY:
            # In Discovery — only flag if appointment date has passed
            if appt_passed:
                finding = AuditFinding(
                    category="missing_field",
                    opp_id=opp_id,
                    opp_name=opp_name,
                    stage=stage_name,
                    assigned_to=assigned_to,
                    description=f"Discovery call was {appt_date_str}, no outcome recorded",
                    field_name="Discovery Outcome",
                    suggested_action="Set Discovery Outcome after the call (Closed Won, Closed Lost, or No Show)",
                    severity="human_gap",
                    owner_hint="Sales",
                )
                result.findings.append(finding)
                result.missing_fields.append(finding)
            # else: appointment in future, skip
        elif stage_at_or_past(stage_id, STAGE_COMMITTED):
            if stage_at_or_past(stage_id, STAGE_ONBOARDING_SCHEDULED):
                finding = AuditFinding(
                    category="missing_field",
                    opp_id=opp_id,
                    opp_name=opp_name,
                    stage=stage_name,
                    assigned_to=assigned_to,
                    description="Discovery Outcome blank but opp is past Discovery — Onboarding Zap should have set 'Closed Won'",
                    field_name="Discovery Outcome",
                    suggested_action="Set Discovery Outcome to 'Closed Won'. Onboarding was booked, which means Discovery was won.",
                    severity="system_failure",
                    suggested_value="Closed Won",
                )
            else:
                finding = AuditFinding(
                    category="missing_field",
                    opp_id=opp_id,
                    opp_name=opp_name,
                    stage=stage_name,
                    assigned_to=assigned_to,
                    description="Discovery Outcome blank — should be set after Discovery call",
                    field_name="Discovery Outcome",
                    suggested_action="Set Discovery Outcome (Closed Won if merchant committed)",
                    severity="human_gap",
                    owner_hint="Sales",
                )
            result.findings.append(finding)
            result.missing_fields.append(finding)

    # -----------------------------------------------------------------------
    # 3. Onboarding Completed Date (set by WF0 when Appointment Status = Completed)
    # -----------------------------------------------------------------------
    onboarding_completed = _get_custom_field_value(opp, FIELD_ONBOARDING_COMPLETED_DATE)
    if not onboarding_completed and stage_at_or_past(stage_id, STAGE_ONBOARDING_SCHEDULED):
        if stage_at_or_past(stage_id, STAGE_MPA_UNDERWRITING):
            appt_status = _get_custom_field_value(opp, FIELD_APPOINTMENT_STATUS)
            if appt_status and appt_status.lower() == "completed":
                finding = AuditFinding(
                    category="missing_field",
                    opp_id=opp_id,
                    opp_name=opp_name,
                    stage=stage_name,
                    assigned_to=assigned_to,
                    description="Appointment Status is 'Completed' but Onboarding Completed Date not stamped — GHL workflow (WF0) may have failed",
                    field_name="Onboarding Completed Date",
                    suggested_action="Check GHL workflow WF0. It should stamp date when Appointment Status = Completed.",
                    severity="system_failure",
                )
            else:
                finding = AuditFinding(
                    category="missing_field",
                    opp_id=opp_id,
                    opp_name=opp_name,
                    stage=stage_name,
                    assigned_to=assigned_to,
                    description="Opp is past Onboarding but Appointment Status not marked 'Completed' — Onboarding Completed Date never stamped",
                    field_name="Onboarding Completed Date",
                    suggested_action="Mark Appointment Status = 'Completed' to stamp Onboarding Completed Date and start MPA submission SLA",
                    severity="human_gap",
                    owner_hint="Onboarding",
                )
            result.findings.append(finding)
            result.missing_fields.append(finding)
        elif stage_id == STAGE_ONBOARDING_SCHEDULED and appt_passed:
            appt_status = _get_custom_field_value(opp, FIELD_APPOINTMENT_STATUS)
            if not appt_status or appt_status.lower() not in ("completed", "cancelled", "no-show"):
                finding = AuditFinding(
                    category="missing_field",
                    opp_id=opp_id,
                    opp_name=opp_name,
                    stage=stage_name,
                    assigned_to=assigned_to,
                    description=f"Onboarding call was {appt_date_str}, Appointment Status not updated",
                    field_name="Onboarding Completed Date",
                    suggested_action="If onboarding call happened, mark Appointment Status = 'Completed'. This stamps Onboarding Completed Date.",
                    severity="human_gap",
                    owner_hint="Onboarding",
                )
                result.findings.append(finding)
                result.missing_fields.append(finding)

    # -----------------------------------------------------------------------
    # 4. Submitted Date (stamped by GHL workflow when opp moves to MPA)
    # -----------------------------------------------------------------------
    submitted_date = _get_custom_field_value(opp, FIELD_SUBMITTED_DATE)
    if not submitted_date and stage_at_or_past(stage_id, STAGE_MPA_UNDERWRITING):
        finding = AuditFinding(
            category="missing_field",
            opp_id=opp_id,
            opp_name=opp_name,
            stage=stage_name,
            assigned_to=assigned_to,
            description="Missing Submitted Date — GHL workflow should stamp when opp moves to MPA stage",
            field_name="Submitted Date",
            suggested_action="GHL workflow should have auto-stamped this. Verify opp was moved to MPA correctly, or manually set the date.",
            severity="system_failure",
        )
        result.findings.append(finding)
        result.missing_fields.append(finding)

    # -----------------------------------------------------------------------
    # 5. Approval Date (stamped by GHL workflow WF5 when opp moves to Approved)
    # -----------------------------------------------------------------------
    approval_date = _get_custom_field_value(opp, FIELD_APPROVAL_DATE)
    if not approval_date and stage_at_or_past(stage_id, STAGE_APPROVED):
        finding = AuditFinding(
            category="missing_field",
            opp_id=opp_id,
            opp_name=opp_name,
            stage=stage_name,
            assigned_to=assigned_to,
            description="Missing Approval Date — GHL workflow WF5 should stamp when opp moves to Approved",
            field_name="Approval Date",
            suggested_action="GHL workflow should have auto-stamped this. Verify opp was moved to Approved correctly.",
            severity="system_failure",
        )
        result.findings.append(finding)
        result.missing_fields.append(finding)

    # -----------------------------------------------------------------------
    # 6. Live Date (set manually by CS)
    # -----------------------------------------------------------------------
    live_date = _get_custom_field_value(opp, FIELD_LIVE_DATE)
    if not live_date and stage_id == STAGE_LIVE:
        finding = AuditFinding(
            category="missing_field",
            opp_id=opp_id,
            opp_name=opp_name,
            stage=stage_name,
            assigned_to=assigned_to,
            description="Missing Live Date — should be set when merchant processes first transaction",
            field_name="Live Date",
            suggested_action="Set Live Date to the date of first live transaction",
            severity="human_gap",
            owner_hint="CS",
        )
        result.findings.append(finding)
        result.missing_fields.append(finding)

    # -----------------------------------------------------------------------
    # 7. Processor (manual — needed before MPA submission)
    # -----------------------------------------------------------------------
    processor = _get_custom_field_value(opp, FIELD_PROCESSOR)
    if not processor:
        if stage_at_or_past(stage_id, STAGE_MPA_UNDERWRITING):
            finding = AuditFinding(
                category="missing_field",
                opp_id=opp_id,
                opp_name=opp_name,
                stage=stage_name,
                assigned_to=assigned_to,
                description="Missing Processor — required for MPA submission and Hub routing",
                field_name="Processor",
                suggested_action="Set Processor (West Town, Argyle, or North). Required for Hub app routing and commission matching.",
                severity="human_gap",
                owner_hint="Onboarding",
            )
            result.findings.append(finding)
            result.missing_fields.append(finding)
        elif stage_id == STAGE_ONBOARDING_SCHEDULED:
            finding = AuditFinding(
                category="missing_field",
                opp_id=opp_id,
                opp_name=opp_name,
                stage=stage_name,
                assigned_to=assigned_to,
                description="Processor not set yet — should be set before MPA submission",
                field_name="Processor",
                suggested_action="Set Processor before or during onboarding call. Needed for MPA submission.",
                severity="info",
                owner_hint="Sales",
            )
            result.findings.append(finding)
            result.missing_fields.append(finding)


def _check_contact_fields(
    opp: dict,
    opp_id: str,
    opp_name: str,
    stage_name: str,
    assigned_to: str,
    result: AuditResult,
) -> None:
    """Check contact-level fields."""
    contact = opp.get("contact") or {}
    if not isinstance(contact, dict):
        contact = {}

    contact_id = opp.get("contactId", "")
    if not contact_id:
        return

    # Email check
    email = contact.get("email", "")
    if not email:
        finding = AuditFinding(
            category="contact_issue",
            opp_id=opp_id,
            opp_name=opp_name,
            stage=stage_name,
            assigned_to=assigned_to,
            description="Contact missing email address",
            suggested_action="Add email to contact record — required for communications",
            severity="human_gap",
        )
        result.findings.append(finding)
        result.missing_fields.append(finding)

    # Lead Source check (contact-level custom field)
    contact_custom = contact.get("customFields")
    lead_source = None
    if isinstance(contact_custom, list):
        for cf in contact_custom:
            if isinstance(cf, dict) and cf.get("id") == FIELD_LEAD_SOURCE:
                val = cf.get("value", "")
                if val and str(val).strip():
                    lead_source = str(val).strip()
    if not lead_source:
        finding = AuditFinding(
            category="contact_issue",
            opp_id=opp_id,
            opp_name=opp_name,
            stage=stage_name,
            assigned_to=assigned_to,
            description="Contact missing Lead Source — needed for nurture cadence routing",
            field_name="Lead Source",
            suggested_action="Set Lead Source on contact. Without it, nurture workflow routes to wrong branch.",
            severity="human_gap",
            owner_hint="EA",
        )
        result.findings.append(finding)
        result.missing_fields.append(finding)


async def _check_overdue_tasks(
    ghl_client: GHLClient,
    opportunities: list[dict],
    result: AuditResult,
    now: datetime,
) -> None:
    """Check for overdue tasks across active opportunities.

    Instead of individual findings, tracks a count per user in
    result.overdue_task_counts for compact digest rendering.
    """
    checked_contacts: set[str] = set()
    contact_opp_map: dict[str, dict] = {}

    for opp in opportunities:
        stage_id = opp.get("pipelineStageId", "")
        if stage_id in SKIP_STAGES:
            continue
        # Skip Close Lost for task checking too
        if stage_id == STAGE_CLOSE_LOST:
            continue
        opp_name = opp.get("name", "Unknown")
        if opp_name in SKIP_OPP_NAMES:
            continue
        contact_id = opp.get("contactId", "")
        if contact_id and contact_id not in checked_contacts:
            checked_contacts.add(contact_id)
            contact_opp_map[contact_id] = opp

    overdue_threshold = now - timedelta(hours=24)

    for contact_id, opp in contact_opp_map.items():
        try:
            tasks = await ghl_client.get_contact_tasks(contact_id)
        except Exception as e:
            log.warning("audit_task_fetch_error", contact_id=contact_id, error=str(e))
            continue

        assigned_to = _get_assigned_user(opp)

        for task in tasks:
            if task.get("completed"):
                continue
            due_date_str = task.get("dueDate", "")
            if not due_date_str:
                continue
            try:
                due_dt = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
                if due_dt < overdue_threshold:
                    result.overdue_task_counts[assigned_to] = (
                        result.overdue_task_counts.get(assigned_to, 0) + 1
                    )
            except (ValueError, TypeError):
                continue
