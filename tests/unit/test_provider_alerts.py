"""AO-2 — an AI vendor that runs out reaches the operator's admins.

Spec: ``project-docs/specs/customer_console.md`` §6A.17b. Metorite task 3683.
Code: ``gateway/provider_alerts.py``, the needs source ``providers`` of
``gateway/routes/shell/needs.py``, ``acb_auth.console_resolve.
provider_health_on_console``, and ``customer_console.provider_balance.
alert_signal`` with its door ``POST /registry/providers/health``.

Hermetic. The tenant Redis is a fake under the REAL ``TenantRedis`` wrapper,
so every key goes through the prefix of the org. The push sender, the member
read and the access read are fakes at their seams. No push leaves the test.

The done-when of the task, one class each:

* A fake judge moves Anthropic to ``out``: one bell row and one push for each
  admin of the operator org, and none for another org or a plain member.
* A second ``out`` within the hour sends nothing. A restart sends nothing for
  a state that did not change.
* ``ok`` after ``out`` sends one "serves again".
* With the flag empty, nothing is sent.
* No vendor text reaches a bell row, a push or a log line.

**Mutations (R7).**

* M1. In ``decide``, return ``signal`` for ``out`` even when it is the
  remembered state. ``TestTheRateLimitAndTheRestart`` goes red.
* M2. Drop the ``nx=True`` of the slot claim in ``run_pass``.
  ``test_out_then_ok_then_out_inside_the_hour_holds_the_second_out`` goes red.
* M3. Let ``alert_orgs`` keep ``*``. ``test_a_star_is_refused`` goes red.
* M4. Drop the ``admin=True`` of the ``providers`` source.
  ``test_a_plain_member_of_the_operator_org_gets_no_row`` goes red.
* M5. Pass ``reason`` through ``alert_rows`` or ``_alert_row``. The vendor
  text tests go red.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import access as access_mod
from acb_auth import console_resolve
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common import get_settings, tenant_redis
from acb_common.tenant_redis import TenantRedis
from gateway import provider_alerts as pa
from gateway.kb_access import resolve as kb_resolve
from gateway.push import flags as push_flags
from gateway.push import sender
from gateway.routes.shell import cache as shell_cache
from gateway.routes.shell import needs as shell
from gateway.routes.shell.intent import ADMIN_PERMISSION

OPERATOR = "0a0a0a0a-0000-4000-8000-000000000001"
CUSTOMER = "0b0b0b0b-0000-4000-8000-000000000002"
ANA = "ana@hathilabs.test"
BO = "bo@hathilabs.test"
MO = "mo@hathilabs.test"  # a plain member of the operator org
OLA = "ola@customer.test"  # an admin of a customer org

#: The members of each org, and who of them is an admin.
MEMBERS = {OPERATOR: [ANA, BO, MO], CUSTOMER: [OLA]}
ADMINS = {ANA, BO, OLA}

#: A sentence that only a vendor would write. It must reach nothing.
VENDOR_TEXT = "Your credit balance is too low to access the Anthropic API sk-ant-SECRET"

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)


def row(signal: str, provider: str = "anthropic", label: str = "Anthropic",
        days_left: str | None = None) -> dict[str, Any]:
    return {"provider": provider, "label": label, "signal": signal, "days_left": days_left}


# ── The fakes ───────────────────────────────────────────────────────────────


class FakeRedis:
    """``GET`` and ``SET`` with ``NX`` and ``EX``, on a clock the test moves."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float | None]] = {}
        self.clock = T0

    def _live(self, name: str) -> tuple[str, float | None] | None:
        found = self.store.get(name)
        if found is None:
            return None
        if found[1] is not None and found[1] <= self.clock.timestamp():
            self.store.pop(name, None)
            return None
        return found

    async def get(self, name: str) -> str | None:
        found = self._live(name)
        return found[0] if found else None

    async def set(self, name: str, value: str, *, nx: bool = False,
                  ex: int | None = None) -> bool | None:
        if nx and self._live(name) is not None:
            return None
        self.store[name] = (value, None if ex is None else self.clock.timestamp() + ex)
        return True


class Recorder:
    """Takes any log call, and keeps it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, level: str):
        def _log(*args: Any, **kwargs: Any) -> None:
            self.calls.append((level, args, kwargs))
        return _log

    def events(self) -> list[str]:
        return [args[0] for _, args, _ in self.calls if args]


@pytest.fixture
def redis(monkeypatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(fake))
    return fake


@pytest.fixture
def flag(monkeypatch):
    """``PROVIDER_ALERT_ORGS`` names the operator org, through its one reader's
    own source."""
    monkeypatch.setattr(get_settings(), "provider_alert_orgs", OPERATOR)


@pytest.fixture
def people(monkeypatch):
    """The member read and the access read, at their seams. Each session
    carries the org it was opened for, and the member read answers that org."""

    @asynccontextmanager
    async def session(org: str):
        yield SimpleNamespace(org=org)

    async def load_members(db: Any, org: str) -> frozenset[str]:
        assert db.org == org, "the member read ran on another org's session"
        return frozenset(MEMBERS.get(org, []))

    async def access_in_org(email: str, org: str, **_: Any) -> EffectiveAccess:
        if email not in MEMBERS.get(org, []):
            return EffectiveAccess(is_active=False)
        grants = {"feature:chat", ADMIN_PERMISSION} if email in ADMINS else {"feature:chat"}
        return EffectiveAccess(role_granted=frozenset(grants))

    monkeypatch.setattr(pa, "tenant_session", session)
    monkeypatch.setattr(kb_resolve, "load_members", load_members)
    monkeypatch.setattr(access_mod, "resolve_access_in_org", access_in_org)


@pytest.fixture
def pushes(monkeypatch) -> list[tuple[str, str, Any]]:
    """Each push the sender would send, as ``(org, member, message)``. No
    push leaves the test."""
    sent: list[tuple[str, str, Any]] = []

    async def send_to_member(org: str, member: str, message: Any, **_: Any):
        message.payload()  # the real check of the url
        sent.append((org, member, message))
        return sender.SendReport(devices=1, sent=1, removed=0, failed=0)

    monkeypatch.setattr(push_flags, "enabled", lambda: True)
    monkeypatch.setattr(push_flags, "configured", lambda: True)
    monkeypatch.setattr(sender, "send_to_member", send_to_member)
    return sent


@pytest.fixture
def bumps(monkeypatch) -> list[str]:
    seen: list[str] = []

    async def bump(org: Any) -> None:
        seen.append(str(org))

    monkeypatch.setattr(shell_cache, "bump_org_safe", bump)
    return seen


@pytest.fixture
def log(monkeypatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(pa, "_log", rec)
    return rec


@pytest.fixture
def world(redis, flag, people, pushes, bumps, log):
    return SimpleNamespace(redis=redis, pushes=pushes, bumps=bumps, log=log)


def judge(world: SimpleNamespace, *rows: dict[str, Any], at: datetime | None = None,
          org: str = OPERATOR) -> list[pa.Alert]:
    """One pass of the job over a fake judge's answer, at *at*."""
    now = at or world.redis.clock
    world.redis.clock = now
    return asyncio.run(pa.run_pass(org, list(rows), now))


def user(email: str, org: str) -> UserContext:
    grants = {"feature:chat", ADMIN_PERMISSION} if email in ADMINS else {"feature:chat"}
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=EffectiveAccess(role_granted=frozenset(grants)))


def bell(u: UserContext, at: datetime) -> list[dict[str, Any]] | None:
    """The rows the ``providers`` source gives *u*, or None when the source
    is absent for *u* (the gate, or the flag)."""
    provider = shell.REGISTRY["providers"]
    if not provider.held_by(u):
        return None
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(shell, "_now", lambda: at)
            found = asyncio.run(provider.run(u))
    except shell.SourceAbsent:
        return None
    return found.rows


# ── The rule ────────────────────────────────────────────────────────────────


class TestDecide:
    @pytest.mark.parametrize(("last", "signal", "fires"), [
        (None, "out", "out"),
        (None, "low", "low"),
        (None, "serving", None),  # nothing to recover from
        (None, "clear", None),
        (None, "other", None),
        ({"state": "ok"}, "out", "out"),
        ({"state": "out"}, "out", None),
        ({"state": "out"}, "low", "low"),
        ({"state": "low"}, "out", "out"),
        ({"state": "out"}, "serving", "ok"),
        ({"state": "low"}, "serving", "ok"),
        ({"state": "ok"}, "serving", None),
        # A refusal that aged out with no call proves nothing: hold the alert.
        ({"state": "out"}, "clear", None),
        ({"state": "out"}, "other", None),
    ])
    def test_the_table(self, last, signal, fires):
        assert pa.decide(last, signal) == fires

    def test_the_words(self):
        assert pa.words("out", "Anthropic") == (
            "Anthropic is out of credit.",
            "The chat now uses the next model in the chain. Top up the Anthropic account.")
        assert pa.words("low", "DeepSeek", "2.5") == (
            "DeepSeek credit is low: about 2.5 days left.", None)
        assert pa.words("low", "DeepSeek", "1") == ("DeepSeek credit is low: about 1 day left.", None)
        assert pa.words("low", "DeepSeek") == ("DeepSeek credit is low.", None)
        assert pa.words("ok", "Anthropic") == ("Anthropic serves again.", None)
        assert pa.words("ok", "Anthropic", was="out") == ("Anthropic serves again.", None)
        assert pa.words("ok", "DeepSeek", was="low") == ("DeepSeek has credit again.", None)


# ── Done-when 1: one bell row and one push for each admin ───────────────────


class TestOutReachesTheOperatorAdmins:
    def test_one_push_to_each_admin_and_none_to_anyone_else(self, world):
        fired = judge(world, row("out"))
        assert [(a.provider, a.state) for a in fired] == [("anthropic", "out")]
        assert sorted((org, who) for org, who, _ in world.pushes) == [(OPERATOR, ANA), (OPERATOR, BO)]
        message = world.pushes[0][2]
        assert message.title == "Anthropic is out of credit."
        assert message.body == ("The chat now uses the next model in the chain. "
                                "Top up the Anthropic account.")
        assert message.url == "/" and message.tag == "provider-anthropic"
        assert world.bumps == [OPERATOR]

    def test_one_bell_row_for_each_admin_of_the_operator_org(self, world):
        judge(world, row("out"))
        for who in (ANA, BO):
            rows = bell(user(who, OPERATOR), T0)
            assert rows is not None and len(rows) == 1
            (r,) = rows
            assert r["kind"] == "provider_alert" and r["app"] == "providers"
            assert r["id"] == "providers:anthropic"
            assert r["title"] == "Anthropic is out of credit."
            assert r["href"] == "https://operator.metorite.com/providers"
            assert r["act"] is None

    def test_a_plain_member_of_the_operator_org_gets_no_row(self, world):
        judge(world, row("out"))
        assert bell(user(MO, OPERATOR), T0) is None

    def test_an_admin_of_another_org_gets_no_row_and_no_push(self, world):
        judge(world, row("out"))
        assert bell(user(OLA, CUSTOMER), T0) is None
        assert all(org == OPERATOR for org, _, _ in world.pushes)

    def test_every_key_carries_the_operator_org(self, world):
        judge(world, row("out"))
        assert world.redis.store
        assert all(k.startswith(f"cc:{OPERATOR}:provider_alerts:") for k in world.redis.store)

    def test_low_names_the_days_left(self, world):
        judge(world, row("low", "deepseek", "DeepSeek", "2.5"))
        assert [m.title for _, _, m in world.pushes] == ["DeepSeek credit is low: about 2.5 days left."] * 2
        (r,) = bell(user(ANA, OPERATOR), T0)
        assert r["title"] == "DeepSeek credit is low: about 2.5 days left."

    def test_push_off_still_shows_the_bell(self, world, monkeypatch):
        monkeypatch.setattr(push_flags, "enabled", lambda: False)
        judge(world, row("out"))
        assert world.pushes == []
        assert len(bell(user(ANA, OPERATOR), T0)) == 1


# ── Done-when 2: the hour and the restart ───────────────────────────────────


class TestTheRateLimitAndTheRestart:
    def test_a_second_out_within_the_hour_sends_nothing(self, world):
        judge(world, row("out"))
        sent = len(world.pushes)
        assert judge(world, row("out"), at=T0 + timedelta(minutes=15)) == []
        assert judge(world, row("out"), at=T0 + timedelta(minutes=59)) == []
        assert len(world.pushes) == sent

    def test_an_unchanged_out_after_the_hour_sends_nothing_either(self, world):
        judge(world, row("out"))
        sent = len(world.pushes)
        assert judge(world, row("out"), at=T0 + timedelta(hours=5)) == []
        assert len(world.pushes) == sent

    def test_a_restart_sends_nothing_for_an_unchanged_state(self, world, monkeypatch):
        judge(world, row("out"))
        sent = len(world.pushes)
        # A new process: a new wrapper and a new pool over the same Redis. The
        # module keeps no state in memory, so this is all a restart changes.
        monkeypatch.setattr(tenant_redis, "get_tenant_redis",
                            lambda **_: TenantRedis(world.redis))
        assert judge(world, row("out"), at=T0 + timedelta(minutes=1)) == []
        assert len(world.pushes) == sent

    def test_out_then_ok_then_out_inside_the_hour_holds_the_second_out(self, world):
        judge(world, row("out"))
        judge(world, row("serving"), at=T0 + timedelta(minutes=15))
        sent = len(world.pushes)
        # Out again inside the hour of the first out: held, and the state
        # does not move, so the bell keeps "serves again" for now.
        assert judge(world, row("out"), at=T0 + timedelta(minutes=30)) == []
        assert len(world.pushes) == sent
        assert "held" in " ".join(world.log.events())
        # When the hour ends and the vendor is still out, the alert goes.
        fired = judge(world, row("out"), at=T0 + timedelta(minutes=61))
        assert [a.state for a in fired] == ["out"]
        assert len(world.pushes) == sent + 2

    def test_two_vendors_each_have_their_own_hour(self, world):
        fired = judge(world, row("out"), row("low", "deepseek", "DeepSeek", "1.5"))
        assert sorted(a.provider for a in fired) == ["anthropic", "deepseek"]
        assert len(world.pushes) == 4


# ── Done-when 3: serves again ───────────────────────────────────────────────


class TestServesAgain:
    def test_ok_after_out_sends_one_serves_again(self, world):
        judge(world, row("out"))
        before = len(world.pushes)
        fired = judge(world, row("serving"), at=T0 + timedelta(minutes=20))
        assert [a.state for a in fired] == ["ok"]
        assert [m.title for _, _, m in world.pushes[before:]] == ["Anthropic serves again."] * 2
        assert judge(world, row("serving"), at=T0 + timedelta(minutes=40)) == []
        assert len(world.pushes) == before + 2

    def test_the_bell_shows_serves_again_for_a_day(self, world):
        judge(world, row("out"))
        judge(world, row("serving"), at=T0 + timedelta(minutes=20))
        (r,) = bell(user(ANA, OPERATOR), T0 + timedelta(hours=2))
        assert r["title"] == "Anthropic serves again."
        assert bell(user(ANA, OPERATOR), T0 + timedelta(hours=25)) == []

    def test_a_vendor_that_was_never_out_sends_no_serves_again(self, world):
        assert judge(world, row("serving")) == []
        assert world.pushes == []

    def test_a_refusal_that_aged_out_keeps_the_alert(self, world):
        judge(world, row("out"))
        assert judge(world, row("clear"), at=T0 + timedelta(hours=2)) == []
        (r,) = bell(user(ANA, OPERATOR), T0 + timedelta(hours=2))
        assert r["title"] == "Anthropic is out of credit."

    def test_low_then_serving_says_credit_again(self, world):
        """AO-2 review: a vendor that was low never stopped serving, so its
        recovery does not say "serves again"."""
        judge(world, row("low", "deepseek", "DeepSeek", "2.5"))
        before = len(world.pushes)
        fired = judge(world, row("serving", "deepseek", "DeepSeek"),
                      at=T0 + timedelta(minutes=30))
        assert [(a.state, a.was) for a in fired] == [("ok", "low")]
        assert [m.title for _, _, m in world.pushes[before:]] == ["DeepSeek has credit again."] * 2
        (r,) = bell(user(ANA, OPERATOR), T0 + timedelta(hours=1))
        assert r["title"] == "DeepSeek has credit again."


# ── A vendor that leaves the Console answer (AO-2 review) ───────────────────


class TestAVendorThatLeavesTheAnswer:
    """A credential revoked while `out` or `low` drops the vendor from the
    answer. The map has no TTL, so without the drop its bell row stayed
    for ever. Mutation: skip the drop in `run_pass`, and these fail."""

    def test_its_bell_row_goes_and_nothing_is_sent(self, world):
        judge(world, row("out"), row("low", "deepseek", "DeepSeek", "2.5"))
        sent = len(world.pushes)
        bumps = len(world.bumps)
        assert judge(world, row("low", "deepseek", "DeepSeek", "2.5"),
                     at=T0 + timedelta(minutes=15)) == []
        assert len(world.pushes) == sent
        assert len(world.bumps) == bumps + 1  # the bell reads the map again
        rows = bell(user(ANA, OPERATOR), T0 + timedelta(minutes=15))
        assert [r["id"] for r in rows] == ["providers:deepseek"]
        assert "provider_alerts.dropped" in world.log.events()

    def test_an_empty_answer_drops_every_vendor(self, world):
        judge(world, row("out"))
        assert judge(world, at=T0 + timedelta(minutes=15)) == []
        assert bell(user(ANA, OPERATOR), T0 + timedelta(minutes=15)) == []
        assert json.loads(world.redis.store[f"cc:{OPERATOR}:provider_alerts:sent"][0]) == {}

    def test_the_drop_rides_along_with_a_new_alert(self, world):
        judge(world, row("out"))
        fired = judge(world, row("low", "deepseek", "DeepSeek", "1.5"),
                      at=T0 + timedelta(minutes=15))
        assert [a.provider for a in fired] == ["deepseek"]
        rows = bell(user(ANA, OPERATOR), T0 + timedelta(minutes=15))
        assert [r["id"] for r in rows] == ["providers:deepseek"]

    def test_a_vendor_that_comes_back_alerts_again_after_its_hour(self, world):
        judge(world, row("out"))
        judge(world, at=T0 + timedelta(minutes=15))
        assert judge(world, row("out"), at=T0 + timedelta(minutes=30)) == []  # its hour holds
        fired = judge(world, row("out"), at=T0 + timedelta(minutes=61))
        assert [a.state for a in fired] == ["out"]


# ── Done-when 4: the flag ───────────────────────────────────────────────────


class TestTheFlag:
    def test_empty_by_default(self):
        from acb_common.settings import Settings

        assert Settings.model_fields["provider_alert_orgs"].default == ""

    def test_empty_sends_nothing(self, redis, people, pushes, bumps, log, monkeypatch):
        monkeypatch.setattr(get_settings(), "provider_alert_orgs", "")
        enqueued: list[Any] = []
        called: list[bool] = []
        settled: list[str] = []

        async def enqueue(*args: Any, **kwargs: Any) -> None:
            enqueued.append(args)

        async def console() -> list[dict[str, Any]]:
            called.append(True)
            return [row("out")]

        async def settle(org: str, claim: Any, now: datetime) -> None:
            settled.append(org)

        monkeypatch.setattr(pa.queue, "enqueue", enqueue)
        monkeypatch.setattr(console_resolve, "provider_health_on_console", console)
        monkeypatch.setattr(pa, "_settle", settle)
        asyncio.run(pa.seed(OPERATOR, T0))
        asyncio.run(pa.check(OPERATOR, {}, SimpleNamespace()))
        assert enqueued == [] and called == [] and pushes == [] and redis.store == {}
        assert settled == [OPERATOR]  # the job still settles
        assert bell(user(ANA, OPERATOR), T0) is None

    def test_the_flag_seeds_one_job_for_the_bucket(self, people, monkeypatch, flag):
        enqueued: list[tuple[tuple, dict]] = []

        async def enqueue(*args: Any, **kwargs: Any) -> None:
            enqueued.append((args, kwargs))

        monkeypatch.setattr(pa.queue, "enqueue", enqueue)
        asyncio.run(pa.seed(OPERATOR, T0 + timedelta(minutes=7)))
        asyncio.run(pa.seed(CUSTOMER, T0))
        ((args, kwargs),) = enqueued
        assert args[0].org == OPERATOR and args[1] == pa.KIND
        assert kwargs["dedupe_key"] == "provider_alerts:2026-11-18T09:00"
        assert kwargs["max_attempts"] == 1  # a job that sends runs once

    def test_a_star_is_refused(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "provider_alert_orgs", "*")
        assert pa.alert_orgs() == frozenset()
        assert not pa.alert_allowed(CUSTOMER)

    def test_a_tenant_cannot_set_the_flag(self):
        """AO-2 review: the flag is a platform key, so the env guard refuses it
        from a tenant (layer B). Mutation: drop `PROVIDER_ALERT_` from
        `PLATFORM_ENV_PREFIXES`, and this fails."""
        from acb_common import env_guard

        assert env_guard.is_platform_env("PROVIDER_ALERT_ORGS")

    def test_a_change_applies_on_the_next_read(self, monkeypatch):
        """The reload rule: each call reads the settings again."""
        monkeypatch.setattr(get_settings(), "provider_alert_orgs", "")
        assert not pa.alert_allowed(OPERATOR)
        monkeypatch.setattr(get_settings(), "provider_alert_orgs", f" {OPERATOR.upper()} ,x")
        assert pa.alert_allowed(OPERATOR)

    def test_the_flag_reloads_on_sighup(self):
        from gateway import flag_reload

        assert flag_reload.reloadable("PROVIDER_ALERT_ORGS")

    def test_the_job_runs_the_console_read_then_the_pass(self, world, monkeypatch):
        settled: list[str] = []

        async def console() -> list[dict[str, Any]]:
            return [row("out")]

        async def settle(org: str, claim: Any, now: datetime) -> None:
            settled.append(org)

        monkeypatch.setattr(console_resolve, "provider_health_on_console", console)
        monkeypatch.setattr(pa, "_settle", settle)
        monkeypatch.setattr(pa, "_now", lambda: T0)
        asyncio.run(pa.check(OPERATOR, {}, SimpleNamespace()))
        assert len(world.pushes) == 2 and settled == [OPERATOR]

    def test_a_console_outage_sends_nothing_and_settles(self, world, monkeypatch):
        settled: list[str] = []

        async def console() -> list[dict[str, Any]]:
            raise console_resolve.ConsoleProviderHealthUnavailable("HTTP 403")

        async def settle(org: str, claim: Any, now: datetime) -> None:
            settled.append(org)

        monkeypatch.setattr(console_resolve, "provider_health_on_console", console)
        monkeypatch.setattr(pa, "_settle", settle)
        asyncio.run(pa.check(OPERATOR, {}, SimpleNamespace()))
        assert world.pushes == [] and settled == [OPERATOR]
        assert "provider_alerts.console_unavailable" in world.log.events()


# ── Done-when 5: no vendor text ─────────────────────────────────────────────


class TestNoVendorText:
    def test_the_client_keeps_four_keys_in_a_closed_shape(self, monkeypatch):
        async def door(endpoint: str, payload: dict, *, unavailable: type) -> tuple[int, dict]:
            assert endpoint == "/registry/providers/health" and payload == {}
            return 200, {"providers": [
                {"provider": "anthropic", "label": "Anthropic", "signal": "out",
                 "days_left": None, "reason": VENDOR_TEXT, "probe_error": VENDOR_TEXT},
                {"provider": "openai", "label": VENDOR_TEXT, "signal": "out", "days_left": None},
                {"provider": "deepseek", "label": "DeepSeek", "signal": VENDOR_TEXT},
                {"provider": VENDOR_TEXT, "label": "X", "signal": "out"},
                {"provider": "aimlapi", "label": "AI/ML API", "signal": "low",
                 "days_left": VENDOR_TEXT},
            ]}

        monkeypatch.setattr(console_resolve, "is_wired", lambda: True)
        monkeypatch.setattr(console_resolve, "_post_deployment_door", door)
        rows = asyncio.run(console_resolve.provider_health_on_console())
        assert VENDOR_TEXT not in json.dumps(rows)
        assert [r["provider"] for r in rows] == ["anthropic", "openai", "aimlapi"]
        assert all(set(r) == {"provider", "label", "signal", "days_left"} for r in rows)
        assert rows[1]["label"] == "openai"  # a label that is not a name reads as the id
        assert rows[2]["days_left"] is None

    def test_the_client_refuses_a_status_that_is_not_200(self, monkeypatch):
        async def door(endpoint: str, payload: dict, *, unavailable: type) -> tuple[int, dict]:
            return 403, {"detail": "deployment key lacks the 'serve' capability"}

        monkeypatch.setattr(console_resolve, "is_wired", lambda: True)
        monkeypatch.setattr(console_resolve, "_post_deployment_door", door)
        with pytest.raises(console_resolve.ConsoleProviderHealthUnavailable, match="HTTP 403"):
            asyncio.run(console_resolve.provider_health_on_console())

    def test_an_unwired_box_makes_no_call(self, monkeypatch):
        monkeypatch.setattr(console_resolve, "is_wired", lambda: False)
        with pytest.raises(console_resolve.ConsoleProviderHealthUnavailable, match="unwired"):
            asyncio.run(console_resolve.provider_health_on_console())

    def test_nothing_a_vendor_wrote_reaches_a_bell_a_push_or_a_log(self, world, monkeypatch):
        async def door(endpoint: str, payload: dict, *, unavailable: type) -> tuple[int, dict]:
            return 200, {"providers": [
                {"provider": "anthropic", "label": "Anthropic", "signal": "out",
                 "days_left": None, "reason": VENDOR_TEXT},
            ]}

        async def settle(org: str, claim: Any, now: datetime) -> None:
            return None

        monkeypatch.setattr(console_resolve, "is_wired", lambda: True)
        monkeypatch.setattr(console_resolve, "_post_deployment_door", door)
        monkeypatch.setattr(pa, "_settle", settle)
        monkeypatch.setattr(pa, "_now", lambda: T0)
        asyncio.run(pa.check(OPERATOR, {}, SimpleNamespace()))
        assert len(world.pushes) == 2
        rows = bell(user(ANA, OPERATOR), T0)
        seen = json.dumps({
            "bell": rows,
            "push": [m.payload() for _, _, m in world.pushes],
            "log": repr(world.log.calls),
            "redis": world.redis.store,
        })
        assert "Anthropic is out of credit." in seen
        assert VENDOR_TEXT not in seen
        assert "credit balance is too low" not in seen

    def test_the_console_row_has_no_reason_and_no_probe_error(self):
        from customer_console import provider_balance as pb

        report = [{
            "provider": "anthropic", "status": "out", "reason": VENDOR_TEXT,
            "probe_error": VENDOR_TEXT, "balance": "0", "days_left": None,
            "last_success_at": None, "last_refusal_at": T0.isoformat(),
        }]
        (out,) = pb.alert_rows(report)
        assert out == {"provider": "anthropic", "label": "Anthropic", "signal": "out",
                       "days_left": None}


# ── The Console's word ──────────────────────────────────────────────────────


class TestTheConsoleSignal:
    @pytest.mark.parametrize(("status", "served", "refused", "signal"), [
        ("out", None, T0, "out"),
        ("low", None, None, "low"),
        ("ok", None, None, "serving"),
        # A vendor with no probe never reads `ok`: a served call after the
        # last refusal is its evidence.
        ("unknown", T0, T0 - timedelta(minutes=5), "serving"),
        ("unknown", T0 - timedelta(minutes=5), T0, "clear"),
        ("unknown", None, None, "clear"),
        ("unknown", T0, None, "serving"),
        ("refusing", T0, None, "other"),
        ("rate_limited", None, None, "other"),
        ("probe_failed", None, None, "other"),
    ])
    def test_the_signal(self, status, served, refused, signal):
        from customer_console import provider_balance as pb

        report_row = {"status": status,
                      "last_success_at": served.isoformat() if served else None,
                      "last_refusal_at": refused.isoformat() if refused else None}
        assert pb.alert_signal(report_row) == signal
        assert signal in pb.ALERT_SIGNALS
        # The box reads the same closed set of words that the Console sends.
        assert set(pb.ALERT_SIGNALS) == console_resolve.PROVIDER_ALERT_SIGNALS

    def test_an_anthropic_402_then_a_served_call_reads_out_then_serving(self):
        """The rule of AO-1 and this signal, end to end on the pure rule."""
        from customer_console import provider_balance as pb

        h = pb.HealthInput(provider="anthropic", last_refusal_by_status={402: T0})
        verdict = pb.assess(h, now=T0 + timedelta(minutes=1))
        assert pb.alert_signal({"status": verdict.status}) == "out"
        served = pb.HealthInput(provider="anthropic", last_refusal_by_status={402: T0},
                                last_success_at=T0 + timedelta(minutes=2))
        verdict = pb.assess(served, now=T0 + timedelta(minutes=3))
        assert verdict.status == "unknown"
        assert pb.alert_signal({"status": verdict.status,
                                "last_success_at": (T0 + timedelta(minutes=2)).isoformat(),
                                "last_refusal_at": T0.isoformat()}) == "serving"

    def test_the_door_answers_the_box_and_refuses_the_operator(self, monkeypatch):
        pytest.importorskip("fastapi")
        from contextlib import contextmanager

        from customer_console import auth as cc_auth
        from customer_console import main as cc_main
        from fastapi.testclient import TestClient

        @contextmanager
        def begin():
            yield object()

        report = [{"provider": "anthropic", "status": "out", "reason": VENDOR_TEXT,
                   "probe_error": VENDOR_TEXT, "days_left": None,
                   "last_success_at": None, "last_refusal_at": T0.isoformat()},
                  {"provider": "deepseek", "status": "low", "reason": "x",
                   "days_left": "2.5", "last_success_at": None, "last_refusal_at": None}]
        monkeypatch.setattr(cc_main, "get_engine", lambda: SimpleNamespace(begin=begin))
        monkeypatch.setattr(cc_main.provider_balance, "health_report",
                            lambda conn, **_: report)
        box = cc_auth.DeploymentCaller(deployment_id="d-1", key_prefix="cc_depl_x",
                                       capabilities=frozenset({"serve"}))
        cc_main.app.dependency_overrides[cc_auth._serve_read_dependency] = lambda: box
        try:
            client = TestClient(cc_main.app)
            r = client.post("/registry/providers/health", json={})
            assert r.status_code == 200
            assert r.json() == {"providers": [
                {"provider": "anthropic", "label": "Anthropic", "signal": "out", "days_left": None},
                {"provider": "deepseek", "label": "DeepSeek", "signal": "low", "days_left": "2.5"},
            ]}
            assert VENDOR_TEXT not in r.text
            cc_main.app.dependency_overrides[cc_auth._serve_read_dependency] = lambda: None
            assert client.post("/registry/providers/health", json={}).status_code == 400
        finally:
            cc_main.app.dependency_overrides.pop(cc_auth._serve_read_dependency, None)


# ── The recipients ──────────────────────────────────────────────────────────


class TestTheRecipients:
    def test_the_admins_of_the_org_in_address_order(self, people):
        assert asyncio.run(pa.recipients(OPERATOR)) == [ANA, BO]
        assert asyncio.run(pa.recipients(CUSTOMER)) == [OLA]

    def test_a_member_with_no_access_is_left_out(self, people, monkeypatch):
        async def refused(email: str, org: str, **_: Any) -> EffectiveAccess:
            return EffectiveAccess(is_active=False)

        monkeypatch.setattr(access_mod, "resolve_access_in_org", refused)
        assert asyncio.run(pa.recipients(OPERATOR)) == []

    def test_the_access_read_is_strict(self, people, monkeypatch):
        asked: list[bool] = []

        async def strict(email: str, org: str, *, strict: bool = False) -> EffectiveAccess:
            asked.append(strict)
            return EffectiveAccess(is_active=False)

        monkeypatch.setattr(access_mod, "resolve_access_in_org", strict)
        asyncio.run(pa.recipients(OPERATOR))
        assert asked and all(asked)

    def test_a_read_fault_moves_no_state_and_the_next_pass_sends(self, world, monkeypatch):
        """A fault must never read as "no admin": the state would move and the
        alert would reach nobody for the whole outage."""
        real = access_mod.resolve_access_in_org

        async def fault(email: str, org: str, **_: Any) -> EffectiveAccess:
            raise access_mod.IdentityUnavailable("access_in_org")

        monkeypatch.setattr(access_mod, "resolve_access_in_org", fault)
        with pytest.raises(access_mod.IdentityUnavailable):
            judge(world, row("out"))
        assert world.pushes == [] and world.redis.store == {}
        monkeypatch.setattr(access_mod, "resolve_access_in_org", real)
        fired = judge(world, row("out"), at=T0 + timedelta(minutes=15))
        assert [a.state for a in fired] == ["out"] and len(world.pushes) == 2

    def test_the_job_logs_a_read_fault_and_settles(self, world, monkeypatch):
        settled: list[str] = []

        async def fault(email: str, org: str, **_: Any) -> EffectiveAccess:
            raise access_mod.IdentityUnavailable("access_in_org")

        async def console() -> list[dict[str, Any]]:
            return [row("out")]

        async def settle(org: str, claim: Any, now: datetime) -> None:
            settled.append(org)

        monkeypatch.setattr(access_mod, "resolve_access_in_org", fault)
        monkeypatch.setattr(console_resolve, "provider_health_on_console", console)
        monkeypatch.setattr(pa, "_settle", settle)
        asyncio.run(pa.check(OPERATOR, {}, SimpleNamespace()))
        assert settled == [OPERATOR] and world.pushes == []
        assert "provider_alerts.pass_failed" in world.log.events()

    def test_the_job_is_detached_and_states_its_effect(self):
        from gateway.jobs import worker

        assert worker.effects()[pa.KIND] == ("send", pa.POLICY)
        assert pa.seed in worker.seed_hooks


def test_decimal_days_left_is_a_string_on_the_wire():
    """The Console sends money and days as strings (``_money``)."""
    from customer_console import provider_balance as pb

    assert pb._money(Decimal("2.50")) == "2.5"
