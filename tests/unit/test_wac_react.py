"""WS-47 WAC-10e — the bot replies and reacts the way a person does.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §13.7.

R7 fences named here:

* ``wac10e-quote``: with the WhatsApp profile, the FIRST part of an answer
  quotes the member's message, and no other part does. With the profile off,
  no part quotes.
* ``wac10e-react``: a reaction goes ON the member's message, is never a part
  of the answer, never counts toward the element cap, and a refused one never
  costs the member the answer. A resend never sends it as text.
* ``wac10e-working``: a long job tells the member first. The AI sends one
  line (kind "working") at once, or code sends :data:`bot_run.REPLY_WORKING`
  past :data:`bot_run.WORKING_AFTER_S`. Never both, and never on a quick run.
* ``wac10e-thanks``: a plain thanks gets a 👍 from code, with no AI call and no
  text. An "ok" is not a thanks.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from acb_skills import whatsapp_ui as wui
from gateway.routes.whatsapp_channel import bot_run, views

from tests.unit.test_wac_bot_run import (  # noqa: F401 - `world` is a fixture
    ANSWER,
    _message,
    _post_and_run,
    _World,
    world,
)
from tests.unit.test_wac_native_ui import _BUTTONS, _LIST, _provider, _switch, _UiAgent


def _wamid(msg: dict[str, Any]) -> str:
    return msg["value"]["messages"][0]["id"]


# ── The quote ───────────────────────────────────────────────────────────────


async def test_the_first_part_quotes_the_members_message_and_only_the_first(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    prov = _provider(monkeypatch, world)
    world.agent = _UiAgent([_LIST, _BUTTONS])
    msg = _message()
    await _post_and_run(msg)
    assert [s for _to, s in world.sent] == [
        ANSWER, ("interactive", "list"), ("interactive", "button")]
    assert prov.quotes == [_wamid(msg), None, None]


async def test_an_answer_of_elements_only_quotes_on_its_first_element(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    prov = _provider(monkeypatch, world)
    world.agent = _UiAgent([_BUTTONS], reply="")
    msg = _message()
    await _post_and_run(msg)
    assert prov.quotes == [_wamid(msg)]


async def test_with_the_profile_off_no_part_quotes(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, False)
    prov = _provider(monkeypatch, world)
    world.agent = _UiAgent([])
    await _post_and_run(_message())
    assert [s for _to, s in world.sent] == [ANSWER] and prov.quotes == [None]


# ── The reaction ────────────────────────────────────────────────────────────


async def test_a_reaction_goes_on_the_members_message_and_is_no_part(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    prov = _provider(monkeypatch, world)
    world.agent = _UiAgent([("react", {"emoji": "🎉"}), _BUTTONS])
    msg = _message()
    await _post_and_run(msg)
    assert prov.reactions == [(_wamid(msg), "🎉")]
    assert [s for _to, s in world.sent] == [ANSWER, ("interactive", "button")]
    assert prov.quotes[0] == _wamid(msg)
    (thread,) = world.store.threads.values()
    assert "[Reaction: 🎉]" in thread[-1]["content"]
    assert world.store.inbound_rows()[0]["state"] == "replied"


async def test_a_reaction_only_answer_sends_no_text_and_closes_replied(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    prov = _provider(monkeypatch, world)
    world.agent = _UiAgent([("react", {"emoji": "👍"})], reply="")
    await _post_and_run(_message())
    assert world.sent == [] and [e for _w, e in prov.reactions] == ["👍"]
    (row,) = world.store.inbound_rows()
    assert row["state"] == "replied" and row["code"] is None


async def test_a_refused_reaction_never_costs_the_answer(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    _provider(monkeypatch, world, fail="reaction")
    world.agent = _UiAgent([("react", {"emoji": "👍"})])
    await _post_and_run(_message())
    assert [s for _to, s in world.sent] == [ANSWER]
    assert world.store.inbound_rows()[0]["state"] == "replied"


async def test_the_tool_takes_one_emoji_and_a_later_one_replaces_it() -> None:
    with wui.whatsapp_run("orchestrator") as run:
        for bad in ("yes", "", "👍 ok", "👍" * 11, None, "👍👍👍", "°", "™x", "⠀",
                    "👍^", "👍‮", "👍⁣", "🇮", "1", "🏻"):
            out = await wui.whatsapp_ui("react", {"emoji": bad})
            assert out["ok"] is False and "one emoji" in out["error"], repr(bad)
        for good in ("👍", "❤️", "❤", "👍🏽", "👨‍👩‍👧", "🇮🇳", "‼️", "ℹ️", "↔️", "〰️",
                     "✅", "☕", "⭐", "🏳️‍🌈", "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
                     "1️⃣"):
            assert (await wui.whatsapp_ui("react", {"emoji": good}))["ok"] is True, good
    reactions = [m for m in run.outbox if m.kind == "reaction"]
    assert [m.emoji for m in reactions] == ["1️⃣"]


async def test_a_reaction_never_counts_toward_the_element_cap() -> None:
    with wui.whatsapp_run("orchestrator") as run:
        for _ in range(wui.MAX_MESSAGES):
            assert (await wui.whatsapp_ui(*_BUTTONS))["ok"] is True
        assert (await wui.whatsapp_ui("react", {"emoji": "✅"}))["ok"] is True
        assert (await wui.whatsapp_ui(*_BUTTONS))["ok"] is False
    assert len(run.outbox) == wui.MAX_MESSAGES + 1


def test_a_resend_never_sends_a_reaction_as_text() -> None:
    only = wui.thread_record("", [wui.reaction("👍")])
    assert wui.resend_text(only) == ""
    assert wui.resend_text(wui.VIEW_MARK + only) == ""
    both = wui.thread_record("Done.", [wui.reaction("✅")])
    assert wui.resend_text(both) == "Done."
    mixed = wui.thread_record("", [wui.reaction("✅"), wui.build(*_BUTTONS)])
    assert wui.resend_text(mixed).endswith("[Buttons: Yes | No]")
    assert "Reaction" not in wui.resend_text(mixed)


def test_the_model_reads_a_reaction_as_a_reaction() -> None:
    record = wui.thread_record("", [wui.reaction("👍")])
    turn = bot_run.model_turn({"role": "assistant", "content": record})
    assert turn["content"] == "(I reacted to the member's message.)"
    record = wui.thread_record("Done.", [wui.reaction("✅")])
    assert bot_run.model_turn({"role": "assistant", "content": record})["content"] == "Done."


def test_a_typed_reaction_record_is_cut_from_the_text() -> None:
    reply, out = wui.polish("[Reaction: 👍]\nAll three are done.", [])
    assert reply == "All three are done." and out == []


# ── The thanks, with no AI call ─────────────────────────────────────────────


@pytest.mark.parametrize(("text", "is_thanks"), [
    ("Thanks!", True), ("thank you", True), ("Thank you so much 🙏", True),
    ("🙏", True), ("🙏🏽", True), ("thx", True),
    ("ok", False), ("👍", False), ("yes", False), ("", False),
    ("Thanks??", False), ("thanks 😡", False), ("Thanks! 😊", True),
    ("thanks, and what is due tomorrow?", False), ("thanks " * 10, False),
])
def test_only_a_plain_thanks_is_a_thanks(text: str, is_thanks: bool) -> None:
    assert views.thanks(text) is is_thanks


async def test_a_thanks_gets_a_thumbs_up_with_no_ai_call_and_no_text(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    prov = _provider(monkeypatch, world)
    world.agent = _UiAgent([])
    msg = _message("Thanks!")
    await _post_and_run(msg)
    assert world.agent.seen == [], "a thanks must not cost an AI call"
    assert prov.reactions == [(_wamid(msg), views.THANKS_REACTION)]
    assert world.sent == []
    (row,) = world.store.inbound_rows()
    assert row["state"] == "replied"
    (thread,) = world.store.threads.values()
    assert thread[-1]["content"].startswith(wui.VIEW_MARK)


async def test_with_the_profile_off_a_thanks_goes_to_the_assistant(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, False)
    prov = _provider(monkeypatch, world)
    world.agent = _UiAgent([])
    await _post_and_run(_message("Thanks!"))
    assert len(world.agent.seen) == 1 and prov.reactions == []


# ── The provider's payloads ─────────────────────────────────────────────────


async def test_the_provider_quotes_and_reacts_with_metas_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from whatsapp_ingestion.providers.cloud_api import WhatsAppCloudProvider

    prov = WhatsAppCloudProvider({"access_token": "t", "phone_number_id": "123456"})
    posted: list[dict[str, Any]] = []

    async def _post(payload: dict[str, Any]) -> str:
        posted.append(payload)
        return "wamid.x"

    monkeypatch.setattr(prov, "_post_message", _post)
    await prov.send_reaction("9199", "wamid.in", "👍")
    await prov.send_interactive("9199", {"type": "button"}, reply_to_wa_message_id="wamid.in")
    await prov.send_image("9199", "m1", reply_to_wa_message_id="wamid.in")
    await prov.send_image("9199", "m1")
    assert posted[0]["type"] == "reaction"
    assert posted[0]["reaction"] == {"message_id": "wamid.in", "emoji": "👍"}
    assert posted[1]["context"] == {"message_id": "wamid.in"}
    assert posted[2]["context"] == {"message_id": "wamid.in"}
    assert "context" not in posted[3]


# ── The early message: "this takes a while" ─────────────────────────────────


class _SlowAgent(_UiAgent):
    """An agent that works for *delay* seconds after its tool calls."""

    def __init__(self, calls: list[tuple[str, dict]], delay: float) -> None:
        super().__init__(calls)
        self.delay = delay

    async def __call__(self, agent: str, payload: dict[str, Any], **kw: Any) -> Any:
        out = await super().__call__(agent, payload, **kw)
        await asyncio.sleep(self.delay)
        return out


_WORKING = ("working", {"text": "Building the chart, about 30 seconds."})


async def test_the_ai_says_first_that_a_long_job_takes_a_while(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    prov = _provider(monkeypatch, world)
    world.agent = _UiAgent([_WORKING])
    msg = _message()
    await _post_and_run(msg)
    assert [s for _to, s in world.sent] == [_WORKING[1]["text"], ANSWER]
    # Both quote the member's message, and "typing…" shows again after it.
    assert prov.quotes == [_wamid(msg), _wamid(msg)]
    assert prov.typing == [_wamid(msg), _wamid(msg)]
    assert world.store.inbound_rows()[0]["state"] == "replied"


async def test_a_long_run_with_no_early_message_gets_one_from_code(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    monkeypatch.setattr(bot_run, "WORKING_AFTER_S", 0.01)
    _provider(monkeypatch, world)
    world.agent = _SlowAgent([], delay=0.2)
    await _post_and_run(_message())
    assert [s for _to, s in world.sent] == [bot_run.REPLY_WORKING, ANSWER]


async def test_the_timer_stays_quiet_after_the_ais_own_early_message(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    monkeypatch.setattr(bot_run, "WORKING_AFTER_S", 0.01)
    _provider(monkeypatch, world)
    world.agent = _SlowAgent([_WORKING], delay=0.2)
    await _post_and_run(_message())
    assert [s for _to, s in world.sent] == [_WORKING[1]["text"], ANSWER]


async def test_a_quick_run_sends_no_early_message(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    _provider(monkeypatch, world)
    world.agent = _UiAgent([])
    await _post_and_run(_message())
    assert [s for _to, s in world.sent] == [ANSWER]


async def test_the_working_kind_sends_once_and_checks_its_text() -> None:
    sent: list[str] = []

    async def _notify(text: str) -> bool:
        sent.append(text)
        return True

    with wui.whatsapp_run("orchestrator", notify=_notify) as run:
        assert (await wui.whatsapp_ui("working", {"text": ""}))["ok"] is False
        too_long = {"text": "x" * (wui.WORKING_MAX + 1)}
        assert (await wui.whatsapp_ui("working", too_long))["ok"] is False
        assert (await wui.whatsapp_ui(*_WORKING))["ok"] is True
        assert (await wui.whatsapp_ui(*_WORKING))["ok"] is False
    assert sent == [_WORKING[1]["text"]] and run.notified and run.outbox == []


async def test_a_failed_early_message_leaves_the_timer_free() -> None:
    async def _broken(text: str) -> bool:
        raise RuntimeError("down")

    with wui.whatsapp_run("orchestrator", notify=_broken) as run:
        assert (await wui.whatsapp_ui(*_WORKING))["ok"] is False
    assert run.notified is False
    with wui.whatsapp_run("orchestrator") as run:
        assert (await wui.whatsapp_ui(*_WORKING))["ok"] is False


# ── The review round of 2026-10-11 ──────────────────────────────────────────


@pytest.mark.parametrize("closer", ["_last_word", "_close_used_up"])
async def test_a_stored_reaction_only_answer_never_ends_on_the_failure_text(
    monkeypatch: pytest.MonkeyPatch, closer: str,
) -> None:
    record = wui.thread_record("", [wui.reaction("👍")])
    delivered: list[str] = []
    failed: list[Any] = []

    async def _stored(_sid: str, _wamid: str) -> str:
        return record

    async def _deliver(_req: Any, reply: str, *_a: Any, **_k: Any) -> None:
        delivered.append(reply)

    async def _send(_req: Any, texts: list[Any], **_k: Any) -> int:
        failed.append(texts)
        return 1

    async def _end(*_a: Any) -> bool:
        return True

    monkeypatch.setattr(bot_run, "_stored_reply", _stored)
    monkeypatch.setattr(bot_run, "_deliver", _deliver)
    monkeypatch.setattr(bot_run, "_send", _send)
    monkeypatch.setattr(bot_run, "_end", _end)
    req = bot_run.RunRequest("m1", "org", "a@b.c", "9199", "wamid.in", "sid")
    if closer == "_last_word":
        await bot_run._last_word(req, bot_run.MAX_TRIES)
    else:
        await bot_run._close_used_up(req)
    assert delivered == [""] and failed == []


async def test_an_unclear_early_message_counts_as_sent(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, True)
    monkeypatch.setattr(bot_run, "WORKING_AFTER_S", 0.01)
    prov = _provider(monkeypatch, world)
    real = prov.send_text
    calls: list[str] = []

    async def _slow_first(to: str, body: str, **quote: Any) -> str:
        calls.append(body)
        if body == _WORKING[1]["text"]:
            raise TimeoutError  # Meta may have it: never send a second line
        return await real(to, body, **quote)

    prov.send_text = _slow_first
    world.agent = _SlowAgent([_WORKING], delay=0.2)
    await _post_and_run(_message())
    assert bot_run.REPLY_WORKING not in calls
    assert [s for _to, s in world.sent] == [ANSWER]
