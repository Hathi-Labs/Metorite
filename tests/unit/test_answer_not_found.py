"""The "not found" check of a chat answer (WS-FIND FIND-7, ``find_engine.md`` §5.5).

A scripted fake model runs inside a real MAF ``Agent``, with its real tool
loop and its real middleware pipeline, as ``test_crm_answer_check.py`` does.
The fake ``search_company`` answers text that the real
``company_search_tools.format_answer`` writes, so the mark of pass 2 is the
mark of production.

The cases:

* the matcher, a corpus of claims and of sentences that are no claim ->
  ``test_a_claim_is_read``, ``test_no_claim_is_read`` and the known false
  positives of §5.5, ``test_a_known_false_positive_is_still_read``;
* the bounded work of the matcher (fix round, P1) ->
  ``test_a_huge_answer_costs_little``, ``test_the_work_is_linear_with_no_cap``,
  ``test_a_claim_after_the_cap_is_not_read``;
* the mark of pass 2, from the real answer text, and no record or query can
  fake it (fix round, P2) -> ``TestTheMarkOfPassTwo``;
* ``off`` changes nothing -> ``TestOff``;
* ``shadow`` logs one miss with no text, and ``armed`` -> ``TestShadow``;
* ``on`` gives one more pass after search_company or resolve_name only, a
  failed retry sends the draft, and the one retry is shared -> ``TestOn``
  and ``TestTheSharedRetry``;
* the one seam and the flag -> ``TestTheSeam``, ``TestTheFlag``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from acb_skills import answer_check
from acb_skills.answer_check import (
    CLAIM_OUTCOMES,
    CLAIM_RETRY_NOTE,
    CLAIM_RULES,
    OFF,
    ON,
    SHADOW,
    AnswerCheckProvider,
    CheckAnswers,
    CheckClaims,
    RunLedger,
    not_found_claim,
)
from acb_skills.company_search_tools import (
    format_answer,
    format_trace,
    ran_second_pass,
)
from agent_framework import Agent, ChatResponseUpdate, Content, Message

from tests.unit.test_crm_answer_check import CRM_RESULT, ScriptedClient, crm_aggregate

ROOT = Path(__file__).resolve().parents[2]

STREAMS = pytest.mark.parametrize("stream", [True, False], ids=["stream", "batch"])

# ── The answers of the fake search, from the real tool text ────────────────

#: A search with no pass 2: the answer of KR-2, with ``CONTEXT_RECENCY`` off.
NO_PASS_TWO = format_answer({"query": "Sharath printer order", "hits": [], "skipped": []})

#: A search with pass 2: the honest zero of FIND-4, with each word alone.
PASS_TWO = format_answer({
    "query": "Sharath printer order", "hits": [], "searched": ["crm", "email"],
    "skipped": [], "meaning": "off", "skipped_why": {}, "where_else": [],
    "broadened": [{"word": "sharath", "hits": 0, "skipped": []},
                  {"word": "printer", "hits": 0, "skipped": []},
                  {"word": "order", "hits": 0, "skipped": []}],
}, dated=True)

#: The answers that the fake search gives, in order.
SEARCH_TEXTS: list[str] = []


async def search_company(query: str) -> str:
    """A fake company search. It answers the next text of SEARCH_TEXTS."""
    return SEARCH_TEXTS.pop(0) if SEARCH_TEXTS else NO_PASS_TWO


async def web_lookup(query: str) -> str:
    """A fake tool that is no find tool."""
    return "The page says nothing."


async def find_tasks(query: str) -> str:
    """A fake list tool of the Projects app. It finds an empty list."""
    return "No tasks match."


async def resolve_name(name: str) -> str:
    """A fake name lookup of FIND-5. It finds no name."""
    return "No person, contact, lead or sender holds that name."


@pytest.fixture(autouse=True)
def _searches() -> Any:
    SEARCH_TEXTS.clear()
    yield
    SEARCH_TEXTS.clear()


@pytest.fixture
def logged(monkeypatch) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    monkeypatch.setattr(answer_check._log, "info",
                        lambda event, **kw: lines.append({"event": event, **kw}))
    return lines


def _claims(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in lines if e["event"] == "answer_check.claim"]


class FailingClient(ScriptedClient):
    """The scripted model, with three more steps: ``("raise",)`` fails the
    request, ``("break", text)`` streams a part of *text* and then fails, and
    ``("cancel",)`` cancels the request."""

    def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        import asyncio

        step = self.script[0] if self.script else ("text", "")
        if step[0] == "raise":
            self.requests.append(list(messages))
            self.script.pop(0)
            raise RuntimeError("the model is down")
        if step[0] == "cancel":
            self.requests.append(list(messages))
            self.script.pop(0)
            raise asyncio.CancelledError()
        if step[0] == "break" and stream:
            self.requests.append(list(messages))
            self.script.pop(0)

            async def _gen() -> Any:
                yield ChatResponseUpdate(contents=[Content.from_text(step[1])],
                                         role="assistant", response_id="rb", message_id="m")
                raise RuntimeError("the stream broke")
            return self._build_response_stream(_gen())
        return super()._inner_get_response(messages=messages, stream=stream,
                                           options=options, **kwargs)


def _agent(script: list[tuple[Any, ...]], tools: list[Any] | None = None) -> tuple[Agent, ScriptedClient]:
    client = ScriptedClient(script)
    agent = Agent(client=client, name="orchestrator",
                  tools=tools if tools is not None else [search_company, web_lookup])
    return agent, client


async def _run(agent: Any, *, stream: bool, mode: str = OFF, claim_mode: str = ON,
               ask: str = "Did Sharath order a 3D printer?") -> str:
    from orchestrator._native_run_context import agent_with_providers

    view = agent_with_providers(agent, [AnswerCheckProvider(mode, claim_mode=claim_mode)])
    if stream:
        return "".join([u.text or "" async for u in view.run(ask, stream=True)])
    return (await view.run(ask)).text


def _user_texts(request: list[Message]) -> list[str]:
    return [m.text for m in request if str(getattr(m.role, "value", m.role)) == "user"]


#: The 11 line boundaries of ``str.splitlines``, by code point, and CR LF.
LINE_BREAKS: list[str] = [chr(c) for c in (
    0x0A, 0x0D, 0x0B, 0x0C, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029)] + ["\r\n"]

CLAIM = "I could not find any order from Sharath for a 3D printer."
FOUND = "I found the deal «Neuropeak - Snowflake 3D printer» from Sharat."


# ── The matcher ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("text", "rule"), [
    ("I could not find any order from Sharath.", "could_not_find"),
    ("I couldn't find a deal named Neuropeak in the CRM.", "could_not_find"),
    ("I couldn" + chr(0x2019) + "t find a deal named Neuropeak.", "could_not_find"),
    ("Sorry, I wasn't able to locate any emails from Sharath.", "could_not_find"),
    ("I was unable to find a matching record.", "could_not_find"),
    ("I can't find any contact named Sharat.", "could_not_find"),
    ("I could not find Sharath in the CRM.", "could_not_find"),
    ("I could not find «Neuropeak» in your inbox.", "could_not_find"),
    ("I couldn't find anything for Neuropeak.", "could_not_find"),
    ("I could not find anyone named Sharath.", "could_not_find"),
    ("I didn't find any tasks matching that.", "could_not_find"),
    ("There is no such order in the CRM.", "no_such"),
    ("No such deal exists.", "no_such"),
    ("There's no record of a 3D printer order.", "no_record_of"),
    ("I got no matches for Neuropeak.", "no_record_of"),
    ("There is no lead called Sharat.", "there_is_no_named"),
    ("The deal Neuropeak does not seem to exist.", "does_not_exist"),
    ("That order doesn't exist in the CRM.", "does_not_exist"),
    ("Here is what I checked.\nI could not find the order.", "could_not_find"),
    # Fix round: the false negatives that the verifier found, now read.
    ("I could not find Neuropeak.", "could_not_find"),
    ("I couldn" + chr(0x2019) + "t find Sharath, but there is a lead named Sharat.",
     "could_not_find"),
    ("I could not find Neuropeak or Sharath in the CRM.", "could_not_find"),
    ("I could not find Bank of Baroda in the CRM.", "could_not_find"),
    ("I found nothing for Sharath.", "found_nothing"),
    ("There is no contact with that name.", "there_is_no_named"),
    ("That company is not in the CRM.", "not_in_place"),
    ("Sharath is not in the CRM.", "not_in_place"),
    # Fix round: no cheap rejection may hide these claims.
    ("I said I could not find the order.", "could_not_find"),
    ("As I said, I could not find the order.", "could_not_find"),
    ("The CRM shows no record of an order from Sharath.", "no_record_of"),
    ("I could not find the order, so I created it.", "could_not_find"),
    # Fix round 3: a tool or a store of records as the subject keeps the claim.
    ("The search results show no matches for Sharath.", "no_record_of"),
    ("Our records show no record of an order from Sharath.", "no_record_of"),
    ("Your CRM records show no match for Neuropeak.", "no_record_of"),
    ("The CRM says there is no such order.", "no_such"),
    ("The CRM confirms there is no such deal.", "no_such"),
    ("Company search reports no matches for Sharath.", "no_record_of"),
    ("I found nothing for Sharath in the CRM.", "found_nothing"),
    # Fix round 4: an app, a product or the assistant is no person.
    ("Zoho says there is no such order.", "no_such"),
    ("Gmail reports no matches for Sharath.", "no_record_of"),
    ("Knowledge reports no record of Sharath.", "no_record_of"),
    ("Salesforce says there is no such deal.", "no_such"),
    ("Email says there is no such thread.", "no_such"),
    ("Odoo says there is no such order.", "no_such"),
    ("Mira says there is no such order.", "no_such"),
])
def test_a_claim_is_read(text: str, rule: str) -> None:
    assert rule in CLAIM_RULES
    assert not_found_claim(text) == rule


@pytest.mark.parametrize(("text", "rule"), [
    # An empty list in other words: the hold arms only after search_company
    # or resolve_name, so an app's list tool alone never pays for it.
    ("I couldn't find any tasks for this week.", "could_not_find"),
    # A capital word that names no record.
    ("I couldn't find Settings.", "could_not_find"),
    # An old fault that the answer reports.
    ("Earlier I could not find the order, but here it is: «Neuropeak».", "could_not_find"),
    # Fix round 3, the verifier's list. Reported speech in other forms.
    ("Priya replied: we could not find the payment in our books.", "could_not_find"),
    ("In his mail, Ravi asks why he could not find the order on the portal.",
     "could_not_find"),
    ("The client mentioned that the deal does not exist on their side.", "does_not_exist"),
    ("Ravi" + chr(0x2019) + "s mail says that the order does not exist.", "does_not_exist"),
    ("According to Ravi, there is no such order.", "no_such"),
    ("The customer could not find the invoice.", "could_not_find"),
    ("Mr. Rao could not find the invoice.", "could_not_find"),
    # Fix round 4, the verifier's list: more forms, by example.
    ("Ravi: I could not find the invoice.", "could_not_find"),
    ("Ravi wrote back and could not find the invoice.", "could_not_find"),
    ("Your note says you could not locate the shipment.", "could_not_find"),
    # The state of a record in other forms.
    ("No record of a payment yet, so the invoice is open.", "no_record_of"),
    ("There is no record of a payment on this invoice.", "no_record_of"),
    # Empty lists in other forms.
    ("I couldn't find any tasks due today.", "could_not_find"),
    ("No results for this week.", "no_record_of"),
    # A record that the answer made after the claim (limit 6).
    ("I could not find the lead, so I created it.", "could_not_find"),
    # A match that is no record.
    ("I could not find a match between the two invoices.", "could_not_find"),
    # A capital word that names no record, in a place of records.
    ("Python is not in the system.", "not_in_place"),
    ("PDF is not in the inbox.", "not_in_place"),
    ("I found nothing for Q4.", "found_nothing"),
])
def test_a_known_false_positive_is_still_read(text: str, rule: str) -> None:
    """The limits of §5.5 name these. A change of the matcher that drops one
    must change the limit too."""
    assert not_found_claim(text) == rule


@pytest.mark.parametrize("text", [
    "I could not find the time to finish the report.",
    "I couldn't find time to update the deal.",
    "I couldn't find a way to export the deal.",
    "I could not find the setting for the deal stage.",
    "I couldn't find any issues with the order.",
    "I didn't find any errors in the invoice.",
    "I couldn't find anything wrong with the deal.",
    "I couldn't find any errors in the system.",
    "I couldn't find a problem in your inbox.",
    "The thread has no mention of a discount.",
    "The mail shows no trace of a payment.",
    'The mail says "We could not find your order".',
    "The note reads «no such account».",
    "The note reads " + chr(0x201C) + "no such account" + chr(0x201D) + ".",
    "The record says 'no such order' in its status.",
    "If I can't find the order, I will tell you.",
    "Let me know if you cannot find the deal.",
    "Could you not find the order?",
    "Is there no such deal?",
    "There are no tasks due today.",
    "This feature does not exist yet.",
    "A zero answer is not proof that no record exists.",
    "(this search found no records)",
    "No record holds all of the words.",
    "I found the order, and it is paid.",
    "Open deals are worth ₹12.3 lakh.",
    "```\nERROR: no such order\n```",
    "> I could not find the order.",
    "Use `no such order` as the test text.",
    "",
    # Fix round: an empty list, a check for duplicates.
    "I couldn't find any overdue tasks.",
    "No matches for your filter.",
    "I couldn't find any deals matching your filter.",
    "I did not find any duplicate contacts.",
    # Fix round: reported speech.
    "Priya replied that there is no such invoice.",
    "Ravi says he could not find the invoice you sent.",
    "The customer wrote that they could not find the invoice.",
    # Fix round: the state of a record.
    "The invoice shows no record of payment.",
    # Fix round: a record that the answer is about to make, or made.
    "The order does not exist yet, so I created it.",
    "The deal did not exist, so I created it.",
    # Fix round: no name.
    "I could not find it.",
    "I found nothing for today.",
    "I found nothing for Monday.",
    "I found nothing wrong with the deal.",
    "It is not in the CRM.",
    # Fix round: false negatives that the limits of §5.5 name.
    "Sharath has no orders.",
    "I could not find anything like that order.",
    "The order you mentioned is not in the CRM.",
    "I could not find nuropeak in the CRM.",
    # Fix round 3: the clause goes on after the place, or no place follows.
    "Neuropeak is not in the CRM pipeline stage you asked about, it is in Won.",
    "I found nothing for Q4 in the forecast.",
    # Fix round 3: false negatives that limit 3 of §5.5 names.
    "Unfortunately, no order for Sharath turns up in the records.",
    "Your inbox has no mails from Sharath.",
    "Our records show no order for Sharath.",
    # Fix round 4: misses that limit 3 of §5.5 names. The rule not_in_place
    # needs the clause to end after the place.
    "Sharath is not in the CRM yet.",
    "Sharath is not in the CRM or the inbox.",
])
def test_no_claim_is_read(text: str) -> None:
    assert not_found_claim(text) is None


# ── The work of the matcher is bounded (fix rounds 1 and 3) ─────────────────

#: The unit of each shape of the reviews, which a test repeats to a length.
#: Before fix round 1, 50,000 characters of "ldquo" took 36 s, of "laquo"
#: 48 s, and of "time_to_order" 6.9 s. The last three units test the rules of
#: fix round 3.
SHAPE_UNITS = {
    "ldquo": chr(0x201C),
    "laquo": chr(0x00AB),
    "time_to_order": "I could not find time to order ",
    "lsquo": chr(0x2018),
    "straight_quote": '"',
    "apostrophe": " '",
    "reported": "Priya said that ",
    "long_word": "x",
    "could_not_find": "I could not find ",
    "reported_system": "The CRM says that ",
    "not_in_place": "Sharath is not in the CRM pipeline ",
    "found_nothing": "I found nothing for Q4 in the forecast ",
}


def _shape(name: str, size: int) -> str:
    """*size* characters of the shape *name*."""
    unit = SHAPE_UNITS[name]
    if name == "long_word":
        return ("I could not find " + unit * size)[:size]
    return (unit * (size // len(unit) + 1))[:size]


def _timed(text: str) -> float:
    """The CPU time of one call, in seconds. A shared box makes a process wait
    for a core. CPU time leaves that wait out, and wall time does not."""
    import time

    started = time.process_time()
    not_found_claim(text)
    return time.process_time() - started


def _best_seconds(text: str, bound: float) -> float:
    """The best of 3 calls, after one call that warms up. The best call is the
    cost of the code, and a slow call is the box. A first call over 3 times
    the bound is the answer at once, so a quadratic mutant fails in one call."""
    first = _timed(text)
    if first > 3 * bound:
        return first
    return min(_timed(text) for _ in range(3))


@pytest.mark.parametrize("shape", list(SHAPE_UNITS))
def test_a_huge_answer_costs_little(shape: str) -> None:
    """2,000,000 characters of each shape. The matcher reads 20,000 of them:
    the best of 3 calls took at most 0.25 s of CPU here. With the cap taken
    out of the code, 11 of the 12 shapes failed the bound of 2 s here. The
    long word did not, and ``test_a_claim_after_the_cap_is_not_read`` fences
    the cap by its effect."""
    assert _best_seconds(_shape(shape, 2_000_000), 2.0) < 2.0


#: The length of each shape for the test with no cap. "time_to_order" is the
#: shape of the hedge for each match: that mutant took 5.2 s for 50,000
#: characters and 45 s for 100,000 here, so it gets the longer text.
NO_CAP_SIZES = {name: 50_000 for name in SHAPE_UNITS} | {"time_to_order": 100_000}


@pytest.mark.parametrize("shape", list(SHAPE_UNITS))
def test_the_work_is_bounded_with_no_cap(shape: str, monkeypatch) -> None:
    """With the cap taken away, the best of 3 calls took 0.03 s to 0.61 s of
    CPU here. The quadratic quote code of the review took 36 s and 48 s for
    50,000 characters, so the bound of 6 s fails it."""
    monkeypatch.setattr(answer_check, "MAX_CLAIM_CHARS", 10**9)
    assert _best_seconds(_shape(shape, NO_CAP_SIZES[shape]), 6.0) < 6.0


def test_the_hedge_runs_once_for_each_sentence(monkeypatch) -> None:
    """The fence of the hedge that ran again for each match (fix round 1, P1).
    It counts calls, so it does not depend on the speed of the box."""
    calls: list[int] = []
    real = answer_check._HEDGE

    class _Counting:
        def search(self, text: str, *args: Any) -> Any:
            calls.append(len(text))
            return real.search(text, *args)

    monkeypatch.setattr(answer_check, "_HEDGE", _Counting())
    text = "I could not find time to order " * 200
    assert not_found_claim(text) is None
    assert len(calls) == 1


def test_a_claim_after_the_cap_is_not_read() -> None:
    """A limit of §5.5: the matcher reads the first MAX_CLAIM_CHARS characters."""
    pad = "Here is the list. " * (answer_check.MAX_CLAIM_CHARS // 18 + 1)
    assert len(pad) > answer_check.MAX_CLAIM_CHARS
    assert not_found_claim(pad + "There is no such order.") is None
    assert not_found_claim("There is no such order. " + pad) == "no_such"


def test_a_quote_longer_than_its_cap_is_no_quote() -> None:
    """A limit of §5.5: a quote of more than MAX_QUOTE_CHARS characters is
    read as prose."""
    long_quote = "«" + "a " * answer_check.MAX_QUOTE_CHARS + "no such order»."
    assert not_found_claim("The mail says «no such order».") is None
    assert not_found_claim("The mail says " + long_quote) == "no_such"


# ── The mark of pass 2 ──────────────────────────────────────────────────────

class TestTheMarkOfPassTwo:
    def test_each_word_alone_marks_pass_two(self) -> None:
        assert ran_second_pass(PASS_TWO)

    def test_the_answer_of_kr_2_shows_no_pass_two(self) -> None:
        assert not ran_second_pass(NO_PASS_TWO)

    def test_a_dated_answer_with_the_words_alone_marks_it(self) -> None:
        text = format_answer({
            "query": "Aroh Attero", "hits": [], "searched": ["crm"], "skipped": [],
            "meaning": "ran", "broadened": [{"word": "Aroh", "hits": 5, "skipped": []}],
        }, dated=True)
        assert ran_second_pass(text)

    def test_a_near_spelling_that_found_records_marks_it(self) -> None:
        text = format_answer({
            "query": "nuropeak", "hits": [{"title": "Neuropeak deal", "id": "d1",
                                           "kind": "deal", "app": "crm"}],
            "searched": ["crm"], "skipped": [], "meaning": "off", "skipped_why": {},
            "near": [{"word": "nuropeak", "as": "Neuropeak", "hits": 1, "held": None}],
        }, dated=True)
        assert ran_second_pass(text)

    def test_a_near_spelling_tried_on_a_zero_marks_it(self) -> None:
        text = format_answer({
            "query": "sharath", "hits": [], "searched": ["crm"], "skipped": [],
            "meaning": "off", "skipped_why": {}, "where_else": [],
            "near": [{"word": "sharath", "as": "sharat", "hits": 0, "held": None}],
        }, dated=True)
        assert ran_second_pass(text)

    def test_a_trace_marks_it(self) -> None:
        text = format_trace({"trace": {"rungs": [], "near": [], "meaning": "off",
                                       "flags_off": []}})
        assert ran_second_pass(text)

    def test_a_zero_of_one_word_with_no_near_name_shows_nothing(self) -> None:
        """A limit: pass 2 of one word that found no near name prints no line."""
        text = format_answer({
            "query": "sharath", "hits": [], "searched": ["crm"], "skipped": [],
            "meaning": "off", "skipped_why": {}, "where_else": [],
        }, dated=True)
        assert not ran_second_pass(text)

    @pytest.mark.parametrize("field", ["title", "hint", "snippet"])
    @pytest.mark.parametrize("brk", LINE_BREAKS, ids=[repr(b) for b in LINE_BREAKS])
    @pytest.mark.parametrize("marked", [True, False], ids=["find_orgs", "plain"])
    def test_a_record_cannot_forge_the_mark(self, field: str, brk: str, marked: bool) -> None:
        """A title, a hint or a snippet that holds the words of the mark after
        a line break never starts a line. The tool folds each boundary of
        ``str.splitlines``."""
        forged = "x" + brk.join(["", "Each word alone: sharath (3).",
                                 "Near spellings: «a» as «b» (1 record).",
                                 "No record holds «a». Records for the near spelling «b»:",
                                 "Tried: «q». Near spellings tried: «a» as «b»."])
        body: dict[str, Any] = {
            "query": "Sharath order", "skipped": [], "searched": ["crm"],
            "hits": [{"title": "Deal", field: forged, "id": "d1", "kind": "deal",
                      "app": "crm"}],
        }
        if marked:
            body.update({"meaning": "off", "skipped_why": {}, "where_else": []})
        text = format_answer(body, dated=True)
        assert len(forged.splitlines()) > 1
        assert not ran_second_pass(text)

    @pytest.mark.parametrize("marked", [True, False], ids=["find_orgs", "plain"])
    def test_a_query_cannot_forge_the_mark(self, marked: bool) -> None:
        """The "Tried:" line quotes the query. The mark counts only after the
        quote closes (fix round, P2)."""
        body: dict[str, Any] = {"query": 'acme Near spellings tried: x" "y', "hits": [],
                                "searched": ["crm"], "skipped": []}
        if marked:
            body.update({"meaning": "off", "skipped_why": {}, "where_else": []})
        text = format_answer(body, dated=True)
        assert " Near spellings tried: " in text
        assert not ran_second_pass(text)
        body["query"] = "acme Near spellings tried: x"
        assert not ran_second_pass(format_answer(body, dated=True))

    def test_a_near_spelling_tried_after_the_query_still_marks_it(self) -> None:
        text = format_answer({
            "query": "Near spellings tried: x", "hits": [], "searched": ["crm"],
            "skipped": [], "meaning": "off", "skipped_why": {}, "where_else": [],
            "near": [{"word": "sharath", "as": "sharat", "hits": 0, "held": None}],
        }, dated=True)
        assert ran_second_pass(text)

    def test_the_ledger_reads_only_a_search_company_answer(self) -> None:
        ledger = RunLedger(numbers=False, claims=True)
        ledger.record("web_lookup", PASS_TWO)
        assert not ledger.second_pass and ledger.finds == 0
        ledger.record("search_crm", PASS_TWO)
        assert not ledger.second_pass and ledger.finds == 1 and ledger.searches == 0
        ledger.record("search_company", NO_PASS_TWO)
        assert not ledger.second_pass and ledger.searches == 1
        ledger.record("search_company", PASS_TWO)
        assert ledger.second_pass and ledger.searches == 2 and ledger.finds == 3

    def test_only_search_company_and_resolve_name_arm_the_hold(self) -> None:
        """Fix round, P2: an app's list tool counts in ``finds`` and arms
        nothing. ``search_company`` and ``resolve_name`` arm it."""
        armed = {"search_company", "resolve_name"}
        assert armed == answer_check.CLAIM_ARM_TOOLS
        ledger = RunLedger(numbers=False, claims=True)
        for tool in ("find_tasks", "query_inbox", "crm_list_records", "search_crm",
                     "find_contact", "resolve_person"):
            ledger.record(tool, "No records.")
        assert (ledger.finds, ledger.lookups) == (6, 0)
        ledger.record("resolve_name", "No name matches.")
        assert (ledger.finds, ledger.lookups, ledger.searches) == (7, 1, 0)


# ── off ─────────────────────────────────────────────────────────────────────

class TestOff:
    @STREAMS
    async def test_off_changes_nothing(self, stream: bool, logged) -> None:
        agent, client = _agent([("call", "search_company", {"query": "Sharath"}),
                                ("text", CLAIM)])
        assert await _run(agent, stream=stream, claim_mode=OFF) == CLAIM
        assert len(client.requests) == 2
        assert _claims(logged) == []

    async def test_off_adds_no_claim_middleware(self) -> None:
        class _Context:
            def __init__(self) -> None:
                self.added: list[Any] = []

            def extend_middleware(self, _source: str, items: list[Any]) -> None:
                self.added += items

        for mode in (ON, SHADOW):
            context = _Context()
            await AnswerCheckProvider(mode).before_run(
                agent=None, session=None, context=context, state={})
            kinds = [type(m) for m in context.added]
            assert CheckClaims not in kinds
            assert kinds[0] is CheckAnswers

    async def test_a_claim_only_run_has_no_number_check(self, monkeypatch) -> None:
        """With ``CRM_ANSWER_CHECK`` off, a CRM result never arms the number
        check, and the ledger parses no figure."""
        from acb_common import number_check

        parsed: list[str] = []
        real = number_check._parse

        def _counting(text: str, *, skip_years: bool) -> list[Any]:
            parsed.append(text)
            return real(text, skip_years=skip_years)

        monkeypatch.setattr(number_check, "_parse", _counting)

        class _Context:
            def __init__(self) -> None:
                self.added: list[Any] = []

            def extend_middleware(self, _source: str, items: list[Any]) -> None:
                self.added = list(items)

        context = _Context()
        provider = AnswerCheckProvider(OFF, claim_mode=ON)
        await provider.before_run(agent=None, session=None, context=context, state={})
        assert CheckAnswers not in [type(m) for m in context.added]
        provider.ledger.record("crm_aggregate", CRM_RESULT, arguments={"entity": "deals"})
        provider.ledger.record("search_company", PASS_TWO)
        assert not provider.ledger.armed
        assert parsed == []
        assert provider.ledger.results == []
        assert provider.ledger.second_pass and provider.ledger.finds == 1
        # The number check alone parses the same result, as before FIND-7.
        numbers_only = RunLedger()
        numbers_only.record("crm_aggregate", CRM_RESULT, arguments={"entity": "deals"})
        assert parsed and numbers_only.armed

    def test_flag_off_counts_no_find_result(self) -> None:
        """Flag off: the ledger of the number check counts no find result."""
        ledger = AnswerCheckProvider(ON).ledger
        ledger.record("search_company", PASS_TWO)
        assert (ledger.finds, ledger.searches, ledger.second_pass) == (0, 0, False)

    @STREAMS
    async def test_a_misquote_is_not_held_by_a_claim_only_run(self, stream: bool) -> None:
        draft = "Open deals hold ₹13.5 lakh."
        agent, client = _agent([("call", "crm_aggregate", {"entity": "deals"}),
                                ("text", draft)],
                               tools=[crm_aggregate, search_company])
        assert await _run(agent, stream=stream, mode=OFF, claim_mode=ON) == draft
        assert len(client.requests) == 2


# ── shadow ──────────────────────────────────────────────────────────────────

class TestShadow:
    @STREAMS
    async def test_a_claim_with_no_pass_two_logs_one_miss(self, stream: bool, logged) -> None:
        agent, client = _agent([("call", "search_company", {"query": "Sharath"}),
                                ("text", CLAIM)])
        assert await _run(agent, stream=stream, claim_mode=SHADOW) == CLAIM
        assert len(client.requests) == 2
        lines = _claims(logged)
        assert len(lines) == 1
        assert lines[0]["outcome"] == "shadow_miss"
        assert lines[0]["rule"] == "could_not_find"
        assert lines[0]["searches"] == 1 and lines[0]["finds"] == 1
        assert lines[0]["armed"] is True

    @STREAMS
    async def test_no_such_order_with_no_search_logs_one_miss(self, stream: bool, logged) -> None:
        agent, _client = _agent([("text", "There is no such order in the CRM.")])
        await _run(agent, stream=stream, claim_mode=SHADOW)
        lines = _claims(logged)
        assert [(e["outcome"], e["rule"], e["searches"], e["armed"]) for e in lines] == [
            ("shadow_miss", "no_such", 0, False)]

    @STREAMS
    async def test_a_claim_after_a_list_tool_only_is_not_armed(self, stream: bool, logged) -> None:
        """The field ``armed`` splits a claim after search_company or
        resolve_name from a claim after an app's list tool."""
        agent, _client = _agent([("call", "find_tasks", {"query": "overdue"}),
                                 ("text", "I couldn't find any open tasks for Sharath.")],
                                tools=[find_tasks, search_company])
        await _run(agent, stream=stream, claim_mode=SHADOW)
        lines = _claims(logged)
        assert [(e["outcome"], e["finds"], e["armed"]) for e in lines] == [
            ("shadow_miss", 1, False)]

    @STREAMS
    async def test_a_claim_with_pass_two_is_no_miss(self, stream: bool, logged) -> None:
        SEARCH_TEXTS.append(PASS_TWO)
        agent, _client = _agent([("call", "search_company", {"query": "Sharath printer order"}),
                                ("text", CLAIM)])
        assert await _run(agent, stream=stream, claim_mode=SHADOW) == CLAIM
        assert [e["outcome"] for e in _claims(logged)] == ["shadow_pass"]

    @STREAMS
    async def test_an_answer_to_another_question_logs_nothing(self, stream: bool, logged) -> None:
        agent, _client = _agent([("call", "web_lookup", {"query": "x"}),
                                ("text", "Open deals are worth ₹12.3 lakh.")])
        await _run(agent, stream=stream, claim_mode=SHADOW)
        assert _claims(logged) == []

    @STREAMS
    async def test_a_step_with_a_tool_call_is_no_answer(self, stream: bool, logged) -> None:
        agent, _client = _agent([
            ("both", "I could not find the order in the CRM, so I will search mail.",
             "search_company", {"query": "Sharath"}),
            ("text", FOUND),
        ])
        assert (await _run(agent, stream=stream, claim_mode=SHADOW)).endswith(FOUND)
        assert _claims(logged) == []

    async def test_the_log_holds_no_text(self, logged) -> None:
        answer = "I could not find any order from Sharath Satyanarayana at Neuropeak."
        agent, _client = _agent([("call", "search_company", {"query": "Sharath Neuropeak"}),
                                 ("text", answer)])
        await _run(agent, stream=True, claim_mode=SHADOW)
        lines = _claims(logged)
        assert len(lines) == 1
        assert set(lines[0]) == {"event", "mode", "outcome", "rule", "searches", "finds",
                                 "armed"}
        assert set(lines[0]) - {"event"} == set(answer_check.CLAIM_LOG_FIELDS)
        assert lines[0]["outcome"] in CLAIM_OUTCOMES and lines[0]["rule"] in CLAIM_RULES
        assert isinstance(lines[0]["armed"], bool)
        flat = json.dumps(logged)
        for word in ("Sharath", "Satyanarayana", "Neuropeak", "order from"):
            assert word not in flat


# ── on ──────────────────────────────────────────────────────────────────────

class TestOn:
    @STREAMS
    async def test_a_claim_with_no_pass_two_gets_one_more_pass(self, stream: bool, logged) -> None:
        SEARCH_TEXTS[:] = [NO_PASS_TWO, PASS_TWO]
        agent, client = _agent([
            ("call", "search_company", {"query": "Sharath"}),
            ("text", CLAIM),
            ("call", "search_company", {"query": "Sharath 3D printer order"}),
            ("text", FOUND),
        ])
        out = await _run(agent, stream=stream)
        assert out == FOUND
        assert CLAIM not in out
        assert len(client.requests) == 4
        assert _user_texts(client.requests[2])[-1] == CLAIM_RETRY_NOTE
        assert [e["outcome"] for e in _claims(logged)] == ["retry"]

    @STREAMS
    async def test_the_answer_of_the_one_more_pass_goes_as_it_is(self, stream: bool, logged) -> None:
        """D-FIND-8: one more pass, then the answer as it is. No note."""
        second = "There is no such order, even with each word alone."
        agent, client = _agent([
            ("call", "search_company", {"query": "Sharath"}),
            ("text", CLAIM),
            ("text", second),
        ])
        assert await _run(agent, stream=stream) == second
        assert len(client.requests) == 3
        assert [e["outcome"] for e in _claims(logged)] == ["retry", "after_retry_miss"]

    @STREAMS
    async def test_a_claim_with_pass_two_goes_out(self, stream: bool, logged) -> None:
        SEARCH_TEXTS.append(PASS_TWO)
        agent, client = _agent([("call", "search_company", {"query": "Sharath printer order"}),
                                ("text", CLAIM)])
        assert await _run(agent, stream=stream) == CLAIM
        assert len(client.requests) == 2
        assert [e["outcome"] for e in _claims(logged)] == ["pass"]

    @STREAMS
    async def test_an_answer_with_no_claim_goes_out(self, stream: bool, logged) -> None:
        agent, client = _agent([("call", "search_company", {"query": "Sharath"}),
                                ("text", FOUND)])
        assert await _run(agent, stream=stream) == FOUND
        assert len(client.requests) == 2
        assert _claims(logged) == []

    async def test_an_answer_before_any_find_is_not_held(self, logged) -> None:
        """No find tool has answered, so the stream is not held. The claim is
        logged, and the run spends no pass."""
        agent, client = _agent([("text", "There is no such order in the CRM.")])
        from orchestrator._native_run_context import agent_with_providers

        view = agent_with_providers(agent, [AnswerCheckProvider(OFF, claim_mode=ON)])
        texts = [u.text async for u in view.run("Any order?", stream=True) if u.text]
        assert len(texts) == 2  # the two deltas of the fake model, as they came
        assert len(client.requests) == 1
        assert [e["outcome"] for e in _claims(logged)] == ["unheld_miss"]

    async def test_a_held_answer_streams_whole(self) -> None:
        agent, _client = _agent([("call", "search_company", {"query": "Sharath"}),
                                 ("text", FOUND)])
        assert await _run(agent, stream=True) == FOUND

    @STREAMS
    async def test_an_empty_list_of_a_list_tool_gets_no_pass(self, stream: bool, logged) -> None:
        """Fix round, P2: an answer that reports an empty list after an app's
        list tool is a true answer. It is not held, and it costs no request."""
        answer = "I couldn't find any open tasks for Sharath."
        agent, client = _agent([("call", "find_tasks", {"query": "Sharath"}),
                                ("text", answer)], tools=[find_tasks, search_company])
        assert await _run(agent, stream=stream) == answer
        assert len(client.requests) == 2
        assert [(e["outcome"], e["armed"]) for e in _claims(logged)] == [
            ("unheld_miss", False)]

    @STREAMS
    async def test_resolve_name_arms_the_hold(self, stream: bool, logged) -> None:
        """A claim after resolve_name alone has no pass 2 of search_company, so
        it gets the one more pass."""
        agent, client = _agent([("call", "resolve_name", {"name": "Sharath"}),
                                ("text", "I could not find anyone named Sharath."),
                                ("text", FOUND)], tools=[resolve_name, search_company])
        assert await _run(agent, stream=stream) == FOUND
        assert len(client.requests) == 3
        assert [(e["outcome"], e["armed"]) for e in _claims(logged)] == [("retry", True)]

    @STREAMS
    async def test_a_retry_that_raises_sends_the_held_draft(self, stream: bool, logged) -> None:
        """Fix round, P3: the one more pass fails. The member reads the draft,
        as with the check off, and the line says ``retry_failed``."""
        client = FailingClient([("call", "search_company", {"query": "Sharath"}),
                                ("text", CLAIM), ("raise",)])
        agent = Agent(client=client, name="orchestrator", tools=[search_company])
        assert await _run(agent, stream=stream) == CLAIM
        assert len(client.requests) == 3
        assert [e["outcome"] for e in _claims(logged)] == ["retry", "retry_failed"]
        assert "retry_failed" in CLAIM_OUTCOMES

    async def test_a_retry_stream_that_breaks_sends_the_held_draft(self, logged) -> None:
        client = FailingClient([("call", "search_company", {"query": "Sharath"}),
                                ("text", CLAIM), ("break", "Half of an answer")])
        agent = Agent(client=client, name="orchestrator", tools=[search_company])
        out = await _run(agent, stream=True)
        assert out == CLAIM
        assert "Half" not in out
        assert [e["outcome"] for e in _claims(logged)] == ["retry", "retry_failed"]

    @STREAMS
    async def test_a_cancel_of_the_retry_is_not_caught(self, stream: bool, logged) -> None:
        import asyncio

        client = FailingClient([("call", "search_company", {"query": "Sharath"}),
                                ("text", CLAIM), ("cancel",)])
        agent = Agent(client=client, name="orchestrator", tools=[search_company])
        with pytest.raises(asyncio.CancelledError):
            await _run(agent, stream=stream)
        assert "retry_failed" not in [e["outcome"] for e in _claims(logged)]


#: A fifth step that a correct run never reads. A second retry reads it, so
#: the count of requests fails, not the script (fix round, item 9).
SPARE = ("text", "A second retry read this.")


class TestTheSharedRetry:
    """``find_engine.md`` §5.5: the check shares the ONE retry of the run. NUM-3
    and HRD-13 are not built, so the number check of CRM-Q29 is the other
    check that spends it."""

    @STREAMS
    async def test_the_claim_takes_the_retry_and_the_number_check_gets_none(
        self, stream: bool, logged,
    ) -> None:
        agent, client = _agent([
            ("call", "crm_aggregate", {"entity": "deals"}),
            ("call", "search_company", {"query": "Sharath"}),
            ("text", "Open deals hold ₹13.5 lakh. " + CLAIM),
            ("text", "Open deals hold ₹13.5 lakh. There is no such order."),
            SPARE,
        ], tools=[crm_aggregate, search_company])
        out = await _run(agent, stream=stream, mode=ON, claim_mode=ON)
        assert len(client.requests) == 4  # one extra request in all, SPARE is unread
        assert out.startswith("Open deals hold ₹13.5 lakh. There is no such order.")
        assert [e["outcome"] for e in _claims(logged)] == ["retry", "after_retry_miss"]
        # The step that calls search_company passes the number check first.
        numbers = [e["outcome"] for e in logged if e["event"] == "answer_check.checked"]
        assert numbers == ["pass", "caveat"]

    @STREAMS
    async def test_the_number_check_takes_the_retry_and_the_claim_gets_none(
        self, stream: bool, logged,
    ) -> None:
        second = "Open deals hold INR 12,34,567. " + CLAIM
        agent, client = _agent([
            ("call", "crm_aggregate", {"entity": "deals"}),
            ("call", "search_company", {"query": "Sharath"}),
            ("text", "Open deals hold ₹13.5 lakh."),
            ("text", second),
            SPARE,
        ], tools=[crm_aggregate, search_company])
        out = await _run(agent, stream=stream, mode=ON, claim_mode=ON)
        assert len(client.requests) == 4  # one extra request in all, SPARE is unread
        assert out == second
        assert [e["outcome"] for e in _claims(logged)] == ["retry_spent"]
        numbers = [e["outcome"] for e in logged if e["event"] == "answer_check.checked"]
        assert numbers == ["pass", "retry", "retry_pass"]

    @STREAMS
    async def test_a_failed_claim_retry_still_spends_the_retry(self, stream: bool, logged) -> None:
        """Fix round 3, F4: the one more pass of the claim raises. It spent the
        retry, so the number check gives its caveat with no retry of its own."""
        draft = "Open deals hold ₹13.5 lakh. " + CLAIM
        client = FailingClient([
            ("call", "crm_aggregate", {"entity": "deals"}),
            ("call", "search_company", {"query": "Sharath"}),
            ("text", draft), ("raise",), SPARE,
        ])
        agent = Agent(client=client, name="orchestrator", tools=[crm_aggregate, search_company])
        out = await _run(agent, stream=stream, mode=ON, claim_mode=ON)
        assert len(client.requests) == 4  # the raise was the one extra request
        assert out.startswith(draft)
        assert [e["outcome"] for e in _claims(logged)] == ["retry", "retry_failed"]
        numbers = [e["outcome"] for e in logged if e["event"] == "answer_check.checked"]
        assert numbers == ["pass", "caveat"]


# ── The one seam and the flag ───────────────────────────────────────────────

class _CopilotShaped:
    _permission_handler = None

    def __init__(self) -> None:
        self._tools = [search_company]


class TestTheSeam:
    def _apply(self, agents: list[Any], monkeypatch, *, claim: str, crm: str = "off",
               name: str = "orchestrator") -> list[Any]:
        import acb_common.settings as settings_mod
        from orchestrator import _tool_injection

        monkeypatch.setattr(settings_mod, "crm_answer_check_mode", lambda: crm)
        monkeypatch.setattr(settings_mod, "find_claim_check_mode", lambda: claim)
        _tool_injection._apply_answer_check(agents, name)
        return agents

    def _provider(self, agent: Any) -> AnswerCheckProvider | None:
        found = [p for p in (agent.context_providers or [])
                 if isinstance(p, AnswerCheckProvider)]
        return found[0] if found else None

    @pytest.mark.parametrize("claim", [SHADOW, ON])
    def test_an_agent_with_search_company_gets_the_check(self, claim: str, monkeypatch) -> None:
        agent, _ = _agent([])
        agents = self._apply([agent], monkeypatch, claim=claim)
        provider = self._provider(agents[0])
        assert provider is not None
        assert provider.claim_mode == claim and provider.mode == OFF

    def test_the_real_dated_tool_gets_the_check(self, monkeypatch) -> None:
        """The orchestrator holds ``search_company_dated`` with CX-1 on, and
        the plain tool with it off. Each one names the check's tool."""
        from acb_skills import company_search_tools as cst

        for tool in (cst.search_company_dated, cst.search_company):
            agent, _ = _agent([], tools=[tool, web_lookup])
            provider = self._provider(self._apply([agent], monkeypatch, claim=ON)[0])
            assert provider is not None and provider.claim_mode == ON

    def test_off_changes_nothing(self, monkeypatch) -> None:
        agent, _ = _agent([])
        assert self._apply([agent], monkeypatch, claim="off")[0] is agent

    def test_an_agent_with_no_search_tool_is_left_alone(self, monkeypatch) -> None:
        agent, _ = _agent([], tools=[web_lookup])
        assert self._apply([agent], monkeypatch, claim=ON)[0] is agent

    def test_a_spawn_child_and_a_copilot_agent_are_left_alone(self, monkeypatch) -> None:
        agent, _ = _agent([])
        assert self._apply([agent], monkeypatch, claim=ON, name="worker")[0] is agent
        copilot = _CopilotShaped()
        assert self._apply([copilot], monkeypatch, claim=ON)[0] is copilot

    def test_both_checks_share_one_provider(self, monkeypatch) -> None:
        agent, _ = _agent([], tools=[crm_aggregate, search_company])
        agents = self._apply([agent], monkeypatch, claim=SHADOW, crm=ON)
        providers = [p for p in agents[0].context_providers
                     if isinstance(p, AnswerCheckProvider)]
        assert len(providers) == 1
        assert (providers[0].mode, providers[0].claim_mode) == (ON, SHADOW)

    def test_with_the_flag_off_the_number_check_is_as_before(self, monkeypatch) -> None:
        """Flag off: a CRM agent gets the provider of CRM-Q29 with no claim
        check, and the log line keeps its fields."""
        lines: list[dict[str, Any]] = []
        from orchestrator import _tool_injection

        monkeypatch.setattr(_tool_injection._log, "info",
                            lambda event, **kw: lines.append({"event": event, **kw}))
        agent, _ = _agent([], tools=[crm_aggregate, search_company])
        agents = self._apply([agent], monkeypatch, claim="off", crm=ON)
        provider = self._provider(agents[0])
        assert provider is not None and provider.claim_mode == OFF
        on = [e for e in lines if e["event"] == "executor.answer_check_on"]
        assert on == [{"event": "executor.answer_check_on", "agent": "orchestrator",
                       "mode": ON}]

    def test_the_seam_reads_the_claim_mode_before_the_import(self, monkeypatch) -> None:
        import sys

        monkeypatch.setitem(sys.modules, "acb_skills.answer_check", None)
        agent, _ = _agent([])
        assert self._apply([agent], monkeypatch, claim="off")[0] is agent
        assert self._apply([agent], monkeypatch, claim=ON)[0] is agent


class TestTheFlag:
    @pytest.mark.parametrize(("raw", "mode"), [
        ("", "off"), ("off", "off"), ("on", "on"), (" ON ", "on"),
        ("shadow", "shadow"), ("yes", "off"),
    ])
    def test_the_mode_reads_the_setting(self, raw: str, mode: str, monkeypatch) -> None:
        from acb_common import get_settings
        from acb_common.settings import find_claim_check_mode

        monkeypatch.setenv("FIND_CLAIM_CHECK", raw)
        get_settings.cache_clear()
        try:
            assert find_claim_check_mode() == mode
        finally:
            get_settings.cache_clear()

    def test_the_default_is_off(self) -> None:
        from acb_common.settings import Settings

        assert Settings.model_fields["find_claim_check"].default == "off"

    def test_only_the_owner_writes_it(self) -> None:
        from acb_common import env_guard

        assert env_guard.is_platform_env("FIND_CLAIM_CHECK")

    def test_the_flag_doc_names_it_and_says_it_needs_a_restart(self) -> None:
        doc = (ROOT / "docs" / "flag_reload.md").read_text(encoding="utf-8")
        start = doc.index("`FIND_CLAIM_CHECK`")
        paragraph = doc[start:doc.index("\n\n", start)]
        assert "restart" in paragraph

    def test_the_retry_note_is_fixed_text(self) -> None:
        """No word of an answer or a record goes into the one more pass."""
        assert "{" not in CLAIM_RETRY_NOTE
        assert "search_company" in CLAIM_RETRY_NOTE
