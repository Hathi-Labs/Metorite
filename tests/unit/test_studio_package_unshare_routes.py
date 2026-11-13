"""WS-STU J2b-5 — the per-person unshare of a Studio app, with no database.

Spec: ``project-docs/specs/ai_studio.md`` §14 J2b-5. Decision: D180.3
(``studio:publish`` gates the share to others). Code: the route
``DELETE /studio/packages/{id}/share/{email}`` (``routes/studio/packages.py``
``unshare_package``), ``_owned_private_install`` (the owner rule of PKG-10d)
and ``package_host/promotion.unshare_person``. The R8 twin is
``tests/unit/test_studio_package_unshare_routes_r8.py``.

The checks here:

1. The two switches answer 404 and open no session. They come first.
2. ``studio:publish`` is checked before any session. A refusal is 403
   ``forbidden``, and nothing is locked or written.
3. A bad address is the store's 422, before any session.
4. One session, in order: the ``apps`` row lock, the find of the private
   install, the unshare, then the audit row.
5. A package of another member is 404. An owner with no private install is
   409 ``private_only``.
6. The sharer is the session member, never the path or a query (R5).
7. A store refusal is its ``detail_for``, with its status. A refusal writes
   no audit row.
8. The answer is 200 ``{removed: bool}``. ``false`` is no error, and it
   still writes its audit row. The audit row holds the op, the install,
   the person and ``removed``, and no value of the app.
9. The writer deletes only the row that the sharer made (text fence).
10. The R8 twin is in the ``--must-run`` lists of both workflows.
"""

from __future__ import annotations

import ast
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
from gateway.package_host import promotion, sharing
from gateway.package_host import store as host_store
from gateway.routes.studio import actions, packages
from gateway.studio import packages_store as store

ROOT = Path(__file__).resolve().parents[2]
ORG = str(uuid.uuid4())
OWEN = "Owen@j2b5.test"
OWEN_LOW = OWEN.lower()
ANA = "Ana@j2b5.test"
BEN = "ben@j2b5.test"
PID = str(uuid.uuid4())
INSTALL = str(uuid.uuid4())
STUDIO = ["feature:studio"]
PUBLISHER = ["feature:studio", "studio:publish"]
NOT_FOUND = {"detail": "Not found"}
BOUND = "organization_id = CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"


# ── The fake world ─────────────────────────────────────────────────────────


class _World:
    """The calls that the route makes, in order. ``owned`` makes the lock
    find the package, ``install`` is the private install of the owner,
    ``refuse`` is a store refusal, ``removed`` is the answer of the store,
    and ``audits`` holds each audit row. ``unshares`` holds the arguments
    of each unshare."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.sessions = 0
        self.owned = True
        self.install: str | None = INSTALL
        self.refuse: host_store.InstallError | None = None
        self.removed = True
        self.audits: list[dict[str, Any]] = []
        self.unshares: list[dict[str, Any]] = []


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    w = _World()

    @asynccontextmanager
    async def session() -> Any:
        w.sessions += 1
        yield object()

    async def lock_for_install(db: Any, member: str, pid: str) -> Any:
        w.calls.append("lock")
        if not (w.owned and member == OWEN and pid == PID):
            return None
        return store.InstallTarget(package_id=pid, live_version=1)

    async def private_install_id(db: Any, *, app_id: str, member: str) -> str | None:
        w.calls.append(f"find:{member}")
        return w.install

    async def unshare(db: Any, *, install_id: str, email: str, sharer: str) -> bool:
        w.calls.append("unshare")
        w.unshares.append({"install_id": install_id, "email": email, "sharer": sharer})
        if w.refuse is not None:
            raise w.refuse
        return w.removed

    async def audit_in(db: Any, **kw: Any) -> None:
        w.calls.append("audit")
        w.audits.append(kw)

    monkeypatch.setattr(packages, "_tenant_session", session)
    monkeypatch.setattr(packages, "record_app_audit_in", audit_in)
    monkeypatch.setattr(store, "lock_for_install", lock_for_install)
    monkeypatch.setattr(host_store, "private_install_id", private_install_id)
    monkeypatch.setattr(promotion, "unshare_person", unshare)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    return w


def _client(grants: list[str], email: str = OWEN) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=ORG, access=build_access(grants))
    return TestClient(app)


def _unshare(person: str = ANA, grants: list[str] = PUBLISHER, email: str = OWEN,
             pid: str = PID, query: str = "") -> Any:
    return _client(grants, email).delete(f"/studio/packages/{pid}/share/{person}{query}")


def _handler() -> ast.AsyncFunctionDef:
    tree = ast.parse(Path(packages.__file__).read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "unshare_package")


def _lines(node: ast.AST) -> dict[str, int]:
    return {ast.unparse(c.func): c.lineno for c in ast.walk(node) if isinstance(c, ast.Call)}


# ── 1. The switches ────────────────────────────────────────────────────────


@pytest.mark.parametrize("off", ["studio_orgs", "package_host_orgs"])
def test_each_switch_off_is_404_and_opens_no_session(
    world: _World, monkeypatch: pytest.MonkeyPatch, off: str,
) -> None:
    monkeypatch.setattr(get_settings(), off, "")
    r = _unshare("not an address")
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0 and world.calls == []


def test_the_switches_come_first_in_the_handler() -> None:
    body = _handler().body[1:]  # the docstring
    tests = [ast.unparse(s.test) for s in body[:2] if isinstance(s, ast.If)]
    assert tests == ["not flags.studio_allowed(user.organization_id)",
                     "not package_host_allowed(user.organization_id)"]


def test_a_bad_id_is_404_and_opens_no_session(world: _World) -> None:
    r = _unshare(pid="not-a-uuid")
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0


# ── 2. The right (D180.3) ──────────────────────────────────────────────────


def test_a_member_without_the_share_verb_gets_403_before_a_session(world: _World) -> None:
    r = _unshare(grants=STUDIO)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "forbidden", "message": packages.NO_UNSHARE_RIGHT}
    assert world.sessions == 0 and world.calls == [] and world.audits == []


def test_the_right_comes_before_the_address_and_the_session_in_the_source() -> None:
    lines = _lines(_handler())
    assert lines["_package_id"] < lines["user.has_permission"]
    assert lines["user.has_permission"] < lines["promotion.person_address"]
    assert lines["promotion.person_address"] < lines["_tenant_session"]


# ── 3. The address ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("person", "code"), [
    ("not an address", "bad_people"),
    ("group:ops", "not_a_person"),
    ("org", "not_a_person"),
    ("agent:bot@j2b5.test", "not_a_person"),
    (OWEN, "own_address"),
    (" owen@J2B5.test ", "own_address"),
])
def test_a_bad_address_is_the_store_422_before_a_session(world: _World, person: str,
                                                         code: str) -> None:
    r = _unshare(person)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == code
    assert world.sessions == 0 and world.calls == [] and world.audits == []


def test_the_address_is_normalised_as_the_share_does(world: _World) -> None:
    r = _unshare(" ANA@J2B5.test ")
    assert r.status_code == 200, r.text
    assert world.unshares == [{"install_id": INSTALL, "email": ANA.lower(), "sharer": OWEN}]


# ── 4. One session, in order ───────────────────────────────────────────────


def test_an_unshare_locks_finds_unshares_and_audits_in_one_session(world: _World) -> None:
    r = _unshare()
    assert (r.status_code, r.json()) == (200, {"removed": True})
    assert world.calls == ["lock", f"find:{OWEN}", "unshare", "audit"]
    assert world.sessions == 1


def test_the_lock_comes_before_the_unshare_in_the_source() -> None:
    """``_owned_private_install`` takes the ``apps`` row ``FOR KEY SHARE``,
    then finds the install. The unshare takes the org lock after it, and the
    audit row follows in the same session."""
    lines = _lines(_handler())
    assert lines["_tenant_session"] < lines["_owned_private_install"]
    assert lines["_owned_private_install"] < lines["promotion.unshare_person"]
    assert lines["promotion.unshare_person"] < lines["record_app_audit_in"]
    tree = ast.parse(Path(packages.__file__).read_text(encoding="utf-8"))
    owner = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                 and n.name == "_owned_private_install")
    inner = _lines(owner)
    assert inner["store.lock_for_install"] < inner["host_store.private_install_id"]


def test_the_writer_takes_the_org_lock_and_the_row_lock_before_the_delete() -> None:
    """The writer calls ``locked_install`` (the org lock, then the row FOR
    UPDATE), as the share does, and runs the DELETE after it."""
    tree = ast.parse(Path(promotion.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
              and n.name == "unshare_person")
    lines = _lines(fn)
    assert lines["locked_install"] < lines["db.execute"]
    assert "refuse_unless_kept_private" not in ast.unparse(fn)


# ── 5. Who reaches the unshare ─────────────────────────────────────────────


def test_a_package_of_another_member_is_404_and_unshares_nothing(world: _World) -> None:
    r = _unshare(email=BEN)
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.calls == ["lock"] and world.audits == []


def test_an_owner_with_no_private_install_gets_409(world: _World) -> None:
    world.install = None
    r = _unshare()
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {"code": "private_only", "message": packages.NO_PRIVATE_SHARE}
    assert world.calls == ["lock", f"find:{OWEN}"] and world.audits == []


# ── 6. The sharer is the session member (R5) ───────────────────────────────


def test_no_query_names_the_sharer_the_org_or_the_install(world: _World) -> None:
    r = _unshare(query=f"?sharer={BEN}&granted_by={BEN}&install_id=x"
                       f"&organization_id={uuid.uuid4()}")
    assert r.status_code == 200, r.text
    assert world.unshares == [{"install_id": INSTALL, "email": ANA.lower(), "sharer": OWEN}]
    params = [a.arg for a in _handler().args.args]
    assert params == ["package_id", "email", "user"]


# ── 7. The refusals of the store ───────────────────────────────────────────


@pytest.mark.parametrize(("refusal", "status"), [
    (host_store.PromotionRefused("not_private", "Only a private install has a share."), 409),
    (host_store.NotTheOwner("not_owner", "Only the owner can take a share back."), 403),
    (host_store.InstallNotFound("install_not_found", "No install has this id."), 404),
])
def test_a_store_refusal_is_its_detail_with_its_status(
    world: _World, refusal: host_store.InstallError, status: int,
) -> None:
    world.refuse = refusal
    r = _unshare()
    assert r.status_code == status, r.text
    assert r.json()["detail"] == {"code": refusal.code, "message": str(refusal)}
    assert world.calls == ["lock", f"find:{OWEN}", "unshare"]
    assert world.audits == []


# ── 8. The answer and the audit row ────────────────────────────────────────


def test_a_person_with_no_share_is_200_removed_false(world: _World) -> None:
    world.removed = False
    r = _unshare(BEN)
    assert (r.status_code, r.json()) == (200, {"removed": False})
    [row] = world.audits
    assert row["detail"]["removed"] is False


def test_the_audit_row_holds_the_person_and_no_value(world: _World) -> None:
    assert _unshare().status_code == 200
    [row] = world.audits
    assert row == {"app_id": PID, "user_email": OWEN, "kind": "action",
                   "detail": {"op": "package.unshare", "install_id": INSTALL,
                              "removed": True, "person": ANA.lower()}}


# ── 9. The DELETE of the writer (text fence) ──────────────────────────────


def test_the_delete_takes_only_the_row_of_this_sharer_for_this_person() -> None:
    sql = promotion._UNSHARE_PERSON_SQL
    assert sql.startswith("DELETE FROM app_grants AS g USING package_install ")
    assert sql.count(BOUND) == 2
    assert "package_install.id = CAST(:install AS uuid)" in sql
    assert "package_install.scope_kind = 'private'" in sql
    assert "AND lower(g.subject) = :person " in sql
    assert "AND lower(g.granted_by) = :sharer " in sql
    assert f"AND {sharing.SHARE_ROW} RETURNING" in sql


# ── 10. The R8 twin runs in CI ─────────────────────────────────────────────


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_list(workflow: str) -> None:
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", text)
    assert block, f"{workflow}: the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_studio_package_unshare_routes_r8\.py(?: \\)?$",
                     block.group(1), re.M), workflow
