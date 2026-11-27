"""The watcher of the held writes of a sandbox run (SBX-3b).

Spec: ``project-docs/specs/agent_sandbox.md`` §12.5 decision 8.

Code in the sandbox writes only through ``POST /sandbox/v1/batch``. The
gateway holds each row for a card, and the batch call waits. Only a tool
call of the chat turn can show a card (``ask_tools.request_confirmation``),
so a tool call runs :func:`watch_held` beside the run command:

1. **The poll.** Every second it asks ``POST /sandbox/held/card`` for the
   next open card of the run.
2. **The card.** It shows the card with the text that the gateway built from
   the held record, and never with text of the model. The card waits
   ``seconds_left``, the time to the deadline of the batch.
3. **The act.** A ``RowsAnswer`` goes to ``POST /sandbox/held/act``
   unchanged. A refusal, a timeout and no answer go as ``refused``. With no
   chat, ``request_confirmation`` gives ``None``, so a run with no person
   writes nothing.
4. **The results.** On each poll it reads ``POST /sandbox/held/results``, and
   sends the result of each row to the receipt of its card
   (``ask_tools.publish_card_rows``).
5. **The stop.** When the run command ends (*stop* is set), it finishes the
   card that it shows and asks for no new card. It reads results until the
   deadline of the last batch plus 30 seconds.

**R5.** The org and the member come from the run binding. Each call carries
the internal bearer and the run's member, and the gateway takes the org from
that member. No argument names an org or a member.
Fence: ``tests/unit/test_sandbox_held_card.py``.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from acb_common import get_logger
from acb_common.settings import internal_auth_headers

_log = get_logger("acb_skills.sandbox_held")

__all__ = ["POLL_S", "RESULTS_GRACE_S", "WatchReport", "watch_held"]

#: The watcher asks for the next card this often, in seconds.
POLL_S = 1.0

#: After the run command ends, the watcher reads results until the deadline
#: of the last batch plus this many seconds (decision 8).
RESULTS_GRACE_S = 30.0

#: The timeout of one call to the gateway, in seconds.
CALL_TIMEOUT_S = 30.0


@dataclass
class WatchReport:
    """What one watch did: counts only, for the tool's own answer."""

    shown: int = 0
    approved: int = 0
    refused: int = 0
    act_failed: int = 0
    results: int = 0
    cards: list[str] = field(default_factory=list)
    #: The cards whose results reached their receipt.
    done: set[str] = field(default_factory=set)


def _gateway_url() -> str:
    return os.environ.get("GATEWAY_URL", "http://localhost:8080").rstrip("/")


def _member() -> str:
    """The member of this run, from the run binding, and nothing else."""
    try:
        from acb_skills.memory_tools import _get_memory_user_id

        return str(_get_memory_user_id() or "").strip().lower()
    except Exception:
        return ""


def _client() -> httpx.AsyncClient:
    """The HTTP client to the gateway. A seam, so a test passes an ASGI app."""
    return httpx.AsyncClient(base_url=_gateway_url(), timeout=CALL_TIMEOUT_S)


async def _call(path: str, body: dict[str, Any], member: str) -> httpx.Response:
    headers = {**internal_auth_headers(), "Content-Type": "application/json",
               "X-User-Email": member}
    async with _client() as client:
        return await client.post(path, json=body, headers=headers)


async def _next_card(run_id: str, member: str) -> dict[str, Any] | None:
    """The next open card of the run, or None. A fault is None."""
    try:
        answer = await _call("/sandbox/held/card", {"run_id": run_id}, member)
    except httpx.HTTPError as exc:
        _log.warning("sandbox_held.card_failed", error=type(exc).__name__)
        return None
    if answer.status_code != 200:
        if answer.status_code != 204:
            _log.warning("sandbox_held.card_refused", status=answer.status_code)
        return None
    try:
        card = answer.json()
    except ValueError:
        return None
    if not isinstance(card, dict) or not isinstance(card.get("card_id"), str):
        return None
    return card


async def _act(body: dict[str, Any], member: str) -> bool:
    """Hand one answer to the gateway. True when the card took a decision."""
    try:
        answer = await _call("/sandbox/held/act", body, member)
    except httpx.HTTPError as exc:
        _log.warning("sandbox_held.act_failed", error=type(exc).__name__)
        return False
    if answer.status_code != 200:
        _log.warning("sandbox_held.act_refused", status=answer.status_code)
        return False
    return True


async def _deliver_results(run_id: str, member: str, report: WatchReport) -> None:
    """Read the results of the run once, and send each card's results to its
    receipt. A fault sends nothing."""
    from acb_skills.ask_tools import publish_card_rows

    try:
        answer = await _call("/sandbox/held/results", {"run_id": run_id}, member)
    except httpx.HTTPError as exc:
        _log.warning("sandbox_held.results_failed", error=type(exc).__name__)
        return
    if answer.status_code != 200:
        return
    try:
        results = answer.json().get("results")
    except (ValueError, AttributeError):
        return
    for one in results if isinstance(results, list) else []:
        if not isinstance(one, dict):
            continue
        card_id = str(one.get("card_id") or "")
        # The route spends a result once, so a result that came back is done,
        # also when no stream takes it (a run with no chat).
        report.done.add(card_id)
        if await publish_card_rows(card_id, one.get("rows")):
            report.results += 1


async def _show(card: dict[str, Any], thread_id: str, member: str,
                report: WatchReport) -> None:
    """Show one card, and hand its answer to the gateway."""
    from acb_skills.ask_tools import RowsAnswer, request_confirmation

    card_id = str(card["card_id"])
    report.shown += 1
    report.cards.append(card_id)
    try:
        answer = await request_confirmation(
            str(card.get("title") or ""), str(card.get("detail") or ""),
            str(card.get("context") or ""),
            rows=list(card.get("rows") or []),
            request_id=card_id, with_answer=True, non_interactive_default="deny",
            fenced=True, responder=member,
            timeout_s=max(float(card.get("seconds_left") or 0), 0.0),
        )
    except (ValueError, TypeError) as exc:
        # A card that the gateway built badly: a refusal, so nothing runs.
        _log.warning("sandbox_held.card_bad", error=type(exc).__name__)
        answer = None
    if isinstance(answer, RowsAnswer) and answer:
        ok = await _act({"card_id": card_id, "card_thread_id": answer.thread_id,
                         "answer": answer.answer}, member)
        report.approved += int(ok)
        report.act_failed += int(not ok)
        return
    # A refusal, a timeout, another member, a closed card or no chat.
    ok = await _act({"card_id": card_id, "card_thread_id": thread_id, "refused": True},
                    member)
    report.refused += int(ok)
    report.act_failed += int(not ok)


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


async def watch_held(run_id: str, thread_id: str, *, stop: asyncio.Event,
                     poll_s: float | None = None) -> WatchReport:
    """Show each held card of the run *run_id*, until *stop* is set.

    *thread_id* is the chat thread of the run, which owns its cards. A
    refusal names it. *stop* is set when the run command ends. The watcher
    then finishes the card that it shows, asks for no new card, and reads
    results until each card that it showed has its results, or until the
    deadline of the last batch plus :data:`RESULTS_GRACE_S`. With no member in the run binding it shows no
    card and returns at once: the batch then times out with nothing run."""
    report = WatchReport()
    member = _member()
    if not member or "@" not in member or not run_id or not thread_id:
        _log.warning("sandbox_held.no_member")
        return report
    step = POLL_S if poll_s is None else poll_s
    last_deadline = 0.0
    while True:
        await _deliver_results(run_id, member, report)
        if stop.is_set():
            break
        card = await _next_card(run_id, member)
        if card is None:
            await _sleep_or_stop(stop, step)
            continue
        last_deadline = max(last_deadline,
                            time.monotonic() + float(card.get("seconds_left") or 0))
        await _show(card, thread_id, member, report)
    end = last_deadline + RESULTS_GRACE_S
    while (last_deadline and time.monotonic() < end
           and not set(report.cards) <= report.done):
        await asyncio.sleep(min(step, max(end - time.monotonic(), 0.0)))
        await _deliver_results(run_id, member, report)
    _log.info("sandbox_held.done", shown=report.shown, approved=report.approved,
              refused=report.refused, act_failed=report.act_failed, results=report.results)
    return report
