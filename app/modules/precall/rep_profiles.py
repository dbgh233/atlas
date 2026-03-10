"""Sales rep profiles for pre-call intelligence.

Stores rep LinkedIn URLs, Slack IDs, master prompt context, and personal details
used to find rapport points with prospects. Easy to update when adding new reps
or refreshing master prompts (recommended quarterly).

To add a new rep:
  1. Add their entry to REP_PROFILES below
  2. Include their Slack user ID, Calendly email, LinkedIn URL
  3. Add any personal/professional context for rapport matching
  4. Redeploy Atlas

To refresh master prompts:
  1. Update the "master_prompt" and "personal_context" fields
  2. Redeploy Atlas
"""

from __future__ import annotations

# Map Calendly host email -> rep profile
REP_PROFILES: dict[str, dict] = {
    "hmashburn@ahgpay.com": {
        "name": "Henry Mashburn",
        "slack_user_id": "U08H642F692",
        "calendly_email": "hmashburn@ahgpay.com",
        "linkedin_url": "https://www.linkedin.com/in/easypayments/",
        "role": "Account Executive",
        "location": "East Coast, US",
        "master_prompt": (
            "Henry is an Account Executive at AHG Payments specializing in high-risk "
            "payment processing. He works with merchants in CBD/hemp, nutraceuticals, "
            "peptides, supplements, and alternative wellness verticals. He focuses on "
            "building genuine relationships and understanding merchant pain points before "
            "presenting solutions. His approach is consultative, not transactional."
        ),
        "personal_context": (
            "Henry is based on the East Coast. He's personable and relationship-focused. "
            "He values authentic connection in sales conversations and tends to open calls "
            "with genuine interest in the person before discussing business."
        ),
        "rapport_interests": [
            "payments industry",
            "high-risk processing",
            "merchant services",
            "entrepreneurship",
            "business development",
        ],
    },
    "ishovan@ahgpay.com": {
        "name": "Ism Shovan",
        "slack_user_id": "U09ECH8G1K9",
        "calendly_email": "ishovan@ahgpay.com",
        "linkedin_url": "https://www.linkedin.com/in/ismshovan/",
        "role": "Account Executive",
        "location": "Central US (CST timezone)",
        "master_prompt": (
            "Ism Shovan is an Account Executive at AHG Payments. He works with merchants "
            "seeking reliable payment processing in high-risk verticals including CBD, "
            "nutraceuticals, peptides, and alternative wellness. Ism is thorough in "
            "understanding each merchant's specific needs and matching them with the right "
            "processing solution."
        ),
        "personal_context": (
            "Ism is based in the Central US timezone. He brings a detail-oriented approach "
            "to merchant relationships and is focused on finding the right fit for each "
            "prospect's processing needs."
        ),
        "rapport_interests": [
            "payments industry",
            "high-risk processing",
            "merchant services",
            "business growth",
            "fintech",
        ],
    },
}

# AHG company context for generating briefs — sourced from ahgpay.com and althorizonsg.com
# Last refreshed: 2026-03-10
AHG_CONTEXT = {
    "company": "AHG Payments (Alternative Horizons Group)",
    "website": "https://ahgpay.com",
    "parent_website": "https://www.althorizonsg.com",
    "tagline": "Hemp and High-Risk Processing — No Contracts, No Holds",
    "verticals": [
        "Hemp / CBD",
        "Kratom",
        "Nutraceuticals & Supplements",
        "Peptides",
        "Telehealth",
        "High-ticket B2B",
        "Alternative Wellness",
        "Gaming",
        "Beverages",
        "Hemp-derived products",
    ],
    "services": [
        "Tailored credit card processing",
        "ACH payment processing",
        "POS solutions for all industries",
        "Formulation, manufacturing, fulfillment, and co-packing (nutraceuticals, beverages, hemp)",
        "Web solutions",
        "Influencer marketing and media buying",
        "End-to-end support ensuring quality, compliance, and scalability",
    ],
    "key_messaging": [
        "No contracts — merchants are never locked in",
        "No holds — funds are not held or reserved unnecessarily",
        "Specialized in high-risk and regulated industries",
        "Help brands grow fast and stay compliant",
        "Experience Elevated, Finance Empowered (parent company tagline)",
    ],
    "pain_points_we_solve": [
        "Payment processing rejections and surprise account terminations",
        "Processors who don't understand high-risk industries dropping merchants without warning",
        "Excessive rolling reserves and holds that strangle cash flow",
        "Being forced into long contracts with hidden fees and no flexibility",
        "Chargebacks and fraud management threatening processing ability",
        "Compliance complexity across state and federal regulations",
        "Long onboarding timelines delaying revenue",
        "Lack of transparency in pricing — hidden markup and junk fees",
        "Having only one processor with no backup plan",
    ],
    "value_propositions": [
        "No contracts, no holds — we earn your business every month",
        "Specialized expertise in high-risk verticals — we know your industry inside out",
        "Stable, long-term processing relationships (not just approve and abandon)",
        "Dedicated account management with direct access to your team",
        "Multiple processor relationships for best fit and backup options",
        "Fast onboarding with clear communication throughout",
        "Proactive chargeback prevention and risk management support",
        "Full-service partner beyond just payments — manufacturing, fulfillment, marketing",
    ],
    "differentiators": [
        "We don't just process payments — we partner with merchants for growth across their entire business",
        "No contracts and no holds is almost unheard of in high-risk processing",
        "Deep industry knowledge from team members who come from these verticals",
        "We maintain relationships with multiple processors so merchants aren't dependent on one",
        "Full-service offering from processing to manufacturing to marketing under one roof",
    ],
}


def get_rep_profile(calendly_email: str) -> dict | None:
    """Look up a rep profile by their Calendly host email."""
    return REP_PROFILES.get(calendly_email)


def get_all_reps() -> list[dict]:
    """Return all rep profiles."""
    return list(REP_PROFILES.values())
