"""WS-61 KB-3 — chunking, the embed door and the fusion, hermetic.

Spec: ``project-docs/specs/knowledge_base.md`` §6.2, §6.3 and §6.4.

* ``chunk_page``: headings give the path, a long section splits on
  paragraphs and repeats the path, a table is its own chunk with the header
  at most 40 rows, and the sha moves only with the text.
* ``embed``: it posts to the Console Router's ``/v1/embeddings`` on
  ``tier-embed`` with the run's member (CP-EMB). A zero vector, a short
  vector, a refusal, an error and a timeout are each ``None``, and it never
  raises. One log line for each call.
* ``fuse``: reciprocal rank fusion with k = 60, the age decay, and at most two
  chunks for each page.
* ``embed_query`` and ``embed_pending`` never call the door with
  ``KNOWLEDGE_EMBED`` off.

The SQL is proved against a real database in ``test_kb_search_r8.py``.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from typing import Any

import httpx
import pytest

pytest.importorskip("gateway.kb_index.chunk", reason="gateway not installed")

from acb_common import get_settings
from gateway.kb_index import embed as embed_mod
from gateway.kb_index import index as index_mod
from gateway.kb_index import query as query_mod
from gateway.kb_index.chunk import (
    CHARS_PER_TOKEN,
    MAX_TABLE_ROWS,
    MAX_TOKENS,
    chunk_page,
    content_sha,
    estimate_tokens,
)

ORG = "33333333-3333-3333-3333-333333333333"


# ── chunk_page ──────────────────────────────────────────────────────────────

def test_headings_give_the_full_path_rooted_at_the_title() -> None:
    body = (
        "Intro words.\n\n"
        "# Refunds\n\nRefund text.\n\n"
        "## Refund window\n\nThirty days.\n\n"
        "## Exceptions\n\nCustom parts.\n\n"
        "# Shipping\n\nShip text.\n"
    )
    chunks = chunk_page("Returns policy", body)
    assert [(c.heading_path, c.raw) for c in chunks] == [
        ("Returns policy", "Intro words."),
        ("Returns policy > Refunds", "Refund text."),
        ("Returns policy > Refunds > Refund window", "Thirty days."),
        ("Returns policy > Refunds > Exceptions", "Custom parts."),
        ("Returns policy > Shipping", "Ship text."),
    ]
    assert [c.ordinal for c in chunks] == list(range(5))


def test_a_body_that_opens_with_the_title_adds_no_level() -> None:
    chunks = chunk_page("Returns", "# Returns\n\nBody.\n\n## Window\n\nDays.")
    assert [c.heading_path for c in chunks] == ["Returns", "Returns > Window"]


def test_a_closing_hash_run_is_dropped_and_a_word_hash_is_kept() -> None:
    chunks = chunk_page("T", "## Use C# ##\n\nBody.")
    assert chunks[0].heading_path == "T > Use C#"


def test_a_heading_in_a_code_fence_is_text() -> None:
    body = "Before.\n\n```\n# not a heading\n\nstill code\n```\n\nAfter."
    chunks = chunk_page("T", body)
    assert len(chunks) == 1
    assert "# not a heading" in chunks[0].raw and chunks[0].heading_path == "T"


def test_a_short_section_is_one_chunk() -> None:
    body = "# A\n\none.\n\ntwo.\n\nthree."
    chunks = chunk_page("T", body)
    assert len(chunks) == 1
    assert chunks[0].raw == "one.\n\ntwo.\n\nthree."


def test_a_long_section_splits_on_paragraphs_and_repeats_the_path() -> None:
    # Five paragraphs of about 1500 characters each, each under the limit.
    paras = [" ".join(f"p{n}w{i}" for i in range(250)) for n in range(5)]
    body = "# Long\n\n" + "\n\n".join(paras)
    chunks = chunk_page("T", body)
    limit = MAX_TOKENS * CHARS_PER_TOKEN
    assert len(chunks) >= 3
    assert all(c.heading_path == "T > Long" for c in chunks)
    assert all(len(c.raw) <= limit for c in chunks)
    assert all(estimate_tokens(c.raw) <= MAX_TOKENS for c in chunks)
    # Each chunk is whole paragraphs: the split is on the blank lines.
    assert [p for c in chunks for p in c.raw.split("\n\n")] == paras


def test_one_paragraph_longer_than_the_limit_splits_on_words() -> None:
    body = " ".join(f"w{i}" for i in range(2000))
    chunks = chunk_page("T", body)
    assert len(chunks) >= 2
    assert all(len(c.raw) <= MAX_TOKENS * CHARS_PER_TOKEN for c in chunks)
    assert " ".join(c.raw for c in chunks).split() == body.split()


def test_a_table_is_its_own_chunk_with_the_header_repeated() -> None:
    rows = "\n".join(f"| sku-{i} | {i} |" for i in range(95))
    body = f"Price list.\n\n| SKU | Price |\n|---|---|\n{rows}\n\nAfter the table."
    chunks = chunk_page("Prices", body)
    tables = [c for c in chunks if c.metadata.get("kind") == "table"]
    assert len(tables) == 3  # 40 + 40 + 15 rows
    for t in tables:
        lines = t.raw.splitlines()
        assert lines[0] == "| SKU | Price |" and lines[1] == "|---|---|"
        assert len(lines) - 2 <= MAX_TABLE_ROWS
    assert [c.raw for c in chunks if not c.metadata] == ["Price list.", "After the table."]
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    # Reading order: the prose before, the table, the prose after.
    assert chunks[0].raw == "Price list." and chunks[-1].raw == "After the table."


def test_a_page_with_no_body_is_found_by_its_title() -> None:
    chunks = chunk_page("Holiday list", "")
    assert len(chunks) == 1
    assert chunks[0].heading_path == "Holiday list" and chunks[0].raw == ""


def test_the_sha_moves_only_with_the_text() -> None:
    body = "# A\n\nalpha text.\n\n# B\n\nbeta text."
    before = chunk_page("T", body)
    after = chunk_page("T", body.replace("beta text", "beta words"))
    assert before[0].content_sha == after[0].content_sha
    assert before[1].content_sha != after[1].content_sha
    assert before[0].content_sha == content_sha("T > A", "alpha text.")


def test_two_parts_with_one_sha_are_one_chunk() -> None:
    body = "# A\n\nsame.\n\n# A\n\nsame."
    assert len(chunk_page("T", body)) == 1


# ── The embed door (CP-EMB, customer_console.md §6A.16) ─────────────────────
#
# 🔴 **The fake is the HTTP transport, and the client is the real one.**
# `console_resolve.embeddings_on_console` runs as it does in production, and
# only `_new_http_client` answers from here. So a test proves the path, the
# credential and the member headers, and no test reaches AI/ML API.

CONSOLE_URL = "https://console.metorite.test"
DEPLOYMENT_KEY = "cc_depl_kbtest_secretsecretsecret"
MEMBER = "owner@acme.test"


class _Console:
    """A stand-in for the Console Router, behind the real client."""

    def __init__(self, answer: Any = None, error: BaseException | None = None,
                 status: int = 200) -> None:
        self.answer = answer
        self.error = error
        self.status = status
        self.requests: list[httpx.Request] = []
        self.timeouts: list[Any] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        body = json.loads(request.content)
        answer = self.answer(body["input"]) if callable(self.answer) else self.answer
        return httpx.Response(self.status, json=answer if answer is not None else {})

    def client(self, timeout: Any = None) -> httpx.AsyncClient:
        self.timeouts.append(timeout)
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Console:
        from acb_auth import console_resolve

        monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
        monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "")
        monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", DEPLOYMENT_KEY)
        monkeypatch.setenv("CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY", "true")
        get_settings.cache_clear()
        monkeypatch.setattr(console_resolve, "_new_http_client", self.client)
        return self


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _vectors(texts: list[str], value: float = 0.5) -> dict[str, Any]:
    return {"data": [{"index": i, "embedding": [value] * 1536} for i in range(len(texts))]}


def _as_member(coro_fn: Any) -> Any:
    """Run *coro_fn* inside a job bound to a PROVEN member, as CRM-Q27 does."""
    from acb_common._log import job_member_scope

    async def _run() -> Any:
        with job_member_scope(MEMBER, app="crm"):
            return await coro_fn()

    return asyncio.run(_run())


def test_embed_posts_to_the_console_door(monkeypatch: pytest.MonkeyPatch) -> None:
    """Item 8. The door is the Router's, on the box key, with the run's member."""
    console = _Console(_vectors).install(monkeypatch)
    out = _as_member(lambda: embed_mod.embed(["a", "b"], org=ORG))
    assert [len(v or []) for v in out] == [1536, 1536]
    req = console.requests[0]
    assert str(req.url) == CONSOLE_URL + "/v1/embeddings"
    assert json.loads(req.content) == {"model": "tier-embed", "input": ["a", "b"]}
    assert req.headers["Authorization"] == f"Bearer {DEPLOYMENT_KEY}"
    assert req.headers["X-CC-Member"] == MEMBER
    assert req.headers["X-CC-Member-Proven"] == "1"
    assert req.headers["X-CC-Module"] == "crm"
    # The caller's own bound, never a second one.
    assert console.timeouts == [embed_mod.TIMEOUT_S]


def test_a_run_with_no_app_reports_knowledge(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common._log import bind_run_context, run_context_scope

    console = _Console(_vectors).install(monkeypatch)

    async def _run() -> Any:
        with run_context_scope():
            bind_run_context(user=MEMBER, member_verified=True)
            return await embed_mod.embed(["a"], org=ORG, timeout=0.8)

    asyncio.run(_run())
    assert console.requests[0].headers["X-CC-Module"] == "knowledge"
    assert console.timeouts == [0.8]


def test_embed_sends_64_texts_at_most(monkeypatch: pytest.MonkeyPatch) -> None:
    console = _Console(_vectors).install(monkeypatch)
    texts = [f"t{i}" for i in range(embed_mod.BATCH * 2 + 1)]
    out = _as_member(lambda: embed_mod.embed(texts, org=ORG))
    sizes = [len(json.loads(r.content)["input"]) for r in console.requests]
    assert sizes == [64, 64, 1]
    assert all(v is not None for v in out) and len(out) == len(texts)


def test_an_empty_text_is_none_and_never_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The door answers 422 for an empty text, and one 422 made all 64 texts
    of its batch None. CRM note search stalled on exactly that."""

    def _vectors_or_422(texts: list[str]) -> dict[str, Any]:
        if any(not t.strip() for t in texts):
            raise AssertionError("an empty text reached the door")
        return _vectors(texts)

    console = _Console(_vectors_or_422).install(monkeypatch)
    out = _as_member(lambda: embed_mod.embed(["good", "", "  ", None, "also good"], org=ORG))
    sent = [json.loads(r.content)["input"] for r in console.requests]
    assert sent == [["good", "also good"]]
    assert [v is not None for v in out] == [True, False, False, False, True]
    assert len(out[0] or []) == 1536 and len(out[4] or []) == 1536


def test_only_empty_texts_send_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    console = _Console(_vectors).install(monkeypatch)
    assert _as_member(lambda: embed_mod.embed(["", " \n "], org=ORG)) == [None, None]
    assert console.requests == []


def test_a_zero_vector_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _Console(lambda texts: _vectors(texts, 0.0)).install(monkeypatch)
    assert _as_member(lambda: embed_mod.embed(["a"], org=ORG)) == [None]


def test_a_short_vector_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _Console({"data": [{"index": 0, "embedding": [0.1, 0.2]}]}).install(monkeypatch)
    assert _as_member(lambda: embed_mod.embed(["a"], org=ORG)) == [None]


def test_a_count_mismatch_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _Console(lambda texts: _vectors(texts[:1])).install(monkeypatch)
    assert _as_member(lambda: embed_mod.embed(["a", "b"], org=ORG)) == [None, None]


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (400, None),  # tier_unknown: nobody bound tier-embed
        (402, None),  # the balance wall
        (403, None),  # the run ceiling
        (422, None),  # an empty text
        (502, None),  # the vendor failed
        (503, None),  # no key for any step
        (200, httpx.ConnectError("down")),
        (200, httpx.ReadTimeout("slow")),
        (200, RuntimeError("anything at all")),
    ],
)
def test_every_failure_is_none_and_never_raises(
    monkeypatch: pytest.MonkeyPatch, status: int, error: BaseException | None,
) -> None:
    """Item 9. The callers' contract: the door never raises."""
    _Console({"detail": "no"}, error=error, status=status).install(monkeypatch)
    assert _as_member(lambda: embed_mod.embed(["a", "b"], org=ORG)) == [None, None]


def test_an_unwired_box_is_none_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "")
    get_settings.cache_clear()
    assert asyncio.run(embed_mod.embed(["a"], org=ORG)) == [None]


def test_a_box_with_no_member_is_none_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment-key box needs a PROVEN member. With none, the client
    refuses locally, no request goes out, and the answer is degraded."""
    console = _Console(_vectors).install(monkeypatch)
    assert asyncio.run(embed_mod.embed(["a"], org=ORG)) == [None]
    assert console.requests == []


def test_a_broken_client_is_none_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_auth import console_resolve

    async def _boom(*a: Any, **kw: Any) -> Any:
        raise ValueError("the client broke")

    _Console(_vectors).install(monkeypatch)
    monkeypatch.setattr(console_resolve, "embeddings_on_console", _boom)
    assert _as_member(lambda: embed_mod.embed(["a", "b"], org=ORG)) == [None, None]


def test_the_names_the_callers_import_stay() -> None:
    """Item 10. CRM-Q27 and the Knowledge search import these at load time.

    A renamed constant stops the CRM sync loop from loading, and a new
    vector size makes every stored vector stale.
    """
    import inspect

    from gateway.kb_index import index as index_mod_
    from gateway.kb_index import query as query_mod_

    assert embed_mod.BATCH == 64
    assert embed_mod.EMBED_DIMS == 1536
    assert callable(index_mod_.vector_literal)
    assert isinstance(query_mod_.QUERY_EMBED_TIMEOUT_S, float)
    sig = inspect.signature(embed_mod.embed)
    assert list(sig.parameters) == ["texts", "org", "timeout"]
    assert sig.parameters["org"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["timeout"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["timeout"].default == embed_mod.TIMEOUT_S
    assert inspect.iscoroutinefunction(embed_mod.embed)


def test_one_log_line_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _Console(_vectors).install(monkeypatch)
    lines: list[tuple[str, dict[str, Any]]] = []

    class _Log:
        def info(self, event: str, **kw: Any) -> None:
            lines.append((event, kw))

        def warning(self, event: str, **kw: Any) -> None:
            lines.append((event, kw))

    monkeypatch.setattr(embed_mod, "_log", _Log())
    texts = ["x" * 400] * (embed_mod.BATCH + 3)  # two requests, one call
    _as_member(lambda: embed_mod.embed(texts, org=ORG))
    assert [e for e, _ in lines] == ["knowledge.embed"]
    kw = lines[0][1]
    assert kw["organization_id"] == ORG and kw["chunks"] == len(texts)
    assert kw["token_estimate"] == 100 * len(texts)
    assert kw["embedded"] == len(texts) and kw["tier"] == "tier-embed"


# ── The flag: no call when off ──────────────────────────────────────────────

def _flags(monkeypatch: pytest.MonkeyPatch, embed_orgs: str) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_enabled", True, raising=False)
    monkeypatch.setattr(settings, "knowledge_orgs", "*", raising=False)
    monkeypatch.setattr(settings, "knowledge_embed", embed_orgs, raising=False)


def _count_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    async def fake(texts: list[str], **kw: Any) -> list[list[float] | None]:
        calls.append(list(texts))
        return [[0.1] * 1536 for _ in texts]

    monkeypatch.setattr(embed_mod, "embed", fake)
    return calls


def test_embed_query_with_the_flag_off_calls_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, "")
    calls = _count_calls(monkeypatch)
    assert asyncio.run(query_mod.embed_query(ORG, "refund window")) is None
    assert calls == []


def test_embed_query_with_the_flag_on_calls_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, ORG)
    calls = _count_calls(monkeypatch)
    assert asyncio.run(query_mod.embed_query(ORG, " refund   window ")) is not None
    assert calls == [["refund window"]]


def test_a_slow_query_embed_is_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, ORG)

    async def slow(texts: list[str], **kw: Any) -> list[list[float] | None]:
        await asyncio.sleep(5)
        return [[0.1] * 1536]

    monkeypatch.setattr(embed_mod, "embed", slow)
    monkeypatch.setattr(query_mod, "QUERY_EMBED_TIMEOUT_S", 0.01)
    assert asyncio.run(query_mod.embed_query(ORG, "refund")) is None


def test_embed_pending_with_the_flag_off_opens_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, "")
    calls = _count_calls(monkeypatch)

    async def no_select(*a: Any, **kw: Any) -> list[Any]:
        raise AssertionError("embed_pending read the index with the flag off")

    monkeypatch.setattr(index_mod, "select_pending", no_select)
    assert asyncio.run(index_mod.embed_pending(ORG, "p")) == 0
    assert calls == []


# ── fuse ────────────────────────────────────────────────────────────────────

TODAY = date(2026, 10, 16)


def _row(cid: int, page: str, age_days: int = 0) -> dict[str, Any]:
    return {"chunk_id": cid, "page_id": page, "valid_from": TODAY - timedelta(days=age_days)}


def test_rrf_with_k_60_sums_both_lists() -> None:
    lexical = [_row(1, "a"), _row(2, "b")]
    semantic = [_row(2, "b"), _row(3, "c")]
    out = query_mod.fuse(lexical, semantic, k=8, today=TODAY)
    scores = {r["chunk_id"]: r["score"] for r in out}
    assert scores[2] == pytest.approx(1 / 62 + 1 / 61)
    assert scores[1] == pytest.approx(1 / 61)
    assert scores[3] == pytest.approx(1 / 62)
    assert [r["chunk_id"] for r in out] == [2, 1, 3]


def test_the_lexical_half_alone_keeps_its_order() -> None:
    lexical = [_row(5, "a"), _row(4, "b"), _row(9, "c")]
    out = query_mod.fuse(lexical, [], k=8, today=TODAY)
    assert [r["chunk_id"] for r in out] == [5, 4, 9]


def test_the_age_decay_is_small_and_breaks_a_tie() -> None:
    old, new = _row(1, "a", age_days=730), _row(2, "b")
    out = query_mod.fuse([old], [new], k=8, today=TODAY)
    assert [r["chunk_id"] for r in out] == [2, 1]
    assert out[1]["score"] == pytest.approx((1 / 61) / (1 + 0.1 * 730 / 365.25))
    assert out[1]["score"] > 0.8 * out[0]["score"]


def test_at_most_two_chunks_for_each_page() -> None:
    lexical = [_row(i, "long") for i in range(1, 6)] + [_row(10, "other")]
    out = query_mod.fuse(lexical, [], k=8, today=TODAY)
    assert [r["page_id"] for r in out] == ["long", "long", "other"]


def test_k_caps_the_answer() -> None:
    lexical = [_row(i, f"p{i}") for i in range(1, 20)]
    assert len(query_mod.fuse(lexical, [], k=3, today=TODAY)) == 3


def test_a_page_indexes_at_most_max_chunks() -> None:
    """KB-3 review: many tiny headed sections cannot write thousands of rows."""
    from gateway.kb_index.chunk import MAX_CHUNKS, chunk_page

    body = "\n".join(f"# Part {i}\nline {i}" for i in range(MAX_CHUNKS * 3))
    assert len(chunk_page("Long", body)) == MAX_CHUNKS
