"""A tiny read-only "ERP": vendor master, open POs and paid-invoice history.

In a real deployment these lookups would hit QuickBase, SAP or a warehouse
(Snowflake) through a service account. Here they read the CSVs in data/.
"""

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

_SUFFIXES = {"sa", "srl", "inc", "llc", "corp", "corporation", "ltd", "co", "group"}


def normalize_name(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return " ".join(w for w in words if w not in _SUFFIXES)


def normalize_tax_id(tax_id: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", tax_id).upper()


def normalize_po(po_number: str) -> str:
    digits = re.findall(r"\d+", po_number)
    return f"PO-{digits[0]}" if digits else po_number.strip().upper()


def normalize_invoice_number(number: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", number).upper()


@dataclass
class Vendor:
    vendor_id: str
    name: str
    tax_id: str
    status: str
    payment_terms: str
    remit_bank_last4: str


@dataclass
class POLine:
    line: int
    description: str
    quantity: float
    unit_price: float


@dataclass
class PurchaseOrder:
    po_number: str
    vendor_id: str
    currency: str
    status: str
    lines: dict[int, POLine] = field(default_factory=dict)


@dataclass
class PaidInvoice:
    vendor_id: str
    invoice_number: str
    invoice_date: str
    total: float
    paid_date: str


class ERP:
    def __init__(self, data_dir: Path):
        self.vendors = [Vendor(**row) for row in _read(data_dir / "vendors.csv")]
        self.purchase_orders: dict[str, PurchaseOrder] = {}
        for row in _read(data_dir / "purchase_orders.csv"):
            po = self.purchase_orders.setdefault(
                row["po_number"],
                PurchaseOrder(row["po_number"], row["vendor_id"], row["currency"], row["status"]),
            )
            line = int(row["line"])
            po.lines[line] = POLine(line, row["description"], float(row["quantity"]), float(row["unit_price"]))
        self.paid_invoices = [
            PaidInvoice(r["vendor_id"], r["invoice_number"], r["invoice_date"], float(r["total"]), r["paid_date"])
            for r in _read(data_dir / "paid_invoices.csv")
        ]

    def find_vendor(self, name: str, tax_id: str | None) -> Vendor | None:
        # Tax ID is the reliable key; the name is a fallback because vendors
        # print their legal name in many variants ("ATLAS INDUSTRIAL PARTS").
        if tax_id:
            wanted = normalize_tax_id(tax_id)
            for vendor in self.vendors:
                if normalize_tax_id(vendor.tax_id) == wanted:
                    return vendor
        wanted_name = normalize_name(name)
        for vendor in self.vendors:
            if normalize_name(vendor.name) == wanted_name:
                return vendor
        return None

    def get_po(self, po_number: str) -> PurchaseOrder | None:
        return self.purchase_orders.get(normalize_po(po_number))

    def find_duplicates(
        self, vendor_id: str, invoice_number: str, invoice_date: str, total: float
    ) -> list[PaidInvoice]:
        number = normalize_invoice_number(invoice_number)
        return [
            paid
            for paid in self.paid_invoices
            if paid.vendor_id == vendor_id
            and (
                normalize_invoice_number(paid.invoice_number) == number
                # Same amount on the same day under a new number is the classic re-keyed duplicate.
                or (paid.invoice_date == invoice_date and abs(paid.total - total) < 0.005)
            )
        ]


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
