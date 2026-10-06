"""A new task's custom values pass the gate an edit's values pass.

Spec: ``project-docs/specs/project_management_app.md`` §11.2 item 4 (custom
fields, WS-27l). Found by the WS-46 P6 build (PR #677): ``POST
/projects/tasks`` stored ``custom_fields`` from the body as it came, and only
``PATCH /projects/tasks/{id}`` put a value through
``custom_fields.apply_values``. The UI, the chat, an import or a script could
create a task that held a number field's ``"three"``, an option the field never
listed, or a key that belongs to another project.

The fix is ONE seam. ``create_task`` calls ``apply_values`` against the
definitions of the root the task is created in, in the same transaction as the
insert, and a bad value gets the PATCH's 422.

Two halves, as in ``test_projects_candidates.py``.

* **Hermetic**: the route over ``FakeProjectsDB`` with the definitions handed
  in, so the call order and the 422 shape never skip.
* **R8, on a real Postgres through asyncpg**: the route's own SQL
  (``load_definitions`` over ``vocabulary_scope``, the insert, the JSONB
  cast) against the tenant ladder. Each refusal leaves no row, and a good
  create stores the shaped value. The count query for rows written before the
  fix runs here too, against rows that bypass the route.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is
not a pass.

⚠️ **A required field is NOT checked at create.** Migration 192 records why:
a required field gates a MOVE into its project, and each board quick add and
the P6 chat create send a title only. ``test_a_create_without_a_required_value
_is_still_accepted`` pins that boundary, so a change to it is a decision and
not an accident.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import tasks as pm_tasks
from sqlalchemy import text

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    projects_user,
    silence_events,
)

USER = projects_user()
MODULES = (pm_core, pm_tasks)

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_needs_db = pytest.mark.skipif(
    not _TENANT_URL, reason="R8 needs TENANT_LADDER_DATABASE_URL",
)


def _definition(key: str, field_type: str, *, options: list[str] | None = None,
                required: bool = False) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()), "project_id": None, "field_key": key,
        "name": key.title(), "description": None, "field_type": field_type,
        "options": list(options or []), "position": 0, "required": required,
        "created_by": "owner@fracktal.in",
    }


#: One project's fields, as ``load_definitions`` answers them.
DEFINITIONS = [
    _definition("hours", "number"),
    _definition("tier", "select", options=["gold", "silver"]),
    _definition("due_review", "date"),
    _definition("po_number", "text", required=True),
]


# ── Hermetic ────────────────────────────────────────────────────────────────

@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


@pytest.fixture
def roots_asked(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """``load_definitions`` answers :data:`DEFINITIONS`, and records the root."""
    asked: list[str] = []

    async def _load(_db: Any, root: str) -> list[dict[str, Any]]:
        asked.append(str(root))
        return [dict(d) for d in DEFINITIONS]

    monkeypatch.setattr(pm_tasks, "load_definitions", _load)
    return asked


def _project(db: FakeProjectsDB) -> Any:
    project = db.seed_project(name="Ops")
    db.seed_status(project.id, name="To do", category="todo", is_default=True)
    return project


async def _create(project_id: str, **body: Any) -> dict[str, Any]:
    return await pm_tasks.create_task(
        pm_tasks.TaskIn(project_id=project_id, title="Weld the frame", **body),
        user=USER,
    )


@pytest.mark.parametrize(
    ("custom", "names"),
    [
        ({"hours": "three"}, "'hours'"),
        ({"tier": "bronze"}, "'tier'"),
        ({"other_project_field": "x"}, "'other_project_field'"),
        ({"due_review": "next week"}, "'due_review'"),
    ],
    ids=["wrong-type", "unknown-option", "unknown-key", "bad-date"],
)
async def test_a_bad_value_on_create_is_refused_and_writes_no_task(
    db: FakeProjectsDB, roots_asked: list[str], custom: Any, names: str,
) -> None:
    project = _project(db)
    with pytest.raises(HTTPException) as refused:
        await _create(str(project.id), custom_fields=custom)

    assert refused.value.status_code == 422
    assert names in str(refused.value.detail)
    assert db.rows("pm_tasks") == []


async def test_the_refusal_is_the_one_a_patch_gives(
    db: FakeProjectsDB, roots_asked: list[str],
) -> None:
    """Same function, so the same words: a client handles one shape."""
    from gateway.routes.projects.custom_fields import apply_values

    project = _project(db)
    with pytest.raises(HTTPException) as on_create:
        await _create(str(project.id), custom_fields={"tier": "bronze"})
    with pytest.raises(HTTPException) as on_patch:
        apply_values({}, {"tier": "bronze"}, DEFINITIONS)

    assert on_create.value.detail == on_patch.value.detail


async def test_a_good_value_is_stored_shaped_against_the_tasks_root(
    db: FakeProjectsDB, roots_asked: list[str],
) -> None:
    root = _project(db)
    child = db.seed_project(name="Line 2", parent=str(root.id))

    created = await _create(
        str(child.id),
        custom_fields={"hours": 3, "tier": "gold", "due_review": "2026-10-06",
                       "po_number": "  PO-17  "},
    )

    # Validated against the ROOT's definitions, as the PATCH validates
    # against `before.root_project_id`, never the subproject's own id.
    assert roots_asked == [str(root.id)]
    # The fake keeps the JSONB bind as the string the cast receives.
    stored = pm_core.from_jsonb(db.rows("pm_tasks")[0]["custom_fields"])
    assert stored == {
        "hours": 3, "tier": "gold",
        "due_review": "2026-10-06T00:00:00+00:00", "po_number": "PO-17",
    }
    assert created["custom_fields"] == stored


async def test_a_stated_null_leaves_the_column_default(
    db: FakeProjectsDB, roots_asked: list[str],
) -> None:
    """A null is "no value", as on the PATCH. It never reaches the INSERT as a
    NULL, which the NOT NULL column would refuse with a 500."""
    project = _project(db)

    await _create(str(project.id), custom_fields=None)
    await _create(str(project.id), custom_fields={"hours": None})

    assert roots_asked == [str(project.id)]
    assert all(
        "custom_fields" not in sql.split("VALUES")[0]
        for sql in db.statements_touching("INSERT INTO pm_tasks")
    )


async def test_a_create_without_values_reads_no_definitions(
    db: FakeProjectsDB, roots_asked: list[str],
) -> None:
    """Each board quick add sends a title only. It costs no extra query."""
    project = _project(db)

    await _create(str(project.id))

    assert roots_asked == []


async def test_a_create_without_a_required_value_is_still_accepted(
    db: FakeProjectsDB, roots_asked: list[str],
) -> None:
    """Migration 192: a required field gates the MOVE, never the create.

    A board quick add and the P6 chat create send no values, so a check here
    would refuse each of them in a project with a required field. Pinned so a
    change is a decision. ``test_promote_required.py`` owns the move.
    """
    project = _project(db)

    created = await _create(str(project.id), custom_fields={"hours": 2})

    assert created["custom_fields"] == {"hours": 2}


# ── R8 — the route's own SQL on asyncpg, over the tenant ladder ─────────────

def _async_url() -> str:
    url = _TENANT_URL
    if "postgresql+psycopg" in url:
        return url.replace("postgresql+psycopg", "postgresql+asyncpg")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


#: The rows written before the fix that a create would now refuse. Read only.
#: The owner runs it on production with the outer ``SELECT problem,
#: count(DISTINCT task_id) ... GROUP BY problem`` (see the PR body). It does
#: not rewrite a row (R6). It mirrors `load_definitions`: a root-local field
#: shadows an org-wide one with the same key.
#:
#: It checks the key, the JSON type and the option. It does NOT check a date's
#: ISO form, a URL's scheme or a text's length, which need the Python coercers.
BAD_VALUES_SQL = """
SELECT t.id AS task_id, t.organization_id, kv.key,
       CASE
         WHEN d.field_type IS NULL THEN 'unknown_key'
         WHEN d.field_type = 'number'  AND jsonb_typeof(kv.value) <> 'number'  THEN 'wrong_type'
         WHEN d.field_type = 'boolean' AND jsonb_typeof(kv.value) <> 'boolean' THEN 'wrong_type'
         WHEN d.field_type IN ('text', 'url', 'date', 'select')
              AND jsonb_typeof(kv.value) <> 'string' THEN 'wrong_type'
         WHEN d.field_type = 'multi_select' AND jsonb_typeof(kv.value) <> 'array' THEN 'wrong_type'
         WHEN d.field_type = 'select' AND NOT d.options @> jsonb_build_array(kv.value)
              THEN 'unknown_option'
         WHEN d.field_type = 'multi_select' AND NOT d.options @> kv.value
              THEN 'unknown_option'
       END AS problem
  FROM pm_tasks t
 CROSS JOIN LATERAL jsonb_each(
         CASE WHEN jsonb_typeof(t.custom_fields) = 'object'
              THEN t.custom_fields ELSE '{}'::jsonb END
       ) AS kv(key, value)
  LEFT JOIN LATERAL (
        SELECT f.field_type, f.options
          FROM pm_custom_fields f
         WHERE f.field_key = kv.key
           AND (f.project_id = t.root_project_id
                OR (f.project_id IS NULL AND f.organization_id = t.organization_id))
         ORDER BY f.project_id NULLS LAST
         LIMIT 1
       ) d ON true
"""


@pytest.fixture(scope="module")
def _ladder():
    """Apply the tenant ladder ONCE for this module (each replay spends
    columns, and per-test replays reached Postgres's 1600)."""
    if not _TENANT_URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    from sqlalchemy import create_engine

    from tests.unit._tenant_ladder import apply_ladder

    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def seeded(_ladder):
    """Two roots in one org. ``home`` has a default lane and four fields.
    ``away`` has one field of its own, ``away_only``, which ``home`` does not
    define. One org-wide field, ``org_flag``, reaches both."""
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    made: dict[str, Any] = {"tag": tag}
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org

        def project(name: str) -> str:
            return str(c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by,"
                    " organization_id, timezone, parent_project_id, owns_statuses)"
                    " VALUES (:n,'active','manual','cf@example.test',"
                    " CAST(:o AS uuid),'UTC',NULL,true) RETURNING id"
                ),
                {"n": f"{name}-{tag}", "o": org},
            ).scalar_one())

        made["home"] = home = project("home")
        made["away"] = away = project("away")
        c.execute(
            text(
                "INSERT INTO pm_task_statuses (project_id, name, color, position,"
                " category, is_default) VALUES"
                " (CAST(:p AS uuid),'To do','gray',0,'todo',true)"
            ),
            {"p": home},
        )

        def field(project_id: str | None, key: str, field_type: str,
                  options: list[str] | None = None, required: bool = False) -> None:
            c.execute(
                text(
                    "INSERT INTO pm_custom_fields (project_id, organization_id,"
                    " field_key, name, field_type, options, required, created_by)"
                    " VALUES (CAST(:p AS uuid), CAST(:o AS uuid), :k, :n, :t,"
                    " CAST(:opts AS jsonb), :r, 'cf@example.test')"
                ),
                {"p": project_id, "o": org, "k": key, "n": f"{key} {tag}",
                 "t": field_type, "opts": _json(options or []), "r": required},
            )

        field(home, "hours", "number")
        field(home, "tier", "select", ["gold", "silver"])
        field(home, "areas", "multi_select", ["cad", "weld"])
        field(home, "po_number", "text", required=True)
        field(away, "away_only", "text")
        made["org_flag"] = org_flag = f"org_flag_{tag}"
        field(None, org_flag, "boolean")
    yield made
    with eng.begin() as c:
        for pid in (made["home"], made["away"]):
            c.execute(text("DELETE FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"),
                      {"p": pid})
            c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                      {"p": pid})
        c.execute(text("DELETE FROM pm_custom_fields WHERE field_key = :k"),
                  {"k": made["org_flag"]})
    eng.dispose()


def _json(value: Any) -> str:
    import json

    return json.dumps(value)


async def _create_on_db(seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
                        custom: Any) -> dict[str, Any]:
    """``POST /projects/tasks`` into ``home``, on one real database, with the
    route's session seam patched to a committing asyncpg connection."""
    from contextlib import asynccontextmanager

    from gateway.routes.projects.core import Visibility
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        # `begin`, so a refusal rolls back and a create commits, which is
        # what `acb_common.db.tenant_session` does around the real route.
        async with eng.begin() as conn:
            yield conn

    async def _visibility(_db: Any, _user: Any) -> Any:
        return Visibility(unrestricted=True, email="", groups=(),
                          organization_id=seeded["org"])

    async def _emit(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(pm_tasks, "_tenant_session", _session)
    monkeypatch.setattr(pm_tasks, "resolve_visibility", _visibility)
    monkeypatch.setattr(pm_tasks, "emit", _emit)
    try:
        return await pm_tasks.create_task(
            pm_tasks.TaskIn(project_id=seeded["home"], title="Weld the frame",
                            custom_fields=custom),
            user=projects_user("cf@example.test"),
        )
    finally:
        await eng.dispose()


def _stored(seeded: dict[str, Any]) -> list[Any]:
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    try:
        with eng.connect() as c:
            return [r.custom_fields for r in c.execute(
                text("SELECT custom_fields FROM pm_tasks"
                     " WHERE root_project_id = CAST(:p AS uuid) ORDER BY created_at"),
                {"p": seeded["home"]},
            )]
    finally:
        eng.dispose()


@_needs_db
@pytest.mark.parametrize(
    ("custom", "names"),
    [
        ({"hours": "three"}, "'hours'"),
        ({"tier": "bronze"}, "'tier'"),
        ({"areas": ["cad", "paint"]}, "'areas'"),
        ({"away_only": "x"}, "'away_only'"),
    ],
    ids=["wrong-type", "unknown-option", "unknown-multi-option", "another-projects-field"],
)
async def test_r8_a_bad_value_is_refused_and_no_row_lands(
    seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch, custom: Any, names: str,
) -> None:
    with pytest.raises(HTTPException) as refused:
        await _create_on_db(seeded, monkeypatch, custom)

    assert refused.value.status_code == 422
    assert names in str(refused.value.detail)
    assert _stored(seeded) == []


@_needs_db
async def test_r8_a_good_create_stores_the_shaped_value(
    seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = await _create_on_db(seeded, monkeypatch, {
        "hours": 2.5, "tier": "gold", "areas": ["weld", "cad", "weld"],
        "po_number": "  PO-17 ", seeded["org_flag"]: False,
    })

    want = {"hours": 2.5, "tier": "gold", "areas": ["weld", "cad"],
            "po_number": "PO-17", seeded["org_flag"]: False}
    assert _stored(seeded) == [want]
    assert created["custom_fields"] == want


@_needs_db
async def test_r8_a_create_with_no_values_and_a_required_field_lands_empty(
    seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The board quick add, and the first half of the P6 chat create."""
    await _create_on_db(seeded, monkeypatch, None)

    assert _stored(seeded) == [{}]


@_needs_db
def test_r8_the_count_query_finds_each_bad_row_and_no_good_one(
    seeded: dict[str, Any],
) -> None:
    """Rows written past the route, the way the create wrote them before."""
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    rows = {
        "good": {"hours": 1, "tier": "silver", "areas": ["cad"],
                 seeded["org_flag"]: True},
        "wrong_type": {"hours": "three"},
        "unknown_option": {"tier": "bronze"},
        "unknown_multi": {"areas": ["cad", "paint"]},
        "unknown_key": {"away_only": "x"},
    }
    ids: dict[str, str] = {}
    try:
        with eng.begin() as c:
            status = str(c.execute(
                text("SELECT id FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"),
                {"p": seeded["home"]},
            ).scalar_one())
            for n, (name, values) in enumerate(rows.items(), start=1):
                ids[name] = str(c.execute(
                    text(
                        "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                        " status_id, created_by, task_number, custom_fields)"
                        " VALUES (:t, CAST(:p AS uuid), CAST(:p AS uuid),"
                        " CAST(:s AS uuid), 'cf@example.test', :n,"
                        " CAST(:cf AS jsonb)) RETURNING id"
                    ),
                    {"t": name, "p": seeded["home"], "s": status,
                     "n": 9000 + n, "cf": _json(values)},
                ).scalar_one())
        with eng.connect() as c:
            found = {
                (str(r.task_id), r.problem)
                for r in c.execute(
                    text(f"SELECT * FROM ({BAD_VALUES_SQL}) q"
                         " WHERE problem IS NOT NULL"
                         " AND task_id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": list(ids.values())},
                )
            }
    finally:
        eng.dispose()

    assert found == {
        (ids["wrong_type"], "wrong_type"),
        (ids["unknown_option"], "unknown_option"),
        (ids["unknown_multi"], "unknown_option"),
        (ids["unknown_key"], "unknown_key"),
    }
