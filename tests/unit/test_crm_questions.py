"""WS-53 CRM-Q32-1 — the question read, hermetic.

Spec: ``project-docs/specs/crm_platform.md`` §18.14.3 to §18.14.7, and the
CRM-Q32-1 done-when list of §18.14.10.

``crm_sync/questions.py`` is pure: it reads no database and calls no model.
Each test below builds the catalogue entries with ``catalogue.build`` from
synthetic sources in the shape of DEWiN, and reads the questions.

* Item 1: the five kinds, the ids, the sentences of why and the order.
* Item 5: ``ranked`` in ``item_problem`` and ``merge_item``.
* Item 7: the org-level item ``@org``.
* Item 9: the gate of ``open`` in ``on``, ``off`` and on a parked key.
* Item 10: ``questions_allowed`` is the AND of two lists.
* Item 11: the reads of ``slot`` and ``stage_value``.
* A dismissal holds until the hash of its question changes.

``test_crm_questions_r8.py`` proves the route, the SQL and the tenancy on a
real Postgres.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from gateway.crm_sync import catalogue as cat
from gateway.crm_sync import map_pipeline as mp
from gateway.crm_sync import questions as qs

REPO = Path(__file__).resolve().parents[2]
PURE = REPO / "apps/services/gateway/gateway/crm_sync/questions.py"
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


# ── DEWiN's shape, invented names ────────────────────────────────────────────


def _f(key: str, kind: str, target: str | None = None, label: str | None = None) -> cat.Field:
    return cat.Field(key, label or key.replace("_", " "), kind, target)


def _module(api: str, label: str, records: int, fields: list[cat.Field],
            fills: dict[str, float] | None = None, *, custom: bool = True,
            singular: str | None = None) -> cat.Source:
    return cat.Source(module=api, entity=f"module:{api}", kind="module", label=label,
                      records=records, singular=singular, custom=custom,
                      fields=tuple(fields), fills=fills, display_field="Name")


def _core(module: str, slug: str, label: str, records: int) -> cat.Source:
    return cat.Source(module=module, entity=slug, kind="entity", label=label,
                      records=records, standard_label=label == module)


INVOICES = _module(
    "Invoices", "Invoices", 30177,
    [_f("Name", "text"), _f("Total_Product_Amount", "currency"),
     _f("Net_Amount", "currency"), _f("Invoice_Date", "date"),
     _f("Status", "select"), _f("Clients", "lookup", "Clients")],
    {"Total_Product_Amount": 0.45, "Net_Amount": 0.4, "Invoice_Date": 0.4, "Status": 0.9},
    custom=False, singular="Invoice")
COLLECTION = _module(
    "Payment_Received", "Collection", 8681,
    [_f("Name", "text"), _f("IP_Amount", "currency"), _f("Rent_Amount", "currency"),
     _f("EB_Amount", "currency"), _f("Date_of_Payment_Record", "date"),
     _f("Clients", "lookup", "Clients")],
    {"IP_Amount": 0.52, "Rent_Amount": 0.5, "EB_Amount": 0.51,
     "Date_of_Payment_Record": 0.85})
CLIENTS = _module("Clients", "Clients", 1240, [_f("Name", "text")], {"Name": 1.0})
TOWERS = _module("Towers", "Towers", 805, [_f("Name", "text")], {"Name": 1.0})
EMPTY = _module("Old_Things", "Old Things", 0, [_f("Name", "text")], {})

SOURCES = [
    _core("Accounts", "organizations", "Accounts", 4),
    _core("Contacts", "contacts", "Field Sites", 805),
    _core("Leads", "leads", "Leads", 0),
    _core("Deals", "deals", "Deals", 0),
    INVOICES, COLLECTION, CLIENTS, TOWERS, EMPTY,
]


def _inputs(items: list[dict[str, Any]] | None = None, *, mode: str = "off",
            cache: mp.Cache | None = None, sources: list[cat.Source] | None = None,
            roles: dict[str, str] | None = None, **more: Any) -> qs.Inputs:
    sources = sources or SOURCES
    items = items or []
    entries = cat.build(sources, roles or {}, items)
    return qs.Inputs(
        entries=entries, sources={s.module: s for s in sources},
        stored=cat.stored_items(items), mode=mode, cache=cache or mp.Cache(), now=NOW,
        **more,
    )


def _ids(questions: list[qs.Question]) -> list[str]:
    return [q.id for q in questions]


def _by_id(questions: list[qs.Question]) -> dict[str, qs.Question]:
    return {q.id: q for q in questions}


# ── Item 1: the five kinds, their ids, their sentences and their order ───────


DEAL_STAGE = qs.StageState(
    slot="deal", module="Invoices", label="Invoices", field="Status",
    values={"Won": "won"}, seen=(("On Hold", 5), ("Won", 3)), options_hash="h1")
EMPTY_DEALS = (qs.SlotState(slot="company", empty=False), qs.SlotState(slot="deal", empty=True))


def test_each_kind_on_dewin_shape() -> None:
    found = qs.build(_inputs(slots=EMPTY_DEALS, stages=(DEAL_STAGE,)))
    by = _by_id(found)
    assert {q.kind for q in found} == set(qs.KINDS)
    # purpose: a renamed core entity and a custom module with no purpose word.
    assert by["purpose:Towers"].reason == "open" and by["purpose:Contacts"].label == "Field Sites"
    assert "purpose:Old_Things" not in by  # 0 records, no question (rule 5)
    # key_field: the open parts of each money entry, and no part that code answers.
    assert {i for i in by if i.startswith("key:Invoices")} == {
        "key:Invoices:amount", "key:Invoices:date"}
    assert {i for i in by if i.startswith("key:Payment_Received")} == {
        "key:Payment_Received:amount", "key:Payment_Received:date",
        "key:Payment_Received:status"}
    # slot: Deals is empty, and Invoices fits it.
    slot = by["slot:deal"]
    assert ([c.value for c in slot.choices], slot.records) == (["Invoices"], 0)
    assert slot.why == "Deals holds no records. Invoices could stand in for it."
    # stage_value: one value of the approved stage with no entry.
    stage = by[qs.stage_id("deal", "On Hold")]
    assert (stage.count, stage.value, stage.why) == (
        5, "On Hold", "5 records with this value show as Unsorted.")
    assert [c.value for c in stage.choices] == ["open", "won", "lost"]
    # revenue: an invoice entry and a payment_in entry, each with records.
    revenue = by["revenue"]
    assert [c.value for c in revenue.choices] == ["invoice", "payment_in"]
    assert revenue.choices[1].modules == ("Collection",)
    assert revenue.why == "Each answer about revenue uses this. Until you answer, Metorite uses Billed."


def test_the_first_five_are_revenue_then_the_money_of_invoices_and_collection() -> None:
    """§18.14.7: on DEWiN's shape with the catalogue off."""
    found = qs.build(_inputs(slots=EMPTY_DEALS, stages=(DEAL_STAGE,)))
    assert _ids(found)[:5] == [
        "revenue", "key:Invoices:amount", "key:Invoices:date",
        "key:Payment_Received:amount", "key:Payment_Received:date",
    ]
    # The purposes come after every question of money, the most records first.
    tail = [q.id for q in found if q.kind == "purpose"]
    assert tail == ["purpose:Contacts", "purpose:Towers"]
    assert found[-1].kind == "purpose"


def test_the_sentences_of_why() -> None:
    by = _by_id(qs.build(_inputs()))
    assert by["key:Invoices:amount"].why == (
        "Totals of Invoices use this field. Each answer about revenue uses it too.")
    assert by["key:Payment_Received:amount"].why == "Totals of Collection use this field."
    assert by["key:Payment_Received:date"].why == (
        "Totals of Collection by month or quarter use this date.")
    assert by["key:Payment_Received:status"].why == "Counts of Collection by status use this field."
    assert qs.why_key("parent", "Collection") == "Totals of Collection by customer use this link."
    assert by["purpose:Towers"].why == "Totals and chat answers about Towers wait for this answer."
    # An admin answer of revenue moves the basis.
    items = [{"kind": "catalogue", "module": "@org", "revenue": "payment_in",
              "by": {"revenue": "admin"}}]
    by = _by_id(qs.build(_inputs(items)))
    assert "revenue" not in by
    assert by["key:Payment_Received:amount"].why.endswith("Each answer about revenue uses it too.")


def test_the_choices_of_a_key_part_rank_and_fill() -> None:
    """With no rank, the most filled first and no best guess. With a rank,
    Jev's best first, and a ranked field that is gone drops out."""
    q = _by_id(qs.build(_inputs()))["key:Payment_Received:amount"]
    assert [c.value for c in q.choices] == ["IP_Amount", "EB_Amount", "Rent_Amount"]
    assert q.best() is None and (q.none, q.other) == (True, True)
    assert [o.value for o in q.options] == ["IP_Amount", "Rent_Amount", "EB_Amount"]
    assert q.choices[0].fill == 0.52
    items = [{"kind": "catalogue", "module": "Payment_Received", "amount": None,
              "by": {"amount": "ai"},
              "ranked": {"amount": ["Rent_Amount", "Gone_Field", "IP_Amount"]}}]
    q = _by_id(qs.build(_inputs(items)))["key:Payment_Received:amount"]
    assert q.reason == "unsure"
    assert [(c.value, c.best) for c in q.choices] == [("Rent_Amount", True), ("IP_Amount", False)]


def test_a_purpose_with_no_rank_offers_only_pick_another() -> None:
    q = _by_id(qs.build(_inputs()))["purpose:Towers"]
    assert q.choices == () and q.other and len(q.options) == len(cat.PURPOSES)
    items = [{"kind": "catalogue", "module": "Towers", "purpose": None, "by": {"purpose": "ai"},
              "ranked": {"purpose": ["asset", "reference"]}}]
    q = _by_id(qs.build(_inputs(items)))["purpose:Towers"]
    assert (q.reason, [c.value for c in q.choices], q.best().value) == (
        "unsure", ["asset", "reference"], "asset")


def test_an_admin_answer_is_never_a_question() -> None:
    """§18.14.8: a part with ``by: admin`` is answered, also a null by admin."""
    items = [
        {"kind": "catalogue", "module": "Payment_Received", "amount": None, "date": None,
         "status": None, "by": {"amount": "admin", "date": "admin", "status": "admin"}},
        {"kind": "catalogue", "module": "Towers", "purpose": "other", "by": {"purpose": "admin"}},
    ]
    ids = _ids(qs.build(_inputs(items)))
    assert not [i for i in ids if "Payment_Received" in i or i == "purpose:Towers"]


def test_a_stale_part_is_a_question_again() -> None:
    items = [{"kind": "catalogue", "module": "Invoices", "amount": "Gone_Amount",
              "by": {"amount": "admin"}}]
    q = _by_id(qs.build(_inputs(items)))["key:Invoices:amount"]
    assert q.reason == "stale"


def test_the_pure_module_reads_no_database_and_calls_no_model() -> None:
    tree = ast.parse(PURE.read_text(encoding="utf-8"))
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom | ast.Import)
             for a in n.names}
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not {"sqlalchemy", "acb_llm", "text"} & (names | modules)
    assert "decide" not in PURE.read_text(encoding="utf-8")
    assert "tenant_session" not in PURE.read_text(encoding="utf-8")


# ── Item 9: the gate of ``open`` ─────────────────────────────────────────────


def test_in_on_an_open_part_makes_no_question_and_in_off_it_does() -> None:
    off = _ids(qs.build(_inputs(mode="off")))
    shadow = _ids(qs.build(_inputs(mode="shadow")))
    on = _ids(qs.build(_inputs(mode="on")))
    assert "purpose:Towers" in off and "key:Invoices:amount" in off
    assert off == shadow
    assert not [i for i in on if i.startswith(("purpose:", "key:"))]
    # The AI closed the part as null: now the admin sees it, in `on` too.
    items = [{"kind": "catalogue", "module": "Invoices", "amount": None, "by": {"amount": "ai"}}]
    assert "key:Invoices:amount" in _ids(qs.build(_inputs(items, mode="on")))


def test_a_parked_key_makes_a_question_in_on() -> None:
    src = INVOICES
    key = f"{mp.CATALOGUE_KEY}Invoices"
    entry = next(e for e in cat.build(SOURCES, {}, []) if e.module == "Invoices")
    digest = mp.catalogue_hash([src], "on", entry.purpose)
    cache = mp.Cache(failures={key: {"count": mp.PARK_AFTER, "schema": digest,
                                     "at": (NOW - timedelta(days=1)).isoformat()}})
    ids = _ids(qs.build(_inputs(mode="on", cache=cache)))
    assert "key:Invoices:amount" in ids and "key:Invoices:date" in ids
    assert "key:Payment_Received:amount" not in ids
    # The park ends after PARK_DAYS: the AI asks again, so no question.
    cache.failures[key]["at"] = (NOW - timedelta(days=mp.PARK_DAYS + 1)).isoformat()
    assert "key:Invoices:amount" not in _ids(qs.build(_inputs(mode="on", cache=cache)))


# ── Item 11: the reads of ``slot`` and ``stage_value`` ───────────────────────


def test_a_module_that_the_admin_dismissed_for_a_slot_is_no_candidate() -> None:
    inputs = _inputs(slots=EMPTY_DEALS, slots_digest="d1",
                     dismissed_roles={"deal": ("Invoices", "d1")})
    assert "slot:deal" not in _ids(qs.build(inputs))
    # A changed schema offers it again.
    inputs = _inputs(slots=EMPTY_DEALS, slots_digest="d2",
                     dismissed_roles={"deal": ("Invoices", "d1")})
    assert "slot:deal" in _ids(qs.build(inputs))


def test_the_doubts_of_the_pipeline_come_first_and_a_role_or_suggestion_hides_the_slot() -> None:
    inputs = _inputs(slots=(qs.SlotState(slot="company", empty=True),),
                     doubts={"company": ["Towers", "Clients", "Gone_Module"]})
    q = _by_id(qs.build(inputs))["slot:company"]
    assert (q.reason, [c.value for c in q.choices], q.choices[0].best) == (
        "unsure", ["Towers", "Clients"], True)
    with_role = _inputs(slots=(qs.SlotState(slot="company", empty=True, role="Clients"),))
    assert "slot:company" not in _ids(qs.build(with_role))
    suggested = _inputs(slots=(qs.SlotState(slot="company", empty=True, suggested=True),))
    assert "slot:company" not in _ids(qs.build(suggested))
    # With no doubt, each module whose purpose fits, the most records first.
    plain = _by_id(qs.build(_inputs(slots=(qs.SlotState(slot="company", empty=True),))))
    assert [c.value for c in plain["slot:company"].choices] == ["Clients"]
    assert plain["slot:company"].reason == "open"


def test_a_stage_value_with_an_entry_makes_no_question() -> None:
    stage = qs.StageState(slot="lead", module="Towers", label="Towers", field="Status",
                          values={"New": "open", "Lost": "lost"},
                          seen=(("New", 9), ("Lost", 2), ("Dormant", 1)))
    found = [q for q in qs.build(_inputs(stages=(stage,))) if q.kind == "stage_value"]
    assert [(q.value, q.count, q.why) for q in found] == [
        ("Dormant", 1, "1 record with this value shows as Unsorted.")]
    assert found[0].id == f"stage:lead:{qs.value_hash('Dormant')}"
    assert "Dormant" not in found[0].id


# ── A dismissal holds until the hash changes ────────────────────────────────


def test_a_dismissal_holds_until_the_hash_of_its_question_changes() -> None:
    q = _by_id(qs.build(_inputs()))["purpose:Towers"]
    held = _inputs(dismissed={"purpose:Towers": q.hash})
    assert "purpose:Towers" not in _ids(qs.build(held))
    moved = _inputs(dismissed={"purpose:Towers": "an-older-hash"})
    assert "purpose:Towers" in _ids(qs.build(moved))
    # A new field changes the hash of the module, so the question comes back.
    towers = _module("Towers", "Towers", 805, [_f("Name", "text"), _f("Height", "number")],
                     {"Name": 1.0})
    sources = [s if s.module != "Towers" else towers for s in SOURCES]
    again = _inputs(sources=sources, dismissed={"purpose:Towers": q.hash})
    assert "purpose:Towers" in _ids(qs.build(again))


def test_revenue_takes_no_dismissal() -> None:
    q = _by_id(qs.build(_inputs()))["revenue"]
    assert q.hash is None and qs.wire(q)["dismissible"] is False
    assert "revenue" in _ids(qs.build(_inputs(dismissed={"revenue": None})))


# ── Item 7: the org-level item, and item 5: ``ranked`` ──────────────────────


def test_the_org_item_is_legal_and_makes_no_entry() -> None:
    from gateway.routes.crm.mapping_plans import kind_key

    item = {"kind": "catalogue", "module": "@org", "revenue": "invoice",
            "by": {"revenue": "admin"}}
    assert cat.item_problem(item) is None
    assert kind_key(item) == ("catalogue", "@org")
    assert cat.item_problem({**item, "revenue": "profit"}) == "revenue"
    assert cat.item_problem({**item, "amount": "X"}) == "module"
    assert cat.item_problem({**item, "by": {"amount": "admin"}}) == "by"
    assert cat.item_problem({"kind": "catalogue", "module": "Invoices", "revenue": "invoice"}
                            ) == "revenue"
    assert cat.item_problem({"kind": "catalogue", "module": "@other"}) == "module"
    entries = cat.build(SOURCES, {}, [item])
    assert "@org" not in {e.module for e in entries}
    merged = cat.merge_item(item, {"kind": "catalogue", "module": "@org",
                                   "revenue": "payment_in", "by": {"revenue": "ai"}})
    assert merged["revenue"] == "invoice"  # the admin's answer stays
    assert cat.revenue_of(cat.stored_items([item])) == ("invoice", "admin")
    assert cat.revenue_of({}) == (None, None)


def test_ranked_is_legal_up_to_three_words_for_each_part() -> None:
    base = {"kind": "catalogue", "module": "Invoices", "amount": None, "by": {"amount": "ai"}}
    assert cat.item_problem({**base, "ranked": {"amount": ["A", "B", "C"]}}) is None
    assert cat.item_problem({**base, "ranked": {"amount": ["A", "B", "C", "D"]}}) == "ranked"
    assert cat.item_problem({**base, "ranked": {"revenue": ["invoice"]}}) == "ranked"
    assert cat.item_problem({**base, "ranked": {"amount": [""]}}) == "ranked"
    assert cat.item_problem({**base, "ranked": ["A"]}) == "ranked"


def test_merge_item_carries_ranked_and_drops_the_rank_of_an_admin_part() -> None:
    old = {"kind": "catalogue", "module": "Invoices", "amount": None, "date": None,
           "by": {"amount": "ai", "date": "ai"},
           "ranked": {"amount": ["A", "B"], "date": ["D"]}}
    # The pipeline writes the amount again with a new rank, and leaves the date.
    out = cat.merge_item(old, {"kind": "catalogue", "module": "Invoices", "amount": None,
                               "by": {"amount": "ai"}, "ranked": {"amount": ["B", "A"]}})
    assert out["ranked"] == {"amount": ["B", "A"], "date": ["D"]}
    # A sure answer drops the rank of its part.
    out = cat.merge_item(out, {"kind": "catalogue", "module": "Invoices", "amount": "A",
                               "by": {"amount": "ai"}})
    assert out["ranked"] == {"date": ["D"]}
    # The admin's answer drops its rank, and the pipeline never brings it back.
    out = cat.merge_item(out, {"kind": "catalogue", "module": "Invoices", "date": "D",
                               "by": {"date": "admin"}})
    assert "ranked" not in out
    out = cat.merge_item(out, {"kind": "catalogue", "module": "Invoices", "date": None,
                               "by": {"date": "ai"}, "ranked": {"date": ["X"]}})
    assert out["date"] == "D" and "ranked" not in out
    # A new item of the admin with a rank keeps no rank.
    fresh = cat.merge_item(None, {"kind": "catalogue", "module": "Invoices", "amount": "A",
                                  "by": {"amount": "admin"}, "ranked": {"amount": ["A"]}})
    assert "ranked" not in fresh


def test_clear_parts_puts_back_the_cached_part_or_removes_it() -> None:
    old = {"kind": "catalogue", "module": "Invoices", "amount": "Net_Amount", "date": "X",
           "by": {"amount": "admin", "date": "admin"}}
    cached = {"kind": "catalogue", "module": "Invoices", "amount": None,
              "by": {"amount": "ai"}, "ranked": {"amount": ["Total_Product_Amount"]}}
    out = cat.clear_parts(old, ["amount"], cached)
    assert (out["amount"], out["by"]["amount"], out["ranked"]) == (
        None, "ai", {"amount": ["Total_Product_Amount"]})
    assert out["date"] == "X" and out["by"]["date"] == "admin"
    out = cat.clear_parts(out, ["date"], cached)
    assert "date" not in out and "date" not in out["by"]
    assert cat.clear_parts({"kind": "catalogue", "module": "Towers", "purpose": "asset",
                            "by": {"purpose": "admin"}}, ["purpose"], None) is None


# ── Item 10: the flag ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("catalogue", "questions", "allowed"), [
    ("", "", False), ("*", "", False), ("", "*", False), ("*", "*", True),
])
def test_questions_allowed_is_the_and_of_two_lists(
    monkeypatch, catalogue: str, questions: str, allowed: bool,
) -> None:
    from acb_common import Settings, get_settings
    from gateway.routes.crm import flags

    from tests.unit._crm_fakes import crm_switch_on

    assert Settings.model_fields["crm_questions_orgs"].default == ""
    crm_switch_on(monkeypatch, "*")
    monkeypatch.setattr(get_settings(), "crm_catalogue_orgs", catalogue, raising=False)
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", questions, raising=False)
    assert flags.questions_allowed("a1") is allowed
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "b2, A1", raising=False)
    assert flags.questions_allowed("a1") is (catalogue == "*")
    monkeypatch.setattr(get_settings(), "crm_enabled", False, raising=False)
    assert flags.questions_allowed("a1") is False
    assert flags.questions_allowed(None) is False


# ── The questions step state, and the wire ──────────────────────────────────


def test_the_step_sets_new_at_only_when_an_id_is_new() -> None:
    first = qs.step_state(None, ["a", "b"], True, NOW)
    assert first == {"seen": ["a", "b"], "money": True, "new_at": NOW.isoformat(), "new": 2}
    # An answer closes "a": no new_at.
    later = NOW + timedelta(hours=3)
    assert qs.step_state(first, ["b"], False, later) == {"seen": ["b"], "money": False}
    # A new id sets new_at again.
    again = qs.step_state({"seen": ["b"]}, ["b", "c"], False, later)
    assert (again["new_at"], again["new"]) == (later.isoformat(), 1)


def test_the_wire_of_a_question() -> None:
    by = _by_id(qs.build(_inputs(slots=EMPTY_DEALS, stages=(DEAL_STAGE,))))
    key = qs.wire(by["key:Payment_Received:amount"])
    assert set(key) >= {"id", "kind", "module", "label", "part", "reason", "records", "money",
                        "why", "choices", "none", "other", "current", "options"}
    assert set(key["choices"][0]) == {"value", "label", "fill", "samples", "best"}
    slot = qs.wire(by["slot:deal"])
    assert "fill" not in slot["choices"][0] and slot["choices"][0]["records"] == 30177
    stage = qs.wire(by[qs.stage_id("deal", "On Hold")])
    assert stage["count"] == 5 and "options" not in stage


def test_answered_lists_each_part_in_use_with_current() -> None:
    items = [{"kind": "catalogue", "module": "Payment_Received", "amount": "IP_Amount",
              "by": {"amount": "admin"}},
             {"kind": "catalogue", "module": "@org", "revenue": "payment_in",
              "by": {"revenue": "admin"}}]
    done = _by_id(qs.answered(_inputs(items)))
    assert (done["key:Payment_Received:amount"].current,
            done["key:Payment_Received:amount"].by) == ("IP_Amount", "admin")
    assert done["key:Invoices:status"].by == "code"
    assert (done["revenue"].current, done["revenue"].reason) == ("payment_in", "answered")
    assert done["purpose:Invoices"].current == "invoice"
    # A part that waits for a question is not in use.
    assert "key:Invoices:amount" not in done


# ── CRM-Q32-2: the answer of each kind, pure ────────────────────────────────


def test_the_answer_of_each_kind_and_its_item() -> None:
    by = _by_id(qs.build(_inputs(slots=EMPTY_DEALS, stages=(DEAL_STAGE,))))
    key, purpose = by["key:Payment_Received:amount"], by["purpose:Towers"]
    assert qs.answer_problem(key, "Rent_Amount", COLLECTION) is None
    assert qs.answer_problem(key, None, COLLECTION) is None
    assert qs.answer_problem(key, "Date_of_Payment_Record", COLLECTION) == "bad_answer"
    assert qs.answer_problem(key, "@auto", COLLECTION) == "bad_answer"
    assert qs.answer_problem(purpose, "castle") == "bad_answer"
    assert qs.answer_problem(by["slot:deal"], "Towers") == "bad_answer"
    assert qs.answer_problem(by["slot:deal"], None) is None
    stage = by[qs.stage_id("deal", "On Hold")]
    assert qs.answer_problem(stage, None) == "bad_answer"
    assert qs.answer_problem(stage, "won") is None
    assert qs.answer_problem(by["revenue"], None) == "bad_answer"
    assert qs.answer_item(key, None) == {
        "kind": "catalogue", "module": "Payment_Received", "amount": None,
        "by": {"amount": "admin"}}
    assert qs.answer_item(purpose, None)["purpose"] == "other"
    assert qs.answer_item(by["revenue"], "invoice")["module"] == "@org"
    with pytest.raises(ValueError):
        qs.answer_item(by["slot:deal"], "Invoices")


def test_only_a_ranked_purpose_or_key_field_joins_a_batch() -> None:
    items = [{"kind": "catalogue", "module": "Payment_Received", "amount": None,
              "by": {"amount": "ai"}, "ranked": {"amount": ["IP_Amount"]}}]
    by = _by_id(qs.build(_inputs(items, slots=EMPTY_DEALS, stages=(DEAL_STAGE,))))
    assert qs.batch_joins(by["key:Payment_Received:amount"])
    assert not qs.batch_joins(by["key:Payment_Received:date"])  # no rank
    for qid in ("slot:deal", "revenue", qs.stage_id("deal", "On Hold")):
        assert not qs.batch_joins(by[qid]), qid
    best = by["key:Payment_Received:amount"].best()
    assert qs.applied_line(by["key:Payment_Received:amount"], best) == (
        "Totals of Collection now use IP Amount.")


# ── CRM-Q32-4: the questions step starts after each sync run ────────────────


async def test_the_step_starts_with_each_mapping_mode_off(monkeypatch) -> None:
    """Item 3: ``questions_allowed`` gates the step, and the modes of the
    mapping pipeline do not, because each of them is ``off`` by default."""
    import asyncio

    from acb_common import get_settings
    from gateway.crm_sync import scheduler
    from gateway.routes.crm import questions as route

    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    monkeypatch.setattr(get_settings(), "crm_catalogue_orgs", "*", raising=False)
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "", raising=False)
    ran: list[str] = []

    async def _step(org, *, now=None):
        ran.append(org)
        return 3

    monkeypatch.setattr(route, "questions_after_run", _step)
    monkeypatch.setattr(scheduler, "_questioning", set())
    assert not mp.enabled_for("o-1")
    assert scheduler.start_questions("o-1") is None, "outside the list: no task"
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "*", raising=False)
    task = scheduler.start_questions("o-1")
    assert task is not None and scheduler.start_questions("o-1") is None, "one at a time"
    assert await task == 3 and ran == ["o-1"]
    await asyncio.sleep(0)
    assert "o-1" not in scheduler._questioning
    # It waits for the mapping pipeline of the same run.
    gate = asyncio.Event()

    async def _mapping():
        await gate.wait()

    mapping = asyncio.get_running_loop().create_task(_mapping())
    task = scheduler.start_questions("o-2", after=mapping)
    await asyncio.sleep(0)
    assert ran == ["o-1"], "the step waits for the pipeline"
    gate.set()
    await task
    assert ran == ["o-1", "o-2"]


def test_each_run_starts_the_step_after_the_mapping_pipeline() -> None:
    """Both points that start the steps of a run start the questions step,
    after ``start_mapping`` of the same run."""
    source = (REPO / "apps/services/gateway/gateway/crm_sync/scheduler.py").read_text(
        encoding="utf-8")
    assert source.count("start_questions(org, after=start_mapping(org))") == 1
    assert source.count(
        "start_questions(handle.org, after=start_mapping(handle.org))") == 1


# ── CRM-Q32-6: the 422 of an open key part names where to answer ────────────


def test_a_no_key_field_of_an_open_part_carries_the_href() -> None:
    from fastapi import HTTPException
    from gateway.routes.crm.field_sql import FieldScope

    plain = FieldScope(column="fields", owner="module:Payment_Received", fields={})
    with pytest.raises(HTTPException) as got:
        plain.ref("@amount")
    assert got.value.detail["code"] == "no_key_field" and "href" not in got.value.detail
    asked = FieldScope(column="fields", owner="module:Payment_Received", fields={},
                       questions=frozenset({"amount"}))
    with pytest.raises(HTTPException) as got:
        asked.ref("@amount")
    assert got.value.detail["href"] == "/crm?tab=settings&section=questions"
    assert got.value.detail["message"].endswith(
        "A CRM admin can answer this in CRM settings, Open questions.")
    with pytest.raises(HTTPException) as got:
        asked.ref("@date")
    assert "href" not in got.value.detail


# ── Fix round: Sync health never fails on the open count ────────────────────


async def test_a_failed_read_of_the_open_count_gives_none(monkeypatch) -> None:
    import contextlib

    from acb_common import get_settings
    from gateway.crm_sync import runs
    from gateway.routes.crm import sync

    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    monkeypatch.setattr(get_settings(), "crm_catalogue_orgs", "*", raising=False)
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "*", raising=False)

    class _Db:
        @contextlib.asynccontextmanager
        async def begin_nested(self):
            yield self

    async def broken(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(runs, "read_cursors", broken)
    assert await sync._open_questions(_Db(), "o-1") is None

    async def state(*_a, **_k):
        return {qs.QUESTIONS_ROW: type("Row", (), {"state": {"seen": ["a", "b", "c"]}})()}

    monkeypatch.setattr(runs, "read_cursors", state)
    assert await sync._open_questions(_Db(), "o-1") == 3
    assert qs.open_count(None) is None and qs.open_count({"seen": []}) == 0


# ── Fix round item 7: "an" before a vowel sound ─────────────────────────────

#: The article of each purpose label, written out by hand.
_ARTICLES = {
    "customer_company": "a", "customer_site": "a", "person": "a", "lead": "a",
    "deal": "a", "contract": "a", "invoice": "an", "payment_in": "a",
    "receivable": "a", "payment_out": "a", "product": "a", "asset": "an",
    "vendor": "a", "partner": "a", "activity": "an", "reference": "a", "other": "an",
}


@pytest.mark.parametrize("word", cat.PURPOSES)
def test_the_purpose_line_takes_the_right_article(word: str) -> None:
    q = _by_id(qs.build(_inputs()))["purpose:Towers"]
    choice = next(c for c in q.options if c.value == word)
    noun = qs.PURPOSE_LABELS[word].lower()
    assert qs.applied_line(q, choice) == f"Towers is now {_ARTICLES[word]} {noun} module."


def test_each_purpose_has_its_article_written_out() -> None:
    assert set(_ARTICLES) == set(cat.PURPOSES)
