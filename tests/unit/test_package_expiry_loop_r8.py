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
"""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime
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
# R8: the tick on a real database (b, c, d, e)
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
