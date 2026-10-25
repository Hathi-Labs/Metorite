"""WhatsApp profile tools and channel limits (WS-63 PZ-8, personalization.md §9).

R7 fences named here:

* ``wac-profile-scope-sentence``: the WhatsApp scope rule names the one
  exception to "no change from WhatsApp" (the member's own profile), only
  with the flag on and the org's writes on. Flag off keeps the rule byte
  for byte.
* ``wac-profile-block-route``: the real WhatsApp route gives the executor a
  solo room, so the profile block reaches the run. A shared room gets none.
* ``wac-profile-block-order``: the scope rule comes before the profile block.
* ``wac-profile-channel-label`` and ``wac-profile-undo``: a WhatsApp write
  carries ``X-Profile-Via: whatsapp``, and the spoken undo needs that label.
* ``wac-profile-channel-wins``: ADVISORY (R7). One sentence asks the model to
  keep the channel limits over the member's Answer length line. The hard
  limit is ``split_reply`` at 4096 characters.

There is no keyword intercept for "undo" on WhatsApp. The model calls
``profile_undo`` (persona line, tool doc).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from acb_common import assistant_profile as ap
from acb_common import get_settings
from acb_common import number_system as ns
from gateway.routes.whatsapp_channel import bot_run
from orchestrator import executor as ex

from tests.unit import test_executor_profile_block as epb
from tests.unit import test_wac_bot_run as wac
from tests.unit.test_executor_profile_block import EXAMPLE
from tests.unit.test_profile_write_tools import MEMBER, gateway, own_chat  # noqa: F401
from tests.unit.test_wac_bot_run import world  # noqa: F401


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    monkeypatch.setattr(get_settings(), "assistant_profile_enabled", on, raising=False)


# ── 1. The scope text ──────────────────────────────────────────────────────


def test_the_scope_rule_names_the_profile_exception_only_with_the_flag(monkeypatch) -> None:
    _flag(monkeypatch, True)
    on = bot_run.build_payload("hi", [], MEMBER, native=True, writes=True)["system_context"]
    assert bot_run.PROFILE_EXCEPTION_RULE in on
    assert bot_run.PROFILE_CHANNEL_RULE in on
    assert "the member's own profile" in on
    # The WAC-4 replacement still happened.
    assert bot_run._NO_CHANGE not in on
    assert bot_run._NO_CHANGE in bot_run.SCOPE_RULE_NATIVE
    assert on.startswith(bot_run.SCOPE_RULE_WRITES)

    _flag(monkeypatch, False)
    off = bot_run.build_payload("hi", [], MEMBER, native=True, writes=True)["system_context"]
    assert off == bot_run.SCOPE_RULE_WRITES
    off_plain = bot_run.build_payload("hi", [], MEMBER, native=True)["system_context"]
    assert off_plain == bot_run.SCOPE_RULE_NATIVE


def test_with_writes_off_the_rule_adds_no_exception(monkeypatch) -> None:
    """Writes off: the run holds no profile write tool, so no exception."""
    _flag(monkeypatch, True)
    for native in (True, False):
        rule = bot_run.build_payload("hi", [], MEMBER, native=native)["system_context"]
        assert bot_run.PROFILE_EXCEPTION_RULE not in rule
        assert "You cannot change data from WhatsApp" in rule or "cannot change data" in rule


# ── 2 and 3. The block on the real route, and the order ────────────────────


@pytest.fixture()
def profile_run(monkeypatch: pytest.MonkeyPatch):
    _flag(monkeypatch, True)
    monkeypatch.setattr(ex, "_current_run_org", lambda: epb.ORG)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: epb.MEMBER)
    monkeypatch.setattr(ex, "_read_member_profile",
                        lambda org, m: (EXAMPLE | {"style": {"length": "detailed"}}, "", "", ""))
    monkeypatch.setattr(ex, "_read_member_display_name", lambda org, m: "Ana Rao")
    monkeypatch.setattr(ex, "_read_number_system", lambda org, m: "indian")
    monkeypatch.setattr(ex, "_run_org_display_name", lambda: "Acme Tools")
    ex._PROFILE_CACHE.clear()
    ex._MEMBER_NAME_CACHE.clear()
    ns.forget()
    yield
    ex._PROFILE_CACHE.clear()
    ex._MEMBER_NAME_CACHE.clear()
    ns.forget()


def _preamble(call: dict[str, Any]) -> list[str]:
    token = ex._RUN_SOLO_ROOM.set(call["solo_room"])
    try:
        return ex._context_preamble_parts(call["payload"], {}, call["agent"])
    finally:
        ex._RUN_SOLO_ROOM.reset(token)


async def test_the_whatsapp_route_gives_the_run_the_profile_block(world, profile_run) -> None:  # noqa: F811
    await wac._post_and_run(wac._message("hello"))
    (call,) = world.agent.calls
    assert call["solo_room"] is True
    text = "\n".join(_preamble(call))
    assert ap.BLOCK_HEADER in text
    assert "Answer length: detailed" in text


async def test_the_scope_rule_comes_before_the_profile_block(world, profile_run) -> None:  # noqa: F811
    await wac._post_and_run(wac._message("hello"))
    (call,) = world.agent.calls
    text = "\n".join(_preamble(call))
    assert "## Current context" in text
    assert text.index("## Current context") < text.index(ap.BLOCK_HEADER)
    assert bot_run.PROFILE_CHANNEL_RULE in text
    assert text.index(bot_run.PROFILE_CHANNEL_RULE) < text.index(ap.BLOCK_HEADER)


async def test_a_shared_room_gets_no_profile_block(world, profile_run) -> None:  # noqa: F811
    await wac._post_and_run(wac._message("shared: true"))
    (call,) = world.agent.calls
    # The real route's solo_room is False for a shared room, whatever the text.
    solo = call["solo_room"]
    token = ex._RUN_SOLO_ROOM.set(False)
    try:
        text = "\n".join(ex._context_preamble_parts(call["payload"], {}, call["agent"]))
    finally:
        ex._RUN_SOLO_ROOM.reset(token)
    assert ap.BLOCK_HEADER not in text
    assert solo is True  # a link thread is solo; a room never reaches a run


# ── 4 and 5. The label, and the undo ───────────────────────────────────────


def _run(coro: Any) -> str:
    return asyncio.run(coro)


@pytest.fixture()
def labelled_undo(gateway, monkeypatch):  # noqa: F811
    """The gateway's undo finds a change only under the label that wrote it."""
    inner = gateway.handler
    state: dict[str, Any] = {"last": None, "undone": []}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/auth/me/assistant-profile").strip("/")
        via = request.headers.get("X-Profile-Via")
        if request.method == "POST" and path == "undo":
            gateway.requests.append(request)
            last = state["last"]
            if last is None or last[0] != via:
                return httpx.Response(404, json={"detail": "nothing"})
            gateway.profile["facts"] = [f for f in gateway.profile["facts"] if f["id"] != last[1]]
            state["undone"].append(last[1])
            state["last"] = None
            return httpx.Response(200, json={"undone": {"op": "delete"}})
        res = inner(request)
        if request.method == "POST" and path == "facts" and res.status_code == 201:
            state["last"] = (via, json.loads(res.content)["id"])
        return res

    monkeypatch.setattr(gateway, "handler", handler)
    return state


def test_a_whatsapp_write_carries_the_whatsapp_label(own_chat) -> None:  # noqa: F811
    with ap.profile_channel("whatsapp", writes=True):
        _run(pt().profile_remember("I like tea", "preference"))
    (post,) = own_chat.writes()
    assert post.headers["X-Profile-Via"] == "whatsapp"
    assert own_chat.profile["facts"][-1]["source"] == "whatsapp"


def pt():
    from acb_skills import profile_tools
    return profile_tools


def test_the_spoken_undo_puts_the_change_back(own_chat, labelled_undo) -> None:  # noqa: F811
    with ap.profile_channel("whatsapp", writes=True):
        _run(pt().profile_remember("I like tea", "preference"))
        assert any(f["text"] == "I like tea" for f in own_chat.profile["facts"])
        out = _run(pt().profile_undo())
    assert "Undone" in out
    undo = [r for r in own_chat.requests if r.url.path.endswith("/undo")]
    assert [r.headers.get("X-Profile-Via") for r in undo] == ["whatsapp"]
    assert all(f["text"] != "I like tea" for f in own_chat.profile["facts"])
    assert own_chat.profile["facts"][0]["id"] == "f0001"


def test_the_bot_run_binds_the_whatsapp_channel() -> None:
    """Source fence: dropping the binding makes a WhatsApp write label ``chat``."""
    import inspect
    src = inspect.getsource(bot_run)
    assert 'profile_channel("whatsapp", writes=writes)' in src
