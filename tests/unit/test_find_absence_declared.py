"""FIND-4b (task 3674, ``find_engine.md`` §3.6): the claim reads each
declaration of ``Source.proves_absence``.

A source declares ``proves_absence`` only when its search reads every field
that its hits print, and every field that holds a word that a member can type.
``find.proves_absence`` needs ``True`` from each app that answered, beside its
three parts (``find.declares_all``). The route builds the map from
``sources.REGISTRY`` (``company._declared``). No source declares it in this
slice, and ``find.ABSENCE_CLAIMS`` stays off.

Hermetic, through the route function ``company.company_search`` (the handler
of ``GET /search/company``), over the fake readers of
``test_find_honest_zero.py``. The slice adds no SQL, so no R8 twin exists.

The claims, each written so that a wrong build fails it:

* **(a) No source declares.** With the switch on, no answer claims. No line
  says "No record holds all of the words", "No record has all the words" or
  "No record holds «X»". The log line keeps its ``miss``.
* **(b) Each source declares.** With the switch on, each of the three claims
  prints as before. This is the positive control.
* **(c) One app that answered does not declare.** No claim, for each such app
  in turn, the past chats too. An app that was not asked, or whose switch is
  closed, does not stop the claim.
* **(d) The switch off.** No claim, with or without the declarations. The
  answer and the tool text are the same in both, and they are the text of
  FIND-4.
* **The fence.** No source of the real ``sources.REGISTRY`` declares it yet,
  and the default is ``False``.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from acb_common import get_settings
from acb_skills import company_search_tools as cst
from gateway.retrieval import company, find, sources

# The fixtures of FIND-4, asked for by name with `usefixtures`.
from tests.unit.test_company_search_recency import recency  # noqa: F401
from tests.unit.test_company_search_sources import ALL, member
from tests.unit.test_find_honest_zero import (  # noqa: F401
    MARKER,
    _log,
    claims_on,
    declares,
    find_on,
    on,
    run,
    world,
)

#: A zero answer that, with proof, opens with "No record holds all of the
#: words" (``cst.NO_RECORD_HOLDS_ALL``).
ZERO = f"{MARKER} Quasar"
#: A miss with hits that, with proof, opens with ``cst.MOST_WORDS``.
MOST = f"Snowflake {MARKER}"
#: A near spelling whose word, with proof, is ``held`` false: "No record holds
#: «Nuropeak»" (``cst.NEAR_HELD_OPENING``).
NEAR = "Nuropeak snowflake"
#: The apps that answer for ``member(*ALL)``: each source but Studio and the
#: past chats, which that member does not hold.
ANSWERED = ("tasks", "email", "people", "crm", "artifacts", "whatsapp", "knowledge")

#: The switch on, and each source of the fakes declares (the order matters:
#: ``declares`` marks the entries that ``on`` put in).
CLAIMING = ("on", "claims_on", "declares")


def _said(body: dict[str, Any]) -> set[str]:
    """Each claim that *body* makes, in the route and in the tool text."""
    text = cst.format_answer(body, dated=True)
    out = set()
    if body.get("none_has_all") is True or cst.NO_RECORD_HOLDS_ALL in text:
        out.add("all")
    if cst.MOST_WORDS in text:
        out.add("most")
    if (any(isinstance(e, dict) and e.get("held") is False for e in body.get("near") or [])
            or cst.NEAR_HELD_OPENING in text):
        out.add("near")
    return out


def _declare(monkeypatch, key: str, value: bool) -> None:
    """Set the declaration of the source *key*, on its current entry."""
    monkeypatch.setitem(sources.REGISTRY, key, dataclasses.replace(
        sources.REGISTRY[key], proves_absence=value))


def _ladder(lines: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """The fields of the one ladder line that hold no time."""
    (fields,) = [f for e, f in lines if e == "search.company.ladder"]
    return {k: v for k, v in fields.items() if not k.endswith("_ms")}


# ── The rule (``find.declares_all``) ────────────────────────────────────────

class TestTheRule:
    def test_each_app_that_answered_must_declare(self):
        assert find.declares_all({"crm": True})
        assert find.declares_all({"crm": True, "email": True})
        for declared in (None, {}, {"crm": False}, {"crm": True, "email": False},
                         {"crm": 1}, {"crm": "yes"}):
            assert not find.declares_all(declared), declared

    @pytest.mark.usefixtures("claims_on")
    def test_the_one_rule_reads_the_declarations(self):
        def proves(declared: Any) -> bool:
            return find.proves_absence("zqvortex", query=ZERO, shown=[],
                                       looked={"zqvortex": True}, declared=declared)

        assert proves({"crm": True, "email": True})
        assert not proves({"crm": True, "email": False})
        assert not proves({})
        assert not proves(None)

    @pytest.mark.usefixtures("claims_on")
    def test_both_proofs_read_the_declarations(self):
        """``held`` false and ``none_has_all`` read the one rule."""
        zero = [{"word": "zqvortex", "hits": 0, "skipped": []}]

        def absent(declared: Any) -> bool:
            return find.word_absent(zero, query=ZERO, skipped=[], limited=False, shown=[],
                                    looked={"zqvortex": True}, declared=declared)

        def held(declared: Any) -> Any:
            (entry,) = find.near_report(
                [{"word": "Zqvortex", "as": "Zqvortez", "hits": 1}], {}, zero, query=ZERO,
                first_zero=True, skipped=[], looked={"zqvortex": True}, declared=declared)
            return entry["held"]

        assert absent({"crm": True}) and held({"crm": True}) is False
        assert not absent({"crm": False}) and held({"crm": False}) is None
        # A caller that sends no map proves nothing.
        assert held(None) is None

    @pytest.mark.usefixtures("world")
    def test_the_map_of_the_route(self, monkeypatch):
        """``company._declared``: each app that answered and is open to the
        member, the past chats when they answered. A key off the registry
        declares nothing."""
        user = member(*ALL)
        assert company._declared(user, list(ANSWERED), chat_answered=False) == dict.fromkeys(
            ANSWERED, False)
        _declare(monkeypatch, "crm", True)
        assert company._declared(user, ["crm"], chat_answered=False) == {"crm": True}
        assert company._declared(user, ["crm", "nope"], chat_answered=False) == {
            "crm": True, "nope": False}
        chatter = member(*ALL, "chat")
        assert company._declared(chatter, ["crm"], chat_answered=True) == {
            "crm": True, "chat": False}
        # The real Studio source is dark: its switch is closed, so it is out.
        studio = member(*ALL, "studio")
        assert not sources.REGISTRY["studio"].reaches(studio)
        assert company._declared(studio, ["crm", "studio"], chat_answered=False) == {
            "crm": True}
        assert company._declared(user, [], chat_answered=False) == {}


# ── (a) No source declares ──────────────────────────────────────────────────

class TestNoSourceDeclares:
    @pytest.mark.usefixtures("on", "claims_on")
    def test_no_claim_and_the_same_log_line(self, monkeypatch):
        """The switch on, and each source as the product ships it."""
        for q in (ZERO, MOST, NEAR):
            lines = _log(monkeypatch)
            body = run(q)
            assert _said(body) == set(), q
            assert body["none_has_all"] is False, q
            text = cst.format_answer(body, dated=True)
            for claim in ("No record holds", "No record has"):
                assert claim not in text, (q, claim)
            undeclared = _ladder(lines)
            # The same search with each source declared claims, and its log
            # line holds the same counts and the same miss.
            with monkeypatch.context() as m:
                for key in list(sources.REGISTRY):
                    _declare(m, key, True)
                lines = _log(monkeypatch)
                assert _said(run(q)), q
                assert _ladder(lines) == undeclared, q


# ── (b) Each source declares ────────────────────────────────────────────────

class TestEachSourceDeclares:
    @pytest.mark.usefixtures(*CLAIMING)
    def test_each_claim_prints_as_before(self):
        zero = run(ZERO)
        assert _said(zero) == {"all"}
        assert cst.format_answer(zero, dated=True).splitlines()[1] == (
            f"{cst.BROADENED_OPENING} zqvortex (0), quasar (0).")
        most = run(MOST)
        assert most["hits"] and _said(most) == {"all", "most"}
        assert cst.format_answer(most, dated=True).splitlines()[1] == cst.MOST_WORDS
        near = run(NEAR)
        assert _said(near) == {"near"}
        assert cst.format_answer(near, dated=True).splitlines()[1] == (
            "No record holds «Nuropeak». Records for the near spelling «Neuropeak»:")


# ── (c) One app that answered does not declare ──────────────────────────────

class TestOneAppDoesNotDeclare:
    @pytest.mark.usefixtures(*CLAIMING)
    @pytest.mark.parametrize("key", ANSWERED)
    def test_one_app_stops_each_claim(self, monkeypatch, key):
        assert sorted(run(ZERO)["searched"]) == sorted(ANSWERED)
        _declare(monkeypatch, key, False)
        for q in (ZERO, MOST, NEAR):
            assert _said(run(q)) == set(), (key, q)

    @pytest.mark.usefixtures(*CLAIMING)
    def test_the_past_chats_must_declare_when_they_answered(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "chat_search_orgs", "*", raising=False)
        user = member(*ALL, "chat")
        body = run(ZERO, user=user, current_chat="t-here")
        assert "chat" in body["searched"] and _said(body) == {"all"}
        _declare(monkeypatch, "chat", False)
        assert _said(run(ZERO, user=user, current_chat="t-here")) == set()
        # With no current chat the source is not asked, so it stops nothing.
        assert _said(run(ZERO, user=user)) == {"all"}

    @pytest.mark.usefixtures(*CLAIMING)
    def test_an_app_that_was_not_asked_stops_nothing(self, monkeypatch):
        _declare(monkeypatch, "whatsapp", False)
        user = member(*(f for f in ALL if f != "whatsapp"))
        body = run(ZERO, user=user)
        assert "whatsapp" not in body["searched"] and _said(body) == {"all"}
        assert _said(run(MOST, user=user)) == {"all", "most"}

    @pytest.mark.usefixtures(*CLAIMING)
    def test_a_dark_studio_stops_nothing(self, monkeypatch):
        """The real Studio source is dark. A member who holds
        ``feature:studio`` gets the answer of one who does not."""
        _declare(monkeypatch, "studio", False)
        with_studio = run(ZERO, user=member(*ALL, "studio"))
        assert "studio" not in with_studio["searched"]
        assert _said(with_studio) == {"all"}
        assert json.dumps(with_studio, sort_keys=True) == json.dumps(run(ZERO), sort_keys=True)


# ── (d) The switch off: the text of FIND-4, byte for byte ──────────────────

class TestTheSwitchOff:
    @pytest.mark.usefixtures("on")
    def test_no_claim_with_or_without_the_declarations(self, monkeypatch):
        assert find.ABSENCE_CLAIMS is False
        plain = {q: run(q) for q in (ZERO, MOST, NEAR)}
        with monkeypatch.context() as m:
            for key in list(sources.REGISTRY):
                _declare(m, key, True)
            declared = {q: run(q) for q in (ZERO, MOST, NEAR)}
        for q in (ZERO, MOST, NEAR):
            for body in (plain[q], declared[q]):
                assert _said(body) == set(), q
                assert body["none_has_all"] is False, q
            assert json.dumps(declared[q], sort_keys=True) == json.dumps(
                plain[q], sort_keys=True), q
            assert cst.format_answer(declared[q], dated=True) == cst.format_answer(
                plain[q], dated=True), q
        # The lines of FIND-4: what the search did, and no claim.
        lines = cst.format_answer(declared[ZERO], dated=True).splitlines()
        assert lines[1] == f"{cst.EACH_WORD_OPENING} zqvortex (0), quasar (0)."
        assert lines[2] == cst.NOTHING_FOUND
        assert cst.format_answer(declared[NEAR], dated=True).splitlines()[1] == (
            "Near spellings: «Nuropeak» as «Neuropeak» (1 record).")
        assert declared[NEAR]["near"] == [
            {"word": "Nuropeak", "as": "Neuropeak", "hits": 1, "held": None}]


# ── The fence: no source declares yet ───────────────────────────────────────

def test_no_source_declares_yet():
    """A source declares ``proves_absence`` only after an audit (§3.6)."""
    declared = sorted(key for key, source in sources.REGISTRY.items() if source.proves_absence)
    assert declared == [], (
        f"{declared} declare Source.proves_absence. find_engine.md §3.6: a source "
        "declares it only after an audit shows that its search reads every field "
        "that its hits print AND every field that holds a word a member can type "
        "(Email To and Cc is the second kind). That slice names its evidence in "
        "§3.6, and it changes this fence.")
    (field,) = [f for f in dataclasses.fields(sources.Source) if f.name == "proves_absence"]
    assert field.default is False
    assert find.ABSENCE_CLAIMS is False
