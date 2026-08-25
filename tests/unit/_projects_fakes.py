"""In-memory doubles for the ``gateway.routes.projects`` package.

One fake, shared by every ``test_projects_*.py``: two copies of a DB mirror
drift, and the copy that drifts is the one that stops failing (the
``_admin_fakes.py`` lesson, restated by ``_crm_fakes.py``).

Not named ``test_*``, so pytest imports it without collecting it.

Convention: route functions are called directly as async functions with
``core._get_db`` monkeypatched onto each SUT submodule, so nothing here touches
Postgres and no TestClient is started.

⚠️ **This is a MIRROR, and a mirror can only agree with itself.** It reads the
statement *text* to decide which rows a clause addresses. That matters most for
the visibility clause: the grant closure is applied **only when the statement
actually contains the ``pm_project_grants`` subquery**, so a route that drops
its visibility clause stops being filtered here too and its 404 test goes red.
A fake that re-implemented the predicate in Python and applied it regardless
would pass against an unscoped route — which is the whole defect class this
package exists to avoid.

⚠️ **The tenant predicate is mirrored the same way** (WS-29b). Three shapes all
say ``organization_id`` — the grant closure's own arm, the ``data:org:read``
subquery, and the outer ``AND`` composed above both — and each is applied only
when the statement carries THAT shape. A mirror that scoped everything by the
caller's organization regardless would agree with a route that dropped its
tenant clause, which is the leak ``specs/multi_tenancy.md`` §6 calls the most
dangerous line in the retrofit.

Its blind spots, stated so nobody reads a green suite as more than it is:

* **Foreign keys and therefore cascades.** Deleting a ``pm_projects`` row leaves
  seeded ``pm_tasks`` where they are; Postgres would take them. The delete
  routes' blast-radius counts are asserted against the statement text and the
  pre-delete counts, never against survivors here.
* **CHECK constraints.** The ``category`` / ``source`` / ``type`` vocabularies
  and ``pm_activities``' "at least one target" are enforced by the database;
  the API's matching guards are asserted structurally in
  ``test_projects_migration.py`` and behaviourally against each route's 422.
* **RESTRICT.** ``pm_tasks.status_id``'s restrict is Postgres's; the route's
  409-with-a-count is what is tested here.

An unreadable WHERE clause raises rather than matching every row: a clause the
fake cannot read must never silently mean "the whole table".
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

# ── Statement shapes the routes actually emit ───────────────────────────────

_FROM_RE = re.compile(
    r"(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|FROM)\s+([a-z_][a-z0-9_]*)", re.I
)
_WHERE_RE = re.compile(
    r"\bWHERE\b(.*?)(?:\bORDER\s+BY\b|\bLIMIT\b|\bRETURNING\b|$)", re.I | re.S
)
_ORDER_RE = re.compile(r"\bORDER\s+BY\s+(?:\w+\.)?([a-z_][a-z0-9_]*)\s*(ASC|DESC)?", re.I)
_LIMIT_RE = re.compile(r"\bLIMIT\s+:(\w+)", re.I)
_OFFSET_RE = re.compile(r"\bOFFSET\s+:(\w+)", re.I)
_INSERT_COLS_RE = re.compile(r"INSERT\s+INTO\s+\w+\s*\(([^)]*)\)", re.I)
_SET_RE = re.compile(r"\bSET\b(.*?)(?:\bWHERE\b|\bRETURNING\b|$)", re.I | re.S)
_BOUND_RE = re.compile(r":(\w+)")
_CONFLICT_RE = re.compile(r"ON\s+CONFLICT\s*\(([^)]*)\)", re.I)

#: ``<col> = CAST(:param AS uuid)``, with an optional table alias.
_UUID_EQ = re.compile(r"(?:\w+\.)?(\w+)\s*=\s*CAST\(:(\w+)\s+AS\s+uuid\)", re.I)
#: ``lower(<col>) = :param``, and ``lower(<col>) = lower(:param)`` — the second
#: spelling is `core.VOCABULARY_IDENTITY`'s, which lowers BOTH sides so the
#: column half and the bound half cannot drift apart. Both mean the same
#: case-insensitive comparison, and this mirror already lowers both.
_LOWER_EQ = re.compile(
    r"lower\((?:\w+\.)?(\w+)\)\s*=\s*(?:lower\()?:(\w+)\)?", re.I
)
#: ``<col> = :param`` — never inside a lower() or a CAST.
_PLAIN_EQ = re.compile(r"(?<!lower\()\b(?:\w+\.)?(\w+)\s*=\s*:(\w+)\b")
#: ``<col> = ANY(CAST(:param AS uuid[]))`` — a bounded id-set membership test
#: (WS-27w's relatives walk and picker exclusion).
_ANY_UUID = re.compile(
    r"(?:\w+\.)?(\w+)\s*=\s*ANY\(CAST\(:(\w+)\s+AS\s+uuid\[\]\)\)", re.I
)
#: ``<col> = 'literal'``
_LITERAL_EQ = re.compile(r"\b(?:\w+\.)?(\w+)\s*=\s*'([^']*)'")
#: ``(<a> = CAST(:p AS uuid) OR <b> = CAST(:p AS uuid))`` — a row addressed
#: from EITHER end, which is how `pm_task_links` is deleted (the caller may be
#: the source or the target). Read as the OR it is: the generic scanner ANDs the
#: predicates it finds, so without this the clause matches nothing and a route
#: that works in production looks broken here.
_EITHER_END = re.compile(
    r"\(\s*(?:\w+\.)?(\w+)\s*=\s*CAST\(:(\w+)\s+AS\s+uuid\)\s+OR\s+"
    r"(?:\w+\.)?(\w+)\s*=\s*CAST\(:(\w+)\s+AS\s+uuid\)\s*\)",
    re.I,
)
#: ``<col> IS [NOT] NULL``
_IS_NULL = re.compile(r"\b(?:\w+\.)?(\w+)\s+IS\s+(NOT\s+)?NULL", re.I)
#: WS-27bj — the tenant anchor on an org-wide vocabulary read: the root
#: project's own `organization_id`, read from `pm_projects` rather than taken as
#: a parameter (`core.vocabulary_scope`).
_PROJECT_TENANT_SUBSELECT = re.compile(
    r"SELECT\s+p\.organization_id\s+FROM\s+pm_projects\s+p", re.I
)
#: …and the union half, present only when the clause offers BOTH scopes.
_VOCABULARY_UNION = re.compile(
    r"project_id\s*=\s*CAST\(:root\s+AS\s+uuid\)\s+OR\s", re.I
)
#: ``<col> ILIKE :q``
_ILIKE = re.compile(r"(?:\w+\.)?(\w+)\s+ILIKE\s+:(\w+)", re.I)
#: ``<col> < now()`` — the date half of the `overdue` filter. Unmirrored until
#: WS-27q, which meant every `overdue` test was really only asserting the
#: status half and would have passed with the date comparison deleted.
_NOW_LT = re.compile(r"\b(?:\w+\.)?(\w+)\s*<\s*now\(\)", re.I)
#: WS-27ae: ``<col> <= :param`` — the delta feed's horizon. Distinct from
#: `_BOUND_LT` below, which requires `:` immediately after the `<` and so cannot
#: match `<=`; the two are told apart because the feed's whole boundary argument
#: is about which comparison is inclusive.
_BOUND_LTE = re.compile(r"\b(?:\w+\.)?(\w+)\s*<=\s*:(\w+)\b")
#: WS-27ae: the delta feed's KEYSET cursor —
#: ``(t.updated_at, t.id) > (:since_ts, CAST(:since_id AS uuid))``. Read as a
#: row comparison, not as two independent predicates: reading it loosely (`ts >`
#: OR `id >`) is exactly the mutation that reintroduces the boundary-second hole
#: this endpoint exists to close, so the mirror has to model the tuple.
_KEYSET_GT = re.compile(
    r"\((?:\w+\.)?(\w+),\s*(?:\w+\.)?(\w+)\)\s*>\s*"
    r"\(:(\w+),\s*CAST\(:(\w+)\s+AS\s+uuid\)\)",
    re.I,
)
#: ``<col> < :param`` — a bound strict-less-than (WS-27z's untouched-since
#: cutoff, and the `due_before` filter). The column must be a bare word right
#: before the `<`, so `coalesce(…) < :window_to` (handled by `_WINDOW_CMP`)
#: never matches, and `<=` never matches (`=` is not `:`).
_BOUND_LT = re.compile(r"\b(?:\w+\.)?(\w+)\s*<\s*:(\w+)\b")
#: WS-27q's calendar window: ``coalesce(<a>, <b>) < :window_to``. The captured
#: expression is what says WHICH interval endpoint the comparison is about, so
#: the mirror reads the SQL's coalesce order rather than assuming one.
_WINDOW_CMP = re.compile(
    r"coalesce\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*(<|>=)\s*:(window_to|window_from)",
    re.I | re.S,
)
#: WS-27t: ``l.source_task_id AS blocker`` — which END plays which ROLE. Read
#: rather than assumed, because swapping the two aliases points every arrow the
#: wrong way and a mirror that hard-codes the roles cannot see it.
_ALIASED_END = re.compile(
    r"l\.(source_task_id|target_task_id)\s+AS\s+(blocker|blocked)\b", re.I
)
#: WS-27t: which end is required to be inside the window.
_END_IN_WINDOW = re.compile(
    r"l\.(source_task_id|target_task_id)\s*=\s*ANY\(CAST\(:ids", re.I
)
#: The column a subquery restricts: ``t.project_id IN ( WITH RECURSIVE …``
_IN_SUBQUERY = re.compile(
    r"(?:\w+\.)?(\w+)\s+IN\s*\(\s*WITH\s+RECURSIVE\s+(\w+)", re.I
)

# ── The tenant predicate (WS-29b) ───────────────────────────────────────────
#
# Three shapes, and they must be told apart because all three say
# `organization_id`. Reading any of them as another is how a mirror agrees with
# a route that scoped the wrong table.

#: ``core._TENANT_PROJECTS_SQL`` — the unrestricted (`data:org:read`) clause,
#: which is the whole portfolio OF ONE ORGANIZATION.
_TENANT_PROJECTS = re.compile(
    r"SELECT id FROM pm_projects WHERE organization_id\s*=\s*CAST\(:vis_org", re.I
)
#: The column that subquery restricts: ``t.root_project_id IN ( SELECT id FROM…``
_IN_TENANT_SUBQUERY = re.compile(
    r"(?:\w+\.)?(\w+)\s+IN\s*\(\s*SELECT id FROM pm_projects WHERE organization_id",
    re.I,
)
#: The grant closure's own tenant arm — ⚠️ the line that makes `subject = 'org'`
#: mean "everybody **in this organization**".
_CLOSURE_IS_TENANTED = re.compile(
    r"g\.organization_id\s*=\s*CAST\(:vis_org", re.I
)
#: ⚠️ And the closure's RECURSIVE step, read SEPARATELY from its seed step.
#: They are two predicates on two tables and a mirror that inferred one from
#: the other cannot see a mutant that deletes just the second — which is
#: exactly what happened, and this is the fix.
_DESCENT_IS_TENANTED = re.compile(
    r"p\.organization_id\s*=\s*CAST\(:vis_org", re.I
)
#: ``<alias>.organization_id = CAST(:vis_org AS uuid)`` — the tenant composed
#: ABOVE the grant closure, and the whole of the unrestricted task clause.
_ROW_TENANT = re.compile(
    r"\w+\.organization_id\s*=\s*CAST\(:vis_org\s+AS\s+uuid\)", re.I
)
#: The closure body, removed before looking for ``_ROW_TENANT`` — it contains
#: `g.organization_id`, and mistaking that for the outer AND would filter the
#: statement's own table by a predicate that is about the grant rows.
_CLOSURE_BODY = re.compile(
    r"WITH RECURSIVE granted AS.*?SELECT id FROM granted", re.I | re.S
)

#: The one organization this fake models. `organization` has exactly one seeded
#: row in the real schema (`slug='default'`), and every test that is not ABOUT
#: tenancy is written against that deployment.
DEFAULT_ORGANIZATION = "00000000-0000-4000-8000-0000000000aa"

#: Migration 161's trigger table, mirrored: ``table → (parent table, FK column)``.
#:
#: The DATABASE derives a child's `organization_id` from its parent on write, so
#: 43 INSERT sites in 16 modules did not have to grow a tenant argument. That
#: derivation has to happen here too, or every one of those inserts would land a
#: NULL and every scoped read of it would come back empty — which looks exactly
#: like the scoping working.
#:
#: Only the first parent is listed. The real trigger also declares SECOND
#: attachments (`pm_tasks.root_project_id`, `pm_task_links.target_task_id`,
#: `pm_view_task_positions.task_id`) whose job is to REFUSE a row straddling two
#: organizations. Those are a database constraint, and constraints are this
#: fake's stated blind spot — they are proved against a real Postgres.
_ORGANIZATION_PARENT: dict[str, tuple[str, str]] = {
    "pm_projects": ("pm_projects", "parent_project_id"),
    "pm_project_grants": ("pm_projects", "project_id"),
    "pm_task_statuses": ("pm_projects", "project_id"),
    "pm_task_types": ("pm_projects", "project_id"),
    "pm_task_counters": ("pm_projects", "project_id"),
    "pm_custom_fields": ("pm_projects", "project_id"),
    "pm_tags": ("pm_projects", "project_id"),
    "pm_recurrences": ("pm_projects", "project_id"),
    "pm_views": ("pm_projects", "project_id"),
    "pm_tasks": ("pm_projects", "project_id"),
    "pm_activities": ("pm_tasks", "task_id"),
    "pm_task_assignees": ("pm_tasks", "task_id"),
    "pm_task_watchers": ("pm_tasks", "task_id"),
    "pm_task_links": ("pm_tasks", "source_task_id"),
    "pm_task_attachments": ("pm_tasks", "task_id"),
    "pm_task_personal": ("pm_tasks", "task_id"),
    "pm_notifications": ("pm_tasks", "task_id"),
    "pm_view_task_positions": ("pm_views", "view_id"),
    # WS-27u. The wrapper's SECOND attachment (`duplicate_of_task_id`) is a
    # refuse-a-straddle constraint, which is this fake's stated blind spot.
    "pm_intake": ("pm_tasks", "task_id"),
    # WS-27ae. `pm_task_tombstones` is deliberately ABSENT: it has no live
    # parent (its task is gone), and migration 168 fills its tenant from the
    # deleted row inside the AFTER DELETE trigger `_delete` mirrors below.
    "pm_view_user_state": ("pm_views", "view_id"),
}


_SUBQUERY_RE = re.compile(r"\b(SELECT|WITH)\b", re.I)


def like_to_regex(pattern: str) -> re.Pattern[str]:
    """A SQL LIKE pattern → the regex it means, honouring the backslash escape.

    Written properly rather than as a `%`-strip-and-substring, because WS-27r's
    whole defect was that `_` and `%` are METACHARACTERS: a fake that treated
    the pattern as a literal substring would agree with both the escaped and
    the unescaped implementation, and the bug this exists to catch would be
    invisible here.
    """
    out = ["(?is)^"]
    escaped = False
    for char in pattern:
        if escaped:
            out.append(re.escape(char))
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "%":
            out.append(".*")
        elif char == "_":
            out.append(".")
        else:
            out.append(re.escape(char))
    out.append("$")
    return re.compile("".join(out))


def _strip_subqueries(where: str) -> tuple[str, list[str]]:
    """Split a WHERE into its top-level text and its subquery blocks.

    Only blocks that actually contain a subquery are removed. ``CAST(:id AS
    uuid)`` keeps its parentheses and stays in the remainder — stripping it too
    would leave ``id = CAST`` and every id predicate would silently stop
    matching, which is the failure mode where a fake quietly returns the wrong
    row instead of raising.

    The simple column predicates then run against the remainder only, so a
    column named inside an ``EXISTS`` over another table (``a.assignee``) can
    never be mistaken for a column of the row being filtered.
    """
    out: list[str] = []
    blocks: list[str] = []
    depth = 0
    current: list[str] = []
    for char in where:
        if char == "(":
            depth += 1
            if depth == 1:
                current = []
                continue
        elif char == ")":
            depth -= 1
            if depth == 0:
                block = "".join(current)
                if _SUBQUERY_RE.search(block):
                    blocks.append(block)
                else:
                    out.append(f"({block})")
                continue
        if depth == 0:
            out.append(char)
        else:
            current.append(char)
    return "".join(out), blocks


def _depth_zero(statement: str) -> str:
    """``statement`` with every parenthesised group blanked out.

    Index-preserving — every removed character becomes a space — so a match
    found in the result slices the ORIGINAL.
    """
    out: list[str] = []
    depth = 0
    for char in statement:
        if char == "(":
            depth += 1
            out.append(" ")
        elif char == ")":
            depth -= 1
            out.append(" ")
        else:
            out.append(" " if depth else char)
    return "".join(out)


def _where_clause(statement: str) -> str | None:
    """The OUTER query's WHERE clause, or ``None``.

    ⚠️ Located at parenthesis depth ZERO, not by a plain regex. A subquery in
    the SELECT list carries a WHERE of its own that comes FIRST in the text —
    ``tags.list_tags`` computes each tag's usage count that way — and a plain
    search hands back that one instead. Every predicate of the real query then
    reads as a predicate of the subquery's table (``t.root_project_id`` against
    a ``pm_tags`` row), so the mirror answers with **no rows at all** and the
    failure looks like a broken route rather than a mis-read statement.
    """
    match = _WHERE_RE.search(_depth_zero(statement))
    if match is None:
        return None
    return statement[match.start(1):match.end(1)]


def _values_exprs(statement: str) -> list[str]:
    """The expressions inside ``VALUES (...)``, in order, split on depth-1 commas.

    A balanced scan rather than a regex, because values are wrapped in
    ``CAST(... AS uuid)`` and a non-greedy ``\\((.*?)\\)`` stops inside the first
    cast — which produced one expression for a three-column insert and a row of
    NULLs. Expressions rather than bind names, because a column may be written
    as a **literal** (``is_system`` is a hard-coded ``false``), and dropping
    those would misalign every value after it.
    """
    head = statement.upper().find(" VALUES ")
    if head < 0:
        return []
    start = statement.find("(", head)
    if start < 0:
        return []
    depth = 0
    current: list[str] = []
    out: list[str] = []
    for char in statement[start:]:
        if char == "(":
            depth += 1
            if depth == 1:
                continue
        elif char == ")":
            depth -= 1
            if depth == 0:
                out.append("".join(current).strip())
                break
        if depth == 1 and char == ",":
            out.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    return out


def _literal(expr: str) -> Any:
    """A VALUES expression that binds nothing → the value Postgres would store."""
    text_value = expr.strip()
    lowered = text_value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered == "null":
        return None
    if lowered.startswith("now()"):
        return _now()
    if text_value.startswith("'") and text_value.endswith("'"):
        return text_value[1:-1]
    try:
        return int(text_value)
    except ValueError:
        return None


class _Result:
    """The slice of the SQLAlchemy result surface these routes use."""

    def __init__(self, rows: list[Any], scalar: Any = None,
                 rowcount: int | None = None):
        self._rows = rows
        self._scalar = scalar
        #: How many rows the statement AFFECTED — which is not always how many
        #: it returns. `ON CONFLICT DO NOTHING` hands back the row that was
        #: already there while affecting nothing, and `tags.register` reports
        #: "how many were created" straight off this number.
        self.rowcount = len(rows) if rowcount is None else rowcount

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def first(self) -> Any:
        return self.fetchone()

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def scalar(self) -> Any:
        return self._scalar


def _now() -> datetime:
    return datetime.now(UTC)


#: Column defaults migration 145 declares. Kept here so a row read back after an
#: INSERT looks like Postgres would have returned it.
_DEFAULTS: dict[str, dict[str, Any]] = {
    "pm_projects": {
        "status": "active", "source": "manual", "parent_project_id": None,
        "position": None, "archived_at": None, "clickup_id": None,
        "clickup_kind": None, "task_prefix": None, "lead": None,
        "description": None,
        # WS-27z — migration 166's lifecycle policy. NULL = off, the default.
        "archive_after_months": None, "close_after_months": None,
        "timezone": "UTC",
    },
    "pm_project_grants": {},
    "pm_task_statuses": {"color": "gray", "position": 0, "is_default": False},
    # `is_epic` mirrors migration 168:230 (`NOT NULL DEFAULT false`). Absent
    # here, a seeded type reached `TypeModel.is_epic: bool` as None and 422'd.
    "pm_task_types": {"is_default": False, "is_system": False, "icon": None,
                      "color": None, "is_epic": False},
    "pm_task_counters": {"last_value": 0},
    # WS-27bj — the two vocabularies no fake-backed suite reached until the
    # effective-vocabulary union gave them behaviour worth exercising here.
    # Mirrors migrations 156 and 155; `color` carries 156's own DEFAULT.
    "pm_tags": {"color": "gray", "description": None, "created_by": None},
    "pm_custom_fields": {
        "description": None, "field_type": "text", "options": [],
        "position": 0, "created_by": None,
    },
    "pm_tasks": {
        "source": "manual", "parent_task_id": None, "type_id": None,
        "description": None, "importance": None, "estimate_mins": None,
        "start_date": None, "due_at": None, "completed_at": None,
        "tags": [], "archived_at": None, "clickup_id": None,
        "clickup_synced_at": None, "task_number": 1,
    },
    "pm_task_assignees": {},
    "pm_task_watchers": {"created_by": None},
    "pm_task_links": {},
    "pm_activities": {"body": None, "meta": None, "task_id": None,
                      "project_id": None, "deleted_at": None},
    "pm_views": {"view_type": "list", "config": {}, "position": None},
    "pm_view_task_positions": {"group_key": None},
    # Composite key, no `id` column — the upsert path keys on (task_id,
    # member_email) via ON CONFLICT, which _insert already handles generically.
    "pm_task_personal": {
        "disposition": None, "next_action": None, "context": None,
        "energy": None, "time_estimate_mins": None, "is_two_minute": False,
        "defer_until": None, "clarified_at": None,
    },
    # WS-27u — migration 164's defaults, mirrored.
    "pm_intake": {
        "status": "pending", "snoozed_until": None,
        "duplicate_of_task_id": None, "source": None, "source_ref": None,
    },
}

_TIMESTAMPED = {
    "pm_projects", "pm_tasks", "pm_task_statuses", "pm_task_types",
    "pm_activities", "pm_views", "pm_intake", "pm_view_user_state",
    # WS-27bj. Both carry `updated_at NOT NULL DEFAULT now()` (155:63, 156:50),
    # and `update_row` stamps it on every write to them.
    "pm_tags", "pm_custom_fields",
}


class FakeProjectsDB:
    """An in-memory ``pm_*`` schema that answers the package's statements."""

    def __init__(self) -> None:
        #: The tenant every seeded `pm_*` row belongs to, and the answer this
        #: fake gives when a route asks the directory which organization the
        #: caller is in. Set it to ``None`` to model somebody with no
        #: ``app_user`` row; seed real ``app_user`` rows to model two tenants.
        self.organization_id: str | None = DEFAULT_ORGANIZATION
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self.statements: list[str] = []
        #: ``(statement, params)`` in order — how a test proves a write happened
        #: with the values it expects rather than merely that a write happened.
        self.calls: list[tuple[str, dict[str, Any]]] = []
        #: What each `_tenant_session(...)` opened by `bind_db` was handed —
        #: `None` for the ambient form. WS-27aa's background paths must never
        #: appear here as `None`.
        self.bound_tenants: list[str | None] = []
        self.committed = 0
        self.closed = False

    # seeding ------------------------------------------------------------
    def seed(self, table: str, **columns: Any) -> SimpleNamespace:
        row = {
            "id": columns.pop("id", str(uuid4())),
            **_DEFAULTS.get(table, {}),
            **columns,
        }
        # WS-29a — every `pm_*` table carries the tenant key (D-MT-3), so a
        # seeded row that lacked one would be invisible to every scoped read and
        # would make the whole suite red for the wrong reason. Derived from the
        # parent exactly as the database does. An explicit `organization_id=`
        # still wins: that is how the two-tenant tests place a row.
        if table.startswith("pm_") and row.get("organization_id") is None:
            row["organization_id"] = self.derive_organization(table, row)
        if table in _TIMESTAMPED:
            row.setdefault("created_at", _now() - timedelta(days=1))
            row.setdefault("updated_at", _now() - timedelta(days=1))
        self.tables.setdefault(table, []).append(row)
        return SimpleNamespace(**row)

    def rows(self, table: str) -> list[dict[str, Any]]:
        return self.tables.get(table, [])

    def derive_organization(self, table: str, row: dict[str, Any]) -> str | None:
        """One row's tenant, the way migration 161's trigger derives it.

        The parent's value, or — for a ROOT project, which has no parent — this
        fake's own organization, standing in for the value the application is
        required to supply. ``pm_activities`` is the one row that may hang off
        either a task or a project, so its second parent is tried too.
        """
        parent = _ORGANIZATION_PARENT.get(table)
        candidates = [parent] if parent else []
        if table == "pm_activities":
            candidates.append(("pm_projects", "project_id"))
        named_a_parent = False
        for parent_table, column in candidates:
            parent_id = row.get(column)
            if parent_id is None:
                continue
            named_a_parent = True
            for candidate in self.rows(parent_table):
                if str(candidate.get("id")) == str(parent_id):
                    return candidate.get("organization_id")
        # ⚠️ The fallback is for a row the APPLICATION must tenant itself, and
        # 161's trigger is explicit that it "does NOT invent a tenant": with a
        # NULL parent it returns NEW unchanged and `NOT NULL` refuses the insert.
        # Two rows are legitimately in that position — a ROOT project, and (since
        # WS-27bj) an ORG-WIDE vocabulary row with no `project_id` — and both are
        # required to pass the tenant explicitly.
        #
        # Handing them this fake's own organization instead made the mirror agree
        # with a route that had stopped passing one: an org-wide INSERT that
        # dropped the tenant kept its 61 green tests here and would have hit
        # `organization_id NOT NULL` against Postgres.
        if candidates and not named_a_parent and table != "pm_projects":
            return None
        return self.organization_id

    def statements_touching(self, needle: str) -> list[str]:
        return [s for s in self.statements if needle in s]

    def activities(self, kind: str | None = None) -> list[dict[str, Any]]:
        """Timeline rows, with ``meta`` decoded.

        The stored value is the JSON **string** the route bound — which is
        faithful: raw ``text()`` over asyncpg hands jsonb back as a string too,
        which is why ``core.from_jsonb`` exists. Decoding it here lets a test
        read ``meta["from"]`` the way the API's own consumer does, instead of
        asserting against a serialization detail.
        """
        import json

        out: list[dict[str, Any]] = []
        for row in self.rows("pm_activities"):
            if kind is not None and row.get("type") != kind:
                continue
            copy = dict(row)
            if isinstance(copy.get("meta"), str):
                copy["meta"] = json.loads(copy["meta"])
            out.append(copy)
        return out

    # convenience scaffolding --------------------------------------------
    def seed_project(
        self, *, name: str = "Root", parent: str | None = None,
        subject: str | None = "org", **columns: Any,
    ) -> SimpleNamespace:
        """A project, its default status, and (optionally) one grant.

        ``subject=None`` seeds a project nobody has been granted — the shape
        every 404 test needs.
        """
        project = self.seed(
            "pm_projects", name=name, parent_project_id=parent,
            created_by="owner@fracktal.in", **columns,
        )
        if subject is not None:
            self.seed(
                "pm_project_grants", project_id=project.id, subject=subject,
                created_by="owner@fracktal.in",
            )
        return project

    def seed_status(
        self, project_id: str, *, name: str = "To do", category: str = "todo",
        is_default: bool = True, position: int = 10,
    ) -> SimpleNamespace:
        return self.seed(
            "pm_task_statuses", project_id=project_id, name=name,
            category=category, is_default=is_default, position=position,
        )

    def seed_task(
        self, project_id: str, status_id: str, *, root: str | None = None,
        title: str = "A task", **columns: Any,
    ) -> SimpleNamespace:
        return self.seed(
            "pm_tasks", project_id=project_id, root_project_id=root or project_id,
            status_id=status_id, title=title, created_by="owner@fracktal.in",
            **columns,
        )

    # session surface ----------------------------------------------------
    async def commit(self) -> None:
        self.committed += 1

    async def close(self) -> None:
        self.closed = True

    def _workflow_owner_org(self, args: dict) -> list[Any]:
        """One workflow owner's organization, or NO ROW.

        No row is the answer for all three misses — the workflow is unknown,
        its owner is not in the directory, or the owner has no organization —
        because the caller must refuse identically in every one of them rather
        than sort them into a default.
        """
        owners = {
            str(w.get("id")): str(w.get("owner_email") or "").lower()
            for w in self.rows("workflows")
        }
        who = owners.get(str(args.get("wid") or ""))
        if who is None:
            return []
        for row in self.rows("app_user"):
            if str(row.get("email") or "").lower() == who:
                org = row.get("organization_id")
                return [SimpleNamespace(organization_id=org)] if org else []
        return []

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        statement = " ".join(str(sql).split())
        args = dict(params or {})
        self.statements.append(statement)
        self.calls.append((statement, args))
        # WS-27aa's tenant source for the scheduled lifecycle sweep:
        # `workflows.owner_email` → `app_user.organization_id`
        # (`routes/workflows/service._workflow_organization`). Taught
        # explicitly — a JOIN is not a shape the generic WHERE reader can
        # parse — and fingerprinted on the JOIN itself, so a caller that stops
        # resolving through `app_user` stops being answered here.
        if "FROM workflows w JOIN app_user" in statement:
            return _Result(self._workflow_owner_org(args))
        # WS-29b's tenant lookup: `X-User-Email` → `app_user.organization_id`.
        # Answered from seeded `app_user` rows when a test has them — the
        # two-tenant tests do — and otherwise from this fake's single
        # organization, which is the deployment every other test is written
        # against. `self.organization_id = None` models a caller the directory
        # does not know, who then sees nothing because `column = NULL` is NULL.
        if "au.organization_id AS organization_id" in statement:
            who = str(args.get("email") or "").lower()
            for row in self.rows("app_user"):
                if str(row.get("email") or "").lower() == who:
                    return _Result([SimpleNamespace(
                        organization_id=row.get("organization_id"),
                    )])
            if self.rows("app_user") or self.organization_id is None:
                return _Result([])
            return _Result([SimpleNamespace(
                organization_id=self.organization_id,
            )])
        # WS-27k's assignee roll-up: one aggregate for a whole page of tasks,
        # rather than a query per card. Taught to the fake explicitly because
        # `GROUP BY` + `array_agg` is not a shape the generic WHERE reader can
        # parse — and an unparsed WHERE is an assertion here, deliberately, so
        # that a route which loses its filter cannot silently match every row.
        if "array_agg(assignee" in statement:
            wanted = {str(i) for i in (args.get("ids") or [])}
            grouped: dict[str, list[str]] = {}
            for row in self.tables.get("pm_task_assignees", []):
                task_id = str(row.get("task_id"))
                if task_id in wanted:
                    grouped.setdefault(task_id, []).append(str(row.get("assignee")))
            return _Result([
                SimpleNamespace(task_id=task_id, people=sorted(people))
                for task_id, people in grouped.items()
            ])
        # WS-27ae's attachment roll-up: one aggregate over the exported ids,
        # the same page-aggregate shape as the assignee one above and taught
        # for the same reason. Fingerprinted on its own ALIAS rather than on
        # `pm_task_attachments`, which several statements name.
        if "count(*) AS files" in statement:
            return _Result(self._attachment_counts(args))
        # The two card-badge roll-ups, taught for the same reason as the
        # assignee one above: `GROUP BY` is not a shape the generic WHERE
        # reader can parse. Both fingerprints name a statement-specific ALIAS
        # rather than a table, because `pm_tasks` and `pm_task_links` each
        # appear in several statements and a fingerprint that is merely
        # *present* in the target is how the WS-27n audience-clause collision
        # happened.
        if "AS parent," in statement and "GROUP BY" in statement:
            return _Result(self._subtask_counts(statement, args))
        if "AS blocked," in statement and "GROUP BY" in statement:
            return _Result(self._blocker_counts(statement, args))
        # WS-27t's drawable edges. Fingerprinted on `AS blocker,` — the count
        # aggregate above uses `AS blocked,` and this one uses BOTH, so the
        # order of these two branches is load-bearing and the more specific
        # fingerprint has to be tested first.
        if "AS blocker," in statement:
            return _Result(self._window_links(statement, args))
        # WS-27r's ranked search. Three joins and a CASE — a shape the generic
        # WHERE reader cannot parse, and one where `:number IS NOT NULL` would
        # be mistaken for a column predicate and drop every row.
        if "END AS rank" in statement:
            return _Result(self._search_hits(statement, args))
        # WS-27v's audience: watchers ∪ assignees, a UNION the generic WHERE
        # reader cannot parse (it would filter watcher rows by the SECOND
        # SELECT's predicates too). Each arm is honoured ONLY when the
        # statement carries it — the `_select` convention — so an audience that
        # loses either arm loses it here as well and the fan-out test goes red.
        if "AS who" in statement and "pm_task_watchers" in statement:
            wanted = str(args.get("tid"))
            people: set[str] = set()
            if "watcher AS who" in statement:
                people |= {
                    str(r.get("watcher"))
                    for r in self.rows("pm_task_watchers")
                    if str(r.get("task_id")) == wanted
                }
            if "assignee AS who" in statement:
                people |= {
                    str(r.get("assignee"))
                    for r in self.rows("pm_task_assignees")
                    if str(r.get("task_id")) == wanted
                }
            return _Result([SimpleNamespace(who=w) for w in sorted(people)])
        # WS-27u's intake queue: a join the generic reader cannot parse, and an
        # OR over the wrapper's two queue states it must not try to. Answered
        # explicitly, every arm keyed off the statement like the inbox's.
        if "JOIN pm_intake i" in statement:
            found = self._intake_queue(statement, args)
            if re.search(r"SELECT\s+count\(\*\)", statement, re.I):
                return _Result([], scalar=len(found))
            offset = _OFFSET_RE.search(statement)
            if offset:
                found = found[int(args.get(offset.group(1), 0)):]
            limit = _LIMIT_RE.search(statement)
            if limit:
                found = found[: int(args.get(limit.group(1), len(found)))]
            return _Result(found)
        # WS-27ae — the delta feed's HORIZON read. It names no table (`_table`
        # would raise), and it deliberately asks the DATABASE for the instant
        # rather than computing it gateway-side, because the two clocks are not
        # the same one and `updated_at` comes from Postgres's.
        if "AS delta_horizon" in statement:
            # ⚠️ The LAG is read out of the statement, never assumed. A mirror
            # that answered a bare `now()` would agree with a route that had
            # dropped the horizon entirely — which is precisely the property
            # `test_the_horizon_withholds_the_most_recent_writes` exists to
            # pin.
            lag = re.search(r"interval '(\d+) seconds'", statement)
            return _Result([SimpleNamespace(
                delta_horizon=_now() - timedelta(seconds=int(lag.group(1)))
                if lag else _now(),
            )])

        # WS-27ae — one member's overlays for a project's views
        # (`views.list_views`). A subquery over `pm_views` the generic WHERE
        # reader cannot parse. Each arm is honoured ONLY when the statement
        # carries it, the `_select` convention: a route that dropped the
        # `member` predicate would start returning colleagues' arrangements
        # here too, and the privacy test goes red.
        if "FROM pm_view_user_state s" in statement:
            out = []
            wanted = str(args.get("member") or "").lower()
            scoped = (
                {
                    str(v["id"]) for v in self.rows("pm_views")
                    if str(v.get("project_id")) == str(args.get("pid"))
                }
                if "project_id = CAST(:pid AS uuid)" in statement else None
            )
            for row in self.rows("pm_view_user_state"):
                if ("lower(s.member) = :member" in statement
                        and str(row.get("member") or "").lower() != wanted):
                    continue
                if scoped is not None and str(row.get("view_id")) not in scoped:
                    continue
                out.append(SimpleNamespace(
                    view_id=row.get("view_id"), config=row.get("config"),
                ))
            return _Result(out)

        head = statement.split(None, 1)[0].upper()
        table = self._table(statement)
        if head == "INSERT":
            return self._insert(statement, table, args)
        if head == "UPDATE":
            return self._update(statement, table, args)
        if head == "DELETE":
            return self._delete(statement, table, args)
        return self._select(statement, table, args)

    def _table(self, sql: str) -> str:
        # ⚠️ At parenthesis depth ZERO, for the same reason `_where_clause` is:
        # a SELECT-list subquery names its own table first (`tags.list_tags`
        # counts usage out of `pm_tasks`), and reading that one means filtering
        # the wrong table and answering with no rows.
        match = _FROM_RE.search(_depth_zero(sql)) or _FROM_RE.search(sql)
        if match is None:  # pragma: no cover — every statement names a table
            raise AssertionError(f"fake could not find a table in: {sql}")
        return match.group(1)

    # page roll-ups ------------------------------------------------------
    def _categories(self) -> dict[str, str]:
        return {
            str(s["id"]): str(s.get("category") or "")
            for s in self.rows("pm_task_statuses")
        }

    def _attachment_counts(self, args: dict) -> list[Any]:
        """WS-27ae — ``{task_id, files}`` per task, over the exported ids.

        A task with no attachments is ABSENT rather than zero, exactly as the
        `GROUP BY` produces: the export fills its own default, and a mirror
        that returned zeroes would hide a route that stopped doing so.
        """
        wanted = {str(i) for i in (args.get("ids") or [])}
        counts: dict[str, int] = {}
        for row in self.rows("pm_task_attachments"):
            task_id = str(row.get("task_id"))
            if task_id in wanted:
                counts[task_id] = counts.get(task_id, 0) + 1
        return [
            SimpleNamespace(task_id=task_id, files=total)
            for task_id, total in counts.items()
        ]

    def _subtask_counts(self, statement: str, args: dict) -> list[Any]:
        """``{parent, total, done}`` per parent, over the page's ids.

        Every clause is applied ONLY when the statement carries it, the
        ``_select`` convention: a mirror that filters unconditionally agrees
        with itself no matter what the route stops emitting, which is how a
        deleted WHERE clause survives a green suite.
        """
        wanted = {str(i) for i in (args.get("ids") or [])}
        closed = set(args.get("closed") or [])
        skips_archived = "t.archived_at IS NULL" in statement
        counts_closed = "FILTER (WHERE s.category = ANY(:closed))" in statement
        categories = self._categories()
        grouped: dict[str, list[str]] = {}
        for task in self.rows("pm_tasks"):
            parent = str(task.get("parent_task_id") or "")
            if parent not in wanted:
                continue
            if skips_archived and task.get("archived_at") is not None:
                continue
            grouped.setdefault(parent, []).append(
                categories.get(str(task.get("status_id")), "")
            )
        return [
            SimpleNamespace(
                parent=parent,
                total=len(found),
                done=(
                    sum(1 for c in found if c in closed)
                    if counts_closed else len(found)
                ),
            )
            for parent, found in grouped.items()
        ]

    def _search_hits(self, statement: str, args: dict) -> list[Any]:
        """WS-27r's ranked hits.

        Every clause honoured only when the statement carries it — including
        the visibility closure, so a search that loses it stops being scoped
        here too and the leak test goes red.
        """
        projects = {str(p["id"]): p for p in self.rows("pm_projects")}
        statuses = {str(x["id"]): x for x in self.rows("pm_task_statuses")}
        term = like_to_regex(str(args.get("term") or ""))
        prefix = like_to_regex(str(args.get("prefix") or ""))
        number = args.get("number")
        scoped = "pm_project_grants" in statement
        visible = self.visible_project_ids(
            str(args.get("vis_email") or ""), list(args.get("vis_groups") or []),
            organization_id=(
                str(args.get("vis_org"))
                if _CLOSURE_IS_TENANTED.search(statement) else None
            ),
            descendant_organization_id=(
                str(args.get("vis_org"))
                if _DESCENT_IS_TENANTED.search(statement) else None
            ),
        )
        skips_archived = "t.archived_at IS NULL" in statement
        # Same rule as everywhere else: the tenant is applied only when the
        # statement carries it. Search reaches every task in the app, so this is
        # the read where losing it costs the most.
        tenanted = bool(_ROW_TENANT.search(_CLOSURE_BODY.sub("", statement)))
        org = str(args.get("vis_org"))
        tenant_only = bool(_TENANT_PROJECTS.search(statement))
        # WS-27w's picker exclusion — applied ONLY when the statement carries
        # the clause, so a search that drops it stops excluding here too and
        # the four-class test goes red.
        excluded = (
            {str(i) for i in (args.get("excluded") or [])}
            if "NOT (t.id = ANY" in statement else set()
        )
        # WS-27u — search excludes the intake queue unless `include_triage`
        # formatted the predicate away. Keyed on the clause's own alias.
        parked = (
            {
                str(s["id"]) for s in self.rows("pm_task_statuses")
                if s.get("category") == "triage"
            }
            if "s_triage" in statement else None
        )

        found: list[Any] = []
        for task in self.rows("pm_tasks"):
            if str(task.get("id")) in excluded:
                continue
            if tenanted and str(task.get("organization_id")) != org:
                continue
            if parked is not None and str(task.get("status_id")) in parked:
                continue
            if tenant_only and str(task.get("project_id")) not in self.tenant_project_ids(org):
                continue
            if scoped and str(task.get("project_id")) not in visible:
                continue
            if skips_archived and task.get("archived_at") is not None:
                continue
            title = str(task.get("title") or "")
            body = str(task.get("description") or "")
            numbered = number is not None and task.get("task_number") == number
            if not (term.match(title) or term.match(body) or numbered):
                continue
            rank = (
                0 if numbered
                else 1 if prefix.match(title)
                else 2 if term.match(title)
                else 3
            )
            project = projects.get(str(task.get("project_id")), {})
            status = statuses.get(str(task.get("status_id")), {})
            found.append(SimpleNamespace(
                **task,
                project_name=project.get("name"),
                status_name=status.get("name"),
                category=status.get("category"),
                rank=rank,
            ))

        # The tie-break is read off the statement, not assumed: `rank` alone
        # leaves ties in seeding order, which would agree with any tie-break
        # the route chose — including none.
        recent_first = "t.updated_at DESC" in statement
        found.sort(key=lambda r: str(r.id))
        if recent_first:
            found.sort(key=lambda r: _sortable(r.updated_at), reverse=True)
        found.sort(key=lambda r: r.rank)
        cap = int(args.get("cap") or len(found))
        return found[:cap]

    def _window_links(self, statement: str, args: dict) -> list[Any]:
        """The `blocks` edges with BOTH ends inside the page's ids.

        **Which column is the blocker and which columns are membership-tested
        are both read off the statement**, never assumed. Assuming them was a
        real mirror gap found by mutation: hard-coding `blocker=source` let a
        mutant swap the SQL's two aliases — an arrow drawn the wrong way round,
        the chart asserting the opposite sequence — with every test still green,
        and counting the membership tests rather than naming their columns let a
        mutant check the wrong end.
        """
        wanted = {str(i) for i in (args.get("ids") or [])}
        only_blocks = "l.link_type = 'blocks'" in statement
        roles = {role: column for column, role in _ALIASED_END.findall(statement)}
        checked = set(_END_IN_WINDOW.findall(statement))
        out: list[Any] = []
        for link in self.rows("pm_task_links"):
            if only_blocks and link.get("link_type") != "blocks":
                continue
            ends = {
                column: str(link.get(column) or "")
                for column in ("source_task_id", "target_task_id")
            }
            if any(ends[column] not in wanted for column in checked):
                continue
            out.append(SimpleNamespace(
                id=link.get("id"),
                blocker=ends.get(roles.get("blocker", ""), ""),
                blocked=ends.get(roles.get("blocked", ""), ""),
            ))
        return sorted(out, key=lambda r: str(r.id))

    def _intake_queue(self, statement: str, args: dict) -> list[Any]:
        """WS-27u's front-door queue: pending wrappers, plus snoozes past due.

        Every arm is applied ONLY when the statement carries it — the module
        docstring's rule, so a route that drops its visibility clause or its
        queue-state arm stops being filtered here and its test goes red. The
        reappearance is mirrored as the SQL means it: a comparison against
        now(), never a status flip, because nothing in the system wakes a
        snoozed row — it reappears by being read.
        """
        wrappers = {str(w.get("task_id")): w for w in self.rows("pm_intake")}
        scoped = "pm_project_grants" in statement
        me = str(args.get("vis_email") or "").lower()
        visible = self.visible_project_ids(
            me, list(args.get("vis_groups") or []),
            organization_id=(
                str(args.get("vis_org"))
                if _CLOSURE_IS_TENANTED.search(statement) else None
            ),
            descendant_organization_id=(
                str(args.get("vis_org"))
                if _DESCENT_IS_TENANTED.search(statement) else None
            ),
        )
        assignee_escape = (
            "pm_task_assignees" in statement and "vis_email" in statement
        )
        tenanted = bool(_ROW_TENANT.search(_CLOSURE_BODY.sub("", statement)))
        tenant_only = bool(_TENANT_PROJECTS.search(statement))
        org = str(args.get("vis_org"))
        skips_archived = "t.archived_at IS NULL" in statement
        wants_pending = "i.status = 'pending'" in statement
        reappears = "i.snoozed_until <= now()" in statement
        subtree = (
            self._subtree_ids(str(args.get("pid")))
            if "RECURSIVE sub" in statement else None
        )

        out: list[Any] = []
        for task in self.rows("pm_tasks"):
            wrap = wrappers.get(str(task.get("id")))
            if wrap is None:
                continue
            if tenanted and str(task.get("organization_id")) != org:
                continue
            if tenant_only and (
                str(task.get("project_id")) not in self.tenant_project_ids(org)
            ):
                continue
            if scoped and str(task.get("project_id")) not in visible and not (
                assignee_escape and me in self._assignees_of(task.get("id"))
            ):
                continue
            if skips_archived and task.get("archived_at") is not None:
                continue
            if subtree is not None and str(task.get("project_id")) not in subtree:
                continue
            state = wrap.get("status")
            queued = (wants_pending and state == "pending") or (
                reappears and state == "snoozed"
                and wrap.get("snoozed_until") is not None
                and _as_datetime(wrap["snoozed_until"]) <= _now()
            )
            if not queued:
                continue
            out.append(SimpleNamespace(
                **task,
                intake_id=wrap.get("id"),
                intake_status=state,
                intake_snoozed_until=wrap.get("snoozed_until"),
                intake_source=wrap.get("source"),
                intake_source_ref=wrap.get("source_ref"),
                intake_duplicate_of_task_id=wrap.get("duplicate_of_task_id"),
                intake_created_at=wrap.get("created_at"),
            ))
        out.sort(key=lambda r: (_sortable(r.intake_created_at), str(r.id)))
        return out

    def _blocker_counts(self, statement: str, args: dict) -> list[Any]:
        """``{blocked, blockers}`` counting only blockers that are still OPEN.

        Which end of the link is the blocked one is read off the statement
        rather than assumed, so reversing the SQL's direction reverses this
        mirror's answer instead of being invisible to it.
        """
        wanted = {str(i) for i in (args.get("ids") or [])}
        closed = set(args.get("closed") or [])
        blocked_col = (
            "target_task_id" if "l.target_task_id AS blocked" in statement
            else "source_task_id"
        )
        blocker_col = (
            "source_task_id" if "t.id = l.source_task_id" in statement
            else "target_task_id"
        )
        only_blocks = "l.link_type = 'blocks'" in statement
        skips_closed = "NOT (s.category = ANY(:closed))" in statement
        categories = self._categories()
        tasks = {str(t["id"]): t for t in self.rows("pm_tasks")}
        counted: dict[str, int] = {}
        for link in self.rows("pm_task_links"):
            blocked = str(link.get(blocked_col) or "")
            if blocked not in wanted:
                continue
            if only_blocks and link.get("link_type") != "blocks":
                continue
            blocker = tasks.get(str(link.get(blocker_col) or ""))
            if blocker is None:
                continue
            category = categories.get(str(blocker.get("status_id")), "")
            if skips_closed and category in closed:
                continue
            counted[blocked] = counted.get(blocked, 0) + 1
        return [
            SimpleNamespace(blocked=blocked, blockers=count)
            for blocked, count in counted.items()
        ]

    # verbs --------------------------------------------------------------
    def _insert(self, statement: str, table: str, args: dict) -> _Result:
        # The task counter is a read-modify-write in one statement; modelling it
        # as a plain insert would hand every task number 1 and hide a collision
        # the real UNIQUE(root_project_id, task_number) would have caught.
        if table == "pm_task_counters":
            rows = self.tables.setdefault(table, [])
            root = str(args.get("root"))
            for row in rows:
                if str(row.get("project_id")) == root:
                    row["last_value"] = int(row.get("last_value") or 0) + 1
                    return _Result([SimpleNamespace(**row)])
            row = {"project_id": root, "last_value": 1}
            rows.append(row)
            return _Result([SimpleNamespace(**row)])

        columns = [
            c.strip() for c in _INSERT_COLS_RE.search(statement).group(1).split(",")
        ]
        # A column NAMED in the INSERT takes the bound value even when that value
        # is None — Postgres applies a DEFAULT only to an omitted column, and
        # collapsing the two would hide "the route explicitly sent NULL".
        # `VALUES (CAST(:root AS uuid), :name, …)` binds by POSITION, not by
        # column name, so the statement's own bind order is the authority and a
        # same-named parameter is only the fallback.
        exprs = _values_exprs(statement)
        if len(exprs) == len(columns):
            values = {}
            for column, expr in zip(columns, exprs, strict=True):
                bound = _BOUND_RE.search(expr)
                values[column] = (
                    args.get(bound.group(1)) if bound else _literal(expr)
                )
        else:
            values = {c: args.get(c) for c in columns}

        conflict = _CONFLICT_RE.search(statement)
        if conflict:
            keys = [k.strip() for k in conflict.group(1).split(",")]
            for row in self.tables.setdefault(table, []):
                if all(str(row.get(k)) == str(values.get(k)) for k in keys):
                    updated = "DO UPDATE" in statement.upper()
                    if updated:
                        row.update({k: v for k, v in values.items() if v is not None})
                    # DO NOTHING affects nothing, so `rowcount` is 0 — the number
                    # `tags.register` reports as "created".
                    return _Result(
                        [SimpleNamespace(**row)], rowcount=1 if updated else 0,
                    )

        row = {"id": str(uuid4()), **_DEFAULTS.get(table, {}), **values}
        # Migration 161's `pm_organization_from_parent` trigger, mirrored: a
        # child row inserted without a tenant INHERITS its parent's rather than
        # being refused, which is what lets 43 INSERT sites stay unedited.
        if table.startswith("pm_") and row.get("organization_id") is None:
            row["organization_id"] = self.derive_organization(table, row)
        if table in _TIMESTAMPED:
            row.setdefault("created_at", _now())
            row.setdefault("updated_at", _now())
        self.tables.setdefault(table, []).append(row)
        return _Result([SimpleNamespace(**row)])

    def _update(self, statement: str, table: str, args: dict) -> _Result:
        assignments = _SET_RE.search(statement)
        matched = self._matching(statement, table, args)
        for row in matched:
            for part in (assignments.group(1) if assignments else "").split(","):
                column, _, value = part.partition("=")
                column, value = column.strip(), value.strip()
                if not column or "." in column:
                    continue
                bound = _BOUND_RE.search(value)
                if bound:
                    row[column] = args.get(bound.group(1))
                elif value.lower().startswith("now()"):
                    row[column] = _now()
                elif value.lower() in ("false", "true"):
                    row[column] = value.lower() == "true"
        return _Result([SimpleNamespace(**r) for r in matched])

    def _delete(self, statement: str, table: str, args: dict) -> _Result:
        matched = self._matching(statement, table, args)
        self.tables[table] = [
            r for r in self.tables.get(table, []) if r not in matched
        ]
        # WS-27ae — migration 168's `trg_pm_tasks_tombstone`, mirrored for the
        # reason 161's tenant trigger is: the tombstone is written by the
        # DATABASE, so a fake that did not model it would make every delta test
        # about deletion pass against a route with no tombstone table at all.
        #
        # ⚠️ This mirrors the TRIGGER, not the cascade that also fires it. FKs
        # are this fake's stated blind spot, so deleting a `pm_projects` row
        # here leaves its tasks — and therefore writes no tombstones — where
        # Postgres would take both. `tests/live/live_ws27ae_delta.py` is what proves
        # the cascade path.
        if table == "pm_tasks":
            tombstones = self.tables.setdefault("pm_task_tombstones", [])
            known = {str(t.get("task_id")) for t in tombstones}
            for row in matched:
                if str(row.get("id")) in known:
                    continue
                tombstones.append({
                    "task_id": str(row.get("id")),
                    "project_id": row.get("project_id"),
                    "root_project_id": row.get("root_project_id"),
                    "task_number": row.get("task_number"),
                    "organization_id": row.get("organization_id"),
                    "deleted_by": None,
                    "deleted_at": _now(),
                })
        return _Result([SimpleNamespace(**r) for r in matched])

    def _select(self, statement: str, table: str, args: dict) -> _Result:
        # `WITH RECURSIVE chain AS (…) SELECT bool_and(…)` — WS-27bg's
        # ancestor-runnable verdict (`core.is_runnable_with_ancestors`).
        #
        # Answered explicitly rather than left to fall through, because the
        # fall-through answer is `None` → False → "nothing is ever runnable",
        # which silently disables recurrence and agent dispatch across the whole
        # suite while every assertion still reads as a real refusal. That is the
        # hermetic-fake failure mode this file keeps re-learning: a fake that
        # cannot answer a query must not answer it *plausibly*.
        if re.match(r"^\s*WITH\s+RECURSIVE\s+chain\b", statement, re.I):
            pid = str(args.get("pid") or "")
            verdict: bool | None = None
            seen: set[str] = set()
            while pid and pid not in seen:
                seen.add(pid)
                row = next(
                    (r for r in self.rows("pm_projects") if str(r["id"]) == pid),
                    None,
                )
                if row is None:
                    break
                ok = (
                    str(row.get("status") or "active") == "active"
                    and row.get("archived_at") is None
                )
                verdict = ok if verdict is None else (verdict and ok)
                pid = str(row.get("parent_project_id") or "")
            return _Result([], scalar=verdict)

        # `WITH RECURSIVE sub AS (…) SELECT count(*) FROM sub` — the subtree
        # size, asked by the project delete before it destroys anything. It does
        # not start with SELECT, so the generic count path never sees it and the
        # route would have reported zero.
        if re.match(r"^\s*WITH\s+RECURSIVE\s+sub\b", statement, re.I):
            subtree = self._subtree_ids(str(args.get("pid")))
            if re.search(r"SELECT\s+count\(\*\)\s+FROM\s+sub", statement, re.I):
                return _Result([], scalar=len(subtree))
            return _Result([SimpleNamespace(id=i) for i in sorted(subtree)])

        # The importer's existing-mapping read joins pm_projects to
        # pm_project_grants. The fake models no joins, so this shape is
        # answered explicitly rather than silently returning project rows with
        # no `subject` attribute — which is how a "no previous mapping" answer
        # would look identical to a broken query.
        # The personal inbox (147/§6.1) joins pm_tasks to its status, to the
        # caller's overlay row and to the project, and computes `is_mine` and
        # `assignee_count`. That is past what a text mirror can read, so the
        # shape is answered explicitly — like the mapping join below. The
        # DISCRIMINATOR is the overlay join, which no other statement carries.
        if "LEFT JOIN pm_task_personal p" in statement:
            return _Result(self._inbox_rows(statement, args))

        # Matched on the JOIN specifically: the visibility CTE also names both
        # tables, and a looser check short-circuited every scoped read.
        if "JOIN pm_project_grants g" in statement:
            return _Result([
                SimpleNamespace(
                    space_id=project.get("clickup_id"), subject=grant.get("subject"),
                )
                for project in self.rows("pm_projects")
                for grant in self.rows("pm_project_grants")
                if project.get("clickup_kind") == "space"
                and project.get("clickup_id")
                and str(grant.get("project_id")) == str(project.get("id"))
                and str(grant.get("subject") or "").startswith("group:")
            ])

        matched = self._matching(statement, table, args)
        if statement.upper().startswith("SELECT COUNT(*)"):
            return _Result([], scalar=len(matched))
        matched = self._ordered(statement, matched)
        matched = self._paged(statement, matched, args)
        rows = [SimpleNamespace(**r) for r in matched]

        # WS-27m — `tags.list_tags` computes each tag's usage as a correlated
        # subquery in the SELECT list, which no text mirror can read; answered
        # explicitly, the same convention as the personal inbox above.
        #
        # ⚠️ Counted against `:root`, mirroring the statement rather than the
        # row. An org-wide tag (WS-27bj) has no `project_id`, so a mirror that
        # correlated on the ROW would report every org-wide tag as used by 0
        # tasks — and would then agree with the very bug that scoping prevents.
        if "AS task_count" in statement:
            # ⚠️ WHAT the count correlates on is READ OFF THE STATEMENT, not
            # assumed. Computing it from `:root` regardless would make this
            # mirror agree with `t.root_project_id = g.project_id` too — and
            # that spelling reports every org-wide tag (WS-27bj: no project_id)
            # as used by 0 tasks, which is the number people decide merges on.
            # A mirror that answers the same for both spellings is a dead fence;
            # this one was dead until a mutant walked through it.
            correlates_on_row = "t.root_project_id = g.project_id" in statement
            for row in rows:
                scope = (
                    str(row.project_id) if correlates_on_row
                    else str(args.get("root"))
                )
                row.task_count = sum(
                    1 for task in self.rows("pm_tasks")
                    if str(task.get("root_project_id")) == scope
                    and task.get("archived_at") is None
                    and row.name in list(task.get("tags") or [])
                )
        return _Result(rows)

    # visibility ---------------------------------------------------------
    def visible_project_ids(
        self, email: str, groups: list[str],
        organization_id: str | None = None,
        descendant_organization_id: str | None = None,
    ) -> set[str]:
        """The grant closure: directly granted projects, plus their descendants.

        Deliberately computed the same way the SQL does — seeds, then descend —
        rather than by walking each project's ancestry, so a subtree granted
        without its parent resolves identically in both.

        ⚠️ ``organization_id=None`` means the STATEMENT carried no tenant arm,
        not "any tenant is fine". The caller reads that off the SQL, so a route
        (or the closure itself) that loses its tenant filter stops being scoped
        here too and the cross-tenant test goes red. Defaulting it to the fake's
        own organization would have made the leak invisible.

        ⚠️ The SEED step and the DESCENT step are told apart, and they are two
        separate arguments for that reason. Inferring the second from the first
        let a mutant delete the recursive term's tenant filter and survive a
        green suite — the descent is the arm that would matter most if the
        database's parent-consistency trigger were ever dropped.
        """
        wanted = {str(g).lower() for g in groups}
        seeds = {
            str(g.get("project_id")) for g in self.rows("pm_project_grants")
            if (
                organization_id is None
                or str(g.get("organization_id")) == organization_id
            )
            and (
                g.get("subject") == "org"
                or str(g.get("subject") or "").lower() == (email or "").lower()
                or str(g.get("subject") or "").lower() in wanted
            )
        }
        out = set(seeds)
        changed = True
        while changed:
            changed = False
            for project in self.rows("pm_projects"):
                parent = project.get("parent_project_id")
                if descendant_organization_id is not None and (
                    str(project.get("organization_id"))
                    != descendant_organization_id
                ):
                    continue
                if parent is not None and str(parent) in out and str(project["id"]) not in out:
                    out.add(str(project["id"]))
                    changed = True
        return out

    def tenant_project_ids(self, organization_id: str | None) -> set[str]:
        """Every project in one organization — the `data:org:read` answer."""
        return {
            str(p["id"]) for p in self.rows("pm_projects")
            if str(p.get("organization_id")) == str(organization_id)
        }

    def _subtree_ids(self, root_id: str) -> set[str]:
        out = {str(root_id)}
        changed = True
        while changed:
            changed = False
            for project in self.rows("pm_projects"):
                parent = project.get("parent_project_id")
                if parent is not None and str(parent) in out and str(project["id"]) not in out:
                    out.add(str(project["id"]))
                    changed = True
        return out

    def _inbox_rows(self, statement: str, args: dict) -> list[Any]:
        """The personal inbox's joined shape, computed in Python.

        Mirrors the SQL's own arms rather than restating them loosely: a task is
        mine if I am assigned OR its project is my personal one — and the second
        arm is the one that keeps a self-captured task visible after I clear my
        own name off it.

        The two optional clauses are keyed off the statement text, so a route
        that stops emitting them stops being filtered here too.
        """
        who = str(args.get("who") or "").lower()
        personal_projects = {
            str(p["id"]) for p in self.rows("pm_projects")
            if str(p.get("personal_owner") or "").lower() == who
        }
        statuses = {str(s["id"]): s for s in self.rows("pm_task_statuses")}
        overlay = {
            str(o["task_id"]): o for o in self.rows("pm_task_personal")
            if str(o.get("member_email") or "").lower() == who
        }

        # Each arm is applied ONLY when the statement carries it. Applying them
        # unconditionally is what let a mutant delete the personal-project arm
        # from the SQL with every test still green — the exact failure this
        # module's docstring warns about, caught by mutation rather than review.
        wants_assigned = "lower(a.assignee) = :who" in statement
        wants_personal = "lower(proj.personal_owner) = :who" in statement
        # ⚠️ WS-29b's tenant, composed above both arms. Read off the statement
        # like everything else here: the inbox has no GRANT clause by design, so
        # this line is the ONLY thing standing between it and another
        # organization's task, and a mirror that applied it unconditionally
        # could not tell whether the route still emits it.
        tenanted = "t.organization_id = CAST(:vis_org AS uuid)" in statement
        org = str(args.get("vis_org"))

        out: list[Any] = []
        for task in self.rows("pm_tasks"):
            if task.get("archived_at") is not None:
                continue
            if tenanted and str(task.get("organization_id")) != org:
                continue
            assignees = self._assignees_of(task["id"])
            reached = (wants_assigned and who in assignees) or (
                wants_personal and str(task.get("project_id")) in personal_projects
            )
            if not reached:
                continue

            mine = overlay.get(str(task["id"]), {})
            if "p.defer_until IS NULL OR p.defer_until <= now()" in statement:
                deferred = mine.get("defer_until")
                if deferred is not None and _as_datetime(deferred) > _now():
                    continue
            if "lower(p.context) = :context" in statement:
                wanted = str(args.get("context") or "").lower()
                if str(mine.get("context") or "").lower() != wanted:
                    continue

            status = statuses.get(str(task.get("status_id")), {})
            out.append(SimpleNamespace(
                **task,
                status_category=status.get("category", ""),
                p_disposition=mine.get("disposition"),
                p_next_action=mine.get("next_action"),
                p_context=mine.get("context"),
                p_energy=mine.get("energy"),
                p_time_estimate_mins=mine.get("time_estimate_mins"),
                p_is_two_minute=bool(mine.get("is_two_minute", False)),
                p_defer_until=mine.get("defer_until"),
                # The scheduled block (187, WS-39 S3a). ⚠️ Projected here
                # because the route reads `p_*` aliases: a fake that omits an
                # alias answers None, which reads as "no block" rather than as
                # "the fake does not know", and that is a green test for a
                # feature that does not work.
                p_scheduled_start=mine.get("scheduled_start"),
                p_scheduled_end=mine.get("scheduled_end"),
                p_flexible=mine.get("flexible"),
                p_is_hard_date=mine.get("is_hard_date"),
                p_actual_start=mine.get("actual_start"),
                p_actual_end=mine.get("actual_end"),
                assignee_count=len(assignees),
                is_mine=who in assignees,
            ))
        return out

    def _assignees_of(self, task_id: str) -> set[str]:
        return {
            str(a.get("assignee") or "").lower()
            for a in self.rows("pm_task_assignees")
            if str(a.get("task_id")) == str(task_id)
        }

    # predicate ----------------------------------------------------------
    def _matching(
        self, statement: str, table: str, args: dict,
    ) -> list[dict[str, Any]]:
        """The rows a statement's WHERE addresses.

        Split in two — subquery blocks, then plain column predicates — because
        the two read different halves of the clause and mixing them made one
        function nobody could follow. Either half may be the one that "saw" a
        readable clause; only if NEITHER did do we refuse.
        """
        rows = list(self.tables.get(table, []))
        where = _where_clause(statement)
        if where is None:
            return rows
        top, blocks = _strip_subqueries(where)

        rows, seen_sub = self._apply_subqueries(rows, table, blocks, where, args)
        rows, seen_top = self._apply_columns(rows, top, args)

        if not (seen_sub or seen_top):
            raise AssertionError(
                f"fake could not read the WHERE clause — refusing to match every "
                f"row of {table}: {statement}"
            )
        return rows

    def _apply_subqueries(
        self, rows: list[dict], table: str, blocks: list[str], where: str,
        args: dict,
    ) -> tuple[list[dict], bool]:
        seen = False

        # ── WS-27bj — the effective-vocabulary scope (`core.vocabulary_scope`)
        #
        # `org-wide ∪ root-local`, with the org arm anchored on the ROOT
        # PROJECT's tenant. Modelled as the UNION it is: the generic column
        # scanner would read `project_id = :root` and `project_id IS NULL` as an
        # AND and match nothing, so every vocabulary list would come back empty
        # and every test would fail for a reason that is not the code's.
        #
        # ⚠️ Keyed on the tenant subselect, never assumed. A route that keeps
        # `project_id = :root` and drops the org arm stops matching org-wide
        # rows here too, so the union tests go red rather than this mirror
        # inventing a feature the SQL no longer has.
        if any(_PROJECT_TENANT_SUBSELECT.search(b) for b in blocks):
            seen = True
            root = str(args.get("root"))
            root_org = next(
                (str(p.get("organization_id"))
                 for p in self.rows("pm_projects") if str(p.get("id")) == root),
                None,
            )
            in_tenant = [
                r for r in rows
                if root_org is not None
                and str(r.get("organization_id")) == root_org
            ]
            if any(_VOCABULARY_UNION.search(b) for b in blocks):
                rows = [
                    r for r in rows
                    if str(r.get("project_id")) == root
                    or (r.get("project_id") is None and r in in_tenant)
                ]
            else:
                # `core.org_wide_exists` and the org-wide re-read: the tenant
                # subselect without the union. `project_id IS NULL` and the
                # identity comparison are plain columns the scanner handles.
                rows = in_tenant

        # ⚠️ The tenant, composed ABOVE the grant closure (WS-29b). Read from
        # the statement with the closure's own body removed first, because that
        # body ALSO says `organization_id` and the two predicates are about
        # different tables.
        #
        # This is what scopes `load_visible_task`'s assignee escape hatch and
        # the whole of the unrestricted (`data:org:read`) task clause. Delete
        # either from the route and this stops applying, which is the point.
        if _ROW_TENANT.search(_CLOSURE_BODY.sub("", where)):
            seen = True
            org = str(args.get("vis_org"))
            rows = [r for r in rows if str(r.get("organization_id")) == org]

        # `data:org:read` — every project in ONE organization, grants ignored.
        if any(_TENANT_PROJECTS.search(b) for b in blocks):
            seen = True
            column_match = _IN_TENANT_SUBQUERY.search(where)
            column = column_match.group(1) if column_match else "id"
            tenant = self.tenant_project_ids(args.get("vis_org"))
            rows = [r for r in rows if str(r.get(column)) in tenant]

        # The grant closure — applied ONLY when the statement actually carries
        # the subquery. A route that loses its visibility clause therefore stops
        # being filtered here, and its 404 test fails.
        if any("pm_project_grants" in b for b in blocks):
            seen = True
            # ⚠️ And whether the CLOSURE is tenant-scoped is read off the
            # closure's own text, not assumed. `subject = 'org'` means
            # "everybody"; only `g.organization_id = :vis_org` makes it mean
            # "everybody in this organization". Assuming it were always there
            # is exactly how the leak §6 names would pass a green suite.
            visible = self.visible_project_ids(
                str(args.get("vis_email") or ""), list(args.get("vis_groups") or []),
                organization_id=(
                    str(args.get("vis_org"))
                    if _CLOSURE_IS_TENANTED.search(where) else None
                ),
                descendant_organization_id=(
                    str(args.get("vis_org"))
                    if _DESCENT_IS_TENANTED.search(where) else None
                ),
            )
            column_match = _IN_SUBQUERY.search(where)
            column = column_match.group(1) if column_match else "id"
            assignee_escape = any(
                "pm_task_assignees" in b and "vis_email" in b for b in blocks
            )
            me = str(args.get("vis_email") or "").lower()
            rows = [
                r for r in rows
                if str(r.get(column)) in visible
                or (assignee_escape and me in self._assignees_of(r.get("id")))
            ]

        # `WITH RECURSIVE sub` — a project-subtree filter, used by the task list,
        # the move re-stamp and the delete counts.
        if any("RECURSIVE sub" in b for b in blocks):
            seen = True
            subtree = self._subtree_ids(str(args.get("pid")))
            key = "id" if table == "pm_projects" else "project_id"
            rows = [r for r in rows if str(r.get(key)) in subtree]

        # EXISTS over pm_task_assignees with a non-visibility parameter: the
        # `?assignee=` list filter and /assigned-to-me.
        for block in blocks:
            if "pm_task_assignees" not in block or "vis_email" in block:
                continue
            bound = [b for b in _BOUND_RE.findall(block) if b != "tid"]
            if not bound:
                continue
            seen = True
            who = str(args.get(bound[-1]) or "").lower()
            rows = [r for r in rows if who in self._assignees_of(r.get("id"))]

        # WS-27u — the default-list exclusion: `NOT EXISTS` over a
        # triage-category status (`core.triage_exclusion_clause`). Applied only
        # when the statement carries the `s_triage` alias, so a surface that
        # drops the predicate — or silently drops `include_triage` — leaks the
        # queue here too and the exclusion tests go red.
        if any("s_triage" in b for b in blocks):
            seen = True
            parked = {
                str(s["id"]) for s in self.rows("pm_task_statuses")
                if s.get("category") == "triage"
            }
            rows = [r for r in rows if str(r.get("status_id")) not in parked]

        # ⚠️ WS-27k's POSITIVE category filter: `EXISTS (… s.category =
        # ANY(:categories))`. It has to be told apart from the two NEGATIVE
        # category clauses below, and it was not until WS-27ae — the branch
        # underneath matched any block naming `pm_task_statuses` and
        # `category`, so `?status_category=` was silently read as "hide closed
        # work". Every behavioural test in the tree happened to filter on
        # `todo`, where the two answers coincide; `status_category=done`
        # returned the OPEN tasks. Keyed on the bound parameter, which only
        # this clause carries.
        if any(":categories" in b for b in blocks):
            seen = True
            wanted_categories = set(args.get("categories") or [])
            by_category = {
                str(s["id"]) for s in self.rows("pm_task_statuses")
                if s.get("category") in wanted_categories
            }
            rows = [r for r in rows if str(r.get("status_id")) in by_category]

        # NOT EXISTS over pm_task_statuses — /assigned-to-me hiding closed work
        # (`IN ('done', 'cancelled')`) and the `overdue` filter's still-open
        # half (`= ANY(:closed)`). The triage predicate above and the positive
        # filter here name the same table, so both are excluded by their own
        # fingerprints and this branch keys on the closed vocabulary itself.
        if any(
            "pm_task_statuses" in b and "s_triage" not in b
            and ("'done', 'cancelled'" in b or ":closed" in b)
            for b in blocks
        ):
            seen = True
            closed = {
                str(s["id"]) for s in self.rows("pm_task_statuses")
                if s.get("category") in ("done", "cancelled")
            }
            rows = [r for r in rows if str(r.get("status_id")) not in closed]

        return rows, seen

    def _apply_columns(
        self, rows: list[dict], top: str, args: dict,
    ) -> tuple[list[dict], bool]:
        seen = False
        # Consumed FIRST and removed from `top`, or the generic scanner below
        # would read the two arms as an AND and match nothing.
        either = _EITHER_END.search(top)
        if either:
            seen = True
            a_column, a_param, b_column, b_param = either.groups()
            rows = [
                r for r in rows
                if str(r.get(a_column)) == str(args.get(a_param))
                or str(r.get(b_column)) == str(args.get(b_param))
            ]
            top = _EITHER_END.sub(" ", top)
        for column, param in _UUID_EQ.findall(top):
            seen = True
            rows = [r for r in rows if str(r.get(column)) == str(args.get(param))]
        for column, param in _LOWER_EQ.findall(top):
            seen = True
            want = str(args.get(param) or "").lower()
            rows = [r for r in rows if str(r.get(column) or "").lower() == want]
        for column, param in _ANY_UUID.findall(top):
            seen = True
            wanted_ids = {str(v) for v in (args.get(param) or [])}
            rows = [r for r in rows if str(r.get(column)) in wanted_ids]
        for column, param in _PLAIN_EQ.findall(top):
            seen = True
            rows = [r for r in rows if r.get(column) == args.get(param)]
        for column, literal in _LITERAL_EQ.findall(top):
            seen = True
            rows = [r for r in rows if str(r.get(column)) == literal]
        for column, negated in _IS_NULL.findall(top):
            seen = True
            rows = [r for r in rows if (r.get(column) is not None) is bool(negated)]
        ilike = _ILIKE.findall(top)
        if ilike:
            seen = True
            needle = str(args.get(ilike[0][1]) or "").strip("%").lower()
            rows = [
                r for r in rows
                if any(needle in str(r.get(c) or "").lower() for c, _ in ilike)
            ]
        if re.search(r"\bAND\s+is_default\b", top, re.I):
            seen = True
            rows = [r for r in rows if r.get("is_default")]
        for column in _NOW_LT.findall(top):
            seen = True
            rows = [
                r for r in rows
                if r.get(column) is not None and _as_datetime(r[column]) < _now()
            ]
        # WS-27z — `<col> < :param` with SQL's NULL semantics (a NULL column
        # never matches). Datetimes compare as instants however they were
        # stored; anything else falls back to `_sortable`'s total order.
        for column, param in _BOUND_LT.findall(top):
            if param not in args:
                continue
            seen = True
            edge = args[param]
            if isinstance(edge, datetime):
                rows = [
                    r for r in rows
                    if r.get(column) is not None
                    and _as_datetime(r[column]) < edge
                ]
            else:
                rows = [
                    r for r in rows
                    if r.get(column) is not None
                    and _sortable(r.get(column)) < _sortable(edge)
                ]
        # WS-27ae — the delta feed's horizon (`<col> <= :param`). SQL's NULL
        # semantics: a NULL column never matches.
        for column, param in _BOUND_LTE.findall(top):
            if param not in args:
                continue
            seen = True
            edge = _as_datetime(args[param])
            rows = [
                r for r in rows
                if r.get(column) is not None and _as_datetime(r[column]) <= edge
            ]
        # WS-27ae — the delta feed's keyset cursor, modelled as the ROW
        # COMPARISON it is. The columns and the bound names are read off the
        # statement, so a route that compares the wrong pair — or drops the id
        # half back to a bare timestamp — changes this mirror's answer instead
        # of being invisible to it.
        keyset = _KEYSET_GT.search(top)
        if keyset and keyset.group(3) in args:
            seen = True
            ts_column, id_column, ts_param, id_param = keyset.groups()
            edge = (_as_datetime(args[ts_param]), str(args[id_param]))
            rows = [
                r for r in rows
                if r.get(ts_column) is not None
                and (_as_datetime(r[ts_column]), str(r.get(id_column))) > edge
            ]
        # WS-27q's calendar window. Applied ONLY when the statement carries the
        # bound, and each comparison is evaluated against the interval endpoint
        # the SQL's own `coalesce` order names — so swapping that order, which
        # is the mutation that turns "overlaps" back into "due inside", changes
        # this mirror's answer instead of being invisible to it.
        window = _WINDOW_CMP.findall(top)
        if window:
            seen = True
            for expr, operator, bound in window:
                edge = _as_datetime(args[bound])
                rows = [
                    r for r in rows
                    if _compare_window(_coalesced(r, expr), operator, edge)
                ]
        if re.search(r"\bTRUE\b", top, re.I):
            # The unrestricted (`data:org:read`) form of the visibility clause.
            # It is a readable clause that filters nothing, which is different
            # from a clause the fake failed to parse — hence `seen`, not a skip.
            seen = True
        return rows, seen

    def _ordered(self, statement: str, rows: list[dict]) -> list[dict]:
        # WS-27w's semantic status sort: category rank, then lane position,
        # then the `(created_at, id)` tiebreaker. The RANK ORDER is read off
        # the statement's own ARRAY literal rather than assumed, so a route
        # that reordered — or alphabetised — the vocabulary changes this
        # mirror's answer instead of being invisible to it.
        if "array_position" in statement and "s.category" in statement:
            literal = re.search(r"ARRAY\[([^\]]*)\]", statement)
            rank = [
                v.strip().strip("'")
                for v in (literal.group(1).split(",") if literal else [])
            ]
            reverse = bool(re.search(r"t\.id\s+DESC", statement, re.I))
            uses_position = "s.position" in statement
            statuses = {str(s["id"]): s for s in self.rows("pm_task_statuses")}

            def status_key(row: dict) -> tuple:
                status = statuses.get(str(row.get("status_id")), {})
                category = str(status.get("category") or "")
                return (
                    rank.index(category) if category in rank else len(rank),
                    _sortable(status.get("position")) if uses_position else 0,
                    _sortable(row.get("created_at")),
                    str(row.get("id")),
                )

            return sorted(rows, key=status_key, reverse=reverse)
        # WS-27ae — the delta feed's total order, `(<stamp>, <id>)` ascending.
        # It is the SAME pair the cursor encodes, so a mirror that ordered on
        # the stamp alone would break ties differently from Postgres and the
        # boundary cases would pass or fail by luck.
        pair = re.search(
            r"ORDER\s+BY\s+(?:\w+\.)?(\w+)\s+ASC\s*,\s*(?:\w+\.)?(\w+)\s+ASC",
            statement, re.I,
        )
        if pair:
            first, second = pair.groups()
            return sorted(
                rows,
                key=lambda r: (_sortable(r.get(first)), str(r.get(second))),
            )
        order = _ORDER_RE.search(statement)
        if order is None:
            return rows
        column = order.group(1)
        reverse = (order.group(2) or "ASC").upper() == "DESC"
        return sorted(
            rows,
            key=lambda r: (r.get(column) is None, _sortable(r.get(column))),
            reverse=reverse,
        )

    def _paged(self, statement: str, rows: list[dict], args: dict) -> list[dict]:
        offset = _OFFSET_RE.search(statement)
        if offset:
            rows = rows[int(args.get(offset.group(1), 0)):]
        limit = _LIMIT_RE.search(statement)
        if limit:
            rows = rows[: int(args.get(limit.group(1), len(rows)))]
        elif re.search(r"\bLIMIT\s+1\b", statement, re.I):
            rows = rows[:1]
        return rows


def _as_datetime(value: Any) -> datetime:
    """A stored `defer_until` as an aware instant, however it was written."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _coalesced(row: dict, expression: str) -> datetime | None:
    """What one ``coalesce(...)`` from the window clause evaluates to.

    The argument ORDER is read off the SQL rather than assumed, because that
    order is the whole rule: `coalesce(start_date, due_at)` is the interval's
    start and `coalesce(due_at, start_date)` is its end, and a mirror that
    hard-coded either would agree with a route that swapped them.
    """
    for part in expression.split(","):
        column = "start_date" if "start_date" in part else "due_at"
        value = row.get(column)
        if value is not None:
            return _as_datetime(value)
    return None


def _compare_window(value: datetime | None, operator: str, edge: datetime) -> bool:
    """One window comparison, with SQL's NULL semantics.

    A task with neither date has no interval, so both comparisons are NULL and
    the row is not matched — the behaviour the endpoint relies on to keep
    undated tasks off the calendar without a clause anybody can see.
    """
    if value is None:
        return False
    return value < edge if operator == "<" else value >= edge


def _sortable(value: Any) -> Any:
    """A total order across the mixed types one column can hold in a fake."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, bool | int | float):
        return float(value)
    return str(value)


# ── Principals ──────────────────────────────────────────────────────────────

def projects_user(
    email: str = "owner@fracktal.in", *, features: str = "*",
) -> Any:
    """A caller holding ``feature:projects``.

    Built with the real ``build_access`` so the permission the routes are gated
    on is the one this principal actually resolves.
    """
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access([features]),
    )


def member_user(email: str) -> Any:
    """A colleague holding the feature but **not** ``data:org:read``.

    This is the principal every scoping test needs: without it a suite proves
    only that the owner (who holds ``*``) can see everything, which is true of
    an unscoped route too.
    """
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:projects"]),
    )


def page(number: int = 1, size: int = 50) -> Any:
    """A ``core.Page`` built without FastAPI, with the same defaults.

    ``_crm_fakes``' ``_params()`` convention: the parameter defaults in the
    signature are FastAPI ``Query`` objects, so a handler called directly must
    be handed a real one rather than left to resolve it.
    """
    from gateway.routes.projects.core import Page

    return Page(page=number, page_size=size)


def bind_db(monkeypatch: Any, fake: FakeProjectsDB, modules: tuple[Any, ...]) -> None:
    """Point each submodule's ``_tenant_session`` seam at ``fake``.

    Per-module and not per-package because each module imports the seam from
    ``core`` by name, so patching ``core`` alone would not reach them.

    H2 note: the real seam is ``acb_common.db.tenant_session`` — an async
    context manager that begins a transaction, issues ``SET LOCAL
    app.tenant_id`` and commits on exit. The fake mirrors only the SHAPE
    (``async with … as db``); transaction semantics stay out of the mirror
    exactly as ``commit``/``close`` no-ops did before, because what these
    tests pin is SQL behaviour, not the GUC plumbing —
    ``test_tenant_session.py`` owns that.

    ⚠️ One thing the mirror DOES record: the argument. ``fake.bound_tenants``
    collects what each ``_tenant_session(...)`` was opened with, so a test can
    assert an EXPLICIT tenant was passed (WS-27aa / H4) rather than the
    ambient no-argument form. A fake that swallowed the argument would agree
    with either, which is exactly the class of blindness the live harnesses
    exist for — ``tests/live/live_ws27aa.py`` proves the rows.
    """
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None) -> Any:
        # Commit-on-clean-exit, exactly like the real wrapper — so "these
        # writes share one transaction" stays an OBSERVABLE fact
        # (`db.committed == 1`) rather than a comment, and a handler that
        # raises mid-block commits nothing here just as it commits nothing
        # against Postgres.
        fake.bound_tenants.append(organization_id)
        yield fake
        await fake.commit()

    async def _get_db() -> FakeProjectsDB:
        return fake

    for module in modules:
        if hasattr(module, "_tenant_session"):
            monkeypatch.setattr(module, "_tenant_session", _tenant_session)
        else:  # pragma: no cover — no Projects module is on `_get_db` today
            # Kept for the shape, not for a current caller: WS-27aa retired
            # the package's last unbound site (`agent_dispatch`, which now
            # takes its tenant off the event payload). A module that reappears
            # here is a new H4 exemption and needs the ratchet entry to match.
            monkeypatch.setattr(module, "_get_db", _get_db)


def silence_events(monkeypatch: Any, modules: tuple[Any, ...]) -> list[tuple[str, dict]]:
    """Capture ``core.emit`` calls instead of reaching the event bus.

    Returned list is ``(event_type, payload)`` in order, so a test can assert
    that a mutation announced itself — WS-27f's automation binding is built on
    these events, and a write that silently stops emitting would break it with
    every route test still green.
    """
    captured: list[tuple[str, dict]] = []

    async def _emit(event_type: str, payload: dict) -> None:
        captured.append((event_type, payload))

    for module in modules:
        # raising=False: only the modules that actually mutate import `emit`.
        # Demanding it everywhere would make adding a read-only module fail a
        # test that has nothing to do with it.
        monkeypatch.setattr(module, "emit", _emit, raising=False)
    return captured
