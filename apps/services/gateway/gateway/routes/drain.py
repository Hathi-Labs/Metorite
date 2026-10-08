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
  only a Redis listener, but the run itself lives in this process.
- the Projects agent runs (``routes.projects.agent_dispatch``). Each one writes
  an "interrupted by a restart" row when the stop cancels it.

Both counts are of this process only. A second gateway process would answer
for itself.

Internal token only. Caddy answers ``/internal/*`` with 404 on every public
host, so the one caller is the box itself.
"""
from __future__ import annotations

from acb_auth.deps import require_internal_auth
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/internal", tags=["meta"])


def _chat_runs() -> int:
    try:
        from orchestrator.stream_relay import live_run_count  # noqa: PLC0415
    except ImportError:
        return 0
    return live_run_count()


def _projects_runs() -> int:
    from gateway.routes.projects.agent_dispatch import live_run_count  # noqa: PLC0415

    return live_run_count()


@router.get("/drain", dependencies=[Depends(require_internal_auth)])
async def drain() -> dict[str, int]:
    """The runs a stop would end now. ``runs`` is the sum the stop step reads."""
    chat = _chat_runs()
    projects = _projects_runs()
    return {"runs": chat + projects, "chat": chat, "projects": projects}
