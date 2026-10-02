"""EM-T4f — a disconnect stops the sync first, then deletes, then drops Graph.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.6, EM-T4f.

Production, 2026-10-02. A member clicked Disconnect during the first sync of
an Outlook mailbox. ``delete_account`` ran the ``DELETE`` and then waited for
the sync task INSIDE the same block. The task waited on a lock of that
``DELETE``. Postgres cannot see a cycle with one half in the app, so only the
statement timeout of 2 minutes broke it. Nothing deleted the Graph
subscription, so Microsoft kept posting for the deleted mailbox.

R7 fence named here: ``email-disconnect-order``.

**Hermetic.** A watched ``_tenant_session`` counts the open blocks, and every
fake records that count when it runs.

* a. ``remove_account_sync`` runs with no block open, after the ownership read
  and before the ``DELETE``. A companion test proves the order check can fail.
* b. A member who does not own the mailbox gets 404, and no loop stops.
* c. A Microsoft row with a subscription id gets ``delete_subscription`` with
  that id. A Graph call that raises, or that is slow, still gives 204. No log
  line holds a token.
* d. A row with no subscription id, or a row of another provider, builds no
  provider and makes no Graph call.

**R8.** The real SQL against the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, as the role ``acb_app_h3rls``
(NOSUPERUSER, NOBYPASSRLS). The Graph call is a fake.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_disconnect_order.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import acb_llm.key_store as key_store_mod
import email_ingestion.scheduler as sched
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes.email.transport import accounts
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are fixtures, used by name, so the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

#: A value that must never reach a log line.
TOKEN = "tok-em-t4f-must-not-leak"
OWNER = "owner@em-t4f.test"
OTHER = "other@em-t4f.test"


# ── hermetic doubles ─────────────────────────────────────────────────────────


class _Ledger:
    """The ordered record of what ran, and how many blocks were open."""

    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []
        self.open = 0
        self.logs: list[tuple[str, str, dict[str, Any]]] = []

    def kinds(self) -> list[str]:
        return [e[0] for e in self.events]

    def first(self, kind: str) -> tuple[Any, ...]:
        return next(e for e in self.events if e[0] == kind)

    def index(self, kind: str) -> int:
        return self.kinds().index(kind)


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _FakeDB:
    """Answers the three statements of ``delete_account`` from a dict."""

    def __init__(self, ledger: _Ledger, rows: dict[str, dict[str, Any]]) -> None:
        self.ledger = ledger
        self.rows = rows

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None):
        sql = " ".join(str(stmt).split())
        p = params or {}
        if sql.startswith("SELECT 1 FROM email_accounts"):
            self.ledger.events.append(("read", self.ledger.open))
            row = self.rows.get(p["id"])
            owned = row is not None and row["user_id"] == p["uid"]
            return _Result(SimpleNamespace() if owned else None)
        if sql.startswith("DELETE FROM email_accounts"):
            self.ledger.events.append(("delete", self.ledger.open))
            row = self.rows.get(p["id"])
            if row is None or row["user_id"] != p["user_id"]:
                return _Result(None)
            del self.rows[p["id"]]
            return _Result(SimpleNamespace(
                is_default=row["is_default"],
                provider=row["provider"],
                credentials_encrypted=row["creds"],
                webhook_subscription_id=row["sub"],
            ))
        if sql.startswith("UPDATE email_accounts SET is_default = true"):
            self.ledger.events.append(("reelect", self.ledger.open))
            return _Result(None)
        raise AssertionError(f"unexpected SQL: {sql[:80]}")


def _watched_session(ledger: _Ledger, db: _FakeDB):
    """A ``_tenant_session`` double that counts the open blocks."""

    @asynccontextmanager
    async def _tenant_session(*args: Any):
        # The route takes the ambient tenant. It names no organization.
        assert not args, "delete_account named a tenant: it must use the ambient one"
        ledger.open += 1
        ledger.events.append(("open", ledger.open))
        try:
            yield db
        finally:
            ledger.open -= 1
            ledger.events.append(("close", ledger.open))

    return _tenant_session


class _FakeGraph:
    def __init__(self, ledger: _Ledger, behaviour: str) -> None:
        self.ledger = ledger
        self.behaviour = behaviour

    async def delete_subscription(self, subscription_id: str) -> None:
        self.ledger.events.append(("graph_delete", self.ledger.open, subscription_id))
        if self.behaviour == "raise":
            raise RuntimeError(f"Graph refused, bearer {TOKEN}")
        if self.behaviour == "slow":
            await asyncio.sleep(30)


class _RecordingLog:
    def __init__(self, ledger: _Ledger) -> None:
        self.ledger = ledger

    def info(self, event: str, **kw: Any) -> None:
        self.ledger.logs.append(("info", event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.ledger.logs.append(("warning", event, kw))


def _row(*, user_id: str = OWNER, provider: str = "microsoft",
         sub: str | None = "sub-em-t4f-1", is_default: bool = True) -> dict[str, Any]:
    return {"user_id": user_id, "provider": provider, "sub": sub,
            "is_default": is_default, "creds": "blob-em-t4f"}


@pytest.fixture()
def wired(monkeypatch):
    """Wire the doubles into ``accounts``, and return a setup function."""

    def _setup(rows: dict[str, dict[str, Any]], *, graph: str = "ok",
               stop_raises: bool = False, decrypt_raises: bool = False):
        ledger = _Ledger()
        db = _FakeDB(ledger, rows)
        monkeypatch.setattr(accounts, "_tenant_session", _watched_session(ledger, db))
        monkeypatch.setattr(accounts, "_log", _RecordingLog(ledger))

        async def _remove(account_id: str) -> None:
            ledger.events.append(("stop", ledger.open, account_id))
            if stop_raises:
                raise RuntimeError("the scheduler lock is broken")

        monkeypatch.setattr(sched, "remove_account_sync", _remove)

        def _decrypt(blob: str) -> str:
            ledger.events.append(("decrypt", ledger.open, blob))
            if decrypt_raises:
                raise ValueError("bad blob")
            return json.dumps({"access_token": TOKEN, "refresh_token": TOKEN})

        monkeypatch.setattr(
            key_store_mod, "get_key_store", lambda: SimpleNamespace(decrypt=_decrypt))

        fake_graph = _FakeGraph(ledger, graph)

        def _build(name: str, creds: dict[str, Any]) -> _FakeGraph:
            ledger.events.append(("build", ledger.open, name, creds))
            return fake_graph

        monkeypatch.setattr(accounts, "_instantiate_provider", _build)
        return ledger

    return _setup


def _user(email: str = OWNER) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       organization_id=str(uuid.uuid4()))


def _order_violations(events: list[tuple[Any, ...]]) -> list[str]:
    """What is wrong with the order of a disconnect. Empty means correct."""
    kinds = [e[0] for e in events]
    problems: list[str] = []
    if "stop" not in kinds:
        return ["the sync loop never stopped"]
    stop = kinds.index("stop")
    if events[stop][1] != 0:
        problems.append(f"the loop stopped with {events[stop][1]} block(s) open")
    if "read" not in kinds or kinds.index("read") > stop:
        problems.append("the loop stopped before the ownership read")
    if "delete" in kinds and kinds.index("delete") < stop:
        problems.append("the DELETE ran before the loop stopped")
    return problems


# ── hermetic: a. the order ───────────────────────────────────────────────────


class TestTheLoopStopsFirstWithNoSessionOpen:

    async def test_the_loop_stops_after_the_read_and_before_the_delete(self, wired):
        ledger = wired({"acc-1": _row()})
        result = await accounts.delete_account("acc-1", user=_user())
        assert result is None
        assert _order_violations(ledger.events) == []
        assert ledger.first("stop") == ("stop", 0, "acc-1")
        assert ledger.first("delete")[1] == 1, "the DELETE ran outside a block"

    async def test_the_order_check_fails_on_the_old_shape(self):
        # The shape before EM-T4f: DELETE, then the stop, in ONE block.
        old = [("open", 1), ("delete", 1), ("reelect", 1), ("stop", 1, "a"),
               ("close", 0)]
        problems = _order_violations(old)
        assert any("block(s) open" in p for p in problems)
        assert any("DELETE ran before" in p for p in problems)

    async def test_each_phase_is_its_own_block(self, wired):
        ledger = wired({"acc-1": _row()})
        await accounts.delete_account("acc-1", user=_user())
        opens = [e for e in ledger.events if e[0] == "open"]
        assert len(opens) == 2, "expected one block to read and one to delete"
        assert all(e[1] == 1 for e in opens), "a block opened inside another"

    async def test_a_failed_stop_still_deletes_and_is_logged(self, wired):
        rows = {"acc-1": _row()}
        ledger = wired(rows, stop_raises=True)
        await accounts.delete_account("acc-1", user=_user())
        assert "acc-1" not in rows
        assert ("warning", "email.disconnect.stop_sync_failed",
                {"account_id": "acc-1", "error": "RuntimeError"}) in ledger.logs

    def test_the_route_answers_204(self):
        routes = [r for r in accounts.router.routes
                  if getattr(r, "path", "") == "/email/accounts/{account_id}"
                  and "DELETE" in getattr(r, "methods", set())]
        assert len(routes) == 1
        assert routes[0].status_code == 204


# ── hermetic: b. ownership comes first ───────────────────────────────────────


class TestOwnershipComesBeforeTheStop:

    @pytest.mark.parametrize("rows", [
        {"acc-1": _row(user_id=OTHER)},
        {},
    ], ids=["another-members-mailbox", "no-such-mailbox"])
    async def test_a_non_owner_gets_404_and_stops_no_loop(self, wired, rows):
        ledger = wired(rows)
        with pytest.raises(HTTPException) as err:
            await accounts.delete_account("acc-1", user=_user(OWNER))
        assert err.value.status_code == 404
        assert "stop" not in ledger.kinds(), "a non-owner stopped a sync loop"
        assert "delete" not in ledger.kinds()
        assert "graph_delete" not in ledger.kinds()
        assert ledger.open == 0


# ── hermetic: c. the Graph subscription ──────────────────────────────────────


class TestTheGraphSubscriptionIsDeleted:

    async def test_a_microsoft_row_with_a_subscription_gets_the_delete(self, wired):
        ledger = wired({"acc-1": _row(sub="sub-abc")})
        await accounts.delete_account("acc-1", user=_user())
        assert ledger.first("graph_delete") == ("graph_delete", 0, "sub-abc")
        assert ledger.first("decrypt") == ("decrypt", 0, "blob-em-t4f")
        build = ledger.first("build")
        assert build[1] == 0 and build[2] == "microsoft"
        assert ledger.index("graph_delete") > ledger.index("delete"), (
            "the Graph call must read the subscription id after the loop stopped")
        assert ledger.logs[-1][:2] == ("info", "email.disconnect.subscription_delete_sent")

    @pytest.mark.parametrize("graph,decrypt_raises,error", [
        ("raise", False, "RuntimeError"),
        ("slow", False, "TimeoutError"),
        ("ok", True, "ValueError"),
    ], ids=["graph-raises", "graph-is-slow", "decrypt-fails"])
    async def test_a_failed_graph_call_still_gives_204(
        self, wired, monkeypatch, graph, decrypt_raises, error,
    ):
        monkeypatch.setattr(accounts, "SUBSCRIPTION_DELETE_TIMEOUT_S", 0.05)
        rows = {"acc-1": _row()}
        ledger = wired(rows, graph=graph, decrypt_raises=decrypt_raises)
        started = time.monotonic()
        result = await accounts.delete_account("acc-1", user=_user())
        assert result is None
        assert time.monotonic() - started < 5, "the bound did not hold"
        assert "acc-1" not in rows, "the mailbox was not deleted"
        warnings = [log for log in ledger.logs if log[0] == "warning"]
        assert [w[1] for w in warnings] == [
            "email.disconnect.subscription_delete_failed"]
        assert warnings[0][2]["error"] == error
        assert warnings[0][2]["account_id"] == "acc-1"

    async def test_no_log_line_holds_a_token(self, wired):
        ledger = wired({"acc-1": _row()}, graph="raise")
        await accounts.delete_account("acc-1", user=_user())
        assert ledger.logs, "expected a log line"
        assert TOKEN not in repr(ledger.logs)

    def test_the_bound_is_five_seconds(self):
        assert accounts.SUBSCRIPTION_DELETE_TIMEOUT_S == 5.0


# ── hermetic: d. no Graph call without a subscription ────────────────────────


class TestNoGraphCallWithoutASubscription:

    @pytest.mark.parametrize("provider,sub", [
        ("microsoft", None),
        ("microsoft", ""),
        ("gmail", "sub-not-graph"),
        ("imap", None),
    ], ids=["microsoft-no-sub", "microsoft-empty-sub", "gmail", "imap"])
    async def test_no_provider_is_built(self, wired, provider, sub):
        rows = {"acc-1": _row(provider=provider, sub=sub)}
        ledger = wired(rows)
        await accounts.delete_account("acc-1", user=_user())
        assert "acc-1" not in rows
        for kind in ("decrypt", "build", "graph_delete"):
            assert kind not in ledger.kinds(), f"{kind} ran for {provider}"


# ── R8: the real SQL under FORCE RLS ─────────────────────────────────────────


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _seed_account(admin, *, org: str, owner: str, default: bool,
                  sub: str | None) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, is_default, webhook_subscription_id, "
            "created_at, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'blob-r8', :d, :s, "
            "now() - CAST(:age AS interval), CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@em-t4f.test",
             "d": default, "s": sub, "age": "1 hour" if default else "1 minute",
             "o": org}).scalar_one())


def _seed_message(admin, *, org: str, account_id: str) -> None:
    with admin.begin() as c:
        c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "from_address, to_addresses, subject, organization_id) "
            "VALUES (CAST(:a AS uuid), :pm, CAST(:f AS jsonb), "
            "CAST(:t AS jsonb), 'hello', CAST(:o AS uuid))"),
            {"a": account_id, "pm": f"pm-{uuid.uuid4().hex[:12]}",
             "f": json.dumps({"email": "s@sender.test"}),
             "t": json.dumps([{"email": "to@em-t4f.test"}]), "o": org})


def _admin_read(admin, sql: str, **params: Any) -> list[Any]:
    with admin.connect() as c:
        return list(c.execute(text(sql), params).fetchall())


@pytest.fixture()
def r8_doubles(monkeypatch):
    """Fake the loop stop and the Graph call. The SQL stays real."""
    seen: dict[str, list[Any]] = {"stop": [], "graph": []}

    async def _remove(account_id: str) -> None:
        seen["stop"].append(account_id)

    class _Graph:
        async def delete_subscription(self, subscription_id: str) -> None:
            seen["graph"].append(subscription_id)

    monkeypatch.setattr(sched, "remove_account_sync", _remove)
    monkeypatch.setattr(key_store_mod, "get_key_store", lambda: SimpleNamespace(
        decrypt=lambda blob: json.dumps({"access_token": TOKEN})))
    monkeypatch.setattr(accounts, "_instantiate_provider",
                        lambda name, creds: _Graph())
    return seen


@_DB_GATE
class TestTheDisconnectUnderForceRLS:

    async def test_the_delete_cascades_and_moves_the_default(
        self, promoted, app_engine, r8_doubles,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-t4f.test"
        gone = _seed_account(p.admin_engine, org=p.org_b, owner=owner,
                             default=True, sub="sub-r8-em-t4f")
        kept = _seed_account(p.admin_engine, org=p.org_b, owner=owner,
                             default=False, sub=None)
        _seed_message(p.admin_engine, org=p.org_b, account_id=gone)
        user = UserContext(email=owner, role=UserRole.EMPLOYEE,
                           organization_id=p.org_b)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                assert await accounts.delete_account(gone, user=user) is None
            assert r8_doubles["stop"] == [gone]
            assert r8_doubles["graph"] == ["sub-r8-em-t4f"], (
                "the RETURNING did not carry the subscription id")
            assert _admin_read(p.admin_engine,
                               "SELECT 1 FROM email_accounts WHERE id = CAST(:a AS uuid)",
                               a=gone) == []
            assert _admin_read(p.admin_engine,
                               "SELECT 1 FROM email_messages "
                               "WHERE account_id = CAST(:a AS uuid)", a=gone) == []
            [(is_default,)] = _admin_read(
                p.admin_engine,
                "SELECT is_default FROM email_accounts WHERE id = CAST(:a AS uuid)",
                a=kept)
            assert is_default is True, "the default did not move to the other mailbox"
        finally:
            release_tenant(token)
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"),
                          {"u": owner})

    async def test_another_member_and_another_org_get_404(
        self, promoted, app_engine, r8_doubles,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-t4f.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner,
                            default=True, sub="sub-r8-keep")
        app_dsn = p.app_url.render_as_string(hide_password=False)
        callers = [
            (p.org_b, UserContext(email=f"x-{owner}", role=UserRole.EMPLOYEE,
                                  organization_id=p.org_b)),
            (p.org_a, UserContext(email=owner, role=UserRole.EMPLOYEE,
                                  organization_id=p.org_a)),
        ]
        try:
            for org, user in callers:
                token = bind_tenant(org)
                try:
                    async with tenant_engine_scope(app_dsn):
                        with pytest.raises(HTTPException) as err:
                            await accounts.delete_account(acc, user=user)
                    assert err.value.status_code == 404
                finally:
                    release_tenant(token)
            assert r8_doubles["stop"] == [], "a non-owner stopped a sync loop"
            assert r8_doubles["graph"] == []
            assert _admin_read(p.admin_engine,
                               "SELECT 1 FROM email_accounts WHERE id = CAST(:a AS uuid)",
                               a=acc) != [], "a non-owner deleted the mailbox"
        finally:
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"),
                          {"u": owner})
