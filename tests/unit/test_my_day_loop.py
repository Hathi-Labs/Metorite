"""WS-58 MD-5b — the ``my_day`` loop, hermetic (``my_day_feed.md`` §8.3, §8.4).

Each case is written so a wrong build fails it:

* **Members come from the active set only** (done-when 2). The Redis fake
  sits under the REAL ``TenantRedis``, as ``test_mcp_edge_writes.py`` does,
  so each key went through the tenant prefix. A stale member is trimmed, and
  another org's set is never read. The tick reads no member table and runs
  no SQL but the discovery read and the sweep.
* **The read writes the set only from the session identity** (done-when 2).
  ``note_active`` takes the ``UserContext`` and nothing else, and
  ``shell_feed`` hands it the session user it was given.
* **The switch** (done-when 3). With ``MY_DAY_LOOP_ENABLED`` off, the start
  makes no task and the read writes no set. main.py reads no flag.
* **No member pass** (done-when 4). ``member_context`` is never called.
* **The sweep guard** (§8.4 item 23): ``SET ... NX EX 90000``, once a day.
  The R8 half, ``test_my_day_loop_r8.py``, runs the DELETE on Postgres.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from pathlib import Path
from typing import Any

import pytest
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common import tenant_redis
from acb_common.settings import get_settings
from acb_common.tenant_redis import KEY_ROOT, TenantRedis
from gateway import my_day_loop
from gateway.routes.shell import feed, needs, state

_REPO = Path(__file__).resolve().parents[2]
ORG_A = "11111111-1111-4111-8111-111111111111"
ORG_B = "22222222-2222-4222-8222-222222222222"
ME = "Member@Customer.Example"


class _FakeRedis:
    """Sorted sets and ``SET NX``, keyed by the RAW key the wrapper sends."""

    def __init__(self) -> None:
        self.zsets: dict[str, dict[str, float]] = {}
        self.strings: dict[str, tuple[Any, dict[str, Any]]] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail: Exception | None = None

    def _note(self, name: str, k: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.calls.append((name, k))

    async def zadd(self, k: str, mapping: dict[str, float], **_: Any) -> int:
        self._note("zadd", k)
        self.zsets.setdefault(k, {}).update(mapping)
        return len(mapping)

    async def zrangebyscore(self, k: str, lo: Any, hi: Any, **_: Any) -> list[str]:
        self._note("zrangebyscore", k)
        zset = self.zsets.get(k, {})
        return [m for m, s in sorted(zset.items(), key=lambda kv: kv[1])
                if float(lo) <= s <= float(hi)]

    async def zremrangebyscore(self, k: str, lo: Any, hi: Any) -> int:
        self._note("zremrangebyscore", k)
        zset = self.zsets.get(k, {})
        gone = [m for m, s in zset.items() if float(lo) <= s <= float(hi)]
        for m in gone:
            del zset[m]
        return len(gone)

    async def set(self, k: str, value: Any, **kwargs: Any) -> bool | None:
        self._note("set", k)
        if kwargs.get("nx") and k in self.strings:
            return None
        self.strings[k] = (value, kwargs)
        return True


class _Session:
    """A tenant session that records each statement."""

    def __init__(self, log: list[tuple[str, str]], org: str) -> None:
        self.log, self.org = log, org

    async def execute(self, sql: Any, *_: Any) -> Any:
        self.log.append((self.org, str(sql)))

        class _R:
            rowcount = 2

        return _R()


@pytest.fixture
def redis_fake(monkeypatch) -> _FakeRedis:
    """The fake under the real wrapper (``test_mcp_edge_writes.py:805``)."""
    fake = _FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(fake))
    return fake


@pytest.fixture
def sql_log(monkeypatch) -> list[tuple[str, str]]:
    """Every statement of the loop. ``get_db`` answers the two orgs."""
    log: list[tuple[str, str]] = []

    class _Unbound:
        async def execute(self, sql: Any, *_: Any) -> Any:
            log.append(("unbound", str(sql)))

            class _Rows:
                def fetchall(self) -> list[Any]:
                    return [type("R", (), {"id": ORG_A}), type("R", (), {"id": ORG_B})]

            return _Rows()

        async def close(self) -> None:
            return None

    async def _get_db() -> Any:
        return _Unbound()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _tenant_session(org: str | None = None):
        assert org, "the sweep must pass its org explicitly"
        yield _Session(log, org)

    monkeypatch.setattr(my_day_loop, "get_db", _get_db)
    monkeypatch.setattr(my_day_loop, "tenant_session", _tenant_session)
    return log


@pytest.fixture
def switch(monkeypatch):
    def _set(on: bool) -> None:
        monkeypatch.setattr(get_settings(), "my_day_loop_enabled", on)

    _set(False)
    return _set


@pytest.fixture(autouse=True)
def _no_member_pass(monkeypatch):
    """MD-5b runs no member pass (done-when 4). A call here fails the test."""
    import acb_auth.deps as deps

    def _refuse(*_: Any, **__: Any) -> Any:
        raise AssertionError("MD-5b must not build a member context")

    monkeypatch.setattr(deps, "member_context", _refuse)


@pytest.fixture(autouse=True)
def _fresh_loop():
    """A stop sets ``_stopping``, and only a start clears it. Each test
    starts with a loop that is neither running nor stopping."""
    my_day_loop._stopping.clear()
    my_day_loop._task = None
    yield
    my_day_loop._stopping.clear()
    my_day_loop._task = None


def _user(email: str | None = ME, org: str | None = ORG_A) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=EffectiveAccess(role_granted=frozenset({"feature:projects"})))


def _zkey(org: str) -> str:
    return f"{KEY_ROOT}:{org}:my_day:active"


# ── The switch ──────────────────────────────────────────────────────────────


class TestTheSwitch:
    def test_the_setting_is_default_off(self) -> None:
        from acb_common.settings import Settings

        assert Settings.model_fields["my_day_loop_enabled"].default is False

    def test_off_starts_no_task(self, switch) -> None:
        async def go() -> tuple[bool, bool]:
            started = await my_day_loop.start_my_day_loop()
            running = my_day_loop.loop_running()
            await my_day_loop.stop_my_day_loop()
            return started, running

        assert asyncio.run(go()) == (False, False)
        assert my_day_loop._task is None

    def test_off_writes_no_active_set(self, switch, redis_fake) -> None:
        asyncio.run(my_day_loop.note_active(_user()))
        assert redis_fake.calls == []
        assert redis_fake.zsets == {}

    def test_on_starts_one_task_and_the_stop_ends_it(self, switch, monkeypatch) -> None:
        switch(True)
        ticks: list[int] = []

        async def _tick() -> int:
            ticks.append(1)
            return 0

        monkeypatch.setattr(my_day_loop, "run_tick", _tick)

        async def go() -> tuple[bool, bool, bool, bool]:
            first = await my_day_loop.start_my_day_loop()
            again = await my_day_loop.start_my_day_loop()
            await asyncio.sleep(0)
            running = my_day_loop.loop_running()
            await my_day_loop.stop_my_day_loop()
            return first, again, running, my_day_loop.loop_running()

        assert asyncio.run(go()) == (True, True, True, False)
        assert ticks == [1]

    def test_main_calls_start_and_stop_and_reads_no_flag(self) -> None:
        """The gate lives inside the start function (§8.4 item 10)."""
        main_src = (_REPO / "apps/services/gateway/gateway/main.py").read_text(
            encoding="utf-8")
        before, sep, after = main_src.partition("\n    yield\n")
        assert sep, "lifespan yield not found"
        assert "await start_my_day_loop()" in before
        assert "await stop_my_day_loop()" in after
        assert "loop_enabled" not in main_src
        assert "my_day_loop_enabled" not in main_src
        assert "sync_enabled" not in main_src

    def test_the_flag_has_one_reader(self) -> None:
        readers = [
            p for p in (_REPO / "apps").rglob("*.py")
            if "my_day_loop_enabled" in p.read_text(encoding="utf-8", errors="ignore")
        ]
        assert [p.name for p in readers] == ["my_day_loop.py"], readers


# ── The active set is written from the session only ────────────────────────


class TestTheReadWritesTheSessionMember:
    def test_note_active_writes_the_lower_case_session_email(self, switch,
                                                             redis_fake) -> None:
        switch(True)
        before = time.time()
        asyncio.run(my_day_loop.note_active(_user()))
        assert list(redis_fake.zsets) == [_zkey(ORG_A)]
        (member, score), = redis_fake.zsets[_zkey(ORG_A)].items()
        assert member == ME.lower()
        assert before <= score <= time.time()

    def test_note_active_takes_the_user_and_nothing_else(self) -> None:
        params = list(inspect.signature(my_day_loop.note_active).parameters)
        assert params == ["user"]

    def test_no_org_or_no_email_writes_nothing(self, switch, redis_fake) -> None:
        switch(True)
        asyncio.run(my_day_loop.note_active(_user(org=None)))
        asyncio.run(my_day_loop.note_active(_user(email=None)))
        assert redis_fake.calls == []

    def test_a_redis_outage_fails_open(self, switch, redis_fake) -> None:
        switch(True)
        redis_fake.fail = ConnectionError("down")
        assert asyncio.run(my_day_loop.note_active(_user())) is None

    def test_a_slow_redis_does_not_hold_the_read(self, switch, monkeypatch) -> None:
        switch(True)

        class _Slow:
            async def zadd(self, *_: Any, **__: Any) -> None:
                await asyncio.sleep(5)

        monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: _Slow())
        started = time.monotonic()
        asyncio.run(my_day_loop.note_active(_user()))
        assert time.monotonic() - started < 0.5

    def test_shell_feed_hands_note_active_its_session_user(self, switch, monkeypatch) -> None:
        """The route's one identity input is the session user, and it passes
        that user object to the one writer of the set. ``request`` and
        ``response`` are the framework's objects (MD-5a's ETag), not input."""
        params = set(inspect.signature(feed.shell_feed).parameters)
        assert "user" in params
        assert params <= {"limit", "user", "request", "response"}, params
        switch(True)
        seen: list[Any] = []

        async def _record(user: Any) -> None:
            seen.append(user)

        class _Stop(Exception):
            pass

        async def _stop(*_: Any, **__: Any) -> Any:
            await asyncio.sleep(0)  # let the write that runs beside the read start
            raise _Stop

        monkeypatch.setattr(my_day_loop, "note_active", _record)
        monkeypatch.setattr(needs, "needs_run", _stop)
        user = _user()
        with pytest.raises(_Stop):
            asyncio.run(feed.shell_feed(limit=40, user=user))
        assert seen == [user] and seen[0] is user

    def test_no_other_module_writes_the_active_set(self) -> None:
        """``note_active`` is the one writer: no other code names the key."""
        pattern = re.compile(r"""key\(\s*["']my_day["']\s*,\s*["']active["']""")
        writers = [
            p.name for p in (_REPO / "apps").rglob("*.py")
            if pattern.search(p.read_text(encoding="utf-8", errors="ignore"))
        ]
        assert writers == ["my_day_loop.py"], writers


# ── The loop reads members from the active set only ────────────────────────


class TestTheLoopReadsTheActiveSetOnly:
    def _seed(self, fake: _FakeRedis) -> None:
        now = time.time()
        fake.zsets[_zkey(ORG_A)] = {
            "fresh@a.test": now - 60,
            "two-days@a.test": now - 2 * 86400,
            "stale@a.test": now - 4 * 86400,
        }
        fake.zsets[_zkey(ORG_B)] = {"b@b.test": now - 60}

    def test_run_org_answers_the_fresh_members_and_trims_the_stale(
            self, redis_fake, sql_log) -> None:
        self._seed(redis_fake)
        members = asyncio.run(my_day_loop.run_org(ORG_A))
        assert members == ["two-days@a.test", "fresh@a.test"]
        assert "stale@a.test" not in redis_fake.zsets[_zkey(ORG_A)]
        assert set(redis_fake.zsets[_zkey(ORG_B)]) == {"b@b.test"}
        touched = {k for _, k in redis_fake.calls if k.endswith(":my_day:active")}
        assert touched == {_zkey(ORG_A)}, "another org's set was read"

    def test_a_tick_runs_only_the_discovery_read_and_the_sweep(
            self, redis_fake, sql_log) -> None:
        self._seed(redis_fake)
        assert asyncio.run(my_day_loop.run_tick()) == 2
        unbound = [sql for org, sql in sql_log if org == "unbound"]
        bound = [(org, sql) for org, sql in sql_log if org != "unbound"]
        assert unbound == ["SELECT id FROM organization ORDER BY id"]
        assert {org for org, _ in bound} == {ORG_A, ORG_B}
        assert all(sql == state._SWEEP_SQL for _, sql in bound), bound
        assert "stale@a.test" not in redis_fake.zsets[_zkey(ORG_A)]

    def test_a_redis_outage_skips_the_org_and_the_tick_goes_on(
            self, redis_fake, sql_log) -> None:
        redis_fake.fail = ConnectionError("down")
        assert asyncio.run(my_day_loop.run_org(ORG_A)) == []
        assert [s for o, s in sql_log if o != "unbound"] == []

    def test_the_loop_names_no_member_table_and_no_member_pass(self) -> None:
        src = (_REPO / "apps/services/gateway/gateway/my_day_loop.py").read_text(
            encoding="utf-8")
        code = re.sub(r'"""[\s\S]*?"""', "", src)
        code = re.sub(r"#.*", "", code)
        for word in ("app_user", "member_context", "resolve_identity", "user_settings",
                     "member_email", "my_day_card_state"):
            assert word not in code, word

    def test_three_orgs_run_at_once_at_most(self, monkeypatch, sql_log) -> None:
        assert my_day_loop.CONCURRENCY == 3
        live = {"now": 0, "peak": 0}

        async def _org(org: str) -> list[str]:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
            await asyncio.sleep(0.01)
            live["now"] -= 1
            return []

        async def _orgs() -> list[str]:
            return [f"org-{n}" for n in range(7)]

        monkeypatch.setattr(my_day_loop, "run_org", _org)
        monkeypatch.setattr(my_day_loop, "_organization_ids", _orgs)
        assert asyncio.run(my_day_loop.run_tick()) == 7
        assert live["peak"] == 3

    def test_a_hung_org_does_not_hold_the_tick(self, monkeypatch) -> None:
        """A Redis that never answers costs one org its tick, not the loop."""
        async def _org(org: str) -> list[str]:
            if org == "hung":
                await asyncio.sleep(60)
            return []

        async def _orgs() -> list[str]:
            return ["hung", "ok-1", "ok-2"]

        monkeypatch.setattr(my_day_loop, "PER_ORG_TIMEOUT_S", 0.05)
        monkeypatch.setattr(my_day_loop, "run_org", _org)
        monkeypatch.setattr(my_day_loop, "_organization_ids", _orgs)
        started = time.monotonic()
        assert asyncio.run(my_day_loop.run_tick()) == 2
        assert time.monotonic() - started < 2

    def test_off_the_read_starts_no_write(self, switch, monkeypatch) -> None:
        """With the switch off, ``mark_active`` starts no task at all."""
        started: list[Any] = []

        async def _note(user: Any) -> None:
            started.append(user)

        async def _go() -> None:
            my_day_loop.mark_active(_user())
            await asyncio.sleep(0)
            assert not my_day_loop._writes

        monkeypatch.setattr(my_day_loop, "note_active", _note)
        asyncio.run(_go())
        assert started == []


# ── The sweep guard ─────────────────────────────────────────────────────────


class TestTheSweepGuard:
    def test_the_first_sweep_of_a_day_claims_the_guard_and_deletes(
            self, redis_fake, sql_log) -> None:
        assert asyncio.run(my_day_loop.sweep(ORG_A, "2026-10-14")) == 2
        guard = f"{KEY_ROOT}:{ORG_A}:my_day:swept:2026-10-14"
        _value, kwargs = redis_fake.strings[guard]
        assert kwargs == {"nx": True, "ex": 90000}
        assert sql_log == [(ORG_A, state._SWEEP_SQL)]

    def test_a_second_sweep_the_same_day_runs_no_sql(self, redis_fake, sql_log) -> None:
        asyncio.run(my_day_loop.sweep(ORG_A, "2026-10-14"))
        assert asyncio.run(my_day_loop.sweep(ORG_A, "2026-10-14")) is None
        assert asyncio.run(my_day_loop.sweep(ORG_B, "2026-10-14")) == 2
        assert asyncio.run(my_day_loop.sweep(ORG_A, "2026-10-15")) == 2
        assert [org for org, _ in sql_log] == [ORG_A, ORG_B, ORG_A]

    def test_the_sweep_keeps_the_rows_that_hide_a_card(self) -> None:
        """The text of item 23. The R8 suite runs it on Postgres."""
        sql = state._SWEEP_SQL
        assert "state IN ('seen', 'later')" in sql
        assert "state = 'snoozed' AND snooze_on_change = false AND snoozed_until < now()" in sql
        assert "interval '30 days'" in sql
        assert "dismissed" not in sql and "acted" not in sql
        assert "organization_id = CAST(NULLIF(current_setting('app.tenant_id'" in sql
