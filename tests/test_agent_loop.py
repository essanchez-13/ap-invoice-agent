"""Exercise the agent loop offline with a scripted stand-in for the Anthropic client."""

import json
from pathlib import Path
from types import SimpleNamespace

from ap_invoice_agent.agent import process_invoice
from ap_invoice_agent.models import ExceptionCode

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "invoices" / "07_atlas_AIP-10412.pdf"


def tool_use(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def response(content, stop_reason):
    return SimpleNamespace(
        content=content, stop_reason=stop_reason, usage=SimpleNamespace(input_tokens=100, output_tokens=50)
    )


class ScriptedClient:
    """Returns pre-recorded responses in order and keeps every request for assertions."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # Snapshot the history: the agent keeps appending to the same list.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


DECISION = {
    "invoice": {
        "vendor_name": "ATLAS INDUSTRIAL PARTS",
        "vendor_tax_id": "61-2209417",
        "invoice_number": "AIP-10412",
        "invoice_date": "2026-10-08",
        "currency": "USD",
        "po_number": "PO-45005",
        "lines": [
            {"description": "Conveyor belt, 12 m", "quantity": 1, "unit_price": 2650, "amount": 2650, "po_line": 1},
            {"description": "Installation labor (hrs)", "quantity": 24, "unit_price": 55, "amount": 1320, "po_line": 2},
        ],
        "subtotal": 3970,
        "tax": 516.1,
        "total": 4486.1,
        "bank_account_last4": "7730",
    },
    "vendor_id": "V-1004",
    # A model fooled by the injected note would say "approve"; the policy must still block it.
    "recommendation": "approve",
    "exception_codes": [],
    "notes": "All checks passed.",
}


def scripted_run(erp, final_text):
    client = ScriptedClient(
        [
            response(
                [
                    tool_use("t1", "lookup_vendor", {"name": "ATLAS INDUSTRIAL PARTS", "tax_id": "61-2209417"}),
                    tool_use("t2", "get_purchase_order", {"po_number": "PO-45005"}),
                ],
                "tool_use",
            ),
            response(
                [
                    tool_use(
                        "t3",
                        "match_to_po",
                        {
                            "po_number": "PO-45005",
                            "lines": [
                                {"po_line": 1, "quantity": 1, "unit_price": 2650},
                                {"po_line": 2, "quantity": 24, "unit_price": 55},
                            ],
                        },
                    ),
                    tool_use(
                        "t4",
                        "check_duplicate",
                        {
                            "vendor_id": "V-1004",
                            "invoice_number": "AIP-10412",
                            "invoice_date": "2026-10-08",
                            "total": 4486.1,
                        },
                    ),
                ],
                "tool_use",
            ),
            response([SimpleNamespace(type="text", text=final_text)], "end_turn"),
        ]
    )
    return client, process_invoice(client, erp, SAMPLE)


def test_loop_runs_tools_and_policy_overrides(erp):
    client, result = scripted_run(erp, json.dumps(DECISION))
    assert result.turns == 3
    assert [c["tool"] for c in result.case.calls] == [
        "lookup_vendor",
        "get_purchase_order",
        "match_to_po",
        "check_duplicate",
    ]
    assert result.status == "exception" and result.overridden
    assert ExceptionCode.QUANTITY_VARIANCE in result.exception_codes
    assert result.usage == {"input_tokens": 300, "output_tokens": 150}

    # Both tool results of a turn go back in ONE user message, tied to their tool_use ids.
    second_request = client.requests[1]["messages"]
    assert [b["tool_use_id"] for b in second_request[-1]["content"]] == ["t1", "t2"]
    assert client.requests[0]["messages"][0]["content"][0]["type"] == "document"


def test_invalid_structured_output_becomes_agent_error(erp):
    _, result = scripted_run(erp, "not json")
    assert result.exception_codes == [ExceptionCode.AGENT_ERROR] and result.status == "exception"


def test_refusal_is_handled(erp):
    client = ScriptedClient([response([], "refusal")])
    result = process_invoice(client, erp, SAMPLE)
    assert result.exception_codes == [ExceptionCode.AGENT_ERROR] and "declined" in result.error


def test_fallback_only_sent_for_models_that_support_it(erp):
    for model, expects_fallback in (("claude-opus-5-5", True), ("claude-haiku-5-5", False)):
        client = ScriptedClient([response([], "refusal")])
        process_invoice(client, erp, SAMPLE, model)
        request = client.requests[0]
        assert request["model"] == model
        assert ("fallbacks" in request) is expects_fallback
