"""HRD-18a: the one matcher of a name as people say it (``chat_failure_modes.md`` F1).

Pure. The fixed pairs come from production (2026-11-11 to 2026-11-14): a
member said "Sharath" for «Sharat Satyanarayana», "Swagata" for «Sougata
Maji», "nuropeak" for «Neuropeak». Each pair must stay near, and each pair of
two different names must stay apart. The floors of the CRM near pass are
pinned here too, so a change to one is a change to this fence.

The repair round of 2026-11-14 added the common transliteration folds
(aspirates, x, ow, iya) and the verifier's 40 pairs of different names. Six of
them stay near, and ``KNOWN_NEAR`` names them: a near name always asks the
member, so a near pair of two names costs one question.

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

#: Transliterations of one name (the repair round). Each is near.
VARIANT_PAIRS = (
    ("Lakshmi", "Laxmi"),
    ("Soumya", "Sowmya"),
    ("Raghav", "Ragav"),
    ("Abhishek", "Abishek"),
    ("Aishwarya", "Aishwariya"),
    ("Pooja", "Puja"),
    ("Santosh", "Santhosh"),
    ("Kartik", "Karthik"),
    ("Venna", "Veena"),
)

#: The verifier's pairs of different names (2026-11-14).
VERIFIER_APART = (
    ("Kiran", "Karan"), ("Ramesh", "Suresh"), ("Mahesh", "Ganesh"), ("Rajesh", "Rakesh"),
    ("Anil", "Sunil"), ("Ravi", "Rani"), ("Arun", "Varun"), ("Vinod", "Vinay"),
    ("Manoj", "Mohan"), ("Sita", "Gita"), ("Deepak", "Deepa"), ("Naresh", "Mahesh"),
    ("Sanjay", "Sanjeev"), ("Rohit", "Rohan"), ("Amit", "Sumit"), ("Neha", "Sneha"),
    ("Kamal", "Kamala"), ("Mukesh", "Mukund"), ("Harish", "Girish"), ("Vijay", "Ajay"),
    ("Akash", "Prakash"), ("Dinesh", "Dhanesh"), ("Pradeep", "Sandeep"), ("Lalit", "Lalita"),
    ("Gopal", "Gopi"), ("Nisha", "Asha"), ("Shalini", "Malini"), ("Sachin", "Sachit"),
    ("Bharath", "Sharath"), ("Sharath", "Sharad"), ("Mohan", "Mohini"), ("Ashok", "Alok"),
    ("Meena", "Meera"), ("Rahul", "Rahil"), ("Kavya", "Kavitha"), ("Hema", "Hima"),
    ("Pavan", "Pavani"), ("Shreya", "Shriya"), ("Venkat", "Venkatesh"), ("Ganesh", "Ganesan"),
)

#: The pairs of VERIFIER_APART that stay near (the docstring of
#: ``acb_common.name_match`` names them). A change to this set is a change of
#: the matcher, so review it.
KNOWN_NEAR = frozenset({
    ("Kamal", "Kamala"), ("Lalit", "Lalita"), ("Pavan", "Pavani"),
    ("Hema", "Hima"), ("Dinesh", "Dhanesh"), ("Venkat", "Venkatesh"),
})

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


@pytest.mark.parametrize(("asked", "name"), VARIANT_PAIRS)
def test_each_transliteration_is_near_both_ways(asked: str, name: str) -> None:
    assert m.is_near(asked, name), (asked, name, m.name_score(asked, name))
    assert m.is_near(name, asked), (name, asked, m.name_score(name, asked))


@pytest.mark.parametrize(("asked", "name"), APART)
def test_two_different_names_stay_apart(asked: str, name: str) -> None:
    assert not m.is_near(asked, name), (asked, name, m.name_score(asked, name))


def test_only_the_known_pairs_of_different_names_are_near() -> None:
    near = {(a, b) for a, b in VERIFIER_APART if m.is_near(a, b)}
    assert near == KNOWN_NEAR, sorted(near ^ KNOWN_NEAR)


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
    # The repair round: aspirates, x, ow and iya.
    assert m.fold("raghav") == m.fold("ragav")
    assert m.fold("abhishek") == m.fold("abishek")
    assert m.fold("sudhir") == m.fold("sudir")
    assert m.fold("sukhdev") == m.fold("sukdev")
    assert m.fold("phani") == m.fold("fani")
    assert m.fold("laxmi") == m.fold("lakshmi")
    assert m.fold("sowmya") == m.fold("soumya")
    assert m.fold("aishwariya") == m.fold("aishwarya")


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
    # A word longer than NEAR_MAX_CHARS gets no near pass (a pasted hash).
    assert m.near_eligible("a" * m.NEAR_MAX_CHARS)
    assert not m.near_eligible("a" * (m.NEAR_MAX_CHARS + 1))
    assert m.near_query(["Sharath", "3D", "printer"])
    assert not m.near_query(["Sharath", "3D", "printer", "order"])
    assert not m.near_query(["3D", "abc"])
    assert not m.near_query([])


def test_the_floors_are_the_ones_the_pairs_were_measured_at() -> None:
    """SQL only finds candidates, and the Python score judges each one.

    The SQL floor sits under the lowest fixed pair, measured on pg_trgm 1.6:
    "Swagata" in «Sougata Maji» is 0.33 by ``strict_word_similarity``. The
    R8 suite ``test_crm_near_search_r8.py`` proves it on a real database.
    """
    assert m.NEAR_CANDIDATE_SIMILARITY == 0.3
    assert m.NEAR_CANDIDATES == 200
    assert m.NEAR_SCORE == 0.6
    assert m.NEAR_MIN_LETTERS == 4 and m.NEAR_MAX_WORDS == 3
    assert m.NEAR_MAX_CHARS == 40
