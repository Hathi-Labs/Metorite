"""Projects · report scope — who a reader may report on (WS-27bn R5a).

Spec: ``project-docs/specs/projects_reports.md`` §7.1 and §8 R5a.

**The reader's own role decides whose report they may open.** An admin (the
``admin:members:read`` grant) reports on everybody. A lead of an `org_group`
reports on themselves, on each team they lead, and on each member of those
teams. Any other member reports on themselves only.

This module is the ONE home of that rule. It holds:

* :func:`normalise_subject` — the SHAPE of ``config.subject``, with no
  database, because ``normalise_report_config`` runs it on every read;
* :func:`reader_scope`, :func:`may_report_on` and :func:`reportable_people`
  — the rule itself;
* :func:`resolve_subject` — the directory check (422), the rule (403) and the
  expansion of a team to its active members, at render time;
* :func:`subject_clause` and :func:`subject_params` — the task predicate that
  each body ANDs into its scope;
* :func:`filter_person_rows` and :func:`filter_conflict_rows` — the row
  filter that runs AFTER a body.

⚠️ **It is not in ``reports.py``**, because ``reports.py`` imports the
analytics modules and the analytics routes call the rule too. A copy of any
function here in a second module is a defect: the picker, the render and the
Analytics panels would then disagree about one reader.

⚠️ **A team is an `org_group`, expanded by the one helper in
``routes/admin/groups.py``** (:func:`~gateway.routes.admin.groups.active_memberships`).
`org_group` has no row security, so that helper states the organization in
its SQL. The Center groups (``sales``, ``company`` and the others) are
`org_group` rows too, so a lead of a company-wide group may report on
everybody in it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from gateway.routes.tasks.core import can_read_hr_fields
from sqlalchemy import text

#: The kinds of subject a report may name.
SUBJECT_KINDS: tuple[str, ...] = ("person", "team")

#: The sections that refuse a subject, and the words of the refusal.
NO_SUBJECT_SECTIONS: tuple[str, ...] = ("outlook", "hygiene")
NO_SUBJECT_REASON = "not available for a person or team scope"

#: The conflict kinds that are about a PAIR of tasks, not about one person.
#: Their rows keep their holders for every reader (§8 R5a): the reader can
#: open each task and see its assignees.
DEPENDENCY_KINDS: frozenset[str] = frozenset({"dependency_order", "blocker_late"})

_SLUG_MAX = 48

#: What the admin grant is called, in a refusal a member reads.
_ADMIN_WORDS = "the admin grant (admin:members:read)"


def _refuse(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def normalise_subject(raw: Any) -> dict[str, str] | None:
    """The shape of ``config.subject``, or 422. It never reads the database.

    ``{"kind": "person", "email": ...}`` or ``{"kind": "team", "slug": ...}``.
    ``None`` means no subject, and a config with no subject renders as it did
    before R5a. The email is lowercased and trimmed, the form
    `pm_task_assignees` is compared in.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _refuse("config.subject must be an object.")
    kind = raw.get("kind")
    if kind not in SUBJECT_KINDS:
        raise _refuse(
            f"config.subject.kind must be one of: {', '.join(SUBJECT_KINDS)}."
        )
    if kind == "person":
        email = str(raw.get("email") or "").strip().lower()
        if not email or "@" not in email or email.startswith("agent:"):
            raise _refuse("config.subject.email must be the address of a person.")
        return {"kind": "person", "email": email}
    slug = str(raw.get("slug") or "").strip().lower()
    if (
        not slug
        or len(slug) > _SLUG_MAX
        or not all(c.isalnum() or c in "_-" for c in slug)
    ):
        raise _refuse("config.subject.slug must be the slug of a team.")
    return {"kind": "team", "slug": slug}


def _me(user: Any) -> str:
    return str(getattr(user, "email", "") or "").strip().lower()


@dataclass(frozen=True)
class ReaderScope:
    """What one reader may report on, read once for one request.

    ``everyone`` is the admin row of §7.1. Otherwise ``people`` is the reader
    and each member of each team they lead, and ``teams`` is those teams.
    """

    everyone: bool
    me: str
    people: frozenset[str]
    teams: frozenset[str]

    @property
    def people_filter(self) -> frozenset[str] | None:
        """The addresses whose rows a reader may see. ``None`` is everyone."""
        return None if self.everyone else self.people

    def allows(self, subject: dict[str, str] | None) -> tuple[bool, str]:
        """``(True, "")``, or ``(False, reason)``. The reason names the role
        that would allow it (§7.1 rule 2)."""
        if subject is None or self.everyone:
            return True, ""
        if subject["kind"] == "person":
            email = subject["email"]
            if email in self.people:
                return True, ""
            own = (
                "You may report on yourself and on the members of the teams"
                " that you lead."
                if self.teams else "You may report on yourself only."
            )
            return False, (
                f"{own} A report on {email} needs {_ADMIN_WORDS}, or the lead"
                " role in a team of that person."
            )
        slug = subject["slug"]
        if slug in self.teams:
            return True, ""
        return False, (
            f"A report on the team {slug} needs {_ADMIN_WORDS}, or the lead"
            " role in that team."
        )


async def reader_scope(db: Any, user: Any, vis: Any) -> ReaderScope:
    """The reader's scope, from their grant and their `lead` rows."""
    me = _me(user)
    if can_read_hr_fields(user):
        return ReaderScope(everyone=True, me=me, people=frozenset(),
                           teams=frozenset())
    from gateway.routes.admin.groups import active_memberships

    rows = await active_memberships(db, getattr(vis, "organization_id", None))
    led = {slug for slug, email, role in rows if email == me and role == "lead"}
    people = {me} | {email for slug, email, _ in rows if slug in led}
    return ReaderScope(everyone=False, me=me, people=frozenset(people),
                       teams=frozenset(led))


async def may_report_on(
    db: Any, user: Any, vis: Any, subject: dict[str, str] | None,
) -> tuple[bool, str]:
    """May this reader open a report on ``subject``? ``(ok, reason)``."""
    return (await reader_scope(db, user, vis)).allows(subject)


async def reportable_people(db: Any, user: Any, vis: Any) -> frozenset[str] | None:
    """The addresses whose rows this reader may see. ``None`` means everyone."""
    return (await reader_scope(db, user, vis)).people_filter


async def check_in_directory(db: Any, vis: Any, subject: dict[str, str]) -> None:
    """422 unless the subject is in the READER's organization.

    A person must be an active `app_user` of that organization, and a team an
    `org_group` slug of it. ⚠️ Both statements name the organization, because
    `org_group` has no row security and `app_user.email` is unique across
    every organization. A NULL organization matches nothing.
    """
    org = getattr(vis, "organization_id", None)
    if subject["kind"] == "person":
        found = (await db.execute(
            text(
                "SELECT 1 FROM app_user"
                " WHERE lower(btrim(email)) = :e AND status = 'active'"
                "   AND organization_id = CAST(:org AS uuid) LIMIT 1"
            ),
            {"e": subject["email"], "org": org},
        )).fetchone()
        if found is None:
            raise _refuse(
                f"{subject['email']} is not an active member of this"
                " organization."
            )
        return
    found = (await db.execute(
        text(
            "SELECT 1 FROM org_group"
            " WHERE organization_id = CAST(:org AS uuid) AND slug = :s LIMIT 1"
        ),
        {"s": subject["slug"], "org": org},
    )).fetchone()
    if found is None:
        raise _refuse(f"{subject['slug']} is not a team of this organization.")


async def require_subject(
    db: Any, user: Any, vis: Any, subject: dict[str, str] | None,
) -> None:
    """The directory (422), then the rule (403). Create and patch call it."""
    if subject is None:
        return
    await check_in_directory(db, vis, subject)
    ok, reason = await may_report_on(db, user, vis, subject)
    if not ok:
        raise HTTPException(status_code=403, detail=reason)


async def resolve_subject(
    db: Any, user: Any, vis: Any, subject: dict[str, str] | None,
) -> list[str] | None:
    """The subject's people, at render time, or ``None`` for no subject.

    A team expands to its ACTIVE members now, never at save time. So a member
    who leaves the team leaves the report on the next render (§7.1 rule 4).
    """
    if subject is None:
        return None
    await require_subject(db, user, vis, subject)
    if subject["kind"] == "person":
        return [subject["email"]]
    from gateway.routes.admin.groups import active_memberships

    rows = await active_memberships(
        db, getattr(vis, "organization_id", None), slug=subject["slug"],
    )
    return sorted({email for _, email, _ in rows})


def subject_clause(alias: str = "t") -> str:
    """The task ``alias`` is held NOW by a person of the subject.

    Each body ANDs this into its scope. It binds ``:subject_people``, which
    :func:`subject_params` supplies.
    """
    return (
        "EXISTS (SELECT 1 FROM pm_task_assignees sa"
        f"         WHERE sa.task_id = {alias}.id"
        "           AND lower(sa.assignee) = ANY(CAST(:subject_people AS text[])))"
    )


def subject_params(subject_people: list[str] | None) -> dict[str, Any]:
    """``:subject_people``, and ONLY when :func:`subject_clause` names it."""
    if subject_people is None:
        return {}
    return {"subject_people": sorted({p.strip().lower() for p in subject_people})}


def with_subject(scope_sql: str, subject_people: list[str] | None) -> str:
    """``scope_sql``, narrowed to the subject when there is one."""
    if subject_people is None:
        return scope_sql
    return f"({scope_sql}) AND {subject_clause('t')}"


def _is_person(who: str) -> bool:
    """An address, and not the unassigned row or an ``agent:`` row."""
    return bool(who) and not who.startswith("agent:")


def filter_person_rows(
    rows: list[dict[str, Any]],
    allowed: frozenset[str] | set[str] | None,
    *,
    key: str = "assignee",
) -> tuple[list[dict[str, Any]], int]:
    """The rows of the people in ``allowed``, and the count of the rest.

    The unassigned row and the ``agent:`` rows are not people, and they stay.
    ``allowed`` of ``None`` keeps every row. A body calls this with the
    subject's people. A route or the render calls it with
    :func:`reportable_people`, and the count is then ``hidden_people``.
    """
    if allowed is None:
        return list(rows), 0
    kept: list[dict[str, Any]] = []
    hidden = 0
    for row in rows:
        who = str(row.get(key) or "").strip().lower()
        if _is_person(who) and who not in allowed:
            hidden += 1
            continue
        kept.append(row)
    return kept, hidden


def filter_conflict_rows(
    rows: list[dict[str, Any]],
    allowed: frozenset[str] | set[str] | None,
) -> tuple[list[dict[str, Any]], int]:
    """The conflict rows the reader may see, and the count of the rest.

    A row about one person (``parallel_person``, and the HR kinds that only
    an admin receives) goes when its person is not in ``allowed``. A
    dependency row keeps its holders (:data:`DEPENDENCY_KINDS`).

    ⚠️ The count is of PEOPLE, not of rows. One person can hold three
    conflict rows, and the shared line says "N other people".
    """
    if allowed is None:
        return list(rows), 0
    kept: list[dict[str, Any]] = []
    hidden: set[str] = set()
    for row in rows:
        if row.get("kind") not in DEPENDENCY_KINDS:
            people = [
                str(p.get("email") or "").strip().lower()
                for p in row.get("people") or []
            ]
            others = {p for p in people if _is_person(p) and p not in allowed}
            if others:
                hidden |= others
                continue
        kept.append(row)
    return kept, len(hidden)


async def subject_choices(db: Any, user: Any, vis: Any) -> dict[str, Any]:
    """The people and the teams that :func:`may_report_on` allows.

    The builder's picker reads this, so it never shows a subject and then
    refuses it (§7.1 rule 1). Both lists name the organization in the SQL.

    ``me`` (WS-27bn R5b) is the reader's own address, from the session. The
    subject chip names that row "Me", and "My day" takes it as its subject.
    """
    scope = await reader_scope(db, user, vis)
    org = getattr(vis, "organization_id", None)
    people = (await db.execute(
        text(
            "SELECT lower(btrim(email)) AS email, display_name FROM app_user"
            " WHERE organization_id = CAST(:org AS uuid) AND status = 'active'"
            " ORDER BY lower(btrim(email))"
        ),
        {"org": org},
    )).fetchall()
    teams = (await db.execute(
        text(
            "SELECT slug, display_name FROM org_group"
            " WHERE organization_id = CAST(:org AS uuid) ORDER BY slug"
        ),
        {"org": org},
    )).fetchall()
    return {
        "everyone": scope.everyone,
        "me": scope.me,
        "people": [
            {"email": str(r.email), "name": r.display_name or None}
            for r in people
            if scope.allows({"kind": "person", "email": str(r.email)})[0]
        ],
        "teams": [
            {"slug": str(r.slug), "name": r.display_name or str(r.slug)}
            for r in teams
            if scope.allows({"kind": "team", "slug": str(r.slug)})[0]
        ],
    }
