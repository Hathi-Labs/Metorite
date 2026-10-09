"""Steer — fold a second message into a live run instead of destroying it.

Spec: ``docs/multiplayer/README.md`` §3.3 (the destructive race), §4.6 (steer is
"a new applier, not new infrastructure" on the existing ``cc:control:`` bus),
§5.2 (the correctness fix), and
``project-docs/specs/multiplayer_prior_art_qm_2026-08.md`` §QM-1 (the
external reference design this mirrors in shape, not in code).

The problem this solves, restated from §3.3: a second ``POST /agent/run/stream``
for a thread that is already running cancels the first run *and deletes its
Redis transcript*. Correct when you supersede your own run (retry, Quick
action); catastrophic the moment two people share a thread. The 409 the gateway
returns today makes that safe but useless — Bob is told "no" and his sentence
goes nowhere.

Steering makes the second message *land*. Four things have to be true for that
to be more than a slogan, and each is a separate mechanism below:

1. **The decision is a pure function.** :func:`route_turn` has no I/O, so the
   four outcomes are testable without a Redis, a run, or a model.
2. **The signal is durable before it is dispatched.** :func:`send_steer` writes
   to ``cc:steer:{thread_id}`` *first* and dispatches second. A steer aimed at a
   run that died mid-send is therefore still on disk, and the next run to start
   on that thread replays it (:func:`take_pending_steers`). Losing a person's
   sentence to a race is the failure this ordering exists to prevent.
3. **The second caller stands down.** Only one surface may deliver the answer.
   The route returns ``202 {"steered": true}`` and starts no run, so the reply
   is posted once — by the turn that was already running. Delivering from both
   sides is how one answer gets posted twice (§QM-1).
4. **The floor cannot move under a running turn.** A steer is admitted only from
   a principal already inside the run's authority floor — see
   :func:`is_inside_run_floor`.

What this module deliberately does NOT contain: floor-control modes, batons,
turn queues, tenure windows. Those stay unscheduled pending the owner's
re-decision now that steer exists (§QM-1's recommendation: *build steer first,
then re-evaluate whether five floor modes still earn their place*).
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from acb_common import get_logger

_log = get_logger("orchestrator.steer")

#: Pending steer signals for a thread. A LIST, not a stream: the only two
#: operations are "append one" and "take all", and the durability window is a
#: run boundary, not a replay window.
STEER_PREFIX = "cc:steer"

#: Long enough to survive a worker restart and a fresh run starting, short
#: enough that a steer nobody ever ran does not resurface hours later attached
#: to a conversation that has moved on.
STEER_TTL_SECONDS = 1800

#: A guard against one participant filling a run's context with guidance.
#: §5.3's "steer rate" dial, enforced as a cap on the buffer rather than a
#: per-person timer — the context window is the scarce resource, not the clock.
MAX_PENDING_STEERS = 20


# ---------------------------------------------------------------------------
# 1. The routing decision — a pure function
# ---------------------------------------------------------------------------

class Route(str, Enum):
    """What to do with a message that arrived for a thread.

    Mirrors the shape of qm's ``wake.ts`` routing (an external MIT reference —
    the shape is borrowed, the code is ours).
    """

    DROP = "drop"
    """The agent's own message, or nothing to say. Never wakes anything."""

    ENGAGE = "engage"
    """Nothing is running (or the running thing is a cron): start a new run."""

    ABORT = "abort"
    """A bare stop. Cancel the run rather than talking to it."""

    STEER = "steer"
    """Fold this into the turn that is already running."""


#: A message is a stop ONLY when stopping is the whole of it. "stop the staging
#: deploy" is guidance and must steer — reading it as an abort would cancel a
#: 40-minute run because someone used the word "stop" in a sentence. The set is
#: deliberately short and exact for that reason.
_BARE_STOPS: frozenset[str] = frozenset({
    "stop", "halt", "cancel", "abort", "quit", "wait",
    "stop it", "stop please", "please stop", "stop now",
    "never mind", "nevermind", "cancel that", "abort it",
})

#: Stripped before matching so "Stop!" and "stop." are the same word.
_STOP_TRIM = " \t\r\n.!?,;:'\"“”‘’"


def is_bare_stop(text: str) -> bool:
    """True when *text* means "stop" and nothing else."""
    return (text or "").strip().strip(_STOP_TRIM).strip().lower() in _BARE_STOPS


@dataclass(frozen=True, slots=True)
class TurnDecision:
    """The routing answer plus why, so a refusal can explain itself."""

    route: Route
    reason: str

    @property
    def steered(self) -> bool:
        return self.route is Route.STEER


def route_turn(
    *,
    author_kind: str,
    text: str,
    run_active: bool,
    target_run_kind: str = "human",
) -> TurnDecision:
    """Decide what a newly-arrived message should do to a thread. Pure.

    Args:
        author_kind:     ``"human"`` or ``"agent"``. An agent's own output must
                         never re-enter as a turn — that is a loop, not a
                         conversation.
        text:            What was said.
        run_active:      Is a run in flight on this thread right now.
        target_run_kind: ``"human"`` or ``"automation"``. A cron turn is not a
                         conversation anyone is standing in, so a person talking
                         during one gets their own run rather than being folded
                         into a schedule they did not start.

    The order of the checks is the contract:

    * **drop** beats everything — the agent's own message is never a turn.
    * **engage** when nothing is running; there is no run to steer.
    * **abort** beats the automation carve-out on purpose: "stop" during a cron
      is a person stopping the cron, which is exactly what they meant.
    * **automation** carve-out beats steer.
    * otherwise **steer**.
    """
    if (author_kind or "").strip().lower() != "human":
        return TurnDecision(Route.DROP, "not_a_human_turn")
    if not (text or "").strip():
        return TurnDecision(Route.DROP, "empty_message")
    if not run_active:
        return TurnDecision(Route.ENGAGE, "no_run_in_flight")
    if is_bare_stop(text):
        return TurnDecision(Route.ABORT, "bare_stop")
    if (target_run_kind or "human").strip().lower() == "automation":
        # §QM-1's one carve-out. Steering a cron would make a scheduled run
        # answer a person's question and file the result as the schedule's
        # output — two different turns wearing one run's identity.
        return TurnDecision(Route.ENGAGE, "automation_turn_not_steerable")
    return TurnDecision(Route.STEER, "folded_into_live_run")


# ---------------------------------------------------------------------------
# 2. The authority floor — who may talk to a turn already in flight
# ---------------------------------------------------------------------------

def is_inside_run_floor(
    steerer: str, floor: list[str] | None, *, room_admits: bool = False,
) -> bool:
    """May *steerer* address a run whose authority floor is *floor*?

    ``groups_sessions_authority.md`` §3: a shared run acts at the
    **intersection** of every participant's access, folded once at run start.
    That fold is what the in-flight turn is currently executing under — its
    credentials are already resolved, its memory compartments already chosen.

    So a steer from someone who was NOT in that fold is refused. Two cases, one
    rule:

    * A **non-participant** steering would be a party outside the room reaching
      into it. Refused for the obvious reason.
    * A participant **added after the run started** is subtler and the reason
      this check exists at all. Admitting them would mean the turn's effective
      access should now be narrower than the fold it is running under — but the
      run has already resolved credentials and read memory at the wider floor.
      We cannot retroactively un-read them, and we will not let a turn keep
      acting above the floor of everyone who can now see its output. Refusing
      the steer is the only answer that is true at both ends; they may send
      normally once the turn finishes, and the next run folds them in.

    ``room_admits`` covers the case the roster cannot answer on its own. A
    participant row may name a ``group:<slug>`` or ``org`` rather than a person,
    so a legitimate member's *email* is not in the roster at all. Refusing them
    would be a false negative on every group-shared room. When the roster holds
    such an unbounded subject and the caller's room access already grants
    sending, we allow — and we accept the residual honestly: somebody who joined
    that group mid-run is admitted, because no roster of subjects can tell the
    difference. Naming a room's participants individually is what buys the
    stricter guarantee.

    ``floor is None`` means the run recorded no roster (a solo run, a run that
    predates this mechanism, or a Redis miss) and resolves to **allow**, the
    same fail-open contract ``get_run_actor`` and ``_thread_owner_ok`` hold: a
    false refusal blocks real work, a false allow restores today's behaviour.
    """
    if floor is None:
        return True
    steerer = (steerer or "").strip().lower()
    if not steerer:
        return False
    subjects = {(m or "").strip().lower() for m in floor}
    if steerer in subjects:
        return True
    unbounded = any(s == "org" or s.startswith("group:") for s in subjects)
    return bool(unbounded and room_admits)


# ---------------------------------------------------------------------------
# 3. The durable signal store
# ---------------------------------------------------------------------------

def _steer_key(thread_id: str) -> str:
    return f"{STEER_PREFIX}:{thread_id}"


def make_signal(
    *, thread_id: str, author: str, text: str, run_id: str | None = None,
) -> dict[str, Any]:
    """One steer, as it is stored and as it travels on the control bus."""
    return {
        "id": uuid.uuid4().hex,
        "threadId": thread_id,
        "author": author,
        "text": text,
        "targetRunId": run_id,
        "ts": int(time.time() * 1000),
    }


async def persist_signal(thread_id: str, signal: dict[str, Any]) -> bool:
    """Write a steer to the durable store. Returns False on a store failure.

    Called BEFORE dispatch, never after: a steer that reached the run but was
    never written would vanish if the run died between applying and answering,
    and a steer that was written but never reached the run is replayed. Only
    one of those two orderings can lose a sentence.
    """
    try:
        from orchestrator.stream_relay import _get_client  # noqa: PLC0415

        r = await _get_client()
        key = _steer_key(thread_id)
        await r.rpush(key, json.dumps(signal, default=str))
        await r.ltrim(key, -MAX_PENDING_STEERS, -1)
        await r.expire(key, STEER_TTL_SECONDS)
        return True
    except Exception:  # noqa: BLE001
        _log.warning("steer.persist_failed", thread_id=thread_id[:12])
        return False


async def take_pending_steers(thread_id: str) -> list[dict[str, Any]]:
    """Atomically remove and return every steer still pending for a thread.

    This is the replay half of durability, and it is called at **run start**:
    a steer whose target run terminated mid-send is still in the list, so the
    fresh run picks it up and acts on it instead of the sentence being lost.

    Uses a rename-then-read so a concurrent :func:`persist_signal` writes into a
    new list rather than into one being drained — two runs starting at once
    must not each replay the same steer.
    """
    try:
        from orchestrator.stream_relay import _get_client  # noqa: PLC0415

        r = await _get_client()
        key = _steer_key(thread_id)
        tmp = f"{key}:taking:{uuid.uuid4().hex[:8]}"
        try:
            await r.rename(key, tmp)
        except Exception:
            return []  # no pending steers (RENAME on a missing key errors)
        raw = await r.lrange(tmp, 0, -1)
        await r.delete(tmp)
    except Exception:  # noqa: BLE001
        _log.warning("steer.take_failed", thread_id=thread_id[:12])
        return []

    out: list[dict[str, Any]] = []
    for item in raw or []:
        try:
            parsed = json.loads(item)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


async def discard_signal(thread_id: str, signal_id: str) -> None:
    """Drop one applied steer from the durable store.

    Called by the applier once the guidance is buffered into the live run, so a
    steer that landed is not replayed a second time into the following run.
    Best-effort: a miss costs a duplicated note, never a lost one, which is the
    right direction for this trade.
    """
    if not signal_id:
        return
    try:
        from orchestrator.stream_relay import _get_client  # noqa: PLC0415

        r = await _get_client()
        key = _steer_key(thread_id)
        for item in await r.lrange(key, 0, -1) or []:
            try:
                if json.loads(item).get("id") == signal_id:
                    await r.lrem(key, 1, item)
                    return
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue
    except Exception:  # noqa: BLE001
        _log.debug("steer.discard_failed", thread_id=thread_id[:12])


# ---------------------------------------------------------------------------
# 4. The in-process guidance buffer — what the model actually reads
# ---------------------------------------------------------------------------
#
# §4.6 step 3: the queue is drained "at the next tool boundary and injected as a
# note". Tool boundaries are the seam because the model is between decisions
# there, so injection does not corrupt a streaming turn.
#
# This buffer is deliberately per-process and keyed by thread: it belongs to the
# worker that owns the run, which is the only worker whose tools are executing.
# Cross-worker delivery is the control bus's job (dispatch_control), not this
# dict's — by the time text lands here it has already reached the right worker.

_PENDING_GUIDANCE: dict[str, list[str]] = {}


def buffer_guidance(thread_id: str, author: str, text: str) -> None:
    """Queue one attributed note for injection at the run's next tool boundary."""
    if not thread_id or not (text or "").strip():
        return
    who = (author or "someone").strip()
    note = f"[steer from {who}] {text.strip()}"
    buf = _PENDING_GUIDANCE.setdefault(thread_id, [])
    if len(buf) >= MAX_PENDING_STEERS:
        buf.pop(0)
    buf.append(note)


def drain_guidance(thread_id: str) -> list[str]:
    """Take every pending note for *thread_id*. Empty list when there is none."""
    return _PENDING_GUIDANCE.pop(thread_id, []) if thread_id else []


def has_guidance(thread_id: str) -> bool:
    return bool(_PENDING_GUIDANCE.get(thread_id))


def clear_guidance(thread_id: str) -> None:
    """Run-boundary cleanup so a note never leaks into an unrelated later run."""
    _PENDING_GUIDANCE.pop(thread_id, None)


def decorate_tool_result(result: Any, thread_id: str | None = None) -> Any:
    """Append any pending steer notes to a tool's return value.

    This is §4.6 step 3 made concrete, and the choice of seam deserves stating
    because a more obvious one does not exist. A native MAF run is driven by
    ``agent.run(input)`` where ``input`` is a list built once by
    ``_compose_maf_run_input`` and consumed at run start — there is no message
    store to append to and no supported way to hand the model another turn
    mid-run. The ONE channel that reliably carries text into a running model's
    context is a tool's return value; it is how ``ask_user``'s answer reaches
    the model today (``ask_tools`` returns ``"User response: …"``).

    So a steer rides the next tool result. That is also the right *moment*
    independent of the mechanism: the model is between decisions at a tool
    boundary, so injecting there cannot corrupt a half-streamed sentence.

    Degrades to a no-op with no pending notes, and never raises — a tool result
    must reach the model whatever happens here.
    """
    try:
        tid = thread_id
        if not tid:
            from orchestrator.executor import (  # noqa: PLC0415
                resolve_relay_thread_id,
            )
            tid = resolve_relay_thread_id()
        if not tid or not has_guidance(tid):
            return result
        notes = drain_guidance(tid)
        if not notes:
            return result
        block = (
            "\n\n[The following arrived from a participant while you were "
            "working. Treat it as instruction from the user, and adjust what "
            "you are doing now.]\n" + "\n".join(notes)
        )
        return f"{result if result is not None else ''}{block}"
    except Exception:  # noqa: BLE001 — never break a tool result
        return result


def format_replayed(signals: list[dict[str, Any]]) -> str:
    """Render replayed steers as a prefix for the run that inherits them.

    A steer whose run died is not guidance about a turn in flight any more — it
    is something a person said that nothing has answered. So it enters the fresh
    run's message as attributed text rather than as a mid-turn aside.
    """
    lines = [
        f"[steer from {(s.get('author') or 'someone')}] {s.get('text') or ''}".strip()
        for s in signals
        if (s.get("text") or "").strip()
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. Sending — durable first, dispatch second
# ---------------------------------------------------------------------------

async def send_steer(
    thread_id: str, author: str, text: str, *, run_id: str | None = None,
) -> dict[str, Any]:
    """Persist a steer and deliver it to whichever worker owns the run.

    Returns the signal, annotated with ``delivered``. ``delivered=False`` is not
    an error the caller should surface as a failure: the signal is on disk and
    the next run on this thread replays it. That is the whole point of writing
    before dispatching.

    ``undelivered`` is True when NO process heard the steer. The route then
    asks ``run_liveness`` whether the run is dead, and if so it starts the
    next run itself, in the same request (incident 2026-10-09).
    """
    signal = make_signal(
        thread_id=thread_id, author=author, text=text, run_id=run_id,
    )
    stored = await persist_signal(thread_id, signal)

    delivered = False
    command: dict[str, Any] = {
        "cmd": "steer",
        "signal_id": signal["id"],
        "author": author,
        "text": text,
    }
    try:
        from orchestrator.stream_relay import dispatch_control  # noqa: PLC0415

        delivered = await dispatch_control(thread_id, command)
    except Exception:  # noqa: BLE001
        _log.warning("steer.dispatch_failed", thread_id=thread_id[:12])
    from orchestrator.stream_relay import delivery_of  # noqa: PLC0415

    status = delivery_of(command)
    if not delivered:
        _log.info(
            "steer.pending_replay",
            thread_id=thread_id[:12], stored=stored,
        )
    return {
        **signal, "delivered": delivered, "stored": stored,
        "undelivered": status == "undelivered",
    }
