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
SECTION_21_WITHHELD = H374_WITHHELD


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
