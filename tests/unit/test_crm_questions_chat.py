"""WS-53 CRM-Q32-8 — the chat asks before it answers, hermetic half.

Spec: ``project-docs/specs/crm_platform.md`` §18.14.10, CRM-Q32-8 (owner
direction 2026-10-18, two parts). An admin's pick is the org fact. Each other
member's pick is a PROPOSAL: it changes that member's answer only, for an
open question, and a CRM admin confirms it.

This file holds the pure rules of ``crm_sync/questions.py`` and the fences
that read source:

* Item 1: the 422 ``no_key_field`` of an open part names the question.
* Item 11: 5 proposals for each question, the newest, one for each member.
* Item 15: no module other than the two question modules reads
  ``proposals`` (a Python scan), so the aggregate and the digest never see a
  member's choice.
* Item 17: ``proposals_seen``, ``proposed`` and the card of a proposal.
* Item 17a: the step never returns ``proposals``.
* Item 17b: a proposal that equals the answer in use drops.
* Item 23: no CRM-Q32-8 module calls ``decide``.

``test_crm_questions_chat_r8.py`` proves the routes, the lock and the
tenancy on a real Postgres. ``test_crm_agent.py`` holds the tool half.
"""

from __future__ import annotations

import ast
import re
from datetime import timedelta
from pathlib import Path

import pytest
from gateway.crm_sync import questions as qs

from tests.unit.test_crm_questions import NOW, _by_id, _inputs

REPO = Path(__file__).resolve().parents[2]
GATEWAY = REPO / "apps/services/gateway/gateway"


# ── Item 1: the 422 of an open part names the question ──────────────────────


def test_a_no_key_field_of_an_open_part_names_its_question() -> None:
    from fastapi import HTTPException
    from gateway.routes.crm.field_sql import FieldScope

    asked = FieldScope(column="fields", owner="module:Payment_Received", fields={},
                       questions=frozenset({"amount"}))
    with pytest.raises(HTTPException) as got:
        asked.ref("@amount")
    assert got.value.detail["question"] == "key:Payment_Received:amount"
    assert got.value.detail["href"] == "/crm?tab=settings&section=questions"
    # A part that is no open question names none.
    with pytest.raises(HTTPException) as got:
        asked.ref("@date")
    assert "question" not in got.value.detail


# ── Item 11: the store keeps 5, the newest, one for each member ─────────────


def test_a_second_proposal_of_one_member_replaces_the_first() -> None:
    entries = qs.with_proposal([], "IP", "Asha@X.test", "2026-10-18T09:00:00+00:00")
    entries = qs.with_proposal(entries, "Rent", "bala@x.test", "2026-10-18T09:01:00+00:00")
    entries = qs.with_proposal(entries, "EB", "asha@x.test", "2026-10-18T09:02:00+00:00")
    assert [(p["member"], p["value"]) for p in entries] == [
        ("bala@x.test", "Rent"), ("asha@x.test", "EB")]


def test_the_store_keeps_the_five_newest() -> None:
    entries: list[dict] = []
    for i in range(7):
        entries = qs.with_proposal(entries, f"F{i}", f"m{i}@x.test",
                                   f"2026-10-18T09:0{i}:00+00:00")
    assert [p["member"] for p in entries] == [f"m{i}@x.test" for i in range(2, 7)]
    assert len(entries) == qs.PROPOSALS_KEEP == 5


def test_a_proposal_that_does_not_parse_is_left_out() -> None:
    state = {qs.PROPOSALS: {"key:A:amount": [{"value": "IP", "member": "a@x.test", "at": "t"},
                                              {"value": 3, "member": "b@x.test"},
                                              "junk", {"value": "X"}],
                            "key:B:amount": "junk"}}
    assert qs.proposals_of(state) == {
        "key:A:amount": [{"value": "IP", "member": "a@x.test", "at": "t"}]}


# ── Item 17b: a proposal that equals the answer in use drops ────────────────


def test_a_proposal_equal_to_the_answer_in_use_drops() -> None:
    items = [{"kind": "catalogue", "module": "Payment_Received", "amount": "IP_Amount",
              "by": {"amount": "admin"}}]
    inputs = _inputs(items)
    done = _by_id(qs.answered(inputs))["key:Payment_Received:amount"]
    entries = [{"value": "IP_Amount", "member": "a@x.test", "at": "1"},
               {"value": "Rent_Amount", "member": "b@x.test", "at": "2"}]
    assert [p["value"] for p in qs.live_proposals(entries, done)] == ["Rent_Amount"]
    assert qs.equals_current(done, "IP_Amount") and not qs.equals_current(done, "Rent_Amount")
    # On an open question each proposal is live, and none equals an answer.
    open_q = _by_id(qs.build(_inputs()))["key:Payment_Received:amount"]
    assert len(qs.live_proposals(entries, open_q)) == 2
    assert not qs.equals_current(open_q, None)


# ── The key of an option: the server maps it ────────────────────────────────


def test_a_key_that_no_option_has_maps_to_nothing() -> None:
    q = _by_id(qs.build(_inputs()))["key:Payment_Received:amount"]
    assert qs.chat_choice(q, "IP_Amount") == (True, "IP_Amount")
    assert qs.chat_choice(q, "none") == (True, None)
    for bad in ("Invented_Field", "@auto", "@default", "", "Date_of_Payment_Record"):
        assert qs.chat_choice(q, bad) == (False, None), bad
    revenue = _by_id(qs.build(_inputs()))["revenue"]
    assert qs.chat_choice(revenue, "payment_in") == (True, "payment_in")
    # Revenue has a default, so "None of these" is no option of it.
    assert qs.chat_choice(revenue, "none") == (False, None)


def test_the_override_of_an_amount_is_a_sum_of_the_field() -> None:
    """⚠️ ``sum_amount`` on an overlay does not read ``field``, so the
    override is always ``measure='sum'`` with ``field=<key>``."""
    by = _by_id(qs.build(_inputs()))
    over = qs.override_of(by["key:Payment_Received:amount"], "IP_Amount", {})
    assert over == {"part": "amount", "word": "@amount", "field": "IP_Amount",
                    "label": "IP Amount", "measure": "sum"}
    date = qs.override_of(by["key:Payment_Received:date"], "Date_of_Payment_Record", {})
    assert "measure" not in date and date["word"] == "@date"
    revenue = qs.override_of(by["revenue"], "payment_in",
                             {"Payment_Received": "payment_in", "Invoices": "invoice"})
    assert revenue == {"revenue": "payment_in", "label": "Collected",
                       "modules": ["Payment_Received"]}


def test_the_card_copy_asks_a_full_question_and_never_names_the_engine() -> None:
    by = _by_id(qs.build(_inputs()))
    assert qs.ask_sentence(by["key:Payment_Received:amount"]) == (
        "Which field holds the amount of each record in Collection?")
    assert qs.ask_header(by["key:Payment_Received:amount"]) == "Collection: amount"
    for q in by.values():
        assert "Jev" not in qs.ask_sentence(q) and "Jev" not in qs.ask_header(q)


# ── Item 17: the step stores proposals_seen, and a proposal raises the card ─


def _state(*proposals: tuple[str, str, str], **more) -> dict:
    found: dict[str, list] = {}
    for qid, member, at in proposals:
        found.setdefault(qid, []).append({"value": "IP", "member": member, "at": at})
    return {qs.PROPOSALS: found, **more}


def test_a_new_proposal_sets_new_at_and_proposed() -> None:
    state = _state(("key:A:amount", "a@x.test", "t1"), seen=["key:A:amount"])
    keys = qs.step_state(state, ["key:A:amount"], True, NOW)
    assert keys["proposals_seen"] == ["key:A:amount|a@x.test|t1"]
    assert (keys["new_at"], keys["new"], keys["proposed"]) == (NOW.isoformat(), 0, 1)
    # The next step sees the same proposal: it is not new.
    later = NOW + timedelta(hours=1)
    again = qs.step_state({**state, **keys}, ["key:A:amount"], True, later)
    assert "new_at" not in again and "proposed" not in again
    # A second member proposes: new again.
    more = _state(("key:A:amount", "a@x.test", "t1"), ("key:A:amount", "b@x.test", "t2"),
                  seen=["key:A:amount"], proposals_seen=keys["proposals_seen"])
    assert qs.step_state(more, ["key:A:amount"], True, later)["proposed"] == 1


def test_the_step_and_seen_keys_never_return_proposals() -> None:
    """Item 17a: only the reply route writes ``proposals``."""
    state = _state(("key:A:amount", "a@x.test", "t1"))
    assert qs.PROPOSALS not in qs.step_state(state, ["key:A:amount"], False, NOW)
    assert qs.PROPOSALS not in qs.step_state(None, [], False, NOW)
    q = _by_id(qs.build(_inputs()))["key:Payment_Received:amount"]
    assert qs.PROPOSALS not in qs.seen_keys([q])


def test_card_of_raises_a_card_for_a_proposal_on_an_answered_part() -> None:
    """A proposal on a part in use: no question is open, and the card still
    reaches the admin, once a day at most (the window of ``new_at``)."""
    state = {"seen": [], "new_at": NOW.isoformat(), "new": 0, "proposed": 2, "money": False}
    card = qs.card_of(state, NOW + timedelta(hours=2))
    assert card is not None and (card["count"], card["open"], card["proposed"]) == (0, 0, 2)
    assert qs.card_of(state, NOW + timedelta(hours=qs.CARD_HOURS)) is None
    # No question and no proposal: no card.
    assert qs.card_of({**state, "proposed": 0}, NOW) is None
    # A row of a step before CRM-Q32-8 keeps its count.
    old = {"seen": ["a", "b"], "new_at": NOW.isoformat(), "money": True}
    assert qs.card_of(old, NOW)["count"] == 2


# ── Item 15: only the two question modules read `proposals` ─────────────────


#: The trees a scan reads: the CRM, My Day, the org facts, and the tools.
_SCANNED = (GATEWAY / "crm_sync", GATEWAY / "routes/crm", GATEWAY / "routes/shell",
            GATEWAY / "org_facts.py", GATEWAY / "routes/org_facts.py",
            REPO / "packages/acb_skills/acb_skills")

#: The two modules that may read the proposals of the members.
_ALLOWED = {GATEWAY / "crm_sync/questions.py", GATEWAY / "routes/crm/questions.py"}

_WORD = re.compile(r"\bproposals\b")


def _python_files() -> list[Path]:
    out: list[Path] = []
    for root in _SCANNED:
        out.extend([root] if root.is_file() else sorted(root.rglob("*.py")))
    return out


def test_no_module_but_the_two_question_modules_reads_proposals() -> None:
    """Item 15 (R7, a fence): a member's choice must never reach
    ``aggregate.compute``, the digest or another member's run. So no module
    other than ``crm_sync/questions.py`` and ``routes/crm/questions.py``
    names ``proposals`` in its code: a string, a name or an attribute."""
    offenders = []
    for path in _python_files():
        if path in _ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            text = (node.value if isinstance(node, ast.Constant) and isinstance(node.value, str)
                    else node.attr if isinstance(node, ast.Attribute)
                    else node.id if isinstance(node, ast.Name) else None)
            if text is not None and _WORD.search(text) and not _is_docstring(tree, node):
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert offenders == [], offenders
    assert len(_python_files()) > 50  # the scan reads the trees it names


def _is_docstring(tree: ast.AST, node: ast.AST) -> bool:
    for parent in ast.walk(tree):
        body = getattr(parent, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and body[0].value is node):
            return True
    return False


def test_the_scan_finds_a_reader() -> None:
    """The fence fails on a reader: a mutation of the rule, kept as a test."""
    tree = ast.parse('x = state.get("proposals")\n')
    found = [n for n in ast.walk(tree) if isinstance(n, ast.Constant)
             and isinstance(n.value, str) and _WORD.search(n.value)]
    assert found
    # `proposals_seen` and `my_proposal` are other words, so they pass.
    assert not _WORD.search("proposals_seen") and not _WORD.search("my_proposal")


# ── Item 23: no decide call ─────────────────────────────────────────────────


@pytest.mark.parametrize("path", [
    GATEWAY / "crm_sync/questions.py", GATEWAY / "routes/crm/questions.py",
    REPO / "packages/acb_skills/acb_skills/crm_tools.py",
])
def test_no_module_of_the_chat_card_calls_decide(path: Path) -> None:
    """The question, the choices and the samples come from the read of
    CRM-Q32-1, so the card costs no model call. The R8 suite counts the calls
    of a fake ``decide`` through the routes too."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
               for a in n.names}
    assert "decide" not in names | attrs | imports


def test_the_proposal_time_is_the_clock_of_the_server() -> None:
    """The member and the time come from the server, never from input: the
    reply body has one field, the key."""
    from gateway.routes.crm.questions import ReplyIn
    from pydantic import ValidationError

    assert set(ReplyIn.model_fields) == {"choice"}
    with pytest.raises(ValidationError):
        ReplyIn(choice="IP", member="x@y.test")  # extra="forbid"
