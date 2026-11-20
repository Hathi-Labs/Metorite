"""The dry-run door of a skill test (WS-STU F4a, F4b). Hermetic: no live model,
no database. The real MAF agent here talks to a scripted transport only.

Code: ``acb_skills/skill_dry_run.py``. It proves that a held call never runs,
that its arguments and its schema check are recorded, that only a captured
tool on the frozen allowlist runs, that the hold is the OUTERMOST function
middleware of a real agent, that the cap guard refuses before a model call,
and that the mark has one setter.

**The mutations (R7).**

* In ``_may_run``, test the name only (drop ``_captured`` and the identity
  test). ``test_a_borrowed_name_is_held`` goes red.
* Add ``name in DISPLAY_TOOLS`` as a run condition in ``_may_run``.
  ``test_a_display_tool_is_held_also_when_captured`` goes red.
* In ``HoldAndRecord.process``, record ``valid=True`` for every call.
  ``test_a_call_that_fails_the_schema_is_invalid`` goes red.
* In ``attach``, append the hold to the END of ``middleware``.
  ``test_the_hold_runs_before_an_agent_middleware_on_a_real_agent`` goes red.
* In ``model_call_refusal``, drop the ``armed`` test.
  ``test_an_unarmed_record_refuses_each_model_call`` goes red.
"""
from __future__ import annotations

import ast
import asyncio
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from acb_skills import skill_dry_run as sdr
from acb_skills.egress import DELEGATION_TOOLS
from acb_skills.skill_dry_run import (
    HELD_TEXT,
    REAL_READS,
    CapGuard,
    DryRun,
    DryRunRefused,
    DryRunStopped,
    HoldAndRecord,
    attach,
    check_args,
    current_dry_run,
    dry_run_scope,
    model_call_refusal,
    tool_identity,
)
from acb_skills.subagent_contract import DISPLAY_TOOLS, EXTRA_CALL_TOOLS, MEMBER_TOOLS
from agent_framework import Agent, FunctionMiddleware, FunctionTool

_ROOT = Path(__file__).resolve().parents[2]


# -- helpers -------------------------------------------------------------------


class _Spy:
    def __init__(self) -> None:
        self.ran = 0

    async def next(self) -> None:
        self.ran += 1


def _go(mw: HoldAndRecord, function: Any, args: Any = None) -> tuple[_Spy, Any]:
    spy = _Spy()
    if isinstance(function, str):
        function = SimpleNamespace(name=function)
    ctx = SimpleNamespace(function=function, arguments=args, result=None)
    asyncio.run(mw.process(ctx, spy.next))
    return spy, ctx


def _pack(*tools: str) -> SimpleNamespace:
    return SimpleNamespace(slug="skill:quotes", tools=tools)


def _armed(*tools: str, **kw: Any) -> DryRun:
    return DryRun(pack=_pack(*tools), armed=True, **kw)


async def _create_lead(company: str, owner: str = "") -> str:
    return company


def _tool(fn: Any, name: str | None = None, **props: Any) -> FunctionTool:
    return FunctionTool(func=fn, name=name or fn.__name__, description="d",
                        additional_properties=props or None)


def _real_read_page() -> FunctionTool:
    from acb_skills import knowledge_tools

    return _tool(knowledge_tools.read_page, "read_page")


# -- the hold (F4a) ------------------------------------------------------------


def test_a_held_write_never_runs() -> None:
    record = _armed("create_lead")
    spy, ctx = _go(HoldAndRecord(record), _tool(_create_lead, "create_lead"), {"company": "A"})
    assert spy.ran == 0 and ctx.result == HELD_TEXT
    assert record.calls[0].held is True and record.calls[0].write is True


def test_the_arguments_are_recorded() -> None:
    record = _armed()
    _go(HoldAndRecord(record), "create_lead", {"company": "Acme", "n": 2})
    call = record.calls[0]
    assert call.args == {"company": "Acme", "n": 2} and call.action == "tool:create_lead"


def test_a_model_shaped_argument_is_recorded_as_a_dict() -> None:
    class _Args:
        def model_dump(self) -> dict[str, Any]:
            return {"company": "Acme"}

    record = _armed()
    _go(HoldAndRecord(record), "create_lead", _Args())
    assert record.calls[0].args == {"company": "Acme"}


def test_an_unarmed_door_holds_every_call() -> None:
    tool = _real_read_page()
    record = DryRun(pack=_pack("read_page"), real=(tool,))
    spy, _ = _go(HoldAndRecord(record), tool)
    assert spy.ran == 0 and record.calls[0].held is True


def test_no_record_holds_every_call() -> None:
    spy, ctx = _go(HoldAndRecord(), _real_read_page())
    assert spy.ran == 0 and ctx.result == HELD_TEXT


def test_the_call_cap_holds_and_names_the_stop() -> None:
    tool = _real_read_page()
    record = _armed("read_page", call_cap=1, real=(tool,))
    mw = HoldAndRecord(record)
    first, _ = _go(mw, tool)
    second, _ = _go(mw, tool)
    assert (first.ran, second.ran) == (1, 0) and record.stopped == "call_cap"


def test_the_held_text_is_fixed_and_holds_no_argument() -> None:
    record = _armed()
    _, ctx = _go(HoldAndRecord(record), "create_lead", {"api_key": "sk-secret-123"})
    assert ctx.result == HELD_TEXT and "sk-secret-123" not in ctx.result


def test_the_mark_is_read_inside_the_scope_only() -> None:
    record = _armed()
    assert current_dry_run() is None
    with dry_run_scope(record):
        assert current_dry_run() is record
        spy, _ = _go(HoldAndRecord(), "create_lead")
        assert spy.ran == 0 and len(record.calls) == 1
    assert current_dry_run() is None


# -- the allowlist (F4b) -------------------------------------------------------

#: The reason of each entry of ``REAL_READS``. A new entry fails
#: ``test_the_allowlist_is_the_reasoned_list`` until it has a line here. Only a
#: GET read of a first-party store may join (F4.md).
REASONS: dict[str, tuple[str, str]] = {
    "skill_projects.reads.list_tasks": (
        "list_tasks", "GET /projects/tasks and GET of the status names, via client.get only"),
    "skill_projects.reads.task_detail": (
        "task_detail", "GET of one task, its relations, refs, attachments and timeline"),
    "skill_projects.reads.my_work": (
        "my_work", "GET of the member's inbox, assigned tasks, plan order and own project"),
    "acb_skills.knowledge_tools.read_page": (
        "read_page", "GET /knowledge/pages/{id}, a SELECT only (load_page with no lock). "
        "It closes the run's profile writes first"),
}


def test_the_allowlist_is_the_reasoned_list() -> None:
    assert dict(REAL_READS) == {k: v[0] for k, v in REASONS.items()}, (
        "A tool that runs for real in a skill test needs a reason in REASONS. "
        "Only a GET read of a first-party store may join.")


def test_the_allowlist_is_frozen() -> None:
    with pytest.raises(TypeError):
        REAL_READS["x.y"] = "y"  # type: ignore[index]


@pytest.mark.parametrize("identity", sorted(REASONS))
def test_each_entry_names_a_real_read_only_function(identity: str) -> None:
    import importlib

    module, _, qualname = identity.rpartition(".")
    fn = getattr(importlib.import_module(module), qualname)
    assert tool_identity(fn) == identity
    hints = getattr(fn, "__tool_risk__", None) or {}
    assert hints.get("read_only") is True and hints.get("open_world") is False, identity


#: The classes that never run for real. Each one is a test (F4.md).
HELD_CLASSES: dict[str, frozenset[str]] = {
    "display": frozenset(DISPLAY_TOOLS),
    "member": frozenset(MEMBER_TOOLS),
    "extra_call": frozenset(EXTRA_CALL_TOOLS),
    "delegation": frozenset(DELEGATION_TOOLS),
    "loader": frozenset({"load_skill_tools", "find_tools"}),
    "model_or_embed": frozenset({"analyze_image", "search_company", "crm_search_notes"}),
    "memory": frozenset({"remember", "recall_timeline", "recall_facts", "query_history"}),
    "diagnostics": frozenset({"get_errors", "run_diagnostics"}),
    "post_read": frozenset({"studio_record_list"}),
    # GET /knowledge/spaces inserts the company space on its first call.
    "route_writes": frozenset({"list_spaces"}),
    "no_action": frozenset({"crm_list_records", "crm_get_record", "org_facts"}),
}


@pytest.mark.parametrize("cls", sorted(HELD_CLASSES))
def test_no_held_class_is_on_the_allowlist(cls: str) -> None:
    assert not HELD_CLASSES[cls] & set(REAL_READS.values()), cls


@pytest.mark.parametrize("cls", sorted(HELD_CLASSES))
def test_each_held_class_is_held_when_the_skill_names_it(cls: str) -> None:
    names = sorted(HELD_CLASSES[cls])
    funcs = [SimpleNamespace(name=n) for n in names]
    record = _armed(*names, real=tuple(funcs))
    for fn in funcs:
        spy, ctx = _go(HoldAndRecord(record), fn)
        assert spy.ran == 0 and ctx.result == HELD_TEXT, fn.name


def test_a_captured_allowlisted_read_of_the_skill_runs() -> None:
    tool = _real_read_page()
    record = _armed("read_page", real=(tool,))
    spy, ctx = _go(HoldAndRecord(record), tool)
    assert spy.ran == 1 and ctx.result is None and record.calls[0].held is False


def test_a_borrowed_name_is_held() -> None:
    async def read_page(page_id: str) -> str:  # the right name, another function
        return "page"

    tool = _tool(read_page, "read_page")
    record = _armed("read_page", real=(tool,))
    spy, _ = _go(HoldAndRecord(record), tool)
    assert spy.ran == 0 and record.calls[0].held is True


def test_an_object_that_attach_did_not_capture_is_held() -> None:
    record = _armed("read_page", real=(_real_read_page(),))
    spy, _ = _go(HoldAndRecord(record), _real_read_page())  # a second object
    assert spy.ran == 0


def test_a_same_name_mcp_tool_is_held() -> None:
    from acb_skills import knowledge_tools

    tool = _tool(knowledge_tools.read_page, "read_page", _mcp_is_tool=True)
    record = _armed("read_page", real=(tool,))
    spy, _ = _go(HoldAndRecord(record), tool)
    assert spy.ran == 0 and record.calls[0].write is True


def test_an_allowlisted_read_outside_the_skill_is_held() -> None:
    tool = _real_read_page()
    record = _armed("list_tasks", real=(tool,))
    spy, _ = _go(HoldAndRecord(record), tool)
    assert spy.ran == 0


def test_a_display_tool_is_held_also_when_captured() -> None:
    names = sorted(DISPLAY_TOOLS)
    tools = [_tool(_create_lead, n) for n in names]
    record = _armed(*names, real=tuple(tools))
    for tool in tools:
        spy, ctx = _go(HoldAndRecord(record), tool, {"company": "A"})
        assert spy.ran == 0 and ctx.result == HELD_TEXT, tool.name


def test_a_held_read_is_no_write() -> None:
    tool = _real_read_page()
    record = _armed("list_tasks")  # not of the skill, so it is held
    _go(HoldAndRecord(record), tool)
    assert record.calls[0].held is True and record.calls[0].write is False


# -- the schema check (F4b) ----------------------------------------------------


def test_a_call_that_fails_the_schema_is_invalid() -> None:
    record = _armed()
    tool = _tool(_create_lead, "create_lead")
    _go(HoldAndRecord(record), tool, {"owner": "sk-secret-999"})
    call = record.calls[0]
    assert call.valid is False and call.invalid_keys == ("company",)
    assert "sk-secret-999" not in repr(call.invalid_keys)


def test_a_call_that_passes_the_schema_is_valid() -> None:
    record = _armed()
    _go(HoldAndRecord(record), _tool(_create_lead, "create_lead"), {"company": "Acme"})
    assert record.calls[0].valid is True and record.calls[0].invalid_keys == ()


def test_a_wrong_type_names_the_key_never_the_value() -> None:
    valid, keys = check_args(_tool(_create_lead), {"company": {"secret": "sk-1"}})
    assert valid is False and keys == ("company",)


def test_a_tool_with_no_schema_fails_closed() -> None:
    assert check_args(SimpleNamespace(name="x"), {"a": 1}) == (False, ())


def test_an_unexpected_key_is_invalid_when_the_schema_refuses_it() -> None:
    tool = FunctionTool(func=None, name="t", description="d", input_model={
        "type": "object", "properties": {"a": {"type": "string"}},
        "required": ["a"], "additionalProperties": False})
    assert check_args(tool, {"a": "x"}) == (True, ())
    assert check_args(tool, {"a": "x", "b": 1}) == (False, ("b",))
    assert check_args(tool, {"a": 5}) == (False, ("a",))


def test_the_bad_key_names_are_cut_and_capped() -> None:
    """The model chose the key names, so a record keeps at most 10 of them,
    each at most 64 characters."""
    tool = FunctionTool(func=None, name="t", description="d", input_model={
        "type": "object", "properties": {"a": {"type": "string"}},
        "additionalProperties": False})
    junk = {f"k{i:02d}" + "x" * 200: 1 for i in range(30)}
    valid, keys = check_args(tool, junk)
    assert valid is False and len(keys) == 10 and all(len(k) <= 64 for k in keys)


# -- the cap guard (F4b) -------------------------------------------------------


def _spend(value: Any) -> Any:
    async def read() -> Any:
        if isinstance(value, Exception):
            raise value
        return value
    return read


def _guard(record: DryRun | None) -> tuple[int, str | None]:
    sent: list[int] = []

    async def call_next() -> None:
        sent.append(1)

    try:
        asyncio.run(CapGuard(record).process(SimpleNamespace(), call_next))
    except DryRunStopped as exc:
        return len(sent), exc.reason
    return len(sent), None


def test_an_unarmed_record_refuses_each_model_call() -> None:
    record = DryRun(pack=_pack(), credit_cap=Decimal("5"), spend=_spend(Decimal(0)))
    assert _guard(record) == (0, "hold_unarmed")
    assert _guard(None) == (0, "hold_unarmed")


def test_no_cap_refuses_with_cap_unset() -> None:
    for cap in (None, Decimal(0), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")):
        record = _armed(credit_cap=cap, spend=_spend(Decimal(0)))
        assert _guard(record) == (0, "cap_unset"), cap


def test_a_meter_fault_refuses_and_never_reads_as_zero() -> None:
    for bad in (RuntimeError("down"), 0.5, None, Decimal("NaN"), Decimal("-1")):
        record = _armed(credit_cap=Decimal("5"), spend=_spend(bad))
        assert _guard(record) == (0, "cost_read_failed"), bad
    assert _guard(_armed(credit_cap=Decimal("5"))) == (0, "cost_read_failed")


def test_a_slow_meter_is_a_fault(monkeypatch) -> None:
    monkeypatch.setattr(sdr, "METER_TIMEOUT_S", 0.01)

    async def slow() -> Decimal:
        await asyncio.sleep(1)
        return Decimal(0)

    assert _guard(_armed(credit_cap=Decimal("5"), spend=slow)) == (0, "cost_read_failed")


def test_the_cap_refuses_at_the_cap_and_the_stop_is_sticky() -> None:
    record = _armed(credit_cap=Decimal("5"), spend=_spend(Decimal("5")))
    assert _guard(record) == (0, "cap_reached") and record.stopped == "cap_reached"
    record.spend = _spend(Decimal(0))  # a later read cannot reopen it
    assert _guard(record) == (0, "cap_reached")


def test_the_model_call_cap_holds_with_no_meter_help() -> None:
    record = _armed(credit_cap=Decimal("5"), spend=_spend(Decimal(0)), model_call_cap=2)
    assert [_guard(record) for _ in range(3)] == [(1, None), (1, None), (0, "call_cap")]
    assert record.model_calls == 2


def test_model_call_refusal_order() -> None:
    record = _armed(credit_cap=None, spend=None, model_call_cap=0)
    assert asyncio.run(model_call_refusal(record)) == "call_cap"


#: The chat middlewares that may call ``call_next`` more than once in one
#: pass. ``CapGuard`` is outermost, so it sees one request for each pass, and
#: a second request of such a pass has no meter read. The answer check and
#: the claim check share ONE retry for each run (``RunLedger.retried``). So
#: a test makes at most ``MODEL_CALL_CAP + 1`` model requests (F4.md).
_RETRYING_CHAT_MIDDLEWARE = frozenset({"CheckAnswers", "CheckClaims"})


def test_only_the_reviewed_chat_middleware_can_send_a_second_request() -> None:
    """Mutation: a new chat middleware calls ``call_next`` twice → red, so the
    bound of F4.md is checked again before it ships."""
    found: set[str] = set()
    for base in ("apps/services/orchestrator/orchestrator", "packages/acb_skills/acb_skills",
                 "packages/acb_llm/acb_llm"):
        for path in (_ROOT / base).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for cls in ast.walk(tree):
                if not isinstance(cls, ast.ClassDef) or not any(
                        getattr(b, "id", getattr(b, "attr", "")) == "ChatMiddleware"
                        for b in cls.bases):
                    continue
                # Each method of the class: a helper can call it too.
                sites = [n for n in ast.walk(cls) if isinstance(n, ast.Call)
                         and getattr(n.func, "id", "") == "call_next"]
                loops = [n for n in ast.walk(cls) if isinstance(n, ast.For | ast.While)
                         and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "call_next"
                                 for c in ast.walk(n))]
                if len(sites) > 1 or loops:
                    found.add(cls.name)
    assert found == _RETRYING_CHAT_MIDDLEWARE, found
    text = (_ROOT / "packages/acb_skills/acb_skills/answer_check.py").read_text(encoding="utf-8")
    assert "if not self._ledger.retried:" in text and "self._ledger.retried = True" in text


# -- the attach (F4b) ----------------------------------------------------------


def _agent(tools: list[Any], middleware: list[Any] | None = None, model: Any = None) -> Agent:
    import openai
    from agent_framework.openai import OpenAIChatCompletionClient

    transport = httpx.MockTransport(model or (lambda _r: httpx.Response(500)))
    client = OpenAIChatCompletionClient(model="m", async_client=openai.AsyncOpenAI(
        base_url="http://127.0.0.1:9/v1", api_key="k", max_retries=0,
        http_client=httpx.AsyncClient(transport=transport)))
    return Agent(client=client, name="orchestrator", instructions="x", tools=tools,
                 middleware=middleware)


class _Probe(FunctionMiddleware):
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def process(self, context: Any, call_next: Any) -> None:
        self.seen.append(str(getattr(context.function, "name", "")))
        await call_next()


def _pool(agent: Any) -> list[Any]:
    return list(agent.default_options.get("tools") or [])


def test_attach_puts_the_hold_first_and_arms_last() -> None:
    probe = _Probe()
    tool = _real_read_page()
    agent = _agent([tool, _tool(_create_lead, "create_lead")], [probe])
    agents = [agent]
    record = DryRun(pack=_pack("read_page", "create_lead"), loaded=True)
    attach(agents, record, native=Agent, tools_of=_pool)
    view = agents[0]
    assert view is not agent and list(agent.middleware) == [probe]
    assert isinstance(view.middleware[0], HoldAndRecord) and isinstance(view.middleware[1], CapGuard)
    assert view.middleware[2] is probe and record.armed is True
    assert record.real[0] is tool and len(record.real) == 2  # the tool and its func


def test_attach_refuses_an_agent_that_cannot_take_the_hold() -> None:
    record = DryRun(pack=_pack(), loaded=True)
    for agents in ([], [object()], [SimpleNamespace(_permission_handler=1)]):
        with pytest.raises(DryRunRefused):
            attach(list(agents), record, native=Agent, tools_of=_pool)
    assert record.armed is False


def test_attach_refuses_a_skill_that_did_not_load() -> None:
    """So a run that cannot test the skill starts no model call."""
    agents = [_agent([])]
    record = DryRun(pack=_pack())
    with pytest.raises(DryRunRefused):
        attach(agents, record, native=Agent, tools_of=_pool)
    assert record.armed is False and isinstance(agents[0], Agent) and not agents[0].middleware


def test_attach_refuses_a_client_middleware(monkeypatch) -> None:
    agent = _agent([])
    monkeypatch.setattr(agent.client, "function_middleware", [_Probe()], raising=False)
    record = DryRun(pack=_pack(), loaded=True)
    with pytest.raises(DryRunRefused):
        attach([agent], record, native=Agent, tools_of=_pool)
    assert record.armed is False


async def test_the_hold_runs_before_an_agent_middleware_on_a_real_agent() -> None:
    """A real MAF ``Agent`` with a scripted transport. The probe stands for
    ``McpPermissionGate`` in ``agent.middleware``. A held call never reaches
    it, and the record has the call. Two model requests pass the guard."""
    from tests.unit._native_maf_harness import ScriptedModel, text_turn, tool_turn

    ran: list[str] = []

    async def create_lead(company: str) -> str:
        ran.append(company)
        return "made"

    model = ScriptedModel([tool_turn("create_lead", '{"company": "Acme"}'), text_turn("Done.")])
    probe = _Probe()
    agents = [_agent([create_lead], [probe], model)]
    record = DryRun(pack=_pack("create_lead"), credit_cap=Decimal("5"),
                    spend=_spend(Decimal(0)), loaded=True)
    attach(agents, record, native=Agent, tools_of=_pool)
    async for _update in agents[0].run("Add Acme", stream=True):
        pass
    assert probe.seen == [] and ran == []
    assert [(c.tool, c.held, dict(c.args)) for c in record.calls] == [
        ("create_lead", True, {"company": "Acme"})]
    assert record.model_calls == 2 == len(model.bodies)


async def test_a_stop_ends_the_real_run_before_the_request() -> None:
    from tests.unit._native_maf_harness import ScriptedModel, text_turn

    model = ScriptedModel([text_turn("Done.")])
    agents = [_agent([], None, model)]
    record = DryRun(pack=_pack(), credit_cap=Decimal("5"), spend=_spend(Decimal("9")),
                    loaded=True)
    attach(agents, record, native=Agent, tools_of=_pool)
    with pytest.raises(Exception):  # noqa: B017 - MAF may wrap the stop
        async for _update in agents[0].run("hi", stream=True):
            pass
    assert model.bodies == [] and record.stopped == "cap_reached"


# -- the fence: one setter ---------------------------------------------------------

#: The ONE setter, and its private state.
_SETTER_NAMES = frozenset({"dry_run_scope", "_DRY_RUN"})
#: Who may name the setter, and how often each calls it. F4b adds the one run
#: boundary: ``run_suite`` sets the mark around each test, once in its code.
_SETTERS = {
    "packages/acb_skills/acb_skills/skill_dry_run.py": 0,
    "apps/services/gateway/gateway/studio/skill_tests.py": 1,
}


def _names_in(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
    return found


def _trees() -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for root in ("apps", "packages"):
        for p in (_ROOT / root).rglob("*.py"):
            if {".venv", "node_modules", "__pycache__"} & set(p.parts):
                continue
            out[p.relative_to(_ROOT).as_posix()] = ast.parse(
                p.read_text(encoding="utf-8-sig"), filename=str(p))
    return out


def test_only_the_listed_modules_name_the_setter() -> None:
    named = {path for path, tree in _trees().items() if _names_in(tree) & _SETTER_NAMES}
    assert named == set(_SETTERS), (
        "A new setter of the dry-run mark needs a reason and a line in this fence. "
        "Only server code at a run boundary may set it.")


def test_each_listed_module_calls_the_setter_the_listed_number_of_times() -> None:
    for path, count in _SETTERS.items():
        tree = ast.parse((_ROOT / path).read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "dry_run_scope"]
        assert len(calls) == count, path
