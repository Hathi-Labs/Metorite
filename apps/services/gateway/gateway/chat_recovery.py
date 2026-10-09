"""A chat recovers from a gateway restart: the gateway's half.

Incident 2026-10-09, 16:18 UTC. ``orchestrator.run_liveness`` says which run is
dead and closes it. This module does what only the gateway can do with that:

* :func:`persist_interrupted` folds a closed run's stream into its saved reply.
  The partial answer and the ``run_interrupted`` marker then sit on the
  assistant row, and the chat shows "The assistant was interrupted by an
  update" with a Continue button. It is the sweep's ``on_interrupted`` hook.
* :func:`recover_dead_run` closes ONE dead run in a request's path, and
  persists it before that request starts the next run (whose reset deletes the
  old stream).
* :func:`compose_resume_note` is the server's words for Continue.
* :func:`card_answer_after_restart` turns a question-card answer that reached
  a dead run into a message the member's browser sends.

Fence (R7): ``tests/unit/test_chat_deploy_recovery.py``.
"""
from __future__ import annotations

from typing import Any

from acb_common import get_logger

_log = get_logger("gateway.chat_recovery")

#: What the chat sends for Continue. The server swaps in the note below, so a
#: client cannot write the instruction the model reads.
RESUME_NOTE = (
    "[Metorite] Your previous reply was cut off by an app update. "
    "Continue from where it stopped. Do not repeat what you already wrote."
)

#: The most of the saved partial reply that the note quotes.
_RESUME_TAIL_CHARS = 1200

#: The custom events that carry a question card's request id and its words.
_CARD_EVENTS = (
    "user_input_requested", "elicitation_requested", "confirmation_requested",
    "generative_ui",
)


def compose_resume_note(partial: str) -> str:
    """The message a Continue sends to the model, built from the saved reply.

    The history already carries the saved reply, so the note quotes only its
    tail: enough for the model to find the exact place to start again.
    """
    tail = (partial or "").strip()
    if not tail:
        return RESUME_NOTE
    if len(tail) > _RESUME_TAIL_CHARS:
        tail = "…" + tail[-_RESUME_TAIL_CHARS:]
    return f"{RESUME_NOTE}\n\nYour saved reply ended with:\n\n{tail}"


def compose_card_answer(question: str | None, answer: str) -> str:
    """The member's card answer, as a message the next run can act on."""
    ans = (answer or "").strip() or "(no answer)"
    q = (question or "").strip()
    if not q:
        return f"My answer to your last question: {ans}"
    return f'You asked me: "{q}"\n\nMy answer: {ans}'


def _question_of(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("question", "title", "prompt", "message"):
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    qs = value.get("questions")
    if isinstance(qs, list):
        parts = [
            str(q.get("question") or q.get("header") or "").strip()
            for q in qs if isinstance(q, dict)
        ]
        joined = " / ".join(p for p in parts if p)
        return joined or None
    return None


def find_card_question(events: list[dict[str, Any]], request_id: str) -> str | None:
    """The words of the card *request_id* asked, from a run's own events."""
    for evt in reversed(events or []):
        if evt.get("type") != "CUSTOM" or evt.get("name") not in _CARD_EVENTS:
            continue
        value = evt.get("value")
        if isinstance(value, dict) and str(value.get("request_id") or "") == request_id:
            return _question_of(value)
    return None


def was_interrupted(events: list[dict[str, Any]]) -> bool:
    """True when a run's stream carries the restart marker."""
    from orchestrator.run_liveness import INTERRUPTED_EVENT

    return any(
        e.get("type") == "CUSTOM" and e.get("name") == INTERRUPTED_EVENT
        for e in events or []
    )


async def persist_interrupted(records: list[dict[str, Any]]) -> int:
    """Fold each closed run's stream into its saved reply. Returns the count.

    A record with no row id or no org came from a run that cannot be
    persisted here (``/copilot/chat`` keeps its own path), and is skipped.
    Best-effort per run: one failure never stops the rest.
    """
    from gateway.chat_fold import persist_final_assistant_message

    done = 0
    for rec in records or []:
        tid = str(rec.get("threadId") or "")
        mid = str(rec.get("messageId") or "")
        org = rec.get("org")
        if not (tid and mid and org):
            continue
        try:
            folded = await persist_final_assistant_message(
                tid, mid,
                user_id=str(rec.get("actor") or ""),
                agent_name=str(rec.get("agent") or "orchestrator"),
                run_id=str(rec.get("runId") or ""),
                model=rec.get("model"),
                organization_id=str(org),
            )
            if folded is not None:
                done += 1
        except Exception:
            _log.warning("chat_recovery.persist_failed", thread_id=tid[:12])
    return done


async def recover_dead_run(thread_id: str, *, why: str) -> dict[str, Any]:
    """Close a dead run in a request's path, and persist it first.

    The caller starts the next run after this returns. That run's reset
    deletes the old stream, so the fold has to happen here, before it.
    """
    from orchestrator.run_liveness import interrupt_run

    rec = await interrupt_run(thread_id, reason="restart")
    persisted = await persist_interrupted([rec])
    _log.info(
        "chat.dead_run_recovered",
        thread_id=thread_id[:12], why=why, persisted=bool(persisted),
        owner=str(rec.get("owner") or "")[:40],
    )
    return rec


async def card_answer_after_restart(
    thread_id: str, request_id: str, answer: str, *, delivery: str,
) -> str | None:
    """The message to send in place of a card answer that reached no run.

    ``None`` when the run is not dead (an old card, or a run that is still
    alive): the route then keeps its old 409. When the run IS dead, it is
    closed here, and the answer comes back with the question it answers, read
    from the run's own stream.
    """
    from orchestrator.run_liveness import run_liveness
    from orchestrator.stream_relay import replay_events

    try:
        events = await replay_events(thread_id, since_id="0-0", count=5000, drain=True)
    except Exception:
        events = []
    liveness = await run_liveness(thread_id, undelivered=delivery == "undelivered")
    if liveness == "dead":
        await recover_dead_run(thread_id, why="card_answer_undelivered")
    elif not (liveness == "idle" and was_interrupted(events)):
        return None
    return compose_card_answer(find_card_question(events, request_id), answer)
