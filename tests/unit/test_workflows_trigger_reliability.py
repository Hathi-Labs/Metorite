"""Trigger reliability — timezones, dropped ticks, and the external hook URL.

Three failures a scheduled automation can have that no amount of engine
correctness fixes:

  1. it fires at the wrong wall-clock time (cron was UTC-only),
  2. it does not fire at all and leaves no trace (the tick was CAS-claimed
     before the run was refused), and
  3. its webhook URL is one the sender cannot successfully call.

No Postgres: the scanner is driven over a scripted session, and the cron maths
is a pure function.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.workflows import crud, scheduler, unattended
from gateway.routes.workflows.core import HOOK_PATH, hook_url


class _ScriptedDb:
    def __init__(self, results: list[Any], rowcounts: list[int] | None = None) -> None:
        self._results = list(results)
        self._rowcounts = list(rowcounts or [])
        self.statements: list[str] = []
        self.params: list[dict] = []

    async def execute(self, clause: Any, params: dict | None = None) -> Any:
        self.statements.append(str(clause))
        self.params.append(params or {})
        value = self._results.pop(0) if self._results else None
        count = self._rowcounts.pop(0) if self._rowcounts else 1

        class _Result:
            rowcount = count

            def fetchone(self) -> Any:
                return value

            def fetchall(self) -> list:
                return value or []

        return _Result()

    async def commit(self) -> None:
        return None

    async def close(self) -> None:
        return None


# ── Timezone: a schedule is a wall clock, not an offset ─────────────────────


def _tick(cron: str, tz: str, last: datetime | None, now: datetime):
    return scheduler.compute_due_fire(cron, last, now, tz)


#: An armed schedule always has a baseline (see _claim_baseline); passing one
#: is what "this trigger is live" looks like to compute_due_fire.
YESTERDAY = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


def test_cron_fires_on_the_makers_wall_clock_not_the_servers() -> None:
    """09:00 Asia/Kolkata is 03:30 UTC — the whole point of the field."""
    # 03:29 UTC: not yet 9am in Kolkata.
    assert _tick(
        "0 9 * * *", "Asia/Kolkata", YESTERDAY, datetime(2026, 7, 30, 3, 29, tzinfo=UTC)
    ) is None
    fired = _tick(
        "0 9 * * *", "Asia/Kolkata", YESTERDAY, datetime(2026, 7, 30, 3, 31, tzinfo=UTC)
    )
    assert fired is not None
    assert fired.astimezone(UTC).hour == 3 and fired.astimezone(UTC).minute == 30


def test_the_same_expression_in_utc_fires_at_a_different_instant() -> None:
    assert _tick(
        "0 9 * * *", "UTC", YESTERDAY, datetime(2026, 7, 30, 3, 31, tzinfo=UTC)
    ) is None
    assert _tick(
        "0 9 * * *", "UTC", YESTERDAY, datetime(2026, 7, 30, 9, 1, tzinfo=UTC)
    ) is not None


def test_a_nine_am_schedule_stays_nine_am_across_a_dst_change() -> None:
    """New York is UTC-4 in July and UTC-5 in January. A fixed offset would
    drift by an hour; a zone does not."""
    summer = _tick(
        "0 9 * * *", "America/New_York",
        datetime(2026, 7, 20, 0, 0, tzinfo=UTC),
        datetime(2026, 7, 20, 23, 0, tzinfo=UTC),
    )
    winter = _tick(
        "0 9 * * *", "America/New_York",
        datetime(2026, 1, 20, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 20, 23, 0, tzinfo=UTC),
    )
    assert summer is not None and winter is not None
    # Same local hour…
    assert summer.astimezone(winter.tzinfo).hour == 9
    assert winter.astimezone(winter.tzinfo).hour == 9
    # …different UTC instant. That difference IS daylight saving.
    assert summer.astimezone(UTC).hour != winter.astimezone(UTC).hour


def test_downtime_across_several_ticks_collapses_to_one_catch_up() -> None:
    """Three days down must not fire three runs at boot."""
    due = _tick(
        "0 9 * * *", "UTC",
        datetime(2026, 7, 27, 9, 0, tzinfo=UTC),
        datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
    )
    assert due == datetime(2026, 7, 30, 9, 0, tzinfo=UTC)  # the most recent one only


def test_default_timezone_is_utc_when_omitted() -> None:
    assert _tick(
        "0 9 * * *", "", YESTERDAY, datetime(2026, 7, 30, 9, 1, tzinfo=UTC)
    ) is not None


# ── The bootstrap trap: a schedule that has never fired ─────────────────────


def test_a_never_fired_schedule_yields_no_tick_from_the_cron_maths() -> None:
    """compute_due_fire only ever looks FORWARD, so with no baseline there is
    no interval to search and it correctly reports nothing. The caller must
    therefore arm the trigger — see the scanner test below. This test exists to
    pin WHY that arming step is load-bearing rather than defensive."""
    now = datetime(2026, 7, 30, 9, 1, 0, 123456, tzinfo=UTC)  # real clocks have µs
    assert _tick("0 9 * * *", "UTC", None, now) is None


def test_a_new_schedule_is_armed_on_first_sight_instead_of_fired(monkeypatch) -> None:
    """The bug this fixes: an unarmed schedule produced no tick, so
    last_fired_at was never set, so it produced no tick — forever. Measured
    before the fix: a brand-new daily schedule fired 0 times in a week."""

    async def unused_start_run(**_kw: Any) -> str:  # pragma: no cover
        raise AssertionError("first sight must arm the schedule, not fire it")

    db = _ScriptedDb([[_trigger_row(last_fired_at=None)], None])
    fired, skipped = _run_scan(monkeypatch, db, start_run=unused_start_run)

    assert fired == 0 and skipped == []
    arm = db.statements[1]
    assert "SET last_fired_at = :now" in arm
    # Guarded, so two workers racing on a new trigger cannot fight over it and
    # an already-running schedule can never be rewound.
    assert "last_fired_at IS NULL" in arm


def test_an_armed_schedule_then_fires_normally(monkeypatch) -> None:
    """The other half: once armed, the next due tick runs."""
    started: list[dict] = []

    async def ok_start_run(**kw: Any) -> str:
        started.append(kw)
        return "run-1"

    async def load_version(*_a: Any) -> dict:
        return {"entry": "start", "blocks": []}

    row = _trigger_row(
        config={"cron": "0 9 * * *"},
        last_fired_at=datetime(2026, 7, 29, 9, 0, tzinfo=UTC),
    )
    db = _ScriptedDb([[row], None])
    fired, _ = _run_scan(
        monkeypatch, db, start_run=ok_start_run, load_version_serialized=load_version
    )
    assert fired == 1 and len(started) == 1


# ── Timezone: rejected at save, not discovered at fire time ─────────────────


def test_unknown_timezone_is_a_422_at_save_time() -> None:
    spec = crud.TriggerSpec(
        kind="schedule", config={"cron": "0 9 * * *", "timezone": "Mars/Olympus"}
    )
    with pytest.raises(HTTPException) as exc:
        crud._validate_trigger_specs([spec])
    assert exc.value.status_code == 422
    assert "Mars/Olympus" in str(exc.value.detail)


def test_a_real_zone_and_a_valid_cron_pass() -> None:
    crud._validate_trigger_specs(
        [crud.TriggerSpec(kind="schedule", config={"cron": "*/5 * * * *", "timezone": "Asia/Kolkata"})]
    )


def test_a_bad_cron_is_still_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        crud._validate_trigger_specs(
            [crud.TriggerSpec(kind="schedule", config={"cron": "not a cron"})]
        )
    assert exc.value.status_code == 422


# ── A claimed tick always leaves a trace ────────────────────────────────────


def _trigger_row(**over: Any) -> SimpleNamespace:
    base = dict(
        trigger_id="t1",
        config={"cron": "* * * * *"},
        last_fired_at=datetime(2026, 7, 30, 11, 58, tzinfo=UTC),
        workflow_id="w1",
        name="Nightly sync",
        latest_version=4,
        variables={},
        organization_id="org-1",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _run_scan(monkeypatch, db: _ScriptedDb, **stubs: Any) -> tuple[int, list[dict]]:
    skipped: list[dict] = []

    async def fake_record(**kw: Any) -> str:
        skipped.append(kw)
        return "run-x"

    async def fake_orgs() -> list[str]:
        return ["org-1"]

    @asynccontextmanager
    async def fake_tenant_session(org: str | None = None) -> Any:
        yield db

    # H-328 part (b): the scan reads the orgs, checks the allowlist, then binds
    # one org for each step. Org "org-1" is the one listed org here.
    monkeypatch.setattr(scheduler, "_organization_ids", fake_orgs)
    monkeypatch.setattr(scheduler, "_tenant_session", fake_tenant_session)
    monkeypatch.setattr(
        unattended, "get_settings",
        lambda: SimpleNamespace(workflow_unattended_orgs="org-1"),
    )
    monkeypatch.setattr(scheduler, "record_skipped_run", fake_record)
    for name, value in stubs.items():
        monkeypatch.setattr(scheduler, name, value)
    fired = asyncio.run(scheduler._scan_once(datetime(2026, 7, 30, 12, 0, tzinfo=UTC)))
    return fired, skipped


def test_a_tick_refused_by_the_concurrency_cap_is_recorded(monkeypatch) -> None:
    """The claim is already committed, so the tick can never be re-offered.
    Silence here reads to the maker as 'my schedule just stopped'."""

    async def rejecting_start_run(**_kw: Any) -> str:
        raise scheduler.RunRejected("this workflow already has the maximum concurrent runs")

    async def load_version(*_a: Any) -> dict:
        return {"entry": "start", "blocks": []}

    db = _ScriptedDb([[_trigger_row()], None])
    fired, skipped = _run_scan(
        monkeypatch, db,
        start_run=rejecting_start_run,
        load_version_serialized=load_version,
    )

    assert fired == 0
    assert len(skipped) == 1
    assert skipped[0]["workflow_id"] == "w1"
    assert skipped[0]["trigger_kind"] == "schedule"
    assert "maximum concurrent" in skipped[0]["reason"]


def test_a_tick_whose_published_version_vanished_is_recorded(monkeypatch) -> None:
    async def missing_version(*_a: Any) -> None:
        return None

    async def unused_start_run(**_kw: Any) -> str:  # pragma: no cover - must not run
        raise AssertionError("start_run must not be called without a version")

    db = _ScriptedDb([[_trigger_row()], None])
    fired, skipped = _run_scan(
        monkeypatch, db,
        start_run=unused_start_run,
        load_version_serialized=missing_version,
    )

    assert fired == 0
    assert len(skipped) == 1
    assert "republish" in skipped[0]["reason"]


def test_a_healthy_tick_records_nothing_and_fires(monkeypatch) -> None:
    started: list[dict] = []

    async def ok_start_run(**kw: Any) -> str:
        started.append(kw)
        return "run-1"

    async def load_version(*_a: Any) -> dict:
        return {"entry": "start", "blocks": []}

    db = _ScriptedDb([[_trigger_row()], None])
    fired, skipped = _run_scan(
        monkeypatch, db, start_run=ok_start_run, load_version_serialized=load_version
    )

    assert fired == 1 and skipped == []
    assert started[0]["trigger_kind"] == "schedule"


def test_the_claim_is_a_cas_so_a_losing_worker_does_not_fire(monkeypatch) -> None:
    """Two gateways scanning at once must produce exactly one run."""

    async def unused_start_run(**_kw: Any) -> str:  # pragma: no cover
        raise AssertionError("the losing worker must not start a run")

    db = _ScriptedDb([[_trigger_row()], None], rowcounts=[1, 0])
    fired, skipped = _run_scan(monkeypatch, db, start_run=unused_start_run)

    assert fired == 0 and skipped == []  # lost the claim: not ours to record
    claim = db.statements[1]
    assert "last_fired_at IS NULL OR last_fired_at <" in claim


# ── Saving the workflow must not re-arm its schedule ────────────────────────


def test_an_unchanged_schedule_keeps_its_fire_state_across_a_save() -> None:
    """Triggers are replaced wholesale on save, but `last_fired_at` is the
    scheduler's dedup state, not part of the maker's document. Dropping it on
    every save would re-arm the cron whenever anyone touched the canvas."""
    same = ("schedule", {"cron": "0 9 * * *", "timezone": "Asia/Kolkata"})
    assert crud._trigger_identity(*same) == crud._trigger_identity(*same)


@pytest.mark.parametrize(
    "changed",
    [
        {"cron": "0 10 * * *", "timezone": "Asia/Kolkata"},  # different time
        {"cron": "0 9 * * *", "timezone": "Europe/London"},  # different zone
    ],
)
def test_editing_the_schedule_re_arms_it(changed: dict) -> None:
    """A different schedule must NOT inherit the old baseline — otherwise
    moving a 9am job to 10am could fire an immediate catch-up tick."""
    original = crud._trigger_identity(
        "schedule", {"cron": "0 9 * * *", "timezone": "Asia/Kolkata"}
    )
    assert crud._trigger_identity("schedule", changed) != original


def test_non_schedule_kinds_are_identified_by_kind_alone() -> None:
    """Webhook/event carry no scheduler state; their config changes freely."""
    assert crud._trigger_identity("webhook", {"secret": "a"}) == crud._trigger_identity(
        "webhook", {"secret": "b"}
    )


def test_update_carries_the_baseline_onto_the_replacement_row(monkeypatch) -> None:
    fired_at = datetime(2026, 7, 29, 3, 30, tzinfo=UTC)
    wf = SimpleNamespace(
        id="w1", name="Nightly", description="", owner_email="o@x.io",
        status="published", latest_version=2, graph={}, variables={},
        hook_token="tok", created_at=None, updated_at=None,
        disabled_reason=None, disabled_at=None,
    )
    old = SimpleNamespace(
        kind="schedule",
        config={"cron": "0 9 * * *", "timezone": "Asia/Kolkata"},
        last_fired_at=fired_at,
    )
    # load wf → previous triggers → DELETE → INSERT → reload wf → load triggers
    db = _ScriptedDb([wf, [old], None, None, wf, []])

    @asynccontextmanager
    async def fake_tenant_session() -> Any:
        yield db
        await db.commit()

    monkeypatch.setattr(crud, "_tenant_session", fake_tenant_session)

    body = crud.WorkflowUpdate(
        triggers=[
            crud.TriggerSpec(
                kind="schedule",
                config={"cron": "0 9 * * *", "timezone": "Asia/Kolkata"},
                enabled=True,
            )
        ]
    )
    asyncio.run(
        crud.update_workflow("w1", body, user=SimpleNamespace(email="maker@x.io"))
    )

    insert = next(
        p
        for s, p in zip(db.statements, db.params, strict=True)
        if "INSERT INTO workflow_triggers" in s
    )
    assert insert["last_fired"] == fired_at


# ── The webhook URL handed to an external caller ────────────────────────────


def test_hook_url_is_absolute_and_gateway_origin(monkeypatch) -> None:
    monkeypatch.setattr(
        "gateway.routes.workflows.core.get_settings",
        lambda: SimpleNamespace(public_api_base_url="https://api.example.com/"),
    )
    assert hook_url("tok123") == "https://api.example.com/workflows/hooks/tok123"


def test_hook_url_is_empty_when_the_public_origin_is_unset(monkeypatch) -> None:
    """Better to show the path and say so than to invent an origin that 404s
    in production — a silently dead webhook is the failure being avoided."""
    monkeypatch.setattr(
        "gateway.routes.workflows.core.get_settings",
        lambda: SimpleNamespace(public_api_base_url=""),
    )
    assert hook_url("tok123") == ""
    assert HOOK_PATH.format(token="tok123") == "/workflows/hooks/tok123"


def test_hook_url_of_a_workflow_without_a_token_is_empty() -> None:
    assert hook_url(None) == ""
    assert hook_url("") == ""
