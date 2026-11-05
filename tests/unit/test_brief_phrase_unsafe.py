"""WS-BRIEF BR-2a-2 review fix — the lead may state no number word and carry
no link (``brief/phrase.py`` ``unsafe_reason``). Pure.

Mutation (R7): empty ``NUMBER_WORDS`` or ``LINK_MARKS``. The tests go red.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from gateway.brief import phrase


@pytest.mark.parametrize("text", [
    "Three people wait on you.", "Priya waits in four threads.", "About a dozen mails.",
    "Half of them are old.", "TWENTY items wait.", "Twenty-one items wait.",
    "Four/five items wait.", "Ten\u2019s of mails."])
def test_a_number_word_is_refused(text: str) -> None:
    assert phrase.unsafe_reason(text) == "number_word"


@pytest.mark.parametrize("text", [
    "Read [Confirm your login](https://evil.example/sso).", "See www.evil.example now.",
    "Open <b>this</b>.", "Mail me at a@b.test.", "Use `this`."])
def test_a_link_or_markup_is_refused(text: str) -> None:
    assert phrase.unsafe_reason(text) == "link"


@pytest.mark.parametrize("text", [
    "Priya waits on you in several threads.", "2 approvals wait on you.",
    "Your day looks calm."])
def test_a_plain_sentence_stands(text: str) -> None:
    assert phrase.unsafe_reason(text) is None


async def test_the_call_is_refused_when_the_router_went_off(monkeypatch) -> None:
    """Review F4: no unbilled call after a flag reload. Mutation: drop the
    ``routing_is_on`` check in ``phrase.phrase``. This test goes red."""
    from acb_llm import routed

    async def never(*a, **kw):
        raise AssertionError("no model call with the Router off")

    monkeypatch.setattr(routed, "routing_is_on", lambda: False)
    monkeypatch.setattr(phrase, "_ask", never)
    with pytest.raises(phrase.RouterOff):
        await phrase.phrase("ana@x.test", [], ref="ref-1")


def test_judge_refuses_a_fence_valid_answer_with_a_number_word_or_a_link() -> None:
    """Review P2: the wiring in ``judge``. Mutation: drop the ``unsafe_reason``
    loop in ``judge``. This test goes red."""
    import json

    from tests.unit.test_brief_phrase import CLAIMS, answer

    ok = answer(("Priya waits on you.", ["c1"]))
    assert phrase.judge(ok, CLAIMS) is not None
    for bad in ("Priya waits on you in four threads.",
                "Read [this](https://evil.example/x) from Priya."):
        assert phrase.judge(answer((bad, ["c1"])), CLAIMS) is None
    assert json.loads(ok)["sentences"]
