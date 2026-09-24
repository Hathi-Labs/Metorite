"""Projects · the TENANT boundary — what one organization cannot see (WS-29b).

Spec: ``project-docs/specs/multi_tenancy.md`` §3 (D-MT-1 (a), D-MT-3) and
§6. Schema: migration 161.

``test_projects_grants.py`` is the fence between two *departments of one
company*. This is the fence between two *companies*, and it is a different
question with a different failure mode: a grant bug shows somebody the wrong
project, a tenancy bug shows somebody another business's entire portfolio.

**Every test here seeds TWO organizations**, because a one-organization suite
cannot tell a scoped route from an unscoped one — the same reason
``test_projects_grants.py`` never tests as the owner.

⚠️ **The two lines §6 calls the retrofit's most dangerous** are pinned
individually, because each of them was CORRECT before this ticket and becomes a
cross-tenant leak the day a second organization exists:

1. ``pm_project_grants.subject = 'org'`` meant "everybody". It must mean
   "everybody **in this organization**".
2. ``data:org:read`` short-circuited to ``unrestricted=True``, whose clause was
   the literal ``TRUE``. It must mean unrestricted **within a tenant**.

And a third this suite adds, which §6 does not name: ``pm_task_assignees``
holds a bare email (D-PM-4) and ``load_visible_task``'s second arm matches on
it. Nothing stops organization B typing organization A's member into it, so the
tenant has to be composed ABOVE that arm rather than inside the grant closure.

Hermetic: no Postgres, no network, no TestClient. The database's own half — the
``pm_organization_from_parent`` trigger that derives a child's tenant and
refuses a mismatched one — is proved against a real Postgres, not here; this
file's fake fills the column the way the trigger does and says so.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from gateway.routes.projects import activities as pm_activities
from gateway.routes.projects import admin as pm_admin
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import me as pm_me
from gateway.routes.projects import personal as pm_personal
from gateway.routes.projects import search as pm_search
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree
from gateway.routes.projects import views as pm_views

from tests.unit._projects_fakes import (
    DEFAULT_ORGANIZATION,
    FakeProjectsDB,
    bind_db,
    member_user,
    page,
    projects_user,
    silence_events,
)

MODULES = (
    pm_core, pm_tree, pm_tasks, pm_activities, pm_admin, pm_views, pm_me,
    pm_search, pm_personal,
)

#: Two organizations, and the ids are readable so a failure message says which.
ORG_A = DEFAULT_ORGANIZATION
ORG_B = "00000000-0000-4000-8000-0000000000bb"

#: One person each. D-MT-1 (a): one email, one person, one organization — which
#: is exactly why the tenant is derivable from `X-User-Email` alone.
ANA = member_user("ana@alpha.example")
BEN = member_user("ben@beta.example")

#: ⚠️ The same address in both directories. Structurally impossible under
#: D-MT-1 (a) (`app_user.email` is globally UNIQUE) — used only where a test
#: needs to prove that a route reads the ORGANIZATION and not the string.
BOSS_A = projects_user("boss@alpha.example")


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    """Two tenants, two directory rows, and no accidental default.

    ``organization_id = None`` on the fake so that a caller who is NOT seeded
    into ``app_user`` resolves to nothing — a suite where the fallback quietly
    supplied a tenant would prove nothing about the lookup.
    """
    fake = FakeProjectsDB()
    fake.organization_id = None
    fake.seed("app_user", email="ana@alpha.example", status="active",
              organization_id=ORG_A)
    fake.seed("app_user", email="boss@alpha.example", status="active",
              organization_id=ORG_A)
    fake.seed("app_user", email="ben@beta.example", status="active",
              organization_id=ORG_B)
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


def _no_groups(monkeypatch: pytest.MonkeyPatch, *groups: str) -> None:
    """Pin group membership, carrying the tenant through.

    Same helper as ``test_projects_grants.py``: the group lookup joins tables
    belonging to the access system, which this app's fake does not model.
    """
    real = pm_core.resolve_visibility

    async def _resolve(db, user):
        vis = await real(db, user)
        if vis.unrestricted:
            return vis
        return pm_core.Visibility(
            unrestricted=False, email=vis.email, groups=tuple(groups),
            organization_id=vis.organization_id,
        )

    for module in MODULES:
        monkeypatch.setattr(module, "resolve_visibility", _resolve, raising=False)


def _two_tenants(db: FakeProjectsDB, *, subject: str = "org") -> tuple:
    """One project per organization, each granted the SAME way.

    Granting both identically is the point: if the two are distinguishable
    afterwards, only the tenant can have distinguished them.
    """
    alpha = db.seed_project(name="Alpha work", subject=subject,
                            organization_id=ORG_A)
    beta = db.seed_project(name="Beta work", subject=subject,
                           organization_id=ORG_B)
    return alpha, beta


# ── ⚠️ Leak 1: `subject = 'org'` ────────────────────────────────────────────

async def test_an_org_grant_reaches_only_the_granting_organization(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⚠️ THE line. `subject = 'org'` means "everybody"; multi_tenancy.md §6
    calls making it mean "everybody in THIS organization" the single most
    dangerous edit in the retrofit — "today it is correct, and after the first
    second tenant onboards it is a cross-tenant leak".

    What breaks without it: every project in the deployment is org-granted by
    default (`create_node` writes that grant itself), so a caller in ANY
    organization sees the entire database.
    """
    _no_groups(monkeypatch)
    alpha, beta = _two_tenants(db)

    assert (await pm_tree.get_node(str(alpha.id), user=ANA))["id"] == str(alpha.id)
    with pytest.raises(HTTPException) as exc:
        await pm_tree.get_node(str(beta.id), user=ANA)
    assert exc.value.status_code == 404


async def test_the_project_list_shows_one_organization_at_a_time(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The portfolio read, which is the one a person actually opens."""
    _no_groups(monkeypatch)
    _two_tenants(db)

    for user, expected in ((ANA, ["Alpha work"]), (BEN, ["Beta work"])):
        listed = await pm_tree.list_nodes(user=user)
        assert sorted(r["name"] for r in listed["rows"]) == expected


async def test_an_email_grant_does_not_cross_the_tenant_either(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⚠️ The parenthesis test, stated as behaviour.

    `WHERE org = :o AND (a OR b OR c)` and `WHERE org = :o AND a OR b OR c` are
    one character apart and Postgres accepts both. The second scopes the
    `subject = 'org'` arm alone and leaves the email and group arms wide open —
    the same leak wearing a subtler hat, and the one a reader's eye skips.
    """
    _no_groups(monkeypatch)
    db.seed_project(name="Beta private", subject="ana@alpha.example",
                    organization_id=ORG_B)

    listed = await pm_tree.list_nodes(user=ANA)
    assert [r["name"] for r in listed["rows"]] == []


async def test_a_group_grant_does_not_cross_the_tenant_either(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third arm, for the same reason as the second. Group slugs are not
    unique across organizations — every company has a `group:sales`."""
    _no_groups(monkeypatch, "group:sales")
    db.seed_project(name="Beta sales", subject="group:sales",
                    organization_id=ORG_B)
    db.seed_project(name="Alpha sales", subject="group:sales",
                    organization_id=ORG_A)

    listed = await pm_tree.list_nodes(user=ANA)
    assert [r["name"] for r in listed["rows"]] == ["Alpha sales"]


async def test_a_granted_subtree_stops_at_the_tenant(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The closure's RECURSIVE step carries the tenant too.

    Migration 161's trigger already makes a cross-tenant parent impossible, so
    this is the defence-in-depth arm: the closure must not be the thing that
    would leak if that trigger were ever dropped.
    """
    _no_groups(monkeypatch)
    alpha = db.seed_project(name="Alpha root", subject="org",
                            organization_id=ORG_A)
    db.seed_project(name="Smuggled", subject=None, parent=str(alpha.id),
                    organization_id=ORG_B)

    listed = await pm_tree.list_nodes(user=ANA)
    assert sorted(r["name"] for r in listed["rows"]) == ["Alpha root"]


# ── ⚠️ Leak 2: `data:org:read` ──────────────────────────────────────────────

async def test_org_read_is_unrestricted_within_a_tenant_not_across_them(
    db: FakeProjectsDB,
) -> None:
    """⚠️ The second leak §6 does not name but §3 implies.

    `data:org:read` is "the permission that opens the whole portfolio". Whose
    portfolio was never a question worth asking while there was one
    organization. Both clause helpers answered the literal `TRUE` for this
    caller; `TRUE` is every row in the table.
    """
    alpha = db.seed_project(name="Alpha work", subject=None,
                            organization_id=ORG_A)
    beta = db.seed_project(name="Beta work", subject=None,
                           organization_id=ORG_B)

    # Ungranted in their OWN organization, and still visible — that is what
    # `data:org:read` buys, and it must keep working.
    assert (await pm_tree.get_node(str(alpha.id), user=BOSS_A))["id"] == str(alpha.id)

    with pytest.raises(HTTPException) as exc:
        await pm_tree.get_node(str(beta.id), user=BOSS_A)
    assert exc.value.status_code == 404


async def test_an_org_read_holders_task_list_stops_at_their_tenant(
    db: FakeProjectsDB,
) -> None:
    """`task_visibility_clause`'s unrestricted arm, which was `TRUE`."""
    alpha = db.seed_project(name="Alpha", subject=None, organization_id=ORG_A)
    beta = db.seed_project(name="Beta", subject=None, organization_id=ORG_B)
    a_status = db.seed_status(str(alpha.id))
    b_status = db.seed_status(str(beta.id))
    db.seed_task(str(alpha.id), str(a_status.id), title="Ours")
    db.seed_task(str(beta.id), str(b_status.id), title="Theirs")

    listed = await pm_tasks.list_tasks(user=BOSS_A, page=page())
    assert [r["title"] for r in listed.rows] == ["Ours"]


async def test_search_does_not_reach_across_the_tenant_for_anybody(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search is the widest read in the app — it deliberately spans every
    project the caller can see, so it is where a missing tenant costs most.
    Checked for BOTH principals, because they take different arms of the clause.
    """
    _no_groups(monkeypatch)
    alpha = db.seed_project(name="Alpha", subject="org", organization_id=ORG_A)
    beta = db.seed_project(name="Beta", subject="org", organization_id=ORG_B)
    db.seed_task(str(alpha.id), str(db.seed_status(str(alpha.id)).id),
                 title="Quarterly margin review")
    db.seed_task(str(beta.id), str(db.seed_status(str(beta.id)).id),
                 title="Quarterly margin secrets")

    for user in (ANA, BOSS_A):
        hits = await pm_search.search_tasks(q="quarterly", user=user)
        assert [r["title"] for r in hits["rows"]] == ["Quarterly margin review"]


# ── ⚠️ Leak 3: the assignee escape hatch ───────────────────────────────────

async def test_being_named_as_an_assignee_in_another_tenant_grants_nothing(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⚠️ The leak §6 does not name.

    `load_visible_task`'s second arm exists so a task delegated ACROSS a Center
    boundary is still openable by the person expected to do it — matched on
    `lower(a.assignee) = :vis_email`, a bare string (D-PM-4).

    Nothing validates that string. Anyone in organization B can type
    `ana@alpha.example` into it, and without the tenant composed ABOVE the two
    arms that row hands Ana the task's title, description and timeline. This is
    why the clause is `(tenant AND (grants OR assigned))` and not
    `(tenant AND grants) OR assigned`.
    """
    _no_groups(monkeypatch)
    beta = db.seed_project(name="Beta", subject=None, organization_id=ORG_B)
    task = db.seed_task(str(beta.id), str(db.seed_status(str(beta.id)).id),
                        title="Their acquisition memo")
    db.seed("pm_task_assignees", task_id=str(task.id),
            assignee="ana@alpha.example", assigned_by="ben@beta.example",
            organization_id=ORG_B)

    with pytest.raises(HTTPException) as exc:
        await pm_tasks.get_task(str(task.id), user=ANA)
    assert exc.value.status_code == 404

    listed = await pm_tasks.list_tasks(user=ANA, page=page())
    assert [r["title"] for r in listed.rows] == []


async def test_an_assignee_in_the_SAME_tenant_still_sees_the_task(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other direction, and the reason the arm exists at all.

    Without this the tenant predicate would look correct while having quietly
    removed cross-Center delegation — a feature, not a leak, and one the
    previous test alone cannot tell apart from a route that dropped the arm.
    """
    _no_groups(monkeypatch)
    alpha = db.seed_project(name="Alpha finance", subject=None,
                            organization_id=ORG_A)
    task = db.seed_task(str(alpha.id), str(db.seed_status(str(alpha.id)).id),
                        title="One thing for Finance")
    db.seed("pm_task_assignees", task_id=str(task.id),
            assignee="ana@alpha.example", assigned_by="boss@alpha.example",
            organization_id=ORG_A)

    row = await pm_tasks.get_task(str(task.id), user=ANA)
    assert row["title"] == "One thing for Finance"


# ── The resolver ────────────────────────────────────────────────────────────

async def test_the_tenant_comes_from_the_directory_not_from_the_request(
    db: FakeProjectsDB,
) -> None:
    """D-MT-1 (a). `X-User-Email` → `app_user.organization_id`, and nothing the
    caller sends can influence it — which is the whole reason (a) was safe to
    take without touching any app's auth seam."""
    vis = await pm_core.resolve_visibility(db, ANA)
    assert vis.organization_id == ORG_A
    assert vis.params["vis_org"] == ORG_A


async def test_org_read_does_not_short_circuit_the_tenant_lookup(
    db: FakeProjectsDB,
) -> None:
    """⚠️ Order-of-operations, pinned. `data:org:read` short-circuits the GROUP
    lookup — groups cannot change an unrestricted answer. It must not
    short-circuit the ORGANIZATION lookup, or the clause binds `:vis_org = NULL`
    and either fails closed for the wrong reason or, worse, is written back to
    `TRUE` by whoever debugs it.
    """
    vis = await pm_core.resolve_visibility(db, BOSS_A)
    assert vis.unrestricted is True
    assert vis.organization_id == ORG_A
    assert vis.params["vis_org"] == ORG_A


async def test_a_caller_the_directory_does_not_know_sees_nothing(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail CLOSED, and by construction rather than by a check: every clause
    compares to `CAST(:vis_org AS uuid)`, and `column = NULL` is NULL in SQL.

    This is the shape a service identity or a stale session takes. It must see
    nothing rather than everything, and it must not need an `if` to do so.
    """
    _no_groups(monkeypatch)
    _two_tenants(db)
    stranger = member_user("nobody@nowhere.example")

    vis = await pm_core.resolve_visibility(db, stranger)
    assert vis.organization_id is None
    listed = await pm_tree.list_nodes(user=stranger)
    assert listed["rows"] == []


async def test_an_org_read_holder_the_directory_does_not_know_sees_nothing(
    db: FakeProjectsDB,
) -> None:
    """The dangerous combination: the widest permission and no tenant. `TRUE`
    would have shown them everything; the tenant clause shows them nothing."""
    _two_tenants(db, subject=None)
    ghost = projects_user("ghost@nowhere.example")

    listed = await pm_tree.list_nodes(user=ghost)
    assert listed["rows"] == []


# ── Writes ──────────────────────────────────────────────────────────────────

async def test_creating_a_root_project_stamps_the_callers_organization(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ONE decision point. `pm_projects` is the root of every other `pm_*`
    row, so this is the only value the database cannot derive."""
    _no_groups(monkeypatch)
    created = await pm_tree.create_node(pm_tree.ProjectIn(name="New"), user=ANA)

    row = next(p for p in db.rows("pm_projects") if str(p["id"]) == created["id"])
    assert row["organization_id"] == ORG_A
    # …and the grant it writes for itself is in the same organization, or the
    # project would be invisible to the person who just created it.
    grant = next(g for g in db.rows("pm_project_grants")
                 if str(g["project_id"]) == created["id"])
    assert grant["organization_id"] == ORG_A


async def test_a_caller_with_no_organization_cannot_create_a_project(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """403, not 404 (and not a 500 from `NOT NULL`).

    R5's 404 rule is about RECORDS — it exists so an error code cannot be used
    to probe what exists elsewhere. This says nothing about any record: it is
    the caller's own account that is not set up, and answering 404 would send
    somebody hunting for a project that was never created.
    """
    _no_groups(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await pm_tree.create_node(
            pm_tree.ProjectIn(name="Orphan"),
            user=member_user("nobody@nowhere.example"),
        )
    assert exc.value.status_code == 403
    assert not db.rows("pm_projects")


async def test_a_capture_creates_the_personal_project_in_the_callers_tenant(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second (and last) place that decides a tenant: a personal project is
    a ROOT project, so nothing upstream can supply one."""
    _no_groups(monkeypatch)
    await pm_personal.capture(pm_personal.CaptureIn(title="Think"), user=ANA)

    project = next(p for p in db.rows("pm_projects")
                   if p.get("personal_owner") == "ana@alpha.example")
    assert project["organization_id"] == ORG_A


async def test_a_hidden_project_cannot_be_used_as_a_parent_across_tenants(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writing is a way of reading. Grafting onto another organization's
    project would inherit its grants — access widened by writing rather than by
    being granted — so the parent must be visible first, and the tenant is what
    makes it invisible."""
    _no_groups(monkeypatch)
    beta = db.seed_project(name="Beta", subject="org", organization_id=ORG_B)

    with pytest.raises(HTTPException) as exc:
        await pm_tree.create_node(
            pm_tree.ProjectIn(name="Wedge", parent_project_id=str(beta.id)),
            user=ANA,
        )
    assert exc.value.status_code == 404


# ── The clause, read as text ────────────────────────────────────────────────
#
# Behaviour is the real assertion; these two read the SQL because the mirror can
# only ever agree with itself, and the shape of these particular clauses is what
# a reviewer's eye is worst at.

def test_the_closure_scopes_all_three_subject_arms_together() -> None:
    """⚠️ The parenthesis, structurally. `AND` binds tighter than `OR`, so
    without the brackets the tenant applies to `subject = 'org'` alone.

    ⚠️ **Loosened 2026-09-23, and the loosening is bounded on purpose.** This
    asserted that the tenant predicate was textually ADJACENT to the opening
    bracket. D63's seal filter (migration 215) now sits between them. That is
    a legitimate `AND` in the same conjunction and it changes nothing about
    the binding.

    Adjacency was never the property worth holding. It was a cheap proxy for
    it, and it broke the first time somebody added a second legitimate
    predicate. So this states the real property instead: the tenant arm is
    present, the three subject arms share ONE bracket, and everything between
    the two is `AND`-joined. An `OR` in that gap is what would actually
    re-open the WS-29b leak, and the old assertion could not have seen one
    either — it would simply have failed, the same way, for any change at all.
    """
    sql = " ".join(pm_core._VISIBLE_PROJECTS_SQL.split())

    tenant = "g.organization_id = CAST(:vis_org AS uuid)"
    opens = "(g.subject = 'org'"
    assert tenant in sql, "the grant seed lost its tenant predicate"
    assert opens in sql
    assert "ANY(:vis_groups))" in sql, "the three subject arms share one bracket"

    # Everything the closure adds between the tenant and the subject bracket
    # must be conjunctive. One `OR` here detaches the tenant from the other two
    # arms, which is the leak this whole file exists to prevent.
    between = sql[sql.index(tenant) + len(tenant) : sql.index(opens)]
    assert " OR " not in between.upper(), (
        f"an OR between the tenant and the subject arms re-opens the leak: {between!r}"
    )
    assert between.strip().endswith("AND"), (
        f"the subject bracket must be AND-joined to what precedes it: {between!r}"
    )


def test_the_task_clause_puts_the_tenant_outside_both_arms() -> None:
    """⚠️ `(tenant AND (grants OR assigned))`, never
    `(tenant AND grants) OR assigned` — the second leaves the assignee escape
    hatch reachable from any organization."""
    vis = pm_core.Visibility(
        unrestricted=False, email="ana@alpha.example", groups=(),
        organization_id=ORG_A,
    )
    clause = " ".join(pm_core.task_visibility_clause(vis).split())
    assert clause.startswith("(t.organization_id = CAST(:vis_org AS uuid) AND (")
    assert clause.endswith(")))")


def test_no_clause_helper_can_answer_the_literal_TRUE() -> None:
    """The regression this whole file exists to prevent, in one line.

    `TRUE` was the unrestricted answer from both helpers before WS-29b, and it
    is the answer somebody reaches for when a tenant clause is inconvenient.
    """
    for unrestricted in (True, False):
        vis = pm_core.Visibility(
            unrestricted=unrestricted, email="ana@alpha.example", groups=(),
            organization_id=ORG_A,
        )
        assert vis.project_clause() != "TRUE"
        assert pm_core.task_visibility_clause(vis) != "TRUE"
        assert vis.params["vis_org"] == ORG_A


# ── ⚠️ The two reads with NO grant clause at all ────────────────────────────
#
# `/assigned-to-me` and `/my/inbox` deliberately carry no visibility clause:
# assignment IS the claim, and filtering them by project grants would hide work
# from the person asked to do it. That makes them the two routes where the
# tenant is the only fence there is — and the two the grant-shaped tests above
# cannot cover, because there is no grant to get wrong.
#
# Both were found unscoped by driving them against a real two-tenant database
# after every grant-based read was already green.

async def test_assigned_to_me_does_not_import_another_tenants_work(
    db: FakeProjectsDB,
) -> None:
    """⚠️ The worst of the three leaks, because it does not stop at a response.

    WS-27e's personal mirror SYNCS this endpoint into the Tasks app's
    ``gtd_items``. A row organization B can create by typing an address would
    therefore be COPIED into Ana's personal task manager and outlive the
    request that leaked it.
    """
    beta = db.seed_project(name="Beta", subject=None, organization_id=ORG_B)
    theirs = db.seed_task(str(beta.id), str(db.seed_status(str(beta.id)).id),
                          title="Their acquisition memo")
    db.seed("pm_task_assignees", task_id=str(theirs.id),
            assignee="ana@alpha.example", assigned_by="ben@beta.example",
            organization_id=ORG_B)

    alpha = db.seed_project(name="Alpha", subject=None, organization_id=ORG_A)
    mine = db.seed_task(str(alpha.id), str(db.seed_status(str(alpha.id)).id),
                        title="My actual work")
    db.seed("pm_task_assignees", task_id=str(mine.id),
            assignee="ana@alpha.example", assigned_by="boss@alpha.example",
            organization_id=ORG_A)

    listed = await pm_me.assigned_to_me(user=ANA, page=page())
    assert [r["title"] for r in listed.rows] == ["My actual work"]


async def test_my_inbox_does_not_import_another_tenants_work(
    db: FakeProjectsDB,
) -> None:
    """The GTD inbox, same shape and same reason. Its first arm is the same
    unvalidated string match; its second (my personal project) is safe only
    because `personal_owner` is written from the session."""
    beta = db.seed_project(name="Beta", subject=None, organization_id=ORG_B)
    theirs = db.seed_task(str(beta.id), str(db.seed_status(str(beta.id)).id),
                          title="Their acquisition memo")
    db.seed("pm_task_assignees", task_id=str(theirs.id),
            assignee="ana@alpha.example", assigned_by="ben@beta.example",
            organization_id=ORG_B)

    listed = await pm_personal.my_inbox(user=ANA, page=page())
    assert [r["title"] for r in listed.rows] == []
