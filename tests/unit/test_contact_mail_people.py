"""CT-1 (task 3602): the mail tier of the colleague lookup, no database.

Three real misspellings from production (2026-11-16). The whole phrase is
scored against the whole name, because one word alone scores too low.
"""
from __future__ import annotations

import pytest
from acb_skills import profile_tools as pt
from gateway.routes.email.transport import contacts as C

PAIRS = [
    ("Vidyadhar Nayak", "Bidyadhar Nayak"),
    ("Sanjeev Goyal", "Sanjeev Goel"),
    ("Vijay Varda", "Vijay Raghav Varada"),
]


@pytest.mark.parametrize(("asked", "name"), PAIRS)
def test_a_real_misspelling_is_a_closest_hit(asked: str, name: str) -> None:
    email = name.lower().replace(" ", ".") + "@x.test"
    got = C._closest_mail(asked.lower(), [(name, email)], [], set(), "")
    assert [(g.email, g.match, g.source) for g in got] == [(email, "closest", "sent")]


def test_a_sender_is_seen_and_a_recipient_wins_over_a_sender() -> None:
    rows = [("Sanjeev Goel", "sg@x.test")]
    assert C._closest_mail("sanjeev goyal", [], rows, set(), "")[0].source == "seen"
    assert C._closest_mail("sanjeev goyal", rows, rows, set(), "")[0].source == "sent"


def test_org_never_appears_and_a_far_name_is_left_out() -> None:
    got = C._closest_mail("vidyadhar nayak", [("Bidyadhar Nayak", "b@x.test"),
                                              ("Zed Quill", "z@x.test")], [], set(), "")
    assert [g.email for g in got] == ["b@x.test"]
    assert all(g.source != "org" for g in got)


def test_have_and_the_domain_filter_apply() -> None:
    rows = [("Bidyadhar Nayak", "b@x.test"), ("Bidyadhar Nayak", "b@y.test")]
    got = C._closest_mail("vidyadhar nayak", rows, [], {"b@x.test"}, r"(^|\.)y\.test$")
    assert [g.email for g in got] == ["b@y.test"]


def test_the_answer_names_the_mail_and_never_says_nobody() -> None:
    body = {"source": "mail", "matches": [{"name": "Bidyadhar Nayak", "email": "b@x.test"}],
            "total": 1, "directory": True, "truncated": False, "near": True, "mail": True}
    out = pt.format_answer(body, "Vidyadhar Nayak")
    assert "from your mail, not a member of Metorite" in out
    assert "Nobody matches" not in out
    assert "<b@x.test>" in out


def _nobody(**flags: bool) -> str:
    body = {"source": None, "matches": [], "total": 0, **flags}
    return pt.format_answer(body, "Vidyadhar Nayak")


def test_the_nobody_text_names_the_three_places() -> None:
    out = _nobody(directory=True, mail_searched=True)
    assert "members" in out and "People" in out and "your mail" in out


@pytest.mark.parametrize("flags", [{"directory": True}, {"directory": False}])
def test_the_nobody_text_never_claims_the_mail_it_did_not_search(flags: dict) -> None:
    # CT-1 verify, finding 2: a member without feature:email gets no mail tier.
    assert "mail" not in _nobody(**flags)


def test_without_the_directory_the_text_names_only_aliases_and_mail() -> None:
    out = _nobody(directory=False, mail_searched=True)
    assert "your saved aliases or your mail" in out
    assert "the members" not in out


def test_a_recipient_with_no_sent_name_is_still_sent() -> None:
    got = C._closest_mail("sanjeev goyal", [("", "sg@x.test")],
                          [("Sanjeev Goel", "sg@x.test")], set(), "")
    assert [(g.email, g.name, g.source) for g in got] == [("sg@x.test", "Sanjeev Goel", "sent")]
