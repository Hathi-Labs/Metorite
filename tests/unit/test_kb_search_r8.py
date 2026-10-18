"""R8 — the Knowledge index and ``GET /knowledge/search`` on a real database.

Spec: ``project-docs/specs/knowledge_base.md`` §6.1 to §6.4 and the KB-3 row
of §17. WS-61 KB-3.

What this proves against a REAL Postgres, through the real ``/knowledge``
router, as the NON-privileged role of the phase-4 rehearsal (no superuser,
no BYPASSRLS), with the whole ladder, migration ``kb_chunks``, the four
generated phases and two organizations:

- two orgs, two spaces, one grant each: a search finds only the visible
  published pages;
- a draft is never found, and has no chunk;
- an archived page leaves the index in the commit of the archive;
- a move updates ``kb_chunks.space_id``, and the readers follow the space;
- the exact token ``ZOHO-4471`` is found by the lexical half;
- a paraphrase is found by the semantic half, with a fixed fake embedder;
- with the embed door down, the answer is the lexical half, ``degraded``;
- a zero vector from the door is stored as NULL;
- a one-word edit re-embeds only the chunk that changed;
- the table forces row level security, refuses a row of another tenant,
  carries the partial HNSW index, and its migration replays.

KB-3c adds, against the same database:

- ``GET /search/company`` finds a page by a paraphrase that shares no word
  with it, with one embed call, and only when ``KNOWLEDGE_EMBED`` names the
  org;
- the command bar's Knowledge provider makes no embed call;
- two concurrent saves embed each chunk once, and the job carries the
  acting member;
- a page published before the flag gets its vectors in one sweep tick, the
  sweep leaves a fresh chunk to its job, takes ``MAX_CHUNKS`` at most, binds
  the author, and never reads an org off the list.

The generated ``tsv``, ``websearch_to_tsquery``, the ``<=>`` operator, the
partial HNSW index, the upsert on ``content_sha`` and the join under the one
visibility clause are each SQL that a hermetic fake would accept unread.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    _URL,
    app_engine,
    promoted,
)
from tests.unit.test_kb_pages_r8 import (  # noqa: F401
    CAPABILITY,
    _add_group,
    _add_member,
    _address,
    _app_dsn,
    _assert_non_priv,
    _call,
    _ok,
    _principal,
    _space,
    ns,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

DIMS = 1536

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"


def _chunks_migration() -> Path:
    """The migration that creates ``kb_chunks``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS kb_chunks" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]

#: The fake embedder's concepts. A text gets one axis for each concept it
#: names, so a paraphrase lands on the axis of the page it means.
CONCEPTS: tuple[tuple[str, ...], ...] = (
    ("refund", "money back", "return"),
    ("ship", "courier", "deliver"),
    ("holiday", "leave", "vacation"),
)


def fake_vector(value: str) -> list[float]:
    """A fixed, non-zero vector for *value*: one axis for each concept."""
    low = value.lower()
    vec = [0.0] * DIMS
    for axis, words in enumerate(CONCEPTS):
        if any(w in low for w in words):
            vec[axis] = 1.0
    vec[DIMS - 1] = 0.01  # never all zero
    return vec


class Embedder:
    """A stand-in for ``kb_index.embed.embed``. It counts each call.

    It records the member of the run context of each call (KB-3c). A
    ``delay`` holds each call open, so two jobs overlap.
    """

    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[list[str]] = []
        self.members: list[str | None] = []
        self.delay = delay

    async def __call__(self, texts: list[str], **kw: Any) -> list[list[float] | None]:
        from acb_llm.routed import run_attribution

        self.calls.append(list(texts))
        self.members.append(run_attribution().get("member"))
        if self.delay:
            await asyncio.sleep(self.delay)
        return [fake_vector(t) for t in texts]


@pytest.fixture
def kb(ns, monkeypatch):  # noqa: F811 — a fixture
    """The KB-1 fixture, and the chunks deleted before its own cleanup."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "knowledge_embed", "", raising=False)
    yield ns
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_chunks"))


def _embed_on(monkeypatch: pytest.MonkeyPatch, embedder: Any = None) -> Embedder | None:
    from acb_common import get_settings
    from gateway.kb_index import embed as embed_mod

    monkeypatch.setattr(get_settings(), "knowledge_embed", "*", raising=False)
    if embedder is not None:
        monkeypatch.setattr(embed_mod, "embed", embedder)
    return embedder


def _door(monkeypatch: pytest.MonkeyPatch, *, error: BaseException | None = None,
          zero: bool = False) -> list[Any]:
    """Replace the HTTP transport of the REAL ``embed()``: down, or zero vectors.

    Since CP-EMB (``customer_console.md`` §6A.16) the real ``embed()`` asks
    the Console Router through ``console_resolve.embeddings_on_console``. So
    the box is wired on an org key, and only the transport answers here.
    """
    import json as json_mod

    from acb_auth import console_resolve
    from acb_common import get_settings

    seen: list[Any] = []

    def _handle(request: httpx.Request) -> httpx.Response:
        body = json_mod.loads(request.content)
        seen.append(body)
        if error is not None:
            raise error
        data = [{"index": i, "embedding": [0.0] * DIMS if zero else fake_vector(t)}
                for i, t in enumerate(body["input"])]
        return httpx.Response(200, json={"data": data})

    settings = get_settings()
    monkeypatch.setattr(settings, "customer_console_url", "https://console.test",
                        raising=False)
    monkeypatch.setattr(settings, "customer_console_org_key", "cc_live_kb_test",
                        raising=False)
    monkeypatch.setattr(
        console_resolve, "_new_http_client",
        lambda timeout=None: httpx.AsyncClient(transport=httpx.MockTransport(_handle)))
    return seen


async def _page(p: Any, writer: Any, space_id: str, title: str, body: str,
                *, publish: bool = True) -> str:
    page = await _ok(p, writer, "POST", "/knowledge/pages",
                     {"space_id": space_id, "title": title, "body_md": body}, status=201)
    if publish:
        await _ok(p, writer, "POST", f"/knowledge/pages/{page['id']}/publish")
    return page["id"]


async def _search(p: Any, user: Any, q: str, **params: Any) -> dict[str, Any]:
    from urllib.parse import urlencode

    query = urlencode({"q": q, **params})
    return await _ok(p, user, "GET", f"/knowledge/search?{query}")


def _found(answer: dict[str, Any]) -> list[str]:
    return [h["page_id"] for h in answer["hits"]]


def _chunks(p: Any, page_id: str) -> list[Any]:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT space_id::text AS space_id, content_sha, heading_path, raw, "
            "embedding IS NOT NULL AS has_vector FROM kb_chunks "
            "WHERE source_id = CAST(:p AS uuid) ORDER BY ordinal"), {"p": page_id}).all()


# ── Visibility: two orgs, two spaces, one grant each ───────────────────────

async def test_search_finds_only_visible_published_pages(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    seller, outsider = _address("seller"), _address("outsider")
    seller_id = _add_member(kb, kb.org_a, seller)
    _add_member(kb, kb.org_a, outsider)
    _add_group(kb, kb.org_a, "sales", [seller_id])
    admin_a = _principal(_address("admin-a"), kb.org_a, CAPABILITY)
    admin_b = _principal(_address("admin-b"), kb.org_b, CAPABILITY)

    sales = await _space(kb, admin_a, "Sales", [{"subject": "group:sales", "level": "read"}])
    ops = await _space(kb, admin_a, "Ops", [{"subject": outsider, "level": "read"}])
    hb = await _space(kb, admin_b, "Handbook", [{"subject": "org", "level": "read"}])
    on_sales = await _page(kb, admin_a, sales, "Sales playbook", "Pricing ladder for distributors.")
    on_ops = await _page(kb, admin_a, ops, "Ops runbook", "Pricing ladder for the warehouse.")
    on_b = await _page(kb, admin_b, hb, "B handbook", "Pricing ladder of org B.")

    seller_a = _principal(seller, kb.org_a)
    outsider_a = _principal(outsider, kb.org_a)
    member_b = _principal(_address("member-b"), kb.org_b)

    assert _found(await _search(kb, seller_a, "pricing ladder")) == [on_sales]
    assert _found(await _search(kb, outsider_a, "pricing ladder")) == [on_ops]
    assert _found(await _search(kb, member_b, "pricing ladder")) == [on_b]
    assert _found(await _search(kb, admin_a, "pricing ladder", space=sales)) == [on_sales]

    hit = (await _search(kb, seller_a, "pricing ladder"))["hits"][0]
    assert set(hit) == {"page_id", "space_id", "space_name", "title", "heading_path",
                        "text", "score", "updated_at", "href"}
    assert hit["space_id"] == sales and hit["space_name"] == "Sales"
    assert hit["href"] == f"/knowledge/pages/{on_sales}"
    assert hit["heading_path"] == "Sales playbook"

    # The rows carry their org, and org B's rows are not readable from org A.
    with kb.admin_engine.connect() as c:
        orgs = {r[0] for r in c.execute(text(
            "SELECT DISTINCT organization_id::text FROM kb_chunks")).all()}
    assert orgs == {kb.org_a, kb.org_b}


async def test_a_draft_is_never_found(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Drafts", [{"subject": "org", "level": "read"}])
    draft = await _page(kb, admin, space, "Secret plan", "Merger codename BLUEFIN.",
                        publish=False)
    # Edits of a draft write no chunk either.
    await _ok(kb, admin, "PATCH", f"/knowledge/pages/{draft}",
              {"body_md": "Merger codename BLUEFIN, phase two."})
    assert _chunks(kb, draft) == []
    # The author can read the draft, and still the search does not find it.
    assert (await _call(kb, admin, "GET", f"/knowledge/pages/{draft}")).status_code == 200
    assert _found(await _search(kb, admin, "BLUEFIN")) == []


async def test_an_archived_page_leaves_the_index(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Policies", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Travel", "Book trains for trips under 500 km.")
    assert _found(await _search(kb, admin, "trains")) == [page]
    assert _chunks(kb, page)

    await _ok(kb, admin, "POST", f"/knowledge/pages/{page}/archive")
    assert _chunks(kb, page) == []
    assert _found(await _search(kb, admin, "trains")) == []

    # Restore brings a published page back, and the index with it.
    await _ok(kb, admin, "POST", f"/knowledge/pages/{page}/restore")
    assert _found(await _search(kb, admin, "trains")) == [page]


async def test_a_move_updates_the_space_of_the_chunks(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    reader_x, reader_y = _address("rx"), _address("ry")
    _add_member(kb, kb.org_a, reader_x)
    _add_member(kb, kb.org_a, reader_y)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    x = await _space(kb, admin, "Xspace", [{"subject": reader_x, "level": "read"}])
    y = await _space(kb, admin, "Yspace", [{"subject": reader_y, "level": "read"}])
    page = await _page(kb, admin, x, "Forklift", "Forklift licence renewal steps.")
    shas = {r.content_sha for r in _chunks(kb, page)}
    assert {r.space_id for r in _chunks(kb, page)} == {x}
    assert _found(await _search(kb, _principal(reader_x, kb.org_a), "forklift")) == [page]

    await _ok(kb, admin, "POST", f"/knowledge/pages/{page}/move", {"space_id": y})
    assert {r.space_id for r in _chunks(kb, page)} == {y}
    assert {r.content_sha for r in _chunks(kb, page)} == shas, "a move keeps the rows"
    assert _found(await _search(kb, _principal(reader_x, kb.org_a), "forklift")) == []
    assert _found(await _search(kb, _principal(reader_y, kb.org_a), "forklift")) == [page]


# ── The two halves ──────────────────────────────────────────────────────────

async def test_the_exact_token_is_found_lexically(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Finance", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Late invoices",
                       "# Open\n\nInvoice ZOHO-4471 was paid late.\n\n"
                       "# Closed\n\nInvoice ZOHO-4472 was paid on time.")
    answer = await _search(kb, _principal(_address("m"), kb.org_a), "ZOHO-4471")
    assert answer["degraded"] is True, "KNOWLEDGE_EMBED is off: the lexical half only"
    assert _found(answer) == [page]
    assert answer["hits"][0]["heading_path"] == "Late invoices > Open"
    assert "ZOHO-4471" in answer["hits"][0]["text"]


async def test_a_paraphrase_is_found_with_a_fixed_embedder(
        kb, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    embedder = _embed_on(monkeypatch, Embedder())
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Customer", [{"subject": "org", "level": "read"}])
    refunds = await _page(kb, admin, space, "Returns policy",
                          "Customers can return goods within thirty days for a full refund.")
    await _page(kb, admin, space, "Shipping", "The courier collects parcels at noon.")
    assert embedder.calls, "the publish embedded its chunks after the commit"
    assert all(r.has_vector for r in _chunks(kb, refunds))

    member = _principal(_address("m"), kb.org_a)
    # No word of the question is in the page, so only the vector finds it.
    lexical = await _search(kb, member, "how do I get my money back", hybrid="false")
    assert lexical["hits"] == [] and lexical["degraded"] is True
    answer = await _search(kb, member, "how do I get my money back")
    assert answer["degraded"] is False
    assert _found(answer)[0] == refunds


async def test_the_door_down_answers_the_lexical_half_degraded(
        kb, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    _embed_on(monkeypatch)
    seen = _door(monkeypatch, error=httpx.ConnectError("door down"))
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Plant", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Kiln", "Kiln temperature limit is 1200 C.")
    assert seen, "the real embed() reached the door, and the door was down"
    assert not any(r.has_vector for r in _chunks(kb, page))

    answer = await _search(kb, _principal(_address("m"), kb.org_a), "kiln temperature")
    assert answer["degraded"] is True
    assert _found(answer) == [page]


async def test_a_zero_vector_is_stored_as_null(kb, app_engine, monkeypatch) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _embed_on(monkeypatch)
    seen = _door(monkeypatch, zero=True)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Zero", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Zero page", "# One\n\nalpha.\n\n# Two\n\nbeta.")
    assert seen, "the door answered"
    rows = _chunks(kb, page)
    assert len(rows) == 2
    assert not any(r.has_vector for r in rows), "a zero vector was stored"
    with kb.admin_engine.connect() as c:
        stored_zero = c.execute(text(
            "SELECT count(*) FROM kb_chunks WHERE embedding IS NOT NULL "
            "AND embedding = CAST(:z AS vector)"),
            {"z": "[" + ",".join(["0"] * DIMS) + "]"}).scalar()
    assert stored_zero == 0


async def test_a_one_word_edit_re_embeds_only_the_changed_chunk(
        kb, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    embedder = _embed_on(monkeypatch, Embedder())
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Manual", [{"subject": "org", "level": "read"}])
    body = ("# Setup\n\nPlug in the printer.\n\n"
            "# Calibrate\n\nLevel the bed with paper.\n\n"
            "# Print\n\nSlice the model and start.")
    page = await _page(kb, admin, space, "Printer", body)
    assert [len(c) for c in embedder.calls] == [3]
    before = {r.heading_path: r.content_sha for r in _chunks(kb, page)}

    await _ok(kb, admin, "PATCH", f"/knowledge/pages/{page}",
              {"body_md": body.replace("with paper", "with cardstock")})
    assert [len(c) for c in embedder.calls] == [3, 1]
    assert "cardstock" in embedder.calls[-1][0]
    after = {r.heading_path: r.content_sha for r in _chunks(kb, page)}
    assert set(after) == set(before)
    changed = {k for k in after if after[k] != before[k]}
    assert changed == {"Printer > Calibrate"}
    assert all(r.has_vector for r in _chunks(kb, page))

    # An edit that changes no chunk embeds nothing.
    await _ok(kb, admin, "PATCH", f"/knowledge/pages/{page}", {"reviewers": []})
    assert [len(c) for c in embedder.calls] == [3, 1]


async def test_with_embed_off_the_door_is_never_called(
        kb, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    seen = _door(monkeypatch)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Quiet", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Quiet page", "Nothing to embed here.")
    answer = await _search(kb, admin, "embed")
    assert seen == []
    assert _found(answer) == [page] and answer["degraded"] is True


# ── The table itself ────────────────────────────────────────────────────────

def test_the_table_forces_row_level_security(kb) -> None:
    with kb.admin_engine.connect() as c:
        forced = c.execute(text(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
            "WHERE oid = to_regclass('kb_chunks')")).scalar()
        policies = c.execute(text(
            "SELECT count(*) FROM pg_policies WHERE tablename = 'kb_chunks'")).scalar()
        indexes = {r[0]: r[1] for r in c.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'kb_chunks'"))}
    assert forced is True and policies == 1
    assert "USING hnsw (embedding vector_cosine_ops) WHERE (embedding IS NOT NULL)" in (
        indexes["kb_chunks_embedding_idx"])
    assert "USING gin (tsv)" in indexes["kb_chunks_tsv_idx"]


def test_a_chunk_for_another_tenant_is_refused(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    with app_engine.connect() as c:
        trans = c.begin()
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": kb.org_a})
        with pytest.raises(DBAPIError):
            c.execute(text(
                "INSERT INTO kb_chunks (organization_id, space_id, source_kind, "
                "source_id, ordinal, raw, content_sha) VALUES (CAST(:o AS uuid), "
                "gen_random_uuid(), 'page', gen_random_uuid(), 0, 'x', 'x')"),
                {"o": kb.org_b})
        trans.rollback()


def test_the_migration_replays_with_no_change(kb) -> None:
    sql = _chunks_migration().read_text(encoding="utf-8")
    with kb.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
    with kb.admin_engine.connect() as c:
        policies = c.execute(text(
            "SELECT count(*) FROM pg_policies WHERE tablename = 'kb_chunks'")).scalar()
    assert policies == 1


# ── KB-3c: meaning in the assistant's search ───────────────────────────────

async def _bound(p: Any, user: Any, coro_fn: Any) -> Any:
    """Run *coro_fn* as a request runs it: the app engine, the user's tenant."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    from tests.unit._tenant_ladder import tenant_engine_scope

    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            if user is not None:
                bind_tenant(user.organization_id)
            return await coro_fn()
    finally:
        release_tenant(scope)


async def _company(p: Any, user: Any, q: str) -> dict[str, Any]:
    """``GET /search/company`` through FastAPI, with room for a cold database."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from gateway.retrieval import company

    async def _caller() -> Any:
        from acb_common.db import bind_tenant

        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(company.router)
    app.dependency_overrides[get_current_user] = _caller

    async def go() -> Any:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://kb3c.test") as client:
            return await client.get("/search/company", params={"q": q})

    res = await _bound(p, None, go)
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture
def roomy(monkeypatch):
    from gateway.retrieval import company

    monkeypatch.setattr(company, "SOURCE_TIMEOUT_S", 30.0)
    monkeypatch.setattr(company, "TOTAL_BUDGET_S", 60.0)


def _knowledge_ids(answer: dict[str, Any]) -> list[str]:
    assert answer["skipped"] == [], answer["skipped"]
    return [h["id"] for h in answer["hits"] if h["app"] == "knowledge"]


async def test_company_search_finds_a_paraphrase_by_meaning(
        kb, app_engine, monkeypatch, roomy,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    embedder = _embed_on(monkeypatch, Embedder())
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Customer", [{"subject": "org", "level": "read"}])
    refunds = await _page(kb, admin, space, "Returns policy",
                          "Customers can return goods within thirty days for a full refund.")
    await _page(kb, admin, space, "Shipping", "The courier collects parcels at noon.")
    member = _principal(_address("m"), kb.org_a)

    embedder.calls.clear()
    answer = await _company(kb, member, "how do I get my money back")
    assert _knowledge_ids(answer)[:1] == [refunds]
    assert embedder.calls == [["how do I get my money back"]], "one embed per search"

    # The org off KNOWLEDGE_EMBED: the lexical half only, and no call.
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "knowledge_embed", kb.org_b, raising=False)
    embedder.calls.clear()
    assert _knowledge_ids(await _company(kb, member, "how do I get my money back")) == []
    assert embedder.calls == []


async def test_the_command_bar_makes_no_embed_call(
        kb, app_engine, monkeypatch,  # noqa: F811
) -> None:
    from gateway.routes.shell import search as shell_search

    _assert_non_priv(app_engine)
    embedder = _embed_on(monkeypatch, Embedder())
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Bar", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Refund window", "Thirty days for a refund.")
    member = _principal(_address("m"), kb.org_a)

    embedder.calls.clear()
    # The bar's provider for the knowledge source: the projection of KB-12.
    provider = shell_search._projected("knowledge")
    items = await _bound(kb, member, lambda: provider(member, "refund window"))
    assert [i["href"] for i in items] == [f"/knowledge/pages/{page}"]
    assert embedder.calls == [], "a keystroke in the bar cost an embed"


# ── KB-3c: each chunk once, with a member ──────────────────────────────────

async def test_two_concurrent_saves_embed_each_chunk_once(
        kb, app_engine, monkeypatch,  # noqa: F811
) -> None:
    from tests.unit.test_kb_pages_r8 import _calls

    _assert_non_priv(app_engine)
    embedder = _embed_on(monkeypatch, Embedder(delay=0.4))
    writer = _address("writer")
    admin = _principal(writer, kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Saves", [{"subject": "org", "level": "read"}])
    body = "# One\n\nalpha text.\n\n# Two\n\nbeta text.\n\n# Three\n\ngamma text."
    page = await _page(kb, admin, space, "Quick saves", body)
    embedder.calls.clear()
    embedder.members.clear()

    first = body.replace("alpha text", "alpha changed")
    second = first.replace("beta text", "beta changed")
    answers = await _calls(kb, admin, [
        ("PATCH", f"/knowledge/pages/{page}", {"body_md": first}),
        ("PATCH", f"/knowledge/pages/{page}", {"body_md": second}),
    ])
    assert [a.status_code for a in answers] == [200, 200], [a.text for a in answers]

    sent = Counter(t for call in embedder.calls for t in call)
    assert sent, "non-vacuity: the saves embedded"
    assert max(sent.values()) == 1, f"a chunk was embedded twice: {sent}"
    assert all(r.has_vector for r in _chunks(kb, page))
    # The job ran after the response, and it carried the acting member.
    assert embedder.members and set(embedder.members) == {writer}


# ── KB-3c: the sweep ────────────────────────────────────────────────────────

def _r8_member(p: Any, org: str, tag: str) -> str:
    """An active member of *org* in the identity tables, as production has."""
    email = f"{tag}.{uuid.uuid4().hex[:8]}@kb3c-r8.test"
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
            {"e": email, "o": org})
        uid = c.execute(text(
            "INSERT INTO user_identity (email, display_name) VALUES (:e, :e) "
            "RETURNING id::text"), {"e": email}).scalar()
        c.execute(text(
            "INSERT INTO org_membership (organization_id, user_id, status, joined_at) "
            "VALUES (CAST(:o AS uuid), CAST(:u AS uuid), 'active', now())"),
            {"o": org, "u": uid})
    return email


@pytest.fixture
def sweeping(kb, monkeypatch):
    """The sweep's state cleared, and the identity rows removed after."""
    from gateway.kb_index import sweep

    # The departed check reads the RLS-exempt identity tables, as production
    # does with the cutover on (my_day_loop.departed).
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    sweep._waiting.clear()
    sweep._stopping.clear()
    yield sweep
    sweep._waiting.clear()
    with kb.admin_engine.begin() as c:
        c.execute(text("DELETE FROM org_membership m USING user_identity ui "
                       "WHERE m.user_id = ui.id AND ui.email LIKE '%@kb3c-r8.test'"))
        c.execute(text("DELETE FROM user_identity WHERE email LIKE '%@kb3c-r8.test'"))
        c.execute(text("DELETE FROM app_user WHERE email LIKE '%@kb3c-r8.test'"))


def _age(p: Any, page_id: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE kb_chunks SET created_at = now() - interval '1 hour' "
                       "WHERE source_id = CAST(:p AS uuid)"), {"p": page_id})


async def test_a_page_published_before_the_flag_gets_vectors_in_one_tick(
        kb, sweeping, app_engine, monkeypatch,  # noqa: F811
) -> None:
    from acb_common import get_settings

    _assert_non_priv(app_engine)
    author = _r8_member(kb, kb.org_a, "author")
    admin = _principal(author, kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Before", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Old policy",
                       "# Leave\n\nTake leave in March.\n\n# Pay\n\nPay day is the 1st.")
    assert not any(r.has_vector for r in _chunks(kb, page)), "the flag was off"

    # The flag names org B only: the sweep never reads org A, and calls nothing.
    embedder = _embed_on(monkeypatch, Embedder())
    monkeypatch.setattr(get_settings(), "knowledge_embed", kb.org_b, raising=False)
    _age(kb, page)
    await _bound(kb, None, sweeping.run_tick)
    assert embedder.calls == [] and not any(r.has_vector for r in _chunks(kb, page))

    # The flag names org A. A fresh chunk belongs to its job, so it waits.
    monkeypatch.setattr(get_settings(), "knowledge_embed", kb.org_a, raising=False)
    with kb.admin_engine.begin() as c:
        c.execute(text("UPDATE kb_chunks SET created_at = now() "
                       "WHERE source_id = CAST(:p AS uuid)"), {"p": page})
    fresh = await _bound(kb, None, sweeping.run_tick)
    assert fresh.chunks == 0 and embedder.calls == []

    # Old enough: one tick indexes the page, as its author.
    _age(kb, page)
    done = await _bound(kb, None, sweeping.run_tick)
    assert done.written == 2 and done.no_member == 0
    assert all(r.has_vector for r in _chunks(kb, page))
    assert embedder.members == [author]

    # The paraphrase now finds it, and a second tick has nothing to do.
    member = _principal(_address("m"), kb.org_a)
    assert _found(await _search(kb, member, "when is my vacation"))[:1] == [page]
    embedder.calls.clear()
    again = await _bound(kb, None, sweeping.run_tick)
    assert again.chunks == 0 and embedder.calls == []


async def test_the_sweep_takes_at_most_max_chunks_in_a_tick(
        kb, sweeping, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    author = _r8_member(kb, kb.org_a, "author")
    admin = _principal(author, kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Bound", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Three parts",
                       "# A\n\nfirst.\n\n# B\n\nsecond.\n\n# C\n\nthird.")
    embedder = _embed_on(monkeypatch, Embedder())
    monkeypatch.setattr(sweeping, "MAX_CHUNKS", 2)
    _age(kb, page)

    first = await _bound(kb, None, sweeping.run_tick)
    assert first.chunks == 2 and first.written == 2
    second = await _bound(kb, None, sweeping.run_tick)
    assert second.chunks == 1 and second.written == 1
    assert sum(len(c) for c in embedder.calls) == 3
    assert all(r.has_vector for r in _chunks(kb, page))


async def test_the_sweep_skips_a_page_whose_members_left(
        kb, sweeping, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    gone = _address("gone")  # never a member: no app_user and no identity row
    admin = _principal(gone, kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Left", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Orphan", "Nobody pays for this page.")
    embedder = _embed_on(monkeypatch, Embedder())
    _age(kb, page)
    result = await _bound(kb, None, sweeping.run_tick)
    assert result.no_member == 1 and embedder.calls == []
    assert not any(r.has_vector for r in _chunks(kb, page))


# ── WS-61 CX-1: the review factor on the company path (company_context.md §4.2)

async def test_a_page_past_its_review_date_ranks_below_a_current_one(
        kb, app_engine, monkeypatch, roomy,  # noqa: F811
) -> None:
    """Two pages with the same text. The page that the fusion puts first gets
    a review date in the past. With ``CONTEXT_RECENCY`` on it goes second on
    ``GET /search/company``. ``/knowledge/search`` keeps today's order."""
    from acb_common import get_settings

    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Policies", [{"subject": "org", "level": "read"}])
    body = "The warranty covers the nozzle for twelve months."
    pages = [await _page(kb, admin, space, "Warranty", body) for _ in range(2)]
    member = _principal(_address("m"), kb.org_a)

    first, second = _knowledge_ids(await _company(kb, member, "warranty nozzle"))
    assert {first, second} == set(pages)
    with kb.admin_engine.begin() as c:
        c.execute(text("UPDATE kb_pages SET review_at = now() - interval '2 days' "
                       "WHERE id = CAST(:p AS uuid)"), {"p": first})

    # Off: the review date changes nothing, on either path.
    assert _knowledge_ids(await _company(kb, member, "warranty nozzle")) == [first, second]
    assert _found(await _search(kb, member, "warranty nozzle")) == [first, second]

    monkeypatch.setattr(get_settings(), "context_recency", True, raising=False)
    answer = await _company(kb, member, "warranty nozzle")
    assert _knowledge_ids(answer) == [second, first]
    assert all(h["stamp"] for h in answer["hits"] if h["app"] == "knowledge")
    # The member's own search keeps the fusion of KB-3, and its keys.
    plain = await _search(kb, member, "warranty nozzle")
    assert _found(plain) == [first, second]
    assert "review_at" not in plain["hits"][0]
