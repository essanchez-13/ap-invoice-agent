from ap_invoice_agent.models import (
    AgentDecision,
    CheckDuplicateInput,
    ExceptionCode,
    ExtractedInvoice,
    GetPurchaseOrderInput,
    InvoiceLine,
    LookupVendorInput,
    MatchLine,
    MatchToPoInput,
)
from ap_invoice_agent.policy import evaluate
from ap_invoice_agent.tools import CaseFile, check_duplicate, get_purchase_order, lookup_vendor, match_to_po


def make_decision(recommendation="approve", codes=(), labor_hours=16, total=None):
    lines = [
        InvoiceLine(description="Conveyor belt", quantity=1, unit_price=2650, amount=2650, po_line=1),
        InvoiceLine(description="Labor", quantity=labor_hours, unit_price=55, amount=labor_hours * 55, po_line=2),
    ]
    subtotal = sum(line.amount for line in lines)
    return AgentDecision(
        invoice=ExtractedInvoice(
            vendor_name="ATLAS INDUSTRIAL PARTS",
            vendor_tax_id="61-2209417",
            invoice_number="AIP-10412",
            invoice_date="2026-10-08",
            currency="USD",
            po_number="PO-45005",
            lines=lines,
            subtotal=subtotal,
            tax=0,
            total=subtotal if total is None else total,
            bank_account_last4="7730",
        ),
        vendor_id="V-1004",
        recommendation=recommendation,
        exception_codes=list(codes),
        notes="",
    )


def run_checks(erp, decision):
    """Simulate the tool calls a well-behaved agent makes for this decision."""
    case = CaseFile()
    inv = decision.invoice
    lookup_vendor(erp, case, LookupVendorInput(name=inv.vendor_name, tax_id=inv.vendor_tax_id))
    get_purchase_order(erp, case, GetPurchaseOrderInput(po_number=inv.po_number))
    match_to_po(
        erp,
        case,
        MatchToPoInput(
            po_number=inv.po_number,
            lines=[
                MatchLine(po_line=line.po_line, quantity=line.quantity, unit_price=line.unit_price)
                for line in inv.lines
            ],
        ),
    )
    check_duplicate(
        erp,
        case,
        CheckDuplicateInput(
            vendor_id="V-1004", invoice_number=inv.invoice_number, invoice_date=inv.invoice_date, total=inv.total
        ),
    )
    return case


def test_clean_invoice_is_approved(erp):
    decision = make_decision()
    final = evaluate(decision, run_checks(erp, decision))
    assert final.status == "approved" and final.exception_codes == []


def test_model_approval_is_overridden_by_evidence(erp):
    # The prompt-injection case: the model says "approve", but labor is over the PO.
    decision = make_decision(labor_hours=24)
    final = evaluate(decision, run_checks(erp, decision))
    assert final.status == "exception" and final.overridden
    assert ExceptionCode.QUANTITY_VARIANCE in final.exception_codes


def test_model_can_add_but_not_remove_exceptions(erp):
    decision = make_decision(codes=[ExceptionCode.SUSPICIOUS_CONTENT], recommendation="exception")
    final = evaluate(decision, run_checks(erp, decision))
    assert final.exception_codes == [ExceptionCode.SUSPICIOUS_CONTENT]


def test_skipped_tools_block_approval(erp):
    final = evaluate(make_decision(), CaseFile())
    assert final.status == "exception"
    assert ExceptionCode.VENDOR_NOT_FOUND in final.exception_codes
    assert ExceptionCode.PO_NOT_FOUND in final.exception_codes


def test_skipped_match_is_flagged(erp):
    decision = make_decision()
    case = run_checks(erp, decision)
    case.match = None
    assert ExceptionCode.MATCH_NOT_PERFORMED in evaluate(decision, case).exception_codes


def test_totals_must_add_up(erp):
    decision = make_decision(total=9999)
    assert ExceptionCode.TOTALS_MISMATCH in evaluate(decision, run_checks(erp, decision)).exception_codes


def test_evidence_must_match_reported_vendor(erp):
    decision = make_decision()
    case = run_checks(erp, decision)
    decision.vendor_id = "V-1001"
    assert ExceptionCode.EVIDENCE_MISMATCH in evaluate(decision, case).exception_codes
