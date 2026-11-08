"""WS-PKG PKG-10b — the daily expiry loop of the package host (R8).

Spec: ``project-docs/specs/package_host.md`` §6.2 and the PKG-10b row of
§10. Code: ``gateway/package_host/expiry_loop.py``. The store function that
the loop calls, ``expire_due``, has its own R8 suite:
``test_package_lifetime_r8.py``. This suite uses its world and its helpers.

The tick runs as the app role, through ``tenant_engine_scope``, so FORCE RLS
binds each statement of ``expire_due``. Each check of rows reads as the
superuser. Org A and org B come from the ``promoted`` fixture.

Run (one R8 suite at a time, because the ``promoted`` fixture drops
``<db>_h3rls``)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_package_expiry_loop_r8.py -v -rs -p no:xdist

Without the database each R8 test here SKIPS, and the run reads green. A
skip is not a pass. The hermetic tests run with no database.

**Acceptance (PKG-10b).**

a. With ``PACKAGE_EXPIRY_ENABLED`` off, a tick reads nothing: no org list,
   no session, no statement. The start makes no task.
b. With the flag on, a tick calls ``expire_due`` one time for each org of
   ``PACKAGE_HOST_ORGS``, in a session bound to that org, with a time that
   has a zone. A due install of each org is archived, and a spent one is
   deleted.
c. An org outside ``PACKAGE_HOST_ORGS`` does not change.
d. A failure in one org does not stop the other orgs, and the tick returns.
e. The share of org A never touches a row of org B.
f. The flag defaults to off. ``main.py`` starts and stops the loop and never
   reads the flag. The loop starts only when the flag is on, and the first
   tick waits for the first delay.
g. The clock guard. When the host clock differs from the database clock by
   more than ``MAX_CLOCK_SKEW``, the tick skips the org. It does not call
   ``expire_due``, and it counts and logs the skip.
h. A cancel in the middle of an org rolls that org back and ends the loop.
   A stop between two orgs starts no further org.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from gateway.package_host import expiry_loop as loop
from gateway.package_host import store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_package_lifetime_r8 import (  # noqa: F401
    DAY,
    ZED,
    _admin_write,
    _claim,
    _dsn,
    _install,
    _k,
    _now,
    _row,
    world,
)

_REPO = Path(__file__).resolve().parents[2]
MAIN = _REPO / "apps" / "services" / "gateway" / "gateway" / "main.py"


@pytest.fixture(autouse=True)
def _fresh_stop(monkeypatch: Any) -> Any:
    """Each test gets a new stop event. An event that waited once is bound
    to the event loop of that test, and each test runs its own loop."""
    monkeypatch.setattr(loop, "_stopping", asyncio.Event())
    monkeypatch.setattr(loop, "_task", None)
    yield


def _flag(monkeypatch: Any, on: bool) -> None:
    monkeypatch.setattr(get_settings(), "package_expiry_enabled", on, raising=False)


def _host_orgs(monkeypatch: Any, *orgs: str) -> None:
    monkeypatch.setattr(get_settings(), "package_host_orgs", ",".join(orgs), raising=False)


# ═══════════════════════════════════════════════════════════════════════════
# Hermetic: the flag and the start (a, f)
# ═══════════════════════════════════════════════════════════════════════════


def test_the_flag_defaults_off() -> None:
    assert type(get_settings()).model_fields["package_expiry_enabled"].default is False


def test_a_the_flag_off_tick_reads_nothing(monkeypatch: Any) -> None:
    """With the flag off, a tick lists no org, opens no session and runs no
    statement. Each door of the module is a spy that fails the test."""
    _flag(monkeypatch, False)
    _host_orgs(monkeypatch, "*")
    opened: list[str] = []

    async def no_db() -> Any:
        opened.append("get_db")
        raise AssertionError("the tick opened an unbound session")

    def no_session(org: str) -> Any:
        opened.append(f"tenant_session:{org}")
        raise AssertionError("the tick opened a tenant session")

    async def no_expire(db: Any, **kw: Any) -> Any:
        opened.append("expire_due")
        raise AssertionError("the tick called expire_due")

    monkeypatch.setattr(loop, "get_db", no_db)
    monkeypatch.setattr(loop, "tenant_session", no_session)
    monkeypatch.setattr(loop.store, "expire_due", no_expire)
    assert asyncio.run(loop.run_tick()) == loop.TickResult()
    assert opened == []


def test_a_the_flag_off_starts_no_task(monkeypatch: Any) -> None:
    _flag(monkeypatch, False)

    async def go() -> bool:
        started = await loop.start_package_expiry()
        assert not loop.loop_running()
        return started

    assert asyncio.run(go()) is False


def test_f_the_flag_on_starts_the_loop_and_the_first_tick_waits(monkeypatch: Any) -> None:
    """The loop starts only when the flag is on. The first tick waits for
    ``FIRST_DELAY_SECONDS``, so a restart deletes nothing at once."""
    _flag(monkeypatch, True)
    ticks: list[int] = []

    async def tick(**kw: Any) -> Any:
        ticks.append(1)
        return loop.TickResult()

    monkeypatch.setattr(loop, "run_tick", tick)
    monkeypatch.setattr(loop, "FIRST_DELAY_SECONDS", 30.0)

    async def go() -> None:
        assert await loop.start_package_expiry() is True
        assert loop.loop_running()
        assert await loop.start_package_expiry() is True  # idempotent
        await asyncio.sleep(0.05)
        assert ticks == []
        await loop.stop_package_expiry()
        assert not loop.loop_running()

    asyncio.run(go())
    assert ticks == []


def test_f_a_tick_that_throws_does_not_end_the_loop(monkeypatch: Any) -> None:
    calls: list[int] = []

    async def tick(**kw: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("a bad tick")
        loop._stopping.set()
        return loop.TickResult()

    monkeypatch.setattr(loop, "run_tick", tick)
    monkeypatch.setattr(loop, "FIRST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(loop, "TICK_SECONDS", 0.01)
    asyncio.run(asyncio.wait_for(loop._loop(), timeout=5))
    assert len(calls) == 2


def test_f_the_interval_is_one_day() -> None:
    assert loop.TICK_SECONDS == 86400.0
    assert loop.FIRST_DELAY_SECONDS > 0


def test_f_the_clock_has_a_zone() -> None:
    now = loop.utc_now()
    assert now.tzinfo is not None and now.utcoffset() is not None


def test_f_the_start_function_reads_the_flag() -> None:
    tree = ast.parse(Path(loop.__file__).read_text(encoding="utf-8"))
    for name in ("start_package_expiry", "run_tick"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        called = {c.func.id for c in ast.walk(fn)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert "expiry_enabled" in called, name


def test_f_main_starts_and_stops_it_and_never_reads_the_flag() -> None:
    src = MAIN.read_text(encoding="utf-8")
    assert "start_package_expiry" in src and "stop_package_expiry" in src
    assert "package_expiry_enabled" not in src
    assert "PACKAGE_EXPIRY_ENABLED" in src  # the comment names the flag


def test_d_one_org_that_fails_leaves_the_others_expired(monkeypatch: Any) -> None:
    _flag(monkeypatch, True)
    _host_orgs(monkeypatch, "org-a", "org-b", "org-c")
    done: list[str] = []

    async def orgs() -> list[str]:
        return ["org-a", "org-b", "org-c", "org-x"]

    async def run_org(org: str, result: Any, **kw: Any) -> None:
        if org == "org-b":
            raise RuntimeError("a bad org")
        done.append(org)
        result.archived += 1

    monkeypatch.setattr(loop, "_organization_ids", orgs)
    monkeypatch.setattr(loop, "run_org", run_org)
    result = asyncio.run(loop.run_tick())
    # org-x is not in PACKAGE_HOST_ORGS, so the tick skips it.
    assert done == ["org-a", "org-c"]
    assert (result.orgs, result.archived, result.failed) == (3, 2, 1)


# ═══════════════════════════════════════════════════════════════════════════
# Hermetic: the clock guard (g), the cancel and the stop (h)
# ═══════════════════════════════════════════════════════════════════════════


class _FakeSession:
    """A session double. It records the commit, the rollback and the close."""

    def __init__(self, org: str, log: list[str]) -> None:
        self.org = org
        self.log = log


def _fake_sessions(monkeypatch: Any) -> list[str]:
    """Replace ``tenant_session`` with a double that has the commit rules of
    the real one: commit at a clean end, rollback on an error, close always."""
    log: list[str] = []

    @contextlib.asynccontextmanager
    async def fake(org: str) -> Any:
        log.append(f"open:{org}")
        try:
            yield _FakeSession(org, log)
            log.append(f"commit:{org}")
        except Exception:
            log.append(f"rollback:{org}")
            raise
        finally:
            log.append(f"close:{org}")

    monkeypatch.setattr(loop, "tenant_session", fake)
    return log


def _capture_warnings(monkeypatch: Any) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []

    class _Spy:
        def __init__(self, real: Any) -> None:
            self.real = real

        def warning(self, event: str, **kw: Any) -> None:
            seen.append((event, kw))

        def info(self, event: str, **kw: Any) -> None:
            seen.append((event, kw))

    monkeypatch.setattr(loop, "_log", _Spy(loop._log))
    return seen


def _skew_case(monkeypatch: Any, offset: timedelta) -> tuple[loop.TickResult, list[str],
                                                              list[str], list[Any]]:
    """Run one org with a host clock *offset* from a fixed database clock."""
    sessions = _fake_sessions(monkeypatch)
    seen = _capture_warnings(monkeypatch)
    expired: list[str] = []
    db_time = datetime(2026, 11, 7, 12, 0, tzinfo=loop.UTC)

    async def fake_expire(db: Any, *, now: datetime, **kw: Any) -> dict[str, int]:
        expired.append(db.org)
        assert now == db_time + offset
        return {"archived": 1, "deleted": 1}

    async def db_clock(db: Any) -> datetime:
        return db_time

    monkeypatch.setattr(loop.store, "expire_due", fake_expire)
    result = loop.TickResult()
    asyncio.run(loop.run_org("org-a", result, clock=lambda: db_time + offset,
                             db_clock=db_clock))
    return result, sessions, expired, seen


def test_g_the_clock_guard_is_five_minutes() -> None:
    assert timedelta(minutes=5) == loop.MAX_CLOCK_SKEW


@pytest.mark.parametrize("offset", [timedelta(days=2), -timedelta(days=2)],
                         ids=["host-2-days-fast", "host-2-days-slow"])
def test_g_a_skewed_host_clock_skips_the_org(monkeypatch: Any, offset: timedelta) -> None:
    result, sessions, expired, seen = _skew_case(monkeypatch, offset)
    assert expired == []
    assert (result.archived, result.deleted, result.skipped_skew) == (0, 0, 1)
    assert sessions == ["open:org-a", "rollback:org-a", "close:org-a"]
    skews = [kw for event, kw in seen if event == "package_host.expiry.clock_skew"]
    assert skews == [{"org": "org-a", "skew_secs": 2 * 86400}]


def test_g_a_small_skew_runs_the_org(monkeypatch: Any) -> None:
    result, sessions, expired, seen = _skew_case(monkeypatch, timedelta(minutes=1))
    assert expired == ["org-a"]
    assert (result.archived, result.deleted, result.skipped_skew) == (1, 1, 0)
    assert sessions == ["open:org-a", "commit:org-a", "close:org-a"]
    assert not [e for e, _ in seen if e == "package_host.expiry.clock_skew"]


def test_g_the_tick_log_line_counts_the_skip(monkeypatch: Any) -> None:
    _flag(monkeypatch, True)
    _host_orgs(monkeypatch, "*")
    seen = _capture_warnings(monkeypatch)

    async def orgs() -> list[str]:
        return ["org-a", "org-b"]

    async def run_org(org: str, result: Any, **kw: Any) -> None:
        if org == "org-a":
            result.skipped_skew += 1

    monkeypatch.setattr(loop, "_organization_ids", orgs)
    monkeypatch.setattr(loop, "run_org", run_org)
    result = asyncio.run(loop.run_tick())
    assert (result.orgs, result.skipped_skew, result.failed) == (2, 1, 0)
    tick = [kw for e, kw in seen if e == "package_host.expiry.tick"]
    assert tick and tick[0]["skipped_skew"] == 1


def test_h_a_cancel_in_an_org_rolls_it_back_and_ends_the_loop(monkeypatch: Any) -> None:
    """The tick task is cancelled while ``expire_due`` of org A waits. Org A
    does not commit, the CancelledError reaches the loop task, and the task
    ends. Org B never starts."""
    _flag(monkeypatch, True)
    _host_orgs(monkeypatch, "*")
    sessions = _fake_sessions(monkeypatch)
    expired: list[str] = []

    async def orgs() -> list[str]:
        return ["org-a", "org-b"]

    async def db_clock(db: Any) -> datetime:
        return loop.utc_now()

    async def go() -> None:
        entered = asyncio.Event()
        never = asyncio.Event()

        async def slow_expire(db: Any, **kw: Any) -> dict[str, int]:
            expired.append(db.org)
            entered.set()
            await never.wait()
            return {"archived": 0, "deleted": 0}

        real_run_org = loop.run_org

        async def run_org(org: str, result: Any, **kw: Any) -> None:
            await real_run_org(org, result, db_clock=db_clock, **kw)

        monkeypatch.setattr(loop.store, "expire_due", slow_expire)
        monkeypatch.setattr(loop, "run_org", run_org)
        monkeypatch.setattr(loop, "_organization_ids", orgs)
        monkeypatch.setattr(loop, "FIRST_DELAY_SECONDS", 0.0)
        task = asyncio.get_running_loop().create_task(loop._loop())
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert task.done() and task.cancelled()

    asyncio.run(go())
    assert expired == ["org-a"]
    assert "commit:org-a" not in sessions
    assert sessions[-1] == "close:org-a"
    assert not [s for s in sessions if s.endswith("org-b")]


def test_h_a_stop_between_orgs_starts_no_next_org(monkeypatch: Any) -> None:
    _flag(monkeypatch, True)
    _host_orgs(monkeypatch, "*")
    done: list[str] = []

    async def orgs() -> list[str]:
        return ["org-a", "org-b", "org-c"]

    async def run_org(org: str, result: Any, **kw: Any) -> None:
        done.append(org)
        loop._stopping.set()  # the gateway stops while org A runs

    monkeypatch.setattr(loop, "_organization_ids", orgs)
    monkeypatch.setattr(loop, "run_org", run_org)
    result = asyncio.run(loop.run_tick())
    assert done == ["org-a"]
    assert result.orgs == 3


# ═══════════════════════════════════════════════════════════════════════════
# R8: the tick on a real database (b, c, d, e, g)
# ═══════════════════════════════════════════════════════════════════════════


async def _bound_org(db: Any) -> str:
    """The org that the session binds, as Postgres reads it."""
    return str((await db.execute(
        text("SELECT current_setting('app.tenant_id', true)"))).scalar_one())


def _spy_expire(monkeypatch: Any, *, fail_org: str | None = None) -> list[tuple[str, datetime]]:
    """Wrap ``store.expire_due``. Each call records its bound org and its
    time. A call bound to *fail_org* raises after its own statements ran."""
    calls: list[tuple[str, datetime]] = []
    real = store.expire_due

    async def spy(db: Any, *, now: datetime, **kw: Any) -> dict[str, int]:
        org = await _bound_org(db)
        calls.append((org, now))
        got = await real(db, now=now, **kw)
        if org == fail_org:
            raise RuntimeError("a bad org")
        return got

    monkeypatch.setattr(loop.store, "expire_due", spy)
    return calls


def _tick(w: Any) -> loop.TickResult:
    async def go() -> loop.TickResult:
        async with tenant_engine_scope(_dsn(w.p.app_url)):
            return await loop.run_tick()

    return asyncio.run(go())


def _run_one_org(w: Any, org: str) -> loop.TickResult:
    async def go() -> loop.TickResult:
        result = loop.TickResult()
        async with tenant_engine_scope(_dsn(w.p.app_url)):
            await loop.run_org(org, result)
        return result

    return asyncio.run(go())


def _two_due(w: Any, tag: str) -> tuple[Any, Any]:
    """One due install in org A and one in org B."""
    past = _now() - DAY
    a = _install(w, "a1", "org", [_claim(_k(f"{tag}a"))], ends_at=past)
    b = _install(w, "b1", ZED, [_claim(_k(f"{tag}b"))], ends_at=past, org="b", by=ZED)
    assert _row(w, a.id)[0] == "active" and _row(w, b.id)[0] == "active"
    return a, b


@_DB_GATE
def test_r8_b_a_tick_expires_each_host_org_once(world: Any, monkeypatch: Any) -> None:  # noqa: F811
    p = world.p
    _flag(monkeypatch, True)
    _host_orgs(monkeypatch, p.org_a, p.org_b)
    a, b = _two_due(world, "b")
    spent = _install(world, "a2", "org", [_claim(_k("bs"))], ends_at=_now() - 40 * DAY)
    _admin_write(
        world,
        "UPDATE package_install SET state = 'archived', archived_at = :a "
        "WHERE id = CAST(:i AS uuid)",
        {"a": _now() - 31 * DAY, "i": spent.id},
    )
    calls = _spy_expire(monkeypatch)

    result = _tick(world)

    assert sorted(org for org, _ in calls) == sorted([p.org_a, p.org_b])
    for _org, now in calls:
        assert now.tzinfo is not None and now.utcoffset() is not None
    assert (result.orgs, result.archived, result.deleted, result.failed) == (2, 2, 1, 0)
    assert _row(world, a.id)[0] == "archived"
    assert _row(world, b.id)[0] == "archived"
    assert _row(world, spent.id) is None


@_DB_GATE
def test_r8_c_an_org_outside_the_host_list_does_not_change(world: Any, monkeypatch: Any) -> None:  # noqa: F811
    p = world.p
    _flag(monkeypatch, True)
    _host_orgs(monkeypatch, p.org_a)
    a, b = _two_due(world, "c")
    before_b = _row(world, b.id)
    calls = _spy_expire(monkeypatch)

    result = _tick(world)

    assert [org for org, _ in calls] == [p.org_a]
    assert (result.orgs, result.archived, result.failed) == (1, 1, 0)
    assert _row(world, a.id)[0] == "archived"
    assert _row(world, b.id) == before_b


@_DB_GATE
def test_r8_d_a_failure_in_one_org_does_not_stop_the_other(world: Any, monkeypatch: Any) -> None:  # noqa: F811
    p = world.p
    _flag(monkeypatch, True)
    _host_orgs(monkeypatch, p.org_a, p.org_b)
    a, b = _two_due(world, "d")
    before_a = _row(world, a.id)
    calls = _spy_expire(monkeypatch, fail_org=p.org_a)

    result = _tick(world)  # returns, and does not raise

    assert sorted(org for org, _ in calls) == sorted([p.org_a, p.org_b])
    assert (result.orgs, result.archived, result.failed) == (2, 1, 1)
    # The session of org A rolled back, so its install did not change.
    assert _row(world, a.id) == before_a
    assert _row(world, b.id)[0] == "archived"


@_DB_GATE
def test_r8_e_the_share_of_org_a_never_touches_org_b(world: Any, monkeypatch: Any) -> None:  # noqa: F811
    p = world.p
    a, b = _two_due(world, "e")
    before_b = _row(world, b.id)
    calls = _spy_expire(monkeypatch)

    result = _run_one_org(world, p.org_a)

    assert [org for org, _ in calls] == [p.org_a]
    assert (result.archived, result.deleted) == (1, 0)
    assert _row(world, a.id)[0] == "archived"
    assert _row(world, b.id) == before_b


def _db_clock_now(w: Any) -> datetime:
    """``now()`` of the real database, read through the app role."""
    async def go() -> datetime:
        async with tenant_engine_scope(_dsn(w.p.app_url)):
            db = await loop.get_db()
            try:
                return await loop.db_now(db)
            finally:
                await db.close()

    return asyncio.run(go())


@_DB_GATE
def test_r8_g_a_fast_host_clock_deletes_nothing(world: Any, monkeypatch: Any) -> None:  # noqa: F811
    """The install was archived 29 days ago on the DATABASE clock, so it is
    not spent. A host clock 2 days fast would read it as 31 days, and
    delete it. The guard skips the org, so the row does not change."""
    p = world.p
    _flag(monkeypatch, True)
    _host_orgs(monkeypatch, p.org_a)
    db_time = _db_clock_now(world)
    assert abs(db_time - loop.utc_now()) < loop.MAX_CLOCK_SKEW
    kept = _install(world, "a1", "org", [_claim(_k("gf"))], ends_at=db_time - 40 * DAY)
    _admin_write(
        world,
        "UPDATE package_install SET state = 'archived', archived_at = :a "
        "WHERE id = CAST(:i AS uuid)",
        {"a": db_time - 29 * DAY, "i": kept.id},
    )
    before = _row(world, kept.id)
    calls = _spy_expire(monkeypatch)
    monkeypatch.setattr(loop, "utc_now", lambda: datetime.now(loop.UTC) + 2 * DAY)
    # run_tick binds its default clock when Python defines it. Pass the fast one.

    async def go() -> loop.TickResult:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await loop.run_tick(clock=loop.utc_now)

    result = asyncio.run(go())

    assert calls == []
    assert (result.orgs, result.deleted, result.skipped_skew, result.failed) == (1, 0, 1, 0)
    assert _row(world, kept.id) == before
