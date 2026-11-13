"""WS-STU J2b-1 — the routes of the approval for the org, with no database.

Spec: ``project-docs/specs/ai_studio.md`` §9 item 3 and the J2b-1 row of
§14, and ``package_host.md`` §6.2 ("The approval record"). The owner chose
path A on 2026-11-13 (task 1200). Code: ``gateway/routes/studio/packages.py``
(``request_approval``, ``list_approvals``, ``approve_approval``,
``reject_approval`` and ``_review_errors``) over
``gateway/studio/packages_store.py`` (``request_org_approval``,
``approve_version``, ``reject_version`` and ``list_pending_versions``). The
R8 twin is ``tests/unit/test_studio_package_approval_routes_r8.py``.

The checks here:

1. Each route reads ``STUDIO_ORGS``, then ``PACKAGE_HOST_ORGS``. An org
   outside either gets 404, and no session opens.
2. The owner asks: 202 ``pending``. A repeat answers the same. A decided
   version is 409 ``review_conflict``. No such version is 404.
3. The list: a member without ``studio:publish`` gets 403 and no session.
   A holder gets each pending row with its full manifest and its hash.
4. The approve and the reject pass ``has_permission(studio:publish)`` to the
   store as ``can_publish``. ``ApprovalRefused`` is 403. A version that
   nobody asked for (``auto``) is 404, never 409 (review P3). Each other
   ``ApprovalConflict`` is 409 with the state.
5. The read of the store refuses a caller without the right before any
   statement, filters on the bound org and the state, and has no member
   filter.
6. The R8 twin is in the ``--must-run`` lists of both workflows.

**R7.** M1: map each ``ApprovalConflict`` to 409. Check 4 goes red on the
``auto`` case. M2: drop the right check of the list. Check 3 goes red. M3:
pass ``True`` to the store for each caller. Check 4 goes red.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.studio import actions, packages
from gateway.studio import packages_store as store

ROOT = Path(__file__).resolve().parents[2]
ORG = str(uuid.uuid4())
OWEN = "owen@j2b1.test"
ADA = "ada@j2b1.test"
PID = str(uuid.uuid4())
STUDIO = ["feature:studio"]
PUBLISHER = ["feature:studio", "studio:publish"]
NOT_FOUND = {"detail": "Not found"}
WHEN = datetime(2026, 11, 13, tzinfo=UTC)


class _World:
    """The store calls that the routes make. ``raise_`` is an exception that
    the next store call raises. ``found`` is False when the store finds no
    version."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.sessions = 0
        self.raise_: Exception | None = None
        self.found = True
        self.review = "pending"
        self.more = False


def _version(pid: str, v: int, state: str) -> store.PackageVersion:
    return store.PackageVersion(
        package_id=pid, version=v, manifest=None, bundle_sha256="0", release_notes="",
        published_by=OWEN, published_at=WHEN, review_status=state)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    w = _World()

    @asynccontextmanager
    async def session() -> Any:
        w.sessions += 1
        yield object()

    def _out(pid: str, v: int, state: str) -> Any:
        if w.raise_ is not None:
            raise w.raise_
        return _version(pid, v, state) if w.found else None

    async def ask(db: Any, member: str, pid: str, version: int) -> Any:
        w.calls.append(("ask", member, pid, version))
        return _out(pid, version, "pending")

    async def approve(db: Any, who: str, pid: str, version: int, *, can_publish: Any) -> Any:
        w.calls.append(("approve", who, pid, version, can_publish))
        return _out(pid, version, "approved")

    async def reject(db: Any, who: str, pid: str, version: int, *, can_publish: Any) -> Any:
        w.calls.append(("reject", who, pid, version, can_publish))
        return _out(pid, version, "rejected")

    async def pending(db: Any, *, can_publish: Any) -> Any:
        w.calls.append(("list", can_publish))
        return [store.PendingVersion(
            package_id=PID, name="Visits", owner_email=OWEN, version=2, published_by=OWEN,
            published_at=WHEN, manifest={"id": "visits", "name": "Visits"},
            requested_by=OWEN, requested_at=WHEN)], w.more

    monkeypatch.setattr(packages, "_tenant_session", session)
    monkeypatch.setattr(store, "request_org_approval", ask)
    monkeypatch.setattr(store, "approve_version", approve)
    monkeypatch.setattr(store, "reject_version", reject)
    monkeypatch.setattr(store, "list_pending_versions", pending)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    return w


def _client(grants: list[str], email: str = OWEN) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=ORG, access=build_access(grants))
    return TestClient(app)


_BASE = f"/studio/packages/{PID}/versions/2/approval"
ROUTES = [("POST", _BASE), ("GET", "/studio/approvals?state=pending"),
          ("POST", f"{_BASE}/approve"), ("POST", f"{_BASE}/reject")]


# ── 1. The switches ────────────────────────────────────────────────────────


@pytest.mark.parametrize("off", ["studio_orgs", "package_host_orgs"])
@pytest.mark.parametrize(("method", "url"), ROUTES)
def test_each_switch_off_is_404_and_opens_no_session(
    world: _World, monkeypatch: pytest.MonkeyPatch, off: str, method: str, url: str,
) -> None:
    monkeypatch.setattr(get_settings(), off, "")
    r = _client(PUBLISHER, ADA).request(method, url)
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0 and world.calls == []


@pytest.mark.parametrize("bad", ["0", "-1", "x", "1.0", "99999999999"])
@pytest.mark.parametrize("tail", ["", "/approve", "/reject"])
def test_a_bad_version_is_422_and_opens_no_session(world: _World, bad: str, tail: str) -> None:
    r = _client(PUBLISHER, ADA).post(f"/studio/packages/{PID}/versions/{bad}/approval{tail}")
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["errors"][0]["path"] == "version"
    assert world.sessions == 0 and world.calls == []


def test_a_bad_id_is_404_and_opens_no_session(world: _World) -> None:
    r = _client(PUBLISHER, ADA).post("/studio/packages/nope/versions/2/approval/approve")
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0


# ── 2. The owner asks ──────────────────────────────────────────────────────


def test_the_owner_asks_and_gets_202_pending(world: _World) -> None:
    """The member is the session member, never a value of the request. The
    ask reads no right: the owner asks, and a holder decides."""
    r = _client(STUDIO).post(_BASE, json={"member": ADA, "organization_id": "x"})
    assert r.status_code == 202, r.text
    assert r.json() == {"package_id": PID, "version": 2, "review_status": "pending"}
    assert world.calls == [("ask", OWEN, PID, 2)]
    again = _client(STUDIO).post(_BASE)
    assert (again.status_code, again.json()) == (202, r.json())


def test_an_ask_of_no_such_version_is_404(world: _World) -> None:
    world.found = False
    r = _client(STUDIO, ADA).post(_BASE)
    assert (r.status_code, r.json()) == (404, NOT_FOUND)


@pytest.mark.parametrize("state", ["approved", "rejected"])
def test_an_ask_of_a_decided_version_is_409(world: _World, state: str) -> None:
    world.raise_ = store.ApprovalConflict("decided", state)
    r = _client(STUDIO).post(_BASE)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "review_conflict"
    assert r.json()["detail"]["review_status"] == state


# ── 3. The list of an approver ─────────────────────────────────────────────


@pytest.mark.parametrize("url", ["/studio/approvals", "/studio/approvals?state=pending"])
def test_a_member_without_the_right_gets_403_and_no_session(world: _World, url: str) -> None:
    r = _client(STUDIO, ADA).get(url)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "forbidden", "message": packages.NO_REVIEW_RIGHT}
    assert world.sessions == 0 and world.calls == []


def test_a_holder_reads_each_pending_version_with_its_full_manifest(world: _World) -> None:
    world.more = True
    r = _client(PUBLISHER, ADA).get("/studio/approvals?state=pending")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "pending" and body["truncated"] is True
    [row] = body["approvals"]
    assert row["manifest"] == {"id": "visits", "name": "Visits"}
    assert row["manifest_hash"] == packages.manifest_hash(row["manifest"])
    assert (row["package_id"], row["version"], row["owner_email"], row["requested_by"]) == (
        PID, 2, OWEN, OWEN)
    assert world.calls == [("list", True)]


@pytest.mark.parametrize("state", ["approved", "rejected", "auto", "PENDING", ""])
def test_another_state_is_422(world: _World, state: str) -> None:
    r = _client(PUBLISHER, ADA).get(f"/studio/approvals?state={state}")
    assert r.status_code == 422, r.text
    assert world.sessions == 0


# ── 4. The approve and the reject ──────────────────────────────────────────


@pytest.mark.parametrize("move", ["approve", "reject"])
@pytest.mark.parametrize(("grants", "can"), [(PUBLISHER, True), (STUDIO, False)])
def test_the_route_passes_the_check_of_the_right_to_the_store(
    world: _World, move: str, grants: list[str], can: bool,
) -> None:
    r = _client(grants, ADA).post(f"{_BASE}/{move}")
    assert world.calls == [(move, ADA, PID, 2, can)]
    assert r.status_code == 200, r.text
    assert r.json()["review_status"] == ("approved" if move == "approve" else "rejected")


@pytest.mark.parametrize("move", ["approve", "reject"])
def test_a_refusal_of_the_store_is_403(world: _World, move: str) -> None:
    world.raise_ = store.ApprovalRefused(store.SELF_APPROVAL)
    r = _client(PUBLISHER).post(f"{_BASE}/{move}")
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "forbidden", "message": store.SELF_APPROVAL}


@pytest.mark.parametrize("move", ["approve", "reject"])
def test_a_version_that_nobody_asked_for_is_404_not_409(world: _World, move: str) -> None:
    """Review P3: a 409 on an ``auto`` version would tell a holder that a
    private package of another member exists."""
    world.raise_ = store.ApprovalConflict("auto", "auto")
    r = _client(PUBLISHER, ADA).post(f"{_BASE}/{move}")
    assert (r.status_code, r.json()) == (404, NOT_FOUND)


@pytest.mark.parametrize("move", ["approve", "reject"])
@pytest.mark.parametrize("state", ["approved", "rejected", None])
def test_a_requested_version_in_another_state_is_409(
    world: _World, move: str, state: str | None,
) -> None:
    world.raise_ = store.ApprovalConflict("moved", state)
    r = _client(PUBLISHER, ADA).post(f"{_BASE}/{move}")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "review_conflict"
    assert r.json()["detail"]["review_status"] == state


@pytest.mark.parametrize("move", ["approve", "reject"])
def test_no_such_version_is_404(world: _World, move: str) -> None:
    world.found = False
    r = _client(PUBLISHER, ADA).post(f"{_BASE}/{move}")
    assert (r.status_code, r.json()) == (404, NOT_FOUND)


# ── 5. The read of the store ───────────────────────────────────────────────


class _NoDb:
    async def execute(self, *_a: Any, **_k: Any) -> Any:
        raise AssertionError("the read ran a statement")


@pytest.mark.parametrize("can", [False, None, 1, "yes"])
def test_the_read_refuses_without_the_right_before_any_statement(can: Any) -> None:
    with pytest.raises(store.ApprovalRefused):
        asyncio.run(store.list_pending_versions(_NoDb(), can_publish=can))


@pytest.mark.parametrize("limit", [0, -1, True, store.MAX_PENDING_PAGE + 1, "5"])
def test_the_read_refuses_a_bad_limit(limit: Any) -> None:
    with pytest.raises(store.PackageStoreError):
        asyncio.run(store.list_pending_versions(_NoDb(), can_publish=True, limit=limit))


def test_the_read_names_the_bound_org_and_the_pending_state_only() -> None:
    sql = store._PENDING_SQL
    assert sql.count(f"organization_id = {store._BOUND_ORG}") == 3
    assert "v.review_status = 'pending'" in sql
    assert "a.format = 'manifest'" in sql
    assert "owner_email = :m" not in sql and ":org" not in sql
    assert "FOR UPDATE" not in sql and sql.rstrip().endswith("LIMIT :limit")


def test_the_read_answers_a_page_and_says_when_more_wait() -> None:
    rows = [(PID, "Visits", OWEN, n, OWEN, WHEN, '{"id": "v"}', None, None)
            for n in range(1, 4)]

    seen: list[dict[str, Any]] = []

    class _Db:
        async def execute(self, stmt: Any, params: Any) -> Any:
            seen.append(params)
            return type("R", (), {"all": lambda self: rows[:params["limit"]]})()

    got, more = asyncio.run(store.list_pending_versions(_Db(), can_publish=True, limit=2))
    assert seen == [{"op": store.REVIEW_OP, "limit": 3}]
    assert [g.version for g in got] == [1, 2] and more is True
    assert got[0].manifest == {"id": "v"} and got[0].requested_by == OWEN


# ── 6. The R8 twin runs in CI ──────────────────────────────────────────────


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_list(workflow: str) -> None:
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", text)
    assert block, f"{workflow}: the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_studio_package_approval_routes_r8\.py(?: \\)?$",
                     block.group(1), re.M), workflow
