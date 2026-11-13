"""HRD-6 (task 2985): one line for the member while a slow tool runs.

Spec: ``project-docs/specs/chat_failure_modes.md`` §4 row B6 and §6 HRD-6,
``chat_run_continuity.md`` §4.11. Module: ``acb_skills.progress_notice``.

The owner's rule (2026-11-13): a large read can take minutes, so the member
hears that it takes a while. These tests hold each rule of the module:

1. The flag ``CHAT_PROGRESS_NOTICE_ORGS`` is empty by default, and then the
   injection seam attaches nothing, so a run is as before. The seam runs right
   before the number check, and its timer wraps each tool call.
2. A call that outlives the timer gets ONE line, with its call id, and one
   clear event when it ends. A read gets the owner's words, any other tool
   gets "This is taking longer than usual". A fast call gets none.
3. A tool's own line wins, and the timer never writes over it.
4. Parallel calls do not stack lines. A waiting call gets its line once.
5. A delegation tool and a tool that waits for the member get no line. A tool
   that shows a card in its body loses its timer, and its line goes.
6. WhatsApp gets the line as its early message, once in a reply, and the
   answer waits for it. The run's cap and the sweep's stale time are per org.
7. The fold never keeps the line, and it is no answer event.
8. The native stream survives a tool that runs past the bare idle cap, and
   the line reaches the stream between the tool's start and its result.
"""
from __future__ import annotations

import ast
import asyncio
import json
from collections.abc import AsyncIterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_skills import progress_notice as pn
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
)
from orchestrator import _tool_injection, executor

ROOT = Path(__file__).resolve().parents[2]
ORG = "org-hrd6"
THREAD = "t-hrd6-progress"
FAST = 0.05  # the timer of a test run, in seconds
OWN = "Adding up 31 totals. This can take two minutes."


@pytest.fixture
def flag_on(monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_progress_notice_orgs", ORG)
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant(ORG)
    try:
        yield
    finally:
        release_tenant(token)


@pytest.fixture(autouse=True)
def _clean_queue_binding():
    yield
    executor._RUN_QUEUES.clear()


# ── A scripted model ────────────────────────────────────────────────────────


class ScriptedClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Answers each request from a step: ``("calls", [(name, args), ...])`` or
    ``("text", words)``."""

    def __init__(self, script: list[Any]) -> None:
        super().__init__()
        self.script = list(script)
        self.n = 0

    def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        self.n += 1
        step = self.script.pop(0)
        if step[0] == "calls":
            contents = [
                Content.from_function_call(f"c{self.n}-{i}", name, arguments=json.dumps(args))
                for i, (name, args) in enumerate(step[1])
            ]
        else:
            contents = [Content.from_text(step[1])]
        n = self.n
        if stream:
            async def _gen() -> AsyncIterable[ChatResponseUpdate]:
                for content in contents:
                    yield ChatResponseUpdate(contents=[content], role="assistant",
                                             response_id=f"r{n}", message_id="m")
            return self._build_response_stream(_gen())

        async def _one() -> ChatResponse:
            return ChatResponse(messages=[Message("assistant", contents)], response_id=f"r{n}")
        return _one()


def _short_timer(view: Any) -> Any:
    for provider in view.context_providers:
        if isinstance(provider, pn.ProgressNoticeProvider):
            provider.after_s = FAST
    return view


def _view(agent: Agent) -> Any:
    """The per-run view of the real seam, with the test's short timer."""
    agents: list[Any] = [agent]
    _tool_injection._apply_progress_notice(agents)
    assert agents[0] is not agent, "a native agent gets a per-run view"
    return _short_timer(agents[0])


class _TimelineQueue(asyncio.Queue):
    """A run queue that writes each event into a shared timeline. A line
    notes how many lines showed when it went."""

    def __init__(self, timeline: list[Any]) -> None:
        super().__init__()
        self.timeline = timeline

    def put_nowait(self, item: Any) -> None:
        if item.get("name") == pn.EVENT:
            value = item["value"]
            showing = len(getattr(self, "_progress_notice_open", None) or ())
            kind = "line" if value["text"] else "clear"
            self.timeline.append((kind, value.get("tool_call_id"), value["text"], showing))
        else:
            self.timeline.append(("event", item.get("name")))
        super().put_nowait(item)


def _kinds(timeline: list[Any], kind: str) -> list[Any]:
    return [t for t in timeline if t[0] == kind]


def _run(view: Any, timeline: list[Any], *, driver: Any = None) -> None:
    async def run() -> None:
        queue = _TimelineQueue(timeline)
        token = executor._active_run_queue.set(queue)
        relay = executor._stream_relay_thread_id.set(THREAD)
        try:
            if driver is None:
                await view.run("hi")
            else:
                await asyncio.gather(view.run("hi"), driver(queue))
        finally:
            executor._stream_relay_thread_id.reset(relay)
            executor._active_run_queue.reset(token)

    asyncio.run(run())


def _one_call(tool: Any, name: str, args: dict[str, Any], *, driver: Any = None) -> list[Any]:
    client = ScriptedClient([("calls", [(name, args)]), ("text", "Done.")])
    timeline: list[Any] = []
    _run(_view(Agent(client=client, name="probe", tools=[tool])), timeline, driver=driver)
    return timeline


# ── 1. The flag and the seam ────────────────────────────────────────────────


def test_the_flag_is_empty_by_default_and_reads_the_bound_tenant(monkeypatch):
    from acb_common import get_settings
    from acb_common.settings import Settings

    assert Settings.model_fields["chat_progress_notice_orgs"].default == ""
    monkeypatch.setattr(get_settings(), "chat_progress_notice_orgs", "")
    assert not pn.progress_notice_on(ORG)
    monkeypatch.setattr(get_settings(), "chat_progress_notice_orgs", f"x, {ORG.upper()}")
    assert pn.progress_notice_on(ORG)
    assert not pn.progress_notice_on("org-other")
    monkeypatch.setattr(get_settings(), "chat_progress_notice_orgs", "*")
    assert pn.progress_notice_on("org-other")


def test_the_flag_is_named_in_the_three_places_of_a_flag():
    from acb_common import env_guard

    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nCHAT_PROGRESS_NOTICE_ORGS=\n" in env
    # Only the operator writes it: no tenant route may set it.
    assert env_guard.is_platform_env("CHAT_PROGRESS_NOTICE_ORGS")
    from gateway import flag_reload

    assert flag_reload.reloadable("CHAT_PROGRESS_NOTICE_ORGS")


def test_flag_off_attaches_nothing_and_sends_nothing(monkeypatch):
    """Rule 6: empty means each run keeps its own object, and no seam of the
    module sends a byte."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_progress_notice_orgs", "")
    agent = Agent(client=ScriptedClient([]), name="probe")
    agents: list[Any] = [agent]
    _tool_injection._apply_progress_notice(agents)
    assert agents[0] is agent
    assert not asyncio.run(pn.emit_progress("Adding up 31 totals."))
    pn.member_wait()  # no call on this frame: nothing happens
    asyncio.run(pn.settle_channel(SimpleNamespace()))  # no early message: returns


def test_flag_on_gives_a_per_run_view_and_leaves_the_shared_agent(flag_on):
    agent = Agent(client=ScriptedClient([]), name="probe")
    view = _view(agent)
    assert any(isinstance(p, pn.ProgressNoticeProvider) for p in view.context_providers)
    assert not any(isinstance(p, pn.ProgressNoticeProvider) for p in agent.context_providers)


def test_the_seam_runs_right_before_the_number_check():
    """``test_crm_answer_check.py`` keeps the number check last. The notice
    runs right before it, after the steer step."""
    tree = ast.parse((ROOT / "apps/services/orchestrator/orchestrator/_tool_injection.py")
                     .read_text(encoding="utf-8-sig"))
    inject = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_inject_agent_tools")
    names = [getattr(getattr(getattr(stmt, "value", None), "func", None), "id", None)
             for stmt in inject.body]
    assert names[-3:-1] == ["_apply_progress_notice", "_apply_answer_check"]
    assert names.index("_apply_steer_turns") < names.index("_apply_progress_notice")


def test_the_timer_wraps_the_call_under_the_number_check(flag_on, monkeypatch):
    """With the number check on, its provider comes after the notice, so the
    notice's middleware is outside the recorder, and the slow read still gets
    its line."""
    from acb_common import get_settings
    from acb_skills.answer_check import AnswerCheckProvider

    monkeypatch.setattr(get_settings(), "crm_answer_check", "shadow")

    async def crm_aggregate(entity: str) -> str:
        """Add up a large CRM set."""
        await asyncio.sleep(FAST * 6)
        return "31 totals"

    client = ScriptedClient([("calls", [("crm_aggregate", {"entity": "deals"})]),
                             ("text", "You have 31 totals.")])
    agents: list[Any] = [Agent(client=client, name="crm", tools=[crm_aggregate])]
    _tool_injection._apply_progress_notice(agents)
    _tool_injection._apply_answer_check(agents, "crm-assistant")
    kinds = [type(p) for p in agents[0].context_providers]
    assert kinds.index(pn.ProgressNoticeProvider) < kinds.index(AnswerCheckProvider)
    timeline: list[Any] = []
    _run(_short_timer(agents[0]), timeline)
    assert [t[1:3] for t in _kinds(timeline, "line")] == [("c1-0", pn.LONG_READ_TEXT)]


# ── 2 and 3. The timer on each call ─────────────────────────────────────────


def test_a_slow_read_gets_one_line_with_its_call_id_and_one_clear(flag_on):
    async def crm_aggregate(entity: str) -> str:
        """Add up a large CRM set."""
        await asyncio.sleep(FAST * 6)
        return "31 totals"

    timeline = _one_call(crm_aggregate, "crm_aggregate", {"entity": "deals"})
    assert [t[:3] for t in timeline] == [
        ("line", "c1-0", pn.LONG_READ_TEXT),
        ("clear", "c1-0", ""),
    ]


def test_a_slow_step_that_is_no_read_gets_the_general_words(flag_on):
    async def make_report(title: str) -> str:
        """Build a long report."""
        await asyncio.sleep(FAST * 6)
        return "done"

    timeline = _one_call(make_report, "make_report", {"title": "Q3"})
    assert [t[1:3] for t in _kinds(timeline, "line")] == [("c1-0", pn.SLOW_STEP_TEXT)]
    assert pn.SLOW_STEP_TEXT == "This is taking longer than usual. Please wait."


def test_a_fast_call_gets_no_line(flag_on):
    async def lookup(q: str) -> str:
        """A quick read."""
        return "ok"

    assert _one_call(lookup, "lookup", {"q": "x"}) == []


def test_a_tool_line_first_stops_the_timer_line(flag_on):
    async def crm_aggregate(entity: str) -> str:
        """Add up a large CRM set."""
        assert await pn.emit_progress("Adding up 31 totals.  This can take two minutes.")
        assert not await pn.emit_progress("A second line"), "one line for each call"
        await asyncio.sleep(FAST * 6)
        return "31 totals"

    timeline = _one_call(crm_aggregate, "crm_aggregate", {"entity": "deals"})
    assert [t[1:3] for t in _kinds(timeline, "line")] == [("c1-0", OWN)]


# ── 4. Parallel calls ───────────────────────────────────────────────────────


def _parallel(waits: dict[str, float],
              own: dict[str, tuple[float, str]] | None = None) -> list[Any]:
    """Run one model turn that calls ``slow_read`` once for each part, in
    parallel. Each call waits its own time. ``own`` gives a part its own line,
    sent after a delay. Returns the shared timeline."""
    timeline: list[Any] = []
    own = own or {}

    async def slow_read(part: str) -> str:
        """A slow read."""
        if part in own:
            delay, words = own[part]
            await asyncio.sleep(delay)
            timeline.append(("own", part, await pn.emit_progress(words)))
        await asyncio.sleep(waits[part])
        timeline.append(("end", part))
        return part

    client = ScriptedClient([
        ("calls", [("slow_read", {"part": p}) for p in waits]),
        ("text", "Done."),
    ])
    _run(_view(Agent(client=client, name="probe", tools=[slow_read])), timeline)
    return timeline


def test_parallel_slow_calls_do_not_stack_lines(flag_on):
    """A ends while B still runs: B gets ONE line, and only after A's line went."""
    timeline = _parallel({"a": FAST * 6, "b": FAST * 14})
    lines = _kinds(timeline, "line")
    assert [t[1] for t in lines] == ["c1-0", "c1-1"], "one line for each call"
    assert all(t[3] == 0 for t in lines), "no line goes while another shows"
    a_clear = timeline.index(("clear", "c1-0", "", 1))
    assert timeline.index(("end", "a")) < a_clear < timeline.index(lines[1])
    assert timeline.index(lines[1]) < timeline.index(("end", "b"))


def test_a_call_that_ends_before_the_timer_never_gets_a_line(flag_on):
    timeline = _parallel({"a": FAST * 6, "b": FAST / 5})
    assert [t[1] for t in _kinds(timeline, "line")] == ["c1-0"]


def test_a_call_that_ends_while_it_waits_gets_no_line(flag_on):
    """B's timer fired while A's line showed, and B ended before A did."""
    timeline = _parallel({"a": FAST * 14, "b": FAST * 4})
    assert [t[1] for t in _kinds(timeline, "line")] == ["c1-0"]


def test_a_waiting_tool_line_keeps_its_own_words(flag_on):
    """B's timer line waits behind A's. B's own words then take its place."""
    timeline = _parallel({"a": FAST * 6, "b": FAST * 14}, own={"b": (FAST * 2, OWN)})
    assert ("own", "b", False) in timeline, "it waits while A's line shows"
    assert [t[1:3] for t in _kinds(timeline, "line")] == [
        ("c1-0", pn.SLOW_STEP_TEXT), ("c1-1", OWN)]


def test_the_own_timer_never_writes_over_a_waiting_tool_line(flag_on):
    """The order of review round 2: A's own line shows, B sends its own words
    BEFORE its timer fires (they wait), and then B's timer fires. B's words
    must show, not the general words."""
    timeline = _parallel({"a": FAST * 6, "b": FAST * 14},
                         own={"a": (0, "A's own line."), "b": (FAST / 2, OWN)})
    assert ("own", "a", True) in timeline and ("own", "b", False) in timeline
    assert [t[1:3] for t in _kinds(timeline, "line")] == [
        ("c1-0", "A's own line."), ("c1-1", OWN)]


def test_a_later_slow_call_gets_its_own_line(flag_on):
    """The mark of a line ends with its call, so the next slow call shows."""
    async def slow_read(part: str) -> str:
        """A slow read."""
        await asyncio.sleep(FAST * 6)
        return part

    client = ScriptedClient([
        ("calls", [("slow_read", {"part": "a"})]),
        ("calls", [("slow_read", {"part": "b"})]),
        ("text", "Done."),
    ])
    timeline: list[Any] = []
    _run(_view(Agent(client=client, name="probe", tools=[slow_read])), timeline)
    assert [t[1] for t in _kinds(timeline, "line")] == ["c1-0", "c2-0"]
    assert [t[1] for t in _kinds(timeline, "clear")] == ["c1-0", "c2-0"]


# ── 5. A wait for the member ────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["call_agent", "request_confirmation", "ask_questions"])
def test_a_delegation_or_a_wait_for_the_member_gets_no_line(flag_on, name):
    async def tool(message: str) -> str:
        """A long wait by design."""
        await asyncio.sleep(FAST * 6)
        return "answer"

    tool.__name__ = name
    assert _one_call(tool, name, {"message": "x"}) == []


def _answer_cards(after_s: float) -> Any:
    """A driver: it answers each confirmation card *after_s* after it shows."""
    async def driver(queue: _TimelineQueue) -> None:
        answered: set[str] = set()
        for _ in range(400):
            await asyncio.sleep(FAST / 5)
            for rid in list(executor._pending_user_input):
                if rid in answered:
                    continue
                answered.add(rid)
                await asyncio.sleep(after_s)
                assert executor.resolve_user_input(
                    rid, "APPROVE", was_freeform=False,
                    thread_id=executor._pending_user_input.owner_of(rid))
            if ("end", "write") in queue.timeline:
                return
    return driver


def test_a_card_in_the_tool_body_stops_the_timer(flag_on):
    """P1 of review round 2: a write waits on ``request_confirmation`` past
    the timer. The member gets the card, and no "Please wait"."""
    from acb_skills.ask_tools import request_confirmation

    async def write(title: str) -> str:
        """A write that asks first."""
        timeline_ref.append(("ok", await request_confirmation("Create the task?", title)))
        timeline_ref.append(("end", "write"))
        return "created"

    client = ScriptedClient([("calls", [("write", {"title": "Demo"})]), ("text", "Done.")])
    timeline: list[Any] = []
    timeline_ref = timeline
    _run(_view(Agent(client=client, name="probe", tools=[write])), timeline,
         driver=_answer_cards(FAST * 6))
    assert ("ok", True) in timeline
    assert ("event", "confirmation_requested") in timeline
    assert _kinds(timeline, "line") == [], "a card wait gets no line"


def test_a_line_that_showed_goes_when_the_card_shows(flag_on):
    """The tool ran past the timer first, so its line showed. The line goes
    right before the card, and no second line comes after the answer."""
    from acb_skills.ask_tools import request_confirmation

    async def write(title: str) -> str:
        """A slow write that asks first."""
        await asyncio.sleep(FAST * 3)
        timeline_ref.append(("ok", await request_confirmation("Send it?", title)))
        await asyncio.sleep(FAST * 3)  # past the timer again, after the answer
        timeline_ref.append(("end", "write"))
        return "sent"

    client = ScriptedClient([("calls", [("write", {"title": "Hi"})]), ("text", "Done.")])
    timeline: list[Any] = []
    timeline_ref = timeline
    _run(_view(Agent(client=client, name="probe", tools=[write])), timeline,
         driver=_answer_cards(FAST * 6))
    seen = [t[0] if t[0] != "event" else t[1] for t in timeline if t[0] in ("line", "clear", "event")]
    assert seen[:3] == ["line", "clear", "confirmation_requested"]
    assert len(_kinds(timeline, "line")) == 1, "no second line after the answer"


def test_ask_tools_marks_both_card_paths():
    """``request_confirmation`` and ``ask_questions`` mark the wait before the
    card event, on every path that shows a card."""
    src = (ROOT / "packages/acb_skills/acb_skills/ask_tools.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for fn_name, card in (("request_confirmation", "confirmation_requested"),
                          ("ask_questions", "elicitation_requested")):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == fn_name)
        body = ast.get_source_segment(src, fn) or ""
        assert "_member_waits()" in body, fn_name
        if card in body:
            assert body.index("_member_waits()") < body.index(card), fn_name


def test_no_queue_and_no_channel_sends_nothing_and_never_fails_the_call(flag_on):
    async def slow_read(part: str) -> str:
        """A slow read."""
        await asyncio.sleep(FAST * 3)
        return "fine"

    client = ScriptedClient([("calls", [("slow_read", {"part": "a"})]), ("text", "Done.")])
    view = _view(Agent(client=client, name="probe", tools=[slow_read]))
    result = asyncio.run(view.run("hi"))
    assert "Done." in result.text


# ── 6. WhatsApp ─────────────────────────────────────────────────────────────


def _whatsapp(waits: list[float], notify: Any) -> Any:
    from acb_skills.whatsapp_ui import whatsapp_run

    async def crm_aggregate(entity: str) -> str:
        """Add up a large CRM set."""
        await asyncio.sleep(waits.pop(0))
        return entity

    client = ScriptedClient(
        [("calls", [("crm_aggregate", {"entity": f"e{i}"})]) for i in range(len(waits))]
        + [("text", "Done.")])
    view = _view(Agent(client=client, name="orchestrator", tools=[crm_aggregate]))

    async def run() -> Any:
        with whatsapp_run("orchestrator", notify=notify) as wa:
            await view.run("hi")
            pending = [t for t in getattr(wa, "_progress_notice_tasks", ()) if not t.done()]
            await pn.settle_channel(wa, timeout=FAST * 10)
            return wa, pending

    return asyncio.run(run())


def test_whatsapp_gets_the_line_as_its_early_message_once(flag_on):
    sent: list[str] = []

    async def notify(text: str) -> bool:
        sent.append(text)
        return True

    wa, _pending = _whatsapp([FAST * 6, FAST * 6], notify)
    assert sent == [pn.LONG_READ_TEXT], "one early message in a reply"
    assert wa.notified is True, "the channel's own 20 s line then stays quiet"


def test_the_answer_waits_for_an_early_message_on_its_way(flag_on):
    """P3 of review round 2: the send of the early message was still on its
    way when the run ended. ``settle_channel`` lets it land first."""
    order: list[str] = []

    async def notify(text: str) -> bool:
        await asyncio.sleep(FAST * 4)  # a slow send that outlives the call
        order.append("early message")
        return True

    _wa, pending = _whatsapp([FAST * 2], notify)
    assert pending, "the send was still on its way when the run ended"
    order.append("answer")
    assert order == ["early message", "answer"]


def test_an_early_message_that_hangs_is_cancelled_before_the_answer(flag_on):
    async def notify(text: str) -> bool:
        await asyncio.sleep(3600)
        return True

    async def run() -> list[Any]:
        from acb_skills.whatsapp_ui import whatsapp_run

        with whatsapp_run("orchestrator", notify=notify) as wa:
            call = pn._Call(call_id=None, channel=wa, managed=True)
            assert pn._send_soon(call, pn.LONG_READ_TEXT)
            tasks = list(wa._progress_notice_tasks)
            await pn.settle_channel(wa, timeout=FAST)
            await asyncio.gather(*tasks, return_exceptions=True)
            return tasks

    tasks = asyncio.run(run())
    assert tasks and all(t.cancelled() for t in tasks)


def test_bot_run_settles_the_early_message_before_any_send():
    """``_answer`` awaits ``_settle_early_message`` in the ``finally`` of the
    run, so it runs before the answer and before the failure text."""
    src = (ROOT / "apps/services/gateway/gateway/routes/whatsapp_channel/bot_run.py"
           ).read_text(encoding="utf-8")
    run_at = src.index("result = await _still_working(req, wa_run")
    settle_at = src.index("await _settle_early_message(wa_run)", run_at)
    assert src.rindex("try:", 0, run_at) > src.rindex("with refuse_cards()", 0, run_at)
    assert "finally:" in src[run_at:settle_at]
    assert src.index('kind="answer"', run_at) > settle_at


def _settings(monkeypatch, **orgs: str) -> None:
    import acb_common

    values = {"chat_progress_notice_orgs": "", "crm_long_read_orgs": "", **orgs}
    monkeypatch.setattr(acb_common, "get_settings", lambda: SimpleNamespace(**values))


@pytest.mark.parametrize(("orgs", "org", "caps"), [
    ({}, ORG, (240, 300)),
    ({"chat_progress_notice_orgs": ORG}, ORG, (420, 480)),
    ({"chat_progress_notice_orgs": ORG}, "org-other", (240, 300)),
    ({"crm_long_read_orgs": f"x, {ORG.upper()}"}, ORG, (420, 480)),
    ({"crm_long_read_orgs": "*"}, "org-other", (420, 480)),
    ({"chat_progress_notice_orgs": "*"}, None, (420, 480)),
    ({"chat_progress_notice_orgs": ORG}, None, (240, 300)),
], ids=["both-empty", "notice-org", "notice-other-org", "crm-org", "crm-star",
        "notice-star-no-org", "notice-org-no-org"])
def test_the_whatsapp_caps_are_per_org(monkeypatch, orgs, org, caps):
    """P2 of review round 2: with both lists empty each cap is as before.
    A long-read org gets 420 s and 480 s. The run's cap stays shorter."""
    from gateway.routes.whatsapp_channel import bot_run

    _settings(monkeypatch, **orgs)
    assert (bot_run.run_timeout_for(org), bot_run.stale_for(org)) == caps
    assert bot_run.run_timeout_for(org) < bot_run.stale_for(org)
    assert bot_run.run_timeout_for(org) >= 180 + 4 * 60 or caps == (240, 300)


def test_a_settings_build_with_no_crm_list_reads_it_as_empty(monkeypatch):
    import acb_common
    from gateway.routes.whatsapp_channel import bot_run

    monkeypatch.setattr(acb_common, "get_settings",
                        lambda: SimpleNamespace(chat_progress_notice_orgs=""))
    assert (bot_run.run_timeout_for(ORG), bot_run.stale_for(ORG)) == (240, 300)


def test_the_sweep_and_the_claim_read_the_stale_time_of_their_org(monkeypatch):
    """The sweep binds one org for each pass, and FORCE RLS keeps each row of
    the pass in it. So each query of the pass gets the time of that org."""
    from gateway.routes.whatsapp_channel import bot_run

    _settings(monkeypatch, chat_progress_notice_orgs=ORG)
    seen: list[Any] = []

    class _Db:
        async def execute(self, _sql: Any, params: dict[str, Any]) -> Any:
            seen.append(params.get("stale"))
            return SimpleNamespace(fetchall=lambda: [], first=lambda: None,
                                   mappings=lambda: SimpleNamespace(all=lambda: []))

        async def __aenter__(self) -> _Db:
            return self

        async def __aexit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(bot_run, "tenant_session", lambda: _Db())
    for org, want in ((ORG, 480), ("org-other", 300)):
        seen.clear()
        asyncio.run(bot_run._sweep_org(org))
        assert [s for s in seen if s is not None] == [want, want, want], org
        from acb_common.db import bind_tenant, release_tenant

        token = bind_tenant(org)
        try:
            seen.clear()
            asyncio.run(bot_run._claim("00000000-0000-0000-0000-000000000001", stale=True))
            assert seen == [want], org
        finally:
            release_tenant(token)


# ── 7. The fold and the profile mark ────────────────────────────────────────


def test_the_fold_never_keeps_the_line_or_its_clear_and_neither_is_an_answer():
    from gateway.chat_fold import fold_run_events

    line = {"type": "CUSTOM", "name": pn.EVENT, "value": {"text": pn.LONG_READ_TEXT}}
    clear = {"type": "CUSTOM", "name": pn.EVENT, "value": {"text": "", "tool_call_id": "c1"}}
    assert executor._is_answer_event(line) is False
    assert executor._is_answer_event(clear) is False
    folded = fold_run_events([
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": "31 totals."},
        line, clear,
    ])
    assert folded is not None and folded["custom_events"] == []
    assert fold_run_events([line, clear]) is None


# ── 8. The stream survives a tool past the bare idle cap ────────────────────


class _SlowToolAgent:
    """A native-MAF-shaped agent. Its stream starts a call, runs the REAL
    middleware around a tool that sleeps *tool_s*, then answers."""

    def __init__(self, tool_s: float, *, call: bool = True) -> None:
        self.name = "test-agent"
        self.tools: list[Any] = []
        self.default_options: dict[str, Any] = {}
        self.tool_s = tool_s
        self.call = call

    def run(self, *_a: Any, **_k: Any) -> Any:
        return self._stream()

    async def _stream(self) -> Any:
        if self.call:
            yield SimpleNamespace(role="assistant", message_id="m1", contents=[SimpleNamespace(
                type="function_call", name="crm_aggregate", arguments='{"entity": "deals"}',
                call_id="call_1")])

        async def call_next() -> None:
            await asyncio.sleep(self.tool_s)

        ctx = SimpleNamespace(function=SimpleNamespace(name="crm_aggregate"),
                              metadata={"call_id": "call_1"})
        await pn.ProgressNotice(after_s=FAST).process(ctx, call_next)
        if self.call:
            yield SimpleNamespace(role="tool", message_id="m2", contents=[SimpleNamespace(
                type="function_result", call_id="call_1", result="31 totals", exception=None)])
        yield SimpleNamespace(role="assistant", message_id="m3", contents=[
            SimpleNamespace(type="text", text="You have 31 totals.")])

    async def __aenter__(self) -> _SlowToolAgent:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


def _drive(monkeypatch, tmp_path, agent: Any) -> list[dict[str, Any]]:
    from acb_common import get_settings
    from orchestrator.watchdog import WatchdogPolicy

    from tests.unit.test_run_agent_stream_e2e import _LoadedStub, _parse_frames

    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(tmp_path / "agents"))
    # The bare idle cap is far shorter than the tool. Only the budget of an
    # open tool keeps the run alive.
    monkeypatch.setattr(executor, "_WATCHDOG", WatchdogPolicy(
        idle=FAST * 4, tool_open=30.0, hitl_pending=30.0, tool_execution=30.0))

    class _Loaded(_LoadedStub):
        def build_agents(self_inner) -> list[Any]:
            return [agent]

    class _Ctx:
        def __enter__(self) -> _LoadedStub:
            return _Loaded("")

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))

    async def _collect() -> list[str]:
        out: list[str] = []
        async for line in executor.run_agent_stream(
            "test-agent-unregistered", {"message": "add up the deals"},
        ):
            out.append(line)
        return out

    from acb_skills.write_artifact import bind_artifact_context

    try:
        return _parse_frames(asyncio.run(_collect()))
    finally:
        bind_artifact_context()
        executor._RUN_QUEUES.clear()
        executor._pending_user_input.clear()


def test_the_stream_survives_a_tool_past_the_idle_cap_and_shows_the_line(
        monkeypatch, tmp_path, flag_on):
    events = _drive(monkeypatch, tmp_path, _SlowToolAgent(tool_s=FAST * 12))
    types = [e.get("type") for e in events]
    assert "RUN_ERROR" not in types, events
    assert types[-1] == "RUN_FINISHED"
    start = types.index("TOOL_CALL_START")
    result = types.index("TOOL_CALL_RESULT")
    notes = [i for i, e in enumerate(events)
             if e.get("type") == "CUSTOM" and e.get("name") == pn.EVENT]
    assert len(notes) == 2 and start < notes[0] < notes[1] < result
    assert events[notes[0]]["value"] == {"text": pn.LONG_READ_TEXT, "tool_call_id": "call_1"}
    assert events[notes[1]]["value"] == {"text": "", "tool_call_id": "call_1"}


def test_the_same_wait_with_no_tool_open_trips_the_idle_cap(monkeypatch, tmp_path, flag_on):
    """The control of the test above: the cap is real, so the open tool's
    budget is what keeps a long read alive."""
    events = _drive(monkeypatch, tmp_path, _SlowToolAgent(tool_s=FAST * 12, call=False))
    errors = [e for e in events if e.get("type") == "RUN_ERROR"]
    assert errors and errors[0].get("code") == "timeout"
