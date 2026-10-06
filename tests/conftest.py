import pytest

FROZEN_TODAY = "2026-01-15"
OWN_DOMAIN = "lumora-analytics.com"


@pytest.fixture(autouse=True)
def frozen_env(monkeypatch):
    monkeypatch.setenv("MEETING_PREP_TODAY", FROZEN_TODAY)
    monkeypatch.setenv("OWN_COMPANY_DOMAINS", OWN_DOMAIN)
    monkeypatch.delenv("MEETING_PREP_DATA_DIR", raising=False)


@pytest.fixture
def anyio_backend():
    return "asyncio"
