"""The held set of the email assistant, with the switch off and on (§21.5).

``email_app_master_plan.md`` §21 lets a held chat keep more sends, each one
behind a card that the gateway records. It ships dark behind
``EMAIL_HELD_CARD_SEND``. The owner's condition for the build: with the
switch off, a held run holds EXACTLY the tool set of H-374.

R7, each with the mutation that turns it red:

* ``test_with_the_switch_off_a_held_run_holds_the_h374_set`` — make a tool
  card-bound whatever the switch says.
* ``test_with_the_switch_on_a_held_run_holds_the_section_21_set`` — leave a
  joined tool out of the pin, or let a tool of 21.5's held list join.
* ``test_a_new_parameter_breaks_a_held_card_pin`` — pin a set with room for
  one more parameter.
* ``test_each_new_path_reads_the_switch`` — let a tool take the held path
  with the switch off.
* ``test_the_hold_note_follows_the_switch`` — read the cards note with the
  switch off.
"""
from __future__ import annotations

import pytest
from acb_skills import egress as eg

from tests.unit import _held_card_fakes as F
from tests.unit.test_no_egress_tool_audit import (  # noqa: F401 - a fixture
    EXPECTED_WITHHELD,
    _hermetic,
    _in_repo_agents,
    _run_tools,
)

#: H-374: what a held run of the email assistant does not get. With the
#: switch off this is the set, exactly.
H374_WITHHELD = frozenset({
    "send_email", "forward_email", "unsubscribe_sender",
    "update_assistant_settings", "save_knowledge",
})

#: §21.5: what a held run does not get with the switch on. A new slice that
#: joins a tool takes it out of this set, in the same change.
SECTION_21_WITHHELD = H374_WITHHELD - {
    "unsubscribe_sender",         # slice (c): the stored link, its host and its hash
    "update_assistant_settings",  # slice (d): closed keys, behind a card
    "forward_email",              # slice (e): each person classed, the mail's files
    "send_email",                 # slice (e): a new mail only
}


def _withheld() -> frozenset[str]:
    rel = _in_repo_agents()["email-assistant"]
    open_run = _run_tools("email-assistant", rel, no_egress=False)
    held_run = _run_tools("email-assistant", rel, no_egress=True)
    assert not held_run - open_run
    return frozenset(open_run - held_run)


@pytest.mark.usefixtures("_hermetic")
def test_with_the_switch_off_a_held_run_holds_the_h374_set(monkeypatch) -> None:
    F.flag_off(monkeypatch)
    assert EXPECTED_WITHHELD["email-assistant"] == H374_WITHHELD
    assert _withheld() == H374_WITHHELD
    assert set(eg.CARD_BOUND_SEND_TOOLS) == {"compose_reply", "send_draft", "schedule_send"}


@pytest.mark.usefixtures("_hermetic")
def test_with_the_switch_on_a_held_run_holds_the_section_21_set(monkeypatch) -> None:
    F.flag_on(monkeypatch)
    assert _withheld() == SECTION_21_WITHHELD
    # 21.5, "Tools that stay held": never a card form for these.
    for name in ("fetch_page", "web_search", *eg.STORE_WRITES):
        assert eg.is_egress_tool(name), name


def test_each_new_path_reads_the_switch(monkeypatch) -> None:
    """The email tools take the held path only in a held run with the switch
    on. The gateway routes answer 404 with it off
    (``test_held_send_card_record.py``)."""
    from acb_skills.write_artifact import bind_artifact_context

    agents = F.load_email_agent("ea_held_pin")
    bind_artifact_context(no_egress=True, agent_name="email-assistant", session_id="s-1")
    F.flag_off(monkeypatch)
    assert agents._held_card_mode() is False
    F.flag_on(monkeypatch)
    assert agents._held_card_mode() is True
    bind_artifact_context(no_egress=False, agent_name="email-assistant", session_id="s-1")
    assert agents._held_card_mode() is False


@pytest.mark.parametrize("on", [False, True])
def test_the_hold_note_follows_the_switch(monkeypatch, on: bool) -> None:
    (F.flag_on if on else F.flag_off)(monkeypatch)
    note = eg.egress_hold_note({"agent_name": "email-assistant"})
    assert note == (eg.EGRESS_HOLD_NOTE_MAIL_CARDS if on else eg.EGRESS_HOLD_NOTE_MAIL)
    sandbox = eg.egress_hold_note({"agent_name": "projects-assistant"})
    assert sandbox == eg.EGRESS_HOLD_NOTE_SANDBOX


@pytest.mark.parametrize("name", sorted(eg.HELD_CARD_SEND_TOOLS))
def test_a_held_card_tool_carries_its_exact_pin(monkeypatch, name: str) -> None:
    import inspect

    F.flag_on(monkeypatch)
    agents = F.load_email_agent(f"ea_held_pin_{name}")
    fn = getattr(agents, name)
    assert frozenset(inspect.signature(fn).parameters) == eg.HELD_CARD_SEND_TOOLS[name]
    assert fn.__tool_risk__[eg.CARD_BOUND_KEY] is True
    assert eg.is_card_bound_send(fn) is True
    F.flag_off(monkeypatch)
    assert eg.is_card_bound_send(fn) is False


@pytest.mark.parametrize("name", sorted(eg.HELD_CARD_SEND_TOOLS))
@pytest.mark.parametrize("extra", ["to", "bcc", "unsubscribe_link", "url", "attachments"])
def test_a_new_parameter_breaks_a_held_card_pin(monkeypatch, name: str, extra: str) -> None:
    """A tool of the held card with one more parameter is an egress tool
    again, also with the switch on."""
    if extra in eg.HELD_CARD_SEND_TOOLS[name]:
        pytest.skip("the tool already has this parameter")
    F.flag_on(monkeypatch)
    agents = F.load_email_agent(f"ea_held_extra_{name}")
    params = ", ".join(f"{p}=None" for p in sorted(eg.HELD_CARD_SEND_TOOLS[name]))
    ns: dict = {}
    exec(f"async def {name}({params}, {extra}=None):\n    return ''\n", ns)
    fn = ns[name]
    fn.__tool_risk__ = dict(getattr(agents, name).__tool_risk__)
    assert eg.is_card_bound_send(fn) is False
    assert eg.is_egress_tool(fn) is True


async def test_send_email_refuses_a_reply_in_a_held_run(monkeypatch) -> None:
    """``HELD_REFUSED_PARAMS``: a reply through ``send_email`` sends nothing
    in a held run, before any card or gateway call. Mutation: drop the
    refusal of ``_held_new_mail`` → red."""
    from acb_skills.write_artifact import bind_artifact_context

    F.flag_on(monkeypatch)
    agents = F.load_email_agent("ea_held_reply_refused")
    bind_artifact_context(no_egress=True, agent_name="email-assistant", session_id="s-1")
    calls: list = []

    async def no_call(*a, **k):
        calls.append(a)
        raise AssertionError("no gateway call")

    monkeypatch.setattr(agents, "_post", no_call)
    monkeypatch.setattr(agents, "_get", no_call)
    assert eg.HELD_REFUSED_PARAMS["send_email"] == {"reply_to_email_id", "reply_all"}
    out = await agents.send_email(F.BOX, body="hi", reply_to_email_id="m-1")
    assert out == agents._HELD_REPLY_REFUSED
    out = await agents.send_email(F.BOX, body="hi", to=[F.KNOWN], reply_all=True)
    assert out == agents._HELD_REPLY_REFUSED
    assert calls == []


@pytest.mark.usefixtures("_hermetic")
def test_only_the_email_agent_holds_a_held_card_tool(monkeypatch) -> None:
    """Fix round 1 (P3). With the switch on, ``is_egress_tool`` reads the
    four tools of the held card as card-bound in any run, not only in a held
    run of the email agent. The scoping is safe only while no other agent
    holds a tool of those names. Mutation: give another in-repo agent a tool
    named ``send_email`` → red."""
    F.flag_on(monkeypatch)
    holders: dict[str, set[str]] = {}
    for name, rel in _in_repo_agents().items():
        held = _run_tools(name, rel, no_egress=False) & set(eg.HELD_CARD_SEND_TOOLS)
        if held:
            holders[name] = held
    assert holders == {"email-assistant": set(eg.HELD_CARD_SEND_TOOLS)}
