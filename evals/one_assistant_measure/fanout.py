"""The fan-out wiring of the measure: web fixtures, the worker plan, the timer
(WS-67 OA-8e phase 3, ``one_assistant_context.md`` §4.3.3 and §6).

``spawn_subagents`` (OA-9b) runs up to 5 read-only children. Each child is the
``worker`` agent, and its web tools are ``web_search`` and ``fetch_page``. A
live run may reach only the Router, and a child must never read the local
database, so everything a child can read comes from here:

* :class:`WebFixtures` replaces ``web_search`` and ``fetch_page`` in
  ``acb_skills.web_tools`` for one chat. It answers from synthetic pages:
  five companies, ten invoices and a price page. A URL with no page answers a
  plain "not found" line and is listed in :attr:`WebFixtures.unknown`.
* :class:`WorkerModel` is the scripted model of the worker. It decides from the
  request, not from a counter, because five children share one model and ask
  in any order.
* :func:`install_timer` times each ``_fan_out`` of ``agent_tools``.

Every name and number here is synthetic.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from evals.routing.harness import ReplyModel, text_turn, tool_turn

COMPANIES = ("Orbit Labs", "Kestrel Labs", "Vantage Systems", "Helix Foods", "Nimbus Cloud")
INVOICE_BASE = "https://billing.example/inv/"
#: The ten invoices, as ``(number, supplier, rupees)``. The total is 233,400.
INVOICES = (
    (1, "Delta Freight", 48_500), (2, "Kaveri Packaging", 12_300), (3, "Sunrise Electricals", 7_800),
    (4, "Orbit Labs", 31_000), (5, "Helix Foods", 22_400), (6, "Vantage Systems", 40_000),
    (7, "Nimbus Cloud", 9_900), (8, "Kestrel Labs", 15_500), (9, "Delta Freight", 27_000),
    (10, "Kaveri Packaging", 19_000),
)
INVOICE_TOTAL = sum(amount for _, _, amount in INVOICES)
PRICE_URL = "https://orbitlabs.example/pricing"


def company_url(name: str) -> str:
    return f"https://{name.split()[0].lower()}.example/about"


def _pages() -> dict[str, str]:
    pages = {
        company_url(c): f"{c} makes industrial sensors. Founded in {2010 + i}. "
                        f"It has {40 + 10 * i} staff and sells in {3 + i} countries."
        for i, c in enumerate(COMPANIES)
    }
    for number, supplier, amount in INVOICES:
        pages[f"{INVOICE_BASE}{number}"] = (
            f"Invoice {number} from {supplier}. Amount due: Rs {amount:,}. Due 15 November 2026.")
    pages[PRICE_URL] = "Orbit Labs pricing. The Basic plan costs Rs 4,999 a month."
    return pages


PAGES = _pages()


class WebFixtures:
    """Fixtures for ``web_search`` and ``fetch_page``. One instance for one chat.

    The functions keep the names of the real tools, because the risk table and
    the tool schema read ``__name__``."""

    def __init__(self) -> None:
        self.searches: list[str] = []
        self.fetches: list[str] = []
        self.unknown: list[str] = []

    def install(self, mp: Any) -> None:
        from acb_skills import web_tools

        fixtures = self

        async def web_search(query: str, max_results: int = 5) -> str:
            """Search the web and return the top results."""
            fixtures.searches.append(query)
            words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2]
            hits = [(u, t) for u, t in PAGES.items() if any(w in t.lower() or w in u for w in words)]
            if not hits:
                fixtures.unknown.append(f"search {query}")
                return f"No results for {query!r}."
            return "\n\n".join(f"{t[:40]}\n{u}\n{t[:120]}" for u, t in hits[:max_results])

        async def fetch_page(url: str, max_chars: int = 8000) -> str:
            """Fetch a public web page and return its content as clean plain text."""
            fixtures.fetches.append(url)
            page = PAGES.get(url.strip())
            if page is None:
                fixtures.unknown.append(f"fetch {url}")
                return f"fetch_page: {url} not found."
            return page[:max_chars]

        mp.setattr(web_tools, "web_search", web_search)
        mp.setattr(web_tools, "fetch_page", fetch_page)


# ── the worker plan ──────────────────────────────────────────────────────────

_URL = re.compile(r"https?://[^\s,;)\"']+")


def items_json(value: str, source: str, kind: str = "quoted") -> str:
    return json.dumps({"items": [{"value": value, "kind": kind, "source": source}]})


class WorkerModel(ReplyModel):
    """The scripted model of the ``worker`` agent.

    Stateless: the reply depends on the request. With no tool result in the
    messages it calls ``fetch_page`` on the first URL of the job (else
    ``web_search`` on the job). With a tool result it ends with one item that
    quotes the first line of that result and names the URL, so the parent can
    parse it (§6.4). A job with a tool named in :attr:`call_tool` calls it
    instead, for the test of a child that reaches for the local database."""

    def __init__(self, call_tool: str = "") -> None:
        super().__init__([text_turn(items_json("none", "inferred", "inferred"))])
        self.call_tool = call_tool

    def __call__(self, request: Any) -> Any:
        body = json.loads(request.content or b"{}")
        messages = body.get("messages") or []
        results = [str(m.get("content") or "") for m in messages if m.get("role") == "tool"]
        # The job is the LAST user message. Earlier ones can hold the words of the
        # member's chat, with URLs that belong to other jobs.
        users = [str(m.get("content") or "") for m in messages if m.get("role") == "user"]
        job = users[-1].rsplit("## The task", 1)[-1] if users else ""
        urls = list(dict.fromkeys(u.rstrip(".,;)") for u in _URL.findall(job)))
        if results:
            items = [{"value": (t.splitlines() or ["none"])[0][:200] or "none",
                      "kind": "quoted", "source": urls[i] if i < len(urls) else "fetch_page"}
                     for i, t in enumerate(results)]
            self.turns = [text_turn(json.dumps({"items": items}))]
        elif self.call_tool and "graph" in job:
            self.turns = [tool_turn(self.call_tool, {"query": "Orbit"}, "w1")]
        elif urls:
            self.turns = [self._calls([("fetch_page", {"url": u}) for u in urls])]
        else:
            self.turns = [tool_turn("web_search", {"query": job[-80:]}, "w1")]
        return super().__call__(request)

    @staticmethod
    def _calls(calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        """One model step with several tool calls, as the worker may make."""
        return [{"role": "assistant", "tool_calls": [
            {"index": i, "id": f"w{i}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}
            for i, (name, args) in enumerate(calls)]}, {"finish_reason": "tool_calls"}]


def jobs_for(urls: list[str], job: str = "Read {url} and give the key facts.") -> str:
    """The ``jobs`` argument of ``spawn_subagents``: one fetch job per URL."""
    return json.dumps([{"job": job.format(url=u), "tools": ["fetch_page"], "tier": "fast"}
                       for u in urls])


# ── the timer ────────────────────────────────────────────────────────────────


def install_timer(mp: Any, sink: list[dict[str, Any]], calls: list[Any]) -> None:
    """Time each fan-out of ``spawn_subagents`` and append it to *sink*."""
    from acb_skills import agent_tools

    real = agent_tools._fan_out

    async def timed(*args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        try:
            return await real(*args, **kwargs)
        finally:
            sink.append({"event": "measure.fanout", "level": "info", "calls_before": len(calls),
                         "ms": round((time.monotonic() - started) * 1000, 1)})

    mp.setattr(agent_tools, "_fan_out", timed)
