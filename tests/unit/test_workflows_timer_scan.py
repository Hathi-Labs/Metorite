"""STU-E9a: the timer scan, with no database.

Spec: ``project-docs/specs/ai_studio.md`` §14 row E9a (task 2376). Code:
``routes/workflows/scheduler.scan_due_timers``, ``triggers.is_breach_trigger``,
``crud._validate_event_filter`` and ``crud._trigger_identity``, and the two
reads ``studio/objects_store.timer_of`` and ``due_timer_records``. The rule
grammar is in ``test_studio_rules.py``. The R8 half is
``test_workflows_timer_r8.py``.

**The fences, by name (R7).**

* Only the org loop of ``scan_due_waits`` calls the timer scan, after the list
  check, through ``_scan_studio_timers`` and after ``studio_allowed``:
  ``test_only_the_org_loop_of_the_wait_scan_calls_the_timer_scan``.
* The scan binds no tenant, never calls ``dispatch_event`` and adds no loop:
  ``test_the_timer_scan_binds_no_tenant_never_dispatches_and_adds_no_loop``.
* A record write never starts a timer trigger:
  ``test_dispatch_event_never_matches_a_breach_trigger``.
* The delivery key of the scan is the key that the store SQL leaves out:
  ``test_the_scan_key_is_the_key_that_the_store_sql_builds``.
* The payload holds ids and names, never a field value:
  ``test_the_payload_holds_no_field_value_and_a_duplicate_starts_nothing``.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.workflows import scheduler, triggers
from gateway.routes.workflows.crud import (
    TriggerSpec,
    _trigger_identity,
    _validate_event_filter,
    _validate_trigger_specs,
)
from gateway.routes.workflows.service import DuplicateDelivery, RunRejected
from gateway.studio import objects_store

_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"
_SCHEDULER = _GATEWAY / "routes" / "workflows" / "scheduler.py"

PKG = "6f0b2d3a-0000-4000-8000-0000000000e9"
ORG_A = "0d4b8f5e-0000-4000-8000-0000000000a1"
ORG_B = "0d4b8f5e-0000-4000-8000-0000000000b1"

_BREACH = {"package_id": PKG, "object": "claim", "op": "breach", "timer": "sla"}


def _cfg(**flt: Any) -> dict[str, Any]:
    return {"source": "studio", "filter": flt}


# ── The source fences ───────────────────────────────────────────────────────


def _tree() -> ast.Module:
    return ast.parse(_SCHEDULER.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"scheduler.py has no function {name}")


def _callee(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _parents(root: ast.AST) -> dict[ast.AST, ast.AST]:
    out: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(root):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _callers(name: str) -> list[tuple[str, str]]:
    """(file, enclosing function) for each call of *name* in the gateway."""
    out = []
    for path in _GATEWAY.rglob("*.py"):
        body = path.read_text(encoding="utf-8", errors="replace")
        if f"{name}(" not in body:
            continue
        for fn in ast.walk(ast.parse(body)):
            if isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef):
                for call in ast.walk(fn):
                    if isinstance(call, ast.Call) and _callee(call) == name:
                        out.append((path.name, fn.name))
    return out


def _chain_to(fn: ast.AST, call: ast.Call) -> list[ast.AST]:
    parents = _parents(fn)
    chain: list[ast.AST] = []
    node: ast.AST | None = call
    while node is not None and node is not fn:
        chain.append(node)
        node = parents.get(node)
    return chain


def test_only_the_org_loop_of_the_wait_scan_calls_the_timer_scan() -> None:
    """One path: the org loop of `scan_due_waits` calls `_scan_studio_timers`
    after the list check, and only that helper calls `scan_due_timers`, after
    `studio_flags.studio_allowed(org)` refused nothing. Mutation: drop the
    Studio check, or call the scan from another place, and this test goes red."""
    assert _callers("scan_due_timers") == [("scheduler.py", "_scan_studio_timers")]
    assert _callers("_scan_studio_timers") == [("scheduler.py", "scan_due_waits")]

    helper = _function(_tree(), "_scan_studio_timers")
    (call,) = [c for c in ast.walk(helper)
               if isinstance(c, ast.Call) and _callee(c) == "scan_due_timers"]
    gates = [n for n in ast.walk(helper) if isinstance(n, ast.If)
             and ast.unparse(n.test) == "not studio_flags.studio_allowed(org)"
             and isinstance(n.body[0], ast.Return)]
    assert len(gates) == 1 and gates[0].lineno < call.lineno, (
        "the timer scan must run only after studio_allowed(org)")
    assert any(isinstance(n, ast.Try) for n in _chain_to(helper, call)), (
        "a failure of the timer scan must not stop the wait scan")

    scan = _function(_tree(), "scan_due_waits")
    (step,) = [c for c in ast.walk(scan)
               if isinstance(c, ast.Call) and _callee(c) == "_scan_studio_timers"]
    loops = [n for n in _chain_to(scan, step) if isinstance(n, ast.For)]
    assert len(loops) == 1 and "_organization_ids" in ast.unparse(loops[0].iter)
    first = loops[0].body[0]
    assert isinstance(first, ast.If) and "unattended.org_allowed(org)" in ast.unparse(first.test)
    assert isinstance(first.body[0], ast.Continue)


_TIMER_FUNCTIONS = ("scan_due_timers", "_scan_studio_timers", "_timer_rows", "_disarm",
                    "_timer_breaches", "_timer_triggers", "_timer_of_config",
                    "_timer_key_prefix")


def test_the_timer_scan_binds_no_tenant_never_dispatches_and_adds_no_loop() -> None:
    """H-328 (c): only a member request binds the tenant of an event, so the
    scan must never call `dispatch_event`. It binds no tenant and reads no
    ambient one. One timekeeper: the module starts ONE task, the loop of
    `start_workflow_scheduler`."""
    tree = _tree()
    banned = {"dispatch_event", "bind_tenant", "release_tenant", "current_tenant",
              "create_task", "ensure_future", "get_running_loop", "_get_db", "sleep"}
    for name in _TIMER_FUNCTIONS:
        fn = _function(tree, name)
        hit = sorted({_callee(c) for c in ast.walk(fn) if isinstance(c, ast.Call)} & banned)
        assert hit == [], (name, hit)
    tasks = [c for c in ast.walk(tree) if isinstance(c, ast.Call) and _callee(c) == "create_task"]
    assert len(tasks) == 1, "the scheduler module starts one task, its one loop"
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert "dispatch_event" not in imported and "bind_tenant" not in imported, imported


def test_the_timer_scan_reads_with_the_db_clock_only() -> None:
    """The DB clock decides due-ness and the arm time, never the app clock."""
    assert "now()" in objects_store._TIMER_DUE_SQL
    assert ":now" not in objects_store._TIMER_DUE_SQL
    assert "now()" in scheduler._ARM_TIMER_SQL
    assert "last_fired_at IS NOT DISTINCT FROM" in scheduler._ARM_TIMER_SQL
    assert "last_fired_at = NULL" in scheduler._DISARM_TIMER_SQL
    for name in _TIMER_FUNCTIONS:
        body = ast.unparse(_function(_tree(), name))
        assert "datetime.now" not in body and "time.time" not in body, name


def test_each_table_of_the_breach_read_names_the_org() -> None:
    """RLS hides a missing filter from an R8 test, so the text is the fence."""
    sql = objects_store._TIMER_DUE_SQL
    for alias in ("h", "p", "r"):
        assert f"{alias}.organization_id = CAST(NULLIF(current_setting('app.tenant_id'" in sql, alias
    assert "ORDER BY h.v_ts, h.record_id LIMIT :n" in sql
    trig = scheduler._TIMER_TRIGGERS_SQL
    assert "w.organization_id = CAST(:org AS uuid)" in trig and "LIMIT :n" in trig
    assert "ORDER BY t.id" in trig


# ── The record trigger never hears a breach ────────────────────────────────


@pytest.mark.parametrize("event_type", ["", "studio.record.breached", "studio.record.updated"])
def test_dispatch_event_never_matches_a_breach_trigger(event_type: str) -> None:
    """A payload that copies the filter, the op `breach` included, still does
    not match. Only the scan starts a timer trigger."""
    payload = dict(_BREACH, record_id="r1")
    cfg = {**_cfg(**_BREACH), "event_type": event_type}
    assert not triggers.event_trigger_matches(cfg, "studio", event_type or "x", payload)
    # A filter that names a timer with another op is a breach trigger too.
    other = _cfg(package_id=PKG, op="update", timer="sla")
    assert not triggers.event_trigger_matches(other, "studio", "studio.record.updated",
                                              {"package_id": PKG, "op": "update", "timer": "sla"})
    # Control: the record trigger of the same package still matches.
    assert triggers.event_trigger_matches(
        _cfg(package_id=PKG, object="claim"), "studio", "studio.record.updated",
        {"package_id": PKG, "object": "claim", "op": "update"})


def test_a_breach_trigger_is_known_by_its_op_or_its_timer() -> None:
    assert triggers.is_breach_trigger(_cfg(**_BREACH))
    assert triggers.is_breach_trigger(_cfg(timer="sla"))
    assert not triggers.is_breach_trigger(_cfg(op="create"))
    assert not triggers.is_breach_trigger({"source": "studio"})
    assert not triggers.is_breach_trigger({"source": "studio", "filter": "breach"})


# ── The save path ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("event_type", [None, "", "studio.record.breached"])
def test_the_save_path_accepts_a_breach_trigger(event_type: str | None) -> None:
    cfg = _cfg(**_BREACH)
    if event_type is not None:
        cfg["event_type"] = event_type
    _validate_event_filter(cfg)
    _validate_trigger_specs([TriggerSpec(kind="event", config=cfg)])


@pytest.mark.parametrize("config", [
    _cfg(package_id=PKG, object="claim", op="breach"),
    _cfg(object="claim", op="breach", timer="sla"),
    _cfg(package_id=PKG, op="breach", timer="sla"),
    _cfg(package_id=PKG, object="claim", op="create", timer="sla"),
    _cfg(package_id=PKG, object="claim", timer="sla"),
    _cfg(package_id=PKG, object="claim", op="breach", timer="Sla"),
    _cfg(package_id=PKG, object="claim", op="breach", timer="s" * 49),
    _cfg(package_id=PKG, object="claim", op="breach", timer="sla "),
    _cfg(package_id=PKG, object="claim", op="Breach", timer="sla"),
    {**_cfg(**_BREACH), "event_type": "studio.record.created"},
    {"source": "zoho", "filter": dict(_BREACH)},
])
def test_the_save_path_refuses_a_bad_breach_trigger(config: dict[str, Any]) -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_trigger_specs([TriggerSpec(kind="event", config=config)])
    assert exc.value.status_code == 422


def test_a_timer_key_outside_the_filter_is_refused() -> None:
    """A stray top-level `timer` would read as a plain Studio trigger and match
    every record event. The save path refuses it."""
    for cfg in ({"source": "studio", "timer": "sla"},
                {**_cfg(**_BREACH), "timer": "sla"}):
        with pytest.raises(HTTPException) as exc:
            _validate_trigger_specs([TriggerSpec(kind="event", config=cfg)])
        assert exc.value.status_code == 422


def test_a_save_keeps_the_arm_time_of_an_unchanged_timer_only() -> None:
    """The identity of a timer trigger is its package, object and rule. A save
    that keeps them keeps the arm time. A changed rule re-arms."""
    same = _trigger_identity("event", _cfg(**_BREACH))
    assert same == _trigger_identity("event", {**_cfg(**_BREACH), "enabled": False})
    assert same != _trigger_identity("event", _cfg(**{**_BREACH, "timer": "sla2"}))
    assert same != _trigger_identity("event", _cfg(**{**_BREACH, "object": "invoice"}))
    assert same != _trigger_identity("event", _cfg(package_id=PKG, object="claim"))
    assert _trigger_identity("event", _cfg(package_id=PKG)) == ("event", "")


# ── The scan, with fakes ────────────────────────────────────────────────────


def test_a_timer_config_reads_fail_closed() -> None:
    assert scheduler._timer_of_config(_cfg(**_BREACH)) == (PKG, "claim", "sla")
    assert scheduler._timer_of_config({**_cfg(**_BREACH), "source": " studio "}) == (
        PKG, "claim", "sla")
    for bad in (_cfg(**{**_BREACH, "op": "create"}), _cfg(**{**_BREACH, "timer": ""}),
                _cfg(**{**_BREACH, "timer": 5}), {"source": "zoho", "filter": dict(_BREACH)},
                {"source": "studio", "filter": [1]}):
        assert scheduler._timer_of_config(bad) is None, bad


def test_the_scan_key_is_the_key_that_the_store_sql_builds() -> None:
    """The store leaves out a breach whose run holds `<prefix><record id>:<due>`.
    The scan starts the run with the same text. If the two differ, a fired
    record holds the head of the list for good (the R8 case
    `test_a_fired_record_never_holds_the_head_of_the_list`)."""
    assert scheduler._timer_key_prefix("sla") == "timer:sla:"
    assert ("r.delivery_key = CAST(:prefix AS text) || h.record_id::text || ':' || d.due_at"
            in objects_store._TIMER_DUE_SQL)
    body = ast.unparse(_function(_tree(), "scan_due_timers"))
    assert "delivery_key=f'{_timer_key_prefix(rule)}{item.record_id}:{item.due_at}'" in body


class _Session:
    """A fake tenant session: it records each statement and its binds."""

    def __init__(self, log: list[tuple[str, dict]]) -> None:
        self._log = log

    async def execute(self, clause: Any, params: dict | None = None) -> Any:
        self._log.append((" ".join(str(clause).split()), dict(params or {})))
        return SimpleNamespace(rowcount=1)


def _row(trigger: str, org: str, *, armed: bool = True, config: dict | None = None,
         enabled: bool = True, status: str = "published") -> Any:
    return SimpleNamespace(
        trigger_id=trigger, config=json.dumps(config or _cfg(**_BREACH)),
        last_fired_at="2026-11-13T00:00:00+00:00" if armed else None,
        workflow_id=f"wf-{trigger}", name=f"Timer {trigger}", owner_email="Owner@A.test",
        latest_version=1, variables="{}", organization_id=org, enabled=enabled,
        status=status)


def _wire(monkeypatch, rows: list[Any], due: list[Any], *,
          refuse: dict[str, Exception] | None = None) -> SimpleNamespace:
    from gateway.studio import access

    seen = SimpleNamespace(sql=[], starts=[], held=[], breaches=[])

    @contextlib.asynccontextmanager
    async def session(org: str | None = None):
        assert org == ORG_A, org
        yield _Session(seen.sql)

    async def _triggers(org: str) -> list[Any]:
        return rows

    async def _held(members, org):
        seen.held.append((sorted(str(m).lower() for m in members), org))
        return {"owner@a.test": True}

    timer = objects_store.Timer(object_id="o1", package_id=PKG, object_name="claim",
                                rule=None)  # type: ignore[arg-type]

    async def _breaches(org, row, timer_ref, held):
        seen.breaches.append((org, row.trigger_id, timer_ref, held))
        return {"blocks": []}, timer, due

    async def _start(**kw: Any) -> str:
        seen.starts.append(kw)
        exc = (refuse or {}).get(kw["trigger_payload"]["record_id"])
        if exc is not None:
            raise exc
        return f"run-{len(seen.starts)}"

    monkeypatch.setattr(scheduler, "_tenant_session", session)
    monkeypatch.setattr(scheduler, "_timer_triggers", _triggers)
    monkeypatch.setattr(access, "members_studio_state", _held)
    monkeypatch.setattr(scheduler, "_timer_breaches", _breaches)
    monkeypatch.setattr(scheduler, "start_run", _start)
    return seen


_DUE = [objects_store.TimerDue("r1", "2026-11-13T09:00:00.000000Z"),
        objects_store.TimerDue("r2", "2026-11-13T09:05:00.000000Z")]


def test_the_payload_holds_no_field_value_and_a_duplicate_starts_nothing(monkeypatch) -> None:
    """The payload holds ids and names, never a field value. Each member who
    reads the runs reads it, past the row rules, so the due value stays in the
    delivery key only (review round 2)."""
    seen = _wire(monkeypatch, [_row("t1", ORG_A)], _DUE,
                 refuse={"r2": DuplicateDelivery("run-0")})
    assert asyncio.run(scheduler.scan_due_timers(ORG_A)) == 1
    assert len(seen.starts) == 2
    first = seen.starts[0]
    assert first["trigger_payload"] == {
        "source": "studio", "event_type": "studio.record.breached", "package_id": PKG,
        "object": "claim", "record_id": "r1", "rule": "sla"}
    assert first["delivery_key"] == "timer:sla:r1:2026-11-13T09:00:00.000000Z"
    assert (first["trigger_kind"], first["started_by"], first["organization_id"]) == (
        "event", "scheduler:timer", ORG_A)
    assert seen.held == [(["owner@a.test"], ORG_A)]


def test_a_run_cap_stops_the_trigger_and_keeps_the_breach_due(monkeypatch) -> None:
    seen = _wire(monkeypatch, [_row("t1", ORG_A)], _DUE,
                 refuse={"r1": RunRejected("cap")})
    assert asyncio.run(scheduler.scan_due_timers(ORG_A)) == 0
    assert [s["trigger_payload"]["record_id"] for s in seen.starts] == ["r1"]


def test_the_row_read_arms_nothing_and_disarms_a_trigger_that_is_not_live(monkeypatch) -> None:
    """No trigger is armed before its checks (P1 of the review). The row read
    arms nothing. It disarms an armed trigger that is disabled, or whose
    workflow is not published. A row of another org and a trigger that is not
    a timer are skipped, and an unarmed live trigger goes on to the checks."""
    rows = [_row("t1", ORG_A, armed=False), _row("t2", ORG_B),
            _row("t3", ORG_A, config=_cfg(package_id=PKG, op="create")),
            _row("t4", ORG_A, enabled=False), _row("t5", ORG_A, status="draft"),
            _row("t6", ORG_A, armed=False, enabled=False)]
    seen = _wire(monkeypatch, rows, [])
    assert asyncio.run(scheduler.scan_due_timers(ORG_A)) == 0
    assert seen.starts == []
    assert [b[1] for b in seen.breaches] == ["t1"]
    disarm = " ".join(scheduler._DISARM_TIMER_SQL.split())
    assert seen.sql == [(disarm, {"id": "t4"}), (disarm, {"id": "t5"})], seen.sql
    assert not any("now()" in sql for sql, _ in seen.sql), "the row read armed a trigger"


def test_the_print_of_a_timer_names_its_due_field_only() -> None:
    """A new pause value only stops breaches, so it must not arm the trigger
    again (review round 2, P2(a)). A new due field does."""
    from acb_common import studio_rules as sr

    base = sr.parse_rule({"name": "sla", "kind": "timer", "field": "due",
                          "pause": {"field": "state", "in": ["waiting"]}})
    wider = sr.parse_rule({"name": "sla", "kind": "timer", "field": "due",
                           "pause": {"field": "state", "in": ["waiting", "hold"]}})
    bare = sr.parse_rule({"name": "sla", "kind": "timer", "field": "due"})
    moved = sr.parse_rule({"name": "sla", "kind": "timer", "field": "deadline"})
    assert objects_store.timer_print(base) == {"field": "due"}
    assert objects_store.timer_print(wider) == objects_store.timer_print(base)
    assert objects_store.timer_print(bare) == objects_store.timer_print(base)
    assert objects_store.timer_print(moved) == {"field": "deadline"}


def test_the_breach_read_has_a_deadline_and_a_lookback() -> None:
    """P2 of the review: the read runs under `_time_limit` (the LOCAL
    `statement_timeout` of C2) and inside the lookback window. The R8 half is
    `test_t17_a_slow_read_meets_the_deadline_and_the_scan_goes_on`."""
    tree = ast.parse((_GATEWAY / "studio" / "objects_store.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "due_timer_records")
    body = ast.unparse(fn)
    assert "_time_limit(db, deadline, _TIMER_TIMEOUT_MESSAGE)" in body
    assert "timed.execute(text(_TIMER_DUE_SQL)" in body
    assert "'lookback': TIMER_LOOKBACK_DAYS" in body
    assert "make_interval(days => CAST(:lookback AS integer))" in objects_store._TIMER_DUE_SQL
    assert 1 <= objects_store.TIMER_LOOKBACK_DAYS <= 31
    assert objects_store.TIMER_READ_TIMEOUT_MS <= 5_000


@pytest.mark.parametrize(("member", "package_id", "held"), [
    ("owner@a.test", "not-a-uuid", True), ("owner@a.test", PKG, False),
    ("no-address", PKG, True), (None, PKG, True),
])
def test_the_owner_check_fails_closed_with_no_statement(member: Any, package_id: Any,
                                                        held: bool) -> None:
    from gateway.studio.access import member_owns_package

    assert asyncio.run(member_owns_package(
        _NoDb(), member=member, package_id=package_id, studio_held=held)) is False


class _Nested:
    """A fake session with a savepoint that does nothing."""

    def begin_nested(self) -> Any:
        return _AsyncNull()


class _AsyncNull:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.mark.parametrize(("held", "read", "answer"), [
    (None, "never", None),        # the feature answer was a fault
    (True, "raise", None),        # the package read failed: a fault
    (True, "none", False),        # a true no: not the owner
    (True, "pkg", True),
])
def test_the_owner_check_tells_a_fault_from_a_no(monkeypatch, held: Any, read: str,
                                                 answer: Any) -> None:
    """Review round 2: a fault answers None and keeps the arm, and a true no
    answers False and disarms. The R8 halves are T23 and T24."""
    from gateway.studio import access, packages_store

    async def get_package(db: Any, member: str, pid: str) -> Any:
        if read == "never":
            raise AssertionError("no read after a fault of the feature answer")
        if read == "raise":
            raise RuntimeError("connection lost")
        return None if read == "none" else object()

    monkeypatch.setattr(packages_store, "get_package", get_package)
    assert asyncio.run(access.member_owns_package(
        _Nested(), member="owner@a.test", package_id=PKG, studio_held=held)) is answer


@pytest.mark.parametrize(("outcome", "state", "holds"), [
    ("raise", None, False), ("inactive", False, False), ("studio", True, True),
])
def test_the_feature_state_tells_a_fault_from_a_no(monkeypatch, outcome: str, state: Any,
                                                   holds: bool) -> None:
    """`members_studio_state` keeps a fault apart (None), and asks the resolver
    in strict mode. `members_holding_studio`, the answer of E3, stays fail
    closed: a fault is False there."""
    import acb_auth.access as acc
    from acb_auth.permissions import build_access
    from gateway.studio import access

    calls: list[bool] = []

    async def resolve(email: Any, org: Any, *, strict: bool = False) -> Any:
        calls.append(strict)
        if outcome == "raise":
            raise acc.IdentityUnavailable("access_in_org")
        if outcome == "inactive":
            return build_access([], is_active=False)
        return build_access(["feature:studio"], is_active=True)

    monkeypatch.setattr(acc, "resolve_access_in_org", resolve)
    got = asyncio.run(access.members_studio_state(["Owner@A.test", "no-address"], ORG_A))
    assert got == {"owner@a.test": state, "no-address": False}
    assert calls == [True]
    held = asyncio.run(access.members_holding_studio(["owner@a.test"], ORG_A))
    assert held == {"owner@a.test": holds} and calls == [True, False]


def test_the_strict_resolver_raises_on_a_read_error(monkeypatch) -> None:
    """`resolve_access_in_org(strict=True)` raises IdentityUnavailable on a read
    error, so a caller can tell a fault from a no. The default still answers
    no access."""
    import acb_auth.access as acc

    class _Broken:
        async def __aenter__(self) -> Any:
            raise RuntimeError("relation does not exist")

        async def __aexit__(self, *exc: Any) -> bool:
            return False

    monkeypatch.setattr(acc, "tenant_session", lambda org: _Broken())
    monkeypatch.setattr(acc.db_busy, "is_db_timeout", lambda exc: False)
    refused = asyncio.run(acc.resolve_access_in_org("a@x.test", ORG_A))
    assert refused.is_active is False
    with pytest.raises(acc.IdentityUnavailable):
        asyncio.run(acc.resolve_access_in_org("a@x.test", ORG_A, strict=True))


def test_a_refusal_logs_once_for_each_trigger_in_its_window(monkeypatch) -> None:
    lines: list[str] = []
    clock = {"now": 1000.0}
    monkeypatch.setattr(scheduler._log, "warning", lambda event, **kw: lines.append(event))
    monkeypatch.setattr(scheduler.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(scheduler, "_timer_warned", {})
    one, two = SimpleNamespace(trigger_id="t1"), SimpleNamespace(trigger_id="t2")
    for _ in range(3):
        scheduler._warn_timer(one, "workflows.timer_refused_package_owner", "wf1")
    scheduler._warn_timer(two, "workflows.timer_refused_package_owner", "wf2")
    scheduler._warn_timer(one, "workflows.timer_not_found", "wf1")
    assert len(lines) == 3
    clock["now"] += scheduler.TIMER_WARN_EVERY_S + 1
    scheduler._warn_timer(one, "workflows.timer_refused_package_owner", "wf1")
    assert len(lines) == 4


def test_a_full_page_of_triggers_moves_the_cursor(monkeypatch) -> None:
    """A pass reads at most TIMER_TRIGGERS_PER_PASS triggers. A full page moves
    the cursor, so the next pass reads the next ones. A short page starts again
    at the first trigger."""
    pages = [[SimpleNamespace(trigger_id="a"), SimpleNamespace(trigger_id="b")],
             [SimpleNamespace(trigger_id="c")]]
    binds: list[dict] = []

    class _Db:
        async def execute(self, clause: Any, params: dict) -> Any:
            binds.append(dict(params))
            return SimpleNamespace(fetchall=lambda: pages[len(binds) - 1])

    @contextlib.asynccontextmanager
    async def session(org: str | None = None):
        yield _Db()

    monkeypatch.setattr(scheduler, "_tenant_session", session)
    monkeypatch.setattr(scheduler, "TIMER_TRIGGERS_PER_PASS", 2)
    scheduler._timer_cursor.pop(ORG_A, None)
    assert [r.trigger_id for r in asyncio.run(scheduler._timer_triggers(ORG_A))] == ["a", "b"]
    assert scheduler._timer_cursor[ORG_A] == "b"
    assert [r.trigger_id for r in asyncio.run(scheduler._timer_triggers(ORG_A))] == ["c"]
    assert ORG_A not in scheduler._timer_cursor
    assert [b["after"] for b in binds] == [scheduler._NO_TRIGGER, "b"]
    assert all(b["n"] == 2 and b["op"] == "breach" and b["org"] == ORG_A for b in binds)


# ── The two reads of the store ──────────────────────────────────────────────


class _NoDb:
    async def execute(self, *_a: Any, **_k: Any) -> Any:
        raise AssertionError("no statement may run")


@pytest.mark.parametrize("limit", [0, objects_store.MAX_TIMER_READ + 1, True, 2.0])
def test_the_breach_read_is_bounded(limit: Any) -> None:
    timer = objects_store.Timer("6f0b2d3a-0000-4000-8000-0000000000f1", PKG, "claim",
                                rule=None)  # type: ignore[arg-type]
    with pytest.raises(objects_store.ObjectStoreError):
        asyncio.run(objects_store.due_timer_records(
            _NoDb(), timer, armed_at=None, workflow_id=PKG,  # type: ignore[arg-type]
            key_prefix="timer:sla:", limit=limit))


@pytest.mark.parametrize(("pid", "obj", "rule"), [
    ("not-a-uuid", "claim", "sla"), (None, "claim", "sla"), (PKG, 5, "sla"), (PKG, "claim", None),
])
def test_a_timer_read_with_a_bad_reference_fails_closed(pid: Any, obj: Any, rule: Any) -> None:
    assert asyncio.run(objects_store.timer_of(
        _NoDb(), package_id=pid, object_name=obj, rule_name=rule)) is None


def test_the_r8_suite_is_named_in_both_ci_skip_guards() -> None:
    """A skip is not a pass. The R8 guard reads its must-run list from
    tests/fences.json (CIV-33c-2), so the R8 half must be named there."""
    from tests.unit import _r8_lists as r8_lists

    assert "tests/unit/test_workflows_timer_r8.py" in r8_lists.must_run()
