"""The restart drain — how much work a stop would end now (H-60 follow-up).

``deploy/hostinger/gateway-drain.sh`` is the ``ExecStop=`` of
``acb-gateway.service``. systemd runs it BEFORE it sends SIGTERM, and it polls
this route until the number reaches zero, or until its own bound passes. While
it waits, the old process keeps its port and serves every request as normal.
So a member sees no gap while a chat answer finishes.

Why the wait is not in uvicorn's own drain. SIGTERM closes the listening
socket first, and only THEN waits for open connections. Every second of that
wait is a second in which nobody can connect. On 2026-10-08 that wait ran 30 s,
32 s, 90 s and 90 s, and each one showed "Metorite is updating" to every
member. The unit now bounds it with ``--timeout-graceful-shutdown``.

What it counts:

- the detached chat runs (``orchestrator.stream_relay``). The HTTP stream is
  only a Redis listener, but the run itself lives in this process. A run that
  waits on a person (a question or an approval card) is NOT counted. It can
  wait an hour, and a restart ends it either way.

  Its ask is not lost (incident 2026-10-09). The card's question is already
  in the run's Redis stream, which outlives the process. The next process
  closes the run (``orchestrator.run_liveness``) and saves its reply with an
  "interrupted" marker. An answer that arrives after the stop comes back from
  ``POST /agent/respond-input`` as ``run_restarted``, with the question beside
  it, and the chat sends it as a new message. That is smaller and safer than
  holding every deploy for the full bound while a card is open.
- the Projects agent runs (``routes.projects.agent_dispatch``). Each one writes
  an "interrupted by a restart" row when the stop cancels it.
- the work a request does AFTER its response, such as the Starlette
  ``BackgroundTasks`` that close an email thread after a send. The bounded
  drain would cut it after 5 s. :class:`AfterResponseCounter` counts it.

An open stream is never counted. It ends only when its client goes, and the
holds in front of the gateway reconnect it.

All counts are of this process only. Internal token only. Caddy answers
``/internal/*`` with 404 on every public host, so the one caller is the box.
"""
from __future__ import annotations

from typing import Any

from acb_auth.deps import require_internal_auth
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/internal", tags=["meta"])

#: Requests that sent their whole response and have not returned yet.
_after_response = [0]


class AfterResponseCounter:
    """Count the requests that are past their response and still running.

    Pure ASGI, like ``TenantScopeMiddleware``. Starlette runs a response's
    ``BackgroundTasks`` after the last body message and before the app call
    returns, in the same task, so this window is exactly that work. A stream
    sends its last body message only when it ends, so it never counts.
    """

    def __init__(self, asgi_app: Any) -> None:
        self.asgi_app = asgi_app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.asgi_app(scope, receive, send)
            return
        counted = False

        async def _send(message: dict[str, Any]) -> None:
            nonlocal counted
            await send(message)
            if (
                not counted
                and message["type"] == "http.response.body"
                and not message.get("more_body", False)
            ):
                counted = True
                _after_response[0] += 1

        try:
            await self.asgi_app(scope, receive, _send)
        finally:
            if counted:
                _after_response[0] -= 1


def _chat_runs() -> tuple[int, int]:
    """``(running, waiting on a person)`` for the detached chat runs."""
    try:
        from orchestrator.executor import threads_waiting_on_a_person  # noqa: PLC0415
        from orchestrator.stream_relay import live_run_count  # noqa: PLC0415
    except ImportError:
        return 0, 0
    waiting = threads_waiting_on_a_person()
    return live_run_count(skip=waiting), len(waiting)


def _projects_runs() -> int:
    from gateway.routes.projects.agent_dispatch import live_run_count  # noqa: PLC0415

    return live_run_count()


@router.get("/drain", dependencies=[Depends(require_internal_auth)])
async def drain() -> dict[str, int]:
    """The work a stop would end now. ``runs`` is the sum the stop step reads.

    ``waiting_on_a_person`` is reported and never added to ``runs``.
    """
    chat, waiting = _chat_runs()
    projects = _projects_runs()
    after = _after_response[0]
    return {
        "runs": chat + projects + after,
        "chat": chat,
        "projects": projects,
        "after_response": after,
        "waiting_on_a_person": waiting,
    }
