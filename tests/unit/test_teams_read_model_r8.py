"""T-1 (task 2532): the read model of teams, on a real database (R8).

Spec: ``project-docs/specs/project_management_app.md`` §14.9, rules 1, 2, 4
and 5, and ``navigation_shell.md`` §4.2a (one value: a team slug or ``all``).

A team is a group, ``group:<slug>`` (D12). There is no team table. The code
under test is ``gateway/routes/projects/team_scope.py`` (the value, the reach
and the flag ``TEAMS_ORGS``) and ``teams.py`` (``GET /projects/teams``), with
the ``team`` filter of the tree, the node list, the task list, the count and
the search. Each read runs through the real gateway app, as a role that
cannot bypass RLS.

**The world.** Tenant one has three groups, ``team-a``, ``team-b`` and
``team-c``, and six spaces. Each space has one project, and the project holds
the tasks. Each name of a space, a project and a task holds a word of its own,
so a leak check finds the word in the whole body of an answer.

* ``Kestrel space``: a grant to ``group:team-a``. One task.
* ``Heron space``: a grant to ``group:team-b``. Two tasks.
* ``Osprey space``: a grant to both groups. Four tasks, and one archived task
  that no list and no count reads.
* ``Gannet space``: a grant to the lead's address only. Its ``Tern project``
  has a grant to ``group:team-a``. Eight tasks. So team A reaches a project
  whose space it cannot read, and that space is never named.
* ``Plover space``: a grant to ``org``. Five tasks. Each member reads it, and
  no team holds it.
* ``Avocet space``: a grant to ``group:team-c``. Three tasks.

Tenant two has a group with the same slug ``team-a``, and a ``Puffin space``
granted to it. Its grant must reach nothing in tenant one.

**The members.** ``a`` is in team A only, ``b`` in team B only, ``ab`` in
both, ``c`` in team C only, and ``nobody`` in no team. ``admin`` holds
``data:org:read`` and is in team A, so the read of its groups takes the other
branch of ``resolve_visibility``. ``x`` is the member of tenant two.

**What it proves.**

* Two teams with different spaces, a space shared with both, a space shared
  with neither (Plover, Avocet), and a member in one team only.
* The count of each team equals the total of ``GET /projects/tasks`` and of
  ``/projects/tasks/stats`` under the same team, and the search under the team
  finds the same tasks. The tree and the node list hold the same nodes.
* A team scope only narrows (rule 5). ``all`` narrows nothing.
* A slug of another member's team is the 404 of a slug that names no group.
  A malformed value is 422, never 500. With ``TEAMS_ORGS`` off, a ``team``
  value is 404, and a read without one does not change.
* A removed grant and a removed member take effect on the next read.

**The mutations (R7).** Each planted fault below turned the named tests red
on 2026-11-11, and each was reverted. ``G`` is
``apps/services/gateway/gateway/routes/projects``.

* M1. ``G/team_scope.py`` ``resolve_team_scope`` gives a scope for any slug
  of the right shape. 7 fail: each read of
  ``TestTheValue.test_a_team_of_another_member_is_the_404_of_no_group``, and
  ``TestTheNextRead``.
* M2. ``G/team_scope.py`` ``parse_team`` without the ``isprintable`` check.
  7 fail: each read of ``TestTheValue.test_a_malformed_value_is_422``, and
  ``test_the_value_takes_a_slug_or_all_through_the_one_grammar``. A NUL then
  answers 404, not 500, because a slug reaches SQL only when it is a group
  of the caller.
* M3. ``G/tasks.py`` ``_task_scope`` drops the team clause. 14 fail: each
  team of ``TestCountsAgree.test_the_list_the_count_and_the_search_agree``
  and of ``TestTheTeamsOfAMember.test_a_team_names_its_spaces_and_only_those``,
  ``test_the_slug_is_matched_folded`` and ``TestTheNextRead``.
* M4. ``G/search.py`` drops the team clause. 7 fail: each team of
  ``test_the_list_the_count_and_the_search_agree``, and
  ``test_a_word_outside_the_team_finds_nothing_under_the_team``.
* M5. ``G/team_scope.py`` ``teams_allowed`` answers True. 3 fail: both cases
  of ``TestTheFlag`` and ``test_the_flag_is_off_by_default_and_reads_a_list_or_a_star``.
* M6. ``G/teams.py`` ``_readable`` keeps each parent id. 5 fail in
  ``TestTheTeamsOfAMember``, for ``a`` and ``ab``: the hidden Gannet space is
  then named by its id.

The tenant line of the grant row in ``_TEAM_PROJECTS_SQL`` cannot fail here
with RLS on, because RLS already hides a grant of tenant two.
``test_the_team_clause_names_the_tenant_on_both_arms`` pins it by its text.

Run (one R8 suite at a time)::

    bash scripts/dev_db.sh
    eval "$(bash scripts/dev_db.sh --export)"
    PYTEST_ADDOPTS="-o timeout_method=thread" \\
        uv run pytest tests/unit/test_teams_read_model_r8.py -v -rs
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_auth import access as access_module
from acb_auth.access import invalidate as invalidate_access
from acb_common import get_settings
from fastapi import HTTPException
from gateway.routes.projects import team_scope
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import tenant_engine_scope

_REPO = Path(__file__).resolve().parents[2]

_APP_ROLE = "acb_app_teamsread"
_APP_PW = "teamsread_scratch_pw"
INTERNAL = "teams-read-internal-token"
DOMAIN = "teamsread.test"

A = f"a@{DOMAIN}"
B = f"b@{DOMAIN}"
AB = f"ab@{DOMAIN}"
C = f"c@{DOMAIN}"
NOBODY = f"nobody@{DOMAIN}"
ADMIN = f"admin@{DOMAIN}"
#: The lead of Gannet space, and of the spaces that the session tests make.
LEAD = f"lead@{DOMAIN}"
#: The member of tenant two.
X = f"x@{DOMAIN}"
#: ``created_by`` of the seeded rows. Not a member, so it opens nothing.
MAKER = f"maker@{DOMAIN}"

#: A word in every task title, so one search reaches every task.
WORD = "marmot"
#: An id that no row has.
MISSING = "00000000-0000-4000-8000-000000000000"

#: Each space of tenant one: (the word of the space, the grants of the space,
#: the word of its project, the grants of the project, the task count).
SPACES: dict[str, tuple[str, tuple[str, ...], str, tuple[str, ...], int]] = {
    "K": ("kestrel", ("group:team-a",), "kestrel", (), 1),
    "H": ("heron", ("group:team-b",), "heron", (), 2),
    "O": ("osprey", ("group:team-a", "group:team-b"), "osprey", (), 4),
    "G": ("gannet", (LEAD,), "tern", ("group:team-a",), 8),
    "P": ("plover", ("org",), "plover", (), 5),
    "V": ("avocet", ("group:team-c",), "avocet", (), 3),
}

#: The tops of each team view: space key -> the node that is the top. A grant
#: of a space makes the space the top. Team A holds only the project of G.
TEAMS: dict[str, dict[str, str]] = {
    "team-a": {"K": "space", "O": "space", "G": "project"},
    "team-b": {"H": "space", "O": "space"},
    "team-c": {"V": "space"},
}

#: The teams of each member of tenant one, in the order of the slugs.
MEMBER_TEAMS: dict[str, tuple[str, ...]] = {
    A: ("team-a",),
    B: ("team-b",),
    AB: ("team-a", "team-b"),
    C: ("team-c",),
    NOBODY: (),
    ADMIN: ("team-a",),
}
MEMBERS = tuple(MEMBER_TEAMS)

#: Each pair of a member and one of its teams.
MEMBER_TEAM_PAIRS = tuple(
    (who, slug) for who, slugs in MEMBER_TEAMS.items() for slug in slugs)

#: The reads that take ``team``. ``{t}`` is the value.
TEAM_READS: tuple[str, ...] = (
    "/projects/teams?team={t}",
    "/projects/tasks?team={t}",
    "/projects/tasks/stats?team={t}",
    "/projects/tree?team={t}",
    "/projects/nodes?team={t}",
    f"/projects/search?q={WORD}&team={{t}}",
)


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


def _task(seeder: w._Seeder, title: str, project: str, root: str, status: str,
          *, archived: bool = False) -> str:
    seeder.number += 1
    # An hour back, as the T-7 world does, so no read sees a row of "now".
    return str(seeder.one(
        "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
        " created_by, organization_id, task_number, updated_at, archived_at)"
        " VALUES (:t, CAST(:p AS uuid), CAST(:r AS uuid), CAST(:s AS uuid), :c,"
        " CAST(:o AS uuid), :n, now() - interval '1 hour',"
        " CASE WHEN :arch THEN now() - interval '1 hour' END) RETURNING id",
        t=title, p=project, r=root, s=status, c=MAKER, o=seeder.org,
        n=seeder.number, arch=archived))


def _space(seeder: w._Seeder, word: str, grants: tuple[str, ...], pword: str,
           pgrants: tuple[str, ...], count: int, *, archived: int = 0,
           lead: str | None = None) -> SimpleNamespace:
    """A space with its grants and its lane, one project with its grants, and
    *count* live tasks in the project, plus *archived* archived ones."""
    space = _node(seeder, f"{word.title()} space", owns=True, lead=lead)
    grant_ids = {subject: _grant(seeder, space, subject) for subject in grants}
    status = str(seeder.one(
        "INSERT INTO pm_task_statuses (project_id, name, color, position, category,"
        " organization_id) VALUES (CAST(:p AS uuid), 'To do', 'gray', 0, 'todo',"
        " CAST(:o AS uuid)) RETURNING id", p=space, o=seeder.org))
    project = _node(seeder, f"{pword.title()} project", parent=space)
    for subject in pgrants:
        _grant(seeder, project, subject)
    tasks = [_task(seeder, f"{WORD} {pword} {i}", project, space, status)
             for i in range(1, count + 1)]
    gone = [_task(seeder, f"{WORD} {pword} archived {i}", project, space, status,
                  archived=True) for i in range(1, archived + 1)]
    return SimpleNamespace(word=word, pword=pword, space=space, project=project,
                           status=status, tasks=tasks, archived=gone,
                           grants=grant_ids)


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: two tenants, the groups and the spaces."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_teamsread"
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

    org, org2 = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'teamsread', 'teamsread')"), {"id": org})
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'teamsread-two', 'teamsread two')"), {"id": org2})
    s = w._Seeder(eng, org)
    s.number = 810_000
    for who in (*MEMBERS, LEAD):
        perms = ["feature:projects"] + (["data:org:read"] if who == ADMIN else [])
        s.member(who, perms)
    groups = {
        "team-a": _group(s, "team-a", "Team A", (A, AB, ADMIN)),
        "team-b": _group(s, "team-b", "Team B", (B, AB)),
        "team-c": _group(s, "team-c", "Team C", (C,)),
    }
    spaces = {
        key: _space(s, word, grants, pword, pgrants, count,
                    archived=1 if key == "O" else 0)
        for key, (word, grants, pword, pgrants, count) in SPACES.items()
    }

    s2 = w._Seeder(eng, org2)
    s2.number = 910_000
    s2.member(X, ["feature:projects"])
    _group(s2, "team-a", "Team A of two", (X,))
    puffin = _space(s2, "puffin", ("group:team-a",), "puffin", (), 2)

    world_ = SimpleNamespace(admin=eng, org=org, org2=org2, seeder=s, seeder2=s2,
                             groups=groups, spaces=spaces, puffin=puffin)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


# ── What each team holds ───────────────────────────────────────────────────


def _team_nodes(world: Any, slug: str) -> set[str]:
    """The node ids of one team view of tenant one."""
    out: set[str] = set()
    for key, top in TEAMS[slug].items():
        sp = world.spaces[key]
        out |= {sp.space, sp.project} if top == "space" else {sp.project}
    return out


def _team_tasks(world: Any, slug: str) -> set[str]:
    return {t for key in TEAMS[slug] for t in world.spaces[key].tasks}


def _top_id(world: Any, key: str, top: str) -> str:
    sp = world.spaces[key]
    return sp.space if top == "space" else sp.project


def _words_outside(world: Any, slugs: tuple[str, ...]) -> set[str]:
    """Each word of tenant one and two that no team of *slugs* holds."""
    held: set[str] = set()
    for slug in slugs:
        for key, top in TEAMS[slug].items():
            sp = world.spaces[key]
            held.add(sp.pword)
            if top == "space":
                held.add(sp.word)
    every = {sp.word for sp in world.spaces.values()}
    every |= {sp.pword for sp in world.spaces.values()} | {world.puffin.word}
    return every - held


def _no_word(body: str, words: set[str], where: str) -> None:
    low = body.lower()
    for word in sorted(words):
        assert word not in low, f"{where} names {word}"


def _no_id(world: Any, body: str, slugs: tuple[str, ...], where: str) -> None:
    """No id of a node that no team of *slugs* holds, and no task id at all."""
    outside = _words_outside(world, slugs)
    for sp in (*world.spaces.values(), world.puffin):
        if sp.word in outside:
            assert sp.space not in body, f"{where} gives the id of {sp.word} space"
        if sp.pword in outside:
            assert sp.project not in body, f"{where} gives the id of {sp.pword} project"
        for task in (*sp.tasks, *sp.archived):
            assert task not in body, f"{where} gives a task id of {sp.word}"


def _flatten(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes:
        out.append(node)
        out += _flatten(node.get("children") or [])
    return out


def _row_ids(body: dict[str, Any]) -> set[str]:
    return {str(row["id"]) for row in body["rows"]}


# ═══════════════════════════════════════════════════════════════════════════
# The doors
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def live(monkeypatch, world):
    """The real gateway app, with ``TEAMS_ORGS`` holding both tenants."""
    from gateway.main import app as gateway_app

    # The import names the router for `scripts/affected_tests.py` (CIV-18),
    # so a change to a Projects route module selects this fence.
    from gateway.routes.projects import router as projects_router

    assert projects_router.routes and gateway_app.routes
    monkeypatch.setattr(get_settings(), "teams_orgs", f"{world.org},{world.org2}",
                        raising=False)
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", DOMAIN)
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    # A process-wide latch that an earlier suite can set
    # (`test_mcp_edge_authority.py` says why).
    monkeypatch.setattr(access_module, "_tables_missing", False)
    invalidate_access()
    try:
        yield world
    finally:
        invalidate_access()


async def _as(world: Any, who: str, path: str) -> httpx.Response:
    """One GET to the gateway app as the person *who*, on the role of the suite."""
    from gateway.main import app as gateway_app

    headers = {"Authorization": f"Bearer {INTERNAL}", "X-User-Email": who}

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.get(path, headers=headers)

    async with tenant_engine_scope(world.app_dsn):
        return await asyncio.create_task(go())


async def _ok(world: Any, who: str, path: str) -> httpx.Response:
    r = await _as(world, who, path)
    assert r.status_code == 200, (who, path, r.status_code, r.text)
    return r


def _team_row(body: dict[str, Any], slug: str) -> dict[str, Any]:
    rows = [row for row in body["rows"] if row["slug"] == slug]
    assert len(rows) == 1, (slug, body)
    return rows[0]


# ═══════════════════════════════════════════════════════════════════════════
# The read model
# ═══════════════════════════════════════════════════════════════════════════


class TestTheTeamsOfAMember:
    """``GET /projects/teams``: which groups, and which spaces each reaches."""

    pytestmark = w._DB_GATE

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_each_member_gets_one_row_for_each_team(self, live, who):
        r = await _ok(live, who, "/projects/teams")
        body = r.json()
        assert [row["slug"] for row in body["rows"]] == list(MEMBER_TEAMS[who])
        assert body["total"] == len(MEMBER_TEAMS[who])
        for row in body["rows"]:
            assert row["subject"] == f"group:{row['slug']}"
        _no_word(r.text, _words_outside(live, MEMBER_TEAMS[who]), f"{who} /projects/teams")
        if who != ADMIN:
            # The admin reads Gannet space, so a top may name it as a parent.
            _no_id(live, r.text, MEMBER_TEAMS[who], f"{who} /projects/teams")

    @pytest.mark.parametrize(("who", "slug"), MEMBER_TEAM_PAIRS)
    async def test_a_team_names_its_spaces_and_only_those(self, live, who, slug):
        r = await _ok(live, who, f"/projects/teams?team={slug}")
        body = r.json()
        assert [row["slug"] for row in body["rows"]] == [slug]
        row = body["rows"][0]
        tops = {space["id"]: space for space in row["spaces"]}
        want = {_top_id(live, key, top): (key, top) for key, top in TEAMS[slug].items()}
        assert set(tops) == set(want), (who, slug, row)
        for top_id, (key, top) in want.items():
            sp, space = live.spaces[key], tops[top_id]
            assert space["level"] == top, (who, slug, space)
            assert space["tasks"] == len(sp.tasks), (who, slug, space)
            under = {p["id"] for p in space["projects"]}
            assert under == ({sp.project} if top == "space" else set())
        assert row["tasks"] == len(_team_tasks(live, slug))
        assert sum(space["tasks"] for space in row["spaces"]) == row["tasks"]
        _no_word(r.text, _words_outside(live, (slug,)), f"{who} team {slug}")
        if who != ADMIN:
            _no_id(live, r.text, (slug,), f"{who} team {slug}")

    async def test_a_space_of_a_team_that_the_caller_cannot_read_is_not_named(self, live):
        """Team A holds Tern project, and Gannet space above it has no grant of
        team A. A member of team A reads the project as a top, and the space is
        not named and not counted."""
        gannet = live.spaces["G"]

        def tern_top(body: dict[str, Any]) -> dict[str, Any]:
            row = _team_row(body, "team-a")
            return next(s for s in row["spaces"] if s["id"] == gannet.project)

        for who in (A, AB):
            r = await _ok(live, who, "/projects/teams?team=team-a")
            assert gannet.space not in r.text and "gannet" not in r.text.lower()
            top = tern_top(r.json())
            assert (top["level"], top["parent_project_id"]) == ("project", None)
        # The admin reads Gannet space, so the top names its parent. Team A
        # still holds only the project, and the space is no top of it.
        tree = await _ok(live, ADMIN, "/projects/tree")
        assert gannet.space in tree.text
        r = await _ok(live, ADMIN, "/projects/teams?team=team-a")
        assert "gannet" not in r.text.lower()
        assert tern_top(r.json())["parent_project_id"] == gannet.space
        tops = {s["id"] for s in _team_row(r.json(), "team-a")["spaces"]}
        assert gannet.space not in tops

    async def test_a_space_that_no_team_holds_is_in_no_row(self, live):
        """Plover has a grant to ``org``: each member reads it, no team holds it."""
        plover = live.spaces["P"]
        for who in MEMBERS:
            tree = await _ok(live, who, "/projects/tree")
            assert plover.space in tree.text, who
            teams = await _ok(live, who, "/projects/teams")
            assert plover.space not in teams.text and "plover" not in teams.text.lower()

    async def test_a_group_of_another_tenant_with_the_same_slug_reaches_nothing(self, live):
        """Tenant two has its own ``team-a`` and a space granted to it."""
        puffin = live.puffin
        r = await _ok(live, X, "/projects/teams")
        rows = r.json()["rows"]
        assert [row["slug"] for row in rows] == ["team-a"]
        assert [s["id"] for s in rows[0]["spaces"]] == [puffin.space]
        assert rows[0]["tasks"] == len(puffin.tasks)
        _no_word(r.text, _words_outside(live, ()) - {"puffin"}, "tenant two")
        for who in (A, AB, ADMIN):
            mine = await _ok(live, who, "/projects/teams?team=team-a")
            assert puffin.space not in mine.text and "puffin" not in mine.text.lower()

    async def test_all_is_every_team(self, live):
        for who in MEMBERS:
            plain = await _ok(live, who, "/projects/teams")
            every = await _ok(live, who, "/projects/teams?team=all")
            assert every.json() == plain.json(), who


class TestCountsAgree:
    """The count of a team equals the list, the count read and the search of
    the same team. The tree and the node list hold the same nodes."""

    pytestmark = w._DB_GATE

    @pytest.mark.parametrize(("who", "slug"), MEMBER_TEAM_PAIRS)
    async def test_the_list_the_count_and_the_search_agree(self, live, who, slug):
        want = _team_tasks(live, slug)
        row = _team_row((await _ok(live, who, f"/projects/teams?team={slug}")).json(), slug)
        assert row["tasks"] == len(want)

        listed = (await _ok(live, who, f"/projects/tasks?team={slug}&page_size=100"
                                       "&with_status_counts=true")).json()
        assert _row_ids(listed) == want
        assert listed["total"] == row["tasks"]
        assert sum((listed["status_counts"] or {}).values()) == row["tasks"]

        stats = (await _ok(live, who, f"/projects/tasks/stats?team={slug}"
                                      "&group_by=project")).json()
        assert stats["total"] == row["tasks"]
        per_project = {g["keys"]["project"]: g["count"] for g in stats["groups"]}
        assert per_project == {
            live.spaces[key].project: len(live.spaces[key].tasks) for key in TEAMS[slug]}

        found = (await _ok(live, who, f"/projects/search?q={WORD}&team={slug}"
                                      "&limit=50")).json()
        assert _row_ids(found) == want and found["truncated"] is False

    @pytest.mark.parametrize(("who", "slug"), MEMBER_TEAM_PAIRS)
    async def test_the_tree_and_the_node_list_hold_the_team_nodes(self, live, who, slug):
        want = _team_nodes(live, slug)
        tree = await _ok(live, who, f"/projects/tree?team={slug}")
        body = tree.json()
        assert {n["id"] for n in _flatten(body["rows"])} == want
        assert body["total"] == len(want)
        _no_word(json.dumps(body["rows"]), _words_outside(live, (slug,)), f"tree {slug}")
        nodes = (await _ok(live, who, f"/projects/nodes?team={slug}")).json()
        assert _row_ids(nodes) == want and nodes["total"] == len(want)

    async def test_a_word_outside_the_team_finds_nothing_under_the_team(self, live):
        """``ab`` reads Heron. Under team A a search for it finds nothing, and
        with no team it finds Heron's tasks: the positive control."""
        heron = live.spaces["H"]
        scoped = (await _ok(live, AB, "/projects/search?q=heron&team=team-a")).json()
        assert scoped["rows"] == []
        plain = (await _ok(live, AB, "/projects/search?q=heron")).json()
        assert _row_ids(plain) == set(heron.tasks)

    async def test_the_archived_task_is_in_no_count(self, live):
        """The archived task of Osprey is a real row, and no team counts it."""
        osprey = live.spaces["O"]
        assert osprey.archived
        for slug in ("team-a", "team-b"):
            row = _team_row((await _ok(live, AB, f"/projects/teams?team={slug}")).json(),
                            slug)
            assert osprey.archived[0] not in json.dumps(row)
            space = next(s for s in row["spaces"] if s["id"] == osprey.space)
            assert space["tasks"] == len(osprey.tasks)


class TestAScopeNeverGrants:
    """Rule 5: a team view is inside the view with no team, and ``all`` is
    the view with no team."""

    pytestmark = w._DB_GATE

    @pytest.mark.parametrize(("who", "slug"), MEMBER_TEAM_PAIRS)
    async def test_a_team_view_is_inside_the_whole_view(self, live, who, slug):
        whole = _row_ids((await _ok(live, who, "/projects/tasks?page_size=100")).json())
        team = _row_ids((await _ok(live, who, f"/projects/tasks?team={slug}"
                                              "&page_size=100")).json())
        assert team and team <= whole
        whole_nodes = _row_ids((await _ok(live, who, "/projects/nodes")).json())
        team_nodes = _row_ids((await _ok(live, who, f"/projects/nodes?team={slug}")).json())
        assert team_nodes and team_nodes <= whole_nodes

    @pytest.mark.parametrize("who", MEMBERS)
    async def test_all_narrows_nothing(self, live, who):
        for path in ("/projects/tasks?page_size=100", "/projects/nodes",
                     f"/projects/search?q={WORD}&limit=50"):
            plain = (await _ok(live, who, path)).json()
            every = (await _ok(live, who, f"{path}&team=all" if "?" in path
                                else f"{path}?team=all")).json()
            assert _row_ids(every) == _row_ids(plain), (who, path)
        plain = (await _ok(live, who, "/projects/tree")).json()
        every = (await _ok(live, who, "/projects/tree?team=ALL")).json()
        assert {n["id"] for n in _flatten(every["rows"])} == {
            n["id"] for n in _flatten(plain["rows"])}


class TestTheValue:
    """The one value: a slug of a team of the caller, or ``all``."""

    pytestmark = w._DB_GATE

    @pytest.mark.parametrize("path", TEAM_READS)
    async def test_a_team_of_another_member_is_the_404_of_no_group(self, live, path):
        none = await _as(live, A, path.format(t="no-such-team"))
        assert none.status_code == 404, none.text
        assert none.json() == {"detail": team_scope.TEAM_NOT_FOUND}
        for who, slug in ((A, "team-b"), (A, "team-c"), (B, "team-a"),
                          (NOBODY, "team-a")):
            got = await _as(live, who, path.format(t=slug))
            assert (got.status_code, got.json()) == (404, none.json()), (who, slug, path)
        # The positive control: the caller's own team reads.
        await _ok(live, A, path.format(t="team-a"))

    @pytest.mark.parametrize("path", TEAM_READS)
    async def test_a_malformed_value_is_422(self, live, path):
        for value in ("", "%20", "%00", "a%0Ab", "team%09a"):
            got = await _as(live, A, path.format(t=value))
            assert got.status_code == 422, (path, value, got.status_code, got.text)
            assert got.json() == {"detail": team_scope.TEAM_SHAPE}

    async def test_the_slug_is_matched_folded(self, live):
        upper = (await _ok(live, A, "/projects/tasks?team=TEAM-A&page_size=100")).json()
        assert _row_ids(upper) == _team_tasks(live, "team-a")


class TestTheFlag:
    """While ``TEAMS_ORGS`` does not hold the org, a ``team`` value is 404
    and a read with no ``team`` does not change."""

    pytestmark = w._DB_GATE

    @pytest.mark.parametrize("off", ("", "{other}"))
    async def test_off_is_404_for_a_team_value(self, live, monkeypatch, off):
        monkeypatch.setattr(get_settings(), "teams_orgs", off.format(other=live.org2),
                            raising=False)
        for path in TEAM_READS:
            for value in ("team-a", "all", "%00"):
                got = await _as(live, A, path.format(t=value))
                assert (got.status_code, got.json()) == (404, {"detail": "Not Found"}), (
                    path, value)
        assert (await _as(live, A, "/projects/teams")).status_code == 404
        # The search refuses before its empty-query answer too.
        got = await _as(live, A, "/projects/search?q=&team=team-a")
        assert got.status_code == 404
        # A read with no team does not change.
        listed = (await _ok(live, A, "/projects/tasks?page_size=100")).json()
        assert set(live.spaces["K"].tasks) <= _row_ids(listed)
        if off:
            # The other tenant is on its list, so its member still reads.
            await _ok(live, X, "/projects/teams")


class TestTheNextRead:
    """No cache holds a grant or a member of a team."""

    pytestmark = w._DB_GATE

    async def test_a_removed_grant_and_a_removed_member_take_effect_at_once(self, live):
        s = live.seeder
        gid = _group(s, "team-d", "Team D", (A,))
        sp = _space(s, "lapwing", ("group:team-d", LEAD), "lapwing", (), 1, lead=LEAD)
        try:
            row = _team_row((await _ok(live, A, "/projects/teams")).json(), "team-d")
            assert [x["id"] for x in row["spaces"]] == [sp.space] and row["tasks"] == 1
            with live.admin.begin() as c:
                c.execute(text("DELETE FROM pm_project_grants WHERE id = CAST(:g AS uuid)"),
                          {"g": sp.grants["group:team-d"]})
            row = _team_row((await _ok(live, A, "/projects/teams")).json(), "team-d")
            assert row["spaces"] == [] and row["tasks"] == 0
            listed = (await _ok(live, A, "/projects/tasks?team=team-d")).json()
            assert listed["rows"] == [] and listed["total"] == 0
            with live.admin.begin() as c:
                c.execute(text("DELETE FROM org_group_member WHERE group_id ="
                               " CAST(:g AS uuid)"), {"g": gid})
            body = (await _ok(live, A, "/projects/teams")).json()
            assert [row["slug"] for row in body["rows"]] == ["team-a"]
            got = await _as(live, A, "/projects/tasks?team=team-d")
            assert (got.status_code, got.json()) == (404, {"detail": team_scope.TEAM_NOT_FOUND})
        finally:
            with live.admin.begin() as c:
                c.execute(text("DELETE FROM pm_tasks WHERE root_project_id ="
                               " CAST(:s AS uuid)"), {"s": sp.space})
                c.execute(text("DELETE FROM pm_projects WHERE id = ANY(CAST(:ids AS uuid[]))"),
                          {"ids": [sp.project, sp.space]})
                c.execute(text("DELETE FROM org_group WHERE id = CAST(:g AS uuid)"),
                          {"g": gid})


class TestTheSuiteIsHonest:
    pytestmark = w._DB_GATE

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
            other = c.execute(text(
                "SELECT count(*) FROM pm_project_grants WHERE organization_id ="
                " CAST(:o AS uuid) AND subject = 'group:team-a'"), {"o": live.org2}).scalar()
        # Each live task, and the one archived task of Osprey.
        assert tasks == sum(spec[-1] for spec in SPACES.values()) + 1
        assert grants == {"group:team-a", "group:team-b", "group:team-c", "org", LEAD}
        assert other == 1


# ═══════════════════════════════════════════════════════════════════════════
# No database: the flag and the value
# ═══════════════════════════════════════════════════════════════════════════


def test_the_flag_is_off_by_default_and_reads_a_list_or_a_star(monkeypatch) -> None:
    org, other = str(uuid.uuid4()), str(uuid.uuid4())
    settings = get_settings()
    assert type(settings).model_fields["teams_orgs"].default == ""
    for raw, org_on, other_on in (
        ("", False, False),
        (f" {org.upper()} , x", True, False),
        ("*", True, True),
        (other, False, True),
    ):
        monkeypatch.setattr(settings, "teams_orgs", raw)
        assert team_scope.teams_allowed(org) is org_on, raw
        assert team_scope.teams_allowed(other) is other_on, raw
    monkeypatch.setattr(settings, "teams_orgs", "*")
    for nobody in (None, "", "  "):
        assert team_scope.teams_allowed(nobody) is False


def test_the_flag_is_named_in_the_three_places_of_a_flag() -> None:
    from acb_common.env_guard import is_platform_env
    from gateway.flag_reload import reloadable

    env = (_REPO / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^TEAMS_ORGS=$", env, re.MULTILINE)
    assert is_platform_env("TEAMS_ORGS")
    assert reloadable("TEAMS_ORGS")


def test_the_value_takes_a_slug_or_all_through_the_one_grammar() -> None:
    assert team_scope.parse_team("all") is None
    assert team_scope.parse_team(" ALL ") is None
    assert team_scope.parse_team("Team-A") == "group:team-a"
    for bad in ("", "  ", "\x00", "a\nb", "a​b"):
        with pytest.raises(HTTPException) as caught:
            team_scope.parse_team(bad)
        assert caught.value.status_code == 422, repr(bad)


def test_the_team_clause_names_the_tenant_on_both_arms() -> None:
    """The grant row and each step of the walk carry ``:vis_org``, as the
    visibility closure does (``core._VISIBLE_PROJECTS_SQL``)."""
    sql = team_scope._TEAM_PROJECTS_SQL
    seed, step = sql.split("UNION")
    assert "g.organization_id = CAST(:vis_org AS uuid)" in seed
    assert "p.organization_id = CAST(:vis_org AS uuid)" in step
    for arm in (seed, step):
        assert "sealed_at IS NULL" in arm and "personal_owner IS NULL" in arm
    assert "UNION ALL" not in sql, "UNION ends the walk on a cycle"
