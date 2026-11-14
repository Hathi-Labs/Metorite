"""WS-STU I8a — Connect in place, the backend half, with no database.

Spec: ``project-docs/specs/ai_studio.md`` §8.1 and §14 I8a, and
``project-docs/specs/integrations_console.md`` §3.2 and §3.5. Code: the route
``GET /studio/packages/{id}/connections`` (``routes/studio/connections.py``)
and the provider-level map ``setup_href_for`` with ``may_connect`` in
``routes/integrations_connections.py``. The R8 twin is
``tests/unit/test_studio_connect_in_place_r8.py``.

The checks here:

1. ``setup_href_for``. Each provider of the connector list (IN-11) links to
   the connect flow of its owning app. Calendar opens its settings sheet and
   Drive opens the Sources view of Knowledge (the fix of I8a). The IN-11 name
   ``console`` is the registry's ``integrations``. ``mcp:<server>`` opens the
   Add form of the console with the name. A bad server name and an unknown id
   open the console.
2. The registry row of a calendar or a Drive connection names its own app
   and links to it, not to the console.
3. ``may_connect``. A holder of ``integrations:manage`` may connect each
   name. A member may connect a member connection of the list only: an org
   connection, an MCP server and an unknown id need the right (§3.5).
4. The read's shape. One row for each name of the draft, in order, with the
   six keys ``name``, ``label``, ``met``, ``checkable``, ``setup_href`` and
   ``can_connect``. ``met`` is the answer of ``met_connections`` for the
   session member. A provider outside the registry (a mailbox, a number) is
   not checkable and offers no Connect, and ``OUTSIDE_REGISTRY`` agrees with
   the met read.
5. The order of the checks. ``STUDIO_ORGS`` off, a bad id and the gate off
   each answer 404 and open no session. With the gate off nothing reads the
   registry. Another member's package is 404, and the met set is not read.
   A draft with no connection reads no met set.
6. The source. The route module reads no right, keeps no copy of the map,
   runs no SQL, and calls ``met_connections`` once and ``met_providers``
   never. The handler reads the switch on its first line.
7. The R8 twin is in the ``--must-run`` lists of both workflows.

**R7.** M1: drop ``calendar`` from ``_SETUP_HREF``. Checks 1 and 2 go red.
M2: return ``True`` from ``may_connect`` for every caller. Check 3 goes red.
M3: move the gate check below the session. Check 5 goes red. M4: drop
``checkable(name) and`` from ``can_connect``. Check 4 goes red.
"""

from __future__ import annotations

import ast
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import connector_list as cl
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.connections import store as conn_store
from gateway.routes import integrations_connections as ic
from gateway.routes.studio import actions, connections
from gateway.studio import packages_store as store

ROOT = Path(__file__).resolve().parents[2]
ORG = str(uuid.uuid4())
OWEN = "owen@i8a.test"
BEN = "ben@i8a.test"
PID = str(uuid.uuid4())
PATH = f"/studio/packages/{PID}/connections"
NOT_FOUND = {"detail": "Not found"}
MEMBER = ["feature:studio"]
ADMIN = ["feature:studio", "integrations:manage"]
ROW_KEYS = {"name", "label", "met", "checkable", "setup_href", "can_connect"}
SRC = ROOT / "apps/services/gateway/gateway/routes/studio/connections.py"


def _user(email: str = OWEN, grants: list[str] | None = None) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=ORG,
                       access=build_access(grants if grants is not None else MEMBER))


# ── 1. setup_href_for ──────────────────────────────────────────────────────


@pytest.mark.parametrize("provider,href", [
    ("zoho", "/crm"),
    ("mailbox", "/email"),
    ("whatsapp", "/whatsapp"),
    ("google_calendar", "/calendar?settings=1"),
    ("outlook_calendar", "/calendar?settings=1"),
    ("google_drive", "/knowledge/sources"),
    ("github", "/integrations"),
    ("apollo", "/integrations"),
    ("mcp", "/integrations"),
    ("not_a_provider", "/integrations"),
    ("mcp:tally", "/integrations?add=mcp&name=tally"),
    ("mcp:my-erp-2", "/integrations?add=mcp&name=my-erp-2"),
    ("mcp:Bad_Name", "/integrations"),
    ("mcp:a/../b", "/integrations"),
    ("mcp:", "/integrations"),
])
def test_setup_href_for_each_provider(provider: str, href: str) -> None:
    assert ic.setup_href_for(provider) == href


def test_each_owning_app_of_the_list_has_its_own_link() -> None:
    """No row of the connector list falls through to the console by
    accident. Only ``console`` and ``integrations`` link to the console."""
    for row in cl.connector_rows():
        app = row["owning_app"]
        expected = "integrations" if app == "console" else app
        assert ic._app_of(app) == expected, row["id"]
        assert ic.setup_href_for(row["id"]) == ic._SETUP_HREF[expected], row["id"]


def test_the_console_name_is_the_registry_name() -> None:
    assert ic._app_of("console") == "integrations"
    assert ic._app_of("integrations") == "integrations"
    assert ic._app_of("nope") == "integrations"
    assert ic._app_of(None) == "integrations"


# ── 2. The registry row of a calendar and a Drive connection ───────────────


def _conn(provider: str, app: str) -> conn_store.Connection:
    now = datetime(2026, 11, 14, tzinfo=UTC)
    return conn_store.Connection(
        id=str(uuid.uuid4()), organization_id=ORG, provider=provider, owning_app=app,
        scope_kind=conn_store.SCOPE_MEMBER, member_email=BEN, status="connected",
        provider_meta={}, scopes=(), connected_by=BEN, last_sync_at=None, last_error=None,
        created_at=now, updated_at=now)


@pytest.mark.parametrize("provider,app,href", [
    ("google_calendar", "calendar", "/calendar?settings=1"),
    ("outlook_calendar", "calendar", "/calendar?settings=1"),
    ("google_drive", "knowledge", "/knowledge/sources"),
])
def test_a_calendar_or_drive_row_links_to_its_own_app(provider: str, app: str, href: str) -> None:
    row = ic._registry_row(_conn(provider, app), _user(grants=ADMIN))
    assert (row.owning_app, row.setup_href) == (app, href)


def test_an_unknown_app_of_a_row_still_links_to_the_console() -> None:
    row = ic._registry_row(_conn("acme", "acme_app"), _user(grants=ADMIN))
    assert (row.owning_app, row.setup_href) == ("integrations", "/integrations")


# ── 3. may_connect ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("provider", [
    "zoho", "github", "apollo", "mcp:tally", "not_a_provider", "google_calendar", "mailbox",
])
def test_a_holder_of_integrations_manage_may_connect_each_name(provider: str) -> None:
    assert ic.may_connect(_user(grants=ADMIN), provider) is True


@pytest.mark.parametrize("provider,allowed", [
    ("google_calendar", True),
    ("outlook_calendar", True),
    ("google_drive", True),
    ("mailbox", True),
    ("whatsapp", True),
    ("zoho", False),
    ("github", False),
    ("apollo", False),
    ("mcp", False),
    ("mcp:tally", False),
    ("not_a_provider", False),
])
def test_a_member_may_connect_a_member_connection_only(provider: str, allowed: bool) -> None:
    assert ic.may_connect(_user(grants=MEMBER), provider) is allowed


def test_the_member_scope_comes_from_the_list() -> None:
    member = {r["id"] for r in cl.connector_rows() if r["scope"] == "member"}
    assert member == {"mailbox", "whatsapp", "google_calendar", "outlook_calendar",
                      "google_drive"}
    for row in cl.connector_rows():
        assert ic.may_connect(_user(grants=MEMBER), row["id"]) is (row["id"] in member)


# ── 4 and 5. The read ──────────────────────────────────────────────────────


class _World:
    def __init__(self) -> None:
        self.sessions = 0
        self.met_reads: list[str] = []
        self.owner = OWEN
        self.names: Any = ["zoho", "mcp:tally", "google_calendar"]
        self.met: frozenset[str] = frozenset({"zoho"})


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
        if member != w.owner or pid != PID:
            return None
        manifest: dict[str, Any] = {"studio": 1, "id": "app", "name": "App", "purpose": "T"}
        if w.names is not None:
            manifest["connections"] = w.names
        return _package(manifest)

    async def met_connections(db: Any, *, viewer: str) -> frozenset[str]:
        w.met_reads.append(viewer)
        return w.met

    monkeypatch.setattr(connections, "_tenant_session", session)
    monkeypatch.setattr(connections.store, "get_package", get_package)
    monkeypatch.setattr(connections.host_resolve, "met_connections", met_connections)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", "*")
    return w


def _client(user: UserContext) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def test_the_read_answers_one_row_for_each_name_in_order(world: _World) -> None:
    r = _client(_user()).get(PATH)
    assert r.status_code == 200, r.text
    rows = r.json()["connections"]
    assert [x["name"] for x in rows] == ["zoho", "mcp:tally", "google_calendar"]
    assert all(set(x) == ROW_KEYS for x in rows)
    assert rows[0] == {"name": "zoho", "label": "Zoho CRM", "met": True, "checkable": True,
                       "setup_href": "/crm", "can_connect": False}
    assert rows[1] == {"name": "mcp:tally", "label": "tally", "met": False, "checkable": True,
                       "setup_href": "/integrations?add=mcp&name=tally", "can_connect": False}
    assert rows[2] == {"name": "google_calendar", "label": "Google Calendar", "met": False,
                       "checkable": True, "setup_href": "/calendar?settings=1", "can_connect": True}
    assert world.sessions == 1
    assert world.met_reads == [OWEN]


def test_an_admin_may_connect_each_row(world: _World) -> None:
    rows = _client(_user(grants=ADMIN)).get(PATH).json()["connections"]
    assert [x["can_connect"] for x in rows] == [True, True, True]


def test_an_unknown_provider_id_reads_its_own_id(world: _World) -> None:
    world.names = ["acme"]
    rows = _client(_user()).get(PATH).json()["connections"]
    assert rows == [{"name": "acme", "label": "acme", "met": False, "checkable": True,
                     "setup_href": "/integrations", "can_connect": False}]


@pytest.mark.parametrize("grants", [MEMBER, ADMIN])
def test_a_provider_outside_the_registry_is_not_checkable_and_offers_no_connect(
    world: _World, grants: list[str],
) -> None:
    """A mailbox and a WhatsApp number live outside the registry, so the read
    cannot meet them (I9). Their row says so, and a Connect would never turn
    the line connected, so there is none, for an admin too."""
    world.names = ["mailbox", "whatsapp", "zoho"]
    rows = _client(_user(grants=grants)).get(PATH).json()["connections"]
    assert [(r["name"], r["met"], r["checkable"], r["can_connect"]) for r in rows] == [
        ("mailbox", False, False, False), ("whatsapp", False, False, False),
        ("zoho", True, True, grants == ADMIN)]
    assert rows[0]["label"] == "Email account"


def test_the_set_outside_the_registry_agrees_with_the_met_read() -> None:
    """``OUTSIDE_REGISTRY`` is the one set of providers that ``met_providers``
    cannot meet. Each name is a provider of the connector list, and its table
    is the table that the console reads for it. While ``_MET_SQL`` reads only
    the registry, the set holds the mailbox and the number. When I9 makes the
    read cover a table, its name must leave the set in the same change."""
    outside = conn_store.OUTSIDE_REGISTRY
    assert set(outside) <= {r["id"] for r in cl.connector_rows()}
    assert "email_accounts" in ic._MAILBOX_SQL and "wa_accounts" in ic._WHATSAPP_SQL
    for name, table in {"mailbox": "email_accounts", "whatsapp": "wa_accounts"}.items():
        read = table in conn_store._MET_SQL
        assert (name in outside) is (not read), (name, table)
    for name, table in outside.items():
        assert table not in conn_store._MET_SQL, name


def test_the_gate_off_answers_nothing_and_reads_no_registry(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", "")
    r = _client(_user()).get(PATH)
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert (world.sessions, world.met_reads) == (0, [])
    # Another org on the list is not this org.
    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", str(uuid.uuid4()))
    assert _client(_user()).get(PATH).status_code == 404
    assert (world.sessions, world.met_reads) == (0, [])


def test_studio_off_answers_404_with_no_session(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", "")
    r = _client(_user()).get(PATH)
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert (world.sessions, world.met_reads) == (0, [])


def test_a_bad_id_answers_404_with_no_session(world: _World) -> None:
    r = _client(_user()).get("/studio/packages/not-a-uuid/connections")
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0


def test_another_member_gets_404_and_no_met_read(world: _World) -> None:
    r = _client(_user(BEN)).get(PATH)
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.met_reads == []


@pytest.mark.parametrize("names", [None, [], "zoho", ["zoho", 3]])
def test_a_draft_with_no_list_of_names_reads_no_met_set(world: _World, names: Any) -> None:
    world.names = names
    r = _client(_user()).get(PATH)
    assert (r.status_code, r.json()) == (200, {"connections": []})
    assert world.met_reads == []


def test_the_query_changes_nothing(world: _World) -> None:
    r = _client(_user()).get(PATH + "?member=" + BEN + "&org=" + str(uuid.uuid4()))
    assert r.status_code == 200
    assert world.met_reads == [OWEN]


# ── 6. The source ──────────────────────────────────────────────────────────


def _tree() -> ast.Module:
    return ast.parse(SRC.read_text(encoding="utf-8"))


def test_the_module_reads_no_right_and_keeps_no_copy_of_the_map() -> None:
    text = SRC.read_text(encoding="utf-8")
    calls = [ast.unparse(n.func) for n in ast.walk(_tree()) if isinstance(n, ast.Call)]
    assert not [c for c in calls if c.endswith(("has_permission", ".has"))], calls
    assert "integrations:manage" not in text
    assert "_SETUP_HREF" not in text
    assert "/calendar" not in text and "/crm" not in text
    assert "text(" not in text and "SELECT" not in text.upper().replace("SELECTED", "")


def test_the_module_uses_the_one_met_read() -> None:
    calls = [ast.unparse(n.func) for n in ast.walk(_tree()) if isinstance(n, ast.Call)]
    assert calls.count("host_resolve.met_connections") == 1, calls
    assert not [c for c in calls if "met_providers" in c], calls
    assert "console.setup_href_for" in calls and "console.may_connect" in calls


def test_the_handler_reads_the_switch_first() -> None:
    fn = next(n for n in ast.walk(_tree())
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "package_connections")
    body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    assert "flags.studio_allowed(user.organization_id)" in ast.unparse(body[0])
    assert "connection_gate_on(user.organization_id)" in ast.unparse(body[2])
    assert isinstance(body[3], ast.AsyncWith)


# ── 7. The R8 twin runs in CI ──────────────────────────────────────────────


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_lists(workflow: str) -> None:
    text = (ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
    assert re.search(r"^[ \t]+tests/unit/test_studio_connect_in_place_r8\.py(?: \\)?$", text, re.M)
