"""Task 2492: only a pack call that reads outside text marks the chat.

The owner's chat of 2026-11-09 sent one email from a clean chat, and the send
marked the session (``egress.untrusted_answer_marked``). From then on the
chat was held: the next email call went to the held card, ``create_task``
refused ``files`` and ``attach_to_task`` was off. The pack call marked the
session before EVERY tool of the email packs, sends included.

``tool_packs.PACK_OUTSIDE_TEXT`` now names, for each tool of a pack whose
owner reads stranger text, whether its answer brings outside text into the
model's context. ``_tool_injection._pack_call`` marks before the tool body
only for a call that ``tool_packs.marks_chat`` names. Each test names the
mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_pack_outside_text.py -q``.
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import egress as eg
from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp
from acb_skills.tool_guard import tool_name
from skill_projects import writes as W

from tests.unit.test_pack_guards import (
    _SENDS,
    EA,
    _build,
    _module,
    _named,
    _probe,
    _run,
    _stub,
)

#: The tools whose answer is mail text, by the brief of task 2492: a read,
#: a search, a thread, a list with bodies and a file read.
READS = ("read_email", "read_thread", "query_inbox", "read_email_attachment",
         "narrow_and_read", "query_insights", "find_priority", "digest")
#: The tools that only write or send, and that the brief names.
SENDS = ("send_email", "send_draft", "schedule_send")


def _outside_owner_tools() -> set[str]:
    """Each tool of each pack whose owner reads stranger text."""
    return {
        name
        for slug in sf.pack_slugs()
        if str(sf.SKILL_FAMILIES[slug].get("owner")) in eg.UNTRUSTED_INPUT_AGENTS
        for name in sf.SKILL_FAMILIES[slug]["tools"]
    }


@pytest.fixture(autouse=True)
def _held_card_writes_on(monkeypatch) -> None:
    """RX-1 binds the files refusal and the hold of ``attach_to_task`` only
    with the switch on, so this file tests with it on."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "*")


def _clear_run() -> Any:
    """The run of a clean chat. The run boundary bound "not marked"."""
    return _run(**{eg.CHAT_MARK_KEY: False})


async def _files_refusal() -> str:
    """The answer of ``create_task(files=...)`` before any read of a file."""
    return await W._held_param_refusal("create_task", "files")


def _attach_off() -> bool:
    """True when the chat hold takes ``attach_to_task`` out of the run."""
    return eg.ChatHoldProvider()._off("attach_to_task")


# ── 1. The classification fence ─────────────────────────────────────────────


def test_each_tool_of_a_mail_pack_is_classified_once() -> None:
    """A new tool of the email packs with no entry fails here, so nobody can
    forget to classify one. An entry with no such tool fails too. Mutation:
    drop an entry, or add one for a tool that the packs do not hold → red."""
    tools = _outside_owner_tools()
    assert tools, "non-vacuity: a pack of an owner that reads stranger text"
    assert set(tp.PACK_OUTSIDE_TEXT) == tools, (
        f"unclassified: {sorted(tools - set(tp.PACK_OUTSIDE_TEXT))}, "
        f"no such tool: {sorted(set(tp.PACK_OUTSIDE_TEXT) - tools)}")
    for name, rule in tp.PACK_OUTSIDE_TEXT.items():
        assert isinstance(rule, bool) or (
            isinstance(rule, tuple) and rule and all(isinstance(a, str) for a in rule)), name


def test_each_read_marks_and_each_send_does_not() -> None:
    """Mutation: classify a read as not marking, or a send as marking → red."""
    for name in READS:
        assert tp.PACK_OUTSIDE_TEXT[name] is True, name
        assert tp.marks_chat(EA, name, {}) is True, name
    for name in SENDS:
        assert tp.marks_chat(EA, name, {}) is False, name


def test_each_argument_of_a_rule_is_a_parameter_of_the_tool() -> None:
    """A tuple names the arguments that make a call read outside text. A name
    that is no parameter would never mark. Mutation: rename the parameter of
    the rule → red."""
    module = _module()
    built = {tool_name(fn): fn for fn in module._agent_tools()}
    rules = {n: r for n, r in tp.PACK_OUTSIDE_TEXT.items() if isinstance(r, tuple)}
    assert rules, "non-vacuity: send_email has a rule"
    for name, args in rules.items():
        params = inspect.signature(inspect.unwrap(built[name])).parameters
        assert set(args) <= set(params), (name, args)


def test_a_reply_marks_and_a_new_mail_does_not() -> None:
    """A reply names the addresses that the server took from the original
    mail. Mutation: classify send_email as False → red."""
    assert tp.marks_chat(EA, "send_email", {"to": ["kim@acme.test"]}) is False
    assert tp.marks_chat(EA, "send_email", {"reply_to_email_id": ""}) is False
    assert tp.marks_chat(EA, "send_email", {"reply_to_email_id": "m-1"}) is True


def test_an_unknown_tool_marks_and_another_owner_never_does() -> None:
    """Fail closed for a tool of the owner that the map does not name.
    Mutation: default the lookup to False → red."""
    assert tp.marks_chat(EA, "a_new_email_tool", {}) is True
    assert tp.marks_chat("projects-assistant", "read_email", {}) is False


# ── 2. A send leaves the chat clear ─────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(
    n for n, r in tp.PACK_OUTSIDE_TEXT.items() if r is False or isinstance(r, tuple)))
async def test_a_call_that_reads_nothing_leaves_the_chat_clear(name: str) -> None:
    """Mutation: mark the session again before every email call → red."""
    with _clear_run():
        build = await _build("email", _stub(_probe(name, _SENDS)))
        tool = next((t for t in build.tools if t.__name__ == name), None)
        if tool is None:  # a tool of the setup pack
            build = await _build("email-setup", _stub(_probe(name, _SENDS)))
            tool = _named(build, name)
        await tool(email_id="m-1")
        assert eg.read_untrusted_answer() is False, name
        assert await eg.session_is_known_marked() is False, name


async def test_after_a_send_create_task_takes_files_and_attach_stays() -> None:
    """The owner's case. A send, then ``create_task(files=...)`` and
    ``attach_to_task`` in the same chat. Mutation: mark the session again
    before every email call → the files refusal and the hold come back → red."""
    with _clear_run():
        build = await _build("email", _stub(_probe("send_email", _SENDS)))
        assert build.held is False
        await _named(build, "send_email")(email_id="")
        assert await eg.chat_is_held() is False
        assert await _files_refusal() == ""
        assert _attach_off() is False
        # The next turn of the same chat builds an open pack too.
        again = await _build("email", _stub(_probe("send_email", _SENDS)))
        assert again.held is False and "send_email" in {t.__name__ for t in again.tools}


# ── 3. A read still holds the chat ──────────────────────────────────────────


async def test_after_a_read_create_task_refuses_files_and_attach_is_off() -> None:
    """The non-vacuity of the case above. Mutation: stop the mark of a read
    tool → the files pass and attach stays → red."""
    with _clear_run():
        build = await _build("email", _stub(_probe("read_email")))
        await _named(build, "read_email")(email_id="m-1")
        assert eg.read_untrusted_answer() is True
        assert await eg.chat_is_held() is True
        assert await _files_refusal() == eg.held_param_refusal("create_task", "files")
        assert _attach_off() is True


async def test_a_read_then_a_send_is_held() -> None:
    """In the same turn the send answers the held answer, and the next turn
    builds a held pack with no open send. Mutation: stop the mark of a read
    tool → the send runs open → red."""
    ran: list[str] = []

    async def sent() -> None:
        ran.append("send")

    with _clear_run():
        build = await _build("email", _stub(
            _probe("read_email"), _probe("send_email", _SENDS, body=sent)))
        assert build.held is False
        await _named(build, "read_email")(email_id="m-1")
        out = await _named(build, "send_email")(email_id="")
        assert ran == [], "a send after a read in the same turn must not run open"
        assert out == eg.EGRESS_HELD_PACK_ANSWER.format(name="send_email")
        later = await _build("email", _stub(
            _probe("read_email"), _probe("send_email", _SENDS, body=sent)))
    assert later.held is True and "send_email" in later.dropped


async def test_a_reply_through_send_email_marks_the_chat() -> None:
    """Mutation: classify send_email as False → the reply leaves it clear → red."""
    async def reply(email_id: str = "", reply_to_email_id: str = "") -> str:
        return "Replied"

    reply.__name__ = "send_email"
    reply.__tool_risk__ = dict(_SENDS)  # type: ignore[attr-defined]
    with _clear_run():
        build = await _build("email", _stub(reply))
        await _named(build, "send_email")(reply_to_email_id="m-1")
        assert eg.read_untrusted_answer() is True
