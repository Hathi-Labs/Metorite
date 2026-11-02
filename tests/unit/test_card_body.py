"""RX-1: the one rule of a card body (``acb_common.card_body``).

D169 rests on the member's click on a server-built card, so the card must show
the exact change. Each case names the mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_card_body.py -q``.
"""
from __future__ import annotations

import pytest
from acb_common import card_body as cb

NOTE = "The fixed note."


def _rows(**fields: str) -> list[tuple[str, str]]:
    return [(key, f"{key}: {value}") for key, value in fields.items()]


def test_a_card_that_fits_is_whole_and_carries_no_mark() -> None:
    """Mutation: always add the mark -> red."""
    body = cb.card_body(NOTE, _rows(status="stopped", description="short"))
    assert body == f"{NOTE}\nstatus: stopped\ndescription: short"
    assert not cb.is_cut(body)
    assert body.cut == ()


def test_a_short_field_after_a_long_text_still_shows() -> None:
    """THE finding of the review: 4100 characters of description, then a status
    and a lead. Mutation: keep the payload order -> the two lines leave -> red."""
    body = cb.card_body(NOTE, _rows(description="d" * 4100, status="stopped", lead="x@y.example"))
    assert len(body) <= cb.CARD_CLIP
    lines = body.split("\n")
    assert lines[:3] == [NOTE, "status: stopped", "lead: x@y.example"]
    assert lines[3].startswith("description: dd")
    assert lines[-1] == cb.CUT_MARK
    assert body.cut == ("description",)
    assert cb.is_cut(body)


def test_short_fields_show_first_also_when_the_card_fits() -> None:
    """A long text never pushes a short field down the card. Mutation: sort only
    when a cut runs -> red."""
    body = cb.card_body(NOTE, _rows(body="b" * 3000, assignees="a@x.example"))
    assert body.split("\n")[1] == "assignees: a@x.example"


def test_each_short_field_survives_a_long_list_of_them() -> None:
    """Mutation: cut the short block too -> red."""
    shorts = {f"field {i}": "v" * 60 for i in range(30)}
    body = cb.card_body(NOTE, [*_rows(**shorts), ("description", "description: " + "d" * 5000)])
    for key in shorts:
        assert f"{key}: {'v' * 60}" in body
    assert len(body) <= cb.CARD_CLIP


def test_short_fields_that_do_not_fit_refuse_and_name_what_to_shorten() -> None:
    """Rule 3: no card can show this change. Mutation: cut the title -> red."""
    with pytest.raises(cb.CardWontFit) as caught:
        cb.card_body(NOTE, _rows(title="t" * 4100, status="todo"))
    assert "title" in str(caught.value)
    assert "nothing was changed" in str(caught.value)


def test_a_long_text_with_no_room_refuses() -> None:
    """The short fields leave no useful part of the text to show. Mutation: drop
    the floor -> a card with an empty description -> red."""
    with pytest.raises(cb.CardWontFit):
        cb.card_body(NOTE, _rows(title="t" * 3950, description="d" * 200))


def test_two_long_texts_each_keep_a_part() -> None:
    """Mutation: cut the joined text from its tail -> the second text leaves
    -> red."""
    body = cb.card_body(NOTE, _rows(description="d" * 4000, notes="n" * 4000))
    assert "description: dd" in body and "notes: nn" in body
    assert set(body.cut) == {"description", "notes"}
    assert len(body) <= cb.CARD_CLIP


def test_a_short_long_text_gives_its_room_to_the_other() -> None:
    """Mutation: an equal share for each -> the long text loses more than it must
    -> red."""
    body = cb.card_body(NOTE, _rows(notes="n" * 10, description="d" * 5000))
    assert f"notes: {'n' * 10}" in body
    assert body.cut == ("description",)
    assert body.count("d") > 3000


def test_a_cut_keeps_the_fence_closed() -> None:
    """The client draws a fenced value as a token. Mutation: drop the close ->
    red."""
    body = cb.card_body(NOTE, [("description", "description: «" + "d" * 5000 + "»")])
    line = next(ln for ln in body.split("\n") if ln.startswith("description"))
    assert line.endswith("…»")


def test_the_refusal_of_a_held_chat_says_what_to_shorten() -> None:
    """Mutation: a refusal with no field -> red."""
    body = cb.card_body(NOTE, _rows(description="d" * 4100))
    said = cb.cut_refusal(body)
    assert "Shorten description to about" in said
    assert "nothing was changed" in said
    assert "new chat" not in said and " app" not in said


def test_a_body_that_lost_its_type_still_refuses_in_plain_words() -> None:
    """``assign`` adds a line with ``+=``, and a plain string has no record."""
    body = cb.card_body(NOTE, _rows(description="d" * 4100))
    assert "Shorten the long text" in cb.cut_refusal(str(body) + "\nmore")


@pytest.mark.parametrize("value", ["a\nstatus: done", "a\r\nb", "a\tb  c", "a" + chr(0x2028) + "b"])
def test_a_value_holds_one_line(value: str) -> None:
    """Rule 4: a value with a newline can forge a line. Mutation: return the value
    as it is -> red."""
    assert "\n" not in cb.one_line(value)
    assert chr(0x2028) not in cb.one_line(value)
