"""Unit tests for E2 observability: correlated logging + durable run trace.

Phase 1 — bind_run_context threads run_id/thread_id/agent/user into structlog
contextvars so every log line for a run is filterable, and JSON output is
machine-parseable. Phase 2 — build_run_trace_row derives the durable agent_run
row (metadata+status for all runs; full trace only for errored/flagged).
"""
from __future__ import annotations

import structlog
from acb_common import (
    bind_run_context,
    clear_run_context,
    get_run_context,
)
from gateway.run_trace import _derive_status, build_run_trace_row

# ── Phase 1: correlated logging ─────────────────────────────────────────────
#
# We assert on the CONTEXTVARS bind/clear manage — that IS the mechanism that
# puts the fields on every line: the configured processor chain includes
# structlog.contextvars.merge_contextvars, so anything in the contextvars at
# emit time lands on the rendered line. (An end-to-end JSON render is verified
# manually; structlog's cache_logger_on_first_use makes cross-test handler
# capture unreliable, so we test the contextvar state directly here.)


def test_merge_contextvars_is_in_the_configured_chain():
    # The whole scheme relies on this processor being present.
    from acb_common import configure_logging
    configure_logging("INFO", json_logs=True)
    names = [
        getattr(p, "__name__", getattr(type(p), "__name__", ""))
        for p in structlog.get_config()["processors"]
    ]
    assert any("merge_contextvars" in n for n in names)


def test_bound_context_visible_to_any_emitter_on_the_task():
    # After bind, ANY code on this task (e.g. the LLM usage emitter that passes
    # no ids) sees the run fields via the contextvars merge processor.
    bind_run_context(run_id="r1", thread_id="t1", agent="sales", user="u@x")
    try:
        merged = structlog.contextvars.get_contextvars()
        assert merged["run_id"] == "r1"
        assert merged["thread_id"] == "t1"
        assert merged["agent"] == "sales"
        assert merged["user"] == "u@x"
    finally:
        clear_run_context()


def test_clear_removes_context_no_leak():
    bind_run_context(run_id="r1", agent="a", instance="u:a@b.com")
    clear_run_context()
    merged = structlog.contextvars.get_contextvars()
    assert "run_id" not in merged
    assert "agent" not in merged
    # WS-6a: the tenant partition must not survive into the next run either —
    # a leaked instance would attribute one member's spend to another's.
    assert "instance" not in merged


# ── WS-6a: the tenant partition is part of the run stamp ────────────────────
# `instance` completes decision D1's (run_id, member, agent, instance) tuple.
# Its vocabulary is the manifest's (AgentManifest.instance_key): '' shared,
# u:<email> personal, t:<team> team — the same key agent_blob carries.

def test_instance_is_bound_and_readable_like_every_other_run_field():
    bind_run_context(
        run_id="r-i", agent="email-assistant", user="alice@fracktal.in",
        instance="u:alice@fracktal.in",
    )
    try:
        assert get_run_context()["instance"] == "u:alice@fracktal.in"
        # And it reaches the log line the same way the others do.
        assert structlog.contextvars.get_contextvars()["instance"] == (
            "u:alice@fracktal.in"
        )
    finally:
        clear_run_context()


def test_shared_agent_binds_no_instance_key_at_all():
    # '' is how the manifest/store spell "shared". It must land as an ABSENT
    # key — never the empty string, and never the literal "''" (a quoted empty
    # value in the feed would read as a real, unreachable partition).
    bind_run_context(run_id="r-s", agent="task-manager", instance="")
    try:
        ctx = get_run_context()
        assert ctx == {"run_id": "r-s", "agent": "task-manager"}
        assert "instance" not in ctx
    finally:
        clear_run_context()


def test_inherit_and_run_context_keys_match():
    """Drift gate: the twin tuples must be extended together.

    ``_log._RUN_CONTEXT_KEYS`` decides what a run BINDS; ``activity._INHERIT``
    decides what an event whose emitter omitted a field COPIES from that bind.
    A key in one and not the other half-lands: bound onto every log line but
    missing from every activity/cost event, or the reverse. Both AGENTS.md
    files that document the stamp say "extend those two tuples together" —
    this is the assertion behind that sentence.
    """
    from acb_common import _log, activity

    bound = set(_log._RUN_CONTEXT_KEYS)
    inherited = set(activity._INHERIT)
    assert inherited == bound, (
        "acb_common.activity._INHERIT and acb_common._log._RUN_CONTEXT_KEYS "
        f"have drifted: bound-but-not-inherited={sorted(bound - inherited)}, "
        f"inherited-but-not-bound={sorted(inherited - bound)}. Add the key to "
        "BOTH tuples (and to bind_run_context's signature + its field list), "
        "or attribution half-lands — see packages/AGENTS.md and "
        "specs/observability_e2.md §7."
    )


def test_a_second_bind_tops_up_instance_without_disturbing_the_first():
    # The ordering fix WS-6a ships: the run boundary binds early (so a failure
    # during agent load is still correlated), then _bind_run_instance tops up
    # `instance` once loaded.config makes it resolvable.
    bind_run_context(run_id="r-2b", thread_id="t", agent="a", user="u@x",
                     source="chat")
    try:
        bind_run_context(instance="t:growth")
        assert get_run_context() == {
            "run_id": "r-2b", "thread_id": "t", "agent": "a", "user": "u@x",
            "source": "chat", "instance": "t:growth",
        }
    finally:
        clear_run_context()


def test_get_run_context_reflects_bound_fields():
    bind_run_context(run_id="rX", agent="emailer")
    try:
        ctx = get_run_context()
        assert ctx == {"run_id": "rX", "agent": "emailer"}
    finally:
        clear_run_context()
    assert get_run_context() == {}


def test_bind_ignores_empty_values():
    bind_run_context(run_id="r", thread_id="", agent=None, user="")
    try:
        assert get_run_context() == {"run_id": "r"}
    finally:
        clear_run_context()


# ── Phase 2: durable run-trace row ──────────────────────────────────────────

_TOOL_EVENTS = [
    {"name": "search", "status": "done", "result": "hits"},
    {"name": "draft", "status": "done", "result": "a draft body"},
]


def _folded(**over):
    base = {
        "content": "Here is your answer.",
        "tool_events": _TOOL_EVENTS,
        "reasoning": '["thinking..."]',
        "custom_events": [],
        "timestamp": 1000,
    }
    base.update(over)
    return base


def test_successful_run_stores_metadata_no_trace_body():
    events = [{"type": "RUN_FINISHED"}]
    row = build_run_trace_row(
        run_id="r1", thread_id="t1", agent_name="sales", user_id="u",
        model="tier3", events=events, folded=_folded(),
        started_ms=200, ended_ms=1000,
    )
    assert row["status"] == "completed"
    assert row["tool_count"] == 2
    assert row["tool_summary"] == [
        {"name": "search", "status": "done"},
        {"name": "draft", "status": "done"},
    ]
    assert row["duration_ms"] == 800
    # Full trace (content / results) is NOT retained for a successful run.
    assert row["trace"] is None
    assert row["error_message"] is None


def test_errored_run_keeps_full_trace_and_error():
    events = [
        {"type": "TOOL_CALL_START"},
        {"type": "RUN_ERROR", "message": "boom: provider 500"},
    ]
    row = build_run_trace_row(
        run_id="r2", thread_id="t2", agent_name="emailer", user_id="u",
        model="tier2", events=events, folded=_folded(),
    )
    assert row["status"] == "error"
    assert row["error_message"] == "boom: provider 500"
    # Full trace IS retained for an errored run (content + tool results).
    assert row["trace"] is not None
    assert row["trace"]["content"] == "Here is your answer."
    assert row["trace"]["tool_events"] == _TOOL_EVENTS


def test_flagged_successful_run_keeps_trace():
    row = build_run_trace_row(
        run_id="r3", thread_id="t3", agent_name="a", user_id="u",
        model="m", events=[{"type": "RUN_FINISHED"}], folded=_folded(),
        flagged=True,
    )
    assert row["status"] == "completed"
    assert row["flagged"] is True
    assert row["trace"] is not None  # flag forces retention


def test_cancelled_run_status():
    events = [{"type": "RUN_FINISHED", "cancelled": True}]
    row = build_run_trace_row(
        run_id="r4", thread_id="t4", agent_name="a", user_id="u",
        model="m", events=events, folded=_folded(),
    )
    assert row["status"] == "cancelled"
    assert row["trace"] is not None  # cancelled keeps the trace too


def test_derive_status_error_wins_over_cancel():
    events = [
        {"type": "RUN_ERROR", "message": "x"},
        {"type": "RUN_FINISHED", "cancelled": True},
    ]
    status, msg, _ = _derive_status(events)
    assert status == "error"
    assert msg == "x"


def test_duration_derived_from_event_stream_ids():
    # Regression: duration_ms was null in prod because started_ms wasn't passed.
    # It now derives from the first/last event's Redis stream-id timestamps.
    events = [
        {"type": "RUN_STARTED", "_stream_id": "1000-0"},
        {"type": "TEXT_MESSAGE_CONTENT", "_stream_id": "2000-0"},
        {"type": "RUN_FINISHED", "_stream_id": "3500-0"},
    ]
    row = build_run_trace_row(
        run_id="rd", thread_id="t", agent_name="a", user_id="u", model="m",
        events=events, folded=_folded(),
    )
    assert row["duration_ms"] == 2500  # 3500 - 1000
    # M7: started_ms is now carried on the row so _persist_row can write the
    # true started_at (was defaulting to now() ≈ run end, breaking ordering).
    assert row["started_ms"] == 1000
    assert row["ended_ms"] == 3500


def test_explicit_times_win_over_event_derivation():
    events = [{"type": "RUN_FINISHED", "_stream_id": "9999-0"}]
    row = build_run_trace_row(
        run_id="rd2", thread_id="t", agent_name="a", user_id="u", model="m",
        events=events, folded=_folded(), started_ms=100, ended_ms=600,
    )
    assert row["duration_ms"] == 500  # explicit values, not the event ms


def test_no_folded_run_still_produces_a_row():
    # A run that errored before producing any message (folded is None) still
    # yields a trace row — that itself is a debuggable outcome.
    events = [{"type": "RUN_ERROR", "message": "died immediately"}]
    row = build_run_trace_row(
        run_id="r5", thread_id="t5", agent_name="a", user_id="u",
        model="m", events=events, folded=None,
    )
    assert row["status"] == "error"
    assert row["error_message"] == "died immediately"
    assert row["tool_count"] == 0
    assert row["trace"] is None  # nothing to store, but the row exists


# ── Phase 6.8: Avatar customization (Office / Avatar Studio) ─────────────────
#
# The avatar-override endpoints let an operator pin a per-agent look / custom
# Pixel Lab sprite. These test the guards + best-effort contracts that don't
# need a live DB or network (name validation, load-degradation, the generate
# 503 when Pixel Lab isn't configured).

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from gateway.routes import observability as _obs  # noqa: E402

# H-201: the readers take the viewer's tenant. Any non-empty id serves here,
# because these tests stub the database out.
_ORG = "00000000-0000-0000-0000-0000000000a1"


def _viewer_user():
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email="op@obs.test", role=UserRole.EMPLOYEE,
                       organization_id=_ORG)


_VIEWER_USER = _viewer_user()


def test_agent_name_regex_accepts_valid_and_rejects_junk():
    ok = ["orchestrator", "email-assistant", "task_manager", "a", "apis.config"]
    bad = ["", "-leading", "UPPER", "has space", "x" * 65, "bad/slash"]
    assert all(_obs._AGENT_NAME_RE.match(n) for n in ok)
    assert not any(_obs._AGENT_NAME_RE.match(n) for n in bad)


def test_load_avatars_degrades_to_empty_when_db_unavailable(monkeypatch):
    # Best-effort contract: a DB failure must NOT raise (it would 500 the whole
    # roster) — it returns {} so the office falls back to derived looks.
    import acb_graph

    def _boom(*_a, **_k):
        raise RuntimeError("no db")

    monkeypatch.setattr(acb_graph, "tenant_session", _boom)
    assert _obs._load_avatars(_ORG) == {}


def test_load_avatars_with_no_tenant_skips_db_entirely(monkeypatch):
    # H-201: no tenant reads nothing, and no session of any kind opens.
    import acb_graph

    def _boom(*_a, **_k):
        raise AssertionError("must not open a session with no tenant")

    monkeypatch.setattr(acb_graph, "tenant_session", _boom)
    monkeypatch.setattr(acb_graph, "get_session", _boom)
    assert _obs._load_avatars(None) == {}


@pytest.mark.asyncio
async def test_generate_avatar_503_without_api_key(monkeypatch):
    monkeypatch.delenv("PIXELLAB_API_KEY", raising=False)
    with pytest.raises(HTTPException) as ei:
        await _obs.generate_avatar(_obs.AvatarGenerate(description="a coder"))
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_generate_avatar_400_on_empty_description(monkeypatch):
    monkeypatch.setenv("PIXELLAB_API_KEY", "test-key")
    with pytest.raises(HTTPException) as ei:
        await _obs.generate_avatar(_obs.AvatarGenerate(description="   "))
    assert ei.value.status_code == 400


# ── Roster: Custom Apps as first-class office cast (RFC §4.6) ────────────────
# A live "app:<slug>" presence key (created by publish_app_activity's
# start/end pair, RFC docs/app-workshop/README.md §4.6) must surface in
# /observability/roster as its own kind="app" entry with the app's real
# name/icon — not swallowed into the generic "live but unregistered agent"
# fallback, and not shown at all when idle (unlike agents, which list the
# full registry regardless of live status).

def test_load_app_details_degrades_to_empty_when_db_unavailable(monkeypatch):
    import acb_graph

    def _boom(*_a, **_k):
        raise RuntimeError("no db")

    monkeypatch.setattr(acb_graph, "tenant_session", _boom)
    assert _obs._load_app_details(["filament-tracker"], _ORG) == {}


def test_load_app_details_empty_slugs_skips_db_entirely(monkeypatch):
    import acb_graph

    def _boom(*_a, **_k):
        raise AssertionError("must not touch the DB for an empty slug list")

    monkeypatch.setattr(acb_graph, "tenant_session", _boom)
    monkeypatch.setattr(acb_graph, "get_session", _boom)
    assert _obs._load_app_details([], _ORG) == {}
    # H-201: no tenant reads nothing either.
    assert _obs._load_app_details(["filament-tracker"], None) == {}


@pytest.mark.asyncio
async def test_roster_includes_a_live_app_as_a_distinct_kind(monkeypatch):
    from gateway.routes import agent as _agent_routes

    monkeypatch.setattr(_agent_routes, "_AGENT_REGISTRY", [])
    monkeypatch.setattr(_agent_routes, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(_agent_routes, "_load_agent_aliases", lambda: {})

    async def _fake_active_runs(*, organization_id):
        return [
            {"agent": "app:filament-tracker", "kind": "app", "phase": "start",
             "ts": "2026-07-26T00:00:00Z", "source": "apps"},
        ]

    monkeypatch.setattr(_obs, "active_runs", _fake_active_runs)
    monkeypatch.setattr(_obs, "_load_avatars", lambda _org: {})
    monkeypatch.setattr(
        _obs, "_load_app_details",
        lambda slugs, _org: {"filament-tracker": {"name": "Filament Tracker", "icon": "🧵"}},
    )

    out = await _obs.roster(viewer=_VIEWER_USER)
    apps = [a for a in out["agents"] if a["kind"] == "app"]
    assert len(apps) == 1
    entry = apps[0]
    assert entry["slug"] == "filament-tracker"
    assert entry["display_name"] == "Filament Tracker"
    assert entry["icon"] == "🧵"
    assert entry["status"] == "working"
    assert entry["active_runs"] == 1
    # Never duplicated into the generic live-but-unregistered agent fallback.
    assert not any(
        a["kind"] == "agent" and a["name"] == "app:filament-tracker"
        for a in out["agents"]
    )


@pytest.mark.asyncio
async def test_roster_has_no_idle_app_rows(monkeypatch):
    # Unlike agents (full registry shown even when idle), an app with no live
    # presence must not appear at all — "show apps being used AT THE MOMENT".
    from gateway.routes import agent as _agent_routes

    monkeypatch.setattr(_agent_routes, "_AGENT_REGISTRY", [])
    monkeypatch.setattr(_agent_routes, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(_agent_routes, "_load_agent_aliases", lambda: {})

    async def _fake_active_runs(*, organization_id):
        return []

    def _unreachable(_slugs, _org):
        raise AssertionError("must not query app details with no live apps")

    monkeypatch.setattr(_obs, "active_runs", _fake_active_runs)
    monkeypatch.setattr(_obs, "_load_avatars", lambda _org: {})
    monkeypatch.setattr(_obs, "_load_app_details", _unreachable)

    out = await _obs.roster(viewer=_VIEWER_USER)
    assert not any(a["kind"] == "app" for a in out["agents"])


@pytest.mark.asyncio
async def test_roster_agent_entries_are_tagged_kind_agent(monkeypatch):
    from gateway.routes import agent as _agent_routes

    monkeypatch.setattr(_agent_routes, "_AGENT_REGISTRY",
                         [{"name": "email-assistant", "description": "d"}])
    monkeypatch.setattr(_agent_routes, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(_agent_routes, "_load_agent_aliases", lambda: {})

    async def _fake_active_runs(*, organization_id):
        return []

    monkeypatch.setattr(_obs, "active_runs", _fake_active_runs)
    monkeypatch.setattr(_obs, "_load_avatars", lambda _org: {})

    out = await _obs.roster(viewer=_VIEWER_USER)
    assert all(a["kind"] in ("agent", "app") for a in out["agents"])
    assert any(a["kind"] == "agent" and a["name"] == "email-assistant"
               for a in out["agents"])
