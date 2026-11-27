"""WS-BRIEF BR-4c — the ``numbers`` section, pure (R7).

Spec: ``project-docs/specs/core_brief.md`` §4.3. Code:
``gateway/brief/gather.py`` (``number_claims``, ``line_claims``), the two
readers of the lines (``run._stored_lead``, ``chat_brief.stored_parts``), the
call site ``run._gather`` and the floor of ``reach.loop.snapshot``. The real database is
``test_brief_numbers_r8.py``.

Each test names the mutation that it catches.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from gateway.brief import gather, run, store
from gateway.reach import loop
from gateway.routes import chat_brief


def card(sid, kind="needs_reply", **kw):
    base = {"signal_id": sid, "app": sid.split(":")[0], "kind": kind, "group": "needs_you",
            "score": 50, "state": "new", "folded": [],
            "href": f"/{sid.split(':')[0]}?x={sid.rsplit(':', 1)[-1]}",
            "title": "T", "facts": {}}
    return {**base, **kw}


def test_three_waiting_threads_make_one_count_claim():
    """Catches a count with the wrong value, kind, section or ids."""
    cards = [card(f"email:acc:t{i}") for i in range(3)]
    got = gather.number_claims(cards, 4)
    assert len(got) == 1
    c = got[0]
    assert (c.section, c.source_kind, c.value, c.value_path, c.position) == (
        "numbers", "count", 3, "count", 4)
    assert sorted(c.source_ids) == [f"email:acc:t{i}" for i in range(3)]
    assert c.source_id in c.source_ids and c.source_app == "email"
    assert c.href.startswith("/email") and c.text == "3 threads wait for your reply"


def test_the_three_counts_of_the_spec_in_order():
    """Catches a missing count of tasks due today, a count that mixes two
    kinds, and another order."""
    cards = [card("tasks:9", "promise_due"), card("tasks:7", "due_today"),
             card("tasks:8", "due_today"), card("email:acc:t1"),
             card("tasks:6", "overdue")]
    got = gather.number_claims(cards, 0)
    assert [(c.text, c.value, c.source_app, c.position) for c in got] == [
        ("1 thread waits for your reply", 1, "email", 0),
        ("2 tasks are due today", 2, "tasks", 1),
        ("1 promise you made falls due", 1, "tasks", 2)]
    assert len(got) <= gather.MAX_NUMBERS == 3


def test_a_folded_kind_counts():
    """A promise folds into the card of its task (my_day_feed.md §4.5).
    Catches a count that reads the main kind only."""
    cards = [card("tasks:7", "due_today", folded=["promise_due"]),
             card("tasks:8", "overdue", folded=["promise_due"])]
    got = {c.text: c.source_ids for c in gather.number_claims(cards, 0)}
    assert got == {"1 task is due today": ("tasks:7",),
                   "2 promises you made fall due": ("tasks:7", "tasks:8")}


def test_the_link_is_the_first_record_in_the_feed_order():
    """Catches a link that follows the order of the list."""
    cards = [card("email:acc:low", score=10), card("email:acc:high", score=90),
             card("email:acc:mid", score=40)]
    for these in (cards, cards[::-1]):
        c = gather.number_claims(these, 0)[0]
        assert c.source_id == "email:acc:high" and c.href == "/email?x=high"
        assert c.source_ids[0] == "email:acc:high"
        assert sorted(c.source_ids) == ["email:acc:high", "email:acc:low", "email:acc:mid"]


def test_a_count_with_no_usable_card_is_skipped():
    """Catches a count that has no source id or no href."""
    cards = [card("email:acc:t1", href="//evil.example"), card("nobody:1"),
             card("email:acc:t2", href=""), card("email:acc:t3", state="dismissed"),
             card("email:acc:t4", group="later")]
    assert gather.number_claims(cards, 0) == []
    assert gather.number_claims([], 0) == []


def test_a_card_that_cannot_open_is_not_counted():
    """Catches a value that is higher than the ids it lists."""
    cards = [card("email:acc:t1"), card("email:acc:t2", href="/api/x")]
    got = gather.number_claims(cards, 0)
    assert got[0].value == 1 and got[0].source_ids == ("email:acc:t1",)


def test_the_ids_are_cut_at_fifty_and_the_value_is_not():
    # 60 is also more than the cut of ``feed_rank.order`` for one app (15):
    # catches a value that reads the cut list.
    cards = [card(f"email:acc:t{i}") for i in range(60)]
    c = gather.number_claims(cards, 0)[0]
    assert c.value == 60 and len(c.source_ids) == store.MAX_SOURCE_IDS


def test_a_cut_source_says_at_least():
    """The feed cuts each source, so the cards are then a floor. Catches an
    exact claim over a cut read, and a floor on the wrong source."""
    cards = [card("email:acc:t1"), card("tasks:7", "due_today"),
             card("tasks:9", "promise_due")]
    got = gather.number_claims(cards, 0, frozenset({"email", "email_followups"}))
    assert [c.text for c in got] == [
        "At least 1 thread waits for your reply", "1 task is due today",
        "At least 1 promise you made falls due"]
    assert gather.number_claims(cards, 0, frozenset({"tasks"}))[1].text == (
        "At least 1 task is due today")


def test_the_snapshot_names_the_cut_sources(monkeypatch):
    """The mail count past the cap sets ``total`` and not ``capped``.
    Catches a floor that reads ``capped`` only."""
    from gateway import my_day_loop
    from gateway.routes.shell import feed

    found = {"email": SimpleNamespace(total=40, rows=[1] * 15),
             "projects": SimpleNamespace(total=1, rows=[1]),
             "tasks": SimpleNamespace(total=15, rows=[1] * 15)}
    day = SimpleNamespace(feed_day=lambda now: date(2026, 10, 15))

    async def cards(user, now=None, **kw):
        return SimpleNamespace(cards=[], now=now, found=found, capped=["tasks"],
                               sources={}, joined=SimpleNamespace(day=day, ok=True))

    async def working(user, d):
        return True

    monkeypatch.setattr(feed, "feed_cards", cards)
    monkeypatch.setattr(feed, "_working_day", working)
    monkeypatch.setattr(my_day_loop, "_read_day", None)
    snap = asyncio.run(loop.snapshot("o", object(), datetime(2026, 10, 15, tzinfo=UTC)))
    assert snap.capped == frozenset({"email", "tasks"}) and snap.complete


def _stored(position, section, text):
    return store.Claim(id=f"id{position}", run_id="r", position=position, section=section,
                       text=text, source_app="email", source_kind="k", source_id="email:a:t",
                       source_ids=(), href="/email", value=None, value_path=None,
                       fingerprint=None)


def _run_row(lead):
    return SimpleNamespace(lead=lead, local_date=date(2026, 10, 15))


def test_a_count_is_no_chat_pill_and_no_more_note():
    """Catches a count that shows as a pill, or that lowers the "more"
    count of the lead."""
    claims = [_stored(0, "needs_you", "A line"),
              _stored(1, "numbers", "9 threads wait for your reply")]
    lead = "Your day, Thu 15 Oct. 9 things need you."
    _, parts = chat_brief.stored_parts(_run_row(lead), claims)
    assert parts.lines == ["A line"] and parts.rest == 8
    assert chat_brief.stored_parts(_run_row(lead), claims[1:]) is None


def test_a_count_is_no_line_of_a_recovered_lead():
    """Catches "2 things need you" for one line and one count."""
    claims = [_stored(0, "needs_you", "A line"),
              _stored(1, "numbers", "1 thread waits for your reply")]
    got = run._stored_lead(_run_row(None), claims)
    assert got == "Your day, Thu 15 Oct. 1 thing needs you."
    assert run._stored_lead(_run_row(None), claims[1:]) is None


# ── The real gather path (``run._gather``), with no database ───────────────

NOW = datetime(2026, 10, 15, 2, 0, tzinfo=UTC)


def waiting(n):
    """*n* threads that wait, each with the facts that make a line."""
    facts = {"who": "Priya", "waiting_since": (NOW - timedelta(minutes=3)).isoformat()}
    return [card(f"email:acc:t{i}", facts=facts, why="W", at=None, fingerprint="fp000001")
            for i in range(n)]


def gather_run(monkeypatch, cards, *, capped=frozenset(), compose=False, answer=None):
    """Run ``run._gather`` on *cards*. Only the snapshot, the session, the
    queue, the store writes and the model call are fakes. Answers the stored
    claims, the prompts of the model and the model lead."""
    from acb_llm import routed
    from gateway.brief import flags, phrase
    from gateway.jobs import queue

    seen = SimpleNamespace(claims=[], prompts=[], model_lead=None)

    async def snapshot(org, user, now=None):
        return loop.Snapshot(list(cards), now, None, True, True, capped)

    @contextlib.asynccontextmanager
    async def session(org):
        yield None

    async def yes(*a, **k):
        return True

    async def add_claims(db, member, run_id, claims):
        seen.claims = list(claims)
        return []

    async def read_claims(db, member, run_id):
        return [store.Claim(id=f"id{c.position}", run_id=run_id, position=c.position,
                            section=c.section, text=c.text, source_app=c.source_app,
                            source_kind=c.source_kind, source_id=c.source_id,
                            source_ids=c.source_ids, href=c.href, value=c.value,
                            value_path=c.value_path, fingerprint=c.fingerprint)
                for c in seen.claims]

    async def set_lead(db, member, run_id, lead, *, source, claim_ids=()):
        if source == "model":
            seen.model_lead = lead

    async def ask(member, messages, ref):
        seen.prompts.append(messages)
        return answer

    async def block(db, org, member):
        return ""

    monkeypatch.setattr(loop, "snapshot", snapshot)
    monkeypatch.setattr(run, "tenant_session", session)
    monkeypatch.setattr(run, "_block", block)
    monkeypatch.setattr(queue, "hold", yes)
    monkeypatch.setattr(queue, "complete", yes)
    monkeypatch.setattr(store, "add_claims", add_claims)
    monkeypatch.setattr(store, "read_claims", read_claims)
    monkeypatch.setattr(store, "set_state", yes)
    monkeypatch.setattr(store, "set_lead", set_lead)
    monkeypatch.setattr(store, "mark_phrasing", yes)
    monkeypatch.setattr(flags, "compose", lambda org: compose)
    monkeypatch.setattr(flags, "in_app", lambda org: False)
    monkeypatch.setattr(routed, "routing_is_on", lambda: True)
    monkeypatch.setattr(phrase, "_ask", ask)
    row = SimpleNamespace(id="r", kind="brief", local_date=date(2026, 10, 15))
    got = asyncio.run(run._gather("o", "a@b.test", object(), row, object(), NOW, NOW))
    assert got == "ready"
    return seen


def _counts(seen):
    return [c.text for c in seen.claims if c.section == "numbers"]


def test_the_gather_adds_the_counts_and_the_floor_of_a_cut_source(monkeypatch):
    """Catches a run with no count (no ``number_claims`` line), and a call
    with no ``snap.capped``, which states a floor as the exact count."""
    assert _counts(gather_run(monkeypatch, waiting(3))) == ["3 threads wait for your reply"]
    cut = gather_run(monkeypatch, waiting(3), capped=frozenset({"email"}))
    assert _counts(cut) == ["At least 3 threads wait for your reply"]


@pytest.mark.parametrize("lines,calls", [(3, 0), (4, 1)])
def test_a_count_adds_nothing_to_the_phrase_threshold(monkeypatch, lines, calls):
    """Catches a call site whose threshold counts the ``numbers`` claims:
    3 lines and 1 count are not 4 claims."""
    seen = gather_run(monkeypatch, waiting(lines), compose=True, answer="{}")
    assert len(seen.claims) == lines + 1 and len(seen.prompts) == calls


def test_the_model_never_gets_a_count(monkeypatch):
    """The fence checks numbers only, so a cited count could lose its "At
    least". Catches a call that passes the stored claims with the counts:
    the count is in the prompt, and the sentence passes the fence."""
    said = {"sentences": [{"text": "11 threads wait for your reply.", "claims": ["c8"]}]}
    seen = gather_run(monkeypatch, waiting(11), capped=frozenset({"email"}),
                      compose=True, answer=json.dumps(said))
    lines = gather.line_claims(seen.claims)
    assert len(lines) == 7 and not any("11" in c.text for c in lines)
    assert _counts(seen) == ["At least 11 threads wait for your reply"]
    prompt = "\n".join(m["content"] for m in seen.prompts[0])
    assert "[c7]" in prompt and "[c8]" not in prompt
    assert "threads wait for your reply" not in prompt and "value:" not in prompt
    assert seen.model_lead is None
