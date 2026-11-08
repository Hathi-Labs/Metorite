"""WS-39 LP-1a — project updates with health (R8).

Spec: ``project-docs/specs/project_management_app.md`` §14.2 row LP-1 and the
design §14.2a. Code: ``gateway/routes/projects/updates.py`` (the one writer),
the two MCP tools of ``agent_edge/tools.py`` and the two chat tools of
``skill_projects/inbox.py``. Migration: the file that creates
``pm_project_updates`` (363 at build, 373 at the repair round).

Two halves.

**Database-free (always runs).** The three copies of the health, the cap and
the refusal detail agree. The route pair is in the MCP sets. Only the route
module writes the table. The migration holds its CHECKs, the trigger and FORCE
row level security. The flag has one reader. The chat tools say that the
feature is off, and show no card then.

**R8 (a real database, as a NON-privileged role, on a promoted catalog of
its own).** The real gateway app and the real MCP edge. Org A: a member who
sees a space and its projects, a member who sees less, and an agent
connection of each kind (write, read only, pinned to one project). Org B: a
member, a project and one update.

Run, on a PRIVATE database (one R8 suite at a time)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_pm_project_updates_r8.py -v -rs

Without the database the R8 half SKIPS, and the run reads green. A skip is
not a pass.

**Who may post.** The right of a task write on the project
(``core.load_visible_project`` with ``lock_share``, the check of
``POST /tasks``). Projects has no read-only grant for a person: a member who
sees a project may add work to it. So "read but not edit" has two real forms,
and both are tested. A filed project reads and takes no new update, as it
takes no new task. A connection that holds ``projects:read`` only reads and
gets 401 on the post (branch 0, ``write_scope``).

**The mutations (R7).** Commit before each, restore with ``git checkout``.
``U`` is ``apps/services/gateway/gateway/routes/projects/updates.py``.

* M1. ``U``: drop ``dependencies=_ENABLED`` from both routes.
  ``test_with_the_flag_off_both_routes_answer_404_and_write_nothing`` goes red.
* M2. ``U`` ``post_update``: take ``author`` from the body (``extra="ignore"``
  and the body's value). ``test_a_member_posts_and_the_author_is_the_session``
  goes red.
* M3. ``U``: replace ``load_visible_project`` with a plain read of the row.
  ``test_a_member_who_cannot_see_the_project_gets_404_on_both_routes``,
  ``test_tenant_b_gets_404_and_the_app_role_obeys_rls`` and
  ``test_a_pinned_agent_stays_in_its_pin`` go red.
* M4. ``U`` ``post_update``: drop ``assert_not_archived``.
  ``test_read_but_not_edit_cannot_post`` goes red.
* M5. The migration: drop the CHECK ``pm_project_updates_health``.
  ``test_the_health_check_holds_in_the_route_and_the_database`` goes red.
* M6. ``acb_auth.agent_oauth``: drop the POST pair from ``MCP_WRITE_ROUTES``.
  ``test_the_route_pair_is_in_the_mcp_sets`` goes red.
* M7. ``U`` ``list_updates``: drop ``AND deleted_at IS NULL``.
  ``test_the_author_deletes_and_the_update_leaves_every_read`` goes red.
* M8. ``U`` ``delete_update``: drop ``assert_may_delete_update``.
  ``test_another_member_gets_403_and_a_projects_admin_deletes`` goes red.
* M9. ``U``: drop the DELETE route (the code before the repair round).
  ``test_the_author_deletes_and_the_update_leaves_every_read`` goes red.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

from acb_auth import agent_oauth, agent_tokens
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from tests.unit import test_mcp_edge_delete as d
from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w

_DB_GATE = w._DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS pm_project_updates (" in p.read_text(encoding="utf-8"))
_UPDATES = _GATEWAY / "routes" / "projects" / "updates.py"

#: The members. The domain is the one ``live_app`` allows.
LEAD = "lead@mcpwrites.test"
OUTSIDER = "outsider@mcpwrites.test"
#: A Projects admin (`projects:settings:write`), who may delete any update.
ADMIN = "admin@mcpwrites.test"
BOB = "bob@mcpwrites.test"

_APP_ROLE = "acb_app_lp1aupd"
_APP_PW = "lp1aupd_scratch_pw"

#: A text that no refusal may carry.
SECRET_TEXT = "The vendor quietly dropped the order"


@pytest.fixture(autouse=True)
def _updates_on(monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "projects_updates_orgs", "*", raising=False)


def _updates():
    from gateway.routes.projects import updates

    return updates


def _path(project: str) -> str:
    return f"/projects/nodes/{project}/updates"


def _authority():
    from gateway.routes.projects import authority

    return authority


# ═══════════════════════════════════════════════════════════════════════════
# Database-free
# ═══════════════════════════════════════════════════════════════════════════


def test_the_three_copies_of_the_health_and_the_cap_agree() -> None:
    """The edge and the chat may not import the gateway, so each holds a copy."""
    from agent_edge import tools as edge
    from skill_projects import inbox

    u = _updates()
    assert u.HEALTHS == edge.UPDATE_HEALTHS == tuple(inbox.UPDATE_HEALTHS)
    assert u.HEALTH_LABELS == inbox.UPDATE_HEALTHS
    assert u.MAX_UPDATE_CHARS == edge.MAX_UPDATE_CHARS == inbox.MAX_UPDATE_CHARS
    assert u.UPDATES_OFF == agent_oauth.PROJECT_UPDATES_OFF
    assert (404, agent_oauth.PROJECT_UPDATES_OFF) in edge._UPDATE_DETAILS
    assert (422, u.NOT_A_PROJECT) in edge._UPDATE_DETAILS
    assert u.MAX_PAGE == edge.UPDATES_PAGE_MAX == inbox.UPDATES_PAGE_MAX
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert f"char_length(body) <= {u.MAX_UPDATE_CHARS}" in sql


def test_the_route_pair_is_in_the_mcp_sets() -> None:
    """The POST is a write pair and the GET a read pair (§14.2a). Mutation
    M6: drop the POST pair, and this goes red."""
    post = ("POST", "/projects/nodes/{project_id}/updates")
    get = ("GET", "/projects/nodes/{project_id}/updates")
    assert post in agent_oauth.MCP_WRITE_ROUTES
    assert post in agent_oauth.MCP_ROUTES
    assert get in agent_oauth.MCP_ROUTES
    assert get not in agent_oauth.MCP_WRITE_ROUTES


def test_only_the_update_module_writes_the_table() -> None:
    write = re.compile(
        r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+pm_project_updates\b", re.IGNORECASE)
    found = []
    for base in (_ROOT / "apps", _ROOT / "packages", _ROOT / "scripts"):
        for path in base.rglob("*.py"):
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            if write.search(path.read_text(encoding="utf-8", errors="replace")):
                found.append(path.relative_to(_ROOT).as_posix())
    assert found == ["apps/services/gateway/gateway/routes/projects/updates.py"], found


def test_the_migration_holds_the_checks_the_rls_and_the_trigger() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in sql and "FORCE  ROW LEVEL SECURITY" in sql
    assert "REFERENCES organization (id) ON DELETE CASCADE" in sql
    assert "REFERENCES pm_projects (id) ON DELETE CASCADE" in sql
    assert re.search(
        r"health\s+IN\s+\('on_track',\s*'at_risk',\s*'off_track'\)", sql)
    assert "pm_project_updates_same_org" in sql
    assert "author = lower(author)" in sql
    assert "drafted_by_ai    BOOLEAN NOT NULL DEFAULT false" in sql
    assert "(project_id, posted_at DESC, id DESC)\n    WHERE deleted_at IS NULL" in sql
    # The soft delete: both columns set together, the actor in lower case.
    assert "deleted_at       TIMESTAMPTZ NULL" in sql
    assert "(deleted_at IS NULL) = (deleted_by IS NULL)" in sql
    assert "deleted_by = lower(deleted_by)" in sql
    # The FK to pm_projects takes SHARE ROW EXCLUSIVE, so the file bounds its wait.
    assert "SET LOCAL lock_timeout" in sql
    # Expand only (R6).
    for banned in ("DROP TABLE", "DROP COLUMN", "RENAME", "ALTER TABLE pm_projects"):
        assert banned not in sql, banned


@pytest.mark.parametrize(("value", "org", "on"), [
    ("", "11111111-2222-4333-8444-555555555555", False),
    ("*", "11111111-2222-4333-8444-555555555555", True),
    ("11111111-2222-4333-8444-555555555555", "11111111-2222-4333-8444-555555555555", True),
    ("aaaaaaaa-2222-4333-8444-555555555555", "11111111-2222-4333-8444-555555555555", False),
    ("*", None, False),
])
def test_the_flag_has_one_reader_and_is_off_by_default(monkeypatch, value, org, on) -> None:
    from acb_common import get_settings
    from acb_common.settings import Settings

    assert Settings.model_fields["projects_updates_orgs"].default == ""
    monkeypatch.setattr(get_settings(), "projects_updates_orgs", value, raising=False)
    assert _updates().updates_allowed(org) is on


def test_the_flag_has_one_reader_in_the_tree() -> None:
    readers = []
    for base in (_ROOT / "apps", _ROOT / "packages"):
        for path in base.rglob("*.py"):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            if "projects_updates_orgs" in path.read_text(encoding="utf-8", errors="replace"):
                readers.append(path.relative_to(_ROOT).as_posix())
    assert sorted(readers) == [
        "apps/services/gateway/gateway/routes/projects/updates.py",
        "packages/acb_common/acb_common/settings.py",
    ], readers


def test_the_body_names_no_author_and_no_org() -> None:
    """R11: the body model forbids every key but the two."""
    from pydantic import ValidationError

    u = _updates()
    u.UpdateIn(health="on_track", body="Fine.")
    for extra in ({"author": "x@y.z"}, {"organization_id": str(uuid.uuid4())},
                  {"drafted_by_ai": True}):
        with pytest.raises(ValidationError):
            u.UpdateIn(health="on_track", body="Fine.", **extra)
    with pytest.raises(ValidationError):
        u.UpdateIn(health="great", body="Fine.")


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert re.search(
            r"^\s+tests/unit/test_pm_project_updates_r8\.py \\?$", workflow, re.M), name


async def test_the_chat_tools_say_the_feature_is_off_and_show_no_card(monkeypatch) -> None:
    import skill_projects
    from skill_projects import inbox

    from tests.unit._projects_agent_fakes import FakeResponse, approve, fake_gateway, writes

    pid = str(uuid.uuid4())

    def responder(call: dict) -> Any:
        if call["path"] == f"/projects/nodes/{pid}":
            return {"id": pid, "name": "Ops", "parent_project_id": str(uuid.uuid4()),
                    "kind": "project"}
        return FakeResponse({"detail": agent_oauth.PROJECT_UPDATES_OFF}, status_code=404)

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    assert await skill_projects.list_project_updates(pid) == inbox.UPDATES_OFF
    out = await skill_projects.post_project_update(pid, "at risk", "The vendor slipped.")
    assert out == inbox.UPDATES_OFF
    assert asked == [], "a dark flag shows no card"
    assert writes(calls) == []


async def test_the_chat_post_says_archived_before_any_card(monkeypatch) -> None:
    """P3-1: a filed project takes no update. The chat says so before the
    card, as `writes._file_destinations` does for a task."""
    import skill_projects

    from tests.unit._projects_agent_fakes import approve, fake_gateway, writes

    pid = str(uuid.uuid4())

    def responder(call: dict) -> Any:
        if call["path"] == f"/projects/nodes/{pid}":
            return {"id": pid, "name": "Filed", "parent_project_id": str(uuid.uuid4()),
                    "kind": "project", "archived_at": "2026-11-01T00:00:00+00:00"}
        return {"rows": [], "next_before": None}

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.post_project_update(pid, "on track", "Fine.")
    assert "archived" in out
    assert asked == [] and writes(calls) == []


async def test_the_chat_delete_quotes_the_server_before_any_card(monkeypatch) -> None:
    """A member who may not delete gets the server's words and no card."""
    import skill_projects

    from tests.unit._projects_agent_fakes import approve, fake_gateway, writes

    pid, uid = str(uuid.uuid4()), str(uuid.uuid4())

    def responder(call: dict) -> Any:
        if call["path"] == f"/projects/nodes/{pid}":
            return {"id": pid, "name": "Ops", "parent_project_id": str(uuid.uuid4()),
                    "kind": "project"}
        return {"rows": [{"id": uid, "project_id": pid, "author": "lead@x.io",
                          "health": "at_risk", "body": "Late.", "can_delete": False,
                          "delete_refusal": _authority().UPDATE_DELETE_REFUSED}],
                "next_before": None}

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.delete_project_update(pid, uid)
    assert _authority().UPDATE_DELETE_REFUSED in out
    assert asked == [] and writes(calls) == []


async def test_the_chat_post_asks_for_a_health_it_cannot_read(monkeypatch) -> None:
    """The member picks the health (§14.2a). A word the tool cannot map is a
    question back, with no read and no card."""
    import skill_projects

    from tests.unit._projects_agent_fakes import approve, fake_gateway

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, lambda _c: {})
    out = await skill_projects.post_project_update(str(uuid.uuid4()), "great", "Fine.")
    assert "on_track, at_risk or off_track" in out
    assert asked == [] and calls == []


# ═══════════════════════════════════════════════════════════════════════════
# R8
# ═══════════════════════════════════════════════════════════════════════════


def _connection(seeder: Any, email: str, scopes: tuple[str, ...],
                pins: tuple[str, ...] = ()) -> tuple[str, str]:
    """A live connection of the member *email*: ``(id, raw token)``."""
    cid = str(seeder.one(
        "INSERT INTO agent_connection (organization_id, member_email, client_id,"
        " client_name, client_origin, scopes, pinned_projects, include_personal,"
        " expires_at) VALUES (CAST(:o AS uuid), :m, 'https://client.example/meta.json',"
        " 'Client', :origin, CAST(:scopes AS text[]), CAST(:pins AS uuid[]), true,"
        " now() + interval '1 day') RETURNING id",
        o=seeder.org, m=email, origin=w.ORIGIN, scopes=list(scopes), pins=list(pins)))
    raw = agent_tokens.new_token()
    seeder.one("INSERT INTO agent_token (organization_id, connection_id, kind, token_hash,"
               " expires_at, client_id, resource, scope) VALUES (CAST(:o AS uuid),"
               " CAST(:c AS uuid), 'access', :h, now() + interval '1 hour',"
               " 'https://client.example/meta.json', :r, :s) RETURNING id",
               o=seeder.org, c=cid, h=agent_tokens.hash_token(raw), r=w.RESOURCE,
               s=" ".join(scopes))
    return cid, raw


def _project(seeder: Any, name: str, *, grants: tuple[str, ...] = (),
             parent: str | None = None, archived: bool = False) -> str:
    pid = str(seeder.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, parent_project_id, owns_statuses, archived_at) VALUES"
        " (:n, 'active', 'manual', :m, CAST(:o AS uuid), 'Asia/Kolkata',"
        " CAST(:p AS uuid), CAST(:own AS boolean),"
        " CASE WHEN CAST(:arch AS boolean) THEN now() END) RETURNING id",
        n=name, m=w.OWNER, o=seeder.org, p=parent, own=parent is None, arch=archived))
    for subject in grants:
        seeder.one("INSERT INTO pm_project_grants (project_id, subject, created_by,"
                   " organization_id) VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid))"
                   " RETURNING project_id", p=pid, s=subject, a=w.OWNER, o=seeder.org)
    return pid


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: two members in org A, one in org B."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_lp1aupd"
    w._clone_promoted_database(admin_url, dedicated)
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{_APP_ROLE}')"
            f" THEN CREATE ROLE {_APP_ROLE} LOGIN; END IF; END $$;"))
        c.execute(text(f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
                       "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"))

    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org_a, "lp1aa"), (org_b, "lp1ab")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": oid, "s": slug})
    a = w._Seeder(eng, org_a)
    a.member(LEAD, ["feature:projects"])
    a.member(OUTSIDER, ["feature:projects"])
    a.member(ADMIN, ["feature:projects", "projects:settings:write"])
    b = w._Seeder(eng, org_b)
    b.member(BOB, ["feature:projects"])

    world_ = SimpleNamespace(
        admin=eng, org=org_a, org_b=org_b, seeder=a, seeder_b=b, c={}, tok={},
        app_dsn=admin_url.set(
            database=dedicated, username=_APP_ROLE, password=_APP_PW,
        ).render_as_string(hide_password=False))
    # SPACE grants the org, so both members see TEAM, OTHER and FILED. HIDDEN
    # sits in a space that only LEAD sees.
    world_.space = _project(a, "Everyone", grants=("org",))
    world_.team = _project(a, "Team", parent=world_.space)
    world_.other = _project(a, "Other", parent=world_.space)
    world_.filed = _project(a, "Filed", parent=world_.space, archived=True)
    world_.hidden_space = _project(a, "Leads only", grants=(LEAD,))
    world_.hidden = _project(a, "Hidden", parent=world_.hidden_space)
    world_.space_b = _project(b, "Bob space", grants=(BOB,))
    world_.project_b = _project(b, "Bob project", parent=world_.space_b)
    world_.update_b = str(b.one(
        "INSERT INTO pm_project_updates (project_id, author, health, body)"
        " VALUES (CAST(:p AS uuid), :a, 'on_track', 'Org B is fine.') RETURNING id",
        p=world_.project_b, a=BOB))
    for name, scopes, pins in (
            ("write", w.WRITE_SCOPES, ()),
            ("read", ("projects:read",), ()),
            ("pinned", w.WRITE_SCOPES, (world_.team,))):
        world_.c[name], world_.tok[name] = _connection(a, LEAD, scopes, pins)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


redis_fake = tw.redis_fake
live_app = tw.live_app
live = tw.live


def _rows(world, sql: str, *, org: str | None = None, **params: Any) -> list[Any]:
    with world.admin.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                  {"t": org or world.org})
        return list(c.execute(text(sql), params))


def _count(world, project: str) -> int:
    return int(_rows(world, "SELECT count(*) AS n FROM pm_project_updates"
                            " WHERE project_id = CAST(:p AS uuid)", p=project)[0].n)


def _live(world, update: str) -> bool:
    """True while the update is not deleted."""
    return bool(_rows(world, "SELECT 1 FROM pm_project_updates WHERE id = CAST(:i AS uuid)"
                             " AND deleted_at IS NULL", i=update))


async def _as(world, who: str, method: str, path: str,
              body: dict[str, Any] | None = None) -> httpx.Response:
    return await d._person(world, who, method, path, body)


async def _post(world, who: str, project: str, health: str = "on_track",
                body: str = "On plan this week.", **extra: Any) -> httpx.Response:
    return await _as(world, who, "POST", _path(project),
                     {"health": health, "body": body, **extra})


async def _signed(world, name: str, method: str, path: str,
                  body: dict[str, Any] | None = None, query: str = "") -> httpx.Response:
    return await tw._send(world, method, path, body=body, query=query, signed=name)


class TestTheRoutes:
    pytestmark = _DB_GATE

    async def test_a_member_posts_and_the_author_is_the_session(self, live):
        """M2: an author from the body would fail here."""
        r = await _post(live, LEAD, live.team, "at_risk", "The vendor slipped a week.")
        assert r.status_code == 201, r.text
        got = r.json()
        assert got["author"] == LEAD and got["health"] == "at_risk"
        assert got["drafted_by_ai"] is False and got["via"] is None
        row = _rows(live, "SELECT organization_id, author, meta FROM pm_project_updates"
                          " WHERE id = CAST(:i AS uuid)", i=got["id"])[0]
        assert str(row.organization_id) == live.org and row.author == LEAD
        assert row.meta == {}
        # One `system` activity on the project, and no new activity type.
        acts = _rows(live, "SELECT type, created_by, meta FROM pm_activities"
                           " WHERE project_id = CAST(:p AS uuid) AND task_id IS NULL"
                           " AND meta->>'update_id' = :u", p=live.team, u=got["id"])
        assert [(a.type, a.created_by) for a in acts] == [("system", LEAD)]
        assert acts[0].meta["health"] == "at_risk"
        # A body that names another author or an org is refused whole.
        before = _count(live, live.team)
        for extra in ({"author": OUTSIDER}, {"organization_id": live.org_b}):
            r = await _post(live, LEAD, live.team, **extra)
            assert r.status_code == 422, r.text
        assert _count(live, live.team) == before

    async def test_the_list_is_newest_first_and_pages_by_cursor(self, live):
        project = live.other
        made = []
        for n in range(3):
            r = await _post(live, OUTSIDER, project, "on_track", f"Week {n}.")
            assert r.status_code == 201, r.text
            made.append(r.json()["id"])
        r = await _as(live, LEAD, "GET", _path(project) + "?limit=2")
        assert r.status_code == 200, r.text
        page = r.json()
        assert [u["id"] for u in page["rows"]] == made[::-1][:2]
        assert page["next_before"] == made[1]
        r = await _as(live, LEAD, "GET", _path(project) + f"?limit=2&before={made[1]}")
        rest = r.json()
        assert [u["id"] for u in rest["rows"]] == [made[0]]
        assert rest["next_before"] is None
        # A cursor of another project, or no update at all, is refused.
        other = (await _post(live, LEAD, live.team)).json()["id"]
        for bad in (other, str(uuid.uuid4()), "not-an-id"):
            r = await _as(live, LEAD, "GET", _path(project) + f"?before={bad}")
            assert r.status_code == 422, (bad, r.text)

    async def test_a_member_who_cannot_see_the_project_gets_404_on_both_routes(self, live):
        """M3. A hidden project and a missing one read the same."""
        assert (await _post(live, LEAD, live.hidden, body=SECRET_TEXT)).status_code == 201
        r = await _as(live, OUTSIDER, "GET", _path(live.hidden))
        assert r.status_code == 404, r.text
        hidden = r.json()
        r = await _as(live, OUTSIDER, "GET", _path(str(uuid.uuid4())))
        assert r.status_code == 404 and r.json() == hidden
        r = await _as(live, OUTSIDER, "GET", _path("not-an-id"))
        assert r.status_code == 404 and r.json() == hidden
        before = _count(live, live.hidden)
        r = await _post(live, OUTSIDER, live.hidden)
        assert r.status_code == 404 and r.json() == hidden
        assert SECRET_TEXT not in r.text
        assert _count(live, live.hidden) == before

    async def test_tenant_b_gets_404_and_the_app_role_obeys_rls(self, live):
        mine = (await _post(live, LEAD, live.team, body="Org A only.")).json()["id"]
        for method in ("GET", "POST"):
            body = {"health": "off_track", "body": "x"} if method == "POST" else None
            r = await _as(live, BOB, method, _path(live.team), body)
            assert r.status_code == 404, (method, r.text)
        r = await _as(live, LEAD, "GET", _path(live.project_b))
        assert r.status_code == 404, r.text
        eng = create_engine(live.app_dsn)
        try:
            with eng.begin() as c:
                role = c.execute(text(
                    "SELECT rolbypassrls, rolsuper FROM pg_roles"
                    " WHERE rolname = current_user")).first()
                assert not role.rolbypassrls and not role.rolsuper
                flags = c.execute(text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
                    " WHERE relname = 'pm_project_updates'")).first()
                assert tuple(flags) == (True, True)
                c.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                          {"t": live.org_b})
                seen = {str(x.id) for x in c.execute(text(
                    "SELECT id FROM pm_project_updates")).fetchall()}
                assert seen == {live.update_b}
                assert mine not in seen
        finally:
            eng.dispose()
        fresh = create_engine(live.app_dsn)
        try:
            with fresh.begin() as c:
                none = c.execute(text("SELECT count(*) FROM pm_project_updates")).scalar()
                assert none == 0, "an unbound session reads nothing"
        finally:
            fresh.dispose()

    async def test_the_database_refuses_a_cross_org_update(self, live):
        sql = ("INSERT INTO pm_project_updates (organization_id, project_id, author,"
               " health, body) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 'x@y.z',"
               " 'on_track', 'x')")
        for org, project in ((live.org, live.project_b), (live.org_b, live.team)):
            with pytest.raises(DBAPIError), live.admin.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": org})
                c.execute(text(sql), {"o": org, "p": project})

    async def test_the_health_check_holds_in_the_route_and_the_database(self, live):
        """M5: without the CHECK the hand-written row lands."""
        before = _count(live, live.team)
        for health in ("great", "", "On track"):
            r = await _post(live, LEAD, live.team, health)
            assert r.status_code == 422, (health, r.text)
        for body in ("", "   ", "a\x00b", "x" * 20_001):
            r = await _post(live, LEAD, live.team, body=body)
            assert r.status_code == 422, (body[:5], r.text)
        assert _count(live, live.team) == before
        for bad in ("great", "ON_TRACK"):
            with pytest.raises(DBAPIError), live.admin.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                          {"t": live.org})
                c.execute(text(
                    "INSERT INTO pm_project_updates (project_id, author, health, body)"
                    " VALUES (CAST(:p AS uuid), 'x@y.z', :h, 'x')"),
                    {"p": live.team, "h": bad})
        with pytest.raises(DBAPIError), live.admin.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": live.org})
            c.execute(text(
                "INSERT INTO pm_project_updates (project_id, author, health, body)"
                " VALUES (CAST(:p AS uuid), 'Mixed@Case.z', 'on_track', 'x')"),
                {"p": live.team})

    async def test_read_but_not_edit_cannot_post(self, live):
        """The two forms of "read but not edit" in Projects. M4 turns the
        first red. Branch 0 holds the second."""
        # A filed project: a member reads it and adds no new update to it.
        r = await _as(live, OUTSIDER, "GET", _path(live.filed))
        assert r.status_code == 200, r.text
        r = await _post(live, OUTSIDER, live.filed)
        assert r.status_code == 422, r.text
        assert "is archived" in r.json()["detail"]
        assert _count(live, live.filed) == 0
        # A connection that holds projects:read only.
        r = await _signed(live, "read", "GET", _path(live.team))
        assert r.status_code == 200, r.text
        before = _count(live, live.team)
        r = await _signed(live, "read", "POST", _path(live.team),
                          {"health": "on_track", "body": "From a read connection."})
        assert r.status_code == 401, r.text
        assert _count(live, live.team) == before

    async def test_a_space_takes_no_update(self, live):
        r = await _post(live, LEAD, live.space)
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == _updates().NOT_A_PROJECT
        assert _count(live, live.space) == 0

    async def test_with_the_flag_off_both_routes_answer_404_and_write_nothing(
            self, live, monkeypatch):
        """M1."""
        from acb_common import get_settings

        before = _count(live, live.team)
        for off in ("", live.org_b):
            monkeypatch.setattr(get_settings(), "projects_updates_orgs", off, raising=False)
            r = await _as(live, LEAD, "GET", _path(live.team))
            assert r.status_code == 404, r.text
            assert r.json()["detail"] == agent_oauth.PROJECT_UPDATES_OFF
            r = await _post(live, LEAD, live.team)
            assert r.status_code == 404, r.text
            r = await _as(live, LEAD, "DELETE", f"{_path(live.team)}/{uuid.uuid4()}")
            assert r.status_code == 404, r.text
            assert r.json()["detail"] == agent_oauth.PROJECT_UPDATES_OFF
        assert _count(live, live.team) == before
        monkeypatch.setattr(get_settings(), "projects_updates_orgs", live.org, raising=False)
        assert (await _as(live, LEAD, "GET", _path(live.team))).status_code == 200


class TestTheAgent:
    pytestmark = _DB_GATE

    async def test_a_pinned_agent_stays_in_its_pin(self, live):
        """M3. OTHER is visible to the member, and outside the pin."""
        r = await _signed(live, "pinned", "POST", _path(live.team),
                          {"health": "on_track", "body": "From the pinned agent."})
        assert r.status_code == 201, r.text
        got = r.json()
        assert got["author"] == LEAD and got["via"] == w.R8_VIA
        assert got["drafted_by_ai"] is True, "a signed agent wrote the text"
        assert (await _signed(live, "pinned", "GET", _path(live.team))).status_code == 200
        before = _count(live, live.other)
        for method in ("GET", "POST"):
            body = {"health": "on_track", "body": "x"} if method == "POST" else None
            r = await _signed(live, "pinned", method, _path(live.other), body)
            assert r.status_code == 404, (method, r.text)
        assert _count(live, live.other) == before
        # The same member, with no pin, reaches OTHER.
        assert (await _signed(live, "write", "GET", _path(live.other))).status_code == 200

    async def test_the_mcp_tools_post_once_per_key_and_list_newest_first(self, live):
        args = {"project_id": live.team, "health": "off_track",
                "body": "Blocked on the supplier.", "idempotency_key": "lp1a-key-0001"}
        first = await tw._mcp(live, "write", "post_project_update", args)
        assert first["isError"] is False, first
        made = first["structuredContent"]
        assert made["created"] is True
        assert made["update"]["body"] == {"untrusted_text": "Blocked on the supplier."}
        assert made["update"]["author"] == LEAD
        assert made["update"]["via"] == w.R8_VIA
        again = await tw._mcp(live, "write", "post_project_update", args)
        assert again["structuredContent"] == {
            "id": made["id"], "project_id": live.team, "created": False}
        rows = _rows(live, "SELECT count(*) AS n FROM pm_project_updates"
                           " WHERE body = 'Blocked on the supplier.'")
        assert rows[0].n == 1, "the same key posts one update"
        listed = await tw._mcp(live, "read", "list_project_updates",
                               {"project_id": live.team, "limit": 1})
        assert listed["isError"] is False, listed
        newest = listed["structuredContent"]["updates"][0]
        assert newest["id"] == made["id"] and newest["health"] == "off_track"
        assert newest["body"] == {"untrusted_text": "Blocked on the supplier."}

    async def test_the_mcp_tools_say_the_feature_is_off_and_stay_in_the_pin(
            self, live, monkeypatch):
        from acb_common import get_settings
        from agent_edge import tools as edge

        out = await tw._mcp(live, "pinned", "list_project_updates",
                            {"project_id": live.other})
        assert out["isError"] is True
        assert out["content"][0]["text"] == edge.TEXT_PROJECT_NOT_FOUND
        out = await tw._mcp(live, "pinned", "post_project_update", {
            "project_id": live.other, "health": "on_track", "body": "x",
            "idempotency_key": "lp1a-key-pin1"})
        assert out["isError"] is True
        assert out["content"][0]["text"] == edge.TEXT_PROJECT_NOT_FOUND
        out = await tw._mcp(live, "write", "post_project_update", {
            "project_id": live.space, "health": "on_track", "body": "x",
            "idempotency_key": "lp1a-key-space"})
        assert out["content"][0]["text"] == edge.TEXT_UPDATE_NOT_A_PROJECT
        monkeypatch.setattr(get_settings(), "projects_updates_orgs", "", raising=False)
        before = _count(live, live.team)
        out = await tw._mcp(live, "write", "list_project_updates", {"project_id": live.team})
        assert out["content"][0]["text"] == edge.TEXT_UPDATES_OFF
        out = await tw._mcp(live, "write", "post_project_update", {
            "project_id": live.team, "health": "on_track", "body": "x",
            "idempotency_key": "lp1a-key-off1"})
        assert out["content"][0]["text"] == edge.TEXT_UPDATES_OFF
        assert _count(live, live.team) == before


class TestTheDelete:
    """The soft delete (§14.2a): the author or a Projects admin, one activity,
    and no read shows the update again. It is not on the MCP."""

    pytestmark = _DB_GATE

    async def test_the_author_deletes_and_the_update_leaves_every_read(self, live):
        """M7: drop `AND deleted_at IS NULL` from the list and the update shows."""
        older = (await _post(live, LEAD, live.other, "on_track", "Older news.")).json()
        mine = (await _post(live, LEAD, live.other, "at_risk", "Delete me.")).json()
        assert mine["can_delete"] is True and mine["delete_refusal"] is None
        summary = (await _as(live, LEAD, "GET", f"/projects/nodes/{live.other}/summary")).json()
        assert summary["latest_update"]["id"] == mine["id"]
        r = await _as(live, LEAD, "DELETE", f"{_path(live.other)}/{mine['id']}")
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] is True and r.json()["id"] == mine["id"]
        rows = (await _as(live, LEAD, "GET", _path(live.other))).json()["rows"]
        assert mine["id"] not in {u["id"] for u in rows}
        assert older["id"] in {u["id"] for u in rows}
        summary = (await _as(live, LEAD, "GET", f"/projects/nodes/{live.other}/summary")).json()
        assert summary["latest_update"]["id"] == older["id"], "the line falls back"
        row = _rows(live, "SELECT deleted_at, deleted_by FROM pm_project_updates"
                          " WHERE id = CAST(:i AS uuid)", i=mine["id"])[0]
        assert row.deleted_at is not None and row.deleted_by == LEAD
        acts = _rows(live, "SELECT type, created_by, body FROM pm_activities"
                           " WHERE project_id = CAST(:p AS uuid) AND task_id IS NULL"
                           " AND meta->>'update_id' = :u ORDER BY created_at",
                     p=live.other, u=mine["id"])
        assert [(a.type, a.created_by) for a in acts] == [("system", LEAD), ("system", LEAD)]
        assert acts[-1].body == "Deleted a project update"
        again = await _as(live, LEAD, "DELETE", f"{_path(live.other)}/{mine['id']}")
        assert again.status_code == 404, again.text

    async def test_another_member_gets_403_and_a_projects_admin_deletes(self, live):
        """M8: drop `assert_may_delete_update` and the other member deletes."""
        made = (await _post(live, LEAD, live.other, "off_track", "Not yours.")).json()
        listed = (await _as(live, OUTSIDER, "GET", _path(live.other))).json()["rows"]
        seen = next(u for u in listed if u["id"] == made["id"])
        assert seen["can_delete"] is False
        assert seen["delete_refusal"] == _authority().UPDATE_DELETE_REFUSED
        r = await _as(live, OUTSIDER, "DELETE", f"{_path(live.other)}/{made['id']}")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == _authority().UPDATE_DELETE_REFUSED
        assert _live(live, made["id"]), "a refused delete changes nothing"
        r = await _as(live, ADMIN, "DELETE", f"{_path(live.other)}/{made['id']}")
        assert r.status_code == 200, r.text
        assert not _live(live, made["id"])
        row = _rows(live, "SELECT deleted_by FROM pm_project_updates"
                          " WHERE id = CAST(:i AS uuid)", i=made["id"])[0]
        assert row.deleted_by == ADMIN

    async def test_a_delete_across_tenants_or_out_of_sight_is_404(self, live):
        hidden = (await _post(live, LEAD, live.hidden, body=SECRET_TEXT)).json()
        team = (await _post(live, LEAD, live.team, body="A team update.")).json()
        cases = [
            # Org B cannot see org A's project at all.
            (BOB, live.team, team["id"]),
            # A member who cannot see the project.
            (OUTSIDER, live.hidden, hidden["id"]),
            # An update of org B, named through a project of org A.
            (LEAD, live.team, live.update_b),
            # An update of another project of the same org.
            (LEAD, live.other, team["id"]),
            (LEAD, live.team, str(uuid.uuid4())),
            (LEAD, live.team, "not-an-id"),
        ]
        for who, project, update in cases:
            r = await _as(live, who, "DELETE", f"{_path(project)}/{update}")
            assert r.status_code == 404, (who, project, update, r.text)
            assert SECRET_TEXT not in r.text
        assert _live(live, hidden["id"]) and _live(live, team["id"])
        rows_b = _rows(live, "SELECT deleted_at FROM pm_project_updates"
                             " WHERE id = CAST(:i AS uuid)", org=live.org_b, i=live.update_b)
        assert rows_b[0].deleted_at is None

    async def test_the_delete_is_off_the_mcp_and_off_with_the_flag(self, live, monkeypatch):
        from acb_common import get_settings

        made = (await _post(live, LEAD, live.team, body="Stays.")).json()
        path = f"{_path(live.team)}/{made['id']}"
        r = await _signed(live, "write", "DELETE", path)
        assert r.status_code == 401, r.text
        assert _live(live, made["id"])
        monkeypatch.setattr(get_settings(), "projects_updates_orgs", "", raising=False)
        r = await _as(live, LEAD, "DELETE", path)
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == agent_oauth.PROJECT_UPDATES_OFF
        assert _live(live, made["id"])


class TestTheSummary:
    """The Overview line comes with the roll-up, so nothing moves (§14.2a)."""

    pytestmark = _DB_GATE

    async def test_the_summary_carries_the_newest_update_only_while_the_flag_is_on(
            self, live, monkeypatch):
        from acb_common import get_settings

        newest = (await _post(live, LEAD, live.team, "at_risk", "Newest for the line.")).json()
        got = (await _as(live, LEAD, "GET", f"/projects/nodes/{live.team}/summary")).json()
        assert got["latest_update"]["id"] == newest["id"]
        assert got["latest_update"]["body"] == "Newest for the line."
        space = (await _as(live, LEAD, "GET", f"/projects/nodes/{live.space}/summary")).json()
        assert "latest_update" not in space, "a space draws no line"
        empty = (await _as(live, LEAD, "GET", f"/projects/nodes/{live.filed}/summary")).json()
        assert empty["latest_update"] is None, "a project with no update keeps no space"
        monkeypatch.setattr(get_settings(), "projects_updates_orgs", "", raising=False)
        off = (await _as(live, LEAD, "GET", f"/projects/nodes/{live.team}/summary")).json()
        assert "latest_update" not in off, "a dark org gets the answer it got before"
