"""EM-G11f2 — a durable Mem0 purge, no write to a key no purge finds, and a
fence on each child of a mailbox.

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.13.9, EM-G11f2
items 1 to 3.

R7 fences named here:

* ``email-purge-row-with-delete``: ``DELETE /email/accounts/{id}`` writes the
  ``email_mailbox_purges`` row (migration 264) in the SAME block as its
  ``DELETE``. A 404, a 409 or a failed ``DELETE`` writes none. On a real
  database the row stays after a stop that comes before the task runs.
* ``email-purge-sweeper``: the sweeper runs ONE pass for each due row. A
  failed pass keeps the row open with a back-off. The first good pass keeps
  it open. Only the second good pass closes it. A disconnect whose task never
  ran ends with an empty Mem0 scope after the sweeps.
* ``email-purge-mem0-off``: with Mem0 off the sweeper reads no row, and the
  task runs no pass and leaves the row open.
* ``email-no-default-scope``: the drafter writes Mem0 only under the key that
  ``memory_purge.mailbox_memory_scope`` builds. With no mailbox it refuses.
* ``email-child-fence``: each foreign key that points at ``email_accounts`` or
  ``email_messages`` comes from ``information_schema``. The test fills one row
  in each child, disconnects, and asserts that each child holds no row of the
  mailbox. A new child table joins the test with no edit.

**R8.** The real SQL on the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, as the role ``acb_app_h3rls``
(NOSUPERUSER, NOBYPASSRLS). Mem0 is the ``_FakeMem0`` of
``test_email_disconnect_memory_purge``.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_mailbox_purge_durable.py -v -rs
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from acb_memory import mem0_client
from fastapi import HTTPException
from gateway.routes.email import memory_purge
from gateway.routes.email.automation import drafting
from gateway.routes.email.transport import accounts
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``wired``, ``r8_doubles``, ``mem0``, ``promoted`` and ``app_engine`` are
# fixtures, used by name, so the imports are load-bearing.
from tests.unit.test_email_disconnect_memory_purge import (  # noqa: F401
    _drain,
    mem0,
)
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

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"
ACC = "7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"


async def _mem0_off() -> bool:
    return False


async def _mem0_on() -> bool:
    return True


def _never_built() -> None:
    raise AssertionError("the probe built the client with the flag off")


def _migration() -> str:
    """Migration 264, found by CONTENT and never by number (R1)."""
    hits = [p for p in _MIGRATIONS.glob("[0-9]*_*.sql")
            if "CREATE TABLE IF NOT EXISTS email_mailbox_purges"
            in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, f"expected one migration of the table, got {hits}"
    return hits[0].read_text(encoding="utf-8")


# ── the migration ────────────────────────────────────────────────────────────


class TestTheMigration:

    def test_the_table_has_no_foreign_key_to_the_mailbox(self):
        body = _migration()
        create = body.split("CREATE TABLE IF NOT EXISTS email_mailbox_purges", 1)[1]
        create = create.split(");", 1)[0]
        assert "REFERENCES email_accounts" not in create, (
            "a foreign key to email_accounts lets the cascade delete the row")
        assert "REFERENCES organization (id) ON DELETE CASCADE" in create

    def test_the_table_is_tenant_scoped_with_force_rls(self):
        body = _migration()
        assert "ALTER TABLE email_mailbox_purges FORCE  ROW LEVEL SECURITY" in body
        assert "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)" in body
        policies = (_MIGRATIONS / "generated" / "04_policies.sql").read_text(
            encoding="utf-8")
        assert "ALTER TABLE email_mailbox_purges FORCE  ROW LEVEL SECURITY" in policies


# ── email-purge-row-with-delete (hermetic) ───────────────────────────────────


class TestThePurgeRowLandsWithTheDelete:

    async def test_the_purge_row_lands_with_the_delete(self, wired, monkeypatch):  # noqa: F811
        ledger = wired({ACC: _row()})
        monkeypatch.setattr(accounts, "schedule_mailbox_memory_purge",
                            lambda *a, **k: None)
        assert await accounts.delete_account(ACC, user=_user()) is None
        kinds = ledger.kinds()
        delete, row = kinds.index("delete"), kinds.index("purge_row")
        assert row > delete, "the purge row came before the DELETE"
        assert "close" not in kinds[delete:row] and "open" not in kinds[delete:row], (
            "the purge row is not in the transaction of the DELETE")
        assert ledger.events[row] == ("purge_row", 1, OWNER, ACC)

    @pytest.mark.parametrize("rows,fails,status", [
        ({}, None, 404),
        ({ACC: _row(user_id="other@em-t4f.test")}, None, 404),
        ({ACC: _row()}, "lock", 409),
    ], ids=["no-such-mailbox", "another-members-mailbox", "lock-timeout"])
    async def test_a_refused_disconnect_writes_no_purge_row(
        self, wired, rows, fails, status,  # noqa: F811
    ):
        ledger = wired(rows, delete_fails=_lock_timeout_error() if fails else None)
        with pytest.raises(HTTPException) as err:
            await accounts.delete_account(ACC, user=_user())
        assert err.value.status_code == status
        assert "purge_row" not in ledger.kinds()

    async def test_with_mem0_off_the_task_leaves_the_row_open(
        self, wired, monkeypatch,  # noqa: F811
    ):
        ledger = wired({ACC: _row()})
        monkeypatch.setattr(memory_purge, "mem0_on", _mem0_off)
        await accounts.delete_account(ACC, user=_user())
        await _drain()
        assert "purge_row" in ledger.kinds()
        assert "purge_done" not in ledger.kinds(), "a purge that never ran closed"

    async def test_two_good_passes_close_the_row(self, wired, mem0):  # noqa: F811
        ledger = wired({ACC: _row()})
        mem0({f"{OWNER}#acct:{ACC}": 3})
        await accounts.delete_account(ACC, user=_user())
        await _drain()
        assert ("purge_done", 1, ACC) in ledger.events

    async def test_a_failed_pass_leaves_the_row_open(self, wired, mem0):  # noqa: F811
        ledger = wired({ACC: _row()})
        mem0({f"{OWNER}#acct:{ACC}": 3}, fail="read")
        await accounts.delete_account(ACC, user=_user())
        await _drain()
        assert "purge_done" not in ledger.kinds()


# ── email-purge-mem0-off and the back-off (hermetic) ─────────────────────────


class TestTheSweeperIsCheapWithMem0Off:

    async def test_no_database_read_with_mem0_off(self, monkeypatch):
        opened: list[Any] = []

        def _tripwire(*a: Any) -> Any:
            opened.append(a)
            raise AssertionError("the sweeper opened a session with Mem0 off")

        async def _no_orgs() -> list[str]:
            opened.append("org list")
            raise AssertionError("the tick read the organizations with Mem0 off")

        monkeypatch.setattr(memory_purge, "mem0_on", _mem0_off)
        monkeypatch.setattr(memory_purge, "_organization_ids", _no_orgs)
        assert await memory_purge.sweep_mailbox_purges(
            [str(uuid.uuid4())], open_session=_tripwire) == 0
        assert await memory_purge.run_tick() == 0
        assert opened == []

    async def test_the_probe_reads_the_flag(self, monkeypatch):
        client = mem0_client.MemoryClient()
        monkeypatch.setattr(mem0_client, "get_memory_client", lambda: client)
        monkeypatch.setattr(memory_purge, "_mem0_flag", lambda: False)
        monkeypatch.setattr(client, "_get_client", _never_built)
        assert await memory_purge.mem0_on() is False

    async def test_a_failed_build_runs_off_the_loop_and_waits(self, monkeypatch):
        client = mem0_client.MemoryClient()
        builds: list[int] = []
        loop_thread = threading.get_ident()

        def _build() -> None:
            builds.append(threading.get_ident())
            return None

        monkeypatch.setattr(mem0_client, "get_memory_client", lambda: client)
        monkeypatch.setattr(memory_purge, "_mem0_flag", lambda: True)
        monkeypatch.setattr(client, "_get_client", _build)
        monkeypatch.setattr(memory_purge, "_mem0_failed_at", None)
        assert await memory_purge.mem0_on() is False
        assert await memory_purge.mem0_on() is False
        assert len(builds) == 1, "a failed build ran again inside the back-off"
        assert builds[0] != loop_thread, "the build ran on the event loop"
        monkeypatch.setattr(memory_purge, "_mem0_failed_at",
                            memory_purge._mem0_failed_at - memory_purge.MEM0_RETRY_S - 1)
        assert await memory_purge.mem0_on() is False
        assert len(builds) == 2, "the build never ran again after the back-off"

    def test_the_back_off_grows_and_has_a_cap(self):
        delays = [memory_purge.retry_delay_s(n) for n in range(1, 12)]
        assert delays[0] == memory_purge.RETRY_BASE_S
        assert delays == sorted(delays)
        assert max(delays) == memory_purge.RETRY_CAP_S


# ── the lease: one row is claimed just before its own pass (hermetic) ───────


class _ClaimLedger:
    """A session double for the sweeper. It hands out ``rows`` one claim at a
    time and records the order of the claims, the passes and the records."""

    def __init__(self, rows: list[Any] | dict[str, list[Any]]) -> None:
        self.by_org = {k: list(v) for k, v in rows.items()} if isinstance(
            rows, dict) else {"org": list(rows)}
        self.events: list[str] = []
        #: The organization of each row that a claim handed out, in order.
        self.taken: list[str] = []

    def session(self, org: str) -> Any:
        ledger = self

        class _DB:
            async def execute(self, stmt: Any, params: dict[str, Any]) -> Any:
                sql = " ".join(str(stmt).split())
                if sql.startswith("UPDATE email_mailbox_purges p SET tries"):
                    assert "LIMIT 1" in sql, "a claim takes more than one row"
                    ledger.events.append("claim")
                    rows = ledger.by_org.get(org, [])
                    row = rows.pop(0) if rows else None
                    if row is not None:
                        ledger.taken.append(org)
                    return SimpleNamespace(fetchone=lambda: row)
                ledger.events.append("record")
                return SimpleNamespace(fetchone=lambda: None)

        @contextlib.asynccontextmanager
        async def _cm():
            yield _DB()

        return _cm()


def _due_row(passes: int = 0) -> Any:
    return SimpleNamespace(id=str(uuid.uuid4()), owner_email=OWNER,
                           account_id=str(uuid.uuid4()), passes=passes, tries=1)


class TestTheLeaseCoversOnePass:

    async def test_each_row_is_claimed_just_before_its_pass(self, monkeypatch):
        ledger = _ClaimLedger([_due_row(), _due_row(), _due_row()])

        async def _pass(_scope: str, _shown: str, _phase: str) -> int:
            ledger.events.append("pass")
            return 0

        monkeypatch.setattr(memory_purge, "mem0_on", _mem0_on)
        monkeypatch.setattr(memory_purge, "_one_pass", _pass)
        await memory_purge.sweep_mailbox_purges(["org"], open_session=ledger.session)
        assert ledger.events == ["claim", "pass", "record"] * 3 + ["claim"]

    async def test_a_spent_budget_claims_no_row(self, monkeypatch):
        ledger = _ClaimLedger([_due_row()])
        monkeypatch.setattr(memory_purge, "mem0_on", _mem0_on)
        assert await memory_purge.sweep_mailbox_purges(
            ["org"], open_session=ledger.session, budget_s=0) == 0
        assert ledger.events == []

    def test_one_pass_fits_inside_the_lease(self):
        assert memory_purge.SWEEP_PASS_BOUND_S * 2 < memory_purge.SWEEP_LEASE_S

    async def test_a_backlog_in_one_org_does_not_starve_the_next(self, monkeypatch):
        """Fix round 2. Org A holds 50 due rows and org B one. Each pass takes
        100 s of a 350 s budget, so the tick runs about four passes. Org B's
        row is among them."""
        ledger = _ClaimLedger({"org-a": [_due_row() for _ in range(50)],
                               "org-b": [_due_row()]})
        clock = [0.0]

        async def _slow_pass(_scope: str, _shown: str, _phase: str) -> int:
            clock[0] += 100.0
            return 0

        monkeypatch.setattr(memory_purge, "mem0_on", _mem0_on)
        monkeypatch.setattr(memory_purge, "_one_pass", _slow_pass)
        monkeypatch.setattr(memory_purge, "time",
                            SimpleNamespace(monotonic=lambda: clock[0]))
        await memory_purge.sweep_mailbox_purges(
            ["org-a", "org-b"], open_session=ledger.session, budget_s=350.0)
        assert len(ledger.taken) == 4, ledger.taken
        assert "org-b" in ledger.taken, "the backlog of org A starved org B"
        assert ledger.taken[:2] == ["org-a", "org-b"], "the orgs did not take turns"


class TestTheDetachedRowsTakeATurn:
    """Rule 9 (EM-G11f1c, §12.3.13.9.2). The detached table is one more lane
    of the same sweep, after the organizations of each round."""

    async def test_the_detached_lane_comes_after_the_orgs(self, monkeypatch):
        ledger = _ClaimLedger({"org-a": [_due_row(), _due_row()]})
        detached = [_due_row()]

        def _open_detached() -> Any:
            class _DB:
                async def execute(self, stmt: Any, params: dict[str, Any]) -> Any:
                    sql = " ".join(str(stmt).split())
                    assert "email_mailbox_purges" not in sql, (
                        "the detached lane touched a tenant table")
                    if "public.claim_detached_purge(" in sql:
                        assert "org" not in params
                        ledger.events.append("claim")
                        row = detached.pop(0) if detached else None
                        if row is not None:
                            ledger.taken.append("detached")
                        return SimpleNamespace(fetchone=lambda: row)
                    assert sql.startswith("SELECT public.settle_detached_purge(")
                    ledger.events.append("record")
                    return SimpleNamespace(fetchone=lambda: None)

            @contextlib.asynccontextmanager
            async def _cm():
                yield _DB()

            return _cm()

        async def _pass(_scope: str, _shown: str, _phase: str) -> int:
            return 0

        monkeypatch.setattr(memory_purge, "mem0_on", _mem0_on)
        monkeypatch.setattr(memory_purge, "_one_pass", _pass)
        await memory_purge.sweep_mailbox_purges(
            ["org-a"], open_session=ledger.session, open_detached=_open_detached)
        assert ledger.taken == ["org-a", "detached", "org-a"]

    async def test_the_tick_sweeps_the_detached_rows(self, monkeypatch):
        seen: dict[str, Any] = {}

        async def _orgs() -> list[str]:
            return ["org-a"]

        async def _sweep(org_ids: Any, **kw: Any) -> int:
            seen.update(kw, orgs=list(org_ids))
            return 0

        monkeypatch.setattr(memory_purge, "mem0_on", _mem0_on)
        monkeypatch.setattr(memory_purge, "_organization_ids", _orgs)
        monkeypatch.setattr(memory_purge, "sweep_mailbox_purges", _sweep)
        await memory_purge.run_tick()
        assert seen["orgs"] == ["org-a"]
        assert seen.get("open_detached") is memory_purge._detached_session, (
            "the tick does not sweep the detached rows")


_MEMORY_PURGE = _REPO / "apps/services/gateway/gateway/routes/email/memory_purge.py"

#: The only functions that a statement of the detached store may call. Each is
#: a SECURITY DEFINER function of migration 276.
_DETACHED_FUNCTIONS = ("public.claim_detached_purge(", "public.settle_detached_purge(")


def _detached_session_violations(source: str) -> list[str]:
    """Fix round 1, item 2. The ONE unbound session of the sweeper is pinned:
    one reference outside its own body, in ``run_tick``, as ``open_detached``.
    Its body runs no statement. The lane that it opens uses the detached
    store only."""
    tree = ast.parse(source)
    problems = []
    refs: list[str] = []

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = owner
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if isinstance(child, ast.Name) and child.id == "_detached_session" \
                    and owner != "_detached_session":
                refs.append(owner)
            visit(child, name)

    visit(tree, "<module>")
    if refs != ["run_tick"]:
        problems.append(f"_detached_session is referenced from {refs}, not once from run_tick")
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_tick":
            kws = [k for c in ast.walk(node) if isinstance(c, ast.Call)
                   for k in c.keywords
                   if isinstance(k.value, ast.Name) and k.value.id == "_detached_session"]
            if [k.arg for k in kws] != ["open_detached"]:
                problems.append("run_tick passes _detached_session other than as open_detached")
        if (isinstance(node, ast.AsyncFunctionDef) and node.name == "_detached_session"
                and any(isinstance(c, ast.Attribute) and c.attr == "execute"
                        for c in ast.walk(node))):
            problems.append("_detached_session runs a statement itself")
        if isinstance(node, ast.FunctionDef) and node.name == "_detached_lane":
            if not any(isinstance(c, ast.Name) and c.id == "_DETACHED_STORE"
                       for c in ast.walk(node)):
                problems.append("the detached lane does not use the detached store")
            if any(isinstance(c, ast.Name) and c.id == "_TENANT_STORE"
                   for c in ast.walk(node)):
                problems.append("the detached lane uses the tenant store")
    return problems


def _detached_statement_violations(statements: dict[str, str]) -> list[str]:
    """Each statement of the detached store calls one migration-276 function
    and names no table: no FROM a table, no write, no other function."""
    problems = []
    for name, sql in statements.items():
        flat = " ".join(sql.split())
        calls = [fn for fn in _DETACHED_FUNCTIONS if fn in flat]
        if len(calls) != 1 or not flat.startswith("SELECT "):
            problems.append(f"{name} does not call exactly one detached function")
        rest = flat
        for fn in _DETACHED_FUNCTIONS:
            rest = rest.replace(fn, "")
        if re.search(r"\b(email_\w+|organization|INSERT|UPDATE|DELETE|JOIN)\b", rest):
            problems.append(f"{name} names a table or a write")
        if flat.count(" FROM ") > (1 if "claim" in name else 0):
            problems.append(f"{name} reads from a table")
    return problems


class TestTheUnboundSessionIsPinned:
    """Fix round 1, item 2 (security review). ``_detached_session`` is the
    one unbound session of the sweeper. It must stay one call site, and every
    statement it runs must call a SECURITY DEFINER function of migration 276
    and name no table."""

    def test_one_caller_and_function_calls_only(self):
        assert _detached_session_violations(
            _MEMORY_PURGE.read_text(encoding="utf-8")) == []
        store = memory_purge._DETACHED_STORE
        assert _detached_statement_violations({
            "claim": store.claim_sql, "pass_done": store.pass_done_sql,
            "pass_failed": store.pass_failed_sql, "refused": store.refused_sql,
        }) == []

    def test_the_check_can_fail(self):
        source = _MEMORY_PURGE.read_text(encoding="utf-8")
        tick = "    return await sweep_mailbox_purges(await _organization_ids(),\n"
        assert tick in source
        second = source.replace(
            "async def _organization_ids() -> list[str]:\n",
            "async def _organization_ids() -> list[str]:\n    _ = _detached_session\n")
        assert "not once from run_tick" in _detached_session_violations(second)[0]
        wrong_store = source.replace(
            "    return _Lane(DETACHED_LANE, _DETACHED_STORE, open_detached,",
            "    return _Lane(DETACHED_LANE, _TENANT_STORE, open_detached,")
        assert "the detached lane uses the tenant store" in (
            _detached_session_violations(wrong_store))
        assert _detached_statement_violations({
            "claim": "SELECT owner_email FROM email_detached_purges",
            "refused": "UPDATE email_detached_purges SET state = 'refused'",
            "pass_done": "SELECT public.settle_detached_purge(:id) FROM organization",
        }) == ["claim does not call exactly one detached function",
               "claim names a table or a write",
               "refused does not call exactly one detached function",
               "refused names a table or a write",
               "pass_done names a table or a write",
               "pass_done reads from a table"]


class TestOneMem0BuildAtATime:

    async def test_two_callers_at_once_build_one_client(self, monkeypatch):
        """Fix round 2. Two callers that both see no client must not each run
        ``Memory.from_config``. The second one waits for the first build."""
        client = mem0_client.MemoryClient()
        builds: list[int] = []

        def _build() -> object:
            builds.append(1)
            time.sleep(0.2)
            client._client = object()
            return client._client

        monkeypatch.setattr(mem0_client, "get_memory_client", lambda: client)
        monkeypatch.setattr(memory_purge, "_mem0_flag", lambda: True)
        monkeypatch.setattr(client, "_get_client", _build)
        monkeypatch.setattr(memory_purge, "_mem0_failed_at", None)
        answers = await asyncio.gather(memory_purge.mem0_on(), memory_purge.mem0_on())
        assert answers == [True, True]
        assert builds == [1], f"{len(builds)} builds ran at once"

    async def test_a_failed_build_is_not_repeated_by_the_waiter(self, monkeypatch):
        client = mem0_client.MemoryClient()
        builds: list[int] = []

        def _build() -> None:
            builds.append(1)
            time.sleep(0.2)
            return None

        monkeypatch.setattr(mem0_client, "get_memory_client", lambda: client)
        monkeypatch.setattr(memory_purge, "_mem0_flag", lambda: True)
        monkeypatch.setattr(client, "_get_client", _build)
        monkeypatch.setattr(memory_purge, "_mem0_failed_at", None)
        answers = await asyncio.gather(memory_purge.mem0_on(), memory_purge.mem0_on())
        assert answers == [False, False]
        assert builds == [1], "the waiter built again inside the back-off"


# ── the loop starts from the lifespan (hermetic) ────────────────────────────


_MAIN = _REPO / "apps/services/gateway/gateway/main.py"


def _lifespan_violations(source: str) -> list[str]:
    """What is wrong with how the lifespan runs the purge loop."""
    head, sep, tail = source.replace("\r\n", "\n").partition("\n    yield\n")
    if not sep:
        return ["the lifespan has no yield"]
    problems = []
    if "start_purge_loop" not in head:
        problems.append("the lifespan does not start the purge loop")
    if "stop_purge_loop" not in tail:
        problems.append("the shutdown does not stop the purge loop")
    return problems


class TestTheLoopStartsFromTheLifespan:

    def test_the_lifespan_starts_and_stops_the_loop(self):
        assert _lifespan_violations(_MAIN.read_text(encoding="utf-8")) == []

    def test_the_check_can_fail(self):
        source = _MAIN.read_text(encoding="utf-8").replace("start_purge_loop", "x")
        assert _lifespan_violations(source) == [
            "the lifespan does not start the purge loop"]

    def test_the_rollover_no_longer_hosts_the_sweeper(self):
        calendar = _REPO / "apps/services/gateway/gateway/routes/tasks/calendar.py"
        assert "memory_purge" not in calendar.read_text(encoding="utf-8")

    async def test_the_loop_ticks_and_stops(self, monkeypatch):
        ticks: list[int] = []

        async def _tick() -> int:
            ticks.append(1)
            return 0

        monkeypatch.setattr(memory_purge, "run_tick", _tick)
        assert await memory_purge.start_purge_loop() is True
        for _ in range(5):
            await asyncio.sleep(0)
        task = memory_purge._loop_task
        assert task is not None and not task.done()
        await memory_purge.stop_purge_loop()
        assert task.done() and memory_purge._loop_task is None
        assert ticks, "the loop never ticked"


# ── email-no-default-scope (hermetic) ────────────────────────────────────────

_GATEWAY = _REPO / "apps/services/gateway/gateway"
_EMAIL_ROUTES = _GATEWAY / "routes/email"
#: The Mem0 write calls. ``add`` is the method of a mem0 client.
_WRITE_NAMES = {"add_memories_background", "add_memories"}


def _add_calls(tree: ast.AST) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.Call]]:
    found = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", getattr(node.func, "attr", ""))
                    in _WRITE_NAMES):
                found.append((fn, node))
    return found


def _scope_violations(source: str) -> list[str]:
    """Each Mem0 add whose key does not come from
    ``memory_purge.writable_memory_scope``. Empty means correct."""
    tree = ast.parse(source)
    problems = []
    for fn, call in _add_calls(tree):
        arg = call.args[0] if call.args else None
        if not isinstance(arg, ast.Name):
            problems.append(f"{fn.name}: the key is not a checked name")
            continue
        made = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == arg.id for t in n.targets)
                and any(getattr(c.func, "id", None) == "writable_memory_scope"
                        for c in ast.walk(n.value) if isinstance(c, ast.Call))]
        if not made:
            problems.append(f"{fn.name}: {arg.id} skips writable_memory_scope")
    return problems


def _gateway_writers() -> dict[str, int]:
    """Each gateway file with a Mem0 write call, and the count of its calls."""
    out = {}
    for path in sorted(_GATEWAY.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not any(name in source for name in _WRITE_NAMES):
            continue
        n = len(_add_calls(ast.parse(source)))
        if n:
            out[path.relative_to(_GATEWAY).as_posix()] = n
    return out


class TestNoMemoryWriteToDefault:

    def test_no_memory_write_to_default(self):
        """Each Mem0 write of ``routes/email`` takes a checked mailbox key."""
        writers = {k: n for k, n in _gateway_writers().items()
                   if k.startswith("routes/email/")}
        assert writers == {"routes/email/automation/assistant.py": 1,
                           "routes/email/automation/drafting.py": 2}, (
            f"the email Mem0 writers moved: {writers}")
        for rel in writers:
            source = (_GATEWAY / rel).read_text(encoding="utf-8")
            assert _scope_violations(source) == [], rel
            assert not re.search(r'or\s+"default"', source), f"{rel}: 'default' is back"

    def test_no_writer_outside_email_writes_a_mailbox_key(self):
        """The Mem0 writes outside ``routes/email`` key on a member, an agent or
        a room. None of them builds a mailbox key, so no purge owes them."""
        outside = {k for k in _gateway_writers() if not k.startswith("routes/email/")}
        assert outside == {"main.py", "routes/agent.py"}, (
            f"a new Mem0 writer: {outside}. Check its key against the purge")
        tools = (_REPO / "packages/acb_skills/acb_skills/memory_tools.py")
        for path in [*(_GATEWAY / k for k in outside), tools]:
            source = path.read_text(encoding="utf-8")
            assert "email_memory_scope" not in source, path.name
            assert "#acct:" not in source and "ACCOUNT_SCOPE_MARK" not in source, path.name

    def test_the_check_can_fail(self):
        old = ("async def f(mem_scope):\n"
               "    await add_memories_background(mem_scope or 'default', [])\n")
        assert _scope_violations(old) == ["f: the key is not a checked name"]
        style = ("async def g(user, account_id):\n"
                 "    scope = email_memory_scope(user.email or 'default', account_id)\n"
                 "    await add_memories_background(scope, [])\n")
        assert _scope_violations(style) == ["g: scope skips writable_memory_scope"]

    def test_the_helper_refuses_a_key_with_no_mailbox(self):
        w = memory_purge.writable_memory_scope
        assert w("", ACC, "t") is None
        assert w(None, ACC, "t") is None
        assert w(OWNER, "", "t") is None
        assert w(OWNER, None, "t") is None
        assert w(OWNER, ACC.upper(), "t") == f"{OWNER}#acct:{ACC}"
        assert w("anonymous", ACC, "t") == f"anonymous#acct:{ACC}"

    async def test_a_draft_with_no_mailbox_writes_no_memory(self, monkeypatch):
        added: list[str] = []

        async def fake_add(uid: str, _messages: list, **_kw: Any) -> None:
            added.append(uid)

        async def fake_draft(*_a: Any, **_kw: Any) -> str:
            return "Hi,\n\nThat works."

        async def fake_plan(_email: dict, **_kw: Any) -> list:
            return []

        async def fake_remember(_q: str) -> str:
            return ""

        import acb_memory
        import acb_skills.memory_tools as mt
        monkeypatch.setattr(acb_memory, "add_memories_background", fake_add)
        monkeypatch.setattr(mt, "remember", fake_remember)
        monkeypatch.setattr(mt, "_set_memory_user_id", lambda _uid: None)
        monkeypatch.setattr(drafting, "_llm_draft_reply", fake_draft)
        monkeypatch.setattr(drafting, "_draft_consult_plan", fake_plan)
        email = {"from": "a@b.test", "subject": "s", "body": "b"}
        for account_id in ("", "not-a-uuid"):
            await drafting._orchestrate_draft(email, "", "", OWNER,
                                              account_id=account_id)
        await drafting._orchestrate_draft(email, "", "", OWNER, account_id=ACC)
        while drafting._BG_TASKS:
            await asyncio.gather(*list(drafting._BG_TASKS), return_exceptions=True)
        assert added == [f"{OWNER}#acct:{ACC}"], "a write landed under a key no purge finds"


# ── R8 ───────────────────────────────────────────────────────────────────────


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed")


def _seed_account(admin, *, org: str, owner: str) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, is_default, organization_id) "
            "VALUES (:u, 'imap', :m, 'blob-g8', true, CAST(:o AS uuid)) "
            "RETURNING id"),
            {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@em-g11f2.test",
             "o": org}).scalar_one())


def _purge_row(admin, account_id: str) -> Any:
    with admin.connect() as c:
        return c.execute(text(
            "SELECT organization_id, owner_email, state, passes, tries, last_error, "
            "next_try_at > now() AS waiting, done_at FROM email_mailbox_purges "
            "WHERE account_id = CAST(:a AS uuid)"), {"a": account_id}).first()


def _make_due(admin, account_id: str) -> None:
    with admin.begin() as c:
        c.execute(text("UPDATE email_mailbox_purges SET next_try_at = now() - "
                       "interval '1 second' WHERE account_id = CAST(:a AS uuid)"),
                  {"a": account_id})


def _wipe(admin, owner: str, account_id: str) -> None:
    with admin.begin() as c:
        c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"), {"u": owner})
        c.execute(text("DELETE FROM email_mailbox_purges "
                       "WHERE account_id = CAST(:a AS uuid)"), {"a": account_id})


async def _disconnect_with_no_task(p, monkeypatch, owner: str, acc: str) -> None:
    """A disconnect whose purge task never runs: the process stopped right
    after the commit."""
    monkeypatch.setattr(accounts, "schedule_mailbox_memory_purge",
                        lambda *a, **k: None)
    user = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
    app_dsn = p.app_url.render_as_string(hide_password=False)
    token = bind_tenant(p.org_b)
    try:
        async with tenant_engine_scope(app_dsn):
            assert await accounts.delete_account(acc, user=user) is None
    finally:
        release_tenant(token)


async def _sweep(p) -> int:
    app_dsn = p.app_url.render_as_string(hide_password=False)
    async with tenant_engine_scope(app_dsn):
        return await memory_purge.sweep_mailbox_purges([p.org_a, p.org_b])


@_DB_GATE
class TestTheDurablePurgeOnARealDatabase:

    async def test_the_row_survives_a_stop_after_the_commit(
        self, promoted, app_engine, r8_doubles, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-g11f2.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        try:
            await _disconnect_with_no_task(p, monkeypatch, owner, acc)
            row = _purge_row(p.admin_engine, acc)
            assert row is not None, "the DELETE committed with no purge row"
            assert str(row.organization_id) == p.org_b
            assert row.owner_email == owner and row.state == "open"
            assert row.passes == 0 and row.tries == 0
            assert row.waiting, "the sweeper may race the route's own task"
        finally:
            _wipe(p.admin_engine, owner, acc)

    async def test_the_sweeper_retries_a_failed_pass(
        self, promoted, app_engine, r8_doubles, mem0, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-g11f2.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        scope = f"{owner}#acct:{acc}"
        m = mem0({scope: 30, owner: 2}, fail="read")
        try:
            await _disconnect_with_no_task(p, monkeypatch, owner, acc)
            # Before the lease ends, the sweeper takes nothing.
            assert await _sweep(p) == 0 and m.fake.reads == []
            _make_due(p.admin_engine, acc)
            assert await _sweep(p) == 0
            row = _purge_row(p.admin_engine, acc)
            assert row.state == "open", "a failed pass closed the row"
            assert (row.tries, row.passes, row.last_error) == (1, 0, "pass_failed")
            assert row.waiting, "a failed pass has no back-off"
            # Mem0 works again. The first good pass keeps the row open.
            m.fake.fail = None
            _make_due(p.admin_engine, acc)
            assert await _sweep(p) == 0
            row = _purge_row(p.admin_engine, acc)
            assert (row.state, row.passes, row.tries) == ("open", 1, 2)
            assert m.fake.left(scope) == 0
            # A late add, then the second pass closes the row.
            m.fake.seed(scope, "late")
            _make_due(p.admin_engine, acc)
            assert await _sweep(p) == 1
            row = _purge_row(p.admin_engine, acc)
            assert (row.state, row.passes) == ("done", 2) and row.done_at is not None
            assert m.fake.left(scope) == 0 and m.fake.left(owner) == 2
            # A closed row is never taken again.
            _make_due(p.admin_engine, acc)
            reads = len(m.fake.reads)
            assert await _sweep(p) == 0 and len(m.fake.reads) == reads
        finally:
            _wipe(p.admin_engine, owner, acc)

    async def test_the_route_task_closes_its_row(
        self, promoted, app_engine, r8_doubles, mem0,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-g11f2.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        m = mem0({f"{owner}#acct:{acc}": 4})
        user = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                assert await accounts.delete_account(acc, user=user) is None
                await _drain()
            row = _purge_row(p.admin_engine, acc)
            assert (row.state, row.passes) == ("done", 2)
            assert m.fake.left(f"{owner}#acct:{acc}") == 0
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, owner, acc)

    async def test_a_refused_purge_row_rolls_the_delete_back(
        self, promoted, app_engine, r8_doubles, monkeypatch,  # noqa: F811
    ):
        """Atomicity. A trigger refuses each purge row. The DELETE is in the
        same transaction, so it rolls back, and the mailbox stays. With the
        INSERT in a block of its own, the DELETE commits first and the mailbox
        is gone."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-g11f2.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        with p.admin_engine.begin() as c:
            c.execute(text(
                "CREATE OR REPLACE FUNCTION g8_refuse_purge_row() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'purge row refused'; "
                "END $$"))
            c.execute(text(
                "CREATE TRIGGER g8_refuse_purge_row BEFORE INSERT ON "
                "email_mailbox_purges FOR EACH ROW "
                "EXECUTE FUNCTION g8_refuse_purge_row()"))
        try:
            with pytest.raises(Exception, match="purge row refused"):
                await _disconnect_with_no_task(p, monkeypatch, owner, acc)
            with p.admin_engine.connect() as c:
                kept = c.execute(text(
                    "SELECT count(*) FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                    {"a": acc}).scalar_one()
            assert kept == 1, "the DELETE committed without its purge row"
        finally:
            with p.admin_engine.begin() as c:
                c.execute(text("DROP TRIGGER IF EXISTS g8_refuse_purge_row "
                               "ON email_mailbox_purges"))
                c.execute(text("DROP FUNCTION IF EXISTS g8_refuse_purge_row()"))
            _wipe(p.admin_engine, owner, acc)

    async def test_the_app_role_sees_no_row_of_another_org(
        self, promoted, app_engine, r8_doubles, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-g11f2.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        try:
            await _disconnect_with_no_task(p, monkeypatch, owner, acc)
            with app_engine.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": p.org_a})
                seen = c.execute(text(
                    "SELECT count(*) FROM email_mailbox_purges "
                    "WHERE account_id = CAST(:a AS uuid)"), {"a": acc}).scalar_one()
            assert seen == 0, "org A reads a purge row of org B"
        finally:
            _wipe(p.admin_engine, owner, acc)


# ── email-child-fence (R8) ───────────────────────────────────────────────────

#: The foreign keys of each child, from information_schema.
_CHILDREN_SQL = """
SELECT DISTINCT kcu.table_name AS child, kcu.column_name AS col,
       ccu.table_name AS parent
  FROM information_schema.table_constraints tc
  JOIN information_schema.key_column_usage kcu
    ON kcu.constraint_name = tc.constraint_name
   AND kcu.table_schema = tc.table_schema
   AND kcu.table_name = tc.table_name
  JOIN information_schema.constraint_column_usage ccu
    ON ccu.constraint_name = tc.constraint_name
   AND ccu.constraint_schema = tc.table_schema
 WHERE tc.constraint_type = 'FOREIGN KEY'
   AND tc.table_schema = 'public'
   AND ccu.table_name IN ('email_accounts', 'email_messages')
 ORDER BY 1, 2"""

#: The same set from the catalog, so a wrong join above fails the test.
_CHILDREN_CATALOG_SQL = """
SELECT DISTINCT c.conrelid::regclass::text AS child, a.attname AS col,
       c.confrelid::regclass::text AS parent
  FROM pg_constraint c
  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
 WHERE c.contype = 'f'
   AND c.confrelid IN ('email_accounts'::regclass, 'email_messages'::regclass)
 ORDER BY 1, 2"""

#: Each column of one table, with its type, its default and its null rule.
_COLUMNS_SQL = """
SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,
       a.attnotnull AS notnull, (d.adbin IS NOT NULL OR a.attidentity <> ''
                                 OR a.attgenerated <> '') AS has_default
  FROM pg_attribute a
  LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
 WHERE a.attrelid = CAST(:t AS regclass) AND a.attnum > 0 AND NOT a.attisdropped
 ORDER BY a.attnum"""

#: Each single-column foreign key of one table, and its target table.
_FKS_SQL = """
SELECT a.attname AS col, c.confrelid::regclass::text AS parent,
       pa.attname AS parent_col
  FROM pg_constraint c
  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
  JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = c.confkey[1]
 WHERE c.contype = 'f' AND c.conrelid = CAST(:t AS regclass)"""

_CHECKS_SQL = """
SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint
 WHERE contype = 'c' AND conrelid = CAST(:t AS regclass)"""

#: The primary-key columns of one table.
_PK_SQL = """
SELECT a.attname FROM pg_index i
  JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
 WHERE i.indrelid = CAST(:t AS regclass) AND i.indisprimary"""

#: Each foreign key under a mailbox, at any depth, with its ON DELETE rule.
#: The walk starts at ``email_accounts`` and takes each table that points at a
#: table already in the set, so a grandchild such as ``email_actions`` joins.
_EDGES_SQL = """
WITH RECURSIVE under(tbl) AS (
    SELECT 'email_accounts'::regclass
    UNION
    SELECT c.conrelid::regclass FROM pg_constraint c JOIN under ON c.confrelid = under.tbl
     WHERE c.contype = 'f')
SELECT c.conrelid::regclass::text AS child, a.attname AS col,
       c.confrelid::regclass::text AS parent, c.confdeltype AS rule
  FROM pg_constraint c
  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
 WHERE c.contype = 'f' AND c.confrelid IN (SELECT tbl FROM under)
 ORDER BY 1, 2"""

#: The foreign keys under a mailbox that do NOT cascade, each with its reason.
#: Each table here ALSO cascades on ``account_id``, so the disconnect still
#: deletes its row. A new edge that does not cascade fails the fence until it
#: joins this map with a reason.
#: The children that take a row only on a SHARED mailbox (WS-65 SM-1,
#: ``shared_mailbox.md`` §4.4 and §5.2). The trigger
#: ``email_shared_mailbox_only`` refuses each other insert.
_SHARED_ONLY = frozenset({"email_mailbox_grants", "email_thread_assignments"})

_NOT_CASCADE: dict[tuple[str, str], str] = {
    ("email_executed_rules", "message_id"):
        "19_email_automation.sql: the rule history outlives a trashed mail, "
        "and the row cascades on account_id",
    ("email_executed_rules", "rule_id"):
        "19_email_automation.sql: the history outlives a deleted rule, and "
        "the row cascades on account_id",
    ("email_rule_guidance", "message_id"):
        "87_email_rule_guidance.sql: a correction outlives the mail that "
        "prompted it, and the row cascades on account_id",
    ("email_scheduled_sends", "draft_id"):
        "290_email_scheduled_sends.sql: a schedule outlives its draft row, so "
        "a deleted draft fails with a reason and a sent row keeps its "
        "history, and the row cascades on account_id",
}


def _edge_violations(edges: list[tuple[str, str, str, str]]) -> list[str]:
    """What is wrong with the delete rules under a mailbox. Empty is correct.

    Each edge cascades, or it is in ``_NOT_CASCADE`` and its table cascades on
    another edge. An entry of ``_NOT_CASCADE`` that matches no edge is stale.
    """
    problems = []
    cascades = {child for child, _c, _p, rule in edges if rule == "c"}
    for child, col, parent, rule in edges:
        if rule == "c":
            continue
        if (child, col) not in _NOT_CASCADE:
            problems.append(f"{child}.{col} -> {parent} is ON DELETE {rule}, not CASCADE")
        elif child not in cascades:
            problems.append(f"{child} has no CASCADE edge, so its row outlives the mailbox")
    for key in sorted(set(_NOT_CASCADE) - {(c, col) for c, col, _p, r in edges if r != "c"}):
        problems.append(f"stale _NOT_CASCADE entry {key}")
    return problems


def _check_literal(checks: list[str], col: str) -> str | None:
    """The first value that a CHECK of the table allows for ``col``."""
    for d in checks:
        hit = re.search(
            rf"\(?{re.escape(col)}\)?(?:::[a-z ]+)?\s*=\s*ANY\s*\(+ARRAY\['([^']*)'", d)
        if hit:
            return hit.group(1)
        hit = re.search(rf"\(?{re.escape(col)}\)?(?:::[a-z ]+)?\s*=\s*'([^']*)'", d)
        if hit:
            return hit.group(1)
    return None


def _value(typ: str, literal: str | None) -> tuple[str, Any]:
    """A SQL expression and a parameter for one synthetic value of ``typ``."""
    if literal is not None:
        return f"CAST(:{{p}} AS {typ})", literal
    t = typ.lower()
    if t.endswith("[]"):
        return f"CAST(:{{p}} AS {typ})", "{}"
    if t in ("jsonb", "json"):
        return f"CAST(:{{p}} AS {typ})", "{}"
    if t == "uuid":
        return "CAST(:{p} AS uuid)", str(uuid.uuid4())
    if t == "boolean":
        return "false", None
    if t.startswith(("integer", "smallint", "bigint", "numeric", "real", "double")):
        return f"CAST(1 AS {typ})", None
    if t.startswith(("timestamp", "date", "time ")):
        return f"CAST(now() AS {typ})", None
    if t == "interval":
        return "interval '1 hour'", None
    if t == "bytea":
        return "'\\x00'::bytea", None
    if t.startswith("vector("):
        dims = int(t[len("vector("):-1])
        return f"CAST(:{{p}} AS {typ})", "[" + ",".join(["0"] * dims) + "]"
    return f"CAST(:{{p}} AS {typ})", f"g8-{uuid.uuid4().hex[:10]}"


class _Seeder:
    """Fill one row in a table, as the superuser, from the catalog alone.

    A foreign key to ``email_accounts`` gets the mailbox, one to
    ``email_messages`` gets its message, and ``organization_id`` gets the org.
    Any other NOT NULL foreign key gets a parent row, made the same way.
    """

    def __init__(self, admin, org: str, ids: dict[str, str]) -> None:
        self.admin = admin
        self.org = org
        self.ids = ids

    def _meta(self, c, table: str) -> tuple[list[Any], dict[str, Any], list[str]]:
        cols = list(c.execute(text(_COLUMNS_SQL), {"t": table}).fetchall())
        fks = {r.col: r for r in c.execute(text(_FKS_SQL), {"t": table}).fetchall()}
        checks = [r[0] for r in c.execute(text(_CHECKS_SQL), {"t": table}).fetchall()]
        return cols, fks, checks

    def _fk_value(self, c, fk: Any, depth: int) -> Any:
        if fk.parent in self.ids:
            return self.ids[fk.parent]
        if fk.parent == "organization":
            return self.org
        return self.insert(c, fk.parent, depth + 1, returning=fk.parent_col)

    def _key(self, c, table: str, row: dict[str, Any]) -> dict[str, Any]:
        """The primary key of an inserted row, or the whole row with no key."""
        pk = [r[0] for r in c.execute(text(_PK_SQL), {"t": table}).fetchall()]
        return {k: row[k] for k in pk} if pk else row

    def insert(self, c, table: str, depth: int = 0, *, full: bool = False,
               returning: str | None = None) -> Any:
        assert depth < 4, f"the parent chain of {table} is too deep"
        cols, fks, checks = self._meta(c, table)
        names, exprs, params = [], [], {}
        for i, col in enumerate(cols):
            fk = fks.get(col.name)
            mine = fk is not None and fk.parent in self.ids
            if col.name == "organization_id" and fk is not None:
                value: Any = self.org
                expr = "CAST(:{p} AS uuid)"
            elif fk is not None and (col.notnull or mine):
                value = self._fk_value(c, fk, depth)
                expr = f"CAST(:{{p}} AS {col.type})"
            elif col.has_default or (not col.notnull and not full) or fk is not None:
                continue
            else:
                expr, value = _value(col.type, _check_literal(checks, col.name))
            names.append(f'"{col.name}"')
            exprs.append(expr.format(p=f"p{i}"))
            if value is not None:
                params[f"p{i}"] = value
        sql = (f'INSERT INTO "{table}" AS t ({", ".join(names)}) '
               f'VALUES ({", ".join(exprs)}) '
               + (f'RETURNING t."{returning}"' if returning else "RETURNING to_jsonb(t.*)"))
        result = c.execute(text(sql), params).scalar_one()
        return str(result) if returning else self._key(c, table, result)

    def fill(self, table: str) -> dict[str, Any]:
        """One row in ``table``, and its primary key. A CHECK that needs a NULL
        column set gets a second try that fills every column."""
        for full in (False, True):
            try:
                with self.admin.begin() as c:
                    return self.insert(c, table, full=full)
            except Exception as exc:
                if full:
                    raise AssertionError(
                        f"the fence cannot fill {table}: {type(exc).__name__}: "
                        f"{str(exc).splitlines()[0][:200]}") from None


@_DB_GATE
class TestEachChildIsEmptyAfterADisconnect:

    def test_the_two_reads_of_the_children_agree(self, promoted):  # noqa: F811
        with promoted.admin_engine.connect() as c:
            schema = {tuple(r) for r in c.execute(text(_CHILDREN_SQL)).fetchall()}
            catalog = {tuple(r) for r in c.execute(text(_CHILDREN_CATALOG_SQL)).fetchall()}
        assert schema == catalog
        assert ("email_messages", "account_id", "email_accounts") in schema
        assert not any(child == "email_mailbox_purges" for child, _c, _p in schema), (
            "the purge row has a foreign key, so the cascade deletes it")

    def test_each_edge_under_a_mailbox_cascades(self, promoted):  # noqa: F811
        """A child with ``ON DELETE SET NULL`` keeps its row after a
        disconnect, with a NULL key that no count by mailbox finds."""
        with promoted.admin_engine.connect() as c:
            edges = [tuple(r) for r in c.execute(text(_EDGES_SQL)).fetchall()]
        assert ("email_actions", "rule_id", "email_rules", "c") in edges, (
            "the walk did not reach a grandchild")
        assert _edge_violations(edges) == []

    def test_the_edge_check_can_fail(self):
        edges = [("email_rules", "account_id", "email_accounts", "c"),
                 ("tmp_child", "account_id", "email_accounts", "n"),
                 *[(c, col, "x", "n") for c, col in _NOT_CASCADE],
                 *[(c, "account_id", "email_accounts", "c") for c, _col in _NOT_CASCADE]]
        assert _edge_violations(edges) == [
            "tmp_child.account_id -> email_accounts is ON DELETE n, not CASCADE"]

    @pytest.mark.parametrize("how", ["disconnect", "member_purge", "org_purge"])
    async def test_each_child_is_empty_after_a_disconnect(
        self, promoted, app_engine, r8_doubles, monkeypatch, how,  # noqa: F811
    ):
        """EM-G11f1b (§12.3.13.9.2) adds the member purge as a second delete
        of a mailbox. PR-G7c adds the org purge, on an org of its own, and its
        purge row is a detached row that outlives the org."""
        from tests.unit.test_email_purge_wiring import (
            OP_TOKEN,
            detached_rows,
            drop_org,
            member_purge,
            org_purge,
            seed_member,
            seed_org,
            wipe,
        )

        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-g11f2.test"
        keeper = f"keeper-{uuid.uuid4().hex[:8]}@em-g11f2.test"
        org, slug = p.org_b, None
        if how == "member_purge":
            seed_member(p.admin_engine, p.org_b, keeper, owner=True)
            seed_member(p.admin_engine, p.org_b, owner)
        if how == "org_purge":
            org, slug = seed_org(p.admin_engine, uuid.uuid4().hex[:8])
        monkeypatch.setenv("GATEWAY_OPERATOR_TOKEN", OP_TOKEN)
        acc = _seed_account(p.admin_engine, org=org, owner=owner)
        with p.admin_engine.begin() as c:
            msg = str(c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, "
                "from_address, to_addresses, subject, organization_id) "
                "VALUES (CAST(:a AS uuid), :pm, CAST(:f AS jsonb), CAST(:t AS jsonb), "
                "'hello', CAST(:o AS uuid)) RETURNING id"),
                {"a": acc, "pm": f"pm-{uuid.uuid4().hex[:12]}",
                 "f": json.dumps({"email": "s@sender.test"}),
                 "t": json.dumps([{"email": "to@em-g11f2.test"}]),
                 "o": org}).scalar_one())
            children = list(c.execute(text(_CHILDREN_SQL)).fetchall())
        ids = {"email_accounts": acc, "email_messages": msg}
        seeder = _Seeder(p.admin_engine, org, ids)
        tables = sorted({r.child for r in children} - {"email_messages"})
        seeded = {table: seeder.fill(table)
                  for table in tables if table not in _SHARED_ONLY}
        # WS-65 SM-1: a trigger refuses a row of these tables on a private
        # mailbox. The seed marks the mailbox shared for their insert only,
        # then private again, so the delete still meets one row of each.
        with p.admin_engine.begin() as c:
            c.execute(text("UPDATE email_accounts SET shared_since = now() "
                           "WHERE id = CAST(:a AS uuid)"), {"a": acc})
        seeded.update({table: seeder.fill(table)
                       for table in tables if table in _SHARED_ONLY})
        with p.admin_engine.begin() as c:
            c.execute(text("UPDATE email_accounts SET shared_since = NULL "
                           "WHERE id = CAST(:a AS uuid)"), {"a": acc})
        seeded["email_messages"] = {"id": msg}

        def _counts() -> dict[tuple[str, str], int]:
            out = {}
            with p.admin_engine.connect() as c:
                for r in children:
                    out[(r.child, r.col)] = c.execute(text(
                        f'SELECT count(*) FROM "{r.child}" WHERE "{r.col}" = '
                        "CAST(:v AS uuid)"), {"v": ids[r.parent]}).scalar_one()
                # By the seeded row's own key too: a SET NULL child keeps its
                # row with a NULL key, and a count by mailbox misses it.
                for table, key in seeded.items():
                    out[(table, "<own key>")] = c.execute(text(
                        f'SELECT count(*) FROM "{table}" AS t WHERE to_jsonb(t.*) @> '
                        "CAST(:k AS jsonb)"), {"k": json.dumps(key, default=str)}).scalar_one()
            return out

        try:
            before = _counts()
            # Each child holds a row of the mailbox, through at least one key.
            for table in {r.child for r in children}:
                assert any(n for (t, col), n in before.items()
                           if t == table and col != "<own key>"), (
                    f"the seed put no row of the mailbox in {table}")
                assert before[(table, "<own key>")] == 1, f"the key of {table} finds no row"
            if how == "disconnect":
                await _disconnect_with_no_task(p, monkeypatch, owner, acc)
            elif how == "member_purge":
                await member_purge(p, owner)
            else:
                await org_purge(p, slug)
            after = _counts()
            left = {k: n for k, n in after.items() if n}
            assert left == {}, f"a child kept rows of the mailbox: {left}"
            if how == "org_purge":
                assert [str(r.account_id) for r in detached_rows(p.admin_engine, org)] == [
                    acc], "the org purge left no detached purge row"
            else:
                assert _purge_row(p.admin_engine, acc) is not None, (
                    "the cascade deleted the purge row")
        finally:
            _wipe(p.admin_engine, owner, acc)
            wipe(p.admin_engine, keeper, owner)
            if how == "org_purge":
                drop_org(p.admin_engine, org)
