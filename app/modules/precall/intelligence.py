"""Pre-call intelligence — morning briefs for sales reps before discovery calls.

Scans Calendly for today's upcoming calls, researches prospects using
available data (Calendly Q&A, GHL contacts, company websites), and
DMs the assigned rep on Slack with a rapport-focused pre-call brief.

Brief format adapts based on rep role:
- Sales reps get prospect research, rapport hooks, pain points, AHG positioning
- CS reps get account health, support tickets, integration status, open commitments
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher

import httpx
import structlog

from app.core.clients.calendly import CalendlyClient
from app.core.clients.claude import ClaudeClient
from app.core.clients.ghl import GHLClient
from app.core.clients.google_search import GoogleSearchClient
from app.core.clients.ninjapear import NinjaPearClient
from app.core.clients.ocean import OceanClient
from app.core.clients.serper import SerperClient
from app.core.clients.slack import SlackClient
from app.modules.precall.rep_profiles import (
    AHG_CONTEXT,
    get_rep_profile,
)

log = structlog.get_logger()

# Calendly event type names that indicate a sales call
CALL_KEYWORDS = [
    "discovery",
    "partner",
    "intro",
    "consultation",
    "demo",
    "sales",
    "meeting",
    "call",
    "onboarding",
]

# Event type names to skip (internal meetings, not prospect calls)
SKIP_KEYWORDS = [
    "pipeline triage",
    "pipeline review",
    "team sync",
    "standup",
    "internal",
    "1:1",
    "one on one",
]


def _is_prospect_call(event_name: str) -> bool:
    """Check if an event name indicates a prospect-facing call."""
    lower = event_name.lower()
    if any(skip in lower for skip in SKIP_KEYWORDS):
        return False
    return any(kw in lower for kw in CALL_KEYWORDS)


def _extract_domain(email: str) -> str | None:
    """Extract company domain from email, filtering out common free providers."""
    free_providers = {
        "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
        "aol.com", "icloud.com", "protonmail.com", "live.com",
        "mail.com", "zoho.com", "yandex.com",
    }
    try:
        domain = email.split("@")[1].lower()
        return domain if domain not in free_providers else None
    except (IndexError, AttributeError):
        return None


async def _fetch_website_snippet(domain: str, http_client: httpx.AsyncClient) -> str:
    """Try to fetch a website's homepage and extract useful text."""
    try:
        resp = await http_client.get(
            f"https://{domain}",
            follow_redirects=True,
            timeout=8.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Atlas/1.0)"},
        )
        if resp.status_code != 200:
            return ""
        html = resp.text[:20000]
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""
        desc_match = re.search(
            r'<meta[^>]*name=["\']description["\'][^>]*content=["\'](.*?)["\']',
            html, re.IGNORECASE,
        )
        desc = desc_match.group(1).strip() if desc_match else ""
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, re.IGNORECASE | re.DOTALL)
        body_text = " ".join(
            re.sub(r"<[^>]+>", "", p).strip()
            for p in paragraphs[:5]
        )
        parts = []
        if title:
            parts.append(f"Title: {title}")
        if desc:
            parts.append(f"Description: {desc}")
        if body_text:
            parts.append(f"About: {body_text[:500]}")
        return "\n".join(parts)
    except Exception as e:
        log.debug("website_fetch_failed", domain=domain, error=str(e))
        return ""


def _names_match(prospect_name: str, linkedin_name: str) -> bool:
    """Check if a LinkedIn result name plausibly matches the prospect.

    Uses fuzzy matching on first/last name components to avoid wrong-person
    LinkedIn profiles contaminating the brief.
    """
    if not prospect_name or not linkedin_name:
        return False

    prospect_parts = prospect_name.lower().strip().split()
    linkedin_parts = linkedin_name.lower().strip().split()

    if not prospect_parts or not linkedin_parts:
        return False

    # Check if first name matches (or is close)
    first_ratio = SequenceMatcher(None, prospect_parts[0], linkedin_parts[0]).ratio()
    if first_ratio < 0.7:
        return False

    # Check if last name matches (if both have one)
    if len(prospect_parts) > 1 and len(linkedin_parts) > 1:
        last_ratio = SequenceMatcher(None, prospect_parts[-1], linkedin_parts[-1]).ratio()
        if last_ratio < 0.7:
            return False

    return True


def _get_brief_type(rep_profile: dict | None) -> str:
    """Determine brief type based on rep role: 'sales', 'onboarding', or 'cs'."""
    if not rep_profile:
        return "sales"
    role = (rep_profile.get("role") or "").lower()
    if "onboarding" in role:
        return "onboarding"
    cs_keywords = ["customer success", "account manager", "implementation", "support"]
    if any(kw in role for kw in cs_keywords):
        return "cs"
    return "sales"


def _is_sales_rep(rep_profile: dict | None) -> bool:
    """Determine if a rep is in a sales role (vs customer success/onboarding)."""
    return _get_brief_type(rep_profile) == "sales"


async def _find_ghl_contact(
    ghl_client: GHLClient, email: str, name: str,
) -> dict | None:
    """Look up a prospect in GHL by email or name."""
    try:
        contacts = await ghl_client.search_contacts(email)
        if contacts:
            return contacts[0]
    except Exception:
        pass
    if name:
        try:
            contacts = await ghl_client.search_contacts(name)
            if contacts:
                return contacts[0]
        except Exception:
            pass
    return None


def _compute_confidence(prospect_data: dict) -> tuple[str, int]:
    """Compute a confidence score for the brief based on data availability.

    Returns (label, percentage) like ("High", 85).

    Scoring weights reflect actual value to brief quality:
    - Calendly Q&A is most valuable (prospect self-reported context)
    - LinkedIn + search results are key for rapport
    - Company data (website, Ocean.io) drives positioning
    - GHL CRM shows existing relationship depth
    """
    score = 0
    max_score = 100  # Fixed denominator for stable percentages

    # Name (baseline)
    if prospect_data.get("name"):
        score += 5

    # Email domain / company identified
    if prospect_data.get("company_domain"):
        score += 10

    # Website info (rich company context)
    if prospect_data.get("website_info"):
        score += 15

    # Calendly Q&A (highest value — prospect's own words)
    if prospect_data.get("calendly_answers"):
        score += 20

    # GHL CRM data (existing relationship data)
    if prospect_data.get("ghl_data"):
        score += 15

    # LinkedIn found (verified match — critical for rapport)
    if prospect_data.get("linkedin_url"):
        score += 15

    # Search results (general web presence)
    if prospect_data.get("google_search_info"):
        score += 8

    # Ocean.io company data
    if prospect_data.get("ocean_company_info"):
        score += 7

    # Ocean.io person data
    if prospect_data.get("ocean_person_info"):
        score += 5

    pct = min(score, 100)

    if pct >= 65:
        return "High", pct
    elif pct >= 35:
        return "Medium", pct
    else:
        return "Low", pct


# Slack mrkdwn formatting rules shared across all brief prompts.
_SLACK_FORMAT_RULES = """IMPORTANT FORMATTING RULES — you are writing for Slack, NOT standard markdown:
- Use *text* for bold (single asterisks, NOT double)
- Use _text_ for italic (underscores)
- Use bullet points with dashes (-)
- Do NOT use ## or ### headers — use *Bold Text* on its own line instead
- Do NOT use **text** — that does NOT work in Slack
- Use regular dashes (-) or double dashes (--), NOT em dashes or en dashes
- Use only basic ASCII characters — avoid special Unicode characters"""

# Sales brief: prospect research, rapport hooks, pain points, AHG positioning
SALES_BRIEF_PROMPT = """You are a pre-call intelligence analyst for AHG Payments.

AHG COMPANY CONTEXT:
{ahg_context}

A sales rep has an upcoming discovery/partner call with a prospect. Generate a concise,
actionable pre-call brief that helps the rep build rapport and position AHG effectively.

PROSPECT DATA:
{prospect_data}

REP PROFILE:
{rep_info}

""" + _SLACK_FORMAT_RULES + """

Generate a brief with these sections:

*Who They Are*
2-3 sentences. Be specific about what their company does, their role, and their likely
decision-making authority. If data is limited, say what we know and flag gaps.

*Rapport Hooks*
3-5 bullet points for the first 5-10 minutes. Prioritize:
- Specific overlaps between the rep's background and the prospect's (shared schools, cities, interests)
- Industry-specific talking points that show expertise
- Location or regional connections
- Any mutual connections or shared groups
Only include hooks grounded in actual data. If limited, suggest 2-3 natural industry openers.

*Their Likely Pain Points*
2-3 bullet points. Be specific to their industry and company size — generic "payment processing
is hard" is not useful. Connect pain points to what we actually know about them.

*How to Position AHG*
2-3 bullet points on which specific AHG capabilities will resonate. Reference their industry,
their current situation, and concrete differentiators (not generic value props).

*Heads Up*
Only include if there's something genuinely important to flag — regulatory risks for their
product type, recent negative press, compliance considerations, or contradictory data.
Skip this section entirely if there's nothing non-obvious to flag.

Keep it conversational and practical — like a trusted colleague giving a quick heads-up
before a call. Be honest when data is limited rather than making things up.
Keep the total brief under 350 words."""

# CS brief: account health, support context, integration status, open items
CS_BRIEF_PROMPT = """You are a customer success intelligence analyst for AHG Payments.

AHG COMPANY CONTEXT:
{ahg_context}

A Customer Success Manager has an upcoming call with an existing client. Generate a concise,
actionable pre-call brief focused on account health and preparation for the meeting.

CLIENT DATA:
{prospect_data}

CS REP PROFILE:
{rep_info}

""" + _SLACK_FORMAT_RULES + """

Generate a brief with these sections:

*Account Snapshot*
2-3 sentences. Company name, what they do, how long they've been with AHG, current processing
status. Include any key numbers (processing volume, transaction counts) if available.

*Meeting Context*
What this meeting is likely about based on the event type, any Q&A responses, and recent
account activity. Flag whether this is onboarding, integration support, account review, or
issue resolution.

*Open Items*
Bullet any outstanding items from CRM data, recent support interactions, or previous meeting
commitments. If none found, note "No open items found in available data."

*Account Health Signals*
2-3 bullet points on positive and negative signals:
- Processing stability (active, holds, reserves)
- Recent support interactions or escalations
- Integration status (gateway setup, POS, etc.)
- Any compliance or risk flags

*Rapport Hooks*
2-3 bullet points for personal connection. Prioritize specific overlaps between the CS rep's
background and the client's. Only include hooks grounded in actual data.

*Preparation Notes*
1-2 bullet points on what to have ready for this call (specific integrations, documentation,
account details to pull up).

Keep it concise and action-oriented. Focus on what the CS rep needs to DO, not just know.
Keep the total brief under 350 words."""

# Onboarding brief: timeline, documentation status, integration needs, handoff context
ONBOARDING_BRIEF_PROMPT = """You are an onboarding intelligence analyst for AHG Payments.

AHG COMPANY CONTEXT:
{ahg_context}

An Onboarding Specialist has an upcoming onboarding call with a new merchant. Generate a concise,
actionable pre-call brief focused on getting this merchant live quickly and smoothly.

MERCHANT DATA:
{prospect_data}

ONBOARDING REP PROFILE:
{rep_info}

""" + _SLACK_FORMAT_RULES + """

Generate a brief with these sections:

*Merchant Overview*
2-3 sentences. Company name, what they sell, industry vertical, estimated monthly volume,
high-ticket amount if known. Flag anything that affects onboarding (high-risk category,
multi-location, special integration needs).

*Onboarding Context*
What stage they're at, how they got here (referral source, sales rep who closed),
any commitments made during discovery that affect onboarding timeline or expectations.
Flag if they're currently processing elsewhere (migration vs new setup).

*Documentation Checklist*
Bullet what's likely needed based on their business type:
- Standard MPA items (ID, voided check, processing statements if migrating)
- Industry-specific docs (hemp license, COA requirements, supplement certifications)
- Any items flagged in CRM or Q&A responses

*Integration Needs*
What gateway/POS/platform setup will likely be needed based on their business model
(e-commerce, retail, both). Flag any known technical requirements.

*Key Numbers*
Bullet the critical numbers: monthly volume, high ticket, average ticket if known,
current processor if migrating. These drive underwriting decisions.

Keep it practical -- focus on what needs to happen to get this merchant processing.
Keep the total brief under 300 words."""


async def generate_precall_brief(
    claude_client: ClaudeClient,
    prospect_data: dict,
    rep_profile: dict | None,
) -> str:
    """Use Claude to generate a pre-call brief, adapted to the rep's role."""
    prospect_text = []
    if prospect_data.get("name"):
        prospect_text.append(f"Name: {prospect_data['name']}")
    if prospect_data.get("email"):
        prospect_text.append(f"Email: {prospect_data['email']}")
    if prospect_data.get("company_domain"):
        prospect_text.append(f"Company website: https://{prospect_data['company_domain']}")
    if prospect_data.get("website_info"):
        prospect_text.append(f"Website content:\n{prospect_data['website_info']}")
    if prospect_data.get("calendly_answers"):
        prospect_text.append(f"Calendly Q&A responses:\n{prospect_data['calendly_answers']}")
    if prospect_data.get("ghl_data"):
        prospect_text.append(f"CRM data:\n{prospect_data['ghl_data']}")
    if prospect_data.get("linkedin_url"):
        prospect_text.append(f"LinkedIn: {prospect_data['linkedin_url']}")
    if prospect_data.get("linkedin_profile_info"):
        prospect_text.append(f"LinkedIn profile summary: {prospect_data['linkedin_profile_info']}")
    if prospect_data.get("google_search_info"):
        prospect_text.append(f"Web search results:\n{prospect_data['google_search_info']}")
    if prospect_data.get("ocean_company_info"):
        prospect_text.append(f"Company intelligence (Ocean.io):\n{prospect_data['ocean_company_info']}")
    if prospect_data.get("ocean_person_info"):
        prospect_text.append(f"Person intelligence (Ocean.io):\n{prospect_data['ocean_person_info']}")
    if prospect_data.get("event_type"):
        prospect_text.append(f"Call type: {prospect_data['event_type']}")

    rep_text = []
    if rep_profile:
        rep_text.append(f"Name: {rep_profile.get('name', 'Unknown')}")
        rep_text.append(f"Role: {rep_profile.get('role', 'Account Executive')}")
        if rep_profile.get("location"):
            rep_text.append(f"Location: {rep_profile['location']}")
        if rep_profile.get("master_prompt"):
            rep_text.append(f"About this rep: {rep_profile['master_prompt']}")
        if rep_profile.get("personal_context"):
            rep_text.append(f"Personal context: {rep_profile['personal_context']}")
        if rep_profile.get("rapport_interests"):
            rep_text.append(f"Rep interests: {', '.join(rep_profile['rapport_interests'])}")
        if rep_profile.get("linkedin_url"):
            rep_text.append(f"Rep LinkedIn: {rep_profile['linkedin_url']}")

    # Build AHG context string
    ahg_text = (
        f"Company: {AHG_CONTEXT['company']}\n"
        f"Website: {AHG_CONTEXT['website']}\n"
        f"Verticals: {', '.join(AHG_CONTEXT['verticals'])}\n"
        f"Pain points we solve: {'; '.join(AHG_CONTEXT['pain_points_we_solve'][:4])}\n"
        f"Key value props: {'; '.join(AHG_CONTEXT['value_propositions'][:4])}\n"
        f"Differentiators: {'; '.join(AHG_CONTEXT['differentiators'][:3])}"
    )

    # Pick prompt based on rep role
    brief_type = _get_brief_type(rep_profile)
    if brief_type == "onboarding":
        template = ONBOARDING_BRIEF_PROMPT
    elif brief_type == "cs":
        template = CS_BRIEF_PROMPT
    else:
        template = SALES_BRIEF_PROMPT

    prompt = template.format(
        ahg_context=ahg_text,
        prospect_data="\n".join(prospect_text) if prospect_text else "Limited data available.",
        rep_info="\n".join(rep_text) if rep_text else "No additional rep info available.",
    )

    raw = await claude_client.ask(prompt)
    # Sanitize Unicode characters that display as garbled text in Slack
    sanitized = raw.replace("\u2014", " -- ").replace("\u2013", " - ")
    sanitized = sanitized.replace("\u2018", "'").replace("\u2019", "'")
    sanitized = sanitized.replace("\u201c", '"').replace("\u201d", '"')
    sanitized = sanitized.replace("\u2264", "<=").replace("\u2265", ">=")
    return sanitized


async def get_todays_calls(
    calendly_client: CalendlyClient,
) -> list[dict]:
    """Fetch today's upcoming Calendly events that look like prospect calls."""
    user_info = await calendly_client.get_current_user()
    org_uri = user_info.get("resource", {}).get("current_organization")
    if not org_uri:
        log.error("precall_no_org_uri")
        return []

    now = datetime.now(UTC)
    end_of_day = now.replace(hour=23, minute=59, second=59)

    events = await calendly_client.list_scheduled_events(
        organization_uri=org_uri,
        min_start_time=now.isoformat(),
        max_start_time=end_of_day.isoformat(),
        status="active",
    )

    prospect_calls = []
    for event in events:
        event_name = event.get("name", "")
        if not _is_prospect_call(event_name):
            continue

        event_uuid = event.get("uri", "").rstrip("/").split("/")[-1]
        start_time = event.get("start_time", "")

        invitees = []
        if event_uuid:
            try:
                invitees = await calendly_client.list_event_invitees(event_uuid)
            except Exception as e:
                log.warning("precall_invitees_failed", event_uuid=event_uuid, error=str(e))

        event_memberships = event.get("event_memberships", [])
        hosts = []
        for membership in event_memberships:
            member_name = membership.get("user_name", "") or membership.get("user", "")
            member_email = membership.get("user_email", "")
            if member_email:
                hosts.append({"name": member_name, "email": member_email})

        # For backwards compat, keep host_name/host_email as primary host
        host_name = hosts[0]["name"] if hosts else ""
        host_email = hosts[0]["email"] if hosts else ""

        prospect_calls.append({
            "event_uuid": event_uuid,
            "event_name": event_name,
            "start_time": start_time,
            "host_name": host_name,
            "host_email": host_email,
            "hosts": hosts,
            "invitees": invitees,
        })

    log.info("precall_todays_calls", total_events=len(events), prospect_calls=len(prospect_calls))
    return prospect_calls


async def run_morning_precall_briefs(
    calendly_client: CalendlyClient,
    claude_client: ClaudeClient,
    ghl_client: GHLClient,
    slack_client: SlackClient,
    http_client: httpx.AsyncClient,
    google_search_client: GoogleSearchClient | SerperClient | None = None,
    ocean_client: OceanClient | None = None,
    ninjapear_client: NinjaPearClient | None = None,
) -> dict:
    """Main orchestrator: fetch today's calls, research each prospect, DM the rep."""
    result = {
        "calls_found": 0,
        "briefs_sent": 0,
        "errors": [],
    }

    try:
        calls = await get_todays_calls(calendly_client)
    except Exception as e:
        result["errors"].append(f"Failed to fetch Calendly events: {e}")
        log.error("precall_fetch_failed", error=str(e))
        return result

    result["calls_found"] = len(calls)

    for call in calls:
        try:
            await _process_single_call(
                call=call,
                claude_client=claude_client,
                ghl_client=ghl_client,
                slack_client=slack_client,
                http_client=http_client,
                google_search_client=google_search_client,
                ocean_client=ocean_client,
                ninjapear_client=ninjapear_client,
            )
            result["briefs_sent"] += 1
        except Exception as e:
            error_msg = f"Failed to process {call.get('event_name', '?')}: {e}"
            result["errors"].append(error_msg)
            log.error("precall_process_failed", event=call.get("event_name"), error=str(e))

    log.info(
        "precall_morning_complete",
        calls=result["calls_found"],
        briefs=result["briefs_sent"],
        errors=len(result["errors"]),
    )
    return result


async def run_precall_dry_run(
    calendly_client: CalendlyClient,
    claude_client: ClaudeClient,
    ghl_client: GHLClient,
    http_client: httpx.AsyncClient,
    google_search_client: GoogleSearchClient | SerperClient | None = None,
    ocean_client: OceanClient | None = None,
) -> dict:
    """Generate briefs but return them as JSON instead of sending to Slack.

    Used for testing and reviewing output before going live.
    """
    result: dict = {
        "calls_found": 0,
        "briefs": [],
        "errors": [],
    }

    try:
        calls = await get_todays_calls(calendly_client)
    except Exception as e:
        result["errors"].append(f"Failed to fetch Calendly events: {e}")
        return result

    result["calls_found"] = len(calls)

    for call in calls:
        host_email = call.get("host_email", "")
        rep_profile = get_rep_profile(host_email)
        invitees = call.get("invitees", [])

        for invitee in invitees:
            prospect_name = invitee.get("name", "")
            prospect_email = invitee.get("email", "")
            if not prospect_email:
                continue

            try:
                prospect_data = await _gather_prospect_data(
                    invitee, call, ghl_client, http_client,
                    google_search_client, ocean_client,
                )
                confidence_label, confidence_pct = _compute_confidence(prospect_data)
                brief = await generate_precall_brief(claude_client, prospect_data, rep_profile)
                time_str = _format_time_est(call.get("start_time", ""))

                # Build the same message that would be DM'd
                dm_text = _build_dm_message(
                    prospect_name or prospect_email,
                    call,
                    time_str,
                    confidence_label,
                    confidence_pct,
                    prospect_data,
                    brief,
                )

                result["briefs"].append({
                    "rep": call.get("host_name", "Unknown"),
                    "rep_slack_id": rep_profile.get("slack_user_id") if rep_profile else None,
                    "prospect": prospect_name,
                    "prospect_email": prospect_email,
                    "call_time": time_str,
                    "confidence": f"{confidence_label} ({confidence_pct}%)",
                    "slack_message": dm_text,
                    "data_sources": {
                        "calendly_qa": bool(prospect_data.get("calendly_answers")),
                        "website": bool(prospect_data.get("website_info")),
                        "ghl_crm": bool(prospect_data.get("ghl_data")),
                        "google_search": bool(prospect_data.get("google_search_info")),
                        "ocean_company": bool(prospect_data.get("ocean_company_info")),
                        "ocean_person": bool(prospect_data.get("ocean_person_info")),
                        "linkedin_url": prospect_data.get("linkedin_url"),
                        "company_domain": prospect_data.get("company_domain"),
                    },
                })
            except Exception as e:
                result["errors"].append(f"Failed: {prospect_name} — {e}")

    return result


def _format_time_est(iso_time: str) -> str:
    """Convert ISO time to a readable EST time string."""
    try:
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        # Convert to EST (UTC-5)
        est_dt = dt - timedelta(hours=5)
        return est_dt.strftime("%I:%M %p").lstrip("0") + " EST"
    except Exception:
        return iso_time


async def _gather_prospect_data(
    invitee: dict,
    call: dict,
    ghl_client: GHLClient,
    http_client: httpx.AsyncClient,
    google_search_client: GoogleSearchClient | SerperClient | None = None,
    ocean_client: OceanClient | None = None,
) -> dict:
    """Gather all available data about a prospect from all sources."""
    prospect_name = invitee.get("name", "")
    prospect_email = invitee.get("email", "")

    prospect_data: dict = {
        "name": prospect_name,
        "email": prospect_email,
        "event_type": call.get("event_name", ""),
        "call_time": call.get("start_time", ""),
    }

    # Extract Q&A responses from Calendly
    questions_and_answers = invitee.get("questions_and_answers", [])
    if questions_and_answers:
        qa_text = "\n".join(
            f"Q: {qa.get('question', '?')}\nA: {qa.get('answer', 'N/A')}"
            for qa in questions_and_answers
        )
        prospect_data["calendly_answers"] = qa_text

    # Company domain from email
    domain = _extract_domain(prospect_email)
    if domain:
        prospect_data["company_domain"] = domain
        website_info = await _fetch_website_snippet(domain, http_client)
        if website_info:
            prospect_data["website_info"] = website_info

    # Look up in GHL
    ghl_contact = await _find_ghl_contact(ghl_client, prospect_email, prospect_name)
    if ghl_contact:
        ghl_parts = []
        if ghl_contact.get("companyName"):
            ghl_parts.append(f"Company: {ghl_contact['companyName']}")
        if ghl_contact.get("city") or ghl_contact.get("state"):
            loc = ", ".join(filter(None, [ghl_contact.get("city"), ghl_contact.get("state")]))
            ghl_parts.append(f"Location: {loc}")
        if ghl_contact.get("tags"):
            ghl_parts.append(f"Tags: {', '.join(ghl_contact['tags'])}")
        if ghl_contact.get("source"):
            ghl_parts.append(f"Lead source: {ghl_contact['source']}")
        if ghl_contact.get("website"):
            ghl_parts.append(f"Website: {ghl_contact['website']}")
        custom_fields = ghl_contact.get("customFields", []) or ghl_contact.get("customField", {})
        if isinstance(custom_fields, dict):
            for k, v in custom_fields.items():
                if v:
                    ghl_parts.append(f"{k}: {v}")
        if ghl_parts:
            prospect_data["ghl_data"] = "\n".join(ghl_parts)

    # Web search enrichment (prospect + company) via Serper or Google
    if google_search_client and prospect_name:
        try:
            search_result = await google_search_client.search_prospect(
                prospect_name, domain,
            )
            if search_result.get("results"):
                snippets = "\n".join(
                    f"- {r['title']}: {r['snippet']}" for r in search_result["results"][:3]
                )
                prospect_data["google_search_info"] = snippets

            # LinkedIn URL validation — only accept if the name on the result
            # plausibly matches the prospect to avoid wrong-person contamination
            li_url = search_result.get("linkedin_url")
            li_snippet = search_result.get("linkedin_snippet", "")
            if li_url:
                # Extract name from LinkedIn URL path or snippet title
                li_name = ""
                for r in search_result.get("results", []):
                    if r.get("link") == li_url:
                        # LinkedIn titles are typically "Name - Title - Company | LinkedIn"
                        li_name = r.get("title", "").split(" - ")[0].split(" | ")[0].strip()
                        break
                if li_name and _names_match(prospect_name, li_name):
                    prospect_data["linkedin_url"] = li_url
                    if li_snippet:
                        prospect_data["linkedin_profile_info"] = li_snippet
                    log.info("linkedin_match_verified", prospect=prospect_name, linkedin_name=li_name)
                elif not li_name:
                    # Can't verify — include but flag
                    prospect_data["linkedin_url"] = li_url
                    if li_snippet:
                        prospect_data["linkedin_profile_info"] = li_snippet
                    log.info("linkedin_match_unverified", prospect=prospect_name, url=li_url)
                else:
                    log.info(
                        "linkedin_match_rejected",
                        prospect=prospect_name,
                        linkedin_name=li_name,
                        url=li_url,
                    )
        except Exception as e:
            log.warning("precall_search_failed", name=prospect_name, error=str(e))

    # Ocean.io enrichment (company + person)
    if ocean_client:
        if domain:
            try:
                company_data = await ocean_client.enrich_company(domain)
                if company_data:
                    ocean_parts = []
                    if company_data.get("name"):
                        ocean_parts.append(f"Company: {company_data['name']}")
                    if company_data.get("description"):
                        ocean_parts.append(f"About: {company_data['description']}")
                    if company_data.get("industries"):
                        ocean_parts.append(f"Industries: {', '.join(company_data['industries'][:3])}")
                    elif company_data.get("linkedin_industry"):
                        ocean_parts.append(f"Industry: {company_data['linkedin_industry']}")
                    if company_data.get("company_size"):
                        ocean_parts.append(f"Company size: {company_data['company_size']}")
                    if company_data.get("revenue"):
                        ocean_parts.append(f"Revenue: {company_data['revenue']}")
                    if company_data.get("keywords"):
                        ocean_parts.append(f"Keywords: {', '.join(company_data['keywords'][:8])}")
                    if ocean_parts:
                        prospect_data["ocean_company_info"] = "\n".join(ocean_parts)
            except Exception as e:
                log.warning("precall_ocean_company_failed", domain=domain, error=str(e))

        if prospect_name:
            try:
                person_data = await ocean_client.enrich_person(prospect_name, domain)
                if person_data:
                    person_parts = []
                    if person_data.get("job_title"):
                        person_parts.append(f"Title: {person_data['job_title']}")
                    if person_data.get("location"):
                        person_parts.append(f"Location: {person_data['location']}")
                    if person_data.get("linkedin_url") and not prospect_data.get("linkedin_url"):
                        prospect_data["linkedin_url"] = person_data["linkedin_url"]
                    if person_data.get("experiences"):
                        exp_text = "; ".join(
                            f"{e['title']} at {e['company']}" for e in person_data["experiences"] if e.get("title")
                        )
                        if exp_text:
                            person_parts.append(f"Experience: {exp_text}")
                    if person_parts:
                        prospect_data["ocean_person_info"] = "\n".join(person_parts)
            except Exception as e:
                log.warning("precall_ocean_person_failed", name=prospect_name, error=str(e))

    return prospect_data


def _build_dm_message(
    display_name: str,
    call: dict,
    time_str: str,
    confidence_label: str,
    confidence_pct: int,
    prospect_data: dict,
    brief: str,
) -> str:
    """Build a formatted Slack DM message for a pre-call brief."""
    domain = prospect_data.get("company_domain")
    linkedin_url = prospect_data.get("linkedin_url")

    # Build links section — use actual LinkedIn URL when found, fallback to search
    links = []
    if domain:
        links.append(f"<https://{domain}|:globe_with_meridians: Website>")
    if linkedin_url:
        links.append(f"<{linkedin_url}|:bust_in_silhouette: LinkedIn>")
    else:
        prospect_name = prospect_data.get("name", "")
        if prospect_name:
            search_name = prospect_name.replace(" ", "%20")
            links.append(
                f"<https://www.linkedin.com/search/results/all/?keywords={search_name}|:mag: LinkedIn Search>"
            )

    # Confidence emoji
    conf_emoji = ":large_green_circle:" if confidence_label == "High" else (
        ":large_yellow_circle:" if confidence_label == "Medium" else ":red_circle:"
    )

    dm_parts = [
        f":crystal_ball:  *Pre-Call Brief  |  {display_name}*",
        f"_{call.get('event_name', 'Call')}  at  {time_str}_",
        "",
        f"{conf_emoji} *Data Confidence:* {confidence_label} ({confidence_pct}%)",
    ]

    if links:
        dm_parts.append("  ".join(links))

    dm_parts.append("")
    dm_parts.append(brief)

    # Data source attribution footer
    sources = []
    if prospect_data.get("calendly_answers"):
        sources.append("Calendly Q&A")
    if prospect_data.get("ghl_data"):
        sources.append("GHL CRM")
    if prospect_data.get("website_info"):
        sources.append("Website")
    if prospect_data.get("google_search_info"):
        sources.append("Web Search")
    if linkedin_url:
        sources.append("LinkedIn")
    if prospect_data.get("ocean_company_info") or prospect_data.get("ocean_person_info"):
        sources.append("Ocean.io")

    if sources:
        dm_parts.append("")
        dm_parts.append(f"_Sources: {', '.join(sources)}_")

    return "\n".join(dm_parts)


async def _process_single_call(
    call: dict,
    claude_client: ClaudeClient,
    ghl_client: GHLClient,
    slack_client: SlackClient,
    http_client: httpx.AsyncClient,
    google_search_client: GoogleSearchClient | SerperClient | None = None,
    ocean_client: OceanClient | None = None,
    ninjapear_client: NinjaPearClient | None = None,
) -> None:
    """Research a prospect and DM ALL assigned reps (each gets role-appropriate brief)."""
    invitees = call.get("invitees", [])
    if not invitees:
        log.info("precall_no_invitees", event=call.get("event_name"))
        return

    # Collect all hosts for this event
    hosts = call.get("hosts", [])
    if not hosts:
        # Fallback for old format
        host_email = call.get("host_email", "")
        host_name = call.get("host_name", "")
        if host_email:
            hosts = [{"name": host_name, "email": host_email}]

    for invitee in invitees:
        prospect_email = invitee.get("email", "")
        if not prospect_email:
            continue

        prospect_data = await _gather_prospect_data(
            invitee, call, ghl_client, http_client,
            google_search_client, ocean_client,
        )
        prospect_name = prospect_data.get("name", prospect_email)
        confidence_label, confidence_pct = _compute_confidence(prospect_data)
        time_str = _format_time_est(call.get("start_time", ""))

        # Send a brief to EACH host with their role-appropriate template
        for host in hosts:
            host_email = host.get("email", "")
            host_name = host.get("name", "")
            rep_profile = get_rep_profile(host_email)

            brief = await generate_precall_brief(claude_client, prospect_data, rep_profile)
            if not brief:
                log.warning("precall_empty_brief", prospect=prospect_name, rep=host_name)
                continue

            dm_text = _build_dm_message(
                prospect_name, call, time_str,
                confidence_label, confidence_pct,
                prospect_data, brief,
            )

            slack_user_id = rep_profile.get("slack_user_id") if rep_profile else None

            if slack_client.web_client and slack_user_id:
                try:
                    await slack_client.send_dm_by_user_id(slack_user_id, dm_text)
                    log.info(
                        "precall_brief_dm_sent",
                        rep=host_name,
                        slack_id=slack_user_id,
                        prospect=prospect_name,
                        confidence=confidence_label,
                    )
                except Exception as e:
                    log.warning("precall_dm_failed", rep=host_name, error=str(e))
                    await slack_client.send_message(dm_text)
            else:
                await slack_client.send_message(dm_text)
