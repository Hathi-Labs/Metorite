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

#: One exception, by file and by exact name, with its reason: in the action
#: broker the name ``proposal`` is a pending broker action, another store.
_OTHER_MEANING: dict[Path, frozenset[str]] = {
    GATEWAY / "routes/crm/broker_handlers.py": frozenset({"proposal"}),
}

#: A name, an attribute or an import that names a proposal, in any case:
#: ``qs.proposals_of``, ``live_proposals``, ``PROPOSALS``, ``my_proposal``.
_NAME = re.compile(r"(?i)proposal")
#: A string key of the store, in any case (``state.get("proposals")``).
_KEY = re.compile(r"(?i)\bproposals\b")


def _python_files() -> list[Path]:
    out: list[Path] = []
    for root in _SCANNED:
        out.extend([root] if root.is_file() else sorted(root.rglob("*.py")))
    return out


def _names(node: ast.AST) -> list[str]:
    """The identifiers that *node* writes in the code."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        found = [a.name for a in node.names] + [a.asname for a in node.names if a.asname]
        return found + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, ast.keyword) and node.arg:
        return [node.arg]
    return []


def _offenders(source: str, other: frozenset[str] = frozenset()) -> list[str]:
    """Each place of *source* that reads or names the proposals."""
    tree = ast.parse(source)
    docs = {id(body[0].value) for parent in ast.walk(tree)
            for body in [getattr(parent, "body", None)]
            if isinstance(body, list) and body and isinstance(body[0], ast.Expr)}
    out = []
    for node in ast.walk(tree):
        for name in _names(node):
            if _NAME.search(name) and name not in other:
                out.append(f"{node.lineno}:{name}")
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docs and _KEY.search(node.value)):
            out.append(f"{node.lineno}:{node.value[:40]!r}")
    return out


def test_no_module_but_the_two_question_modules_reads_proposals() -> None:
    """Item 15 (R7, a fence): a member's choice must never reach
    ``aggregate.compute``, the digest or another member's run. So no module
    other than ``crm_sync/questions.py`` and ``routes/crm/questions.py``
    names a proposal in its code, in any case: a name, an attribute, an
    import, a function, an argument or a string key."""
    offenders = []
    for path in _python_files():
        if path in _ALLOWED:
            continue
        found = _offenders(path.read_text(encoding="utf-8"),
                           _OTHER_MEANING.get(path, frozenset()))
        offenders.extend(f"{path.relative_to(REPO)}:{x}" for x in found)
    assert offenders == [], offenders
    assert len(_python_files()) > 50  # the scan reads the trees it names


@pytest.mark.parametrize("reader", [
    'x = state.get("proposals")\n',
    'x = state.get("Proposals")\n',
    "from gateway.crm_sync import questions as qs\nx = qs.proposals_of(state)\n",
    "from gateway.crm_sync.questions import live_proposals\n",
    "from gateway.crm_sync.questions import with_proposal as w\n",
    "x = qs.PROPOSALS\n",
    "x = body['questions'][0].my_proposal\n",
])
def test_the_scan_finds_each_kind_of_reader(reader: str) -> None:
    """The fence fails on each reader that the first scan let through: a
    mutation of the rule, kept as a test (fix round of CRM-Q32-8)."""
    assert _offenders(reader)


def test_the_scan_turns_red_on_a_reader_in_the_aggregate() -> None:
    """The mutation of the review: ``qs.proposals_of(...)`` in
    ``routes/crm/aggregate.py`` is an offender."""
    path = GATEWAY / "routes/crm/aggregate.py"
    mutated = path.read_text(encoding="utf-8") + (
        "\n\ndef _leak(state):\n"
        "    from gateway.crm_sync import questions as qs\n\n"
        "    return qs.proposals_of(state)\n")
    assert _offenders(path.read_text(encoding="utf-8")) == []
    assert any("proposals_of" in x for x in _offenders(mutated))


def test_a_docstring_and_another_word_pass() -> None:
    assert _offenders('def f():\n    """It reads no proposals."""\n') == []
    assert _offenders("x = 'proposed_by'\n") == []


def test_only_the_two_showing_reads_load_the_proposals() -> None:
    """``load`` fills ``Inputs.proposals`` only on request. Only the admin
    list and the read of a card ask for them, so the count, the catalogue
    and the 422 of the aggregate never hold a member's choice."""
    import inspect

    from gateway.routes.crm import questions as routes

    assert inspect.signature(routes.load).parameters["proposals"].default is False
    tree = ast.parse((GATEWAY / "routes/crm/questions.py").read_text(encoding="utf-8"))
    askers = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(fn):
            if (isinstance(call, ast.Call) and getattr(call.func, "id", None) == "load"
                    and any(k.arg == "proposals" for k in call.keywords)):
                askers.add(fn.name)
    assert askers == {"list_questions", "ask_questions_read"}


async def test_the_aggregate_path_gets_no_proposals(monkeypatch) -> None:
    """``module_question_ids`` (the catalogue entry and the 422 of the
    aggregate) loads with no proposals, even when the row holds some."""
    from gateway.routes.crm import questions as routes

    seen: list[dict] = []

    async def fake_load(db, org, **kw):
        seen.append(kw)
        return None

    monkeypatch.setattr(routes, "load", fake_load)
    monkeypatch.setattr(routes, "questions_allowed", lambda org: True)
    assert await routes.module_question_ids(object(), "org-1") == {}
    assert await routes.count_open(object(), "org-1") == 0
    assert seen and all(not kw.get("proposals") for kw in seen)


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


def test_the_run_boundary_opens_and_releases_the_holder() -> None:
    """Item 6: the holder opens beside ``refuse_cards_for_run`` and closes in
    the same ``finally``, once for each streamed run."""
    source = (REPO / "apps/services/orchestrator/orchestrator/executor.py").read_text(
        encoding="utf-8")
    assert source.count("open_asks_for_run()") == 1
    assert source.count("release_asks(_asks_token)") == 1
    opened = source.index("_cards_token = refuse_cards_for_run(")
    assert opened < source.index("_asks_token = open_asks_for_run()") < opened + 600
    released = source.index("release_cards_switch(_cards_token)")
    assert released < source.index("release_asks(_asks_token)") < released + 200


def test_the_tool_and_the_gateway_name_one_record_of_a_late_answer() -> None:
    """The gateway writes the record of a late answer, and the tool reads it
    once: one namespace, one key shape (question, member), and the value is
    the key of the option. No CLARIFY block writes a record."""
    import acb_skills.crm_tools as tools
    from gateway.routes.crm import questions as routes

    assert tools.ANSWERED_NAMESPACE == routes.ANSWERED_NAMESPACE == "crm_question_answered"
    assert routes.LATE_ANSWER_TTL_S == 30 * 60
    src = (REPO / "packages/acb_skills/acb_skills/crm_tools.py").read_text(encoding="utf-8")
    assert "getdel(key(ANSWERED_NAMESPACE, question_id, member))" in src
    assert ".setex(" not in src and "crm_question_shown" not in src
    gw = (GATEWAY / "routes/crm/questions.py").read_text(encoding="utf-8")
    assert "key(ANSWERED_NAMESPACE, qid, member.strip().lower())" in gw


def _crm_card(qid: str = "key:A:amount") -> dict:
    return {"request_id": "r1", "questions": [{
        "header": "Collection: amount", "question": "Which field?", "crm_question": qid,
        "options": [{"label": "IP Basic Amount (52% filled)", "key": "IP"},
                    {"label": "None of these", "key": "none"},
                    {"label": "Use the default", "key": "@default"}]}]}


#: The labels that the server's own card of ``key:A:amount`` shows.
_SERVER_LABELS = {"IP": "IP Basic Amount (52% filled)", "none": "None of these",
                  "@default": "Use the default"}


@pytest.fixture
def late_redis(monkeypatch):
    from gateway.routes.crm import questions as routes

    from tests.unit._crm_redis_fake import RawRedis, tenant_client

    raw = RawRedis()
    monkeypatch.setattr(routes, "_redis", lambda: tenant_client(raw))

    async def server_label(org, qid, key):
        return _SERVER_LABELS.get(key) if qid == "key:A:amount" else None

    monkeypatch.setattr(routes, "_server_label", server_label)
    return raw


async def test_two_options_with_one_label_record_nothing(late_redis) -> None:
    """``ask_questions`` cuts a label at 100 characters, so two options can
    show one label. Then the answer names no single option."""
    from gateway.routes.crm import questions as routes

    card = _crm_card()
    card["questions"][0]["options"] = [{"label": "Same", "key": "IP"},
                                       {"label": "Same", "key": "none"}]
    assert not await routes.record_late_answer("org-a", "a@x.test", "questions", card, "Same")
    assert late_redis.store == {}


async def test_a_card_label_that_the_server_would_not_show_records_nothing(late_redis) -> None:
    """A card that a model wrote: "Yes, continue" over a real key. The
    server's own card shows another label for that key, so nothing is
    recorded. A failed check records nothing either."""
    from gateway.routes.crm import questions as routes

    card = _crm_card()
    card["questions"][0]["options"] = [{"label": "Yes, continue", "key": "IP"}]
    assert not await routes.record_late_answer("org-a", "a@x.test", "questions", card,
                                               "Yes, continue")
    assert late_redis.store == {}


async def test_a_failed_label_check_records_nothing(late_redis, monkeypatch) -> None:
    from gateway.routes.crm import questions as routes

    async def broken(org, qid, key):
        raise RuntimeError("db down")

    monkeypatch.setattr(routes, "_server_label", broken)
    assert not await routes.record_late_answer("org-a", "a@x.test", "questions", _crm_card(),
                                               "IP Basic Amount (52% filled)")
    assert late_redis.store == {}


async def test_a_late_answer_records_the_option_that_the_member_picked(late_redis) -> None:
    from gateway.routes.crm import questions as routes

    assert await routes.record_late_answer(
        "org-a", "Asha@X.test", "questions", _crm_card(), "IP Basic Amount (52% filled)")
    assert late_redis.store == {
        "cc:org-a:crm_question_answered:key:A:amount:asha@x.test": (1800, "IP")}


@pytest.mark.parametrize(("kind", "payload", "answer"), [
    # A label that no option has: typed text, or a forged answer.
    ("questions", _crm_card(), "IP"),
    # Another kind of card.
    ("confirmation", _crm_card(), "None of these"),
    # A card of no CRM question.
    ("questions", {"questions": [{"header": "H", "question": "Q?",
                                  "options": [{"label": "A", "key": "IP"}]}]}, "A"),
    # A card of two questions.
    ("questions", {"questions": [_crm_card()["questions"][0]] * 2}, "None of these"),
    # A tag of the wrong shape.
    ("questions", _crm_card("slot:deal"), "None of these"),
    # A payload that the row cut (truncated).
    ("questions", {"request_id": "r1", "question": "Q", "truncated": True}, "None of these"),
])
async def test_a_late_answer_records_nothing_unless_it_picked_an_option_of_a_crm_card(
    late_redis, kind, payload, answer,
) -> None:
    from gateway.routes.crm import questions as routes

    assert not await routes.record_late_answer("org-a", "asha@x.test", kind, payload, answer)
    assert late_redis.store == {}


async def test_a_late_answer_needs_an_org_and_a_member(late_redis) -> None:
    from gateway.routes.crm import questions as routes

    assert not await routes.record_late_answer("", "asha@x.test", "questions", _crm_card(),
                                               "None of these")
    assert not await routes.record_late_answer("org-a", "", "questions", _crm_card(),
                                               "None of these")
    assert late_redis.store == {}


async def test_a_fault_of_redis_never_fails_an_answer_that_committed(late_redis) -> None:
    """The clear of a late answer runs after the commit, and the record runs
    beside the resend. A fault of Redis, an outage or any other, only logs."""
    from gateway.routes.crm import questions as routes
    from redis.exceptions import ConnectionError as RedisDown

    raw = late_redis
    for fault in (RedisDown("down"), RuntimeError("Event loop is closed")):
        raw.fail = fault
        await routes.forget_late_answers("org-a", ["revenue"])
        await routes.forget_late_answers("org-a", ["revenue"], member="a@x.test")
        assert not await routes.record_late_answer("org-a", "a@x.test", "questions",
                                                   _crm_card(), "None of these")
    raw.fail = None
    raw.store = {"cc:org-a:crm_question_answered:revenue:a@x.test": (1, "invoice"),
                 "cc:org-a:crm_question_answered:revenue:b@x.test": (1, "invoice"),
                 "cc:org-a:crm_question_answered:key:A:amount:a@x.test": (1, "IP"),
                 "cc:org-b:crm_question_answered:revenue:a@x.test": (1, "invoice")}
    await routes.forget_late_answers("org-a", ["revenue"])
    assert sorted(raw.store) == ["cc:org-a:crm_question_answered:key:A:amount:a@x.test",
                                 "cc:org-b:crm_question_answered:revenue:a@x.test"]


def test_the_late_answer_route_records_the_act_after_the_row_moves() -> None:
    """The record is written only on the path where the member's own late
    answer moved the row to answered, with the org and the member of the
    session."""
    src = (GATEWAY / "routes/agent.py").read_text(encoding="utf-8")
    moved = src.index("    if moved is None:\n        return None, True\n")
    call = src.index("await record_late_answer(org, me, ")
    assert moved < call < src.index('"agent.pending_ask_answered_late"')
