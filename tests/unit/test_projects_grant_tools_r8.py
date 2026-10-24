"""WS-64 OP-6b: the grant tools of the chat against the real gateway (R8, D-OP-9).

Spec: ``project-docs/specs/one_projects_app.md`` §5a, D-OP-11 and §9 OP-6b,
done-when 3 of OP-6: "A grant write from the chat succeeds only where the
member could write the grant in the app."

``grant_access`` and ``revoke_access`` run through the skill's own client
against the REAL gateway app, on a promoted catalog of their own as a
NON-privileged role. The world is the one of ``test_projects_write_authority.py``
(OP-8): a creator, a lead, an admin and a viewer, and one more member who is
not granted the project. Each test makes its own root project. Its creator is
CREATOR, its lead is LEAD, and the four are granted it. The fifth member is not.

What a fake cannot prove, and this file does:

* a lead grants ``org`` through the chat tool, the row is written, and the
  fifth member now sees the project;
* an admin who does not lead the project revokes that grant, and the fifth
  member no longer sees it;
* a viewer and the creator who is not a lead are refused BEFORE any card,
  with the 403 sentence of the routes, and no grant row changes;
* a declined card writes nothing;
* a subject outside the grammar is refused, at the tool and at the route.

Run (a private ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_projects_grant_tools_r8.py -v -rs

**The mutations (R7).** Each one turned the named test red. Commit before each.

* R1. ``guarded.py`` ``grant_refusal``: returns ``""``.
  ``test_r8_a_viewer_and_the_creator_get_no_card_and_the_servers_sentence``.
* R2. ``tree.py`` ``get_node``: no ``attach_can_write_grants``.
  ``test_r8_a_viewer_and_the_creator_get_no_card_and_the_servers_sentence``.
* R3. ``tree.py`` ``create_grant``: no ``assert_may_write_grant``, and the tool
  does not read the flag. ``test_r8_the_route_still_decides_without_the_flag``.
* R4. ``guarded.py`` ``grant_access``: skip the card.
  ``test_r8_a_declined_card_writes_nothing``.
* R5. ``guarded.py`` ``revoke_access``: send the DELETE for the first grant in
  the list. ``test_r8_an_admin_revokes_and_the_member_loses_sight``.

Fix round 1 (adversarial review). Each one turned the named test red.

* R6. ``authority.py`` ``grant_write_refusal``: no ``in_personal_tree`` check.
  ``test_r8_nobody_changes_the_grants_of_a_personal_space``.
* R7. ``authority.py`` ``_PERSONAL_TREE_SQL``: the node only, no walk up.
  ``test_r8_a_team_node_under_a_personal_root_is_personal_too``.
* R8. ``tree.py`` ``create_grant``: no ``assert_grant_group_exists``.
  ``test_r8_a_group_that_does_not_exist_is_refused_at_the_tool_and_the_route``.
* R9. ``tree.py`` ``list_grants``: ``yours`` always false.
  ``test_r8_a_lead_who_revokes_their_own_grant_is_warned_first``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

from sqlalchemy import text

from tests.unit import test_mcp_edge_delete as d
from tests.unit import test_mcp_edge_writes as w
from tests.unit._projects_agent_fakes import approve, deny
from tests.unit.test_projects_write_authority import (  # noqa: F401 — the fixtures
    ADMIN,
    CREATOR,
    LEAD,
    VIEWER,
    _as,
    _chat,
    _grants,
    live,
    live_app,
    redis_fake,
    world,
)

pytestmark = w._DB_GATE

#: The fifth member: in the organization, and not granted the project.
OUTSIDER = w.MEMBER


def _words() -> str:
    from gateway.routes.projects.authority import GRANT_WRITE_REFUSED

    return GRANT_WRITE_REFUSED


def _private(world: Any, name: str) -> str:
    """A root project made by CREATOR, led by LEAD, granted to the four."""
    pid = str(world.seeder.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, owns_statuses, lead) VALUES (:n, 'active', 'manual', :c,"
        " CAST(:o AS uuid), 'Asia/Kolkata', true, :l) RETURNING id",
        n=name, c=CREATOR, o=world.org, l=LEAD))
    for who in (CREATOR, LEAD, ADMIN, VIEWER):
        d._grant(world.seeder, pid, who)
    return pid


def _grant_rows(world: Any, project: str) -> list[tuple[str, str]]:
    with world.admin.begin() as c:
        return sorted((str(r.id), r.subject) for r in c.execute(text(
            "SELECT id, subject FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)"),
            {"p": project}))


async def _sees(world: Any, who: str, project: str) -> bool:
    r = await _as(world, who, "GET", f"/projects/nodes/{project}")
    assert r.status_code in (200, 404), r.text
    return r.status_code == 200


async def test_r8_the_node_read_carries_the_flag_for_each_member(live) -> None:
    pid = _private(live, f"op6b flags {uuid.uuid4().hex[:6]}")
    for who, may in ((LEAD, True), (ADMIN, True), (VIEWER, False), (CREATOR, False)):
        got = (await _as(live, who, "GET", f"/projects/nodes/{pid}")).json()
        assert got["can_write_grants"] is may, (who, got)
        if may:
            assert "grant_refusal" not in got
        else:
            assert got["grant_refusal"] == _words()


async def test_r8_a_lead_grants_org_and_the_member_now_sees_it(live, monkeypatch) -> None:
    from skill_projects import guarded

    pid = _private(live, f"op6b grant {uuid.uuid4().hex[:6]}")
    assert not await _sees(live, OUTSIDER, pid)
    asked = approve(monkeypatch)
    out = await _chat(live, monkeypatch, LEAD, lambda: guarded.grant_access(pid, "org"))
    assert len(asked) == 1 and "everyone in the organization" in asked[0]["detail"], asked
    assert out.startswith("everyone in the organization can now see"), out
    assert "org" in _grants(live, pid)
    assert await _sees(live, OUTSIDER, pid)


async def test_r8_an_admin_revokes_and_the_member_loses_sight(live, monkeypatch) -> None:
    from skill_projects import guarded

    pid = _private(live, f"op6b revoke {uuid.uuid4().hex[:6]}")
    d._grant(live.seeder, pid, "org")
    assert await _sees(live, OUTSIDER, pid)
    before = _grant_rows(live, pid)
    [org_id] = [gid for gid, subject in before if subject == "org"]
    asked = approve(monkeypatch)
    out = await _chat(live, monkeypatch, ADMIN, lambda: guarded.revoke_access(pid, "org"))
    assert len(asked) == 1 and out.startswith("Removed the grant of everyone in the organization")
    assert f"grant_id: {org_id}" in out
    # Only the org grant went. The four members keep theirs.
    assert _grant_rows(live, pid) == [g for g in before if g[0] != org_id]
    assert not await _sees(live, OUTSIDER, pid)


async def test_r8_a_viewer_and_the_creator_get_no_card_and_the_servers_sentence(
    live, monkeypatch,
) -> None:
    from skill_projects import guarded

    pid = _private(live, f"op6b refused {uuid.uuid4().hex[:6]}")
    d._grant(live.seeder, pid, "group:design")
    before = _grant_rows(live, pid)
    [design] = [gid for gid, subject in before if subject == "group:design"]
    asked = approve(monkeypatch)
    for who in (VIEWER, CREATOR):
        for act in (
            lambda: guarded.grant_access(pid, "org"),
            lambda: guarded.revoke_access(pid, design),
        ):
            out = await _chat(live, monkeypatch, who, act)
            assert out.startswith("Refused:"), (who, out)
            assert f"Gateway said: «{_words()}»" in out, (who, out)
    assert asked == [], "no card for a grant write the server refuses"
    assert _grant_rows(live, pid) == before


async def test_r8_the_route_still_decides_without_the_flag(live, monkeypatch) -> None:
    """The flag is the chat's courtesy. Without it the card shows, and the route
    still answers 403 with the same sentence and writes nothing."""
    from skill_projects import guarded

    pid = _private(live, f"op6b route {uuid.uuid4().hex[:6]}")
    before = _grant_rows(live, pid)
    monkeypatch.setattr(guarded, "grant_refusal", lambda _node: "")
    asked = approve(monkeypatch)
    from skill_projects.refusals import refusals_as_text

    out = await _chat(live, monkeypatch, VIEWER,
                      lambda: refusals_as_text(guarded.grant_access)(pid, "org"))
    assert len(asked) == 1
    assert out.startswith("Refused:") and _words() in out, out
    assert _grant_rows(live, pid) == before


async def test_r8_a_declined_card_writes_nothing(live, monkeypatch) -> None:
    from skill_projects import guarded
    from skill_projects import writes as W

    pid = _private(live, f"op6b declined {uuid.uuid4().hex[:6]}")
    d._grant(live.seeder, pid, "group:design")
    before = _grant_rows(live, pid)
    deny(monkeypatch)
    out = await _chat(live, monkeypatch, LEAD, lambda: guarded.grant_access(pid, "org"))
    assert out == W.CANCELLED
    out = await _chat(live, monkeypatch, LEAD,
                      lambda: guarded.revoke_access(pid, "group:design"))
    assert out == W.CANCELLED
    assert _grant_rows(live, pid) == before


async def test_r8_a_subject_outside_the_grammar_is_refused(live, monkeypatch) -> None:
    from skill_projects import guarded

    pid = _private(live, f"op6b grammar {uuid.uuid4().hex[:6]}")
    before = _grant_rows(live, pid)
    asked = approve(monkeypatch)
    out = await _chat(live, monkeypatch, LEAD, lambda: guarded.grant_access(pid, "everybody"))
    assert out.startswith("Refused: grant_access could not read the subject"), out
    assert asked == []
    # The route says the same: one grammar, two doors.
    r = await _as(live, LEAD, "POST", f"/projects/nodes/{pid}/grants", {"subject": "everybody"})
    assert r.status_code == 422 and "Unknown grant subject" in r.json()["detail"], r.text
    assert _grant_rows(live, pid) == before


# ── Fix round 1 ──────────────────────────────────────────────────────────────

#: A member who sees every project of the organization (``data:org:read``)
#: and is a Projects admin. Only such a member reaches another member's
#: personal tree, through ``_TENANT_PROJECTS_SQL``.
ORG_ADMIN = "orgadmin@mcpwrites.test"


def _org_admin(wld: Any) -> str:
    with wld.admin.begin() as c:
        known = c.execute(text("SELECT 1 FROM app_user WHERE email = :e"),
                          {"e": ORG_ADMIN}).first()
    if known is None:
        wld.seeder.member(
            ORG_ADMIN, ["feature:projects", "projects:settings:write", "data:org:read"])
    return ORG_ADMIN


async def _personal_root(wld: Any, who: str) -> str:
    """The personal root of *who*, minted by the real route (the bootstrap)."""
    r = await _as(wld, who, "POST", "/projects/my/project")
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_r8_nobody_changes_the_grants_of_a_personal_space(live, monkeypatch) -> None:
    from gateway.routes.projects.authority import PERSONAL_GRANT_REFUSED
    from skill_projects import guarded

    admin = _org_admin(live)
    root = await _personal_root(live, VIEWER)
    # The bootstrap wrote the owner's grant directly, not through the route.
    before = _grant_rows(live, root)
    [owner_grant] = [gid for gid, subject in before if subject == VIEWER]
    r = await _as(live, VIEWER, "POST", "/projects/my/areas", {"name": f"Home {uuid.uuid4().hex[:4]}"})
    assert r.status_code == 201, r.text
    area = r.json()["id"]
    assert [s for _g, s in _grant_rows(live, area)] == [VIEWER]

    # The org admin sees the tree, and the flag says no, with the sentence.
    got = (await _as(live, admin, "GET", f"/projects/nodes/{root}")).json()
    assert got["can_write_grants"] is False, got
    assert got["grant_refusal"] == PERSONAL_GRANT_REFUSED

    for node in (root, area):
        # An admin grants org: 403, no row.
        r = await _as(live, admin, "POST", f"/projects/nodes/{node}/grants", {"subject": "org"})
        assert r.status_code == 403 and r.json()["detail"] == PERSONAL_GRANT_REFUSED, r.text
        # The member themself cannot share their own space either.
        r = await _as(live, VIEWER, "POST", f"/projects/nodes/{node}/grants",
                      {"subject": "org"})
        assert r.status_code == 403 and r.json()["detail"] == PERSONAL_GRANT_REFUSED, r.text
    # An admin revokes the owner's grant: 403, the row stays.
    r = await _as(live, admin, "DELETE", f"/projects/nodes/{root}/grants/{owner_grant}")
    assert r.status_code == 403 and r.json()["detail"] == PERSONAL_GRANT_REFUSED, r.text
    assert _grant_rows(live, root) == before

    # The chat refuses before its card, with the same sentence.
    asked = approve(monkeypatch)
    for act in (lambda: guarded.grant_access(root, "org"),
                lambda: guarded.revoke_access(root, owner_grant)):
        out = await _chat(live, monkeypatch, admin, act)
        assert out.startswith("Refused:") and PERSONAL_GRANT_REFUSED in out, out
    assert asked == []
    assert _grant_rows(live, root) == before
    assert not await _sees(live, OUTSIDER, root)


async def test_r8_a_team_node_under_a_personal_root_is_personal_too(live) -> None:
    """A node without ``personal_owner`` that sits under a personal root is
    still in the private tree. The walk up finds it."""
    from gateway.routes.projects.authority import PERSONAL_GRANT_REFUSED

    admin = _org_admin(live)
    root = await _personal_root(live, VIEWER)
    child = str(live.seeder.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, parent_project_id) VALUES (:n, 'active', 'manual', :c,"
        " CAST(:o AS uuid), 'Asia/Kolkata', CAST(:p AS uuid)) RETURNING id",
        n=f"unstamped {uuid.uuid4().hex[:6]}", c=VIEWER, o=live.org, p=root))
    r = await _as(live, admin, "POST", f"/projects/nodes/{child}/grants", {"subject": "org"})
    assert r.status_code == 403 and r.json()["detail"] == PERSONAL_GRANT_REFUSED, r.text
    assert "org" not in _grants(live, child)


def _group(wld: Any, slug: str, name: str) -> None:
    wld.seeder.one(
        "INSERT INTO org_group (organization_id, slug, display_name) VALUES"
        " (CAST(:o AS uuid), :s, :n) ON CONFLICT (organization_id, slug)"
        " DO UPDATE SET display_name = EXCLUDED.display_name RETURNING id",
        o=wld.org, s=slug, n=name)


async def test_r8_a_group_that_does_not_exist_is_refused_at_the_tool_and_the_route(
    live, monkeypatch,
) -> None:
    from skill_projects import guarded

    _group(live, "design", "Design")
    pid = _private(live, f"op6b group {uuid.uuid4().hex[:6]}")
    before = _grant_rows(live, pid)
    asked = approve(monkeypatch)
    out = await _chat(live, monkeypatch, LEAD, lambda: guarded.grant_access(pid, "group:desing"))
    assert out.startswith("Refused: There is no group called «desing»"), out
    assert "«Design» («group:design»)" in out, out
    assert asked == []
    # The route says the same, with the groups in the sentence.
    r = await _as(live, LEAD, "POST", f"/projects/nodes/{pid}/grants", {"subject": "group:desing"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"].startswith("There is no group called desing. The groups are: ")
    assert "Design (group:design)" in r.json()["detail"], r.text
    assert _grant_rows(live, pid) == before
    # A group that exists is granted.
    out = await _chat(live, monkeypatch, LEAD, lambda: guarded.grant_access(pid, "group:design"))
    assert len(asked) == 1 and "can now see" in out, out
    assert "group:design" in _grants(live, pid)


async def test_r8_a_lead_who_revokes_their_own_grant_is_warned_first(
    live, monkeypatch,
) -> None:
    from skill_projects import guarded

    pid = _private(live, f"op6b self {uuid.uuid4().hex[:6]}")
    got = (await _as(live, LEAD, "GET", f"/projects/nodes/{pid}/grants")).json()
    assert {r["subject"]: r["yours"] for r in got["rows"]} == {
        CREATOR: False, LEAD: True, ADMIN: False, VIEWER: False}, got
    assert got["you_see_all"] is False
    asked = approve(monkeypatch)
    out = await _chat(live, monkeypatch, LEAD, lambda: guarded.revoke_access(pid, LEAD))
    [impact] = [x for x in asked[0]["context"].splitlines() if x.startswith("impact: ")]
    assert impact.startswith(f"impact: {guarded.LOSE_SIGHT_CLAUSE} access narrows"), impact
    assert guarded.LAST_GRANT_CLAUSE not in impact
    assert out.startswith("Removed the grant"), out
    assert not await _sees(live, LEAD, pid)
