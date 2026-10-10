"""WS-47 WAC-10a — the WhatsApp run profile and its native UI, database-free.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §12.

The REAL ``whatsapp_ui`` tool, the REAL renderer, the REAL injection seam and
the REAL ``bot_run`` and ``inbound`` logic run here. The executor, the bound
reads and writes, and the Cloud API provider are the fakes of
``test_wac_bot_run.py``.

R7 fences named here:

* ``wac10-profile-strips``: in a WhatsApp run, no agent gets a tool of
  ``WITHHELD_TOOLS``, from the platform or its own pool, and no web prompt
  block. Outside one, nothing changes.
* ``wac10-ui-tool-own-agent``: ``whatsapp_ui`` goes to the run's own agent
  only, never to an agent it calls.
* ``wac10-elements-checked``: an element over WhatsApp's limits, or a link off
  ``metorite.com``, is refused with a reason, and nothing is queued.
* ``wac10-text-then-elements``: the text goes first, each element after it,
  under the one send mark. The thread keeps each element's rendition.
* ``wac10-tap-is-text``: a tap on a button or a row runs as the title text.
  A Flow reply gets no action.
* ``wac10-dark``: with ``WHATSAPP_ASSISTANT_NATIVE_UI`` off, the run is the
  WAC-3 run: the old scope rule, no profile, no typing indicator.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import httpx
import orchestrator._tool_injection as ti
import pytest
from acb_common import get_settings
from acb_skills import whatsapp_render as render
from acb_skills import whatsapp_ui as wui
from gateway.routes.whatsapp_channel import bot_run

from tests.unit.test_wac_bot_run import (  # noqa: F401 - `world` is a fixture
    ANSWER,
    MEMBER,
    PHONE,
    _later_sweeps,
    _message,
    _post_and_run,
    _status_error,
    _World,
    world,
)

PNG = b"\x89PNG\r\n\x1a\n"


async def _in_run(kind: str, data: Any, *, agent: str = "orchestrator") -> tuple[dict, list]:
    with wui.whatsapp_run(agent) as run:
        out = await wui.whatsapp_ui(kind, data)
        return out, list(run.outbox)


# ── The tool ────────────────────────────────────────────────────────────────


async def test_the_tool_refuses_outside_a_whatsapp_run() -> None:
    out = await wui.whatsapp_ui("buttons", {"body": "Pick", "buttons": ["A"]})
    assert out["ok"] is False and "WhatsApp" in out["error"]


async def test_buttons_become_reply_buttons_with_cut_titles() -> None:
    out, box = await _in_run("buttons", {
        "body": "Which project?", "footer": "Metorite",
        "buttons": ["Website redesign", "Printer firmware v3.2 release", "Ops"],
    })
    assert out["ok"] is True
    (msg,) = box
    inter = msg.interactive
    assert inter["type"] == "button" and inter["body"] == {"text": "Which project?"}
    titles = [b["reply"]["title"] for b in inter["action"]["buttons"]]
    ids = [b["reply"]["id"] for b in inter["action"]["buttons"]]
    assert titles[1] == "Printer firmware v3…" and len(titles[1]) == 20
    assert ids == ["b1", "b2", "b3"]
    assert inter["footer"] == {"text": "Metorite"}
    assert "[Buttons: Website redesign | Printer firmware v3… | Ops]" in msg.rendition


@pytest.mark.parametrize(("data", "says"), [
    ({"body": "x", "buttons": ["a", "b", "c", "d"]}, "1 to 3"),
    ({"body": "x", "buttons": []}, "1 to 3"),
    ({"body": "x", "buttons": ["Same", "same"]}, "same title"),
    ({"buttons": ["a"]}, '"body" is required'),
    ({"body": "x" * 1025, "buttons": ["a"]}, "limit is 1024"),
    ({"body": "x", "buttons": ["a", 3]}, "text title"),
])
async def test_bad_buttons_are_refused_and_nothing_is_queued(data: dict, says: str) -> None:
    out, box = await _in_run("buttons", data)
    assert out["ok"] is False and says in out["error"]
    assert box == []


async def test_a_list_numbers_its_rows_and_cuts_long_text() -> None:
    out, box = await _in_run("list", {
        "body": "Due today", "button": "View tasks",
        "rows": [{"title": "Fix the extruder nozzle assembly", "description": "d" * 90},
                 "Order PLA"],
    })
    assert out["ok"] is True
    action = box[0].interactive["action"]
    rows = action["sections"][0]["rows"]
    assert [r["id"] for r in rows] == ["r1", "r2"]
    assert len(rows[0]["title"]) == 24 and rows[0]["title"].endswith("…")
    assert len(rows[0]["description"]) == 72
    assert action["button"] == "View tasks"
    assert "- Order PLA" in box[0].rendition


async def test_rows_of_one_title_are_kept_apart() -> None:
    """WAC-10c review: a refused list lost a whole view, so a repeated title
    gets a suffix instead."""
    out, box = await _in_run("list", {"body": "x", "rows": ["dup", "Dup"]})
    assert out["ok"] is True
    rows = box[0].interactive["action"]["sections"][0]["rows"]
    assert [r["title"] for r in rows] == ["dup", "Dup (2)"]


@pytest.mark.parametrize(("data", "says"), [
    ({"body": "x", "rows": [f"t{i}" for i in range(11)]}, "at most 10 rows"),
    ({"body": "x", "sections": [{"title": "A", "rows": ["a"]}, {"rows": ["b"]}]},
     "each section needs a title"),
    ({"body": "x", "rows": []}, "list of"),
])
async def test_bad_lists_are_refused(data: dict, says: str) -> None:
    out, box = await _in_run("list", data)
    assert out["ok"] is False and says in out["error"]
    assert box == []


@pytest.mark.parametrize(("url", "sent"), [
    ("https://app.metorite.com/tasks", "https://app.metorite.com/tasks"),
    ("https://APP.Metorite.com/tasks?view=today#t7", "https://app.metorite.com/tasks?view=today#t7"),
    ("https://app.metorite.com", "https://app.metorite.com/"),
])
async def test_a_link_opens_the_metorite_app_as_a_rebuilt_url(url: str, sent: str) -> None:
    out, box = await _in_run("link", {"body": "Open it", "url": url})
    assert out["ok"] is True
    params = box[0].interactive["action"]["parameters"]
    assert params == {"display_text": "Open in Metorite", "url": sent}


@pytest.mark.parametrize("url", [
    "http://app.metorite.com/tasks", "https://evil.com/", "https://metorite.com.evil.com/",
    "https://evilmetorite.com/", "javascript:alert(1)", "https://user@evil.com/@metorite.com",
    # A browser reads these as evil.com, and Python's urlsplit as Metorite.
    "https://evil.com\\@app.metorite.com/tasks", "https://evil.com@app.metorite.com/",
    "https://evil.com:443@app.metorite.com/",
    # Not the app host exactly.
    "https://metorite.com/", "https://api.metorite.com/", "https://x.app.metorite.com/a",
    "https://app.metorite.com:8443/", "https://app.metorite.com./tasks",
    "https://app.metorite.com/ta sks", "https://app.metorite.com/\ttasks",
])
async def test_a_link_anywhere_else_is_refused(url: str) -> None:
    out, box = await _in_run("link", {"body": "Open it", "url": url})
    assert out["ok"] is False and box == []


@pytest.mark.parametrize(("chart", "extra"), [
    ("bar", {}), ("line", {}), ("progress", {"totals": [10, 10, 4]}),
])
async def test_a_chart_is_a_png_with_its_caption(chart: str, extra: dict) -> None:
    pytest.importorskip("pymupdf")
    out, box = await _in_run("chart", {
        "type": chart, "title": "Hours this week", "labels": ["Design", "Firmware", "Ops"],
        "values": [25, 18.5, 4], "caption": "Design took most of the week.", **extra,
    })
    assert out["ok"] is True, out
    (msg,) = box
    assert msg.kind == "image" and msg.png.startswith(PNG)
    assert msg.caption == "Design took most of the week."
    assert "Design: 25" in msg.rendition


async def test_a_table_is_a_png_and_its_rendition_keeps_the_cells() -> None:
    pytest.importorskip("pymupdf")
    out, box = await _in_run("table", {
        "title": "Due this week", "columns": ["Task", "Due", "Hours"],
        "rows": [["Ship firmware", "Mon", 4], ["Vendor call", "Tue", None]],
    })
    assert out["ok"] is True, out
    assert box[0].png.startswith(PNG)
    assert "Ship firmware | Mon | 4" in box[0].rendition


@pytest.mark.parametrize(("kind", "data", "says"), [
    ("chart", {"type": "pie", "title": "t", "labels": ["a"], "values": [1]}, "chart"),
    ("chart", {"type": "bar", "title": "t", "labels": ["a", "b"], "values": [1, "x"]},
     "numbers"),
    ("chart", {"type": "bar", "title": "t", "labels": ["a"], "values": [1, 2]}, "same length"),
    ("table", {"title": "t", "columns": list("abcdefg"), "rows": [list("abcdefg")]},
     "1 to 6 columns"),
    ("table", {"title": "t", "columns": ["a", "b"], "rows": [["only one"]]}, "one cell"),
    ("nope", {}, '"kind" must be one of'),
])
async def test_bad_images_are_refused(kind: str, data: dict, says: str) -> None:
    out, box = await _in_run(kind, data)
    assert out["ok"] is False and says in out["error"], out
    assert box == []


async def test_a_reply_holds_three_elements_at_most() -> None:
    with wui.whatsapp_run("orchestrator") as run:
        for _ in range(3):
            assert (await wui.whatsapp_ui("buttons", {"body": "b", "buttons": ["x"]}))["ok"]
        out = await wui.whatsapp_ui("buttons", {"body": "b", "buttons": ["x"]})
    assert out["ok"] is False and len(run.outbox) == 3


async def test_a_task_the_run_starts_sees_the_profile_and_closing_ends_it() -> None:
    with wui.whatsapp_run("orchestrator") as run:
        seen = await asyncio.create_task(asyncio.sleep(0, result=wui.current_run()))
        assert seen is run
    assert wui.current_run() is None


async def test_long_labels_and_cells_draw_fast() -> None:
    """WAC-10a review: a 4096-character label once held the event loop for
    minutes. Text is cut before it is measured, and the fit is a search."""
    import time

    long = "Lorem ipsum dolor sit amet " * 160
    start = time.monotonic()
    out, _box = await _in_run("chart", {"type": "bar", "title": long[:80],
                                        "labels": [long] * 12, "values": list(range(12))})
    out2, _box2 = await _in_run("table", {"title": "t", "columns": [long] * 6,
                                          "rows": [[long] * 6] * 20})
    assert out["ok"] is True and out2["ok"] is True, (out, out2)
    assert time.monotonic() - start < 10


async def test_the_tool_draws_off_the_event_loop() -> None:
    """The loop keeps turning while an image draws in a worker thread."""
    ticks = 0

    async def _tick() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    ticker = asyncio.create_task(_tick())
    try:
        await _in_run("table", {"title": "t", "columns": ["a", "b"],
                                "rows": [["x" * 150, 1]] * 20})
    finally:
        ticker.cancel()
    assert ticks > 1


@pytest.mark.parametrize("values", [[10 ** 400], [1e300], [float("nan")]])
async def test_a_number_a_chart_cannot_draw_is_refused(values: list) -> None:
    out, box = await _in_run("chart", {"type": "bar", "title": "t", "labels": ["a"],
                                       "values": values})
    assert out["ok"] is False and box == []


def test_a_resend_takes_the_text_and_never_the_renditions() -> None:
    msg = wui.build("buttons", {"body": "More?", "buttons": ["Yes"]})
    record = wui.thread_record("Two tasks are due.", [msg])
    assert "[Buttons: Yes]" in record
    assert wui.resend_text(record) == "Two tasks are due."
    only = wui.thread_record("", [msg])
    assert wui.resend_text(only) == msg.rendition, "an elements-only answer sends something"
    assert wui.resend_text("plain") == "plain" and wui.resend_text(None) is None
    assert wui.thread_record("plain", []) == "plain"


def test_the_renderer_numbers_read_short() -> None:
    assert render.fmt(1234) == "1,234"
    assert render.fmt(12.5, "h") == "12.5 h"
    assert render.fmt(1200, "₹") == "₹1,200"


# ── The injection seam ─────────────────────────────────────────────────────


class _FakeMafAgent:
    def __init__(self, name: str, own: list[Any] | None = None) -> None:
        self.name = name
        self.default_options: dict[str, Any] = {"tools": list(own or []),
                                                "instructions": "Base."}


class _Specialist:
    """A specialist tool as MAF holds it: a name and no ``__name__``."""

    def __init__(self, name: str) -> None:
        self.name = name


def _names(agent: _FakeMafAgent) -> set[str]:
    return {ti._tool_name(t) for t in agent.default_options["tools"]}


def _orchestrator_scope() -> list[str]:
    import json
    from pathlib import Path

    cfg = Path(__file__).resolve().parents[2] / "apps/agents/agent-orchestrator/config.json"
    return json.loads(cfg.read_text(encoding="utf-8"))["tool_scope"]


@pytest.fixture()
def _no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator.app_tools as app_tools

    monkeypatch.setattr(ti, "_build_registry_block", lambda: "Registered agents:")
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda name: frozenset())
    monkeypatch.setattr(app_tools, "load_app_action_tools", lambda name: [])


def _build(agent_name: str, *, whatsapp: str | None) -> _FakeMafAgent:
    async def spawn_copilot_agent() -> None: ...
    async def retrieve_entity_context() -> None: ...

    own = [spawn_copilot_agent, retrieve_entity_context,
           _Specialist("app_builder"), _Specialist("projects_assistant")]
    agent = _FakeMafAgent(agent_name, own)
    ctx = wui.whatsapp_run(whatsapp) if whatsapp else None
    if ctx is not None:
        ctx.__enter__()
    try:
        ti._inject_agent_tools([agent], tool_scope=_orchestrator_scope(),
                               agent_name=agent_name, agent_config={}, no_egress=False)
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)
    return agent


def test_a_web_run_is_unchanged(_no_db: None) -> None:
    agent = _build("orchestrator", whatsapp=None)
    names = _names(agent)
    assert {"emit_generative_ui", "write_artifact", "spawn_copilot_agent",
            "app_builder"} <= names
    assert "whatsapp_ui" not in names
    assert "Rich UI by default" in agent.default_options["instructions"]


def test_a_whatsapp_run_strips_the_web_tools_and_blocks(_no_db: None) -> None:
    from acb_skills.addendum import OUTPUT_DISCIPLINE_MARKERS

    web = _build("orchestrator", whatsapp=None)
    wa = _build("orchestrator", whatsapp="orchestrator")
    names = _names(wa)
    assert not names & wui.WITHHELD_TOOLS, sorted(names & wui.WITHHELD_TOOLS)
    assert {"whatsapp_ui", "retrieve_entity_context", "projects_assistant",
            "call_agent", "remember", "query_history"} <= names
    # The owner's rule (2026-10-10): only DELIVERY tools leave. A tool that
    # does something stays on WhatsApp, the build and set-up ones included.
    assert {"web_search", "fetch_page", "get_errors", "run_diagnostics",
            "list_integrations", "spawn_copilot_agent", "app_builder"} <= names
    text = wa.default_options["instructions"]
    assert "Rich UI by default" not in text
    assert not any(m in text for m in OUTPUT_DISCIPLINE_MARKERS)
    assert len(text) < len(web.default_options["instructions"])
    assert len(names) < len(_names(web))


def test_an_agent_the_run_calls_gets_no_ui_tool_and_no_web_tools(_no_db: None) -> None:
    agent = _build("projects-assistant", whatsapp="orchestrator")
    names = _names(agent)
    assert "whatsapp_ui" not in names
    assert not names & wui.WITHHELD_TOOLS


def test_a_run_with_no_scope_still_loses_the_web_tools(_no_db: None) -> None:
    """The executor's self-anneal retry injects with no ``tool_scope``. Only
    the final-list union guards that path."""
    agent = _FakeMafAgent("orchestrator")
    with wui.whatsapp_run("orchestrator"):
        ti._inject_agent_tools([agent], tool_scope=None, agent_name="orchestrator",
                               agent_config={}, no_egress=False)
    names = _names(agent)
    assert "whatsapp_ui" in names
    assert not names & wui.WITHHELD_TOOLS, sorted(names & wui.WITHHELD_TOOLS)


def test_the_final_list_drops_a_web_tool_that_a_scope_let_through(
    _no_db: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The last word: the no-match fallback and a drifted scope can both hand
    back a web tool. The final ``_drop_withheld`` still takes it out."""
    monkeypatch.setattr(ti, "_resolve_injected_scope",
                        lambda *a, **k: {"emit_generative_ui", "write_artifact", "call_agent"})
    agent = _FakeMafAgent("orchestrator")
    with wui.whatsapp_run("orchestrator"):
        ti._inject_agent_tools([agent], tool_scope=["call_agent"], agent_name="orchestrator",
                               agent_config={}, no_egress=False)
    names = _names(agent)
    assert "call_agent" in names
    assert not names & {"emit_generative_ui", "write_artifact"}


def test_the_copilot_addendum_offers_no_withheld_tool(_no_db: None) -> None:
    """The scope union: the addendum describes only the tools in scope."""
    def _addendum(wa: bool) -> str:
        agent = type("CopilotAgent", (), {})()
        agent.name = "copilot-agent"
        agent._tools = []
        agent._default_options = {"system_message": {"mode": "append", "content": "Base."}}
        ctx = wui.whatsapp_run("orchestrator") if wa else contextlib.nullcontext()
        ti._build_injected_tools_addendum.cache_clear()
        with ctx:
            ti._inject_agent_tools([agent], tool_scope=_orchestrator_scope(),
                                   agent_name="copilot-agent", agent_config={},
                                   no_egress=False)
        return agent._default_options["system_message"]["content"]

    # A scope-gated entry. The addendum's fixed workspace section names
    # write_artifact and emit_generative_ui whatever the scope, a known gap
    # of a Copilot-shaped agent (spec §12.2). Every in-tree agent is MAF.
    entries = ("- **manage_todo_list(", "### Task planning & progress tracking")
    web, wa = _addendum(False), _addendum(True)
    assert all(e in web for e in entries)
    assert not any(e in wa for e in entries)


def test_a_plain_maf_agent_gets_no_output_discipline_block(_no_db: None) -> None:
    """The other MAF shape: ``tools`` and a string ``instructions``."""
    from acb_skills.addendum import OUTPUT_DISCIPLINE_MARKERS

    def _plain(wa: bool) -> str:
        agent = type("PlainAgent", (), {})()
        agent.name = "plain"
        agent.tools = []
        agent.instructions = "Base."
        ctx = wui.whatsapp_run("orchestrator") if wa else contextlib.nullcontext()
        with ctx:
            ti._inject_agent_tools([agent], tool_scope=_orchestrator_scope(),
                                   agent_name="plain", agent_config={}, no_egress=False)
        return agent.instructions

    assert any(m in _plain(False) for m in OUTPUT_DISCIPLINE_MARKERS)
    assert not any(m in _plain(True) for m in OUTPUT_DISCIPLINE_MARKERS)


# ── The run (the WAC-3 world, with the switch on) ──────────────────────────


class _UiProvider:
    """The Cloud API provider, with the WAC-10a methods."""

    def __init__(self, world: _World, *, fail: str | None = None) -> None:
        self.world = world
        self.fail = fail
        self.typing: list[str] = []
        self.uploads: list[tuple[int, str]] = []
        #: The message each sent part quoted, in send order (WAC-10e).
        self.quotes: list[str | None] = []
        self.reactions: list[tuple[str, str]] = []

    def _sent(self, item: Any, quote: str | None = None) -> str:
        self.world.sent.append((PHONE, item))
        self.quotes.append(quote)
        return f"wamid.out.{len(self.world.sent)}"

    async def send_text(self, to: str, body: str, *,
                        reply_to_wa_message_id: str | None = None) -> str:
        return self._sent(body, reply_to_wa_message_id)

    async def send_reaction(self, to: str, wamid: str, emoji: str) -> str:
        if self.fail == "reaction":
            raise RuntimeError("reaction broke")
        self.reactions.append((wamid, emoji))
        return "wamid.reaction"

    async def send_interactive(self, to: str, interactive: dict, *,
                               reply_to_wa_message_id: str | None = None) -> str:
        if self.fail == "interactive":
            req = httpx.Request("POST", "https://graph.facebook.com")
            raise httpx.HTTPStatusError("bad", request=req,
                                        response=httpx.Response(400, request=req))
        return self._sent(("interactive", interactive["type"]), reply_to_wa_message_id)

    async def upload_media(self, data: bytes, mime: str, name: str) -> str:
        self.uploads.append((len(data), mime))
        return "media-1"

    async def send_image(self, to: str, media_id: str, *, caption: str | None = None,
                         reply_to_wa_message_id: str | None = None) -> str:
        return self._sent(("image", media_id, caption), reply_to_wa_message_id)

    async def show_typing(self, wamid: str) -> bool:
        self.typing.append(wamid)
        if self.fail == "typing":
            raise RuntimeError("typing broke")
        return True


class _UiAgent:
    """``run_agent`` that calls ``whatsapp_ui`` the way the model would."""

    def __init__(self, calls: list[tuple[str, dict]], reply: str = ANSWER) -> None:
        self.ui_calls = calls
        self.reply = reply
        self.seen: list[dict[str, Any]] = []

    async def __call__(self, agent: str, payload: dict[str, Any], **kw: Any) -> Any:
        self.seen.append({"payload": payload, "run": wui.current_run()})
        for kind, data in self.ui_calls:
            out = await wui.whatsapp_ui(kind, data)
            assert out["ok"] is True or wui.current_run() is None, out
        return {"result": self.reply}


def _switch(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_native_ui", on, raising=False)


def _provider(monkeypatch: pytest.MonkeyPatch, world: _World, **kw: Any) -> _UiProvider:
    from whatsapp_ingestion.providers import factory

    prov = _UiProvider(world, **kw)
    monkeypatch.setattr(factory, "build_provider", lambda name, creds: prov)
    return prov


_BUTTONS = ("buttons", {"body": "Want the details?", "buttons": ["Yes", "No"]})
_LIST = ("list", {"body": "Due today", "rows": ["Fix the extruder", "Order the nozzle"]})


async def test_the_text_goes_first_then_each_element_and_the_thread_keeps_both(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    prov = _provider(monkeypatch, world)
    world.agent = _UiAgent([_LIST, _BUTTONS])
    msg = _message()
    await _post_and_run(msg)

    wamid = msg["value"]["messages"][0]["id"]
    assert prov.typing == [wamid]
    assert [s for _to, s in world.sent] == [
        ANSWER, ("interactive", "list"), ("interactive", "button")]
    (seen,) = world.agent.seen
    assert seen["payload"]["system_context"] == bot_run.SCOPE_RULE_NATIVE
    assert seen["run"] is not None and seen["run"].agent == bot_run.AGENT
    (thread,) = world.store.threads.values()
    reply = thread[-1]["content"]
    assert reply.startswith(ANSWER)
    assert "[Buttons: Yes | No]" in reply and "- Fix the extruder" in reply
    (row,) = world.store.inbound_rows()
    assert row["state"] == "replied"


async def test_an_answer_that_is_only_an_element_is_sent(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    _provider(monkeypatch, world)
    world.agent = _UiAgent([_BUTTONS], reply="")
    await _post_and_run(_message())
    assert [s for _to, s in world.sent] == [("interactive", "button")]
    assert world.store.inbound_rows()[0]["state"] == "replied"


async def test_a_chart_is_uploaded_then_sent_as_an_image(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pymupdf")
    _switch(monkeypatch, True)
    prov = _provider(monkeypatch, world)
    world.agent = _UiAgent([("chart", {"type": "bar", "title": "Hours", "labels": ["a"],
                                       "values": [3], "caption": "Three hours."})])
    await _post_and_run(_message())
    assert prov.uploads and prov.uploads[0][1] == "image/png"
    assert world.sent[-1][1] == ("image", "media-1", "Three hours.")


async def test_an_element_meta_refuses_after_the_text_closes_the_row_without_a_resend(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    _provider(monkeypatch, world, fail="interactive")
    world.agent = _UiAgent([_BUTTONS])
    await _post_and_run(_message())
    assert [s for _to, s in world.sent] == [ANSWER]
    (row,) = world.store.inbound_rows()
    assert row["state"] == "replied" and row["code"] == "send_partial"


async def test_a_typing_failure_never_stops_the_run(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    _provider(monkeypatch, world, fail="typing")
    world.agent = _UiAgent([])
    await _post_and_run(_message())
    assert [s for _to, s in world.sent] == [ANSWER]


async def test_with_the_switch_off_the_run_is_the_wac3_run(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, False)
    prov = _provider(monkeypatch, world)
    world.agent = _UiAgent([_BUTTONS])
    await _post_and_run(_message())
    (seen,) = world.agent.seen
    assert seen["payload"]["system_context"] == bot_run.SCOPE_RULE
    assert seen["run"] is None and prov.typing == []
    assert [s for _to, s in world.sent] == [ANSWER]


def test_the_native_scope_rule_keeps_the_wac3_promises() -> None:
    """Advisory (§9): the text carries the rule. No test can prove a refusal."""
    rule = bot_run.SCOPE_RULE_NATIVE
    assert "Metorite" in rule and "refuse in one line" in rule
    assert "cannot change data" in rule and "web app" in rule
    assert "whatsapp_ui" in rule and "https://app.metorite.com" in rule


# ── A tap ──────────────────────────────────────────────────────────────────


def _tap(reply: dict[str, Any], kind: str = "button_reply") -> dict[str, Any]:
    change = _message(mtype="interactive")
    change["value"]["messages"][0]["interactive"] = {"type": kind, kind: reply}
    return change


@pytest.fixture()
def native(monkeypatch: pytest.MonkeyPatch) -> None:
    _switch(monkeypatch, True)


async def test_a_button_tap_runs_as_its_title(world: _World, native: None) -> None:
    await _post_and_run(_tap({"id": "b1", "title": "Yes"}))
    (call,) = world.agent.calls
    assert call["payload"]["message"] == "Yes"
    assert call["organization_id"] and call["session_user"] == MEMBER


async def test_a_row_tap_runs_as_its_title_and_description(
    world: _World, native: None,
) -> None:
    await _post_and_run(_tap({"id": "r2", "title": "Order the nozzle",
                              "description": "Due 5 pm"}, "list_reply"))
    assert world.agent.calls[0]["payload"]["message"] == "Order the nozzle (Due 5 pm)"


async def test_a_flow_reply_or_an_empty_tap_gets_no_action(
    world: _World, native: None,
) -> None:
    await _post_and_run(_tap({"response_json": "{}"}, "nfm_reply"))
    await _post_and_run(_tap({"id": "b1", "title": "  "}))
    assert world.agent.calls == [] and world.sent == []


async def test_a_tap_never_redeems_a_link_code(world: _World, native: None) -> None:
    world.links = []
    await _post_and_run(_tap({"id": "b1", "title": "Link me: ABCD2345"}))
    from gateway.routes.whatsapp_channel.inbound import REPLY_UNKNOWN

    assert [s for _to, s in world.sent] == [REPLY_UNKNOWN]


async def test_with_the_switch_off_a_tap_gets_no_action(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, False)
    await _post_and_run(_tap({"id": "b1", "title": "Yes"}))
    assert world.agent.calls == [] and world.sent == []


# ── A resend through the real run ───────────────────────────────────────────


async def test_a_refused_first_send_resends_the_text_without_renditions(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    prov = _provider(monkeypatch, world)
    refuse = [True]
    real_send = prov.send_text

    async def _flaky(to: str, body: str, **quote: Any) -> str:
        if refuse[0]:
            refuse[0] = False
            raise _status_error(400)   # Meta refused: surely not sent
        return await real_send(to, body, **quote)

    prov.send_text = _flaky
    world.agent = _UiAgent([_BUTTONS])
    await _post_and_run(_message())
    (row,) = world.store.inbound_rows()
    assert row["state"] == "received" and world.sent == []

    await _later_sweeps(world, row, times=1)
    assert [s for _to, s in world.sent] == [ANSWER]
    assert len(world.agent.seen) == 1, "the agent ran again for a resend"
