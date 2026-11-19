"""WS-STU J2c (WS-TOF TOF-4a) — the share of a skill with the org, with no
database.

Spec: ``project-docs/specs/ai_studio_rows/J2c.md``. Code:
``gateway/routes/studio/skills.py`` (``request_skill_share``,
``list_skill_shares``, ``approve_skill_share``, ``reject_skill_share`` and
``unshare_skill``) over ``gateway/studio/skills_store.py``
(``request_share``, ``approve_share``, ``reject_share``, ``unshare``,
``list_pending_shares`` and the loadable reads). The R8 twin is
``tests/unit/test_studio_skill_share_r8.py``.

The checks here:

1. Each route reads ``STUDIO_ORGS`` first. An org outside it gets 404, and
   no session opens.
2. The routes take the member from the session and pass the check of
   ``studio:publish`` to the store as ``can_publish``. ``ShareRefused`` is
   403, ``ShareConflict`` is 409 ``share_conflict``, and None is 404.
3. The list: a member without ``studio:publish`` gets 403 and no session.
4. The store on a fake database: a second ask writes nothing, a decided
   version cannot be asked again, the owner cannot approve, an ask that
   nobody made is None (404), and an unshare returns ``private``.
5. ``system``: no route takes a visibility, and the store writes ``org`` in
   the approve only and ``private`` in the unshare only. No statement names
   ``system``, and the CHECK of the table holds it out.
6. The pinned version: the loadable reads join the version through
   ``_LOADABLE_VERSION``, which gives the owner ``current_version`` and each
   other member ``shared_version``.

**R7.** M1: in ``skills_store._decide_share``, drop the owner check. Check 4
(``test_the_owner_cannot_approve_the_share``) goes red, and the R8 twin too.
M2: in ``_LOADABLE_VERSION``, read ``s.current_version`` for each member.
Check 6 goes red, and ``test_r8_a_later_version_reaches_no_other_member``.
M3: in ``list_skill_shares``, drop the right check. Check 3 goes red.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.unit import _r8_lists as r8_lists

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from gateway.routes.studio import actions, skills
from gateway.studio import skills_store as store

ROOT = Path(__file__).resolve().parents[2]
ORG = str(uuid.uuid4())
OWEN = "owen@j2c.test"
ADA = "ada@j2c.test"
SID = str(uuid.uuid4())
STUDIO = ["feature:studio"]
PUBLISHER = ["feature:studio", "studio:publish"]
NOT_FOUND = {"detail": "Not found"}
WHEN = datetime(2026, 11, 18, tzinfo=UTC)


def _share(**kw: Any) -> store.Share:
    base = store.Share(
        skill_id=SID, owner_email=OWEN, visibility="private", shared_version=None,
        share_state="pending", share_version=2, requested_by=OWEN, requested_at=WHEN,
        decided_by=None, decided_at=None)
    return replace(base, **kw)


class _World:
    """The store calls that the routes make."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.sessions = 0
        self.raise_: Exception | None = None
        self.found = True
        self.more = False


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    w = _World()

    @asynccontextmanager
    async def session() -> Any:
        w.sessions += 1
        yield object()

    def _out(**kw: Any) -> Any:
        if w.raise_ is not None:
            raise w.raise_
        return _share(**kw) if w.found else None

    async def ask(db: Any, member: str, sid: str, version: int) -> Any:
        w.calls.append(("ask", member, sid, version))
        return _out()

    async def approve(db: Any, who: str, sid: str, version: int, *, can_publish: Any) -> Any:
        w.calls.append(("approve", who, sid, version, can_publish))
        return _out(share_state="approved", visibility="org", shared_version=version)

    async def reject(db: Any, who: str, sid: str, version: int, *, can_publish: Any) -> Any:
        w.calls.append(("reject", who, sid, version, can_publish))
        return _out(share_state="rejected")

    async def unshare(db: Any, who: str, sid: str, *, can_publish: Any) -> Any:
        w.calls.append(("unshare", who, sid, can_publish))
        return _out(share_state=None, visibility="private")

    async def pending(db: Any, *, can_publish: Any) -> Any:
        w.calls.append(("list", can_publish))
        return [store.PendingShare(
            skill_id=SID, name="Quotes", slug="quotes", description="", owner_email=OWEN,
            version=2, requested_by=OWEN, requested_at=WHEN,
            body={"description": "d", "instructions": "i"}, content_hash="0" * 64)], w.more

    monkeypatch.setattr(skills, "_tenant_session", session)
    monkeypatch.setattr(store, "request_share", ask)
    monkeypatch.setattr(store, "approve_share", approve)
    monkeypatch.setattr(store, "reject_share", reject)
    monkeypatch.setattr(store, "unshare", unshare)
    monkeypatch.setattr(store, "list_pending_shares", pending)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    return w


def _client(grants: list[str], email: str = OWEN) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=ORG, access=build_access(grants))
    return TestClient(app)


_BASE = f"/studio/skills/{SID}/versions/2/share"
ROUTES = [("POST", _BASE), ("GET", "/studio/skill-shares?state=pending"),
          ("POST", f"{_BASE}/approve"), ("POST", f"{_BASE}/reject"),
          ("DELETE", f"/studio/skills/{SID}/share")]


# ── 1. The switch ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(("method", "url"), ROUTES)
def test_the_switch_off_is_404_and_opens_no_session(
    world: _World, monkeypatch: pytest.MonkeyPatch, method: str, url: str,
) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", "")
    r = _client(PUBLISHER, ADA).request(method, url)
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0 and world.calls == []


@pytest.mark.parametrize("bad", ["0", "-1", "x", "1.0"])
@pytest.mark.parametrize("tail", ["", "/approve", "/reject"])
def test_a_bad_version_is_422_and_opens_no_session(world: _World, bad: str, tail: str) -> None:
    r = _client(PUBLISHER, ADA).post(f"/studio/skills/{SID}/versions/{bad}/share{tail}")
    assert r.status_code == 422, r.text
    assert world.sessions == 0 and world.calls == []


def test_a_bad_id_is_422_and_opens_no_session(world: _World) -> None:
    r = _client(PUBLISHER, ADA).delete("/studio/skills/nope/share")
    assert r.status_code == 422, r.text
    assert world.sessions == 0


# ── 2. The routes ──────────────────────────────────────────────────────────


def test_the_owner_asks_and_gets_202_pending(world: _World) -> None:
    """The member is the session member, never a value of the request. A
    visibility in the request changes nothing. The ask reads no right."""
    r = _client(STUDIO).post(_BASE, json={"member": ADA, "visibility": "system",
                                          "organization_id": "x"})
    assert r.status_code == 202, r.text
    assert r.json()["share_state"] == "pending" and r.json()["visibility"] == "private"
    assert world.calls == [("ask", OWEN, SID, 2)]


def test_an_ask_of_no_such_version_is_404(world: _World) -> None:
    world.found = False
    r = _client(STUDIO, ADA).post(_BASE)
    assert (r.status_code, r.json()) == (404, NOT_FOUND)


@pytest.mark.parametrize("state", ["pending", "approved", "rejected"])
def test_a_conflict_of_the_store_is_409(world: _World, state: str) -> None:
    world.raise_ = store.ShareConflict("decided", state)
    r = _client(STUDIO).post(_BASE)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "share_conflict"
    assert r.json()["detail"]["share_state"] == state


@pytest.mark.parametrize("url", ["/studio/skill-shares", "/studio/skill-shares?state=pending"])
def test_a_member_without_the_right_gets_403_on_the_list_and_no_session(
    world: _World, url: str,
) -> None:
    r = _client(STUDIO, ADA).get(url)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "forbidden", "message": skills.NO_SHARE_LIST_RIGHT}
    assert world.sessions == 0 and world.calls == []


def test_a_holder_reads_each_pending_ask_with_its_body(world: _World) -> None:
    world.more = True
    r = _client(PUBLISHER, ADA).get("/studio/skill-shares?state=pending")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "pending" and body["truncated"] is True
    [row] = body["shares"]
    assert (row["skill_id"], row["version"], row["owner_email"]) == (SID, 2, OWEN)
    assert row["body"] == {"description": "d", "instructions": "i"}
    assert world.calls == [("list", True)]


@pytest.mark.parametrize("state", ["approved", "rejected", "PENDING", ""])
def test_another_state_is_422(world: _World, state: str) -> None:
    r = _client(PUBLISHER, ADA).get(f"/studio/skill-shares?state={state}")
    assert r.status_code == 422, r.text
    assert world.sessions == 0


@pytest.mark.parametrize("move", ["approve", "reject"])
@pytest.mark.parametrize(("grants", "can"), [(PUBLISHER, True), (STUDIO, False)])
def test_the_route_passes_the_check_of_the_right_to_the_store(
    world: _World, move: str, grants: list[str], can: bool,
) -> None:
    r = _client(grants, ADA).post(f"{_BASE}/{move}")
    assert world.calls == [(move, ADA, SID, 2, can)]
    assert r.status_code == 200, r.text


@pytest.mark.parametrize(("grants", "can"), [(PUBLISHER, True), (STUDIO, False)])
def test_the_unshare_passes_the_check_of_the_right_to_the_store(
    world: _World, grants: list[str], can: bool,
) -> None:
    r = _client(grants, ADA).delete(f"/studio/skills/{SID}/share")
    assert r.status_code == 200, r.text
    assert r.json()["visibility"] == "private"
    assert world.calls == [("unshare", ADA, SID, can)]


@pytest.mark.parametrize(("method", "url"), [("POST", f"{_BASE}/approve"),
                                             ("POST", f"{_BASE}/reject"),
                                             ("DELETE", f"/studio/skills/{SID}/share")])
def test_a_refusal_of_the_store_is_403(world: _World, method: str, url: str) -> None:
    world.raise_ = store.ShareRefused(store.SELF_SHARE)
    r = _client(PUBLISHER).request(method, url)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "forbidden", "message": store.SELF_SHARE}


@pytest.mark.parametrize(("method", "url"), [("POST", f"{_BASE}/approve"),
                                             ("POST", f"{_BASE}/reject"),
                                             ("DELETE", f"/studio/skills/{SID}/share")])
def test_no_such_ask_is_404(world: _World, method: str, url: str) -> None:
    world.found = False
    r = _client(PUBLISHER, ADA).request(method, url)
    assert (r.status_code, r.json()) == (404, NOT_FOUND)


# ── 4. The store on a fake database ────────────────────────────────────────


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _Db:
    """One skill row of the bound org, in the shape of the share columns.
    It answers each share statement of the store by its text, and counts the
    writes."""

    def __init__(self, owner: str = OWEN, versions: int = 3) -> None:
        self.row: dict[str, Any] = {
            "id": SID, "owner": owner, "visibility": "private", "shared": None,
            "state": None, "version": None, "req_by": None, "req_at": None,
            "dec_by": None, "dec_at": None}
        self.versions = set(range(1, versions + 1))
        self.writes = 0

    def _tuple(self) -> tuple[Any, ...]:
        r = self.row
        return (r["id"], r["owner"], r["visibility"], r["shared"], r["state"], r["version"],
                r["req_by"], r["req_at"], r["dec_by"], r["dec_at"])

    async def execute(self, stmt: Any, params: dict[str, Any]) -> _Result:
        sql = str(stmt)
        r = self.row
        if params.get("id") != r["id"]:
            return _Result([])
        if sql == store._SHARE_OWNER_SQL:
            return _Result([self._tuple()] if params["m"] == r["owner"] else [])
        if sql == store._SHARE_ANY_SQL:
            return _Result([self._tuple()])
        if sql == store._VERSION_EXISTS_SQL:
            return _Result([(1,)] if params["v"] in self.versions else [])
        self.writes += 1
        if sql == store._SHARE_ASK_SQL:
            r.update(state="pending", version=params["v"], req_by=params["m"], req_at=WHEN,
                     dec_by=None, dec_at=None)
        elif sql in (store._SHARE_APPROVE_SQL, store._SHARE_REJECT_SQL):
            if r["state"] != "pending" or r["version"] != params["v"]:
                return _Result([])
            if sql == store._SHARE_APPROVE_SQL:
                r.update(state="approved", visibility="org", shared=r["version"])
            else:
                r.update(state="rejected")
            r.update(dec_by=params["a"], dec_at=WHEN)
        elif sql == store._UNSHARE_SQL:
            r.update(visibility="private", shared=None,
                     state="rejected" if r["state"] == "rejected" else None)
        else:  # pragma: no cover - a statement that this fake does not know
            raise AssertionError(sql)
        return _Result([self._tuple()])


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_a_second_ask_writes_nothing() -> None:
    db = _Db()
    first = _run(store.request_share(db, OWEN, SID, 2))
    assert first is not None and first.share_state == "pending" and db.writes == 1
    again = _run(store.request_share(db, OWEN, SID, 2))
    assert again == first and db.writes == 1


def test_a_second_ask_of_another_version_is_a_conflict() -> None:
    db = _Db()
    _run(store.request_share(db, OWEN, SID, 2))
    with pytest.raises(store.ShareConflict) as exc:
        _run(store.request_share(db, OWEN, SID, 3))
    assert exc.value.current == "pending" and db.writes == 1


@pytest.mark.parametrize("move", ["approve", "reject"])
@pytest.mark.parametrize("asked", [1, 2])
def test_a_decided_version_cannot_be_asked_again(move: str, asked: int) -> None:
    db = _Db()
    _run(store.request_share(db, OWEN, SID, 2))
    decide = store.approve_share if move == "approve" else store.reject_share
    _run(decide(db, ADA, SID, 2, can_publish=True))
    writes = db.writes
    with pytest.raises(store.ShareConflict):
        _run(store.request_share(db, OWEN, SID, asked))
    assert db.writes == writes
    # A newer version may be asked.
    assert _run(store.request_share(db, OWEN, SID, 3)).share_state == "pending"


def test_another_member_or_no_such_version_is_none() -> None:
    db = _Db()
    assert _run(store.request_share(db, ADA, SID, 2)) is None
    assert _run(store.request_share(db, OWEN, SID, 9)) is None
    assert _run(store.request_share(db, OWEN, str(uuid.uuid4()), 1)) is None
    assert db.writes == 0


def test_the_owner_cannot_approve_the_share() -> None:
    db = _Db()
    _run(store.request_share(db, OWEN, SID, 2))
    with pytest.raises(store.ShareRefused):
        _run(store.approve_share(db, OWEN, SID, 2, can_publish=True))
    assert db.row["visibility"] == "private" and db.row["state"] == "pending"


@pytest.mark.parametrize("can", [False, None, 1, "yes"])
@pytest.mark.parametrize("fn", ["approve_share", "reject_share"])
def test_a_move_without_the_right_is_refused_before_any_statement(can: Any, fn: str) -> None:
    class _NoDb:
        async def execute(self, *_a: Any, **_k: Any) -> Any:
            raise AssertionError("the move ran a statement")

    with pytest.raises(store.ShareRefused):
        _run(getattr(store, fn)(_NoDb(), ADA, SID, 2, can_publish=can))


@pytest.mark.parametrize("can", [False, None, 1])
def test_the_list_without_the_right_is_refused_before_any_statement(can: Any) -> None:
    class _NoDb:
        async def execute(self, *_a: Any, **_k: Any) -> Any:
            raise AssertionError("the read ran a statement")

    with pytest.raises(store.ShareRefused):
        _run(store.list_pending_shares(_NoDb(), can_publish=can))


def test_an_ask_that_nobody_made_is_none_for_a_holder() -> None:
    """A 409 here would tell a holder that a private skill exists."""
    db = _Db()
    assert _run(store.approve_share(db, ADA, SID, 2, can_publish=True)) is None
    assert _run(store.reject_share(db, ADA, SID, 2, can_publish=True)) is None
    _run(store.request_share(db, OWEN, SID, 2))
    assert _run(store.approve_share(db, ADA, SID, 1, can_publish=True)) is None
    assert db.row["visibility"] == "private"


def test_an_approve_pins_the_version_and_a_reject_keeps_the_older_share() -> None:
    db = _Db()
    _run(store.request_share(db, OWEN, SID, 1))
    got = _run(store.approve_share(db, ADA, SID, 1, can_publish=True))
    assert (got.visibility, got.shared_version, got.decided_by) == ("org", 1, ADA)
    _run(store.request_share(db, OWEN, SID, 2))
    got = _run(store.reject_share(db, ADA, SID, 2, can_publish=True))
    assert (got.visibility, got.shared_version, got.share_state) == ("org", 1, "rejected")


def test_an_unshare_returns_private() -> None:
    db = _Db()
    _run(store.request_share(db, OWEN, SID, 1))
    _run(store.approve_share(db, ADA, SID, 1, can_publish=True))
    got = _run(store.unshare(db, OWEN, SID, can_publish=False))
    assert (got.visibility, got.shared_version, got.share_state) == ("private", None, None)


def test_a_holder_unshares_only_an_org_skill_and_a_member_without_the_right_is_refused() -> None:
    db = _Db()
    with pytest.raises(store.ShareRefused):
        _run(store.unshare(db, ADA, SID, can_publish=False))
    assert _run(store.unshare(db, ADA, SID, can_publish=True)) is None
    assert db.writes == 0
    _run(store.request_share(db, OWEN, SID, 1))
    _run(store.approve_share(db, "hal@j2c.test", SID, 1, can_publish=True))
    with pytest.raises(store.ShareRefused):
        _run(store.unshare(db, ADA, SID, can_publish=False))
    got = _run(store.unshare(db, ADA, SID, can_publish=True))
    assert got.visibility == "private"


# ── 5. `system` only by a reviewed migration ───────────────────────────────


_SHARE_ROUTES = ("request_skill_share", "list_skill_shares", "approve_skill_share",
                 "reject_skill_share", "unshare_skill")


def test_no_share_route_takes_a_body() -> None:
    """A route with no body cannot take a visibility from a request."""
    routes = {r.endpoint.__name__: r for r in actions.router.routes
              if isinstance(r, APIRoute) and r.endpoint.__module__ == skills.__name__}
    for name in _SHARE_ROUTES:
        assert routes[name].dependant.body_params == [], name


def test_no_skill_route_or_tool_names_system() -> None:
    """``system`` comes only through a reviewed PR: no route, store or tool
    of a skill names it as a value."""
    files = [
        ROOT / "apps/services/gateway/gateway/routes/studio/skills.py",
        ROOT / "apps/services/gateway/gateway/studio/skills_store.py",
        ROOT / "packages/acb_skills/acb_skills/studio_tools.py",
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"""["']system["']""", text), path.name
    assert "system" not in store.VISIBILITIES


def test_the_store_writes_org_in_the_approve_only_and_private_in_the_unshare_only() -> None:
    writes = {name: getattr(store, name) for name in dir(store)
              if name.endswith("_SQL") and "UPDATE studio_skills" in getattr(store, name)}
    sets_visibility = sorted(n for n, sql in writes.items() if "visibility =" in
                             sql.split("WHERE", 1)[0])
    assert sets_visibility == ["_SHARE_APPROVE_SQL", "_UNSHARE_SQL"]
    assert "visibility = 'org'" in store._SHARE_APPROVE_SQL
    assert "visibility = 'private'" in store._UNSHARE_SQL
    for sql in writes.values():
        assert ":vis" not in sql and "system" not in sql


def test_the_migration_keeps_system_out_of_the_check() -> None:
    first = next(p for p in (ROOT / "infra/postgres").glob("*.sql")
                 if "CREATE TABLE IF NOT EXISTS studio_skills (" in p.read_text(encoding="utf-8"))
    assert "visibility IN ('private', 'group', 'org')" in first.read_text(encoding="utf-8")
    share = [p for p in (ROOT / "infra/postgres").glob("*.sql")
             if "studio_skills_shared_version" in p.read_text(encoding="utf-8")]
    assert len(share) == 1
    sql = share[0].read_text(encoding="utf-8")
    assert "studio_skills_visibility" in sql  # named in the header only
    assert "DROP CONSTRAINT" not in sql and "RENAME" not in sql
    assert "SET LOCAL lock_timeout" in sql


# ── 6. The pinned version ──────────────────────────────────────────────────


def test_the_loadable_reads_join_the_pinned_version_for_another_member() -> None:
    expr = store._LOADABLE_VERSION
    assert "WHEN s.owner_email = :m THEN s.current_version ELSE s.shared_version" in expr
    assert f"AND {expr} IS NOT NULL" in store._LOADABLE_WHERE
    assert f"v.version = {expr}" in store._LOADABLE_VERSIONS_SQL
    assert "v.version = s.current_version" not in store._LOADABLE_VERSIONS_SQL
    assert f"s.state, {expr}," in store._LOADABLE_SQL


# ── 7. The R8 twin runs in CI ──────────────────────────────────────────────


def test_the_r8_twin_is_in_the_must_run_list() -> None:
    assert "tests/unit/test_studio_skill_share_r8.py" in r8_lists.must_run()
