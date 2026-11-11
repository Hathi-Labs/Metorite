"""WS-PKG PKG-10a — the lifetime of an install, on a real database (R8).

Spec: ``project-docs/specs/package_host.md`` §6.2 and the PKG-10a row of
§10. Code: ``keep_install``, ``restore_install``, ``expire_due`` and
``notice_times`` of ``gateway/package_host/store.py``. The hermetic fence of
the one writer is ``tests/unit/test_package_host_store.py``.

Each call runs as the app role, through ``tenant_session``, so FORCE RLS
binds each statement. Each check of rows reads as the superuser, which RLS
does not bind. Org A has six Studio packages with version 1 and two members.
Org B has one Studio package and one member. The world, the helpers and the
lock wait are those of ``test_package_disposal_r8.py``.

Run (one R8 suite at a time, because the ``promoted`` fixture drops
``<db>_h3rls``)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_package_lifetime_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-10a).**

a. After ``expire_due(now)``, an active install whose ``ends_at`` is at or
   before ``now`` is archived with ``archived_at = now``, and keeps each
   claim row. A later or NULL ``ends_at`` does not change, and a paused
   install does not change. ``resolve_for`` shows no claim of it.
b. A restore at ``archived_at`` plus 29 days gives ``active``,
   ``archived_at`` NULL and ``ends_at`` = now plus 30 days. It keeps the id,
   the version, the scope and the claim rows, and ``resolve_for`` shows the
   claims again.
c. At ``archived_at`` plus 30 days, a restore raises ``StateRefused``, and
   ``expire_due`` deletes the install and its claims.
d. After ``keep_install``, ``expire_due`` leaves the install. A keep of an
   archived install, and a restore of an install that is not archived,
   raise ``StateRefused`` and write nothing.
e. A restore of an install whose app is archived raises
   ``AppNotInstallable``.
f. Org B cannot keep, restore or expire an install of org A. An unbound
   session raises ``TenantUnbound``. A time with no zone is refused.
g. Two ``expire_due`` calls at once archive the install one time.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import TenantUnbound, get_session_factory, tenant_session
from gateway.package_host import store
from gateway.package_host.resolve import resolve_for
from gateway.package_host.store import (
    AppNotInstallable,
    Contribution,
    InstallError,
    InstallNotFound,
    StateRefused,
)
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@pkg10.test"
BEN = f"ben.{TAG}@pkg10.test"
ZED = f"zed.{TAG}@pkg10.test"
_SLUG_LIKE = f"pkg10-{TAG}-%"
_EMAIL_LIKE = f"%.{TAG}@pkg10.test"
_A_APPS = ("a1", "a2", "a3", "a4", "a5", "a6")
DAY = timedelta(days=1)


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _k(name: str) -> str:
    """A claim key that no system package holds."""
    return f"{name}-{TAG}"


def _now() -> datetime:
    """The real time. ``resolve_for`` compares ``ends_at`` with ``now()``."""
    return datetime.now(UTC)


@pytest.fixture
def world(promoted):  # noqa: F811
    """Org A: six Studio packages, each with version 1, and two members.
    Org B: one Studio package and one member. Teardown deletes the apps, and
    the installs and the claims go by cascade."""
    p = promoted
    apps: dict[str, str] = {}

    def app(c: Any, name: str, org: str) -> None:
        man = json.dumps({"studio": 1, "id": f"r-{name}-{TAG}", "name": "P", "purpose": "T"})
        apps[name] = str(
            c.execute(
                text(
                    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
                    "format, status, manifest) VALUES (:s, 'pkg10', :o, '', :org, 'manifest', "
                    "'live', CAST(:m AS jsonb)) RETURNING id"
                ),
                {"s": f"pkg10-{TAG}-{name}", "o": ANA, "org": org, "m": man},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO app_versions (app_id, version, manifest, bundle_html, "
                "bundle_sha256, published_by, organization_id) VALUES "
                "(CAST(:i AS uuid), 1, CAST(:m AS jsonb), '', 'sha', :by, CAST(:o AS uuid))"
            ),
            {"i": apps[name], "m": man, "by": ANA, "o": org},
        )

    with p.admin_engine.begin() as c:
        for name in _A_APPS:
            app(c, name, p.org_a)
        app(c, "b1", p.org_b)
        for email, org in ((ANA, p.org_a), (BEN, p.org_a), (ZED, p.org_b)):
            c.execute(
                text(
                    "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                    "VALUES (:e, :e, 'employee', 'active', :org)"
                ),
                {"e": email, "org": org},
            )
    yield SimpleNamespace(p=p, apps=apps)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE slug LIKE :s"), {"s": _SLUG_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


# ── Helpers ────────────────────────────────────────────────────────────────


def _org(world: Any, tag: str) -> str:
    return world.p.org_a if tag == "a" else world.p.org_b


def _go(world: Any, fn: Any) -> Any:
    """Run ``fn()`` in a new event loop, on the engine of the app role."""

    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()

    return asyncio.run(go())


def _claim(key: str, point: str = "jobs") -> Contribution:
    return Contribution(point=point, key=key)


def _install(
    world: Any,
    app: str,
    scope: str,
    claims: list[Contribution],
    *,
    ends_at: datetime | None = None,
    org: str = "a",
    by: str = ANA,
) -> Any:
    """One install through the store, as the app role. Answers the install
    or the refusal."""
    if scope == "org":
        kind, installer = "org", by
    elif scope.startswith("group:"):
        kind, installer = "group", by
    else:
        kind, installer = "private", scope

    async def go() -> Any:
        try:
            async with tenant_session(_org(world, org)) as db:
                return await store.install(
                    db,
                    app_id=world.apps[app],
                    version=1,
                    scope_subject=scope,
                    scope_kind=kind,
                    installed_by=installer,
                    ends_at=ends_at,
                    contributions=claims,
                )
        except InstallError as exc:
            return exc

    got = _go(world, go)
    return got


def _call(world: Any, fn: Any, *, org: str = "a", **kw: Any) -> Any:
    """One lifetime call as the app role, bound to *org*. Answers the result
    or the refusal. A refusal rolls the transaction back."""

    async def go() -> Any:
        try:
            async with tenant_session(_org(world, org)) as db:
                return await fn(db, **kw)
        except InstallError as exc:
            return exc

    return _go(world, go)


def _expire(world: Any, now: datetime, *, org: str = "a") -> Any:
    return _call(world, store.expire_due, org=org, now=now)


def _admin(world: Any, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    """Rows read as the superuser, which RLS does not bind."""
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params or {}).all())


def _admin_write(world: Any, sql: str, params: dict[str, Any] | None = None) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text(sql), params or {})


def _row(world: Any, install_id: str) -> tuple[Any, ...] | None:
    """The whole lifetime of one install, with its claim count."""
    rows = _admin(
        world,
        (
            "SELECT i.state, i.ends_at, i.archived_at, i.updated_at, i.version, "
            "i.scope_kind, i.scope_subject, "
            "(SELECT count(*) FROM package_contribution c WHERE c.install_id = i.id) "
            "FROM package_install i WHERE i.id = CAST(:i AS uuid)"
        ),
        {"i": install_id},
    )
    return tuple(rows[0]) if rows else None


def _claim_rows(world: Any, install_id: str) -> list[tuple[Any, ...]]:
    """Each claim row of one install, in a fixed order. The table has no id
    column, so the physical row id (``ctid``) and the writer transaction
    (``xmin``) stand for it. A claim row that was deleted and written again
    shows a new pair."""
    return [
        tuple(r)
        for r in _admin(
            world,
            "SELECT CAST(ctid AS text), CAST(xmin AS text), point, key, payload, position "
            "FROM package_contribution WHERE install_id = CAST(:i AS uuid) ORDER BY point, key",
            {"i": install_id},
        )
    ]


def _resolve(world: Any, who: str, *, org: str = "a") -> dict[str, Any]:
    """The answer of ``resolve_for`` for *who*, who may use each feature."""

    async def go() -> dict[str, Any]:
        async with tenant_session(_org(world, org)) as db:
            return await resolve_for(db, viewer=who, can_use=lambda _slug: True)

    return _go(world, go)


def _install_ids(body: dict[str, Any]) -> set[str]:
    return {
        e["install_id"]
        for entries in body["contributions"].values()
        for e in entries
        if e["install_id"] is not None
    }


def _archive_at(world: Any, install_id: str, at: datetime) -> None:
    """Archive one install at *at*, through ``expire_due``. The end date
    goes to one day before *at* first, as the superuser."""
    _admin_write(
        world,
        "UPDATE package_install SET ends_at = :e WHERE id = CAST(:i AS uuid)",
        {"e": at - DAY, "i": install_id},
    )
    got = _expire(world, at)
    assert isinstance(got, dict) and got["archived"] >= 1, got
    assert _row(world, install_id)[2] == at


# ── a. expire_due archives the due installs only ───────────────────────────


def test_r8_a_expire_archives_a_due_install_and_keeps_its_claims(world) -> None:
    now = _now()
    due = _install(
        world, "a1", "org", [_claim(_k("a1")), _claim(_k("/a1"), point="nav")], ends_at=now - DAY
    )
    at_now = _install(world, "a2", ANA, [_claim(_k("a2"))], ends_at=now)
    later = _install(world, "a3", "org", [_claim(_k("a3"))], ends_at=now + DAY)
    never = _install(world, "a4", "org", [_claim(_k("a4"))])
    paused = _install(world, "a5", "org", [_claim(_k("a5"))], ends_at=now - DAY)
    assert _call(world, store.pause_install, install_id=paused.id) is True
    claims = {i.id: _claim_rows(world, i.id) for i in (due, at_now)}
    untouched = {i.id: _row(world, i.id) for i in (later, never, paused)}

    assert _expire(world, now) == {"archived": 2, "deleted": 0}
    for got in (due, at_now):
        row = _row(world, got.id)
        assert (row[0], row[2]) == ("archived", now), got.id
        assert _claim_rows(world, got.id) == claims[got.id]
    # A later or NULL end date, and a paused install past its end, stay.
    assert {i: _row(world, i) for i in untouched} == untouched
    for who in (ANA, BEN):
        seen = _install_ids(_resolve(world, who))
        assert due.id not in seen and at_now.id not in seen, who
        assert later.id in seen and never.id in seen, who
    # A second run finds nothing to do.
    assert _expire(world, now) == {"archived": 0, "deleted": 0}


def test_r8_a_paused_install_past_its_end_is_never_archived_or_deleted(world) -> None:
    """PKG-Q8: the kill switch of J3 owns a paused install."""
    now = _now()
    paused = _install(world, "a1", "org", [_claim(_k("p1"))], ends_at=now - 90 * DAY)
    assert _call(world, store.pause_install, install_id=paused.id) is True
    before = _row(world, paused.id)
    for at in (now, now + 31 * DAY, now + 120 * DAY):
        assert _expire(world, at) == {"archived": 0, "deleted": 0}
    assert _row(world, paused.id) == before


# ── b. A restore inside the 30 days ────────────────────────────────────────


def test_r8_b_a_restore_at_29_days_brings_back_the_same_install(world) -> None:
    now = _now()
    got = _install(
        world, "a1", "org", [_claim(_k("b1")), _claim(_k("/b1"), point="nav")], ends_at=now
    )
    claims = _claim_rows(world, got.id)
    _archive_at(world, got.id, now - 29 * DAY)
    assert got.id not in _install_ids(_resolve(world, ANA))

    ends = _call(world, store.restore_install, install_id=got.id, now=now)
    assert ends == now + timedelta(days=30)
    row = _row(world, got.id)
    assert row[0] == "active" and row[2] is None and row[1] == now + timedelta(days=30)
    assert (row[4], row[5], row[6]) == (1, "org", "org")
    assert _claim_rows(world, got.id) == claims
    for who in (ANA, BEN):
        assert got.id in _install_ids(_resolve(world, who)), who


def test_r8_b_a_restore_keeps_the_same_claim_rows_of_a_private_install(world) -> None:
    now = _now()
    got = _install(world, "a2", ANA, [_claim(_k("b2")), _claim(_k("b2"), point="cards")],
                   ends_at=now)
    claims = _claim_rows(world, got.id)
    assert len(claims) == 2
    _archive_at(world, got.id, now - DAY)
    assert isinstance(_call(world, store.restore_install, install_id=got.id, now=now), datetime)
    assert _claim_rows(world, got.id) == claims
    row = _row(world, got.id)
    assert (row[0], row[4], row[5], row[6]) == ("active", 1, "private", ANA)
    assert got.id in _install_ids(_resolve(world, ANA))
    assert got.id not in _install_ids(_resolve(world, BEN))


# ── c. At 30 days the restore closes and the job deletes ───────────────────


def test_r8_c_at_30_days_a_restore_is_refused_and_expire_deletes(world) -> None:
    now = _now()
    got = _install(
        world, "a1", "org", [_claim(_k("c1")), _claim(_k("/c1"), point="nav")], ends_at=now
    )
    kept = _install(world, "a2", "org", [_claim(_k("c2"))], ends_at=now)
    _archive_at(world, got.id, now - 30 * DAY)
    _admin_write(
        world,
        "UPDATE package_install SET state = 'archived', archived_at = :a "
        "WHERE id = CAST(:i AS uuid)",
        {"a": now - 29 * DAY, "i": kept.id},
    )
    before = _row(world, got.id)
    refused = _call(world, store.restore_install, install_id=got.id, now=now)
    assert isinstance(refused, StateRefused), refused
    assert (refused.code, refused.status) == ("restore_closed", 409)
    assert _row(world, got.id) == before

    assert _expire(world, now) == {"archived": 0, "deleted": 1}
    assert _row(world, got.id) is None and _claim_rows(world, got.id) == []
    # An install archived 29 days before stays, with its claims.
    assert _row(world, kept.id)[0] == "archived" and len(_claim_rows(world, kept.id)) == 1


def test_r8_c_one_call_never_deletes_an_install_that_it_archived(world) -> None:
    """Step 1 stamps ``archived_at`` = now, so step 2 of the same call cannot
    reach it, even for an end date long past."""
    now = _now()
    got = _install(world, "a1", "org", [_claim(_k("c3"))], ends_at=now - 400 * DAY)
    assert _expire(world, now) == {"archived": 1, "deleted": 0}
    assert _row(world, got.id)[0] == "archived" and len(_claim_rows(world, got.id)) == 1
    assert _expire(world, now + 30 * DAY) == {"archived": 0, "deleted": 1}
    assert _row(world, got.id) is None


# ── d. Keep, and the refusals that write nothing ───────────────────────────


def test_r8_d_after_a_keep_expire_leaves_the_install(world) -> None:
    now = _now()
    got = _install(world, "a1", ANA, [_claim(_k("d1"))], ends_at=now + DAY)
    paused = _install(world, "a2", "org", [_claim(_k("d2"))], ends_at=now + DAY)
    assert _call(world, store.pause_install, install_id=paused.id) is True
    first = _row(world, got.id)
    assert _call(world, store.keep_install, install_id=got.id) is True
    row = _row(world, got.id)
    assert row[1] is None and row[3] > first[3] and row[0] == "active"
    # A paused install can be kept too, and stays paused.
    assert _call(world, store.keep_install, install_id=paused.id) is True
    assert _row(world, paused.id)[:2] == ("paused", None)
    # A second keep changes nothing.
    assert _call(world, store.keep_install, install_id=got.id) is False
    assert _row(world, got.id) == row
    assert _expire(world, now + 365 * DAY) == {"archived": 0, "deleted": 0}
    assert _row(world, got.id) == row


def test_r8_d_a_keep_of_an_archived_install_writes_nothing(world) -> None:
    now = _now()
    got = _install(world, "a1", "org", [_claim(_k("d3"))], ends_at=now)
    _archive_at(world, got.id, now)
    before = _row(world, got.id)
    refused = _call(world, store.keep_install, install_id=got.id)
    assert isinstance(refused, StateRefused), refused
    assert (refused.code, refused.status) == ("state_refused", 409)
    assert _row(world, got.id) == before


def test_r8_d_a_restore_of_an_install_that_is_not_archived_writes_nothing(world) -> None:
    now = _now()
    active = _install(world, "a1", "org", [_claim(_k("d4"))], ends_at=now + DAY)
    paused = _install(world, "a2", "org", [_claim(_k("d5"))], ends_at=now + DAY)
    assert _call(world, store.pause_install, install_id=paused.id) is True
    for got in (active, paused):
        before = _row(world, got.id)
        refused = _call(world, store.restore_install, install_id=got.id, now=now)
        assert isinstance(refused, StateRefused), refused
        assert (refused.code, refused.status) == ("state_refused", 409)
        assert _row(world, got.id) == before


# ── e. The app is archived ─────────────────────────────────────────────────


def test_r8_e_a_restore_of_an_install_of_an_archived_app_is_refused(world) -> None:
    now = _now()
    got = _install(world, "a1", "org", [_claim(_k("e1"))], ends_at=now)
    _archive_at(world, got.id, now - DAY)
    _admin_write(
        world,
        "UPDATE apps SET status = 'archived' WHERE id = CAST(:a AS uuid)",
        {"a": world.apps["a1"]},
    )
    before = _row(world, got.id)
    refused = _call(world, store.restore_install, install_id=got.id, now=now)
    assert isinstance(refused, AppNotInstallable), refused
    assert (refused.code, refused.status) == ("app_not_installable", 404)
    assert _row(world, got.id) == before


# ── f. Tenancy and the time ────────────────────────────────────────────────


def test_r8_f_org_b_cannot_keep_restore_or_expire_an_install_of_org_a(world) -> None:
    now = _now()
    live = _install(world, "a1", "org", [_claim(_k("f1"))], ends_at=now + DAY)
    gone = _install(world, "a2", "org", [_claim(_k("f2"))], ends_at=now)
    _archive_at(world, gone.id, now - 29 * DAY)
    due = _install(world, "a3", "org", [_claim(_k("f3"))], ends_at=now - DAY)
    spent = _install(world, "a4", "org", [_claim(_k("f4"))], ends_at=now)
    _admin_write(
        world,
        "UPDATE package_install SET state = 'archived', archived_at = :a "
        "WHERE id = CAST(:i AS uuid)",
        {"a": now - 60 * DAY, "i": spent.id},
    )
    ids = (live.id, gone.id, due.id, spent.id)
    before = {i: _row(world, i) for i in ids}
    for fn, kw in (
        (store.keep_install, {"install_id": live.id}),
        (store.restore_install, {"install_id": gone.id, "now": now}),
    ):
        got = _call(world, fn, org="b", **kw)
        assert isinstance(got, InstallNotFound), (fn.__name__, got)
        assert got.status == 404
    assert _expire(world, now + DAY, org="b") == {"archived": 0, "deleted": 0}
    assert {i: _row(world, i) for i in ids} == before
    # Org A's own run moves them.
    assert _expire(world, now, org="a") == {"archived": 1, "deleted": 1}


def test_r8_f_an_unbound_session_raises_and_writes_nothing(world) -> None:
    now = _now()
    got = _install(world, "a1", "org", [_claim(_k("f5"))], ends_at=now - DAY)
    before = _row(world, got.id)

    async def go() -> list[Any]:
        out: list[Any] = []
        for fn, kw in (
            (store.keep_install, {"install_id": got.id}),
            (store.restore_install, {"install_id": got.id, "now": now}),
            (store.expire_due, {"now": now}),
        ):
            session = get_session_factory()()
            try:
                await fn(session, **kw)
                out.append(None)
            except TenantUnbound as exc:
                out.append(exc)
            finally:
                await session.rollback()
                await session.close()
        return out

    assert all(isinstance(e, TenantUnbound) for e in _go(world, go))
    assert _row(world, got.id) == before


def test_r8_f_a_time_with_no_zone_is_refused(world) -> None:
    now = _now()
    got = _install(world, "a1", "org", [_claim(_k("f6"))], ends_at=now - DAY)
    before = _row(world, got.id)
    naive = now.replace(tzinfo=None)
    for fn, kw in (
        (store.restore_install, {"install_id": got.id, "now": naive}),
        (store.expire_due, {"now": naive}),
    ):
        refused = _call(world, fn, **kw)
        assert isinstance(refused, InstallError), (fn.__name__, refused)
        assert (refused.code, refused.status) == ("bad_now", 422)
    assert _row(world, got.id) == before


def test_r8_f_a_bad_install_id_is_a_typed_refusal(world) -> None:
    for fn, kw in (
        (store.keep_install, {"install_id": "not-a-uuid"}),
        (store.restore_install, {"install_id": "not-a-uuid", "now": _now()}),
    ):
        got = _call(world, fn, **kw)
        assert isinstance(got, InstallError) and got.code == "bad_install_id", got
    missing = str(uuid.uuid4())
    assert isinstance(_call(world, store.keep_install, install_id=missing), InstallNotFound)


# ── g. Two expiries at once ────────────────────────────────────────────────


def _waits_on_the_lock(world: Any, pid: int, task: asyncio.Task[Any]) -> str:
    """``waited`` when the backend *pid* waits on an advisory lock, ``done``
    when the task ended first. Polls the server, so no sleep sets the order."""
    rows = _admin(
        world,
        "SELECT wait_event_type, wait_event FROM pg_stat_activity WHERE pid = :pid",
        {"pid": pid},
    )
    if rows and tuple(rows[0]) == ("Lock", "advisory"):
        return "waited"
    return "done" if task.done() else ""


def test_r8_g_two_expiries_at_once_archive_the_install_one_time(world) -> None:
    """Expiry 1 takes the lock and archives, and its transaction stays open.
    Expiry 2 starts in a second session and must wait on the lock. The test
    sees the wait on the server before it lets expiry 1 commit. Then expiry 2
    finds no due install, and ``archived_at`` is the time of expiry 1."""
    a = world.p.org_a
    now = _now()
    later = now + timedelta(seconds=5)
    got = _install(world, "a1", "org", [_claim(_k("g1"))], ends_at=now - DAY)

    async def go() -> SimpleNamespace:
        done, release, started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        pid: dict[str, int] = {}

        async def first() -> Any:
            async with tenant_session(a) as db:
                out = await store.expire_due(db, now=now)
                done.set()
                await release.wait()
                return out

        async def second() -> Any:
            async with tenant_session(a) as db:
                pid["pid"] = int((await db.execute(text("SELECT pg_backend_pid()"))).scalar_one())
                started.set()
                return await store.expire_due(db, now=later)

        one = asyncio.create_task(first())
        await asyncio.wait_for(done.wait(), 30)
        two = asyncio.create_task(second())
        await asyncio.wait_for(started.wait(), 30)
        state = ""
        end = time.monotonic() + 30
        while not state and time.monotonic() < end:
            state = await asyncio.to_thread(_waits_on_the_lock, world, pid["pid"], two)
            if not state:
                await asyncio.sleep(0.02)
        release.set()
        return SimpleNamespace(state=state, one=await one, two=await two)

    out = _go(world, go)
    assert out.state == "waited", "expiry 2 did not wait on the advisory lock"
    assert out.one == {"archived": 1, "deleted": 0}
    assert out.two == {"archived": 0, "deleted": 0}
    row = _row(world, got.id)
    assert (row[0], row[2]) == ("archived", now)


# ── notice_times ───────────────────────────────────────────────────────────


def test_r8_notice_times_of_a_real_row(world) -> None:
    now = _now()
    got = _install(world, "a1", "org", [_claim(_k("n1"))], ends_at=now)
    _archive_at(world, got.id, now - DAY)
    row = _row(world, got.id)
    assert store.notice_times(row[1], row[2]) == (
        row[1] - timedelta(days=3),
        row[2] + timedelta(days=27),
    )
