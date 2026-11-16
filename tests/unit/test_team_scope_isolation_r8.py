"""T-7 (task 2538): the isolation fence of teams. Two teams, three spaces, the listed surfaces.

Spec: ``project-docs/specs/project_management_app.md`` §14.9, rules 2 and 4.
``navigation_shell.md`` §4.2 rule 4 and §4.2a.

A team is a group, ``group:<slug>`` (D12). There is no team object and no
team table (D87, D49). A grant of a space to a group in ``pm_project_grants``
puts the space in the view of that team. This file proves two rules on a
real database, as a role that cannot bypass RLS, for the surfaces that it
lists below:

* **Rule 2.** A space that is shared with no team of the member is hidden.
* **Rule 4.** A hidden space is never named and never counted. A direct link
  answers the same "not found" as an id that does not exist.

The file does not cover all of rule 4. The section "Reads not driven" names
each read that it leaves out. Those reads are follow-up work.

It also proves that a grant that goes away takes effect on the next read of
the same session, and that a member who leaves a team loses its spaces on
the next read.

**The world.** One tenant. Two groups, ``team-a`` and ``team-b``. Three
spaces, each with one project, and the tasks in the project:

* ``Kestrel space``, granted to ``group:team-a`` only. Two tasks.
* ``Heron space``, granted to ``group:team-b`` only. Four tasks.
* ``Osprey space``, granted to both groups. Eight tasks.

The counts 2, 4 and 8 are on purpose. Each set of spaces then has its own
sum, so a count alone shows which spaces a read counted. Four members:

* ``a`` is in ``team-a`` only. It reaches Kestrel and Osprey (10 tasks).
* ``b`` is in ``team-b`` only. It reaches Heron and Osprey (12 tasks).
* ``ab`` is in both teams. It reaches all three (14 tasks). This is the read
  of "All my teams".
* ``nobody`` is in no team. It reaches nothing.

``a`` and ``b`` each have these rows of My tasks:

* a WAITING overlay and an INBOX overlay, each on a task of the space that
  they cannot see;
* a WAITING overlay on a task of Osprey;
* one assigned task of Osprey, with an INBOX overlay.

So each My tasks read holds a hidden row to leave out and a visible row to
keep. Nobody is assigned a task of a hidden space, because an assignment is
a way in by design (``load_visible_task``).

**The leak check.** Each name of a space, a project and a task holds the
word of its space. For each read, the test looks for the words and the ids
of the hidden spaces in the whole body of the answer. One hit fails the
test. Each read also has a positive control for each member who sees a
space. The read must name, count or open what the member can see, so a read
that returns nothing cannot pass. ``nobody`` sees no space, so its reads
have no positive control.

**The surfaces, one test class each.**

* The sidebar tree: ``GET /projects/tree``, ``/projects/nodes`` and the
  portfolio counts ``/projects/summary``.
* The board and the list: ``GET /projects/tasks`` with the lane counts, the
  list of one space, and the 404 of a hidden project, its views and its
  summary.
* ``board_stats``: ``GET /projects/tasks/stats``, with ``group_by=project``.
* My tasks: ``GET /projects/tasks?scope=mine``, ``/projects/my/inbox``,
  ``/projects/my/summary``, ``/projects/assigned-to-me`` and the single read
  ``/projects/my/tasks/{id}``.
* Search: ``GET /projects/search`` and ``/projects/search/refs``.
* The CSV export and the delta feed.
* The direct link: ``GET /projects/tasks/{id}``, ``/projects/nodes/{id}`` and
  ``/projects/nodes/{id}/grants`` of a hidden row answer the body of an id
  that does not exist.
* The assistant: the chat tools ``projects_tree``, ``project_summary``,
  ``find_tasks``, ``list_tasks``, ``my_work`` and ``task_detail`` of
  ``skill_projects``, through the real gateway app.
* The MCP door: ``list_tasks``, ``board_stats``, ``list_projects``,
  ``search_tasks``, ``get_task`` and ``get_project`` through ``POST /mcp``
  of the real edge, once with an OAuth access token and once with a box
  token (EA-19b).
* The session: a grant deleted through the route, and a member removed from
  a group, each hide the space on the next read of the app, the chat and
  the MCP door.

**Reads not driven.** This file does not drive these reads. Each one is
named here so that nobody reads the file as wider than it is. Each one is
follow-up work for this fence.

* The MCP tools ``my_work``, ``get_task_context``, ``get_plan``,
  ``get_task_timeline``, ``list_project_updates`` and ``list_tags``.
* ``GET /projects/my/today``, ``/projects/plan/order``, the notifications
  and the intake reads.
* ``GET /projects/tasks/{id}/subtree``, ``/projects/tasks/{id}/timeline``
  and ``/projects/tasks/{id}/relations``.
* ``GET /projects/nodes/{id}/statuses``, ``/projects/nodes/{id}/fields`` and
  ``/projects/nodes/{id}/tags``.
* The team chip and the team scope of the shell (T-2, T-4). They are not
  built. Rule 3 and rule 5 ("a scope never grants") wait for
  ``src/lib/shell/teamScope.test.ts``.
* The read model of the teams of a member (T-1, task 2532). It is not built.
* ``GET /projects/needs-attention``. Its rows need agent runs or a parked
  blocker, and this world seeds neither.
* The analytics and the reports (``/projects/analytics/*``). They read
  ``reader_scope`` on top of the grants, and this world has no lead of a
  team.
* The Calendar lens and the My Day feed. Their rows need dated tasks and the
  feed, and this world seeds neither.
* ``GET /projects/my/led`` reads the lead of a project, not a grant, by
  design. The world names no lead of a team space.
* The chat has no ``board_stats`` tool yet (task 2223). The chat count is
  proved through ``list_tasks`` and ``project_summary``.
* A holder of ``data:org:read`` reads every space of the tenant by design
  (D14). That is not a team rule, so no member here holds it.

**The mutations (R7).** Each planted fault below turned the named tests red
on 2026-11-11. Each was reverted. ``G`` is
``apps/services/gateway/gateway/routes/projects``. M1 to M5 ran against the
first world, which had 1, 2 and 4 tasks and no INBOX overlays.

* M1. ``G/core.py`` ``_MY_GROUPS_SQL``: no member filter, so each member is
  in every group. 94 of 146 tests fail, in every class, for ``a``, ``b`` and
  ``nobody``.
* M2. ``G/search.py`` ``search_tasks``: the visibility clause is ``TRUE``.
  15 tests fail: the two word tests of ``TestSearch``,
  ``TestTheAssistant.test_find_tasks`` and ``TestTheMcpDoor.test_search_tasks``.
* M3. ``G/core.py`` ``mine_clause``: the WAITING arm drops its grant
  closure. ``TestMyTasks.test_the_inbox`` fails for ``a`` and ``b``. The list
  over mine and the Overview also AND the visibility clause, so they stay
  green. Rerun on the current world: ``test_the_inbox`` and
  ``test_the_single_read`` fail for ``a`` and ``b`` (4 failed). The chat
  ``my_work`` stays green, because its inbox view reads INBOX rows only.
* M3b. ``G/core.py`` ``mine_clause``: the WAITING arm takes an overlay of any
  disposition, and drops its grant closure. ``TestTheAssistant.test_my_work``
  fails for ``a`` and ``b``, because the hidden INBOX row reaches the chat.
  ``test_the_inbox`` and ``test_the_single_read`` fail too (6 failed).
* M6. ``G/tasks.py`` ``_task_scope``: ``TRUE`` in place of the visibility
  clause. 23 tests fail on the current world: the list and its lane counts,
  ``TestBoardStats``, the chat ``list_tasks``, the MCP ``list_tasks`` and
  ``board_stats``, for ``a``, ``b`` and ``nobody``, and both tests of
  ``TestTheNextRead``.
* M4. ``G/tasks.py`` ``get_task``: a hidden task answers a 404 text of its
  own. ``TestADirectLinkIsNotFound.test_a_hidden_task``, the ``task_detail``
  test of ``TestTheAssistant`` and the deleted-grant test of
  ``TestTheNextRead`` fail. The MCP door stays green, because the edge never
  sends a text of the gateway.
* M5. ``G/core.py`` ``resolve_visibility``: the groups of a member are kept
  in a cache. The test of ``TestTheNextRead`` for a member who leaves a team
  fails.

Run (a private ladder database, one R8 suite at a time)::

    bash scripts/dev_db.sh
    eval "$(bash scripts/dev_db.sh --export)"
    PYTEST_ADDOPTS="-o timeout_method=thread" \\
        uv run pytest tests/unit/test_team_scope_isolation_r8.py -v -rs
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import types
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

from acb_auth import access as access_module
from acb_auth import agent_tokens
from acb_auth.access import invalidate as invalidate_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from agent_edge import app as edge_app_module
from agent_edge import gateway_client, tools
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import tenant_engine_scope

pytestmark = w._DB_GATE

_APP_ROLE = "acb_app_teamscope"
_APP_PW = "teamscope_scratch_pw"
INTERNAL = "team-scope-internal-token"
DOMAIN = "teamscope.test"

A = f"a@{DOMAIN}"
B = f"b@{DOMAIN}"
AB = f"ab@{DOMAIN}"
NOBODY = f"nobody@{DOMAIN}"
#: The lead of the spaces that the session tests make and remove.
LEAD = f"lead@{DOMAIN}"
#: ``created_by`` of the seeded rows. Not a member, so it opens nothing.
MAKER = f"maker@{DOMAIN}"

TEAM_A = "group:team-a"
TEAM_B = "group:team-b"
#: A word in every task title, so one search reaches every task.
WORD = "marmot"
#: The key of each space: the word of its names, its grants, its task count.
SPACES: dict[str, tuple[str, tuple[str, ...], int]] = {
    "A": ("kestrel", (TEAM_A,), 2),
    "B": ("heron", (TEAM_B,), 4),
    "AB": ("osprey", (TEAM_A, TEAM_B), 8),
}
#: The spaces that the teams of each member reach.
REACH: dict[str, tuple[str, ...]] = {
    A: ("A", "AB"),
    B: ("B", "AB"),
    AB: ("A", "B", "AB"),
    NOBODY: (),
}
MEMBERS = tuple(REACH)
#: An id that no row has. A hidden row must answer as this one does.
MISSING = "00000000-0000-4000-8000-000000000000"


# ═══════════════════════════════════════════════════════════════════════════
# The world
# ═══════════════════════════════════════════════════════════════════════════


def _node(seeder: w._Seeder, name: str, *, parent: str | None = None,
          owns: bool = False, lead: str | None = None) -> str:
    return str(seeder.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, parent_project_id, owns_statuses, lead) VALUES (:n, 'active',"
        " 'manual', :c, CAST(:o AS uuid), 'Asia/Kolkata', CAST(:p AS uuid), :own,"
        " :lead) RETURNING id",
        n=name, c=MAKER, o=seeder.org, p=parent, own=owns, lead=lead))


def _grant(seeder: w._Seeder, project: str, subject: str) -> str:
    return str(seeder.one(
        "INSERT INTO pm_project_grants (project_id, subject, created_by,"
        " organization_id) VALUES (CAST(:p AS uuid), :s, :c, CAST(:o AS uuid))"
        " RETURNING id", p=project, s=subject, c=MAKER, o=seeder.org))


def _group(seeder: w._Seeder, slug: str, name: str, members: tuple[str, ...]) -> str:
    gid = str(seeder.one(
        "INSERT INTO org_group (organization_id, slug, display_name)"
        " VALUES (CAST(:o AS uuid), :s, :n) RETURNING id",
        o=seeder.org, s=slug, n=name))
    for email in members:
        seeder.one(
            "INSERT INTO org_group_member (group_id, user_id) SELECT CAST(:g AS uuid),"
            " id FROM app_user WHERE lower(email) = :e RETURNING user_id",
            g=gid, e=email)
    return gid


def _space(seeder: w._Seeder, word: str, grants: tuple[str, ...], count: int, *,
           lead: str | None = None) -> SimpleNamespace:
    """A space with its grants and its lane, one project in it, and *count*
    tasks in the project. Each name holds *word*."""
    space = _node(seeder, f"{word.title()} space", owns=True, lead=lead)
    grant_ids = {subject: _grant(seeder, space, subject) for subject in grants}
    status = str(seeder.one(
        "INSERT INTO pm_task_statuses (project_id, name, color, position, category,"
        " organization_id) VALUES (CAST(:p AS uuid), 'To do', 'gray', 0, 'todo',"
        " CAST(:o AS uuid)) RETURNING id", p=space, o=seeder.org))
    project = _node(seeder, f"{word.title()} project", parent=space)
    tasks: list[str] = []
    numbers: list[int] = []
    for i in range(1, count + 1):
        seeder.number += 1
        # An hour back, so the delta feed reads the row (`delta.HORIZON_LAG_SECONDS`).
        tasks.append(str(seeder.one(
            "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
            " created_by, organization_id, task_number, updated_at) VALUES (:t,"
            " CAST(:p AS uuid), CAST(:r AS uuid), CAST(:s AS uuid), :c,"
            " CAST(:o AS uuid), :n, now() - interval '1 hour') RETURNING id",
            t=f"{WORD} {word} {i}", p=project, r=space, s=status, c=MAKER,
            o=seeder.org, n=seeder.number)))
        numbers.append(seeder.number)
    return SimpleNamespace(word=word, space=space, project=project, status=status,
                           tasks=tasks, numbers=numbers, grants=grant_ids)


def _overlay(seeder: w._Seeder, task: str, member: str, disposition: str) -> None:
    seeder.one(
        "INSERT INTO pm_task_personal (task_id, member_email, disposition,"
        " organization_id) VALUES (CAST(:t AS uuid), :m, :d, CAST(:o AS uuid))"
        " RETURNING task_id", t=task, m=member, d=disposition, o=seeder.org)


def _assign(seeder: w._Seeder, task: str, member: str) -> None:
    seeder.one(
        "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by,"
        " organization_id) VALUES (CAST(:t AS uuid), :m, :c, CAST(:o AS uuid))"
        " RETURNING task_id", t=task, m=member, c=MAKER, o=seeder.org)


def _oauth_connection(seeder: w._Seeder, email: str) -> str:
    """A read connection of the member *email*, and its raw access token."""
    cid = str(seeder.one(
        "INSERT INTO agent_connection (organization_id, member_email, client_id,"
        " client_name, client_origin, scopes, pinned_projects, include_personal,"
        " expires_at) VALUES (CAST(:o AS uuid), :m, 'https://client.example/meta.json',"
        " 'Client', :origin, CAST(:scopes AS text[]), CAST(:pins AS uuid[]), true,"
        " now() + interval '1 day') RETURNING id",
        o=seeder.org, m=email, origin=w.ORIGIN, scopes=["projects:read"], pins=[]))
    raw = agent_tokens.new_token()
    seeder.one(
        "INSERT INTO agent_token (organization_id, connection_id, kind, token_hash,"
        " expires_at, client_id, resource, scope) VALUES (CAST(:o AS uuid),"
        " CAST(:c AS uuid), 'access', :h, now() + interval '1 hour',"
        " 'https://client.example/meta.json', :r, 'projects:read') RETURNING id",
        o=seeder.org, c=cid, h=agent_tokens.hash_token(raw), r=w.RESOURCE)
    return raw


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: one tenant, two teams, three spaces."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_teamscope"
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

    org = str(uuid.uuid4())
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'teamscope', 'teamscope')"), {"id": org})
    s = w._Seeder(eng, org)
    s.number = 700_000
    for who in (*MEMBERS, LEAD):
        s.member(who, ["feature:projects"])
    groups = {
        "team-a": _group(s, "team-a", "Team A", (A, AB)),
        "team-b": _group(s, "team-b", "Team B", (B, AB)),
    }
    spaces = {key: _space(s, word, grants, count)
              for key, (word, grants, count) in SPACES.items()}
    sa, sb, sab = spaces["A"], spaces["B"], spaces["AB"]
    # My tasks, for `a` and for `b`. A WAITING row and an INBOX row on tasks
    # of the hidden space. A WAITING row on a task of Osprey. One assigned
    # task of Osprey with an INBOX row, so the inbox of the chat has a row.
    for who, hidden, waits, owns in ((A, sb, 0, 2), (B, sa, 1, 3)):
        _overlay(s, hidden.tasks[0], who, "WAITING")
        _overlay(s, hidden.tasks[1], who, "INBOX")
        _overlay(s, sab.tasks[waits], who, "WAITING")
        _assign(s, sab.tasks[owns], who)
        _overlay(s, sab.tasks[owns], who, "INBOX")

    world_ = SimpleNamespace(
        admin=eng, org=org, seeder=s, groups=groups, spaces=spaces,
        mine={A: {sab.tasks[0], sab.tasks[2]}, B: {sab.tasks[1], sab.tasks[3]},
              AB: set(), NOBODY: set()},
        assigned={A: {sab.tasks[2]}, B: {sab.tasks[3]}, AB: set(), NOBODY: set()},
        #: The hidden tasks with a WAITING row and an INBOX row of the member.
        overlay_hidden={A: sb.tasks[:2], B: sa.tasks[:2]},
        oauth={who: _oauth_connection(s, who) for who in MEMBERS},
        box={},
    )
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


# ── What each member may and may not read ──────────────────────────────────


def _seen(world: Any, who: str) -> list[SimpleNamespace]:
    return [world.spaces[key] for key in REACH[who]]


def _unseen(world: Any, who: str) -> list[SimpleNamespace]:
    return [sp for key, sp in world.spaces.items() if key not in REACH[who]]


def _tasks(spaces: list[SimpleNamespace]) -> set[str]:
    return {t for sp in spaces for t in sp.tasks}


def _nodes(spaces: list[SimpleNamespace]) -> set[str]:
    return {n for sp in spaces for n in (sp.space, sp.project)}


def _no_leak(world: Any, who: str, body: str, where: str) -> None:
    """No word and no id of a hidden space anywhere in *body*."""
    low = body.lower()
    for sp in _unseen(world, who):
        assert sp.word not in low, f"{who} reads the name of the {sp.word} space in {where}"
        for ident in (sp.space, sp.project, sp.status, *sp.tasks):
            assert ident not in low, f"{who} reads an id of the {sp.word} space in {where}"


def _names_all(world: Any, who: str, body: str, where: str) -> None:
    """The positive control: each space the member may see is named."""
    low = body.lower()
    for sp in _seen(world, who):
        assert sp.word in low, f"{where} does not name the {sp.word} space for {who}"


# ═══════════════════════════════════════════════════════════════════════════
# The fixtures and the doors
# ═══════════════════════════════════════════════════════════════════════════

redis_fake = tw.redis_fake


@pytest.fixture
def live_app(monkeypatch, world, redis_fake):
    """The real gateway app behind the real edge, with box tokens on."""
    from gateway.main import app as gateway_app

    # The app mounts this router. The import also names it for
    # `scripts/affected_tests.py` (CIV-18), so that a change to any Projects
    # route module selects this fence.
    from gateway.routes.projects import router as projects_router

    assert projects_router.routes, "the Projects router holds no route"

    settings = get_settings()
    for name, value in (
            ("agent_access_enabled", True), ("agent_access_orgs", "*"),
            ("agent_box_tokens_enabled", True), ("agent_edge_signing_key", tw.KEY),
            ("agent_edge_signing_key_previous", ""), ("agent_issuer", w.ISSUER),
            ("agent_resource", w.RESOURCE),
            ("agent_edge_gateway_url", "http://gateway.test")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", DOMAIN)
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    # A process-wide latch that an earlier suite can set
    # (`test_mcp_edge_authority.py` says why).
    monkeypatch.setattr(access_module, "_tables_missing", False)
    invalidate_access()
    with gateway_client.use_transport(httpx.ASGITransport(app=gateway_app)):
        try:
            yield world
        finally:
            invalidate_access()


@pytest.fixture
async def live(live_app):
    """:func:`live_app` after one warm-up request, so that the first tool call
    does not pass the limit of the edge (EA3-17) on a slow machine."""
    if not getattr(live_app, "warmed", False):
        r = await _as(live_app, A, "GET", "/projects/tree")
        assert r.status_code == 200, r.text
        live_app.warmed = True
    return live_app


async def _in_scope(world: Any, work: Any) -> Any:
    """Run *work* in its own task, on the suite's non-privileged DSN."""
    async with tenant_engine_scope(world.app_dsn):
        return await asyncio.create_task(work())


async def _as(world: Any, who: str, method: str, path: str,
              body: dict[str, Any] | None = None) -> httpx.Response:
    """One request to the gateway app as the person *who*."""
    from gateway.main import app as gateway_app

    raw = json.dumps(body).encode() if body is not None else b""
    headers = {"Authorization": f"Bearer {INTERNAL}", "X-User-Email": who}
    if body is not None:
        headers["Content-Type"] = "application/json"

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(method, path, headers=headers, content=raw)
    return await _in_scope(world, go)


async def _ok(world: Any, who: str, path: str) -> httpx.Response:
    r = await _as(world, who, "GET", path)
    assert r.status_code == 200, (who, path, r.status_code, r.text)
    return r


async def _not_found(world: Any, who: str, path: str, hidden: str,
                     visible: str | None = None) -> None:
    """*path* with the hidden id answers as the same path with :data:`MISSING`.

    *visible* is the positive control: the same path with an id that the
    member may open answers 200, so a path that always answers 404 fails.
    """
    got = await _as(world, who, "GET", path.format(id=hidden))
    missing = await _as(world, who, "GET", path.format(id=MISSING))
    assert got.status_code == 404, (who, path, got.status_code, got.text)
    assert (missing.status_code, got.json()) == (404, missing.json()), (who, path)
    if visible is not None:
        seen = await _as(world, who, "GET", path.format(id=visible))
        assert seen.status_code == 200, (who, path, seen.status_code, seen.text)


def _control(world: Any, who: str) -> SimpleNamespace | None:
    """A space that *who* may see, for a positive control, or ``None``."""
    seen = _seen(world, who)
    return seen[0] if seen else None


async def _chat(world: Any, monkeypatch: Any, who: str, act: Any) -> Any:
    """Run a chat tool as *who*, against the real gateway app."""
    import skill_projects.client as client
    from gateway.main import app as gateway_app

    real = httpx.AsyncClient
    monkeypatch.setattr(client, "httpx", types.SimpleNamespace(
        AsyncClient=lambda **kw: real(transport=httpx.ASGITransport(app=gateway_app), **kw),
        TransportError=httpx.TransportError, Response=httpx.Response))
    monkeypatch.setattr(client, "gateway_url", lambda: "http://gateway.test")
    monkeypatch.setattr(client, "current_user_email", lambda: who)
    monkeypatch.setattr(client, "internal_token", lambda: INTERNAL)
    return await _in_scope(world, act)


async def _box_token(world: Any, who: str) -> str:
    """The box token of *who*, minted once by the real store (EA-19b)."""
    if who not in world.box:
        async def mint() -> Any:
            bound = bind_tenant(world.org)
            try:
                return await agent_tokens.create_box_connection(
                    who, f"t7 {who.split('@')[0]}")
            finally:
                release_tenant(bound)
        made = await _in_scope(world, mint)
        assert isinstance(made, agent_tokens.BoxToken), made
        world.box[who] = made.token
    return world.box[who]


async def _token(world: Any, who: str, credential: str) -> str:
    return world.oauth[who] if credential == "oauth" else await _box_token(world, who)


async def _mcp(world: Any, token: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One ``tools/call`` through ``POST /mcp`` of the real edge."""
    async def go() -> httpx.Response:
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=edge_app_module.create_app()),
                base_url=w.ISSUER) as client:
            return await client.post(
                "/mcp", content=json.dumps(w._call(tool, arguments)),
                headers={"authorization": f"Bearer {token}",
                         "content-type": "application/json",
                         "mcp-protocol-version": "2025-11-25"})
    r = await _in_scope(world, go)
    assert r.status_code == 200, (tool, r.status_code, r.text)
    return r.json()["result"]


def _flatten(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes:
        out.append(node)
        out += _flatten(node.get("children") or [])
    return out


def _row_ids(body: dict[str, Any]) -> set[str]:
    return {str(row["id"]) for row in body["rows"]}


# ═══════════════════════════════════════════════════════════════════════════
# The app
# ═══════════════════════════════════════════════════════════════════════════


class TestTheSidebarTree:
    """Rule 2: the tree, the node list and the portfolio counts."""

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_the_tree_names_and_counts_only_the_team_spaces(self, live, who):
        r = await _ok(live, who, "/projects/tree")
        _no_leak(live, who, r.text, "/projects/tree")
        _names_all(live, who, r.text, "/projects/tree")
        body = r.json()
        seen = _seen(live, who)
        assert {n["id"] for n in _flatten(body["rows"])} == _nodes(seen)
        assert body["total"] == len(_nodes(seen))
        assert sum(n["tasks"] for n in body["rows"]) == len(_tasks(seen))

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_the_node_list_holds_only_the_team_spaces(self, live, who):
        r = await _ok(live, who, "/projects/nodes")
        _no_leak(live, who, r.text, "/projects/nodes")
        body = r.json()
        assert _row_ids(body) == _nodes(_seen(live, who))
        assert body["total"] == len(_nodes(_seen(live, who)))

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_the_portfolio_counts_only_the_team_spaces(self, live, who):
        r = await _ok(live, who, "/projects/summary")
        _no_leak(live, who, r.text, "/projects/summary")
        body = r.json()
        seen = _seen(live, who)
        assert body["tasks"] == len(_tasks(seen)), body
        assert {str(c["id"]) for c in body["children"]} == {sp.space for sp in seen}


class TestTheBoardAndTheList:
    """Rule 4: the one task list, its lane counts, and a hidden project."""

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_the_list_and_its_lane_counts_hold_only_the_team_tasks(self, live, who):
        r = await _ok(live, who, "/projects/tasks?page_size=100&with_status_counts=true")
        _no_leak(live, who, r.text, "/projects/tasks")
        body = r.json()
        want = _tasks(_seen(live, who))
        assert _row_ids(body) == want
        assert body["total"] == len(want)
        lanes = body["status_counts"] or {}
        assert sum(lanes.values()) == len(want)
        assert set(lanes) <= {sp.status for sp in _seen(live, who)}

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_the_board_of_a_team_space_reads_its_tasks(self, live, who):
        for sp in _seen(live, who):
            r = await _ok(live, who, f"/projects/tasks?project_id={sp.space}"
                                     "&include_subtree=true&page_size=100")
            assert _row_ids(r.json()) == set(sp.tasks), (who, sp.word)

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_a_hidden_project_is_the_404_of_a_missing_one(self, live, who):
        ctrl = _control(live, who)
        for sp in _unseen(live, who):
            for kind in ("space", "project"):
                visible = getattr(ctrl, kind) if ctrl else None
                for path in ("/projects/tasks?project_id={id}",
                             "/projects/nodes/{id}/summary",
                             "/projects/nodes/{id}/views"):
                    await _not_found(live, who, path, getattr(sp, kind), visible)


class TestBoardStats:
    """Rule 4: ``board_stats`` counts what the list lists, and no more."""

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_the_count_and_its_groups_leave_the_hidden_space_out(self, live, who):
        want = _tasks(_seen(live, who))
        r = await _ok(live, who, "/projects/tasks/stats")
        assert r.json()["total"] == len(want)
        r = await _ok(live, who, "/projects/tasks/stats?group_by=project")
        _no_leak(live, who, r.text, "/projects/tasks/stats")
        body = r.json()
        assert body["total"] == len(want)
        groups = {g["keys"]["project"]: g["count"] for g in body["groups"]}
        assert groups == {sp.project: len(sp.tasks) for sp in _seen(live, who)}

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_a_hidden_project_is_the_404_of_a_missing_one(self, live, who):
        ctrl = _control(live, who)
        for sp in _unseen(live, who):
            await _not_found(live, who, "/projects/tasks/stats?project_id={id}", sp.space,
                             ctrl.space if ctrl else None)


class TestMyTasks:
    """Rule 4 in My tasks. A WAITING or INBOX overlay on a task of a hidden
    space is left out. The rows of Osprey stay."""

    @pytest.mark.parametrize("who", (A, B))
    async def test_the_single_read(self, live, who):
        """``/projects/my/tasks/{id}`` of a hidden task with a WAITING row of
        the member answers the 404 of a missing id. A visible WAITING row reads."""
        visible = live.spaces["AB"].tasks[0 if who == A else 1]
        for hidden in live.overlay_hidden[who]:
            await _not_found(live, who, "/projects/my/tasks/{id}", hidden, visible)

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_the_list_over_mine(self, live, who):
        r = await _ok(live, who, "/projects/tasks?scope=mine&page_size=100")
        _no_leak(live, who, r.text, "/projects/tasks?scope=mine")
        assert _row_ids(r.json()) == live.mine[who]
        assert r.json()["total"] == len(live.mine[who])

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_the_inbox(self, live, who):
        r = await _ok(live, who, "/projects/my/inbox?page_size=100")
        _no_leak(live, who, r.text, "/projects/my/inbox")
        assert _row_ids(r.json()) == live.mine[who]

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_the_overview_counts(self, live, who):
        r = await _ok(live, who, "/projects/my/summary")
        _no_leak(live, who, r.text, "/projects/my/summary")
        body = r.json()
        assert body["tasks"] == len(live.mine[who]), body
        if live.mine[who]:
            assert {str(c["id"]) for c in body["children"]} == {live.spaces["AB"].space}

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_assigned_to_me(self, live, who):
        r = await _ok(live, who, "/projects/assigned-to-me?page_size=100")
        _no_leak(live, who, r.text, "/projects/assigned-to-me")
        assert _row_ids(r.json()) == live.assigned[who]


class TestSearch:
    """Rule 4: a search never names a hidden task, by a word or by a number."""

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_a_word_in_every_title_finds_only_the_team_tasks(self, live, who):
        r = await _ok(live, who, f"/projects/search?q={WORD}&limit=50")
        _no_leak(live, who, r.text, "/projects/search")
        assert _row_ids(r.json()) == _tasks(_seen(live, who))

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_the_word_of_a_hidden_space_finds_nothing(self, live, who):
        for sp in _unseen(live, who):
            r = await _ok(live, who, f"/projects/search?q={sp.word}&limit=50")
            assert r.json()["rows"] == [], (who, sp.word)
        for sp in _seen(live, who):
            r = await _ok(live, who, f"/projects/search?q={sp.word}&limit=50")
            assert _row_ids(r.json()) == set(sp.tasks), (who, sp.word)

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_the_task_numbers_resolve_only_the_team_tasks(self, live, who):
        numbers = ",".join(str(n) for sp in live.spaces.values() for n in sp.numbers)
        r = await _ok(live, who, f"/projects/search/refs?numbers={numbers}")
        _no_leak(live, who, r.text, "/projects/search/refs")
        assert _row_ids(r.json()) == _tasks(_seen(live, who))


class TestTheExportAndTheDelta:
    @pytest.mark.parametrize("who", MEMBERS)
    async def test_the_csv_export(self, live, who):
        r = await _ok(live, who, "/projects/export/tasks.csv")
        _no_leak(live, who, r.text, "/projects/export/tasks.csv")
        _names_all(live, who, r.text, "/projects/export/tasks.csv")
        rows = list(csv.reader(io.StringIO(r.text)))
        assert len(rows) - 1 == len(_tasks(_seen(live, who))), "one line for each task"

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_the_delta_feed(self, live, who):
        r = await _ok(live, who, "/projects/delta/tasks")
        _no_leak(live, who, r.text, "/projects/delta/tasks")
        assert _row_ids(r.json()) == _tasks(_seen(live, who))


class TestADirectLinkIsNotFound:
    """Criterion 3: a hidden task and a hidden project answer the body of an
    id that does not exist, so the answer says nothing about what exists."""

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_a_hidden_task(self, live, who):
        ctrl = _control(live, who)
        for sp in _unseen(live, who):
            for task in sp.tasks:
                await _not_found(live, who, "/projects/tasks/{id}", task,
                                 ctrl.tasks[0] if ctrl else None)

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_a_hidden_space_and_its_project(self, live, who):
        ctrl = _control(live, who)
        for sp in _unseen(live, who):
            for kind in ("space", "project"):
                visible = getattr(ctrl, kind) if ctrl else None
                await _not_found(live, who, "/projects/nodes/{id}", getattr(sp, kind), visible)
                await _not_found(live, who, "/projects/nodes/{id}/grants",
                                 getattr(sp, kind), visible)

    async def test_the_control_a_visible_task_reads(self, live):
        task = live.spaces["AB"].tasks[0]
        for who in (A, B, AB):
            await _ok(live, who, f"/projects/tasks/{task}")


# ═══════════════════════════════════════════════════════════════════════════
# The assistant: the chat tools of skill_projects
# ═══════════════════════════════════════════════════════════════════════════


class TestTheAssistant:
    """Rule 4: an assistant answer. Each tool runs as the member, against the
    real gateway app, and its text is the answer the model reads."""

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_projects_tree(self, live, monkeypatch, who):
        from skill_projects import reads

        out = await _chat(live, monkeypatch, who, lambda: reads.projects_tree())
        _no_leak(live, who, out, "projects_tree")
        _names_all(live, who, out, "projects_tree")
        if not REACH[who]:
            assert out == "You can see no projects yet."

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_project_summary(self, live, monkeypatch, who):
        from skill_projects import reads

        out = await _chat(live, monkeypatch, who, lambda: reads.project_summary())
        _no_leak(live, who, out, "project_summary")
        _names_all(live, who, out, "project_summary")
        assert f"tasks {len(_tasks(_seen(live, who)))} " in out, out

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_find_tasks(self, live, monkeypatch, who):
        from skill_projects import reads

        out = await _chat(live, monkeypatch, who, lambda: reads.find_tasks(WORD, 50))
        _no_leak(live, who, out, "find_tasks")
        _names_all(live, who, out, "find_tasks")
        want = len(_tasks(_seen(live, who)))
        if want:
            assert f"in title or description ({want}):" in out, out
        else:
            assert out.startswith("No task matches"), out

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_list_tasks(self, live, monkeypatch, who):
        from skill_projects import reads

        out = await _chat(live, monkeypatch, who, lambda: reads.list_tasks(page_size=50))
        _no_leak(live, who, out, "list_tasks")
        _names_all(live, who, out, "list_tasks")
        want = len(_tasks(_seen(live, who)))
        if want:
            assert f"Tasks ({want} total, showing {want}," in out, out
        else:
            assert out == "No task matches those filters."

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_my_work(self, live, monkeypatch, who):
        """The inbox view reads INBOX rows over mine. ``a`` and ``b`` each have
        an INBOX row on a hidden task, which must stay out, and one on their
        assigned task of Osprey, which must show. The assigned view reads
        ``/projects/assigned-to-me``, which has no grant clause by design: an
        assignment is a way in, so only the positive control binds it."""
        from skill_projects import reads

        osprey = live.spaces["AB"].word
        for view in ("assigned", "inbox"):
            out = await _chat(live, monkeypatch, who, lambda v=view: reads.my_work(view=v))
            _no_leak(live, who, out, f"my_work {view}")
            if live.assigned[who]:
                assert osprey in out.lower(), (who, view, out)
            else:
                assert out.rstrip().endswith("nothing."), (who, view, out)

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_task_detail_of_a_hidden_task_is_the_refusal_of_a_missing_one(
            self, live, monkeypatch, who):
        from acb_common.tool_refusals import GatewayRefusal
        from skill_projects import reads

        async def refusal(task: str) -> tuple[Any, str]:
            with pytest.raises(GatewayRefusal) as caught:
                await _chat(live, monkeypatch, who, lambda: reads.task_detail(task))
            return caught.value.status, str(caught.value).replace(task, "<id>")

        missing = await refusal(MISSING)
        assert missing[0] == 404, missing
        for sp in _unseen(live, who):
            for task in sp.tasks:
                assert await refusal(task) == missing, (who, sp.word)
        ctrl = _control(live, who)
        if ctrl:
            out = await _chat(live, monkeypatch, who, lambda: reads.task_detail(ctrl.tasks[0]))
            assert f"full_id: {ctrl.tasks[0]}" in out and ctrl.word in out.lower(), out
            _no_leak(live, who, out, "task_detail")


# ═══════════════════════════════════════════════════════════════════════════
# The MCP door: an OAuth access token and a box token
# ═══════════════════════════════════════════════════════════════════════════

CREDENTIALS = ("oauth", "box")


class TestTheMcpDoor:
    """Rule 4: an MCP read. Each tool runs through ``POST /mcp`` of the real
    edge, which signs its request to the real gateway app (branch 0)."""

    @pytest.mark.parametrize("credential", CREDENTIALS)
    @pytest.mark.parametrize("who", MEMBERS)
    async def test_list_tasks(self, live, who, credential):
        token = await _token(live, who, credential)
        result = await _mcp(live, token, "list_tasks", {"limit": 100})
        assert result["isError"] is False, result
        _no_leak(live, who, json.dumps(result), "MCP list_tasks")
        got = result["structuredContent"]
        want = _tasks(_seen(live, who))
        assert {t["id"] for t in got["tasks"]} == want
        assert got["total"] == len(want)

    @pytest.mark.parametrize("credential", CREDENTIALS)
    @pytest.mark.parametrize("who", MEMBERS)
    async def test_board_stats(self, live, who, credential):
        token = await _token(live, who, credential)
        result = await _mcp(live, token, "board_stats", {"group_by": ["project"]})
        assert result["isError"] is False, result
        _no_leak(live, who, json.dumps(result), "MCP board_stats")
        got = result["structuredContent"]
        assert got["total"] == len(_tasks(_seen(live, who)))
        assert {g["keys"]["project"]: g["count"] for g in got["groups"]} == {
            sp.project: len(sp.tasks) for sp in _seen(live, who)}

    @pytest.mark.parametrize("credential", CREDENTIALS)
    @pytest.mark.parametrize("who", MEMBERS)
    async def test_list_projects(self, live, who, credential):
        token = await _token(live, who, credential)
        result = await _mcp(live, token, "list_projects", {"limit": 100})
        assert result["isError"] is False, result
        _no_leak(live, who, json.dumps(result), "MCP list_projects")
        got = result["structuredContent"]
        assert {p["id"] for p in got["projects"]} == _nodes(_seen(live, who))
        assert got["total"] == len(_nodes(_seen(live, who)))

    @pytest.mark.parametrize("credential", CREDENTIALS)
    @pytest.mark.parametrize("who", MEMBERS)
    async def test_search_tasks(self, live, who, credential):
        token = await _token(live, who, credential)
        result = await _mcp(live, token, "search_tasks", {"query": WORD})
        assert result["isError"] is False, result
        _no_leak(live, who, json.dumps(result), "MCP search_tasks")
        hits = result["structuredContent"]["hits"]
        assert {h["id"] for h in hits} == _tasks(_seen(live, who))

    @pytest.mark.parametrize("credential", CREDENTIALS)
    @pytest.mark.parametrize("who", MEMBERS)
    async def test_a_hidden_task_and_project_read_as_missing(self, live, who, credential):
        token = await _token(live, who, credential)
        missing_task = await _mcp(live, token, "get_task", {"task_id": MISSING})
        missing_node = await _mcp(live, token, "get_project", {"project_id": MISSING})
        assert missing_task["isError"] is True and missing_node["isError"] is True
        assert missing_task["content"][0]["text"] == tools.TEXT_TASK_NOT_FOUND
        assert missing_node["content"][0]["text"] == tools.TEXT_PROJECT_NOT_FOUND
        for sp in _unseen(live, who):
            for task in sp.tasks:
                got = await _mcp(live, token, "get_task", {"task_id": task})
                assert got == missing_task, (who, sp.word)
            for node in (sp.space, sp.project):
                got = await _mcp(live, token, "get_project", {"project_id": node})
                assert got == missing_node, (who, sp.word)
        # The positive control: a task and a space that the member may open.
        ctrl = _control(live, who)
        if ctrl:
            got = await _mcp(live, token, "get_task", {"task_id": ctrl.tasks[0]})
            assert got["isError"] is False and ctrl.tasks[0] in json.dumps(got), got
            _no_leak(live, who, json.dumps(got), "MCP get_task")
            got = await _mcp(live, token, "get_project", {"project_id": ctrl.space})
            assert got["isError"] is False and ctrl.space in json.dumps(got), got
            _no_leak(live, who, json.dumps(got), "MCP get_project")


# ═══════════════════════════════════════════════════════════════════════════
# Criterion 4: a change of the grants takes effect on the next read
# ═══════════════════════════════════════════════════════════════════════════


def _drop_space(world: Any, sp: SimpleNamespace) -> None:
    with world.admin.begin() as c:
        c.execute(text("DELETE FROM pm_tasks WHERE root_project_id = CAST(:s AS uuid)"),
                  {"s": sp.space})
        c.execute(text("DELETE FROM pm_projects WHERE id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": [sp.project, sp.space]})


async def _reads(world: Any, monkeypatch: Any, who: str, sp: SimpleNamespace) -> dict[str, bool]:
    """Which reads of *who* name the space *sp*: the app, the chat and MCP."""
    from skill_projects import reads

    tree = (await _ok(world, who, "/projects/tree")).text.lower()
    board = (await _ok(world, who, "/projects/tasks?page_size=100")).text.lower()
    task = await _as(world, who, "GET", f"/projects/tasks/{sp.tasks[0]}")
    chat = (await _chat(world, monkeypatch, who, lambda: reads.list_tasks(page_size=50))).lower()
    found = await _mcp(world, world.oauth[who], "list_tasks", {"limit": 100})
    mcp = json.dumps(found).lower()
    return {
        "tree": sp.word in tree,
        "board": sp.word in board,
        "task": task.status_code == 200,
        "chat": sp.word in chat,
        "mcp": sp.word in mcp,
    }


class TestTheNextRead:
    """No cache holds a grant. A change lands on the next read of the same
    session: the member's chat and the member's MCP token stay as they were."""

    async def test_a_deleted_grant_hides_the_space_on_the_next_read(self, live, monkeypatch):
        s = live.seeder
        sp = _space(s, "plover", (TEAM_A, LEAD), 1, lead=LEAD)
        try:
            for who in (A, AB):
                assert set((await _reads(live, monkeypatch, who, sp)).values()) == {True}, who
            # The lead removes the grant through the real route.
            r = await _as(live, LEAD, "DELETE",
                          f"/projects/nodes/{sp.space}/grants/{sp.grants[TEAM_A]}")
            assert r.status_code == 200, r.text
            for who in (A, AB):
                assert set((await _reads(live, monkeypatch, who, sp)).values()) == {False}, who
                _no_leak(live, who, (await _ok(live, who, "/projects/tree")).text, "tree")
                await _not_found(live, who, "/projects/tasks/{id}", sp.tasks[0])
                stats = (await _ok(live, who, "/projects/tasks/stats")).json()
                assert stats["total"] == len(_tasks(_seen(live, who)))
            # The lead keeps the space through the grant of its own address.
            await _ok(live, LEAD, f"/projects/tasks/{sp.tasks[0]}")
        finally:
            _drop_space(live, sp)

    async def test_a_member_who_leaves_a_team_loses_its_spaces_on_the_next_read(
            self, live, monkeypatch):
        s = live.seeder
        gid = _group(s, "team-c", "Team C", (A,))
        sp = _space(s, "avocet", ("group:team-c",), 1)
        try:
            assert set((await _reads(live, monkeypatch, A, sp)).values()) == {True}
            # The act of the members route: the membership row goes.
            with live.admin.begin() as c:
                c.execute(text("DELETE FROM org_group_member WHERE group_id ="
                               " CAST(:g AS uuid)"), {"g": gid})
            assert set((await _reads(live, monkeypatch, A, sp)).values()) == {False}
            await _not_found(live, A, "/projects/nodes/{id}", sp.space)
        finally:
            _drop_space(live, sp)
            with live.admin.begin() as c:
                c.execute(text("DELETE FROM org_group WHERE id = CAST(:g AS uuid)"),
                          {"g": gid})


# ═══════════════════════════════════════════════════════════════════════════
# The suite is honest
# ═══════════════════════════════════════════════════════════════════════════


class TestTheSuiteIsHonest:
    async def test_the_suite_runs_as_a_role_that_cannot_bypass_rls(self, live):
        engine = create_engine(make_url(live.app_dsn), future=True)
        try:
            with engine.connect() as c:
                row = c.execute(text(
                    "SELECT rolbypassrls, rolsuper FROM pg_roles"
                    " WHERE rolname = current_user")).one()
        finally:
            engine.dispose()
        assert (row.rolbypassrls, row.rolsuper) == (False, False)

    async def test_every_hidden_row_is_really_there(self, live):
        """A hidden row that does not exist would make every test pass."""
        with live.admin.connect() as c:
            tasks = c.execute(text(
                "SELECT count(*) FROM pm_tasks WHERE organization_id = CAST(:o AS uuid)"),
                {"o": live.org}).scalar()
            grants = {r.subject for r in c.execute(text(
                "SELECT subject FROM pm_project_grants WHERE organization_id ="
                " CAST(:o AS uuid)"), {"o": live.org})}
        assert tasks == sum(count for _w, _g, count in SPACES.values())
        assert grants == {TEAM_A, TEAM_B}

    async def test_the_teams_are_groups_of_the_directory(self, live):
        """``a`` is in team A only, ``b`` in team B only, ``ab`` in both."""
        with live.admin.connect() as c:
            rows = c.execute(text(
                "SELECT lower(au.email) AS email, g.slug FROM org_group_member m"
                " JOIN org_group g ON g.id = m.group_id"
                " JOIN app_user au ON au.id = m.user_id"
                " WHERE g.organization_id = CAST(:o AS uuid)"), {"o": live.org}).fetchall()
        got: dict[str, set[str]] = {}
        for row in rows:
            got.setdefault(row.email, set()).add(row.slug)
        assert got == {A: {"team-a"}, B: {"team-b"}, AB: {"team-a", "team-b"}}
