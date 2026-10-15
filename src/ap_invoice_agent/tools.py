"""Tools Claude can call, plus the evidence they leave behind.

Each tool is deterministic Python. Claude decides *which* lookups to run and
maps messy invoice lines to PO lines; the arithmetic and the system-of-record
checks happen here, never in the model. Every result is also written to a
CaseFile, and the policy layer decides from that evidence, not from what the
model says it saw.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from anthropic import transform_schema
from pydantic import BaseModel, ValidationError

from .erp import ERP, normalize_po
from .models import CheckDuplicateInput, GetPurchaseOrderInput, LookupVendorInput, MatchToPoInput

PRICE_TOLERANCE = 0.02  # 2% unit-price variance allowed


@dataclass
class CaseFile:
    """Latest result of each tool for one invoice, plus a full call log for the audit trail."""

    vendor: dict[str, Any] | None = None
    purchase_order: dict[str, Any] | None = None
    duplicate: dict[str, Any] | None = None
    match: dict[str, Any] | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)


def lookup_vendor(erp: ERP, case: CaseFile, args: LookupVendorInput) -> dict[str, Any]:
    vendor = erp.find_vendor(args.name, args.tax_id)
    if vendor is None:
        result = {"found": False, "message": "No vendor in the master matches this name or tax ID."}
    else:
        result = {
            "found": True,
            "vendor_id": vendor.vendor_id,
            "name": vendor.name,
            "status": vendor.status,
            "payment_terms": vendor.payment_terms,
        }
    case.vendor = result
    return result


def get_purchase_order(erp: ERP, case: CaseFile, args: GetPurchaseOrderInput) -> dict[str, Any]:
    po = erp.get_po(args.po_number)
    if po is None:
        result = {"found": False, "po_number": normalize_po(args.po_number)}
    else:
        result = {
            "found": True,
            "po_number": po.po_number,
            "vendor_id": po.vendor_id,
            "currency": po.currency,
            "status": po.status,
            "lines": [
                {"line": ln.line, "description": ln.description, "quantity": ln.quantity, "unit_price": ln.unit_price}
                for ln in po.lines.values()
            ],
        }
    case.purchase_order = result
    return result


def check_duplicate(erp: ERP, case: CaseFile, args: CheckDuplicateInput) -> dict[str, Any]:
    matches = erp.find_duplicates(args.vendor_id, args.invoice_number, args.invoice_date, args.total)
    result = {
        "duplicate": bool(matches),
        "invoice_number": args.invoice_number,
        "matches": [
            {
                "invoice_number": m.invoice_number,
                "invoice_date": m.invoice_date,
                "total": m.total,
                "paid_date": m.paid_date,
            }
            for m in matches
        ],
    }
    case.duplicate = result
    return result


def match_to_po(erp: ERP, case: CaseFile, args: MatchToPoInput) -> dict[str, Any]:
    po = erp.get_po(args.po_number)
    if po is None:
        result = {"po_found": False, "within_tolerance": False, "lines": []}
        case.match = result
        return result

    line_results = []
    billed_by_line: dict[int, float] = {}
    for item in args.lines:
        po_line = po.lines.get(item.po_line)
        if po_line is None:
            line_results.append({"po_line": item.po_line, "status": "unknown_po_line"})
            continue
        billed_by_line[item.po_line] = billed_by_line.get(item.po_line, 0) + item.quantity
        variance = (item.unit_price - po_line.unit_price) / po_line.unit_price
        status = "ok" if abs(variance) <= PRICE_TOLERANCE else "price_variance"
        line_results.append(
            {
                "po_line": item.po_line,
                "status": status,
                "invoice_unit_price": item.unit_price,
                "po_unit_price": po_line.unit_price,
                "price_variance_pct": round(variance * 100, 2),
            }
        )

    # Quantity is checked per PO line after summing, so a line split across
    # two invoice rows can't slip past the ordered quantity.
    for result_line in line_results:
        line_no = result_line["po_line"]
        if result_line["status"] == "ok" and billed_by_line[line_no] > po.lines[line_no].quantity:
            result_line["status"] = "quantity_over_po"
        if line_no in po.lines:
            result_line["billed_quantity"] = billed_by_line.get(line_no, 0)
            result_line["po_quantity"] = po.lines[line_no].quantity

    result = {
        "po_found": True,
        "po_number": po.po_number,
        "within_tolerance": all(r["status"] == "ok" for r in line_results) and bool(line_results),
        "price_tolerance_pct": PRICE_TOLERANCE * 100,
        "lines": line_results,
    }
    case.match = result
    return result


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Any

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": transform_schema(self.input_model),
            "strict": True,
        }


TOOLS = [
    Tool(
        "lookup_vendor",
        "Look up the vendor in the vendor master by tax ID (preferred) or name. "
        "Returns vendor_id and status (active or blocked), or found=false.",
        LookupVendorInput,
        lookup_vendor,
    ),
    Tool(
        "get_purchase_order",
        "Fetch a purchase order and its lines (description, quantity, unit price). "
        "Accepts the PO reference in any printed form.",
        GetPurchaseOrderInput,
        get_purchase_order,
    ),
    Tool(
        "check_duplicate",
        "Check the paid-invoice history for this vendor for the same invoice number, "
        "or the same total on the same date.",
        CheckDuplicateInput,
        check_duplicate,
    ),
    Tool(
        "match_to_po",
        "Three-way-match check: compare every invoice line against the PO line it bills. "
        "Flags unit-price variance above tolerance and quantities above the ordered amount. "
        "You map invoice lines to PO line numbers; this tool does the math.",
        MatchToPoInput,
        match_to_po,
    ),
]
TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}
TOOL_SPECS = [tool.spec() for tool in TOOLS]


def run_tool(erp: ERP, case: CaseFile, name: str, raw_input: dict[str, Any]) -> tuple[str, bool]:
    """Execute one tool call. Returns (JSON result, is_error)."""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        output, is_error = {"error": f"Unknown tool: {name}"}, True
    else:
        try:
            # strict=True already guarantees the shape; validating again keeps
            # the handlers safe if a tool is ever called without strict mode.
            output, is_error = tool.handler(erp, case, tool.input_model.model_validate(raw_input)), False
        except ValidationError as exc:
            output, is_error = {"error": "Invalid input", "details": exc.errors(include_url=False)}, True
    case.calls.append({"tool": name, "input": raw_input, "output": output, "is_error": is_error})
    return json.dumps(output), is_error
