"""The one catalogue of forms of address (personalization spec 16.4.2).

DATA plus small pure helpers. PZ-41 (the email drafter) and PZ-40 (the name
question) both read this module, and no other module may keep a second list
of titles or forms (fences: tests/unit/test_salutation_titles.py and
tests/unit/test_honorifics.py).

Rules that bind every reader (spec 16.4.1):
  * Nothing infers a form from a name, a region, a language or a community.
    A reader may RECOGNISE a form that the person's own name already carries.
  * The tag shows how sure the row is. V = a fetched page confirms it.
    K = many people use it. U = unverified, so a native speaker must check it
    before any choice list ships it. The tag of a row can differ by language,
    so each row lists its languages with one tag each.
  * Spelling variants are one form (rule 9). :func:`fold` maps a variant to
    its canonical form.
  * The name question offers "just my name" first (rule 5). It offers no
    kinship form by default (rule 7), and a neutral form before a marital one
    (rule 8). :func:`offered_forms` is the one function that decides this.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# Where the form goes with the name.
PREFIX = "prefix"  # before the name: "Shri Ramesh"
SUFFIX = "suffix"  # after the name, spaced: "Ramesh garu"
ATTACHED = "attached"  # joined to the name: "Vijaybhai"
BARE = "bare"  # no form: the name alone, or its initials

# What a prefix takes after it.
GIVEN_NAME = "given_name"  # "Shri Ramesh": the title goes with the given name
SURNAME = "surname"  # "Dr. Mehta": the title goes with the surname

# What the form is.
TITLE = "title"
POLITE = "polite_suffix"
KINSHIP = "kinship"
CREDENTIAL = "credential"
NAME_ONLY_KIND = "name"

# Who the form fits.
NEUTRAL = "neutral"
MALE = "male"
FEMALE = "female"

# What the form implies about the person.
MARRIED = "married"
UNMARRIED = "unmarried"

#: The language key of a row that fits each language.
ANY_LANGUAGE = "*"


@dataclass(frozen=True)
class Honorific:
    form: str  # the canonical spelling, for a choice list
    spellings: tuple[str, ...]  # lower case, no full stop, every match
    placement: str  # PREFIX, SUFFIX, ATTACHED or BARE
    follows: str  # GIVEN_NAME or SURNAME for a prefix, "" for the rest
    register: str
    tag: str  # "V", "K" or "U": the best tag of the row over its languages
    # True when a greeting keeps the title ("Dear Shri Ramesh,"). False for a
    # regulated or professional title, which the greeting drops ("Dear Priya,").
    greet_title: bool = True
    # True when the form is also a common given name (Kumari, Selvi, Anna).
    # Such a form is a title only when a name follows it, and a suffix reader
    # never keeps it.
    also_name: bool = False
    # PZ-40. The language and the tag of each one the form belongs to, as
    # ((language, tag), ...). The language is the first part of a BCP 47 tag.
    # ANY_LANGUAGE means each language.
    languages: tuple[tuple[str, str], ...] = ()
    kind: str = TITLE
    gender: str = NEUTRAL
    marital: str = ""  # MARRIED, UNMARRIED or "" (the form says nothing)
    # True for a form that no list may ever offer (spec 16.4.2, "Do not offer
    # these"). Such a row sits in NEVER_OFFERED, not in HONORIFICS.
    never: bool = False


def _langs(spec: str) -> tuple[tuple[str, str], ...]:
    """``"hi=V gu=K"`` as ``(("hi", "V"), ("gu", "K"))``."""
    return tuple((lang, tag) for lang, tag in (item.split("=") for item in spec.split()))


def _row(form, spellings, placement, follows, register, langs, **kw):
    pairs = _langs(langs)
    best = min((t for _, t in pairs), key="VKU".index)
    return Honorific(
        form, tuple(spellings), placement, follows, register, best, languages=pairs, **kw
    )


def _p(form, spellings, follows, register, langs, **kw):
    return _row(form, spellings, PREFIX, follows, register, langs, **kw)


def _s(form, spellings, register, langs, **kw):
    kw.setdefault("kind", POLITE)
    return _row(form, spellings, SUFFIX, "", register, langs, **kw)


def _a(form, spellings, register, langs, **kw):
    kw.setdefault("kind", KINSHIP)
    return _row(form, spellings, ATTACHED, "", register, langs, **kw)


def _kin(form, spellings, register, langs, **kw):
    return _row(form, spellings, SUFFIX, "", register, langs, kind=KINSHIP, **kw)


_G, _S = GIVEN_NAME, SURNAME
_ANY = ANY_LANGUAGE

# Native-script spellings (task 1894). Each one is a SPELLING of a row that the
# catalogue already has for that meaning (spec 16.4.1 rule 9), so the row keeps
# its form, placement and source tags. No spelling here adds a form. The tuples
# join the spellings of the row they name, below.
_SHRI_NATIVE = (
    "श्री",  # Devanagari
    "శ్రీ",  # Telugu
    "ಶ್ರೀ",  # Kannada
    "ശ്രീ",  # Malayalam
    "শ্রী",  # Bengali
    "શ્રી",  # Gujarati
    "ਸ੍ਰੀ", "ਸ਼੍ਰੀ",  # Gurmukhi, two spellings
    "ଶ୍ରୀ",  # Odia
)
_SHRIMATI_NATIVE = (
    "श्रीमती",  # Devanagari
    "శ్రీమతి",  # Telugu
    "ಶ್ರೀಮತಿ",  # Kannada
    "ശ്രീമതി",  # Malayalam
    "শ্রীমতী",  # Bengali
    "શ્રીમતી",  # Gujarati
    "ਸ੍ਰੀਮਤੀ",  # Gurmukhi
    "ଶ୍ରୀମତୀ",  # Odia
)

HONORIFICS: tuple[Honorific, ...] = (
    # --- Pan-Indian and English
    _s("sir", ("sir", "sar", "saar"), "formal, warm", f"{_ANY}=K"),
    _s("madam", ("madam", "mam", "ma'am"), "formal", f"{_ANY}=K"),
    _p("Mr.", ("mr",), _S, "formal", f"{_ANY}=K", gender=MALE),
    _p("Mrs.", ("mrs",), _S, "formal, married", f"{_ANY}=K", gender=FEMALE, marital=MARRIED),
    _p("Ms.", ("ms",), _S, "formal", f"{_ANY}=K", gender=FEMALE),
    _p("Mx.", ("mx",), _S, "formal", f"{_ANY}=K"),
    _p("Miss", ("miss",), _S, "formal, unmarried", f"{_ANY}=K", gender=FEMALE, marital=UNMARRIED),
    _p("Dr.", ("dr", "डॉ"), _S, "formal", f"{_ANY}=K", kind=CREDENTIAL),
    # --- Hindi belt (Punjabi and Gujarati share ji)
    _s("ji", ("ji", "jee", "जी"), "respectful, warm", "hi=K pa=K gu=K"),
    _p("Shri", ("shri", "sri", "shree", "sree", "shrii", "shiri", *_SHRI_NATIVE), _G, "formal, male",
       "hi=V bn=V gu=K mr=K or=K as=K te=K kn=K ml=K", gender=MALE),
    _p("Shrimati", ("shrimati", "smt", "srimati", "srimathi", "shrimathi",
                  *_SHRIMATI_NATIVE), _G,
       "formal, female, married", "hi=V bn=V gu=K or=K as=K te=K kn=K ml=K",
       gender=FEMALE, marital=MARRIED),
    _p("Sushri", ("sushri", "सुश्री"), _G, "formal, female, any status", "hi=V", gender=FEMALE),
    _p("Kumari", ("kumari", "kum", "कुमारी"), _G, "formal, female, unmarried", "hi=K mr=K kn=K ml=K",
       also_name=True, gender=FEMALE, marital=UNMARRIED),
    _s("Sahab", ("sahab", "sahib", "saheb", "saab"), "respectful, old style",
       "hi=V gu=V mr=V ur=V"),
    _kin("Bhaisahab", ("bhaisahab",), "respectful, kinship", "hi=K", gender=MALE),
    _kin("Bhai", ("bhai", "bhaiya"), "kinship", "hi=K or=U as=U", gender=MALE),
    _kin("Didi", ("didi", "behenji"), "kinship", "hi=K", gender=FEMALE),
    _kin("Dada", ("dada",), "kinship, ambiguous", "hi=K mr=K", gender=MALE),
    _p("Pandit", ("pandit", "pt", "पंडित", "पण्डित"), _G, "title, linked to a community", "hi=V", gender=MALE),
    # --- Punjabi and Sikh
    _p("Sardar", ("sardar",), _G, "respectful, male", "pa=V", gender=MALE),
    _p("Giani", ("giani",), _G, "title, a religious or scholarly role", "pa=V"),
    _kin("Bhaji", ("bhaji",), "kinship", "pa=K", gender=MALE),
    # --- Gujarati and Marathi
    _a("-bhai", ("bhai",), "respectful, warm, male", "gu=K", kind=POLITE, gender=MALE),
    _a("-ben", ("ben",), "respectful, warm, female", "gu=K", kind=POLITE, gender=FEMALE),
    _p("Sau.", ("sau",), _G, "formal, female, married", "mr=K", gender=FEMALE, marital=MARRIED),
    _a("-rao", ("rao",), "traditional, male", "mr=K", kind=POLITE, gender=MALE),
    _kin("Tai", ("tai",), "kinship, elder sister", "mr=K", also_name=True, gender=FEMALE),
    _kin("Bhau", ("bhau",), "kinship, elder brother", "mr=K", gender=MALE),
    # --- Bengali, Odia, Assamese, northeast
    _a("-da", ("da",), "kinship, elder brother", "bn=K", gender=MALE),
    _a("-di", ("di",), "kinship, elder sister", "bn=K", gender=FEMALE),
    _a("-babu", ("babu",), "respectful, old style", "bn=K", kind=POLITE, gender=MALE),
    _s("Moshai", ("moshai", "mashai"), "formal, rare in business", "bn=K"),
    _kin("Bhaina", ("bhaina",), "kinship", "or=U"),
    _kin("Apa", ("apa",), "kinship", "or=U ur=K", gender=FEMALE),
    _kin("Bhauni", ("bhauni",), "kinship", "or=U"),
    _kin("Baideo", ("baideo",), "kinship", "as=U"),
    _p("Pu", ("pu",), _G, "formal, respectful (Mizo)", "lus=K", also_name=True),
    _p("Pi", ("pi",), _G, "formal, respectful (Mizo)", "lus=K", also_name=True),
    _s("Sap", ("sap",), "respectful, from sahib (Mizo)", "lus=V"),
    _p("Kong", ("kong",), _G, "respectful (Khasi)", "kha=K", also_name=True),
    _p("Bah", ("bah",), _G, "respectful (Khasi)", "kha=K", also_name=True),
    # --- Telugu, Tamil, Kannada, Malayalam
    _s("garu", ("garu", "gaaru"), "respectful to formal, any gender", "te=K"),
    _kin("Anna", ("anna",), "kinship", "te=K ta=K kn=K mr=K", also_name=True, gender=MALE),
    _kin("Akka", ("akka",), "kinship", "te=K ta=K kn=K", gender=FEMALE),
    _p("Thiru", ("thiru", "tiru", "திரு"), _G, "formal, male", "ta=V", gender=MALE),
    _p("Thirumathi", ("thirumathi", "tmt", "திருமதி"), _G, "formal, female, married", "ta=V",
       gender=FEMALE, marital=MARRIED),
    _p("Selvi", ("selvi", "செல்வி"), _G, "formal, female, unmarried", "ta=K", also_name=True,
       gender=FEMALE, marital=UNMARRIED),
    _p("Selvan", ("selvan",), _G, "young men, on invitations", "ta=U", also_name=True,
       gender=MALE),
    _s("avargal", ("avargal", "avarkal", "avl"), "very formal, rare in chat", "ta=K ml=U"),
    _s("avare", ("avare", "avaru"), "respectful, formal or spoken", "kn=K"),
    _kin("Chettan", ("chettan",), "kinship, elder brother", "ml=K", gender=MALE),
    _kin("Chechi", ("chechi",), "kinship, elder sister", "ml=K", gender=FEMALE),
    # --- Urdu and Muslim usage
    _p("Janab", ("janab", "جناب"), _G, "formal, male", "ur=K", gender=MALE),
    _p("Mohtaram", ("mohtaram", "mohtarma", "محترم"), _G, "formal", "ur=K"),
    _s("Sahiba", ("sahiba",), "respectful", "ur=V", gender=FEMALE),
    _s("Begum", ("begum", "بیگم"), "traditional, female, married", "ur=V", gender=FEMALE,
       marital=MARRIED),
    _p("Haji", ("haji",), _G, "religious title", "ur=K"),
    _p("Hafiz", ("hafiz",), _G, "religious title", "ur=K"),
    _p("Maulana", ("maulana",), _G, "religious title", "ur=K"),
    _p("Maulvi", ("maulvi",), _G, "religious title", "ur=K"),
    _p("Qari", ("qari",), _G, "religious title", "ur=K"),
    _p("Mufti", ("mufti",), _G, "religious title", "ur=K"),
    _kin("Bhai jaan", ("bhaijaan",), "kinship", "ur=K", gender=MALE),
    _kin("Baji", ("baji",), "kinship, elder sister", "ur=K", gender=FEMALE),
    # --- Professional prefixes and suffixes (all regions)
    _p("Er.", ("er",), _G, "title", f"{_ANY}=K", kind=CREDENTIAL),
    _p("Adv.", ("adv",), _G, "title", f"{_ANY}=K", greet_title=False, kind=CREDENTIAL),
    _p("CA", ("ca",), _G, "regulated title, also after the name", f"{_ANY}=K",
       greet_title=False, kind=CREDENTIAL),
    _p("CS", ("cs",), _G, "regulated title, also after the name", f"{_ANY}=K",
       greet_title=False, kind=CREDENTIAL),
    _p("Prof.", ("prof",), _S, "title", f"{_ANY}=K", kind=CREDENTIAL),
    _p("Col.", ("col",), _S, "rank", f"{_ANY}=K", kind=CREDENTIAL),
    _p("Maj.", ("maj",), _S, "rank", f"{_ANY}=K", kind=CREDENTIAL),
    _p("Capt.", ("capt",), _S, "rank", f"{_ANY}=K", kind=CREDENTIAL),
    _p("Lt.", ("lt",), _S, "rank", f"{_ANY}=K", kind=CREDENTIAL),
    _p("Gen.", ("gen",), _S, "rank", f"{_ANY}=K", kind=CREDENTIAL),
    _p("Cdr.", ("cdr",), _S, "rank", f"{_ANY}=K", kind=CREDENTIAL),
    _p("Hon'ble", ("hon'ble", "honble"), _S, "judges and ministers", f"{_ANY}=K"),
    _s("IAS", ("ias",), "civil service", f"{_ANY}=K", kind=CREDENTIAL),
    _s("IPS", ("ips",), "civil service", f"{_ANY}=K", kind=CREDENTIAL),
    _s("IFS", ("ifs",), "civil service", f"{_ANY}=K", kind=CREDENTIAL),
)

#: "Do not offer these" (spec 16.4.2). A reader never offers one and never
#: reads one as a title. They stay out of HONORIFICS on purpose: a clan name
#: such as Thakur is a surname, and the drafter must not strip it.
NEVER_OFFERED: tuple[Honorific, ...] = tuple(
    Honorific(form, tuple(spellings), SUFFIX, "", why, "K", kind=POLITE, never=True)
    for form, spellings, why in (
        ("Memsahib", ("memsahib",), "colonial"),
        ("Uncle", ("uncle",), "carries an age"),
        ("Aunty", ("aunty", "auntie"), "carries an age"),
        ("Sethji", ("sethji",), "links to wealth and a community"),
        ("Babuji", ("babuji",), "sounds like a clerk"),
        ("Thakur", ("thakur",), "a clan name"),
        ("Chaudhary", ("chaudhary", "chaudhry"), "a clan name"),
        ("Thambi", ("thambi",), "addresses a younger person"),
        ("Thangachi", ("thangachi",), "addresses a younger person"),
        ("Waheguru ji ka Khalsa", ("waheguru ji ka khalsa",), "a religious greeting"),
        ("Achayan", ("achayan",), "Christian usage, never inferred"),
    )
)

#: The first choice of the name question. It is no form: the name alone.
NAME_ONLY = Honorific(
    "just my name", ("name only",), BARE, "", "casual, the safe default", "K",
    languages=((ANY_LANGUAGE, "K"),), kind=NAME_ONLY_KIND,
)
#: The initials ("VJ"). The catalogue holds the row. The list does not offer it.
INITIALS = Honorific(
    "initials", ("initials",), BARE, "", "casual", "K",
    languages=((ANY_LANGUAGE, "K"),), kind=NAME_ONLY_KIND,
)

def normalize(token: str) -> str:
    """NFC form, lower case, no full stop at the end, a plain apostrophe.

    NFC first, so a spelling in an Indian or Arabic script compares the same
    however a keyboard typed it. ``lower()`` changes Latin letters only."""
    text = unicodedata.normalize("NFC", token or "")
    return text.strip().replace(chr(0x2019), "'").rstrip(".").lower()


_PREFIX_INDEX: dict[str, Honorific] = {
    normalize(s): h for h in HONORIFICS if h.placement == PREFIX for s in h.spellings
}
_SUFFIX_INDEX: dict[str, Honorific] = {
    normalize(s): h for h in HONORIFICS if h.placement == SUFFIX for s in h.spellings
}
#: Every spelling of every row, any placement. The first row of a spelling wins.
_FOLD_INDEX: dict[str, str] = {}
for _row_ in (*HONORIFICS, *NEVER_OFFERED, NAME_ONLY, INITIALS):
    for _spelling in _row_.spellings:
        _FOLD_INDEX.setdefault(normalize(_spelling), _row_.form)


#: Canonical forms of a spoken suffix that a drafter skips when the form LEADS
#: a name ("Sir Ramesh" gives "Ramesh"). These are polite or credential forms
#: that are rarely a given name (spec 16.4.3, task 1894). A kinship form is not
#: here, because Bhau, Baji, Dada and Didi are real given names. Sahiba and
#: Begum are not here either. The key is the canonical form of the row.
LEADING_SKIPPED_SUFFIX_FORMS: frozenset[str] = frozenset({
    "sir", "madam", "Sahab", "ji", "garu", "Bhaisahab", "Moshai",
    "avargal", "avare", "IAS", "IPS", "IFS",
})


def find_prefix(token: str) -> Honorific | None:
    """The prefix title that this token spells, or None."""
    return _PREFIX_INDEX.get(normalize(token))


def find_suffix(token: str) -> Honorific | None:
    """The spoken suffix (ji, garu, sir, ...) that this token spells, or None."""
    return _SUFFIX_INDEX.get(normalize(token))


def fold(token: str) -> str:
    """The canonical form of a spelling variant, or ``""`` for an unknown word.

    Rule 9: "garu" and "gaaru" are one form, so are "ji" and "jee". A leading
    hyphen does not matter ("-bhai" and "bhai")."""
    return _FOLD_INDEX.get(normalize(token).lstrip("-"), "")


# --- What the name question offers (spec 16.4.1 rules 2 to 8, 16.9) --------

#: The languages whose rows need a native-speaker check before a `K` row ships
#: (spec 16.9 question 2). Only a `V` row of these ships without one. Konkani
#: shares the Marathi rows, so it is not a key here.
NATIVE_CHECK_LANGUAGES: frozenset[str] = frozenset(
    {"te", "ta", "kn", "ml", "bn", "gu", "mr", "or", "as", "lus", "kha"}
)
#: Mizo and Khasi hold back even a `V` row (spec 16.4.2: the research file has
#: the least confidence there, and these rows need a native-speaker check
#: before they ship).
HELD_LANGUAGES: frozenset[str] = frozenset({"lus", "kha"})
#: The `K` rows that the owner confirmed in the PZ-40 preview, as
#: ``(form, language)``. A row that fits each language uses ANY_LANGUAGE as
#: its language. A `K` row ships only when its pair is here and the member's
#: language is not in :data:`NATIVE_CHECK_LANGUAGES` (spec 16.9 question 1).
#: The owner's answer on task 1824, 2026-11-07 18:28 UTC: the English forms,
#: the work and rank titles, Hindi "ji" and "Kumari", Punjabi "ji", and Urdu
#: "Janab" and "Mohtaram". NOT confirmed: the Urdu religious titles (Haji,
#: Hafiz, Maulana, Maulvi, Qari, Mufti). A new pair needs a new owner answer.
_CONFIRMED_ANY = (
    # English forms.
    "sir", "madam", "Mr.", "Mrs.", "Ms.", "Mx.", "Miss", "Dr.",
    # Work and rank titles.
    "Er.", "Adv.", "CA", "CS", "Prof.", "Col.", "Maj.", "Capt.", "Lt.", "Gen.",
    "Cdr.", "Hon'ble", "IAS", "IPS", "IFS",
)
OWNER_CONFIRMED_K: frozenset[tuple[str, str]] = frozenset(
    {(form, ANY_LANGUAGE) for form in _CONFIRMED_ANY}
    | {("ji", "hi"), ("Kumari", "hi"), ("ji", "pa"), ("Janab", "ur"), ("Mohtaram", "ur")}
)

#: Tags of the catalogue that stand for a language group. The key is the first
#: part of a BCP 47 tag. No code guesses a language: the caller passes the one
#: that the profile or the member gave.
_LANGUAGE_GROUP: dict[str, str] = {
    "hi": "hi", "bho": "hi", "raj": "hi", "bgc": "hi", "mai": "hi",
    "mr": "mr", "kok": "mr",
}

_PLACE_TEXT = {
    PREFIX: "before the name",
    SUFFIX: "after the name",
    ATTACHED: "joined to the name",
}


def language_group(language: str | None) -> str:
    """The language of the catalogue for a BCP 47 tag ("hi-IN" gives "hi"),
    or ``""`` when no tag was given. Pure string work on the tag alone."""
    head = normalize(language or "").replace("_", "-").split("-")[0]
    return _LANGUAGE_GROUP.get(head, head)


@dataclass(frozen=True)
class Offer:
    """One choice of the name question."""

    form: str  # the canonical form, or NAME_ONLY.form
    placement: str  # PREFIX, SUFFIX, ATTACHED or BARE
    tag: str  # the tag of the row for the language of the question

    @property
    def text(self) -> str:
        where = _PLACE_TEXT.get(self.placement, "")
        return f"{self.form} ({where})" if where else self.form


def _may_ship(row: Honorific, lang: str, confirmed: frozenset[tuple[str, str]]) -> str:
    """The tag of *row* for *lang* when a choice list may offer it, else ``""``."""
    if row.never:
        return ""
    tags = dict(row.languages)
    key = lang if lang in tags else ANY_LANGUAGE
    # The checks of spec 16.9 concern the language of the ROW. A row for all
    # languages (English forms, work titles) is not a Mizo or Telugu row.
    own = key != ANY_LANGUAGE
    if own and lang in HELD_LANGUAGES:
        return ""
    tag = tags.get(key, "")
    if not tag or tag == "U":
        return ""
    if tag == "V":
        return tag
    # A `K` row: the owner confirmed it, and no native-speaker check is due.
    if (row.form, key) in confirmed and not (own and lang in NATIVE_CHECK_LANGUAGES):
        return tag
    return ""


def offered_forms(
    language: str | None = None,
    *,
    kinship: bool = False,
    confirmed: frozenset[tuple[str, str]] | None = None,
) -> tuple[Offer, ...]:
    """The choices of the name question for *language*, in the order to ask.

    "just my name" is always first (rule 5). The forms of the member's own
    language follow, then the forms for every language. In each group a
    neutral form comes before a gendered one, and a form that implies a
    marital status comes last (rule 8). A kinship form comes only when *kinship* is true,
    that is, when the member asked for it (rule 7). A `U` row, an unconfirmed
    `K` row and a held row never come (spec 16.9). A form that is not for the
    member's language never comes.

    This function takes NO name, region or mail. *language* is a BCP 47 tag
    that the profile or the member's own answer gave, or ``None``.
    *confirmed* defaults to :data:`OWNER_CONFIRMED_K`."""
    ok = OWNER_CONFIRMED_K if confirmed is None else confirmed
    lang = language_group(language)
    picked: list[tuple[int, int, Offer]] = []
    for n, row in enumerate(HONORIFICS):
        if row.kind == KINSHIP and not kinship:
            continue
        tag = _may_ship(row, lang, ok)
        if not tag:
            continue
        rank = 2 if row.marital else (1 if row.gender != NEUTRAL else 0)
        # The forms of the member's own language first, then the forms for
        # every language. Rule 8 orders each group.
        group = 0 if lang in dict(row.languages) else 1
        picked.append((group, rank, n, Offer(row.form, row.placement, tag)))
    picked.sort(key=lambda item: item[:3])
    return (Offer(NAME_ONLY.form, BARE, "K"), *(item[3] for item in picked))


def offer_line(language: str | None = None, **kw) -> str:
    """The sentence that tells the model which choices to offer for the name
    question, in order. Pure. It takes the same arguments as
    :func:`offered_forms`."""
    offers = offered_forms(language, **kw)
    if len(offers) == 1:
        return (
            "Offer no form of address. Use their name alone, unless they name a "
            "form themselves. Save that form exactly as they wrote it."
        )
    listed = "; ".join(f'"{o.text}"' for o in offers)
    return (
        f"Offer these choices for how to address them, in this order: {listed}. "
        "Offer no other form, and add none of your own. For the first choice, "
        "save the name alone. For any other, save the name with the form in "
        "its place, exactly as they chose it. Never pick one for them."
    )
