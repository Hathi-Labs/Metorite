"""WS-PKG PKG-11a — the promotion and the share, on a real database (R8).

Spec: ``project-docs/specs/package_host.md`` §5.1 decision 3, §6.2 ("The
rules of a promotion") and the PKG-11a row of §10. Code:
``store.promote_to_group`` and ``package_host/promotion.share_with_people``.
The hermetic twin is ``tests/unit/test_package_promotion.py``.

Each call runs as the app role, through ``tenant_session``, so FORCE RLS
binds each statement. Each check of rows reads as the superuser, which RLS
does not bind. Org A has five members and two groups: OPS holds Ana, Ben and
Dan, and ENG holds Cat and Dan. Org A has eight Studio packages with version
1. Org B has one Studio package and one member.

Run (one R8 suite at a time, because the ``promoted`` fixture drops
``<db>_h3rls``)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_package_promotion_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-11a).**

a. A promotion to a group keeps the install id, the version,
   ``installed_by``, ``created_at`` and each claim row.
b. After it, the resolve shows the install to each member of the group and
   to the owner, and not to a person outside the group.
c. A claim of another app that overlaps the group scope raises
   ``ClaimTaken`` and writes nothing.
d. A second install of the app at that group raises ``AlreadyInstalled``.
e. A draft, a paused or an archived install, an install with ``ends_at``,
   and an owner outside the group are refused, and nothing is written.
f. A share with a person outside the group blocks the promotion (PKG-Q9).
g. Org B cannot promote or share an install of org A. An unbound session
   raises ``TenantUnbound``.
h. Two promotions at once give one winner.
i. A share onto an existing consent row counts for the resolve and for the
   purge, and keeps the role and the consent hash (PK-7).
j. A share by a member other than the owner, a share with the owner's own
   address, and a share with an address outside the org are refused, and
   no row is written.
k. A share with a person whom another member already shares the app with
   is refused (``shared_by_another_owner``), and the whole call writes no
   row. The row of the first sharer stays.
l. A promotion deletes the share rows of the install in its transaction,
   and keeps a consent row and a row of another sharer. A new private
   install of the app then shows to no old grantee.
m. A promotion by a member other than the owner is refused (``not_owner``).
   A grantee who is not active does not block a promotion.
n. A removal of a private install deletes its share rows in its
   transaction, and keeps a consent row and a row of another sharer. A new
   private install of the app then shows to no old grantee, and another
   owner can share with that grantee. A removal of an org or a group
   install deletes no row of ``app_grants``. A removal that fails deletes
   nothing. Two removals at once give one winner and leave no share row.
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
from gateway.package_host import promotion, store
from gateway.package_host.promotion import ShareRefused
from gateway.package_host.resolve import resolve_for
from gateway.package_host.store import (
    AlreadyInstalled,
    ClaimTaken,
    Contribution,
    InstallError,
    InstallNotFound,
    NotTheOwner,
    PromotionRefused,
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
ANA = f"ana.{TAG}@pkg11.test"
BEN = f"ben.{TAG}@pkg11.test"
CAT = f"cat.{TAG}@pkg11.test"
DAN = f"Dan.{TAG}@pkg11.test"
EVE = f"eve.{TAG}@pkg11.test"
ZED = f"zed.{TAG}@pkg11.test"
OPS = f"group:ops{TAG}"
ENG = f"group:eng{TAG}"
_SLUG_LIKE = f"pkg11-{TAG}-%"
_EMAIL_LIKE = f"%.{TAG}@pkg11.test"
_GROUP_LIKE = f"%{TAG}"
_A_APPS = ("a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8")
_IN_OPS = (ANA, BEN, DAN)
_OUT_OF_OPS = (CAT, EVE)


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _k(name: str) -> str:
    """A claim key that no system package holds."""
    return f"{name}-{TAG}"


@pytest.fixture
def world(promoted):  # noqa: F811
    """The world of ``test_package_disposal_r8.py``, with its own tag.
    Teardown deletes the apps, and the installs, the claims, the versions
    and the grants go by cascade."""
    p = promoted
    apps: dict[str, str] = {}

    def app(c: Any, name: str, org: str) -> None:
        man = json.dumps({"studio": 1, "id": f"r-{name}-{TAG}", "name": "P", "purpose": "T"})
        apps[name] = str(c.execute(text(
            "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
            "format, status, manifest) VALUES (:s, 'pkg11', :o, '', :org, 'manifest', "
            "'live', CAST(:m AS jsonb)) RETURNING id"),
            {"s": f"pkg11-{TAG}-{name}", "o": ANA, "org": org, "m": man}).scalar_one())
        c.execute(text(
            "INSERT INTO app_versions (app_id, version, manifest, bundle_html, "
            "bundle_sha256, published_by, organization_id) VALUES "
            "(CAST(:i AS uuid), 1, CAST(:m AS jsonb), '', 'sha', :by, CAST(:o AS uuid))"),
            {"i": apps[name], "m": man, "by": ANA, "o": org})

    with p.admin_engine.begin() as c:
        for name in _A_APPS:
            app(c, name, p.org_a)
        app(c, "b1", p.org_b)
        users: dict[str, str] = {}
        for email, org in ((ANA, p.org_a), (BEN, p.org_a), (CAT, p.org_a), (DAN, p.org_a),
                           (EVE, p.org_a), (ZED, p.org_b)):
            users[email] = str(c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org) RETURNING id"),
                {"e": email, "org": org}).scalar_one())
        for group, members in ((OPS, (ANA, BEN, DAN)), (ENG, (CAT, DAN))):
            gid = str(c.execute(text(
                "INSERT INTO org_group (organization_id, slug, display_name) "
                "VALUES (:org, :s, :s) RETURNING id"),
                {"org": p.org_a, "s": group.removeprefix("group:")}).scalar_one())
            for email in members:
                c.execute(text(
                    "INSERT INTO org_group_member (group_id, user_id, organization_id) "
                    "VALUES (:g, :u, :org)"), {"g": gid, "u": users[email], "org": p.org_a})
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


def _install(world: Any, app: str, scope: str, claims: list[Contribution], *, org: str = "a",
             by: str = ANA, version: int | None = 1, ends_at: datetime | None = None) -> Any:
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
                    db, app_id=world.apps[app], version=version, scope_subject=scope,
                    scope_kind=kind, installed_by=installer, ends_at=ends_at,
                    contributions=claims)
        except InstallError as exc:
            return exc

    return _go(world, go)


def _promote(world: Any, install_id: str, group: str, *, org: str = "a",
             actor: str = ANA) -> Any:
    """One promotion as the app role. Answers the install or the refusal."""

    async def go() -> Any:
        try:
            async with tenant_session(_org(world, org)) as db:
                return await store.promote_to_group(db, install_id=install_id, group=group,
                                                    actor=actor)
        except InstallError as exc:
            return exc

    return _go(world, go)


def _share(world: Any, install_id: str, emails: list[str], sharer: str = ANA, *,
           org: str = "a") -> Any:
    """One share as the app role. Answers the addresses or the refusal."""

    async def go() -> Any:
        try:
            async with tenant_session(_org(world, org)) as db:
                return await promotion.share_with_people(
                    db, install_id=install_id, emails=emails, sharer=sharer)
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
    """Each column of one install, with its ctid and xmin."""
    rows = _admin(world, (
        "SELECT CAST(id AS text), CAST(app_id AS text), version, scope_kind, scope_subject, "
        "installed_by, ends_at, state, archived_at, created_at, updated_at, "
        "CAST(ctid AS text), CAST(xmin AS text) "
        "FROM package_install WHERE id = CAST(:i AS uuid)"), {"i": install_id})
    return tuple(rows[0]) if rows else None


def _claim_rows(world: Any, install_id: str) -> list[tuple[Any, ...]]:
    """Each claim row of one install, with its ctid and xmin, in order."""
    return [tuple(r) for r in _admin(world, (
        "SELECT point, key, payload, position, CAST(ctid AS text), CAST(xmin AS text) "
        "FROM package_contribution WHERE install_id = CAST(:i AS uuid) ORDER BY point, key"),
        {"i": install_id})]


def _grants(world: Any, app: str) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in _admin(world, (
        "SELECT subject, role, consented_scope_hash, granted_by FROM app_grants "
        "WHERE app_id = CAST(:a AS uuid) ORDER BY subject"), {"a": world.apps[app]})]


def _resolve(world: Any, who: str, *, org: str = "a") -> dict[str, Any]:
    """The answer of ``resolve_for`` for *who*, who may use each feature."""

    async def go() -> dict[str, Any]:
        async with tenant_session(_org(world, org)) as db:
            return await resolve_for(db, viewer=who, can_use=lambda _slug: True)

    return _go(world, go)


def _install_ids(body: dict[str, Any]) -> set[str]:
    return {e["install_id"] for entries in body["contributions"].values() for e in entries
            if e["install_id"] is not None}


def _sees(world: Any, who: str, install_id: str) -> bool:
    return install_id in _install_ids(_resolve(world, who))


def _unchanged(world: Any, install_id: str, before: tuple[Any, ...],
               claims: list[tuple[Any, ...]]) -> None:
    assert _row(world, install_id) == before
    assert _claim_rows(world, install_id) == claims


# ── a. and b. A promotion keeps the install ────────────────────────────────


def test_r8_a_promotion_keeps_the_install_and_shows_it_to_the_group(world) -> None:
    got = _install(world, "a1", ANA, [_claim(_k("a1")), _claim(_k("/a1"), point="nav"),
                                      _claim(_k("t1"), point="triggers")])
    assert isinstance(got, store.Install), got
    before = _row(world, got.id)
    claims = _claim_rows(world, got.id)
    assert len(claims) == 3
    assert [w for w in (*_IN_OPS, *_OUT_OF_OPS) if _sees(world, w, got.id)] == [ANA]

    moved = _promote(world, got.id, OPS.upper().replace("GROUP:", "group:"))
    assert isinstance(moved, store.Install), moved
    assert (moved.id, moved.scope_kind, moved.scope_subject) == (got.id, "group", OPS)
    after = _row(world, got.id)
    # The id, the app, the version, installed_by, ends_at, the state,
    # archived_at and created_at stay. The scope and updated_at move.
    assert after[:3] == before[:3] and after[5:10] == before[5:10]
    assert (after[3], after[4]) == ("group", OPS)
    assert after[10] > before[10]
    # Each claim row is the same tuple: no claim was written again.
    assert _claim_rows(world, got.id) == claims
    for w in _IN_OPS:
        assert _sees(world, w, got.id), w
    for w in _OUT_OF_OPS:
        assert not _sees(world, w, got.id), w


# ── c. An overlapping claim of another app ─────────────────────────────────


def test_r8_an_overlapping_claim_of_another_app_is_taken_and_nothing_is_written(world) -> None:
    """Ben is in OPS, Cat is not. The private claims of Ben and Ana never
    overlap, but a group claim of OPS overlaps the private claim of Ben."""
    mine = _install(world, "a1", ANA, [_claim(_k("c1"))])
    cats = _install(world, "a3", CAT, [_claim(_k("c1"))])
    assert isinstance(cats, store.Install), cats
    bens = _install(world, "a2", BEN, [_claim(_k("c1"))])
    assert isinstance(mine, store.Install) and isinstance(bens, store.Install), (mine, bens)
    before, claims = _row(world, mine.id), _claim_rows(world, mine.id)
    got = _promote(world, mine.id, OPS)
    assert isinstance(got, ClaimTaken), got
    assert got.holder.install_id == bens.id
    _unchanged(world, mine.id, before, claims)
    # Without Ben's claim, the promotion goes, and Cat's claim stays apart.
    _admin_write(world, "DELETE FROM package_install WHERE id = CAST(:i AS uuid)",
                 {"i": bens.id})
    assert isinstance(_promote(world, mine.id, OPS), store.Install)


def test_r8_another_install_of_the_same_app_is_no_conflict(world) -> None:
    mine = _install(world, "a1", ANA, [_claim(_k("c2"))])
    bens = _install(world, "a1", BEN, [_claim(_k("c2"))])
    assert isinstance(bens, store.Install), bens
    assert isinstance(_promote(world, mine.id, OPS), store.Install)


# ── d. A second install at the group ───────────────────────────────────────


def test_r8_a_second_install_at_the_group_is_refused(world) -> None:
    held = _install(world, "a1", OPS, [_claim(_k("d1"))], by=BEN)
    mine = _install(world, "a1", ANA, [_claim(_k("d1"))])
    assert isinstance(held, store.Install) and isinstance(mine, store.Install), (held, mine)
    before, claims = _row(world, mine.id), _claim_rows(world, mine.id)
    got = _promote(world, mine.id, OPS)
    assert isinstance(got, AlreadyInstalled) and got.status == 409, got
    _unchanged(world, mine.id, before, claims)


# ── e. What cannot move ────────────────────────────────────────────────────


def test_r8_an_install_that_cannot_move_is_refused_and_nothing_is_written(world) -> None:
    later = datetime.now(UTC) + timedelta(days=30)
    draft = _install(world, "a1", ANA, [_claim(_k("e1"))], version=None)
    paused = _install(world, "a2", ANA, [_claim(_k("e2"))])
    archived = _install(world, "a3", ANA, [_claim(_k("e3"))])
    ending = _install(world, "a4", ANA, [_claim(_k("e4"))], ends_at=later)
    outside = _install(world, "a5", ANA, [_claim(_k("e5"))])
    grouped = _install(world, "a6", OPS, [_claim(_k("e6"))])
    for got in (draft, paused, archived, ending, outside, grouped):
        assert isinstance(got, store.Install), got
    assert _go(world, lambda: _pause(world, paused.id)) is True
    _admin_write(world, "UPDATE package_install SET state = 'archived', archived_at = now() "
                        "WHERE id = CAST(:i AS uuid)", {"i": archived.id})
    cases = ((draft, OPS, "draft"), (paused, OPS, "not_active"),
             (archived, OPS, "not_active"), (ending, OPS, "not_kept"),
             (outside, ENG, "owner_not_in_group"), (grouped, ENG, "not_private"))
    for inst, group, code in cases:
        before, claims = _row(world, inst.id), _claim_rows(world, inst.id)
        got = _promote(world, inst.id, group)
        assert isinstance(got, PromotionRefused), (code, got)
        assert (got.code, got.status) == (code, 409)
        _unchanged(world, inst.id, before, claims)


async def _pause(world: Any, install_id: str) -> bool:
    async with tenant_session(world.p.org_a) as db:
        return await store.pause_install(db, install_id=install_id)


def test_r8_a_removed_member_of_the_group_cannot_promote(world) -> None:
    mine = _install(world, "a1", ANA, [_claim(_k("e7"))])
    _admin_write(world, "UPDATE app_user SET status = 'removed' WHERE email = :e", {"e": ANA})
    got = _promote(world, mine.id, OPS)
    assert isinstance(got, PromotionRefused) and got.code == "owner_not_in_group", got


# ── f. A share outside the group blocks the promotion ──────────────────────


def test_r8_a_share_with_a_person_outside_the_group_blocks_the_promotion(world) -> None:
    mine = _install(world, "a1", ANA, [_claim(_k("f1"))])
    assert _share(world, mine.id, [CAT]) == (CAT,)
    before, claims = _row(world, mine.id), _claim_rows(world, mine.id)
    got = _promote(world, mine.id, OPS)
    assert isinstance(got, PromotionRefused) and got.code == "grantee_outside_group", got
    assert CAT not in str(got)
    _unchanged(world, mine.id, before, claims)
    # Without the share of Cat, a share with Ben (in OPS) does not block.
    _admin_write(world, "DELETE FROM app_grants WHERE app_id = CAST(:a AS uuid)",
                 {"a": world.apps["a1"]})
    assert _share(world, mine.id, [BEN]) == (BEN,)
    assert isinstance(_promote(world, mine.id, OPS), store.Install)


def test_r8_a_consent_row_outside_the_group_does_not_block(world) -> None:
    """A consent row of Cat names Cat as ``granted_by``. It fails test 3 of
    the share rule, so it shares nothing and blocks nothing."""
    mine = _install(world, "a1", ANA, [_claim(_k("f2"))])
    _admin_write(world, "INSERT INTO app_grants (app_id, subject, granted_by, organization_id) "
                        "VALUES (CAST(:a AS uuid), :s, :s, CAST(:o AS uuid))",
                 {"a": world.apps["a1"], "s": CAT, "o": world.p.org_a})
    assert isinstance(_promote(world, mine.id, OPS), store.Install)


# ── g. Another org and an unbound session ──────────────────────────────────


def test_r8_org_b_cannot_promote_or_share_an_install_of_org_a(world) -> None:
    mine = _install(world, "a1", ANA, [_claim(_k("g1"))])
    before, claims = _row(world, mine.id), _claim_rows(world, mine.id)
    got = _promote(world, mine.id, OPS, org="b")
    assert isinstance(got, InstallNotFound) and got.status == 404, got
    got = _share(world, mine.id, [BEN], sharer=ANA, org="b")
    assert isinstance(got, InstallNotFound), got
    got = _share(world, mine.id, [BEN], sharer=ZED, org="b")
    assert isinstance(got, InstallNotFound), got
    _unchanged(world, mine.id, before, claims)
    assert _grants(world, "a1") == []


def test_r8_an_unbound_session_raises_and_writes_nothing(world) -> None:
    mine = _install(world, "a1", ANA, [_claim(_k("g2"))])
    before, claims = _row(world, mine.id), _claim_rows(world, mine.id)

    async def go() -> list[Any]:
        out: list[Any] = []
        calls = (lambda db: store.promote_to_group(db, install_id=mine.id, group=OPS,
                                                   actor=ANA),
                 lambda db: promotion.share_with_people(db, install_id=mine.id, emails=[BEN],
                                                        sharer=ANA))
        for call in calls:
            session = get_session_factory()()
            try:
                await call(session)
                out.append(None)
            except TenantUnbound as exc:
                out.append(exc)
            finally:
                await session.rollback()
                await session.close()
        return out

    assert all(isinstance(e, TenantUnbound) for e in _go(world, go))
    _unchanged(world, mine.id, before, claims)
    assert _grants(world, "a1") == []


# ── h. Two promotions at once ──────────────────────────────────────────────


def _waits_on_the_lock(world: Any, pid: int, task: asyncio.Task[Any]) -> str:
    """``waited`` when the backend *pid* waits on an advisory lock, ``done``
    when the task ended first. Polls the server, so no sleep sets the order."""
    rows = _admin(world, "SELECT wait_event_type, wait_event FROM pg_stat_activity "
                         "WHERE pid = :pid", {"pid": pid})
    if rows and tuple(rows[0]) == ("Lock", "advisory"):
        return "waited"
    return "done" if task.done() else ""


def test_r8_two_promotions_at_once_give_one_winner(world) -> None:
    """Promotion 1 takes the lock and writes, and its transaction stays
    open. Promotion 2 starts in a second session and must wait on the lock.
    The test sees the wait on the server before it lets promotion 1 commit.
    Then promotion 2 finds a group install and raises ``not_private``."""
    a = world.p.org_a
    mine = _install(world, "a1", ANA, [_claim(_k("h1"))])

    async def go() -> SimpleNamespace:
        done, release, started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        pid: dict[str, int] = {}

        async def first() -> Any:
            async with tenant_session(a) as db:
                out = await store.promote_to_group(db, install_id=mine.id, group=OPS,
                                                              actor=ANA)
                done.set()
                await release.wait()
                return out

        async def second() -> Any:
            try:
                async with tenant_session(a) as db:
                    pid["pid"] = int(
                        (await db.execute(text("SELECT pg_backend_pid()"))).scalar_one())
                    started.set()
                    return await store.promote_to_group(db, install_id=mine.id, group=OPS,
                                                              actor=ANA)
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
    assert out.state == "waited", "promotion 2 did not wait on the advisory lock"
    assert isinstance(out.one, store.Install)
    assert isinstance(out.two, PromotionRefused) and out.two.code == "not_private", out.two
    assert _row(world, mine.id)[3:5] == ("group", OPS)


# ── i. A share onto a consent row (PK-7) ───────────────────────────────────


def _purge_counts(world: Any, email: str) -> dict[str, int]:
    """The two counts of the purge for the private installs of *email*."""
    from gateway.routes.admin.members import _PURGE_DELETES, _PURGE_KEEPS, _purge_params

    rows = {r.key: r for r in (*_PURGE_DELETES, *_PURGE_KEEPS)
            if r.key in ("private_app_installs", "shared_private_app_installs")}

    async def go() -> dict[str, int]:
        async with tenant_session(world.p.org_a) as db:
            return {key: int((await db.execute(text(r.count_sql), _purge_params(
                r, uid=str(uuid.uuid4()), email=email))).scalar_one())
                for key, r in rows.items()}

    return _go(world, go)


def test_r8_a_share_onto_a_consent_row_counts_for_the_resolve_and_the_purge(world) -> None:
    mine = _install(world, "a1", ANA, [_claim(_k("i1"))])
    _admin_write(world, (
        "INSERT INTO app_grants (app_id, subject, role, consented_scope_hash, granted_by, "
        "organization_id) VALUES (CAST(:a AS uuid), :s, 'edit', 'hash-1', :s, "
        "CAST(:o AS uuid))"), {"a": world.apps["a1"], "s": BEN, "o": world.p.org_a})
    assert not _sees(world, BEN, mine.id)
    assert _purge_counts(world, ANA) == {"private_app_installs": 1,
                                         "shared_private_app_installs": 0}

    assert _share(world, mine.id, [BEN.upper()]) == (BEN,)
    # The row keeps its role and its consent hash. granted_by is Ana now.
    assert _grants(world, "a1") == [(BEN, "edit", "hash-1", ANA)]
    assert _sees(world, BEN, mine.id)
    assert not _sees(world, CAT, mine.id)
    assert _purge_counts(world, ANA) == {"private_app_installs": 0,
                                         "shared_private_app_installs": 1}
    # The install stays private.
    assert _row(world, mine.id)[3:5] == ("private", ANA)


def test_r8_a_new_share_writes_a_use_row(world) -> None:
    mine = _install(world, "a1", ANA, [_claim(_k("i2"))])
    assert _share(world, mine.id, [DAN, f" {CAT} "]) == (DAN.lower(), CAT)
    assert _grants(world, "a1") == sorted([(DAN.lower(), "use", "", ANA), (CAT, "use", "", ANA)])
    assert _sees(world, DAN, mine.id) and _sees(world, CAT, mine.id)


# ── j. A share that is refused ─────────────────────────────────────────────


def test_r8_a_share_that_is_refused_writes_no_row(world) -> None:
    mine = _install(world, "a1", ANA, [_claim(_k("j1"))])
    draft_of_ben = _install(world, "a2", BEN, [_claim(_k("j2"))])
    assert isinstance(draft_of_ben, store.Install), draft_of_ben
    cases = (
        (mine.id, [CAT], BEN, "not_owner"),
        (mine.id, [ANA.upper()], ANA, "own_address"),
        (mine.id, [f"nobody.{TAG}@pkg11.test"], ANA, "not_a_member"),
        (mine.id, [BEN, ZED], ANA, "not_a_member"),
        (mine.id, [OPS], ANA, "not_a_person"),
        (mine.id, ["org"], ANA, "not_a_person"),
    )
    for install_id, emails, sharer, code in cases:
        got = _share(world, install_id, emails, sharer)
        assert isinstance(got, InstallError) and got.code == code, (code, got)
    assert _grants(world, "a1") == []
    # A removed member is no active member of the org.
    _admin_write(world, "UPDATE app_user SET status = 'removed' WHERE email = :e", {"e": EVE})
    got = _share(world, mine.id, [EVE])
    assert isinstance(got, InstallError) and got.code == "not_a_member", got
    assert _grants(world, "a1") == []


# ── k. One sharer for each person ──────────────────────────────────────────


def test_r8_a_share_with_a_person_of_another_sharer_is_refused(world) -> None:
    """Ana and Ben each have a private install of a1. Ana shares with Cat.
    Ben must not take the row of Cat, or Cat loses Ana's install."""
    anas = _install(world, "a1", ANA, [_claim(_k("k1"))])
    bens = _install(world, "a1", BEN, [_claim(_k("k1"))])
    assert isinstance(anas, store.Install) and isinstance(bens, store.Install), (anas, bens)
    assert _share(world, anas.id, [CAT]) == (CAT,)
    got = _share(world, bens.id, [CAT], sharer=BEN)
    assert isinstance(got, ShareRefused), got
    assert (got.code, got.status) == ("shared_by_another_owner", 422)
    assert CAT not in str(got) and ANA not in str(got)
    assert _grants(world, "a1") == [(CAT, "use", "", ANA)]
    assert _sees(world, CAT, anas.id) and not _sees(world, CAT, bens.id)
    assert _purge_counts(world, ANA) == {"private_app_installs": 0,
                                         "shared_private_app_installs": 1}


def test_r8_a_share_twice_is_idempotent(world) -> None:
    mine = _install(world, "a1", ANA, [_claim(_k("k2"))])
    assert _share(world, mine.id, [CAT]) == (CAT,)
    assert _share(world, mine.id, [CAT.upper()]) == (CAT,)
    assert _grants(world, "a1") == [(CAT, "use", "", ANA)]


def test_r8_a_call_where_one_person_conflicts_writes_no_row(world) -> None:
    anas = _install(world, "a1", ANA, [_claim(_k("k3"))])
    bens = _install(world, "a1", BEN, [_claim(_k("k3"))])
    assert _share(world, anas.id, [CAT]) == (CAT,)
    got = _share(world, bens.id, [DAN, CAT], sharer=BEN)
    assert isinstance(got, ShareRefused) and got.code == "shared_by_another_owner", got
    assert _grants(world, "a1") == [(CAT, "use", "", ANA)]


# ── l. A promotion deletes the share rows ──────────────────────────────────


def _leave(world: Any, email: str, group: str) -> None:
    _admin_write(world, (
        "DELETE FROM org_group_member WHERE user_id = (SELECT id FROM app_user WHERE email = :e) "
        "AND group_id = (SELECT id FROM org_group WHERE slug = :s)"),
        {"e": email, "s": group.removeprefix("group:")})


def test_r8_a_new_private_install_after_a_promotion_shows_to_no_old_grantee(world) -> None:
    mine = _install(world, "a1", ANA, [_claim(_k("l1"))])
    assert _share(world, mine.id, [BEN]) == (BEN,)
    assert isinstance(_promote(world, mine.id, OPS), store.Install)
    assert _grants(world, "a1") == []
    again = _install(world, "a1", ANA, [_claim(_k("l1b"))])
    assert isinstance(again, store.Install), again
    assert not _sees(world, BEN, again.id)
    _leave(world, BEN, OPS)
    assert not _sees(world, BEN, again.id) and not _sees(world, BEN, mine.id)
    assert _purge_counts(world, ANA) == {"private_app_installs": 1,
                                         "shared_private_app_installs": 0}


def test_r8_a_promotion_keeps_a_consent_row_and_a_row_of_another_sharer(world) -> None:
    mine = _install(world, "a1", ANA, [_claim(_k("l2"))])
    bens = _install(world, "a1", BEN, [_claim(_k("l2"))])
    assert isinstance(bens, store.Install), bens
    _admin_write(world, (
        "INSERT INTO app_grants (app_id, subject, role, consented_scope_hash, granted_by, "
        "organization_id) VALUES (CAST(:a AS uuid), :s, 'edit', 'hash-1', :s, "
        "CAST(:o AS uuid))"), {"a": world.apps["a1"], "s": CAT, "o": world.p.org_a})
    assert _share(world, bens.id, [EVE], sharer=BEN) == (EVE,)
    assert _share(world, mine.id, [DAN]) == (DAN.lower(),)
    assert isinstance(_promote(world, mine.id, OPS), store.Install)
    assert _grants(world, "a1") == sorted([(CAT, "edit", "hash-1", CAT),
                                           (EVE, "use", "", BEN)])
    assert _sees(world, EVE, bens.id)


def test_r8_a_promotion_that_is_refused_deletes_no_share_row(world) -> None:
    mine = _install(world, "a1", ANA, [_claim(_k("l3"))])
    bens = _install(world, "a2", BEN, [_claim(_k("l3"))])
    assert isinstance(bens, store.Install), bens
    assert _share(world, mine.id, [DAN]) == (DAN.lower(),)
    before, claims = _row(world, mine.id), _claim_rows(world, mine.id)
    got = _promote(world, mine.id, OPS)
    assert isinstance(got, ClaimTaken), got
    _unchanged(world, mine.id, before, claims)
    assert _grants(world, "a1") == [(DAN.lower(), "use", "", ANA)]
    assert _sees(world, DAN, mine.id)


# ── m. The owner promotes, and a grantee who is not active does not block ──


def test_r8_a_promotion_by_another_member_is_refused(world) -> None:
    mine = _install(world, "a1", ANA, [_claim(_k("m1"))])
    assert _share(world, mine.id, [BEN]) == (BEN,)
    before, claims = _row(world, mine.id), _claim_rows(world, mine.id)
    got = _promote(world, mine.id, OPS, actor=BEN)
    assert isinstance(got, NotTheOwner), got
    assert (got.code, got.status) == ("not_owner", 403)
    _unchanged(world, mine.id, before, claims)
    assert _grants(world, "a1") == [(BEN, "use", "", ANA)]
    assert isinstance(_promote(world, mine.id, OPS, actor=ANA.upper()), store.Install)


def test_r8_a_grantee_who_is_not_active_does_not_block(world) -> None:
    """Ben (in OPS) and Cat (outside OPS) hold shares. While Cat is active,
    Cat blocks. When both are suspended, neither blocks, and the promotion
    deletes both share rows."""
    mine = _install(world, "a1", ANA, [_claim(_k("m2"))])
    assert _share(world, mine.id, [BEN, CAT]) == (BEN, CAT)
    _admin_write(world, "UPDATE app_user SET status = 'suspended' WHERE email = :e", {"e": BEN})
    got = _promote(world, mine.id, OPS)
    assert isinstance(got, PromotionRefused) and got.code == "grantee_outside_group", got
    _admin_write(world, "UPDATE app_user SET status = 'suspended' WHERE email = :e", {"e": CAT})
    assert isinstance(_promote(world, mine.id, OPS), store.Install)
    assert _grants(world, "a1") == []


# ── n. A removal deletes the share rows ────────────────────────────────────


def _remove(world: Any, install_id: str, *, org: str = "a") -> Any:
    """One removal as the app role. Answers True or the refusal."""

    async def go() -> Any:
        try:
            async with tenant_session(_org(world, org)) as db:
                return await store.remove_install(db, install_id=install_id)
        except InstallError as exc:
            return exc

    return _go(world, go)


def _consent(world: Any, app: str, who: str) -> None:
    """A consent row of *who*: the subject and ``granted_by`` are one."""
    _admin_write(world, (
        "INSERT INTO app_grants (app_id, subject, role, consented_scope_hash, granted_by, "
        "organization_id) VALUES (CAST(:a AS uuid), :s, 'edit', 'hash-1', :s, "
        "CAST(:o AS uuid))"), {"a": world.apps[app], "s": who, "o": world.p.org_a})


def test_r8_a_new_private_install_after_a_removal_shows_to_no_old_grantee(world) -> None:
    """The probe of the review. Ana installs a1, shares with Cat, removes the
    install, and installs a1 again with version 2. Cat must not see it, and
    Ben must be able to share his install of a1 with Cat."""
    mine = _install(world, "a1", ANA, [_claim(_k("n1"))])
    assert _share(world, mine.id, [CAT]) == (CAT,)
    assert _sees(world, CAT, mine.id)
    assert _remove(world, mine.id) is True
    assert _row(world, mine.id) is None and _grants(world, "a1") == []
    _admin_write(world, (
        "INSERT INTO app_versions (app_id, version, manifest, bundle_html, bundle_sha256, "
        "published_by, organization_id) SELECT id, 2, manifest, '', 'sha2', :by, "
        "organization_id FROM apps WHERE id = CAST(:a AS uuid)"),
        {"a": world.apps["a1"], "by": ANA})
    again = _install(world, "a1", ANA, [_claim(_k("n1b"))], version=2)
    assert isinstance(again, store.Install) and again.version == 2, again
    assert not _sees(world, CAT, again.id)
    assert _purge_counts(world, ANA) == {"private_app_installs": 1,
                                         "shared_private_app_installs": 0}
    bens = _install(world, "a1", BEN, [_claim(_k("n1c"))])
    assert isinstance(bens, store.Install), bens
    assert _share(world, bens.id, [CAT], sharer=BEN) == (CAT,)
    assert _grants(world, "a1") == [(CAT, "use", "", BEN)]
    assert _sees(world, CAT, bens.id) and not _sees(world, CAT, again.id)


def test_r8_a_removal_keeps_a_consent_row_and_a_row_of_another_sharer(world) -> None:
    mine = _install(world, "a1", ANA, [_claim(_k("n2"))])
    bens = _install(world, "a1", BEN, [_claim(_k("n2"))])
    assert isinstance(bens, store.Install), bens
    _consent(world, "a1", CAT)
    assert _share(world, bens.id, [EVE], sharer=BEN) == (EVE,)
    assert _share(world, mine.id, [DAN]) == (DAN.lower(),)
    assert _remove(world, mine.id) is True
    assert _grants(world, "a1") == sorted([(CAT, "edit", "hash-1", CAT),
                                           (EVE, "use", "", BEN)])
    assert _sees(world, EVE, bens.id)


def test_r8_a_removal_of_an_org_or_group_install_deletes_no_share_row(world) -> None:
    """Ana has a private install of a1 that she shares with Cat, and an org
    install and a group install of a1. Ben shares his own install of a1 with
    Eve. The removal of the org and the group installs keeps each row."""
    private = _install(world, "a1", ANA, [_claim(_k("n3"))])
    at_org = _install(world, "a1", "org", [_claim(_k("n3"))])
    at_ops = _install(world, "a1", OPS, [_claim(_k("n3"))])
    bens = _install(world, "a1", BEN, [_claim(_k("n3"))])
    for got in (private, at_org, at_ops, bens):
        assert isinstance(got, store.Install), got
    assert _share(world, private.id, [CAT]) == (CAT,)
    assert _share(world, bens.id, [EVE], sharer=BEN) == (EVE,)
    before = _grants(world, "a1")
    assert before == sorted([(CAT, "use", "", ANA), (EVE, "use", "", BEN)])
    assert _remove(world, at_org.id) is True
    assert _remove(world, at_ops.id) is True
    assert _row(world, at_org.id) is None and _row(world, at_ops.id) is None
    assert _grants(world, "a1") == before
    assert _sees(world, CAT, private.id) and _sees(world, EVE, bens.id)


def test_r8_a_removal_that_fails_deletes_nothing(world) -> None:
    mine = _install(world, "a1", ANA, [_claim(_k("n4"))])
    assert _share(world, mine.id, [CAT]) == (CAT,)
    before, claims = _row(world, mine.id), _claim_rows(world, mine.id)
    got = _remove(world, mine.id, org="b")
    assert isinstance(got, InstallNotFound) and got.status == 404, got
    got = _remove(world, str(uuid.uuid4()))
    assert isinstance(got, InstallNotFound), got
    _unchanged(world, mine.id, before, claims)
    assert _grants(world, "a1") == [(CAT, "use", "", ANA)]
    assert _sees(world, CAT, mine.id)


def test_r8_two_removals_at_once_give_one_winner(world) -> None:
    """Removal 1 takes the lock, deletes, and keeps its transaction open.
    Removal 2 must wait on the lock. The test sees the wait on the server
    before it lets removal 1 commit. Then removal 2 finds no install and
    raises ``InstallNotFound``. No share row stays."""
    a = world.p.org_a
    mine = _install(world, "a1", ANA, [_claim(_k("n5"))])
    assert _share(world, mine.id, [CAT, BEN]) == (CAT, BEN)

    async def go() -> SimpleNamespace:
        done, release, started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        pid: dict[str, int] = {}

        async def first() -> Any:
            async with tenant_session(a) as db:
                out = await store.remove_install(db, install_id=mine.id)
                done.set()
                await release.wait()
                return out

        async def second() -> Any:
            try:
                async with tenant_session(a) as db:
                    pid["pid"] = int(
                        (await db.execute(text("SELECT pg_backend_pid()"))).scalar_one())
                    started.set()
                    return await store.remove_install(db, install_id=mine.id)
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
    assert _row(world, mine.id) is None and _claim_rows(world, mine.id) == []
    assert _grants(world, "a1") == []
