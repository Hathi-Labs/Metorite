"""EM-T8f-1 — a disconnect purges the Mem0 memory of the mailbox (MB-17).

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.7.6, EM-T8f-1 items
2 and 3, and the notes of review round 1.

Three writers put the drafting memory of a mailbox into Mem0 under
``<owner>#acct:<id>``. Before EM-T8f-1 a disconnect left those memories
behind. ``transport/accounts.py::delete_account`` now starts
``memory_purge.schedule_mailbox_memory_purge`` after its DELETE commits.

R7 fences named here:

* ``email-disconnect-purges-memory``: a 204 deletes each memory under
  ``<owner>#acct:<id>``, over more than one page. It deletes no memory of the
  bare scope, of another mailbox, or of another member. The key holds the
  canonical form of the id, so a path id in capitals or with no hyphens still
  finds it.
* ``email-memory-key-canonical`` (review round 2): ``core.email_memory_scope``
  gives one key for each form of one id. A real writer that takes the id from
  the request, given capitals, writes the key that the purge deletes (R8).
* ``email-purge-second-pass``: a second pass, ``SECOND_PASS_DELAY_S`` after the
  first, deletes a memory that a late add wrote between the passes.
* ``email-purge-refuses-bare-scope``: an empty or invalid account id deletes
  nothing. ``delete_scope`` refuses ``"*"``, a value that is not a ``str``,
  and a blank value, before any read.
* ``email-purge-page-cap``: a scope that never empties raises after
  ``DELETE_SCOPE_MAX_PAGES`` reads, fast.
* ``email-purge-strong-reference``: ``_PURGES`` holds the task while it runs,
  and lets it go after.
* ``email-purge-after-delete``: a 404, a 409 or a failed DELETE makes no purge
  call. A failed purge still gives 204, and its log holds no memory text.
* ``email-account-created-at``: each account read returns ``created_at``, with
  six digits of microseconds even when they are zero (R8).

**Hermetic.** The doubles of ``test_email_disconnect_order.py`` (the
``wired`` fixture) stand in for the session, the scheduler and Graph. The
REAL ``MemoryClient.delete_scope`` runs over ``_FakeMem0``, a stand-in for
the mem0ai ``Memory`` that matches ``user_id`` exactly and caps a read at
``top_k``, as pgvector does. ``memory_purge._sleep`` is replaced, so no test
waits two minutes.

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
from gateway.routes.email.automation import assistant as assistant_mod
from gateway.routes.email.core import email_memory_scope
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
#: The mailbox under test, in the canonical form that the writers use.
ACC = "6f1c2d3e-4a5b-4c6d-8e7f-90a1b2c3d4e5"
MAILBOX = f"{OWNER}#acct:{ACC}"


# ── the fake Mem0 ────────────────────────────────────────────────────────────


class _FakeMem0:
    """Stands in for mem0ai ``Memory``. ``get_all`` matches ``user_id``
    exactly and returns at most ``top_k`` rows. ``delete`` removes one row.

    ``endless`` makes each read return one new row and each delete do
    nothing, so the scope never empties and no page comes back twice. Past
    ``safety`` reads it raises ``_SafetyStop``, so a regression of the page
    cap fails the test and never hangs the run.

    ``add`` has the shape of ``Memory.add``, so a real writer reaches it
    through ``add_memories_background``. ``seed`` puts one row in directly.
    """

    def __init__(self, scopes: dict[str, int], *, fail: str | None = None,
                 stuck: bool = False, endless: bool = False) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        for scope, n in scopes.items():
            for i in range(n):
                self.seed(scope, f"{SECRET} {scope} {i}")
        self.reads: list[tuple[str, int, bool]] = []
        self.deleted: list[str] = []
        self.added: list[str] = []
        self.fail = fail
        self.stuck = stuck
        self.endless = endless
        self.safety = 2 * mem0_client.DELETE_SCOPE_MAX_PAGES + 10

    def seed(self, scope: str, memory: str) -> None:
        mid = str(uuid.uuid4())
        self.rows[mid] = {"id": mid, "user_id": scope, "memory": memory}

    def add(self, messages: list[dict[str, str]], *, user_id: str,
            agent_id: str | None = None) -> dict[str, Any]:
        self.added.append(user_id)
        self.seed(user_id, " ".join(m.get("content", "") for m in messages))
        return {"results": []}

    def get_all(self, *, filters: dict[str, Any], top_k: int = 20,
                show_expired: bool = False) -> dict[str, Any]:
        scope = filters["user_id"]
        self.reads.append((scope, top_k, show_expired))
        if len(self.reads) > self.safety:
            raise _SafetyStop("the page loop has no cap")
        if self.fail == "read" or (self.fail == "first-read" and len(self.reads) == 1):
            raise RuntimeError(f"pgvector refused near {SECRET}")
        if self.endless:
            return {"results": [{"id": str(uuid.uuid4()), "user_id": scope,
                                 "memory": SECRET}]}
        hits = [dict(r) for r in self.rows.values() if r["user_id"] == scope]
        return {"results": hits[:top_k]}

    def delete(self, memory_id: str) -> dict[str, str]:
        if self.fail == "delete":
            raise RuntimeError(f"row {self.rows[memory_id]['memory']} is locked")
        if self.stuck or self.endless:
            return {"message": "ok"}
        self.deleted.append(memory_id)
        del self.rows[memory_id]
        return {"message": "ok"}

    def left(self, scope: str) -> int:
        return sum(1 for r in self.rows.values() if r["user_id"] == scope)


class _SafetyStop(Exception):
    """The fake stopped a loop that the code under test did not stop."""


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.lines.append(("info", event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.lines.append(("warning", event, kw))


@pytest.fixture()
def mem0(monkeypatch):
    """Wire a ``_FakeMem0`` under the REAL ``MemoryClient``, a recording log
    and an instant sleep into ``memory_purge``. Returns a setup function.

    ``between`` runs in place of the wait between the two passes.
    """

    def _setup(scopes: dict[str, int], *, between: Any = None,
               **kw: Any) -> SimpleNamespace:
        fake = _FakeMem0(scopes, **kw)
        client = mem0_client.MemoryClient()
        client._client = fake
        monkeypatch.setattr(mem0_client, "get_memory_client", lambda: client)
        log = _Log()
        monkeypatch.setattr(memory_purge, "_log", log)
        delays: list[float] = []

        async def _sleep(seconds: float) -> None:
            delays.append(seconds)
            if between is not None:
                await between(fake)

        monkeypatch.setattr(memory_purge, "_sleep", _sleep)
        return SimpleNamespace(fake=fake, client=client, log=log, delays=delays)

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

    The ``0`` key starts with the mailbox key, so a prefix match would take it.
    """
    return {
        MAILBOX: 250,
        OWNER: 3,
        f"{OWNER}#acct:{uuid.uuid4()}": 3,
        f"{MAILBOX}0": 3,
        f"someone@else.test#acct:{ACC}": 3,
    }


def _purged(first: int, second: int) -> tuple[str, str, dict[str, Any]]:
    return ("info", "email.disconnect.memory_purged",
            {"account_id": ACC, "first": first, "second": second})


def _failed(phase: str) -> tuple[str, str, dict[str, Any]]:
    return ("warning", "email.disconnect.memory_purge_failed",
            {"account_id": ACC, "phase": phase, "error": "RuntimeError"})


# ── email-disconnect-purges-memory ───────────────────────────────────────────


class TestADisconnectPurgesTheMemoryOfTheMailbox:

    async def test_a_204_deletes_each_memory_of_the_mailbox_over_pages(
        self, wired, mem0,  # noqa: F811
    ):
        wired({ACC: _row()})
        m = mem0(_seeded())
        assert await accounts.delete_account(ACC, user=_user()) is None
        await _drain()
        assert m.fake.left(MAILBOX) == 0, "a memory of the mailbox is left"
        assert len(m.fake.deleted) == 250
        pages = [r for r in m.fake.reads if r[0] == MAILBOX]
        # 250 rows at 100 a page: three pages and one empty read, then the
        # second pass reads once and finds nothing.
        assert len(pages) == 5
        assert all(top_k == mem0_client.DELETE_SCOPE_PAGE == 100 and expired
                   for _s, top_k, expired in pages)
        assert {r[0] for r in m.fake.reads} == {MAILBOX}, "it read another scope"
        assert _purged(250, 0) in m.log.lines

    async def test_it_deletes_no_memory_of_another_scope(self, wired, mem0):  # noqa: F811
        wired({ACC: _row()})
        seeded = _seeded()
        m = mem0(seeded)
        await accounts.delete_account(ACC, user=_user())
        await _drain()
        for scope, n in seeded.items():
            if scope != MAILBOX:
                assert m.fake.left(scope) == n, f"the purge touched {scope}"

    async def test_the_owner_is_the_member_in_any_case(self, wired, mem0):  # noqa: F811
        # The writers key on the lower-case member, so the purge does too.
        wired({ACC: _row(user_id=OWNER.upper())})
        m = mem0(_seeded())
        await accounts.delete_account(ACC, user=_user(OWNER.upper()))
        await _drain()
        assert m.fake.left(MAILBOX) == 0

    @pytest.mark.parametrize("path_id", [ACC.upper(), ACC.replace("-", ""),
                                         "{" + ACC + "}"],
                             ids=["capitals", "no-hyphens", "braces"])
    async def test_a_path_id_in_another_form_purges_the_canonical_key(
        self, wired, mem0, path_id,  # noqa: F811
    ):
        # Postgres reads each of these forms as the same uuid, so the DELETE
        # finds the row. The writers keyed Mem0 on the canonical form.
        wired({path_id: _row()})
        m = mem0(_seeded())
        assert await accounts.delete_account(path_id, user=_user()) is None
        await _drain()
        assert m.fake.left(MAILBOX) == 0, "the purge missed the canonical key"
        assert {r[0] for r in m.fake.reads} == {MAILBOX}

    async def test_the_purge_starts_after_the_delete_block_closes(
        self, wired, monkeypatch,  # noqa: F811
    ):
        ledger = wired({ACC: _row()})

        def _record(owner: str, account_id: str) -> None:
            ledger.events.append(("purge", ledger.open, owner, account_id))

        monkeypatch.setattr(accounts, "schedule_mailbox_memory_purge", _record)
        await accounts.delete_account(ACC, user=_user())
        purge = ledger.first("purge")
        assert purge == ("purge", 0, OWNER, ACC), "the purge ran inside a block"
        closes = [i for i, e in enumerate(ledger.events) if e[0] == "close"]
        assert ledger.index("purge") > ledger.index("delete")
        assert ledger.index("purge") > closes[-1], "the purge ran before the commit"


# ── email-purge-second-pass ──────────────────────────────────────────────────


class TestTheSecondPass:

    async def test_a_late_add_between_the_passes_is_deleted(self, wired, mem0):  # noqa: F811
        async def _late_add(fake: _FakeMem0) -> None:
            # A background add of the drafter lands after the first pass.
            fake.seed(MAILBOX, f"{SECRET} late")

        wired({ACC: _row()})
        m = mem0(_seeded(), between=_late_add)
        await accounts.delete_account(ACC, user=_user())
        await _drain()
        assert m.delays == [memory_purge.SECOND_PASS_DELAY_S]
        assert m.fake.left(MAILBOX) == 0, "the late add survived the purge"
        assert _purged(250, 1) in m.log.lines

    def test_the_passes_are_two_minutes_apart(self):
        assert memory_purge.SECOND_PASS_DELAY_S == 120.0

    async def test_a_failed_first_pass_still_runs_the_second(self, mem0):
        m = mem0({MAILBOX: 7}, fail="first-read")
        assert await memory_purge.purge_mailbox_memory(OWNER, ACC) is None
        assert m.fake.left(MAILBOX) == 0
        assert _failed("first") in m.log.lines
        assert not [ln for ln in m.log.lines if ln[1] == "email.disconnect.memory_purged"]


# ── email-purge-refuses-bare-scope ───────────────────────────────────────────


class TestThePurgeRefusesTheBareScope:

    @pytest.mark.parametrize("owner,account_id", [
        (OWNER, ""), (OWNER, "   "), (OWNER, None), (OWNER, "acc-1"),
        ("", ACC), ("  ", ACC),
    ], ids=["no-account", "blank-account", "none-account", "not-a-uuid",
            "no-owner", "blank-owner"])
    async def test_a_key_with_no_mailbox_deletes_nothing(
        self, mem0, owner, account_id,
    ):
        m = mem0(_seeded())
        assert await memory_purge.purge_mailbox_memory(owner, account_id) is None
        assert m.fake.reads == [] and m.fake.deleted == [], "it read or deleted"
        assert [line[1] for line in m.log.lines] == [
            "email.disconnect.memory_purge_refused"]

    def test_the_scope_helper_raises_and_builds_the_canonical_key(self):
        with pytest.raises(memory_purge.ScopeRefused):
            memory_purge.mailbox_memory_scope(OWNER, "")
        assert memory_purge.mailbox_memory_scope(OWNER, ACC.upper()) == MAILBOX
        assert memory_purge.mailbox_memory_scope(OWNER, uuid.UUID(ACC)) == MAILBOX

    @pytest.mark.parametrize("bad", [
        "", "  ", "*", " * ", None, 7, {"in": [OWNER]}, [MAILBOX],
    ], ids=["empty", "blank", "wildcard", "padded-wildcard", "none", "int",
            "dict", "list"])
    async def test_delete_scope_refuses_before_any_read(self, mem0, bad):
        m = mem0(_seeded())
        with pytest.raises(ValueError):
            await m.client.delete_scope(bad)
        assert m.fake.reads == [] and m.fake.deleted == []


# ── email-memory-key-canonical ───────────────────────────────────────────────


class TestTheKeyIsCanonical:

    @pytest.mark.parametrize("given", [
        ACC, ACC.upper(), ACC.replace("-", ""), "{" + ACC + "}", f"  {ACC.upper()}  ",
    ], ids=["canonical", "capitals", "no-hyphens", "braces", "padded"])
    def test_each_form_of_one_id_gives_one_key(self, given):
        assert email_memory_scope(OWNER, given) == MAILBOX
        assert memory_purge.mailbox_memory_scope(OWNER, given) == MAILBOX

    def test_an_id_that_is_not_a_uuid_stays_as_it_is(self):
        assert email_memory_scope("me@acme.com", "acc-1") == "me@acme.com#acct:acc-1"
        assert email_memory_scope("me@acme.com", "ACC-1") == "me@acme.com#acct:ACC-1"


# ── email-purge-page-cap ─────────────────────────────────────────────────────


class TestThePageCap:

    async def test_a_scope_that_never_empties_raises_and_does_not_hang(self, mem0):
        m = mem0({}, endless=True)
        with pytest.raises(RuntimeError, match="pages"):
            await asyncio.wait_for(m.client.delete_scope(MAILBOX), timeout=30)
        assert len(m.fake.reads) == mem0_client.DELETE_SCOPE_MAX_PAGES

    def test_the_cap_is_a_thousand_pages(self):
        assert mem0_client.DELETE_SCOPE_MAX_PAGES == 1000


# ── email-purge-strong-reference ─────────────────────────────────────────────


class TestTheStrongReference:

    async def test_the_set_holds_the_task_while_it_runs(self, mem0):
        gate = asyncio.Event()

        async def _hold(_fake: _FakeMem0) -> None:
            await gate.wait()

        m = mem0({MAILBOX: 3}, between=_hold)
        task = memory_purge.schedule_mailbox_memory_purge(OWNER, ACC)
        for _ in range(5):
            await asyncio.sleep(0)
        assert not task.done(), "the task should wait between the passes"
        assert task in memory_purge._PURGES, "nothing holds the running purge"
        gate.set()
        assert await asyncio.wait_for(task, timeout=10) == 3
        await asyncio.sleep(0)
        assert task not in memory_purge._PURGES, "a finished purge kept its task"
        assert m.fake.left(MAILBOX) == 0


# ── email-purge-after-delete ─────────────────────────────────────────────────


class TestNoPurgeWithoutADelete:

    @pytest.mark.parametrize("rows,delete_fails,status", [
        ({}, None, 404),
        ({ACC: _row(user_id="other@em-t4f.test")}, None, 404),
        ({ACC: _row()}, "lock", 409),
    ], ids=["no-such-mailbox", "another-members-mailbox", "lock-timeout"])
    async def test_a_refused_disconnect_makes_no_purge_call(
        self, wired, mem0, rows, delete_fails, status,  # noqa: F811
    ):
        fails = _lock_timeout_error() if delete_fails == "lock" else None
        wired(rows, delete_fails=fails)
        m = mem0(_seeded())
        with pytest.raises(HTTPException) as err:
            await accounts.delete_account(ACC, user=_user())
        assert err.value.status_code == status
        await _drain()
        assert m.fake.reads == [] and m.fake.deleted == []
        assert not _purges_here()

    async def test_a_failed_delete_makes_no_purge_call(self, wired, mem0):  # noqa: F811
        wired({ACC: _row()}, delete_fails=RuntimeError("connection reset"))
        m = mem0(_seeded())
        with pytest.raises(RuntimeError):
            await accounts.delete_account(ACC, user=_user())
        await _drain()
        assert m.fake.reads == [] and m.fake.deleted == []

    @pytest.mark.parametrize("fail", ["read", "delete"])
    async def test_a_failed_purge_still_gives_204_and_logs_no_memory_text(
        self, wired, mem0, fail,  # noqa: F811
    ):
        rows = {ACC: _row()}
        ledger = wired(rows)
        m = mem0(_seeded(), fail=fail)
        assert await accounts.delete_account(ACC, user=_user()) is None
        assert ACC not in rows, "the mailbox was not deleted"
        await _drain()
        assert _failed("first") in m.log.lines
        assert _failed("second") in m.log.lines
        assert SECRET not in repr(m.log.lines) + repr(ledger.logs)
        assert OWNER not in repr(m.log.lines), "a purge log holds the address"

    async def test_a_delete_with_no_effect_ends_as_a_failure(self, wired, mem0):  # noqa: F811
        wired({ACC: _row()})
        m = mem0(_seeded(), stuck=True)
        assert await accounts.delete_account(ACC, user=_user()) is None
        await _drain()
        assert _failed("first") in m.log.lines

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
                  age: str, whole_second: bool = False) -> str:
    """One mailbox. ``whole_second`` gives a ``created_at`` with zero
    microseconds, which a plain ``isoformat()`` prints with no fraction."""
    at = ("date_trunc('second', now()) - CAST(:age AS interval)" if whole_second
          else "now() - CAST(:age AS interval)")
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, is_default, created_at, organization_id) "
            f"VALUES (:u, 'microsoft', :m, 'blob-r8', :d, {at}, "
            "CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@em-t8f.test",
             "d": default, "age": age, "o": org}).scalar_one())


def _seed_sent(admin, *, org: str, account_id: str) -> None:
    """One sent mail, so the writing-style route has a sample."""
    with admin.begin() as c:
        c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, folder, "
            "from_address, to_addresses, subject, body_text, received_at, "
            "organization_id) VALUES (CAST(:a AS uuid), :pm, 'sent', "
            "CAST(:f AS jsonb), CAST(:t AS jsonb), 'Re: plan', "
            "'Thanks, that works for me. Talk soon.', now(), CAST(:o AS uuid))"),
            {"a": account_id, "pm": f"pm-{uuid.uuid4().hex[:12]}",
             "f": json.dumps({"email": "me@em-t8f.test"}),
             "t": json.dumps([{"email": "you@outside.test"}]), "o": org})


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

    @pytest.mark.parametrize("form", ["canonical", "capitals"])
    async def test_the_real_delete_purges_the_mailbox_of_the_row_owner(
        self, promoted, app_engine, r8_doubles, mem0, form,  # noqa: F811
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
        path_id = acc if form == "canonical" else acc.upper()
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                assert await accounts.delete_account(path_id, user=user) is None
                await _drain()
            assert m.fake.left(scope) == 0
            assert m.fake.left(owner) == 2, "the purge took the bare scope"
        finally:
            release_tenant(token)
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"),
                          {"u": owner})

    async def test_a_writer_given_capitals_writes_the_key_that_the_purge_deletes(
        self, promoted, app_engine, mem0, monkeypatch,  # noqa: F811
    ):
        """``email-memory-key-canonical`` (review round 2, F1). The
        writing-style route takes the account id from the request. Postgres
        finds the row for the id in capitals. Before round 2 the route keyed
        Mem0 on the capitals, and the purge deleted another key."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-t8f.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner,
                            default=True, age="1 hour")
        _seed_sent(p.admin_engine, org=p.org_b, account_id=acc)
        scope = f"{owner}#acct:{acc}"
        m = mem0({})

        async def _style(_samples: list[str]) -> str:
            return "Short and warm."

        monkeypatch.setattr(assistant_mod, "_llm_writing_style", _style)
        user = UserContext(email=owner, role=UserRole.EMPLOYEE,
                           organization_id=p.org_b)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                out = await assistant_mod.generate_writing_style(
                    account_id=acc.upper(), user=user)
            assert out == {"writing_style": "Short and warm."}
            assert m.fake.added == [scope], "the writer keyed Mem0 on the request id"
            assert await memory_purge.purge_mailbox_memory(owner, acc.upper()) == 1
            assert m.fake.rows == {}, "a memory of the mailbox is left"
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
        of microseconds. ``old`` has zero microseconds on purpose."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-t8f.test"
        old = _seed_account(p.admin_engine, org=p.org_b, owner=owner,
                            default=True, age="2 days", whole_second=True)
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
            assert want[old].endswith(".000000+00:00"), "the seed has a fraction"
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
