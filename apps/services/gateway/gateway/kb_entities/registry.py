"""The entity registry: the vocabulary, the one writer and the owner rule.

Spec: ``project-docs/specs/knowledge_curation.md`` §6.1 (the page types),
§6.2 (the registry), §6.4 (who sees a directory row) and §16 (two rules).

**A pointer, not a copy.** ``kb_entities`` holds one row for each thing the
brain knows about. For a ``person`` the row holds ``ref_table = 'people'``
and the id, and the People app keeps the name. A Knowledge-owned entity (a
product, a topic, a written workflow) names ``ref_table = 'kb_entities'``
and its own id, and it holds its ``space_id`` and its ``name``.

**The one writer.** :func:`ensure_entity` is the only ``INSERT INTO
kb_entities`` in the gateway (fence: ``tests/unit/test_kc_entities.py``). It
is a get-or-create on the partial unique index ``kb_entities_org_ref_key``,
and it inserts only when the owner row is in the bound tenant. So two calls
for one record give one row, and org B cannot point at a record of org A.

**The owner rule (§6.4).** Most entities have no page, so the page clause
cannot gate them. :func:`owner_rules` gives, for one type, the read of the
owner table under the rule of the app that owns it, with a LEFT JOIN of
``kb_entities``. It calls the app's own function and never copies its SQL:

* ``person``: ``feature:people``, active people only, the name only;
* ``customer``: ``require_crm_enabled`` and ``require_crm_access``;
* ``workstream``: ``feature:projects``, ``resolve_visibility(db, user)``
  ``.project_clause("o.id")`` and ``o.personal_owner IS NULL``;
* ``workflow`` (a row of ``workflows``): ``feature:workflows``, the name only;
* ``company``: any member who passed the Knowledge gate of the router;
* ``product``, ``topic`` and a written ``workflow``: the space passes
  ``routes/knowledge/core.readable_spaces_clause``.

The name comes from the owner row by a live join. ``kb_entities.name`` is a
cache for the Knowledge-owned types only. The page joins under
``visible_pages_clause``, so ``page_id`` is null for a page the caller
cannot see. Every read of ``kb_entities`` takes its FROM from a rule, and
the fence fails on a bare one.

**The tenant comes from the session (R5, R11).** Each read names
``user.organization_id`` beside row level security. :func:`ensure_entity`
takes the tenant from the bound ``app.tenant_id`` and from nothing else.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from acb_auth import UserContext
from fastapi import HTTPException
from sqlalchemy import text

from gateway.routes.knowledge import core
from gateway.routes.projects.core import resolve_visibility

# ── The vocabulary ──────────────────────────────────────────────────────────

#: The seven page types of §6.1. The CHECK ``kb_entities_type_check`` of the
#: migration mirrors this tuple, in this order (fence: test_kc_entities.py).
ENTITY_TYPES: tuple[str, ...] = (
    "company", "product", "person", "customer", "workstream", "workflow", "topic",
)

#: The ``ref_table`` of an entity that Knowledge owns.
KNOWLEDGE_TABLE = "kb_entities"

#: The owner table of each type. The CHECK ``kb_entities_type_ref_check``
#: mirrors these pairs. A workflow is a row of ``workflows`` or a written
#: procedure that Knowledge owns.
OWNER_TABLES: dict[str, tuple[str, ...]] = {
    "company": ("organization",),
    "product": (KNOWLEDGE_TABLE,),
    "person": ("people",),
    "customer": ("crm_companies",),
    "workstream": ("pm_projects",),
    "workflow": ("workflows", KNOWLEDGE_TABLE),
    "topic": (KNOWLEDGE_TABLE,),
}

#: The type of an entity whose record another app owns, by its table.
TYPE_OF_TABLE: dict[str, str] = {
    "organization": "company",
    "people": "person",
    "crm_companies": "customer",
    "pm_projects": "workstream",
    "workflows": "workflow",
}

#: The types that Knowledge owns, so ``ref_table = 'kb_entities'``.
KNOWLEDGE_TYPES: tuple[str, ...] = tuple(
    t for t in ENTITY_TYPES if KNOWLEDGE_TABLE in OWNER_TABLES[t]
)

#: The types that ``POST /knowledge/entities`` makes (KC-8a).
CREATABLE_TYPES: tuple[str, ...] = ("product", "topic")

#: The feature each owner app checks first.
PEOPLE_FEATURE = "feature:people"
PROJECTS_FEATURE = "feature:projects"
WORKFLOWS_FEATURE = "feature:workflows"

#: The most rows that one rule reads.
DIRECTORY_CAP = 2000
#: The longest name, and the most aliases of one entity (the CHECKs).
NAME_MAX = 200
ALIASES_MAX = 50
#: The longest alias.
ALIAS_MAX = 120

#: The bound tenant, as ``people/core.py::has_login`` writes it. ``NULLIF``
#: fails closed: an unbound GUC matches no row.
_BOUND = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: Is the owner row in the bound tenant? One check for each ``ref_table``.
#: ``organization`` has no row level security, so it compares its own id. A
#: Knowledge-owned entity checks its space instead.
OWNER_EXISTS_SQL: dict[str, str] = {
    "organization": (
        "SELECT 1 FROM organization o "
        f"WHERE o.id = CAST(:ke_ref AS uuid) AND o.id = {_BOUND}"
    ),
    "people": (
        "SELECT 1 FROM people o "
        f"WHERE o.id = CAST(:ke_ref AS uuid) AND o.organization_id = {_BOUND}"
    ),
    "crm_companies": (
        "SELECT 1 FROM crm_companies o "
        f"WHERE o.id = CAST(:ke_ref AS uuid) AND o.organization_id = {_BOUND}"
    ),
    "pm_projects": (
        "SELECT 1 FROM pm_projects o "
        f"WHERE o.id = CAST(:ke_ref AS uuid) AND o.organization_id = {_BOUND}"
    ),
    "workflows": (
        "SELECT 1 FROM workflows o "
        f"WHERE o.id = CAST(:ke_ref AS uuid) AND o.organization_id = {_BOUND}"
    ),
    KNOWLEDGE_TABLE: (
        "SELECT 1 FROM kb_spaces s "
        f"WHERE s.id = CAST(:ke_space AS uuid) AND s.organization_id = {_BOUND}"
    ),
}


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def clean_name(value: Any) -> str:
    """The name, with its white space collapsed. 422 when it is out of range."""
    out = " ".join(str(value or "").split())
    if not 1 <= len(out) <= NAME_MAX:
        raise _unprocessable(f"Give a name of 1 to {NAME_MAX} characters.")
    return out


def clean_aliases(values: list[Any] | None) -> list[str]:
    """Aliases, collapsed and once each by ``fold()``. 422 on a bad one."""
    from gateway.kb_entities.resolver import fold

    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        alias = " ".join(str(raw or "").split())
        if not 1 <= len(alias) <= ALIAS_MAX:
            raise _unprocessable(f"An alias has 1 to {ALIAS_MAX} characters.")
        key = fold(alias)
        if key not in seen:
            seen.add(key)
            out.append(alias)
    if len(out) > ALIASES_MAX:
        raise _unprocessable(f"An entity has at most {ALIASES_MAX} aliases.")
    return out


# ── The one writer ──────────────────────────────────────────────────────────

async def ensure_entity(
    db: Any, ref_table: str, ref_id: Any, *, type: str | None = None,
    name: str | None = None, space_id: str | None = None,
    aliases: list[str] | None = None,
) -> str | None:
    """Get or make the entity of one record, and return its id.

    *ref_table* is an owner table of :data:`OWNER_TABLES`, and *ref_id* is the
    id of the owner row. The tenant is the bound ``app.tenant_id``. The
    INSERT runs only when the owner row is in that tenant, and it does
    nothing on the partial unique index, so a second call returns the first
    row. The answer is None when the owner row is not in the tenant.

    For a Knowledge-owned entity, *ref_table* is ``kb_entities`` and
    *ref_id* is None. *type* (product, topic or workflow), *name* and
    *space_id* are required, and the new row points at itself. Each call
    makes a new entity. The caller checked ``write`` on the space.

    It decides nothing about who may see the entity. A caller with a viewer
    reads it back through :func:`get_entity`.
    """
    if ref_table not in OWNER_EXISTS_SQL:
        raise ValueError(f"unknown owner table {ref_table!r}")
    new_id = str(uuid.uuid4())
    if ref_table == KNOWLEDGE_TABLE:
        if type not in KNOWLEDGE_TYPES:
            raise ValueError(f"a Knowledge-owned entity needs a type of {KNOWLEDGE_TYPES}")
        space = core.parse_id(space_id)
        if space is None or not name:
            raise ValueError("a Knowledge-owned entity needs a space_id and a name")
        ref: str | None = new_id
        etype = type
    else:
        etype = TYPE_OF_TABLE[ref_table]
        if type is not None and type != etype:
            raise ValueError(f"{ref_table} holds entities of type {etype!r}")
        ref = core.parse_id(ref_id)
        if ref is None:
            return None
        name, space = None, None
    params = {
        "ke_id": new_id, "ke_type": etype, "ke_table": ref_table, "ke_ref": ref,
        "ke_name": name, "ke_aliases": list(aliases or []), "ke_space": space,
    }
    made = (await db.execute(
        text("INSERT INTO kb_entities "
             "(id, organization_id, type, ref_table, ref_id, name, aliases, space_id) "
             f"SELECT CAST(:ke_id AS uuid), {_BOUND}, :ke_type, :ke_table, "
             "CAST(:ke_ref AS uuid), :ke_name, CAST(:ke_aliases AS text[]), "
             "CAST(:ke_space AS uuid) "
             f"WHERE EXISTS ({OWNER_EXISTS_SQL[ref_table]}) "
             "ON CONFLICT (organization_id, ref_table, ref_id) "
             "WHERE ref_id IS NOT NULL DO NOTHING "
             "RETURNING id"),
        params,
    )).first()
    if made is not None:
        return str(made.id)
    # Another call made the row first, or the owner row is not in the tenant.
    # An integrity read: one id, never a name (listed in the fence).
    found = (await db.execute(
        text("SELECT id FROM kb_entities "
             f"WHERE organization_id = {_BOUND} "
             "AND ref_table = :ke_table AND ref_id = CAST(:ke_ref AS uuid)"),
        {"ke_table": ref_table, "ke_ref": ref},
    )).first()
    return str(found.id) if found is not None else None


async def set_page(db: Any, user: UserContext, entity_id: str, page_id: str) -> None:
    """Point the entity at its page. The caller holds the row locked."""
    await db.execute(
        text("UPDATE kb_entities SET page_id = CAST(:ke_page AS uuid), "
             "updated_at = now() "
             "WHERE organization_id = CAST(:ke_org AS uuid) "
             "AND id = CAST(:ke_id AS uuid)"),
        {"ke_org": core.org_of(user), "ke_id": entity_id, "ke_page": page_id},
    )


# ── The owner rule ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OwnerRule:
    """The read of one type under its owner rule, as SQL fragments.

    ``source`` is the FROM: the owner table ``o`` with a LEFT JOIN of
    ``kb_entities e``, or ``kb_entities e`` with its space for a
    Knowledge-owned type. ``where`` is the owner predicate. A statement that
    puts in ``source`` puts in ``where`` too (fence). ``name`` and ``ref``
    are the expressions of the live name and the owner id.
    """

    type: str
    ref_table: str
    source: str
    where: str
    name: str
    ref: str
    params: dict[str, Any]


def _company_rule(user: UserContext, org: str) -> OwnerRule:
    """The org itself. Any member who passed the Knowledge gate sees it."""
    return OwnerRule(
        type="company", ref_table="organization",
        source=("FROM organization o LEFT JOIN kb_entities e "
                "ON e.organization_id = CAST(:ke_org AS uuid) "
                "AND e.ref_table = 'organization' AND e.ref_id = o.id"),
        where="o.id = CAST(:ke_org AS uuid)",
        name="o.display_name", ref="o.id", params={"ke_org": org},
    )


def _person_rule(user: UserContext, org: str) -> OwnerRule | None:
    """Active people, for a holder of ``feature:people``. The name only."""
    if not user.has_permission(PEOPLE_FEATURE):
        return None
    return OwnerRule(
        type="person", ref_table="people",
        source=("FROM people o LEFT JOIN kb_entities e "
                "ON e.organization_id = CAST(:ke_org AS uuid) "
                "AND e.ref_table = 'people' AND e.ref_id = o.id"),
        where="o.organization_id = CAST(:ke_org AS uuid) AND o.status = 'active'",
        name="o.name", ref="o.id", params={"ke_org": org},
    )


async def _customer_rule(user: UserContext, org: str) -> OwnerRule | None:
    """CRM companies, for a member the CRM's own two gates admit.

    ``require_crm_enabled`` is the kill switch (a 404) and
    ``require_crm_access`` reads ``crm_settings.access``. Either refusal
    means no customer row. They import here, at call time, so a Knowledge
    import does not load the whole CRM package.
    """
    from gateway.routes.crm.access import require_crm_access
    from gateway.routes.crm.flags import require_crm_enabled

    try:
        await require_crm_enabled(user)
        await require_crm_access(user)
    except HTTPException:
        return None
    return OwnerRule(
        type="customer", ref_table="crm_companies",
        source=("FROM crm_companies o LEFT JOIN kb_entities e "
                "ON e.organization_id = CAST(:ke_org AS uuid) "
                "AND e.ref_table = 'crm_companies' AND e.ref_id = o.id"),
        where="o.organization_id = CAST(:ke_org AS uuid)",
        name="o.name", ref="o.id", params={"ke_org": org},
    )


async def _workstream_rule(db: Any, user: UserContext, org: str) -> OwnerRule | None:
    """Projects the caller sees in Projects, and never a personal project."""
    if not user.has_permission(PROJECTS_FEATURE):
        return None
    vis = await resolve_visibility(db, user)
    return OwnerRule(
        type="workstream", ref_table="pm_projects",
        source=("FROM pm_projects o LEFT JOIN kb_entities e "
                "ON e.organization_id = CAST(:ke_org AS uuid) "
                "AND e.ref_table = 'pm_projects' AND e.ref_id = o.id"),
        where=(f"o.organization_id = CAST(:ke_org AS uuid) "
               f"AND o.personal_owner IS NULL AND {vis.project_clause('o.id')}"),
        name="o.name", ref="o.id", params={"ke_org": org, **vis.params},
    )


def _workflow_rule(user: UserContext, org: str) -> OwnerRule | None:
    """Rows of ``workflows``, for a holder of ``feature:workflows``.

    The name only, never the graph or the hook token.
    """
    if not user.has_permission(WORKFLOWS_FEATURE):
        return None
    return OwnerRule(
        type="workflow", ref_table="workflows",
        source=("FROM workflows o LEFT JOIN kb_entities e "
                "ON e.organization_id = CAST(:ke_org AS uuid) "
                "AND e.ref_table = 'workflows' AND e.ref_id = o.id"),
        where="o.organization_id = CAST(:ke_org AS uuid)",
        name="o.name", ref="o.id", params={"ke_org": org},
    )


def _knowledge_rule(user: UserContext, org: str, kind: str) -> OwnerRule:
    """A Knowledge-owned type: the space must pass the read side of a space."""
    clause, params = core.readable_spaces_clause(user, "kb_es")
    return OwnerRule(
        type=kind, ref_table=KNOWLEDGE_TABLE,
        source=("FROM kb_entities e "
                "JOIN kb_spaces kb_es ON kb_es.id = e.space_id"),
        where=(f"e.organization_id = CAST(:ke_org AS uuid) "
               f"AND e.ref_table = 'kb_entities' AND e.ref_id = e.id "
               f"AND e.type = :ke_type AND {clause}"),
        name="e.name", ref="e.id",
        params={**params, "ke_org": org, "ke_type": kind},
    )


async def owner_rules(db: Any, user: UserContext, kind: str) -> list[OwnerRule]:
    """The reads of *kind* the caller may make, each under its owner rule.

    An empty list means the caller sees no entity of that type. A workflow
    has two: the rows of ``workflows`` and the written procedures.
    """
    org = core.org_of(user)
    rules: list[OwnerRule | None]
    if kind == "company":
        rules = [_company_rule(user, org)]
    elif kind == "person":
        rules = [_person_rule(user, org)]
    elif kind == "customer":
        rules = [await _customer_rule(user, org)]
    elif kind == "workstream":
        rules = [await _workstream_rule(db, user, org)]
    elif kind == "workflow":
        rules = [_workflow_rule(user, org), _knowledge_rule(user, org, kind)]
    elif kind in KNOWLEDGE_TYPES:
        rules = [_knowledge_rule(user, org, kind)]
    else:
        raise ValueError(f"unknown entity type {kind!r}")
    return [r for r in rules if r is not None]


# ── The reads ───────────────────────────────────────────────────────────────

def _row_dict(rule: OwnerRule, row: Any) -> dict[str, Any]:
    return {
        "id": str(row.entity_id) if row.entity_id is not None else None,
        "type": rule.type,
        "ref_table": rule.ref_table,
        "ref_id": str(row.ref_id) if row.ref_id is not None else None,
        "name": row.name,
        "aliases": list(row.aliases or []),
        "space_id": str(row.space_id) if row.space_id is not None else None,
        "page_id": str(row.page_id) if row.page_id is not None else None,
    }


async def directory_rows(
    db: Any, user: UserContext, kind: str, *, entity_id: str | None = None,
    extra_where: str | None = None, extra_params: dict[str, Any] | None = None,
    cap: int = DIRECTORY_CAP,
) -> tuple[list[dict[str, Any]], bool]:
    """The rows of *kind* the caller sees, and whether a rule hit the cap.

    The read starts from the owner table under the owner rule and LEFT JOINs
    ``kb_entities``. So an owner row with no entity shows with ``id`` None,
    and an entity whose owner row is gone (an orphan) never shows. The page
    joins under ``visible_pages_clause``, so ``page_id`` is None for a page
    the caller cannot see. *extra_where* is a code constant of this package
    (an alias or an email filter), never input.
    """
    vis, vparams = core.visible_pages_clause(user, "p")
    out: list[dict[str, Any]] = []
    truncated = False
    for rule in await owner_rules(db, user, kind):
        params: dict[str, Any] = {**vparams, **rule.params, "ke_cap": cap}
        cond = ""
        if entity_id is not None:
            cond += " AND e.id = CAST(:ke_id AS uuid)"
            params["ke_id"] = entity_id
        if extra_where:
            cond += f" AND ({extra_where})"
            params.update(extra_params or {})
        rows = (await db.execute(
            text(f"SELECT {rule.ref} AS ref_id, {rule.name} AS name, e.id AS entity_id, "
                 f"e.aliases AS aliases, e.space_id AS space_id, p.id AS page_id "
                 f"{rule.source} "
                 f"LEFT JOIN kb_pages p ON p.id = e.page_id AND {vis} "
                 f"WHERE {rule.where}{cond} "
                 f"ORDER BY lower({rule.name}), {rule.ref} LIMIT :ke_cap"),
            params,
        )).fetchall()
        truncated = truncated or len(rows) >= cap
        out.extend(_row_dict(rule, r) for r in rows)
    return out, truncated


async def entity_type(db: Any, user: UserContext, entity_id: str) -> str | None:
    """The type of one entity of the caller's org, or None.

    An integrity read (listed in the fence): it picks the owner rule for a
    GET by id. It returns the type only, and the caller answers 404 unless
    :func:`directory_rows` then returns the row under the rule.
    """
    row = (await db.execute(
        text("SELECT type FROM kb_entities "
             "WHERE organization_id = CAST(:ke_org AS uuid) "
             "AND id = CAST(:ke_id AS uuid)"),
        {"ke_org": core.org_of(user), "ke_id": entity_id},
    )).first()
    return str(row.type) if row is not None else None


async def get_entity(db: Any, user: UserContext, entity_id: str | None) -> dict[str, Any] | None:
    """One entity under its owner rule, or None (the caller says 404)."""
    eid = core.parse_id(entity_id)
    if eid is None:
        return None
    kind = await entity_type(db, user, eid)
    if kind is None:
        return None
    rows, _ = await directory_rows(db, user, kind, entity_id=eid, cap=1)
    return rows[0] if rows else None


async def lock_page_slot(db: Any, user: UserContext, entity_id: str) -> str | None:
    """Lock the entity row and return its raw ``page_id``.

    An integrity read (listed in the fence): the lazy page route calls it
    after it read the entity through the rule. Two first writes then make
    one page, and a page the caller cannot see is never replaced.
    """
    row = (await db.execute(
        text("SELECT page_id FROM kb_entities "
             "WHERE organization_id = CAST(:ke_org AS uuid) "
             "AND id = CAST(:ke_id AS uuid) FOR UPDATE"),
        {"ke_org": core.org_of(user), "ke_id": entity_id},
    )).first()
    if row is None or row.page_id is None:
        return None
    return str(row.page_id)
