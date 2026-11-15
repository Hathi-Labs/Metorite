"""The phase of a streamed chat run (CS-3a, task 3438).

Spec: ``project-docs/specs/chat_failure_modes.md`` §6 row CS-3.

CS-1 shows one fixed line from the moment of Send ("Reading your message"),
because the first model request takes 5 to 10 seconds. The server knows more
than that line: it gathers the context of the turn, and then it asks the
model. This module names those phases, so that a later client slice can show
them. This slice is the server half only, and no client draws a phase yet.

Rules that bind:

1. **A closed vocabulary.** :data:`PHASES` holds each phase. :func:`phase_event`
   is the ONE builder of the event: a CUSTOM event named :data:`EVENT` whose
   value holds the key ``phase`` and no other key. It refuses a name that is
   not in :data:`PHASES`. The event holds no org, member, run, tool or model
   text. Do not reuse the ``progress`` event of ``progress_notice``, which
   carries free text.
2. **No output.** The executor yields the event straight through ``_sse``. It
   never puts it on the run queue, so the event never sets ``_n_emitted`` and
   never seals the tier of the turn. A Tier 1 fault before the first output
   still falls back to Tier 2, and a parallel start can still restart.
3. **Once.** Each phase leaves at most once in a run. ``gathering`` goes when
   the setup of a native run ends. ``asking`` goes when its model request
   opens, and only while no output has left the run. The Tier 2 batch path
   sends no phase.
4. **Never saved.** ``gateway.chat_fold``, ``executor._is_answer_event``, the
   BFF route and ``withCustomEvent`` of the client drop the name, so no saved
   message holds it and no profile mark leaves on it.
5. **Dark.** ``CHAT_RUN_PHASE_ORGS`` (a comma list of organization ids, or
   ``*``) opens it for an org. Empty is the default, and then a run sends no
   byte that it did not send before. The org is the bound tenant of the run,
   never request input (R5).

Fence: ``tests/unit/test_run_phase_event.py``.
"""
from __future__ import annotations

from typing import Any

#: The name of the CUSTOM event on the run's stream.
EVENT = "run_phase"

#: The setup of the run ended: the context of the turn is ready.
GATHERING = "gathering"

#: The model request of the run opened.
ASKING = "asking"

#: Each phase, in the order that a run sends them.
PHASES: tuple[str, ...] = (GATHERING, ASKING)


def run_phase_on(org: object = None) -> bool:
    """True when ``CHAT_RUN_PHASE_ORGS`` names *org*. THE reader.

    *org* defaults to the bound tenant of the run. A comma list of org ids, or
    ``*`` for each one. Empty means off, the default. It reads the settings on
    each call, so a flag reload (``docs/flag_reload.md``) applies a change
    with no restart. It fails closed.
    """
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "chat_run_phase_orgs", "") or "")
    except Exception:
        return False
    entries = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if not entries:
        return False
    if "*" in entries:
        return True
    if org is None:
        try:
            from acb_common.db import current_tenant

            org = current_tenant()
        except Exception:
            return False
    return str(org or "").strip().lower() in entries


def phase_event(phase: str) -> dict[str, Any]:
    """The CUSTOM event of *phase*. THE builder.

    It raises :class:`ValueError` for a name outside :data:`PHASES`, so no
    free text can reach the stream under this event name.
    """
    if phase not in PHASES:
        raise ValueError(f"unknown run phase: {phase!r}")
    return {"type": "CUSTOM", "name": EVENT, "value": {"phase": phase}}
