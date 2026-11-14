"""HRD-18a: the one matcher of a name as people say it (``chat_failure_modes.md`` F1).

Pure. The fixed pairs come from production (2026-11-11 to 2026-11-14): a
member said "Sharath" for «Sharat Satyanarayana», "Swagata" for «Sougata
Maji», "nuropeak" for «Neuropeak». Each pair must stay near, and each pair of
two different names must stay apart. The SQL floor of the CRM near pass is
pinned here too, so a change to it is a change to this fence.

The R8 suite ``test_crm_near_search_r8.py`` proves the SQL on a real database.
"""

from __future__ import annotations

import pytest
from acb_common import name_match as m

#: The fixed pairs of the task (HRD-18a acceptance 1). Each is near.
FIXED_PAIRS = (
    ("Sharath", "Sharat"),
    ("Swagata", "Sougata"),
    ("nuropeak", "Neuropeak"),
    ("Neuropeak", "neuropeek"),
    ("Magnatech", "Magnetech"),
)

#: Two different names each. None may be near.
APART = (
    ("Sharath", "Suresh"),
    ("Sharath", "Sharma"),
    ("Sharath", "Bharath"),
    ("Swagata", "Sanjay"),
    ("Magnatech", "Microtech"),
    ("Neuropeak", "Europe"),
    ("Kiran", "Karan"),
    ("Sita", "Shweta"),
    ("Rajesh", "Ramesh"),
)


@pytest.mark.parametrize(("asked", "name"), FIXED_PAIRS)
def test_each_fixed_pair_is_near_both_ways(asked: str, name: str) -> None:
    assert m.is_near(asked, name), (asked, name, m.name_score(asked, name))
    assert m.is_near(name, asked), (name, asked, m.name_score(name, asked))


@pytest.mark.parametrize(("asked", "name"), APART)
def test_two_different_names_stay_apart(asked: str, name: str) -> None:
    assert not m.is_near(asked, name), (asked, name, m.name_score(asked, name))


def test_the_fold_takes_each_listed_variant() -> None:
    # th/t, sh/s, w/v, aa/a, ee/i, oo/u, ou/o, a double letter, a final h.
    assert m.fold("sharath") == m.fold("sarat") == "sarat"
    assert m.fold("swati") == m.fold("svati")
    assert m.fold("aarav") == m.fold("arav")
    assert m.fold("deepak") == m.fold("dipak")
    assert m.fold("pooja") == m.fold("puja")
    assert m.fold("sougata") == m.fold("sogata")
    assert m.fold("anna") == m.fold("ana")
    assert m.fold("mahesh") == m.fold("mahes")


def test_a_name_matches_one_of_its_words() -> None:
    # The production cases, against the whole name of the record.
    assert m.is_near("Swagata", "Sougata Maji")
    assert m.is_near("Sharath", "Sharat Satyanarayana")
    assert m.is_near("nuropeak", "Neuropeak - Snowflake 3D printer")
    # An initial matches a word that starts with it.
    assert m.is_near("Sougata M", "Sougata Maji")
    # A second word that nothing holds pulls the score down.
    assert not m.is_near("Sharath Kumar", "Sharat Satyanarayana")


def test_an_exact_fold_outranks_a_sound_match() -> None:
    assert m.name_score("Sharath", "Sharat") == 1.0
    assert m.name_score("Swagata", "Sougata") == pytest.approx(m.SOUND_WEIGHT)


def test_the_words_of_a_text() -> None:
    assert m.words("Neuropeak - Snowflake 3D") == ["neuropeak", "snowflake", "3d"]
    assert m.words("Ś́arat") == ["sarat"]
    assert m.words("") == [] and m.words(None) == []


def test_which_query_gets_a_near_pass() -> None:
    assert m.near_eligible("Sharath") and m.near_eligible("Mira")
    assert not m.near_eligible("3D") and not m.near_eligible("2024")
    assert not m.near_eligible("abc")
    assert m.near_query(["Sharath", "3D", "printer"])
    assert not m.near_query(["Sharath", "3D", "printer", "order"])
    assert not m.near_query(["3D", "abc"])
    assert not m.near_query([])


def test_the_floors_are_the_ones_the_pairs_were_measured_at() -> None:
    """The SQL floor sits under the lowest fixed pair that SQL must find.

    Measured on pg_trgm 1.6 (``test_crm_near_search_r8.py`` repeats it):
    Magnatech in «Magnetech Ltd» is 0.54, so a floor above 0.54 loses it.
    """
    assert m.NEAR_WORD_SIMILARITY == 0.5
    assert m.NEAR_SCORE == 0.6
    assert m.NEAR_MIN_LETTERS == 4 and m.NEAR_MAX_WORDS == 3
