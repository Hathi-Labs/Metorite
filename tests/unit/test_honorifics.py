"""WS-63 PZ-40: the forms of address (personalization.md 16.4, 16.7, 16.9).

The catalogue is ``acb_common.honorifics``. The name question offers "just my
name" first, then the forms that fit the member's language. The block keeps a
chosen form as written. R7 fences named here:

* ``pz40-catalogue``: every row of 16.4.2 is present with its tag, variants
  fold to one form, and a title never parses as a first name.
* ``pz40-offered``: a ``U`` row, an unconfirmed ``K`` row, a kinship form and a
  "do not offer" form are never offered. "just my name" is first.
* ``pz40-no-inference``: the offered list takes no name, region or mail.
* ``pz40-block``: with the flag on, "Vijay garu" stays "Vijay garu".
* ``pz40-interview``: ``te-IN`` and ``hi-IN`` give their lists, name first.
* ``pz40-second-list``: no module outside the catalogue keeps a list of forms.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from acb_common import assistant_profile as ap
from acb_common import get_settings
from acb_common import honorifics as hon
from gateway.routes.email.automation import drafting as dr
from orchestrator import executor as ex

from tests.unit import test_executor_profile_block as epb
from tests.unit.test_executor_profile_block import run  # noqa: F401

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)


def _forms(language, **kw) -> list[str]:
    return [o.form for o in hon.offered_forms(language, **kw)]


# ── pz40-catalogue ─────────────────────────────────────────────────────────

#: Each row of 16.4.2 as (canonical form, language, tag). The tag is the one
#: the spec table gives for that language.
SPEC_ROWS = [
    ("sir", "*", "K"), ("madam", "*", "K"), ("Mr.", "*", "K"), ("Mrs.", "*", "K"),
    ("Ms.", "*", "K"), ("Miss", "*", "K"), ("Dr.", "*", "K"),
    ("ji", "hi", "K"), ("Shri", "hi", "V"), ("Shrimati", "hi", "V"), ("Sushri", "hi", "V"),
    ("Kumari", "hi", "K"), ("Sahab", "hi", "V"), ("Bhaisahab", "hi", "K"), ("Bhai", "hi", "K"),
    ("Didi", "hi", "K"), ("Dada", "hi", "K"), ("Pandit", "hi", "V"),
    ("ji", "pa", "K"), ("Sardar", "pa", "V"), ("Giani", "pa", "V"), ("Bhaji", "pa", "K"),
    ("-bhai", "gu", "K"), ("-ben", "gu", "K"), ("ji", "gu", "K"), ("Sahab", "gu", "V"),
    ("Shri", "gu", "K"), ("Shrimati", "gu", "K"),
    ("Shri", "mr", "K"), ("Sau.", "mr", "K"), ("Kumari", "mr", "K"), ("-rao", "mr", "K"),
    ("Sahab", "mr", "V"), ("Tai", "mr", "K"), ("Dada", "mr", "K"), ("Anna", "mr", "K"),
    ("Bhau", "mr", "K"),
    ("-da", "bn", "K"), ("-di", "bn", "K"), ("-babu", "bn", "K"), ("Moshai", "bn", "K"),
    ("Shri", "bn", "V"), ("Shrimati", "bn", "V"),
    ("Shri", "or", "K"), ("Shrimati", "or", "K"), ("Bhai", "or", "U"), ("Bhaina", "or", "U"),
    ("Apa", "or", "U"), ("Bhauni", "or", "U"), ("Shri", "as", "K"), ("Bhai", "as", "U"),
    ("Baideo", "as", "U"), ("Pu", "lus", "K"), ("Pi", "lus", "K"), ("Sap", "lus", "V"),
    ("Kong", "kha", "K"), ("Bah", "kha", "K"),
    ("garu", "te", "K"), ("Shri", "te", "K"), ("Shrimati", "te", "K"), ("Anna", "te", "K"),
    ("Akka", "te", "K"),
    ("Thiru", "ta", "V"), ("Thirumathi", "ta", "V"), ("Selvi", "ta", "K"),
    ("avargal", "ta", "K"), ("Anna", "ta", "K"), ("Akka", "ta", "K"), ("Selvan", "ta", "U"),
    ("avare", "kn", "K"), ("Shri", "kn", "K"), ("Shrimati", "kn", "K"), ("Kumari", "kn", "K"),
    ("Anna", "kn", "K"), ("Akka", "kn", "K"),
    ("Chettan", "ml", "K"), ("Chechi", "ml", "K"), ("Shri", "ml", "K"),
    ("Shrimati", "ml", "K"), ("Kumari", "ml", "K"), ("avargal", "ml", "U"),
    ("Janab", "ur", "K"), ("Mohtaram", "ur", "K"), ("Sahab", "ur", "V"), ("Sahiba", "ur", "V"),
    ("Begum", "ur", "V"), ("Haji", "ur", "K"), ("Hafiz", "ur", "K"), ("Maulana", "ur", "K"),
    ("Maulvi", "ur", "K"), ("Qari", "ur", "K"), ("Mufti", "ur", "K"), ("Bhai jaan", "ur", "K"),
    ("Apa", "ur", "K"), ("Baji", "ur", "K"),
    ("Er.", "*", "K"), ("Adv.", "*", "K"), ("CA", "*", "K"), ("CS", "*", "K"),
    ("Prof.", "*", "K"), ("Col.", "*", "K"), ("Maj.", "*", "K"), ("Capt.", "*", "K"),
    ("Lt.", "*", "K"), ("Gen.", "*", "K"), ("Cdr.", "*", "K"), ("IAS", "*", "K"),
    ("IPS", "*", "K"), ("IFS", "*", "K"), ("Hon'ble", "*", "K"),
]


@pytest.mark.parametrize(("form", "lang", "tag"), SPEC_ROWS)
def test_every_spec_row_is_in_the_catalogue_with_its_tag(form, lang, tag) -> None:
    rows = [h for h in hon.HONORIFICS if h.form == form]
    assert rows, form
    assert dict(rows[0].languages).get(lang) == tag, (form, lang)


def test_the_bare_forms_and_the_never_offered_forms_are_rows() -> None:
    assert hon.NAME_ONLY.placement == hon.BARE
    assert hon.INITIALS.placement == hon.BARE
    never = {h.form for h in hon.NEVER_OFFERED}
    assert never == {
        "Memsahib", "Uncle", "Aunty", "Sethji", "Babuji", "Thakur", "Chaudhary",
        "Thambi", "Thangachi", "Waheguru ji ka Khalsa", "Achayan"}
    assert all(h.never for h in hon.NEVER_OFFERED)
    assert not any(h.never for h in hon.HONORIFICS)


def test_every_row_is_typed() -> None:
    for h in hon.HONORIFICS:
        assert h.placement in {hon.PREFIX, hon.SUFFIX, hon.ATTACHED}, h.form
        assert h.kind in {hon.TITLE, hon.POLITE, hon.KINSHIP, hon.CREDENTIAL}, h.form
        assert h.gender in {hon.NEUTRAL, hon.MALE, hon.FEMALE}, h.form
        assert h.marital in {"", hon.MARRIED, hon.UNMARRIED}, h.form
        assert h.languages and all(t in "VKU" for _, t in h.languages), h.form
        assert h.tag == min((t for _, t in h.languages), key="VKU".index), h.form


@pytest.mark.parametrize(
    "group",
    [("garu", "gaaru", "Garu"), ("ji", "jee", "JI"), ("sahab", "sahib", "saheb", "saab"),
     ("avare", "avaru"), ("avargal", "avarkal", "avl", "Avl."),
     ("Shri", "Sri", "Shree", "Sree", "Shrii", "Shiri"), ("Smt.", "Shrimati", "Srimathi"),
     ("Kum.", "Kumari"), ("Tmt.", "Thirumathi"), ("sir", "sar", "saar"),
     ("-bhai", "bhai"), ("Behenji", "Didi")],
)
def test_variants_fold_to_one_form(group) -> None:
    folded = {hon.fold(word) for word in group}
    assert len(folded) == 1 and "" not in folded, group


def test_fold_gives_the_canonical_form() -> None:
    assert hon.fold("gaaru") == "garu"
    assert hon.fold("Jee") == "ji"
    assert hon.fold("Smt.") == "Shrimati"
    assert hon.fold("Memsahib") == "Memsahib"
    assert hon.fold("Vijay") == ""
    assert hon.fold("") == ""


@pytest.mark.parametrize(
    "h", [h for h in hon.HONORIFICS if h.placement == hon.PREFIX], ids=lambda h: h.form)
def test_a_title_never_parses_as_a_first_name(h) -> None:
    for spelling in h.spellings:
        got = dr._salutation_name(f"{spelling.capitalize()} Ramesh Kumar")
        assert got.split()[-1] != spelling.capitalize(), spelling
        assert hon.fold(got.split()[0]) in {h.form, ""} or not h.greet_title
    # A title alone is no name, unless the form is also a given name.
    for spelling in h.spellings:
        got = dr._salutation_name(spelling.capitalize())
        assert (got == spelling.capitalize()) == h.also_name


# ── pz40-offered ───────────────────────────────────────────────────────────


def test_just_my_name_is_first_for_every_language_and_none() -> None:
    for language in (None, "", "en", "hi-IN", "te-IN", "ta", "bn", "ur", "pa", "mr", "xx"):
        offers = hon.offered_forms(language)
        assert offers[0].form == hon.NAME_ONLY.form == "just my name"
        assert offers[0].placement == hon.BARE


def test_hindi_offers_its_v_rows_name_first() -> None:
    got = _forms("hi-IN")
    assert got[0] == "just my name"
    assert set(got[1:]) == {"Shri", "Shrimati", "Sushri", "Sahab", "Pandit"}
    # A neutral form comes before a gendered one, and a marital one is last.
    assert got.index("Sahab") < got.index("Shri") < got.index("Shrimati")
    assert got[-1] == "Shrimati"


def test_telugu_offers_the_name_only_until_the_owner_confirms_a_k_row() -> None:
    assert _forms("te-IN") == ["just my name"]
    assert "garu" not in _forms("te-IN", confirmed=frozenset({("garu", "te")}))
    # The 16.9 default: a K row of a language in the native-check set never ships.
    assert "te" in hon.NATIVE_CHECK_LANGUAGES


def test_a_confirmed_k_row_of_hindi_ships_and_a_u_row_never_does() -> None:
    ok = frozenset({("ji", "hi"), ("Didi", "hi"), ("Bhai", "or"), ("Selvan", "ta")})
    got = _forms("hi-IN", confirmed=ok)
    assert "ji" in got and got[0] == "just my name"
    assert "Didi" not in got  # kinship, not asked for
    assert "Didi" in _forms("hi-IN", kinship=True, confirmed=ok)
    assert "Bhai" not in _forms("or", kinship=True, confirmed=ok)  # U row
    assert "Selvan" not in _forms("ta", confirmed=ok)  # U row


def test_the_shipped_k_set_is_empty() -> None:
    assert not hon.OWNER_CONFIRMED_K


def test_no_kinship_form_is_offered_by_default() -> None:
    kin = {h.form for h in hon.HONORIFICS if h.kind == hon.KINSHIP}
    everything = frozenset((h.form, lang) for h in hon.HONORIFICS for lang, _ in h.languages)
    for language in ("hi", "pa", "ur", "te", "ta", "kn", "ml", "mr", "bn", "or", "as", "gu"):
        assert not kin & set(_forms(language, confirmed=everything)), language


def test_a_u_row_is_never_offered_even_when_confirmed() -> None:
    everything = frozenset((h.form, lang) for h in hon.HONORIFICS for lang, _ in h.languages)
    for language in ("or", "as", "ta", "ml", "hi", "ur", "pa", "bn"):
        offered = {o.form for o in hon.offered_forms(language, kinship=True, confirmed=everything)}
        for h in hon.HONORIFICS:
            if dict(h.languages).get(language) == "U":
                assert h.form not in offered, (language, h.form)


def test_a_k_row_of_the_eleven_languages_waits_for_a_native_check() -> None:
    everything = frozenset((h.form, lang) for h in hon.HONORIFICS for lang, _ in h.languages)
    for language in hon.NATIVE_CHECK_LANGUAGES - hon.HELD_LANGUAGES:
        for o in hon.offered_forms(language, kinship=True, confirmed=everything):
            if o.form != "just my name":
                assert o.tag == "V", (language, o.form)


def test_mizo_and_khasi_are_held() -> None:
    assert _forms("lus", confirmed=frozenset({("Sap", "lus")})) == ["just my name"]
    assert _forms("kha") == ["just my name"]


def test_konkani_and_the_hindi_belt_languages_use_their_group() -> None:
    assert _forms("kok") == _forms("mr")
    assert _forms("bho") == _forms("mai") == _forms("hi")


def test_the_never_offered_forms_are_never_offered() -> None:
    everything = frozenset((h.form, lang) for h in hon.HONORIFICS for lang, _ in h.languages)
    never = {h.form for h in hon.NEVER_OFFERED}
    for language in (None, "hi", "pa", "ta", "te", "ml", "ur"):
        assert not never & set(_forms(language, kinship=True, confirmed=everything))


def test_a_marital_form_comes_after_a_neutral_one() -> None:
    everything = frozenset((h.form, "*") for h in hon.HONORIFICS)
    got = _forms("en", confirmed=everything)
    assert got.index("sir") < got.index("Mr.") < got.index("Mrs.")
    assert got.index("Ms.") < got.index("Mrs.")


# ── pz40-no-inference ──────────────────────────────────────────────────────


def test_the_offered_list_takes_no_name_region_or_mail() -> None:
    params = set(inspect.signature(hon.offered_forms).parameters)
    assert params == {"language", "kinship", "confirmed"}
    assert set(inspect.signature(hon.offer_line).parameters) == {"language", "kw"}
    # The same language gives the same list, whatever surrounds it.
    assert hon.offered_forms("hi-IN") == hon.offered_forms("HI_in") == hon.offered_forms("hi")


def test_offer_line_lists_the_choices_in_order_and_picks_none() -> None:
    line = hon.offer_line("hi-IN")
    assert line.index('"just my name"') < line.index("Shri")
    assert "Never pick one for them" in line
    assert "garu" not in line
    only = hon.offer_line("te-IN")
    assert "Offer no form of address" in only


# ── pz40-block ─────────────────────────────────────────────────────────────


def _raw(name: str) -> dict:
    return {"v": 1, "address": {"name": name}}


def test_a_chosen_form_is_kept_as_written_with_the_flag_on() -> None:
    block = ap.render_block(_raw("Vijay garu"), forms=True)
    assert 'Address them as "Vijay garu".' in block
    assert ap.AS_WRITTEN_LINE in block
    assert "Never add, drop, respell or move a form." in block


def test_the_flag_off_block_is_as_on_main() -> None:
    block = ap.render_block(_raw("Vijay garu"))
    assert 'Address them as "Vijay garu".' in block
    assert ap.AS_WRITTEN_LINE not in block
    assert ap.render_block(_raw("Vijay garu")) == ap.render_block(_raw("Vijay garu"), forms=False)


def test_no_choice_means_the_name_only() -> None:
    # A directory name is a name, not a form (rule 4): the line stays the directory line.
    block = ap.render_block({"v": 1, "style": {"length": "short"}}, "Vijay", forms=True)
    assert 'Their directory name is "Vijay".' in block
    assert ap.AS_WRITTEN_LINE not in block
    assert "ji" not in block.split("Vijay")[1].split("\n")[0].split()


def test_member_block_reads_the_flag_by_org(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "assistant_profile_enabled", True, raising=False)
    monkeypatch.setattr(get_settings(), "address_forms_orgs", epb.ORG, raising=False)
    on = ap.member_block(_raw("Vijay garu"), None, None, epb.ORG)
    off = ap.member_block(_raw("Vijay garu"), None, None, "22222222-2222-4222-8222-222222222222")
    none = ap.member_block(_raw("Vijay garu"), None, None)
    assert ap.AS_WRITTEN_LINE in on
    assert ap.AS_WRITTEN_LINE not in off and ap.AS_WRITTEN_LINE not in none


def test_the_flag_is_off_by_default_and_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "assistant_profile_enabled", True, raising=False)
    assert get_settings().address_forms_orgs == ""
    assert ap.address_forms_for(epb.ORG) is False
    monkeypatch.setattr(get_settings(), "address_forms_orgs", "*", raising=False)
    assert ap.address_forms_for(epb.ORG) is True
    assert ap.address_forms_for(None) is False
    monkeypatch.setattr(get_settings(), "assistant_profile_enabled", False, raising=False)
    assert ap.address_forms_for(epb.ORG) is False


# ── pz40-interview ─────────────────────────────────────────────────────────


def _iv() -> dict:
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return {"started_at": (NOW - timedelta(days=40)).strftime(fmt),
            "last_asked_at": (NOW - timedelta(hours=30)).strftime(fmt)}


@pytest.fixture()
def interview(run, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ex, "_interview_now", lambda: NOW)
    ex._INTERVIEW_SHOWN.clear()
    token = ap.bind_writes(True)
    monkeypatch.setattr(get_settings(), "address_forms_orgs", epb.ORG, raising=False)

    def entry(language: str) -> str:
        raw = {**epb.EXAMPLE, "interview": _iv(),
               "style": {**epb.EXAMPLE["style"], "language": language}}
        run["raw"] = raw
        ex._PROFILE_CACHE.clear()
        ex._INTERVIEW_SHOWN.clear()
        return ex._member_profile_entry("orchestrator")[0]

    try:
        yield entry
    finally:
        ap.release_writes(token)
        ex._INTERVIEW_SHOWN.clear()


def _interview_line(block: str) -> str:
    return block.splitlines()[-1]


def test_the_name_question_offers_the_hindi_forms_with_just_my_name_first(interview) -> None:
    line = _interview_line(interview("hi-IN"))
    assert line.startswith("Interview (one question is due): name: ")
    offer = line[line.index("Offer these choices"):]
    assert offer.index('"just my name"') < offer.index('"Sahab') < offer.index('"Shrimati')
    assert '"Shri (before the name)"' in offer
    assert "garu" not in offer


def test_the_name_question_offers_the_name_only_for_telugu(interview) -> None:
    line = _interview_line(interview("te-IN"))
    assert "Offer no form of address" in line
    assert "garu" not in line.split("Example")[1].split("Ask it only")[0]


def test_the_flag_off_leaves_the_interview_line_as_on_main(run, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(ex, "_interview_now", lambda: NOW)
    ex._INTERVIEW_SHOWN.clear()
    token = ap.bind_writes(True)
    try:
        run["raw"] = {**epb.EXAMPLE, "interview": _iv()}
        line = _interview_line(ex._member_profile_entry("orchestrator")[0])
    finally:
        ap.release_writes(token)
        ex._INTERVIEW_SHOWN.clear()
    assert "Offer" not in line
    assert line.startswith("Interview (one question is due): name: ")


def test_only_the_name_area_carries_the_offer() -> None:
    iv = {**_iv(), "areas": {"name": {"state": "answered", "at": "2026-10-20T10:00:00Z"},
                             "language": {"state": "answered", "at": "2026-10-20T10:00:00Z"}}}
    offer = hon.offer_line("hi")
    line = ap.interview_due_line(iv, NOW, offer)
    assert line.startswith("Interview (one question is due): role: ")
    assert offer not in line


# ── pz40-second-list ───────────────────────────────────────────────────────

_FORMS_ALL = {"garu", "ji"}


def second_list_files(sources: dict[str, str]) -> list[str]:
    """Files whose string literals hold both ``garu`` and ``ji`` (a list of
    forms). Docstrings and comments are not literals that code reads."""
    hits: list[str] = []
    for name, src in sources.items():
        found: set[str] = set()
        tree = ast.parse(src)
        docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr)}
        for node in ast.walk(tree):
            if id(node) in docs:
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                words = {w.strip(".,;:\"'()[]").lower() for w in node.value.replace("|", " ").split()}
                found |= words & _FORMS_ALL
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                items = {e.value.lower() for e in node.elts
                         if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                found |= items & _FORMS_ALL
        if found == _FORMS_ALL:
            hits.append(name)
    return hits


def test_the_fence_fails_on_a_second_list_of_forms() -> None:
    assert second_list_files({"x.py": 'FORMS = ("garu", "ji", "sir")\n'}) == ["x.py"]
    assert second_list_files({"x.py": 'import re\nX = re.compile(r"garu|ji")\n'}) == ["x.py"]
    assert second_list_files({"x.py": 'X = "ji"\nY = "ok"\n'}) == []


def test_no_module_outside_the_catalogue_keeps_a_list_of_forms() -> None:
    sources: dict[str, str] = {}
    for root in ("packages", "apps"):
        for path in (REPO / root).rglob("*.py"):
            if path.name == "honorifics.py" or "node_modules" in path.parts:
                continue
            sources[str(path.relative_to(REPO))] = path.read_text(encoding="utf-8-sig")
    assert second_list_files(sources) == []
