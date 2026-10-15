import json

from ap_invoice_agent.models import CheckDuplicateInput, LookupVendorInput, MatchLine, MatchToPoInput
from ap_invoice_agent.tools import TOOL_SPECS, CaseFile, check_duplicate, lookup_vendor, match_to_po, run_tool


def test_vendor_found_by_tax_id_despite_formatting(erp):
    result = lookup_vendor(erp, CaseFile(), LookupVendorInput(name="whatever", tax_id="3101458812"))
    assert result["found"] and result["vendor_id"] == "V-1001"


def test_vendor_found_by_name_variant(erp):
    result = lookup_vendor(erp, CaseFile(), LookupVendorInput(name="ATLAS INDUSTRIAL PARTS", tax_id=None))
    assert result["vendor_id"] == "V-1004"


def test_unknown_vendor(erp):
    case = CaseFile()
    result = lookup_vendor(erp, case, LookupVendorInput(name="Summit Lab Equipment Corp.", tax_id="27-5530981"))
    assert result == case.vendor and not result["found"]


def test_po_reference_is_normalized(erp):
    for printed in ("PO-45004", "45004 (hydraulics)", "po #45004"):
        assert erp.get_po(printed).po_number == "PO-45004"


def test_match_within_tolerance(erp):
    args = MatchToPoInput(
        po_number="45002",
        lines=[MatchLine(po_line=1, quantity=1, unit_price=3200), MatchLine(po_line=2, quantity=1, unit_price=275)],
    )
    assert match_to_po(erp, CaseFile(), args)["within_tolerance"]


def test_price_variance_flagged(erp):
    args = MatchToPoInput(po_number="PO-45003", lines=[MatchLine(po_line=1, quantity=4, unit_price=1450)])
    result = match_to_po(erp, CaseFile(), args)
    assert not result["within_tolerance"]
    assert result["lines"][0]["status"] == "price_variance"
    assert result["lines"][0]["price_variance_pct"] == 16.0


def test_quantity_summed_across_split_lines(erp):
    # 10 + 10 hours billed against a 16-hour PO line.
    args = MatchToPoInput(
        po_number="PO-45005",
        lines=[MatchLine(po_line=2, quantity=10, unit_price=55), MatchLine(po_line=2, quantity=10, unit_price=55)],
    )
    statuses = {line["status"] for line in match_to_po(erp, CaseFile(), args)["lines"]}
    assert statuses == {"quantity_over_po"}


def test_duplicate_by_invoice_number(erp):
    args = CheckDuplicateInput(vendor_id="V-1001", invoice_number="INV NW 2104", invoice_date="2026-09-02", total=1)
    assert check_duplicate(erp, CaseFile(), args)["duplicate"]


def test_duplicate_by_same_amount_and_date(erp):
    args = CheckDuplicateInput(vendor_id="V-1002", invoice_number="NEW-1", invoice_date="2026-08-20", total=3475.00)
    assert check_duplicate(erp, CaseFile(), args)["duplicate"]


def test_run_tool_logs_and_rejects_bad_input(erp):
    case = CaseFile()
    output, is_error = run_tool(erp, case, "get_purchase_order", {"wrong": "field"})
    assert is_error and "Invalid input" in json.loads(output)["error"]
    assert case.calls[0]["is_error"]


def test_tool_specs_are_strict():
    for spec in TOOL_SPECS:
        assert spec["strict"] is True
        assert spec["input_schema"]["additionalProperties"] is False
