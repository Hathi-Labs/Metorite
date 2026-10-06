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
    result, _card, closed = _ask(at.ROWS_ANSWER_PREFIX + "[" * 100_000)
    assert result == frozenset()
    assert closed["value"]["answer"] == "REJECT"
