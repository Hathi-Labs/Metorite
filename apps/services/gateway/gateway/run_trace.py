"""Durable per-run observability trace (E2, specs/observability_e2.md).

Writes one ``agent_run`` row per run at the run boundary: metadata (agent,
model, tokens, duration, status) + full error/traceback for ALL runs, and the
full folded trace (content, tool results, reasoning) ONLY for errored/flagged
runs — so an engineer can answer "error X happened with agent Y" long after the
1-hour Redis event TTL has expired.

Derived from the same Redis event replay ``chat_fold`` already does at run end,
so this adds one DB write at a point that already holds all the data. Never
raises — observability must never break a run.
"""
from __future__ import annotations

from datetime import UTC
from typing import Any

from acb_common import get_logger

_log = get_logger("gateway.run_trace")


def _derive_status(events: list[dict[str, Any]]) -> tuple[str, str, str]:
    """Return ``(status, error_message, error_type)`` from a run's events.

    Scans for a terminal RUN_ERROR (→ 'error', with its message) or a cancelled
    RUN_FINISHED (→ 'cancelled'); otherwise 'completed'. The traceback is not in
    the AG-UI stream (it's log-only), so error_type is best-effort from the msg.
    """
    status = "completed"
    err_msg = ""
    for ev in events:
        t = str(ev.get("type") or "")
        if t == "RUN_ERROR":
            status = "error"
            err_msg = str(ev.get("message") or ev.get("error") or "")
        elif t == "RUN_FINISHED" and ev.get("cancelled"):
            if status != "error":
                status = "cancelled"
    return status, err_msg, ""


def _stream_id_ms(event: dict[str, Any]) -> int | None:
    """Epoch-ms from a Redis stream id (``<ms>-<seq>``), if present.

    Mirror of chat_fold._stream_id_ms — kept local so run_trace has no import
    dependency on the fold module's internals.
    """
    sid = str(event.get("_stream_id") or "")
    ms, _, _ = sid.partition("-")
    return int(ms) if ms.isdigit() else None


def _tool_summary(folded: dict[str, Any] | None) -> list[dict[str, str]]:
    """Lightweight [{"name","status"}...] over the run's tools (all runs)."""
    if not folded:
        return []
    out: list[dict[str, str]] = []
    for te in folded.get("tool_events") or []:
        if isinstance(te, dict):
            out.append({
                "name": str(te.get("name") or "tool"),
                "status": str(te.get("status") or "done"),
            })
    return out


def build_run_trace_row(
    *,
    run_id: str,
    thread_id: str | None,
    agent_name: str,
    user_id: str,
    model: str | None,
    events: list[dict[str, Any]],
    folded: dict[str, Any] | None,
    started_ms: int | None = None,
    ended_ms: int | None = None,
    flagged: bool = False,
) -> dict[str, Any]:
    """Assemble the ``agent_run`` row dict from a run's events + folded message.

    Pure function (no DB / no clock) so it's unit-testable. The full ``trace``
    is attached ONLY for errored or flagged runs; successful runs store just the
    metadata + tool_summary.
    """
    status, err_msg, err_type = _derive_status(events)
    keep_trace = status in ("error", "cancelled") or flagged

    tool_summary = _tool_summary(folded)

    # Derive start/end from the events' Redis stream-id timestamps (<ms>-<seq>)
    # when the caller didn't pass them — the run boundary rarely has the start
    # time handy, but the first/last event bracket the run. This is what makes
    # duration_ms populate (it was null before: only ended_ms was passed).
    _event_ms = [
        _ms
        for ev in events
        if (_ms := _stream_id_ms(ev)) is not None
    ]
    if started_ms is None and _event_ms:
        started_ms = min(_event_ms)
    if ended_ms is None and _event_ms:
        ended_ms = max(_event_ms)

    duration_ms = (
        (ended_ms - started_ms)
        if (started_ms is not None and ended_ms is not None
            and ended_ms >= started_ms)
        else None
    )

    trace: dict[str, Any] | None = None
    if keep_trace and folded is not None:
        trace = {
            "content": folded.get("content"),
            "tool_events": folded.get("tool_events"),
            "reasoning": folded.get("reasoning"),
            "custom_events": folded.get("custom_events"),
        }

    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "agent_name": agent_name,
        "user_id": user_id or None,
        "model": model or None,
        "status": status,
        "started_ms": started_ms,
        "ended_ms": ended_ms,
        "duration_ms": duration_ms,
        "tool_count": len(tool_summary),
        "tool_summary": tool_summary,
        "error_message": err_msg or None,
        "error_type": err_type or None,
        "trace": trace,
        "flagged": flagged,
    }


def _persist_row(row: dict[str, Any], organization_id: str | None) -> None:
    """Upsert the agent_run row (sync — run off the event loop).

    ``agent_run`` is FORCE RLS in production, so the write binds the run's
    tenant (WS-27bm S15, ``projects_ai_chat.md`` §21). With no tenant,
    ``tenant_session`` raises ``TenantUnbound`` and no row is written.
    """
    import json
    from datetime import datetime

    from acb_graph import tenant_session
    from sqlalchemy import text

    ended_at = None
    if row.get("ended_ms"):
        ended_at = datetime.fromtimestamp(
            row["ended_ms"] / 1000.0, tz=UTC,
        )

    # started_at derived from the first event's stream-id ms. Previously never
    # written, so the NOT NULL DEFAULT now() stamped it to ≈ run END — the
    # /observability feed orders/filters by started_at, so ordering was by end
    # time (M7). COALESCE keeps the DEFAULT now() when we can't derive a start.
    started_at = None
    if row.get("started_ms"):
        started_at = datetime.fromtimestamp(
            row["started_ms"] / 1000.0, tz=UTC,
        )

    with tenant_session(organization_id) as s:
        s.execute(
            text(
                """
                INSERT INTO agent_run (
                    run_id, thread_id, agent_name, user_id, model, status,
                    started_at, ended_at, duration_ms, tool_count, tool_summary,
                    error_message, error_type, error_traceback, trace, flagged
                ) VALUES (
                    :run_id, :thread_id, :agent_name, :user_id, :model, :status,
                    COALESCE(:started_at, now()),
                    :ended_at, :duration_ms, :tool_count,
                    CAST(:tool_summary AS JSONB),
                    :error_message, :error_type, :error_traceback,
                    CAST(:trace AS JSONB), :flagged
                )
                ON CONFLICT (run_id) DO UPDATE SET
                    -- started_at is intentionally NOT updated: the first write
                    -- (at run end) records the true start; keep it stable.
                    status          = EXCLUDED.status,
                    ended_at        = EXCLUDED.ended_at,
                    duration_ms     = EXCLUDED.duration_ms,
                    tool_count      = EXCLUDED.tool_count,
                    tool_summary    = EXCLUDED.tool_summary,
                    error_message   = COALESCE(EXCLUDED.error_message,
                                               agent_run.error_message),
                    error_type      = COALESCE(EXCLUDED.error_type,
                                               agent_run.error_type),
                    error_traceback = COALESCE(EXCLUDED.error_traceback,
                                               agent_run.error_traceback),
                    trace           = COALESCE(EXCLUDED.trace, agent_run.trace),
                    flagged         = agent_run.flagged OR EXCLUDED.flagged
                """
            ),
            {
                "run_id": row["run_id"],
                "thread_id": row.get("thread_id"),
                "agent_name": row["agent_name"],
                "user_id": row.get("user_id"),
                "model": row.get("model"),
                "status": row["status"],
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_ms": row.get("duration_ms"),
                "tool_count": row.get("tool_count", 0),
                "tool_summary": json.dumps(row.get("tool_summary") or []),
                "error_message": row.get("error_message"),
                "error_type": row.get("error_type"),
                "error_traceback": row.get("error_traceback"),
                "trace": json.dumps(row["trace"]) if row.get("trace") else None,
                "flagged": row.get("flagged", False),
            },
        )


async def record_run_trace(
    *,
    run_id: str,
    thread_id: str | None,
    agent_name: str,
    user_id: str,
    model: str | None,
    events: list[dict[str, Any]],
    folded: dict[str, Any] | None,
    started_ms: int | None = None,
    ended_ms: int | None = None,
    flagged: bool = False,
    organization_id: str | None,
) -> None:
    """Build + persist the agent_run trace row at the run boundary. Never raises."""
    try:
        import asyncio

        row = build_run_trace_row(
            run_id=run_id, thread_id=thread_id, agent_name=agent_name,
            user_id=user_id, model=model, events=events, folded=folded,
            started_ms=started_ms, ended_ms=ended_ms, flagged=flagged,
        )
        await asyncio.to_thread(_persist_row, row, organization_id)
        _log.info(
            "run_trace.recorded",
            run_id=run_id[:40],
            agent=agent_name,
            status=row["status"],
            tools=row["tool_count"],
            trace_kept=row["trace"] is not None,
        )
    except Exception as exc:
        _log.warning("run_trace.record_failed", run_id=run_id[:40], error=str(exc))
