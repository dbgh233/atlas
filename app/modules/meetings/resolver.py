"""Commitment name resolution — cross-references vague transcript text with GHL pipeline data.

Resolves descriptions like "the guy doing 20k per month" or "that merchant from yesterday"
to actual GHL opportunity names, IDs, stages, and volumes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import structlog

from app.core.clients.ghl import GHLClient
from app.modules.audit.rules import STAGE_NAMES

log = structlog.get_logger()


@dataclass
class ResolvedCommitment:
    """A commitment enriched with GHL opportunity data."""

    # Original commitment fields
    assignee: str
    action: str
    merchant_name: str | None
    deadline: str | None
    source_quote: str | None

    # Resolved fields from GHL
    resolved_merchant_name: str | None = None
    opportunity_id: str | None = None
    opportunity_stage: str | None = None
    opportunity_stage_name: str | None = None
    monetary_value: float | None = None
    contact_name: str | None = None
    confidence: float = 0.0  # 0.0 to 1.0 match confidence

    @property
    def display_merchant(self) -> str:
        """Best merchant name to display — resolved name if available, else original."""
        return self.resolved_merchant_name or self.merchant_name or "Unknown Merchant"

    @property
    def display_action(self) -> str:
        """Action text with resolved merchant name substituted in."""
        if not self.resolved_merchant_name or not self.merchant_name:
            return self.action
        if not self.action:
            return self.action
        # If the action already contains the resolved name, return as-is
        if self.resolved_merchant_name.lower() in self.action.lower():
            return self.action
        # If the action contains the vague name, substitute
        if self.merchant_name.lower() in self.action.lower():
            # Case-insensitive replacement preserving original casing
            pattern = re.compile(re.escape(self.merchant_name), re.IGNORECASE)
            return pattern.sub(self.resolved_merchant_name, self.action)
        return self.action


# ---------------------------------------------------------------------------
# Volume extraction from vague text
# ---------------------------------------------------------------------------

_VOLUME_PATTERNS = [
    # "$20k" / "$20K" / "$20,000"
    re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*k\b", re.IGNORECASE),
    # "20k per month" / "20k/month" / "20k a month"
    re.compile(r"([\d,]+(?:\.\d+)?)\s*k\s*(?:per|/|a)\s*(?:month|mo)\b", re.IGNORECASE),
    # "$20,000 per month"
    re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*(?:per|/|a)\s*(?:month|mo)\b", re.IGNORECASE),
    # "doing 20k" / "processes 20k"
    re.compile(r"(?:doing|processes?|processing|running|at)\s+\$?([\d,]+(?:\.\d+)?)\s*k\b", re.IGNORECASE),
    # "20,000 volume" / "20000 in volume"
    re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:in\s+)?(?:volume|monthly)\b", re.IGNORECASE),
]


def _extract_volume_hint(text: str) -> float | None:
    """Extract a monthly volume number from vague descriptions.

    Returns the value in dollars (e.g., "20k" -> 20000.0).
    """
    for pattern in _VOLUME_PATTERNS:
        match = pattern.search(text)
        if match:
            raw = match.group(1).replace(",", "")
            try:
                value = float(raw)
            except ValueError:
                continue
            # If the pattern was a "k" variant, multiply by 1000
            if "k" in pattern.pattern.lower():
                value *= 1000
            # Sanity check — volumes are typically $1k-$50M/month
            if 500 <= value <= 50_000_000:
                return value
    return None


# ---------------------------------------------------------------------------
# Stage extraction from vague text
# ---------------------------------------------------------------------------

_STAGE_KEYWORDS: dict[str, list[str]] = {
    "Discovery": ["discovery", "prospect", "new lead", "just came in"],
    "Committed": ["committed", "verbal", "said yes", "agreed"],
    "Onboarding Scheduled": ["onboarding", "scheduled", "call booked", "meeting set"],
    "MPA & Underwriting": [
        "mpa", "underwriting", "application", "submitted",
        "docs", "documents", "paperwork",
    ],
    "Approved": ["approved", "approval", "green light"],
    "Live": ["live", "processing", "boarding complete", "activated"],
    "Close Lost": ["lost", "declined", "dead", "cancelled"],
}


def _extract_stage_hint(text: str) -> str | None:
    """Extract a pipeline stage hint from vague descriptions."""
    lower = text.lower()
    for stage_name, keywords in _STAGE_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return stage_name
    return None


# ---------------------------------------------------------------------------
# Timing extraction
# ---------------------------------------------------------------------------

_TIMING_PATTERNS = [
    re.compile(r"(?:from|discussed?)\s+(?:yesterday|last\s+(?:meeting|call|week))", re.IGNORECASE),
    re.compile(r"(?:new|just|recent)\s+(?:one|deal|merchant|lead)", re.IGNORECASE),
]


def _extract_timing_hint(text: str) -> str | None:
    """Extract timing hints like 'from yesterday' or 'new deal'."""
    for pattern in _TIMING_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------


def _name_similarity(a: str, b: str) -> float:
    """Calculate normalized similarity between two merchant names."""
    a_clean = re.sub(r"[^a-z0-9\s]", "", a.lower()).strip()
    b_clean = re.sub(r"[^a-z0-9\s]", "", b.lower()).strip()

    if not a_clean or not b_clean:
        return 0.0

    # Exact match
    if a_clean == b_clean:
        return 1.0

    # Substring containment (high confidence)
    if a_clean in b_clean or b_clean in a_clean:
        shorter = min(len(a_clean), len(b_clean))
        longer = max(len(a_clean), len(b_clean))
        return 0.85 + 0.15 * (shorter / longer)

    # Word-level overlap
    a_words = set(a_clean.split())
    b_words = set(b_clean.split())
    if a_words and b_words:
        overlap = a_words & b_words
        if overlap:
            jaccard = len(overlap) / len(a_words | b_words)
            if jaccard >= 0.5:
                return 0.7 + 0.15 * jaccard

    # Sequence matcher for typos / partial names
    ratio = SequenceMatcher(None, a_clean, b_clean).ratio()
    return ratio


def _get_opp_monetary_value(opp: dict) -> float | None:
    """Extract monetary value from a GHL opportunity."""
    val = opp.get("monetaryValue")
    if val is not None:
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    return None


def _get_opp_stage_name(opp: dict) -> str | None:
    """Get human-readable stage name from a GHL opportunity."""
    stage_id = opp.get("pipelineStageId")
    if stage_id:
        return STAGE_NAMES.get(stage_id)
    return None


def fuzzy_match_merchant(
    description: str,
    opportunities: list[dict],
    merchant_name_hint: str | None = None,
) -> tuple[dict | None, float]:
    """Match a vague merchant description to a GHL opportunity.

    Uses multiple signals:
    - Name similarity (primary)
    - Volume hints ("20k/month" compared to opp monetary value)
    - Stage hints ("in underwriting" compared to opp stage)

    Args:
        description: The vague text (action + source_quote combined).
        opportunities: List of GHL opportunity dicts.
        merchant_name_hint: Extracted merchant name from Claude, if any.

    Returns:
        Tuple of (best_matching_opp or None, confidence 0.0-1.0).
    """
    if not opportunities:
        return None, 0.0

    volume_hint = _extract_volume_hint(description)
    stage_hint = _extract_stage_hint(description)

    best_match: dict | None = None
    best_score: float = 0.0

    for opp in opportunities:
        opp_name = opp.get("name", "")
        if not opp_name:
            continue

        score = 0.0

        # --- Name similarity (weight: 0.6) ---
        if merchant_name_hint:
            name_sim = _name_similarity(merchant_name_hint, opp_name)
            score += name_sim * 0.6
        else:
            # Try to match words from the description against opp name
            name_sim = _name_similarity(description, opp_name)
            score += name_sim * 0.4  # Lower weight without explicit name

        # --- Volume match (weight: 0.25) ---
        if volume_hint:
            opp_value = _get_opp_monetary_value(opp)
            if opp_value and opp_value > 0:
                # How close is the volume hint to the opp value?
                ratio = min(volume_hint, opp_value) / max(volume_hint, opp_value)
                # Within 30% is a strong match
                if ratio >= 0.7:
                    score += 0.25 * ratio
                elif ratio >= 0.4:
                    score += 0.10 * ratio

        # --- Stage match (weight: 0.15) ---
        if stage_hint:
            opp_stage = _get_opp_stage_name(opp)
            if opp_stage and opp_stage.lower() == stage_hint.lower():
                score += 0.15

        # --- Contact name in description (bonus) ---
        contact = opp.get("contact", {})
        contact_name = contact.get("name", "") or ""
        if contact_name and len(contact_name) > 2:
            if contact_name.lower() in description.lower():
                score += 0.20

        if score > best_score:
            best_score = score
            best_match = opp

    # Only return matches above a minimum confidence threshold
    if best_score < 0.35:
        return None, best_score

    return best_match, best_score


def enrich_commitment(
    commitment: dict,
    matched_opp: dict | None,
    confidence: float = 0.0,
) -> ResolvedCommitment:
    """Enrich a raw commitment dict with matched GHL opportunity data.

    Args:
        commitment: Raw commitment dict from Claude extraction.
        matched_opp: GHL opportunity dict, or None if no match.
        confidence: Match confidence score 0.0-1.0.

    Returns:
        ResolvedCommitment with all available enrichment.
    """
    resolved = ResolvedCommitment(
        assignee=commitment.get("assignee", "Unknown"),
        action=commitment.get("action", ""),
        merchant_name=commitment.get("merchant_name"),
        deadline=commitment.get("deadline"),
        source_quote=commitment.get("source_quote"),
    )

    if matched_opp and confidence >= 0.35:
        resolved.resolved_merchant_name = matched_opp.get("name")
        resolved.opportunity_id = matched_opp.get("id")
        resolved.opportunity_stage = matched_opp.get("pipelineStageId")
        resolved.opportunity_stage_name = _get_opp_stage_name(matched_opp)
        resolved.monetary_value = _get_opp_monetary_value(matched_opp)
        resolved.confidence = confidence

        contact = matched_opp.get("contact", {})
        resolved.contact_name = contact.get("name")

    return resolved


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------


async def resolve_commitment_names(
    commitments: list[dict],
    ghl_client: GHLClient,
    all_opps: list[dict] | None = None,
) -> list[ResolvedCommitment]:
    """Resolve vague commitment text to actual GHL merchant names.

    Takes raw commitment dicts from Claude extraction and cross-references
    with the GHL pipeline to find matching opportunities.

    Args:
        commitments: List of raw commitment dicts with keys:
            assignee, action, merchant_name, deadline, source_quote
        ghl_client: GHL API client for fetching opportunities.
        all_opps: Pre-fetched opportunities list (avoids duplicate API call).

    Returns:
        List of ResolvedCommitment objects with enriched data.
    """
    if not commitments:
        return []

    # Fetch all open opportunities once
    if all_opps is None:
        try:
            all_opps = await ghl_client.search_opportunities()
        except Exception as exc:
            log.error("resolver_opp_fetch_failed", error=str(exc))
            # Return un-enriched commitments
            return [enrich_commitment(c, None) for c in commitments]

    resolved: list[ResolvedCommitment] = []

    for commitment in commitments:
        merchant_name = commitment.get("merchant_name")
        action = commitment.get("action", "")
        source_quote = commitment.get("source_quote", "")

        # Build search text from all available context
        search_text = " ".join(filter(None, [action, source_quote, merchant_name]))

        # First try: exact/substring name match against opportunities
        # (this is the fast path for when Claude already extracted a good name)
        if merchant_name:
            exact_match = _try_exact_match(merchant_name, all_opps)
            if exact_match:
                enriched = enrich_commitment(commitment, exact_match, confidence=1.0)
                resolved.append(enriched)
                log.debug(
                    "resolver_exact_match",
                    merchant=merchant_name,
                    opp_name=exact_match.get("name"),
                )
                continue

        # Second try: fuzzy match using all available signals
        matched_opp, confidence = fuzzy_match_merchant(
            search_text, all_opps, merchant_name_hint=merchant_name,
        )

        enriched = enrich_commitment(commitment, matched_opp, confidence)
        resolved.append(enriched)

        if matched_opp:
            log.info(
                "resolver_fuzzy_match",
                merchant_hint=merchant_name,
                matched_to=matched_opp.get("name"),
                confidence=round(confidence, 3),
            )
        elif merchant_name:
            log.debug(
                "resolver_no_match",
                merchant_hint=merchant_name,
                best_confidence=round(confidence, 3),
            )

    return resolved


def _try_exact_match(merchant_name: str, opportunities: list[dict]) -> dict | None:
    """Try exact or substring match of merchant name against opportunities."""
    name_lower = merchant_name.lower().strip()
    if not name_lower:
        return None

    # Pass 1: exact match
    for opp in opportunities:
        opp_name = (opp.get("name") or "").lower().strip()
        if opp_name and name_lower == opp_name:
            return opp

    # Pass 2: substring containment
    for opp in opportunities:
        opp_name = (opp.get("name") or "").lower().strip()
        if not opp_name:
            continue
        if name_lower in opp_name or opp_name in name_lower:
            return opp

    return None


# ---------------------------------------------------------------------------
# Digest formatting with resolution
# ---------------------------------------------------------------------------


def format_resolved_digest(
    resolved_commitments: list[ResolvedCommitment],
) -> str:
    """Format resolved commitments into a Slack-ready digest.

    Shows actual merchant names, stages, and volumes instead of vague text.
    """
    if not resolved_commitments:
        return ""

    # Group by assignee
    by_assignee: dict[str, list[ResolvedCommitment]] = {}
    for rc in resolved_commitments:
        key = rc.assignee
        if key not in by_assignee:
            by_assignee[key] = []
        by_assignee[key].append(rc)

    lines: list[str] = []
    lines.append(f":memo: *Meeting Commitments* ({len(resolved_commitments)})")

    for assignee, items in sorted(by_assignee.items()):
        lines.append(f"\n*{assignee}:*")
        for rc in items:
            parts: list[str] = []

            # Action with resolved name
            action_text = rc.display_action

            # Merchant context line
            merchant_context = ""
            if rc.resolved_merchant_name:
                ctx_parts = [f"*{rc.resolved_merchant_name}*"]
                if rc.opportunity_stage_name:
                    ctx_parts.append(rc.opportunity_stage_name)
                if rc.monetary_value:
                    ctx_parts.append(f"${rc.monetary_value:,.0f}/mo")
                merchant_context = f" [{' | '.join(ctx_parts)}]"

            # Deadline
            deadline_str = ""
            if rc.deadline:
                deadline_str = f" -- due {rc.deadline}"

            # Confidence indicator for fuzzy matches
            conf_indicator = ""
            if 0.35 <= rc.confidence < 0.7:
                conf_indicator = " :grey_question:"  # Low confidence

            lines.append(
                f"  :white_circle: {action_text}{merchant_context}{deadline_str}{conf_indicator}"
            )

    return "\n".join(lines)
