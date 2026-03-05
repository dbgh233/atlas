"""HMAC-SHA256 signature verification for Calendly webhooks.

Calendly sends a `Calendly-Webhook-Signature` header with format:
    t=<timestamp>,v1=<signature>

Verification: HMAC-SHA256 of `<timestamp>.<payload_body>` using the webhook
signing key, compared against the v1 value(s) with constant-time comparison.

Reference: https://developer.calendly.com/api-docs/ZG9jOjM2MzE2MDM4-webhook-signatures
"""

from __future__ import annotations

import hashlib
import hmac

import structlog

log = structlog.get_logger()


def verify_signature(
    payload_body: bytes,
    signature_header: str,
    webhook_secret: str,
) -> bool:
    """Verify a Calendly webhook signature.

    Args:
        payload_body: Raw request body bytes.
        signature_header: Value of the Calendly-Webhook-Signature header.
        webhook_secret: The webhook signing key from Calendly.

    Returns:
        True if signature is valid, False otherwise.
    """
    if not signature_header:
        log.warning("webhook_signature_missing", reason="empty header")
        return False

    # Parse header: t=<timestamp>,v1=<sig>[,v1=<sig>...]
    timestamp: str | None = None
    signatures: list[str] = []

    try:
        parts = signature_header.split(",")
        for part in parts:
            key, _, value = part.strip().partition("=")
            if key == "t":
                timestamp = value
            elif key == "v1":
                signatures.append(value)
    except Exception:
        log.warning("webhook_signature_malformed", header=signature_header)
        return False

    if not timestamp or not signatures:
        log.warning(
            "webhook_signature_incomplete",
            has_timestamp=bool(timestamp),
            signature_count=len(signatures),
        )
        return False

    # Compute expected signature: HMAC-SHA256(secret, "<timestamp>.<body>")
    data = f"{timestamp}.{payload_body.decode()}".encode()
    expected = hmac.new(
        webhook_secret.encode(),
        data,
        hashlib.sha256,
    ).hexdigest()

    # Compare against all v1 values (constant-time)
    for sig in signatures:
        if hmac.compare_digest(expected, sig):
            log.debug("webhook_signature_valid")
            return True

    log.warning("webhook_signature_invalid", signature_count=len(signatures))
    return False
