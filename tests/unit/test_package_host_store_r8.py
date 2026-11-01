"""WS-PKG PKG-5b part 1 — the install store on a real database (R8).

Spec: ``project-docs/specs/package_host.md`` §4, §5.1 and the PKG-5b row of
§10. Code: ``gateway/package_host/store.py`` and
``gateway/package_host/sharing.py``. The hermetic twin is
``tests/unit/test_package_host_store.py``.

The rows live on a phase-4 promoted scratch database: the full ladder, then
the four generated phases. Each install runs as the app role, through
``acb_common.db.tenant_session``, so RLS binds each statement. Two
organizations. Org A has three members and two groups: Ana and Ben are in
``ops``, and Cat is in ``eng``.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<db>
    uv run pytest tests/unit/test_package_host_store_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-5b part 1).**

a. An install writes one ``package_install`` row and its claims. The org
   comes from the column DEFAULT. A payload at the limit of the CHECK
   passes, and one byte more is refused before the database.
b. A duplicate claim raises ``ClaimTaken`` with the point, the key and the
   holder, and writes nothing. ``triggers`` and ``slot`` allow many claims.
   The overlap of org, group and private claims follows §4, from both sides.
   A key that a system package holds is refused. A claim of org B never
   conflicts with org A.
c. Two installs of one key at once give one winner and one ``ClaimTaken``.
   The second install waits on the advisory lock of the org.
d. ``GROUP:x@y.test`` asked for as private, a ``scope_kind`` that does not
   agree, and a private scope of another member each raise ``ScopeRefused``
   (a 422), never a database error.
e. The store installs only a Studio package of the bound org that is not
   archived, one install at each scope. An unbound session writes nothing.
f. ``sharing.OTHERS_SEE_THE_INSTALL`` holds its three tests in SQL.
g. ``app_grants`` is tenant-scoped (§5.1, "Check app_grants first").

**R7: mutations.** Each ran on 2026-11-01, and each made this suite red.
The PKG-5b part 1 report lists them.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import TenantUnbound, get_session_factory, tenant_session
from gateway.package_host import sharing, store
from gateway.package_host.store import (
    AlreadyInstalled,
    AppNotInstallable,
    ClaimTaken,
    Contribution,
    ContributionRefused,
    ScopeRefused,
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
ANA = f"ana.{TAG}@pkg5b.test"
BEN = f"ben.{TAG}@pkg5b.test"
CAT = f"cat.{TAG}@pkg5b.test"
OPS = f"group:ops{TAG}"
ENG = f"group:eng{TAG}"
_SLUG_LIKE = f"pkg5b-{TAG}-%"
_EMAIL_LIKE = f"%.{TAG}@pkg5b.test"
_GROUP_LIKE = f"%{TAG}"
#: The Studio packages of org A, by name.
_A_APPS = ("p1", "p2", "p3", "s1", "s2", "s3", "s4", "s5", "s6")


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted):  # noqa: F811
    """Org A: nine Studio packages, an archived one, an App Workshop app,
    three members and two groups. Org B: one Studio package. Seeded as the
    superuser. Teardown deletes the apps, and the installs, the claims and
    the grants go by cascade."""
    p = promoted
    apps: dict[str, str] = {}
    manifest = json.dumps({"studio": 1, "id": "pkg5b", "name": "P", "purpose": "T"})

    def app(c: Any, name: str, org: str, *, fmt: str = "manifest",
            status: str = "draft") -> None:
        apps[name] = str(c.execute(text(
            "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
            "format, status, manifest) VALUES (:s, 'pkg5b', :o, '', :org, :f, :st, "
            "CAST(:m AS jsonb)) RETURNING id"),
            {"s": f"pkg5b-{TAG}-{name}", "o": ANA, "org": org, "f": fmt, "st": status,
             "m": manifest}).scalar_one())

    with p.admin_engine.begin() as c:
        for name in _A_APPS:
            app(c, name, p.org_a)
        app(c, "archived", p.org_a, status="archived")
        app(c, "code", p.org_a, fmt="code")
        app(c, "b1", p.org_b)
        users: dict[str, str] = {}
        for email in (ANA, BEN, CAT):
            users[email] = str(c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org) RETURNING id"),
                {"e": email, "org": p.org_a}).scalar_one())
        for group, members in ((OPS, (ANA, BEN)), (ENG, (CAT,))):
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


def _go(url: Any, fn: Any) -> Any:
    """Run ``fn()`` in a new event loop, on the engine of *url*."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(url)):
            return await fn()
    return asyncio.run(go())


def _claim(key: str, point: str = "jobs", **kw: Any) -> Contribution:
    return Contribution(point=point, key=key, **kw)


def _args(world: Any, app: str, **kw: Any) -> dict[str, Any]:
    """The arguments of one org install of *app* by Ana. *kw* overrides them."""
    return {"app_id": world.apps[app], "version": 1, "scope_subject": "org",
            "scope_kind": "org", "installed_by": ANA, "ends_at": None,
            "contributions": [], **kw}


def _scope(subject: str) -> dict[str, Any]:
    """The scope arguments for *subject*, with the right kind and installer."""
    if subject == "org":
        return {"scope_subject": "org", "scope_kind": "org"}
    if subject.startswith("group:"):
        return {"scope_subject": subject, "scope_kind": "group"}
    return {"scope_subject": subject, "scope_kind": "private", "installed_by": subject}


async def _install(org: str, **args: Any) -> Any:
    """One install as the app role, bound to *org*. Answers the install or
    the refusal, so a test can read either."""
    try:
        async with tenant_session(org) as db:
            return await store.install(db, **args)
    except store.InstallError as exc:
        return exc


def _run(world: Any, org_tag: str = "a", **args: Any) -> Any:
    org = world.p.org_a if org_tag == "a" else world.p.org_b
    return _go(world.p.app_url, lambda: _install(org, **args))


def _admin(world: Any, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    """Rows read as the superuser, which RLS does not bind."""
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params or {}).all())


def _installs_of(world: Any, app: str) -> int:
    return len(_admin(world, "SELECT 1 FROM package_install WHERE app_id = CAST(:a AS uuid)",
                      {"a": world.apps[app]}))


def _suite_installs(world: Any) -> int:
    return len(_admin(world, "SELECT 1 FROM package_install i JOIN apps a ON a.id = i.app_id "
                             "WHERE a.slug LIKE :s", {"s": _SLUG_LIKE}))


# ── a. One row and its claims ──────────────────────────────────────────────


def test_r8_an_install_writes_one_row_and_its_claims(world) -> None:
    got = _run(world, **_args(world, "p1", installed_by=f"  {ANA.upper()} ", contributions=[
        _claim("quote", payload={"label": "New quote", "feature": "studio"}, position=2),
        _claim("/studio/quotes", point="nav", payload={"label": "Quotes"}),
        _claim("quotes-today", point="cards"),
    ]))
    assert isinstance(got, store.Install), got
    [row] = _admin(world, (
        "SELECT CAST(organization_id AS text), CAST(app_id AS text), version, scope_kind, "
        "scope_subject, installed_by, state, ends_at FROM package_install "
        "WHERE id = CAST(:i AS uuid)"), {"i": got.id})
    assert tuple(row) == (world.p.org_a, world.apps["p1"], 1, "org", "org", ANA, "active", None)
    claims = _admin(world, (
        "SELECT point, key, payload, position, CAST(organization_id AS text) "
        "FROM package_contribution WHERE install_id = CAST(:i AS uuid) ORDER BY point"),
        {"i": got.id})
    assert [tuple(r) for r in claims] == [
        ("cards", "quotes-today", {}, 0, world.p.org_a),
        ("jobs", "quote", {"label": "New quote", "feature": "studio"}, 2, world.p.org_a),
        ("nav", "/studio/quotes", {"label": "Quotes"}, 0, world.p.org_a),
    ]


def test_r8_a_payload_at_the_limit_passes_and_one_byte_more_is_refused(world) -> None:
    """The store counts the bytes of the jsonb text, as the CHECK does. Each
    ``é`` is two bytes, so a count of characters would be wrong."""
    base = len(json.dumps({"t": ""}, ensure_ascii=False).encode("utf-8"))
    room = store.MAX_PAYLOAD_BYTES - base
    at_limit = {"t": "é" * (room // 2) + "x" * (room % 2)}
    ends = datetime(2026, 12, 1, tzinfo=UTC)
    got = _run(world, **_args(world, "p1", version=None, ends_at=ends,
                              contributions=[_claim("big", payload=at_limit)],
                              **_scope(ANA)))
    assert isinstance(got, store.Install), got
    [[size, kept_end]] = _admin(world, (
        "SELECT octet_length(c.payload::text), i.ends_at FROM package_contribution c "
        "JOIN package_install i ON i.id = c.install_id WHERE c.install_id = CAST(:i AS uuid)"),
        {"i": got.id})
    assert size == store.MAX_PAYLOAD_BYTES
    assert kept_end == ends
    over = {"t": at_limit["t"] + "x"}
    refused = _run(world, **_args(world, "p2", contributions=[_claim("big", payload=over)]))
    assert isinstance(refused, ContributionRefused) and refused.code == "payload_too_large"
    assert _installs_of(world, "p2") == 0


# ── b. The claims ──────────────────────────────────────────────────────────


def test_r8_a_duplicate_claim_names_the_point_the_key_and_the_holder(world) -> None:
    first = _run(world, **_args(world, "p1", contributions=[_claim("quote")]))
    taken = _run(world, **_args(world, "p2", contributions=[
        _claim("other", point="cards"), _claim("quote")]))
    assert isinstance(taken, ClaimTaken), taken
    assert (taken.point, taken.key, taken.status) == ("jobs", "quote", 409)
    assert taken.holder == store.Holder(
        source="org", package=world.apps["p1"], install_id=first.id, scope_subject="org")
    assert _installs_of(world, "p2") == 0


#: (the scope of the claim that exists, the scope of the new claim, conflict).
#: Ana and Ben are in OPS, and Cat is in ENG (the world fixture).
_OVERLAP = [
    ("org", "org", True),
    ("org", OPS, True),
    ("org", ANA, True),
    (OPS, "org", True),
    (OPS, OPS, True),
    (OPS, ENG, False),
    (OPS, ANA, True),
    (OPS, CAT, False),
    (ANA, "org", True),
    (ANA, ANA, True),
    (ANA, BEN, False),
    (ANA, OPS, True),
    (ANA, ENG, False),
    (CAT, ENG, True),
]


@pytest.mark.parametrize(("held", "new", "conflict"), _OVERLAP)
def test_r8_the_overlap_of_two_scopes_follows_section_4(
    world, held: str, new: str, conflict: bool,
) -> None:
    """§4 from both sides. A private claim of Ana and an OPS claim conflict
    in either order of install, because Ana is in OPS."""
    first = _run(world, **_args(world, "p1", contributions=[_claim("k")], **_scope(held)))
    assert isinstance(first, store.Install), first
    got = _run(world, **_args(world, "p2", contributions=[_claim("k")], **_scope(new)))
    if conflict:
        assert isinstance(got, ClaimTaken), got
        assert got.holder.install_id == first.id
        assert _installs_of(world, "p2") == 0
    else:
        assert isinstance(got, store.Install), got
        assert _installs_of(world, "p2") == 1


@pytest.mark.parametrize("point", ["triggers", "slot"])
def test_r8_triggers_and_slot_allow_many_claims(world, point: str) -> None:
    for app, subject in (("p1", "org"), ("p2", "org"), ("p3", ANA)):
        got = _run(world, **_args(world, app, contributions=[_claim("task.panel", point=point)],
                                  **_scope(subject)))
        assert isinstance(got, store.Install), (app, got)
    assert len(_admin(world, "SELECT 1 FROM package_contribution WHERE point = :p "
                             "AND key = 'task.panel'", {"p": point})) == 3


@pytest.mark.parametrize(("point", "key", "package"), [
    ("jobs", "capture", "projects"), ("nav", "/projects", "projects"),
    ("cards", "today", "home")])
def test_r8_a_key_of_a_system_package_is_refused(world, point, key, package) -> None:
    got = _run(world, **_args(world, "p1", contributions=[_claim(key, point=point)]))
    assert isinstance(got, ClaimTaken), got
    assert got.holder == store.Holder(source="system", package=package)
    assert _installs_of(world, "p1") == 0


def test_r8_a_claim_of_another_org_never_conflicts(world) -> None:
    a = _run(world, **_args(world, "p1", contributions=[_claim("quote")]))
    b = _run(world, "b", **_args(world, "b1", contributions=[_claim("quote")]))
    assert isinstance(a, store.Install) and isinstance(b, store.Install), (a, b)


def test_r8_a_paused_install_still_holds_its_keys(world) -> None:
    """Each claim that exists counts, until the disposal of PKG-9 deletes it."""
    first = _run(world, **_args(world, "p1", contributions=[_claim("quote")]))
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE package_install SET state = 'paused' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": first.id})
    got = _run(world, **_args(world, "p2", contributions=[_claim("quote")]))
    assert isinstance(got, ClaimTaken), got


# ── c. Two installs at once ────────────────────────────────────────────────


def _waits_on_the_lock(world: Any, pid: int, task: asyncio.Task[Any]) -> str:
    """``waited`` when the backend *pid* waits on an advisory lock, ``done``
    when the task ended first. Polls the server, so no sleep sets the order."""
    rows = _admin(world, "SELECT wait_event_type, wait_event FROM pg_stat_activity "
                         "WHERE pid = :pid", {"pid": pid})
    if rows and tuple(rows[0]) == ("Lock", "advisory"):
        return "waited"
    return "done" if task.done() else ""


def test_r8_two_installs_of_one_key_at_once_give_one_winner(world) -> None:
    """Install 1 takes the lock and writes, and its transaction stays open.
    Install 2 starts in a second session and must wait on the lock. The
    test sees the wait on the server before it lets install 1 commit. Then
    install 2 reads the claim of install 1 and raises ``ClaimTaken``."""
    a = world.p.org_a

    async def go() -> SimpleNamespace:
        written, release, started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        pid: dict[str, int] = {}

        async def first() -> Any:
            async with tenant_session(a) as db:
                got = await store.install(db, **_args(world, "p1", contributions=[
                    _claim("race")]))
                written.set()
                await release.wait()
                return got

        async def second() -> Any:
            try:
                async with tenant_session(a) as db:
                    pid["pid"] = int((await db.execute(text(
                        "SELECT pg_backend_pid()"))).scalar_one())
                    started.set()
                    return await store.install(db, **_args(world, "p2", contributions=[
                        _claim("race")]))
            except store.InstallError as exc:
                return exc

        one = asyncio.create_task(first())
        await asyncio.wait_for(written.wait(), 30)
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

    out = _go(world.p.app_url, go)
    assert out.state == "waited", "install 2 did not wait on the advisory lock"
    assert isinstance(out.one, store.Install), out.one
    assert isinstance(out.two, ClaimTaken), out.two
    assert out.two.holder.install_id == out.one.id
    assert _installs_of(world, "p1") == 1 and _installs_of(world, "p2") == 0


# ── d. The scope is a 422, never a database error ──────────────────────────


@pytest.mark.parametrize(("args", "code"), [
    ({"scope_subject": "GROUP:x@y.test", "scope_kind": "private",
      "installed_by": "x@y.test"}, "scope_kind_mismatch"),
    ({"scope_subject": "org", "scope_kind": "group"}, "scope_kind_mismatch"),
    ({"scope_subject": OPS, "scope_kind": "org"}, "scope_kind_mismatch"),
    ({"scope_subject": BEN, "scope_kind": "private"}, "private_not_owner"),
    ({"scope_subject": OPS, "scope_kind": "group", "version": None}, "draft_not_private"),
])
def test_r8_a_bad_scope_is_a_typed_422_and_writes_nothing(world, args, code) -> None:
    got = _run(world, **_args(world, "p1", **args))
    assert isinstance(got, ScopeRefused), got
    assert (got.code, got.status) == (code, 422)
    assert _suite_installs(world) == 0


def test_r8_the_kind_comes_from_the_normalised_scope(world) -> None:
    """``GROUP:x@y.test`` normalises through the email branch to
    ``group:x@y.test``, a group. Asked for as a group, it installs, and the
    CHECK on ``scope_subject`` takes the row."""
    got = _run(world, **_args(world, "p1", scope_subject="GROUP:x@y.test",
                              scope_kind="group"))
    assert isinstance(got, store.Install), got
    assert _admin(world, "SELECT scope_kind, scope_subject FROM package_install "
                         "WHERE id = CAST(:i AS uuid)", {"i": got.id})[0] == (
        "group", "group:x@y.test")


# ── e. The app, the scope key and the tenant ───────────────────────────────


@pytest.mark.parametrize("app", ["archived", "code", "b1", "unknown"])
def test_r8_only_a_live_studio_package_of_the_bound_org_installs(world, app: str) -> None:
    app_id = str(uuid.uuid4()) if app == "unknown" else world.apps[app]
    got = _run(world, **_args(world, "p1", app_id=app_id))
    assert isinstance(got, AppNotInstallable), got
    assert got.status == 404
    assert _suite_installs(world) == 0


def test_r8_one_app_has_one_install_at_each_scope(world) -> None:
    first = _run(world, **_args(world, "p1", **_scope(ANA)))
    again = _run(world, **_args(world, "p1", **_scope(ANA)))
    other = _run(world, **_args(world, "p1"))
    assert isinstance(first, store.Install) and isinstance(other, store.Install)
    assert isinstance(again, AlreadyInstalled) and again.status == 409
    assert _installs_of(world, "p1") == 2


def test_r8_an_unbound_session_takes_no_lock_and_writes_nothing(world) -> None:
    async def go() -> Any:
        session = get_session_factory()()
        try:
            await store.install(session, **_args(world, "p1"))
        except TenantUnbound as exc:
            return exc
        finally:
            await session.rollback()
            await session.close()
        return None

    assert isinstance(_go(world.p.app_url, go), TenantUnbound)
    assert _suite_installs(world) == 0


# ── f. The share rule of sharing.py ────────────────────────────────────────


def test_r8_only_a_share_by_the_owner_to_another_address_counts(world) -> None:
    """Each app holds one grant. Only the grant to Ben that Ana made counts.
    The consent row of Ben, a share by Cat, a grant to an agent, a grant to
    every agent, and Ana's own address in another case do not count."""
    grants = {
        "s1": (BEN, ANA.upper()),
        "s2": (BEN, BEN),
        "s3": (BEN, CAT),
        "s4": ("agent:helper", ANA),
        "s5": ("agents:*", ANA),
        "s6": (ANA.upper(), ANA),
    }
    ids = {}
    for app in grants:
        got = _run(world, **_args(world, app, **_scope(ANA)))
        assert isinstance(got, store.Install), got
        ids[got.id] = app

    async def go() -> list[str]:
        async with tenant_session(world.p.org_a) as db:
            for app, (subject, by) in grants.items():
                await db.execute(text(
                    "INSERT INTO app_grants (app_id, subject, granted_by) "
                    "VALUES (CAST(:a AS uuid), :s, :by)"),
                    {"a": world.apps[app], "s": subject, "by": by})
            rows = (await db.execute(text(
                "SELECT CAST(id AS text) FROM package_install "
                f"WHERE {sharing.OTHERS_SEE_THE_INSTALL}"))).scalars().all()
        return [ids[r] for r in rows if r in ids]

    assert _go(world.p.app_url, go) == ["s1"]


# ── g. app_grants is tenant-scoped ─────────────────────────────────────────


def test_r8_app_grants_is_tenant_scoped(world) -> None:
    """§5.1 asked PKG-5b to confirm it. ENABLE, FORCE and one policy, and
    org B reads no grant of org A."""
    [[enabled, forced]] = _admin(world, (
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = 'app_grants'::regclass"))
    policies = [r[0] for r in _admin(world, (
        "SELECT polname FROM pg_policy WHERE polrelid = 'app_grants'::regclass"))]
    assert (enabled, forced, policies) == (True, True, ["app_grants_tenant_isolation"])

    async def go() -> int:
        async with tenant_session(world.p.org_a) as db:
            await db.execute(text(
                "INSERT INTO app_grants (app_id, subject, granted_by) "
                "VALUES (CAST(:a AS uuid), :s, :s)"), {"a": world.apps["p1"], "s": BEN})
        async with tenant_session(world.p.org_b) as db:
            return int((await db.execute(text(
                "SELECT count(*) FROM app_grants WHERE app_id = CAST(:a AS uuid)"),
                {"a": world.apps["p1"]})).scalar_one())

    assert _go(world.p.app_url, go) == 0
    assert len(_admin(world, "SELECT 1 FROM app_grants WHERE app_id = CAST(:a AS uuid)",
                      {"a": world.apps["p1"]})) == 1
