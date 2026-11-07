"""The share of a private install with named people (WS-PKG PKG-11a).

Spec: ``project-docs/specs/package_host.md`` §5.1 decision 3, §6.2 ("The
rules of a promotion") and the PKG-11a row of §10. The R8 suite is
``tests/unit/test_package_promotion_r8.py``, and the hermetic suite is
``tests/unit/test_package_promotion.py``.

**A share stays private.** :func:`share_with_people` writes one row of
``app_grants`` for each person. The install stays private, and the resolve
shows it to each grantee that passes the three tests of
``sharing.SHARE_ROW``. The promotion to a group is
``store.promote_to_group``. This module never writes ``package_install``,
because ``store.py`` is the one writer of that table. The promotion deletes
the share rows of the install in its own transaction, so ``store.py`` holds
that DELETE (see its docstring).

**The PK-7 requirement.** A row of ``app_grants`` can exist already: the
consent row of a colleague, or the grant of another editor. Test 3 of
``SHARE_ROW`` counts a row only when the owner made it. So the statement of
this module sets ``granted_by`` to the sharer on a conflict, and keeps the
role and ``consented_scope_hash`` of the row. ``routes/apps/grants.py``
keeps its own statement, and this module does not call it.

**One sharer for each person (fail closed).** The key of ``app_grants`` is
the app and the person, not the install. Two members can each have a private
install of one app. If the second sharer took the row of the first, the
first grantee would lose the first install in silence, and the purge of the
first owner would delete it. So the share reads the rows that it meets with
the row lock. It sets ``granted_by`` only on a consent row (``granted_by`` is
the subject) or on a row of this sharer. A row of another sharer refuses the
whole call with ``shared_by_another_owner``, and no row is written. The guard
of the conflict clause holds the same rule. This is a known limit: PKG-11
notes it for J2.

**The locks.** The share takes the org lock and the row lock of
``store.locked_install``, as the promotion does. So a share cannot land
between the group check of a promotion and its write.

**Tenancy (R5).** The function takes a session that the caller opened with
``tenant_session(org)``, and it takes no org. Each read names the bound org
beside RLS. The INSERT writes no ``organization_id``, because the column
DEFAULT reads the bound tenant. The sharer is the session member, from the
authenticated ``UserContext``, never from request input (contract R11).

**Rights.** The function checks only that the sharer is the owner of the
install. The route of PKG-11b or J2 checks ``studio:publish``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from acb_auth.permissions import normalize_grant_subject
from sqlalchemy import text

from gateway.package_host.store import (
    _BOUND_ORG,
    MAX_SUBJECT_BYTES,
    InstallError,
    NotTheOwner,
    _install_id,
    _member,
    _plain,
    locked_install,
    refuse_unless_kept_private,
)

#: The most people in one share.
MAX_PEOPLE = 50

#: The subjects that name no person. A ``group:`` or ``org`` subject is a
#: promotion, and an agent is no person.
_NOT_A_PERSON = ("group:", "agent:", "agents:")

#: The active members of the bound org among ``:emails``.
_ACTIVE_MEMBERS_SQL = (
    "SELECT lower(email) FROM app_user "
    f"WHERE organization_id = {_BOUND_ORG} AND status = 'active' "
    "AND lower(email) = ANY(CAST(:emails AS text[]))"
)

#: The rows of the app that the share meets, with the row lock. The key of
#: the conflict is ``(app_id, subject)``, so the read matches the subject
#: exactly.
_MET_ROWS_SQL = (
    "SELECT subject, granted_by FROM app_grants "
    f"WHERE organization_id = {_BOUND_ORG} AND app_id = CAST(:app AS uuid) "
    "AND subject = ANY(CAST(:people AS text[])) FOR UPDATE"
)

#: One share. A conflict keeps the role and the consent hash, and sets
#: ``granted_by`` to the sharer (PK-7). The guard names the bound org, and
#: it changes only a consent row or a row of this sharer.
_SHARE_SQL = (
    "INSERT INTO app_grants (app_id, subject, role, granted_by) "
    "VALUES (CAST(:app AS uuid), :subject, 'use', :sharer) "
    "ON CONFLICT (app_id, subject) DO UPDATE SET granted_by = excluded.granted_by "
    f"WHERE app_grants.organization_id = {_BOUND_ORG} "
    "AND (lower(app_grants.granted_by) = lower(app_grants.subject) "
    "OR lower(app_grants.granted_by) = excluded.granted_by)"
)


class ShareRefused(InstallError):
    """A share that the store refuses (422). Nothing is written.

    The codes: ``bad_people``, ``no_people``, ``too_many_people``,
    ``not_a_person``, ``own_address``, ``not_a_member`` and
    ``shared_by_another_owner``. No message names an address.
    """


def _people(emails: object, sharer: str) -> tuple[str, ...]:
    """The normalised addresses, in order and with no copy, or a refusal."""
    if isinstance(emails, str) or not isinstance(emails, Sequence):
        raise ShareRefused("bad_people", "The people must be a list of addresses.")
    if not emails:
        raise ShareRefused("no_people", "Name at least one person.")
    if len(emails) > MAX_PEOPLE:
        raise ShareRefused("too_many_people", f"Name at most {MAX_PEOPLE} people in one share.")
    out: list[str] = []
    for raw in emails:
        if not _plain(raw, limit=MAX_SUBJECT_BYTES):
            raise ShareRefused("bad_people", "Each person must be an email address.")
        try:
            subject = normalize_grant_subject(str(raw))
        except ValueError as exc:
            raise ShareRefused("bad_people", "Each person must be an email address.") from exc
        if subject == "org" or subject.startswith(_NOT_A_PERSON) or "@" not in subject:
            raise ShareRefused(
                "not_a_person", "A share names people. Use a promotion for a group or the org.")
        if subject == sharer:
            raise ShareRefused("own_address", "The owner sees the install already.")
        if subject not in out:
            out.append(subject)
    return tuple(out)


async def share_with_people(db: Any, *, install_id: str, emails: Sequence[str],
                            sharer: str) -> tuple[str, ...]:
    """Share the private install *install_id* with the people *emails*.

    *db* is a session that the caller opened with ``tenant_session(org)``.
    *sharer* is the address of the session member, who must own the
    install. Each person must be an active member of the bound org. The
    install must be private, active and kept (PKG-Q12). A person whom another
    member already shares the app with refuses the whole call
    (``shared_by_another_owner``). Each refusal writes nothing. Answers the
    normalised addresses, in order. :class:`NotTheOwner` comes from
    ``store.py``, which the promotion shares.
    """
    install = _install_id(install_id)
    by = _member(sharer)
    people = _people(emails, by)
    row = await locked_install(db, install, "a share")
    if str(row[4]) != by:
        raise NotTheOwner("not_owner", "Only the owner of the install can share it.")
    refuse_unless_kept_private(row, allow_draft=True)
    found = {str(r[0]) for r in (await db.execute(
        text(_ACTIVE_MEMBERS_SQL), {"emails": list(people)})).all()}
    if any(p not in found for p in people):
        raise ShareRefused("not_a_member", "Each person must be an active member of the org.")
    met = (await db.execute(text(_MET_ROWS_SQL), {"app": str(row[0]),
                                                  "people": list(people)})).all()
    if any(str(r[1]).lower() not in (str(r[0]).lower(), by) for r in met):
        raise ShareRefused(
            "shared_by_another_owner",
            "Another member of the org shares this app with a person in the list already.")
    await db.execute(text(_SHARE_SQL), [
        {"app": str(row[0]), "subject": p, "sharer": by} for p in people])
    return people
