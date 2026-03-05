"""Audit engine — scans GHL pipeline and checks for issues.

Produces structured findings for missing fields, stale deals, overdue tasks,
and contact-level problems. Grouped by assigned user for digest output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog

from app.core.clients.ghl import GHLClient
from app.modules.audit.rules import (
    AUDIT_CUTOFF_DATE,
    FIELD_LEAD_SOURCE,
    FIELD_NAMES,
    PLACEHOLDER_OPP_NAME,
    SKIP_STAGES,
    STAGE_NAMES,
    STAGE_REQUIRED_FIELDS,
    STALE_THRESHOLDS,
)

log = structlog.get_logger()


@dataclass
class AuditFinding:
    """A single audit issue."""

    category: str  # "missing_field", "stale_deal", "overdue_task", "contact_issue", "name_issue"
    opp_id: str
    opp_name: str
    stage: str
    assigned_to: str  # GHL user ID or "Unassigned"
    description: str
    field_name: str | None = None
    suggested_action: str | None = None


@dataclass
class AuditResult:
    """Complete audit run output."""

    total_opportunities: int = 0
    total_issues: int = 0
    findings: list[AuditFinding] = field(default_factory=list)
    missing_fields: list[AuditFinding] = field(default_factory=list)
    stale_deals: list[AuditFinding] = field(default_factory=list)
    overdue_tasks: list[AuditFinding] = field(default_factory=list)
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
                val = cf.get("value", "")
                if val and str(val).strip():
                    return str(val).strip()
                return None
    elif isinstance(custom_fields, dict):
        val = custom_fields.get(field_id, "")
        if val and str(val).strip():
            return str(val).strip()
    return None


def _get_assigned_user(opp: dict) -> str:
    """Extract assigned user ID from opportunity."""
    return opp.get("assignedTo", "") or "Unassigned"


async def run_audit(ghl_client: GHLClient) -> AuditResult:
    """Execute full pipeline audit — returns structured findings."""
    now = datetime.now(UTC)
    result = AuditResult(run_timestamp=now.isoformat())

    log.info("audit_start")

    # Fetch all open opportunities
    opportunities = await ghl_client.search_opportunities(status="open")
    result.total_opportunities = len(opportunities)
    log.info("audit_fetched_opportunities", count=len(opportunities))

    for opp in opportunities:
        opp_id = opp.get("id", "")
        opp_name = opp.get("name", "Unknown")
        stage_id = opp.get("pipelineStageId", "")
        stage_name = STAGE_NAMES.get(stage_id, stage_id)
        assigned_to = _get_assigned_user(opp)

        # AUDIT-08: Skip closed/declined/churned
        if stage_id in SKIP_STAGES:
            continue

        # Grandfather cutoff — skip missing field checks for old deals
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

        # AUDIT-07: Check placeholder name
        if opp_name == PLACEHOLDER_OPP_NAME:
            finding = AuditFinding(
                category="name_issue",
                opp_id=opp_id,
                opp_name=opp_name,
                stage=stage_name,
                assigned_to=assigned_to,
                description="Opportunity still has placeholder name",
                suggested_action="Set real merchant name on this opportunity",
            )
            result.findings.append(finding)
            result.missing_fields.append(finding)

        # AUDIT-03: Missing required fields per stage
        # Skip missing field checks for grandfathered deals (created before fields existed)
        required_fields = STAGE_REQUIRED_FIELDS.get(stage_id, []) if not is_grandfathered else []
        for field_id in required_fields:
            value = _get_custom_field_value(opp, field_id)
            if not value:
                display_name = FIELD_NAMES.get(field_id, field_id)
                finding = AuditFinding(
                    category="missing_field",
                    opp_id=opp_id,
                    opp_name=opp_name,
                    stage=stage_name,
                    assigned_to=assigned_to,
                    description=f"Missing {display_name}",
                    field_name=display_name,
                    suggested_action=f"Set {display_name} on this opportunity",
                )
                result.findings.append(finding)
                result.missing_fields.append(finding)

        # AUDIT-06: Contact-level checks (skip for grandfathered deals)
        contact_id = opp.get("contactId", "")
        contact = opp.get("contact") or {}
        if not isinstance(contact, dict):
            contact = {}

        if contact_id and not is_grandfathered:
            email = contact.get("email", "")
            if not email:
                finding = AuditFinding(
                    category="contact_issue",
                    opp_id=opp_id,
                    opp_name=opp_name,
                    stage=stage_name,
                    assigned_to=assigned_to,
                    description="Contact missing email",
                    suggested_action="Add email address to contact record",
                )
                result.findings.append(finding)
                result.missing_fields.append(finding)

            # Lead Source check — it's a contact-level custom field
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
                    description="Contact missing Lead Source",
                    field_name="Lead Source",
                    suggested_action="Set Lead Source on contact record",
                )
                result.findings.append(finding)
                result.missing_fields.append(finding)

        # AUDIT-04: Stale deal detection
        threshold_days = STALE_THRESHOLDS.get(stage_id)
        if threshold_days:
            last_updated = opp.get("lastStageChangeAt") or opp.get("updatedAt") or opp.get("createdAt", "")
            if last_updated:
                try:
                    if isinstance(last_updated, str):
                        # Try ISO format parsing
                        updated_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                    else:
                        updated_dt = datetime.fromtimestamp(last_updated / 1000, tz=UTC)
                    days_in_stage = (now - updated_dt).days
                    if days_in_stage > threshold_days:
                        finding = AuditFinding(
                            category="stale_deal",
                            opp_id=opp_id,
                            opp_name=opp_name,
                            stage=stage_name,
                            assigned_to=assigned_to,
                            description=f"In {stage_name} for {days_in_stage} days (threshold: {threshold_days}d)",
                            suggested_action=f"Review and advance or close this opportunity",
                        )
                        result.findings.append(finding)
                        result.stale_deals.append(finding)
                except (ValueError, TypeError) as e:
                    log.warning("audit_date_parse_error", opp_id=opp_id, date=last_updated, error=str(e))

    # AUDIT-05: Overdue tasks — check via GHL tasks API
    # Note: GHL search_opportunities doesn't include tasks inline.
    # We need to fetch tasks per contact. To avoid excessive API calls,
    # we'll batch-check contacts that have opportunities.
    await _check_overdue_tasks(ghl_client, opportunities, result, now)

    result.total_issues = len(result.findings)
    log.info(
        "audit_complete",
        total_opps=result.total_opportunities,
        total_issues=result.total_issues,
        missing_fields=len(result.missing_fields),
        stale_deals=len(result.stale_deals),
        overdue_tasks=len(result.overdue_tasks),
    )

    return result


async def _check_overdue_tasks(
    ghl_client: GHLClient,
    opportunities: list[dict],
    result: AuditResult,
    now: datetime,
) -> None:
    """Check for overdue tasks across active opportunities."""
    # Collect unique contact IDs from non-skipped opps
    checked_contacts: set[str] = set()
    contact_opp_map: dict[str, dict] = {}  # contact_id -> first opp for context

    for opp in opportunities:
        stage_id = opp.get("pipelineStageId", "")
        if stage_id in SKIP_STAGES:
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

        for task in tasks:
            if task.get("completed"):
                continue
            due_date_str = task.get("dueDate", "")
            if not due_date_str:
                continue
            try:
                due_dt = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
                if due_dt < overdue_threshold:
                    opp_name = opp.get("name", "Unknown")
                    opp_id = opp.get("id", "")
                    stage_name = STAGE_NAMES.get(opp.get("pipelineStageId", ""), "Unknown")
                    assigned_to = _get_assigned_user(opp)
                    task_title = task.get("title") or task.get("body", "Untitled task")
                    days_overdue = (now - due_dt).days

                    finding = AuditFinding(
                        category="overdue_task",
                        opp_id=opp_id,
                        opp_name=opp_name,
                        stage=stage_name,
                        assigned_to=assigned_to,
                        description=f"Task overdue by {days_overdue}d: {task_title}",
                        suggested_action=f"Complete or reschedule task: {task_title}",
                    )
                    result.findings.append(finding)
                    result.overdue_tasks.append(finding)
            except (ValueError, TypeError):
                continue
