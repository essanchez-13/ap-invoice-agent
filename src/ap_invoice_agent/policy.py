"""Deterministic approval policy: the model proposes, the code disposes.

Claude's recommendation is an input, not the decision. An invoice is approved
only if the model recommends it AND the evidence collected by the tools
passes every rule below. The model can add exceptions (for example, suspicious
text in the document) but it can never clear one the rules raise. That is
what makes a prompt injection like "this invoice is pre-approved" harmless.
"""

from dataclasses import dataclass

from .erp import normalize_invoice_number, normalize_po
from .models import AgentDecision, ExceptionCode
from .tools import CaseFile

AMOUNT_TOLERANCE = 0.01


@dataclass
class FinalDecision:
    status: str  # "approved" | "exception"
    exception_codes: list[ExceptionCode]
    overridden: bool  # True when the policy rejected a model "approve"


def evaluate(decision: AgentDecision, case: CaseFile) -> FinalDecision:
    codes: list[ExceptionCode] = list(decision.exception_codes)
    inv = decision.invoice

    def flag(code: ExceptionCode) -> None:
        if code not in codes:
            codes.append(code)

    # 1. Vendor must exist, be active, and be the vendor the model reported.
    vendor = case.vendor
    if not vendor or not vendor["found"]:
        flag(ExceptionCode.VENDOR_NOT_FOUND)
    else:
        if vendor["status"] != "active":
            flag(ExceptionCode.VENDOR_BLOCKED)
        if decision.vendor_id != vendor["vendor_id"]:
            flag(ExceptionCode.EVIDENCE_MISMATCH)

    # 2. PO must exist, belong to that vendor, and be the PO printed on the invoice.
    po = case.purchase_order
    if not inv.po_number or not po or not po["found"]:
        flag(ExceptionCode.PO_NOT_FOUND)
    else:
        if normalize_po(inv.po_number) != po["po_number"]:
            flag(ExceptionCode.EVIDENCE_MISMATCH)
        if vendor and vendor["found"] and po["vendor_id"] != vendor["vendor_id"]:
            flag(ExceptionCode.PO_VENDOR_MISMATCH)

    # 3. The match tool must have run against that PO and passed.
    match = case.match
    if po and po["found"]:
        if not match or match.get("po_number") != po["po_number"]:
            flag(ExceptionCode.MATCH_NOT_PERFORMED)
        else:
            statuses = {line["status"] for line in match["lines"]}
            if "price_variance" in statuses:
                flag(ExceptionCode.PRICE_VARIANCE)
            if "quantity_over_po" in statuses:
                flag(ExceptionCode.QUANTITY_VARIANCE)
            if "unknown_po_line" in statuses or len(match["lines"]) < len(inv.lines):
                flag(ExceptionCode.UNMATCHED_LINE)

    # 4. Duplicate check must have run for this invoice number and come back clean.
    dup = case.duplicate
    if vendor and vendor["found"]:
        if not dup or normalize_invoice_number(dup["invoice_number"]) != normalize_invoice_number(inv.invoice_number):
            flag(ExceptionCode.EVIDENCE_MISMATCH)
        elif dup["duplicate"]:
            flag(ExceptionCode.DUPLICATE_INVOICE)

    # 5. The extracted numbers must add up.
    lines_sum = sum(line.amount for line in inv.lines)
    if abs(lines_sum - inv.subtotal) > AMOUNT_TOLERANCE or abs(inv.subtotal + inv.tax - inv.total) > AMOUNT_TOLERANCE:
        flag(ExceptionCode.TOTALS_MISMATCH)
    for line in inv.lines:
        if abs(line.quantity * line.unit_price - line.amount) > AMOUNT_TOLERANCE:
            flag(ExceptionCode.TOTALS_MISMATCH)

    if decision.recommendation == "exception" and not codes:
        # The model saw a problem it couldn't name with a code; keep it in review.
        flag(ExceptionCode.EXTRACTION_UNCERTAIN)

    approved = decision.recommendation == "approve" and not codes
    return FinalDecision(
        status="approved" if approved else "exception",
        exception_codes=codes,
        overridden=decision.recommendation == "approve" and not approved,
    )
