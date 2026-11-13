"""WS-STU J2b-2 — the share of a Studio app with named people, with no database.

Spec: ``project-docs/specs/ai_studio.md`` §14 J2b-2 and §9 item 3, and
``package_host.md`` §6.2 ("The rules of a promotion"). Decision: D180.3
(``studio:publish`` gates the share to others). Code: the route
``POST /studio/packages/{id}/share`` (``routes/studio/packages.py``
``share_package``), ``_owned_private_install`` (the owner rule of PKG-10d)
and ``package_host/promotion.share_with_people`` (PKG-11a). The R8 twin is
``tests/unit/test_studio_package_share_routes_r8.py``.

The checks here:

1. The two switches answer 404 and open no session. They come first.
2. ``studio:publish`` is checked before any session and any body read. A
   refusal is 403 ``forbidden``, and nothing is locked or written.
3. One session, in order: the ``apps`` row lock, the find of the private
   install, the share, then the audit row. The lock comes before the share
   (``promotion.share_with_people`` docstring: else a share and a publish
   can deadlock).
4. A package of another member is 404. An owner with no private install is
   409 ``private_only``. A body that is not a JSON object is 422.
5. The sharer is the session member. No body key names the sharer, the org
   or the install (R5).
6. A store refusal is its ``detail_for``, with its status, and
   ``shared_by_another_owner`` is 409. A refusal writes no audit row.
7. The audit row holds the op, the install, the count and the addresses,
   and no value of the app.
8. The R8 twin is in the ``--must-run`` lists of both workflows.

**R7.** Each mutation ran on 2026-11-13 on this file (34 tests). M1: drop
the ``studio:publish`` check. Check 2 goes red (2 failed). M3: pass a body
key as the sharer. Check 5 goes red (1 failed). M4: drop the 409 of
``shared_by_another_owner``. Check 6 goes red (1 failed). M2 (call the
share before ``_owned_private_install``) was not run. The source-order
test of check 3 reads the line of each call.
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
from gateway.package_host import promotion
from gateway.package_host import store as host_store
from gateway.routes.studio import actions, packages
from gateway.studio import packages_store as store

ROOT = Path(__file__).resolve().parents[2]
ORG = str(uuid.uuid4())
OWEN = "Owen@j2b2.test"
OWEN_LOW = OWEN.lower()
ANA = "Ana@j2b2.test"
BEN = "ben@j2b2.test"
PID = str(uuid.uuid4())
INSTALL = str(uuid.uuid4())
STUDIO = ["feature:studio"]
PUBLISHER = ["feature:studio", "studio:publish"]
NOT_FOUND = {"detail": "Not found"}


# ── The fake world ─────────────────────────────────────────────────────────


class _World:
    """The calls that the route makes, in order. ``owned`` makes the lock
    find the package, ``install`` is the private install of the owner,
    ``refuse`` is a store refusal of the share, and ``audits`` holds each
    audit row. ``shares`` holds the arguments of each share."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.sessions = 0
        self.owned = True
        self.install: str | None = INSTALL
        self.refuse: host_store.InstallError | None = None
        self.audits: list[dict[str, Any]] = []
        self.shares: list[dict[str, Any]] = []


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

    async def share(db: Any, *, install_id: str, emails: Any, sharer: str) -> tuple[str, ...]:
        w.calls.append("share")
        w.shares.append({"install_id": install_id, "emails": emails, "sharer": sharer})
        if w.refuse is not None:
            raise w.refuse
        # The real check of the list, so a bad list is the store's own 422.
        return promotion._people(emails, host_store._member(sharer))

    async def audit_in(db: Any, **kw: Any) -> None:
        w.calls.append("audit")
        w.audits.append(kw)

    monkeypatch.setattr(packages, "_tenant_session", session)
    monkeypatch.setattr(packages, "record_app_audit_in", audit_in)
    monkeypatch.setattr(store, "lock_for_install", lock_for_install)
    monkeypatch.setattr(host_store, "private_install_id", private_install_id)
    monkeypatch.setattr(promotion, "share_with_people", share)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    return w


def _client(grants: list[str], email: str = OWEN) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=ORG, access=build_access(grants))
    return TestClient(app)


def _share(body: Any, grants: list[str] = PUBLISHER, email: str = OWEN,
           pid: str = PID) -> Any:
    return _client(grants, email).post(f"/studio/packages/{pid}/share", json=body)


def _handler() -> ast.AsyncFunctionDef:
    tree = ast.parse(Path(packages.__file__).read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "share_package")


# ── 1. The switches ────────────────────────────────────────────────────────


@pytest.mark.parametrize("off", ["studio_orgs", "package_host_orgs"])
def test_each_switch_off_is_404_and_opens_no_session(
    world: _World, monkeypatch: pytest.MonkeyPatch, off: str,
) -> None:
    monkeypatch.setattr(get_settings(), off, "")
    r = _client(PUBLISHER).post(f"/studio/packages/{PID}/share", content=b"{not json",
                                headers={"content-type": "application/json"})
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0 and world.calls == []


def test_the_switches_come_first_in_the_handler() -> None:
    body = _handler().body[1:]  # the docstring
    tests = [ast.unparse(s.test) for s in body[:2] if isinstance(s, ast.If)]
    assert tests == ["not flags.studio_allowed(user.organization_id)",
                     "not package_host_allowed(user.organization_id)"]


def test_a_bad_id_is_404_and_opens_no_session(world: _World) -> None:
    r = _share({"people": [ANA]}, pid="not-a-uuid")
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0


# ── 2. The right (D180.3) ──────────────────────────────────────────────────


def test_a_member_without_the_share_verb_gets_403_before_a_session(world: _World) -> None:
    r = _share({"people": [ANA]}, grants=STUDIO)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "forbidden",
                                  "message": packages.NO_SHARE_PEOPLE_RIGHT}
    assert world.sessions == 0 and world.calls == [] and world.audits == []


def test_the_right_comes_before_the_body_and_the_session_in_the_source() -> None:
    lines = {ast.unparse(c.func): c.lineno for c in ast.walk(_handler())
             if isinstance(c, ast.Call)}
    assert lines["user.has_permission"] < lines["_raw_body"] < lines["_tenant_session"]


def test_a_body_past_the_cap_is_413_and_opens_no_session(world: _World) -> None:
    big = b'{"people": ["' + b"x" * packages.MAX_SHARE_BODY_BYTES + b'"]}'
    r = _client(PUBLISHER).post(f"/studio/packages/{PID}/share", content=big,
                                headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert world.sessions == 0


def test_the_cap_holds_the_most_people_of_one_share() -> None:
    most = promotion.MAX_PEOPLE * (host_store.MAX_SUBJECT_BYTES + 4) + 64
    assert most <= packages.MAX_SHARE_BODY_BYTES


# ── 3. One session, in order ───────────────────────────────────────────────


def test_a_share_locks_finds_shares_and_audits_in_one_session(world: _World) -> None:
    r = _share({"people": [ANA, BEN]})
    assert r.status_code == 200, r.text
    assert r.json() == {"people": [ANA.lower(), BEN]}
    assert world.calls == ["lock", f"find:{OWEN}", "share", "audit"]
    assert world.sessions == 1


def test_the_lock_comes_before_the_share_in_the_source() -> None:
    """``_owned_private_install`` takes the ``apps`` row ``FOR KEY SHARE``,
    then finds the install. The share takes the org lock after it, and the
    audit row follows in the same session."""
    lines = {ast.unparse(c.func): c.lineno for c in ast.walk(_handler())
             if isinstance(c, ast.Call)}
    assert lines["_tenant_session"] < lines["_owned_private_install"]
    assert lines["_owned_private_install"] < lines["promotion.share_with_people"]
    assert lines["promotion.share_with_people"] < lines["record_app_audit_in"]
    tree = ast.parse(Path(packages.__file__).read_text(encoding="utf-8"))
    owner = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                 and n.name == "_owned_private_install")
    inner = {ast.unparse(c.func): c.lineno for c in ast.walk(owner) if isinstance(c, ast.Call)}
    assert inner["store.lock_for_install"] < inner["host_store.private_install_id"]


# ── 4. Who reaches the share ───────────────────────────────────────────────


def test_a_package_of_another_member_is_404_and_shares_nothing(world: _World) -> None:
    r = _share({"people": [ANA]}, email=BEN)
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.calls == ["lock"] and world.audits == []


def test_an_owner_with_no_private_install_gets_409(world: _World) -> None:
    world.install = None
    r = _share({"people": [ANA]})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {"code": "private_only", "message": packages.NO_PRIVATE_SHARE}
    assert world.calls == ["lock", f"find:{OWEN}"] and world.audits == []


@pytest.mark.parametrize("raw", [b"[1]", b"{not json", b""])
def test_a_body_that_is_not_a_json_object_is_422(world: _World, raw: bytes) -> None:
    r = _client(PUBLISHER).post(f"/studio/packages/{PID}/share", content=raw,
                                headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text
    assert "share" not in world.calls and world.audits == []


@pytest.mark.parametrize(("body", "code"), [
    ({}, "bad_people"),
    ({"people": ANA}, "bad_people"),
    ({"people": []}, "no_people"),
    ({"people": [7]}, "bad_people"),
    ({"people": ["group:ops"]}, "not_a_person"),
    ({"people": ["org"]}, "not_a_person"),
    ({"people": [OWEN]}, "own_address"),
    ({"people": [f"p{i}@j2b2.test" for i in range(promotion.MAX_PEOPLE + 1)]},
     "too_many_people"),
])
def test_a_bad_list_is_the_store_422(world: _World, body: dict[str, Any], code: str) -> None:
    r = _share(body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == code
    assert world.audits == []


# ── 5. The sharer is the session member (R5) ───────────────────────────────


def test_no_body_key_names_the_sharer_the_org_or_the_install(world: _World) -> None:
    r = _share({"people": [ANA], "sharer": BEN, "granted_by": BEN, "install_id": "x",
                "organization_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    assert world.shares == [{"install_id": INSTALL, "emails": [ANA], "sharer": OWEN}]
    source = ast.unparse(_handler())
    assert 'body.get("people")' in source.replace("'", '"')
    for key in ("sharer", "granted_by", "install_id", "organization_id"):
        assert f'body.get("{key}")' not in source.replace("'", '"'), key
        assert f'body["{key}"]' not in source.replace("'", '"'), key


# ── 6. The refusals of the store ───────────────────────────────────────────


@pytest.mark.parametrize(("refusal", "status"), [
    (promotion.ShareRefused("not_a_member", "Each person must be an active member."), 422),
    (promotion.ShareRefused("shared_by_another_owner", "Another member shares it."), 409),
    (host_store.PromotionRefused("not_active", "An install that is paused cannot move."), 409),
    (host_store.PromotionRefused("not_kept", "Keep the install first."), 409),
    (host_store.NotTheOwner("not_owner", "Only the owner can share it."), 403),
    (host_store.InstallNotFound("install_not_found", "No install has this id."), 404),
])
def test_a_store_refusal_is_its_detail_with_its_status(
    world: _World, refusal: host_store.InstallError, status: int,
) -> None:
    world.refuse = refusal
    r = _share({"people": [ANA]})
    assert r.status_code == status, r.text
    assert r.json()["detail"] == {"code": refusal.code, "message": str(refusal)}
    assert world.calls == ["lock", f"find:{OWEN}", "share"]
    assert world.audits == []


def test_only_the_share_of_another_owner_moves_to_409() -> None:
    assert packages.SHARE_TAKEN == "shared_by_another_owner"
    assert promotion.ShareRefused.status == 422


# ── 7. The audit row ───────────────────────────────────────────────────────


def test_the_audit_row_holds_the_addresses_and_no_value(world: _World) -> None:
    assert _share({"people": [ANA, BEN, ANA]}).status_code == 200
    [row] = world.audits
    assert row == {"app_id": PID, "user_email": OWEN, "kind": "action",
                   "detail": {"op": "package.share", "install_id": INSTALL, "count": 2,
                              "people": [ANA.lower(), BEN]}}


# ── 8. The R8 twin runs in CI ──────────────────────────────────────────────


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_list(workflow: str) -> None:
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", text)
    assert block, f"{workflow}: the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_studio_package_share_routes_r8\.py(?: \\)?$",
                     block.group(1), re.M), workflow
