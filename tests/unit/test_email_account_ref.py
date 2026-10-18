"""The ``account_id`` of an email route: one reader, four shapes.

Defect (production gateway log, October 2026): ``GET /email/messages``
answered 500 twenty-eight times. The chat agent sent ``account_id=all``,
``__all__``, ``ALL``, ``placeholder`` and a mailbox address, and asyncpg
refused each one as a UUID inside the query.

R7 fences named here:

* ``email-account-ref-shapes``: ``account_ref.resolve_account_filter`` reads
  "all" (any case) and empty as no filter, an address as a mailbox of the
  member, a UUID as itself, and anything else as 422. No shape reaches the
  SQL as a bad UUID.
* ``email-account-ref-every-route``: each email route that takes an
  ``account_id`` query or path value calls the helper. A new route that
  binds the raw value fails ``test_every_account_id_route_calls_the_helper``.
* ``email-account-ref-scope`` (R8): an address resolves only among the
  mailboxes of the acting member in the bound tenant. The same address of
  another member, or of the same member in another tenant, is a 404.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_account_ref.py -v -rs
"""
from __future__ import annotations

import ast
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from gateway.routes import email as m
from gateway.routes.email import account_ref

from tests.unit._email_fakes import bind_db

_OWN_ID = "3f1c2d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
_OWN_ADDRESS = "mahesh@shaurryatele.example"


class _Db:
    """A session that answers the address read and records each statement."""

    def __init__(self, ids_by_address: dict[str, list[str]] | None = None):
        self.ids_by_address = {k.lower(): v for k, v in (ids_by_address or {}).items()}
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        sql, params = str(stmt), dict(params or {})
        self.statements.append((sql, params))
        r = MagicMock()
        if "FROM email_accounts" in sql and "LOWER(email_address)" in sql:
            ids = self.ids_by_address.get(params["addr"].lower(), [])
            r.fetchall.return_value = [SimpleNamespace(id=i) for i in ids]
            return r
        r.scalar.return_value = 0
        r.fetchall.return_value = []
        r.fetchone.return_value = None
        return r


# ── The helper ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [None, "", "  ", "all", "ALL", "All", "__all__",
                                 "__ALL__", "*", "any", "ANY"])
async def test_all_and_empty_mean_no_filter(raw):
    db = _Db()
    assert await account_ref.resolve_account_filter(db, raw, "u@x.example") is None
    assert db.statements == []


@pytest.mark.parametrize("raw", ["all", "", None])
async def test_a_route_that_needs_one_mailbox_refuses_all(raw):
    with pytest.raises(HTTPException) as exc:
        await account_ref.resolve_account_filter(
            _Db(), raw, "u@x.example", required=True)
    assert exc.value.status_code == 422
    assert "needs one mailbox" in exc.value.detail


@pytest.mark.parametrize("raw", [
    _OWN_ID, f" {_OWN_ID.upper()} ", _OWN_ID.replace("-", ""),
    "{" + _OWN_ID + "}", "urn:uuid:" + _OWN_ID,
])
async def test_a_uuid_goes_on_in_canonical_form(raw):
    """asyncpg takes 32 to 36 characters only, and ``uuid.UUID`` also reads
    the braced and the urn forms. So the reader gives the canonical text."""
    db = _Db()
    assert await account_ref.resolve_account_filter(db, raw, "u@x.example") == _OWN_ID
    assert db.statements == []


@pytest.mark.parametrize("raw", ["placeholder", "ALL-accounts", "123", "acc-1",
                                 "3f1c2d4e-5a6b"])
async def test_garbage_is_a_422_that_names_the_three_shapes(raw):
    db = _Db()
    with pytest.raises(HTTPException) as exc:
        await account_ref.resolve_account_filter(db, raw, "u@x.example")
    assert exc.value.status_code == 422
    assert exc.value.detail == account_ref.BAD_ACCOUNT_ID_DETAIL
    assert "list_accounts" in exc.value.detail and '"all"' in exc.value.detail
    assert db.statements == []


async def test_the_storage_routes_keep_their_404_for_garbage():
    with pytest.raises(HTTPException) as exc:
        await account_ref.resolve_account_filter(
            _Db(), "acc-1", "u@x.example", required=True, bad_is_not_found=True)
    assert (exc.value.status_code, exc.value.detail) == (404, "Account not found")


async def test_an_own_address_resolves_to_its_id_in_any_case():
    db = _Db({_OWN_ADDRESS: [_OWN_ID]})
    got = await account_ref.resolve_account_filter(
        db, _OWN_ADDRESS.upper(), "u@x.example")
    assert got == _OWN_ID
    [(sql, params)] = db.statements
    assert "user_id = :uid" in sql
    assert "organization_id = " in sql and "app.tenant_id" in sql
    assert params["uid"] == "u@x.example"


async def test_an_address_of_no_own_mailbox_is_a_404():
    with pytest.raises(HTTPException) as exc:
        await account_ref.resolve_account_filter(
            _Db(), "someone@else.example", "u@x.example")
    assert exc.value.status_code == 404
    # No address in the text: the tool layer removes one (safe_detail).
    assert exc.value.detail == account_ref.NO_SUCH_ADDRESS_DETAIL
    assert "@" not in exc.value.detail and "list_accounts" in exc.value.detail


async def test_an_address_of_two_own_mailboxes_asks_for_the_id():
    db = _Db({_OWN_ADDRESS: [_OWN_ID, str(uuid.uuid4())]})
    with pytest.raises(HTTPException) as exc:
        await account_ref.resolve_account_filter(db, _OWN_ADDRESS, "u@x.example")
    assert exc.value.status_code == 422


async def test_no_session_opens_one_only_for_an_address():
    db = _Db({_OWN_ADDRESS: [_OWN_ID]})
    with patch.object(account_ref, "_tenant_session", bind_db(db)):
        assert await account_ref.resolve_account_filter(
            None, _OWN_ADDRESS, "u@x.example") == _OWN_ID
        assert await account_ref.resolve_account_filter(
            None, "all", "u@x.example") is None
    assert len(db.statements) == 1


# ── The message list (the route of the log) ─────────────────────────────────

_OFF = {
    "folder": "inbox", "label": None, "uncategorized": False, "query": None,
    "thread_id": None, "received_after": None, "received_before": None,
    "is_read": None, "is_starred": None, "has_attachments": None,
    "importance": None, "from_email": None, "sender_category": None,
    "sort": "newest", "collapse": False, "page": 1, "page_size": 50,
}


async def _list(account_id, db=None):
    db = db or _Db({_OWN_ADDRESS: [_OWN_ID]})
    user = SimpleNamespace(email="u@x.example")
    with patch.object(m.transport.messages, "_tenant_session", bind_db(db)):
        out = await m.list_messages(account_id=account_id, user=user, **_OFF)
    return out, db


def _bound_account_ids(db: _Db) -> set[str]:
    return {p["account_id"] for _, p in db.statements if "account_id" in p}


@pytest.mark.parametrize("raw", ["all", "ALL", "__all__"])
async def test_the_list_reads_all_inboxes_for_all(raw):
    out, db = await _list(raw)
    assert out["total"] == 0
    assert _bound_account_ids(db) == set()
    assert any("in_all_inboxes" in sql for sql, _ in db.statements)


async def test_the_list_reads_the_mailbox_of_an_own_address():
    _, db = await _list(_OWN_ADDRESS)
    assert _bound_account_ids(db) == {_OWN_ID}


async def test_the_list_answers_404_for_a_foreign_address_and_reads_no_mail():
    db = _Db({})  # the address is a mailbox of another member, or tenant
    with pytest.raises(HTTPException) as exc:
        await _list("mallory@other.example", db)
    assert exc.value.status_code == 404
    assert not any("email_messages" in sql for sql, _ in db.statements)


async def test_the_list_answers_422_for_garbage_before_any_sql():
    db = _Db()
    with pytest.raises(HTTPException) as exc:
        await _list("placeholder", db)
    assert exc.value.status_code == 422
    assert db.statements == []


async def test_the_list_keeps_a_real_uuid():
    _, db = await _list(_OWN_ID)
    assert _bound_account_ids(db) == {_OWN_ID}


async def test_the_priority_route_refuses_all_with_a_422():
    with (pytest.raises(HTTPException) as exc,
          patch.object(m.transport.messages, "_tenant_session", bind_db(_Db()))):
        await m.priority_inbox(
            account_id="all", days=30, limit=20,
            user=SimpleNamespace(email="u@x.example"))
    assert exc.value.status_code == 422


# ── Every route: the helper, or a test failure ──────────────────────────────

_EMAIL_ROUTES = (Path(__file__).resolve().parents[2]
                 / "apps/services/gateway/gateway/routes/email")


def _routes_with_account_id() -> list[tuple[str, str, bool]]:
    """``(file, function, calls_the_helper)`` for each route with an
    ``account_id`` query or path parameter."""
    out = []
    for path in sorted(_EMAIL_ROUTES.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if not any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                       and getattr(d.func.value, "id", "") == "router"
                       for d in node.decorator_list):
                continue
            params = node.args.args + node.args.kwonlyargs
            if not any(a.arg == "account_id" for a in params):
                continue
            body = ast.get_source_segment(src, node) or ""
            out.append((path.name, node.name, "resolve_account_filter(" in body))
    return out


def test_every_account_id_route_calls_the_helper():
    routes = _routes_with_account_id()
    assert len(routes) >= 40, routes  # the walk found the routes
    missing = [f"{f}:{fn}" for f, fn, ok in routes if not ok]
    assert not missing, (
        "These email routes take account_id and do not read it through "
        f"account_ref.resolve_account_filter: {missing}")


# ── R8: the address read on a real database, under FORCE RLS ────────────────

try:
    from acb_common.db import bind_tenant, release_tenant
    from sqlalchemy import text

    from tests.unit._tenant_ladder import tenant_engine_scope
    from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
        _DB_GATE,
        app_engine,
        promoted,
    )
except Exception:  # pragma: no cover - the DB stack is not installed
    _DB_GATE = pytest.mark.skip(reason="the database stack is not installed")


def _account(admin_engine, *, org: str, owner: str, address: str) -> str:
    with admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'x', CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "m": address, "o": org}).scalar_one())


def _mail(admin_engine, *, org: str, account_id: str) -> None:
    with admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, folder, "
            "from_address, to_addresses, subject, received_at, organization_id) "
            "VALUES (CAST(:a AS uuid), :p, 'inbox', "
            "'{\"email\": \"ravi@contoso.test\"}'::jsonb, '[]'::jsonb, 's', "
            "now(), CAST(:o AS uuid))"),
            {"a": account_id, "p": f"pm-{uuid.uuid4().hex[:10]}", "o": org})


def _purge(admin_engine, *account_ids: str) -> None:
    with admin_engine.begin() as c:
        for a in account_ids:
            c.execute(text("DELETE FROM email_messages WHERE account_id = CAST(:a AS uuid)"),
                      {"a": a})
            c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                      {"a": a})


@_DB_GATE
class TestTheAddressReadOnARealDatabase:

    async def test_an_address_resolves_only_among_own_mailboxes_of_this_tenant(
        self, promoted, app_engine,
    ):
        p = promoted
        tag = uuid.uuid4().hex[:8]
        owner = f"owner-{tag}@acct-ref.test"
        mine = f"Mine-{tag}@acct-ref.test"
        theirs = f"theirs-{tag}@acct-ref.test"
        elsewhere = f"elsewhere-{tag}@acct-ref.test"
        own_box = _account(p.admin_engine, org=p.org_b, owner=owner, address=mine)
        # Another member of the same tenant: the address is theirs alone.
        their_box = _account(p.admin_engine, org=p.org_b,
                             owner=f"mallory-{tag}@acct-ref.test", address=theirs)
        # The same member in another tenant: RLS and the tenant predicate
        # keep it out of a read bound to org_b.
        far_box = _account(p.admin_engine, org=p.org_a, owner=owner, address=elsewhere)
        _mail(p.admin_engine, org=p.org_b, account_id=own_box)
        _mail(p.admin_engine, org=p.org_b, account_id=their_box)
        _mail(p.admin_engine, org=p.org_a, account_id=far_box)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                got = await account_ref.resolve_account_filter(
                    None, mine.lower(), owner)
                assert got == own_box
                for foreign in (theirs, elsewhere):
                    with pytest.raises(HTTPException) as exc:
                        await account_ref.resolve_account_filter(None, foreign, owner)
                    assert exc.value.status_code == 404

                me = SimpleNamespace(email=owner, organization_id=p.org_b)
                # Only the own mail, by address and by "ALL".
                for ref in (mine, "ALL"):
                    out = await m.list_messages(account_id=ref, user=me, **_OFF)
                    assert out["total"] == 1, ref
                    assert {e["account_id"] for e in out["emails"]} == {own_box}
        finally:
            release_tenant(token)
            _purge(p.admin_engine, own_box, their_box, far_box)
