"""Task 3601: a held reply card warns loudly about a person it leaves unticked.

The case is session a8296fc6 (2026-11-16): the assistant added
vinay@d3datacenters.com as a CC. He is a new address, so he starts UNTICKED
(owner rule, unchanged). The draft body named him, the member approved, and he
got nothing. Owner decision, 2026-11-16: keep the unticked rule, and make the
card name each unticked address and say when the draft names that person.

Mutations this suite catches:

* the warning is removed: the first test fails.
* the new address starts ticked: the "stays unticked" assertion fails.
* the warning sits after the four-notice cut: the four-notice test fails.

Hermetic: no database, no send.
"""
from __future__ import annotations

from typing import Any

from tests.unit import _held_card_fakes as F
from tests.unit._held_card_fakes import email_tool_mode  # noqa: F401 - OA-7b: owner and pack

TO = "manish@shaurryatele.com"
SB = "sb@d3datacenters.com"
GAURAV = "gaurav@d3datacenters.com"
EA = "ea2md@meghatantra.com"
VINAY = "vinay@d3datacenters.com"
BODY = "Hi Manish,\n\nIntroducing Srishty, Gaurav and Vinay from D3.\n\nRegards"


def _prep(body: str = BODY) -> dict[str, Any]:
    everyone = {"to": [TO], "cc": [SB, GAURAV, EA, VINAY]}
    return {
        "message_id": F.MAIL, "mailbox": "Shaurryatele", "subject": "Re: D3 intro",
        "body": body,
        "recipients": {"reply": {"to": [TO], "cc": []}, "reply_all": everyone},
        "classes": {TO: "thread", SB: "thread", GAURAV: "thread", EA: "thread",
                    VINAY: "new"},
        "marks": {VINAY: "new address — not in your contacts · the assistant added them"},
        "reply_all": True,
    }


def _form(agents: Any, prep: dict[str, Any]) -> dict[str, Any]:
    form = agents._reply_form(prep, True)
    agents._held_reply_form(form, prep)
    return form


def test_vinay_stays_unticked_and_the_card_says_the_draft_names_him() -> None:
    agents = F.load_email_agent("ea_unticked_warn")
    form = _form(agents, _prep())
    # The rule is unchanged: he is unticked.
    assert VINAY not in form["ticked"]["reply_all"]["cc"]
    assert set(form["ticked"]["reply_all"]["cc"]) == {SB, GAURAV, EA}
    first = form["notices"][0]
    assert VINAY in first and "Not ticked" in first
    assert "Your draft names" in first and VINAY in first.split("Your draft names")[1]
    # The older notice stays.
    assert agents._REPLY_NEW_UNTICKED_NOTICE in form["notices"]


def test_the_warning_does_not_claim_a_name_the_draft_lacks() -> None:
    agents = F.load_email_agent("ea_unticked_warn_plain")
    form = _form(agents, _prep("Thanks Manish, talk soon."))
    first = form["notices"][0]
    assert VINAY in first and "Your draft names" not in first


def test_nobody_unticked_means_no_warning() -> None:
    agents = F.load_email_agent("ea_unticked_warn_none")
    prep = _prep()
    prep["classes"][VINAY] = "sent"
    form = _form(agents, prep)
    assert form["notices"] == []


def test_the_warning_survives_four_other_notices() -> None:
    agents = F.load_email_agent("ea_unticked_warn_four")
    prep = _prep()
    prep["notices"] = ["n1", "n2", "n3", "n4"]
    form = _form(agents, prep)
    assert len(form["notices"]) == 4
    assert "Not ticked" in form["notices"][0]
    assert "n4" in form["note"]


def test_a_name_match_is_a_whole_word() -> None:
    agents = F.load_email_agent("ea_unticked_warn_word")
    assert agents._named_in("Vinay Dwivedi", VINAY)
    assert not agents._named_in("Vinayak will join", VINAY)
    assert not agents._named_in("", VINAY)


def test_a_mass_cc_thread_still_gets_a_card() -> None:
    """The warning is bounded. 60 new addresses all start unticked, and the form
    still passes ``clean_reply_form`` (a notice over 1000 characters raises there,
    and the card would not draw: review of PR 2500)."""
    from acb_skills import ask_tools

    agents = F.load_email_agent("ea_unticked_warn_many")
    new = [f"person{n}@vendor{n}.example.com" for n in range(60)]
    prep = _prep("Hi all, " + " ".join(f"person{n}" for n in range(60)))
    prep["recipients"]["reply_all"] = {"to": [TO], "cc": [SB, *new]}
    prep["classes"].update({a: "new" for a in new})
    prep["marks"] = {a: "new address — not in your contacts" for a in new}
    form = _form(agents, prep)
    warning = form["notices"][0]
    assert len(warning) < 900
    assert "Not ticked" in warning and " more" in warning
    assert person_first(new) in warning
    assert ask_tools.clean_reply_form(form) is not None  # no "notice is too long"


def person_first(addresses: list[str]) -> str:
    return addresses[0]
