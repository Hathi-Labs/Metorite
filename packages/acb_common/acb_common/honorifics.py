"""The one catalogue of forms of address (personalization spec 16.4.2).

DATA plus small pure helpers. PZ-41 (the email drafter) and PZ-40 (the name
question) both read this module, and no other module may keep a second list
of titles (fence: tests/unit/test_salutation_titles.py).

Rules that bind every reader (spec 16.4.1):
  * Nothing infers a form from a name, a region, a language or a community.
    A reader may RECOGNISE a form that the person's own name already carries.
  * The tag shows how sure the row is. V = a fetched page confirms it.
    K = many people use it. U = unverified, so a native speaker must check it
    before any choice list ships it.
  * Spelling variants are one form (rule 9).
"""

from __future__ import annotations

from dataclasses import dataclass

# Where the form goes with the name.
PREFIX = "prefix"  # before the name: "Shri Ramesh"
SUFFIX = "suffix"  # after the name, spaced: "Ramesh garu"
ATTACHED = "attached"  # joined to the name: "Vijaybhai"

# What a prefix takes after it.
GIVEN_NAME = "given_name"  # "Shri Ramesh": the title goes with the given name
SURNAME = "surname"  # "Dr. Mehta": the title goes with the surname


@dataclass(frozen=True)
class Honorific:
    form: str  # the canonical spelling, for a choice list
    spellings: tuple[str, ...]  # lower case, no full stop, every match
    placement: str  # PREFIX, SUFFIX or ATTACHED
    follows: str  # GIVEN_NAME or SURNAME for a prefix, "" for the rest
    register: str
    tag: str  # "V", "K" or "U"
    # True when a greeting keeps the title ("Dear Shri Ramesh,"). False for a
    # regulated or professional title, which the greeting drops ("Dear Priya,").
    greet_title: bool = True
    # True when the form is also a common given name (Kumari, Selvi, Anna).
    # Such a form is a title only when a name follows it, and a suffix reader
    # never keeps it.
    also_name: bool = False


def _p(form, spellings, follows, register, tag, **kw):
    return Honorific(form, tuple(spellings), PREFIX, follows, register, tag, **kw)


def _s(form, spellings, register, tag, **kw):
    return Honorific(form, tuple(spellings), SUFFIX, "", register, tag, **kw)


def _a(form, spellings, register, tag, **kw):
    return Honorific(form, tuple(spellings), ATTACHED, "", register, tag, **kw)


_G, _S = GIVEN_NAME, SURNAME

HONORIFICS: tuple[Honorific, ...] = (
    # --- Pan-Indian and English
    _s("sir", ("sir", "sar", "saar"), "formal, warm", "K"),
    _s("madam", ("madam", "mam", "ma'am"), "formal", "K"),
    _p("Mr.", ("mr",), _S, "formal", "K"),
    _p("Mrs.", ("mrs",), _S, "formal, married", "K"),
    _p("Ms.", ("ms",), _S, "formal", "K"),
    _p("Mx.", ("mx",), _S, "formal", "K"),
    _p("Miss", ("miss",), _S, "formal, unmarried", "K"),
    _p("Dr.", ("dr",), _S, "formal", "K"),
    # --- Hindi belt
    _s("ji", ("ji", "jee"), "respectful, warm", "K"),
    _p("Shri", ("shri", "sri", "shree", "sree", "shrii", "shiri"), _G, "formal, male", "V"),
    _p(
        "Shrimati",
        ("shrimati", "smt", "srimati", "srimathi", "shrimathi"),
        _G,
        "formal, female, married",
        "V",
    ),
    _p("Sushri", ("sushri",), _G, "formal, female, any status", "V"),
    _p("Kumari", ("kumari", "kum"), _G, "formal, female, unmarried", "K", also_name=True),
    _s("Sahab", ("sahab", "sahib", "saheb", "saab"), "respectful, old style", "V"),
    _s("Bhaisahab", ("bhaisahab",), "respectful, kinship", "K"),
    _s("Bhai", ("bhai", "bhaiya"), "kinship", "K"),
    _s("Didi", ("didi", "behenji"), "kinship", "K"),
    _s("Dada", ("dada",), "kinship, ambiguous", "K"),
    _p("Pandit", ("pandit", "pt"), _G, "title, linked to a community", "V"),
    # --- Punjabi and Sikh
    _p("Sardar", ("sardar",), _G, "respectful, male", "V"),
    _p("Giani", ("giani",), _G, "title, a religious or scholarly role", "V"),
    _s("Bhaji", ("bhaji",), "kinship", "K"),
    # --- Gujarati and Marathi
    _a("-bhai", ("bhai",), "respectful, warm, male", "K"),
    _a("-ben", ("ben",), "respectful, warm, female", "K"),
    _p("Sau.", ("sau",), _G, "formal, female, married", "K"),
    _a("-rao", ("rao",), "traditional, male", "K"),
    _s("Tai", ("tai",), "kinship, elder sister", "K", also_name=True),
    _s("Bhau", ("bhau",), "kinship, elder brother", "K"),
    # --- Bengali, Odia, Assamese, northeast
    _a("-da", ("da",), "kinship, elder brother", "K"),
    _a("-di", ("di",), "kinship, elder sister", "K"),
    _s("Moshai", ("moshai", "mashai"), "formal, rare in business", "K"),
    _s("Bhaina", ("bhaina",), "kinship", "U"),
    _s("Apa", ("apa",), "kinship", "U"),
    _s("Bhauni", ("bhauni",), "kinship", "U"),
    _s("Baideo", ("baideo",), "kinship", "U"),
    _p("Pu", ("pu",), _G, "formal, respectful (Mizo)", "K", also_name=True),
    _p("Pi", ("pi",), _G, "formal, respectful (Mizo)", "K", also_name=True),
    _s("Sap", ("sap",), "respectful, from sahib (Mizo)", "V"),
    _p("Kong", ("kong",), _G, "respectful (Khasi)", "K", also_name=True),
    _p("Bah", ("bah",), _G, "respectful (Khasi)", "K", also_name=True),
    # --- Telugu, Tamil, Kannada, Malayalam
    _s("garu", ("garu", "gaaru"), "respectful to formal, any gender", "K"),
    _s("Anna", ("anna",), "kinship", "K", also_name=True),
    _s("Akka", ("akka",), "kinship", "K"),
    _p("Thiru", ("thiru", "tiru"), _G, "formal, male", "V"),
    _p("Thirumathi", ("thirumathi", "tmt"), _G, "formal, female, married", "V"),
    _p("Selvi", ("selvi",), _G, "formal, female, unmarried", "K", also_name=True),
    _p("Selvan", ("selvan",), _G, "young men, on invitations", "U", also_name=True),
    _s("avargal", ("avargal", "avarkal", "avl"), "very formal, rare in chat", "K"),
    _s("avare", ("avare", "avaru"), "respectful, formal or spoken", "K"),
    _s("Chettan", ("chettan",), "kinship, elder brother", "K"),
    _s("Chechi", ("chechi",), "kinship, elder sister", "K"),
    # --- Urdu and Muslim usage
    _p("Janab", ("janab",), _G, "formal, male", "K"),
    _p("Mohtaram", ("mohtaram", "mohtarma"), _G, "formal", "K"),
    _s("Sahiba", ("sahiba",), "respectful", "V"),
    _s("Begum", ("begum",), "traditional, female, married", "V"),
    _p("Haji", ("haji",), _G, "religious title", "K"),
    _p("Hafiz", ("hafiz",), _G, "religious title", "K"),
    _p("Maulana", ("maulana",), _G, "religious title", "K"),
    _p("Maulvi", ("maulvi",), _G, "religious title", "K"),
    _p("Qari", ("qari",), _G, "religious title", "K"),
    _p("Mufti", ("mufti",), _G, "religious title", "K"),
    _s("Baji", ("baji",), "kinship, elder sister", "K"),
    # --- Professional prefixes
    _p("Er.", ("er",), _G, "title", "K"),
    _p("Adv.", ("adv",), _G, "title", "K", greet_title=False),
    _p("CA", ("ca",), _G, "regulated title, also after the name", "K", greet_title=False),
    _p("CS", ("cs",), _G, "regulated title, also after the name", "K", greet_title=False),
    _p("Prof.", ("prof",), _S, "title", "K"),
    _p("Col.", ("col",), _S, "rank", "K"),
    _p("Maj.", ("maj",), _S, "rank", "K"),
    _p("Capt.", ("capt",), _S, "rank", "K"),
    _p("Lt.", ("lt",), _S, "rank", "K"),
    _p("Gen.", ("gen",), _S, "rank", "K"),
    _p("Cdr.", ("cdr",), _S, "rank", "K"),
    _p("Hon'ble", ("hon'ble", "honble"), _S, "judges and ministers", "K"),
)

_PREFIX_INDEX: dict[str, Honorific] = {
    s: h for h in HONORIFICS if h.placement == PREFIX for s in h.spellings
}
_SUFFIX_INDEX: dict[str, Honorific] = {
    s: h for h in HONORIFICS if h.placement == SUFFIX for s in h.spellings
}


def normalize(token: str) -> str:
    """Lower case, no full stop at the end, a plain apostrophe."""
    return (token or "").strip().replace(chr(0x2019), "'").rstrip(".").lower()


def find_prefix(token: str) -> Honorific | None:
    """The prefix title that this token spells, or None."""
    return _PREFIX_INDEX.get(normalize(token))


def find_suffix(token: str) -> Honorific | None:
    """The spoken suffix (ji, garu, sir, ...) that this token spells, or None."""
    return _SUFFIX_INDEX.get(normalize(token))
