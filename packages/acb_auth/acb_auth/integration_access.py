"""Who can use an integration: the pure rule (ACC-1).

Spec: ``project-docs/specs/integrations_console.md`` §3.9.2.

An organization may hold one access rule for each integration service. The
rules sit in ``organization.settings`` under :data:`RULES_KEY`, one key for
each service::

    {"integration_access": {"mcp-tally": {"subjects": ["a@x.com"],
                                          "set_by": "b@x.com", "set_at": "..."}}}

A rule only narrows. It never grants. This module turns the stored value and
the member's facts into the narrowed :class:`EffectiveAccess`. It is pure: no
database, no settings, no I/O. ``acb_auth.access`` reads the value, and the
admin routes write it.

The rules, in the order of §3.9.2:

1. A member who holds ``integrations:manage`` passes each rule.
2. A member who fails a rule gets a deny of the exact permission
   ``integrations:use:<service>``. So no check changes, and no second check
   exists. ``EffectiveAccess.intersect`` keeps each deny, so a shared session
   with one refused member refuses.
3. A rule never gives a permission.
4. An exact allow override does not pass a rule. Two exact patterns tie in
   ``EffectiveAccess.decide``, and a tie goes to deny.
5. A subject is an email or ``group:<slug>``, in the one grammar of D12
   (:func:`acb_auth.permissions.normalize_grant_subject`). ``org`` is not
   stored, because it is the same as no rule.
6. A rule that the code cannot read means admins only for that service. A
   value that the code cannot read at all, and a failed read, refuse every
   ``integrations:use:*`` to a member with no ``integrations:manage``.

:func:`narrow` never raises. An exception in the run authorizer means "no
filter", so a fault here must narrow and never escape.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from acb_auth.permissions import (
    EffectiveAccess,
    InvalidPermission,
    normalize_grant_subject,
    validate_permission,
)

#: The key of ``organization.settings`` that holds the rules.
RULES_KEY = "integration_access"

#: The permission that passes every rule (§3.9.2 rule 1).
MANAGE = "integrations:manage"

#: The prefix of the permission that a rule narrows.
USE_PREFIX = "integrations:use:"

#: The deny of a failed read, and of a value that the code cannot read.
USE_ALL = f"{USE_PREFIX}*"

#: The source that the access preview shows for a refusal by a rule.
RULE_SOURCE = "integration-rule"

#: The three modes of the route. ``everyone`` is no rule, ``admins`` is a rule
#: with no subject, and ``chosen`` is a rule with subjects.
MODES: tuple[str, ...] = ("everyone", "chosen", "admins")


@dataclass(frozen=True, slots=True)
class Narrowed:
    """The access after the rules, and why.

    ``access`` is the input object itself when no rule refuses anything, so
    the access of a member with no rule is equal to the access of before.
    """

    access: EffectiveAccess
    #: The services that hold a rule, readable or not.
    ruled: frozenset[str] = frozenset()
    #: The services that a rule refuses to this member.
    refused: frozenset[str] = frozenset()
    #: True when every integration is refused: a failed read, or a value that
    #: the code cannot read.
    all_refused: bool = False


def service_name_ok(service: object) -> bool:
    """True when *service* can name ``integrations:use:<service>`` exactly.

    The name is one permission segment: lower case, no ``:`` and no ``*``.
    """
    if not isinstance(service, str) or not service or ":" in service or "*" in service:
        return False
    permission = f"{USE_PREFIX}{service}"
    try:
        return validate_permission(permission) == permission
    except InvalidPermission:
        return False


def parse_subject(raw: object) -> str | None:
    """One stored or typed subject, folded, or ``None`` when it is not legal.

    An email or ``group:<slug>`` passes. ``org`` and anything else do not.
    """
    if not isinstance(raw, str):
        return None
    try:
        subject = normalize_grant_subject(raw)
    except ValueError:
        return None
    return None if subject == "org" else subject


def _decode(raw: Any) -> Any:
    """A JSONB value: the asyncpg driver gives text, psycopg gives a dict."""
    if isinstance(raw, bytes | bytearray):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _rule_subjects(value: Any) -> frozenset[str] | None:
    """The subjects of one stored rule, or ``None`` when the code cannot read it."""
    if not isinstance(value, Mapping):
        return None
    subjects = value.get("subjects")
    if not isinstance(subjects, list):
        return None
    out: set[str] = set()
    for raw in subjects:
        subject = parse_subject(raw)
        if subject is None:
            return None
        out.add(subject)
    return frozenset(out)


def parse_rules(raw: Any) -> tuple[dict[str, frozenset[str] | None], bool]:
    """``(rules, readable)`` from the stored value of :data:`RULES_KEY`.

    Each rule maps a service to its subjects, or to ``None`` when the code
    cannot read that rule. *readable* is False when the code cannot read the
    value at all, or a key cannot name a service. Then the caller cannot tell
    which service a rule names, so it refuses every one. Never raises.
    """
    try:
        value = _decode(raw)
    except (ValueError, UnicodeDecodeError):
        return {}, False
    if value is None:
        return {}, True
    if not isinstance(value, Mapping):
        return {}, False
    rules: dict[str, frozenset[str] | None] = {}
    for service, rule in value.items():
        if not service_name_ok(service):
            return {}, False
        rules[service] = _rule_subjects(rule)
    return rules, True


def passes(subjects: frozenset[str] | None, email: str, groups: Iterable[str]) -> bool:
    """True when the member is a subject of the rule, by address or by group."""
    if not subjects:
        return False
    if (email or "").strip().lower() in subjects:
        return True
    return any(f"group:{(g or '').strip().lower()}" in subjects for g in groups)


def _with_denies(access: EffectiveAccess, extra: Iterable[str]) -> EffectiveAccess:
    return dataclasses.replace(access, denied=access.denied | frozenset(extra))


def _refuse_all(access: EffectiveAccess, ruled: frozenset[str]) -> Narrowed:
    """Every ``integrations:use:*`` refused. Nothing else changes.

    A wildcard deny loses to an exact allow override in ``decide``. So each
    exact ``integrations:use:<x>`` allow gets an exact deny too, and the tie
    goes to deny.
    """
    exact = {p for p in access.allowed if p.startswith(USE_PREFIX) and not p.endswith("*")}
    return Narrowed(_with_denies(access, {USE_ALL, *exact}), ruled, frozenset(), True)


def narrow(
    access: EffectiveAccess,
    raw: Any,
    *,
    email: str,
    groups: Iterable[str] = (),
    read_failed: bool = False,
) -> Narrowed:
    """The access of one member after the rules of the org. Never raises.

    *raw* is the stored value of :data:`RULES_KEY` (a dict, JSON text, or
    ``None``). *groups* are the slugs of the member's groups in the same org.
    *read_failed* says that the read of *raw* failed.
    """
    try:
        rules, readable = ({}, False) if read_failed else parse_rules(raw)
        ruled = frozenset(rules)
        if not access.is_active or access.has(MANAGE):
            return Narrowed(access, ruled)
        if not readable:
            return _refuse_all(access, ruled)
        slugs = tuple(groups)
        refused = frozenset(
            service for service, subjects in rules.items()
            if not passes(subjects, email, slugs)
        )
        if not refused:
            return Narrowed(access, ruled)
        return Narrowed(
            _with_denies(access, (f"{USE_PREFIX}{s}" for s in refused)), ruled, refused,
        )
    except Exception:  # a fault narrows, it never escapes
        try:
            return _refuse_all(access, frozenset())
        except Exception:
            return Narrowed(EffectiveAccess(is_active=False))


def rules_name_group(raw: Any, slug: str) -> bool:
    """True when a rule names ``group:<slug>``, or when the code cannot tell.

    A value or a rule that the code cannot read counts as naming the group,
    so a group-write gate fails closed (§3.9.2 "Group writes").
    """
    rules, readable = parse_rules(raw)
    if not readable:
        return True
    subject = f"group:{(slug or '').strip().lower()}"
    return any(subjects is None or subject in subjects for subjects in rules.values())


def mode_of(subjects: frozenset[str] | None) -> str:
    """The mode of a stored rule. An empty or unreadable rule is admins only."""
    return "chosen" if subjects else "admins"


def split_subjects(subjects: Iterable[str] | None) -> tuple[list[str], list[str]]:
    """``(group slugs, addresses)``, each sorted."""
    groups: list[str] = []
    people: list[str] = []
    for subject in subjects or ():
        if subject.startswith("group:"):
            groups.append(subject[len("group:"):])
        else:
            people.append(subject)
    return sorted(groups), sorted(people)


def rule_value(subjects: Iterable[str], *, set_by: str, set_at: str) -> dict[str, Any]:
    """The stored value of one rule. The subjects are sorted, so a read agrees."""
    return {"subjects": sorted(set(subjects)), "set_by": set_by, "set_at": set_at}


__all__ = [
    "MANAGE",
    "MODES",
    "RULES_KEY",
    "RULE_SOURCE",
    "USE_ALL",
    "USE_PREFIX",
    "Narrowed",
    "mode_of",
    "narrow",
    "parse_rules",
    "parse_subject",
    "passes",
    "rule_value",
    "rules_name_group",
    "service_name_ok",
    "split_subjects",
]
