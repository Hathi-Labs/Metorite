"""Who can use an integration: the admin routes (ACC-1).

Spec: ``project-docs/specs/integrations_console.md`` §3.9.2 to §3.9.4.

* ``GET /admin/integration-access`` lists each service of the closed list
  with its rule.
* ``PUT /admin/integration-access/{service}`` takes
  ``{"mode": "everyone"|"chosen"|"admins", "groups": [], "people": []}``.

Both routes answer 404 for an org off ``INTEGRATION_ACCESS_ORGS``, first.
Then both need ``integrations:manage`` AND ``admin:access:manage``, on top of
the package floor ``require_admin_user``: a rule is an access write.

The rules sit in ``organization.settings`` under ``integration_access``. This
module is the ONE writer of that key. ``organization`` carries no row
security, so each statement names the org of the session (R5), from
:func:`get_org_id`. No route takes an org from the request.

The resolver (``acb_auth.access``) applies a stored rule whatever the flag
says. A rule only narrows, and ``acb_auth.integration_access`` holds the one
rule of how.

The closed list of services (§3.9.2):

* each MCP server of the org: ``mcp-<name>``, the rule of
  ``connections.mcp_tools.mcp_service`` and ``orchestrator._maf_mcp``;
* each API template: ``api-<template>``, the name that
  ``routes/api_connections.py`` checks;
* each key of ``acb_skills.integrations._REGISTRY``.

Any other service gets 422. One exception: ``everyone`` on a service that
holds a stored rule clears it, also when the service has left the list.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from acb_auth import UserContext, get_current_user, invalidate_access
from acb_auth.integration_access import (
    MANAGE,
    RULE_SOURCE,
    mode_of,
    parse_rules,
    parse_subject,
    rule_value,
    service_name_ok,
    split_subjects,
    without_member,
)
from fastapi import Depends, HTTPException, Request
from gateway.routes.admin._common import (
    _log,
    _tenant_session,
    get_org_id,
    integration_access_allowed,
    org_integration_rules,
    record_admin_change,
    require_admin_user,
    router,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text

#: The second right a rule write needs. A rule is an access write.
ACCESS_MANAGE = "admin:access:manage"

#: The one write of a rule. It sets the key of one service, and it makes the
#: parent object when none exists: a plain ``jsonb_set`` on a missing parent
#: changes nothing and reports success. ``CAST(:svc AS text)`` is load-bearing
#: on asyncpg, which cannot type a parameter of ``jsonb_build_object``.
SET_RULE_SQL = """
    UPDATE organization
       SET settings = jsonb_set(
               settings, '{integration_access}',
               COALESCE(settings -> 'integration_access', '{}'::jsonb)
                 || jsonb_build_object(CAST(:svc AS text), CAST(:rule AS jsonb)))
     WHERE id = CAST(:org AS uuid)
"""

#: The removal of a rule: it deletes the key of one service.
REMOVE_RULE_SQL = """
    UPDATE organization
       SET settings = jsonb_set(
               settings, '{integration_access}',
               COALESCE(settings -> 'integration_access', '{}'::jsonb) - CAST(:svc AS text))
     WHERE id = CAST(:org AS uuid)
"""

#: The write of the scrub of a member purge (:func:`scrub_member_rules`). It
#: sets the whole value that the scrub read under its row lock. The key exists,
#: because the scrub writes only when it took an address off a rule.
SCRUB_RULES_SQL = """
    UPDATE organization
       SET settings = jsonb_set(settings, '{integration_access}', CAST(:rules AS jsonb))
     WHERE id = CAST(:org AS uuid)
"""

#: The groups of the org among the named slugs. ``org_group`` carries no row
#: security, so the org is in the statement.
_KNOWN_GROUPS_SQL = (
    "SELECT slug FROM org_group "
    " WHERE organization_id = CAST(:org AS uuid) AND slug = ANY(CAST(:slugs AS text[]))"
)

#: The members of the org among the named addresses.
_KNOWN_PEOPLE_SQL = (
    "SELECT lower(email) AS email FROM app_user "
    " WHERE organization_id = CAST(:org AS uuid) "
    "   AND lower(email) = ANY(CAST(:emails AS text[]))"
)


class RuleIn(BaseModel):
    """The body of a PUT. An unknown key, such as an org, is a 422."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["everyone", "chosen", "admins"]
    groups: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)


async def require_integration_access_flag(
    user: UserContext = Depends(get_current_user),
) -> None:
    """404 unless ``INTEGRATION_ACCESS_ORGS`` holds the org of the session.

    It reads ``user.organization_id`` and nothing else from the request.
    """
    if not integration_access_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail="Not Found")


async def require_rule_admin(
    admin: UserContext = Depends(require_admin_user),
) -> UserContext:
    """403 without ``integrations:manage`` or without ``admin:access:manage``."""
    for permission in (MANAGE, ACCESS_MANAGE):
        if not admin.has_permission(permission):
            raise HTTPException(
                status_code=403, detail=f"Forbidden: missing permission '{permission}'.",
            )
    return admin


async def closed_list(db: Any, org_id: str) -> dict[str, str]:
    """``{service: kind}`` for each service that may hold a rule in *org_id*.

    *kind* is ``mcp``, ``api`` or ``agent``. A registry that does not load
    gives fewer services, so a write is refused rather than widened.
    """
    out: dict[str, str] = {}
    try:
        from acb_skills.integrations import list_registered

        out.update({name: "agent" for name in list_registered()})
    except Exception as exc:
        _log.warning("integration_access.registry_unavailable", error=type(exc).__name__)
    from gateway.connections import api as api_templates
    from gateway.connections import mcp, store
    from gateway.connections.mcp_tools import mcp_service

    out.update({f"api-{tid}": "api" for tid in api_templates.TEMPLATES})
    for conn in await store.list_by_prefix(db, org_id, mcp.PREFIX, mcp.OWNING_APP):
        out[mcp_service(conn.provider[len(mcp.PREFIX):])] = "mcp"
    return {name: kind for name, kind in out.items() if service_name_ok(name)}


def _item(service: str, kind: str, raw_rule: Any, subjects: Any, *, listed: bool,
          readable: bool) -> dict[str, Any]:
    """One row of the answer. ``subjects`` is the parsed rule, or ``None``."""
    if raw_rule is None and readable:
        return {"service": service, "kind": kind, "mode": "everyone", "groups": [],
                "people": [], "set_by": "", "set_at": "", "listed": listed,
                "readable": True}
    groups, people = split_subjects(subjects)
    meta = raw_rule if isinstance(raw_rule, dict) else {}
    return {
        "service": service, "kind": kind,
        "mode": mode_of(subjects) if readable else "admins",
        "groups": groups, "people": people,
        "set_by": str(meta.get("set_by") or ""), "set_at": str(meta.get("set_at") or ""),
        "listed": listed, "readable": readable and subjects is not None,
    }


def _decoded(raw: Any) -> dict[str, Any]:
    """The stored value as a dict, for the ``set_by`` and ``set_at`` of a row."""
    try:
        value = json.loads(raw) if isinstance(raw, str | bytes) else raw
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _items(services: dict[str, str], raw: Any) -> tuple[list[dict[str, Any]], bool]:
    rules, readable = parse_rules(raw)
    stored = _decoded(raw)
    names = sorted(set(services) | set(rules))
    return [
        _item(name, services.get(name, ""), stored.get(name), rules.get(name),
              listed=name in services, readable=readable)
        for name in names
    ], readable


@router.get(
    "/integration-access", summary="Who can use each integration",
    dependencies=[Depends(require_integration_access_flag)],
)
async def list_integration_access(
    admin: UserContext = Depends(require_rule_admin),
) -> dict[str, Any]:
    """Each service of the closed list with its rule, and each stored rule.

    A stored rule of a service that left the list has ``listed`` false, so
    an admin can see it and clear it. ``readable`` false means the code
    cannot read the stored value: every integration is then admins only.
    """
    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        services = await closed_list(db, org_id)
        raw = await org_integration_rules(db, org_id)
    items, readable = _items(services, raw)
    return {"services": items, "readable": readable, "source": RULE_SOURCE}


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _clean_subjects(rule: RuleIn) -> tuple[list[str], list[str]]:
    """``(group slugs, addresses)``, folded, or a 422.

    ``org`` and any value outside the D12 grammar are refused.
    """
    if rule.mode != "chosen" and (rule.groups or rule.people):
        raise _unprocessable(f"The mode '{rule.mode}' takes no groups and no people.")
    slugs: set[str] = set()
    for raw in rule.groups:
        value = (raw or "").strip()
        subject = parse_subject(value if value.startswith("group:") else f"group:{value}")
        if subject is None or not subject.startswith("group:"):
            raise _unprocessable(f"'{raw}' is not a group.")
        slugs.add(subject[len("group:"):])
    people: set[str] = set()
    for raw in rule.people:
        subject = parse_subject(raw)
        if subject is None or subject.startswith("group:"):
            raise _unprocessable(f"'{raw}' is not an email address.")
        people.add(subject)
    return sorted(slugs), sorted(people)


async def _check_known(db: Any, org_id: str, slugs: list[str], people: list[str]) -> None:
    """422 for a group or an address that is not in the org."""
    if slugs:
        found = {r[0] for r in (await db.execute(
            text(_KNOWN_GROUPS_SQL), {"org": org_id, "slugs": slugs})).fetchall()}
        missing = [s for s in slugs if s not in found]
        if missing:
            raise _unprocessable(f"Unknown group(s): {missing}.")
    if people:
        found = {r[0] for r in (await db.execute(
            text(_KNOWN_PEOPLE_SQL), {"org": org_id, "emails": people})).fetchall()}
        missing = [p for p in people if p not in found]
        if missing:
            raise _unprocessable(f"Not a member of this organization: {missing}.")


async def _read_body(request: Request) -> RuleIn:
    """The body, read AFTER the flag and the permissions, so a dark route is
    404 for any body."""
    try:
        raw = await request.json()
    except ValueError as exc:
        raise _unprocessable("The body is not JSON.") from exc
    try:
        return RuleIn.model_validate(raw)
    except ValidationError as exc:
        raise _unprocessable(
            "The body is {\"mode\": \"everyone\"|\"chosen\"|\"admins\", "
            "\"groups\": [], \"people\": []}."
        ) from exc


@router.put(
    "/integration-access/{service}", summary="Set who can use one integration",
    dependencies=[Depends(require_integration_access_flag)],
)
async def set_integration_access(
    service: str,
    request: Request,
    admin: UserContext = Depends(require_rule_admin),
) -> dict[str, Any]:
    """Set, change or clear the rule of one service.

    ``everyone`` deletes the key. ``admins`` stores a rule with no subject.
    ``chosen`` stores the groups and the people. One row must change, and the
    route reads the rule back before it answers. Then it records the audit
    line and clears the access cache of every member.
    """
    rule = await _read_body(request)
    if service == "org" or not service_name_ok(service):
        raise _unprocessable(f"Unknown service '{service}'.")
    slugs, people = _clean_subjects(rule)
    subjects = [f"group:{s}" for s in slugs] + people

    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        # ACC-1 fix round 1: the row lock first, before the checks that the
        # groups and the people exist. A member write to a group, a group
        # delete and a member purge each lock the same row first
        # (`_common.ORG_RULE_LOCKS`). So none of them can run between the
        # check and the write below.
        stored, _readable = parse_rules(
            await org_integration_rules(db, org_id, lock="update"))
        services = await closed_list(db, org_id)
        clearing_stale = rule.mode == "everyone" and service in stored
        if service not in services and not clearing_stale:
            raise _unprocessable(f"Unknown service '{service}'.")
        await _check_known(db, org_id, slugs, people)

        if rule.mode == "everyone":
            result = await db.execute(text(REMOVE_RULE_SQL), {"svc": service, "org": org_id})
        else:
            value = rule_value(subjects, set_by=(admin.email or "").lower(),
                               set_at=datetime.now(UTC).isoformat())
            result = await db.execute(
                text(SET_RULE_SQL),
                {"svc": service, "rule": json.dumps(value), "org": org_id},
            )
        if getattr(result, "rowcount", 0) != 1:
            raise HTTPException(status_code=500, detail="The rule was not saved.")

        raw = await org_integration_rules(db, org_id)
        after, readable = parse_rules(raw)
        want = None if rule.mode == "everyone" else frozenset(subjects)
        if not readable or after.get(service) != want:
            raise HTTPException(status_code=500, detail="The rule did not read back.")

    record_admin_change(
        admin.email, "org.integration_access_set", f"integration:{service}",
        mode=rule.mode, subjects=subjects,
    )
    invalidate_access()
    _log.info("integration_access_set", service=service, mode=rule.mode, by=admin.email)
    return _item(service, services.get(service, ""), _decoded(raw).get(service),
                 after.get(service), listed=service in services, readable=True)


async def scrub_member_rules(db: Any, org_id: str, email: str) -> int:
    """Take a purged member's address off each rule of *org_id* (ACC-1 fix round 1).

    The member purge (``members.purge_member_rows``) calls this in its own
    transaction, as its FIRST statement. It reads the rules with the
    ``update`` row lock (``_common.ORG_RULE_LOCKS``), so no PUT and no group
    gate runs between the read and the write. Returns the number of rules
    that changed.

    Without it, the address stays on a rule after the ``app_user`` row goes.
    A PUT of the stored list then gets 422, and a later invite of the same
    address passes the rule, which §3.9.2 rule 5 forbids.

    A rule only gets narrower here. A rule that becomes empty stays, and it
    then means admins only, plus its teams. A value or a rule that the code
    cannot read stays as it is, because it already means admins only.
    """
    raw = await org_integration_rules(db, org_id, lock="update")
    scrubbed, changed = without_member(raw, email)
    if not changed:
        return 0
    result = await db.execute(
        text(SCRUB_RULES_SQL), {"rules": json.dumps(scrubbed), "org": org_id})
    if getattr(result, "rowcount", 0) != 1:
        # The purge must not commit with the address still on a rule.
        raise HTTPException(
            status_code=500,
            detail="Could not take the member off who can use each integration.",
        )
    _log.info("integration_access_member_scrubbed", rules=changed)
    return changed
