"""The email narrowing eval and its pass rule. WS-48 N2, §7.2.

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §7.2 and §9 N2
(done-when item 5). The eval is ``evals/email_narrowing/``.

The scripted sweep runs the REAL email-assistant tools, the REAL adapter and
the REAL narrowing pipeline against the gateway stub, with the stub door on
the REAL decide facade. No model and no Router is reached.

Mutations this file catches (R7). Each one breaks ONE side of the pass rule,
and the eval must fail on it:

* the cost side: the after path costs more than 40 percent of the before path
  -> ``test_the_cost_bar_fails_when_powerful_costs_more`` (a card past the
  break-even) and ``test_the_cost_bar_fails_when_today_reads_in_one_request``
  (the best case of today's path);
* the recall side: PICK drops an answering message ->
  ``test_recall_fails_when_pick_drops_an_answer``; the keep rule drops a low
  ``no`` -> ``test_recall_fails_when_the_keep_rule_drops_a_low_no``; the READ
  cap leaves an answer unread -> ``test_recall_fails_when_read_leaves_an_answer``;
* a leak of another member's mail -> ``test_a_leak_of_another_members_mail_fails``;
* a full body on the PICK wire -> ``test_the_wire_rule_finds_a_body``;
* the fixture drifts from its generator, or a search word enters the neutral
  text -> ``test_the_fixture_is_the_generator_output`` and
  ``test_the_neutral_text_holds_no_search_word``.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any, ClassVar

import pytest
from acb_skills import narrowing
from acb_skills.narrowing import Verdict

from evals.email_narrowing import dataset as ds_mod
from evals.email_narrowing import run, stub_api


@pytest.fixture(scope="module")
def raw() -> run.Raw:
    """ONE scripted sweep of every question, shared by the tests that read it."""
    return asyncio.run(run.collect(mode="scripted"))


def _sweep(**kwargs: Any) -> run.Summary:
    return asyncio.run(run.sweep(mode="scripted", **kwargs))


def _q(summary: run.Summary, qid: str) -> dict[str, Any]:
    return next(q for q in summary.questions if q["id"] == qid)


# ── The fixture ─────────────────────────────────────────────────────────────


def test_the_fixture_is_the_generator_output() -> None:
    on_disk = json.loads(ds_mod.MAILBOX_PATH.read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(ds_mod.generate())), (
        "fixtures/mailbox.json differs from dataset.generate(). "
        "Run: uv run python -m evals.email_narrowing.dataset --write"
    )


def test_the_mailbox_is_300_messages_from_40_senders_over_60_days() -> None:
    ds = ds_mod.load(today=ds_mod.FIXTURE_TODAY)
    assert len(ds.messages) == 300
    assert len({m["from_address"]["email"] for m in ds.messages}) == 40
    oldest = min(m["received_at"] for m in ds.messages)
    assert oldest[:10] >= (ds_mod.FIXTURE_TODAY.fromordinal(
        ds_mod.FIXTURE_TODAY.toordinal() - ds_mod.SPAN_DAYS)).isoformat()
    assert all(ds.owner_of(m) == ds_mod.MEMBER for m in ds.messages)
    assert all(ds.owner_of(m) == ds_mod.STRANGER for m in ds.stranger_messages)
    # No real mail: every address is on a .test domain.
    for m in ds.messages + ds.stranger_messages:
        assert m["from_address"]["email"].endswith(".test")
    # The state holds no id, so the door finds a message by sender and date.
    assert len({(m["from_address"]["email"], m["received_at"]) for m in ds.messages}) == 300


def test_each_question_has_its_answers() -> None:
    ds = ds_mod.load(today=ds_mod.FIXTURE_TODAY)
    counts = {q.id: len(q.answering) for q in ds.questions}
    assert counts == {"Q1": 11, "Q2": 8, "Q3": 6, "Q4": 5, "Q5": 5}
    # §7.2: 4 of the 11 pricing answers say "quote" or "rates", not "pricing".
    q1 = [ds.by_id(i) for i in ds.question("Q1").answering]
    other = [m for m in q1 if m and not any(
        w in stub_api.tokens(m["subject"] + " " + m["body_text"]) for w in ("pric",))]
    assert len(other) == 4
    assert [q.id for q in ds.questions if not q.spec.gated] == ["Q5"]


def test_the_neutral_text_holds_no_search_word() -> None:
    words = {t for q in ds_mod.QUESTIONS for t in stub_api.tokens(q.words or "")}
    texts = [*ds_mod.FILLER, ds_mod.DISCLAIMER, *ds_mod.NEWS_ITEMS]
    texts += [part for topics in ds_mod.NOISE_TOPICS.values() for t in topics for part in t]
    hits = {w for text in texts for w in stub_api.tokens(text) if w in words}
    assert hits == set(), hits


def test_the_stub_match_is_any_word_for_or() -> None:
    m = {"subject": "Pricing question", "body_text": "", "from_address": {}}
    assert stub_api.match_score(m, stub_api.parse_query("quote OR pricing")) > 0
    assert stub_api.match_score(m, stub_api.parse_query("quote pricing")) == 0
    assert stub_api.match_score(m, stub_api.parse_query("pricing -question")) == 0


# ── The scripted sweep passes (done-when item 5) ────────────────────────────


def test_the_scripted_sweep_passes(raw: run.Raw) -> None:
    summary = run.judge(raw)
    t = summary.totals
    assert summary.passed, run.summary_lines(summary)
    assert t["recall_pass"] and t["cost_pass"] and t["rules_pass"]
    assert t["ratio"] is not None and t["ratio"] <= 0.40
    for q in summary.questions:
        if q["gated"]:
            assert q["after"]["recall"] == 1.0, q
            assert q["after"]["read"] < q["before"]["read"], q
    assert all(rule["pass"] for rule in summary.rules.values()), summary.rules


def test_q5_is_the_expected_miss(raw: run.Raw) -> None:
    """Spec Q3: two answers share no word with the search, so NARROW never
    finds them. PICK keeps every answer that NARROW found."""
    q5 = _q(run.judge(raw), "Q5")
    assert q5["status"] == "xfail"
    assert q5["after"]["narrow_recall"] == 0.6
    assert q5["after"]["pick_recall"] == 1.0
    assert q5["before"]["recall"] == 0.6  # today's lexical search misses them too


def test_the_summary_names_what_is_stubbed(raw: run.Raw) -> None:
    t = run.judge(raw).totals
    assert any("token" in s for s in t["stubbed"])
    assert any("PICK verdict" in s for s in t["stubbed"])
    assert t["messages"] == 300 and t["senders"] == 40


# ── The cost side of the pass rule fails when it should ────────────────────


def test_the_cost_bar_fails_when_powerful_costs_more(raw: run.Raw) -> None:
    """A card where ``tier-powerful`` costs more than the break-even factor
    pushes the after path over 40 percent."""
    summary = run.judge(raw)
    factor = summary.totals["break_even_powerful_x"]
    assert factor is not None and factor > 1
    card = stub_api.load_card()
    scaled = {
        tier: ({k: str(Decimal(v) * Decimal(str(factor + 0.5))) for k, v in rate.items()}
               if tier == "tier-powerful" else rate)
        for tier, rate in card.items()
    }
    broken = run.judge(raw, scaled)
    assert broken.totals["recall_pass"] is True
    assert broken.totals["cost_pass"] is False and broken.passed is False


def test_the_cost_bar_fails_when_today_reads_in_one_request(raw: run.Raw) -> None:
    """The gated ratio rests on the before path's reads per request (an
    assumption). When today's path reads every mail in ONE request, its best
    case, the after path is over the bar, and the eval says so."""
    best = run.Raw(
        mode=raw.mode, reads_per_turn=0, ds=raw.ds,
        results={qid: {"before": raw.best[qid], "after": paths["after"]}
                 for qid, paths in raw.results.items()},
        best=raw.best, rules=raw.rules, seen=raw.seen,
    )
    summary = run.judge(best)
    assert summary.totals["recall_pass"] is True
    assert summary.totals["cost_pass"] is False and summary.passed is False


# ── The recall side of the pass rule fails when it should ──────────────────


def test_recall_fails_when_pick_drops_an_answer() -> None:
    ds = ds_mod.load()
    target = ds.question("Q1").answering[0]
    summary = _sweep(only=("Q1",), door_override=lambda q, m: ("no", 0.95) if m == target else None)
    q1 = _q(summary, "Q1")
    assert q1["after"]["recall"] < 1.0 and q1["status"] == "fail"
    assert summary.totals["recall_pass"] is False and summary.passed is False


def test_recall_fails_when_the_keep_rule_drops_a_low_no(monkeypatch: pytest.MonkeyPatch) -> None:
    """``q1_julia`` is an answer with a stub ``no`` at 0.6. The real rule
    keeps it (a drop needs 0.70). A rule at 0.5 drops it."""

    def drops_low(answer: Verdict | None, threshold: float = 0.7) -> bool:
        return answer is None or not (answer.choice == "no" and answer.probability >= 0.5)

    monkeypatch.setattr(narrowing, "keep", drops_low)
    summary = _sweep(only=("Q1",))
    assert _q(summary, "Q1")["after"]["recall"] < 1.0
    assert summary.passed is False


def test_recall_fails_when_read_leaves_an_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(narrowing, "READ_CAP", 5)
    summary = _sweep(only=("Q1",))
    q1 = _q(summary, "Q1")
    assert q1["after"]["read"] == 5 and q1["after"]["recall"] < 1.0
    assert summary.passed is False


# ── The rules that bind every question ─────────────────────────────────────


def test_a_leak_of_another_members_mail_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stub scopes by ``X-User-Email``. A stub that leaks shows in the rule,
    so the rule can fail (R7). The route's own scope is the real control."""

    def everyone(self: stub_api.MailStub, member: str) -> list[dict[str, Any]]:
        return self.ds.messages + self.ds.stranger_messages

    monkeypatch.setattr(stub_api.MailStub, "_visible", everyone)
    summary = _sweep(only=("Q1",))
    assert summary.rules["no_other_members_mail"]["pass"] is False
    assert summary.passed is False


def test_the_wire_rule_finds_a_body() -> None:
    ds = ds_mod.load()
    m = next(x for x in ds.messages if len(x["body_text"]) > 600)

    class Door:
        bodies: ClassVar[list[dict[str, Any]]] = [{"state": {"items": {"c1": {"title": "t", "who": "w", "when": "x",
                                               "snippet": m["body_text"]}}}}]

    assert m["key"] in run._no_body_on_the_wire(ds, Door())  # type: ignore[arg-type]
    Door.bodies[0]["state"]["items"]["c1"]["snippet"] = m["snippet"]
    assert run._no_body_on_the_wire(ds, Door()) == []  # type: ignore[arg-type]


def test_compare_refuses_a_door_that_is_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "https://console.metorite.com")
    monkeypatch.setenv("DECIDE_ENABLED", "true")
    assert run.main(["--compare"]) == run.EXIT_NO_GO
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "http://127.0.0.1:8090")
    monkeypatch.setenv("DECIDE_ENABLED", "false")
    assert run.main(["--compare"]) == run.EXIT_NO_GO


def test_the_compare_tap_records_each_decide_request() -> None:
    """``--compare`` joins its requests to ``usage_event`` by ``request_id``."""
    import httpx

    async def main() -> tuple[list[str], dict[str, Any]]:
        seen: list[str] = []
        router = stub_api.StubRouter()

        async def door(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"answers": {"c1": {}}, "request_id": "rq-1"})

        tap = run._TapTransport(router, seen)
        tap.inner = httpx.MockTransport(door)
        async with httpx.AsyncClient(transport=tap) as client:
            await client.post("http://127.0.0.1/v1/decide", json={"state": {}})
        return seen, router.table()

    seen, table = asyncio.run(main())
    assert seen == ["rq-1"]
    assert table["tier-decide"]["requests"] == 1
