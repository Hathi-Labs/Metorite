"""WS-64, task #304 — each node of a private tree carries ``personal_owner``.

Spec: ``project-docs/specs/one_projects_app.md`` OP-3 (the Personal space in
Projects), ``external_agent_access.md`` D-EA-4, and migration 297.

**The bug.** ``POST /projects/nodes`` under a personal parent did not copy
``personal_owner`` to the child. Each read tests the node's OWN column, so
the child read as a team node:

* ``tree.py`` ``_visible_projects`` showed it as an orphan root in the tree
  of each ``data:org:read`` holder, and in the portfolio summary.
* ``core.py`` ``_AGENT_NOT_PERSONAL_SQL`` let an agent connection with
  ``include_personal=false`` read the child and its tasks.
* ``assert_project_move_keeps_privacy`` saw None on both sides, so a move put
  the child in a team space.

**The fix.** ``create_node`` stamps the child with the tree owner
(``core.personal_tree_owner``, which walks up), and gives it the owner grant
(``core.grant_personal_owner``, the one seam that ``mint_personal_child``
uses too). Only the tree owner may file a node there. Anyone else gets the
404 of an unseen parent. Migration 297 stamps the rows written before.

**R8.** A real database, as a NON-privileged role, on a promoted catalog of
its own (FORCE RLS). The real gateway app, signed agent calls through the EA
fixtures. Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/stamp_fix \\
        uv run pytest tests/unit/test_personal_owner_stamp.py -v -rs

**The mutations (R7).** Each one turned the named tests red. ``G`` is
``apps/services/gateway/gateway/routes/projects``.

* M1. ``G/tree.py`` ``create_node``: no ``values["personal_owner"] = tree_owner``.
  ``test_a_*``, ``test_b_*`` and ``test_c_*``.
* M2. ``G/tree.py`` ``create_node``: no 404 for a caller who is not the tree
  owner. ``test_a_an_org_reader_cannot_file_a_node_in_my_tree``.
* M3. ``G/core.py`` ``personal_tree_owner``: reads only the node itself, no
  walk. ``test_a_a_node_under_a_legacy_unstamped_child_is_stamped``.
* M4. ``G/tree.py`` ``create_node``: no ``grant_personal_owner``.
  ``test_a_a_child_under_my_root_carries_my_name``.
* M5. ``infra/postgres/297_personal_owner_stamp_backfill.sql``: no per-tenant
  loop. ``test_d_the_migration_stamps_idempotently_and_leaves_team_rows``.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import agent_tokens
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_delete as d
from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import apply_ladder

_DB_GATE = w._DB_GATE
pytestmark = _DB_GATE

_APP_ROLE = "acb_app_stampfix"
_APP_PW = "stampfix_scratch_pw"

#: The domain is the one ``live_app`` allows.
OWNER_P = "owner@mcpwrites.test"
READER = "reader@mcpwrites.test"

_MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "infra" / "postgres").glob(
        "*_personal_owner_stamp_backfill.sql"))


def _connection(a: w._Seeder, email: str, *, include_personal: bool) -> tuple[str, str]:
    """A write connection of *email*: ``(id, raw token)``."""
    cid = str(a.one(
        "INSERT INTO agent_connection (organization_id, member_email, client_id,"
        " client_name, client_origin, scopes, pinned_projects, include_personal,"
        " expires_at) VALUES (CAST(:o AS uuid), :m, 'https://client.example/meta.json',"
        " 'Client', :origin, CAST(:scopes AS text[]), CAST(:pins AS uuid[]), :ip,"
        " now() + interval '1 day') RETURNING id",
        o=a.org, m=email, origin=w.ORIGIN, scopes=list(w.WRITE_SCOPES), pins=[],
        ip=include_personal))
    raw = agent_tokens.new_token()
    a.one("INSERT INTO agent_token (organization_id, connection_id, kind, token_hash,"
          " expires_at, client_id, resource, scope) VALUES (CAST(:o AS uuid),"
          " CAST(:c AS uuid), 'access', :h, now() + interval '1 hour',"
          " 'https://client.example/meta.json', :r, :s) RETURNING id",
          o=a.org, c=cid, h=agent_tokens.hash_token(raw), r=w.RESOURCE,
          s=" ".join(w.WRITE_SCOPES))
    return cid, raw


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: the owner of a private tree, an org reader
    and one team space that both of them can see."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_stampfix"
    w._drop_database(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql", "03_constraints.sql",
                  "04_policies.sql"):
        with eng.begin() as conn, open(os.path.join(w._GENERATED, phase),
                                       encoding="utf-8") as fh:
            w._exec_sql_file(conn, fh.read())
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

    org = str(uuid.uuid4())
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'stampfix', 'stampfix')"), {"id": org})
    a = w._Seeder(eng, org)
    a.number = 900_000
    a.member(w.MEMBER, ["feature:projects"])
    # The owner is a Projects admin, so the move authority of OP-8 passes and
    # only the privacy rule can refuse the move of test (c).
    a.member(OWNER_P, ["feature:projects", "projects:settings:write"])
    a.member(READER, ["feature:projects", "data:org:read"])
    team = a.project("Team")
    for who in (OWNER_P, READER):
        d._grant(a, team, who)
    world_ = SimpleNamespace(admin=eng, org=org, seeder=a, team=team, c={}, tok={},
                             todo=a.status(team, "To do", "todo", 0))
    world_.c["np"], world_.tok["np"] = _connection(a, OWNER_P, include_personal=False)
    world_.c["p"], world_.tok["p"] = _connection(a, OWNER_P, include_personal=True)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    world_.app_url = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


redis_fake = tw.redis_fake
live_app = tw.live_app


@pytest.fixture
async def live(live_app):
    """The real gateway app, and the owner's personal root with one Area."""
    if not getattr(live_app, "root", None):
        r = await d._person(live_app, OWNER_P, "POST", "/projects/my/areas",
                            {"name": "Home"})
        assert r.status_code == 201, r.text
        live_app.root = _one(live_app,
                             "SELECT id FROM pm_projects WHERE parent_project_id IS NULL"
                             " AND lower(personal_owner) = :o", o=OWNER_P)
        live_app.lane = _one(live_app,
                             "SELECT id FROM pm_task_statuses WHERE project_id ="
                             " CAST(:p AS uuid) ORDER BY position LIMIT 1",
                             p=live_app.root)
    return live_app


def _one(world, sql: str, **params: Any) -> Any:
    with world.admin.begin() as c:
        row = c.execute(text(sql), params).first()
    return None if row is None else (str(row[0]) if isinstance(row[0], uuid.UUID) else row[0])


def _owner_of(world, node: str) -> str | None:
    return _one(world, "SELECT personal_owner FROM pm_projects WHERE id = CAST(:n AS uuid)",
                n=node)


def _task(world, project: str, title: str) -> str:
    world.seeder.number += 1
    return str(world.seeder.one(
        "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
        " created_by, organization_id, task_number) VALUES (:t, CAST(:p AS uuid),"
        " CAST(:r AS uuid), CAST(:s AS uuid), :m, CAST(:o AS uuid), :n) RETURNING id",
        t=title, p=project, r=world.root, s=world.lane, m=OWNER_P, o=world.org,
        n=world.seeder.number))


def _legacy_child(world, parent: str, name: str) -> str:
    """A child of a private tree as the old ``create_node`` wrote it: no owner."""
    return str(world.seeder.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, parent_project_id) VALUES (:n, 'active', 'manual', :m,"
        " CAST(:o AS uuid), 'Asia/Kolkata', CAST(:p AS uuid)) RETURNING id",
        n=name, m=OWNER_P, o=world.org, p=parent))


async def _make(world, who: str, parent: str, name: str) -> Any:
    return await d._person(world, who, "POST", "/projects/nodes",
                           {"name": name, "parent_project_id": parent})


async def _child(world, name: str) -> str:
    r = await _make(world, OWNER_P, world.root, name)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _signed_get(world, name: str, path: str) -> Any:
    return await tw._send(world, "GET", path, signed=name)


# ═══════════════════════════════════════════════════════════════════════════
# (a) The stamp, and who may file a node
# ═══════════════════════════════════════════════════════════════════════════


async def test_a_a_child_under_my_root_carries_my_name(live):
    child = await _child(live, "a stamped child")
    assert _owner_of(live, child) == OWNER_P
    # The owner grant, the same row `mint_personal_child` writes.
    assert _one(live, "SELECT subject FROM pm_project_grants WHERE project_id ="
                " CAST(:p AS uuid) AND subject = :s", p=child, s=OWNER_P) == OWNER_P


async def test_a_an_org_reader_does_not_see_the_child_in_the_tree(live):
    # Only the tree reads. ⚠️ An org reader still reads a personal TASK
    # through `GET /projects/tasks`. That is an open owner question, and not
    # this fix, so this test does not claim it.
    child = await _child(live, "a hidden child")
    task = _task(live, child, "a hidden task")
    for path in ("/projects/tree", "/projects/nodes", "/projects/summary"):
        for who in (READER, OWNER_P):
            r = await d._person(live, who, "GET", path)
            assert r.status_code == 200, r.text
            assert child not in r.text, (who, path)
            assert task not in r.text, (who, path)
    # The summary counts the task nowhere: it has one space, and no task.
    r = await d._person(live, READER, "GET", "/projects/summary")
    assert [c["id"] for c in r.json()["children"]] == [live.team]


async def test_a_an_org_reader_cannot_file_a_node_in_my_tree(live):
    r = await _make(live, READER, live.root, "a planted node")
    assert r.status_code == 404, r.text
    assert _one(live, "SELECT count(*) FROM pm_projects WHERE name = 'a planted node'") == 0


async def test_a_a_node_under_a_legacy_unstamped_child_is_stamped(live):
    legacy = _legacy_child(live, live.root, "a legacy folder")
    assert _owner_of(live, legacy) is None
    r = await _make(live, OWNER_P, legacy, "under the legacy folder")
    assert r.status_code == 201, r.text
    assert _owner_of(live, r.json()["id"]) == OWNER_P


async def test_a_a_team_child_stays_a_team_child(live):
    r = await _make(live, OWNER_P, live.team, "a team project")
    assert r.status_code == 201, r.text
    assert _owner_of(live, r.json()["id"]) is None


# ═══════════════════════════════════════════════════════════════════════════
# (b) An agent with include_personal=false
# ═══════════════════════════════════════════════════════════════════════════


async def test_b_an_agent_without_personal_cannot_read_the_child_or_its_tasks(live):
    child = await _child(live, "an agent-hidden child")
    task = _task(live, child, "an agent-hidden task")
    r = await _signed_get(live, "np", f"/projects/nodes/{child}")
    assert r.status_code == 404, r.text
    r = await _signed_get(live, "np", f"/projects/tasks/{task}")
    assert r.status_code == 404, r.text
    # The two list reads of the MCP allowlist (`agent_oauth.MCP_ROUTES`).
    for path in ("/projects/tree", "/projects/tasks"):
        r = await _signed_get(live, "np", path)
        assert r.status_code == 200, r.text
        assert child not in r.text and task not in r.text, path
    r = await _signed_get(live, "p", "/projects/tasks")
    assert task in r.text
    # The control: the same member's connection WITH personal reads it.
    r = await _signed_get(live, "p", f"/projects/nodes/{child}")
    assert r.status_code == 200, r.text
    r = await _signed_get(live, "p", f"/projects/tasks/{task}")
    assert r.status_code == 200, r.text


# ═══════════════════════════════════════════════════════════════════════════
# (c) The move into a team space
# ═══════════════════════════════════════════════════════════════════════════


async def test_c_a_move_of_the_child_into_a_team_space_is_refused(live):
    child = await _child(live, "an unmovable child")
    r = await d._person(live, OWNER_P, "POST", f"/projects/nodes/{child}/move",
                        {"parent_project_id": live.team})
    assert r.status_code == 422, r.text
    assert "personal area" in r.json()["detail"]
    assert _one(live, "SELECT parent_project_id FROM pm_projects WHERE id ="
                " CAST(:c AS uuid)", c=child) == live.root


async def test_c_a_move_of_the_child_to_the_top_level_is_refused(live):
    child = await _child(live, "an unpromotable child")
    r = await d._person(live, OWNER_P, "POST", f"/projects/nodes/{child}/move",
                        {"parent_project_id": None})
    assert r.status_code == 422, r.text
    assert "personal area" in r.json()["detail"]


async def test_c_a_team_node_cannot_go_under_an_unstamped_node_of_my_tree(live):
    # The destination side walks up. An unstamped legacy child reads NULL in
    # its own column, and the old guard let a team node in under it.
    legacy = _legacy_child(live, live.root, "a legacy landing")
    r = await _make(live, OWNER_P, live.team, "a team project to move")
    assert r.status_code == 201, r.text
    team_node = r.json()["id"]
    r = await d._person(live, OWNER_P, "POST", f"/projects/nodes/{team_node}/move",
                        {"parent_project_id": legacy})
    assert r.status_code == 422, r.text
    assert "personal space" in r.json()["detail"]
    assert _one(live, "SELECT parent_project_id FROM pm_projects WHERE id ="
                " CAST(:c AS uuid)", c=team_node) == live.team


# ═══════════════════════════════════════════════════════════════════════════
# (d) The migration
# ═══════════════════════════════════════════════════════════════════════════


def _run_migration(url) -> list[str]:
    """Run 297 once, and return the text of each NOTICE it raised."""
    notices: list[str] = []
    eng = create_engine(url, future=True)
    try:
        with eng.begin() as conn:
            raw = conn.connection.dbapi_connection
            raw.add_notice_handler(lambda diag: notices.append(diag.message_primary))
            w._exec_sql_file(conn, _MIGRATION.read_text(encoding="utf-8"))
    finally:
        eng.dispose()
    return notices


def _team_root_under(world, parent: str, name: str) -> tuple[str, str]:
    """A team root with an `org` grant, moved under *parent* as the old
    `move_node` let it be: the privacy check read only the direct parent.
    Returns ``(root, its child)``."""
    root = world.seeder.project(name)
    d._grant(world.seeder, root, "org")
    kid = _legacy_child(world, root, f"{name} child")
    world.seeder.one("UPDATE pm_projects SET parent_project_id = CAST(:p AS uuid)"
                     " WHERE id = CAST(:t AS uuid) RETURNING id", p=parent, t=root)
    return root, kid


def _snapshot(world) -> dict[str, Any]:
    with world.admin.begin() as c:
        return {str(r.id): (r.personal_owner, r.parent_project_id, r.name)
                for r in c.execute(text("SELECT id, personal_owner, parent_project_id,"
                                        " name FROM pm_projects ORDER BY id"))}


async def test_d_the_migration_stamps_idempotently_and_leaves_team_rows(live):
    legacy = _legacy_child(live, live.root, "a legacy child")
    grand = _legacy_child(live, legacy, "a legacy grandchild")
    team_kid = _legacy_child(live, live.team, "a team kid")
    team_grand = _legacy_child(live, team_kid, "a team grandchild")
    before = _snapshot(live)
    assert before[legacy][0] is None and before[grand][0] is None

    # As the NON-privileged role, under FORCE RLS: the per-tenant pass does it.
    _run_migration(live.app_url)
    after = _snapshot(live)
    assert after[legacy][0] == OWNER_P
    assert after[grand][0] == OWNER_P
    assert after[team_kid][0] is None and after[team_grand][0] is None
    changed = {k for k in before if before[k] != after[k]}
    unstamped_private = {k for k, v in before.items()
                         if v[0] is None and k in (legacy, grand)}
    # Only the unstamped rows of the private tree change. The earlier
    # tests' legacy folder is one more such row.
    assert unstamped_private <= changed
    assert all(after[k][0] == OWNER_P for k in changed)
    assert all(before[k][1:] == after[k][1:] for k in before)

    # Idempotent: a second run, as each role, changes no row.
    _run_migration(live.app_url)
    _run_migration(make_url(w._URL).set(database=live.app_url.database))
    assert _snapshot(live) == after
    assert json.dumps(sorted(after)) == json.dumps(sorted(before))


async def test_d_the_migration_stops_at_a_team_root_under_my_tree(live):
    # The adversarial review's case. A team root T under an unstamped
    # personal child X. 297 stamps X, and leaves T and its child as they are.
    x = _legacy_child(live, live.root, "a legacy X")
    t, t_kid = _team_root_under(live, x, "Team T")
    notices = _run_migration(live.app_url)
    assert _owner_of(live, x) == OWNER_P
    assert _owner_of(live, t) is None
    assert _owner_of(live, t_kid) is None
    # The deploy log names the node it kept.
    assert any(t in n and live.org in n for n in notices), notices
    # A team member, who reads T only through the grant, still sees it.
    r = await d._person(live, w.MEMBER, "GET", "/projects/tree")
    assert r.status_code == 200, r.text
    assert t in r.text


async def test_d_a_team_node_left_under_my_tree_can_be_moved_out(live):
    # The repair of the case above. T is not personal itself, so the guard
    # lets the owner (or a lead) move it out to a team space.
    x = _legacy_child(live, live.root, "a legacy X2")
    t, _ = _team_root_under(live, x, "Team T2")
    _run_migration(live.app_url)
    assert _owner_of(live, x) == OWNER_P and _owner_of(live, t) is None
    r = await d._person(live, OWNER_P, "POST", f"/projects/nodes/{t}/move",
                        {"parent_project_id": live.team})
    assert r.status_code == 200, r.text
    assert _one(live, "SELECT parent_project_id FROM pm_projects WHERE id ="
                " CAST(:c AS uuid)", c=t) == live.team


async def test_d_the_migration_keeps_an_owner_that_is_already_set(live):
    # A descendant that carries an owner keeps it, even one that differs
    # from the root's owner. Only a NULL takes the stamp.
    other = "someone.else@mcpwrites.test"
    same = _legacy_child(live, live.root, "an already stamped child")
    odd = _legacy_child(live, live.root, "a child stamped for another")
    with live.admin.begin() as c:
        c.execute(text("UPDATE pm_projects SET personal_owner = :o WHERE id ="
                       " CAST(:i AS uuid)"), {"o": OWNER_P, "i": same})
        c.execute(text("UPDATE pm_projects SET personal_owner = :o WHERE id ="
                       " CAST(:i AS uuid)"), {"o": other, "i": odd})
    _run_migration(live.app_url)
    assert _owner_of(live, same) == OWNER_P
    assert _owner_of(live, odd) == other
