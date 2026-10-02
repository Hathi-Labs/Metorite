"""EM-T4f — a disconnect stops the sync first, then deletes, then drops Graph.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.6, EM-T4f.

Production, 2026-10-02. A member clicked Disconnect during the first sync of
an Outlook mailbox. The member waited about 2 minutes, and Microsoft kept
posting for the deleted mailbox. Two syncs of one mailbox ran at once, and the
old route waited for the loop task inside the block of its ``DELETE``. Part 1
(this file) bounds the wait of the ``DELETE``, starts the loop again when the
delete fails, and reads the Graph status. Part 2 (one sync for each mailbox)
is not built.

R7 fence named here: ``email-disconnect-order``.

**Hermetic.** A watched ``_tenant_session`` counts the open blocks, and every
fake records that count when it runs.

* a. ``remove_account_sync`` runs with no block open, after the ownership read
  and before the ``DELETE``. A companion test proves the order check can fail.
* b. A member who does not own the mailbox gets 404, and no loop stops.
* c. A Microsoft row with a subscription id gets ``delete_subscription`` with
  that id. A Graph call that raises, or that is slow, still gives 204. The
  REAL ``OutlookProvider`` over ``httpx.MockTransport`` shows that 204 and 404
  log ``subscription_deleted``, and 403 and 500 log the failure with the
  status. No log line holds a token.
* d. A row with no subscription id, or a row of another provider, builds no
  provider and makes no Graph call.
* e. The ``DELETE`` runs under ``SET LOCAL lock_timeout``. A lock timeout
  answers 409, and any failure of the delete block starts the loop again with
  the organization of the row. With the real scheduler, a failed ``DELETE``
  leaves a running loop.

**R8.** The real SQL against the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, as the role ``acb_app_h3rls``
(NOSUPERUSER, NOBYPASSRLS). The Graph call is a fake. One case holds a KEY
SHARE lock on the row from a second connection, and the 409 comes back after
about 5 seconds, not 2 minutes.

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
import httpx
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from email_ingestion.providers.outlook import GRAPH_API_BASE, OutlookProvider
from fastapi import HTTPException
from gateway.routes.email.transport import accounts
from sqlalchemy import exc as sa_exc
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
ROW_ORG = "11111111-2222-3333-4444-555555555555"


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
        for event in self.events:
            if event[0] == kind:
                return event
        raise AssertionError(f"no {kind!r} event ran; saw {self.kinds()}")

    def index(self, kind: str) -> int:
        return self.kinds().index(kind)

    def log_names(self) -> list[str]:
        return [name for _level, name, _kw in self.logs]


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _DriverError(Exception):
    """Stands in for the adapted asyncpg error, which carries ``sqlstate``."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"sqlstate {sqlstate}")
        self.sqlstate = sqlstate


def _lock_timeout_error() -> sa_exc.OperationalError:
    """The shape SQLAlchemy raises for SQLSTATE 55P03 over asyncpg."""
    return sa_exc.OperationalError(
        "DELETE FROM email_accounts", {}, _DriverError("55P03"))


class _FakeDB:
    """Answers the statements of ``delete_account`` from a dict."""

    def __init__(self, ledger: _Ledger, rows: dict[str, dict[str, Any]],
                 delete_fails: BaseException | None) -> None:
        self.ledger = ledger
        self.rows = rows
        self.delete_fails = delete_fails

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None):
        sql = " ".join(str(stmt).split())
        p = params or {}
        if sql.startswith("SELECT organization_id FROM email_accounts"):
            self.ledger.events.append(("read", self.ledger.open))
            row = self.rows.get(p["id"])
            owned = row is not None and row["user_id"] == p["uid"]
            return _Result(SimpleNamespace(organization_id=row["org"]) if owned else None)
        if sql.startswith("SET LOCAL lock_timeout"):
            self.ledger.events.append(("lock_timeout", self.ledger.open, sql))
            return _Result(None)
        if sql.startswith("DELETE FROM email_accounts"):
            self.ledger.events.append(("delete", self.ledger.open))
            if self.delete_fails is not None:
                raise self.delete_fails
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

    async def delete_subscription(self, subscription_id: str) -> int:
        self.ledger.events.append(("graph_delete", self.ledger.open, subscription_id))
        if self.behaviour == "raise":
            raise RuntimeError(f"Graph refused, bearer {TOKEN}")
        if self.behaviour == "slow":
            await asyncio.sleep(30)
        return 204


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
            "is_default": is_default, "creds": "blob-em-t4f", "org": ROW_ORG}


@pytest.fixture()
def wired(monkeypatch):
    """Wire the doubles into ``accounts``, and return a setup function.

    With ``fake_scheduler=False`` the scheduler stays real, so a test can see
    the loop bookkeeping of ``remove_account_sync`` and ``refresh_account_sync``.
    """

    def _setup(rows: dict[str, dict[str, Any]], *, graph: Any = "ok",
               stop_raises: bool = False, decrypt_raises: bool = False,
               delete_fails: BaseException | None = None,
               loops: tuple[str, ...] = ("acc-1",),
               fake_scheduler: bool = True):
        ledger = _Ledger()
        db = _FakeDB(ledger, rows, delete_fails)
        monkeypatch.setattr(accounts, "_tenant_session", _watched_session(ledger, db))
        monkeypatch.setattr(accounts, "_log", _RecordingLog(ledger))

        if fake_scheduler:
            async def _remove(account_id: str) -> None:
                ledger.events.append(("stop", ledger.open, account_id))
                if stop_raises:
                    raise RuntimeError("the scheduler lock is broken")

            async def _refresh(account_id: str, organization_id: str | None = None) -> None:
                ledger.events.append(("restart", ledger.open, account_id, organization_id))

            monkeypatch.setattr(sched, "remove_account_sync", _remove)
            monkeypatch.setattr(sched, "refresh_account_sync", _refresh)
            monkeypatch.setattr(sched, "get_scheduler_status",
                                lambda: {"accounts": list(loops)})

        def _decrypt(blob: str) -> str:
            ledger.events.append(("decrypt", ledger.open, blob))
            if decrypt_raises:
                raise ValueError("bad blob")
            return json.dumps({"access_token": TOKEN, "refresh_token": TOKEN})

        monkeypatch.setattr(
            key_store_mod, "get_key_store", lambda: SimpleNamespace(decrypt=_decrypt))

        provider = graph if not isinstance(graph, str) else _FakeGraph(ledger, graph)

        def _build(name: str, creds: dict[str, Any]) -> Any:
            ledger.events.append(("build", ledger.open, name, creds))
            return provider

        monkeypatch.setattr(accounts, "_instantiate_provider", _build)
        return ledger

    return _setup


def _user(email: str = OWNER) -> UserContext:
    # The session org differs from the row org on purpose: a restart must
    # take the org of the row.
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
        assert "restart" not in ledger.kinds(), "a good disconnect restarted the loop"

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
        assert "restart" not in ledger.kinds()
        assert ledger.open == 0


# ── hermetic: c. the Graph subscription ──────────────────────────────────────


def _real_outlook(status: int | None, seen: list[httpx.Request]) -> OutlookProvider:
    """The REAL provider, with a MockTransport client in place of Graph.

    ``status=None`` makes the transport raise ``httpx.ConnectError``.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status is None:
            raise httpx.ConnectError("graph unreachable", request=request)
        return httpx.Response(status)

    provider = OutlookProvider({"access_token": TOKEN, "refresh_token": TOKEN})
    provider._http = httpx.AsyncClient(
        base_url=GRAPH_API_BASE, transport=httpx.MockTransport(_handler))
    return provider


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
        assert ledger.logs[-1][:2] == ("info", "email.disconnect.subscription_deleted")

    @pytest.mark.parametrize("status,level,event", [
        (204, "info", "email.disconnect.subscription_deleted"),
        (404, "info", "email.disconnect.subscription_deleted"),
        (403, "warning", "email.disconnect.subscription_delete_failed"),
        (500, "warning", "email.disconnect.subscription_delete_failed"),
    ])
    async def test_the_real_provider_reports_the_graph_status(
        self, wired, status, level, event,
    ):
        seen: list[httpx.Request] = []
        rows = {"acc-1": _row(sub="sub-real")}
        ledger = wired(rows, graph=_real_outlook(status, seen))
        assert await accounts.delete_account("acc-1", user=_user()) is None
        assert "acc-1" not in rows
        assert [(r.method, r.url.path) for r in seen] == [
            ("DELETE", "/v1.0/subscriptions/sub-real")]
        graph_logs = [log for log in ledger.logs if "subscription" in log[1]]
        assert len(graph_logs) == 1
        assert graph_logs[0][:2] == (level, event)
        assert graph_logs[0][2]["status"] == status
        assert TOKEN not in repr(ledger.logs)

    async def test_a_transport_error_of_the_real_provider_is_logged(self, wired):
        seen: list[httpx.Request] = []
        ledger = wired({"acc-1": _row()}, graph=_real_outlook(None, seen))
        assert await accounts.delete_account("acc-1", user=_user()) is None
        [failed] = [log for log in ledger.logs if "subscription" in log[1]]
        assert failed[:2] == ("warning", "email.disconnect.subscription_delete_failed")
        assert failed[2]["error"] == "ConnectError"
        assert failed[2]["status"] is None

    @pytest.mark.parametrize("status", [204, 404, 403, 500])
    async def test_delete_subscription_returns_the_graph_status(self, status):
        seen: list[httpx.Request] = []
        provider = _real_outlook(status, seen)
        assert await provider.delete_subscription("sub-x") == status
        assert [(r.method, r.url.path) for r in seen] == [
            ("DELETE", "/v1.0/subscriptions/sub-x")]

    async def test_delete_subscription_raises_on_a_transport_error(self):
        provider = _real_outlook(None, [])
        with pytest.raises(httpx.ConnectError):
            await provider.delete_subscription("sub-x")

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


# ── hermetic: e. the lock bound, the 409 and the restart ─────────────────────


class TestAFailedDeleteStartsTheLoopAgain:

    async def test_the_delete_runs_under_a_lock_timeout(self, wired):
        ledger = wired({"acc-1": _row()})
        await accounts.delete_account("acc-1", user=_user())
        bound = ledger.first("lock_timeout")
        assert bound[1] == 1, "SET LOCAL ran outside the delete block"
        assert bound[2] == "SET LOCAL lock_timeout = '5s'"
        assert ledger.index("lock_timeout") < ledger.index("delete")
        assert ledger.index("lock_timeout") > ledger.index("stop")

    async def test_a_lock_timeout_answers_409_and_restarts_the_loop(self, wired):
        rows = {"acc-1": _row()}
        ledger = wired(rows, delete_fails=_lock_timeout_error())
        with pytest.raises(HTTPException) as err:
            await accounts.delete_account("acc-1", user=_user())
        assert err.value.status_code == 409
        assert err.value.detail == accounts.DISCONNECT_BUSY_DETAIL
        assert "acc-1" in rows, "a refused disconnect deleted the mailbox"
        assert ledger.first("restart") == ("restart", 0, "acc-1", ROW_ORG), (
            "the loop must start again, with the org of the row and no block open")
        assert ledger.index("restart") > ledger.index("delete")
        assert "graph_delete" not in ledger.kinds()
        assert "email.disconnect.busy" in ledger.log_names()

    async def test_any_failed_delete_restarts_the_loop_and_raises(self, wired):
        rows = {"acc-1": _row()}
        ledger = wired(rows, delete_fails=RuntimeError("connection reset"))
        with pytest.raises(RuntimeError, match="connection reset"):
            await accounts.delete_account("acc-1", user=_user())
        assert "acc-1" in rows
        assert ledger.first("restart") == ("restart", 0, "acc-1", ROW_ORG)
        assert "graph_delete" not in ledger.kinds()

    async def test_no_restart_when_no_loop_ran_before(self, wired):
        # Sync off, or EMAIL_SYNC_ENABLED off: a failed disconnect must not
        # start a loop that did not run.
        rows = {"acc-1": _row()}
        ledger = wired(rows, delete_fails=_lock_timeout_error(), loops=())
        with pytest.raises(HTTPException) as err:
            await accounts.delete_account("acc-1", user=_user())
        assert err.value.status_code == 409
        assert "restart" not in ledger.kinds()

    def test_only_sqlstate_55p03_is_a_lock_timeout(self):
        assert accounts._is_lock_timeout(_lock_timeout_error())
        assert not accounts._is_lock_timeout(sa_exc.OperationalError(
            "DELETE", {}, _DriverError("40P01")))
        assert not accounts._is_lock_timeout(RuntimeError("no sqlstate"))

    async def test_with_the_real_scheduler_a_failed_delete_leaves_a_running_loop(
        self, wired, monkeypatch,
    ):
        started: list[tuple[str, int, str]] = []

        async def _sleeper(account_id: str, interval: int, *, organization_id: str):
            started.append((account_id, interval, organization_id))
            await asyncio.sleep(3600)

        async def _interval(account_id: str, organization_id: str) -> int:
            return 300

        monkeypatch.setattr(sched, "_account_sync_loop", _sleeper)
        monkeypatch.setattr(sched, "_get_account_sync_interval", _interval)
        old = asyncio.create_task(asyncio.sleep(3600))
        monkeypatch.setitem(sched._scheduler_tasks, "acc-1", old)
        rows = {"acc-1": _row()}
        wired(rows, delete_fails=RuntimeError("connection reset"),
              fake_scheduler=False)
        try:
            with pytest.raises(RuntimeError):
                await accounts.delete_account("acc-1", user=_user())
            await asyncio.sleep(0)
            assert old.cancelled(), "step 2 did not stop the old loop"
            new = sched._scheduler_tasks.get("acc-1")
            assert new is not None and new is not old and not new.done(), (
                "a failed DELETE left the mailbox with no running loop")
            assert started == [("acc-1", 300, ROW_ORG)]
        finally:
            await sched.remove_account_sync("acc-1")
            if not old.done():
                old.cancel()


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
        async def delete_subscription(self, subscription_id: str) -> int:
            seen["graph"].append(subscription_id)
            return 204

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

    async def test_a_held_key_share_lock_gives_409_in_seconds_and_the_loop_runs(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """A second connection holds the KEY SHARE lock that an FK check of a
        sync outside the loop holds. The real scheduler stops the loop, the
        DELETE gives up after about 5 seconds, and the loop starts again with
        the organization of the row."""
        _assert_non_priv(app_engine)
        p = promoted
        started: list[tuple[str, int, str]] = []

        async def _sleeper(account_id: str, interval: int, *, organization_id: str):
            started.append((account_id, interval, organization_id))
            await asyncio.sleep(3600)

        monkeypatch.setattr(sched, "_account_sync_loop", _sleeper)
        monkeypatch.setattr(accounts, "_instantiate_provider", lambda *_a: pytest.fail(
            "a refused disconnect reached Graph"))
        owner = f"member-{uuid.uuid4().hex[:8]}@em-t4f.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner,
                            default=True, sub="sub-r8-held")
        old = asyncio.create_task(asyncio.sleep(3600))
        monkeypatch.setitem(sched._scheduler_tasks, acc, old)
        user = UserContext(email=owner, role=UserRole.EMPLOYEE,
                           organization_id=p.org_b)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        holder = p.admin_engine.connect()
        held = holder.begin()
        token = bind_tenant(p.org_b)
        try:
            holder.execute(text(
                "SELECT 1 FROM email_accounts WHERE id = CAST(:a AS uuid) "
                "FOR KEY SHARE"), {"a": acc})
            async with tenant_engine_scope(app_dsn):
                t0 = time.monotonic()
                with pytest.raises(HTTPException) as err:
                    await accounts.delete_account(acc, user=user)
                waited = time.monotonic() - t0
                await asyncio.sleep(0)
                assert err.value.status_code == 409
                assert err.value.detail == accounts.DISCONNECT_BUSY_DETAIL
                assert 3.0 < waited < 30.0, f"the DELETE waited {waited:.1f}s"
                assert old.cancelled(), "step 2 did not stop the old loop"
                new = sched._scheduler_tasks.get(acc)
                assert new is not None and new is not old and not new.done(), (
                    "the refused disconnect left the mailbox with no loop")
                assert started and started[0][0] == acc
                assert started[0][2] == str(p.org_b)
            assert _admin_read(p.admin_engine,
                               "SELECT 1 FROM email_accounts WHERE id = CAST(:a AS uuid)",
                               a=acc) != [], "the refused DELETE removed the row"
        finally:
            held.rollback()
            holder.close()
            release_tenant(token)
            await sched.remove_account_sync(acc)
            if not old.done():
                old.cancel()
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"),
                          {"u": owner})
