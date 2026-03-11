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
        "role": "Chief Strategy Officer & Co-Founder",
        "location": "South Florida",
        "master_prompt": (
            "Henry Mashburn is the CSO & Co-Founder of Alternative Horizons Group. Age 26. "
            "He leads sales, revenue, marketing, and strategy for AHG Payments, a retail ISO "
            "specializing in high-risk merchants. His sales philosophy is consultative and "
            "story-driven — never hard-sell. He demonstrates expertise through success stories, "
            "maintains non-desperate positioning, and focuses on merchant growth and success "
            "metrics. Average onboarding is 7-14 business days from signature to processing. "
            "AHG's competitive differentiators: no volume caps, direct relationships with sponsor "
            "banks/processors, same-day issue resolution, niche expertise in CBD/Hemp/Alternative "
            "Wellness, vetted ecosystem of partner solutions (DTC agencies, fulfillment, manufacturing), "
            "and political advocacy (CBD/Hemp lobbying in Washington DC). Focus on client growth "
            "vs. churn-and-burn model."
        ),
        "personal_context": (
            "Born in Lima, Peru. Multicultural background — American father, Honduran mother. "
            "Lived in Peru and Suriname, moved to US at age 11. Extensive international travel "
            "(all continents except Australia, Africa, Antarctica). Former competitive soccer "
            "player — University of Michigan for 3 years, Peru U-17/U-20 national teams. "
            "Goalkeeper mentality (results-driven, competitive). Bilingual English/Spanish. "
            "Education: Biopsychology, Cognition & Neuroscience degree from University of Michigan. "
            "Lives with girlfriend Melody in South Florida. Values: faith, service, learning, "
            "integrity, gratitude. Enneagram Type 7 (Enthusiast) with Type 3 (Achiever) tendencies — "
            "high energy, variety-seeking, optimistic. Natural storyteller. Relationship-focused "
            "approach from multicultural background."
        ),
        "rapport_interests": [
            "soccer / football (played competitively, Peru national teams)",
            "University of Michigan (alma mater)",
            "Peru / Latin America / Honduras",
            "international travel (visited most continents)",
            "South Florida lifestyle",
            "entrepreneurship and startups",
            "CBD/hemp/alternative wellness industry",
            "payments and fintech",
            "neuroscience / biopsychology",
            "faith and service",
            "Spanish language / bilingual",
        ],
        # Last updated from master prompt: September 2025
        # Next refresh: Q1 2026
    },
    "ishovan@ahgpay.com": {
        "name": "Ism Shovan",
        "slack_user_id": "U09ECH8G1K9",
        "calendly_email": "ishovan@ahgpay.com",
        "linkedin_url": "https://www.linkedin.com/in/ismshovan/",
        "role": "Customer Success Manager",
        "location": "Central US (CST timezone)",
        "master_prompt": (
            "Ism Shovan is the Customer Success Manager at Alternative Horizons Group. He leads "
            "customer success and implementation for payments and merchant services, ensuring "
            "seamless onboarding, world-class support, and long-term partnerships. His expertise "
            "spans SaaS client success, onboarding, account management, technical support, and "
            "process optimization. He handles gateway setup, POS, Authorize.net, and NMI integration. "
            "Communication style: professional yet approachable, combining clarity, empathy, and "
            "supportive presence. Prefers concise, actionable insights over fluff. Excels at turning "
            "ambiguity into clarity. Enneagram: Type 1 (Reformer — principled, structured), Type 3 "
            "(Achiever — goal-oriented, adaptable), Type 8 (Challenger — decisive, assertive). "
            "Principles-first: never oversells capabilities. Trust, adoption, and long-term retention "
            "guide every decision."
        ),
        "personal_context": (
            "Born in the Philippines, moved to the US in grade school. Has two families: American "
            "family (the Shovans, who adopted him in high school) and Filipino family in the "
            "Philippines. Christian faith is the most important part of his life — priorities are "
            "faith first, family second, work third. Married to Elisabeth for 9+ years, has a "
            "3.5-year-old daughter and another child expected. They've traveled to 30+ countries "
            "together — daughter has visited 20+ countries. Travel is a major passion — specializes "
            "in credit card points and miles, loves helping others unlock travel experiences. "
            "Career path from Apple to SaaS startups in customer service and client success. "
            "Long-term goal is to join the C-suite. Proven remote worker who performs at a high "
            "level even while traveling abroad."
        ),
        "rapport_interests": [
            "travel (30+ countries, points and miles expert)",
            "Philippines / Filipino heritage",
            "Christian faith and community",
            "family life (young children, parenting)",
            "Apple (former employee)",
            "SaaS and customer success",
            "payments and merchant services",
            "process optimization and automation",
            "credit card rewards and travel hacking",
            "remote work and digital nomad lifestyle",
        ],
        # Last updated from master prompt: 2025
        # Next refresh: Q2 2026
    },
    "hness@ahgpay.com": {
        "name": "Hannah Ness",
        "slack_user_id": "U0A16L99ANB",
        "calendly_email": "hness@ahgpay.com",
        "linkedin_url": "",
        "role": "Onboarding Specialist",
        "location": "South Florida (in-office)",
        "master_prompt": (
            "Hannah Ness is the Merchant Onboarding Specialist at Alternative Horizons Group. "
            "She was brought in to take over merchant onboarding from Henry so he can focus on sales. "
            "Her job: once Henry gets verbal commitment, Hannah takes reign — collects all documents, "
            "submits complete application packages to underwriting (not piecemeal), gets merchants "
            "approved, then hands off to Ism for customer success. The ideal model is BamFam — "
            "book a meeting from a meeting. Henry books the onboarding call for Hannah, she collects "
            "docs and gets signatures on-call, then books a CS handoff meeting for Ism. "
            "She came in with Nutra merchant processing experience where applications were always "
            "submitted in full and double-checked — errors were not accepted. She's bringing that "
            "same devotion to completeness to AHG but in a more streamlined, AI-assisted way. "
            "She works closely with Henry in-office and is building SOPs and flowcharts for the "
            "onboarding process from scratch. Key tools: GHL (CRM), IRIS (underwriting portal), "
            "PandaDoc (merchant applications), Notion (knowledge base). "
            "Communication style: professional but approachable, uses humor to keep merchants "
            "engaged through the documentation process. Has an escalating follow-up approach — "
            "starts with friendly emails, then light humor (ghosting memes), then hands back to "
            "Henry if merchant goes unresponsive. Comfortable with AI writing routine emails but "
            "reviews anything requiring human context before it sends. "
            "Prefers notifications kept to a minimum — relevant, quick, and useful. Gets overstimulated "
            "by too many automated messages. Most productive for detail-oriented compliance work "
            "in the morning, better at merchant communication in the afternoon. "
            "Underwriter relationships: Tabitha (Shield Engage), Virge and Vicki (West Town), "
            "building Argyle relationships. Key priority is merchant satisfaction above all else."
        ),
        "personal_context": (
            "Creative person who likes to use 'color' in everything — both literally (aesthetic "
            "flowcharts, color-coded docs) and figuratively (quirky related facts in conversation, "
            "humor, personal touches). Self-described artist. Finds silver linings in tough situations. "
            "Quick learner with a wide knowledge base from working across many industries. "
            "Low risk tolerance in business — gets anxiety about incomplete applications or "
            "representing the company improperly. Prefers to see entire task strings carried out "
            "before moving on. Building habit of using AI tools — understands the speed benefit "
            "but adjusting from manual spreadsheet/transcript habits. Values doing respectable "
            "work — meeting expectations plus going above and beyond with unexpected extras."
        ),
        "rapport_interests": [
            "creative projects and art",
            "humor and finding silver linings",
            "process optimization and flowcharting",
            "merchant processing (Nutra background)",
            "organization and documentation",
        ],
        # Added: 2026-03-11 from actual master prompt PDF
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
