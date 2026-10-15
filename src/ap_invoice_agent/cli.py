"""Command-line entry point: process a folder of invoice PDFs.

uv run ap-invoice-agent samples/invoices
"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .agent import DEFAULT_MODEL, InvoiceResult, process_invoice
from .erp import ERP
from .models import ExceptionCode
from .redact import mask


def main() -> None:
    parser = argparse.ArgumentParser(description="AP invoice agent: extract, match and route vendor invoices.")
    parser.add_argument("invoices", type=Path, nargs="?", default=Path("samples/invoices"))
    parser.add_argument("--data", type=Path, default=Path("data"), help="Folder with the ERP CSVs.")
    parser.add_argument("--out", type=Path, default=Path("out"))
    parser.add_argument("--workers", type=int, default=4, help="Invoices processed in parallel.")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help="Claude model ID, e.g. claude-haiku-5-5 for cheap test runs."
    )
    parser.add_argument("--svg", type=Path, help="Also save the terminal output as an SVG screenshot.")
    args = parser.parse_args()

    load_dotenv()  # reads ANTHROPIC_API_KEY from a local, git-ignored .env
    # A fixed width keeps the SVG screenshot readable regardless of the terminal size.
    console = Console(record=True, width=150) if args.svg else Console()
    pdfs = sorted(args.invoices.glob("*.pdf"))
    if not pdfs:
        console.print(f"[red]No PDFs found in {args.invoices}[/red]")
        raise SystemExit(1)

    erp = ERP(args.data)
    client = anthropic.Anthropic()
    console.print(f"[bold]AP invoice agent[/bold]  model={args.model}  invoices={len(pdfs)}\n")

    results: list[InvoiceResult] = []
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console, transient=True) as progress:
        task = progress.add_task(f"Processing {len(pdfs)} invoices...", total=len(pdfs))
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_safe_process, client, erp, pdf, args.model): pdf for pdf in pdfs}
            for future in as_completed(futures):
                results.append(future.result())
                progress.advance(task)
    results.sort(key=lambda r: r.file)

    _write_outputs(results, args.out, args.model)
    console.print(_results_table(results))
    approved = sum(r.status == "approved" for r in results)
    tokens_in = sum(r.usage["input_tokens"] for r in results)
    tokens_out = sum(r.usage["output_tokens"] for r in results)
    console.print(
        f"\n[green]{approved} approved[/green], [yellow]{len(results) - approved} sent to the exception queue[/yellow]"
        f"  |  tokens in/out: {tokens_in:,}/{tokens_out:,}  |  outputs in {args.out}/"
    )
    for r in results:
        if r.overridden:
            console.print(
                f"[magenta]Policy override:[/magenta] {r.file}: the model recommended approval, the rules did not."
            )
    if args.svg:
        args.svg.parent.mkdir(parents=True, exist_ok=True)
        console.save_svg(str(args.svg), title="ap-invoice-agent")


def _safe_process(client: anthropic.Anthropic, erp: ERP, pdf: Path, model: str) -> InvoiceResult:
    # One bad invoice or API error must not stop the batch.
    try:
        return process_invoice(client, erp, pdf, model)
    except anthropic.AuthenticationError:
        raise SystemExit("Authentication failed: set ANTHROPIC_API_KEY (see .env.example).") from None
    except anthropic.APIError as exc:
        return InvoiceResult(
            file=pdf.name,
            status="exception",
            exception_codes=[ExceptionCode.AGENT_ERROR],
            error=f"API error: {type(exc).__name__}",
        )


def _results_table(results: list[InvoiceResult]) -> Table:
    table = Table(header_style="bold")
    for column in ("File", "Vendor", "Invoice", "PO", "Total", "Decision", "Exception codes"):
        table.add_column(column, justify="right" if column == "Total" else "left")
    for r in results:
        inv = r.decision.invoice if r.decision else None
        decision = "[green]approved[/green]" if r.status == "approved" else "[yellow]exception[/yellow]"
        table.add_row(
            r.file,
            inv.vendor_name if inv else "-",
            inv.invoice_number if inv else "-",
            (inv.po_number or "-") if inv else "-",
            f"{inv.currency} {inv.total:,.2f}" if inv else "-",
            decision,
            ", ".join(r.exception_codes) or (r.error or ""),
        )
    return table


def _write_outputs(results: list[InvoiceResult], out_dir: Path, model: str) -> None:
    (out_dir / "audit").mkdir(parents=True, exist_ok=True)
    approved, exceptions = [], []
    for r in results:
        inv = r.decision.invoice if r.decision else None
        summary = {
            "file": r.file,
            "vendor_id": r.decision.vendor_id if r.decision else None,
            "vendor_name": inv.vendor_name if inv else None,
            "invoice_number": inv.invoice_number if inv else None,
            "invoice_date": inv.invoice_date if inv else None,
            "po_number": inv.po_number if inv else None,
            "currency": inv.currency if inv else None,
            "total": inv.total if inv else None,
        }
        if r.status == "approved":
            approved.append(summary)
        else:
            exceptions.append(
                {
                    **summary,
                    "exception_codes": [str(c) for c in r.exception_codes],
                    "notes": r.decision.notes if r.decision else r.error,
                    "policy_override": r.overridden,
                }
            )
        audit = {
            "file": r.file,
            "model": model,
            "final_status": r.status,
            "exception_codes": [str(c) for c in r.exception_codes],
            "policy_override": r.overridden,
            "error": r.error,
            "turns": r.turns,
            "usage": r.usage,
            "model_decision": r.decision.model_dump(mode="json") if r.decision else None,
            "tool_calls": r.case.calls,
        }
        _dump(out_dir / "audit" / f"{Path(r.file).stem}.json", mask(audit))
    _dump(out_dir / "approved.json", mask(approved))
    _dump(out_dir / "exceptions.json", mask(exceptions))


def _dump(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
