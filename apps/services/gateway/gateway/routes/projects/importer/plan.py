"""The dry run — what an import WOULD write, and what the admin must decide.

Spec: ``project_import.md`` §3.1 (the dry run), §6.2 (people), §6.3
(statuses), §6.6 (completion dates), §6.9 (work that exists) · D80 · WS-41 I-2.

A pure function. It takes the bundle, the admin's mapping, the member
directory and the source ids that already exist, and returns a JSON-ready
dict. It reads nothing and writes nothing: the route does the two reads and
passes the answers in, so ``test_import_plan.py`` can prove the plan never
touches the database by never giving it one.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Literal

from gateway.routes.projects.importer.bundle import ImportBundle
from pydantic import BaseModel, Field, field_validator

Category = Literal["backlog", "todo", "in_progress", "done", "cancelled"]
CLOSED: frozenset[str] = frozenset({"done", "cancelled"})

#: §6.3 — proposed category by the words in a status name, first match wins.
#: "cancel" comes before "done", so "Cancelled - done" reads as cancelled.
_PROPOSALS: tuple[tuple[re.Pattern[str], Category], ...] = (
    (re.compile(r"cancel|won'?t|wont|rejected|dropped"), "cancelled"),
    (re.compile(r"\b(done|complete|completed|closed|shipped|resolved|finished)\b"), "done"),
    (re.compile(r"backlog|hold|someday|later|parked|icebox"), "backlog"),
    (re.compile(r"^(to ?do|open|new|not started|pending)$"), "todo"),
)

#: A group grant is ``group:<slug>`` with the slug grammar of ``lib/centers.ts``.
_GRANT = re.compile(r"^(org|group:[a-z][a-z0-9-]{0,62})$")
MAX_NAME = 120


def propose_category(name: str) -> Category:
    folded = " ".join(name.lower().split())
    for pattern, category in _PROPOSALS:
        if pattern.search(folded):
            return category
    return "in_progress"


# ── the admin's choices ─────────────────────────────────────────────────────


class StatusChoice(BaseModel):
    """What one source status name becomes. Two names with one ``name``
    merge into one Metorite status (§6.3, "one name, one status")."""

    category: Category
    name: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if not value or len(value) > 64:
            raise ValueError("a status name holds 1 to 64 characters")
        return value


class Target(BaseModel):
    kind: Literal["new_space", "existing"] = "new_space"
    #: The new space's name. ``None`` takes the source space name.
    name: str | None = None
    #: The existing space, for ``kind='existing'``.
    project_id: str | None = None


class ImportMapping(BaseModel):
    #: Person ref → a member's email, or ``None`` for "leave unassigned".
    #: A ref absent from the map takes the proposal.
    people: dict[str, str | None] = Field(default_factory=dict)
    #: Source status name → the choice. Absent takes the proposal.
    statuses: dict[str, StatusChoice] = Field(default_factory=dict)
    target: Target = Field(default_factory=Target)
    grant: str = "org"


# ── the plan ────────────────────────────────────────────────────────────────


def build_plan(
    bundle: ImportBundle,
    mapping: ImportMapping,
    directory: dict[str, str],
    existing_refs: set[str] | None = None,
    legacy_refs: set[str] | None = None,
    target_ok: bool = True,
) -> dict[str, Any]:
    """The dry run.

    ``directory`` maps each active member's lowercased email to their name,
    in THIS organization only (§6.2 rule 3). ``existing_refs`` are the source
    ids this organization already imported, and ``legacy_refs`` the ones the
    pre-D52 importer wrote into ``pm_tasks.clickup_id`` (§11 Q-7). Both are
    skipped at apply, so the plan counts them. ``target_ok`` is the route's
    answer to "may this admin write into the chosen existing space".
    """
    existing_refs = existing_refs or set()
    legacy_refs = legacy_refs or set()
    errors: list[str] = []

    people = _people(bundle, mapping, directory, errors)
    statuses, final = _statuses(bundle, mapping, errors)
    tree = _tree(bundle)

    closed = [t for t in bundle.tasks if t.status_name and final[t.status_name][1] in CLOSED]
    skipped = {t.ref for t in bundle.tasks if t.ref in existing_refs or t.ref in legacy_refs}

    # D79: every status set keeps a Done status. Each imported project owns
    # its set (§6.3), so count the projects whose mapped set has none.
    per_project: dict[str, set[str]] = defaultdict(set)
    for t in bundle.tasks:
        if t.status_name:
            per_project[t.container_ref].add(final[t.status_name][1])
    projects = [c.ref for c in bundle.containers if c.kind == "project"]
    done_added = sum(1 for ref in projects if "done" not in per_project.get(ref, set()))

    target = mapping.target
    if target.kind == "existing" and not target.project_id:
        errors.append("Choose the existing space to import into.")
    if target.kind == "existing" and target.project_id and not target_ok:
        errors.append("You cannot import into that space.")
    if target.name is not None and not (0 < len(target.name.strip()) <= MAX_NAME):
        errors.append(f"A space name holds 1 to {MAX_NAME} characters.")
    if not _GRANT.match(mapping.grant):
        errors.append("Share the space with the whole organization or with one group.")

    return {
        "summary": bundle.summary(),
        "utc_offset_minutes": (
            int(bundle.utc_offset.total_seconds() // 60) if bundle.utc_offset is not None else None
        ),
        "encoding": bundle.encoding,
        "warnings": [w.model_dump() for w in bundle.warnings],
        "losses": [loss.model_dump() for loss in bundle.losses],
        "people": people,
        "statuses": statuses,
        "tree": tree,
        "closed_tasks": len(closed),
        # §6.6 — the file carries no completion date, so every closed task
        # gets an estimated one. Never the import time.
        "completed_at_estimated": sum(1 for t in closed if t.completed_at is None),
        "done_status_added": done_added,
        "skip": {
            "already_imported": len(existing_refs & {t.ref for t in bundle.tasks}),
            "written_by_old_importer": len(legacy_refs & {t.ref for t in bundle.tasks}),
            "total": len(skipped),
        },
        "to_write": {
            "tasks": len(bundle.tasks) - len(skipped),
            "comments": sum(1 for c in bundle.comments if c.task_ref not in skipped),
        },
        "target": target.model_dump(),
        "grant": mapping.grant,
        "errors": errors,
        "ready": not errors,
    }


def _people(
    bundle: ImportBundle,
    mapping: ImportMapping,
    directory: dict[str, str],
    errors: list[str],
) -> list[dict[str, Any]]:
    """§6.2 — propose a member for each person, email first, then an exact
    full name that only ONE member carries. Never invite, never create."""
    by_name: dict[str, list[str]] = defaultdict(list)
    for email, name in directory.items():
        if name:
            by_name[" ".join(name.lower().split())].append(email)

    tasks = Counter(r for t in bundle.tasks for r in t.assignee_refs)
    comments = Counter(c.author_ref for c in bundle.comments if c.author_ref)
    out: list[dict[str, Any]] = []
    for person in bundle.people:
        proposal, how = None, None
        if person.email and person.email in directory:
            proposal, how = person.email, "email"
        else:
            same = by_name.get(" ".join(person.display_name.lower().split()), [])
            if len(same) == 1:
                proposal, how = same[0], "name"
            elif len(same) > 1:
                how = "ambiguous"
        chosen = mapping.people.get(person.ref, proposal)
        if chosen is not None:
            chosen = chosen.strip().lower()
            if chosen not in directory:
                errors.append(
                    f"{person.display_name}: {chosen} is not a member of this organization."
                )
        out.append(
            {
                "ref": person.ref,
                "display_name": person.display_name,
                "email": person.email,
                "tasks": tasks.get(person.ref, 0),
                "comments": comments.get(person.ref, 0),
                "proposed": proposal,
                "match": how,
                "member": chosen,
            }
        )
    out.sort(key=lambda p: (-p["tasks"] - p["comments"], p["display_name"].lower()))
    return out


def _statuses(
    bundle: ImportBundle,
    mapping: ImportMapping,
    errors: list[str],
) -> tuple[list[dict[str, Any]], dict[str, tuple[str, Category]]]:
    """§6.3 — one row per distinct source name, across every List. Returns the
    rows and ``source name → (Metorite name, category)``."""
    tasks: Counter[str] = Counter()
    lists: dict[str, set[str]] = defaultdict(set)
    order: list[str] = []
    for seen in bundle.statuses:
        if seen.name not in tasks:
            order.append(seen.name)
        tasks[seen.name] += seen.task_count
        lists[seen.name].add(seen.container_ref)

    final: dict[str, tuple[str, Category]] = {}
    rows = []
    for name in order:
        proposed = propose_category(name)
        choice = mapping.statuses.get(name)
        category = choice.category if choice else proposed
        target_name = choice.name if choice and choice.name else name
        final[name] = (target_name, category)
        rows.append(
            {
                "name": name,
                "tasks": tasks[name],
                "lists": len(lists[name]),
                "proposed": proposed,
                "category": category,
                "becomes": target_name,
            }
        )

    # Two source names merged into one Metorite name must agree on the category.
    merged: dict[str, set[str]] = defaultdict(set)
    for target_name, category in final.values():
        merged[target_name.lower()].add(category)
    for name, categories in sorted(merged.items()):
        if len(categories) > 1:
            errors.append(f'The statuses merged into "{name}" have different stages.')
    return rows, final


def _tree(bundle: ImportBundle) -> list[dict[str, Any]]:
    counts = Counter(t.container_ref for t in bundle.tasks)
    return [
        {
            "ref": c.ref,
            "kind": c.kind,
            "name": c.name,
            "parent_ref": c.parent_ref,
            "tasks": counts.get(c.ref, 0),
        }
        for c in bundle.containers
    ]


# ── what the writer reads ───────────────────────────────────────────────────
#
# The writer (I-3) resolves people and statuses through THESE, so it writes
# exactly what the dry run showed the admin. A second resolution in the writer
# could disagree with the plan the admin confirmed.


def resolve_people(
    bundle: ImportBundle, mapping: ImportMapping, directory: dict[str, str]
) -> dict[str, str | None]:
    """Person ref → the member it lands on, or ``None`` for unassigned. A
    choice outside the directory resolves to ``None``; the plan already
    refused it, so the writer never sees one unless the directory changed."""
    rows = _people(bundle, mapping, directory, [])
    return {r["ref"]: (r["member"] if r["member"] in directory else None) for r in rows}


def resolve_statuses(
    bundle: ImportBundle, mapping: ImportMapping
) -> dict[str, tuple[str, Category]]:
    """Source status name → (Metorite status name, stage)."""
    return _statuses(bundle, mapping, [])[1]
