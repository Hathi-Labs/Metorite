"""WS-58 MD-5a — the read cache of ``GET /shell/feed`` (``my_day_feed.md``
§8.1, §8.2, §8.4 and MD-5a done-when 1 to 12).

Hermetic. The Redis here is a fake UNDER the real ``TenantRedis``, as
``test_mcp_edge_writes.py`` does, so every key goes through the real
tenant-prefix wrapper. The app reads are the fakes of ``test_shell_feed.py``.
The R8 half, the statement counts of done-when 2 and 4, is
``test_my_day_cache_r8.py``.

Each class fences one done-when, and each is written so a wrong build fails:

* ``TestAHitRunsNothing`` (1): a hit calls no provider, no state join and no
  Handled read.
* ``TestTheClockEdge`` (3): a ``block_soon`` card appears 30 minutes before
  its block, with no event in between.
* ``TestTheSink`` (4, hermetic half): a ``pm.*`` event bumps the org version.
* ``TestTheMemberBumps`` (5): a state POST, a prefs PUT and new mail bump the
  member version.
* ``TestAnOutage`` (7): Redis down serves the uncached answer.
* ``TestTheSwitch`` (8): with ``MY_DAY_CACHE`` off nothing touches Redis.
* ``TestTheGrantHash`` (9): a revoked feature or admin test misses at once.
* ``TestTheETag`` (10): a body that differs never answers 304.
* ``TestAWriteDuringACompute`` (11): the versions are read before the compute.
* ``TestWhatIsNotStored`` (12): a failed source or Handled read is not kept.
"""

from __future__ import annotations

import ast
import asyncio
import json
import time as _time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common import tenant_redis
from acb_common.settings import get_settings
from acb_common.tenant_redis import TenantRedis
from gateway.routes.shell import cache, feed, handled, state
from gateway.routes.shell import needs as shell
from starlette.responses import Response

from tests.unit.test_shell_feed import (  # noqa: F401  (the fixture is used by name)
    FULL,
    ME,
    ORG,
    _block,
    _bound,
    fakes,
)

REPO = Path(__file__).resolve().parents[2]


# ── The fake Redis, under the real wrapper ──────────────────────────────────


class _FakeRedis:
    """``GET``, ``MGET``, ``SET`` (with ``NX`` and ``EX``) and ``INCR``.

    Its clock is the feed's one clock (``needs._now``), so a test that moves
    the feed's time moves the TTLs too. ``fail`` raises on every command, and
    ``slow`` delays each one."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float | None]] = {}
        self.fail: BaseException | None = None
        self.slow = 0.0
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def _now() -> float:
        return shell._now().timestamp()

    def _live(self, name: str) -> tuple[str, float | None] | None:
        found = self.store.get(name)
        if found is None or (found[1] is not None and found[1] <= self._now()):
            self.store.pop(name, None)
            return None
        return found

    async def _check(self, op: str, name: str) -> None:
        self.calls.append((op, name))
        if self.slow:
            await asyncio.sleep(self.slow)
        if self.fail is not None:
            raise self.fail

    async def get(self, name: str) -> str | None:
        await self._check("get", name)
        found = self._live(name)
        return found[0] if found else None

    async def mget(self, names: list[str]) -> list[str | None]:
        await self._check("mget", ",".join(names))
        return [(f[0] if (f := self._live(n)) else None) for n in names]

    async def set(self, name: str, value: str, *, nx: bool = False,
                  ex: int | None = None) -> bool | None:
        await self._check("set", name)
        if nx and self._live(name) is not None:
            return None
        self.store[name] = (value, None if ex is None else self._now() + ex)
        return True

    async def incr(self, name: str, amount: int = 1) -> int:
        await self._check("incr", name)
        found = self._live(name)
        value = int(found[0]) + amount if found else amount
        self.store[name] = (str(value), found[1] if found else None)
        return value

    def ops(self, op: str) -> list[str]:
        return [name for o, name in self.calls if o == op]


@pytest.fixture
def redis(monkeypatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(fake))
    return fake


@pytest.fixture
def on(monkeypatch) -> None:
    """``MY_DAY_CACHE`` on, through its one reader's own source."""
    monkeypatch.setattr(get_settings(), "my_day_cache", True)


@pytest.fixture
def off(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "my_day_cache", False)


# ── Helpers ─────────────────────────────────────────────────────────────────


def member(*features: str, email: str = ME, admin: bool = False) -> UserContext:
    grants = {f"feature:{f}" for f in features}
    if admin:
        grants.add(shell.ADMIN_PERMISSION)
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=ORG,
                       access=EffectiveAccess(role_granted=frozenset(grants)))


class _Req:
    def __init__(self, tag: str | None) -> None:
        self.headers = {"if-none-match": tag} if tag else {}


def read(user: UserContext, tag: str | None = None, limit: int = feed.DEFAULT_FEED_LIMIT):
    """One feed read, as the route sees it: ``(answer, response)``. The
    answer is a dict, or a 304 ``Response``."""
    resp = Response()
    answer = _bound(lambda: feed.shell_feed(limit=limit, user=user, request=_Req(tag),
                                            response=resp))
    return answer, resp


def app_calls(f) -> int:
    """How many app reads the providers made (every fake records its call)."""
    return len(f.calls)


def advance(monkeypatch, f, delta: timedelta) -> datetime:
    """Move the feed's one clock, and with it the fake Redis's clock."""
    later = f.now + delta
    monkeypatch.setattr(shell, "_now", lambda: later)
    return later


def answer_keys(r: _FakeRedis) -> list[str]:
    return [k for k in r.store if ":my_day:answer:" in k]


def bump_org(org: str = ORG) -> None:
    async def go():
        from acb_common.db import bind_tenant, release_tenant

        token = bind_tenant(org)
        try:
            await cache.on_event("projects", "pm.task.updated", {"task_id": "t-1"})
        finally:
            release_tenant(token)

    asyncio.run(go())


# ── done-when 1 ─────────────────────────────────────────────────────────────


class TestAHitRunsNothing:
    def test_a_hit_runs_no_provider_no_state_join_and_no_handled_read(
            self, fakes, redis, on, monkeypatch):  # noqa: F811
        user = member(*FULL)
        first, resp = read(user)
        assert app_calls(fakes) > 0
        assert answer_keys(redis), "the miss did not store the answer"

        counts = {"apply_state": 0, "handled_run": 0, "run_providers": 0}

        def counted(name, fn):
            async def wrapper(*a, **k):
                counts[name] += 1
                return await fn(*a, **k)
            return wrapper

        monkeypatch.setattr(state, "apply_state", counted("apply_state", state.apply_state))
        monkeypatch.setattr(handled, "handled_run", counted("handled_run", handled.handled_run))
        monkeypatch.setattr(shell, "run_providers", counted("run_providers", shell.run_providers))
        before = app_calls(fakes)

        second, resp2 = read(user)
        assert counts == {"apply_state": 0, "handled_run": 0, "run_providers": 0}
        assert app_calls(fakes) == before
        assert second == first
        assert resp2.headers["etag"] == resp.headers["etag"]

    def test_the_counters_see_a_miss(self, fakes, redis, on, monkeypatch):  # noqa: F811
        # Non-vacuity of the case above: the same wrappers count a miss.
        counts = {"n": 0}

        async def counted(*a, **k):
            counts["n"] += 1
            return await real(*a, **k)

        real = state.apply_state
        monkeypatch.setattr(state, "apply_state", counted)
        read(member(*FULL))
        assert counts["n"] == 1

    def test_the_key_holds_the_tenant_the_member_the_limit_and_the_hash(
            self, fakes, redis, on):  # noqa: F811
        user = member(*FULL, email=ME.upper())
        read(user, limit=40)
        [stored] = answer_keys(redis)
        assert stored == (f"cc:{ORG}:my_day:answer:{ME}:40:{cache.features_hash(user)}")


# ── done-when 3 ─────────────────────────────────────────────────────────────


class TestTheClockEdge:
    def test_a_block_appears_30_minutes_before_it_with_no_event(
            self, fakes, redis, on, monkeypatch):  # noqa: F811
        f = fakes
        f.tasks[ME], f.notes[ME] = [], []
        # The block starts in 35 minutes, so its edge is 5 minutes away:
        # sooner than the 600-second bound, so only the edge can end the hit.
        start = f.now + timedelta(minutes=35)
        f.blocks[ME] = [_block("t-later", "Review the drawings", start, 30)]
        user = member("projects", "tasks")

        first, _ = read(user)
        assert first["sources"]["calendar"] == "ok"
        assert not [c for c in first["cards"] if c["kind"] == "block_soon"]

        # Inside the edge: a hit, so the answer is the same and nothing ran.
        advance(monkeypatch, f, timedelta(minutes=4, seconds=50))
        before = app_calls(f)
        same, _ = read(user)
        assert same == first and app_calls(f) == before

        # Past the edge (start minus 30 minutes): a miss, and the card is here.
        advance(monkeypatch, f, timedelta(minutes=5, seconds=1))
        later, _ = read(user)
        assert app_calls(f) > before
        soon = [c for c in later["cards"] if c["kind"] == "block_soon"]
        assert [c["signal_id"] for c in soon] == ["tasks:t-later"]
        assert redis.ops("incr") == [], "an event moved the version"

    def test_the_calendar_provider_names_the_edge(self, fakes):  # noqa: F811
        now = fakes.now
        blocks = [{"start": (now + timedelta(minutes=m)).isoformat()} for m in (10, 45, 90)]
        # The block in 10 minutes is already a signal. The next one enters
        # the window 15 minutes from now.
        assert feed._next_block_edge(blocks, now) == (now + timedelta(minutes=15)).isoformat()
        assert feed._next_block_edge(blocks[:1], now) is None


class TestTheTtl:
    NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)

    def test_600_seconds_at_most(self):
        assert cache.ttl_seconds(self.NOW, [], {"cards": []}) == 600
        far = self.NOW + timedelta(hours=5)
        assert cache.ttl_seconds(self.NOW, [far], {"cards": []}) == 600

    def test_the_next_future_edge(self):
        past = self.NOW - timedelta(minutes=5)
        soon = self.NOW + timedelta(minutes=4)
        assert cache.ttl_seconds(self.NOW, [past, soon], {"cards": []}) == 240

    @pytest.mark.parametrize("card", [
        {"why_key": "starts_in", "folded": []},
        {"why_key": "due_soon", "folded": []},
        {"why_key": "days_late", "folded": ["block_soon"]},
    ])
    def test_a_countdown_holds_60_seconds_at_most(self, card):
        assert cache.ttl_seconds(self.NOW, [], {"cards": [card]}) == 60

    def test_no_countdown_no_60_second_limit(self):
        card = {"why_key": "days_late", "folded": ["block_overlap"]}
        assert cache.ttl_seconds(self.NOW, [], {"cards": [card]}) == 600

    def test_the_signal_edges(self):
        due = self.NOW + timedelta(hours=5)
        sig = SimpleNamespace(urgent_at=due.isoformat(), expires_at=None,
                              facts={"due_at": due.isoformat()})
        edges = set(cache.signal_edges([sig]))
        assert {due, due - timedelta(hours=2)} <= edges

    def test_the_day_edges(self):
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Kolkata")
        day = state.Day(tz, 9, 18)
        now = datetime(2026, 10, 14, 8, 0, tzinfo=tz)
        edges = cache.day_edges(day, tz, now)
        assert datetime(2026, 10, 14, 9, 0, tzinfo=tz) in edges   # the day start
        assert datetime(2026, 10, 14, 10, 0, tzinfo=tz) in edges  # no_plan_yet's grace
        assert datetime(2026, 10, 14, 17, 0, tzinfo=tz) in edges  # the day end less 60
        assert datetime(2026, 10, 15, 0, 0, tzinfo=tz) in edges   # midnight

    def test_the_snooze_edge_is_the_earliest_time_snooze(self):
        now = self.NOW
        rows = {
            "a": {"state": "snoozed", "snooze_on_change": False,
                  "snoozed_until": now + timedelta(hours=3)},
            "b": {"state": "snoozed", "snooze_on_change": False,
                  "snoozed_until": now + timedelta(hours=1)},
            "c": {"state": "snoozed", "snooze_on_change": True, "snoozed_until": None},
            "d": {"state": "snoozed", "snooze_on_change": False,
                  "snoozed_until": now - timedelta(hours=1)},
            "e": {"state": "dismissed"},
        }
        assert state.snooze_edge(rows, now) == now + timedelta(hours=1)
        assert state.snooze_edge({}, now) is None

    def test_a_snoozed_card_returns_on_time(self, fakes, redis, on, monkeypatch):  # noqa: F811
        # The snooze edge reaches the stored TTL: the card comes back at its
        # end, with no event in between (§8.4 item 2, item 21).
        f = fakes
        f.blocks[ME] = []
        until = f.now + timedelta(minutes=7)
        user = member(*FULL)
        first, _ = read(user)
        sid = first["cards"][0]["signal_id"]
        f.state_rows[ME] = {sid: {"state": "snoozed", "snooze_on_change": False,
                                  "snoozed_until": until, "fingerprint": "x"}}
        # The write bumped the member version, as the POST does.
        asyncio.run(_bump_member(ME))
        hidden, _ = read(user)
        assert sid not in [c["signal_id"] for c in hidden["cards"]]
        advance(monkeypatch, f, timedelta(minutes=7, seconds=1))
        back, _ = read(user)
        assert sid in [c["signal_id"] for c in back["cards"]]


async def _bump_member(email: str) -> None:
    await cache.bump_member(ORG, email)


# ── done-when 4, the hermetic half ──────────────────────────────────────────


class TestTheSink:
    def test_a_pm_event_bumps_the_org_version_of_the_bound_tenant(self, redis, on):
        bump_org()
        assert redis.ops("incr") == [f"cc:{ORG}:my_day:ver"]

    def test_with_no_tenant_bound_the_payload_names_the_org(self, redis, on):
        asyncio.run(cache.on_event("projects", "pm.task.assigned",
                                   {"task_id": "t", "organization_id": "org-x"}))
        assert redis.ops("incr") == ["cc:org-x:my_day:ver"]

    def test_an_event_with_no_org_bumps_nothing(self, redis, on):
        asyncio.run(cache.on_event("projects", "pm.task.updated", {"task_id": "t"}))
        assert redis.calls == []

    @pytest.mark.parametrize("source,event", [("zoho", "pm.task.updated"),
                                              ("projects", "task.updated")])
    def test_another_event_bumps_nothing(self, redis, on, source, event):
        asyncio.run(cache.on_event(source, event, {"organization_id": ORG}))
        assert redis.calls == []

    def test_a_slow_redis_costs_the_write_50_ms_at_most(self, redis, on):
        redis.slow = 2.0
        began = _time.monotonic()
        bump_org()  # never raises
        assert _time.monotonic() - began < 0.5

    def test_a_failed_redis_never_raises(self, redis, on):
        redis.fail = ConnectionError("down")
        bump_org()

    def test_the_bump_makes_the_next_read_miss(self, fakes, redis, on):  # noqa: F811
        user = member(*FULL)
        read(user)
        before = app_calls(fakes)
        read(user)
        assert app_calls(fakes) == before
        bump_org()
        read(user)
        assert app_calls(fakes) > before

    def test_the_sink_is_registered_in_its_own_try(self):
        """§8.4 item 20: a failed workflows import must not drop the sink."""
        tree = ast.parse((REPO / "apps/services/gateway/gateway/main.py").read_text(
            encoding="utf-8"))
        owners = []
        for node in tree.body:
            if not isinstance(node, ast.Try):
                continue
            mods = {n.module for n in ast.walk(node) if isinstance(n, ast.ImportFrom)}
            if "gateway.routes.shell.cache" in mods:
                owners.append(mods)
        assert len(owners) == 1, "register the sink once, at module level"
        assert not any(m and m.startswith("gateway.routes.workflows") for m in owners[0])
        assert "ingestion.event_hooks" in owners[0]


# ── done-when 5 ─────────────────────────────────────────────────────────────


def _member_key(email: str = ME) -> str:
    return f"cc:{ORG}:my_day:ver:{email}"


class TestTheMemberBumps:
    def test_a_state_post_bumps_the_member_version(self, fakes, redis, on):  # noqa: F811
        user = member(*FULL)
        first, _ = read(user)
        c = first["cards"][0]
        body = state.StateBody.model_validate({"items": [{
            "signal_id": c["signal_id"], "state_fp": c["state_fp"], "kind": c["kind"],
            "action": "dismiss"}]})
        out = _bound(lambda: state.post_feed_state(body=body, user=user))
        assert out == {"written": 1}
        assert redis.ops("incr") == [_member_key()]
        before = app_calls(fakes)
        after, _ = read(user)
        assert app_calls(fakes) > before
        assert c["signal_id"] not in [x["signal_id"] for x in after["cards"]]

    def test_a_prefs_put_bumps_the_member_version(self, redis, on, monkeypatch):
        from contextlib import asynccontextmanager

        from gateway.routes.shell import prefs

        @asynccontextmanager
        async def _session(organization_id: Any = None):
            yield None

        async def _write(db, uid, key, value):
            return {key: value}

        monkeypatch.setattr(prefs, "_tenant_session", _session)
        monkeypatch.setattr(prefs, "_write", _write)
        out = _bound(lambda: prefs.put_feed_prefs(body={"key": "mutes", "value": []},
                                                  user=member(*FULL)))
        assert out == {"mutes": []}
        assert redis.ops("incr") == [_member_key()]

    def test_new_mail_bumps_the_mailbox_owner(self, redis, on, monkeypatch):
        from gateway.routes.email import scheduler_hooks

        async def owner(account_id):
            return "Owner@Customer.Example" if account_id == "a-1" else None

        monkeypatch.setattr(scheduler_hooks, "mailbox_owner", owner)
        _bound(lambda: scheduler_hooks.bump_my_day_owner("a-1"))
        assert redis.ops("incr") == [_member_key("owner@customer.example")]

    def test_new_mail_bumps_nothing_without_a_tenant(self, redis, on, monkeypatch):
        from gateway.routes.email import scheduler_hooks

        async def owner(account_id):
            raise AssertionError("no tenant: no read")

        monkeypatch.setattr(scheduler_hooks, "mailbox_owner", owner)
        asyncio.run(scheduler_hooks.bump_my_day_owner("a-1"))
        assert redis.calls == []

    def test_the_new_mail_hook_bumps_after_every_step(self):
        """Only ``on_new_mail`` bumps (§8.4 item 19), and it bumps LAST."""
        tree = ast.parse((REPO / "apps/services/gateway/gateway/routes/email/"
                          "scheduler_hooks.py").read_text(encoding="utf-8"))
        callers = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                for call in ast.walk(node):
                    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                            and call.func.id == "bump_my_day_owner"):
                        callers[node.name] = node
        assert set(callers) == {"process_new_mail"}
        last = callers["process_new_mail"].body[-1]
        names = {n.func.id for n in ast.walk(last)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "bump_my_day_owner" in names


# ── done-when 7 ─────────────────────────────────────────────────────────────


class TestAnOutage:
    def test_redis_down_serves_the_uncached_answer(self, fakes, redis, off, monkeypatch):  # noqa: F811
        user = member(*FULL)
        plain, _ = read(user)

        monkeypatch.setattr(get_settings(), "my_day_cache", True)
        redis.fail = ConnectionError("redis away")
        before = app_calls(fakes)
        down, _ = read(user)
        assert app_calls(fakes) > before
        assert down == json.loads(json.dumps(plain, default=str))
        assert redis.calls, "the read did not try Redis"
        # Each read runs the providers while it is down.
        again = app_calls(fakes)
        read(user)
        assert app_calls(fakes) > again

    def test_a_slow_redis_is_an_outage(self, fakes, redis, on):  # noqa: F811
        redis.slow = 2.0
        began = _time.monotonic()
        answer, _ = read(member(*FULL))
        assert _time.monotonic() - began < 1.5
        assert answer["cards"]


# ── done-when 8 ─────────────────────────────────────────────────────────────


class TestTheSwitch:
    def test_off_every_read_runs_the_providers_and_redis_hears_nothing(
            self, fakes, redis, off):  # noqa: F811
        user = member(*FULL)
        _, resp = read(user)
        first = app_calls(fakes)
        read(user)
        assert app_calls(fakes) == 2 * first
        assert "etag" not in resp.headers
        bump_org()
        asyncio.run(_bump_member(ME))
        assert redis.calls == []

    def test_off_the_mail_hook_reads_no_owner(self, redis, off, monkeypatch):
        from gateway.routes.email import scheduler_hooks

        async def owner(account_id):
            raise AssertionError("the switch is off: no read")

        monkeypatch.setattr(scheduler_hooks, "mailbox_owner", owner)
        _bound(lambda: scheduler_hooks.bump_my_day_owner("a-1"))
        assert redis.calls == []

    def test_the_default_is_off(self):
        from acb_common.settings import Settings

        assert Settings.model_fields["my_day_cache"].default is False

    def test_one_reader(self):
        """§8.4 item 7: the flag is read in one place."""
        readers = []
        for root in ("apps", "packages"):
            for path in (REPO / root).rglob("*.py"):
                if "node_modules" in path.parts or ".venv" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if "my_day_cache" in text:
                    readers.append(path.relative_to(REPO).as_posix())
        assert sorted(readers) == [
            "apps/services/gateway/gateway/routes/shell/cache.py",
            "packages/acb_common/acb_common/settings.py",
        ]


# ── done-when 9 ─────────────────────────────────────────────────────────────


class TestTheGrantHash:
    def test_a_revoked_feature_stops_its_cards_at_the_next_read(
            self, fakes, redis, on):  # noqa: F811
        first, _ = read(member(*FULL))
        assert [c for c in first["cards"] if c["app"] == "email"]
        before = app_calls(fakes)
        after, _ = read(member("projects", "tasks"))
        assert app_calls(fakes) > before, "the revoked member got the stored answer"
        assert after["sources"]["email"] == "absent"
        assert not [c for c in after["cards"] if c["app"] == "email"]

    def test_a_revoked_admin_test_stops_the_approval_cards(self, fakes, redis, on):  # noqa: F811
        first, _ = read(member(*FULL, "approvals", admin=True))
        assert [c for c in first["cards"] if c["app"] == "approvals"]
        before = app_calls(fakes)
        after, _ = read(member(*FULL, "approvals"))
        assert after["sources"]["approvals"] == "absent"
        assert not [c for c in after["cards"] if c["app"] == "approvals"]
        assert after != first and app_calls(fakes) >= before

    def test_the_hash_moves_with_each_grant(self):
        base = cache.features_hash(member(*FULL))
        assert cache.features_hash(member(*FULL)) == base
        assert cache.features_hash(member("projects", "email")) != base
        assert cache.features_hash(member(*FULL, admin=True)) != base


# ── done-when 10 ────────────────────────────────────────────────────────────


class TestTheETag:
    def test_the_tag_is_the_body_hash(self, fakes, redis, on):  # noqa: F811
        answer, resp = read(member(*FULL))
        assert resp.headers["etag"] == cache.etag(cache.body_json(answer))
        assert resp.headers["etag"].startswith('W/"') and len(resp.headers["etag"]) == 20

    def test_a_hit_with_the_tag_answers_304(self, fakes, redis, on):  # noqa: F811
        user = member(*FULL)
        _, resp = read(user)
        tag = resp.headers["etag"]
        before = app_calls(fakes)
        again, _ = read(user, tag)
        assert isinstance(again, Response) and again.status_code == 304
        assert again.headers["etag"] == tag and again.body == b""
        assert app_calls(fakes) == before

    def test_a_recompute_whose_body_differs_never_answers_304(self, fakes, redis, on):  # noqa: F811
        user = member(*FULL)
        _, resp = read(user)
        tag = resp.headers["etag"]
        fakes.tasks[ME].append({**fakes.tasks[ME][0], "id": "t-new", "title": "A new task"})
        bump_org()
        answer, resp2 = read(user, tag)
        assert isinstance(answer, dict), "a changed body answered 304"
        assert resp2.headers["etag"] != tag
        assert "tasks:t-new" in [c["signal_id"] for c in answer["cards"]]

    def test_a_recompute_with_the_same_body_answers_304(self, fakes, redis, on):  # noqa: F811
        user = member(*FULL)
        _, resp = read(user)
        tag = resp.headers["etag"]
        bump_org()
        before = app_calls(fakes)
        answer, _ = read(user, tag)
        assert app_calls(fakes) > before, "the bump did not make a miss"
        assert isinstance(answer, Response) and answer.status_code == 304

    def test_another_tag_gets_the_body(self, fakes, redis, on):  # noqa: F811
        user = member(*FULL)
        read(user)
        answer, resp = read(user, 'W/"0000000000000000"')
        assert isinstance(answer, dict) and resp.headers["etag"]

    @pytest.mark.parametrize("header,hit", [
        ('W/"abc"', True), ('"abc"', True), ('W/"x", W/"abc"', True),
        ('W/"abcd"', False), ("*", False), ("", False), (None, False)])
    def test_the_header_match(self, header, hit):
        assert cache.matches(header, 'W/"abc"') is hit


# ── done-when 11 ────────────────────────────────────────────────────────────


class TestAWriteDuringACompute:
    def test_a_write_during_the_compute_makes_the_next_read_miss(
            self, fakes, redis, on, monkeypatch):  # noqa: F811
        from gateway.routes.projects import personal

        real = fakes.my_due_tasks
        fired = {"n": 0}

        async def write_meanwhile(user, *, limit):
            if not fired["n"]:
                fired["n"] = 1
                await cache.bump_member(ORG, ME)  # a state POST lands now
            return await real(user, limit=limit)

        monkeypatch.setattr(personal, "my_due_tasks", write_meanwhile)
        user = member(*FULL)
        read(user)
        [stored] = [json.loads(v) for k, (v, _) in redis.store.items() if ":answer:" in k]
        assert stored["member_ver"] == "0", "the versions were read after the compute"
        before = app_calls(fakes)
        read(user)
        assert app_calls(fakes) > before


# ── done-when 12 ────────────────────────────────────────────────────────────


class TestWhatIsNotStored:
    @pytest.mark.parametrize("fail", ["list_notifications", "my_completed_since"])
    def test_a_failed_source_or_handled_read_is_served_and_not_stored(
            self, fakes, redis, on, fail):  # noqa: F811
        fakes.fail[fail] = RuntimeError("away")
        user = member(*FULL)
        answer, resp = read(user)
        assert ("failed" in answer["sources"].values()) or answer["handled_ok"] is False
        assert answer_keys(redis) == []
        assert resp.headers["etag"]
        before = app_calls(fakes)
        read(user)
        assert app_calls(fakes) > before

    def test_a_failed_state_read_is_not_stored(self, fakes, redis, on, monkeypatch):  # noqa: F811
        async def broken(*a, **k):
            raise RuntimeError("pool away")

        monkeypatch.setattr(state, "_read_rows", broken)
        answer, _ = read(member(*FULL))
        assert answer["cards"]
        assert answer_keys(redis) == []

    def test_a_sound_answer_is_stored(self, fakes, redis, on):  # noqa: F811
        # Non-vacuity of the two cases above.
        answer, _ = read(member(*FULL))
        assert "failed" not in answer["sources"].values() and answer["handled_ok"]
        assert len(answer_keys(redis)) == 1
