"""The WhatsApp narrowing eval and its pass rule. WS-48 N4.

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §7.2 and §9 N4. The
eval is ``evals/whatsapp_narrowing/``. It follows ``test_email_narrowing_eval.py``.

The scripted sweep runs the REAL whatsapp-assistant tools, the REAL adapter
and the REAL narrowing pipeline against the gateway stub, with the stub door
on the REAL decide facade. No model and no Router is reached.

Mutations this file catches (R7). Each one breaks ONE side of the pass rule,
and the eval must fail on it:

* the cost side: ``tier-powerful`` costs past the break-even ->
  ``test_the_cost_bar_fails_when_powerful_costs_more``;
* the recall side: PICK drops an answer ->
  ``test_recall_fails_when_pick_drops_an_answer``; the keep rule drops a low
  ``no`` -> ``test_recall_fails_when_the_keep_rule_drops_a_low_no``; the READ
  cap leaves an answer unread -> ``test_recall_fails_when_read_leaves_an_answer``;
* a leak of another member's chat -> ``test_a_leak_of_another_members_chat_fails``;
* a PICK item with more than its one message -> ``test_the_wire_rule_finds_a_thread``;
* a write on either path -> ``test_a_write_fails_the_no_state_rule``;
* the fixture drifts from its generator, or a search word enters the noise ->
  ``test_the_fixture_is_the_generator_output`` and
  ``test_the_noise_holds_no_search_word``.
"""
from __future__ import annotations

import asyncio
import json
import re
from decimal import Decimal
from typing import Any, ClassVar

import pytest
from acb_skills import narrowing
from acb_skills.narrowing import Verdict

from evals.whatsapp_narrowing import dataset as ds_mod
from evals.whatsapp_narrowing import run, stub_api


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
    on_disk = json.loads(ds_mod.CHATS_PATH.read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(ds_mod.generate())), (
        "fixtures/chats.json differs from dataset.generate(). "
        "Run: uv run python -m evals.whatsapp_narrowing.dataset --write"
    )


def test_the_chats_are_synthetic_and_owned() -> None:
    ds = ds_mod.load(today=ds_mod.FIXTURE_TODAY)
    member_chats = [c for c in ds.chats if ds.owner_of_account(c["account_id"]) == ds_mod.MEMBER]
    assert len(member_chats) == 27 and len(ds.messages) == 410
    assert sum(c["kind"] == "group" for c in member_chats) == 6
    assert all(ds.owner_of(m) == ds_mod.MEMBER for m in ds.messages)
    assert all(ds.owner_of(m) == ds_mod.STRANGER for m in ds.stranger_messages)
    # No real number: no run of 7 digits in any text, and every id is a test id.
    texts = [ds_mod.message_text(m) for m in ds.messages + ds.stranger_messages]
    assert not [t for t in texts if re.search(r"\d{7,}", t.replace(",", ""))]
    assert all(c["wa_chat_id"].startswith("test-") for c in ds.chats)
    assert all(m["wa_message_id"].startswith("test-") for m in ds.messages)
    # The stranger has chats with the member's names: a leak trap for `contact`.
    stranger_names = {c["name"] for c in ds.chats if c["slug"].startswith("stranger_")}
    assert "Dealers North" in stranger_names
    # The door finds a message by its sender and time, so each pair is unique.
    pairs = {(stub_api.who_of(m), m["sent_at"]) for m in ds.messages + ds.stranger_messages}
    assert len(pairs) == len(ds.messages) + len(ds.stranger_messages)


def test_each_question_has_its_answers() -> None:
    ds = ds_mod.load(today=ds_mod.FIXTURE_TODAY)
    assert {q.id: len(q.answering) for q in ds.questions} == {
        "Q1": 8, "Q2": 7, "Q3": 5, "Q4": 5, "Q5": 5}
    assert [q.id for q in ds.questions if not q.spec.gated] == ["Q5"]
    # Voice notes answer Q1, Q2, Q3 and Q4: the adapter must read transcripts.
    voiced = {a for m in ds.messages if m["transcript_text"] for a in m["answers"]}
    assert {"Q1", "Q2", "Q3", "Q4"} <= voiced


def test_the_noise_holds_no_search_word() -> None:
    texts = [*ds_mod.NOISE_IN, *ds_mod.NOISE_OUT, *ds_mod.NOISE_GROUP, *ds_mod.FORWARDS]
    hits = {w for t in texts for w in stub_api.tokens(t) if w in ds_mod.SEARCH_WORDS}
    assert hits == set(), hits


def test_the_stub_match_reads_both_grammars() -> None:
    m = {"body_text": "What is the price", "transcript_text": None, "sender_name": "A"}
    assert stub_api.matches(m, stub_api.parse_query("quote OR price", websearch=True))
    assert not stub_api.matches(m, stub_api.parse_query("quote price", websearch=False))
    assert not stub_api.matches(m, stub_api.parse_query("price -what", websearch=True))
    # 'simple' has no stems: "prices" does not find "price".
    assert not stub_api.matches(m, stub_api.parse_query("prices", websearch=True))


# ── The scripted sweep passes ───────────────────────────────────────────────


def test_the_scripted_sweep_passes(raw: run.Raw) -> None:
    summary = run.judge(raw)
    t = summary.totals
    assert summary.passed, run.summary_lines(summary)
    assert t["recall_pass"] and t["cost_pass"] and t["rules_pass"]
    for q in summary.questions:
        if q["gated"]:
            assert q["after"]["recall"] == 1.0, q
            assert q["after"]["read"] < q["before"]["read"], q
    assert all(rule["pass"] for rule in summary.rules.values()), summary.rules


def test_the_summary_says_the_email_bar_is_not_met(raw: run.Raw) -> None:
    """The bar here is "no worse than today". The summary prints the email
    bar beside it, and each question that costs more, so a pass never reads
    as a measured saving."""
    summary = run.judge(raw)
    lines = "\n".join(run.summary_lines(summary))
    assert summary.totals["cost_bar"] == "1.00" and summary.totals["email_bar"] == "0.40"
    assert summary.totals["ratio"] > 0.40
    assert "EMAIL BAR: the email eval gates at 0.40. This ratio is OVER it" in lines
    assert "WEAK CASE: today's path with every chat read in ONE request" in lines
    assert "break-even: tier-powerful x" in lines
    assert "COSTS MORE: Q2" in lines
    assert any("PICK verdict" in s for s in summary.totals["stubbed"])


def test_q5_is_the_expected_miss(raw: run.Raw) -> None:
    """Spec Q3: two answers share no word with the search, so NARROW never
    finds them. PICK keeps every answer that NARROW found."""
    q5 = _q(run.judge(raw), "Q5")
    assert q5["status"] == "xfail"
    assert q5["after"]["narrow_recall"] == 0.6
    assert q5["after"]["pick_recall"] == 1.0


# ── The cost side fails when it should ──────────────────────────────────────


def test_the_cost_bar_fails_when_powerful_costs_more(raw: run.Raw) -> None:
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


# ── The recall side fails when it should ────────────────────────────────────


def test_recall_fails_when_pick_drops_an_answer() -> None:
    ds = ds_mod.load()
    target = ds.question("Q1").answering[0]
    summary = _sweep(only=("Q1",), door_override=lambda q, m: ("no", 0.95) if m == target else None)
    q1 = _q(summary, "Q1")
    assert q1["after"]["recall"] < 1.0 and q1["status"] == "fail"
    assert summary.totals["recall_pass"] is False and summary.passed is False


def test_recall_fails_when_the_keep_rule_drops_a_low_no(monkeypatch: pytest.MonkeyPatch) -> None:
    """``q1_gopal`` is an answer with a stub ``no`` at 0.6. The real rule
    keeps it (a drop needs 0.70). A rule at 0.5 drops it."""

    def drops_low(answer: Verdict | None, threshold: float = 0.7) -> bool:
        return answer is None or not (answer.choice == "no" and answer.probability >= 0.5)

    monkeypatch.setattr(narrowing, "keep", drops_low)
    summary = _sweep(only=("Q1",))
    assert _q(summary, "Q1")["after"]["recall"] < 1.0
    assert summary.passed is False


def test_recall_fails_when_read_leaves_an_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(narrowing, "READ_CAP", 4)
    summary = _sweep(only=("Q1",))
    q1 = _q(summary, "Q1")
    assert q1["after"]["read"] == 4 and q1["after"]["recall"] < 1.0
    assert summary.passed is False


# ── The rules that bind every question ─────────────────────────────────────


def test_a_leak_of_another_members_chat_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stub scopes by ``X-User-Email``. A stub that leaks shows in the rule,
    so the rule can fail (R7). The route's own scope is the real control, and
    ``test_whatsapp_read_no_mark.py`` holds it on a real database."""

    def everyone(self: stub_api.ChatStub, member: str) -> list[dict[str, Any]]:
        return self.ds.messages + self.ds.stranger_messages

    monkeypatch.setattr(stub_api.ChatStub, "_visible", everyone)
    summary = _sweep(only=("Q2",))
    assert summary.rules["no_other_members_chats"]["pass"] is False
    assert summary.passed is False


def test_the_wire_rule_finds_a_thread() -> None:
    ds = ds_mod.load()
    forward = next(m for m in ds.messages if len(ds_mod.message_text(m)) > 420)
    other = next(m for m in ds.messages if m["slug"] == "q1_asha")

    class Door:
        bodies: ClassVar[list[dict[str, Any]]] = []

        def message_of(self, item: dict[str, Any]) -> str:
            return forward["id"]

    def item(snippet: str) -> dict[str, Any]:
        return {"state": {"items": {"c1": {"title": "t", "who": "w", "when": "x",
                                           "snippet": snippet}}}}

    door = Door()
    # The whole long message, past its clip.
    door.bodies = [item(ds_mod.message_text(forward))]
    assert any("past its clip" in b for b in run._one_message_each(ds, door))  # type: ignore[arg-type]
    # Two messages in one summary: a part of a thread.
    door.bodies = [item(ds_mod.message_text(forward)[:100] + " " + ds_mod.message_text(other))]
    assert forward["slug"] in run._one_message_each(ds, door)  # type: ignore[arg-type]
    # The head of the one message is fine.
    door.bodies = [item(ds_mod.message_text(forward)[:300])]
    assert run._one_message_each(ds, door) == []  # type: ignore[arg-type]


def test_a_write_fails_the_no_state_rule(raw: run.Raw) -> None:
    """READ must change no state. A POST on any path, or a READ that is not the
    thread route with ``around``, fails the rule."""
    after = raw.results["Q1"]["after"]
    write = stub_api.ChatRequest("POST", "/whatsapp/chats/x/read", {}, ds_mod.MEMBER, 405, [])
    after.requests.append(write)
    try:
        rules = run._rules(raw.ds, raw.results, None)
        assert rules["read_changes_no_state"]["pass"] is False
    finally:
        after.requests.remove(write)
    assert run._rules(raw.ds, raw.results, None)["read_changes_no_state"]["pass"] is True


def test_compare_refuses_a_door_that_is_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "https://console.metorite.com")
    monkeypatch.setenv("DECIDE_ENABLED", "true")
    assert run.main(["--compare"]) == run.EXIT_NO_GO
