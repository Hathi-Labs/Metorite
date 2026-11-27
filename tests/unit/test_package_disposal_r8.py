"""WS-PKG PKG-9 — the disposal of an install, on a real database (R8).

Spec: ``project-docs/specs/package_host.md`` §5.3 and the PKG-9 row of §10.
Code: ``remove_install``, ``pause_install`` and ``resume_install`` of
``gateway/package_host/store.py``. The hermetic fence of the one writer is
``tests/unit/test_package_host_store.py``.

Each call runs as the app role, through ``tenant_session``, so FORCE RLS
binds each statement. Each check of rows reads as the superuser, which RLS
does not bind. Org A has five members and two groups, and eight Studio
packages with version 1. Org B has one Studio package and one member.

Run (one R8 suite at a time, because the ``promoted`` fixture drops
``<db>_h3rls``)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_package_disposal_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-9).**

a. A removal leaves no row of the install in ``package_install`` or in
   ``package_contribution``.
b. Org B cannot remove, pause or resume an install of org A. Each call
   raises ``InstallNotFound``, and the rows of org A do not change.
c. An unbound session raises ``TenantUnbound`` and writes nothing.
d. After a pause, the next ``resolve_for`` of each viewer holds no claim of
   the install, and the version returns to its value before the install.
   After a resume, the claims come back.
e. A paused install still holds its keys: an overlapping claim of another
   app raises ``ClaimTaken``.
f. After a removal, the key is free, and the same install succeeds.
g. A pause or a resume of an archived install raises ``StateRefused``
   (409) and writes nothing. A removal of an archived install succeeds.
h. Two removals at once give one deletion and one ``InstallNotFound``.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import TenantUnbound, get_session_factory, tenant_session
from gateway.package_host import store
from gateway.package_host.resolve import resolve_for
from gateway.package_host.store import (
    ClaimTaken,
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
ANA = f"ana.{TAG}@pkg9.test"
BEN = f"ben.{TAG}@pkg9.test"
CAT = f"cat.{TAG}@pkg9.test"
DAN = f"Dan.{TAG}@pkg9.test"
EVE = f"eve.{TAG}@pkg9.test"
ZED = f"zed.{TAG}@pkg9.test"
OPS = f"group:ops{TAG}"
ENG = f"group:eng{TAG}"
_SLUG_LIKE = f"pkg9-{TAG}-%"
_EMAIL_LIKE = f"%.{TAG}@pkg9.test"
_GROUP_LIKE = f"%{TAG}"
_A_APPS = ("a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8")
_ALL_A = (ANA, BEN, CAT, DAN, EVE)


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _k(name: str) -> str:
    """A claim key that no system package holds."""
    return f"{name}-{TAG}"


@pytest.fixture
def world(promoted):  # noqa: F811
    """Org A: eight Studio packages, each with version 1, five members and
    two groups. Org B: one Studio package and one member. Teardown deletes
    the apps, and the installs and the claims go by cascade."""
    p = promoted
    apps: dict[str, str] = {}

    def app(c: Any, name: str, org: str) -> None:
        man = json.dumps({"studio": 1, "id": f"r-{name}-{TAG}", "name": "P", "purpose": "T"})
        apps[name] = str(
            c.execute(
                text(
                    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
                    "format, status, manifest) VALUES (:s, 'pkg9', :o, '', :org, 'manifest', "
                    "'live', CAST(:m AS jsonb)) RETURNING id"
                ),
                {"s": f"pkg9-{TAG}-{name}", "o": ANA, "org": org, "m": man},
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
        users: dict[str, str] = {}
        for email, org in (
            (ANA, p.org_a),
            (BEN, p.org_a),
            (CAT, p.org_a),
            (DAN, p.org_a),
            (EVE, p.org_a),
            (ZED, p.org_b),
        ):
            users[email] = str(
                c.execute(
                    text(
                        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                        "VALUES (:e, :e, 'employee', 'active', :org) RETURNING id"
                    ),
                    {"e": email, "org": org},
                ).scalar_one()
            )
        for group, members in ((OPS, (ANA, BEN, DAN)), (ENG, (CAT, DAN))):
            gid = str(
                c.execute(
                    text(
                        "INSERT INTO org_group (organization_id, slug, display_name) "
                        "VALUES (:org, :s, :s) RETURNING id"
                    ),
                    {"org": p.org_a, "s": group.removeprefix("group:")},
                ).scalar_one()
            )
            for email in members:
                c.execute(
                    text(
                        "INSERT INTO org_group_member (group_id, user_id, organization_id) "
                        "VALUES (:g, :u, :org)"
                    ),
                    {"g": gid, "u": users[email], "org": p.org_a},
                )
    yield SimpleNamespace(p=p, apps=apps)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE slug LIKE :s"), {"s": _SLUG_LIKE})
        c.execute(text("DELETE FROM org_group WHERE slug LIKE :s"), {"s": _GROUP_LIKE})
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
    world: Any, app: str, scope: str, claims: list[Contribution], *, org: str = "a", by: str = ANA
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
                    ends_at=None,
                    contributions=claims,
                )
        except InstallError as exc:
            return exc

    return _go(world, go)


def _call(world: Any, fn: Any, install_id: str, *, org: str = "a") -> Any:
    """One disposal call as the app role, bound to *org*. Answers the result
    or the refusal. A refusal rolls the transaction back."""

    async def go() -> Any:
        try:
            async with tenant_session(_org(world, org)) as db:
                return await fn(db, install_id=install_id)
        except InstallError as exc:
            return exc

    return _go(world, go)


def _admin(world: Any, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    """Rows read as the superuser, which RLS does not bind."""
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params or {}).all())


def _admin_write(world: Any, sql: str, params: dict[str, Any] | None = None) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text(sql), params or {})


def _row(world: Any, install_id: str) -> tuple[Any, ...] | None:
    """The state, the stamps and the claim count of one install."""
    rows = _admin(
        world,
        (
            "SELECT i.state, i.archived_at, i.updated_at, "
            "(SELECT count(*) FROM package_contribution c WHERE c.install_id = i.id) "
            "FROM package_install i WHERE i.id = CAST(:i AS uuid)"
        ),
        {"i": install_id},
    )
    return tuple(rows[0]) if rows else None


def _claims_of(world: Any, install_id: str) -> int:
    return len(
        _admin(
            world,
            "SELECT 1 FROM package_contribution WHERE install_id = CAST(:i AS uuid)",
            {"i": install_id},
        )
    )


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


# ── a. A removal leaves no row ─────────────────────────────────────────────


def test_r8_a_removal_leaves_no_install_and_no_claim(world) -> None:
    got = _install(
        world,
        "a1",
        "org",
        [_claim(_k("r1")), _claim(_k("/r1"), point="nav"), _claim(_k("r1"), point="cards")],
    )
    kept = _install(world, "a2", "org", [_claim(_k("r2"))])
    assert isinstance(got, store.Install) and isinstance(kept, store.Install)
    assert _claims_of(world, got.id) == 3
    assert _call(world, store.remove_install, got.id) is True
    assert _row(world, got.id) is None
    assert _claims_of(world, got.id) == 0
    assert _row(world, kept.id) is not None and _claims_of(world, kept.id) == 1
    # A second removal of the same id finds nothing.
    again = _call(world, store.remove_install, got.id)
    assert isinstance(again, InstallNotFound) and again.status == 404


def test_r8_a_bad_install_id_is_a_typed_refusal(world) -> None:
    for fn in (store.remove_install, store.pause_install, store.resume_install):
        got = _call(world, fn, "not-a-uuid")
        assert isinstance(got, InstallError) and got.code == "bad_install_id", got
        assert got.status == 422


# ── b. Another org ─────────────────────────────────────────────────────────


def test_r8_org_b_cannot_remove_pause_or_resume_an_install_of_org_a(world) -> None:
    active = _install(world, "a1", "org", [_claim(_k("b1"))])
    paused = _install(world, "a2", "org", [_claim(_k("b2"))])
    assert _call(world, store.pause_install, paused.id) is True
    before = {i: _row(world, i) for i in (active.id, paused.id)}
    for fn, iid in (
        (store.remove_install, active.id),
        (store.pause_install, active.id),
        (store.resume_install, paused.id),
        (store.remove_install, paused.id),
    ):
        got = _call(world, fn, iid, org="b")
        assert isinstance(got, InstallNotFound), (fn.__name__, got)
        assert got.status == 404
    assert {i: _row(world, i) for i in (active.id, paused.id)} == before


# ── c. An unbound session ──────────────────────────────────────────────────


def test_r8_an_unbound_session_raises_and_writes_nothing(world) -> None:
    got = _install(world, "a1", "org", [_claim(_k("c1"))])
    before = _row(world, got.id)

    async def go() -> list[Any]:
        out: list[Any] = []
        for fn in (store.remove_install, store.pause_install, store.resume_install):
            session = get_session_factory()()
            try:
                await fn(session, install_id=got.id)
                out.append(None)
            except TenantUnbound as exc:
                out.append(exc)
            finally:
                await session.rollback()
                await session.close()
        return out

    assert all(isinstance(e, TenantUnbound) for e in _go(world, go))
    assert _row(world, got.id) == before


# ── d. A pause hides the install at the next read ──────────────────────────


def test_r8_a_pause_hides_the_claims_and_a_resume_brings_them_back(world) -> None:
    before = {w: _resolve(world, w)["version"] for w in _ALL_A}
    got = _install(world, "a1", "org", [_claim(_k("d1")), _claim(_k("/d1"), point="nav")])
    seen = {w: _resolve(world, w) for w in _ALL_A}
    for w in _ALL_A:
        assert got.id in _install_ids(seen[w]), w
        assert seen[w]["version"] != before[w], w

    assert _call(world, store.pause_install, got.id) is True
    assert _row(world, got.id)[0] == "paused"
    assert _claims_of(world, got.id) == 2
    for w in _ALL_A:
        body = _resolve(world, w)
        assert got.id not in _install_ids(body), w
        assert body["version"] == before[w], w
    # A second pause changes nothing.
    stamp = _row(world, got.id)
    assert _call(world, store.pause_install, got.id) is False
    assert _row(world, got.id) == stamp

    assert _call(world, store.resume_install, got.id) is True
    assert _row(world, got.id)[0] == "active"
    for w in _ALL_A:
        body = _resolve(world, w)
        assert got.id in _install_ids(body), w
        assert body["version"] == seen[w]["version"], w
    # A second resume changes nothing.
    stamp = _row(world, got.id)
    assert _call(world, store.resume_install, got.id) is False
    assert _row(world, got.id) == stamp


def test_r8_a_pause_moves_updated_at(world) -> None:
    got = _install(world, "a1", ANA, [_claim(_k("d2"))])
    first = _row(world, got.id)[2]
    assert _call(world, store.pause_install, got.id) is True
    assert _row(world, got.id)[2] > first


# ── e. A paused install holds its keys ─────────────────────────────────────


def test_r8_a_paused_install_still_blocks_an_overlapping_claim(world) -> None:
    held = _install(world, "a1", OPS, [_claim(_k("e1"))])
    assert _call(world, store.pause_install, held.id) is True
    for app, scope in (("a2", "org"), ("a3", OPS), ("a4", BEN)):
        got = _install(world, app, scope, [_claim(_k("e1"))])
        assert isinstance(got, ClaimTaken), (scope, got)
        assert got.holder.install_id == held.id


# ── f. A removal frees the key ─────────────────────────────────────────────


def test_r8_after_a_removal_the_key_is_free(world) -> None:
    held = _install(world, "a1", "org", [_claim(_k("f1"))])
    blocked = _install(world, "a2", "org", [_claim(_k("f1"))])
    assert isinstance(blocked, ClaimTaken), blocked
    assert _call(world, store.remove_install, held.id) is True
    got = _install(world, "a2", "org", [_claim(_k("f1"))])
    assert isinstance(got, store.Install), got
    # The removed app can install again at the same scope, and now it waits.
    again = _install(world, "a1", "org", [_claim(_k("f1"))])
    assert isinstance(again, ClaimTaken) and again.holder.install_id == got.id


# ── g. An archived install ─────────────────────────────────────────────────


def test_r8_an_archived_install_is_refused_a_state_move(world) -> None:
    got = _install(world, "a1", "org", [_claim(_k("g1"))])
    _admin_write(
        world,
        "UPDATE package_install SET state = 'archived', archived_at = now() "
        "WHERE id = CAST(:i AS uuid)",
        {"i": got.id},
    )
    before = _row(world, got.id)
    for fn in (store.pause_install, store.resume_install):
        refused = _call(world, fn, got.id)
        assert isinstance(refused, StateRefused), (fn.__name__, refused)
        assert (refused.code, refused.status) == ("state_refused", 409)
        assert _row(world, got.id) == before
    # A removal takes an install in any state.
    assert _call(world, store.remove_install, got.id) is True
    assert _row(world, got.id) is None and _claims_of(world, got.id) == 0


# ── h. Two removals at once ────────────────────────────────────────────────


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


def test_r8_two_removals_at_once_give_one_deletion_and_one_not_found(world) -> None:
    """Removal 1 takes the lock and deletes, and its transaction stays open.
    Removal 2 starts in a second session and must wait on the lock. The test
    sees the wait on the server before it lets removal 1 commit. Then
    removal 2 finds no row and raises ``InstallNotFound``."""
    a = world.p.org_a
    got = _install(world, "a1", "org", [_claim(_k("h1"))])

    async def go() -> SimpleNamespace:
        done, release, started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        pid: dict[str, int] = {}

        async def first() -> Any:
            async with tenant_session(a) as db:
                out = await store.remove_install(db, install_id=got.id)
                done.set()
                await release.wait()
                return out

        async def second() -> Any:
            try:
                async with tenant_session(a) as db:
                    pid["pid"] = int(
                        (await db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                    )
                    started.set()
                    return await store.remove_install(db, install_id=got.id)
            except InstallError as exc:
                return exc

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
    assert out.state == "waited", "removal 2 did not wait on the advisory lock"
    assert out.one is True
    assert isinstance(out.two, InstallNotFound), out.two
    assert _row(world, got.id) is None and _claims_of(world, got.id) == 0
