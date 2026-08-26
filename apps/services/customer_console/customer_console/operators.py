"""Who a platform operator is — the admission decision, and nothing else.

Spec: ``project-docs/specs/operator_identity_and_access.md`` §4 · **D64**.
Board: ``work_plan.md`` §2 WS-31, ticket **CP-12a**.

**Three checks admit an operator, and all three must pass** (spec §4.1):

  1. **The directory** — the Microsoft ``tid`` claim equals ours.
  2. **The domain** — the email domain is one we named.
  3. **The registry** — an ``operator`` row exists and its status is ``active``.

⚠️ **Check 3 is not redundant, and a future reader will think it is.** Without
it, every person our Entra directory ever admits becomes a platform operator on
their first sign-in. The directory tells us somebody works here. It does not
tell us they run the platform. This is **D34.4** — *"Supabase Auth
authenticates; it never decides entitlement"* — applied to staff rather than to
customers, and it is the same split, not a second one.

⚠️ **This module decides. It does not do SQL.** Reads and writes live in
:mod:`customer_console.store`, exactly as :mod:`customer_console.seats` and
:mod:`customer_console.credits` are pure beside it. A policy module that grew
its own queries would put "which rows count as admitted" in two places.

**Fails CLOSED.** An unset ``OPERATOR_ENTRA_TENANT_ID`` refuses everybody with a
**503**, the same posture ``workbench/operator_console/src/lib/staff.ts`` already
takes and the same lesson D33.1 recorded: a mis-provisioned box must be shut,
not open.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger("platform.operators")

__all__ = [
    "ADMIN",
    "EDITOR",
    "ROLES",
    "STATUSES",
    "VIEWER",
    "AdminRefused",
    "Operator",
    "OperatorForbidden",
    "OperatorUnconfigured",
    "admit",
    "bootstrap_email",
    "guard_known_role",
    "guard_known_status",
    "guard_last_admin",
    "guard_not_self",
    "normalise_email",
    "staff_domains",
    "staff_tenant_id",
]

#: The three roles (**D64.3**). Ordered narrowest first, which is the order the
#: permission matrix in spec §5 reads.
VIEWER = "viewer"
EDITOR = "editor"
ADMIN = "admin"
ROLES: tuple[str, ...] = (VIEWER, EDITOR, ADMIN)

#: ``deactivated`` SEALS rather than erases (**D63**): the row stays so the
#: person's ``control_audit`` history stays readable.
STATUSES: tuple[str, ...] = ("active", "suspended", "deactivated")

#: The ONE refusal an admitted-check failure produces, whatever the cause.
#:
#: ⚠️ Deliberately uninformative, and a future reader will want to "improve" it.
#: A refusal must not answer a question. If a wrong-directory refusal read
#: differently from a not-an-operator refusal, the sign-in page would become an
#: oracle telling an attacker which of the three checks they had already
#: passed. The CAUSE goes to the log, where staff can read it and a stranger
#: cannot.
_REFUSAL = "not a platform operator"


class OperatorUnconfigured(Exception):
    """The staff gate is not configured. Callers map this to **503**.

    Distinct from :class:`OperatorForbidden` because the two mean opposite
    things to whoever is on call: this one says *the box is wrong*, and the
    other says *the person is wrong*.
    """


class OperatorForbidden(Exception):
    """This identity is not an admitted operator. Callers map this to **403**."""


@dataclass(frozen=True)
class Operator:
    """An admitted operator. Only :func:`admit` constructs one."""

    id: str
    email: str
    role: str
    status: str

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN


def normalise_email(email: str | None) -> str:
    """Lower-case and strip. The one place email casing is decided.

    A directory may return ``Vijay@Fracktal.in`` today and ``vijay@fracktal.in``
    tomorrow, and an operator whose row stopped matching because of that would
    be locked out by a display choice.
    """
    return (email or "").strip().lower()


def staff_tenant_id(env: dict[str, str] | None = None) -> str:
    """Our own Microsoft directory id. Raises when unset — the gate fails closed."""
    source = os.environ if env is None else env
    value = (source.get("OPERATOR_ENTRA_TENANT_ID") or "").strip()
    if not value:
        raise OperatorUnconfigured(
            "OPERATOR_ENTRA_TENANT_ID is not configured — the operator console "
            "refuses everyone until the staff directory is pinned (D64.1)"
        )
    return value


def staff_domains(env: dict[str, str] | None = None) -> frozenset[str]:
    """The email domains we admit, as a set. Raises when unset.

    Comma-separated, because one directory can legitimately carry more than one
    verified domain and hard-coding a single one is how the second one becomes
    an emergency.
    """
    source = os.environ if env is None else env
    raw = (source.get("OPERATOR_STAFF_DOMAINS") or "").strip()
    if not raw:
        raise OperatorUnconfigured(
            "OPERATOR_STAFF_DOMAINS is not configured — the operator console "
            "refuses everyone until the staff domains are named (D64.2)"
        )
    domains = frozenset(
        part.strip().lower().lstrip("@") for part in raw.split(",") if part.strip()
    )
    if not domains:
        raise OperatorUnconfigured(
            "OPERATOR_STAFF_DOMAINS is set but names no domain"
        )
    return domains


def bootstrap_email(env: dict[str, str] | None = None) -> str | None:
    """The one email the empty-registry bootstrap admits, or ``None``.

    Unset is NOT an error here, unlike the two above. A configured box with a
    populated registry has no use for this value, and demanding it forever
    would make the variable a permanent back door rather than a one-time path.
    """
    source = os.environ if env is None else env
    return normalise_email(source.get("OPERATOR_BOOTSTRAP_EMAIL")) or None


def _check_directory(tid: str | None, env: dict[str, str] | None) -> None:
    """Check 1 — the identity came from OUR directory."""
    expected = staff_tenant_id(env)
    presented = (tid or "").strip()
    if not presented or presented != expected:
        _log.warning(
            "operator.refused",
            extra={"operator_check": "directory", "operator_tid": presented or "<none>"},
        )
        raise OperatorForbidden(_REFUSAL)


def _check_domain(email: str, env: dict[str, str] | None) -> None:
    """Check 2 — the email is on a domain we named."""
    allowed = staff_domains(env)
    _, _, domain = email.partition("@")
    if not domain or domain not in allowed:
        _log.warning(
            "operator.refused",
            extra={"operator_check": "domain", "operator_domain": domain or "<none>"},
        )
        raise OperatorForbidden(_REFUSAL)


def _check_registry(row: Any) -> Operator:
    """Check 3 — a row exists, and it is ``active``.

    Takes the row rather than the connection so this module stays free of SQL.
    The caller reads it through ``store.operator_by_email``.
    """
    if row is None:
        _log.warning("operator.refused", extra={"operator_check": "registry"})
        raise OperatorForbidden(_REFUSAL)

    status = str(row["status"])
    if status != "active":
        # A suspended operator is a person we know, which is why the log says so
        # and the browser still does not.
        _log.warning(
            "operator.refused",
            extra={"operator_check": "status", "operator_status": status},
        )
        raise OperatorForbidden(_REFUSAL)

    return Operator(
        id=str(row["id"]),
        email=str(row["email"]),
        role=str(row["role"]),
        status=status,
    )


def admit(
    row: Any,
    *,
    tid: str | None,
    email: str | None,
    env: dict[str, str] | None = None,
) -> Operator:
    """Run all three checks. Return the operator, or raise.

    ``row`` is what ``store.operator_by_email`` returned for *email*, or
    ``None``. The caller does the read. This function does the deciding.

    ⚠️ **Order matters, and it is cheapest-first on purpose.** The two
    configuration checks run before the registry row is consulted, so a box with
    no directory pinned answers 503 rather than quietly reaching the database
    and answering 403. Those are different incidents.
    """
    normalised = normalise_email(email)
    _check_directory(tid, env)
    _check_domain(normalised, env)
    operator = _check_registry(row)

    if operator.email != normalised:
        # The read is `lower(email) = :email`, so this cannot happen unless the
        # caller passed a different email than it looked up. Refuse rather than
        # trust either one: admitting the row would authenticate the wrong
        # person, which is the single worst outcome this module has.
        _log.warning("operator.refused", extra={"operator_check": "mismatch"})
        raise OperatorForbidden(_REFUSAL)

    return operator


def bootstrap_role() -> str:
    """The role the bootstrap row takes.

    ``admin``, because a registry whose only member cannot add anybody else is a
    registry nobody can ever grow. Named as a function rather than written at
    the call site so the choice is one thing to find.
    """
    return ADMIN


class BootstrapRefused(Exception):
    """The registry is not empty, so the one-time bootstrap does not apply."""


def bootstrap(conn: Any, *, env: dict[str, str] | None = None) -> str | None:
    """Insert the first operator, ONCE, when the registry is empty.

    Returns the new operator id, or ``None`` when ``OPERATOR_BOOTSTRAP_EMAIL``
    is unset. Raises :class:`BootstrapRefused` when any row already exists.

    ⚠️ **This is a one-time path and it must stay one-time.** The moment the
    registry holds a row — in ANY status — this refuses, and the environment
    variable is inert for the life of the box. A bootstrap that kept working
    would be an env-var-shaped back door into a cross-customer console, which
    is precisely the thing the three checks exist to close.

    ⚠️ The count is over every status, not over ``active`` alone. Counting only
    active rows would re-open the path the moment a sole admin was deactivated,
    and "deactivate the admin, then bootstrap yourself" is a one-step
    escalation an attacker with database reach would find immediately.

    No SQL here — :mod:`customer_console.store` owns the two statements, and
    this function owns the decision about when they run.
    """
    from customer_console import store

    email = bootstrap_email(env)
    if not email:
        return None

    # The two configuration checks bind the bootstrap too. A box that has not
    # pinned a directory must not be able to mint its first operator either.
    staff_tenant_id(env)
    domains = staff_domains(env)
    _, _, domain = email.partition("@")
    if domain not in domains:
        raise BootstrapRefused(
            "OPERATOR_BOOTSTRAP_EMAIL is not on a domain in "
            "OPERATOR_STAFF_DOMAINS"
        )

    if store.operator_count(conn) > 0:
        raise BootstrapRefused(
            "the operator registry is not empty — the one-time bootstrap "
            "does not apply, and OPERATOR_BOOTSTRAP_EMAIL is now inert"
        )

    operator_id = store.operator_insert(
        conn, email=email, role=bootstrap_role(), added_by=None
    )
    _log.info("operator.bootstrapped", extra={"operator_email": email})
    return operator_id


# ── Operator administration — the four guards (CP-12d) ──────────────────────
#
# Spec: operator_identity_and_access.md §6.1 · §8.1 done-whens 16-19.
#
# Each guard below has a test named beside it (R7). They are POLICY, so they
# live here rather than in a route body: two routes already change a role or a
# status, and a guard written at one call site is a guard the other forgets.


class AdminRefused(Exception):
    """A registry write that must not happen. Callers map this to **409**.

    ⚠️ **409, not 403.** These are not authorization failures — the caller IS
    an admin and IS allowed here. They are refusals of a specific CHANGE that
    would break the registry, and telling the two apart matters to whoever
    reads the response. A 403 would send them hunting for a missing role.
    """


def guard_not_self(actor_id: str, target_id: str) -> None:
    """Guard 2 — nobody edits their own role or status.

    An admin who could promote themselves holds no role at all, and an admin
    who could suspend themselves has invented a new way to lock the team out.
    Both directions are refused by the same rule.

    Fence: ``test_operator_admin.py::test_an_operator_cannot_change_their_own_role``.
    """
    if actor_id == target_id:
        raise AdminRefused(
            "an operator cannot change their own role or status"
        )


def guard_last_admin(
    *,
    active_admins: int,
    target_role: str,
    target_status: str,
    new_role: str | None = None,
    new_status: str | None = None,
) -> None:
    """Guard 1 — the last ACTIVE admin cannot be demoted or switched off.

    Without this, one careless change locks the whole team out of a live
    console, and the only way back is the break-glass token.

    The arithmetic is deliberately explicit rather than clever: work out
    whether the target is an active admin TODAY, and whether they would still
    be one after the change. Refuse only when that flips the last one off.

    Fence: ``test_operator_admin.py::test_the_last_active_admin_cannot_be_demoted``.
    """
    was_active_admin = target_role == ADMIN and target_status == "active"
    if not was_active_admin:
        return

    role_after = new_role if new_role is not None else target_role
    status_after = new_status if new_status is not None else target_status
    still_active_admin = role_after == ADMIN and status_after == "active"

    if not still_active_admin and active_admins <= 1:
        raise AdminRefused(
            "this is the last active admin — promote somebody else first"
        )


def guard_known_role(role: str) -> None:
    """A role the product does not have never reaches the table.

    The CHECK constraint in migration 009 would refuse it too. This turns a
    database error into a 400 an operator can read, and it keeps the
    vocabulary in one place rather than two.
    """
    if role not in ROLES:
        raise AdminRefused(f"unknown role: {role!r}")


def guard_known_status(status: str) -> None:
    """The same, for status."""
    if status not in STATUSES:
        raise AdminRefused(f"unknown status: {status!r}")
