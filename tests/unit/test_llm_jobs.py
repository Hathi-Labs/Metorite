"""Every in-product AI call names its job (task 1128, owner 2026-10-31).

DeepSeek V4 thinks before it answers, and the vendor counts the thinking
against ``max_tokens``. With a small cap the model used the whole cap on
thinking and returned no text. The day planner stopped at 1200 of 1200
tokens on 8 of 8 calls, and the CRM digest at 800 of 800 on 6 of 6.
``acb_llm.jobs`` holds the policy: a job turns thinking off, or lifts the cap.

What this file holds (R7):

* The job vocabulary and :func:`acb_llm.jobs.apply_job`.
* The vendor rule: a thinking parameter reaches only a model that takes it,
  off the Router (``params_for_model``) and on it (the Console, per step).
* The JSON guard and the empty-answer log, at each of the three seams.
* **The fence.** It walks the gateway, the orchestrator and the ``acb_*``
  packages. A model call with a cap under 2000 must name a job, or send
  ``thinking`` or ``reasoning_effort`` itself.

Mutations this file catches:

* Remove ``job=`` from a call with a small cap: the fence fails.
* ``apply_job`` sends thinking for a judgement job, or overwrites a caller's
  own thinking parameter: two tests fail.
* The seam sends ``thinking`` to OpenAI off the Router: one test fails.
* The Console drops the step filter from its chat path: one test fails.
* The seam stops logging an empty answer: three tests fail.
"""
from __future__ import annotations

import ast
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from acb_llm import jobs
from acb_llm.jobs import (
    JOBS,
    JSON_ASK,
    JUDGEMENT_MIN_TOKENS,
    THINKING_OFF,
    apply_job,
    ensure_json_word,
    model_takes,
    note_empty_answer,
    params_for_model,
)

ROOT = Path(__file__).resolve().parents[2]


# ── The job vocabulary ──────────────────────────────────────────────────────


def test_three_jobs_with_their_tiers() -> None:
    assert {name: p.tier for name, p in JOBS.items()} == {
        "structured": "tier-fast",
        "writing": "tier-balanced",
        "judgement": "tier-powerful",
    }
    assert jobs.default_tier("judgement") == "tier-powerful"


def test_an_unknown_job_raises() -> None:
    with pytest.raises(ValueError, match="unknown AI job"):
        apply_job("summarise", max_tokens=100, extra={})


@pytest.mark.parametrize("job", ["structured", "writing"])
def test_a_short_job_turns_thinking_off_and_keeps_the_cap(job: str) -> None:
    extra = {"response_format": {"type": "json_object"}}
    cap, out = apply_job(job, max_tokens=120, extra=extra)
    assert cap == 120
    assert out["thinking"] == THINKING_OFF
    assert out["response_format"] == {"type": "json_object"}
    assert "thinking" not in extra, "the caller's dict must not change"


def test_judgement_keeps_thinking_and_lifts_the_cap() -> None:
    cap, out = apply_job("judgement", max_tokens=2200, extra={})
    assert cap == JUDGEMENT_MIN_TOKENS >= 6000
    assert "thinking" not in out and "reasoning_effort" not in out
    assert apply_job("judgement", max_tokens=8000, extra={})[0] == 8000


@pytest.mark.parametrize("own", [
    {"thinking": {"type": "enabled"}},
    {"reasoning_effort": "high"},
])
def test_the_callers_own_thinking_parameter_wins(own: dict[str, Any]) -> None:
    _cap, out = apply_job("structured", max_tokens=100, extra=dict(own))
    assert out == own


def test_no_job_changes_nothing_and_a_second_apply_is_a_no_op() -> None:
    extra = {"x": 1}
    assert apply_job(None, max_tokens=50, extra=extra) == (50, extra)
    once = apply_job("judgement", max_tokens=100, extra={})
    assert apply_job("judgement", max_tokens=once[0], extra=once[1]) == once


# ── The vendor rule (measured on litellm 1.103.0, see jobs docstring) ───────


@pytest.mark.parametrize(("model", "takes"), [
    ("deepseek/deepseek-v4-pro", True),
    ("deepseek/deepseek-flash", True),
    ("anthropic/claude-sonnet-4-5", True),
    ("openai/gpt-4o", False),
    ("groq/llama-3.3-70b-versatile", False),
    ("gemini/gemini-2.0-flash", False),
    ("aimlapi/typesafe/jev", False),
])
def test_which_model_takes_thinking(model: str, takes: bool) -> None:
    assert model_takes(model, "thinking") is takes


def test_the_direct_path_drops_thinking_for_a_model_that_refuses_it() -> None:
    extra = {"thinking": THINKING_OFF, "response_format": {"type": "json_object"}}
    assert params_for_model(extra, "deepseek/deepseek-v4-pro") is extra
    assert params_for_model(extra, "openai/gpt-4o") == {
        "response_format": {"type": "json_object"}}


def test_the_console_drops_thinking_per_step() -> None:
    from customer_console.reasoning import reasoning_params_for

    kwargs = {"messages": [], "thinking": THINKING_OFF, "reasoning_effort": "none"}
    assert reasoning_params_for(kwargs, "deepseek/deepseek-v4-pro") is kwargs
    assert reasoning_params_for(kwargs, "openai/gpt-4o") == {"messages": []}
    assert reasoning_params_for(kwargs, "aimlapi/typesafe/jev") == {"messages": []}


def test_the_console_chat_path_runs_the_step_filter() -> None:
    """Structural: the chat route's ``_kwargs_for`` builds each step's body
    with ``reasoning_params_for(passthrough, step.model)``. A behaviour test
    of the route needs the Console database (R8), so this pins the wiring."""
    src = (ROOT / "apps/services/customer_console/customer_console/main.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    hits = []
    for fn in ast.walk(tree):
        if not (isinstance(fn, ast.FunctionDef) and fn.name == "_kwargs_for"):
            continue
        for call in ast.walk(fn):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "reasoning_params_for"):
                hits.append(ast.unparse(call))
    assert hits == ["reasoning_params_for(passthrough, step.model)"]


# ── The JSON guard ──────────────────────────────────────────────────────────


_JSON = {"type": "json_object"}


def test_json_mode_with_no_json_word_gets_one_sentence() -> None:
    messages = [{"role": "system", "content": "Decide yes or no."},
                {"role": "user", "content": "the sender"}]
    out = ensure_json_word(messages, _JSON)
    assert out[0]["content"] == f"Decide yes or no.\n\n{JSON_ASK}"
    assert out[1] == messages[1]
    assert messages[0]["content"] == "Decide yes or no.", "no change in place"


def test_json_mode_with_no_system_message_gets_one() -> None:
    out = ensure_json_word([{"role": "user", "content": "x"}], _JSON)
    assert out[0] == {"role": "system", "content": JSON_ASK}


@pytest.mark.parametrize("messages", [
    [{"role": "system", "content": "Respond with ONLY a Json object."}],
    [{"role": "system", "content": "x"}, {"role": "user", "content": "send json"}],
    [{"role": "user", "content": [{"type": "text", "text": "as JSON"}]}],
])
def test_a_json_word_anywhere_needs_nothing(messages: list[dict[str, Any]]) -> None:
    assert ensure_json_word(messages, _JSON) is messages


@pytest.mark.parametrize("fmt", [None, {"type": "text"}, {"type": "json_schema"}])
def test_no_json_object_mode_needs_nothing(fmt: Any) -> None:
    messages = [{"role": "system", "content": "x"}]
    assert ensure_json_word(messages, fmt) is messages


# ── The empty-answer log ────────────────────────────────────────────────────


def _resp(content: Any, *, finish: str = "length", tokens: int = 500,
          tool_calls: Any = None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        model="deepseek/deepseek-v4-pro",
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        usage=SimpleNamespace(completion_tokens=tokens,
                              completion_tokens_details=SimpleNamespace(
                                  reasoning_tokens=tokens)))


def _empty_lines(caps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in caps if c["event"] == "acb_llm.empty_answer"]


@pytest.mark.parametrize("content", [None, "", "  \n "])
def test_an_empty_answer_logs_one_line_with_its_numbers(content: Any) -> None:
    with structlog.testing.capture_logs() as caps:
        assert note_empty_answer(_resp(content), model="m", job="writing",
                                 max_tokens=500)
    [line] = _empty_lines(caps)
    assert line["model"] == "deepseek/deepseek-v4-pro"
    assert line["finish_reason"] == "length"
    assert line["completion_tokens"] == 500 == line["reasoning_tokens"]
    assert line["job"] == "writing" and line["max_tokens"] == 500
    assert "agent" in line


def test_text_or_a_tool_call_is_an_answer() -> None:
    with structlog.testing.capture_logs() as caps:
        assert not note_empty_answer(_resp("OK", finish="stop"), model="m")
        assert not note_empty_answer(_resp(None, tool_calls=[{"id": "1"}]), model="m")
    assert _empty_lines(caps) == []


def test_the_log_never_raises() -> None:
    assert note_empty_answer(object(), model="m") is True
    assert note_empty_answer(SimpleNamespace(choices=[]), model="m") is True


# ── The three seams ─────────────────────────────────────────────────────────


@pytest.fixture()
def direct(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """``acompletion_with_fallback`` and ``complete`` off the Router, with a
    fake litellm that answers ``state["content"]``."""
    import acb_llm.client as client
    import acb_llm.routed as routed
    import litellm

    state: dict[str, Any] = {"calls": [], "content": ""}

    async def fake_acompletion(**kw: Any) -> Any:
        state["calls"].append(kw)
        message = {"role": "assistant", "content": state["content"]}
        return litellm.ModelResponse(
            model=kw["model"],
            choices=[{"index": 0, "message": message, "finish_reason": "length"}],
            usage={"prompt_tokens": 5, "completion_tokens": kw["max_tokens"],
                   "total_tokens": 5 + kw["max_tokens"]})

    async def no_keys() -> None:
        return None

    monkeypatch.setattr(routed, "routing_is_on", lambda: False)
    monkeypatch.setattr(client, "_ensure_keys_loaded", no_keys)
    monkeypatch.setattr(client, "ensure_model_registered", lambda _m: None)
    monkeypatch.setattr(client, "_emit_usage", lambda *_a, **_k: None)
    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(client, "acompletion", fake_acompletion)
    return state


async def test_the_direct_seam_sends_the_job_and_logs_an_empty_answer(direct) -> None:
    from acb_llm.context import acompletion_with_fallback

    with structlog.testing.capture_logs() as caps:
        resp, used = await acompletion_with_fallback(
            model="deepseek/deepseek-v4-pro",
            messages=[{"role": "system", "content": "Pick one."},
                      {"role": "user", "content": "x"}],
            max_tokens=200, response_format=_JSON, job="structured")
    [call] = direct["calls"]
    assert call["thinking"] == THINKING_OFF and call["max_tokens"] == 200
    assert JSON_ASK in call["messages"][0]["content"]
    assert used == "deepseek/deepseek-v4-pro" and resp.choices[0].message.content == ""
    [line] = _empty_lines(caps)
    assert line["job"] == "structured" and line["finish_reason"] == "length"


async def test_the_direct_seam_sends_no_thinking_to_openai(direct) -> None:
    from acb_llm.context import acompletion_with_fallback

    direct["content"] = "fine"
    with structlog.testing.capture_logs() as caps:
        await acompletion_with_fallback(
            model="openai/gpt-4o", messages=[{"role": "user", "content": "x"}],
            max_tokens=200, job="writing")
    [call] = direct["calls"]
    assert "thinking" not in call
    assert _empty_lines(caps) == []


async def test_a_judgement_job_gets_its_cap_floor(direct) -> None:
    from acb_llm.context import acompletion_with_fallback

    direct["content"] = '{"rules": []}'
    await acompletion_with_fallback(
        model="deepseek/deepseek-v4-pro", messages=[{"role": "user", "content": "x"}],
        max_tokens=2500, job="judgement")
    [call] = direct["calls"]
    assert call["max_tokens"] == JUDGEMENT_MIN_TOKENS and "thinking" not in call


async def test_complete_off_the_router_sends_the_job(direct, monkeypatch) -> None:
    import acb_llm.client as client

    monkeypatch.setitem(client._TIER_MODEL, "tier1", "deepseek/deepseek-v4-pro")
    with structlog.testing.capture_logs() as caps:
        text = await client.complete(
            tier=client.LLMTier.TIER_1, messages=[{"role": "user", "content": "x"}],
            max_tokens=8, job="structured")
    assert text == ""
    [call] = direct["calls"]
    assert call["thinking"] == THINKING_OFF
    assert len(_empty_lines(caps)) == 1


@pytest.fixture()
def router(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The Router arm, with a fake Console that answers ``state["content"]``."""
    import acb_auth.console_resolve as console_resolve
    import acb_llm.routed as routed

    state: dict[str, Any] = {"payloads": [], "content": ""}

    async def fake_console(payload: dict[str, Any], **_attribution: Any) -> Any:
        state["payloads"].append(payload)
        return 200, {
            "id": "r-1", "model": "deepseek/deepseek-v4-pro", "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": "length",
                         "message": {"role": "assistant", "content": state["content"]}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": payload["max_tokens"],
                      "total_tokens": 5 + payload["max_tokens"]},
        }

    monkeypatch.setattr(routed, "routing_is_on", lambda: True)
    monkeypatch.setattr(console_resolve, "chat_completion_on_console", fake_console)
    return state


async def test_the_router_seam_forwards_the_job_and_logs_an_empty_answer(router) -> None:
    from acb_llm.context import acompletion_with_fallback

    with structlog.testing.capture_logs() as caps:
        await acompletion_with_fallback(
            model="tier-fast", messages=[{"role": "user", "content": "x"}],
            max_tokens=300, response_format=_JSON, job="structured")
    [payload] = router["payloads"]
    assert payload["model"] == "tier-fast"
    assert payload["thinking"] == THINKING_OFF
    assert payload["messages"][0] == {"role": "system", "content": JSON_ASK}
    [line] = _empty_lines(caps)
    assert line["model"] == "deepseek/deepseek-v4-pro" and line["max_tokens"] == 300


async def test_complete_on_the_router_now_forwards_its_options(router) -> None:
    """``_routed_text`` dropped every option before task 1128, so a job could
    not reach the Router from ``complete``."""
    import acb_llm.client as client

    router["content"] = "YES"
    text = await client.complete(
        tier=client.LLMTier.TIER_1, messages=[{"role": "user", "content": "x"}],
        max_tokens=8, job="structured")
    assert text == "YES"
    [payload] = router["payloads"]
    assert payload["model"] == "tier-fast" and payload["thinking"] == THINKING_OFF


# ── The fence ───────────────────────────────────────────────────────────────

#: The directories the fence walks. ``packages`` means its ``acb_*`` packages.
FENCE_ROOTS = ("apps/services/gateway", "apps/services/orchestrator", "packages")

#: The model entry points. ``complete`` counts only with a ``tier=`` keyword,
#: because a job queue has a ``complete`` method too.
ENTRY_POINTS = frozenset({
    "acompletion_with_fallback", "_llm_json", "complete", "completion_on_router"})

#: A cap below this needs a job: the thinking of DeepSeek V4 can fill it.
CAP_LIMIT = 2000

#: The keywords that settle thinking for a call.
SETTLED = frozenset({"job", "thinking", "reasoning_effort"})

#: ``"<path>::<function>": reason``. Each entry is a call that may keep a small
#: cap with no job, and the reason. An entry that matches no call does not
#: fail, so a merge of the PR that owns the call cannot turn main red. Empty:
#: the nine Tasks calls pass through ``thinking=dict(NO_THINKING)`` (PR 1538).
EXEMPT: dict[str, str] = {}


def _module_constants(tree: ast.Module) -> dict[str, Any]:
    """The module-level ``NAME = <int or str>`` constants of one file."""
    out: dict[str, Any] = {}
    for node in tree.body:
        target = value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if (isinstance(target, ast.Name) and isinstance(value, ast.Constant)
                and isinstance(value.value, int | str)):
            out[target.id] = value.value
    return out


def _value(node: ast.AST | None, constants: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _settled_in_extra(call: ast.Call) -> bool:
    """A ``completion_on_router(extra={"thinking": ...})`` settles it too."""
    for kw in call.keywords:
        if kw.arg == "extra" and isinstance(kw.value, ast.Dict):
            keys = {k.value for k in kw.value.keys if isinstance(k, ast.Constant)}
            if keys & SETTLED:
                return True
    return False


class _Walker(ast.NodeVisitor):
    def __init__(self, rel: str, constants: dict[str, Any]) -> None:
        self.rel, self.constants = rel, constants
        self.stack: list[str] = []
        self.unsettled: list[str] = []
        self.bad_jobs: list[str] = []

    def _visit_fn(self, node: ast.AST) -> None:
        self.stack.append(node.name)  # type: ignore[attr-defined]
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _visit_fn

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name not in ENTRY_POINTS:
            return
        kws = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        if name == "complete" and "tier" not in kws:
            return
        where = f"{self.rel}::{self.stack[-1] if self.stack else '<module>'}"
        job = _value(kws.get("job"), self.constants)
        if "job" in kws and isinstance(job, str) and job not in JOBS:
            self.bad_jobs.append(f"{where} job={job!r}")
        cap = _value(kws.get("max_tokens"), self.constants)
        if not isinstance(cap, int) or cap >= CAP_LIMIT:
            return
        if SETTLED & kws.keys() or _settled_in_extra(node) or where in EXEMPT:
            return
        self.unsettled.append(f"{where}:{node.lineno} max_tokens={cap}")


def _walk_tree(rel: str, tree: ast.Module) -> _Walker:
    walker = _Walker(rel, _module_constants(tree))
    walker.visit(tree)
    return walker


def _walk_source(rel: str, source: str) -> _Walker:
    return _walk_tree(rel, ast.parse(source))


def _fence_files() -> list[Path]:
    files: list[Path] = []
    for root in FENCE_ROOTS:
        base = ROOT / root
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if "/tests/" in rel or "/.venv/" in rel:
                continue
            if root == "packages" and not rel.split("/")[1].startswith("acb_"):
                continue
            files.append(path)
    return files


@cache
def _trees() -> tuple[tuple[str, ast.Module], ...]:
    """Each fenced file as ``(relative path, tree)``. ``utf-8-sig`` drops a
    byte order mark, which ``ast.parse`` refuses."""
    return tuple(
        (p.relative_to(ROOT).as_posix(), ast.parse(p.read_text(encoding="utf-8-sig")))
        for p in _fence_files())


@cache
def _fence() -> tuple[tuple[str, ...], tuple[str, ...], int]:
    unsettled: list[str] = []
    bad_jobs: list[str] = []
    for rel, tree in _trees():
        walker = _walk_tree(rel, tree)
        unsettled += walker.unsettled
        bad_jobs += walker.bad_jobs
    return tuple(unsettled), tuple(bad_jobs), len(_trees())


def test_every_small_cap_call_names_its_job() -> None:
    """R7 fence ``ai-call-names-its-job``. Name the job (``acb_llm.jobs``), or
    send ``thinking`` or ``reasoning_effort`` yourself."""
    unsettled, _bad, files = _fence()
    assert files > 200, f"the walk found too few files ({files})"
    assert unsettled == (), (
        "each AI call with a cap under 2000 must name its job "
        "(acb_llm.jobs: structured, writing or judgement):\n" + "\n".join(unsettled))


def test_every_named_job_is_a_known_job() -> None:
    _unsettled, bad_jobs, _files = _fence()
    assert bad_jobs == (), "\n".join(bad_jobs)


def test_the_fence_sees_each_entry_point() -> None:
    """The walker must SEE the calls it guards, or a green run proves nothing.
    Each entry point has at least one call with a job in the tree."""
    seen: set[str] = set()
    for _rel, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and any(k.arg == "job" for k in node.keywords):
                func = node.func
                seen.add(func.id if isinstance(func, ast.Name)
                         else getattr(func, "attr", ""))
    assert seen >= ENTRY_POINTS, sorted(ENTRY_POINTS - seen)


def test_the_fence_can_fail() -> None:
    planted = (
        "MAX = 300\n"
        "JOB = 'writing'\n"
        "async def pick():\n"
        "    await acompletion_with_fallback(model='tier-fast', max_tokens=40)\n"
        "    await _llm_json('tier-fast', [], max_tokens=MAX)\n"
        "    await client.complete(tier=LLMTier.TIER_1, max_tokens=4)\n"
        "    await queue.complete(db, claim, max_tokens=4)\n"
        "    await _llm_json('tier-fast', [], max_tokens=MAX, job=JOB)\n"
        "    await _llm_json('tier-fast', [], max_tokens=MAX, job='summary')\n"
        "    await completion_on_router(tier='t', max_tokens=9,"
        " extra={'thinking': {}})\n"
        "    await acompletion_with_fallback(model='t', max_tokens=3000)\n"
        # The Tasks shape of PR 1538: a thinking= keyword settles it too.
        "    await acompletion_with_fallback(model='t', max_tokens=40,"
        " thinking=dict(NO_THINKING))\n"
    )
    walker = _walk_source("planted.py", planted)
    assert walker.unsettled == [
        "planted.py::pick:4 max_tokens=40",
        "planted.py::pick:5 max_tokens=300",
        "planted.py::pick:6 max_tokens=4",
    ]
    assert walker.bad_jobs == ["planted.py::pick job='summary'"]
