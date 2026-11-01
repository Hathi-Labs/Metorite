"""The skill routes of AI Studio — the hermetic half (STU-F2a).

Spec: ``project-docs/specs/ai_studio.md`` §7 and §14 F2a. Code:
``gateway/routes/studio/skills.py``. The R8 half, on a real Postgres, is
``test_studio_skills_routes_r8.py``. It proves the walls of the org and of
the member, and two writers at once.

Here the real store runs on a fake session. The fake keeps the two tables
of F1 in memory, filters each statement on the bound org and the member, and
rolls a failed request back. It is patched at ``skills._tenant_session``. The
catalog session is patched at ``actions._tenant_session``.

This file proves:

- the switch: a not-listed org gets 404 on each of the six routes, and a
  listed org and ``*`` get 200 or 201;
- the org and the member come from the session, never from the request;
- the three body checks, in order, before the store session opens;
- the slug rule and the 409 of a slug that the member holds;
- the state table, each legal and each illegal move, and the return to
  ``draft`` of a published skill that gets a new version.

**The mutation (R7).** In ``skills._check_body``, comment out the catalog
check (the ``_catalog_problem`` call). ``test_an_unknown_action_is_422_with_its_path``
goes red, and so does the R8 test of an unknown action.
"""

from __future__ import annotations

import copy
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.studio import actions, skills
from gateway.studio import skills_store as store

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
ANA = "ana@example.test"
BEN = "ben@example.test"
NOT_FOUND = {"detail": "Not found"}


# ── The fake session ───────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _Tables:
    """``studio_skills`` and ``studio_skill_versions`` of every org."""

    def __init__(self) -> None:
        self.skills: dict[str, dict[str, Any]] = {}
        self.versions: list[dict[str, Any]] = []
        self.clock = datetime(2026, 11, 1, tzinfo=UTC)

    def tick(self) -> datetime:
        self.clock += timedelta(seconds=1)
        return self.clock


def _skill_row(r: dict[str, Any]) -> tuple[Any, ...]:
    return (r["id"], r["name"], r["slug"], r["description"], r["owner_email"],
            r["visibility"], r["state"], r["current_version"], r["created_at"],
            r["updated_at"])


def _version_row(v: dict[str, Any]) -> tuple[Any, ...]:
    return (v["skill_id"], v["version"], v["body"], v["content_hash"], v["created_by"],
            v["created_at"])


class _FakeDB:
    """One session bound to *org*. It runs the six statements of the store,
    and each one filters on the bound org, as RLS does."""

    def __init__(self, tables: _Tables, org: str | None) -> None:
        assert org, "a store session opened with no bound org"
        self.t = tables
        self.org = org

    def _mine(self, member: str, sid: str) -> dict[str, Any] | None:
        row = self.t.skills.get(sid)
        if row and row["org"] == self.org and row["owner_email"] == member:
            return row
        return None

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql, p, t = str(stmt), dict(params or {}), self.t
        if sql == store._CREATE_SQL:
            key = (self.org, p["m"], p["slug"])
            if any((r["org"], r["owner_email"], r["slug"]) == key for r in t.skills.values()):
                return _Result()
            now = t.tick()
            row = {"id": str(uuid.uuid4()), "org": self.org, "name": p["name"],
                   "slug": p["slug"], "description": p["desc"], "owner_email": p["m"],
                   "visibility": "private", "state": "draft", "current_version": None,
                   "created_at": now, "updated_at": now}
            t.skills[row["id"]] = row
            return _Result([_skill_row(row)])
        if sql == store._GET_SQL:
            row = self._mine(p["m"], p["id"])
            return _Result([_skill_row(row)] if row else [])
        if sql == store._LIST_SQL:
            rows = [r for r in t.skills.values()
                    if r["org"] == self.org and r["owner_email"] == p["m"]]
            rows.sort(key=lambda r: r["updated_at"], reverse=True)
            return _Result([_skill_row(r) for r in rows])
        if sql == store._SET_STATE_SQL:
            row = self._mine(p["m"], p["id"])
            if row:
                row["state"] = p["st"]
                row["updated_at"] = t.tick()
            return _Result(rowcount=1 if row else 0)
        if sql == store._ADD_VERSION_SQL:
            row = self._mine(p["m"], p["id"])
            if row is None:
                return _Result()
            row["current_version"] = (row["current_version"] or 0) + 1
            row["updated_at"] = t.tick()
            v = {"org": self.org, "skill_id": row["id"], "version": row["current_version"],
                 "body": json.loads(p["body"]), "content_hash": p["hash"],
                 "created_by": p["m"], "created_at": row["updated_at"]}
            t.versions.append(v)
            return _Result([_version_row(v)])
        if sql == store._GET_VERSION_SQL:
            row = self._mine(p["m"], p["id"])
            want = None if row is None else (p["v"] or row["current_version"])
            for v in t.versions:
                if row and v["org"] == self.org and v["skill_id"] == row["id"] \
                        and v["version"] == want:
                    return _Result([_version_row(v)])
            return _Result()
        raise AssertionError(f"an unexpected statement: {sql[:80]}")


class _CatalogDB:
    def __init__(self, world: Any) -> None:
        self.world = world

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        return _Result(self.world.workflows)


@pytest.fixture
def world(monkeypatch):
    w = SimpleNamespace(tables=_Tables(), org=None, workflows=[], log=[], store_calls=[])

    @asynccontextmanager
    async def _store_session(*args: Any):
        w.store_calls.append(args)
        w.log.append("store-open")
        snapshot = copy.deepcopy((w.tables.skills, w.tables.versions))
        try:
            yield _FakeDB(w.tables, w.org)
        except BaseException:
            w.tables.skills, w.tables.versions = snapshot
            raise
        finally:
            w.log.append("store-close")

    @asynccontextmanager
    async def _catalog_session(*args: Any):
        w.log.append("catalog-open")
        try:
            yield _CatalogDB(w)
        finally:
            w.log.append("catalog-close")

    monkeypatch.setattr(skills, "_tenant_session", _store_session)
    monkeypatch.setattr(actions, "_tenant_session", _catalog_session)
    _open(monkeypatch, "*")
    return w


def _open(monkeypatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", value)


def _client(world: Any, org: str | None = ORG, email: str = ANA) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)

    def _user() -> UserContext:
        # The auth layer binds the session's org. The fake session reads it.
        world.org = org
        return UserContext(email=email, role=UserRole.EMPLOYEE,
                           access=build_access(["feature:workflows"]), organization_id=org)

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


#: Two door ops of the real catalog.
_DOORS = sorted(r["id"] for r in actions._manifest_rows())
A1, A2 = _DOORS[0], _DOORS[1]


def _body(actions_: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return {"description": "Use this when someone asks for a quote.",
            "instructions": "Read the price list, then fill the template.",
            "actions": [A1, A2] if actions_ is None else actions_, **extra}


def _create(c: TestClient, name: str = "Quotes", body: dict[str, Any] | None = None,
            **kw: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, **kw}
    if body is not None:
        payload["body"] = body
    r = c.post("/studio/skills", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _deep(n: int) -> str:
    """JSON text of *n* nested lists, built by hand: ``json.dumps`` of such a
    value would overflow first."""
    return "[" * n + "]" * n


def _deep_payload(n: int, *, envelope: bool = True) -> str:
    body = ('{"description": "d", "instructions": "i", "actions": [], '
            f'"cards": {_deep(n)}}}')
    return f'{{"name": "Deep", "body": {body}}}' if envelope else f'{{"body": {body}}}'


def _six(c: TestClient, sid: str) -> list[Any]:
    """One call of each route."""
    return [
        c.get("/studio/skills"),
        c.post("/studio/skills", json={"name": "Other", "body": _body()}),
        c.get(f"/studio/skills/{sid}"),
        c.get(f"/studio/skills/{sid}/versions/1"),
        c.post(f"/studio/skills/{sid}/versions", json={"body": _body()}),
        c.patch(f"/studio/skills/{sid}/state", json={"state": "published"}),
    ]


# ── The switch ─────────────────────────────────────────────────────────────


class TestTheSwitch:
    def test_a_not_listed_org_gets_404_on_each_route(self, monkeypatch, world):
        sid = _create(_client(world), body=_body())["skill"]["id"]
        world.log.clear()
        world.store_calls.clear()
        _open(monkeypatch, OTHER)
        answers = _six(_client(world), sid)
        assert [r.status_code for r in answers] == [404] * 6
        assert all(r.json() == NOT_FOUND for r in answers)
        # The switch runs first: no session, no catalog read.
        assert world.log == [] and world.store_calls == []

    def test_empty_opens_no_org(self, monkeypatch, world):
        _open(monkeypatch, "")
        assert _client(world).get("/studio/skills").status_code == 404

    def test_no_org_gets_404_even_with_star(self, world):
        assert _client(world, org=None).get("/studio/skills").status_code == 404

    @pytest.mark.parametrize("listed", ["*", f"{OTHER}, {ORG.upper()}"])
    def test_a_listed_org_and_star_get_200_or_201(self, monkeypatch, world, listed):
        _open(monkeypatch, listed)
        c = _client(world)
        sid = _create(c, body=_body())["skill"]["id"]
        assert [r.status_code for r in _six(c, sid)] == [200, 201, 200, 200, 201, 200]

    def test_the_routes_join_the_one_studio_router(self):
        paths = {(m, r.path) for r in actions.router.routes for m in r.methods}
        assert {("GET", "/studio/skills"), ("POST", "/studio/skills"),
                ("GET", "/studio/skills/{skill_id}"),
                ("GET", "/studio/skills/{skill_id}/versions/{version}"),
                ("POST", "/studio/skills/{skill_id}/versions"),
                ("PATCH", "/studio/skills/{skill_id}/state")} <= paths
        assert skills.router is actions.router
        from pathlib import Path

        main = Path(actions.__file__).resolve().parents[2] / "main.py"
        assert main.read_text(encoding="utf-8").count("gateway.routes.studio") == 1


# ── The org and the member ─────────────────────────────────────────────────


class TestTheOrgAndTheMember:
    def test_the_session_takes_no_org_from_the_route(self, world):
        c = _client(world)
        sid = _create(c, body=_body())["skill"]["id"]
        _six(c, sid)
        assert world.store_calls and all(args == () for args in world.store_calls)

    def test_a_body_that_names_an_org_or_an_owner_changes_nothing(self, world):
        c = _client(world)
        got = _create(c, body={**_body(), "owner_email": BEN, "organization_id": OTHER},
                      organization_id=OTHER, owner_email=BEN, visibility="org",
                      state="published")
        row = world.tables.skills[got["skill"]["id"]]
        assert (row["org"], row["owner_email"]) == (ORG, ANA)
        assert (row["visibility"], row["state"]) == ("private", "draft")
        assert world.tables.versions[0]["created_by"] == ANA
        r = c.post(f"/studio/skills/{row['id']}/versions",
                   json={"body": _body(), "owner_email": BEN, "organization_id": OTHER})
        assert r.status_code == 201
        assert world.tables.versions[-1]["created_by"] == ANA

    def test_a_query_that_names_an_org_or_an_owner_changes_nothing(self, world):
        _create(_client(world, email=BEN), name="Bens")
        mine = _create(_client(world), name="Mine")["skill"]["id"]
        r = _client(world).get("/studio/skills",
                               params={"organization_id": OTHER, "owner_email": BEN})
        assert [s["id"] for s in r.json()["skills"]] == [mine]

    @pytest.mark.parametrize("org,email", [(ORG, BEN), (OTHER, ANA)])
    def test_a_skill_of_another_member_or_org_is_not_found(self, world, org, email):
        theirs = _create(_client(world, org=org, email=email), body=_body())["skill"]["id"]
        before = copy.deepcopy(world.tables.skills[theirs])
        c = _client(world)
        answers = [
            c.get(f"/studio/skills/{theirs}"),
            c.get(f"/studio/skills/{theirs}/versions/1"),
            c.post(f"/studio/skills/{theirs}/versions", json={"body": _body()}),
            c.patch(f"/studio/skills/{theirs}/state", json={"state": "archived"}),
        ]
        assert [r.status_code for r in answers] == [404] * 4
        assert all(r.json() == NOT_FOUND for r in answers)
        assert c.get("/studio/skills").json()["skills"] == []
        assert world.tables.skills[theirs] == before
        assert len(world.tables.versions) == 1

    def test_an_answer_names_no_owner(self, world):
        c = _client(world)
        sid = _create(c, body=_body())["skill"]["id"]
        for r in _six(c, sid):
            text = r.text
            assert "owner_email" not in text and "created_by" not in text
            assert ANA not in text


# ── The body checks ────────────────────────────────────────────────────────


class TestTheBodyChecks:
    def test_a_body_1500_levels_deep_is_422_on_create(self, world):
        r = _client(world).post("/studio/skills", content=_deep_payload(1500),
                                headers={"content-type": "application/json"})
        assert r.status_code == 422, r.text
        assert "deeper than 20" in r.json()["detail"]
        assert world.tables.skills == {}

    def test_a_body_1500_levels_deep_is_422_on_a_new_version(self, world):
        c = _client(world)
        sid = _create(c)["skill"]["id"]
        r = c.post(f"/studio/skills/{sid}/versions", content=_deep_payload(1500, envelope=False),
                   headers={"content-type": "application/json"})
        assert r.status_code == 422, r.text
        assert world.tables.versions == []

    def test_a_deep_body_with_a_bad_envelope_is_422_not_500(self, world):
        # No name: a model error must not echo the deep input back.
        r = _client(world).post(
            "/studio/skills", content='{"body": {"cards": ' + _deep(1500) + "}}",
            headers={"content-type": "application/json"})
        assert r.status_code == 422, r.text

    def test_a_body_too_deep_to_parse_is_never_500(self, world):
        r = _client(world).post("/studio/skills", content=_deep_payload(20000),
                                headers={"content-type": "application/json"})
        assert r.status_code in (400, 422), r.text

    def test_the_depth_cap_is_20(self, world):
        c = _client(world)

        def nest(n: int) -> Any:
            value: Any = []
            for _ in range(n - 1):
                value = [value]
            return value

        assert c.post("/studio/skills", json={"name": "Twenty", "body": {
            **_body(), "cards": nest(20)}}).status_code == 201
        r = c.post("/studio/skills", json={"name": "Deeper", "body": {
            **_body(), "cards": nest(21)}})
        assert r.status_code == 422 and "deeper than 20" in r.json()["detail"]

    def test_an_unknown_action_is_422_with_its_path(self, world):
        c = _client(world)
        r = c.post("/studio/skills", json={"name": "Quotes",
                                           "body": _body([A1, "crm.no_such_op"])})
        assert r.status_code == 422
        assert r.json()["detail"].startswith("$.actions[1]:")
        assert "crm.no_such_op" in r.json()["detail"]
        assert world.tables.skills == {}
        sid = _create(c, name="Other")["skill"]["id"]
        r = c.post(f"/studio/skills/{sid}/versions", json={"body": _body(["nope.op"])})
        assert r.status_code == 422 and r.json()["detail"].startswith("$.actions[0]:")
        assert world.tables.versions == []

    def test_a_duplicate_action_is_422_with_its_path(self, world):
        r = _client(world).post("/studio/skills", json={"name": "Quotes",
                                                        "body": _body([A1, A2, A1])})
        assert r.status_code == 422
        assert r.json()["detail"].startswith("$.actions[2]:")
        assert "twice" in r.json()["detail"]

    def test_more_than_35_actions_is_422(self, world):
        many = [f"workflow:{uuid.uuid4()}" for _ in range(36)]
        world.workflows = [SimpleNamespace(id=a.removeprefix("workflow:"), name=a) for a in many]
        r = _client(world).post("/studio/skills", json={"name": "Many", "body": _body(many)})
        assert r.status_code == 422 and "at most 35" in r.json()["detail"]
        assert _client(world).post("/studio/skills", json={
            "name": "Enough", "body": _body(many[:35])}).status_code == 201

    def test_a_class_b_or_c_action_is_legal_in_a_draft(self, world):
        writes = [r["id"] for r in actions._manifest_rows() if r["class"] in ("B", "C")]
        assert writes
        assert _client(world).post("/studio/skills", json={
            "name": "Writes", "body": _body(writes[:3])}).status_code == 201

    def test_a_published_workflow_of_the_org_is_an_action(self, world):
        wid = uuid.uuid4()
        world.workflows = [SimpleNamespace(id=wid, name="Flow")]
        assert _client(world).post("/studio/skills", json={
            "name": "Flow", "body": _body([f"workflow:{wid}"])}).status_code == 201

    def test_an_oversized_body_is_422(self, world):
        big = _body(instructions="x" * (store.MAX_BODY_BYTES + 1))
        r = _client(world).post("/studio/skills", json={"name": "Big", "body": big})
        assert r.status_code == 422 and "bytes" in r.json()["detail"]
        assert world.tables.skills == {}

    @pytest.mark.parametrize("body", [
        {"instructions": "i"},
        {"description": "d", "instructions": " "},
        {"description": "d", "instructions": "i", "actions": "crm.read"},
        {"description": "d", "instructions": "i", "actions": [""]},
        {"description": "d", "instructions": "i", "n": 1e300},
    ])
    def test_the_store_check_refuses_a_bad_body(self, world, body):
        r = _client(world).post("/studio/skills", json={"name": "Bad", "body": body})
        assert r.status_code == 422
        assert world.tables.skills == {}

    @pytest.mark.parametrize("payload", [None, [], "x", {"name": 5}, {"body": {}},
                                         {"name": "x", "body": [1]}])
    def test_a_bad_envelope_is_422(self, world, payload):
        r = _client(world).post("/studio/skills", json=payload)
        assert r.status_code == 422
        assert world.store_calls == []

    def test_the_checks_run_before_the_store_session(self, world):
        _client(world).post("/studio/skills", json={"name": "Quotes", "body": _body()})
        assert world.log == ["catalog-open", "catalog-close", "store-open", "store-close"]

    def test_no_actions_reads_no_catalog(self, world):
        _client(world).post("/studio/skills", json={"name": "Q", "body": _body([])})
        assert world.log == ["store-open", "store-close"]

    def test_the_counts_are_checked_before_the_catalog(self, world):
        _client(world).post("/studio/skills", json={"name": "Q", "body": _body([A1, A1])})
        assert world.log == []

    @pytest.mark.parametrize("bad", ["nope", "123", "' OR 1=1 --"])
    def test_a_bad_skill_id_is_422(self, world, bad):
        c = _client(world)
        answers = [
            c.get(f"/studio/skills/{bad}"),
            c.get(f"/studio/skills/{bad}/versions/1"),
            c.post(f"/studio/skills/{bad}/versions", json={"body": _body()}),
            c.patch(f"/studio/skills/{bad}/state", json={"state": "archived"}),
        ]
        assert [r.status_code for r in answers] == [422] * 4
        assert world.store_calls == []

    @pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5", "9999999999"])
    def test_a_bad_version_number_is_422(self, world, bad):
        sid = _create(_client(world), body=_body())["skill"]["id"]
        assert _client(world).get(f"/studio/skills/{sid}/versions/{bad}").status_code == 422

    def test_a_missing_version_is_404(self, world):
        sid = _create(_client(world), body=_body())["skill"]["id"]
        r = _client(world).get(f"/studio/skills/{sid}/versions/2")
        assert r.status_code == 404 and r.json() == NOT_FOUND
        assert _client(world).get(f"/studio/skills/{uuid.uuid4()}").json() == NOT_FOUND


# ── The slug ───────────────────────────────────────────────────────────────


class TestTheSlug:
    @pytest.mark.parametrize("name,slug", [
        ("Quotes", "quotes"),
        ("  Layoff Plan!! ", "layoff-plan"),
        ("Café quotes", "cafe-quotes"),
        ("--a--b--", "a-b"),
        ("x" * 63 + " y", "x" * 63),
        ("a" * 200, "a" * 64),
    ])
    def test_the_slug_comes_from_the_name(self, name, slug):
        assert skills.slug_from_name(name) == slug

    def test_an_absent_slug_comes_from_the_name(self, world):
        assert _create(_client(world), name="Price Quotes")["skill"]["slug"] == "price-quotes"

    def test_a_given_slug_is_kept(self, world):
        assert _create(_client(world), name="Quotes", slug="q-1")["skill"]["slug"] == "q-1"

    def test_a_name_with_no_slug_is_422(self, world):
        r = _client(world).post("/studio/skills", json={"name": "日本語"})
        assert r.status_code == 422 and world.store_calls == []

    def test_a_bad_slug_is_422(self, world):
        assert _client(world).post("/studio/skills", json={
            "name": "Q", "slug": "Not A Slug"}).status_code == 422

    def test_a_slug_that_the_member_holds_is_409_and_names_no_slug(self, world):
        c = _client(world)
        _create(c, name="Quotes", slug="secret-layoff-plan")
        r = c.post("/studio/skills", json={"name": "Again", "slug": "secret-layoff-plan",
                                           "body": _body()})
        assert r.status_code == 409
        assert r.json() == {"detail": "You already have a skill with this name"}
        assert "secret" not in r.text and "layoff" not in r.text
        assert len(world.tables.skills) == 1 and world.tables.versions == []

    def test_another_member_may_hold_the_same_slug(self, world):
        _create(_client(world), name="Quotes")
        assert _create(_client(world, email=BEN), name="Quotes")["skill"]["slug"] == "quotes"


# ── The states ─────────────────────────────────────────────────────────────


_STATES = ("draft", "published", "paused", "archived")
_LEGAL = {
    ("draft", "published"), ("draft", "archived"),
    ("published", "paused"), ("published", "archived"),
    ("paused", "published"), ("paused", "archived"),
    ("archived", "draft"),
}


def test_state_machine_table():
    """The table of §7. A change to it changes this test on purpose."""
    assert set(skills.TRANSITIONS) == set(store.STATES) == set(_STATES)
    assert {(a, b) for a, targets in skills.TRANSITIONS.items() for b in targets} == _LEGAL


class TestTheStates:
    @pytest.mark.parametrize("start", _STATES)
    @pytest.mark.parametrize("target", _STATES)
    def test_each_move(self, world, start, target):
        c = _client(world)
        sid = _create(c, body=_body())["skill"]["id"]
        world.tables.skills[sid]["state"] = start
        r = c.patch(f"/studio/skills/{sid}/state", json={"state": target})
        if start == target or (start, target) in _LEGAL:
            assert r.status_code == 200, r.text
            assert r.json()["skill"]["state"] == target
            assert world.tables.skills[sid]["state"] == target
        else:
            assert r.status_code == 409, r.text
            assert world.tables.skills[sid]["state"] == start
        assert world.tables.skills[sid]["visibility"] == "private"

    def test_the_same_state_is_a_no_op(self, world):
        c = _client(world)
        sid = _create(c, body=_body())["skill"]["id"]
        before = copy.deepcopy(world.tables.skills[sid])
        assert c.patch(f"/studio/skills/{sid}/state", json={"state": "draft"}).status_code == 200
        assert world.tables.skills[sid] == before

    def test_a_publish_with_no_version_is_409(self, world):
        c = _client(world)
        sid = _create(c)["skill"]["id"]
        r = c.patch(f"/studio/skills/{sid}/state", json={"state": "published"})
        assert r.status_code == 409 and r.json()["detail"] == "Add a version first"
        assert world.tables.skills[sid]["state"] == "draft"

    def test_a_publish_checks_the_catalog_again(self, world):
        wid = uuid.uuid4()
        world.workflows = [SimpleNamespace(id=wid, name="Flow")]
        c = _client(world)
        sid = _create(c, body=_body([A1, f"workflow:{wid}"]))["skill"]["id"]
        world.workflows = []  # the workflow is no longer published
        r = c.patch(f"/studio/skills/{sid}/state", json={"state": "published"})
        assert r.status_code == 422 and r.json()["detail"].startswith("$.actions[1]:")
        assert world.tables.skills[sid]["state"] == "draft"

    def test_an_unknown_state_is_422(self, world):
        c = _client(world)
        sid = _create(c, body=_body())["skill"]["id"]
        assert c.patch(f"/studio/skills/{sid}/state",
                       json={"state": "deleted"}).status_code == 422

    def test_a_new_version_of_a_published_skill_returns_it_to_draft(self, world):
        c = _client(world)
        sid = _create(c, body=_body())["skill"]["id"]
        assert c.patch(f"/studio/skills/{sid}/state",
                       json={"state": "published"}).json()["skill"]["state"] == "published"
        r = c.post(f"/studio/skills/{sid}/versions", json={"body": _body([A2])})
        assert r.status_code == 201
        assert r.json()["skill"]["state"] == "draft"
        assert r.json()["skill"]["current_version"] == r.json()["version"]["version"] == 2
        assert world.tables.skills[sid]["state"] == "draft"
        got = c.get(f"/studio/skills/{sid}").json()
        assert got["skill"]["state"] == "draft" and got["version"]["body"]["actions"] == [A2]

    def test_a_version_that_comes_in_during_a_move_is_409(self, world, monkeypatch):
        c = _client(world)
        sid = _create(c, body=_body())["skill"]["id"]
        real = store.set_state

        async def late_version(db, member, skill_id, state):
            # Another request stores version 2 before this move takes the row.
            world.tables.skills[skill_id]["current_version"] = 2
            return await real(db, member, skill_id, state)

        monkeypatch.setattr(store, "set_state", late_version)
        r = c.patch(f"/studio/skills/{sid}/state", json={"state": "published"})
        assert r.status_code == 409 and r.json()["detail"] == skills.CHANGED
        assert world.tables.skills[sid]["state"] == "draft"

    def test_create_with_a_body_gives_version_1(self, world):
        got = _create(_client(world), body=_body())
        assert got["skill"]["current_version"] == got["version"]["version"] == 1
        assert got["skill"]["state"] == "draft" and got["skill"]["visibility"] == "private"
        assert _create(_client(world), name="Bare")["version"] is None
