"""The search provider list of ``web_search`` (WS-WEB WEB-4, ``web_research.md`` §3.3).

``web_tools.web_search`` calls :func:`search` for an org that ``WEB_RESEARCH_ORGS``
holds. For another org it keeps the old chain (SerpAPI, then ddgs) byte for byte.

The rules, in order:

1. **Ready.** A provider is offered only when it is ready. SearXNG needs a
   ``SEARXNG_URL`` that answers ``/healthz``. SerpAPI needs its key. ddgs is
   always offered, as the fallback. The scholarly sources (OpenAlex, arXiv,
   Wikipedia) answer only a call that names ``source="papers"`` or ``"wiki"``.
2. **Health.** A provider that fails is marked for ``HEALTH_TTL_S`` (5 minutes).
   The list skips a marked provider. A later success clears the mark.
3. **Order.** For a web call: SearXNG, SerpAPI, then ddgs. The list asks the
   next provider only when the hits so far do not fill the call. A full answer
   of SearXNG never costs a SerpAPI call.
4. **Merge.** When two providers answer one call, the list merges their hits by
   URL. The first provider keeps the hit. The hit lists the others in ``also``.
5. **Stamp.** Each hit names its provider and its date. The date is the one the
   source states. With none, the stamp reads ``retrieved <day>``.
6. **Text.** Hit text comes from outside Metorite. The output holds one line of
   plain text for each field, with no control character, and a cap on each.

Every provider call has its own timeout. A fault in one provider is a mark and a
log line, never an exception to the caller.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
from acb_common import get_logger

_log = get_logger("acb_skills.web_search_providers")

#: How long the list skips a provider after a fault (spec §3.3).
HEALTH_TTL_S = 300.0
#: The timeout of one provider call, and of one health check.
PROVIDER_TIMEOUT_S = 10.0
HEALTH_TIMEOUT_S = 3.0
#: The most bytes that one provider answer may hold.
MAX_ANSWER_BYTES = 2 * 1024 * 1024
#: Caps on the text of one hit.
TITLE_CHARS = 200
SNIPPET_CHARS = 300
URL_CHARS = 500

SOURCE_WEB = "web"
SOURCE_PAPERS = "papers"
SOURCE_WIKI = "wiki"
SOURCES = frozenset({SOURCE_WEB, SOURCE_PAPERS, SOURCE_WIKI})

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]+")
_TAG_RE = re.compile(r"<[^<>]+>")
_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?")
#: Query parameters that name a visit, never a page. The merge drops them.
_TRACKING = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid", "ref_src"})


@dataclass
class Hit:
    """One search hit, with the provider that gave it and a date."""

    title: str
    url: str
    snippet: str
    provider: str
    date: str = ""
    also: list[str] = field(default_factory=list)


# ── Health marks ─────────────────────────────────────────────────────────────

_FAILED_AT: dict[str, float] = {}


def _now() -> float:
    return time.monotonic()


def mark_failed(name: str) -> None:
    _FAILED_AT[name] = _now()


def mark_ok(name: str) -> None:
    _FAILED_AT.pop(name, None)


def is_marked(name: str) -> bool:
    """True when *name* failed in the last ``HEALTH_TTL_S`` seconds."""
    at = _FAILED_AT.get(name)
    if at is None:
        return False
    if _now() - at >= HEALTH_TTL_S:
        _FAILED_AT.pop(name, None)
        return False
    return True


def reset_health() -> None:
    _FAILED_AT.clear()


# ── Text and URL helpers ─────────────────────────────────────────────────────


def clean(value: object, limit: int) -> str:
    """One line of plain text: no tag, no control character, capped."""
    text = _TAG_RE.sub(" ", str(value or ""))
    text = _CONTROL_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text[:limit]


def _day(value: object) -> str:
    """The leading ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD`` of *value*, or ``""``."""
    match = _DATE_RE.match(str(value or "").strip())
    return match.group(0) if match else ""


def normal_url(url: str) -> str:
    """The key under which two hits of one page meet.

    Lower-case scheme and host, no fragment, no default port, no tracking
    parameter, no trailing slash. Equal keys merge. An unparsable URL is its
    own key."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:  # a bad port or bracket: the URL is its own key
        return url.strip()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    scheme = parts.scheme.lower()
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING
    ]
    path = parts.path.rstrip("/")
    return urlunsplit(("https" if scheme in ("http", "https") else scheme, host, path,
                       urlencode(query), ""))


def merge(lists: list[list[Hit]], limit: int) -> list[Hit]:
    """Merge hit lists by URL. The first list keeps a hit. Later providers join ``also``."""
    seen: dict[str, Hit] = {}
    order: list[Hit] = []
    for hits in lists:
        for hit in hits:
            key = normal_url(hit.url)
            if not key:
                continue
            held = seen.get(key)
            if held is None:
                seen[key] = hit
                order.append(hit)
            elif hit.provider != held.provider and hit.provider not in held.also:
                held.also.append(hit.provider)
                if not held.date and hit.date:
                    held.date = hit.date
    return order[:limit]


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def format_hits(query: str, hits: list[Hit]) -> str:
    """The text block that ``web_search`` returns. Each hit names provider and date."""
    lines = [f"Web search results for: {query!r}\n"]
    today = _today()
    for i, hit in enumerate(hits, 1):
        stamp = hit.date or f"retrieved {today}"
        via = hit.provider + (f" (also {', '.join(hit.also)})" if hit.also else "")
        lines.append(f"[{i}] {hit.title or '(no title)'}")
        lines.append(f"    URL: {hit.url}")
        lines.append(f"    Provider: {via} · Date: {stamp}")
        if hit.snippet:
            lines.append(f"    {hit.snippet}")
        lines.append("")
    return "\n".join(lines).strip()


# ── Settings ─────────────────────────────────────────────────────────────────


def searxng_url() -> str:
    """The operator's ``SEARXNG_URL``, with no trailing slash, or ``""``.

    Read on each call. Only the operator writes it (``env_guard``), so it is
    never a value from a member."""
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "searxng_url", "") or "").strip()
    except Exception:
        return ""
    if not raw.lower().startswith(("http://", "https://")):
        return ""
    return raw.rstrip("/")


def _serpapi_key() -> str:
    from acb_skills.integrations import credential

    key = credential("SERPAPI_API_KEY")
    if not key:
        try:
            from acb_common import get_settings

            key = getattr(get_settings(), "serpapi_api_key", "") or ""
        except Exception:
            key = ""
    return key


# ── The providers ────────────────────────────────────────────────────────────


async def _get(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    """GET with a size cap. The body is read only up to ``MAX_ANSWER_BYTES``."""
    async with client.stream("GET", url, **kw) as resp:
        resp.raise_for_status()
        body = bytearray()
        async for chunk in resp.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_ANSWER_BYTES:
                raise ValueError("answer too large")
        out = httpx.Response(resp.status_code, headers=resp.headers, content=bytes(body),
                             request=resp.request)
    return out


def _client() -> httpx.AsyncClient:
    # No redirect and no proxy from the env: a provider is a fixed endpoint.
    return httpx.AsyncClient(timeout=PROVIDER_TIMEOUT_S, follow_redirects=False,
                             trust_env=False,
                             headers={"User-Agent": "Metorite-web-search/1.0"})


def _hit(provider: str, title: object, url: object, snippet: object, date: object = "") -> Hit | None:
    link = str(url or "").strip()
    if not link.lower().startswith(("http://", "https://")) or len(link) > URL_CHARS:
        return None
    try:
        urlsplit(link).port  # noqa: B018 - a bad port drops the hit
    except ValueError:
        return None
    return Hit(clean(title, TITLE_CHARS), link, clean(snippet, SNIPPET_CHARS), provider, _day(date))


async def _searxng(query: str, n: int) -> list[Hit]:
    base = searxng_url()
    async with _client() as client:
        resp = await _get(client, f"{base}/search",
                          params={"q": query, "format": "json", "safesearch": "1"})
    data = resp.json()
    hits = [_hit("searxng", r.get("title"), r.get("url"), r.get("content"),
                 r.get("publishedDate")) for r in (data.get("results") or [])]
    return [h for h in hits if h][:n]


async def _searxng_health() -> bool:
    base = searxng_url()
    if not base:
        return False
    async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_S, follow_redirects=False,
                                 trust_env=False) as client:
        resp = await client.get(f"{base}/healthz")
    return resp.status_code == 200


async def _serpapi(query: str, n: int) -> list[Hit]:
    key = _serpapi_key()
    async with _client() as client:
        resp = await _get(client, "https://serpapi.com/search.json",
                          params={"q": query, "num": n, "engine": "google", "api_key": key})
    data = resp.json()
    hits = [_hit("serpapi", r.get("title"), r.get("link"), r.get("snippet"), r.get("date"))
            for r in (data.get("organic_results") or [])]
    return [h for h in hits if h][:n]


async def _ddgs(query: str, n: int) -> list[Hit]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS  # legacy name

    def _sync() -> list[dict]:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=n, backend="auto"))

    rows = await asyncio.to_thread(_sync)
    hits = [_hit("ddgs", r.get("title"), r.get("href"), r.get("body"), r.get("date"))
            for r in rows]
    return [h for h in hits if h][:n]


async def _openalex(query: str, n: int) -> list[Hit]:
    async with _client() as client:
        resp = await _get(client, "https://api.openalex.org/works",
                          params={"search": query, "per-page": n,
                                  "select": "id,doi,title,publication_date,primary_location"})
    hits: list[Hit] = []
    for r in resp.json().get("results") or []:
        loc = r.get("primary_location") or {}
        url = r.get("doi") or loc.get("landing_page_url") or r.get("id")
        source = (loc.get("source") or {}).get("display_name") or ""
        hit = _hit("openalex", r.get("title"), url, source, r.get("publication_date"))
        if hit:
            hits.append(hit)
    return hits[:n]


_ATOM = "{http://www.w3.org/2005/Atom}"


async def _arxiv(query: str, n: int) -> list[Hit]:
    from xml.etree import ElementTree

    async with _client() as client:
        resp = await _get(client, "https://export.arxiv.org/api/query",
                          params={"search_query": f"all:{query}", "max_results": n})
    text = resp.text
    # The standard parser expands entities. arXiv sends none, so refuse any.
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError("arxiv answer holds a DTD")
    root = ElementTree.fromstring(text)
    hits: list[Hit] = []
    for entry in root.iter(f"{_ATOM}entry"):
        hit = _hit("arxiv", entry.findtext(f"{_ATOM}title"), entry.findtext(f"{_ATOM}id"),
                   entry.findtext(f"{_ATOM}summary"), entry.findtext(f"{_ATOM}published"))
        if hit:
            hits.append(hit)
    return hits[:n]


async def _wikipedia(query: str, n: int) -> list[Hit]:
    async with _client() as client:
        resp = await _get(client, "https://en.wikipedia.org/w/api.php",
                          params={"action": "query", "list": "search", "srsearch": query,
                                  "srlimit": n, "format": "json"})
    hits: list[Hit] = []
    for r in (resp.json().get("query") or {}).get("search") or []:
        title = r.get("title") or ""
        hit = _hit("wikipedia", title,
                   f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                   r.get("snippet"), r.get("timestamp"))
        if hit:
            hits.append(hit)
    return hits[:n]


@dataclass(frozen=True)
class Provider:
    name: str
    source: str
    run: Callable[[str, int], Awaitable[list[Hit]]]
    #: ``None`` means no ready check beyond :attr:`configured`.
    health: Callable[[], Awaitable[bool]] | None
    configured: Callable[[], bool]


def providers() -> list[Provider]:
    """Every provider, in the order of the list. Built on each call so that a
    test can patch a runner."""
    return [
        Provider("searxng", SOURCE_WEB, _searxng, _searxng_health, lambda: bool(searxng_url())),
        Provider("serpapi", SOURCE_WEB, _serpapi, None, lambda: bool(_serpapi_key())),
        Provider("ddgs", SOURCE_WEB, _ddgs, None, lambda: True),
        Provider("openalex", SOURCE_PAPERS, _openalex, None, lambda: True),
        Provider("arxiv", SOURCE_PAPERS, _arxiv, None, lambda: True),
        Provider("wikipedia", SOURCE_WIKI, _wikipedia, None, lambda: True),
    ]


async def _ready(p: Provider) -> bool:
    """True when *p* is configured, unmarked and (if it has one) passes its health check."""
    if not p.configured() or is_marked(p.name):
        return False
    if p.health is None:
        return True
    try:
        ok = await asyncio.wait_for(p.health(), HEALTH_TIMEOUT_S + 1)
    except Exception as exc:
        _log.warning("web_search.health", provider=p.name, fault=type(exc).__name__)
        ok = False
    if not ok:
        mark_failed(p.name)
    return ok


async def _ask(p: Provider, query: str, n: int) -> list[Hit] | str:
    """Run one provider. A fault marks it and returns its text for the error line."""
    try:
        hits = await asyncio.wait_for(p.run(query, n), PROVIDER_TIMEOUT_S + 2)
    except Exception as exc:
        mark_failed(p.name)
        _log.warning("web_search.provider_fault", provider=p.name, fault=type(exc).__name__)
        return f"{p.name}: {type(exc).__name__}"
    mark_ok(p.name)
    return hits


async def search(query: str, max_results: int = 5, source: str = SOURCE_WEB) -> str:
    """Search with the providers that are ready. See the module doc."""
    n = max(1, min(10, int(max_results)))
    kind = source if source in SOURCES else SOURCE_WEB
    chosen = [p for p in providers() if p.source == kind]

    if kind == SOURCE_WEB:
        # Ask the ready providers in order. Stop when the merged hits fill the
        # call. A fault or an empty answer passes to the next provider.
        faults: list[str] = []
        lists: list[list[Hit]] = []
        for p in chosen:
            if not await _ready(p):
                continue
            got = await _ask(p, query, n)
            if isinstance(got, str):
                faults.append(got)
                continue
            if got:
                _log.info("web_search.answer", provider=p.name, hits=len(got))
                lists.append(got)
                if len(merge(lists, n)) >= n:
                    break
        hits = merge(lists, n)
        if hits:
            return format_hits(query, hits)
        if faults:
            return "web_search failed: " + "; ".join(faults)
        return f"No results found for: {query!r}"

    # Scholarly and reference sources run together and merge by URL.
    ready = [p for p in chosen if await _ready(p)]
    answers = await asyncio.gather(*[_ask(p, query, n) for p in ready])
    lists = [a for a in answers if isinstance(a, list)]
    faults = [a for a in answers if isinstance(a, str)]
    hits = merge(lists, n)
    if hits:
        return format_hits(query, hits)
    if faults:
        return "web_search failed: " + "; ".join(faults)
    return f"No results found for: {query!r}"
