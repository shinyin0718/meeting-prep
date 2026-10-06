import pytest

FROZEN_TODAY = "2026-01-15"
OWN_DOMAIN = "lumora-analytics.com"


@pytest.fixture(autouse=True)
def frozen_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MEETING_PREP_TODAY", FROZEN_TODAY)
    monkeypatch.setenv("MEETING_PREP_ATTACHMENTS_DIR", str(tmp_path / "attachments"))
    monkeypatch.setenv("OWN_COMPANY_DOMAINS", OWN_DOMAIN)
    monkeypatch.delenv("MEETING_PREP_DATA_DIR", raising=False)
    monkeypatch.setenv("MEETING_PREP_USER_DATA_DIR", str(tmp_path / "mine"))


@pytest.fixture
def anyio_backend():
    return "asyncio"
