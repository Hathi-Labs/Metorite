"""People Center · the field-class authority (WS-28g).

Spec: ``project-docs/specs/people_center_app.md`` §4 · decisions D-PC-1…D-PC-5.

**One map, in one module, and every consumer reads it.** Three things ask "may
this caller touch this field" — the write route, the read projection, and the
UI — and the only way those three cannot drift is for there to be one answer.
The UI's copy is not a copy: the read sends ``editable_fields`` and the client
renders from that (D-PC-4).

The module is **pure** — no DB, no request, no I/O — so the rules are testable
as rules rather than through a database fake that would only agree with itself.

Two axes, and they are not the same axis
----------------------------------------
*Write class* answers "who may change it": ``ADMIN`` (the organisation's claim
about a person), ``SELF`` (the person's claim about themselves, or an admin),
``DERIVED`` (nobody — computed).

*Read tier* answers "who may see it": directory (any feature holder), HR
(``admin:members:read`` **or** self), private (``admin:members:manage`` **or**
self).

A field is in exactly one write class and one read tier. The fence is
``tests/unit/test_people_profile.py``'s partition test, which discovers every
column of ``people`` from the migrations themselves and fails when one is
in no class — so a column added next month cannot default into the permissive
answer by being forgotten (R7).
"""

from __future__ import annotations

from collections.abc import Iterable

from fastapi import HTTPException

# ── Write classes ───────────────────────────────────────────────────────────

#: The organisation's claims about a person. Title, manager, department, status
#: and capacity are not self-describing facts — a product where you can promote
#: yourself is not an org chart.
#:
#: ``email`` is here for a sharper reason (D-PC-2): the self predicate IS
#: ``lower(caller.email) = lower(person.email)``, so a self-editable address
#: would let anyone point their row at a colleague's address and inherit that
#: row's self-rights on the very next request. Self-editable identity is
#: privilege escalation with extra steps.
ADMIN_FIELDS: frozenset[str] = frozenset({
    "name",
    "email",
    "role",
    "title",
    "department",
    "team",
    "reports_to",
    "manager_id",
    "status",
    "capacity_hours_per_week",
    "current_load_hours_per_week",
    "clickup_user_id",
    # §3.2 employment
    "employee_id",
    "employment_type",
    "start_date",
    "end_date",
    "seniority",
    "cost_center",
    # An identity fact about the engagement (off-boarding, reaching an
    # alumnus), not a self-description.
    "personal_email",
})

#: What a person is the best source for about themselves. An admin may write
#: these too — admin is a superset, never a disjoint set, because "an admin
#: cannot fix a typo in somebody's timezone" is a support ticket, not a policy.
SELF_FIELDS: frozenset[str] = frozenset({
    # §3.1 the self-describing directory half
    "preferred_name",
    "pronouns",
    "location",
    "timezone",
    "working_hours",
    "bio",
    "links",
    "languages",
    "interests",
    # §3.3 capability — the person knows what they can do
    "skills",
    "domain",
    "resume_summary",
    "years_experience",
    # §3.4 their own stated ceiling on parallel work
    "max_concurrent_tasks",
    # §3.5 private contact
    "phone",
    "emergency_contact",
    "birthday",
    # §3.1a the display image. Self-writable — but through the dedicated upload
    # endpoint, never through the PATCH payload (see UPLOAD_ONLY_FIELDS).
    "avatar",
})

#: Fields in a write class that the PATCH payload deliberately cannot carry,
#: because they arrive as a FILE through their own endpoint.
#:
#: They stay in :data:`SELF_FIELDS` on purpose: the authorization question
#: ("may this caller change this person's picture") is the same question the
#: PATCH asks about a timezone, and answering it in a second place is how the
#: two would drift. What differs is the transport, so `editable_fields` still
#: names them — the UI has to know it may offer the control — while the payload
#: fence excludes them rather than demanding a data-URI-shaped request body.
UPLOAD_ONLY_FIELDS: frozenset[str] = frozenset({"avatar"})

#: Computed, never posted. Present here so the partition fence can prove every
#: column of ``people`` was classified rather than forgotten.
DERIVED_FIELDS: frozenset[str] = frozenset({
    "id",
    # Which customer this row belongs to (migration 209, H-125). Written by
    # the column DEFAULT from the bound tenant, or by the member trigger.
    # No form may set it: a person does not move themselves between
    # customers, and a writable tenant key is a way out of one's own tenant.
    "organization_id",
    "avatar_updated_at",
    "created_at",
    "updated_at",
    "updated_by",
    "synced_at",
    "source",
    "source_key",
    "email_conflict",
    "skills_source",            # provenance, stamped by the write path
    "available_hours_per_week",  # capacity minus load
    "capability_embedding",
    "capability_text_hash",
})

#: Every field any caller may ever write, by any transport. The write payload
#: model (``routes/tasks/people.PersonWrite``) must expose exactly this set
#: **less** :data:`UPLOAD_ONLY_FIELDS` — pinned by a test in both directions,
#: because a field the payload accepts and this map has never heard of is a
#: field with no owner and therefore no gate, and a field in the map that no
#: transport can carry is a permission granted for something nobody can do.
WRITABLE_FIELDS: frozenset[str] = ADMIN_FIELDS | SELF_FIELDS


# ── Read tiers ──────────────────────────────────────────────────────────────

#: Restricted to `admin:members:read` **or** self. This is WS-24 N4's projection
#: — the tuple itself lives in ``routes/tasks/people.HR_FIELDS`` and is imported
#: by every consumer; re-listing it here would be the second answer this module
#: exists to prevent. Named here only to say where it lives.
#:
#: v2 adds the §3.2 employment half to the same tier.
HR_EXTRA_FIELDS: tuple[str, ...] = (
    "employee_id",
    "employment_type",
    "start_date",
    "end_date",
    "seniority",
    "cost_center",
    "interests",
    "max_concurrent_tasks",
)

#: §3.5 — self, or an ``admin:members:manage`` holder, and nobody else
#: (D-PC-3). **Re-exported, not re-listed**: the projection that applies it
#: (``_row_to_person``) lives beside ``HR_FIELDS`` in the tasks package, and a
#: second copy of a projection's field list is the drift this module exists to
#: prevent.
# ── Vocabularies validated in the route, not (yet) in the database ──────────
#
# 148's lesson, applied forward: ONE tuple read by the validator, the facets
# response and the UI's select, so a sixth value cannot be accepted in one
# place and refused in another. The database CHECK is P-6 (D-PC-8) — narrowing
# a vocabulary over live data is the contract half of expand/contract and it
# does not travel in the same release as the column.
#
# **Re-exported, not defined here.** The People package imports from the tasks
# package and never the reverse (`routes/people/core.py` already does this for
# `can_read_hr_fields` and `PEOPLE_STATUSES`), and both write doors need the
# validator — including `PATCH /tasks/people/{id}`, which predates this module.
# Defining it here and importing it from there would close an import cycle
# through this package's `__init__`.
from gateway.routes.tasks.core import (  # noqa: E402 — re-exports
    EMPLOYMENT_TYPES,
    SENIORITY_LEVELS,
    is_month_day,
    validate_person_vocabularies,
)
from gateway.routes.tasks.people import PRIVATE_FIELDS  # noqa: E402

__all__ = [
    "ADMIN_FIELDS",
    "DERIVED_FIELDS",
    "EMPLOYMENT_TYPES",
    "HR_EXTRA_FIELDS",
    "PRIVATE_FIELDS",
    "SELF_FIELDS",
    "SENIORITY_LEVELS",
    "UPLOAD_ONLY_FIELDS",
    "WRITABLE_FIELDS",
    "authorize_write",
    "editable_fields",
    "is_month_day",
    "refused_fields",
    "validate_person_vocabularies",
]


# ── The questions the routes ask ────────────────────────────────────────────

def editable_fields(*, is_admin: bool, is_self: bool) -> list[str]:
    """Which fields THIS caller may write on THIS row, sorted.

    Empty for a caller who is neither — and empty is the honest answer, not an
    error: the person page is readable by any feature holder, and what varies
    is the shape of what comes back, never whether an answer comes back at all.

    Sorted so the response is stable: an unordered set rendered into JSON makes
    every read look like a change to anything diffing it.
    """
    allowed: set[str] = set()
    if is_admin:
        allowed |= WRITABLE_FIELDS
    if is_self:
        allowed |= SELF_FIELDS
    return sorted(allowed)


def refused_fields(names: Iterable[str], *, is_admin: bool, is_self: bool) -> list[str]:
    """The subset of ``names`` this caller may not write, sorted.

    Unknown names are refused too. A payload key nothing recognises is either a
    typo the caller wants to hear about or a field somebody expects to exist,
    and both are better answered than dropped.
    """
    allowed = set(editable_fields(is_admin=is_admin, is_self=is_self))
    return sorted(n for n in names if n not in allowed)


def authorize_write(names: Iterable[str], *, is_admin: bool, is_self: bool) -> None:
    """Raise 403 unless every named field is writable by this caller.

    **The refusal names the fields** (D-PC-5). Silently dropping the ones the
    caller may not write would mean a save that reports success and discards
    half the form — the person then believes their manager changed, and nothing
    in the product ever tells them otherwise. A 403 they can read is strictly
    better than a lie they cannot see.

    A caller who is neither admin nor self gets the short refusal: listing which
    fields they *would* be able to write is a permission map for somebody with
    no permissions.
    """
    if not (is_admin or is_self):
        raise HTTPException(
            status_code=403,
            detail="You may not edit this person's record.",
        )
    refused = refused_fields(names, is_admin=is_admin, is_self=is_self)
    if refused:
        raise HTTPException(
            status_code=403,
            detail=(
                "These fields are not yours to change: "
                + ", ".join(refused)
                + ". They are set by an administrator."
            ),
        )


