"""R8 — LOOP-19: sent mail by recipient and by file name, on a REAL Postgres.

Spec: ``project-docs/specs/proactive_loop.md`` §4.9.1. Migration 496 adds
``email_messages.attachment_names`` and a trigger on ``email_attachments``.
``/email/messages`` (the chat tools' route) and ``/email/search`` take a
recipient word and a file-name word. Each test runs as the NON-privileged app
role under FORCE ROW LEVEL SECURITY, through the real route functions.

* A member finds their own sent mail by recipient, by file name, and by both.
* A second member of the same org, with the same recipient and a file of the
  same name, is never found, and never finds the first member's mail.
* A member of another org finds nothing of org A.
* The trigger keeps the names equal to the attachment rows on insert, rename
  and delete. A NULL column gives the same answer (the old-row fallback).
* ``%`` and ``_`` in the word are literal.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_search_attachment_names_r8.py -v -rs
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
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

_SEARCH: dict[str, Any] = {
    "account_id": None, "folder": "sent", "label": None, "labels": None,
    "uncategorized": False, "from_addr": None, "to_addr": None,
    "received_after": None, "received_before": None, "is_read": None,
    "is_starred": None, "has_attachments": None, "sender_category": None,
    "attachment_name": None, "importance": None, "hybrid": False, "light": False,
    "page": 1, "page_size": 25, "with_total": True, "q": None,
}
_LIST: dict[str, Any] = {
    "account_id": None, "folder": "sent", "label": None, "uncategorized": False,
    "query": None, "thread_id": None, "received_after": None,
    "received_before": None, "is_read": None, "is_starred": None,
    "has_attachments": None, "importance": None, "from_email": None,
    "to_addr": None, "attachment_name": None,
    "sender_category": None, "sort": "newest", "collapse": False,
    "page": 1, "page_size": 25,
}


def _user(email: str, org: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(["feature:email"]))


@asynccontextmanager
async def _as(w: Any, org: str) -> AsyncIterator[None]:
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(w.p.app_url.render_as_string(hide_password=False)):
            yield
    finally:
        release_tenant(token)


@pytest.fixture
def world(promoted, app_engine):  # noqa: F811 — fixtures
    _assert_non_priv(app_engine)
    p = promoted
    tag = uuid.uuid4().hex[:8]
    me, other, zed = (f"{n}-{tag}@lp19.test" for n in ("me", "other", "zed"))
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        def box(owner: str, org: str) -> str:
            return str(c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, initial_sync_done, organization_id) "
                "VALUES (:u, 'microsoft', :a, 'x', true, CAST(:o AS uuid)) RETURNING id"),
                {"u": owner, "a": f"box-{uuid.uuid4().hex[:6]}@lp19.test", "o": org}
            ).scalar_one())

        def sent(account: str, org: str, to: str, subject: str, files: list[str]) -> str:
            mid = str(c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, folder, "
                "from_address, to_addresses, subject, body_text, snippet, received_at, "
                "has_attachments, organization_id) VALUES (CAST(:a AS uuid), :pid, 'sent', "
                "'{\"name\": \"Me\", \"email\": \"me@lp19.test\"}'::jsonb, "
                "jsonb_build_array(jsonb_build_object('name', CAST(:n AS text), 'email', "
                "CAST(:t AS text))), :s, 'Please sign.', 'Please sign.', now(), "
                "CAST(:h AS boolean), CAST(:o AS uuid)) RETURNING id"),
                {"a": account, "pid": f"lp19-{uuid.uuid4().hex[:8]}", "t": to,
                 "n": "Mahesh Rao" if to.startswith("mahesh") else "Ria Shah", "s": subject,
                 "h": bool(files), "o": org}).scalar_one())
            for f in files:
                c.execute(text(
                    "INSERT INTO email_attachments (message_id, filename, provider_attachment_id, "
                    "organization_id) VALUES (CAST(:m AS uuid), :f, :p, CAST(:o AS uuid))"),
                    {"m": mid, "f": f, "p": uuid.uuid4().hex[:8], "o": org})
            return mid

        a_me, a_other, a_zed = box(me, p.org_a), box(other, p.org_a), box(zed, p.org_b)
        ids["nda"] = sent(a_me, p.org_a, "mahesh@vendor.test", "NDA for signature",
                          ["NDA_Acme_v2.pdf", "cover.txt"])
        ids["plain"] = sent(a_me, p.org_a, "mahesh@vendor.test", "Thanks", [])
        ids["pct"] = sent(a_me, p.org_a, "ria@vendor.test", "Rates", ["rates_100%.xlsx"])
        ids["other_nda"] = sent(a_other, p.org_a, "mahesh@vendor.test", "Other NDA",
                                ["NDA_Other.pdf"])
        ids["zed_nda"] = sent(a_zed, p.org_b, "mahesh@vendor.test", "Zed NDA", ["NDA_Zed.pdf"])
    try:
        yield SimpleNamespace(p=p, me=me, other=other, zed=zed, ids=ids)
    finally:
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM email_attachments WHERE message_id = "
                           "ANY(CAST(:i AS uuid[]))"), {"i": list(ids.values())})
            c.execute(text("DELETE FROM email_messages WHERE id = ANY(CAST(:i AS uuid[]))"),
                      {"i": list(ids.values())})
            c.execute(text("DELETE FROM email_accounts WHERE user_id = ANY(:u)"),
                      {"u": [me, other, zed]})


async def _found(w: Any, who: str, org: str, **kw: Any) -> dict[str, set[str]]:
    """The ids that the list route and the search route give, for one member."""
    from gateway.routes.email.transport import messages as messages_mod
    from gateway.routes.email.transport import search as search_mod

    async with _as(w, org):
        listed = await messages_mod.list_messages(user=_user(who, org), **{**_LIST, **kw})
        searched = await search_mod.search_messages(
            user=_user(who, org),
            **{**_SEARCH, **{("to_addr" if k == "to_addr" else k): v for k, v in kw.items()
                              if k in ("to_addr", "attachment_name", "folder")}})
    return {"list": {str(e["id"]) for e in listed["emails"]},
            "search": {str(e["id"]) for e in searched["emails"]}}


def _both(got: dict[str, set[str]]) -> set[str]:
    assert got["list"] == got["search"], got
    return got["list"]


def test_the_migration_is_found_by_content() -> None:
    from tests.unit._tenant_ladder import ladder

    texts = {f: Path(f).read_text(encoding="utf-8") for f in ladder()}
    found = [f for f, body in texts.items() if "email_attachment_names_fill" in body]
    assert len(found) == 1, found
    body = texts[found[0]]
    assert "ADD COLUMN IF NOT EXISTS attachment_names text;" in body  # nullable, no default
    assert "GENERATED ALWAYS" not in body and "attachment_names text NOT NULL" not in body
    assert "attachment_names text DEFAULT" not in body


@pytest.mark.asyncio
async def test_r8_a_member_finds_their_own_sent_mail_by_recipient(world) -> None:
    w = world
    got = _both(await _found(w, w.me, w.p.org_a, to_addr="mahesh"))
    assert got == {w.ids["nda"], w.ids["plain"]}
    assert _both(await _found(w, w.me, w.p.org_a, to_addr="Mahesh Rao")) == got


@pytest.mark.asyncio
async def test_r8_a_member_finds_their_own_sent_mail_by_file_name(world) -> None:
    w = world
    assert _both(await _found(w, w.me, w.p.org_a, attachment_name="nda")) == {w.ids["nda"]}
    assert _both(await _found(w, w.me, w.p.org_a, attachment_name="COVER")) == {w.ids["nda"]}
    assert _both(await _found(
        w, w.me, w.p.org_a, to_addr="mahesh", attachment_name="nda")) == {w.ids["nda"]}
    assert _both(await _found(
        w, w.me, w.p.org_a, to_addr="ria", attachment_name="nda")) == set()


@pytest.mark.asyncio
async def test_r8_never_the_mail_of_another_member_or_another_org(world) -> None:
    w = world
    mine = _both(await _found(w, w.me, w.p.org_a, attachment_name="nda"))
    assert w.ids["other_nda"] not in mine and w.ids["zed_nda"] not in mine
    theirs = _both(await _found(w, w.other, w.p.org_a, attachment_name="nda"))
    assert theirs == {w.ids["other_nda"]}
    zed = _both(await _found(w, w.zed, w.p.org_b, attachment_name="nda", to_addr="mahesh"))
    assert zed == {w.ids["zed_nda"]}
    # Zed asking under the org A tenant (the wrong one) gets nothing of org A.
    assert _both(await _found(w, w.zed, w.p.org_a, attachment_name="nda")) == set()


def _stored(w: Any, mid: str) -> str | None:
    with w.p.admin_engine.connect() as c:
        return c.execute(text("SELECT attachment_names FROM email_messages "
                              "WHERE id = CAST(:m AS uuid)"), {"m": mid}).scalar_one()


@pytest.mark.asyncio
async def test_r8_the_trigger_follows_insert_rename_move_and_delete(world) -> None:
    """Each step reads the STORED column, so a trigger that never writes fails
    here. The search alone would not: its fallback gives the same ids."""
    w = world
    p, mid, other_mid = w.p, w.ids["plain"], w.ids["pct"]
    assert _both(await _found(w, w.me, p.org_a, attachment_name="contract")) == set()
    assert _stored(w, mid) is None
    with p.admin_engine.begin() as c:
        c.execute(text("INSERT INTO email_attachments (message_id, filename, "
                       "provider_attachment_id, organization_id) VALUES "
                       "(CAST(:m AS uuid), 'Contract.docx', 'x1', CAST(:o AS uuid))"),
                  {"m": mid, "o": p.org_a})
    assert _stored(w, mid) == "Contract.docx"
    assert _both(await _found(w, w.me, p.org_a, attachment_name="contract")) == {mid}
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE email_attachments SET filename = 'Lease.docx' "
                       "WHERE message_id = CAST(:m AS uuid)"), {"m": mid})
    assert _stored(w, mid) == "Lease.docx"
    assert _both(await _found(w, w.me, p.org_a, attachment_name="contract")) == set()
    assert _both(await _found(w, w.me, p.org_a, attachment_name="lease")) == {mid}
    # A move to another message refreshes the old message and the new one.
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE email_attachments SET message_id = CAST(:n AS uuid) "
                       "WHERE message_id = CAST(:m AS uuid)"), {"m": mid, "n": other_mid})
    assert _stored(w, mid) is None
    assert _stored(w, other_mid) == "Lease.docx rates_100%.xlsx"
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM email_attachments WHERE message_id = CAST(:m AS uuid)"),
                  {"m": other_mid})
    assert _stored(w, other_mid) is None
    assert _both(await _found(w, w.me, p.org_a, attachment_name="lease")) == set()


@pytest.mark.asyncio
async def test_r8_a_null_column_gives_the_same_answer(world) -> None:
    w = world
    before = _both(await _found(w, w.me, w.p.org_a, attachment_name="nda"))
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE email_messages SET attachment_names = NULL "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": w.p.org_a})
    assert _both(await _found(w, w.me, w.p.org_a, attachment_name="nda")) == before == {w.ids["nda"]}


@pytest.mark.asyncio
async def test_r8_a_percent_in_the_word_is_literal(world) -> None:
    w = world
    assert _both(await _found(w, w.me, w.p.org_a, attachment_name="100%")) == {w.ids["pct"]}
    assert _both(await _found(w, w.me, w.p.org_a, attachment_name="%")) == {w.ids["pct"]}
    assert _both(await _found(w, w.me, w.p.org_a, attachment_name="n_a")) == set()
