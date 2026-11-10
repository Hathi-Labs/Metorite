"""WS-53 §18.16 — CRM answers are consistent and fast.

Spec: ``project-docs/specs/crm_platform.md`` §18.16.

The incident (production, 2026-10-19). One question about the total of a
named fee for one region "for the month of September" was asked twice, and
it got two different answers, both wrong. The first run took 890 s and 48
tool calls. It waited 419 s on a follow-up card, filtered on a column that
nobody asked for, and summed a sparse field. The second run read the year
before. The org's data is a fixture here, with synthetic values.

R7 fences named here, one for each part:

1. ``crm-today-line``: both channels carry today's date and its zone, and
   the CRM guidance carries the rule of a period with no year.
2. ``crm-follow-up-parks``: a follow-up card after an answer parks the run
   at once. A card before an answer still waits.
3. ``crm-read-budget``: a second field list of one module reads nothing, and
   a read past the budget answers the budget line.
4. ``crm-term-map``: one word maps to one field the same way each time, and
   the confidence rule asks first only when the totals differ.
5. ``crm-amount-ex-gst``: the amount pick prefers the most filled currency
   field that the evidence marks as excluding GST.
6. ``crm-value-alias``: a filter value counts its case and short forms.
7. ``crm-filter-line``: each filter of a module total is stated.
8. ``crm-generic-words``: no word of the incident's org reaches product code.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from acb_skills import crm_tools

REPO = Path(__file__).resolve().parents[2]

#: The DEWiN Invoices shape as a FIXTURE (synthetic labels of the real
#: shape): key, label, type, fill.
INVOICE_FIELDS = [
    ("Total_Product_Amount", "Total Invoice Amount.", "currency", 0.999),
    ("Total_SGST", "Total SGST", "currency", 0.979),
    ("Total_Invoice_Amount", "Total Tax Invoice Amount", "currency", 0.565),
    ("Rent_Charges_Per_Month", "Rent Charges", "currency", 0.543),
    ("IPF_Charges", "IPF Charges", "currency", 0.5417),
    ("Base_IP_Fees", "Base  IP", "currency", 0.4784),
    ("SGST_Charges_For_IPF", "SGST Charges For IPF", "currency", 0.427),
    ("Infra_IP", "Infra + IP", "currency", 0.075),
    ("Total_IP_Infra", "Total (IP + Infra)", "number", 0.4954),
    ("Units1", "EB Units", "decimal", 0.42),
    ("Circle", "Circle", "text", 0.96),
]


class _F:
    def __init__(self, key: str, label: str, type_: str) -> None:
        self.key, self.label, self.type = key, label, type_


def _fields() -> list[_F]:
    return [_F(k, label, t) for k, label, t, _fill in INVOICE_FIELDS]


def _fills() -> dict[str, float]:
    return {k: fill for k, _label, _t, fill in INVOICE_FIELDS}


# ── 1. Today's date, on both channels ───────────────────────────────────────


def test_the_date_line_names_the_weekday_the_date_and_the_zone() -> None:
    from acb_common.today import DEFAULT_ZONE, today_line

    when = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
    assert today_line(when) == "Today is Monday, 19 October 2026 (Asia/Kolkata)."
    assert DEFAULT_ZONE == "Asia/Kolkata"
    # Late on the 18th in UTC is already the 19th in the zone.
    assert "19 October 2026" in today_line(datetime(2026, 10, 18, 20, 0, tzinfo=UTC))
    # A zone that zoneinfo does not know falls back to the default.
    assert today_line(when, "Not/AZone").endswith("(Asia/Kolkata).")


@pytest.fixture()
def bound_run(monkeypatch: pytest.MonkeyPatch) -> str:
    import acb_common.today as today
    import orchestrator.executor as executor

    line = "Today is Monday, 19 October 2026 (Asia/Kolkata)."
    monkeypatch.setattr(executor, "_current_run_org", lambda: "org-1")
    monkeypatch.setattr(executor, "_run_org_display_name", lambda: "Acme")
    monkeypatch.setattr(executor, "run_number_system", lambda: "indian")
    monkeypatch.setattr(executor, "_knowledge_index_block", lambda name: "")
    monkeypatch.setattr(today, "today_line", lambda *a, **k: line)
    return line


def test_a_web_turn_carries_the_date_line_last(bound_run: str) -> None:
    import orchestrator.executor as executor

    parts = executor._context_preamble_parts({}, {}, "orchestrator")
    assert parts[-1] == bound_run
    assert parts[0] == "Organisation: Acme."
    assert bound_run in executor._build_event_message(
        "orchestrator", "r1", {"message": "hi"}, {})
    assert bound_run in executor._run_context_text({}, {}, "orchestrator")


async def test_a_whatsapp_turn_carries_the_date_line(bound_run: str) -> None:
    import orchestrator.executor as executor
    from gateway.routes.whatsapp_channel import bot_run

    seen: list[Any] = []

    class _Agent:
        async def run(self, run_input: Any) -> Any:
            seen.append(run_input)

            class _R:
                text = "ok"

            return _R()

    payload = bot_run.build_payload("A total for September?", [], "m@example.com", native=True)
    await executor._run_with_maf_agent(
        [_Agent()], agent_name="orchestrator", run_id="r1", thread_id="wa-1",
        event_payload=payload, integrations={})
    (run_input,) = seen
    assert bound_run in str(run_input[0].text)


def test_the_crm_guidance_holds_the_period_the_filter_and_the_term_lines() -> None:
    from acb_skills import addendum

    section = next(s for s in addendum.FULL_SECTIONS if s.family == "crm"
                   and isinstance(s.text, str) and "### CRM (read only)" in s.text)
    for line in (addendum.CRM_PERIOD_LINE, addendum.CRM_FILTER_LINE, addendum.CRM_TERM_LINE):
        assert line in section.text
    assert "names the year" in addendum.CRM_PERIOD_LINE
    assert "only on what the question names" in addendum.CRM_FILTER_LINE


# ── 2. A follow-up card never holds the run ─────────────────────────────────


@pytest.fixture()
def run_holder() -> Any:
    token = crm_tools.open_asks_for_run()
    crm_tools.mark_answer_tool(True)
    yield crm_tools.run_asks()
    crm_tools.release_asks(token)


def _card(qid: str) -> list[dict[str, Any]]:
    return [{"header": "h", "question": "Which?", "crm_question": qid,
             "options": [{"label": "A", "key": "A"}]}]


def test_only_a_follow_up_card_of_this_run_parks_early(run_holder: Any) -> None:
    run_holder.follow_ups.add("key:Payments:amount")
    assert crm_tools.follow_up_park_after(_card("key:Payments:amount")) == \
        crm_tools.FOLLOW_UP_PARK_SECONDS
    # A card that asks BEFORE the answer (a CLARIFY or an ask-first term).
    assert crm_tools.follow_up_park_after(_card("key:Payments:date")) is None
    assert crm_tools.follow_up_park_after([*_card("key:Payments:amount"), {}]) is None


def test_with_no_holder_no_card_parks_early() -> None:
    assert crm_tools.follow_up_park_after(_card("key:Payments:amount")) is None


async def test_the_follow_up_of_an_answer_first_total_is_marked(
    run_holder: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real path: ``_follow_up`` offers the card after the answer, and
    marks it, so ``ask_questions`` parks the run at once."""
    from acb_skills import ask_tools

    q = {"id": "key:Payments:amount", "question": "Which field?", "why": "Two fit.",
         "choices": [{"value": "A", "label": "A"}], "answer_first": True, "can_answer": True}
    monkeypatch.setattr(ask_tools, "cards_refused", lambda: False)
    block = crm_tools._follow_up(q, None, {"used": False})
    assert crm_tools.FOLLOW_UP in block
    assert "key:Payments:amount" in run_holder.follow_ups


async def test_ask_questions_parks_a_follow_up_and_waits_on_a_clarify(
    run_holder: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orchestrator.executor as executor
    from acb_skills import ask_tools

    waits: list[Any] = []

    async def wait(fut: Any, timeout: float, thread_id: Any = None,
                   slice_seconds: float = 60.0, park_after: Any = None) -> dict:
        waits.append(park_after)
        return {"answer": "A", "wasFreeform": False, "responder": ""}

    monkeypatch.setattr(executor, "wait_user_future", wait)
    queue: asyncio.Queue = asyncio.Queue()
    token = executor._active_run_queue.set(queue)
    run_holder.follow_ups.add("key:Payments:amount")
    run_holder.notes.append(f"{crm_tools.NOTE_HEAD}: the note.")
    try:
        for qid in ("key:Payments:amount", "key:Payments:date"):
            card = {"questions": [{"header": "h", "question": "Which?", "crm_question": qid,
                                   "options": [{"label": "A", "key": "A"}]}]}
            await ask_tools.ask_questions(json.dumps(card))
    finally:
        executor._active_run_queue.reset(token)
    assert waits == [crm_tools.FOLLOW_UP_PARK_SECONDS, None]
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    # The note that the answer left out goes in BEFORE the parked card.
    kinds = [e.get("type") for e in events]
    assert kinds[:3] == ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"]
    assert "the note." in events[1]["delta"]


@pytest.fixture()
def durable(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """``pending_ask`` as a durable box sees it, with ``park`` as a fake."""
    import orchestrator.executor as executor
    from orchestrator import pending_ask

    calls: list[str] = []

    async def park(rid: str, tid: str, fut: Any = None) -> bool:
        calls.append(rid)
        return True

    async def settle(rid: str, *, answer: Any) -> None:
        return None

    monkeypatch.setattr(pending_ask, "durable_asks_enabled", lambda: True)
    monkeypatch.setattr(pending_ask, "park_after_seconds", lambda: 600.0)
    monkeypatch.setattr(pending_ask, "park", park)
    monkeypatch.setattr(pending_ask, "settle", settle)
    monkeypatch.setattr(executor, "resolve_relay_thread_id", lambda: "t1")
    return calls


async def test_an_early_park_ends_the_wait_at_once(durable: list[str]) -> None:
    import orchestrator.executor as executor

    fut = asyncio.get_running_loop().create_future()
    executor._pending_user_input["r" * 32] = fut
    started = asyncio.get_running_loop().time()
    try:
        with pytest.raises(asyncio.CancelledError):
            await executor.wait_user_future(fut, 30, thread_id="t1", park_after=0.05)
    finally:
        executor._pending_user_input.pop("r" * 32, None)
    assert durable == ["r" * 32]
    assert asyncio.get_running_loop().time() - started < 5


async def test_with_no_early_park_the_wait_is_as_before(durable: list[str]) -> None:
    import orchestrator.executor as executor

    fut = asyncio.get_running_loop().create_future()
    executor._pending_user_input["s" * 32] = fut
    try:
        with pytest.raises(asyncio.TimeoutError):
            await executor.wait_user_future(fut, 0.3, thread_id="t1", slice_seconds=0.1)
    finally:
        executor._pending_user_input.pop("s" * 32, None)
    assert durable == []


async def test_a_refused_early_park_waits_for_the_normal_window(
    durable: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orchestrator.executor as executor
    from orchestrator import pending_ask

    monkeypatch.setattr(pending_ask, "park_after_seconds", lambda: 0.4)
    answers = iter([False, True])

    async def park(rid: str, tid: str, fut: Any = None) -> bool:
        durable.append(rid)
        return next(answers)

    monkeypatch.setattr(pending_ask, "park", park)
    fut = asyncio.get_running_loop().create_future()
    executor._pending_user_input["u" * 32] = fut
    try:
        with pytest.raises(asyncio.CancelledError):
            await executor.wait_user_future(fut, 30, thread_id="t1", slice_seconds=0.1,
                                            park_after=0.05)
    finally:
        executor._pending_user_input.pop("u" * 32, None)
    assert durable == ["u" * 32, "u" * 32]


# ── 3. The cache of the field list, and the budget ──────────────────────────


@pytest.fixture()
def gateway(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []

    async def _get(path: str, params: dict | None = None) -> Any:
        calls.append((path, dict(params or {})))
        if path.startswith("/crm/fields/"):
            return [{"key": "Amount", "label": "Amount", "type": "currency"}]
        if path == "/crm/aggregate":
            return {"total": 10, "records": 1, "groups": []}
        return {}

    monkeypatch.setattr(crm_tools, "_get", _get)
    monkeypatch.setattr(crm_tools, "_short_note", _no_note)
    return calls


async def _no_note(keys: Any) -> str:
    return ""


async def test_a_second_field_list_of_one_module_reads_nothing(
    run_holder: Any, gateway: list,
) -> None:
    first = await crm_tools.crm_list_fields("module:Payments")
    again = await crm_tools.crm_list_fields(entity="module:Payments")
    assert [c[0] for c in gateway] == ["/crm/fields/module:Payments"]
    assert again == f"{first}\n{crm_tools.FIELDS_CACHED_NOTE}"
    await crm_tools.crm_list_fields("module:Payments", page=2)
    assert len(gateway) == 2  # another page is another read
    assert run_holder.reads.total == 2


async def test_the_ninth_aggregate_answers_the_budget_line(
    run_holder: Any, gateway: list,
) -> None:
    for _ in range(crm_tools.AGGREGATE_BUDGET):
        assert "BUDGET" not in await crm_tools.crm_aggregate("deals")
    over = await crm_tools.crm_aggregate("deals")
    assert over.startswith("BUDGET:") and "state the assumption" in over
    reads = len(gateway)
    # A read of another kind still runs until the run's total budget.
    await crm_tools.crm_list_fields("module:Payments")
    assert len(gateway) == reads + 1


async def test_the_total_budget_stops_every_read(run_holder: Any, gateway: list) -> None:
    run_holder.reads.total = crm_tools.READ_BUDGET
    assert (await crm_tools.crm_list_fields("module:X")).startswith("BUDGET:")
    assert gateway == []


async def test_a_run_with_no_holder_has_no_budget(gateway: list) -> None:
    for _ in range(crm_tools.AGGREGATE_BUDGET + 2):
        assert "BUDGET" not in await crm_tools.crm_aggregate("deals")


def test_the_budget_numbers_are_the_spec_s() -> None:
    assert (crm_tools.AGGREGATE_BUDGET, crm_tools.READ_BUDGET) == (8, 20)
    assert crm_tools.FOLLOW_UP_PARK_SECONDS == 2.0


# ── 4. The org's word for a figure ──────────────────────────────────────────


def test_the_term_candidates_of_the_fixture_are_the_three_ip_fields() -> None:
    from gateway.crm_sync import terms

    found = terms.candidates(_fields(), _fills(), "IP billing")
    assert [c.key for c in found] == ["IPF_Charges", "Total_IP_Infra", "Base_IP_Fees"]
    # A tax part and an empty or low-type field never lead.
    assert all("SGST" not in c.key for c in found)
    assert terms.term_words("IP billing") == ["ip"]
    assert terms.term_words("billing") == ["billing"]
    assert terms.candidates(_fields(), _fills(), "rent billing")[0].key == \
        "Rent_Charges_Per_Month"


def test_a_field_with_gst_by_the_arithmetic_drops() -> None:
    from gateway.crm_sync import terms

    found = terms.candidates(_fields(), _fills(), "IP billing",
                             {"IPF_Charges": ("includes_gst",)})
    assert [c.key for c in found] == ["Total_IP_Infra", "Base_IP_Fees", "Infra_IP"]


def test_who_decides_a_term_in_order() -> None:
    from gateway.crm_sync import terms

    cands = terms.candidates(_fields(), _fills(), "IP billing")
    rule = "IP billing means IPF charges"
    assert terms.decide(cands, answer="Base_IP_Fees", rules=[rule], term="IP billing").by == \
        "admin"
    got = terms.decide(cands, rules=[rule], term="IP billing", mine="Base_IP_Fees")
    assert (got.by, got.field) == ("rule", "IPF_Charges")
    assert terms.decide(cands, term="IP billing", mine="Base_IP_Fees").by == "me"
    got = terms.decide(cands, term="IP billing", others=["Total_IP_Infra", "Base_IP_Fees"])
    assert (got.by, got.field) == ("member", "Base_IP_Fees")
    assert terms.decide(cands[:1], term="IP billing").by == "single"
    assert terms.decide(cands, term="IP billing").by is None
    # A pick of a gone field never decides.
    assert terms.decide(cands, term="IP billing", answer="Gone").by is None
    # A rule that names two fields decides nothing.
    two = "IP billing is Base IP or IPF charges"
    assert terms.rule_pick([two], "IP billing", cands) is None


def test_the_close_rule_and_its_number() -> None:
    from gateway.crm_sync import terms

    assert terms.CLOSE_SHARE == crm_tools.CLOSE_SHARE == 0.05
    assert terms.close([100.0, 96.0]) and not terms.close([7942000.0, 4371000.0])
    assert crm_tools._close([100.0, 95.5]) and not crm_tools._close([100.0, 94.0])


def test_a_term_id_is_its_words() -> None:
    from gateway.crm_sync import terms

    assert terms.term_id("Invoices", "  IP   Billing ") == "term:Invoices:ip-billing"
    assert crm_tools._QUESTION_ID.match("term:Invoices:ip-billing")
    from acb_skills.ask_tools import crm_card_tag

    assert crm_card_tag({"crm_question": "term:Invoices:ip-billing"})


SEP = {"field.Circle": "Mumbai", "field.Invoice_Date.from": "2026-09-01",
       "field.Invoice_Date.to": "2026-09-30"}
TOTALS = {"IPF_Charges": 7942000.0, "Total_IP_Infra": 4371000.0, "Base_IP_Fees": 3100000.0}
MATCHED = {"Circle": ["Mumbai", "MUM", "MUMBAI"]}


def _resolve(by: str | None = None, field: str | None = None, ask: bool = True) -> dict:
    cands = [{"key": k, "label": label, "fill": f} for k, label, _t, f in INVOICE_FIELDS
             if k in TOTALS]
    cands.sort(key=lambda c: -c["fill"])
    q = {"id": "term:Invoices:ip-billing", "kind": "term", "label": "Invoices",
         "question": 'When you say "IP billing", which field of Invoices do you mean?',
         "header": "Invoices: IP billing", "why": "Two fields fit.", "can_answer": False,
         "choices": [{"value": c["key"], "label": c["label"], "fill": c["fill"],
                      "best": i == 0} for i, c in enumerate(cands)],
         "none": False, "answer_first": True}
    return {"id": q["id"], "word": "IP billing", "module": "Invoices", "label": "Invoices",
            "by": by, "field": field, "why": "a business rule names it" if by else "",
            "candidates": cands, "held": False, "ask": q if ask else None}


@pytest.fixture()
def term_gateway(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"resolve": _resolve(), "calls": []}

    async def _get(path: str, params: dict | None = None) -> Any:
        state["calls"].append((path, dict(params or {})))
        if path == "/crm/terms/resolve":
            return state["resolve"]
        if path == "/crm/aggregate":
            key = (params or {}).get("field")
            return {"total": TOTALS.get(key), "records": 12, "groups": [],
                    "field": key, "field_type": "currency", "field_label": key,
                    "values": 12, "matched": MATCHED}
        return {}

    monkeypatch.setattr(crm_tools, "_get", _get)
    monkeypatch.setattr(crm_tools, "_short_note", _no_note)
    from acb_skills import ask_tools

    monkeypatch.setattr(ask_tools, "cards_refused", lambda: False)
    return state


def _ask_sep() -> Any:
    return crm_tools.crm_aggregate(
        "module:Invoices", term="IP billing",
        field_filters={"Circle": "Mumbai", "Invoice_Date.from": "2026-09-01",
                       "Invoice_Date.to": "2026-09-30"})


async def test_unsure_asks_first_with_the_two_totals_on_the_web(
    run_holder: Any, term_gateway: dict,
) -> None:
    out = await _ask_sep()
    assert out.startswith(crm_tools.ASK_FIRST)
    assert "₹79.4 lakh" in out and "₹43.7 lakh" in out
    payload = json.loads(next(line for line in out.splitlines() if line.startswith("{")))
    options = payload["questions"][0]["options"]
    assert [o["key"] for o in options][:2] == ["IPF_Charges", "Total_IP_Infra"]
    assert options[0]["description"] == "Total: ₹79.4 lakh"
    assert payload["questions"][0]["crm_question"] == "term:Invoices:ip-billing"
    assert "No other read is needed" in out
    # One resolve and one total for each of the two first choices.
    reads = [c for c in term_gateway["calls"] if c[0] == "/crm/aggregate"]
    assert [r[1]["field"] for r in reads] == ["IPF_Charges", "Total_IP_Infra"]
    assert all(r[1]["measure"] == "sum" for r in reads)
    assert all(r[1][k] == v for r in reads for k, v in SEP.items())
    assert run_holder.asked == {"term:Invoices:ip-billing"}


async def test_unsure_on_whatsapp_names_the_question_with_the_totals(
    term_gateway: dict,
) -> None:
    token = crm_tools.open_text_asks("whatsapp")
    try:
        out = await _ask_sep()
        holder = crm_tools.run_text_asks()
    finally:
        crm_tools.release_text_asks(token)
    assert out.startswith(crm_tools.TEXT_ASK_FIRST)
    assert holder.ask["id"] == "term:Invoices:ip-billing"
    assert holder.ask["figures"] == {"IPF_Charges": "₹79.4 lakh",
                                     "Total_IP_Infra": "₹43.7 lakh"}


async def test_one_question_a_turn_then_the_best_guess(
    run_holder: Any, term_gateway: dict,
) -> None:
    run_holder.asked.add("revenue")
    out = await _ask_sep()
    assert crm_tools.ASK_FIRST not in out
    assert out.splitlines()[0].endswith("INR 7,942,000 across 12 matching records.")
    assert "it holds the most values" in out


async def test_a_rule_decides_and_the_answer_comes_first(
    run_holder: Any, term_gateway: dict,
) -> None:
    term_gateway["resolve"] = _resolve("rule", "IPF_Charges")
    out = await _ask_sep()
    assert crm_tools.ASK_FIRST not in out and run_holder.asked == set()
    assert out.splitlines()[0].endswith("INR 7,942,000 across 12 matching records.")
    note = next(line for line in out.splitlines() if line.startswith(crm_tools.SHOW_NOTE))
    assert '"IP billing" reads IPF Charges (IPF_Charges), because a business rule names it.' \
        in note
    assert "Circle is Mumbai (counted Mumbai, MUM and MUMBAI)" in note
    assert "Invoice_Date from 2026-09-01 to 2026-09-30" in note
    assert "With Total (IP + Infra) instead, the total is ₹43.7 lakh." in note
    calls = [c for c in term_gateway["calls"] if c[0] == "/crm/aggregate"]
    assert len(calls) <= 3


async def test_close_totals_answer_first(run_holder: Any, term_gateway: dict,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(TOTALS, "Total_IP_Infra", 7800000.0)
    out = await _ask_sep()
    assert crm_tools.ASK_FIRST not in out
    assert "within 5% of each other" in out


async def test_a_term_on_a_record_type_is_refused_in_words(term_gateway: dict) -> None:
    out = await crm_tools.crm_aggregate("deals", term="IP billing")
    assert out.startswith("term goes with a module entity")


async def test_a_term_pick_needs_no_other_read(run_holder: Any,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    async def allowed(qid: str, choice: str) -> bool:
        return True

    async def _post(path: str, body: dict) -> Any:
        return {"result": "proposal", "line": "This uses IPF Charges, as you chose."}

    monkeypatch.setattr(crm_tools, "_pick_allowed", allowed)
    monkeypatch.setattr(crm_tools, "_post", _post)
    out = await crm_tools.crm_answer_question("term:Invoices:ip-billing", "IPF_Charges")
    assert "No other read is needed." in out


# ── 5. The amount pick ──────────────────────────────────────────────────────


def _source() -> Any:
    from gateway.crm_sync import catalogue as cat

    return cat.Source(module="Invoices", entity="module:Invoices", kind="module",
                      label="Invoices", records=30000,
                      fields=tuple(cat.Field(k, label, t) for k, label, t, _f in INVOICE_FIELDS),
                      fills=_fills())


def test_the_amount_pick_weighs_currency_fields_with_no_tax_part() -> None:
    from gateway.crm_sync import catalogue as cat

    keys = [f.key for f in cat.amount_evidence_fields(_source())]
    # A total "with tax" stays, so the arithmetic compares it with the
    # amount. A field of a tax part alone (SGST) never weighs.
    assert keys == ["Total_Product_Amount", "Total_Invoice_Amount", "Rent_Charges_Per_Month",
                    "IPF_Charges"]


def test_the_amount_pick_is_the_most_filled_field_that_excludes_gst() -> None:
    from gateway.crm_sync import catalogue as cat

    src = _source()
    evidence = {"Total_Product_Amount": ("excludes_gst",),
                "Total_Invoice_Amount": ("includes_gst",),
                "IPF_Charges": ("excludes_gst",)}
    assert cat.code_amount_ex_gst(src, evidence).key == "Total_Product_Amount"
    assert cat.code_amount_ex_gst(src, {}) is None


class _Pipe:
    """The parts of ``map_pipeline.Pipeline`` that the step reads."""

    def __init__(self, evidence: dict[str, tuple[str, ...]]) -> None:
        from gateway.crm_sync import map_pipeline as mp

        async def read(src: Any) -> dict[str, tuple[str, ...]]:
            return evidence

        self.source = type("S", (), {"amount_evidence": staticmethod(read)})()
        self.inputs = type("I", (), {"profile_version": 3})()
        self.result = mp.Result()


async def _step(approved: list[dict[str, Any]], evidence: dict) -> dict[str, dict]:
    from gateway.crm_sync import map_pipeline as mp

    work = object.__new__(mp._CatalogueWork)
    work.p, work.roles = _Pipe(evidence), {}
    work.sources = {"Invoices": _source()}
    working: dict[str, dict] = {}
    fresh: dict[str, dict] = {}
    await work._ex_gst_amounts(approved, working, fresh)
    return fresh


EX_GST = {"Total_Product_Amount": ("excludes_gst",), "Total_Invoice_Amount": ("includes_gst",)}


async def test_the_sync_moves_a_sparse_ai_amount_to_the_ex_gst_field() -> None:
    approved = [{"kind": "catalogue", "module": "Invoices", "amount": "IPF_Charges",
                 "by": {"amount": "ai"}}]
    fresh = await _step(approved, EX_GST)
    assert fresh["Invoices"]["amount"] == "Total_Product_Amount"
    assert fresh["Invoices"]["by"] == {"amount": "code"}


async def test_the_sync_never_replaces_an_admin_amount() -> None:
    approved = [{"kind": "catalogue", "module": "Invoices", "amount": "IPF_Charges",
                 "by": {"amount": "admin"}}]
    assert await _step(approved, EX_GST) == {}
    # With no field marked as excluding GST, nothing moves.
    assert await _step([{"kind": "catalogue", "module": "Invoices", "amount": "IPF_Charges",
                         "by": {"amount": "ai"}}], {}) == {}


# ── 6. Value aliases, and 7. the filter line ────────────────────────────────


def test_a_value_counts_its_case_and_its_one_short_form() -> None:
    from gateway.crm_sync.terms import value_matches

    distinct = [("Mumbai", 12755), ("Pune", 900), ("MUM", 147), ("MUMBAI", 29),
                ("Andhra Pradesh", 50), ("AP", 9)]
    assert value_matches("Mumbai", distinct) == ["Mumbai", "MUM", "MUMBAI"]
    assert value_matches("mumbai", distinct) == ["Mumbai", "MUM", "MUMBAI"]
    # A short form asked for reads the whole value.
    assert value_matches("MUM", distinct) == ["Mumbai", "MUM", "MUMBAI"]
    assert value_matches("Andhra Pradesh", distinct) == ["Andhra Pradesh", "AP"]
    assert value_matches("Pune", distinct) == ["Pune"]
    assert value_matches("Nowhere", distinct) == ["Nowhere"]


def test_a_short_form_of_two_values_counts_for_neither() -> None:
    from gateway.crm_sync.terms import value_matches

    distinct = [("Mumbai", 10), ("Mumbai Suburban", 5), ("MUM", 3)]
    assert value_matches("Mumbai", distinct) == ["Mumbai"]


def test_the_filter_line_states_each_filter_and_its_counted_values() -> None:
    line = crm_tools.filters_line(SEP, {"matched": MATCHED})
    assert line == ("Filters: Circle is Mumbai (counted Mumbai, MUM and MUMBAI); "
                    "Invoice_Date from 2026-09-01 to 2026-09-30.")
    assert crm_tools.filters_line({"entity": "deals"}, {}) == ""


def test_the_text_filter_is_one_bound_list_in_any_case() -> None:
    from gateway.routes.crm import field_sql

    scope = field_sql.FieldScope(column="fields", owner="module:Invoices", fields={
        "Circle": field_sql.FieldRef(key="Circle", type="text", label="Circle")})
    where = field_sql.module_where(scope, "Invoices", [("Circle", "Mumbai")], zone="UTC",
                                   matches={0: ["Mumbai", "MUM", "MUMBAI"]})
    assert "lower(btrim(fields ->> :agf_k0)) = ANY(CAST(:agf_v0 AS text[]))" in where.sql
    assert where.params["agf_v0"] == ["mum", "mumbai"]
    assert "Mumbai" not in where.sql  # every value is bound


def test_the_whatsapp_question_shows_each_total_and_still_matches() -> None:
    from gateway.routes.crm import text_asks

    figures = text_asks.figures_of({"figures": {"IPF_Charges": "₹79.4 lakh",
                                                "Total_IP_Infra": "<b>x</b>"}})
    assert figures == {"IPF_Charges": "₹79.4 lakh"}
    options = text_asks.with_figures([("IPF_Charges", "IPF Charges"),
                                      ("Base_IP_Fees", "Base IP (excludes GST)")], figures)
    assert options[0] == ("IPF_Charges", "IPF Charges (total ₹79.4 lakh)")
    assert options[1] == ("Base_IP_Fees", "Base IP (excludes GST)")
    pending = {"kind": "question", "id": "term:Invoices:ip", "options": [list(o) for o in options]}
    assert text_asks.match(pending, "1") == "IPF_Charges"
    assert text_asks.match(pending, "ipf charges") == "IPF_Charges"


# ── 8. Generic for every org ────────────────────────────────────────────────

#: The words of the incident's org. A test and a fixture may hold them, and
#: product code may not.
ORG_WORDS = re.compile(r"IPF|Mumbai|\bcircles?\b|Ip_Rent|Total_Product_Amount|DEWiN",
                       re.IGNORECASE)

#: The product files that this batch adds, read in full.
NEW_FILES = (
    "packages/acb_common/acb_common/today.py",
    "apps/services/gateway/gateway/crm_sync/terms.py",
    "apps/services/gateway/gateway/routes/crm/terms.py",
)


def test_the_new_product_files_name_no_word_of_one_org() -> None:
    for rel in NEW_FILES:
        text = (REPO / rel).read_text(encoding="utf-8")
        assert not ORG_WORDS.search(text), (rel, ORG_WORDS.search(text))


def test_the_new_prompt_text_names_no_word_of_one_org() -> None:
    from acb_skills import addendum

    texts = [addendum.CRM_PERIOD_LINE, addendum.CRM_FILTER_LINE, addendum.CRM_TERM_LINE,
             crm_tools.BUDGET_LINE, crm_tools.FIELDS_CACHED_NOTE, crm_tools.TEXT_ASK_FIRST,
             crm_tools.crm_aggregate.__doc__ or ""]
    for text in texts:
        assert not ORG_WORDS.search(text), text


def _added_lines() -> list[str] | None:
    """The lines that this branch adds to product code, or None when git
    has no main to compare with (a shallow checkout)."""
    with contextlib.suppress(Exception):
        base = subprocess.run(["git", "merge-base", "HEAD", "origin/main"], cwd=REPO,
                              capture_output=True, text=True, check=True).stdout.strip()
        diff = subprocess.run(
            ["git", "diff", "-U0", base, "--", "apps", "packages", ":(exclude)*tests*"],
            cwd=REPO, capture_output=True, text=True, check=True, encoding="utf-8").stdout
        return [ln[1:] for ln in diff.splitlines()
                if ln.startswith("+") and not ln.startswith("+++")]
    return None


def test_no_added_line_of_product_code_names_a_word_of_one_org() -> None:
    lines = _added_lines()
    if lines is None:
        pytest.skip("no origin/main to compare with; the two tests above still hold")
    hits = [ln for ln in lines if ORG_WORDS.search(ln)]
    assert hits == [], hits
