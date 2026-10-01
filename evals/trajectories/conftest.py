"""Harness-state isolation for the trajectory gate.

The trajectory evals lock HARNESS behaviour, so each eval must start from a
clean harness. When the repo suite and the evals run in ONE pytest process
(``pytest tests/unit/ evals/trajectories/``), unit tests that drove the real
run path leave cross-runtime HITL breadcrumbs behind — most notably the
``session_id`` of the run artifact context, which ``resolve_relay_thread_id``
consults. It is a ContextVar since H-201, and a synchronous test that binds it
binds the main thread's context. A stale session id turns the fail-closed no-channel path of
``request_confirmation`` into "push the card to a dead relay and park on the
Future for an hour" — the eval HANGS instead of failing closed. CI never
sees this (skill-eval.yml runs the evals in their own process); this
conftest makes the gate order-independent anyway.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_harness_state():
    from acb_skills.write_artifact import _RUN_ARTIFACT_CONTEXT
    from orchestrator import executor

    def _reset() -> None:
        _RUN_ARTIFACT_CONTEXT.set(None)
        executor._RUN_QUEUES.clear()
        executor._pending_user_input.clear()

    _reset()
    yield
    _reset()
