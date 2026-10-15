from ap_invoice_agent.redact import mask, mask_text


def test_iban_and_account_numbers_keep_last4():
    assert mask_text("IBAN CR05 0152 0200 1026 2840 4821") == "IBAN ****4821"
    assert mask_text("Acct 7781 2290 0937") == "Acct ****0937"


def test_email_keeps_domain():
    assert mask_text("send to billing@northwind-supply.example") == "send to ***@northwind-supply.example"


def test_dates_amounts_and_business_ids_survive():
    data = {"invoice_date": "2026-10-01", "tax_id": "3-101-458812", "total": 2047.56, "notes": "Paid 2026-10-01"}
    assert mask(data) == data


def test_nested_structures_are_masked():
    data = {"tool_calls": [{"output": {"notes": "account 5502118933 07"}}]}
    assert mask(data)["tool_calls"][0]["output"]["notes"] == "account ****3307"
