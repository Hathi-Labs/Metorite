"""PZ-52 (personalization.md 5.3a): ``style.languages``, the ordered list.

R7 fences named here:

* ``languages-cap``: at most 6 tags, a seventh is a 422 through the route.
* ``languages-repeat-drops``: a repeat drops and never counts toward the cap.
* ``languages-dual-write``: ``language`` always equals ``languages[0]``.
* ``languages-old-read``: an old profile reads as a list of one, and the
  mismatch rule lets ``language`` win.
* ``languages-block-line``: the list line, and ``block_language`` still
  returns the first tag.
"""

from __future__ import annotations

import pytest
from acb_common import assistant_profile as ap
from fastapi import HTTPException
from gateway.routes.admin import assistant_profile as route


def _clean(**style):
    return ap._clean_style(style)


# ── The write ──────────────────────────────────────────────────────────────


def test_a_list_sets_language_to_its_first_tag() -> None:
    assert _clean(languages=["en", "hi", "kn"]) == {
        "language": "en", "languages": ["en", "hi", "kn"]}


def test_a_language_alone_gives_a_list_of_one() -> None:
    assert _clean(language="kn") == {"language": "kn", "languages": ["kn"]}


def test_a_list_wins_when_one_write_holds_both() -> None:
    assert _clean(language="ta", languages=["hi", "en"]) == {
        "language": "hi", "languages": ["hi", "en"]}


def test_an_empty_list_clears_both() -> None:
    assert _clean(language="kn", languages=[]) == {}
    assert _clean(languages=[]) == {}


def test_tags_are_lowercased_and_a_repeat_drops() -> None:
    assert _clean(languages=["EN", "hi", "en", "HI", "kn"])["languages"] == ["en", "hi", "kn"]


def test_six_tags_pass_and_seven_fail() -> None:
    six = ["en", "hi", "kn", "ta", "te", "mr"]
    assert _clean(languages=six)["languages"] == six
    with pytest.raises(ValueError, match="at most 6"):
        _clean(languages=[*six, "bn"])


def test_a_repeat_is_not_a_seventh_tag() -> None:
    six = ["en", "hi", "kn", "ta", "te", "mr"]
    assert _clean(languages=[*six, "EN"])["languages"] == six


@pytest.mark.parametrize("bad", ["", "x", "en-48213", "e n", 5, None, "a" * 17])
def test_a_bad_tag_is_refused(bad) -> None:
    with pytest.raises(ValueError):
        _clean(languages=["en", bad])


def test_the_list_must_be_a_list() -> None:
    with pytest.raises(ValueError, match="list"):
        _clean(languages="en")


def test_the_route_answers_422_for_a_seventh_tag() -> None:
    body = {"key": "style", "value": {"languages": ["en", "hi", "kn", "ta", "te", "mr", "bn"]}}
    with pytest.raises(HTTPException) as caught:
        route._validate(route.ProfilePut, body)
    assert caught.value.status_code == 422


def test_the_route_keeps_an_old_client_that_sends_only_language() -> None:
    put = route._validate(route.ProfilePut, {"key": "style", "value": {"language": "hi"}})
    assert put.value == {"language": "hi", "languages": ["hi"]}


# ── The read ───────────────────────────────────────────────────────────────


def _read(style):
    return ap.stored_profile({"v": 1, "style": style})["style"]


def test_an_old_profile_reads_as_a_list_of_one() -> None:
    assert _read({"language": "kn", "length": "short"}) == {
        "language": "kn", "languages": ["kn"], "length": "short"}


def test_a_current_profile_reads_as_stored() -> None:
    assert _read({"language": "en", "languages": ["en", "hi", "kn"]}) == {
        "language": "en", "languages": ["en", "hi", "kn"]}


def test_a_mismatch_lets_language_win_and_returns_a_list_of_one() -> None:
    assert _read({"language": "ta", "languages": ["en", "hi"]}) == {
        "language": "ta", "languages": ["ta"]}


def test_a_list_with_no_language_reads_with_its_first_tag() -> None:
    assert _read({"languages": ["hi", "en"]}) == {"language": "hi", "languages": ["hi", "en"]}


def test_a_bad_stored_list_drops_and_language_stays() -> None:
    assert _read({"language": "kn", "languages": "en,hi"}) == {
        "language": "kn", "languages": ["kn"]}


def test_nothing_stored_reads_empty() -> None:
    assert _read({}) == {}
    assert _read({"languages": []}) == {}


# ── The block ──────────────────────────────────────────────────────────────


def test_the_block_line_lists_the_languages_in_order() -> None:
    line = ap._style_line({"language": "en", "languages": ["en", "hi", "kn"]}, [])
    assert line == (
        "Languages: English (en), then Hindi (hi), then Kannada (kn). "
        "Answer in the language of their message. "
        "When that is unclear, use the first in the list.")
    assert "\n" not in line


def test_a_list_of_one_keeps_the_pz51_line() -> None:
    line = ap._style_line({"language": "kn", "languages": ["kn"]}, [])
    assert line == "Language: Kannada (kn). Answer in it, unless they write in another language."


def test_an_unknown_tag_stays_bare_in_the_list() -> None:
    line = ap._style_line({"languages": ["en", "or-in"]}, [])
    assert line.startswith("Languages: English (en), then or-in. Answer in")


def test_block_language_returns_the_first_tag_of_the_list() -> None:
    line = ap._style_line({"language": "en", "languages": ["en", "hi"]}, [])
    assert ap.block_language("## x\n" + line + "\nend") == "en"
    bare = ap._style_line({"languages": ["or-in", "hi"]}, [])
    assert ap.block_language(bare) == "or-in"


def test_the_whole_block_carries_the_list_line() -> None:
    block = ap.render_block({"v": 1, "style": {"languages": ["en", "hi", "kn"]}})
    assert "Languages: English (en), then Hindi (hi), then Kannada (kn)." in block
    assert ap.block_language(block) == "en"


def test_an_old_profile_with_kn_renders_the_pz51_line() -> None:
    block = ap.render_block({"v": 1, "style": {"language": "kn"}})
    assert "Language: Kannada (kn). Answer in it" in block
    assert ap.block_language(block) == "kn"


# ── The chat tool keeps the pair in step ───────────────────────────────────


def test_profile_set_merge_replaces_the_list_when_one_language_is_named() -> None:
    from acb_skills.profile_tools import _merge_style

    prior = {"language": "en", "languages": ["en", "hi", "kn"], "length": "short"}
    assert _merge_style(prior, {"language": "ta"}) == {"language": "ta", "length": "short"}
    # The same first language changes nothing, so the tool answers "Already set".
    assert _merge_style(prior, {"language": "en"}) == prior
    # A null clears the list too.
    assert _merge_style(prior, {"language": None}) == {"length": "short"}
    assert _merge_style(prior, {"languages": None}) == {"length": "short"}
    assert _merge_style(prior, {"languages": ["kn", "en"]}) == {
        "language": "en", "languages": ["kn", "en"], "length": "short"}
    # The gateway then lets the list win, so ``language`` follows its first tag.
    assert ap._clean_style(_merge_style(prior, {"languages": ["kn", "en"]})) == {
        "language": "kn", "languages": ["kn", "en"], "length": "short"}
