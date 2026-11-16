"""HRD-18b (task 3226, ``find_engine.md`` §3.1 rung 3, §3.2, §3.3, §3.5):
the company search tries the near spelling of a name.

Hermetic: each app's reader and each near reader is a fake over a small
corpus, as in ``test_company_search_zero.py``. The real SQL of the three near
readers, and the privacy of their names, is in
``test_find_near_visibility_r8.py``.

The claims, each written so that a wrong build fails it:

* **Only with the flag, only on a zero.** With ``FIND_ORGS`` empty the answer
  is byte-identical and no near reader runs. A first pass with a hit asks no
  near reader.
* **The words.** At most 3 words, each of 4 letters or more. First the words
  that no hit holds, then the capitalised ones, then the longest.
* **The spelling.** Each source takes its own best spelling, else the best
  of all. The asked word itself and its plural are no near spelling.
* **The asks.** The query with one word replaced, then the spelling alone.
  Email asks for the mail from the near sender.
* **The answer.** ``near`` names each word, its spelling and its hits. A hit
  says ``matched_by`` ``near`` with ``word`` and ``as``. A hit that two rungs
  found keeps the lower rung. The tool text opens with the near line.
* **Bounded.** A late or failed near reader gives no name and no skip.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import time
from typing import Any, ClassVar

import pytest
from acb_common import get_settings, name_match
from acb_skills import company_search_tools as cst
from gateway.retrieval import company, find, sources
from gateway.retrieval.sources import Hit

from tests.unit.test_company_search_recency import recency  # noqa: F401
from tests.unit.test_company_search_sources import ALL, FEATURES, member

#: The Fracktal evidence (2026-11-14), as rows the fakes read. A row is
#: ``(id, title, kind, the text that the app matches)``. The deal matches its
#: lead «Sharat Satyanarayana» through HRD-18a's related names, so its match
#: text holds the lead. The other Sharath is on «Twin Dragon 300».
CRM_ROWS = [
    ("d-1", "Neuropeak - Snowflake 3D printer", "deal",
     "neuropeak snowflake 3d printer sharat satyanarayana"),
    ("d-2", "Twin Dragon 300 - 3D Printer Hardware", "deal", "twin dragon 300 3d printer hardware"),
    ("l-1", "Sharath .", "lead", "sharath"),
    ("c-1", "Sharath BMS Purchase Dept", "contact", "sharath bms purchase dept"),
]
#: A mail is ``(id, subject, sender name, sender address, body)``. It never
#: says «Neuropeak»: the domain of the sender is ``neuropeek.com``.
MAIL_ROWS = [
    ("m-7", "Snowflake order and payment", "Sharat Satyanarayana", "sharat@neuropeek.com",
     "the order of the 3d printer"),
    ("m-9", "BMS purchase", "Sharath BMS", "sharath@bms.edu", "a quote for filament"),
]
#: The names that each near reader can give, newest first.
NAMES = {
    "crm": ["Sharath .", "Sharath BMS Purchase Dept", "Sharat Satyanarayana",
            "Neuropeak - Snowflake 3D printer", "Twin Dragon 300 - 3D Printer Hardware"],
    "email": ["Sharat Satyanarayana", "sharat@neuropeek.com", "Sharath BMS", "sharath@bms.edu"],
    "people": ["Lakshmi Rao"],
}


def _holds(q: str, text: str) -> bool:
    return all(w in text.lower().split() for w in q.lower().split())


class Near:
    """Fake readers of every app, and fake near readers of three."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.near_calls: list[tuple[str, list[str]]] = []
        self.names = {key: list(v) for key, v in NAMES.items()}
        self.slow: dict[str, float] = {}
        #: Seconds that a reader takes for one query, by the query.
        self.slow_q: dict[str, float] = {}
        self.broken: set[str] = set()
        #: The near lookups running now, and the most at one time.
        self.active = 0
        self.peak = 0

    def reader(self, key: str):
        async def read(user: Any, q: str, k: int, **options: Any) -> list[Hit]:
            self.calls.append((key, q, dict(options)))
            await asyncio.sleep(self.slow_q.get(q, 0.0))
            hits = []
            if key == "crm":
                for ident, title, kind, text in CRM_ROWS:
                    if _holds(q, text):
                        hits.append(Hit(app="crm", kind=kind, id=ident, title=title,
                                        hint=f"{kind.title()} · Fracktal", href=f"/crm/{ident}"))
            if key == "email":
                sender = (options.get("sender") or "").lower()
                for ident, subject, name, address, body in MAIL_ROWS:
                    who = f"{name} {address}".lower()
                    if sender and sender not in who:
                        continue
                    # Full text holds the subject, the body and the sender name,
                    # and an address only as one whole token. With a sender,
                    # the reader reads the mail from that sender, and the
                    # words of `q` still filter it, as the route does.
                    text = f"{subject} {body} {name} {address}"
                    if not q.strip() or _holds(q, text):
                        hits.append(Hit(app="email", kind="email", id=ident, title=subject,
                                        hint=f"Email · from {name}", href=f"/email?email={ident}",
                                        snippet=body))
            return hits[:k]
        return read

    def near(self, key: str):
        async def look(user: Any, words: list[str], k: int) -> dict[str, list[Any]]:
            self.near_calls.append((key, list(words)))
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(self.slow.get(key, 0.0))
            finally:
                self.active -= 1
            if key in self.broken:
                raise RuntimeError("the app is down")
            return name_match.near_lookup(words, self.names.get(key, []), k)
        return look

    def asked(self, key: str) -> list[tuple[str, dict[str, Any]]]:
        return [(q, o) for k, q, o in self.calls if k == key]


def _install(monkeypatch, w: Near, *, near: bool = True) -> Near:
    for key, source in list(sources.REGISTRY.items()):
        monkeypatch.setitem(sources.REGISTRY, key, dataclasses.replace(
            source, read=w.reader(key), gate=None,
            near=w.near(key) if near and source.near is not None else None,
        ))
    return w


@pytest.fixture()
def world(monkeypatch) -> Near:
    return _install(monkeypatch, Near())


@pytest.fixture()
def find_on(monkeypatch):
    def _set(value: str) -> None:
        monkeypatch.setattr(get_settings(), "find_orgs", value, raising=False)

    _set("")
    return _set


@pytest.fixture()
def claims_on(monkeypatch):
    """``find.ABSENCE_CLAIMS`` on (FIND-4 review round 4), so the rules of
    ``held`` false stay fenced. The product ships with it off."""
    monkeypatch.setattr(find, "ABSENCE_CLAIMS", True)


def run(q: str, user: Any = None, k: int = 5) -> dict[str, Any]:
    return asyncio.run(company.company_search(q=q, apps=None, k=k, user=user or member(*ALL)))


def _hit(answer: dict[str, Any], ident: str) -> dict[str, Any]:
    return next(h for h in answer["hits"] if h["id"] == ident)


# ── The matcher's part ──────────────────────────────────────────────────────

class TestTheSpelling:
    def test_the_word_itself_and_its_plural_are_no_near_spelling(self):
        found = name_match.near_names("printer", ["3D printer", "3D Printers", "printed"], 5)
        best = name_match.best_spelling("printer", found)
        assert best is not None and best.spelling == "printed"
        assert name_match.best_spelling("printer", found[:2]) is None

    def test_a_tie_goes_to_the_spelling_with_more_raw_trigrams(self):
        found = name_match.near_names("Sharath", ["Sarath Kumar", "Sharat Satyanarayana"], 5)
        assert {n.score for n in found} == {1.0}
        assert name_match.best_spelling("Sharath", found).spelling == "Sharat"

    def test_one_name_stands_for_each_spelling_the_newest_first(self):
        found = name_match.near_names(
            "Sharath", ["Sharat Satyanarayana", "Sharat Old", "Sharath ."], 5)
        assert [(n.spelling, n.name) for n in found] == [
            ("Sharath", "Sharath ."), ("Sharat", "Sharat Satyanarayana")]

    def test_the_f1_pairs_are_near_in_a_vocabulary(self):
        """F1 of task 3226: the raw SQL floor of HRD-18a never let these reach
        the judge. Scored in Python, each is near."""
        names = ["Lakshmi Rao", "Pooja Shah", "Sharat Satyanarayana", "Dipa Das", "Gita M",
                 "Fani K"]
        for asked, spelt in (("Laxmi", "Lakshmi"), ("Puja", "Pooja"), ("Sarath", "Sharat"),
                             ("Deepa", "Dipa"), ("Geeta", "Gita"), ("Phani", "Fani")):
            best = name_match.best_spelling(asked, name_match.near_names(asked, names, 5))
            assert best is not None and best.spelling == spelt, asked

    def test_a_spelling_is_one_token_of_letters_and_digits(self):
        found = name_match.near_names("nuropeak", ["sharat@neuropeek.com"], 5)
        assert [n.spelling for n in found] == ["neuropeek"]
        assert name_match.near_names("abc", ["abcd"], 5) == []


class TestTheWords:
    def test_at_most_three_words_each_of_four_letters(self):
        q = "Ram Lakshmi Sharath Neuropeak printer"
        words = find.content_words(q)
        assert find.near_words(q, words, set()) == ["neuropeak", "lakshmi", "sharath"]

    def test_a_word_with_no_hit_comes_before_a_capital(self):
        q = "Sharath printer order"
        words = find.content_words(q)
        assert find.near_words(q, words, {"sharath"}) == ["printer", "order", "sharath"]

    def test_the_word_as_typed(self):
        assert find.asked_form("Sharath's 3D printer", "sharath") == "Sharath"
        assert find.asked_form("x", "printer") == "printer"


class TestTheAsks:
    def test_one_ask_for_each_word_with_the_query_replaced(self):
        """Review of 2026-11-16 (P2-a), round 3 (P2-1): one ask for each word,
        so at most 3. The ask is the whole query with that one word replaced
        by the best spelling of all. Email reads the mail from its own best
        sender, and the other words still filter that mail (``rest``)."""
        names = {key: name_match.near_lookup(["sharath"], NAMES[key], 5) for key in NAMES}
        asks = company.near_asks(["sharath", "3d", "printer"], ["sharath"], names,
                                 ["crm", "email", "tasks"])
        assert [(a.q, a.keys, a.sender, a.rest, a.spelling) for a in asks] == [
            ("sharat 3d printer", ("crm", "email", "tasks"), "Sharat", "3d printer", "Sharat"),
        ]

    def test_the_best_of_all_and_the_best_sender_of_email(self):
        names = {"crm": name_match.near_lookup(["nuropeak"], NAMES["crm"], 5),
                 "email": name_match.near_lookup(["nuropeak"], NAMES["email"], 5)}
        asks = company.near_asks(["nuropeak"], ["nuropeak"], names, ["crm", "email", "people"])
        # A tie of score goes to the raw trigrams: «Neuropeak» over «neuropeek».
        assert [(a.q, a.keys, a.spelling, a.sender, a.rest) for a in asks] == [
            ("neuropeak", ("crm", "email", "people"), "Neuropeak", "neuropeek", ""),
        ]

    def test_three_words_and_three_sources_give_at_most_three_asks(self):
        words = ["sharath", "laxmi", "puja"]
        pool = {"crm": ["Sharat S", "Lakshmi R", "Pooja K"],
                "email": ["Sarath M", "Laxmy T", "Pujaa N", "sharat@x.test"],
                "people": ["Sharat P", "Lakshmi Q", "Pooja Z"]}
        names = {key: name_match.near_lookup(words, pool[key], 5) for key in pool}
        asks = company.near_asks(words, words, names, ["crm", "email", "people", "tasks"])
        assert len(asks) == 3
        assert [a.word for a in asks] == words


class TestAHitFoundTwice:
    WORDS: ClassVar[list[str]] = ["sharath", "3d", "printer"]

    def _base(self, **fields: Any) -> Hit:
        return Hit(app="crm", kind="deal", id="d-1", title="Neuropeak 3D printer", hint="",
                   href="/x", **fields)

    def test_on_a_tie_of_words_it_keeps_the_lower_rung_in_its_first_place(self):
        base = self._base()
        word = find.stamped(base, self.WORDS, "word", word="3d")
        near = find.stamped(base, self.WORDS, "near", word="Sharath", as_="Sharat")
        assert word.covers == near.covers == 2
        other = find.stamped(dataclasses.replace(base, id="d-9"), self.WORDS, "near",
                             word="Sharath", as_="Sharat")
        merged = company._merge_near({"crm": [word]}, [{"crm": [near, other]}], ["crm"], 5)
        assert [(h.id, h.matched_by) for h in merged["crm"]] == [("d-1", "word"), ("d-9", "near")]
        # The other order: the near copy first, then the lower rung replaces it.
        merged = company._merge_near({"crm": [near]}, [{"crm": [word]}], ["crm"], 5)
        assert [(h.id, h.matched_by) for h in merged["crm"]] == [("d-1", "word")]

    def test_the_copy_with_more_words_wins(self):
        """Review of 2026-11-16 (P2-d): §3.4 puts coverage first, so a record
        that two rungs found keeps the copy that holds more words. The near
        copy holds «Sharat», 3 of 3 words. The copy of "3D" alone holds 2."""
        base = self._base(snippet="lead Sharat Satyanarayana")
        word = find.stamped(base, self.WORDS, "word", word="3d")
        near = find.stamped(base, self.WORDS, "near", word="Sharath", as_="Sharat")
        assert (word.covers, near.covers) == (2, 3)
        for merged in (company._merge_near({"crm": [word]}, [{"crm": [near]}], ["crm"], 5),
                       company._merge_near({"crm": [near]}, [{"crm": [word]}], ["crm"], 5)):
            assert [(h.id, h.matched_by, h.covers) for h in merged["crm"]] == [("d-1", "near", 3)]

    def test_the_cut_to_k_keeps_the_hits_with_more_words(self):
        weak = [find.stamped(Hit(app="crm", kind="deal", id=f"w-{i}", title=f"Printer {i}",
                                 hint="", href="/x"), self.WORDS, "word", word="printer")
                for i in range(5)]
        strong = find.stamped(self._base(snippet="Sharat"), self.WORDS, "near",
                              word="Sharath", as_="Sharat")
        merged = company._merge_near({"crm": weak}, [{"crm": [strong]}], ["crm"], 5)
        assert [h.id for h in merged["crm"]] == ["d-1", "w-0", "w-1", "w-2", "w-3"]

    def test_a_near_spelling_counts_for_its_word(self):
        hit = Hit(app="email", kind="email", id="m-7", title="Snowflake 3D printer", hint="",
                  href="/x", snippet="from Sharat")
        near = find.stamped(hit, ["sharath", "3d", "printer"], "near", word="Sharath",
                            as_="Sharat")
        assert (near.covers, near.of, near.word, near.as_) == (3, 3, "Sharath", "Sharat")


class TestTheStem:
    """Review of 2026-11-16 (P2-c): the full text matched by stem, so a word
    that the text holds in another form must count, or a hit reads as a false
    miss and starts a second pass."""

    def test_a_common_ending_folds_on_both_sides(self):
        assert find.coverage(["shipping", "delays"], "Shipping delayed") == 2
        assert find.coverage(["printer"], "3D printers") == 1
        assert find.coverage(["shipping"], "the order shipped") == 1
        assert find.coverage(["delayed"], "two delays") == 1
        assert find.coverage(["addresses"], "one address") == 1

    def test_a_short_stem_and_another_word_stay_apart(self):
        # "printed" is not "printer", and a stem under 4 letters is not cut.
        assert find.coverage(["printer"], "Neuropeak printed 3Dx") == 0
        assert find.coverage(["need"], "a ne b") == 0
        assert find.coverage(["sharath"], "Sharat") == 0


class TestTheScoringStops:
    def test_a_deadline_in_the_past_stops_the_scoring(self):
        """The thread that scores names keeps no CPU after its lookup timed
        out: it checks a deadline and stops."""
        names = [f"Name{i} Sharat" for i in range(5000)]
        began = time.monotonic()
        found = name_match.near_names("sharath", names, 5, deadline=time.monotonic() - 1)
        assert found == [] and time.monotonic() - began < 0.5
        assert name_match.near_lookup(["sharath"], names, 5, deadline=time.monotonic() - 1) == {
            "sharath": []}
        # No deadline: the whole list.
        assert name_match.near_names("sharath", names, 5)[0].spelling == "Sharat"


# ── The route ───────────────────────────────────────────────────────────────

class TestOnlyWithTheFlag:
    def test_flag_off_no_near_reader_runs_and_the_answer_is_the_same(
            self, monkeypatch, recency, find_on):  # noqa: F811
        recency(True)
        w = _install(monkeypatch, Near())
        with_near = json.dumps(run("Sharath 3D printer"), sort_keys=True)
        assert w.near_calls == []
        _install(monkeypatch, Near(), near=False)
        assert json.dumps(run("Sharath 3D printer"), sort_keys=True) == with_near
        assert "near" not in json.loads(with_near)

    def test_another_org_on_the_list_changes_nothing(self, world, recency, find_on):  # noqa: F811
        recency(True)
        off = json.dumps(run("nuropeak"), sort_keys=True)
        find_on("not-this-org")
        assert json.dumps(run("nuropeak"), sort_keys=True) == off
        assert world.near_calls == []

    def test_the_flag_acts_only_with_context_recency_on(self, world, recency, find_on):  # noqa: F811
        find_on("*")
        recency(False)
        answer = run("nuropeak")
        assert "near" not in answer and world.near_calls == []


#: The «Sharath BMS» contact on «Twin Dragon 300» (production, 2026-11-14).
#: The CRM matches the deal through its contact, so the first pass finds it.
#: Its title holds 1 of the 3 words, so the pass is a miss (D-FIND-4).
TWIN_DRAGON = ("d-9", "Twin Dragon 300 - 3D", "deal", "twin dragon 300 3d printer sharath bms")


class TestOnlyOnAMiss:
    def test_a_first_pass_hit_with_every_word_asks_nothing_again(
            self, world, recency, find_on):  # noqa: F811
        recency(True)
        find_on("*")
        answer = run("Snowflake printer")
        assert (_hit(answer, "d-1")["covers"], _hit(answer, "d-1")["of"]) == (2, 2)
        assert world.near_calls == []
        assert "near" not in answer and "broadened" not in answer
        assert world.asked("crm") == [("Snowflake printer", {})]

    def test_a_first_pass_hit_with_1_of_3_words_asks_again(
            self, world, recency, find_on, monkeypatch):  # noqa: F811
        """D-FIND-4: the deal of another Sharath holds "3D" only. Both the
        near step and the words alone run, and the first hit stays exact."""
        monkeypatch.setattr(sys.modules[__name__], "CRM_ROWS", [*CRM_ROWS, TWIN_DRAGON])
        recency(True)
        find_on("*")
        answer = run("Sharath 3D printer")
        twin = _hit(answer, "d-9")
        assert (twin["matched_by"], twin["covers"], twin["of"]) == ("exact", 1, 3)
        assert [b["word"] for b in answer["broadened"]] == ["sharath", "3d", "printer"]
        assert [(e["word"], e["as"]) for e in answer["near"]] == [("Sharath", "Sharat")]
        # The deal of «Sharat Satyanarayana» is found too.
        assert "d-1" in {h["id"] for h in answer["hits"]}
        # "sharath" is the word that no hit holds, so the lookup takes it first.
        assert world.near_calls[0][1][0] == "sharath"

    def test_the_rule_of_kr_zero_stays_with_the_flag_off(
            self, world, recency, find_on, monkeypatch):  # noqa: F811
        monkeypatch.setattr(sys.modules[__name__], "CRM_ROWS", [*CRM_ROWS, TWIN_DRAGON])
        recency(True)
        answer = run("Sharath 3D printer")
        assert "broadened" not in answer and "near" not in answer
        assert [h["id"] for h in answer["hits"]] == ["d-9"]

    def test_an_operator_asks_no_near_reader(self, world, recency, find_on):  # noqa: F811
        recency(True)
        find_on("*")
        run('"Sharath printer"')
        run("Sharath -printer")
        assert world.near_calls == []


class TestTheFracktalCase:
    def test_sharath_3d_printer_finds_the_deal_and_the_mail_in_one_call(
            self, world, recency, find_on):  # noqa: F811
        recency(True)
        find_on("*")
        answer = run("Sharath 3D printer")
        # The deal and the mail of «Sharat»: the mail from «Sharath BMS» holds
        # "sharat" in its sender, but not "3d printer", so the ask leaves it
        # out (round 3, P2-1). "Sharath" alone found records, so the word is
        # held, and no near line prints.
        assert answer["near"] == [{"word": "Sharath", "as": "Sharat", "hits": 2, "held": True}]
        assert "No record holds «" not in cst.format_answer(answer, dated=True)
        # "3D" alone found the mail too. Its near copy holds 3 of 3 words and
        # the word copy 2, so the near copy stays (P2-d), and it comes first.
        mail = _hit(answer, "m-7")
        assert (mail["matched_by"], mail["word"], mail["as"], mail["covers"], mail["of"]) == (
            "near", "Sharath", "Sharat", 3, 3)
        assert answer["hits"][0]["id"] == "m-7"
        # The deal holds 2 words in each copy: the tie goes to the lower rung.
        deal = _hit(answer, "d-1")
        assert (deal["matched_by"], deal["covers"]) == ("word", 2)
        assert _hit(answer, "m-9")["matched_by"] == "word"
        # Each word alone still ran, after the one near ask.
        assert [b["word"] for b in answer["broadened"]] == ["sharath", "3d", "printer"]
        # CRM asks the replaced query. Email asks the other words, from the
        # sender (round 3, P2-1).
        asked = world.asked("crm")
        assert asked.index(("sharat 3d printer", {})) < asked.index(("sharath", {}))
        assert ("3d printer", {"sender": "Sharat"}) in world.asked("email")

    def test_the_order_older_than_the_senders_newest_mail_comes_back(
            self, world, recency, find_on, monkeypatch):  # noqa: F811
        """Round 3 (P2-1): Sharat sent 6 newer mails about other things. The
        near ask keeps the other words, so the printer order comes back, and
        no mail that holds none of them is tagged near."""
        chatter = [(f"m-x{i}", f"Lunch plan {i}", "Sharat Satyanarayana",
                    "sharat@neuropeek.com", "see you soon") for i in range(6)]
        monkeypatch.setattr(sys.modules[__name__], "MAIL_ROWS", [*chatter, *MAIL_ROWS])
        recency(True)
        find_on("*")
        answer = run("Sharath 3D printer")
        near = {h["id"]: h for h in answer["hits"] if h["matched_by"] == "near"}
        assert "m-7" in near and (near["m-7"]["covers"], near["m-7"]["of"]) == (3, 3)
        assert not any(i.startswith("m-x") for i in near)
        assert not any(h["id"].startswith("m-x") for h in answer["hits"])

    def test_a_long_query_that_never_ran_alone_claims_nothing(
            self, world, recency, find_on):  # noqa: F811
        """Review of 2026-11-16 (P1-b): 7 tokens never ask each word alone, so
        nothing proves that no record holds «Sharath». The BMS contact does."""
        recency(True)
        find_on("*")
        answer = run("Sharath BMS order for the 3D printer")
        assert "broadened" not in answer
        (entry,) = answer["near"]
        assert (entry["word"], entry["as"], entry["held"]) == ("Sharath", "Sharat", None)
        assert "No record holds «" not in cst.format_answer(answer, dated=True)

    def test_the_near_asks_leave_the_words_alone_most_of_the_time(
            self, world, recency, find_on, monkeypatch):  # noqa: F811
        """P2-a: the near asks take at most a third of the second pass, so a
        slow near ask never starves the words alone."""
        recency(True)
        find_on("*")
        monkeypatch.setattr(company, "BROADEN_BUDGET_S", 1.2)
        for q in ("sharat", "sharat 3d printer", "3d printer", ""):
            world.slow_q[q] = 3.0
        lines: list[tuple[str, dict[str, Any]]] = []

        class _Log:
            def info(self, event: str, **fields: Any) -> None:
                lines.append((event, fields))

        monkeypatch.setattr(company, "_events", _Log())
        answer = run("Sharath 3D printer")
        (_e, near), = [line for line in lines if line[0] == "search.company.near"]
        assert near["asks"] == 1 and near["ms"] <= 1200 / 3 + 150, near
        alone = {b["word"]: b["hits"] for b in answer["broadened"]}
        assert alone["3d"] >= 2 and alone["printer"] >= 2

    def test_the_near_lookups_take_a_session_slot(
            self, world, recency, find_on, monkeypatch):  # noqa: F811
        """P2-b: each near lookup takes a slot of FIND_SESSIONS, as a source
        does, so the near step never opens more sessions than the cap."""
        recency(True)
        find_on("*")
        monkeypatch.setattr(company, "FIND_SESSIONS", 2)
        world.slow.update({"crm": 0.15, "email": 0.15, "people": 0.15})
        answer = run("nuropeak")
        assert len(world.near_calls) == 3 and world.peak == 2
        assert {e["as"] for e in answer["near"]} == {"Neuropeak", "neuropeek"}

    def test_nuropeak_finds_the_deal_and_the_mail_from_its_domain(
            self, world, recency, find_on):  # noqa: F811
        recency(True)
        find_on("*")
        answer = run("nuropeak")
        # FIND-4 review round 4: `find.ABSENCE_CLAIMS` is off, so `held` is
        # never false. The word alone found nothing, so it is not known.
        assert answer["near"] == [
            {"word": "nuropeak", "as": "Neuropeak", "hits": 1, "held": None},
            {"word": "nuropeak", "as": "neuropeek", "hits": 1, "held": None},
        ]
        assert {h["id"] for h in answer["hits"]} == {"d-1", "m-7"}
        for hit in answer["hits"]:
            assert hit["matched_by"] == "near" and hit["word"] == "nuropeak"
        assert "broadened" not in answer

    def test_the_tool_text_opens_with_the_near_line(self, world, recency, find_on):  # noqa: F811
        recency(True)
        find_on("*")
        lines = cst.format_answer(run("nuropeak"), dated=True).splitlines()
        # FIND-4 review round 4: no line says that no record holds the word.
        # The line names the spellings and their records only.
        assert lines[1] == ("Near spellings: «nuropeak» as «Neuropeak» (1 record), "
                            "«nuropeak» as «neuropeek» (1 record).")
        assert "No record holds" not in "\n".join(lines)
        assert any(line.endswith("[near: nuropeak as Neuropeak · 1 of 1 words]")
                   for line in lines)
        assert cst.NOTHING not in lines

    def test_the_answer_keys_of_a_near_hit(self, world, recency, find_on):  # noqa: F811
        recency(True)
        find_on("*")
        mail = _hit(run("nuropeak"), "m-7")
        assert set(mail) == {"app", "kind", "id", "title", "hint", "snippet", "when", "href",
                             "stamp", "matched_by", "covers", "of", "word", "as"}
        assert mail["as"] == "neuropeek"


class TestTheNearLookup:
    def test_a_source_the_member_lacks_gives_no_name(self, world, recency, find_on):  # noqa: F811
        recency(True)
        find_on("*")
        crm_only = member(FEATURES["crm"], FEATURES["tasks"])
        run("nuropeak", user=crm_only)
        assert {key for key, _w in world.near_calls} == {"crm"}

    def test_a_late_reader_gives_no_name_and_no_skip(
            self, world, recency, find_on, monkeypatch):  # noqa: F811
        recency(True)
        find_on("*")
        monkeypatch.setattr(company, "NEAR_LOOKUP_S", 0.1)
        world.slow["crm"] = 2.0
        began = time.monotonic()
        answer = run("nuropeak")
        assert time.monotonic() - began < 1.5
        assert answer["skipped"] == []
        # FIND-4 review round 3: the CRM never gave its names, so a name of
        # the word itself may hide there. `held` is not known.
        assert answer["near"] == [
            {"word": "nuropeak", "as": "neuropeek", "hits": 1, "held": None}]
        assert "No record holds «" not in cst.format_answer(answer, dated=True)

    def test_a_failed_reader_gives_no_name_and_no_skip(self, world, recency, find_on):  # noqa: F811
        recency(True)
        find_on("*")
        world.broken.add("email")
        answer = run("nuropeak")
        assert answer["skipped"] == []
        # FIND-4 review round 3: Email gave no names, so `held` is not known.
        assert answer["near"] == [
            {"word": "nuropeak", "as": "Neuropeak", "hits": 1, "held": None}]
        assert "No record holds «" not in cst.format_answer(answer, dated=True)

    def test_no_near_name_no_near_key(self, world, recency, find_on):  # noqa: F811
        recency(True)
        find_on("*")
        answer = run("Zqxjwv")
        assert "near" not in answer and answer["hits"] == []

    def test_a_short_word_asks_no_near_reader(self, world, recency, find_on):  # noqa: F811
        recency(True)
        find_on("*")
        run("XYZ")
        assert world.near_calls == []

    def test_the_log_line_holds_no_word(self, world, recency, find_on, monkeypatch):  # noqa: F811
        recency(True)
        find_on("*")
        lines: list[tuple[str, dict[str, Any]]] = []

        class _Log:
            def info(self, event: str, **fields: Any) -> None:
                lines.append((event, fields))

        monkeypatch.setattr(company, "_events", _Log())
        run("nuropeak")
        (_event, fields), = [line for line in lines if line[0] == "search.company.near"]
        assert fields["words"] == 1 and fields["asks"] == 1 and fields["hits"] == 2
        assert "nuropeak" not in repr(lines).lower() and "neuropeek" not in repr(lines)


# ── The print ───────────────────────────────────────────────────────────────

class TestThePrint:
    def test_a_word_that_found_nothing_says_no_record_holds_it(self):
        body = {"query": "nuropeak", "hits": [], "skipped": [], "near": [
            {"word": "nuropeak", "as": "Neuropeak", "hits": 1, "held": False}]}
        lines = cst.format_answer(body, dated=True).splitlines()
        assert lines[1] == "No record holds «nuropeak». Records for the near spelling «Neuropeak»:"
        # No hit in the list: the zero lines still follow.
        assert lines[2] == cst.NOTHING

    def test_one_clause_for_each_word_with_no_hit_of_its_own(self):
        body = {"query": "Sharath Laxmi Puja", "hits": [], "near": [
            {"word": "Sharath", "as": "Sharat", "hits": 3, "held": False},
            {"word": "Laxmi", "as": "Lakshmi", "hits": 1, "held": False},
            # Puja found records alone, so no line says that no record holds it.
            {"word": "Puja", "as": "Pooja", "hits": 2, "held": True}]}
        line = cst.format_answer(body, dated=True).splitlines()[1]
        assert line == ("No record holds «Sharath». Records for the near spelling «Sharat»: "
                        "No record holds «Laxmi». Records for the near spelling «Lakshmi»:")

    def test_a_held_word_a_spelling_with_no_record_and_a_bad_entry_give_no_clause(self):
        body = {"hits": [], "near": [
            {"word": "Sharath", "as": "Sharat", "hits": 0, "held": False},
            {"word": "Laxmi «x»", "as": "Lakshmi", "hits": 1, "held": False},
            {"word": "", "as": "nobody", "hits": 1, "held": False}, "not a dict",
            {"word": "Puja", "as": "Pooja", "hits": True, "held": False},
            # No `held`: the route did not say, so the line claims nothing.
            {"word": "Deepa", "as": "Dipa", "hits": 1},
            {"word": "Geeta", "as": "Gita", "hits": 1, "held": "no"}]}
        lines = cst.format_answer(body, dated=True).splitlines()
        assert lines[1] == "No record holds «Laxmi x». Records for the near spelling «Lakshmi»:"

    def test_the_route_says_held_from_the_words_alone_and_from_an_own_hit(
            self, claims_on):
        def hit(ident: str, title: str, rung: str) -> Hit:
            return Hit(app="crm", kind="deal", id=ident, title=title, hint="", href="/x",
                       matched_by=rung)

        near = [{"word": "Sharath", "as": "Sharat", "hits": 1},
                {"word": "Laxmi", "as": "Lakshmi", "hits": 1},
                {"word": "Puja", "as": "Pooja", "hits": 1}]
        # A near hit shows the spelling «Pooja», not the word. A hit that shows
        # «Puja» itself makes `held` unknown (FIND-4 review round 3,
        # `test_find_honest_zero.py`).
        found = {"crm": [hit("a", "Laxmi Rao", "exact"), hit("b", "Pooja Shah", "near")]}
        broadened = [{"word": "sharath", "hits": 2, "skipped": []},
                     {"word": "puja", "hits": 0, "skipped": []}]
        # FIND-4 review round 3: the near lookup ran for each word, and gave no
        # name of the word itself (`looked`).
        got = {e["word"]: e["held"] for e in find.near_report(
            near, found, broadened, query="Sharath Laxmi Puja", first_zero=False,
            skipped=[], looked=dict.fromkeys(("sharath", "laxmi", "puja"), True))}
        # Sharath alone found 2. Laxmi is in a hit of its own words. Puja is
        # only in a near hit, and alone it found nothing in every app.
        assert got == {"Sharath": True, "Laxmi": True, "Puja": False}

    def test_held_is_never_false_while_the_claim_is_off(self):
        """FIND-4 review round 4: `find.ABSENCE_CLAIMS` ships off, so the
        word that the switch would prove absent is not known."""
        assert find.ABSENCE_CLAIMS is False
        (entry,) = find.near_report(
            [{"word": "Puja", "as": "Pooja", "hits": 1}], {},
            [{"word": "puja", "hits": 0, "skipped": []}], query="Puja Shah",
            first_zero=True, skipped=[], looked={"puja": True})
        assert entry["held"] is None

    def test_held_is_unknown_unless_every_app_searched_the_word_alone(self, claims_on):
        """P1-b: "No record holds" needs proof. A word alone that an app
        skipped, a first pass with a skipped app, and a word that was never
        searched alone (a query of more than 5 tokens) are all unknown."""
        near = [{"word": "Puja", "as": "Pooja", "hits": 1}]

        def held(broadened: Any, *, first_zero: bool = True, skipped: Any = (),
                 query: str = "Puja Shah", limited: bool = False) -> Any:
            (entry,) = find.near_report(near, {}, broadened, query=query,
                                        first_zero=first_zero, skipped=list(skipped),
                                        limited=limited, looked={"puja": True})
            return entry["held"]

        zero = [{"word": "puja", "hits": 0, "skipped": []}]
        assert held(zero) is False
        assert held([{"word": "puja", "hits": 0, "skipped": ["crm"]}]) is None
        assert held(zero, skipped=["email"]) is None
        assert held(None) is None
        # A query of the word alone: the first pass is the word alone.
        assert held(None, query="Puja") is False
        assert held(None, query="  puja ") is False
        assert held(None, query="Puja", skipped=["crm"]) is None
        assert held(None, query="Puja", first_zero=False) is True

    def test_a_typed_token_that_is_not_the_word_proves_nothing(self, claims_on):
        """Round 3 (P2-2): the first pass searched "Sharath's" as typed. The
        CRM matches each typed token, so «Sharath BMS» was missed. The first
        pass is the word alone only when the typed token is the word."""
        near = [{"word": "Sharath", "as": "Sharat", "hits": 1}]
        for query in ("Sharath's", "Sharath.", "Sharath\u2019s"):
            (entry,) = find.near_report(near, {}, None, query=query, first_zero=True,
                                        skipped=[])
            assert entry["held"] is None, query
        (entry,) = find.near_report(near, {}, None, query="Sharath", first_zero=True,
                                    skipped=[], looked={"sharath": True})
        assert entry["held"] is False

    def test_the_route_makes_no_claim_for_a_possessive(
            self, world, recency, find_on):  # noqa: F811
        """P2-2 on the route: the lead «Sharath .» and the contact «Sharath BMS»
        hold the word, and the first pass asked "Sharath's" as typed."""
        recency(True)
        find_on("*")
        answer = run("Sharath's")
        assert [(e["word"], e["held"]) for e in answer["near"]] == [("Sharath", None)]
        assert "No record holds «" not in cst.format_answer(answer, dated=True)

    def test_a_search_limited_by_a_date_or_by_verified_proves_nothing(self, claims_on):
        """Round 3 (P3-3): with `after` or `verified_only`, an older or an
        unverified record may hold the word, so held is not known."""
        near = [{"word": "Puja", "as": "Pooja", "hits": 1}]
        zero = [{"word": "puja", "hits": 0, "skipped": []}]
        for broadened, query in ((zero, "Puja Shah"), (None, "Puja")):
            (entry,) = find.near_report(near, {}, broadened, query=query, first_zero=True,
                                        skipped=[], limited=True)
            assert entry["held"] is None, query

    def test_the_route_limits_held_with_after(
            self, world, recency, find_on, claims_on):  # noqa: F811
        recency(True)
        find_on("*")
        answer = asyncio.run(company.company_search(
            q="nuropeak", apps=None, k=5, user=member(*ALL), after="2020-01-01"))
        assert [e["held"] for e in answer["near"]] == [None, None]
        assert "No record holds «" not in cst.format_answer(answer, dated=True)
        # Non-vacuity: with no date, the word alone that found nothing is false.
        assert {e["held"] for e in run("nuropeak")["near"]} == {False}

    def test_undated_text_has_no_near_line(self):
        body = {"hits": [], "near": [{"word": "Sharath", "as": "Sharat", "hits": 1}],
                "skipped": []}
        assert "near" not in cst.format_answer(body)

    def test_the_hit_dict_names_as_only_with_find(self):
        hit = Hit(app="crm", kind="deal", id="d", title="T", hint="", href="/x",
                  matched_by="near", covers=1, of=1, word="Sharath", as_="Sharat")
        assert hit.as_dict(stamp=True, find=True)["as"] == "Sharat"
        assert "as" not in hit.as_dict(stamp=True)


# ── query_inbox (the email agent's tool) ────────────────────────────────────

def _email_agent() -> Any:
    from tests.unit.test_crm_email_tool_refusals import _load_email

    return _load_email()


class TestQueryInbox:
    @pytest.fixture()
    def agent(self, monkeypatch):
        pytest.importorskip("agent_framework", reason="agent_framework not installed")
        module = _email_agent()
        state: dict[str, Any] = {"answer": {}}

        async def fake_get(path: str, params: dict[str, Any] | None = None) -> Any:
            state["path"], state["params"] = path, dict(params or {})
            return state["answer"]

        async def no_labels() -> dict[str, str]:
            return {}

        monkeypatch.setattr(module, "_get", fake_get)
        monkeypatch.setattr(module, "_account_labels", no_labels)
        return module, state

    def _mail(self) -> dict[str, Any]:
        return {"id": "m-7", "subject": "Snowflake order", "received_at": "2026-11-12T08:00:00",
                "from_address": {"name": "Sharat Satyanarayana", "email": "sharat@neuropeek.com"},
                "is_read": True, "snippet": "the order"}

    def test_the_route_answer_with_no_near_reads_as_before(self, agent):
        module, state = agent
        state["answer"] = {"emails": [self._mail()], "total": 1}
        out = asyncio.run(module.query_inbox(from_email="Sharat"))
        assert out.splitlines()[0] == "Found 1 emails (filtered); showing 1:"
        assert "near" not in out

    def test_a_near_sender_names_the_spelling_it_used(self, agent):
        module, state = agent
        state["answer"] = {"emails": [self._mail()], "total": 1,
                           "near": {"word": "Sharath", "as": "Sharat"}}
        out = asyncio.run(module.query_inbox(from_email="Sharath"))
        assert out.splitlines()[0] == (
            "No mail from «Sharath». Mail from the near spelling «Sharat»:")
        assert out.splitlines()[1] == "Found 1 emails (filtered); showing 1:"

    def test_only_a_sender_filter_opts_in_to_the_near_sender(self, agent):
        """The route retries a near sender only when the caller asks. This tool
        asks with a sender filter, and only then."""
        module, state = agent
        state["answer"] = {"emails": [], "total": 0}
        asyncio.run(module.query_inbox(from_email="Sharath"))
        assert (state["path"], state["params"]["near_sender"]) == ("/email/messages", "true")
        asyncio.run(module.query_inbox(query="invoice"))
        assert "near_sender" not in state["params"]

    def test_a_near_sender_with_no_mail_says_so(self, agent):
        module, state = agent
        state["answer"] = {"emails": [], "total": 0,
                           "near": {"word": "Sharath\n«x»", "as": "Sharat"}}
        out = asyncio.run(module.query_inbox(from_email="Sharath"))
        # P1-a: the near sender may have mail that the other filters leave
        # out, so the line says only that none of it matches these filters.
        assert out.splitlines()[0] == (
            "No mail from «Sharath x». No mail from the near spelling «Sharat» "
            "matches these filters.")

    def test_a_page_past_the_end_makes_no_false_claim(self, agent):
        """P1-a: the route found 3 mails from the near spelling, and the page
        asked is past them. The line must not say that none came."""
        module, state = agent
        state["answer"] = {"emails": [], "total": 3,
                           "near": {"word": "Sharath", "as": "Sharat"}}
        out = asyncio.run(module.query_inbox(from_email="Sharath", offset=50))
        assert out.splitlines()[0] == (
            "No mail from «Sharath». Mail from the near spelling «Sharat»:")
        assert "matches these filters" not in out and "none from" not in out
