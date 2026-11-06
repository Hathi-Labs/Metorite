"""PZ-41: the email drafter reads an Indian title as a title, from one catalogue.

The catalogue is ``acb_common.honorifics`` (personalization spec 16.4.2). The
drafter only RECOGNISES a title that the display name already carries.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from acb_common import honorifics as hon
from gateway.routes.email.automation import drafting as dr

PREFIXES = [h for h in hon.HONORIFICS if h.placement == hon.PREFIX]


def _variants(spelling: str):
    for word in (spelling, spelling.upper(), spelling.capitalize()):
        yield word
        yield word + "."


@pytest.mark.parametrize("h", PREFIXES, ids=lambda h: h.form)
def test_every_catalogue_prefix_is_a_title_in_every_case(h) -> None:
    for spelling in h.spellings:
        for word in _variants(spelling):
            got = dr._salutation_name(f"{word} Ramesh Kumar")
            assert got.split()[0] != "", word
            if h.follows == hon.SURNAME:
                assert got == f"{word} Kumar", word
            elif h.greet_title:
                assert got == f"{word} Ramesh", word
            else:
                assert got == "Ramesh", word
            # A title is never the whole greeting name.
            assert got != word


@pytest.mark.parametrize("h", PREFIXES, ids=lambda h: h.form)
def test_a_title_alone_is_not_a_name(h) -> None:
    for spelling in h.spellings:
        got = dr._salutation_name(spelling.upper() + ".")
        if h.also_name:
            assert got == spelling.upper() + "."
        else:
            assert got == ""
            out = dr._ensure_salutation("Thanks.", spelling)
            assert out.startswith("Hello,\n\n")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Shri Ramesh Kumar", "Shri Ramesh"),
        ("Smt. Lakshmi Rao", "Smt. Lakshmi"),
        ("Sri Venkatesh", "Sri Venkatesh"),
        ("THIRU. Murugan", "THIRU. Murugan"),
        ("Thiru Kumar Raja", "Thiru Kumar"),
        ("Kum. Anita", "Kum. Anita"),
        ("Pandit Mohan Lal", "Pandit Mohan"),
        ("Er. Sunil Singh", "Er. Sunil"),
        ("Dr. Anil Mehta", "Dr. Mehta"),
        ("Prof. Jane Smith", "Prof. Smith"),
        ("Adv. Priya Shah", "Priya"),
        ("CA Rakesh Jain", "Rakesh"),
        ("Janab Imran Khan", "Janab Imran"),
        ("Sardar Harpreet Singh", "Sardar Harpreet"),
        ("Ramesh Garu", "Ramesh Garu"),
        ("Vijay ji", "Vijay ji"),
        ("Shri Ramesh Garu", "Shri Ramesh Garu"),
        ("Shri", ""),
        ("Dr.", ""),
        ("Kumari", "Kumari"),
        ("Kumari Devi", "Kumari Devi"),
        ("Anna Smith", "Anna"),
        ("Alice Kumar", "Alice"),
        ("Singh Rao", "Singh"),
        ("alice@example.com", ""),
    ],
)
def test_salutation_name_table(name: str, expected: str) -> None:
    assert dr._salutation_name(name) == expected


def test_suffix_stays_in_the_greeting() -> None:
    assert dr._ensure_salutation("The note.", "Ramesh Garu").startswith("Dear Ramesh Garu,\n\n")
    assert dr._ensure_salutation("The note.", "Vijay ji").startswith("Dear Vijay ji,\n\n")


def test_shri_never_greeted_as_a_name() -> None:
    assert dr._ensure_salutation("Body.", "Shri Ramesh Kumar").startswith("Dear Shri Ramesh,\n\n")
    assert dr._ensure_salutation("Body.", "Shri").startswith("Hello,\n\n")


@pytest.mark.parametrize(
    "opener", ["Namaskar", "Namaskaram", "Vanakkam", "Respected Sir", "Namaskara"]
)
def test_new_openers_count_as_a_greeting(opener: str) -> None:
    body = f"{opener},\n\nThe note."
    assert dr._ensure_salutation(body, "Ramesh") == body


def test_catalogue_rows_are_well_formed() -> None:
    seen: dict[tuple[str, str], str] = {}
    for h in hon.HONORIFICS:
        assert h.tag in {"V", "K", "U"}
        assert h.placement in {hon.PREFIX, hon.SUFFIX, hon.ATTACHED}
        assert h.spellings and all(s == s.lower() and not s.endswith(".") for s in h.spellings)
        if h.placement == hon.PREFIX:
            assert h.follows in {hon.GIVEN_NAME, hon.SURNAME}
        for s in h.spellings:
            key = (h.placement, s)
            assert key not in seen, f"{s} twice: {seen.get(key)} and {h.form}"
            seen[key] = h.form
    # The English rows stay, so nothing regresses.
    for s in ("mr", "mrs", "ms", "mx", "dr", "prof", "er"):
        assert hon.find_prefix(s) is not None
    # Surname parts are never titles (spec 16.4.3).
    for s in ("rao", "naidu", "reddy", "sharma", "singh", "kaur", "kumar", "devi"):
        assert hon.find_prefix(s) is None


def test_drafter_keeps_no_second_title_list() -> None:
    """Fence: the drafter reads titles from acb_common.honorifics only."""
    src = Path(dr.__file__).read_text(encoding="utf-8")
    assert "from acb_common import honorifics" in src
    assert "_HONORIFIC_RE" not in src
    # No title spelled in a literal, such as a tuple or a regex alternation.
    for word in ("shri", "smt", "thiru", "janab", "pandit", "sardar", "adv", "prof"):
        assert not re.search(rf"""["'|(]{word}["'|)]""", src, re.IGNORECASE), word
