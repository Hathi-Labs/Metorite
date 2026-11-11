"""R8 — task 2975, email search that finds prefixes and broadens, on a REAL Postgres.

Each test runs the route as the non-privileged role of the phase-4 rehearsal
(NOSUPERUSER, NOBYPASSRLS) under FORCE RLS.

* (1) "Aroh" finds a mail that holds only the token ``aroh.si``. A query with
  an operator keeps its old meaning.
* (2) Hostile text raises no tsquery syntax error.
* (3) Two words that no one mail holds: page 1 searches again with ANY word and
  answers ``broadened: true``. ``broaden=False`` turns it off.
* (7) A mail that matched before keeps its rank.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_keep_separate import _assert_non_priv
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_EXPR = (
    "to_tsvector('english', coalesce(subject,'') || ' ' || coalesce(body_text,'')"
    " || ' ' || coalesce(from_address->>'name','') || ' ' ||"
    " coalesce(from_address->>'email',''))"
)
OWNER = "bro2975-owner@bro2975.test"

_SEARCH: dict[str, Any] = {
    "account_id": None, "folder": "all", "label": None, "labels": None,
    "uncategorized": False, "from_addr": None, "to_addr": None,
    "received_after": None, "received_before": None, "is_read": None,
    "is_starred": None, "has_attachments": None, "sender_category": None,
    "importance": None, "hybrid": False, "light": True, "page": 1,
    "page_size": 25, "with_total": True, "broaden": True,
}

# (subject, body) of the seeded mails of org A.
_MAILS = [
    ("Aroh.si domain access", "please grant the access"),
    ("Printer order", "the printer arrived"),
    ("Invoice for resin", "payment due"),
    ("Kettle", "unrelated note"),
]


def _member(org: str) -> UserContext:
    return UserContext(email=OWNER, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(["feature:email"]))


@asynccontextmanager
async def _as_member(p: Any, org: str) -> AsyncIterator[None]:
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            yield
    finally:
        release_tenant(token)


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    p = promoted
    with p.admin_engine.begin() as c:
        for org in (p.org_a, p.org_b):
            box = str(c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, organization_id) VALUES (:u, 'microsoft', "
                ":m, 'x', CAST(:o AS uuid)) RETURNING id"),
                {"u": OWNER, "m": f"box-{org[:8]}@bro2975.test", "o": org}).scalar_one())
            for i, (subject, body) in enumerate(_MAILS):
                c.execute(text(
                    "INSERT INTO email_messages (account_id, provider_message_id, "
                    "folder, from_address, to_addresses, subject, body_text, snippet, "
                    "received_at, organization_id) VALUES (CAST(:a AS uuid), :pm, "
                    "'inbox', '{\"name\": \"Zed\", \"email\": \"zed@x.test\"}'::jsonb, "
                    "'[]'::jsonb, :s, :b, :b, now() - make_interval(mins => :i), "
                    "CAST(:o AS uuid))"),
                    {"a": box, "pm": f"bro2975-{org[:4]}-{i}", "s": subject, "b": body,
                     "i": i, "o": org})
    return p


async def _search(p: Any, q: str, **over: Any) -> dict[str, Any]:
    from gateway.routes.email.transport import search as search_mod

    async with _as_member(p, p.org_a):
        return await search_mod.search_messages(
            user=_member(p.org_a), q=q, **{**_SEARCH, **over})


def _subjects(got: dict[str, Any]) -> list[str]:
    return [e["subject"] for e in got["emails"]]


async def test_a_prefix_finds_a_dotted_token_and_ranks_and_highlights(seeded, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    # Proof of the fault: the plain websearch query misses the token.
    with seeded.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT to_tsvector('english','Aroh.si domain access') "
            "@@ websearch_to_tsquery('english','Aroh')")).scalar_one() is False
    got = await _search(seeded, "Aroh")
    assert _subjects(got) == ["Aroh.si domain access"]
    assert got["total"] == 1 and got["broadened"] is False
    assert got["emails"][0]["rank"] > 0
    assert "<mark>" in got["emails"][0]["highlight"]
    # An exact match keeps matching, with AND kept between two words.
    both = await _search(seeded, "printer order")
    assert _subjects(both) == ["Printer order"] and both["broadened"] is False
    # A prefix of one word AND a prefix of another.
    both = await _search(seeded, "arohs doma")
    assert _subjects(both) == ["Aroh.si domain access"]


async def test_operators_keep_their_meaning(seeded, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    quoted = await _search(seeded, '"printer order"')
    assert _subjects(quoted) == ["Printer order"]
    either = await _search(seeded, "kettle or resin")
    assert sorted(_subjects(either)) == ["Invoice for resin", "Kettle"]
    excl = await _search(seeded, "printer -arrived")
    assert _subjects(excl) == [] and excl["broadened"] is False
    # An operator query never broadens, even with no hit.
    none = await _search(seeded, '"kettle resin"')
    assert none["emails"] == [] and none["broadened"] is False


async def test_hostile_text_raises_no_syntax_error(seeded, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    for q in ("a:* | b", "foo' & (bar", "!!", "::", r"x\y <-> z", "a&b|c:d)", "(((", "'", "ünï ☃ test"):
        got = await _search(seeded, q)
        assert isinstance(got["emails"], list), q


async def test_two_words_that_no_mail_holds_broaden_to_any(seeded, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    got = await _search(seeded, "printer resin")
    assert got["broadened"] is True
    assert sorted(_subjects(got)) == ["Invoice for resin", "Printer order"]
    assert got["total"] == 2
    # Off: the answer is empty and not broadened.
    off = await _search(seeded, "printer resin", broaden=False)
    assert off["emails"] == [] and off["broadened"] is False
    # One word never broadens, and a hit on page 1 never broadens.
    assert (await _search(seeded, "zzzqqq"))["broadened"] is False
    assert (await _search(seeded, "printer arrived"))["broadened"] is False
    # Page 2 never broadens.
    assert (await _search(seeded, "printer resin", page=2))["broadened"] is False
    # Org B never leaks in (FORCE RLS as the app role).
    async with _as_member(seeded, seeded.org_b):
        from gateway.routes.email.transport import search as search_mod
        own = await search_mod.search_messages(
            user=_member(seeded.org_b), q="printer resin", **_SEARCH)
    assert len(own["emails"]) == 2 and own["broadened"] is True
    mine_b = f"bro2975-{seeded.org_b[:4]}-"
    assert all(e["provider_message_id"].startswith(mine_b) for e in own["emails"])
    assert not any(e["provider_message_id"].startswith(mine_b) for e in got["emails"])


async def test_a_mail_that_matched_before_keeps_its_rank(seeded, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    got = await _search(seeded, "printer")
    mine = {e["id"]: e["rank"] for e in got["emails"]}
    with seeded.admin_engine.connect() as c:
        old = {str(r[0]): float(r[1]) for r in c.execute(text(
            "SELECT id, ts_rank_cd(COALESCE(fts, " + _EXPR + "), "
            "websearch_to_tsquery('english', 'printer')) FROM email_messages "
            "WHERE organization_id = CAST(:o AS uuid) AND subject = 'Printer order'"),
            {"o": seeded.org_a})}
    assert mine and {k: round(v, 6) for k, v in mine.items()} == \
        {k: round(v, 6) for k, v in old.items()}


def test_the_index_plan_of_the_prefix_query(seeded) -> None:  # noqa: F811
    """The plan, printed for the report. The stored column is tested first."""
    with seeded.admin_engine.connect() as c:
        plan = "\n".join(r[0] for r in c.execute(text(
            "EXPLAIN SELECT id FROM email_messages em WHERE "
            "COALESCE(em.fts, " + _EXPR + ") @@ "
            "(websearch_to_tsquery('english', 'aroh') || to_tsquery('english', 'aroh:*'))")))
    print(plan)
    assert "fts" in plan
