"""WS-27bj slice 2 / D-PM-16 — the effective-vocabulary READ path.

Spec: ``project-docs/specs/project_management_app.md`` §9.11. Schema: migration
175, whose written form ``test_projects_org_vocabularies.py`` pins and whose
BEHAVIOUR was proved against a real Postgres (R8) — a partial-unique pair is
exactly the thing a hermetic fake agrees with whatever it is handed.

What this file pins is the half that lives in Python:

* **``org-wide ∪ root-local``**, on all three vocabularies and on every reader —
  the list, the create's duplicate check, the export's columns, and the
  validation that decides what a task may store. A field that lists but whose
  values are then refused as an unknown key would be the worst of both.
* **root-local SHADOWS org-wide**, and for tags that is a correctness rule
  rather than a preference: ``pm_tasks.tags`` stores display text (migration
  156), so two registry rows describe ONE tag on every task and the union has to
  yield one colour.
* **the flag gates the CREATE, never the read.** Creating an org-wide row is the
  half that is hard to walk back.
* **an org-wide row may be RENAMED and nothing else** (owner decision,
  2026-09-20). Merge and delete stay refused: a rename is reversible by
  renaming back, and the other two are not. The rename is scoped by
  ``governed_tasks_scope``, which is what removed the old ``"None"`` cast —
  every write path used to hand ``str(row.project_id)`` to a uuid cast, and
  for an org-wide row that is the literal string ``"None"``.

Hermetic. The tenant fence has two halves and only one is here: this file proves
the SQL carries the anchor and that the mirror honours it; that Postgres honours
it is the live check.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from gateway.routes.projects import admin as pm_admin
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import custom_fields as pm_fields
from gateway.routes.projects import export as pm_export
from gateway.routes.projects import tags as pm_tags
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree

from tests.unit._projects_fakes import (
    DEFAULT_ORGANIZATION,
    FakeProjectsDB,
    bind_db,
    projects_user,
    silence_events,
)

MODULES = (pm_core, pm_tree, pm_tasks, pm_admin, pm_fields, pm_tags, pm_export)

ORG_A = DEFAULT_ORGANIZATION
ORG_B = "00000000-0000-4000-8000-0000000000bb"

#: Holds ``*`` — including ``admin:settings:manage``, the permission an org-wide
#: write needs.
OWNER = projects_user("owner@fracktal.in")
#: Holds the feature and nothing else. The principal that proves the permission
#: gate is a gate rather than decoration.
MEMBER = projects_user("member@fracktal.in", features="feature:projects")


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROJECTS_ORG_VOCABULARIES", "1")


def _row(project_id: str | None, **columns: object) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(project_id=project_id, **columns)


# ── shadowed() — the tie-break, in isolation ────────────────────────────────

def test_root_local_wins_whichever_order_the_rows_arrive():
    """The winner cannot depend on the ORDER BY. A `DISTINCT ON` would make the
    tie-break a property of a sort clause somebody may later "tidy"; asserting
    both orders is what makes the rule the rule."""
    org = _row(None, name="bug", color="#111111")
    local = _row("p1", name="bug", color="#222222")
    for rows in ([org, local], [local, org]):
        won = pm_core.shadowed(rows, lambda r: r.name)
        assert [r.color for r in won] == ["#222222"], rows


def test_the_union_yields_each_identity_exactly_once():
    """⚠️ The tag correctness rule. `pm_tasks.tags` stores display TEXT, so an
    org-wide "bug" and a root-local "bug" are the same tag on every task. Two
    rows back means two colours for one chip, and whichever the renderer reached
    first is the answer — a bug that looks like a flicker."""
    rows = pm_core.shadowed(
        [_row(None, name="bug"), _row("p1", name="bug"), _row(None, name="ops")],
        lambda r: r.name,
    )
    assert [r.name for r in rows] == ["bug", "ops"]


def test_an_org_wide_row_with_no_local_twin_survives():
    """The whole point of the union: a project that has never defined "bug"
    still gets the organization's."""
    rows = pm_core.shadowed([_row(None, name="bug")], lambda r: r.name)
    assert len(rows) == 1 and rows[0].project_id is None


def test_incoming_order_is_preserved():
    """The list is alphabetical by name and a shadowed pair shares a name, so
    the position is the same either way — stated so a later reorder cannot
    quietly become a reshuffle."""
    rows = pm_core.shadowed(
        [_row(None, name="a"), _row("p1", name="b"), _row("p1", name="a")],
        lambda r: r.name,
    )
    assert [r.name for r in rows] == ["a", "b"]


def test_two_local_rows_cannot_both_survive():
    """Migration 175's `uq_*_project_*` half makes this unreachable from the
    database, but the mirror must not be the thing that hides a regression in
    it: the first wins, not both."""
    rows = pm_core.shadowed(
        [_row("p1", name="bug", color="#1"), _row("p1", name="bug", color="#2")],
        lambda r: r.name,
    )
    assert [r.color for r in rows] == ["#1"]


# ── vocabulary_scope() — both arms, and the tenant on the org one ───────────

@pytest.mark.parametrize("alias", ["", "g"])
def test_the_clause_offers_both_scopes(alias: str):
    clause = pm_core.vocabulary_scope(alias)
    prefix = f"{alias}." if alias else ""
    assert f"{prefix}project_id = CAST(:root AS uuid)" in clause
    assert f"{prefix}project_id IS NULL" in clause


@pytest.mark.parametrize("alias", ["", "g"])
def test_the_org_arm_carries_the_tenant(alias: str):
    """⚠️ The fence. `project_id = :root` is anchored by a project the caller was
    already shown; `project_id IS NULL` is anchored by NOTHING on its own.
    Delete this and one forgotten FORCE ROW LEVEL SECURITY is the difference
    between a tenant's private vocabulary and every tenant's — a failure no test
    of the endpoint's own behaviour would show."""
    clause = pm_core.vocabulary_scope(alias)
    prefix = f"{alias}." if alias else ""
    assert f"{prefix}organization_id = (" in clause
    assert "SELECT p.organization_id FROM pm_projects p" in clause


def test_the_clause_binds_only_the_root():
    """A pure function of `:root`, so no caller can compose it correctly-but-
    without the tenant. That is why the anchor is read from the project row
    rather than taken as a second parameter."""
    import re

    assert set(re.findall(r":(\w+)", pm_core.vocabulary_scope())) == {"root"}


def test_each_identity_lowers_the_same_number_of_sides():
    """⚠️ Lowering the column but not the parameter matches nothing and reports
    "no such tag" — a silent wrong answer rather than an error, which is the
    shape that survives review. The pair is one entry so they cannot drift."""
    for table, (column, bind) in pm_core.VOCABULARY_IDENTITY.items():
        assert ("lower(" in column) == ("lower(" in bind), table


def test_the_identities_are_the_ones_the_tables_already_had():
    """Not a new normalisation invented here (§9.11), and each is one half of
    migration 175's index pair."""
    assert pm_core.VOCABULARY_IDENTITY["pm_tags"][0] == "lower(name)"
    assert pm_core.VOCABULARY_IDENTITY["pm_task_types"][0] == "name"
    assert pm_core.VOCABULARY_IDENTITY["pm_custom_fields"][0] == "field_key"


# ── The flag, and who may flip past it ──────────────────────────────────────

def test_the_flag_is_off_when_unset(monkeypatch: pytest.MonkeyPatch):
    """Ship dark. An absent variable is the state on every box that has not been
    told otherwise, so this is the default that actually ships."""
    monkeypatch.delenv("PROJECTS_ORG_VOCABULARIES", raising=False)
    assert pm_core.org_vocabularies_enabled() is False


@pytest.mark.parametrize("raw", ["1", "on", "true", "TRUE", " yes "])
def test_the_flag_accepts_the_usual_spellings(
    monkeypatch: pytest.MonkeyPatch, raw: str,
):
    monkeypatch.setenv("PROJECTS_ORG_VOCABULARIES", raw)
    assert pm_core.org_vocabularies_enabled() is True


@pytest.mark.parametrize("raw", ["0", "off", "false", "", "no", "maybe"])
def test_anything_else_is_off(monkeypatch: pytest.MonkeyPatch, raw: str):
    """Fail closed: a value nobody recognises means OFF, not "probably on"."""
    monkeypatch.setenv("PROJECTS_ORG_VOCABULARIES", raw)
    assert pm_core.org_vocabularies_enabled() is False


def test_the_flag_is_read_at_call_time(monkeypatch: pytest.MonkeyPatch):
    """Read at import time, the flip would need a redeploy rather than a
    restart — and no test could set it around one call."""
    monkeypatch.delenv("PROJECTS_ORG_VOCABULARIES", raising=False)
    assert pm_core.org_vocabularies_enabled() is False
    monkeypatch.setenv("PROJECTS_ORG_VOCABULARIES", "1")
    assert pm_core.org_vocabularies_enabled() is True


def test_a_dark_flag_refuses_even_the_owner(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PROJECTS_ORG_VOCABULARIES", raising=False)
    with pytest.raises(HTTPException) as exc:
        pm_core.require_org_vocabulary_write(OWNER)
    assert exc.value.status_code == 403


def test_the_flag_alone_is_not_enough(flag_on: None):
    """⚠️ An org-wide row lands in every project in the organization, including
    ones the writer cannot see — it crosses the visibility boundary the rest of
    this package is built to respect. Holding a grant on the project the request
    came through is not the same authority."""
    with pytest.raises(HTTPException) as exc:
        pm_core.require_org_vocabulary_write(MEMBER)
    assert exc.value.status_code == 403
    assert "organization settings" in str(exc.value.detail)


def test_both_gates_open_for_an_owner(flag_on: None):
    pm_core.require_org_vocabulary_write(OWNER)      # no raise


def test_the_permission_is_owner_admin_only():
    """`admin:settings:manage`, not `data:org:read` — a manager holds the latter
    (migration 130) and reading the whole portfolio is not authority to rewrite
    its vocabulary."""
    assert pm_core.ORG_VOCABULARY_WRITE == "admin:settings:manage"
    assert not MEMBER.has_permission(pm_core.ORG_VOCABULARY_WRITE)


def test_a_caller_with_no_tenant_cannot_mint_one():
    """`Visibility.organization_id` is None for somebody the directory does not
    know. Reads fail closed by construction (`column = NULL` is never true); a
    write would reach `organization_id NOT NULL` and surface as a 500 on a
    request that was simply not answerable."""
    unknown = pm_core.Visibility(
        unrestricted=False, email="ghost@nowhere", groups=(),
        organization_id=None,
    )
    with pytest.raises(HTTPException) as exc:
        pm_core.require_known_tenant(unknown, "tag")
    assert exc.value.status_code == 403


# ── refuse_org_wide_write() — the "None" cast guard ─────────────────────────

def test_an_org_wide_row_refuses_a_per_project_mutation():
    with pytest.raises(HTTPException) as exc:
        pm_core.refuse_org_wide_write(_row(None, name="bug"), "tag")
    assert exc.value.status_code == 409
    assert "organization-wide" in str(exc.value.detail)


def test_a_root_local_row_passes_straight_through():
    pm_core.refuse_org_wide_write(_row("p1", name="bug"), "tag")   # no raise


# ── The three lists, against the fake ───────────────────────────────────────

@pytest.mark.asyncio
async def test_tags_list_the_union(db: FakeProjectsDB):
    project = db.seed_project()
    db.seed("pm_tags", project_id=project.id, name="local")
    db.seed("pm_tags", project_id=None, name="shared", organization_id=ORG_A)

    out = await pm_tags.list_tags(project.id, user=OWNER)
    assert sorted(r["name"] for r in out["rows"]) == ["local", "shared"]


@pytest.mark.asyncio
async def test_an_org_wide_tag_reports_its_project_id_as_null(db: FakeProjectsDB):
    """Never the string "None". A client reads this to know whether the row is
    editable here, which is the difference between an enabled pencil and a 409."""
    project = db.seed_project()
    db.seed("pm_tags", project_id=None, name="shared", organization_id=ORG_A)

    (row,) = (await pm_tags.list_tags(project.id, user=OWNER))["rows"]
    assert row["project_id"] is None


@pytest.mark.asyncio
async def test_a_root_local_tag_shadows_the_org_wide_one(db: FakeProjectsDB):
    """⚠️ ONE row, and it is the local colour. Both rows describe the same text
    on every task, so a list returning both makes "what colour is this tag"
    depend on which row the renderer reached first."""
    project = db.seed_project()
    db.seed("pm_tags", project_id=None, name="Bug", color="#org",
            organization_id=ORG_A)
    db.seed("pm_tags", project_id=project.id, name="bug", color="#local")

    rows = (await pm_tags.list_tags(project.id, user=OWNER))["rows"]
    assert len(rows) == 1
    assert rows[0]["color"] == "#local"
    assert rows[0]["project_id"] == str(project.id)


@pytest.mark.asyncio
async def test_an_org_wide_tags_usage_count_is_the_projects_own(db: FakeProjectsDB):
    """⚠️ The count correlates on `:root`, not on `g.project_id`. An org-wide row
    has no project_id, so correlating on it reports every org-wide tag as used by
    0 tasks — and a wrong number here is worse than a missing one, because it is
    the number people decide merges on."""
    project = db.seed_project()
    status = db.seed_status(project.id)
    db.seed("pm_tags", project_id=None, name="shared", organization_id=ORG_A)
    db.seed_task(project.id, status.id, title="one", tags=["shared"])
    db.seed_task(project.id, status.id, title="two", tags=["shared"])

    (row,) = (await pm_tags.list_tags(project.id, user=OWNER))["rows"]
    assert row["task_count"] == 2


@pytest.mark.asyncio
async def test_types_list_the_union_and_shadow(db: FakeProjectsDB):
    project = db.seed_project()
    db.seed("pm_task_types", project_id=None, name="Bug", icon="org",
            organization_id=ORG_A)
    db.seed("pm_task_types", project_id=project.id, name="Bug", icon="local")
    db.seed("pm_task_types", project_id=None, name="Spike", icon="org",
            organization_id=ORG_A)

    rows = (await pm_admin.list_types(project.id, user=OWNER))["rows"]
    assert {r["name"]: r["icon"] for r in rows} == {"Bug": "local", "Spike": "org"}


@pytest.mark.asyncio
async def test_fields_list_the_union_and_shadow_on_field_key(db: FakeProjectsDB):
    """`field_key`, not `name`: the key is the identity every stored value is
    filed under, and two fields may legitimately both read "Priority"."""
    project = db.seed_project()
    db.seed("pm_custom_fields", project_id=None, field_key="priority",
            name="Org priority", field_type="text", organization_id=ORG_A)
    db.seed("pm_custom_fields", project_id=project.id, field_key="priority",
            name="Our priority", field_type="text")

    rows = (await pm_fields.list_fields(project.id, user=OWNER))["rows"]
    assert [r["name"] for r in rows] == ["Our priority"]


@pytest.mark.asyncio
async def test_an_org_wide_field_is_a_key_tasks_may_actually_store(
    db: FakeProjectsDB,
):
    """The union is on `load_definitions`, the seam ALL FOUR readers share —
    including `apply_values`. An org-wide field that listed but whose values were
    then refused as an unknown key would be the worst of both."""
    project = db.seed_project()
    db.seed("pm_custom_fields", project_id=None, field_key="owner_team",
            name="Owner team", field_type="text", organization_id=ORG_A)

    definitions = await pm_fields.load_definitions(db, str(project.id))
    merged, changes = pm_fields.apply_values({}, {"owner_team": "Ops"}, definitions)
    assert merged == {"owner_team": "Ops"}
    assert changes


# ── The tenant fence, in the mirror ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_another_tenants_org_wide_row_is_not_visible(db: FakeProjectsDB):
    """§9.11's own done-when. `project_id IS NULL` is anchored by the ROOT
    PROJECT's organization, so a row belonging to organization B never joins
    organization A's union — with or without RLS underneath."""
    project = db.seed_project()                          # ORG_A
    db.seed("pm_tags", project_id=None, name="theirs", organization_id=ORG_B)
    db.seed("pm_tags", project_id=None, name="ours", organization_id=ORG_A)

    rows = (await pm_tags.list_tags(project.id, user=OWNER))["rows"]
    assert [r["name"] for r in rows] == ["ours"]


# ── Auto-registration meets the shared registry ─────────────────────────────

@pytest.mark.asyncio
async def test_first_use_of_an_org_wide_tag_does_not_duplicate_it(
    db: FakeProjectsDB,
):
    """⚠️ Without the union on the WRITE path, the first task in every project to
    type "bug" would auto-register a root-local duplicate and shadow the
    organization's own tag — the registry filling with exactly the
    near-duplicates it exists to prevent, one project at a time."""
    project = db.seed_project()
    db.seed("pm_tags", project_id=None, name="bug", organization_id=ORG_A)

    stored = await pm_tags.apply_task_tags(
        db, str(project.id), ["Bug"], by="owner@fracktal.in",
    )
    assert stored == ["bug"]                     # the registry's own spelling
    assert len(db.rows("pm_tags")) == 1          # nothing registered


@pytest.mark.asyncio
async def test_the_cap_counts_the_effective_set(db: FakeProjectsDB):
    """A picker showing 500 tags is unusable whether the organization or the
    project contributed them; counting only the local half would let the real
    number reach 1000."""
    project = db.seed_project()
    for index in range(pm_tags.MAX_TAGS_PER_PROJECT):
        db.seed("pm_tags", project_id=None, name=f"t{index}",
                organization_id=ORG_A)

    with pytest.raises(HTTPException) as exc:
        await pm_tags.register(
            db, str(project.id), ["one more"], by="owner@fracktal.in",
        )
    assert exc.value.status_code == 409


# ── Creating one: the flag, then the permission, then the row ──────────────

@pytest.mark.asyncio
async def test_creating_an_org_wide_tag_is_refused_while_dark(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("PROJECTS_ORG_VOCABULARIES", raising=False)
    project = db.seed_project()
    with pytest.raises(HTTPException) as exc:
        await pm_tags.create_tag(
            project.id, pm_tags.TagIn(name="shared", scope="org"), user=OWNER,
        )
    assert exc.value.status_code == 403
    assert db.rows("pm_tags") == []


@pytest.mark.asyncio
async def test_the_read_union_works_while_the_flag_is_dark(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
):
    """⚠️ The flag gates the CREATE, never the read (§9.11). A dark read arm
    would mean flipping the flag changes what existing rows mean, which is the
    opposite of a safe flip."""
    monkeypatch.delenv("PROJECTS_ORG_VOCABULARIES", raising=False)
    project = db.seed_project()
    db.seed("pm_tags", project_id=None, name="shared", organization_id=ORG_A)

    rows = (await pm_tags.list_tags(project.id, user=OWNER))["rows"]
    assert [r["name"] for r in rows] == ["shared"]


@pytest.mark.asyncio
async def test_an_owner_may_create_one_when_the_flag_is_on(
    db: FakeProjectsDB, flag_on: None,
):
    project = db.seed_project()
    out = await pm_tags.create_tag(
        project.id, pm_tags.TagIn(name="shared", scope="org"), user=OWNER,
    )
    assert out["project_id"] is None
    (row,) = db.rows("pm_tags")
    assert row["project_id"] is None
    # ⚠️ Migration 161's trigger fills the tenant FROM THE PARENT PROJECT, so a
    # row with no project must carry it explicitly or hit `NOT NULL`.
    assert str(row["organization_id"]) == ORG_A


@pytest.mark.asyncio
async def test_a_default_scope_still_lands_on_the_project(
    db: FakeProjectsDB, flag_on: None,
):
    """R6 — expand only. An unchanged caller keeps today's behaviour verbatim."""
    project = db.seed_project()
    out = await pm_tags.create_tag(
        project.id, pm_tags.TagIn(name="mine"), user=OWNER,
    )
    assert out["project_id"] == str(project.id)


@pytest.mark.asyncio
async def test_a_project_may_still_register_its_own_shadowing_name(
    db: FakeProjectsDB, flag_on: None,
):
    """⚠️ Only a SAME-SCOPE clash is refused. A project keeping its own "bug"
    after the organization gains one is the shadowing D-PM-16 permits — refusing
    it would contradict the ruling this ticket implements."""
    project = db.seed_project()
    db.seed("pm_tags", project_id=None, name="bug", organization_id=ORG_A)

    out = await pm_tags.create_tag(
        project.id, pm_tags.TagIn(name="bug", color="#local"), user=OWNER,
    )
    assert out["project_id"] == str(project.id)


@pytest.mark.asyncio
async def test_two_org_wide_tags_of_one_name_are_refused(
    db: FakeProjectsDB, flag_on: None,
):
    """⚠️ Asked of the TABLE, not of the effective list: a shadowed org-wide row
    is absent from that list by design, so a check against it would miss the row
    the INSERT is about to collide with — and migration 175's `uq_*_org_*` index
    would answer that with a 500 instead of a 409."""
    project = db.seed_project()
    db.seed("pm_tags", project_id=None, name="Shared", organization_id=ORG_A)
    db.seed("pm_tags", project_id=project.id, name="shared")   # shadows it

    with pytest.raises(HTTPException) as exc:
        await pm_tags.create_tag(
            project.id, pm_tags.TagIn(name="shared", scope="org"), user=OWNER,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_the_organization_may_adopt_a_name_a_project_already_uses(
    db: FakeProjectsDB, flag_on: None,
):
    """⚠️ The case that tells `org_wide_exists` apart from a check against the
    effective list — and the reason the distinction is not academic. Asking the
    registry "is 'shared' taken?" answers YES because the PROJECT has one, and
    an organization would then be unable to adopt any name any of its projects
    had ever used. Migration 175's index pair permits this row (its R8 case 3);
    only a check at the right scope does too.
    """
    project = db.seed_project()
    db.seed("pm_tags", project_id=project.id, name="shared", color="#local")

    out = await pm_tags.create_tag(
        project.id, pm_tags.TagIn(name="shared", scope="org"), user=OWNER,
    )
    assert out["project_id"] is None
    assert len(db.rows("pm_tags")) == 2
    # …and the project keeps seeing its own.
    (visible,) = (await pm_tags.list_tags(project.id, user=OWNER))["rows"]
    assert visible["color"] == "#local"


@pytest.mark.asyncio
async def test_a_field_key_a_project_already_uses_may_still_go_org_wide(
    db: FakeProjectsDB, flag_on: None,
):
    """The same distinction on the custom-field create, which shares the helper
    but has its own duplicate check to get wrong."""
    project = db.seed_project()
    db.seed("pm_custom_fields", project_id=project.id, field_key="priority",
            name="Ours", field_type="text")

    out = await pm_fields.create_field(
        project.id,
        pm_fields.FieldIn(name="Priority", field_key="priority",
                          field_type="text", scope="org"),
        user=OWNER,
    )
    assert out["project_id"] is None
    (visible,) = (await pm_fields.list_fields(project.id, user=OWNER))["rows"]
    assert visible["name"] == "Ours"


@pytest.mark.asyncio
async def test_a_project_may_still_add_a_field_the_organization_defines(
    db: FakeProjectsDB, flag_on: None,
):
    """The field half of the shadowing create. Refusing it would say "the
    organization has a 'priority', so you may never have your own" — the
    opposite of what D-PM-16 ruled."""
    project = db.seed_project()
    db.seed("pm_custom_fields", project_id=None, field_key="priority",
            name="Org priority", field_type="text", organization_id=ORG_A)

    out = await pm_fields.create_field(
        project.id,
        pm_fields.FieldIn(name="Ours", field_key="priority", field_type="text"),
        user=OWNER,
    )
    assert out["project_id"] == str(project.id)


@pytest.mark.asyncio
async def test_a_second_local_field_of_one_key_is_still_refused(
    db: FakeProjectsDB, flag_on: None,
):
    """The check must narrow to the same scope, not disappear: two root-local
    definitions under one key is the collision the 409 exists for."""
    project = db.seed_project()
    db.seed("pm_custom_fields", project_id=project.id, field_key="priority",
            name="Ours", field_type="text")

    with pytest.raises(HTTPException) as exc:
        await pm_fields.create_field(
            project.id,
            pm_fields.FieldIn(name="Again", field_key="priority",
                              field_type="text"),
            user=OWNER,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_a_member_cannot_create_one(db: FakeProjectsDB, flag_on: None):
    project = db.seed_project()
    with pytest.raises(HTTPException) as exc:
        await pm_tags.create_tag(
            project.id, pm_tags.TagIn(name="shared", scope="org"), user=MEMBER,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_an_org_wide_custom_field_carries_the_tenant(
    db: FakeProjectsDB, flag_on: None,
):
    project = db.seed_project()
    out = await pm_fields.create_field(
        project.id,
        pm_fields.FieldIn(name="Owner team", field_type="text", scope="org"),
        user=OWNER,
    )
    assert out["project_id"] is None
    (row,) = db.rows("pm_custom_fields")
    assert str(row["organization_id"]) == ORG_A


@pytest.mark.asyncio
async def test_an_org_wide_task_type_carries_the_tenant(
    db: FakeProjectsDB, flag_on: None,
):
    project = db.seed_project()
    out = await pm_admin.create_type(
        project.id, pm_admin.TypeIn(name="Spike", scope="org"), user=OWNER,
    )
    assert out["project_id"] is None
    (row,) = db.rows("pm_task_types")
    assert str(row["organization_id"]) == ORG_A


@pytest.mark.asyncio
async def test_an_org_wide_type_cannot_be_the_default(
    db: FakeProjectsDB, flag_on: None,
):
    """"Exactly one default per project" is a per-project invariant. An org-wide
    default would be a second answer to the same question for every project at
    once, and which type a project starts tasks in is that project's call."""
    project = db.seed_project()
    with pytest.raises(HTTPException) as exc:
        await pm_admin.create_type(
            project.id,
            pm_admin.TypeIn(name="Spike", scope="org", is_default=True),
            user=OWNER,
        )
    assert exc.value.status_code == 422


# ── An org-wide row may be RENAMED, and nothing else ───────────────────────
#
# Owner decision, 2026-09-20. It reads: allow the rename, show the org-wide
# affected-task count before confirming, keep it behind
# ``admin:settings:manage``.
#
# ⚠️ **Merge and delete stay refused**, and that is the whole of the
# conservatism. A rename is reversible by renaming back. A delete removes a row
# every project in the organization is using and strips it off their tasks, and
# a merge destroys one of two rows. Those need the admin surface (H-4) and a
# decision of their own.

@pytest.mark.asyncio
async def test_an_owner_may_rename_an_org_wide_tag(db: FakeProjectsDB):
    """🔴 And the rewrite reaches the WHOLE organization.

    `pm_tasks.tags` stores display text, so the rename is a rewrite of every
    task that wears it — in every project, not in the one the request came
    through. A scope of one root would rename the registry row and leave most
    of the tasks still carrying the old word.
    """
    here = db.seed_project()
    there = db.seed_project()
    tag = db.seed("pm_tags", project_id=None, name="shared", organization_id=ORG_A)
    db.seed("pm_tasks", root_project_id=here.id, tags=["shared"],
            organization_id=ORG_A)
    db.seed("pm_tasks", root_project_id=there.id, tags=["shared", "other"],
            organization_id=ORG_A)

    out = await pm_tags.patch_tag(
        tag.id, pm_tags.TagIn(name="renamed"), user=OWNER,
    )
    assert out["name"] == "renamed"
    assert out["retagged"] == 2, "both projects' tasks, not just one"
    assert sorted(
        sorted(r["tags"]) for r in db.rows("pm_tasks")
    ) == [["other", "renamed"], ["renamed"]]


@pytest.mark.asyncio
async def test_a_member_cannot_rename_an_org_wide_tag(db: FakeProjectsDB):
    """The permission IS the authorization here — there is no project to check."""
    db.seed_project()
    tag = db.seed("pm_tags", project_id=None, name="shared", organization_id=ORG_A)
    with pytest.raises(HTTPException) as exc:
        await pm_tags.patch_tag(tag.id, pm_tags.TagIn(name="x"), user=MEMBER)
    assert exc.value.status_code == 403
    assert db.rows("pm_tags")[0]["name"] == "shared"


@pytest.mark.asyncio
async def test_the_create_flag_does_NOT_gate_the_rename(db: FakeProjectsDB):
    """Turning creates off must not strand the rows already minted.

    `org_vocabularies_enabled` guards the act that is hard to walk back. A row
    that exists and cannot be corrected is a worse state than either setting of
    that flag, so the edit is gated on the permission alone. The `db` fixture
    leaves the flag at its default, which is OFF.
    """
    db.seed_project()
    tag = db.seed("pm_tags", project_id=None, name="shared", organization_id=ORG_A)
    assert pm_core.org_vocabularies_enabled() is False
    out = await pm_tags.patch_tag(tag.id, pm_tags.TagIn(name="fine"), user=OWNER)
    assert out["name"] == "fine"


@pytest.mark.asyncio
async def test_a_rename_onto_a_name_ANY_project_uses_is_refused(
    db: FakeProjectsDB,
):
    """🔴 The silent merge this exists to prevent.

    `pm_tasks.tags` stores display TEXT. Renaming the organization's "shared"
    to "mine" inside a tree that already has its own "mine" would leave both on
    one task and `merged_tags` would fold them — a destructive act nobody asked
    for, reported as a rename. The per-project rule already refuses a rename
    onto an existing name. This is that rule at the scope the row has.
    """
    project = db.seed_project()
    tag = db.seed("pm_tags", project_id=None, name="shared", organization_id=ORG_A)
    db.seed("pm_tags", project_id=project.id, name="mine", organization_id=ORG_A)

    with pytest.raises(HTTPException) as exc:
        await pm_tags.patch_tag(tag.id, pm_tags.TagIn(name="mine"), user=OWNER)
    assert exc.value.status_code == 409
    assert "merge" in str(exc.value.detail).lower()


# ⚠️ **The impact READ is not here, and that is deliberate.** It is a
# `count(*) … count(DISTINCT root_project_id)` aggregate, and an aggregate is
# the exact shape a hermetic fake answers with whatever it was handed (R8).
# Its behaviour is proved in `tests/live/live_ws27bj_rename.py`.

@pytest.mark.asyncio
async def test_deleting_an_org_wide_tag_is_STILL_refused(db: FakeProjectsDB):
    db.seed_project()
    tag = db.seed("pm_tags", project_id=None, name="shared",
                  organization_id=ORG_A)
    with pytest.raises(HTTPException) as exc:
        await pm_tags.delete_tag(tag.id, user=OWNER)
    assert exc.value.status_code == 409
    assert len(db.rows("pm_tags")) == 1


@pytest.mark.asyncio
async def test_merging_at_EITHER_end_is_STILL_refused(db: FakeProjectsDB):
    """Merging INTO an org-wide tag would rewrite one project's tasks while
    claiming an organization-wide result; merging one AWAY would delete a row
    every other project is still using."""
    project = db.seed_project()
    org_tag = db.seed("pm_tags", project_id=None, name="shared",
                      organization_id=ORG_A)
    local = db.seed("pm_tags", project_id=project.id, name="mine")

    for source, target in ((local.id, org_tag.id), (org_tag.id, local.id)):
        with pytest.raises(HTTPException) as exc:
            await pm_tags.merge_tag(
                source, pm_tags.MergeIn(into_tag_id=target), user=OWNER,
            )
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_a_field_and_a_type_rename_org_wide_too(db: FakeProjectsDB):
    """Neither touches a task, and that is why neither needs a preview.

    `field_key` is never editable and `pm_tasks.type_id` is a foreign key, so
    these two renames move a LABEL and nothing else.
    """
    db.seed_project()
    field = db.seed("pm_custom_fields", project_id=None, field_key="k",
                    name="K", field_type="text", organization_id=ORG_A)
    type_row = db.seed("pm_task_types", project_id=None, name="Spike",
                       organization_id=ORG_A)

    assert (await pm_fields.patch_field(
        field.id, pm_fields.FieldIn(name="Kilo"), user=OWNER,
    ))["name"] == "Kilo"
    assert (await pm_admin.patch_type(
        type_row.id, pm_admin.TypeIn(name="Spike II"), user=OWNER,
    ))["name"] == "Spike II"


@pytest.mark.asyncio
async def test_the_per_PROJECT_attributes_are_refused_by_name(
    db: FakeProjectsDB,
):
    """⚠️ Each of these has no answer yet at organization scope.

    `is_default` — the default type of WHICH project? `_clear_other_defaults`
    is scoped to one root. `field_type` and `options` are both guarded by "is
    this in use", which is a different question across an organization.
    """
    db.seed_project()
    field = db.seed("pm_custom_fields", project_id=None, field_key="k",
                    name="K", field_type="text", organization_id=ORG_A)
    type_row = db.seed("pm_task_types", project_id=None, name="Spike",
                       organization_id=ORG_A)

    for call in (
        pm_fields.patch_field(
            field.id, pm_fields.FieldIn(name="K", field_type="number"), user=OWNER,
        ),
        pm_admin.patch_type(
            type_row.id, pm_admin.TypeIn(name="Spike", is_default=True), user=OWNER,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 409
        assert "per project" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_deleting_a_field_or_a_type_org_wide_is_STILL_refused(
    db: FakeProjectsDB,
):
    db.seed_project()
    field = db.seed("pm_custom_fields", project_id=None, field_key="k",
                    name="K", field_type="text", organization_id=ORG_A)
    type_row = db.seed("pm_task_types", project_id=None, name="Spike",
                       organization_id=ORG_A)

    for call in (
        pm_fields.delete_field(field.id, user=OWNER),
        pm_admin.delete_type(type_row.id, user=OWNER),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_scope_is_never_written_as_a_column(db: FakeProjectsDB):
    """`scope` selects where a NEW row lands; it is not a column. Left in the
    patch payload it reaches `UPDATE pm_tags SET scope = …` and 500s."""
    project = db.seed_project()
    tag = db.seed("pm_tags", project_id=project.id, name="mine")
    await pm_tags.patch_tag(
        tag.id, pm_tags.TagIn(color="#abc", scope="org"), user=OWNER,
    )
    assert not db.statements_touching("SET scope")
    assert db.rows("pm_tags")[0]["project_id"] == str(project.id)
