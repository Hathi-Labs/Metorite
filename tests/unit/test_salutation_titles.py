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
        ("Ramesh Bhaijaan", "Ramesh Bhaijaan"),
        ("Vijay ji", "Vijay ji"),
        ("Shri Ramesh Garu", "Shri Ramesh Garu"),
        ("Dr Ramesh Garu", "Dr Ramesh"),
        ("Dr. Anil Mehta Jr", "Dr. Mehta"),
        ("Prof. Jane Smith III", "Prof. Smith"),
        ("Dr Garu", ""),
        ("Dr. Jr", ""),
        ("Shri", ""),
        ("Dr.", ""),
        ("Kumari", "Kumari"),
        ("Rajesh IAS", "Rajesh"),
        ("Priya Rao IFS", "Priya"),
        ("Dr. Rajesh Kumar IPS", "Dr. Kumar"),
        ("Vijay ji", "Vijay ji"),
        ("Ramesh Garu", "Ramesh Garu"),
        ("Kumari Devi", "Kumari Devi"),
        ("Anna Smith", "Anna"),
        ("Alice Kumar", "Alice"),
        ("Singh Rao", "Singh"),
        ("alice@example.com", ""),
        # Task 1894: one native-script title for each script.
        ("श्री रमेश कुमार", "श्री रमेश"),  # Devanagari
        ("डॉ. अनिल मेहता", "डॉ. मेहता"),
        ("திரு முருகன்", "திரு முருகன்"),  # Tamil
        ("శ్రీ వెంకటేష్ రావు", "శ్రీ వెంకటేష్"),  # Telugu
        ("ಶ್ರೀ ರಮೇಶ್ ಕುಮಾರ್", "ಶ್ರೀ ರಮೇಶ್"),  # Kannada
        ("ശ്രീ രമേഷ് കുമാർ", "ശ്രീ രമേഷ്"),  # Malayalam
        ("শ্রী রমেশ দাস", "শ্রী রমেশ"),  # Bengali
        ("શ્રી રમેશ પટેલ", "શ્રી રમેશ"),  # Gujarati
        ("ਸ੍ਰੀ ਹਰਪ੍ਰੀਤ ਸਿੰਘ", "ਸ੍ਰੀ ਹਰਪ੍ਰੀਤ"),  # Gurmukhi
        ("ਸ਼੍ਰੀ ਹਰਪ੍ਰੀਤ ਸਿੰਘ", "ਸ਼੍ਰੀ ਹਰਪ੍ਰੀਤ"),  # Gurmukhi, composed sha
        ("ଶ୍ରୀ ରମେଶ ଦାସ", "ଶ୍ରୀ ରମେଶ"),  # Odia
        ("جناب عمران خان", "جناب عمران"),  # Urdu
        ("श्री", ""),
        # A leading suffix form is not the name. A lone one stays.
        ("Sir Ramesh", "Ramesh"),
        ("Madam Curie", "Curie"),
        ("Sahab Khan", "Khan"),
        ("Ji Kumar", "Kumar"),
        ("Sir", "Sir"),
        # A first token with no letter is not the name.
        ("😀 Ramesh", "Ramesh"),
        ("123 Ramesh", "Ramesh"),
        ("😀", "😀"),
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


# Openers of _GREETING_START_RE that a catalogue spelling also spells. They are
# greeting words, not a second title list. Each one needs a reason.
# None collides today: no opener of _GREETING_START_RE (dear, hi, hello, hey,
# greetings, respected, good ..., namaste, namaskar, namaskaram, namaskara,
# vanakkam, hola, bonjour, salut, hallo, ciao) is a catalogue spelling. "sir"
# is a suffix row, and it is not an opener. Add an entry here, with a reason,
# only when a future opener collides.
_OPENER_ALLOW: dict[str, str] = {}

_REGEX_NOISE = re.compile(r"\\\.\??|[\^\$\(\)\?:\\]")


def _norm(word: str) -> str:
    return word.strip().casefold().rstrip(".")


def _catalogue_spellings() -> set[str]:
    return {_norm(s) for h in hon.HONORIFICS for s in h.spellings}


def second_list_hits(src: str) -> set[str]:
    """Catalogue spellings that a module spells in a string literal.

    A literal counts when it, or one `|` alternative of it, equals a catalogue
    spelling after case-folding and dropping a trailing full stop. Docstrings
    are skipped."""
    import ast

    tree = ast.parse(src)
    docs: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(
                body[0].value, ast.Constant
            ):
                docs.add(id(body[0].value))
    spellings = _catalogue_spellings() - set(_OPENER_ALLOW)
    hits: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docs:
            continue
        for alt in node.value.split("|"):
            cleaned = _norm(_REGEX_NOISE.sub("", alt))
            if cleaned in spellings:
                hits.add(cleaned)
    return hits


_MUTATIONS = {
    "regex_new_titles": (
        "import re\n"
        'X = re.compile(r"^(tmt|hafiz|sushri|maulana|haji|ca|cs)\\.?$")\n'
    ),
    "tuple_of_titles": 'X = ("tmt", "hafiz", "sushri")\n',
    "old_list_back": (
        "import re\n"
        'X = re.compile(r"^(mr|mrs|ms|mx|dr|er)")\n'
    ),
}


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_fence_fails_on_a_second_title_list(name: str) -> None:
    assert second_list_hits(_MUTATIONS[name]), name


def test_fence_expected_hits() -> None:
    assert second_list_hits(_MUTATIONS["regex_new_titles"]) == {
        "tmt", "hafiz", "sushri", "maulana", "haji", "ca", "cs"
    }
    assert second_list_hits(_MUTATIONS["old_list_back"]) == {"mr", "mrs", "ms", "mx", "dr", "er"}


def test_drafter_keeps_no_second_title_list() -> None:
    """Fence: the drafter reads titles from acb_common.honorifics only."""
    src = Path(dr.__file__).read_text(encoding="utf-8")
    assert "from acb_common import honorifics" in src
    assert "_HONORIFIC_RE" not in src
    assert second_list_hits(src) == set()
