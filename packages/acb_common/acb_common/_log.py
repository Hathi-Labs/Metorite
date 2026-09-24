"""Structured logging via structlog. Call configure_logging() at process start.

Two output modes (``LOG_FORMAT`` env, or the ``json_logs`` arg):
  * ``console`` (default) — colored, human-readable; good for local dev.
  * ``json``            — one JSON object per line; machine-parseable so prod
                          logs (journald → ``journalctl -o cat``) can be
                          grepped/filtered by field and shipped to an aggregator.

Correlation: :func:`bind_run_context` binds ``run_id``/``thread_id``/``agent``/
``user``/``instance`` into structlog's contextvars so EVERY log line emitted
during a run carries them automatically — the thing that makes "show me all logs
for run X" possible (E2 observability). Bind at the run boundary, clear in
``finally``.
"""
from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Iterator

import structlog


def _want_json(json_logs: bool | None) -> bool:
    """Resolve the renderer: explicit arg wins, else ``LOG_FORMAT`` env.

    ``LOG_FORMAT=json`` (or ``1``/``true``) → JSON; anything else → console.
    """
    if json_logs is not None:
        return json_logs
    fmt = os.environ.get("LOG_FORMAT", "").strip().lower()
    return fmt in ("json", "1", "true", "yes")


def configure_logging(
    level: str = "INFO", *, json_logs: bool | None = None,
) -> None:
    logging.basicConfig(format="%(message)s", level=level.upper())
    renderer: object = (
        structlog.processors.JSONRenderer()
        if _want_json(json_logs)
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


# ── Run correlation (E2 observability) ───────────────────────────────────────

# The context keys bound per run. Kept small + stable so every log line during a
# run gains the same joinable fields (and so bind/clear stay symmetric).
# ``source`` is the originating app (chat / email / tasks / …) so the live
# activity feed can attribute an activation to the surface that triggered it.
# ``instance`` is the run's tenant partition, drawn from the SAME vocabulary
# the manifest, the blob store (migration 136) and the state directory use
# (``AgentManifest.instance_key``: ``''`` shared · ``u:<email>`` personal ·
# ``t:<team>`` team). It completes decision D1's attribution four-tuple
# (run_id, member, agent, instance) — WS-6a, specs/observability_e2.md §7.
# The value identifies the partition of the run THAT RESOLVED IT; because the
# context is inherited, a delegated sub-run that resolves none carries the
# caller's key while writing blobs under the shared partition (the delegation
# asymmetry recorded in §7 WS-6a). Do not treat the stamp as a join key onto
# ``agent_blob`` without checking which run emitted it.
# ⚠️ Twin tuple: ``activity._INHERIT`` must stay in sync — pinned by
# tests/unit/test_observability.py::test_inherit_and_run_context_keys_match.
_RUN_CONTEXT_KEYS = (
    "run_id", "thread_id", "agent", "user", "source", "instance",
    # 📌 Usage attribution. `app` is the product app the agent belongs to, as
    # its `config.json` declares it, so a bill can say which app spent the
    # credits. `source` says which SURFACE started the run (chat, a workflow),
    # and one app is reached from several surfaces, so the two are not the
    # same fact. `member_verified` is "1" only when `user` came from the
    # signed-in session and not from the request body. Only a verified member
    # may be SIGNED for the Router (H-73).
    "app", "member_verified",
)


def bind_run_context(
    *,
    run_id: str | None = None,
    thread_id: str | None = None,
    agent: str | None = None,
    user: str | None = None,
    source: str | None = None,
    instance: str | None = None,
    app: str | None = None,
    member_verified: bool = False,
) -> None:
    """Bind run-correlation fields into structlog contextvars.

    After this, every ``get_logger(...).info(...)`` on the SAME asyncio task /
    thread context automatically includes the given fields — so you can filter
    all log lines for one agent run. Only non-empty values are bound. Pair with
    :func:`clear_run_context` in a ``finally`` at the run boundary.

    Additive by design: each call binds only the fields it is given, leaving
    already-bound ones alone. That is what lets the run boundary bind early
    (so failures *during* agent load are still correlated) and top up
    ``instance`` once the loaded config makes it resolvable — see
    ``orchestrator.executor._bind_run_instance``.

    The shared partition is ``''``, which is falsy, so a shared agent binds no
    ``instance`` key at all rather than an empty/quoted value — "absent" is the
    wire representation of shared, matching ``agent_blob.instance``.
    """
    fields = {
        k: v
        for k, v in (
            ("run_id", run_id),
            ("thread_id", thread_id),
            ("agent", agent),
            ("user", user),
            ("source", source),
            ("instance", instance),
            ("app", app),
            # A string, because every other field is one and a log line that
            # prints `True` beside `"chat"` is a second vocabulary for a flag.
            ("member_verified", "1" if member_verified else None),
        )
        if v
    }
    if fields:
        structlog.contextvars.bind_contextvars(**fields)


@contextlib.contextmanager
def run_context_scope() -> Iterator[None]:
    """Snapshot this task's run fields, and put them back EXACTLY on exit.

    🔴 **Not :func:`clear_run_context`, and a nested run is why.** A run can
    start inside another run: a sub-agent dispatch awaits ``run_agent`` on its
    parent's task. Clearing on the way out would wipe the PARENT's member and
    app, so every model call the parent made after the child finished would
    bill nobody. Restoring the snapshot leaves the parent exactly as it was.
    """
    before = {
        k: v
        for k, v in structlog.contextvars.get_contextvars().items()
        if k in _RUN_CONTEXT_KEYS
    }
    try:
        yield
    finally:
        structlog.contextvars.unbind_contextvars(*_RUN_CONTEXT_KEYS)
        if before:
            structlog.contextvars.bind_contextvars(**before)


def clear_run_context() -> None:
    """Unbind the run-correlation fields bound by :func:`bind_run_context`."""
    structlog.contextvars.unbind_contextvars(*_RUN_CONTEXT_KEYS)


def get_run_context() -> dict[str, str]:
    """Return the currently-bound run-correlation fields (for attribution).

    Lets non-run code (e.g. the LLM client's usage emitter) tag its records
    with the active run without threading ids through every call signature.
    """
    ctx = structlog.contextvars.get_contextvars()
    return {k: ctx[k] for k in _RUN_CONTEXT_KEYS if ctx.get(k)}
