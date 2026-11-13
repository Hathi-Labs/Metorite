"""WS-AUTO AUTO-8b-2d: the real meter of the headless turn, hermetic (no
database and no model).

Spec: ``project-docs/specs/proactive_agent.md`` §4.4, the §9 row AUTO-8b-2d
and §11. Code: ``gateway/jobs/headless_turn.py`` (``console_meter``),
``acb_auth/console_resolve.py`` (``run_spend_on_console``) and the Console
door ``POST /registry/usage/run``. The door's R8 half is
``test_customer_console_billing_reads.py::TestTheRunSpendDoor``.

Each Console answer here comes from an ``httpx.MockTransport``. So the test
runs the real client, the real meter and the real turn, and only the wire is
a fake.

**The fences (R7), by name.**

* The client builds the call with the member and the run id and nothing else:
  ``test_the_client_sends_the_member_and_the_run_id_and_nothing_else``.
* A door error before the call makes no call (``CostReadFailed``):
  ``test_a_door_error_before_the_call_makes_no_call``.
* An error after the call charges the rest of the budget:
  ``test_a_door_error_after_the_call_charges_the_rest``.
* The meter's cost equals the door's number:
  ``test_a_round_records_the_number_of_the_door``.
* No tool that bills with no run id: ``test_a_headless_turn_holds_no_tool_that_bills_without_a_run_id``.
* The runner stays non-streaming: ``test_the_runner_of_the_turn_stays_non_streaming``.

**Mutation (b)** (run on the branch, then restored). ``console_meter``
answers ``Decimal(0)`` in place of each ``raise CostReadFailed`` of a door
error. Then ``test_a_door_error_before_the_call_makes_no_call`` goes red (the
call runs), and so does ``test_a_door_error_after_the_call_charges_the_rest``
(the round charges 0).

Run::

    uv run pytest tests/unit/test_headless_turn_meter.py -q -p no:cacheprovider
"""

from __future__ import annotations

import ast
import asyncio
import functools
import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.jobs", reason="gateway not installed")

import httpx
import structlog
from acb_auth import console_resolve
from acb_auth.console_resolve import (
    BILLING_READ_DOORS,
    RUN_SPEND_DOOR,
    ConsoleBillingUnavailable,
    run_spend_on_console,
)
from acb_common import bind_run_context, job_member_scope, run_context_scope
from gateway.jobs import headless_turn as ht

from tests.unit.test_headless_turn import (
    ANA,
    FakeRunner,
    Reports,
    _payload,
    _run,
    make_world,
)

_ROOT = Path(__file__).resolve().parents[2]

CONSOLE_URL = "https://console.invalid"
DEPLOYMENT_KEY = "cc_depl_fixture_notarealsecret"
BEN = "ben@auto8b2d.test"


# ── The wire ────────────────────────────────────────────────────────────────


class Console:
    """A scripted Console. Each request takes the next answer: a pair
    ``(status, body)`` or an exception that the transport raises."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.seen: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append({
            "url": str(request.url),
            "auth": request.headers.get("authorization"),
            "body": json.loads(request.content),
        })
        if not self.answers:
            raise AssertionError("the Console got a request that the test did not script")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        status, body = answer
        return httpx.Response(status, json=body)


def _wire(monkeypatch: pytest.MonkeyPatch, console: Console) -> None:
    def _new_client(timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(console.handler))

    monkeypatch.setattr(console_resolve, "_new_http_client", _new_client)


def _no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(timeout: float | None = None) -> Any:
        raise AssertionError("no client may be built here")

    monkeypatch.setattr(console_resolve, "_new_http_client", _boom)


@pytest.fixture
def world(monkeypatch):
    w = make_world(monkeypatch)
    monkeypatch.setattr(w.settings, "customer_console_url", CONSOLE_URL)
    monkeypatch.setattr(w.settings, "customer_console_deployment_key", DEPLOYMENT_KEY)
    return w


def _credits(value: str) -> tuple[int, dict[str, Any]]:
    return 200, {"credits": value}


class QuietRunner:
    """One round that records its run id and spends nothing on a fake meter.
    The Console script says what the round cost."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, message: str, *, run_id: str, org: str, member: str,
                       tools: tuple[str, ...]) -> dict[str, Any]:
        self.calls.append({"run_id": run_id, "member": member, "tools": tools})
        return {"answer": f"answer {len(self.calls)}"}


def _turn(world: Any, runner: Any, **kw: Any) -> ht.HeadlessTurn:
    """A turn with the DEFAULT meter, the real ``console_meter``."""
    kw.setdefault("candidates", world.checks)
    return ht.HeadlessTurn(runner=runner, **kw)


def _always(_result: Any) -> bool:
    return True


# ── The client ──────────────────────────────────────────────────────────────


def test_the_client_sends_the_member_and_the_run_id_and_nothing_else(world, monkeypatch) -> None:
    run_id = str(uuid.uuid4())
    console = Console(_credits("7.5000"))
    _wire(monkeypatch, console)
    status, body = asyncio.run(run_spend_on_console(actor_email=ANA, run_id=run_id))
    assert (status, body) == (200, {"credits": "7.5000"})
    [seen] = console.seen
    assert seen["url"] == f"{CONSOLE_URL}{RUN_SPEND_DOOR}"
    assert seen["auth"] == f"Bearer {DEPLOYMENT_KEY}"
    # R11: the member and the run id, and never an org.
    assert seen["body"] == {"actor_email": ANA, "run_id": run_id}


def test_the_door_is_not_a_browser_door() -> None:
    """Each door of ``BILLING_READ_DOORS`` has a browser route
    (``test_billing_proxy_route.py``). This one serves the job worker only."""
    assert RUN_SPEND_DOOR == "/registry/usage/run"
    assert RUN_SPEND_DOOR not in BILLING_READ_DOORS.values()


def test_an_unwired_box_raises_with_no_hop(world, monkeypatch) -> None:
    monkeypatch.setattr(world.settings, "customer_console_url", "")
    _no_client(monkeypatch)
    with pytest.raises(ConsoleBillingUnavailable):
        asyncio.run(run_spend_on_console(actor_email=ANA, run_id=str(uuid.uuid4())))


@pytest.mark.parametrize("status", [401, 408, 429, 500, 503])
def test_a_status_with_no_answer_is_an_outage(world, monkeypatch, status) -> None:
    _wire(monkeypatch, Console((status, {})))
    with pytest.raises(ConsoleBillingUnavailable):
        asyncio.run(run_spend_on_console(actor_email=ANA, run_id=str(uuid.uuid4())))


@pytest.mark.parametrize("status", [400, 403, 409, 422])
def test_a_refusal_is_returned_as_it_is(world, monkeypatch, status) -> None:
    _wire(monkeypatch, Console((status, {"detail": "no"})))
    got = asyncio.run(run_spend_on_console(actor_email=ANA, run_id=str(uuid.uuid4())))
    assert got == (status, {"detail": "no"})


# ── The default meter ───────────────────────────────────────────────────────


def _as(member: str, coro_fn: Any) -> Any:
    """Run ``coro_fn()`` inside ``job_member_scope(member)``, as the turn does."""
    async def go() -> Any:
        with job_member_scope(member, app=ht.APP, agent=ht.AGENT):
            return await coro_fn()
    return asyncio.run(go())


def test_the_meter_answers_the_number_of_the_door(world, monkeypatch) -> None:
    run_id = str(uuid.uuid4())
    console = Console(_credits("7.5000"))
    _wire(monkeypatch, console)
    got = _as(ANA, lambda: ht.console_meter("org-1", ANA, run_id))
    assert isinstance(got, Decimal)
    assert got == Decimal("7.5") and str(got) == "7.5000"
    assert console.seen[0]["body"] == {"actor_email": ANA, "run_id": run_id}


@pytest.mark.parametrize("how", ["no_scope", "another_member", "not_verified"])
def test_the_meter_reads_only_for_the_proven_member(world, monkeypatch, how) -> None:
    """The member of the read is the member that ``job_member_scope`` bound
    VERIFIED. Any other address raises before any hop."""
    _no_client(monkeypatch)

    async def go() -> Any:
        if how == "no_scope":
            return await ht.console_meter("org-1", ANA, "r")
        if how == "another_member":
            with job_member_scope(BEN):
                return await ht.console_meter("org-1", ANA, "r")
        with run_context_scope():
            bind_run_context(user=ANA)  # a claim, not verified
            return await ht.console_meter("org-1", ANA, "r")

    with pytest.raises(ht.CostReadFailed, match="NoProvenMember"):
        asyncio.run(go())


#: A Console answer, and the code of the ``CostReadFailed`` that it gives.
_DOOR_ERRORS = {
    "org_slug_400": ((400, {"detail": "may not name an organization"}), "HTTP 400"),
    "not_a_member_403": ((403, {"detail": "not an admin on this deployment"}), "HTTP 403"),
    "two_orgs_409": ((409, {"detail": "more than one organization"}), "HTTP 409"),
    "bad_body_422": ((422, {"detail": []}), "HTTP 422"),
    "outage_503": ((503, {}), "ConsoleUnavailable"),
    "network": (httpx.ConnectError("refused"), "ConsoleUnavailable"),
    "a_float": ((200, {"credits": 7.5}), "BadAnswer"),
    "a_negative": (_credits("-1.0000"), "BadAnswer"),
    "two_places": (_credits("7.50"), "BadAnswer"),
    "no_credits": ((200, {}), "BadAnswer"),
}


@pytest.mark.parametrize("case", sorted(_DOOR_ERRORS))
def test_each_door_error_raises_and_never_reads_as_zero(world, monkeypatch, case) -> None:
    answer, code = _DOOR_ERRORS[case]
    _wire(monkeypatch, Console(answer))
    with pytest.raises(ht.CostReadFailed) as caught:
        _as(ANA, lambda: ht.console_meter("org-1", ANA, str(uuid.uuid4())))
    assert str(caught.value) == code


# ── The turn with the real meter ────────────────────────────────────────────


@pytest.mark.parametrize("case", [*sorted(_DOOR_ERRORS), "unwired"])
def test_a_door_error_before_the_call_makes_no_call(world, monkeypatch, case) -> None:
    """The read BEFORE the first call fails at the door: no call, no write,
    no mark, and the job gives up with the NEW reason ``CostReadFailed``."""
    if case == "unwired":
        monkeypatch.setattr(world.settings, "customer_console_deployment_key", "")
        _no_client(monkeypatch)
    else:
        _wire(monkeypatch, Console(_DOOR_ERRORS[case][0]))
    goal = world.goals.add()
    runner = QuietRunner()
    with structlog.testing.capture_logs() as caps:
        claim = _run(_turn(world, runner, go_on=_always), _payload(goal))
    assert runner.calls == [] and world.goals.writes == 0
    assert world.goals.rows[goal.id] == goal
    assert world.jobs.given_up == [(claim.id, ht.COST_READ_FAILED)]
    assert ht.COST_READ_FAILED.startswith("CostReadFailed:")
    assert ht.COST_READ_FAILED not in (ht.NO_READER, ht.METER_FAILED)
    lines = [c["log_level"] for c in caps if c["event"] == "headless_turn.cost_read_failed"]
    assert lines == ["error"]


def test_a_door_error_before_a_later_round_gives_meter_failed(world, monkeypatch) -> None:
    """Round 1 read the door twice. The read before round 2 is refused: no
    second call, and the reason is ``MeterFailed``, because a reader answered
    before."""
    _wire(monkeypatch, Console(_credits("0.0000"), _credits("1.0000"),
                               (403, {"detail": "not an admin on this deployment"})))
    goal = world.goals.add(cost_cap="10")
    runner = QuietRunner()
    claim = _run(_turn(world, runner, go_on=_always), _payload(goal))
    after = world.goals.rows[goal.id]
    assert len(runner.calls) == 1
    assert (after.round_count, after.cost_used_credits, after.job_id) == (1, Decimal("1"), None)
    assert world.jobs.given_up == [(claim.id, ht.METER_FAILED)]


@pytest.mark.parametrize("after", ["outage_503", "not_a_member_403", "a_negative", "network"])
def test_a_door_error_after_the_call_charges_the_rest(world, monkeypatch, after) -> None:
    """Fail closed for money, with the REAL meter: the read after the call
    fails, so the round charges the rest of the budget (cap minus used), the
    goal blocks with ``cap_reached`` and the report runs once."""
    _wire(monkeypatch, Console(_credits("0.0000"), _DOOR_ERRORS[after][0]))
    goal = world.goals.add(cost_cap="10", used="3")
    runner, reports = QuietRunner(), Reports()
    with structlog.testing.capture_logs() as caps:
        claim = _run(_turn(world, runner, report=reports, go_on=_always), _payload(goal))
    done = world.goals.rows[goal.id]
    assert len(runner.calls) == 1
    assert (done.block_code, done.cost_used_credits, done.job_id) == (
        "cap_reached", Decimal("10"), None)
    assert len(reports.seen) == 1
    assert world.jobs.done == [claim.id] and world.jobs.given_up == []
    lines = [(c["log_level"], c["error"]) for c in caps
             if c["event"] == "headless_turn.cost_unknown"]
    assert lines == [("error", "CostReadFailed")]


def test_a_round_records_the_number_of_the_door(world, monkeypatch) -> None:
    """The cost of the round is the change of the door's number, as a
    Decimal with 4 places. Each read names the goal's member and the run id
    of the round, and nothing else."""
    console = Console(_credits("0.0000"), _credits("7.5000"))
    _wire(monkeypatch, console)
    goal = world.goals.add(cost_cap="50", used="1.2500")
    runner = QuietRunner()
    claim = _run(_turn(world, runner), _payload(goal))
    done = world.goals.rows[goal.id]
    assert done.cost_used_credits == Decimal("8.7500")
    assert (done.round_count, done.state, done.job_id) == (1, "active", None)
    [call] = runner.calls
    assert [s["body"] for s in console.seen] == [
        {"actor_email": goal.member_email, "run_id": call["run_id"]}] * 2
    assert world.jobs.done == [claim.id]


def test_the_fake_meter_of_the_other_tests_gets_the_member(world) -> None:
    """The ``Meter`` seam takes the member: the turn passes the goal's
    member to each read, before the call and after it."""
    goal = world.goals.add()
    runner = FakeRunner(world.meter)
    _run(ht.HeadlessTurn(runner=runner, meter=world.meter, candidates=world.checks),
         _payload(goal))
    assert world.meter.members == [ANA, ANA]


# ── No tool that bills with no run id ───────────────────────────────────────

_GATEWAY_ROUTES = _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes"
_SKILLS = _ROOT / "packages" / "acb_skills" / "acb_skills"

#: The imports that let a gateway route make a billed Console call: the embed
#: door, the AI seams of ``acb_llm`` and each billed client of
#: ``console_resolve`` by name. (The module itself also holds the sign-in
#: resolve and the billing reads, which bill nothing.) A tool reaches such a
#: route over HTTP, so the route's call carries no run id of the round, and
#: neither the meter nor the ceiling sees its cost.
_BILLED_IMPORTS = (
    "gateway.kb_index.embed", "gateway.kb_index.query", "acb_llm.decide", "acb_llm.routed",
    "acb_llm.context",
    *(f"acb_auth.console_resolve.{name}" for name in (
        "chat_completion_on_console", "stream_completion_on_console", "decide_on_console",
        "embeddings_on_console", "transcribe_on_console")),
)
_ROUTE_VERBS = frozenset({"get", "post", "put", "patch", "delete"})


@functools.cache
def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _imports(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


@functools.cache
def _billed_route_paths() -> frozenset[str]:
    """The paths of each gateway route whose module imports a billed client."""
    paths: set[str] = set()
    for path in _GATEWAY_ROUTES.rglob("*.py"):
        tree = _parse(path)
        if not _imports(tree) & set(_BILLED_IMPORTS):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for deco in node.decorator_list:
                if (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)
                        and deco.func.attr in _ROUTE_VERBS and deco.args
                        and isinstance(deco.args[0], ast.Constant)
                        and isinstance(deco.args[0].value, str)):
                    paths.add(deco.args[0].value)
    return frozenset(paths)


@functools.cache
def _tool_functions() -> dict[str, list[tuple[ast.Module, ast.AST]]]:
    """Each module-level function of ``acb_skills``, with its module."""
    out: dict[str, list[tuple[ast.Module, ast.AST]]] = {}
    for path in _SKILLS.rglob("*.py"):
        tree = _parse(path)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                out.setdefault(node.name, []).append((tree, node))
    return out


def _reach(tree: ast.Module, fn: ast.AST) -> tuple[set[str], set[str]]:
    """The gateway paths that *fn* names, and the modules that it imports,
    following the module-level string constants and the functions of the same
    module that it calls."""
    consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name)
              and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
    local = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    paths: set[str] = set()
    imported: set[str] = set()
    seen: set[str] = set()
    stack = [fn]
    while stack:
        node = stack.pop()
        imported |= _imports(node)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                paths.add(sub.value)
            elif isinstance(sub, ast.Name) and sub.id in consts:
                paths.add(consts[sub.id])
            elif isinstance(sub, ast.JoinedStr) and sub.values and isinstance(
                    sub.values[0], ast.Constant):
                paths.add(str(sub.values[0].value))
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id in local and sub.func.id not in seen):
                seen.add(sub.func.id)
                stack.append(local[sub.func.id])
    return {p for p in paths if p.startswith("/")}, imported


def _ends_with(tool_path: str, route: str) -> bool:
    tail = [s for s in tool_path.split("?")[0].split("/") if s]
    want = [s for s in route.split("/") if s]
    return bool(want) and len(want) <= len(tail) and tail[-len(want):] == want


def _unmetered(name: str, billed: frozenset[str]) -> list[str]:
    """Why the tool *name* can bill with no run id, or an empty list."""
    why: list[str] = []
    for tree, fn in _tool_functions().get(name, []):
        paths, imported = _reach(tree, fn)
        why += [f"{p} reaches the billed route {r}" for p in sorted(paths) for r in sorted(billed)
                if _ends_with(p, r)]
        why += [f"imports {m}" for m in sorted(imported & set(_BILLED_IMPORTS))]
    return why


def test_a_headless_turn_holds_no_tool_that_bills_without_a_run_id() -> None:
    """AUTO-8b-2d: the embed of ``crm_search_notes`` is a billed Console call
    with no run id. So the turn holds no such tool while the real meter is
    the default. The scan finds each such tool from the source: a route of
    the gateway that imports a billed client, or a billed import in the tool.

    Its limit: it follows one HTTP hop and the functions of the tool's own
    module. Mutation: drop the ``UNMETERED_TOOLS`` check of ``headless_tools``,
    and this test goes red on ``crm_search_notes``."""
    billed = _billed_route_paths()
    tools = _tool_functions()
    kept = ht.headless_tools((*ht.DEFAULT_TOOLS, *sorted(ht.UNMETERED_TOOLS)))
    assert kept, "the turn holds no tool at all, so this fence proves nothing"
    for name in kept:
        assert name in tools, f"{name} has no function in acb_skills, so the scan cannot see it"
        assert _unmetered(name, billed) == [], name
    assert not ht.UNMETERED_TOOLS & set(kept)
    assert not ht.UNMETERED_TOOLS & set(ht.DEFAULT_TOOLS)


def test_the_scan_sees_the_known_unmetered_tool() -> None:
    """The fence's own fence: the scan finds the gap that the review named,
    so a scan that can see nothing would fail here."""
    billed = _billed_route_paths()
    assert "/notes/search" in billed
    assert _unmetered("crm_search_notes", billed), "the scan no longer sees the embed call"
    assert "crm_search_notes" in ht.UNMETERED_TOOLS
    assert ht.headless_tools(("crm_search_notes",)) == ()


# ── The runner stays non-streaming ──────────────────────────────────────────

_EXECUTOR = _ROOT / "apps" / "services" / "orchestrator" / "orchestrator" / "executor.py"
_CONSOLE_MAIN = (_ROOT / "apps" / "services" / "customer_console" / "customer_console"
                 / "main.py")
_TURN = _ROOT / "apps" / "services" / "gateway" / "gateway" / "jobs" / "headless_turn.py"


def _called(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            out.add(f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name)
                    else "")
    return out


def test_the_runner_of_the_turn_stays_non_streaming() -> None:
    """A streamed call writes its usage row after its last frame
    (``customer_console/router.py``, ``relay_stream``), so a read just after
    the call can be low. So the turn's runner takes the batch path only.

    1. ``run_worker`` calls ``run_agent`` and never ``run_agent_stream``, and
       no call of the module passes ``stream``.
    2. Each executor function that ``run_agent`` reaches passes no ``stream``
       to any call. ``_run_with_maf_agent`` calls ``agent.run(run_input)``,
       which is MAF's buffered run."""
    turn = _parse(_TURN)
    worker = next(n for n in turn.body if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "run_worker")
    assert "run_agent" in _called(worker)
    assert "run_agent_stream" not in _called(turn)
    for sub in ast.walk(turn):
        if isinstance(sub, ast.Call):
            assert "stream" not in {k.arg for k in sub.keywords}, ast.unparse(sub)

    tree = _parse(_EXECUTOR)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    reach, stack = set(), ["run_agent"]
    while stack:
        name = stack.pop()
        if name in reach or name not in fns:
            continue
        reach.add(name)
        stack.extend(c for c in _called(fns[name]) if c in fns)
    # Non-vacuity: the batch path runs the MAF agent.
    assert {"_run_agent_inner", "_run_with_maf_agent"} <= reach
    assert not {"run_agent_stream", "_run_sub_agent_streaming", "_open_native_stream",
                "_run_copilot_attempt"} & reach
    for name in sorted(reach):
        for sub in ast.walk(fns[name]):
            if isinstance(sub, ast.Call):
                assert "stream" not in {k.arg for k in sub.keywords}, (name, ast.unparse(sub))


def test_the_buffered_console_path_writes_the_row_before_it_answers() -> None:
    """The other half of the rule: on the buffered chat path of the Console,
    ``_record_completion`` runs in the body of the route, after the provider
    call and before the last ``return``. It commits its own transaction, so
    the row is in the database when the gateway reads the answer."""
    tree = _parse(_CONSOLE_MAIN)
    route = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name == "chat_completions")
    body = route.body
    records = [i for i, stmt in enumerate(body) if isinstance(stmt, ast.Expr)
               and isinstance(stmt.value, ast.Call)
               and isinstance(stmt.value.func, ast.Name)
               and stmt.value.func.id == "_record_completion"]
    assert len(records) == 1, "the buffered path has one usage write in the route body"
    assert isinstance(body[-1], ast.Return)
    assert records[0] < len(body) - 1
    calls = [i for i, stmt in enumerate(body) if "call_chain" in _called(stmt)]
    assert calls and calls[-1] < records[0]
