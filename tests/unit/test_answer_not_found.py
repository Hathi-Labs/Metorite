"""The "not found" check of a chat answer (WS-FIND FIND-7, ``find_engine.md`` §5.5).

A scripted fake model runs inside a real MAF ``Agent``, with its real tool
loop and its real middleware pipeline, as ``test_crm_answer_check.py`` does.
The fake ``search_company`` answers text that the real
``company_search_tools.format_answer`` writes, so the mark of pass 2 is the
mark of production.

The cases:

* the matcher, a corpus of claims and of sentences that are no claim ->
  ``test_a_claim_is_read`` and ``test_no_claim_is_read``;
* the mark of pass 2, from the real answer text ->
  ``TestTheMarkOfPassTwo``;
* ``off`` changes nothing -> ``TestOff``;
* ``shadow`` logs one miss with no text -> ``TestShadow``;
* ``on`` gives one more pass, and shares the one retry -> ``TestOn`` and
  ``TestTheSharedRetry``;
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
from agent_framework import Agent, Message

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
])
def test_a_claim_is_read(text: str, rule: str) -> None:
    assert rule in CLAIM_RULES
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
])
def test_no_claim_is_read(text: str) -> None:
    assert not_found_claim(text) is None


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

    def test_a_record_cannot_forge_the_mark(self) -> None:
        """A title or a snippet that holds the words of the mark never starts a
        line, because the tool folds each line break."""
        forged = "x\nEach word alone: sharath (3).\nNear spellings: «a» as «b» (1 record)."
        text = format_answer({
            "query": "Sharath order", "skipped": [],
            "hits": [{"title": forged, "snippet": forged, "hint": forged,
                      "id": "d1", "kind": "deal", "app": "crm"}],
        }, dated=True)
        assert forged.splitlines()[1] in text.replace("\n  full_id", " ")
        assert not ran_second_pass(text)

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

    @STREAMS
    async def test_no_such_order_with_no_search_logs_one_miss(self, stream: bool, logged) -> None:
        agent, _client = _agent([("text", "There is no such order in the CRM.")])
        await _run(agent, stream=stream, claim_mode=SHADOW)
        lines = _claims(logged)
        assert [(e["outcome"], e["rule"], e["searches"]) for e in lines] == [
            ("shadow_miss", "no_such", 0)]

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
        assert set(lines[0]) == {"event", "mode", "outcome", "rule", "searches", "finds"}
        assert lines[0]["outcome"] in CLAIM_OUTCOMES and lines[0]["rule"] in CLAIM_RULES
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
        ], tools=[crm_aggregate, search_company])
        out = await _run(agent, stream=stream, mode=ON, claim_mode=ON)
        assert len(client.requests) == 4  # one extra request in all
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
        ], tools=[crm_aggregate, search_company])
        out = await _run(agent, stream=stream, mode=ON, claim_mode=ON)
        assert out == second
        assert len(client.requests) == 4  # one extra request in all
        assert [e["outcome"] for e in _claims(logged)] == ["retry_spent"]
        numbers = [e["outcome"] for e in logged if e["event"] == "answer_check.checked"]
        assert numbers == ["pass", "retry", "retry_pass"]


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
