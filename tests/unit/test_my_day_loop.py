"""WS-58 MD-5b and MD-6a-2 — the ``my_day`` loop, hermetic
(``my_day_feed.md`` §8.3, §8.4 and §10.9).

Each case is written so a wrong build fails it:

* **Members come from the active set only** (MD-5b done-when 2). The Redis
  fake sits under the REAL ``TenantRedis``, as ``test_mcp_edge_writes.py``
  does, so each key went through the tenant prefix. A stale member is
  trimmed, and another org's set is never read. With each mode off, the
  tick runs no SQL but the discovery read and the two sweeps.
* **The read writes the set only from the session identity** (MD-5b
  done-when 2). ``note_active`` takes the ``UserContext`` and nothing else,
  and ``shell_feed`` hands it the session user it was given.
* **The switch** (MD-5b done-when 3). With ``MY_DAY_LOOP_ENABLED`` off, the
  start makes no task and the read writes no set. main.py reads no flag.
* **The sweep guard** (§8.4 item 23): ``SET ... NX EX 90000``, once a day.
  The R8 half, ``test_my_day_loop_r8.py``, runs the DELETEs on Postgres.
* **The member pass** (MD-6a-2 done-whens 1 to 9): the cheap gate, the
  departed checks, the context, the morning pass, the dirty pass, the bound
  and the judgments sweep. Each class names its done-when.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common import tenant_redis
from acb_common.settings import get_settings
from acb_common.tenant_redis import KEY_ROOT, TenantRedis
from gateway import my_day_loop
from gateway.routes.shell import feed, feed_decide, needs, state

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

    async def zrem(self, k: str, *members: str) -> int:
        self._note("zrem", k)
        zset = self.zsets.get(k, {})
        gone = [m for m in members if m in zset]
        for m in gone:
            del zset[m]
        return len(gone)

    async def set(self, k: str, value: Any, **kwargs: Any) -> bool | None:
        self._note("set", k)
        if kwargs.get("nx") and k in self.strings:
            return None
        self.strings[k] = (value, kwargs)
        return True

    async def get(self, k: str) -> Any:
        self._note("get", k)
        held = self.strings.get(k)
        return None if held is None else held[0]

    async def mget(self, keys: list[str]) -> list[Any]:
        for k in keys:
            self._note("mget", k)
        return [None if self.strings.get(k) is None else self.strings[k][0] for k in keys]

    async def delete(self, *keys: str) -> int:
        for k in keys:
            self._note("delete", k)
        return sum(1 for k in keys if self.strings.pop(k, None) is not None)

    async def exists(self, *keys: str) -> int:
        for k in keys:
            self._note("exists", k)
        return sum(1 for k in keys if k in self.strings)


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
def _no_member_context(monkeypatch):
    """Every ``my_day.*`` mode is off, as in production, and a member context
    fails the test. A test of the member pass sets its own."""
    import acb_auth.deps as deps

    def _refuse(*_: Any, **__: Any) -> Any:
        raise AssertionError("no member context was expected here")

    monkeypatch.setattr(deps, "member_context", _refuse)
    monkeypatch.setattr(get_settings(), "decide_feature_modes", "")
    monkeypatch.setattr(get_settings(), "my_day_decide_orgs", "")
    monkeypatch.setattr(get_settings(), "my_day_cache", False)


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
    def test_note_active_writes_the_exact_session_email(self, switch,
                                                        redis_fake) -> None:
        """The session's own spelling, not a lower-case copy (fix round 1,
        P1): every member table keys on it."""
        switch(True)
        before = time.time()
        asyncio.run(my_day_loop.note_active(_user(email=f"  {ME} ")))
        assert list(redis_fake.zsets) == [_zkey(ORG_A)]
        (member, score), = redis_fake.zsets[_zkey(ORG_A)].items()
        assert member == ME and member != ME.lower()
        assert before <= score <= time.time()

    def test_note_active_drops_an_old_lower_case_copy(self, switch, redis_fake) -> None:
        switch(True)
        redis_fake.zsets[_zkey(ORG_A)] = {ME.lower(): time.time() - 60}
        asyncio.run(my_day_loop.note_active(_user()))
        assert list(redis_fake.zsets[_zkey(ORG_A)]) == [ME]

    def test_a_lower_case_session_writes_one_entry(self, switch, redis_fake) -> None:
        switch(True)
        asyncio.run(my_day_loop.note_active(_user(email="plain@a.test")))
        assert list(redis_fake.zsets[_zkey(ORG_A)]) == ["plain@a.test"]
        assert [n for n, _ in redis_fake.calls] == ["zadd"]

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

    def test_a_tick_runs_only_the_discovery_read_and_the_sweeps(
            self, redis_fake, sql_log) -> None:
        """Each mode is off, as in production: the tick runs the discovery
        read and the two sweeps, and no member read at all."""
        self._seed(redis_fake)
        assert asyncio.run(my_day_loop.run_tick()) == 2
        unbound = [sql for org, sql in sql_log if org == "unbound"]
        bound = [(org, sql) for org, sql in sql_log if org != "unbound"]
        assert unbound == ["SELECT id FROM organization ORDER BY id"]
        assert {org for org, _ in bound} == {ORG_A, ORG_B}
        sweeps = {state._SWEEP_SQL, feed_decide._sweep_sql()}
        assert all(sql in sweeps for _, sql in bound), bound
        assert len(bound) == 4
        assert "stale@a.test" not in redis_fake.zsets[_zkey(ORG_A)]
        assert {name for name, _ in redis_fake.calls} <= {
            "zremrangebyscore", "zrangebyscore", "set"}

    def test_a_redis_outage_skips_the_org_and_the_tick_goes_on(
            self, redis_fake, sql_log) -> None:
        redis_fake.fail = ConnectionError("down")
        assert asyncio.run(my_day_loop.run_org(ORG_A)) == []
        assert [s for o, s in sql_log if o != "unbound"] == []

    def test_the_loop_writes_no_member_table(self) -> None:
        """The loop reads ``app_user.status`` and nothing else of a member
        table. Each table it sweeps has its own writer module."""
        src = (_REPO / "apps/services/gateway/gateway/my_day_loop.py").read_text(
            encoding="utf-8")
        code = re.sub(r'"""[\s\S]*?"""', "", src)
        code = re.sub(r"#.*", "", code)
        for word in ("user_settings", "member_email", "my_day_card_state",
                     "my_day_judgments", "INSERT", "UPDATE", "DELETE"):
            assert word not in code, word
        assert code.count("app_user") == 1, "the status read is the one app_user read"

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
        # The fake answers a rowcount of 2 for each of the two statements.
        assert asyncio.run(my_day_loop.sweep(ORG_A, "2026-10-14")) == 4
        guard = f"{KEY_ROOT}:{ORG_A}:my_day:swept:2026-10-14"
        _value, kwargs = redis_fake.strings[guard]
        assert kwargs == {"nx": True, "ex": 90000}
        assert sql_log == [(ORG_A, state._SWEEP_SQL), (ORG_A, feed_decide._sweep_sql())]

    def test_a_second_sweep_the_same_day_runs_no_sql(self, redis_fake, sql_log) -> None:
        asyncio.run(my_day_loop.sweep(ORG_A, "2026-10-14"))
        assert asyncio.run(my_day_loop.sweep(ORG_A, "2026-10-14")) is None
        assert asyncio.run(my_day_loop.sweep(ORG_B, "2026-10-14")) == 4
        assert asyncio.run(my_day_loop.sweep(ORG_A, "2026-10-15")) == 4
        assert [org for org, _ in sql_log] == [ORG_A, ORG_A, ORG_B, ORG_B, ORG_A, ORG_A]

    def test_a_failed_judgments_sweep_keeps_the_card_state_sweep(
            self, redis_fake, sql_log, monkeypatch) -> None:
        async def _fail(db: Any) -> int:
            raise RuntimeError("relation does not exist")

        monkeypatch.setattr(feed_decide, "sweep_old_rows", _fail)
        assert asyncio.run(my_day_loop.sweep(ORG_A, "2026-10-14")) == 2
        assert sql_log == [(ORG_A, state._SWEEP_SQL)]

    def test_the_sweep_keeps_the_rows_that_hide_a_card(self) -> None:
        """The text of item 23. The R8 suite runs it on Postgres."""
        sql = state._SWEEP_SQL
        assert "state IN ('seen', 'later')" in sql
        assert "state = 'snoozed' AND snooze_on_change = false AND snoozed_until < now()" in sql
        assert "interval '30 days'" in sql
        assert "dismissed" not in sql and "acted" not in sql
        assert "organization_id = CAST(NULLIF(current_setting('app.tenant_id'" in sql


# ── The member pass (MD-6a-2) ───────────────────────────────────────────────

ZONE = "Asia/Kolkata"
#: 08:30 in Kolkata on 2026-10-14: 30 minutes before a 09:00 day start.
EARLY = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)
SHADOW = "my_day.triage=shadow,my_day.weight=shadow"


def _ctx(org: str = ORG_A, *grants: str) -> UserContext:
    return UserContext(email=ME, role=UserRole.EMPLOYEE, organization_id=org,
                       access=EffectiveAccess(role_granted=frozenset(grants)))


class _World:
    """Every outside read of the member pass, each one counted.

    ``identity`` is what ``resolve_identity`` answers, ``failed`` sets
    ``identity_read_failed``, ``status`` is the ``app_user.status`` row (None
    for no row), and ``context`` is what ``member_context`` builds."""

    def __init__(self) -> None:
        self.identity: tuple[str | None, str | None] = ("u-1", ORG_A)
        self.failed = False
        self.unavailable = False
        self.status: str | None = "active"
        self.context: UserContext = _ctx(ORG_A, "feature:projects")
        self.day = state.Day(state._zone(ZONE), 9, 18)
        self.log: list[str] = []
        self.judged: list[tuple[UserContext, datetime | None]] = []
        #: The address that each outside read was handed, by read.
        self.emails: dict[str, list[str]] = {}


@pytest.fixture
def world(monkeypatch, redis_fake) -> _World:
    import acb_auth.access as access
    import acb_auth.deps as deps
    from acb_common.db import bind_tenant

    w = _World()

    async def _resolve(email: str) -> tuple[str | None, str | None]:
        w.log.append("resolve_identity")
        w.emails.setdefault("resolve_identity", []).append(email)
        if w.unavailable:
            raise access.IdentityUnavailable("identity")
        access._identity_read_failed.set(w.failed)
        return (None, None) if w.failed else w.identity

    async def _status(org: str, member: str) -> str | None:
        w.log.append("status")
        w.emails.setdefault("status", []).append(member)
        return w.status

    async def _member_context(email: str) -> UserContext:
        # The request path's resolve keeps the address it is handed
        # (deps.member_context builds UserContext(email=email)).
        w.log.append("member_context")
        w.emails.setdefault("member_context", []).append(email)
        bind_tenant(str(w.context.organization_id))  # bound for ever, as the real one
        return UserContext(email=email, role=w.context.role,
                           organization_id=w.context.organization_id,
                           access=w.context.access)

    async def _read_day(org: str, user: Any) -> Any:
        w.log.append("read_day")
        return w.day

    async def _judge(user: UserContext, now: datetime | None = None) -> Any:
        w.log.append("judge")
        w.judged.append((user, now))
        return feed_decide.Judged("asked", rows=1)

    monkeypatch.setattr(access, "resolve_identity", _resolve)
    monkeypatch.setattr(my_day_loop, "_member_status", _status)
    monkeypatch.setattr(deps, "member_context", _member_context)
    monkeypatch.setattr(my_day_loop, "_read_day", _read_day)
    monkeypatch.setattr(feed_decide, "judge", _judge)
    return w


def _seed_active(fake: _FakeRedis, *members: str, org: str = ORG_A) -> None:
    fake.zsets[_zkey(org)] = {m: time.time() for m in members}


def _pass(when: datetime, org: str = ORG_A, member: str = ME) -> str:
    return asyncio.run(my_day_loop.member_pass(org, member, when))


class TestTheCheapGate:
    """Done-when 7 (§10.9 item 9): with each ``my_day.*`` mode off, the pass
    makes no facade call and writes no row. The gate stops it before Redis,
    before an identity read, before any SQL and before ``member_context``."""

    @pytest.fixture
    def nothing(self, monkeypatch, redis_fake, sql_log) -> list[str]:
        import acb_auth.access as access
        import acb_auth.deps as deps
        from gateway import decide_features as df

        hits: list[str] = []

        def _no(name: str):
            async def _call(*_: Any, **__: Any) -> Any:
                hits.append(name)
                raise AssertionError(name)
            return _call

        monkeypatch.setattr(access, "resolve_identity", _no("resolve_identity"))
        monkeypatch.setattr(deps, "member_context", _no("member_context"))
        monkeypatch.setattr(df, "shadow", _no("shadow"))
        monkeypatch.setattr(feed_decide, "write_rows", _no("write_rows"))
        monkeypatch.setattr(feed, "feed_cards", _no("feed_cards"))
        _seed_active(redis_fake, "a@a.test", "b@a.test")
        redis_fake.calls.clear()
        return hits

    def test_each_mode_off_does_nothing_for_any_member(self, nothing, redis_fake,
                                                       sql_log) -> None:
        assert asyncio.run(my_day_loop.run_members(ORG_A, ["a@a.test", "b@a.test"])) == []
        assert nothing == [] and redis_fake.calls == [] and sql_log == []

    def test_a_shadow_mode_outside_the_org_list_does_nothing(
            self, nothing, redis_fake, sql_log, monkeypatch) -> None:
        monkeypatch.setattr(get_settings(), "decide_feature_modes", SHADOW)
        monkeypatch.setattr(get_settings(), "my_day_decide_orgs", ORG_B)
        assert asyncio.run(my_day_loop.run_members(ORG_A, ["a@a.test"])) == []
        assert nothing == [] and redis_fake.calls == [] and sql_log == []

    def test_the_email_org_list_does_not_open_the_gate(self, monkeypatch) -> None:
        monkeypatch.setattr(get_settings(), "decide_feature_modes", SHADOW)
        monkeypatch.setattr(get_settings(), "decide_feature_orgs", "*")
        assert my_day_loop.judging_on(ORG_A) is False

    def test_the_gate_opens_for_a_listed_org_and_binds_nothing(self, monkeypatch) -> None:
        from acb_common.db import current_tenant

        monkeypatch.setattr(get_settings(), "decide_feature_modes", SHADOW)
        monkeypatch.setattr(get_settings(), "my_day_decide_orgs", ORG_A)
        assert my_day_loop.judging_on(ORG_A) is True
        assert my_day_loop.judging_on(ORG_B) is False
        assert current_tenant() is None

    def test_a_tick_with_each_mode_off_reads_no_member(self, nothing, redis_fake,
                                                       sql_log) -> None:
        assert asyncio.run(my_day_loop.run_tick()) == 2
        assert nothing == []
        assert {name for name, _ in redis_fake.calls} <= {
            "zremrangebyscore", "zrangebyscore", "set"}
        assert not any(":judged:" in k or ":morning:" in k for _, k in redis_fake.calls)

    def test_the_real_judge_with_each_mode_off_makes_no_call(self, nothing) -> None:
        """Past the gate, ``judge`` itself stops: no compute, no call, no row."""
        got = asyncio.run(feed_decide.judge(_ctx(ORG_A, "feature:email")))
        assert got.reason == "off" and nothing == []


class TestDeparted:
    """Done-whens 3 and 4 (§8.4 item 24). The R8 half runs each check on a
    real ``app_user`` row."""

    def test_no_org_removes_the_member_before_member_context(self, world,
                                                              redis_fake) -> None:
        _seed_active(redis_fake, ME, "other@a.test")
        world.identity = (None, None)
        assert _pass(EARLY) == "departed"
        assert ME not in redis_fake.zsets[_zkey(ORG_A)]
        assert "other@a.test" in redis_fake.zsets[_zkey(ORG_A)]
        assert world.log == ["resolve_identity"]

    def test_another_org_removes_the_member(self, world, redis_fake) -> None:
        _seed_active(redis_fake, ME)
        world.identity = ("u-1", ORG_B)
        assert _pass(EARLY) == "departed"
        assert redis_fake.zsets[_zkey(ORG_A)] == {}
        assert world.log == ["resolve_identity"]

    @pytest.mark.parametrize("status", ["removed", "suspended", "invited", None])
    def test_a_status_that_is_not_active_removes_the_member(self, world, redis_fake,
                                                            status) -> None:
        _seed_active(redis_fake, ME)
        world.status = status
        assert _pass(EARLY) == "departed"
        assert redis_fake.zsets[_zkey(ORG_A)] == {}
        assert world.log == ["resolve_identity", "status"]

    def test_a_failed_identity_read_keeps_the_member(self, world, redis_fake) -> None:
        _seed_active(redis_fake, ME)
        world.failed = True
        assert _pass(EARLY) == "identity_failed"
        assert ME in redis_fake.zsets[_zkey(ORG_A)]
        assert world.log == ["resolve_identity"]

    def test_identity_unavailable_keeps_the_member(self, world, redis_fake) -> None:
        _seed_active(redis_fake, ME)
        world.unavailable = True
        assert _pass(EARLY) == "identity_failed"
        assert ME in redis_fake.zsets[_zkey(ORG_A)]
        assert world.log == ["resolve_identity"]

    def test_a_failed_status_read_keeps_the_member(self, world, redis_fake,
                                                   monkeypatch) -> None:
        async def _boom(org: str, member: str) -> str | None:
            raise ConnectionError("db down")

        monkeypatch.setattr(my_day_loop, "_member_status", _boom)
        _seed_active(redis_fake, ME)
        assert _pass(EARLY) == "identity_failed"
        assert ME in redis_fake.zsets[_zkey(ORG_A)]
        assert "member_context" not in world.log

    def test_an_active_member_reaches_member_context(self, world, redis_fake) -> None:
        _seed_active(redis_fake, ME)
        _pass(EARLY)
        assert world.log[:3] == ["resolve_identity", "status", "member_context"]
        assert ME in redis_fake.zsets[_zkey(ORG_A)]

    def test_the_status_read_is_bound_and_names_the_bound_org(self) -> None:
        sql = my_day_loop._STATUS_SQL
        assert "FROM app_user" in sql and "lower(email) = lower(:email)" in sql
        assert "organization_id = CAST(NULLIF(current_setting('app.tenant_id'" in sql


class TestTheContext:
    """Done-whens 1 and 2 (§8.3): the context is the request path's own."""

    def test_a_context_of_another_org_is_skipped(self, world, redis_fake) -> None:
        _seed_active(redis_fake, ME)
        world.context = _ctx(ORG_B, "feature:projects")
        assert _pass(EARLY + timedelta(minutes=15)) == "other_org"
        assert world.judged == []
        assert "read_day" not in world.log
        assert ME in redis_fake.zsets[_zkey(ORG_A)]

    @pytest.mark.parametrize(("grants", "email_ran"), [
        (("feature:projects",), False),
        (("feature:projects", "feature:email"), True),
    ])
    def test_the_providers_run_under_the_members_own_grants(
            self, world, redis_fake, monkeypatch, grants, email_ran) -> None:
        """The pass hands ``judge`` the context that ``member_context`` built.
        So a member without ``feature:email`` gets no email signal: the
        provider gate reads that context, and the email read never runs."""
        import dataclasses

        ran: list[str] = []

        def _stub(source: str):
            async def _run(user: UserContext) -> needs.Found:
                ran.append(source)
                return needs.Found(rows=[], total=0, capped=False)
            return _run

        for source, provider in list(needs.REGISTRY.items()):
            monkeypatch.setitem(needs.REGISTRY, source,
                                dataclasses.replace(provider, run=_stub(source)))
        sources: dict[str, str] = {}

        async def _judge(user: UserContext, now: datetime | None = None) -> Any:
            run = await needs.run_providers(
                user, list(needs.REGISTRY.values()), budget_s=5, timeout_s=5, event="t")
            sources.update(run.sources)
            return feed_decide.Judged("asked")

        monkeypatch.setattr(feed_decide, "judge", _judge)
        world.context = _ctx(ORG_A, *grants)
        _seed_active(redis_fake, ME)
        assert _pass(EARLY + timedelta(minutes=15)) == "judged:asked"
        assert ("email" in ran) is email_ran
        assert sources["email"] == ("ok" if email_ran else "absent")


class TestTheMorningPass:
    """Done-when 5 (§8.3): once a day, 15 minutes before ``day_start_hour``,
    in the member's zone. The guard is ``SET ... NX``."""

    def test_the_window(self) -> None:
        day = state.Day(state._zone(ZONE), 9, 18)

        def at(h: int, m: int) -> datetime:
            return datetime(2026, 10, 14, h, m, tzinfo=state._zone(ZONE))

        assert my_day_loop.morning_date(day, at(8, 44)) is None
        assert my_day_loop.morning_date(day, at(8, 45)) == "2026-10-14"
        assert my_day_loop.morning_date(day, at(17, 59)) == "2026-10-14"
        assert my_day_loop.morning_date(day, at(18, 0)) is None
        assert my_day_loop.morning_date(day, at(23, 50)) is None

    def test_the_morning_pass_runs_once(self, world, redis_fake) -> None:
        _seed_active(redis_fake, ME)
        # 08:30: not yet. The first look reads the day once, and keeps it.
        assert _pass(EARLY) == "not_due"
        assert world.judged == []
        world.log.clear()
        # 08:44: not yet, and Redis alone says so.
        assert _pass(EARLY + timedelta(minutes=14)) == "not_due"
        assert world.log == []
        # 08:45: the morning pass.
        assert _pass(EARLY + timedelta(minutes=15)) == "judged:asked"
        assert len(world.judged) == 1 and world.judged[0][0].email == ME
        guard = f"{KEY_ROOT}:{ORG_A}:my_day:morning:{ME.lower()}:2026-10-14"
        _value, kwargs = redis_fake.strings[guard]
        assert kwargs == {"nx": True, "ex": my_day_loop.MORNING_GUARD_S}
        assert kwargs["ex"] >= 25 * 3600
        world.log.clear()
        # The rest of the day: no second pass, and no identity read.
        for minutes in (16, 60, 300):
            assert _pass(EARLY + timedelta(minutes=minutes)) == "not_due"
        assert world.log == [] and len(world.judged) == 1
        # The next morning.
        assert _pass(EARLY + timedelta(days=1, minutes=15)) == "judged:asked"
        assert len(world.judged) == 2

    def test_a_guard_another_process_holds_runs_no_pass(self, world, redis_fake) -> None:
        _seed_active(redis_fake, ME)
        guard = f"{KEY_ROOT}:{ORG_A}:my_day:morning:{ME.lower()}:2026-10-14"
        redis_fake.strings[guard] = ("1", {})
        assert _pass(EARLY + timedelta(minutes=15)) == "not_due"
        assert world.judged == []

    def test_with_the_cache_off_the_versions_are_never_read(self, world,
                                                            redis_fake) -> None:
        _seed_active(redis_fake, ME)
        _pass(EARLY)
        assert not any(name == "mget" for name, _ in redis_fake.calls)


class TestTheDirtyPass:
    """Done-when 6 (§10.9 item 7): a dirty member gets one pass in 2 minutes
    at most. Dirty is ``cache.versions`` against the judged versions."""

    LATE = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)  # 20:30 in Kolkata: no morning

    @staticmethod
    def _bump(fake: _FakeRedis) -> None:
        k = f"{KEY_ROOT}:{ORG_A}:my_day:ver:{ME.lower()}"
        old = int((fake.strings.get(k) or ("0", {}))[0])
        fake.strings[k] = (str(old + 1), {})

    def test_one_pass_in_two_minutes(self, world, redis_fake, monkeypatch) -> None:
        monkeypatch.setattr(get_settings(), "my_day_cache", True)
        _seed_active(redis_fake, ME)
        # Never judged: dirty.
        assert _pass(self.LATE) == "judged:asked"
        dirty_key = f"{KEY_ROOT}:{ORG_A}:my_day:dirty:{ME.lower()}"
        assert redis_fake.strings[dirty_key][1] == {"nx": True, "ex": 120}
        judged_key = f"{KEY_ROOT}:{ORG_A}:my_day:judged:{ME.lower()}"
        stored, kwargs = redis_fake.strings[judged_key]
        assert json.loads(stored)["ver"] == ["0", "0"] and kwargs == {"ex": 26 * 3600}
        world.log.clear()
        # Nothing moved: not dirty, and Redis alone says so.
        assert _pass(self.LATE + timedelta(seconds=60)) == "not_due"
        assert world.log == []
        # A burst of writes inside the 2 minutes: no second pass.
        for n in range(5):
            self._bump(redis_fake)
            assert _pass(self.LATE + timedelta(seconds=61 + n)) == "not_due"
        assert len(world.judged) == 1 and world.log == []
        # The guard ends: one pass for the whole burst.
        del redis_fake.strings[dirty_key]
        assert _pass(self.LATE + timedelta(seconds=130)) == "judged:asked"
        assert len(world.judged) == 2
        assert json.loads(redis_fake.strings[judged_key][0])["ver"] == ["0", "5"]
        del redis_fake.strings[dirty_key]
        assert _pass(self.LATE + timedelta(seconds=260)) == "not_due"
        assert len(world.judged) == 2

    def test_with_the_cache_off_a_bump_makes_no_pass(self, world, redis_fake) -> None:
        _seed_active(redis_fake, ME)
        assert _pass(self.LATE) == "not_due"
        self._bump(redis_fake)
        assert _pass(self.LATE + timedelta(minutes=5)) == "not_due"
        assert world.judged == []

    def test_the_versions_are_the_caches_own_keys(self, redis_fake) -> None:
        from gateway.routes.shell import cache

        redis_fake.strings[f"{KEY_ROOT}:{ORG_A}:my_day:ver"] = ("17-ab", {})
        redis_fake.strings[f"{KEY_ROOT}:{ORG_A}:my_day:ver:{ME.lower()}"] = ("3", {})

        async def go() -> tuple[str, str]:
            with tenant_redis.organization_scope(ORG_A):
                return await cache.versions(ME)

        assert asyncio.run(go()) == ("17-ab", "3")
        redis_fake.strings.clear()
        assert asyncio.run(go()) == ("0", "0")


class TestTheBound:
    """Done-when 8 (§10.9 item 8), and three members at once at most."""

    @pytest.fixture(autouse=True)
    def _gate_on(self, monkeypatch) -> None:
        monkeypatch.setattr(my_day_loop, "judging_on", lambda org: True)

    def test_a_member_past_30_seconds_is_cancelled_and_the_next_runs(
            self, monkeypatch) -> None:
        assert my_day_loop.MEMBER_TIMEOUT_S == 30.0
        cancelled: list[str] = []

        async def _pass_fake(org: str, member: str, now: Any = None) -> str:
            if member == "hung@a.test":
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    cancelled.append(member)
                    raise
            return "judged:asked"

        monkeypatch.setattr(my_day_loop, "MEMBER_TIMEOUT_S", 0.05)
        monkeypatch.setattr(my_day_loop, "member_pass", _pass_fake)
        started = time.monotonic()

        async def go() -> list[str | None]:
            # One lane: the next member waits for the hung one.
            return await my_day_loop.run_members(
                ORG_A, ["hung@a.test", "next@a.test"], asyncio.Semaphore(1))

        assert asyncio.run(go()) == ["timeout", "judged:asked"]
        assert cancelled == ["hung@a.test"]
        assert time.monotonic() - started < 2

    def test_three_members_run_at_once_at_most(self, monkeypatch) -> None:
        assert my_day_loop.MEMBER_CONCURRENCY == 3
        live = {"now": 0, "peak": 0}

        async def _pass_fake(org: str, member: str, now: Any = None) -> str:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
            await asyncio.sleep(0.01)
            live["now"] -= 1
            return "not_due"

        monkeypatch.setattr(my_day_loop, "member_pass", _pass_fake)
        members = [f"m{n}@a.test" for n in range(8)]
        assert asyncio.run(my_day_loop.run_members(ORG_A, members)) == ["not_due"] * 8
        assert live["peak"] == 3

    def test_each_member_runs_in_its_own_tenant_context(self, monkeypatch) -> None:
        """``member_context`` binds for ever. One caller runs two members, one
        after the other, IN ONE TASK. Without ``_one_member``'s own task the
        first member's binding reaches the second, and the caller (fix round
        1, P2-3: ``gather`` alone cannot show it, so this does not use it)."""
        from acb_common.db import bind_tenant, current_tenant

        seen: list[str | None] = []

        async def _pass_fake(org: str, member: str, now: Any = None) -> str:
            seen.append(current_tenant())
            bind_tenant(f"bound-by-{member}")
            return "judged:asked"

        monkeypatch.setattr(my_day_loop, "member_pass", _pass_fake)

        async def go() -> str | None:
            gate = asyncio.Semaphore(1)
            await my_day_loop._one_member(ORG_A, "a@a.test", gate, None)
            await my_day_loop._one_member(ORG_A, "b@a.test", gate, None)
            return current_tenant()

        assert asyncio.run(go()) is None
        assert seen == [None, None]

    def test_the_member_pass_runs_after_the_org_and_outside_its_bound(
            self, monkeypatch) -> None:
        order: list[str] = []

        async def _org(org: str) -> list[str]:
            order.append("run_org")
            return ["a@a.test"]

        async def _orgs() -> list[str]:
            return [ORG_A]

        async def _pass_fake(org: str, member: str, now: Any = None) -> str:
            await asyncio.sleep(0.2)  # longer than the org's bound below
            order.append("member")
            return "judged:asked"

        monkeypatch.setattr(my_day_loop, "PER_ORG_TIMEOUT_S", 0.05)
        monkeypatch.setattr(my_day_loop, "run_org", _org)
        monkeypatch.setattr(my_day_loop, "_organization_ids", _orgs)
        monkeypatch.setattr(my_day_loop, "member_pass", _pass_fake)
        assert asyncio.run(my_day_loop.run_tick()) == 1
        assert order == ["run_org", "member"]


class TestTheJudgmentsSweep:
    """Done-when 9 (§13.2). The R8 half deletes on a real Postgres."""

    def test_the_statement(self) -> None:
        sql = feed_decide._sweep_sql()
        assert sql.startswith("DELETE FROM my_day_judgments WHERE ")
        assert "organization_id = CAST(NULLIF(current_setting('app.tenant_id'" in sql
        assert "created_at < now() - interval '30 days'" in sql

    def test_feed_decide_is_the_one_writer_of_the_table(self) -> None:
        pattern = re.compile(r"(INSERT INTO|DELETE FROM|UPDATE)\s+my_day_judgments")
        writers = [
            p.name for p in (_REPO / "apps").rglob("*.py")
            if pattern.search(p.read_text(encoding="utf-8", errors="ignore"))
        ]
        assert writers == ["feed_decide.py"], writers


# ── Fix round 1 ─────────────────────────────────────────────────────────────


def _members(org: str = ORG_A) -> list[str]:
    return asyncio.run(my_day_loop.active_members(org))


def _run(members: list[str], when: datetime, org: str = ORG_A) -> list[str | None]:
    return asyncio.run(my_day_loop.run_members(org, members, now=when))


class TestTheExactAddress:
    """P1: a mixed-case session address goes end to end with its own
    spelling. ``my_day_card_state``, ``user_settings`` and
    ``my_day_judgments`` key on that spelling, so a lower-case copy finds
    none of the member's rows."""

    def test_the_session_spelling_reaches_every_read_and_judge(
            self, world, redis_fake, switch, monkeypatch) -> None:
        switch(True)
        monkeypatch.setattr(my_day_loop, "judging_on", lambda org: True)
        asyncio.run(my_day_loop.note_active(_user()))
        members = _members()
        assert members == [ME]
        assert _run(members, EARLY + timedelta(minutes=15)) == ["judged:asked"]
        assert world.emails == {"resolve_identity": [ME], "status": [ME],
                                "member_context": [ME]}
        (user, _now), = world.judged
        assert user.email == ME
        # The loop's own guards key on the lower case, as the cache does.
        assert f"{KEY_ROOT}:{ORG_A}:my_day:morning:{ME.lower()}:2026-10-14" in \
            redis_fake.strings

    def test_a_departed_mixed_case_member_leaves_the_set(self, world, redis_fake) -> None:
        _seed_active(redis_fake, ME)
        world.status = "removed"
        assert _pass(EARLY) == "departed"
        assert redis_fake.zsets[_zkey(ORG_A)] == {}


class TestTheDayRead:
    """P2-1: the day read holds one pool connection at most."""

    @pytest.fixture
    def hours(self, monkeypatch) -> tuple[list[Any], list[str]]:
        from contextlib import asynccontextmanager

        got: list[Any] = []
        opened: list[str] = []

        async def _hours(user: Any, db: Any) -> Any:
            got.append(db)
            return state.Day(state._zone(ZONE))

        @asynccontextmanager
        async def _session(org: str | None = None):
            opened.append(str(org))
            yield "db"

        monkeypatch.setattr(state, "_hours", _hours)
        monkeypatch.setattr(my_day_loop, "tenant_session", _session)
        return got, opened

    def test_a_tasks_holder_opens_no_session_around_the_settings_read(self, hours) -> None:
        got, opened = hours
        asyncio.run(my_day_loop._read_day(ORG_A, _ctx(ORG_A, "feature:tasks")))
        assert got == [None] and opened == []

    def test_any_other_member_reads_the_zone_in_one_session(self, hours) -> None:
        got, opened = hours
        asyncio.run(my_day_loop._read_day(ORG_A, _ctx(ORG_A, "feature:projects")))
        assert got == ["db"] and opened == [ORG_A]


class TestTheFailureThrottle:
    """P2-2: a failure holds the member back for 2 minutes, so a starved
    database does not get the slow path again at each tick."""

    @pytest.fixture(autouse=True)
    def _gate_on(self, monkeypatch) -> None:
        monkeypatch.setattr(my_day_loop, "judging_on", lambda org: True)

    HOLD = f"{KEY_ROOT}:{ORG_A}:my_day:hold:{ME.lower()}"

    def test_a_failed_identity_read_holds_the_member(self, world, redis_fake) -> None:
        _seed_active(redis_fake, ME)
        world.failed = True
        assert _run([ME], EARLY) == ["identity_failed"]
        assert redis_fake.strings[self.HOLD][1] == {"nx": True, "ex": 120}
        world.log.clear()
        assert _run([ME], EARLY + timedelta(seconds=60)) == ["held"]
        assert world.log == []
        del redis_fake.strings[self.HOLD]  # the 2 minutes pass
        world.failed = False
        assert _run([ME], EARLY + timedelta(seconds=130)) == ["not_due"]
        assert "member_context" in world.log

    def test_a_failed_pass_holds_the_member(self, world, redis_fake, monkeypatch) -> None:
        import acb_auth.deps as deps

        async def _boom(email: str) -> Any:
            raise ConnectionError("pool exhausted")

        monkeypatch.setattr(deps, "member_context", _boom)
        _seed_active(redis_fake, ME)
        assert _run([ME], EARLY) == ["failed"]
        assert self.HOLD in redis_fake.strings
        assert _run([ME], EARLY + timedelta(seconds=60)) == ["held"]

    def test_a_timed_out_pass_holds_the_member(self, world, redis_fake, monkeypatch) -> None:
        async def _slow(org: str, user: Any) -> Any:
            await asyncio.sleep(60)

        monkeypatch.setattr(my_day_loop, "_read_day", _slow)
        monkeypatch.setattr(my_day_loop, "MEMBER_TIMEOUT_S", 0.05)
        _seed_active(redis_fake, ME)
        assert _run([ME], EARLY) == ["timeout"]
        assert self.HOLD in redis_fake.strings

    def test_an_org_in_its_cool_down_skips_the_slow_path(self, world, redis_fake,
                                                         monkeypatch) -> None:
        from gateway import decide_features as df

        monkeypatch.setitem(df._cooldown_until, ORG_A, df._now() + 600)
        _seed_active(redis_fake, ME)
        assert _pass(EARLY) == "cooldown"
        assert world.log == []
        assert self.HOLD not in redis_fake.strings

    @pytest.mark.parametrize("reason", ["cooldown", "cap", "failed"])
    def test_a_morning_that_judged_nothing_is_not_lost(self, world, redis_fake,
                                                       monkeypatch, reason) -> None:
        outcomes = [reason, "asked"]

        async def _judge(user: UserContext, now: datetime | None = None) -> Any:
            world.judged.append((user, now))
            return feed_decide.Judged(outcomes.pop(0))

        monkeypatch.setattr(feed_decide, "judge", _judge)
        _seed_active(redis_fake, ME)
        guard = f"{KEY_ROOT}:{ORG_A}:my_day:morning:{ME.lower()}:2026-10-14"
        assert _run([ME], EARLY + timedelta(minutes=15)) == [f"judged:{reason}"]
        assert guard not in redis_fake.strings, "the morning was given back"
        assert self.HOLD in redis_fake.strings
        assert _run([ME], EARLY + timedelta(minutes=16)) == ["held"]
        del redis_fake.strings[self.HOLD]
        del redis_fake.strings[f"{KEY_ROOT}:{ORG_A}:my_day:dirty:{ME.lower()}"]
        assert _run([ME], EARLY + timedelta(minutes=18)) == ["judged:asked"]
        assert guard in redis_fake.strings and len(world.judged) == 2


class TestTheMorningTakesTheDirtyGuard:
    """Verifier P3: the morning pass takes the 2-minute guard too, so a
    dirty member gets no second pass a tick later."""

    def test_no_dirty_pass_a_tick_after_the_morning(self, world, redis_fake,
                                                    monkeypatch) -> None:
        monkeypatch.setattr(get_settings(), "my_day_cache", True)
        _seed_active(redis_fake, ME)
        # The first look reads the day and judges: never judged is dirty.
        assert _pass(EARLY + timedelta(minutes=15)) == "judged:asked"
        dirty = f"{KEY_ROOT}:{ORG_A}:my_day:dirty:{ME.lower()}"
        assert dirty in redis_fake.strings
        redis_fake.strings[f"{KEY_ROOT}:{ORG_A}:my_day:ver:{ME.lower()}"] = ("1", {})
        assert _pass(EARLY + timedelta(minutes=16)) == "not_due"
        assert len(world.judged) == 1
