"""F4 and F5: the Projects assistant can act on a refusal (WS-46 P2, D91.3).

Spec: ``project-docs/specs/projects_agent_parity.md`` §6.5, §7.5, §7.6.

**F4.** MAF answers a tool that raises with "Error: Function failed." So the
model never read the 422 that named the bad field. Every exported tool now
joins the agent wrapped in ``refusals_as_text``, and a ``GatewayRefusal``
becomes text: "Refused:", the gateway's safe detail, and a "Next:" line.
This file holds that every registered tool carries the wrapper, and drives
a 404, a 403 and a 422 through the fake gateway. It also holds the safety
half: no route path, no URL or DSN, no stack, and no 5xx body reach the
model, and an exception that is not a refusal still raises.

**F5.** MAF builds a tool's input model from its signature with no
``extra="forbid"``, so an argument the tool does not declare vanished and
the call succeeded. Every registered tool now has a strict input model, and
a call with an unknown argument is refused by name before any request.

The last two tests run the REAL executor with the real factory agent, and
read the tool result in the model's next request. That is the text the
model reads, so it is the claim of D91.3 itself.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")
pytest.importorskip("agent_framework", reason="agent_framework not installed")

import skill_projects
from skill_projects.client import safe_detail
from skill_projects.priority import IMPORTANCE_REMOVED
from skill_projects.refusals import (
    REFUSED,
    WRAPPED_ATTR,
    refusals_as_text,
)

from tests.unit._native_maf_harness import (
    ScriptedModel,
    _a_tenant,  # noqa: F401 — a fixture, used by name
    drive_native,
    text_turn,
    tool_turn,
)
from tests.unit._projects_agent_fakes import (
    FakeResponse,
    approve,
    fake_gateway,
    load_agent_module,
    writes,
)

_M = load_agent_module("projects_assistant_agent_refusals")

TASK = "11111111-1111-1111-1111-111111111111"
PROJECT = "22222222-2222-2222-2222-222222222222"


def _tools() -> dict[str, Any]:
    """The Projects tools as the built agent holds them, by name."""
    agent = _M.build_agents()[0]
    held = {t.name: t for t in agent.default_options["tools"] if hasattr(t, "name")}
    return {name: held[name] for name in skill_projects.__all__}


def _text(result: Any) -> str:
    """The text of a tool's ``invoke`` result, as the model reads it."""
    if isinstance(result, str):
        return result
    return "\n".join(str(getattr(c, "text", "") or "") for c in result)


async def _call(name: str, **arguments: Any) -> str:
    return _text(await _tools()[name].invoke(arguments=arguments))


def _refuse(status: int, body: Any) -> FakeResponse:
    return FakeResponse(body, status_code=status)


# ── F4: the wrapper is on every tool ─────────────────────────────────────────


def test_every_exported_tool_answers_a_refusal_as_text() -> None:
    """F4, part 1. Each tool the agent registers runs through the wrapper."""
    tools = _tools()
    assert set(tools) == set(skill_projects.__all__)
    bare = [name for name, t in tools.items() if not getattr(t.func, WRAPPED_ATTR, False)]
    assert not bare, f"tools without refusals_as_text: {bare}"


def test_the_wrapper_keeps_the_name_the_signature_and_the_annotation() -> None:
    """The egress rule reads ``__tool_risk__`` (H-236), and MAF reads the
    signature. The wrapper keeps both, so the covered list does not move."""
    for name, t in _tools().items():
        raw = getattr(skill_projects, name)
        assert t.func.__name__ == name
        assert inspect.signature(t.func) == inspect.signature(raw), name
        assert getattr(t.func, "__tool_risk__", None) == raw.__tool_risk__, name
        assert t.func.__tool_risk__.get("open_world") is False, name


# ── F4: a 404, a 403 and a 422 reach the model with the gateway's detail ─────


async def test_a_404_reaches_the_model_with_its_detail(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, lambda _c: _refuse(404, {"detail": "Task not found."}))
    text = await _call("task_detail", task_id=TASK)
    assert text.startswith(REFUSED), text
    assert "Not found, or not visible to you" in text
    assert "Gateway said: «Task not found.»" in text
    assert "Next:" in text
    assert "/projects/" not in text, "the route path reached the model"
    assert len(calls) == 1


async def test_a_403_after_the_card_reaches_the_model_with_its_detail(monkeypatch) -> None:
    def answer(call: dict) -> Any:
        if call["method"] == "GET":
            return {"id": TASK, "task_number": 7, "title": "Fix the extruder"}
        return _refuse(403, {"detail": "Only a member with edit access may comment."})

    calls = fake_gateway(monkeypatch, answer)
    approve(monkeypatch)
    text = await _call("comment", task_id=TASK, body="Looks good")
    assert text.startswith(REFUSED), text
    assert "Not permitted (403)" in text
    assert "«Only a member with edit access may comment.»" in text
    assert "do not try again" in text
    assert [c["method"] for c in writes(calls)] == ["POST"]


async def test_a_422_names_the_field_and_drops_the_input_and_the_url(monkeypatch) -> None:
    body = {"detail": [{
        "type": "datetime_parsing",
        "loc": ["body", "due_at"],
        "msg": "Input should be a valid datetime",
        "input": "2026-13-45",
        "url": "https://errors.pydantic.dev/2.11/v/datetime_parsing",
    }]}
    fake_gateway(monkeypatch, lambda _c: _refuse(422, body))
    approve(monkeypatch)
    text = await _call("create_task", project_id=PROJECT, title="Send the timesheet", due="2026-13-45")
    assert text.startswith(REFUSED), text
    assert "«due_at: Input should be a valid datetime»" in text
    assert "Next: Fix the value for due_at" in text
    assert "pydantic.dev" not in text and "2026-13-45" not in text


async def test_a_refusal_of_the_client_itself_is_text_too(monkeypatch) -> None:
    """A bad id never reaches the wire, and the model reads why."""
    calls = fake_gateway(monkeypatch, {})
    text = await _call("task_detail", task_id="task seven")
    assert text.startswith(f"{REFUSED} task_id must be a UUID"), text
    assert "Next:" in text
    assert calls == []


# ── F4: the safe half ────────────────────────────────────────────────────────


async def test_a_503_says_try_again(monkeypatch) -> None:
    """PR #652 made an outage a 503. The status stays, and the model is told
    to try again, with the gateway's own outage sentence."""
    said = "We could not reach the directory. Try again in a moment."
    fake_gateway(monkeypatch, lambda _c: _refuse(503, {"detail": said, "code": "identity_unavailable"}))
    text = await _call("task_detail", task_id=TASK)
    assert "(503)" in text and "Try again in a moment" in text
    assert f"«{said}»" in text


@pytest.mark.parametrize("status", [500, 502])
async def test_a_5xx_body_never_reaches_the_model(status: int, monkeypatch) -> None:
    leak = "relation pm_tasks: SELECT * FROM pm_tasks at postgresql://acb:pw@10.0.0.5/acb"
    fake_gateway(monkeypatch, lambda _c: _refuse(status, {"detail": leak}))
    text = await _call("task_detail", task_id=TASK)
    assert text.startswith(REFUSED) and f"({status})" in text
    assert "Gateway said" not in text
    assert "SELECT" not in text and "postgresql" not in text and "10.0.0.5" not in text


@pytest.mark.parametrize(
    ("body", "absent"),
    [
        ({"detail": "Bad link postgresql://acb:pw@db.internal:5432/acb here"}, "db.internal"),
        ({"detail": "Proxy at http://127.0.0.1:8080/projects refused"}, "127.0.0.1"),
        ({"detail": "Use Bearer sk-live-abcdef0123456789 for this"}, "abcdef0123456789"),
        ({"detail": 'Traceback (most recent call last): File "core.py"'}, "core.py"),
        ({"detail": "(psycopg.errors.UniqueViolation) duplicate key"}, "UniqueViolation"),
    ],
)
def test_a_4xx_detail_loses_every_url_secret_and_stack(body: dict, absent: str) -> None:
    detail, _fields = safe_detail(400, body)
    assert absent not in detail, detail


def test_a_body_that_is_not_json_gives_no_detail() -> None:
    assert safe_detail(400, None) == ("", ())


def test_a_long_detail_is_cut() -> None:
    detail, _ = safe_detail(400, {"detail": "x " * 400})
    assert len(detail) <= 300


async def test_an_exception_that_is_not_a_refusal_still_raises() -> None:
    """MAF then answers "Error: Function failed." and the log keeps the
    trace. A stray error's own text can carry a stack or a query."""

    async def broken() -> str:
        raise RuntimeError("SELECT secret FROM pm_tasks")

    with pytest.raises(RuntimeError):
        await refusals_as_text(broken)()


# ── F5: an unknown argument is refused by name ───────────────────────────────


def test_every_tool_has_a_strict_input_model() -> None:
    """F5, part 1. The model reads ``additionalProperties: false``."""
    for name, t in _tools().items():
        assert t.input_model is not None, name
        assert t.input_model.model_config.get("extra") == "forbid", name
        assert t.parameters().get("additionalProperties") is False, name


def test_the_strict_model_keeps_every_argument_of_the_signature() -> None:
    """The strict model is MAF's own model plus ``extra="forbid"``. It drops
    no argument, so a call that worked before works now."""
    for name, t in _tools().items():
        declared = set(inspect.signature(getattr(skill_projects, name)).parameters)
        assert set(t.input_model.model_fields) == declared, name


async def test_create_task_refuses_recurrence_by_name(monkeypatch) -> None:
    """F5, part 2. The invented argument of §4 step 6 is refused, and the
    refusal points at the repeat arguments that exist, or before P1 ships,
    at ``set_recurrence``. Nothing reaches the gateway."""
    calls = fake_gateway(monkeypatch, {})
    asked = approve(monkeypatch)
    text = await _call("create_task", project_id=PROJECT, title="Send the timesheet", recurrence="weekly")
    assert text.startswith(f"{REFUSED} create_task has no argument 'recurrence'"), text
    repeat_args = [
        p for p in inspect.signature(skill_projects.create_task).parameters if p.startswith("repeat")
    ]
    if repeat_args:
        for arg in repeat_args:
            assert arg in text, arg
    else:
        assert "set_recurrence" in text
    assert "project_id" in text and "title" in text
    assert calls == [] and asked == []


async def test_an_unknown_argument_offers_the_near_name(monkeypatch) -> None:
    fake_gateway(monkeypatch, {})
    text = await _call("task_detail", task_id=TASK, task_ids=TASK)
    assert "has no argument 'task_ids'" in text
    assert "Did you mean 'task_id' for 'task_ids'?" in text


async def test_a_hidden_argument_is_declared_not_unknown(monkeypatch) -> None:
    """``importance: Removed`` is out of the schema on purpose and still a
    declared field, so its own answer holds (priority.py)."""
    calls = fake_gateway(monkeypatch, {})
    approve(monkeypatch)
    text = await _call("create_task", project_id=PROJECT, title="Send the timesheet", importance=3)
    assert text == IMPORTANCE_REMOVED
    assert calls == []


# ── Through the real executor: what the model reads next ─────────────────────


def _tool_results(body: dict) -> list[str]:
    return [str(m.get("content") or "") for m in body.get("messages", []) if m.get("role") == "tool"]


@pytest.mark.usefixtures("_a_tenant")
def test_the_model_reads_the_refusal_of_an_unknown_argument(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, {})
    args = json.dumps({"project_id": PROJECT, "title": "Send the timesheet", "recurrence": "weekly"})
    model = ScriptedModel([tool_turn("create_task", args), text_turn("done")])
    drive_native("projects-assistant", "apps/agents/agent-projects", monkeypatch, model)
    assert len(model.bodies) >= 2, "the run never sent the tool result back"
    results = _tool_results(model.bodies[1])
    assert any("create_task has no argument 'recurrence'" in r for r in results), results
    assert calls == []


@pytest.mark.usefixtures("_a_tenant")
def test_the_model_reads_the_gateway_detail_of_a_refusal(monkeypatch) -> None:
    fake_gateway(monkeypatch, lambda _c: _refuse(404, {"detail": "Task not found."}))
    model = ScriptedModel([tool_turn("task_detail", json.dumps({"task_id": TASK})), text_turn("done")])
    drive_native("projects-assistant", "apps/agents/agent-projects", monkeypatch, model)
    results = _tool_results(model.bodies[1])
    assert any(r.startswith(REFUSED) and "«Task not found.»" in r for r in results), results
    assert not any("Function failed" in r for r in results), results
