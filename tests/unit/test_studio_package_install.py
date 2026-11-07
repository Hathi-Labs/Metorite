"""WS-PKG PKG-8b — the org and group install of a Studio app, with no database.

Spec: ``project-docs/specs/package_host.md`` §4 (PKG-Q6, the org wins),
§5.5, §6.1 and the PKG-8b row of §10. Code: the route
``POST /studio/packages/{id}/install`` (``routes/studio/packages.py``
``install_package``), ``package_host/studio_app.py`` (``install_scope`` and
``keep_scope_install``), ``package_host/store.py`` (``in_group`` and
``OVERLAP``) and ``studio/packages_store.py`` (``lock_for_install``). The R8
twin is ``tests/unit/test_studio_package_install_r8.py``.

The checks here:

1. The scope of the body is ``org`` or ``group:<slug>``. Each other value is
   a 422, and the route reads nothing.
2. The two switches answer 404 and open no session.
3. Who installs (§6.1): an org install needs ``studio:publish``, and a group
   install needs a membership of the group. A refusal is 403, and the route
   locks nothing and writes nothing.
4. The order of one session: the group check, the row lock, the live
   version, then the install. A draft with no live version is 409
   ``not_published``.
5. A new install is 201, and a move is 200. A store refusal is its
   ``detail_for`` of the member.
6. The lock statement is ``FOR KEY SHARE`` on a live package of the member
   in the bound org. ``in_group`` reads nothing for a value that is not a
   group.
7. The R8 twin is in the ``--must-run`` lists of both workflows.

**R7.** The build report of task 1963 holds the result of each mutation.
M1: drop the ``studio:publish`` check. Check 3 goes red. M2: drop the
group check. Check 3 goes red. M3: skip the lock call. Check 4 goes red.
"""

from __future__ import annotations

import ast
import asyncio
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.package_host import store as host_store
from gateway.package_host import studio_app
from gateway.routes.studio import actions, packages
from gateway.studio import packages_store as store

ROOT = Path(__file__).resolve().parents[2]
ORG = str(uuid.uuid4())
OWEN = "owen@pkg8b.test"
BEN = "ben@pkg8b.test"
PID = str(uuid.uuid4())
STUDIO = ["feature:studio"]
PUBLISHER = ["feature:studio", "studio:publish"]
NOT_FOUND = {"detail": "Not found"}


# ── The fake world ─────────────────────────────────────────────────────────


class _World:
    """The calls that the route makes, in order. ``live`` is the live version
    of the package, ``moved`` makes ``keep_scope_install`` answer a move, and
    ``refuse`` is a store refusal that it raises."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.sessions = 0
        self.owned = True
        self.live: int | None = 3
        self.groups: set[str] = {"group:ops"}
        self.moved = False
        self.refuse: host_store.InstallError | None = None


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    w = _World()

    @asynccontextmanager
    async def session() -> Any:
        w.sessions += 1
        yield object()

    async def get_package(db: Any, member: str, pid: str, **_: Any) -> Any:
        w.calls.append("owner")
        return object() if w.owned and member == OWEN and pid == PID else None

    async def in_group(db: Any, *, member: str, group: str) -> bool:
        w.calls.append(f"in_group:{group}")
        return member == OWEN and group in w.groups

    async def lock_for_install(db: Any, member: str, pid: str) -> Any:
        w.calls.append("lock")
        return store.InstallTarget(package_id=pid, live_version=w.live)

    async def get_version(db: Any, member: str, pid: str, version: int) -> Any:
        w.calls.append(f"version:{version}")
        return store.PackageVersion(
            package_id=pid, version=version, manifest={"name": "Visits"}, bundle_sha256="0",
            release_notes="", published_by=member, published_at=None, review_status="auto")

    async def keep(db: Any, **kw: Any) -> tuple[host_store.Install, bool]:
        w.calls.append(f"keep:{kw['scope_kind']}:{kw['scope_subject']}:{kw['version']}")
        if w.refuse is not None:
            raise w.refuse
        made = host_store.Install(
            id="install-1", app_id=kw["app_id"], version=kw["version"],
            scope_kind=kw["scope_kind"], scope_subject=kw["scope_subject"],
            installed_by=kw["member"], ends_at=None, contributions=())
        return made, not w.moved

    monkeypatch.setattr(packages, "_tenant_session", session)
    monkeypatch.setattr(store, "get_package", get_package)
    monkeypatch.setattr(store, "lock_for_install", lock_for_install)
    monkeypatch.setattr(store, "get_version", get_version)
    monkeypatch.setattr(host_store, "in_group", in_group)
    monkeypatch.setattr(studio_app, "keep_scope_install", keep)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    return w


def _client(grants: list[str], email: str = OWEN) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=ORG, access=build_access(grants))
    return TestClient(app)


def _install(grants: list[str], body: Any, email: str = OWEN, pid: str = PID) -> Any:
    return _client(grants, email).post(f"/studio/packages/{pid}/install", json=body)


# ── 1. The scope ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(("raw", "want"), [
    ("org", ("org", "org")),
    (" org ", ("org", "org")),
    ("group:Ops", ("group", "group:ops")),
    ("group: ops ", ("group", "group:ops")),
])
def test_the_scope_is_org_or_a_group(raw: str, want: tuple[str, str]) -> None:
    assert studio_app.install_scope(raw) == want


@pytest.mark.parametrize("raw", [
    None, 7, "", "  ", "private", "ana@x.test", "GROUP:ops", "group:", "agent:x",
    "group:a\x00b", "group:\ud800", "group:" + "x" * 400, ["org"]])
def test_each_other_scope_is_refused(raw: Any) -> None:
    with pytest.raises(host_store.ScopeRefused) as exc:
        studio_app.install_scope(raw)
    assert exc.value.code == "bad_scope" and exc.value.status == 422


@pytest.mark.parametrize("body", [{}, {"scope": "me@pkg8b.test"}, {"scope": None},
                                  {"scope": "private"}])
def test_a_bad_scope_is_422_and_locks_nothing(world: _World, body: dict[str, Any]) -> None:
    r = _install(PUBLISHER, body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "bad_scope"
    assert world.calls == ["owner"]


def test_a_body_that_is_not_a_json_object_is_422(world: _World) -> None:
    r = _client(PUBLISHER).post(f"/studio/packages/{PID}/install", content=b"[1]",
                                headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert world.calls == ["owner"]


# ── 2. The switches ────────────────────────────────────────────────────────


@pytest.mark.parametrize("off", ["studio_orgs", "package_host_orgs"])
def test_each_switch_off_is_404_and_opens_no_session(
    world: _World, monkeypatch: pytest.MonkeyPatch, off: str,
) -> None:
    monkeypatch.setattr(get_settings(), off, "")
    r = _client(PUBLISHER).post(f"/studio/packages/{PID}/install", content=b"{not json",
                                headers={"content-type": "application/json"})
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0 and world.calls == []


def test_the_switches_come_first_in_the_handler() -> None:
    fn = next(n for n in ast.walk(ast.parse(Path(packages.__file__).read_text(encoding="utf-8")))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "install_package")
    body = fn.body[1:]  # the docstring
    tests = [ast.unparse(s.test) for s in body[:2] if isinstance(s, ast.If)]
    assert tests == ["not flags.studio_allowed(user.organization_id)",
                     "not package_host_allowed(user.organization_id)"]


def test_a_package_of_another_member_is_404_before_the_body_is_read(world: _World) -> None:
    world.owned = False
    r = _install(PUBLISHER, {"scope": "nope"})
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.calls == ["owner"]


# ── 3. Who installs (§6.1) ─────────────────────────────────────────────────


def test_an_org_install_needs_the_share_verb(world: _World) -> None:
    r = _install(STUDIO, {"scope": "org"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "forbidden", "message": packages.NO_ORG_RIGHT}
    assert world.calls == ["owner"]
    assert world.sessions == 1     # the owner read only


def test_a_group_install_needs_a_membership_of_the_group(world: _World) -> None:
    r = _install(PUBLISHER, {"scope": "group:eng"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "forbidden", "message": packages.NOT_IN_GROUP}
    assert world.calls == ["owner", "in_group:group:eng"]


def test_a_group_member_installs_with_the_feature_only(world: _World) -> None:
    r = _install(STUDIO, {"scope": "group:OPS"})
    assert r.status_code == 201, r.text
    assert world.calls == ["owner", "in_group:group:ops", "lock", "version:3",
                           "keep:group:group:ops:3"]


def test_an_org_install_reads_no_group(world: _World) -> None:
    r = _install(PUBLISHER, {"scope": "org"})
    assert r.status_code == 201, r.text
    assert world.calls == ["owner", "lock", "version:3", "keep:org:org:3"]


# ── 4. One session, in order ───────────────────────────────────────────────


def test_a_draft_with_no_live_version_is_409_and_installs_nothing(world: _World) -> None:
    world.live = None
    r = _install(PUBLISHER, {"scope": "org"})
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "not_published", "message": packages.NOT_LIVE}
    assert world.calls == ["owner", "lock"]


def test_the_lock_comes_before_the_install_in_the_source() -> None:
    """``lock_for_install`` runs in the session of the install, before
    ``keep_scope_install``: the lock order of §5.5."""
    fn = next(n for n in ast.walk(ast.parse(Path(packages.__file__).read_text(encoding="utf-8")))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "install_package")
    lines = {ast.unparse(c.func): c.lineno for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert lines["store.lock_for_install"] < lines["store.get_version"]
    assert lines["store.get_version"] < lines["studio_app.keep_scope_install"]
    assert lines["host_store.in_group"] < lines["store.lock_for_install"]


# ── 5. The answers ─────────────────────────────────────────────────────────


def test_a_new_install_is_201_and_a_move_is_200(world: _World) -> None:
    first = _install(PUBLISHER, {"scope": "org"})
    assert first.status_code == 201
    assert first.json() == {"created": True, "install": {
        "id": "install-1", "app_id": PID, "version": 3, "scope_kind": "org",
        "scope_subject": "org"}}
    world.moved = True
    again = _install(PUBLISHER, {"scope": "org"})
    assert again.status_code == 200
    assert again.json()["created"] is False


def test_a_store_refusal_is_its_detail_for_the_member(world: _World) -> None:
    """Ben's private install holds the key. The answer for Owen names a
    private install, and never Ben, his app or his install."""
    secret_app = str(uuid.uuid4())
    world.refuse = host_store.ClaimTaken("nav", f"/apps/{PID}", host_store.Holder(
        source="private", package=secret_app, install_id="install-9", scope_subject=BEN))
    r = _install(PUBLISHER, {"scope": "org"})
    assert r.status_code == 409
    assert r.json()["detail"]["holder"] == {"source": "private"}
    for secret in (BEN, secret_app, "install-9"):
        assert secret not in r.text


# ── 6. The statements ──────────────────────────────────────────────────────


def test_the_lock_is_key_share_on_a_live_package_of_the_member() -> None:
    sql = store._INSTALL_LOCK_SQL
    assert sql.endswith("FOR KEY SHARE"), sql
    for part in (f"organization_id = {store._BOUND_ORG}", "format = 'manifest'",
                 "owner_email = :m", "id = CAST(:id AS uuid)", "status <> 'archived'"):
        assert part in sql, part


class _NoDb:
    async def execute(self, *_a: Any, **_k: Any) -> Any:
        raise AssertionError("in_group read the database")


@pytest.mark.parametrize(("member", "group"), [
    ("owen@pkg8b.test", "org"), ("owen@pkg8b.test", "ben@pkg8b.test"),
    ("group:ops", "group:ops"), ("", "group:ops"), ("owen@pkg8b.test", "group:a\x00b")])
def test_in_group_reads_nothing_for_a_value_that_is_not_a_group(member: str, group: str) -> None:
    assert asyncio.run(host_store.in_group(_NoDb(), member=member, group=group)) is False


def test_keep_scope_install_moves_first_and_writes_only_when_nothing_moved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    moved: list[host_store.Install | None] = [None]

    async def set_version(db: Any, **kw: Any) -> host_store.Install | None:
        calls.append(f"set_version:{kw['scope_kind']}:{kw['scope_subject']}:{kw['version']}")
        return moved[0]

    async def install(db: Any, **kw: Any) -> host_store.Install:
        calls.append(f"install:{kw['scope_kind']}:{kw['installed_by']}:{kw['ends_at']}")
        return host_store.Install(
            id="new", app_id=kw["app_id"], version=kw["version"], scope_kind=kw["scope_kind"],
            scope_subject=kw["scope_subject"], installed_by=kw["installed_by"], ends_at=None,
            contributions=tuple(kw["contributions"]))

    monkeypatch.setattr(host_store, "set_version", set_version)
    monkeypatch.setattr(host_store, "install", install)

    def keep() -> tuple[host_store.Install, bool]:
        return asyncio.run(studio_app.keep_scope_install(
            object(), app_id=PID, version=2, scope_kind="org", scope_subject="org",
            member=OWEN, manifest={"name": "Visits"}))

    made, created = keep()
    assert created is True and made.contributions == tuple(studio_app.claims_of(
        PID, {"name": "Visits"}))
    assert calls == ["set_version:org:org:2", f"install:org:{OWEN}:None"]
    calls.clear()
    moved[0] = made
    assert keep() == (made, False)
    assert calls == ["set_version:org:org:2"]
    with pytest.raises(host_store.ScopeRefused):
        asyncio.run(studio_app.keep_scope_install(
            object(), app_id=PID, version=2, scope_kind="private", scope_subject=OWEN,
            member=OWEN, manifest={}))


# ── 7. The R8 twin runs in CI ──────────────────────────────────────────────


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_list(workflow: str) -> None:
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", text)
    assert block, f"{workflow}: the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_studio_package_install_r8\.py(?: \\)?$",
                     block.group(1), re.M), workflow
