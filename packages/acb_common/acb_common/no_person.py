"""The mark of a run with no person present (WS-AUTO AUTO-7a).

Spec: ``project-docs/specs/proactive_agent.md`` §4.2. Decision: D131 rule 4.

An unattended run reads text that a stranger wrote: a web page, a mail, a tool
result. That text can tell the run to approve its own write. So the server
marks each run that no person watches. A marked run cannot approve, reject,
resume or answer anything. Until AUTO-7b parks the act and asks a person, a
marked run fails closed.

**Why this module.** The readers are ``action_broker.broker``,
``acb_skills.ask_tools``, ``orchestrator.executor`` and two gateway route
modules. ``acb_common`` sits below all of them. The ``action-broker`` package
does not depend on ``acb_skills``, and such a dependency would point up the
import graph. This module imports the standard library only, so it adds no
cycle.

**Only server code sets the mark.** :func:`no_person_scope` is the one setter.
Two modules call it: ``gateway.jobs.worker`` around each handler, and
``gateway.routes.workflows.service`` for a run of an unattended trigger. No
tool, no tool argument and no request field reaches it.

**Nothing clears the mark.** This module has no function that sets False. The
scope gives no token to its caller, and it undoes only its own set. A nested
scope and a task that a marked run starts stay marked.

**What carries the mark.** A ContextVar copies into a new task and into
``asyncio.to_thread``. ``orchestrator.copilot_agent.carry_run_context`` carries
it into a Copilot SDK callback.

⚠️ **Two hops drop the mark.** A bare ``run_in_executor`` runs in an empty
context, so a tool must not run there. An HTTP call to the gateway is a new
request, and a tool that calls the gateway acts as its member. The route
cannot tell that call from a person's. So no tool may name a route that
approves or answers. ``tests/unit/test_no_person_present.py`` scans for one.

Fences: ``tests/unit/test_no_person_present.py``,
``tests/unit/test_no_person_present_r8.py`` (R8).
"""
from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator
from typing import Any

__all__ = [
    "NO_PERSON_PRESENT",
    "NO_PERSON_TEXT",
    "no_person_present",
    "no_person_refusal",
    "no_person_scope",
]

#: The named error of each refusal.
NO_PERSON_PRESENT = "no_person_present"
#: The words of each refusal. A model reads them.
NO_PERSON_TEXT = (
    "This run has no person present, so it cannot approve, reject or answer. "
    "A person must do this."
)

_NO_PERSON_PRESENT: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "acb_no_person_present", default=False,
)


def no_person_present() -> bool:
    """True inside :func:`no_person_scope`: no person watches this run."""
    return _NO_PERSON_PRESENT.get()


@contextlib.contextmanager
def no_person_scope() -> Iterator[None]:
    """Mark the run that this scope holds. Only server code opens it."""
    token = _NO_PERSON_PRESENT.set(True)
    try:
        yield
    finally:
        _NO_PERSON_PRESENT.reset(token)


def no_person_refusal() -> dict[str, Any]:
    """The answer of a seam that returns ``{"ok": ...}`` and refused a marked run."""
    return {"ok": False, "error": NO_PERSON_PRESENT, "detail": NO_PERSON_TEXT}
