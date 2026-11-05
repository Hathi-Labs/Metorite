"""WEB-4 fence — the search provider list (``web_research.md`` §3.3).

1. **Off is old.** An org off ``WEB_RESEARCH_ORGS`` keeps the SerpAPI/ddgs chain.
2. **Ready only.** A provider with a failed health check, or a fault in the last
   5 minutes, is skipped. A success clears the mark. After the TTL it is asked again.
3. **Stamp.** Each hit names its provider and a date.
4. **Merge.** Hits of one page from two providers meet by normal URL.
5. **Sources.** ``papers`` and ``wiki`` ask only the scholarly providers.
6. **Text.** Hit text loses tags and control characters, and a bad URL is dropped.
"""
from __future__ import annotations

import pytest
from acb_common import get_settings
from acb_skills import web_search_providers as wsp
from acb_skills import web_tools
from acb_common import env_guard
from acb_common.settings import Settings

ORG = "org-web4"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch):
    wsp.reset_health()
    monkeypatch.setattr(get_settings(), "web_research_orgs", ORG)
    monkeypatch.setattr(get_settings(), "searxng_url", "")
    monkeypatch.setattr(web_tools, "_run_org", lambda: ORG)
    monkeypatch.setattr(wsp, "_serpapi_key", lambda: "")
    yield
    wsp.reset_health()


def _hit(provider: str, url: str, title: str = "T", date: str = "") -> wsp.Hit:
    return wsp.Hit(title, url, "snippet", provider, date)


def _providers(monkeypatch, table: dict[str, object], *, searxng_ok: bool = True):
    """Patch the runners. A value is a list of Hit, or an Exception to raise."""
    calls: list[str] = []

    def _runner(name):
        async def run(query, n):
            calls.append(name)
            value = table[name]
            if isinstance(value, Exception):
                raise value
            return list(value)
        return run

    async def health():
        calls.append("searxng.health")
        return searxng_ok

    def build():
        return [
            wsp.Provider("searxng", "web", _runner("searxng"), health, lambda: "searxng" in table),
            wsp.Provider("serpapi", "web", _runner("serpapi"), None, lambda: "serpapi" in table),
            wsp.Provider("ddgs", "web", _runner("ddgs"), None, lambda: "ddgs" in table),
            wsp.Provider("openalex", "papers", _runner("openalex"), None, lambda: "openalex" in table),
            wsp.Provider("arxiv", "papers", _runner("arxiv"), None, lambda: "arxiv" in table),
            wsp.Provider("wikipedia", "wiki", _runner("wikipedia"), None, lambda: "wikipedia" in table),
        ]

    monkeypatch.setattr(wsp, "providers", build)
    return calls


# ── 1. Off is old ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_org_off_the_flag_keeps_the_old_chain(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "web_research_orgs", "")

    async def boom(*a, **k):
        raise AssertionError("the provider list must not run")

    monkeypatch.setattr(wsp, "search", boom)

    async def serp(query, n):
        return [{"title": "Old", "href": "https://old.example", "body": "b"}]

    monkeypatch.setattr(web_tools, "_serpapi_search", serp)
    out = await web_tools.web_search("q")
    assert "Old" in out and "Provider:" not in out


@pytest.mark.asyncio
async def test_an_org_on_the_flag_gets_the_list(monkeypatch) -> None:
    _providers(monkeypatch, {"ddgs": [_hit("ddgs", "https://a.example")]})
    out = await web_tools.web_search("q")
    assert "Provider: ddgs" in out


# ── 2. Ready only ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_health_check_skips_the_provider(monkeypatch) -> None:
    calls = _providers(monkeypatch, {"searxng": [_hit("searxng", "https://s.example")],
                                     "ddgs": [_hit("ddgs", "https://d.example")]},
                       searxng_ok=False)
    out = await wsp.search("q")
    assert "searxng" not in calls[1:] and "Provider: ddgs" in out
    assert "Provider: searxng" not in out
    assert wsp.is_marked("searxng")


@pytest.mark.asyncio
async def test_an_unconfigured_provider_is_not_asked(monkeypatch) -> None:
    calls = _providers(monkeypatch, {"ddgs": [_hit("ddgs", "https://d.example")]})
    await wsp.search("q")
    assert calls == ["ddgs"]


@pytest.mark.asyncio
async def test_a_fault_marks_the_provider_and_the_next_one_answers(monkeypatch) -> None:
    calls = _providers(monkeypatch, {"serpapi": RuntimeError("boom"),
                                     "ddgs": [_hit("ddgs", "https://d.example")]})
    out = await wsp.search("q")
    assert "Provider: ddgs" in out and wsp.is_marked("serpapi")
    calls.clear()
    await wsp.search("q")
    assert "serpapi" not in calls  # marked: skipped


@pytest.mark.asyncio
async def test_the_mark_ends_after_the_ttl_and_a_success_clears_it(monkeypatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(wsp, "_now", lambda: clock[0])
    _providers(monkeypatch, {"serpapi": [_hit("serpapi", "https://s.example")]})
    wsp.mark_failed("serpapi")
    assert wsp.is_marked("serpapi")
    clock[0] += wsp.HEALTH_TTL_S - 1
    assert wsp.is_marked("serpapi")
    clock[0] += 2
    assert not wsp.is_marked("serpapi")
    wsp.mark_failed("serpapi")
    clock[0] += wsp.HEALTH_TTL_S + 1
    out = await wsp.search("q")
    assert "Provider: serpapi" in out and not wsp.is_marked("serpapi")


@pytest.mark.asyncio
async def test_every_provider_failing_names_each_one(monkeypatch) -> None:
    _providers(monkeypatch, {"serpapi": RuntimeError("x"), "ddgs": ValueError("y")})
    out = await wsp.search("q")
    assert out.startswith("web_search failed:")
    assert "serpapi: RuntimeError" in out and "ddgs: ValueError" in out


@pytest.mark.asyncio
async def test_no_ready_provider_and_no_fault_says_no_results(monkeypatch) -> None:
    _providers(monkeypatch, {})
    assert "No results found" in await wsp.search("q")


@pytest.mark.asyncio
async def test_a_full_answer_does_not_ask_the_next_provider(monkeypatch) -> None:
    hits = [_hit("serpapi", f"https://s.example/{i}") for i in range(3)]
    calls = _providers(monkeypatch, {"serpapi": hits, "ddgs": [_hit("ddgs", "https://d.example")]})
    await wsp.search("q", max_results=3)
    assert calls == ["serpapi"]


# ── 3 and 4. Stamp and merge ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_each_hit_names_its_provider_and_a_date(monkeypatch) -> None:
    _providers(monkeypatch, {"serpapi": [_hit("serpapi", "https://a.example", date="2026-10-01"),
                                         _hit("serpapi", "https://b.example")]})
    out = await wsp.search("q")
    assert "Provider: serpapi · Date: 2026-10-01" in out
    assert f"Date: retrieved {wsp._today()}" in out


@pytest.mark.asyncio
async def test_two_providers_merge_by_url(monkeypatch) -> None:
    _providers(monkeypatch, {
        "serpapi": [_hit("serpapi", "https://www.Example.com/page/?utm_source=x#top")],
        "ddgs": [_hit("ddgs", "http://example.com/page", date="2026-09-09"),
                 _hit("ddgs", "https://other.example")],
    })
    out = await wsp.search("q", max_results=5)
    assert out.lower().count("example.com/page") == 1
    assert "also ddgs" in out and "Date: 2026-09-09" in out
    assert "https://other.example" in out


def test_normal_url_keeps_a_real_query_and_drops_tracking() -> None:
    assert wsp.normal_url("https://a.com/p?id=2&utm_medium=x") == "https://a.com/p?id=2"
    assert wsp.normal_url("https://a.com:8443/p") != wsp.normal_url("https://a.com/p")
    assert wsp.normal_url("HTTP://A.com:80/p/") == wsp.normal_url("https://a.com/p")


# ── 5. Sources ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_papers_asks_only_the_scholarly_providers_and_merges(monkeypatch) -> None:
    calls = _providers(monkeypatch, {
        "ddgs": [_hit("ddgs", "https://web.example")],
        "openalex": [_hit("openalex", "https://doi.org/10.1/x", date="2025-01-02")],
        "arxiv": [_hit("arxiv", "https://doi.org/10.1/x"), _hit("arxiv", "https://arxiv.org/abs/1")],
        "wikipedia": [_hit("wikipedia", "https://en.wikipedia.org/wiki/X")],
    })
    out = await wsp.search("q", source="papers")
    assert set(calls) == {"openalex", "arxiv"}
    assert "web.example" not in out and "wikipedia" not in out
    assert out.count("10.1/x") == 1 and "also arxiv" in out


@pytest.mark.asyncio
async def test_wiki_asks_wikipedia_and_an_unknown_source_means_web(monkeypatch) -> None:
    calls = _providers(monkeypatch, {"ddgs": [_hit("ddgs", "https://d.example")],
                                     "wikipedia": [_hit("wikipedia", "https://en.wikipedia.org/wiki/X")]})
    await wsp.search("q", source="wiki")
    assert calls == ["wikipedia"]
    calls.clear()
    await wsp.search("q", source="nonsense")
    assert calls == ["ddgs"]


# ── 6. Text ──────────────────────────────────────────────────────────────────


def test_hit_text_is_one_clean_capped_line() -> None:
    hit = wsp._hit("ddgs", "<b>Ti</b>\x00tle\nx", "https://a.example", "a" * 900 + " b")
    assert hit.title == "Ti tle x"
    assert len(hit.snippet) <= wsp.SNIPPET_CHARS and "\n" not in hit.snippet


@pytest.mark.parametrize("url", ["", "javascript:alert(1)", "ftp://x.example", "/relative",
                                 "https://a.example/" + "p" * 600])
def test_a_bad_url_is_dropped(url) -> None:
    assert wsp._hit("ddgs", "t", url, "s") is None


def test_a_date_keeps_only_its_leading_day() -> None:
    assert wsp._day("2026-10-01T10:00:00Z") == "2026-10-01"
    assert wsp._day("Oct 1, 2026") == ""
    assert wsp._day(None) == ""


@pytest.mark.asyncio
async def test_arxiv_refuses_an_answer_with_a_dtd(monkeypatch) -> None:
    import httpx

    async def get(client, url, **kw):
        return httpx.Response(200, text='<!DOCTYPE x [<!ENTITY a "b">]><feed/>',
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(wsp, "_get", get)
    with pytest.raises(ValueError):
        await wsp._arxiv("q", 3)


@pytest.mark.asyncio
async def test_arxiv_reads_an_atom_feed(monkeypatch) -> None:
    import httpx

    feed = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1</id>'
            '<title>A  paper</title><summary>Sum</summary><published>2026-01-02T00:00:00Z</published>'
            '</entry></feed>')

    async def get(client, url, **kw):
        return httpx.Response(200, text=feed, request=httpx.Request("GET", url))

    monkeypatch.setattr(wsp, "_get", get)
    [hit] = await wsp._arxiv("q", 3)
    assert (hit.provider, hit.title, hit.date) == ("arxiv", "A paper", "2026-01-02")


# ── Settings ─────────────────────────────────────────────────────────────────


def test_searxng_url_is_off_by_default_and_owner_only() -> None:
    assert Settings.model_fields["searxng_url"].default == ""
    assert env_guard.is_platform_env("SEARXNG_URL")


@pytest.mark.parametrize(("value", "want"), [
    ("", ""), ("http://127.0.0.1:8888/", "http://127.0.0.1:8888"),
    ("file:///etc/passwd", ""), ("searx", ""),
])
def test_searxng_url_takes_only_http(monkeypatch, value, want) -> None:
    monkeypatch.setattr(get_settings(), "searxng_url", value)
    assert wsp.searxng_url() == want
