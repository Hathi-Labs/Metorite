"""The dry-run door of a skill test (WS-STU F4a). Hermetic: no model, no database.

Code: ``acb_skills/skill_dry_run.py``. It proves that a held call never runs,
that its arguments are recorded, that a closed-world read runs, and that the
mark has one setter.

**The mutations (R7).**

* In ``HoldAndRecord.process``, call ``call_next()`` for every tool.
  ``test_a_held_write_never_runs`` goes red.
* In ``runs_for_real``, drop the ``open_world`` test.
  ``test_an_open_world_read_is_held`` goes red.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from acb_skills.skill_dry_run import (
    HELD_TEXT,
    DryRun,
    DryRunProvider,
    HoldAndRecord,
    current_dry_run,
    dry_run_scope,
    runs_for_real,
)

_ROOT = Path(__file__).resolve().parents[2]


class _Spy:
    def __init__(self) -> None:
        self.ran = 0

    async def next(self) -> None:
        self.ran += 1


def _go(mw: HoldAndRecord, tool: str, args: Any = None) -> tuple[_Spy, Any]:
    spy = _Spy()
    ctx = SimpleNamespace(function=SimpleNamespace(name=tool), arguments=args, result=None)
    asyncio.run(mw.process(ctx, spy.next))
    return spy, ctx


def _armed(**kw: Any) -> DryRun:
    return DryRun(pack="quotes", armed=True, **kw)


def test_a_held_write_never_runs() -> None:
    record = _armed()
    spy, ctx = _go(HoldAndRecord(record), "write_artifact", {"title": "T"})
    assert spy.ran == 0 and ctx.result == HELD_TEXT
    assert record.calls[0].held is True


def test_a_tool_with_no_registry_entry_is_held() -> None:
    record = _armed()
    spy, _ = _go(HoldAndRecord(record), "create_lead", {"company": "Acme"})
    assert spy.ran == 0 and record.calls[0].tool == "create_lead"


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


def test_a_closed_world_read_runs() -> None:
    record = _armed()
    spy, ctx = _go(HoldAndRecord(record), "recall_timeline", {"q": "x"})
    assert spy.ran == 1 and ctx.result is None
    assert record.calls[0].held is False


def test_an_open_world_read_is_held() -> None:
    record = _armed()
    spy, _ = _go(HoldAndRecord(record), "web_search", {"q": "x"})
    assert spy.ran == 0 and record.calls[0].held is True


def test_the_member_delegation_and_loader_tools_are_held() -> None:
    for tool in ("ask_user", "ask_questions", "decide", "call_agent", "spawn_subagents",
                 "load_skill_tools"):
        assert runs_for_real(tool) is False, tool


def test_an_unarmed_door_holds_every_call() -> None:
    record = DryRun(pack="quotes")
    spy, _ = _go(HoldAndRecord(record), "recall_timeline")
    assert spy.ran == 0 and record.calls[0].held is True


def test_no_record_holds_every_call() -> None:
    spy, ctx = _go(HoldAndRecord(), "recall_timeline")
    assert spy.ran == 0 and ctx.result == HELD_TEXT


def test_the_call_cap_holds_and_names_the_stop() -> None:
    record = _armed(call_cap=1)
    mw = HoldAndRecord(record)
    first, _ = _go(mw, "recall_timeline")
    second, _ = _go(mw, "recall_timeline")
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


def test_the_provider_adds_the_middleware() -> None:
    added: list[Any] = []
    ctx = SimpleNamespace(extend_middleware=lambda sid, mw: added.append((sid, mw)))
    asyncio.run(DryRunProvider(_armed()).before_run(
        agent=None, session=None, context=ctx, state={}))
    assert added[0][0] == "skill_dry_run" and isinstance(added[0][1][0], HoldAndRecord)


# -- the fence: one setter ---------------------------------------------------------

#: The ONE setter, and its private state.
_SETTER_NAMES = frozenset({"dry_run_scope", "_DRY_RUN"})
#: Who may name the setter, and how often each calls it. F4b adds the one run
#: boundary that arms a run, with a reason and a count of 1.
_SETTERS = {
    "packages/acb_skills/acb_skills/skill_dry_run.py": 0,
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
