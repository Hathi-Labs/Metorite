"""EM-T8f-1 — a disconnect purges the Mem0 memory of the mailbox (MB-17).

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.7.6, EM-T8f-1 items
2 and 3.

Two writers put the drafting memory of a mailbox into Mem0 under
``<owner>#acct:<id>``. Before EM-T8f-1 a disconnect left those memories
behind. ``transport/accounts.py::delete_account`` now starts
``memory_purge.schedule_mailbox_memory_purge`` after its DELETE commits.

R7 fences named here:

* ``email-disconnect-purges-memory``: a 204 deletes each memory under
  ``<owner>#acct:<id>``, over more than one page. It deletes no memory of the
  bare scope, of another mailbox, or of another member.
* ``email-purge-refuses-bare-scope``: an empty account id deletes nothing.
* ``email-purge-after-delete``: a 404, a 409 or a failed DELETE makes no purge
  call. A failed purge still gives 204, and its log holds no memory text.
* ``email-account-created-at``: each account read returns ``created_at``
  (R8).

**Hermetic.** The doubles of ``test_email_disconnect_order.py`` (the
``wired`` fixture) stand in for the session, the scheduler and Graph. The
REAL ``MemoryClient.delete_scope`` runs over ``_FakeMem0``, a stand-in for
the mem0ai ``Memory`` that matches ``user_id`` exactly and caps a read at
``top_k``, as pgvector does.

**R8.** The real DELETE under FORCE RLS as ``acb_app_h3rls``, with the fake
Mem0, and the four account reads against the real catalog.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_disconnect_memory_purge.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import acb_llm.key_store as key_store_mod
import email_ingestion.scheduler as sched
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from acb_memory import mem0_client
from fastapi import HTTPException
from gateway.routes.email import memory_purge
from gateway.routes.email.transport import accounts
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``wired``, ``r8_doubles``, ``promoted`` and ``app_engine`` are fixtures, used
# by name, so the imports are load-bearing even though they read as unused.
from tests.unit.test_email_disconnect_order import (  # noqa: F401
    OWNER,
    _lock_timeout_error,
    _row,
    _user,
    r8_doubles,
    wired,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

#: The text of every seeded memory starts with this. It must never reach a log.
SECRET = "secret-memory-text"
MAILBOX = f"{OWNER}#acct:acc-1"


# ── the fake Mem0 ────────────────────────────────────────────────────────────


class _FakeMem0:
    """Stands in for mem0ai ``Memory``. ``get_all`` matches ``user_id``
    exactly and returns at most ``top_k`` rows. ``delete`` removes one row."""

    def __init__(self, scopes: dict[str, int], *, fail: str | None = None,
                 stuck: bool = False) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        for scope, n in scopes.items():
            for i in range(n):
                mid = str(uuid.uuid4())
                self.rows[mid] = {"id": mid, "user_id": scope,
                                  "memory": f"{SECRET} {scope} {i}"}
        self.reads: list[tuple[str, int, bool]] = []
        self.deleted: list[str] = []
        self.fail = fail
        self.stuck = stuck

    def get_all(self, *, filters: dict[str, Any], top_k: int = 20,
                show_expired: bool = False) -> dict[str, Any]:
        scope = filters["user_id"]
        self.reads.append((scope, top_k, show_expired))
        if self.fail == "read":
            raise RuntimeError(f"pgvector refused near {SECRET}")
        hits = [dict(r) for r in self.rows.values() if r["user_id"] == scope]
        return {"results": hits[:top_k]}

    def delete(self, memory_id: str) -> dict[str, str]:
        if self.fail == "delete":
            raise RuntimeError(f"row {self.rows[memory_id]['memory']} is locked")
        if self.stuck:
            return {"message": "ok"}
        self.deleted.append(memory_id)
        del self.rows[memory_id]
        return {"message": "ok"}

    def left(self, scope: str) -> int:
        return sum(1 for r in self.rows.values() if r["user_id"] == scope)


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.lines.append(("info", event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.lines.append(("warning", event, kw))


@pytest.fixture()
def mem0(monkeypatch):
    """Wire a ``_FakeMem0`` under the REAL ``MemoryClient``, and a recording
    log into ``memory_purge``. Returns a setup function."""

    def _setup(scopes: dict[str, int], **kw: Any) -> SimpleNamespace:
        fake = _FakeMem0(scopes, **kw)
        client = mem0_client.MemoryClient()
        client._client = fake
        monkeypatch.setattr(mem0_client, "get_memory_client", lambda: client)
        log = _Log()
        monkeypatch.setattr(memory_purge, "_log", log)
        return SimpleNamespace(fake=fake, client=client, log=log)

    return _setup


def _purges_here() -> list[asyncio.Task[Any]]:
    """The purge tasks of THIS event loop. Another suite may have left a
    task of a loop that is closed now."""
    loop = asyncio.get_running_loop()
    return [t for t in memory_purge._PURGES if t.get_loop() is loop]


async def _drain() -> None:
    """Wait for each purge task that a disconnect started."""
    for _ in range(3):
        await asyncio.sleep(0)
    await asyncio.wait_for(asyncio.gather(*_purges_here()), timeout=10)


def _seeded() -> dict[str, int]:
    """250 memories of the mailbox, and three of each neighbour scope.

    ``acc-10`` starts with ``acc-1``, so a prefix match would take it.
    """
    return {
        MAILBOX: 250,
        OWNER: 3,
        f"{OWNER}#acct:acc-2": 3,
        f"{OWNER}#acct:acc-10": 3,
        "someone@else.test#acct:acc-1": 3,
    }


# ── email-disconnect-purges-memory ───────────────────────────────────────────


class TestADisconnectPurgesTheMemoryOfTheMailbox:

    async def test_a_204_deletes_each_memory_of_the_mailbox_over_pages(
        self, wired, mem0,  # noqa: F811
    ):
        wired({"acc-1": _row()})
        m = mem0(_seeded())
        assert await accounts.delete_account("acc-1", user=_user()) is None
        await _drain()
        assert m.fake.left(MAILBOX) == 0, "a memory of the mailbox is left"
        assert len(m.fake.deleted) == 250
        pages = [r for r in m.fake.reads if r[0] == MAILBOX]
        assert len(pages) == 4, "250 rows at 100 a page is three pages and one empty read"
        assert all(top_k == mem0_client.DELETE_SCOPE_PAGE == 100 and expired
                   for _s, top_k, expired in pages)
        assert {r[0] for r in m.fake.reads} == {MAILBOX}, "it read another scope"
        assert ("info", "email.disconnect.memory_purged",
                {"account_id": "acc-1", "count": 250}) in m.log.lines

    async def test_it_deletes_no_memory_of_another_scope(self, wired, mem0):  # noqa: F811
        wired({"acc-1": _row()})
        m = mem0(_seeded())
        await accounts.delete_account("acc-1", user=_user())
        await _drain()
        for scope, n in _seeded().items():
            if scope != MAILBOX:
                assert m.fake.left(scope) == n, f"the purge touched {scope}"

    async def test_the_owner_is_the_member_in_any_case(self, wired, mem0):  # noqa: F811
        # The writers key on the lower-case member, so the purge does too.
        wired({"acc-1": _row(user_id=OWNER.upper())})
        m = mem0(_seeded())
        await accounts.delete_account("acc-1", user=_user(OWNER.upper()))
        await _drain()
        assert m.fake.left(MAILBOX) == 0

    async def test_the_purge_starts_after_the_delete_block_closes(
        self, wired, monkeypatch,  # noqa: F811
    ):
        ledger = wired({"acc-1": _row()})

        def _record(owner: str, account_id: str) -> None:
            ledger.events.append(("purge", ledger.open, owner, account_id))

        monkeypatch.setattr(accounts, "schedule_mailbox_memory_purge", _record)
        await accounts.delete_account("acc-1", user=_user())
        purge = ledger.first("purge")
        assert purge == ("purge", 0, OWNER, "acc-1"), "the purge ran inside a block"
        closes = [i for i, e in enumerate(ledger.events) if e[0] == "close"]
        assert ledger.index("purge") > ledger.index("delete")
        assert ledger.index("purge") > closes[-1], "the purge ran before the commit"


# ── email-purge-refuses-bare-scope ───────────────────────────────────────────


class TestThePurgeRefusesTheBareScope:

    @pytest.mark.parametrize("owner,account_id", [
        (OWNER, ""), (OWNER, "   "), ("", "acc-1"), ("  ", "acc-1"),
    ], ids=["no-account", "blank-account", "no-owner", "blank-owner"])
    async def test_a_key_with_no_mailbox_deletes_nothing(
        self, mem0, owner, account_id,
    ):
        m = mem0(_seeded())
        assert await memory_purge.purge_mailbox_memory(owner, account_id) is None
        assert m.fake.reads == [] and m.fake.deleted == [], "it read or deleted"
        assert [line[1] for line in m.log.lines] == [
            "email.disconnect.memory_purge_refused"]

    def test_the_scope_helper_raises(self):
        with pytest.raises(memory_purge.BareScopeRefused):
            memory_purge.mailbox_memory_scope(OWNER, "")
        assert memory_purge.mailbox_memory_scope(OWNER, "acc-1") == MAILBOX

    async def test_delete_scope_refuses_a_blank_key_before_any_read(self, mem0):
        m = mem0(_seeded())
        with pytest.raises(ValueError):
            await m.client.delete_scope("  ")
        assert m.fake.reads == []


# ── email-purge-after-delete ─────────────────────────────────────────────────


class TestNoPurgeWithoutADelete:

    @pytest.mark.parametrize("rows,delete_fails,status", [
        ({}, None, 404),
        ({"acc-1": _row(user_id="other@em-t4f.test")}, None, 404),
        ({"acc-1": _row()}, "lock", 409),
    ], ids=["no-such-mailbox", "another-members-mailbox", "lock-timeout"])
    async def test_a_refused_disconnect_makes_no_purge_call(
        self, wired, mem0, rows, delete_fails, status,  # noqa: F811
    ):
        fails = _lock_timeout_error() if delete_fails == "lock" else None
        wired(rows, delete_fails=fails)
        m = mem0(_seeded())
        with pytest.raises(HTTPException) as err:
            await accounts.delete_account("acc-1", user=_user())
        assert err.value.status_code == status
        await _drain()
        assert m.fake.reads == [] and m.fake.deleted == []
        assert not _purges_here()

    async def test_a_failed_delete_makes_no_purge_call(self, wired, mem0):  # noqa: F811
        wired({"acc-1": _row()}, delete_fails=RuntimeError("connection reset"))
        m = mem0(_seeded())
        with pytest.raises(RuntimeError):
            await accounts.delete_account("acc-1", user=_user())
        await _drain()
        assert m.fake.reads == [] and m.fake.deleted == []

    @pytest.mark.parametrize("fail", ["read", "delete"])
    async def test_a_failed_purge_still_gives_204_and_logs_no_memory_text(
        self, wired, mem0, fail,  # noqa: F811
    ):
        rows = {"acc-1": _row()}
        ledger = wired(rows)
        m = mem0(_seeded(), fail=fail)
        assert await accounts.delete_account("acc-1", user=_user()) is None
        assert "acc-1" not in rows, "the mailbox was not deleted"
        await _drain()
        assert ("warning", "email.disconnect.memory_purge_failed",
                {"account_id": "acc-1", "error": "RuntimeError"}) in m.log.lines
        assert SECRET not in repr(m.log.lines) + repr(ledger.logs)
        assert OWNER not in repr(m.log.lines), "a purge log holds the address"

    async def test_a_delete_with_no_effect_ends_as_a_failure(self, wired, mem0):  # noqa: F811
        wired({"acc-1": _row()})
        m = mem0(_seeded(), stuck=True)
        assert await accounts.delete_account("acc-1", user=_user()) is None
        await _drain()
        assert ("warning", "email.disconnect.memory_purge_failed",
                {"account_id": "acc-1", "error": "RuntimeError"}) in m.log.lines

    async def test_with_mem0_off_the_purge_counts_zero(self, monkeypatch):
        client = mem0_client.MemoryClient()
        monkeypatch.setattr(client, "_get_client", lambda: None)
        assert await client.delete_scope(MAILBOX) == 0


# ── R8: the real DELETE, and the account reads ───────────────────────────────


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _seed_account(admin, *, org: str, owner: str, default: bool,
                  age: str) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, is_default, created_at, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'blob-r8', :d, "
            "now() - CAST(:age AS interval), CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@em-t8f.test",
             "d": default, "age": age, "o": org}).scalar_one())


def _created_at(admin, account_id: str) -> datetime:
    with admin.connect() as c:
        return c.execute(text(
            "SELECT created_at FROM email_accounts WHERE id = CAST(:a AS uuid)"),
            {"a": account_id}).scalar_one()


def _iso(value: datetime) -> str:
    # asyncpg gives the route UTC. The admin driver gives the session zone.
    return value.astimezone(UTC).isoformat(timespec="microseconds")


@_DB_GATE
class TestOnARealDatabase:

    async def test_the_real_delete_purges_the_mailbox_of_the_row_owner(
        self, promoted, app_engine, r8_doubles, mem0,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-t8f.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner,
                            default=True, age="1 hour")
        scope = f"{owner}#acct:{acc}"
        m = mem0({scope: 120, owner: 2})
        user = UserContext(email=owner, role=UserRole.EMPLOYEE,
                           organization_id=p.org_b)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                assert await accounts.delete_account(acc, user=user) is None
                await _drain()
            assert m.fake.left(scope) == 0
            assert m.fake.left(owner) == 2, "the purge took the bare scope"
        finally:
            release_tenant(token)
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"),
                          {"u": owner})

    async def test_another_org_gets_404_and_no_purge(
        self, promoted, app_engine, r8_doubles, mem0,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-t8f.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner,
                            default=True, age="1 hour")
        m = mem0({f"{owner}#acct:{acc}": 5})
        user = UserContext(email=owner, role=UserRole.EMPLOYEE,
                           organization_id=p.org_a)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_a)
        try:
            async with tenant_engine_scope(app_dsn):
                with pytest.raises(HTTPException) as err:
                    await accounts.delete_account(acc, user=user)
                await _drain()
            assert err.value.status_code == 404
            assert m.fake.reads == [] and m.fake.deleted == []
        finally:
            release_tenant(token)
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"),
                          {"u": owner})

    async def test_each_account_read_returns_created_at(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """``email-account-created-at``: the list, the create, the default
        and the update each return the time of the connect, with six digits
        of microseconds."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-t8f.test"
        old = _seed_account(p.admin_engine, org=p.org_b, owner=owner,
                            default=True, age="2 days")
        new = _seed_account(p.admin_engine, org=p.org_b, owner=owner,
                            default=False, age="1 hour")

        async def _no_sync(*_a: Any, **_k: Any) -> None:
            return None

        monkeypatch.setattr(sched, "refresh_account_sync", _no_sync)
        monkeypatch.setattr(key_store_mod, "get_key_store", lambda: SimpleNamespace(
            encrypt=lambda raw: "blob-" + json.loads(raw)["imap_host"]))
        user = UserContext(email=owner, role=UserRole.EMPLOYEE,
                           organization_id=p.org_b)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                listed = await accounts.list_accounts(user=user)
                made = await accounts.set_default_account(new, user=user)
                patched = await accounts.update_account(
                    old, accounts.AccountUpdateModel(label="Old box"), user=user)
                created = await accounts.create_account(
                    accounts.CreateAccountRequest(
                        provider="imap", email_address=f"imap-{owner}",
                        credentials={
                            "imap_host": "h", "imap_port": 993,
                            "imap_username": "u", "imap_password": "p",
                            "smtp_host": "h", "smtp_port": 465}),
                    user=user)
            want = {old: _iso(_created_at(p.admin_engine, old)),
                    new: _iso(_created_at(p.admin_engine, new))}
            assert {a.id: a.created_at for a in listed} == want
            assert made.created_at == want[new]
            assert patched.created_at == want[old]
            assert created.created_at == _iso(_created_at(p.admin_engine, created.id))
            # Text order is time order, so the UI can sort the ISO text.
            assert want[old] < want[new] < created.created_at
            for value in [*want.values(), created.created_at]:
                assert len(value.split(".")[1]) == len("123456+00:00"), value
        finally:
            release_tenant(token)
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"),
                          {"u": owner})
