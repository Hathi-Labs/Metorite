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

The generated ``tsv``, ``websearch_to_tsquery``, the ``<=>`` operator, the
partial HNSW index, the upsert on ``content_sha`` and the join under the one
visibility clause are each SQL that a hermetic fake would accept unread.
"""

from __future__ import annotations

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
    """A stand-in for ``kb_index.embed.embed``. It counts each call."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, texts: list[str], **kw: Any) -> list[list[float] | None]:
        self.calls.append(list(texts))
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
    """Replace the HTTP client of the REAL ``embed()``: down, or zero vectors."""
    from gateway.kb_index import embed as embed_mod

    seen: list[Any] = []

    class _Client:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def post(self, url: str, json: Any, headers: Any) -> httpx.Response:
            seen.append(json)
            if error is not None:
                raise error
            data = [{"index": i, "embedding": [0.0] * DIMS if zero else fake_vector(t)}
                    for i, t in enumerate(json["input"])]
            return httpx.Response(200, json={"data": data},
                                  request=httpx.Request("POST", url))

    monkeypatch.setattr(embed_mod, "_client", _Client)
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
