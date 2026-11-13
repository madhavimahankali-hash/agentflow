import pytest

from app.config import get_settings
from app.runner import Runner

VENDOR_OK = dict(vendor_name="FastFreight LLC", country="US", category="logistics",
                 contact_email="ap@fastfreight.com", quoted_rate=2.05, annual_spend=90000,
                 iban="US12 3456 7890 1234 5678 90", account_name="FastFreight LLC")
VENDOR_BAD = {**VENDOR_OK, "vendor_name": "Shadow Cargo Ltd", "country": "XX"}


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("APPROVAL_MODE", "policy")
    monkeypatch.delenv("OPENSANCTIONS_API_KEY", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    return get_settings()


@pytest.fixture
def runner(settings):
    r = Runner(settings)
    yield r
    r.close()
