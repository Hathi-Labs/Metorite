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
    "Half of them are old.", "TWENTY items wait."])
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
