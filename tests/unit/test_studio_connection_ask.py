"""WS-STU I8b-1a — Ask an admin, the backend half, with no database.

Spec: ``project-docs/specs/ai_studio.md`` §8.2 and §14 I8b-1a. Code: the
store ``gateway/connections/attention.py``, the clear hook
``connections/store._clear_asks``, the routes ``POST`` and ``DELETE
/studio/packages/{id}/connections/ask`` and ``asked`` on the read of I8a
(``routes/studio/connections.py``), the purge clause ``connection_asks``
(``routes/admin/members.py``) and the prune hook of the job worker. The R8
twin is ``tests/unit/test_studio_connection_ask_r8.py``.

The checks here:

1. The order of the checks of the two routes. ``STUDIO_ORGS`` off, a bad id
   and the gate off each answer 404, open no session and call no store
   function. A bad body is 413 or 422 before a session. Another member's
   package is 404. Then the four refusals of §8.2, in order: 422
   ``not_in_draft``, 409 ``not_checkable``, 409 ``already_met`` and 409
   ``can_connect``. A good ask answers ``{"asked": true, "new": ...}``, writes
   one audit row and moves the cache only when the ask is new. WS-STU I9a:
   ``already_met`` reads the set of the account of the need, the rule of the
   line of the read. A member row of the caller never meets an ``org`` ask.
2. The org and the member come from the session, never from the body.
3. The read of I8a reads the asks once, for the names that are not met.
4. The clear hook. With the gate off, it sends no statement. It acts only for
   a ``connected`` row at org scope, in a savepoint, and it never raises.
5. The source. One module runs SQL on the table, and it names no credential
   column and no registry table. Each of the six writers calls the hook, and
   no other function does. The hook reads the gate before the savepoint.
6. The migration, found by content: the shape of §8.2.
7. The prune hook is registered once and never raises.
8. The R8 twin is in the ``must_run`` list.

**R7.** M1: drop the gate check from ``_clear_asks``. Check 4 goes red. M2:
drop ``_clear_asks`` from ``save_headers``. Check 5 goes red. M3: move the
gate check of the ask below the session. Check 1 goes red. M4: let the ask
take ``member`` from the body. Check 2 goes red.
"""

from __future__ import annotations

import ast
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
from gateway.connections import attention
from gateway.connections import store as conn_store
from gateway.jobs import worker
from gateway.package_host.resolve import Met
from gateway.routes.studio import actions, connections
from gateway.studio import packages_store as store

ROOT = Path(__file__).resolve().parents[2]
ORG = str(uuid.uuid4())
OWEN = "owen@i8b.test"
BEN = "ben@i8b.test"
PID = str(uuid.uuid4())
PATH = f"/studio/packages/{PID}/connections/ask"
READ = f"/studio/packages/{PID}/connections"
NOT_FOUND = {"detail": "Not found"}
MEMBER = ["feature:studio"]
ADMIN = ["feature:studio", "integrations:manage"]
ROUTES = ROOT / "apps/services/gateway/gateway/routes/studio/connections.py"
STORE = ROOT / "apps/services/gateway/gateway/connections/store.py"
ATTENTION = ROOT / "apps/services/gateway/gateway/connections/attention.py"
TABLE = "integration_attention"
#: The six writers of the registry that can leave a row ``connected``.
WRITERS = {"save_credential", "save_no_secret", "save_headers", "attach_oauth_credential",
           "update_oauth_public", "set_status"}


def _user(email: str = OWEN, grants: list[str] | None = None) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=ORG,
                       access=build_access(grants if grants is not None else MEMBER))


# ── 1, 2 and 3. The routes ─────────────────────────────────────────────────


class _World:
    def __init__(self) -> None:
        self.sessions = 0
        self.owner = OWEN
        self.names: Any = ["zoho", "mcp:tally", "google_calendar", "mailbox", "mcp", "slack"]
        #: I9a: the org rows and the rows of the caller stay apart.
        self.met_org: frozenset[str] = frozenset({"zoho"})
        self.met_member: frozenset[str] = frozenset()
        self.calls: list[tuple[Any, ...]] = []
        self.live: set[tuple[str, str]] = set()
        self.audits: list[dict[str, Any]] = []
        self.bumps: list[Any] = []


def _package(manifest: dict[str, Any]) -> store.Package:
    now = datetime(2026, 11, 14, tzinfo=UTC)
    return store.Package(
        id=PID, slug="app-12345678", name="App", description="", owner_email=OWEN,
        visibility="private", status="draft", manifest=manifest, live_version=None,
        created_at=now, updated_at=now)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    w = _World()

    @asynccontextmanager
    async def session() -> Any:
        w.sessions += 1
        yield object()

    async def get_package(db: Any, member: str, pid: str, **_k: Any) -> store.Package | None:
        w.calls.append(("get_package", member))
        if member != w.owner or pid != PID:
            return None
        manifest: dict[str, Any] = {"studio": 1, "id": "app", "name": "App", "purpose": "T"}
        if w.names is not None:
            manifest["connections"] = w.names
        return _package(manifest)

    async def met_connections(db: Any, *, viewer: str) -> Met:
        w.calls.append(("met", viewer))
        # I9a: the real answer is a Met pair. A set here would hide the split.
        return Met(org=frozenset(w.met_org), member=frozenset(w.met_member))

    async def ask(db: Any, org: str, member: str, provider: str, package_id: str) -> bool:
        w.calls.append(("ask", org, member, provider, package_id))
        new = (member, provider) not in w.live
        if new and sum(1 for m, _p in w.live if m == member) >= attention.MAX_LIVE_ASKS:
            raise attention.TooManyAsks(provider)
        w.live.add((member, provider))
        return new

    async def withdraw(db: Any, org: str, member: str, provider: str) -> bool:
        w.calls.append(("withdraw", org, member, provider))
        gone = (member, provider) in w.live
        w.live.discard((member, provider))
        return gone

    async def asked_by(db: Any, org: str, member: str, providers: Any) -> frozenset[str]:
        w.calls.append(("asked_by", org, member, list(providers)))
        return frozenset(p for m, p in w.live if m == member) & frozenset(providers)

    async def audit(db: Any, **kw: Any) -> None:
        w.audits.append(kw)

    async def bump(org: Any) -> None:
        w.bumps.append(org)

    monkeypatch.setattr(connections, "_tenant_session", session)
    monkeypatch.setattr(connections.store, "get_package", get_package)
    monkeypatch.setattr(connections.host_resolve, "met_connections", met_connections)
    monkeypatch.setattr(connections.attention, "ask", ask)
    monkeypatch.setattr(connections.attention, "withdraw", withdraw)
    monkeypatch.setattr(connections.attention, "asked_by", asked_by)
    monkeypatch.setattr(connections, "record_app_audit_in", audit)
    monkeypatch.setattr(connections, "_bump", bump)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", "*")
    return w


def _client(user: UserContext) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _post(user: UserContext, body: Any = None, *, raw: bytes | None = None,
          path: str = PATH) -> Any:
    client = _client(user)
    if raw is not None:
        return client.post(path, content=raw, headers={"content-type": "application/json"})
    return client.post(path, json=body)


def _store_calls(w: _World) -> list[tuple[Any, ...]]:
    return [c for c in w.calls if c[0] in ("ask", "withdraw", "asked_by")]


def test_a_good_ask_is_new_once_and_writes_one_audit_row(world: _World) -> None:
    r = _post(_user(), {"name": "mcp:tally"})
    assert (r.status_code, r.json()) == (200, {"asked": True, "new": True})
    assert world.sessions == 1
    assert [c for c in world.calls if c[0] == "ask"] == [("ask", ORG, OWEN, "mcp:tally", PID)]
    assert world.audits == [{"app_id": PID, "user_email": OWEN, "kind": "action",
                             "detail": {"op": "connection_ask", "name": "mcp:tally"}}]
    assert world.bumps == [ORG]
    r = _post(_user(), {"name": "mcp:tally"})
    assert (r.status_code, r.json()) == (200, {"asked": True, "new": False})
    assert len(world.audits) == 1 and world.bumps == [ORG]


def test_the_eleventh_new_ask_is_429_and_a_repeat_or_a_withdraw_keeps_the_count(
        world: _World) -> None:
    world.names = [f"mcp:t{i}" for i in range(12)]
    for i in range(10):
        r = _post(_user(), {"name": f"mcp:t{i}"})
        assert (r.status_code, r.json()) == (200, {"asked": True, "new": True})
    audits = len(world.audits)
    r = _post(_user(), {"name": "mcp:t10"})
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "too_many_asks"
    assert len(world.audits) == audits and world.bumps == [ORG] * 10
    # A repeat press of an ask that exists is still 200 and not new.
    r = _post(_user(), {"name": "mcp:t3"})
    assert (r.status_code, r.json()) == (200, {"asked": True, "new": False})
    # A withdraw frees one slot.
    assert _client(_user()).delete(f"{PATH}?name=mcp:t0").status_code == 200
    r = _post(_user(), {"name": "mcp:t10"})
    assert (r.status_code, r.json()) == (200, {"asked": True, "new": True})


@pytest.mark.parametrize("setting", ["studio_orgs", "studio_connection_gate_orgs"])
@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_either_flag_off_answers_404_with_no_session_and_no_store_call(
    world: _World, monkeypatch: pytest.MonkeyPatch, setting: str, method: str,
) -> None:
    monkeypatch.setattr(get_settings(), setting, "")
    client = _client(_user())
    r = (client.post(PATH, json={"name": "mcp:tally"}) if method == "POST"
         else client.delete(PATH + "?name=mcp:tally"))
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert (world.sessions, world.calls, world.audits, world.bumps) == (0, [], [], [])
    # Another org on the list is not this org.
    monkeypatch.setattr(get_settings(), setting, str(uuid.uuid4()))
    assert _client(_user()).post(PATH, json={"name": "mcp:tally"}).status_code == 404
    assert (world.sessions, world.calls) == (0, [])


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_a_bad_id_answers_404_with_no_session(world: _World, method: str) -> None:
    path = "/studio/packages/not-a-uuid/connections/ask"
    client = _client(_user())
    r = (client.post(path, json={"name": "mcp:tally"}) if method == "POST"
         else client.delete(path + "?name=mcp:tally"))
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0


@pytest.mark.parametrize("raw", [
    b"", b"not json", b"[]", b'"mcp:tally"', b'{"name": 3}', b'{"name": ""}',
    b'{"name": "' + b"a" * 61 + b'"}', b'{}', b'{"name": "mcp:tally", "note": "please"}',
    b'{"name": "mcp:tally", "member": "ben@i8b.test"}',
    b'{"name": "mcp:tally", "org": "00000000-0000-0000-0000-000000000001"}',
])
def test_a_bad_body_is_422_before_a_session(world: _World, raw: bytes) -> None:
    r = _post(_user(), raw=raw)
    assert r.status_code == 422, (raw, r.text)
    assert r.json()["detail"]["code"] == connections.BAD_ASK
    assert world.sessions == 0 and world.calls == []


def test_a_body_past_the_cap_is_413_before_a_session(world: _World) -> None:
    r = _post(_user(), raw=b'{"name": "' + b"a" * 2000 + b'"}')
    assert r.status_code == 413
    assert world.sessions == 0


def test_another_member_gets_404_and_no_store_call(world: _World) -> None:
    r = _post(_user(BEN), {"name": "mcp:tally"})
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert _store_calls(world) == [] and ("met", BEN) not in world.calls
    r = _client(_user(BEN)).delete(PATH + "?name=mcp:tally")
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert _store_calls(world) == []


@pytest.mark.parametrize("name,grants,status,code", [
    ("github", MEMBER, 422, connections.NOT_IN_DRAFT),
    ("mailbox", MEMBER, 409, connections.NOT_CHECKABLE),
    ("mcp", MEMBER, 409, connections.NOT_CHECKABLE),
    ("slack", MEMBER, 409, connections.NOT_CHECKABLE),
    ("zoho", MEMBER, 409, connections.ALREADY_MET),
    ("google_calendar", MEMBER, 409, connections.CAN_CONNECT),
    ("mcp:tally", ADMIN, 409, connections.CAN_CONNECT),
])
def test_the_refusals_come_in_the_order_of_the_spec(
    world: _World, name: str, grants: list[str], status: int, code: str,
) -> None:
    r = _post(_user(grants=grants), {"name": name})
    assert r.status_code == status, r.text
    assert r.json()["detail"]["code"] == code
    assert _store_calls(world) == [] and world.audits == [] and world.bumps == []
    # The met set is read only past the two checks of the name.
    read_met = any(c[0] == "met" for c in world.calls)
    assert read_met is (code in (connections.ALREADY_MET, connections.CAN_CONNECT))


def test_a_member_row_of_the_caller_never_makes_an_org_ask_met(world: _World) -> None:
    """I9a. An ``org`` need is met only by an org row. So a member row of the
    caller leaves the ask open, for a plain org name and for a declared ``org``."""
    world.names = ["zoho", {"name": "mcp:tally", "as": "org"}]
    world.met_org = frozenset()
    world.met_member = frozenset({"zoho", "mcp:tally"})
    for name in ("zoho", "mcp:tally"):
        r = _post(_user(), {"name": name})
        assert (r.status_code, r.json()) == (200, {"asked": True, "new": True}), (name, r.text)
    assert [c[3] for c in world.calls if c[0] == "ask"] == ["zoho", "mcp:tally"]


def test_an_org_row_makes_an_org_ask_met(world: _World) -> None:
    world.names = [{"name": "mcp:tally", "as": "org"}]
    world.met_org = frozenset({"mcp:tally"})
    r = _post(_user(), {"name": "mcp:tally"})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, connections.ALREADY_MET)
    assert _store_calls(world) == []


def test_a_member_need_reads_the_rows_of_the_caller_only(world: _World) -> None:
    """I9a. A ``member`` need is met only by a row of the caller, never by an org
    row. With no row of the caller, the refusal is ``can_connect``: a member
    connects their own member connection."""
    world.names = [{"name": "google_drive", "as": "member"}]
    world.met_org = frozenset({"google_drive"})
    world.met_member = frozenset()
    r = _post(_user(), {"name": "google_drive"})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, connections.CAN_CONNECT)
    world.met_member = frozenset({"google_drive"})
    r = _post(_user(), {"name": "google_drive"})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, connections.ALREADY_MET)
    assert _store_calls(world) == []


def test_the_ask_and_the_read_use_one_rule_of_met() -> None:
    """The ``already_met`` refusal and ``met`` on the line read ``_need_met``,
    and no other line of the module reads ``met.org`` or ``met.member``."""
    tree = ast.parse(ROUTES.read_text(encoding="utf-8"))
    reads = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute)
             and isinstance(n.value, ast.Name) and n.value.id == "met"
             and n.attr in ("org", "member")]
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_need_met")
    assert reads and all(fn.lineno <= r.lineno <= (fn.end_lineno or 0) for r in reads)
    for name in ("_check_member", "connection_rows"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        assert "_need_met" in ast.unparse(fn), name


def test_the_can_connect_refusal_says_so(world: _World) -> None:
    r = _post(_user(grants=ADMIN), {"name": "mcp:tally"})
    assert r.json()["detail"]["message"] == "You can connect it yourself."


def test_a_draft_with_no_list_of_names_refuses_each_name(world: _World) -> None:
    world.names = "zoho"
    r = _post(_user(), {"name": "zoho"})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, connections.NOT_IN_DRAFT)


def test_the_withdraw_deletes_the_ask_of_the_caller(world: _World) -> None:
    world.live = {(OWEN, "mcp:tally"), (BEN, "mcp:tally")}
    r = _client(_user()).delete(PATH + "?name=mcp:tally")
    assert (r.status_code, r.json()) == (200, {"asked": False, "withdrawn": True})
    assert world.live == {(BEN, "mcp:tally")}
    assert [c for c in world.calls if c[0] == "withdraw"] == [("withdraw", ORG, OWEN, "mcp:tally")]
    assert world.audits[0]["detail"] == {"op": "connection_withdraw", "name": "mcp:tally"}
    assert world.bumps == [ORG]
    r = _client(_user()).delete(PATH + "?name=mcp:tally")
    assert r.json() == {"asked": False, "withdrawn": False}
    assert len(world.audits) == 1 and world.bumps == [ORG]


@pytest.mark.parametrize("query", ["", "?name=", "?name=a&name=b", "?name=" + "a" * 61])
def test_a_withdraw_needs_one_name(world: _World, query: str) -> None:
    r = _client(_user()).delete(PATH + query)
    assert r.status_code == 422
    assert world.sessions == 0


def test_the_query_names_no_org_and_no_member(world: _World) -> None:
    """R11: a query of org or member changes nothing."""
    other = str(uuid.uuid4())
    r = _client(_user()).delete(PATH + f"?name=mcp:tally&member={BEN}&org={other}")
    assert r.status_code == 200
    assert [c for c in world.calls if c[0] == "withdraw"] == [("withdraw", ORG, OWEN, "mcp:tally")]


def test_the_read_reads_the_asks_of_the_names_that_are_not_met(world: _World) -> None:
    world.live = {(OWEN, "mcp:tally"), (BEN, "github"), (OWEN, "zoho")}
    rows = _client(_user()).get(READ).json()["connections"]
    assert {r["name"]: r["asked"] for r in rows} == {
        "zoho": False, "mcp:tally": True, "google_calendar": False, "mailbox": False,
        "mcp": False, "slack": False}
    asks = [c for c in world.calls if c[0] == "asked_by"]
    assert asks == [("asked_by", ORG, OWEN,
                     ["mcp:tally", "google_calendar", "mailbox", "mcp", "slack"])]


def test_the_read_of_a_draft_with_each_name_met_reads_no_ask(world: _World) -> None:
    world.names = ["zoho"]
    rows = _client(_user()).get(READ).json()["connections"]
    assert [r["asked"] for r in rows] == [False]
    assert [c for c in world.calls if c[0] == "asked_by"] == []


def test_each_ask_route_reads_the_switch_first() -> None:
    tree = ast.parse(ROUTES.read_text(encoding="utf-8"))
    for name in ("ask_admin", "withdraw_ask"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        body = [s for s in fn.body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        assert "flags.studio_allowed(user.organization_id)" in ast.unparse(body[0]), name
        assert "_package_id(package_id)" in ast.unparse(body[1]), name
        assert "connection_gate_on(user.organization_id)" in ast.unparse(body[2]), name
        assert isinstance(body[4], ast.AsyncWith), name
        # The org and the member come from the session only.
        text = ast.unparse(fn)
        assert "user.organization_id" in text and "user.email" in text
        assert "body." not in text and "query_params" not in text


# ── 4. The clear hook ──────────────────────────────────────────────────────


class _Db:
    """A session that records each savepoint and each statement."""

    def __init__(self, fail: bool = False) -> None:
        self.events: list[str] = []
        self.params: list[dict[str, Any]] = []
        self.fail = fail

    @asynccontextmanager
    async def _nested(self) -> Any:
        self.events.append("savepoint")
        try:
            yield self
        except Exception:
            self.events.append("rollback to savepoint")
            raise

    def begin_nested(self) -> Any:
        return self._nested()

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        self.events.append(str(stmt))
        self.params.append(dict(params or {}))
        if self.fail:
            raise RuntimeError("the statement failed: secret-value")

        class _R:
            rowcount = 1
        return _R()


def _conn(*, status: str = "connected", scope: str = "org",
          org: str = ORG, provider: str = "mcp:tally") -> conn_store.Connection:
    now = datetime(2026, 11, 14, tzinfo=UTC)
    return conn_store.Connection(
        id=str(uuid.uuid4()), organization_id=org, provider=provider, owning_app="integrations",
        scope_kind=scope, member_email=BEN if scope == "member" else None, status=status,
        provider_meta={}, scopes=(), connected_by=BEN, last_sync_at=None, last_error=None,
        created_at=now, updated_at=now)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_with_the_gate_off_the_hook_sends_no_statement(monkeypatch: pytest.MonkeyPatch) -> None:
    for value in ("", str(uuid.uuid4())):
        monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", value)
        db = _Db()
        _run(conn_store._clear_asks(db, _conn()))
        assert db.events == []


@pytest.mark.parametrize("conn", [
    None,
    _conn(scope="member"),
    _conn(status="disconnected"),
    _conn(status="needs_reconnect"),
    _conn(status="needs_reset"),
])
def test_the_hook_clears_only_a_connected_org_row(
    monkeypatch: pytest.MonkeyPatch, conn: conn_store.Connection | None,
) -> None:
    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", "*")
    db = _Db()
    _run(conn_store._clear_asks(db, conn))
    assert db.events == []


def test_a_connected_org_row_clears_the_asks_of_its_provider_in_a_savepoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", ORG)
    db = _Db()
    _run(conn_store._clear_asks(db, _conn()))
    assert db.events[0] == "savepoint"
    assert len(db.events) == 2 and db.events[1].startswith(f"DELETE FROM {TABLE} ")
    assert db.params == [{"org": ORG, "provider": "mcp:tally"}]


def test_a_failed_clear_never_raises_and_logs_the_class_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", "*")
    db = _Db(fail=True)
    _run(conn_store._clear_asks(db, _conn()))
    assert db.events[-1] == "rollback to savepoint"
    assert "secret-value" not in caplog.text


def test_with_the_gate_off_a_writer_sends_what_it_sent_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``save_no_secret`` sends its one upsert and nothing more."""
    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", "")
    row = _conn(provider="github")

    class _Row:
        def __getattr__(self, key: str) -> Any:
            return getattr(row, key)

    class _Db2(_Db):
        async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
            self.events.append(str(stmt))

            class _R:
                def first(self) -> Any:
                    return _Row()
            return _R()

    db = _Db2()
    _run(conn_store.save_no_secret(db, ORG, "github", "integrations", provider_meta={},
                                   connected_by=BEN))
    assert len(db.events) == 1 and db.events[0].startswith("INSERT INTO integration_" + "connections")


# ── 5. The source ──────────────────────────────────────────────────────────


def _string_hits(roots: list[Path], needle: str) -> set[str]:
    """Each python file that names *needle* in a string, a name or an attribute."""
    hits: set[str] = set()
    for root in roots:
        for path in root.rglob("*.py"):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if needle not in text:
                continue
            for node in ast.walk(ast.parse(text)):
                value = (node.value if isinstance(node, ast.Constant) and isinstance(node.value, str)
                         else node.id if isinstance(node, ast.Name)
                         else node.attr if isinstance(node, ast.Attribute) else None)
                if value and needle in value:
                    hits.add(path.relative_to(ROOT).as_posix())
                    break
    return hits


def test_one_module_runs_sql_on_the_table() -> None:
    """Only the store of an ask names the table. The purge names it in its
    clause list, and nothing else does."""
    hits = _string_hits([ROOT / "apps", ROOT / "packages"], TABLE)
    assert hits == {
        "apps/services/gateway/gateway/connections/attention.py",
        "apps/services/gateway/gateway/routes/admin/members.py",
    }, hits


def test_the_store_of_an_ask_names_no_credential_column_and_no_registry_table() -> None:
    text = ATTENTION.read_text(encoding="utf-8")
    assert "credentials" + "_encrypted" not in text
    assert "integration_" + "connections" not in text
    assert "provider_keys" not in text


def _functions(path: Path) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)}


def _calls(fn: ast.AST) -> list[str]:
    return [ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)]


def test_each_of_the_six_writers_calls_the_hook_and_no_other_function_does() -> None:
    funcs = _functions(STORE)
    callers = {name for name, fn in funcs.items() if "_clear_asks" in _calls(fn)}
    assert callers == WRITERS
    for name in WRITERS:
        assert _calls(funcs[name]).count("_clear_asks") == 1, name


def test_the_hook_reads_the_row_then_the_gate_then_opens_a_savepoint() -> None:
    fn = _functions(STORE)["_clear_asks"]
    src = ast.unparse(fn)
    status = src.index("conn.status != STATUS_CONNECTED")
    scope = src.index("conn.scope_kind != SCOPE_ORG")
    gate = src.index("connection_gate_on(conn.organization_id)")
    nested = src.index("db.begin_nested()")
    clear = src.index("attention.clear_on_connect(")
    assert status < gate and scope < gate < nested < clear
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert len(tries) == 1
    assert [ast.unparse(h.type) for h in tries[0].handlers] == ["Exception"]
    assert "type(exc).__name__" in ast.unparse(tries[0].handlers[0])
    withs = [n for n in ast.walk(tries[0]) if isinstance(n, ast.AsyncWith)]
    assert len(withs) == 1 and "attention.clear_on_connect(" in ast.unparse(withs[0])


def test_the_route_module_runs_no_sql() -> None:
    text = ROUTES.read_text(encoding="utf-8")
    assert "text(" not in text and TABLE not in text


def test_each_statement_of_the_store_names_the_org() -> None:
    sql = [v for k, v in vars(attention).items() if k.endswith("_SQL") and isinstance(v, str)]
    # The lock of the asks is ``_ASK_LOCK``. It names the bound org and no kind.
    assert len(sql) == 8
    assert "app.tenant_id" in attention._ASK_LOCK and "pg_advisory_xact_lock" in attention._ASK_LOCK
    for statement in sql:
        assert "organization_id = " in statement or "(organization_id, " in statement, statement
        assert f"kind = '{attention.KIND_ASK}'" in statement, statement


def test_the_conflict_target_is_the_partial_unique_index() -> None:
    target = re.search(r"ON CONFLICT \(([^)]*)\) WHERE (kind = '\w+')", attention._ASK_SQL)
    assert target is not None
    index = re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS integration_attention_ask_uq\s+"
        r"ON integration_attention \(([^)]*)\)\s+WHERE (kind = '\w+');", _migration())
    assert index is not None
    assert (target.group(1), target.group(2)) == (index.group(1), index.group(2))


# ── 6. The migration ───────────────────────────────────────────────────────


def _migration() -> str:
    """The file that creates the table, found by content (R1)."""
    found = [p for p in (ROOT / "infra/postgres").glob("[0-9]*_*.sql")
             if f"CREATE TABLE IF NOT EXISTS {TABLE}" in p.read_text(encoding="utf-8")]
    assert len(found) == 1, found
    return found[0].read_text(encoding="utf-8")


def test_the_migration_has_the_shape_of_the_spec() -> None:
    sql = _migration()
    body = sql.split("BEGIN;", 1)[1]
    assert re.search(r"organization_id\s+UUID NOT NULL\s+REFERENCES organization \(id\) "
                     r"ON DELETE CASCADE\s+DEFAULT current_setting\('app.tenant_id', true\)::uuid",
                     body)
    assert "CHECK (kind IN ('ask', 'broken'))" in body
    assert "char_length(provider) BETWEEN 1 AND 60" in body
    assert "member_email     TEXT," in body
    assert "member_email = lower(member_email)" in body
    assert ("FOREIGN KEY (organization_id, package_id)\n"
            "        REFERENCES apps (organization_id, id) ON DELETE CASCADE") in body
    assert "kind <> 'ask' OR (member_email IS NOT NULL AND package_id IS NOT NULL)" in body
    assert "ON integration_attention (organization_id, kind, provider)" in body
    assert "ENABLE ROW LEVEL SECURITY" in body and "FORCE  ROW LEVEL SECURITY" in body
    assert body.count("organization_id = current_setting(''app.tenant_id'', true)::uuid") == 2
    assert "cleared_at" not in body and "note" not in body.split("COMMENT ON", 1)[0].lower()
    assert body.rstrip().endswith("COMMIT;")
    # The generated phases carry it too: test_tenant_coverage.py, the Studio list.


# ── 7. The prune hook ──────────────────────────────────────────────────────


def test_the_prune_hook_is_registered_once() -> None:
    attention.register_prune()
    attention.register_prune()
    assert worker.prune_hooks.count(attention.prune_hook) == 1


@pytest.mark.parametrize("db", [_Db(fail=True), object()])
def test_the_prune_hook_never_raises(db: Any) -> None:
    _run(attention.prune_hook(db, datetime(2026, 11, 14, tzinfo=UTC).date()))


def test_the_prune_deletes_the_expired_asks_of_the_bound_org_only() -> None:
    sql = attention._PRUNE_SQL
    assert "current_setting('app.tenant_id', true)" in sql
    assert f"interval '{attention.ASK_DAYS} days'" in sql and "asked_at <=" in sql
    assert attention.ASK_DAYS == 30


# ── 8. The R8 twin runs in CI ──────────────────────────────────────────────


def test_the_r8_twin_is_in_the_must_run_list() -> None:
    from tests.unit import _r8_lists as r8_lists

    assert "tests/unit/test_studio_connection_ask_r8.py" in r8_lists.must_run()
