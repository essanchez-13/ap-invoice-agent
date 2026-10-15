"""Pydantic models shared by the agent, the tools and the policy layer.

The same models produce the JSON schemas sent to Claude (strict tool inputs and
the final structured output), so the contract lives in one place.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    # Strict schemas need additionalProperties: false.
    model_config = ConfigDict(extra="forbid")


class ExceptionCode(StrEnum):
    VENDOR_NOT_FOUND = "VENDOR_NOT_FOUND"
    VENDOR_BLOCKED = "VENDOR_BLOCKED"
    PO_NOT_FOUND = "PO_NOT_FOUND"
    PO_VENDOR_MISMATCH = "PO_VENDOR_MISMATCH"
    PRICE_VARIANCE = "PRICE_VARIANCE"
    QUANTITY_VARIANCE = "QUANTITY_VARIANCE"
    UNMATCHED_LINE = "UNMATCHED_LINE"
    MATCH_NOT_PERFORMED = "MATCH_NOT_PERFORMED"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
    TOTALS_MISMATCH = "TOTALS_MISMATCH"
    EVIDENCE_MISMATCH = "EVIDENCE_MISMATCH"
    SUSPICIOUS_CONTENT = "SUSPICIOUS_CONTENT"
    EXTRACTION_UNCERTAIN = "EXTRACTION_UNCERTAIN"
    AGENT_ERROR = "AGENT_ERROR"


# ---------- Final structured output ----------


class InvoiceLine(Strict):
    description: str
    quantity: float
    unit_price: float
    amount: float
    po_line: int | None = Field(description="Matching PO line number, or null if no PO line fits.")


class ExtractedInvoice(Strict):
    vendor_name: str
    vendor_tax_id: str | None
    invoice_number: str
    invoice_date: str = Field(description="ISO date, YYYY-MM-DD.")
    currency: str = Field(description="ISO 4217 code, e.g. USD.")
    po_number: str | None
    lines: list[InvoiceLine]
    subtotal: float
    tax: float
    total: float
    bank_account_last4: str | None = Field(
        description="Only the last 4 digits of the remit-to account. Never the full number."
    )


class AgentDecision(Strict):
    invoice: ExtractedInvoice
    vendor_id: str | None
    recommendation: Literal["approve", "exception"]
    exception_codes: list[ExceptionCode]
    notes: str = Field(description="One or two sentences for the AP clerk.")


# ---------- Tool inputs ----------


class LookupVendorInput(Strict):
    name: str = Field(description="Vendor name as printed on the invoice.")
    tax_id: str | None = Field(description="Vendor tax ID as printed, or null if absent.")


class GetPurchaseOrderInput(Strict):
    po_number: str = Field(description="PO reference as printed, e.g. 'PO-45001' or '45001'.")


class CheckDuplicateInput(Strict):
    vendor_id: str
    invoice_number: str
    invoice_date: str = Field(description="ISO date, YYYY-MM-DD.")
    total: float


class MatchLine(Strict):
    po_line: int
    quantity: float
    unit_price: float


class MatchToPoInput(Strict):
    po_number: str
    lines: list[MatchLine] = Field(description="Every invoice line, mapped to the PO line it bills.")
