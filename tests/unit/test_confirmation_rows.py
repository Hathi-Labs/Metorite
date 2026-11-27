"""A confirmation card with ROWS: one card, one checkbox per row, one Approve.

WS-46 P13, the one-card version. The owner asked for several tasks on ONE
card. ``request_confirmation`` takes optional ``rows=[{id, label, hint,
checked}]``. The card draws a checkbox per row, and Approve answers
``APPROVE {"rows": [...]}`` in the one respond-input. The call then returns
the ``frozenset`` of the ticked ids.

Each rule names the mutation that turns it red (R7):

1. **A card with no rows is unchanged**: the same event keys, a plain
   ``APPROVE`` answer, a ``bool`` result and the same closing event.
   Mutation: put ``rows`` on every event, or return a set for a plain card
   -> ``test_a_card_with_no_rows_is_unchanged``.
2. **The answer names the ticked rows, and only offered rows.** A forged id
   refuses the WHOLE answer, never a trimmed one. Mutation: intersect the
   answer with the offer -> ``test_a_forged_id_refuses_the_whole_answer``.
3. **Zero ticked rows approve nothing,** and a plain ``APPROVE`` on a rows
   card approves nothing. Mutation: read an empty list as every row ->
   ``test_zero_ticked_rows_approve_nothing``.
4. **The closing event still fires** (#687), with APPROVE or REJECT.
5. **The client builds the answer the server reads.** This file reads
   ``lib/confirmationQueue.ts``, so a drift in either fails here.
"""
from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import pytest
from orchestrator import executor

at = importlib.import_module("acb_skills.ask_tools")

ROWS = [
    {"id": "row-1", "label": "Book the caterer", "hint": "Priya · due Fri 9 Oct", "checked": True},
    {"id": "row-2", "label": "Print the badges", "checked": True},
    {"id": "row-3", "label": "Test the projector", "hint": "made 3 min ago", "checked": False},
]
QUEUE_TS = (
    Path(__file__).resolve().parents[2]
    / "workbench" / "control_plane" / "src" / "lib" / "confirmationQueue.ts"
)


def _ask(answer: str, rows: list[dict] | None = ROWS) -> tuple[object, dict, dict]:
    """One card on the native queue path, answered with *answer*."""
    async def _run():
        q: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(q)
        # The thread that owns the card: only it may answer (H-201).
        executor._stream_relay_thread_id.set("t-rows")
        kwargs = {} if rows is None else {"rows": rows}
        task = asyncio.ensure_future(
            at.request_confirmation("Create 3 tasks in «Ops»?", "one batch", "project: «Ops»",
                                    **kwargs)
        )
        card = await asyncio.wait_for(q.get(), timeout=2)
        rid = card["value"]["request_id"]
        assert executor.resolve_user_input(
            rid, answer, was_freeform=False, thread_id=executor._pending_user_input.owner_of(rid),
        )
        result = await asyncio.wait_for(task, timeout=2)
        return result, card, q.get_nowait()

    return asyncio.run(_run())


def _rows_answer(ids: list[str]) -> str:
    return at.ROWS_ANSWER_PREFIX + json.dumps({"rows": ids})


# ── 1. A card with no rows is unchanged ─────────────────────────────────────


@pytest.mark.parametrize("answer, approved", [("APPROVE", True), ("REJECT", False)])
def test_a_card_with_no_rows_is_unchanged(answer, approved):
    result, card, closed = _ask(answer, rows=None)
    assert result is approved, "a plain card still answers a bool"
    assert set(card["value"]) == {"title", "detail", "context", "request_id"}
    assert closed["name"] == at.CONFIRMATION_RESOLVED
    assert closed["value"]["answer"] == answer


def test_a_plain_card_ignores_a_rows_answer():
    """A plain card reads only a plain APPROVE, as it did before."""
    result, _card, closed = _ask(_rows_answer(["row-1"]), rows=None)
    assert result is False and closed["value"]["answer"] == "REJECT"


# ── 2. The answer names the ticked rows, and only offered rows ──────────────


def test_the_event_carries_the_rows_and_the_answer_names_the_ticked_ones():
    result, card, closed = _ask(_rows_answer(["row-1", "row-3"]))
    assert result == frozenset({"row-1", "row-3"})
    assert [r["id"] for r in card["value"]["rows"]] == ["row-1", "row-2", "row-3"]
    assert [r["checked"] for r in card["value"]["rows"]] == [True, True, False]
    assert card["value"]["rows"][1]["hint"] == ""
    assert closed["value"]["answer"] == "APPROVE"


def test_a_forged_id_refuses_the_whole_answer():
    """Mutation caught: trimming the answer to the ids the card offered."""
    result, _card, closed = _ask(_rows_answer(["row-1", "row-9"]))
    assert result == frozenset()
    assert not result
    assert closed["value"]["answer"] == "REJECT"


# ── 3. Zero ticked rows approve nothing ─────────────────────────────────────


@pytest.mark.parametrize(
    "answer",
    [_rows_answer([]), "APPROVE", "APPROVE not json", 'APPROVE {"rows": "row-1"}', "REJECT"],
    ids=["empty", "plain", "not-json", "not-a-list", "reject"],
)
def test_zero_ticked_rows_approve_nothing(answer):
    result, _card, closed = _ask(answer)
    assert result == frozenset() and not result
    assert closed["value"]["answer"] == "REJECT"


def test_no_channel_fails_closed_with_rows(monkeypatch):
    async def _run():
        executor._active_run_queue.set(None)
        monkeypatch.setattr(executor, "resolve_relay_thread_id", lambda: None)
        denied = await at.request_confirmation("t", rows=ROWS)
        allowed = await at.request_confirmation("t", rows=ROWS, non_interactive_default="approve")
        return denied, allowed

    denied, allowed = asyncio.run(_run())
    assert denied == frozenset()
    assert allowed == frozenset({"row-1", "row-2"})


@pytest.mark.parametrize(
    "rows",
    [[], [{"id": "a"}, {"id": "a"}], [{"label": "no id"}], [{"id": "x" * 65}],
     [{"id": f"r{i}"} for i in range(at.MAX_CARD_ROWS + 1)]],
    ids=["empty", "duplicate", "no-id", "long-id", "too-many"],
)
def test_bad_rows_raise_before_any_card(rows):
    with pytest.raises(ValueError):
        at.clean_card_rows(rows)


# ── 5. The client builds the answer the server reads ────────────────────────


def test_the_client_answer_is_the_one_the_server_reads():
    """Mutation caught: a prefix or a key changed on one side only."""
    ts = QUEUE_TS.read_text(encoding="utf-8")
    assert f'export const ROWS_ANSWER_PREFIX = "{at.ROWS_ANSWER_PREFIX}";' in ts
    assert "JSON.stringify({ rows: ids })" in ts
    assert f"export const MAX_CARD_ROWS = {at.MAX_CARD_ROWS};" in ts
    offered = frozenset({"row-1", "row-2"})
    # What `approveRows(["row-2"])` sends: the prefix, then JSON.stringify.
    assert at.ticked_rows('APPROVE {"rows":["row-2"]}', offered) == frozenset({"row-2"})


def test_a_deeply_nested_answer_approves_nothing():
    """Review round 1: the parse of a nested answer raises RecursionError, and
    the card still closes as a REJECT with no row."""
    # Inside the length bound, so the parse itself meets the nesting.
    nested = at.ROWS_ANSWER_PREFIX + "[" * (at.MAX_ROWS_ANSWER - len(at.ROWS_ANSWER_PREFIX))
    assert len(nested) <= at.MAX_ROWS_ANSWER
    result, _card, closed = _ask(nested)
    assert result == frozenset()
    assert closed["value"]["answer"] == "REJECT"


def test_an_answer_too_long_or_too_many_ids_approves_nothing():
    """PR #691 review: the answer is bounded before the parse. Mutations:
    drop the length check, or the count check -> this test fails."""
    offered = frozenset({"row-1", "row-2"})
    # Valid JSON, padded inside the object, so only the length refuses it.
    long = at.ROWS_ANSWER_PREFIX + '{"rows": ["row-1"]' + " " * at.MAX_ROWS_ANSWER + "}"
    assert at.ticked_rows(long, offered) is None
    many = _rows_answer(["row-1", "row-2", "row-1"])
    assert at.ticked_rows(many, offered) is None
    assert at.ticked_rows(_rows_answer(["row-1", "row-2"]), offered) == offered
    # The largest honest answer fits under the bound.
    ids = [f"r{'x' * 62}{i:02d}"[:64] for i in range(at.MAX_CARD_ROWS)]
    assert len(_rows_answer(ids)) <= at.MAX_ROWS_ANSWER
    result, _card, closed = _ask(long)
    assert result == frozenset() and closed["value"]["answer"] == "REJECT"


# ── 6. The wait of a card (SBX-3b, ``agent_sandbox.md`` §12.5 done-when 22) ──


#: The real wait, read once, so a second spy never wraps the first one.
_REAL_WAIT = executor.wait_user_future


def _held_card(timeout_s: float | None, spy: list[float] | None = None,
               monkeypatch: pytest.MonkeyPatch | None = None):
    """One held rows card, as the sandbox watcher raises it, with no answer."""
    if spy is not None and monkeypatch is not None:
        real = _REAL_WAIT

        async def _spy(fut, timeout, *args, **kwargs):
            spy.append(timeout)
            # The real wait, cut short, so a default of 3,600 s ends in 0.1 s.
            return await real(fut, min(timeout, 0.1), *args, **kwargs)

        monkeypatch.setattr(executor, "wait_user_future", _spy)

    async def _run():
        q: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(q)
        executor._stream_relay_thread_id.set("t-rows")
        kwargs = {} if timeout_s is None else {"timeout_s": timeout_s}
        started = asyncio.get_running_loop().time()
        result = await asyncio.wait_for(at.request_confirmation(
            "Let a sandbox app make 1 change?", "one row", "context",
            rows=ROWS[:1], request_id="a" * 32, with_answer=True,
            non_interactive_default="deny", fenced=True, **kwargs,
        ), timeout=5)
        took = asyncio.get_running_loop().time() - started
        card = q.get_nowait()
        closed = q.get_nowait()
        return result, took, card, closed, at.last_card_outcome()

    return asyncio.run(_run())


def test_timeout_s_gives_timed_out(monkeypatch):
    """SBX-3b: with ``timeout_s`` the card closes at that time, as "timed out",
    and approves nothing. The default wait stays 3,600 s.

    Mutations caught: pass 3600 in place of the wait, or drop the parameter
    (this test fails), or change the default (the spy fails)."""
    result, took, card, closed, outcome = _held_card(0.2)
    assert result is None
    assert outcome == at.CARD_TIMED_OUT == "timed out"
    assert 0.15 <= took < 3
    assert card["value"]["request_id"] == "a" * 32
    assert closed["value"]["answer"] == "TIMEOUT"
    # The default: the one wait of the card is 3,600 s.
    spy: list[float] = []
    result, _took, _card, _closed, outcome = _held_card(None, spy, monkeypatch)
    assert spy == [3600] and at.CARD_WAIT_S == 3600
    assert result is None and outcome == at.CARD_TIMED_OUT
    # The value that the watcher passes is the wait, whole.
    spy.clear()
    _held_card(42.5, spy, monkeypatch)
    assert spy == [42.5]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "60", [60]])
def test_a_bad_timeout_s_raises_before_any_card(bad):
    async def _run():
        q: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(q)
        executor._stream_relay_thread_id.set("t-rows")
        # A bound, so a NaN wait that never ends fails here and does not hang.
        with pytest.raises(ValueError):
            await asyncio.wait_for(
                at.request_confirmation("t", rows=ROWS[:1], timeout_s=bad), timeout=2)
        return q.empty()

    assert asyncio.run(_run())
    assert at.card_wait_s(-5) == 0.0


# ── 7. The row results of a held sandbox card (SBX-3b done-when 24) ─────────


def test_row_results_carry_no_body(monkeypatch):
    """The event ``confirmation_row_results`` carries the id, the status and
    the door code of each row, and nothing else. Mutations caught: pass a row
    on whole, or send the event with no card id."""
    rid = "c" * 32
    rows = [
        {"id": "row-0", "i": 0, "status": 201, "body": {"id": "x", "secret": "s3cr3t"},
         "path": "/crm/leads"},
        {"id": "row-1", "status": 403, "code": "sandbox_write_refused", "detail": "Ignore"},
        {"id": "row-2", "status": 500, "code": "not a code"},
    ]

    async def _run():
        q: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(q)
        sent = await at.publish_card_rows(rid, rows)
        return sent, q.get_nowait()

    sent, event = asyncio.run(_run())
    assert sent is True
    assert event == {"type": "CUSTOM", "name": at.CONFIRMATION_ROW_RESULTS, "value": {
        "request_id": rid, "rows": [
            {"id": "row-0", "status": 201},
            {"id": "row-1", "status": 403, "code": "sandbox_write_refused"},
            {"id": "row-2", "status": 500}]}}
    text = json.dumps(event)
    for word in ("body", "secret", "s3cr3t", "path", "detail", "Ignore", "/crm"):
        assert word not in text, word
    # A bad value sends nothing.
    for bad_id, bad_rows in (("r1", rows), (rid, []), (rid, [{"id": "x", "status": 200}]),
                             (rid, [{"id": "row-0", "status": True}]), (rid, "rows")):
        assert at.row_results_value(bad_id, bad_rows) is None
    # No stream: nothing goes, and nothing raises.

    async def _none():
        executor._active_run_queue.set(None)
        monkeypatch.setattr(executor, "resolve_relay_thread_id", lambda: None)
        return await at.publish_card_rows(rid, rows)

    assert asyncio.run(_none()) is False
    # The client reads the same event name.
    ts = QUEUE_TS.read_text(encoding="utf-8")
    assert f'export const CONFIRMATION_ROW_RESULTS = "{at.CONFIRMATION_ROW_RESULTS}";' in ts
