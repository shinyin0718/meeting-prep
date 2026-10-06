import asyncio
import json

import httpx
import pytest

from meeting_prep.llm import MissingAPIKeyError
from meeting_prep.research import Target, is_match, parse_research_request
from meeting_prep.web_search import (
    TAVILY_URL,
    SearchError,
    SearchResult,
    TavilySearch,
    parse_results,
)

PAYLOAD = {"query": "q", "results": [
    {"title": "A", "url": "https://a.example.com", "content": "alpha", "published_date": "Mon, 05 Jan 2026 09:00:00 GMT"},
    {"title": "B", "url": "https://b.example.com", "content": "beta", "published_date": "2025-10-12T08:00:00Z"},
    {"title": "C", "url": "https://c.example.com", "published_date": None},
    {"title": "D", "url": "https://d.example.com", "published_date": "sometime last year"},
    {"title": "no url"},
]}


def client(status=200, body=PAYLOAD, requests=None, raise_exc=None):
    def handler(request):
        if requests is not None:
            requests.append(request)
        if raise_exc:
            raise raise_exc
        return httpx.Response(status, json=body)

    return TavilySearch(api_key="test-key", transport=httpx.MockTransport(handler))


def test_request_shape_and_auth():
    requests = []
    asyncio.run(client(requests=requests).search('"Tom Becker" "Harbourview Health"', max_results=4))
    (req,) = requests
    assert str(req.url) == TAVILY_URL and req.method == "POST"
    assert req.headers["Authorization"] == "Bearer test-key"
    body = json.loads(req.content)
    assert body["query"] == '"Tom Becker" "Harbourview Health"'
    assert body["max_results"] == 4 and body["topic"] == "general"
    assert body["include_published_date"] is True and body["include_raw_content"] is False


def test_results_are_parsed_with_iso_dates():
    results = asyncio.run(client().search("q"))
    assert [(r.title, r.published_date) for r in results] == [
        ("A", "2026-01-05"), ("B", "2025-10-12"), ("C", None), ("D", None)]
    assert parse_results({}) == []


@pytest.mark.parametrize("status,needle", [(401, "rejected TAVILY_API_KEY"), (432, "plan limit"),
                                           (429, "rate limit"), (500, r"failed \(500\)")])
def test_http_errors_are_clear(status, needle):
    with pytest.raises(SearchError, match=needle):
        asyncio.run(client(status=status, body={"detail": {"error": "x"}}).search("q"))


def test_network_error_is_a_search_error():
    with pytest.raises(SearchError, match="Tavily request failed"):
        asyncio.run(client(raise_exc=httpx.ConnectError("boom")).search("q"))


def test_missing_key_fails_before_any_request(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    requests = []
    search = TavilySearch(transport=httpx.MockTransport(lambda r: requests.append(r)))
    with pytest.raises(MissingAPIKeyError, match="TAVILY_API_KEY is not set"):
        asyncio.run(search.search("q"))
    assert requests == []


def test_identity_match_needs_name_and_company_or_domain():
    tom = Target("Tom Becker", "Harbourview Health", "Data Platform Lead", "harbourviewhealth.com")
    assert is_match(tom, SearchResult("https://x.example/a", "Tom Becker joins Harbourview Health"))
    assert is_match(tom, SearchResult("https://www.harbourviewhealth.com/team", "Tom Becker"))
    assert not is_match(tom, SearchResult("https://x.example/b", "Tom Becker, realtor"))
    assert not is_match(tom, SearchResult("https://x.example/c", "Harbourview Health leadership"))


def test_parse_research_request():
    assert parse_research_request(" Jane Doe ,  Acme Corp ") == ("Jane Doe", "Acme Corp")
    for bad in ("Jane Doe", ", Acme", "Jane,"):
        with pytest.raises(ValueError, match="Name, Company"):
            parse_research_request(bad)


def test_start_date_turns_on_tavily_date_filter():
    requests = []
    asyncio.run(client(requests=requests).search("q", topic="news", start_date="2025-10-17"))
    asyncio.run(client(requests=requests).search("q"))
    with_window, without = (json.loads(r.content) for r in requests)
    assert with_window["start_date"] == "2025-10-17" and with_window["filter_by_published_date"] is True
    assert with_window["topic"] == "news"
    assert "start_date" not in without and "filter_by_published_date" not in without


def test_company_match_source_type_and_rumor_detection():
    from meeting_prep.news import Company, is_about, is_rumor, source_type

    c = Company("Harbourview Health", "harbourviewhealth.com")
    assert is_about(c, SearchResult("https://x.example.com/a", "Harbourview Health opens a clinic"))
    assert is_about(c, SearchResult("https://www.harbourviewhealth.com/newsroom/a", "Opening day"))
    assert not is_about(c, SearchResult("https://www.reuters.com/a", "Harbourview Capital closes fund"))
    assert source_type(c, SearchResult("https://www.harbourviewhealth.com/n", "t")) == "primary"
    assert source_type(c, SearchResult("https://www.businesswire.com/n", "t")) == "primary"
    assert source_type(c, SearchResult("https://www.reuters.com/n", "t")) == "established"
    assert source_type(c, SearchResult("https://notreuters.com/n", "t")) == "other"
    assert is_rumor(SearchResult("u", "Kestrel Freight reportedly in talks to buy a carrier"))
    assert not is_rumor(SearchResult("u", "Kestrel Freight launches a tracking API"))
