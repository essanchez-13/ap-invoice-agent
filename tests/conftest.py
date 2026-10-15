from pathlib import Path

import pytest

from ap_invoice_agent.erp import ERP

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture
def erp() -> ERP:
    return ERP(DATA_DIR)
