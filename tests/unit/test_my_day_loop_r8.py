"""WS-58 MD-5b and MD-6a-2, R8: the sweeps and the departed checks of the
``my_day`` loop on a REAL Postgres (``my_day_feed.md`` §8.4 items 13, 14, 23
and 24, MD-5b done-when 5, MD-6a-2 done-whens 3 and 9).

A hermetic fake agrees with any DELETE it is handed, so this suite runs the
REAL ``my_day_loop.sweep`` on the phase-4-promoted catalog of
``test_h3_rls_promotion_rehearsal.py``. It connects as the NON-privileged
role with RLS forced (``_as_member``), as ``test_my_day_state_r8.py`` does.
The daily guard goes to a fake under the real ``TenantRedis``.

It shows:

* The sweep deletes old ``seen`` and ``later`` rows and an old time snooze
  that ended, and only in the bound organization.
* It keeps ``dismissed``, ``acted``, a snooze until a change, a time snooze
  that has not ended, and every row younger than 30 days.
* The discovery read lists both organizations as the app role, because
  ``organization`` is exempt from row level security.
* MD-6a-2: each of the three departed checks removes a member whose
  ``app_user`` row has ``status = 'removed'``, and ``member_context`` never
  runs for them. The status read is bound, and sees only its own org.
* MD-6a-2: the sweep deletes ``my_day_judgments`` rows older than 30 days,
  and only in the bound organization.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.access import mirror_identity_membership
from acb_common import tenant_redis
from acb_common.settings import get_settings
from acb_common.tenant_redis import KEY_ROOT, TenantRedis
from gateway import my_day_loop
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# The catalog, the app role and the binding, shared rather than copied.
# Fixtures are used by name, so the import is load-bearing.
from tests.unit.test_email_keep_separate import (  # noqa: F401
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)
from tests.unit.test_my_day_loop import _FakeRedis

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
M = f"sweep-{TAG}@md5b.test"

#: signal id -> (state, snoozed_until offset, snooze_on_change, age in days,
#: kept by the sweep). A negative offset is a snooze that ended.
ROWS: dict[str, tuple[str, str | None, bool, int, bool]] = {
    "tasks:seen-old": ("seen", None, False, 40, False),
    "tasks:later-old": ("later", None, False, 40, False),
    "tasks:snooze-ended-old": ("snoozed", "-10 days", False, 40, False),
    "tasks:snooze-open-old": ("snoozed", "5 days", False, 40, True),
    "tasks:on-change-old": ("snoozed", None, True, 40, True),
    "tasks:on-change-dated-old": ("snoozed", "-10 days", True, 40, True),
    "tasks:dismissed-old": ("dismissed", None, False, 40, True),
    "tasks:acted-old": ("acted", None, False, 40, True),
    "tasks:seen-young": ("seen", None, False, 5, True),
    "tasks:snooze-ended-young": ("snoozed", "-1 days", False, 5, True),
}


def _seed(p, org: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM my_day_card_state WHERE member_email = :m "
                       "AND organization_id = CAST(:o AS uuid)"), {"m": M, "o": org})
        for sid, (st, until, on_change, age, _kept) in ROWS.items():
            c.execute(text(
                "INSERT INTO my_day_card_state (organization_id, member_email, signal_id, "
                "kind, state, dismiss_reason, snoozed_until, snooze_on_change, updated_at) "
                "VALUES (CAST(:o AS uuid), :m, :s, 'overdue', :st, :reason, "
                "CASE WHEN CAST(:until AS text) IS NULL THEN NULL "
                "ELSE now() + CAST(:until AS interval) END, :chg, "
                "now() - make_interval(days => :age))"),
                {"o": org, "m": M, "s": sid, "st": st, "until": until, "chg": on_change,
                 "age": age, "reason": "dismiss" if st == "dismissed" else None})


def _ids(p, org: str) -> set[str]:
    with p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT signal_id FROM my_day_card_state WHERE member_email = :m "
            "AND organization_id = CAST(:o AS uuid)"), {"m": M, "o": org})
        return {r.signal_id for r in rows}


@pytest.fixture
def redis_fake(monkeypatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(fake))
    return fake


@pytest.fixture
def seeded(promoted):  # noqa: F811
    p = promoted
    _seed(p, p.org_a)
    _seed(p, p.org_b)
    yield p
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM my_day_card_state WHERE member_email = :m"), {"m": M})


async def _sweep(p, org: str, day: str) -> int | None:
    async with _as_member(p, org):
        return await my_day_loop.sweep(org, day)


def test_the_sweep_deletes_only_the_bound_orgs_rows_that_hide_nothing(
        seeded, redis_fake) -> None:
    p = seeded
    deleted = asyncio.run(_sweep(p, p.org_b, "2026-10-14"))
    kept = {sid for sid, row in ROWS.items() if row[4]}
    assert deleted == len(ROWS) - len(kept)
    assert _ids(p, p.org_b) == kept
    assert "tasks:dismissed-old" in _ids(p, p.org_b)
    # Org A was not bound, so not one of its rows moved.
    assert _ids(p, p.org_a) == set(ROWS)


def test_the_guard_runs_the_sweep_once_a_day(seeded, redis_fake) -> None:
    p = seeded
    assert asyncio.run(_sweep(p, p.org_b, "2026-10-14")) == 3
    _seed(p, p.org_b)
    assert asyncio.run(_sweep(p, p.org_b, "2026-10-14")) is None
    assert _ids(p, p.org_b) == set(ROWS)
    assert asyncio.run(_sweep(p, p.org_b, "2026-10-15")) == 3


def test_a_session_bound_to_org_a_cannot_sweep_org_b(seeded, redis_fake) -> None:
    """The sweep of A, run as the app role bound to A, leaves B whole."""
    p = seeded
    asyncio.run(_sweep(p, p.org_a, "2026-10-14"))
    assert _ids(p, p.org_b) == set(ROWS)
    assert "tasks:dismissed-old" in _ids(p, p.org_a)
    assert "tasks:seen-old" not in _ids(p, p.org_a)


def test_the_discovery_read_lists_both_orgs_as_the_app_role(seeded) -> None:
    p = seeded

    async def go() -> list[str]:
        async with _as_member(p, p.org_a):
            return await my_day_loop._organization_ids()

    got = asyncio.run(go())
    assert {p.org_a, p.org_b} <= set(got)


# ── MD-6a-2: the departed checks on a real app_user row (§8.4 item 24) ──────

#: 08:30 in UTC. No member has a judged key, so each pass reaches the check.
WHEN = datetime(2026, 10, 14, 8, 30, tzinfo=UTC)


def _seed_user(p, email: str, org: str, status: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', :s, CAST(:o AS uuid))"),
            {"e": email, "s": status, "o": org})


def _unseed(p, email: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "DELETE FROM org_membership m USING user_identity ui "
            "WHERE m.user_id = ui.id AND lower(ui.email) = lower(:e)"), {"e": email})
        c.execute(text("DELETE FROM user_identity WHERE lower(email) = lower(:e)"),
                  {"e": email})
        c.execute(text("DELETE FROM app_user WHERE lower(email) = lower(:e)"), {"e": email})


@pytest.fixture
def no_context(monkeypatch) -> list[str]:
    """``member_context`` must never run for a departed member (done-when 3).
    The cache is off, so each pass reaches the check at once."""
    import acb_auth.deps as deps

    called: list[str] = []

    async def _refuse(email: str) -> Any:
        called.append(email)
        raise AssertionError("member_context ran for a departed member")

    monkeypatch.setattr(deps, "member_context", _refuse)
    monkeypatch.setattr(get_settings(), "my_day_cache", False)
    return called


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


async def _check(dsn: str, org: str, member: str, *, shadow: tuple[str, str] | None = None
                 ) -> tuple[str | None, str | None, str]:
    """Seed the shadow membership if asked. Then answer the reason of
    ``departed``, the status row, and what the member pass did."""
    async with tenant_engine_scope(dsn):
        if shadow is not None:
            await mirror_identity_membership(email=member, org_id=shadow[0], status=shadow[1])
        reason = await my_day_loop.departed(org, member)
        status = await my_day_loop._member_status(org, member)
        did = await my_day_loop.member_pass(org, member, WHEN)
    return reason, status, did


def _in_set(fake: _FakeRedis, org: str, member: str) -> bool:
    return member in fake.zsets.get(f"{KEY_ROOT}:{org}:my_day:active", {})


def _activate(fake: _FakeRedis, org: str, member: str) -> None:
    fake.zsets.setdefault(f"{KEY_ROOT}:{org}:my_day:active", {})[member] = time.time()


class TestTheDepartedChecks:
    """Done-when 3, R8: each of the three checks of §8.4 item 24 removes the
    member from the active set, and ``member_context`` never runs. Each case
    seeds a real ``app_user`` row with ``status = 'removed'``, as
    ``members.remove_member`` leaves it."""

    def test_no_org_with_the_cutover_on(self, promoted, redis_fake, no_context,  # noqa: F811
                                        monkeypatch) -> None:
        p = promoted
        member = f"no-org-{TAG}@md6a2.test"
        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        _seed_user(p, member, p.org_a, "removed")
        _activate(redis_fake, p.org_a, member)
        try:
            got = asyncio.run(_check(_dsn(p.app_url), p.org_a, member,
                                     shadow=(p.org_a, "removed")))
        finally:
            _unseed(p, member)
        assert got == ("no_org", "removed", "departed")
        assert not _in_set(redis_fake, p.org_a, member)
        assert no_context == []

    def test_another_org_with_the_cutover_on(self, promoted, redis_fake, no_context,  # noqa: F811
                                             monkeypatch) -> None:
        p = promoted
        member = f"other-org-{TAG}@md6a2.test"
        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        _seed_user(p, member, p.org_a, "removed")
        _activate(redis_fake, p.org_a, member)
        try:
            got = asyncio.run(_check(_dsn(p.app_url), p.org_a, member,
                                     shadow=(p.org_b, "active")))
        finally:
            _unseed(p, member)
        assert got == ("other_org", "removed", "departed")
        assert not _in_set(redis_fake, p.org_a, member)
        assert no_context == []

    def test_the_status_with_the_cutover_off(self, promoted, redis_fake, no_context,  # noqa: F811
                                             monkeypatch) -> None:
        """The case the third check exists for: ``resolve_identity`` reads
        ``app_user`` with no status filter, so it gives the loop's own org.
        It runs as the database owner, as production connects today. As the
        app role, the unbound read sees no ``app_user`` row at all."""
        p = promoted
        member = f"status-off-{TAG}@md6a2.test"
        monkeypatch.delenv("IDENTITY_CUTOVER", raising=False)
        _seed_user(p, member, p.org_a, "removed")
        _activate(redis_fake, p.org_a, member)
        try:
            got = asyncio.run(_check(_dsn(p.admin_engine.url), p.org_a, member))
        finally:
            _unseed(p, member)
        assert got == ("status", "removed", "departed")
        assert not _in_set(redis_fake, p.org_a, member)
        assert no_context == []

    def test_the_status_with_a_stale_shadow_and_the_cutover_on(
            self, promoted, redis_fake, no_context, monkeypatch) -> None:  # noqa: F811
        """``org_membership.status`` can stay ``active`` after a removal. The
        status read, bound to the org as the app role, still finds it."""
        p = promoted
        member = f"status-on-{TAG}@md6a2.test"
        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        _seed_user(p, member, p.org_a, "removed")
        _activate(redis_fake, p.org_a, member)
        try:
            got = asyncio.run(_check(_dsn(p.app_url), p.org_a, member,
                                     shadow=(p.org_a, "active")))
        finally:
            _unseed(p, member)
        assert got == ("status", "removed", "departed")
        assert not _in_set(redis_fake, p.org_a, member)
        assert no_context == []

    def test_an_active_member_is_not_departed(self, promoted, redis_fake,  # noqa: F811
                                              monkeypatch) -> None:
        """The control: the bound status read sees an active row as the app
        role. So the cases above are not a row that RLS hid."""
        p = promoted
        member = f"active-{TAG}@md6a2.test"
        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        _seed_user(p, member, p.org_a, "active")

        async def go() -> tuple[str | None, str | None]:
            async with tenant_engine_scope(_dsn(p.app_url)):
                await mirror_identity_membership(email=member, org_id=p.org_a,
                                                 status="active")
                return (await my_day_loop.departed(p.org_a, member),
                        await my_day_loop._member_status(p.org_a, member))

        try:
            assert asyncio.run(go()) == (None, "active")
        finally:
            _unseed(p, member)

    def test_the_status_read_of_another_org_sees_no_row(self, promoted,  # noqa: F811
                                                        redis_fake) -> None:
        """Bound to org B as the app role, org A's row is not there."""
        p = promoted
        member = f"cross-{TAG}@md6a2.test"
        _seed_user(p, member, p.org_a, "active")

        async def go() -> str | None:
            async with tenant_engine_scope(_dsn(p.app_url)):
                return await my_day_loop._member_status(p.org_b, member)

        try:
            assert asyncio.run(go()) is None
        finally:
            _unseed(p, member)


# ── MD-6a-2: the judgments sweep (done-when 9, §13.2) ───────────────────────

JM = f"judged-{TAG}@md6a2.test"

#: signal id -> (age in days, kept by the sweep).
JROWS: dict[str, tuple[int, bool]] = {
    "tasks:old": (40, False),
    "tasks:older": (31, False),
    "tasks:young": (5, True),
    "tasks:edge-young": (29, True),
}


def _seed_judgments(p, org: str) -> None:
    with p.admin_engine.begin() as c:
        for sid, (age, _kept) in JROWS.items():
            c.execute(text(
                "INSERT INTO my_day_judgments (organization_id, member_email, signal_id, "
                "fingerprint, feature, mode, old_value, judged_value, used, created_at) "
                "VALUES (CAST(:o AS uuid), :m, :s, 'fp', 'my_day.triage', 'shadow', "
                "'now', 'later', false, now() - make_interval(days => :age))"),
                {"o": org, "m": JM, "s": sid, "age": age})


def _judgment_ids(p, org: str) -> set[str]:
    with p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT signal_id FROM my_day_judgments WHERE member_email = :m "
            "AND organization_id = CAST(:o AS uuid)"), {"m": JM, "o": org})
        return {r.signal_id for r in rows}


def test_the_sweep_deletes_old_judgments_of_the_bound_org_only(
        promoted, redis_fake) -> None:  # noqa: F811
    p = promoted
    _seed_judgments(p, p.org_a)
    _seed_judgments(p, p.org_b)
    try:
        deleted = asyncio.run(_sweep(p, p.org_b, "2026-10-20"))
        kept = {sid for sid, (_age, keep) in JROWS.items() if keep}
        assert deleted == len(JROWS) - len(kept)
        assert _judgment_ids(p, p.org_b) == kept
        # Org A was not bound, so not one of its rows moved.
        assert _judgment_ids(p, p.org_a) == set(JROWS)
    finally:
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM my_day_judgments WHERE member_email = :m"),
                      {"m": JM})


def test_the_org_predicate_holds_without_rls(promoted, redis_fake) -> None:  # noqa: F811
    """As the database owner, which production uses today, RLS does not
    filter. The statement's own org predicate keeps org A whole."""
    p = promoted
    _seed_judgments(p, p.org_a)
    _seed_judgments(p, p.org_b)

    async def go() -> int | None:
        async with tenant_engine_scope(_dsn(p.admin_engine.url)):
            return await my_day_loop.sweep(p.org_b, "2026-10-21")

    try:
        asyncio.run(go())
        kept = {sid for sid, (_age, keep) in JROWS.items() if keep}
        assert _judgment_ids(p, p.org_b) == kept
        assert _judgment_ids(p, p.org_a) == set(JROWS)
    finally:
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM my_day_judgments WHERE member_email = :m"),
                      {"m": JM})
