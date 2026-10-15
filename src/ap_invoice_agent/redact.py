"""Mask PII and bank details before anything is written to disk.

Audit logs are useful only if they are safe to share with the AP team, so
account numbers keep their last 4 digits and email addresses keep the domain.
"""

import re
from typing import Any

# 10+ digits, optionally broken up by spaces or dashes, with an optional IBAN prefix (CR05 ...).
_ACCOUNT = re.compile(r"\b(?:[A-Z]{2}\d{2}[ -]?)?\d(?:[ -]?\d){9,}\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

# Structured fields that are business identifiers, not personal data. They
# are kept as-is so the audit trail stays traceable to the source document.
SAFE_KEYS = {"tax_id", "vendor_tax_id", "invoice_number", "po_number", "vendor_id", "invoice_date", "paid_date"}


def _mask_account(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return f"****{digits[-4:]}"


def mask_text(text: str) -> str:
    text = _ACCOUNT.sub(_mask_account, text)
    return _EMAIL.sub(lambda m: f"***@{m.group(1)}", text)


def mask(value: Any, key: str | None = None) -> Any:
    """Recursively mask every string in a JSON-like structure, except SAFE_KEYS fields."""
    if isinstance(value, str):
        return value if key in SAFE_KEYS else mask_text(value)
    if isinstance(value, dict):
        return {k: mask(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v, key) for v in value]
    return value
