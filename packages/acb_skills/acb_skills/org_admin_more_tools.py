"""More chat tools of the ``org-admin`` pack (PAR-4a, Metorite task 3107).

Spec: ``project-docs/specs/chat_parity.md`` section 6.3, rung 3. These tools
let an admin do from the chat what the Groups, Roles and Access parts of
Organisation do. They join the four tools of PAR-2a (``org_admin_tools``) in
the same dark pack, and they use its helpers: the call as the member, the
plain words of a refusal, the fence of a name and the one card.

The reads, with no card:

* :func:`list_org_groups` reads the groups with their members (``GET /admin/groups``).
* :func:`list_roles_and_rights` reads the roles (``GET /admin/roles``) and the
  words of each right (``GET /admin/features``).
* :func:`read_member_access` reads the resolved access of one member
  (``GET /admin/members/{email}/access``).
* :func:`preview_member_removal` reads what a removal would hand over and seal
  (``GET /admin/members/{email}/seal-preview``). It removes nobody.

The writes, each after ONE card:

* :func:`create_org_group`, :func:`edit_org_group`, :func:`delete_org_group`,
  :func:`add_org_group_member` and :func:`remove_org_group_member` send the
  group routes (``admin:members:manage``).
* :func:`create_org_role` and :func:`delete_org_role` send the role routes
  (``admin:roles:manage``).
* :func:`set_member_overrides` replaces the allow and deny overrides of one
  member (``admin:access:manage``).
* :func:`rename_org` changes the display name of the organization
  (``admin:settings:manage``).

The removal and the purge of a member (PAR-4c, task 3935), each after ONE card
(``admin:members:manage``):

* :func:`remove_member` sends ``DELETE /admin/members/{email}``. Its card shows
  the status, the roles and the numbers of the seal preview.
* :func:`purge_member` sends ``DELETE /admin/members/{email}/purge``, a delete
  for good. It refuses, with no card and no request, unless the member's OWN
  typed words of this turn hold the exact address. A run with no typed words,
  a delegated run and a spawned child have none. Its card says that the act is
  one-way and lists what is lost and what is kept.

**The gateway decides.** Each tool calls the gateway as the member of the run.
No tool takes an org or a caller argument (R5). ``email`` is the address of
the TARGET member. Each write reads ``GET /auth/me`` first, so a member without
the right of the route gets plain words and no card.

**Stricter than the page** (PAR-4a lane decision, for the owner to see). A role
create and an overrides write refuse, with plain words and no card, each right
that the caller does not hold. The check reads the rights of the caller from
``GET /auth/me``. An overrides write that removes a deny counts that right too.
So the chat never raises a role or a member above the caller. The page has no
such check. The check runs again after ``approved``.

**The race rule.** A write reads its row before the card and again after
``approved``. It sends nothing when the row changed. Only ``approved`` sends,
and it sends ONE request.

**Text of other people is data.** A group name, a description, a role name, a
reason and a member name are text of people. A list puts them in ONE
``UntrustedFrame`` block, and a card fences each such value in « ». Text that
the member gives a write must hold no hidden character, by the one rule of
``hidden_text``.

Fences: ``tests/unit/test_org_admin_more_tools.py``, and the R8 suite
``tests/unit/test_org_admin_more_tools_r8.py``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal
from urllib.parse import quote

from acb_common.card_body import one_line
from acb_common.paging import paged

from acb_skills import org_admin_tools as oa
from acb_skills.hidden_text import hidden_problem, strip_hidden
from acb_skills.tool_annotations import annotate

__all__ = [
    "ORG_ADMIN_MORE_TOOLS",
    "add_org_group_member",
    "create_org_group",
    "create_org_role",
    "delete_org_group",
    "delete_org_role",
    "edit_org_group",
    "list_org_groups",
    "list_roles_and_rights",
    "preview_member_removal",
    "purge_member",
    "read_member_access",
    "remove_member",
    "remove_org_group_member",
    "rename_org",
    "set_member_overrides",
]

#: The gateway routes. ``test_chat_parity.py`` reads them from this source.
GROUPS_PATH = "/admin/groups"
ROLES_PATH = "/admin/roles"
FEATURES_PATH = "/admin/features"
ORG_PATH = "/admin/organization"

#: The right of each group write (``groups.py``).
MANAGE_RIGHT = oa.MANAGE_RIGHT
#: The right of the role writes (``roles.py``).
ROLES_RIGHT = "admin:roles:manage"
#: The right of the overrides write (``members.py``), and of a Center grant.
ACCESS_RIGHT = "admin:access:manage"
#: The right of the rename of the organization (``organization.py``).
SETTINGS_RIGHT = "admin:settings:manage"

#: The group slugs that the route refuses (``groups.py`` ``RESERVED_GROUP_SLUGS``).
#: ``all`` means every team. ``acb_skills`` cannot import the gateway, so this is
#: a copy, and ``test_org_admin_more_tools.py`` fails when the two differ.
RESERVED_GROUP_SLUGS: frozenset[str] = frozenset({"all"})
#: The roles of a member in a group (``groups.py`` ``MEMBER_ROLES``).
GROUP_ROLES = ("member", "lead")
#: The effects of an override.
EFFECTS = ("allow", "deny")

#: The most groups of one page.
MAX_GROUPS = 30
#: The most characters of a group or a role name that a member gives.
MAX_NAME_CHARS = 120
#: The most characters of a description that a member gives.
MAX_DESC_CHARS = 1000
#: The most characters of the name of the organization (``organization.py``).
MAX_ORG_NAME_CHARS = 80
#: The most characters of the reason of an override.
MAX_REASON_CHARS = 200
#: The most rights in one role, and the most overrides in one write.
MAX_RIGHTS = 50
#: The most characters of a description in a result or on a card.
MAX_SHOWN_DESC = 300

_BAD_GROUP = ("slug is the short name of a group, as the group list shows it: 1 to 48 "
              "letters, digits, '_' or '-'. Nothing changed.")
_BAD_ROLE = ("slug is the short name of a role, as the role list shows it: 1 to 48 "
             "letters, digits, '_' or '-'. Nothing changed.")
_BAD_GROUP_ROLE = "role is member or lead. Nothing changed."
_BAD_TEXT = "{field} is text of at most {limit} characters. Nothing changed."
_FENCE_MARK = ("{field} holds « or ». A card marks the text of people with them, so leave "
               "them out. Nothing changed.")
_RESERVED_GROUP = ("The short name {slug} is reserved, because it means every team. Give the "
                   "group another short name. Nothing changed.")
#: What the lead role gives (``routes/projects/report_scope.py``: a lead reports
#: on each team it leads, and on each member of those teams).
_LEAD_WORDS = ("As a lead, the member can open a Projects report on each group that the "
               "member leads, and on each member of those groups.")
_NO_CHANGE_ASKED = "Send a new display_name, a new description, or both. Nothing changed."
_BAD_RIGHTS = ("permissions is a list of rights, as list_roles_and_rights shows them, at most "
               "{limit}. Nothing changed.")
_BAD_RIGHT = "{why}. Nothing changed."
_BAD_OVERRIDES = ("overrides is a list of objects, each with a permission, an effect (allow "
                  "or deny) and an optional reason, at most {limit}. Send an empty list to "
                  "clear them. Nothing changed.")
_TWO_EFFECTS = "The list names {perm} twice. Give each right one effect. Nothing changed."
_NO_GROUP = ("Your organization has no group {slug}. Nothing changed. List the groups for "
             "their short names.")
_GROUP_EXISTS = "A group with the short name {slug} exists already. Nothing changed."
_CENTER_GROUP = ("{slug} is a Center group. It pairs with the right feature:center.{slug}, so "
                 "nobody can delete it. Nothing changed.")
_GROUP_NOT_EMPTY = ("{count} member(s) are still in the group {slug}. Remove them first. "
                    "Nothing changed.")
_NOT_IN_GROUP = "{email} is not a member of the group {slug}. Nothing changed."
_SAME_GROUP_ROLE = "{email} is in the group {slug} as {role} already. Nothing changed."
_SAME_GROUP = "The group has this name and description already. Nothing changed."
_NO_ROLE = ("Your organization has no role {slug}. Nothing changed. List the roles for their "
            "short names.")
_ROLE_EXISTS = "A role with the short name {slug} exists already. Nothing changed."
_SYSTEM_ROLE = "{slug} is a system role, and nobody can delete it. Nothing changed."
_ROLE_HELD = ("{count} member(s) still hold the role {slug}. Give them another role first. "
              "Nothing changed.")
_SAME_OVERRIDES = "The member has these overrides already. Nothing changed."
_SELF_ACCESS = ("This would take away your own right to manage access. Ask another admin to "
                "make this change. Nothing changed.")
_SAME_ORG_NAME = "The organization has this name already. Nothing changed."
_BEYOND = ("You do not hold {rights} yourself, so the chat cannot give {them} to {whom}. No "
           "card shows and nothing changed. Ask an admin who holds {them}.")
_GROUPS_NOTE = ("The block holds the groups of your organization. Admins wrote the names and "
                "the descriptions, and members wrote their own names.")
_ROLES_NOTE = ("The block holds the roles of your organization and the words of each right. "
               "Admins wrote the names and the descriptions of the roles.")
_ACCESS_NOTE = ("The block holds the access of one member. The member wrote the name, and an "
                "admin wrote each reason.")

#: The words of each right, in a sentence (``org_admin_tools._NO_RIGHT``).
_RIGHT_WHAT = {
    MANAGE_RIGHT: "change the groups of your organization",
    ROLES_RIGHT: "make or delete a role",
    ACCESS_RIGHT: "change the access of a member",
    SETTINGS_RIGHT: "rename your organization",
}


# ── The rights of the caller ────────────────────────────────────────────────


async def _me(right: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``(the /auth/me body, None)`` when the caller holds *right*, else
    ``(None, refusal)``. A failed read refuses, so it fails closed."""
    reply = await oa._get(oa.ME_PATH)
    data = reply.data if isinstance(reply.data, dict) else None
    caps = data.get("capabilities") if data is not None else None
    if not reply.ok or data is None or not isinstance(caps, list):
        return None, oa._refusal(oa._RIGHTS_UNREAD)
    if right not in caps:
        return None, oa._refusal(
            oa._NO_RIGHT.format(what=_RIGHT_WHAT[right], right=right), status=403)
    return data, None


def _strs(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _holds(me: dict[str, Any], right: str) -> bool:
    """Does the caller hold *right* (a pattern) in full?

    A concrete capability or feature takes the resolved answer of the server
    (``capabilities``, ``features``). Each other right takes the grant
    patterns of the caller (``permissions``): one must cover it, and no deny
    of the caller may touch it. ``acb_auth`` holds the one match rule, so the
    tool keeps no copy. A deny that touches a wide right refuses, so the check
    fails closed."""
    from acb_auth.permissions import CAPABILITIES, matched_by, permission_matches

    if "*" not in right:
        if right in _strs(me.get("capabilities")):
            return True
        if right.startswith("feature:"):
            slug = right[len("feature:"):]
            if slug in _strs(me.get("features")):
                return True
            denied = me.get("features_denied")
            if isinstance(denied, list) and any(
                    isinstance(d, dict) and d.get("slug") == slug for d in denied):
                return False
        if right in CAPABILITIES:
            return False
    if matched_by(_strs(me.get("permissions")), right) is None:
        return False
    return not any(permission_matches(d, right) or permission_matches(right, d)
                   for d in _strs(me.get("denied")))


def _beyond(me: dict[str, Any], rights: list[str], whom: str) -> dict[str, Any] | None:
    """The stricter-than-page refusal for each right that the caller lacks."""
    missing = [r for r in rights if not _holds(me, r)]
    if not missing:
        return None
    one = len(missing) == 1
    return oa._refusal(_BEYOND.format(rights=", ".join(missing), them="it" if one else "them",
                                      whom=whom), status=403, beyond=missing)


def _wide(right: str) -> bool:
    """A right that a card flags: every right, an admin right or every feature."""
    return right in ("*", "feature:*") or right.startswith("admin:")


# ── The arguments ───────────────────────────────────────────────────────────


def _slug(raw: Any, space: str) -> str | None:
    """*raw* as a slug, as the route makes one, or None. *space* is the
    character that the route puts for a space."""
    if not isinstance(raw, str):
        return None
    slug = raw.strip().lower().replace(" ", space)
    return slug if oa._slug_shape(slug) else None


def _seg(slug: str) -> str:
    """A slug as one path segment."""
    return quote(slug, safe="")


def _text(raw: Any, field: str, limit: int,
          flat: bool = False) -> tuple[str | None, dict[str, Any] | None]:
    """``(text, None)``, ``(None, None)`` for no value, or ``(None, refusal)``.
    A text with a hidden character is refused by the one rule. A text with « or
    » is refused, because the card cannot show it as it is sent. *flat* makes
    each run of white space one space, so a name is sent as the card shows it."""
    if raw is None:
        return None, None
    if not isinstance(raw, str) or len(raw.strip()) > limit:
        return None, oa._refusal(_BAD_TEXT.format(field=field, limit=limit))
    problem = hidden_problem(raw, field)
    if problem is not None:
        return None, oa._refusal(f"{problem} Nothing changed.")
    if "«" in raw or "»" in raw:
        return None, oa._refusal(_FENCE_MARK.format(field=field))
    return (one_line(raw) if flat else raw.strip()), None


def _list_arg(raw: Any) -> list[Any] | None:
    """A list, or a list as JSON text, or None."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, list) else None


def _right(raw: Any) -> tuple[str | None, dict[str, Any] | None]:
    """One right, as ``acb_auth.validate_permission`` makes it, or a refusal."""
    from acb_auth.permissions import InvalidPermission, validate_permission

    if not isinstance(raw, str):
        return None, oa._refusal(_BAD_RIGHT.format(why="Each right is text"))
    try:
        return validate_permission(raw), None
    except InvalidPermission as exc:
        return None, oa._refusal(_BAD_RIGHT.format(why=str(exc)))


def _rights_arg(raw: Any) -> tuple[list[str] | None, dict[str, Any] | None]:
    items = [] if raw is None else _list_arg(raw)
    if items is None or len(items) > MAX_RIGHTS:
        return None, oa._refusal(_BAD_RIGHTS.format(limit=MAX_RIGHTS))
    out: list[str] = []
    for item in items:
        right, problem = _right(item)
        if problem is not None or right is None:
            return None, problem
        if right not in out:
            out.append(right)
    return out, None


def _overrides_arg(raw: Any) -> tuple[list[dict[str, str]] | None, dict[str, Any] | None]:
    """The overrides as ``[{permission, effect, reason}]``, or a refusal."""
    items = _list_arg(raw)
    bad = oa._refusal(_BAD_OVERRIDES.format(limit=MAX_RIGHTS))
    if items is None or len(items) > MAX_RIGHTS:
        return None, bad
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            return None, bad
        effect = str(item.get("effect") or "").strip().lower()
        if effect not in EFFECTS:
            return None, bad
        right, problem = _right(item.get("permission"))
        if problem is not None or right is None:
            return None, problem
        if any(o["permission"] == right for o in out):
            return None, oa._refusal(_TWO_EFFECTS.format(perm=right))
        reason, problem = _text(item.get("reason") or "", "reason", MAX_REASON_CHARS,
                                flat=True)
        if problem is not None:
            return None, problem
        out.append({"permission": right, "effect": effect, "reason": reason or ""})
    return out, None


# ── The text of other people ────────────────────────────────────────────────


def _desc(value: Any) -> str:
    return oa._cut(strip_hidden(value) if isinstance(value, str) else "", MAX_SHOWN_DESC)


def _whole(value: Any) -> str:
    """A value on a card, whole, between « and ». It is never cut here (D169).
    ``card_body`` cuts only a long text, and its last line then says so."""
    text = one_line(strip_hidden(value)) if isinstance(value, str) else ""
    return "«" + text.replace("«", "").replace("»", "") + "»"


def _fence_desc(value: Any) -> str:
    return _whole(value)


# ── The reads ───────────────────────────────────────────────────────────────


def _dicts(data: Any, key: str) -> list[dict[str, Any]] | None:
    if not isinstance(data, list):
        return None
    return [r for r in data if isinstance(r, dict) and isinstance(r.get(key), str)]


async def _groups() -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    reply = await oa._get(GROUPS_PATH)
    if not reply.ok:
        return None, oa._fail(reply)
    rows = _dicts(reply.data, "slug")
    return (rows, None) if rows is not None else (None, oa._refusal(oa._SHAPE))


async def _group(slug: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    rows, problem = await _groups()
    if problem is not None or rows is None:
        return None, problem or oa._refusal(oa._SHAPE)
    row = next((g for g in rows if g["slug"] == slug), None)
    if row is None:
        return None, oa._refusal(_NO_GROUP.format(slug=slug), status=404)
    return row, None


def _group_members(row: dict[str, Any]) -> list[dict[str, Any]]:
    return _dicts(row.get("members"), "email") or []


def _in_group(row: dict[str, Any], email: str) -> dict[str, Any] | None:
    return next((m for m in _group_members(row)
                 if m["email"].strip().lower() == email), None)


def _group_snap(row: dict[str, Any]) -> tuple[Any, ...]:
    """What a group card must still match when the member answers."""
    members = sorted((m["email"].strip().lower(), str(m.get("role")))
                     for m in _group_members(row))
    return (row.get("display_name"), row.get("description"), tuple(members))


async def _roles() -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    reply = await oa._get(ROLES_PATH)
    if not reply.ok:
        return None, oa._fail(reply)
    rows = _dicts(reply.data, "slug")
    return (rows, None) if rows is not None else (None, oa._refusal(oa._SHAPE))


def _role_snap(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("display_name"), row.get("description"), row.get("is_system") is True,
            tuple(_strs(row.get("permissions"))), row.get("member_count"))


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _group_row(g: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": g["slug"],
        "name": oa._name(g.get("display_name")),
        "description": _desc(g.get("description")),
        "is_center": g.get("is_center") is True,
        "member_count": _count(g.get("member_count")),
        "members": [{"email": m["email"], "name": oa._name(m.get("display_name")),
                     "role": "lead" if m.get("role") == "lead" else "member"}
                    for m in _group_members(g)],
    }


@annotate(read_only=True, idempotent=True, open_world=False)
async def list_org_groups(offset: int = 0, limit: int = MAX_GROUPS) -> dict[str, Any]:
    """List the groups of your organization with their members."""
    try:
        rows, problem = await _groups()
    except oa._Refused as exc:
        return oa._refusal(str(exc))
    if problem is not None or rows is None:
        return problem or oa._refusal(oa._SHAPE)
    from acb_skills.untrusted import UntrustedFrame

    page = paged(rows, offset, limit, default=MAX_GROUPS, maximum=MAX_GROUPS, noun="groups")
    frame = UntrustedFrame.new()
    block = json.dumps([_group_row(g) for g in page.items], ensure_ascii=False,
                       separators=(",", ":"))
    return {"ok": True, "rows": frame.wrap("ORGANIZATION GROUPS", block),
            "groups_total": page.total, "groups_shown": len(page.items), "page": page.line,
            "note": frame.note(_GROUPS_NOTE)}


def _role_row(r: dict[str, Any]) -> dict[str, Any]:
    return {"slug": r["slug"], "name": oa._name(r.get("display_name")),
            "description": _desc(r.get("description")), "system": r.get("is_system") is True,
            "members": _count(r.get("member_count")),
            "rights": _strs(r.get("permissions"))}


def _feature_row(f: Any) -> dict[str, str] | None:
    if not isinstance(f, dict) or not isinstance(f.get("permission"), str):
        return None
    return {"right": f["permission"], "label": oa._name(f.get("label"))}


@annotate(read_only=True, idempotent=True, open_world=False)
async def list_roles_and_rights() -> dict[str, Any]:
    """List the roles of your organization with their rights, and the words of each right that a role or an override can name."""
    try:
        roles, problem = await _roles()
        if problem is not None or roles is None:
            return problem or oa._refusal(oa._SHAPE)
        vocab = await oa._get(FEATURES_PATH)
    except oa._Refused as exc:
        return oa._refusal(str(exc))
    if not vocab.ok:
        return oa._fail(vocab)
    data = vocab.data if isinstance(vocab.data, dict) else {}
    features = [row for row in map(_feature_row, data.get("features") or []) if row]
    from acb_skills.untrusted import UntrustedFrame

    frame = UntrustedFrame.new()
    block = json.dumps({"roles": [_role_row(r) for r in roles], "features": features,
                        "capabilities": _strs(data.get("capabilities"))},
                       ensure_ascii=False, separators=(",", ":"))
    return {"ok": True, "rows": frame.wrap("ROLES AND RIGHTS", block),
            "roles_total": len(roles), "features_total": len(features),
            "note": frame.note(_ROLES_NOTE)}


def _decided(rows: Any, key: str) -> tuple[list[str], list[str]]:
    """``(allowed, refused)`` names of the resolved rows of an access read."""
    yes: list[str] = []
    no: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and isinstance(row.get(key), str):
            (yes if row.get("allowed") is True else no).append(row[key])
    return yes, no


def _override_rows(data: Any) -> list[dict[str, str]]:
    rows = data.get("overrides") if isinstance(data, dict) else None
    out: list[dict[str, str]] = []
    for o in rows if isinstance(rows, list) else []:
        if isinstance(o, dict) and isinstance(o.get("permission"), str):
            out.append({"permission": o["permission"], "effect": str(o.get("effect")),
                        "reason": o.get("reason") if isinstance(o.get("reason"), str) else ""})
    return out


async def _access(email: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    reply = await oa._get(f"/admin/members/{oa._path_email(email)}/access")
    if reply.status == 404:
        return None, oa._refusal(oa._NOT_IN_ORG.format(email=email), status=404)
    if not reply.ok:
        return None, oa._fail(reply)
    if not isinstance(reply.data, dict):
        return None, oa._refusal(oa._SHAPE)
    return reply.data, None


@annotate(read_only=True, idempotent=True, open_world=False)
async def read_member_access(email: str) -> dict[str, Any]:
    """Read what one member of your organization can reach, and why: the roles, each right and each override."""
    target = oa._email_arg(email)
    if target is None:
        return oa._refusal(oa._BAD_EMAIL)
    try:
        data, problem = await _access(target)
    except oa._Refused as exc:
        return oa._refusal(str(exc))
    if problem is not None or data is None:
        return problem or oa._refusal(oa._SHAPE)
    features, no_features = _decided(data.get("features"), "slug")
    caps, no_caps = _decided(data.get("capabilities"), "permission")
    agents, _ = _decided(data.get("agents"), "name")
    tools, _ = _decided(data.get("integrations"), "name")
    from acb_skills.untrusted import UntrustedFrame

    frame = UntrustedFrame.new()
    block = json.dumps({
        "email": target, "name": oa._name(data.get("display_name")),
        "status": oa._STATUS_WORDS.get(str(data.get("status")), "other"),
        "roles": _strs(data.get("roles")), "features": features,
        "features_refused": no_features, "rights": caps, "rights_refused": no_caps,
        "agents": agents, "integrations": tools,
        "overrides": [{"right": o["permission"], "effect": o["effect"],
                       "reason": oa._name(o["reason"])} for o in _override_rows(data)],
    }, ensure_ascii=False, separators=(",", ":"))
    return {"ok": True, "rows": frame.wrap("MEMBER ACCESS", block),
            "note": frame.note(_ACCESS_NOTE)}


@annotate(read_only=True, idempotent=True, open_world=False)
async def preview_member_removal(email: str) -> dict[str, Any]:
    """Read what a removal of one member would hand over and seal, in numbers; it removes nobody."""
    target = oa._email_arg(email)
    if target is None:
        return oa._refusal(oa._BAD_EMAIL)
    try:
        reply = await oa._get(f"/admin/members/{oa._path_email(target)}/seal-preview")
    except oa._Refused as exc:
        return oa._refusal(str(exc))
    if reply.status == 404:
        return oa._refusal(oa._NOT_IN_ORG.format(email=target), status=404)
    if not reply.ok:
        return oa._fail(reply)
    data = reply.data if isinstance(reply.data, dict) else {}
    counts = _seal_counts(data)
    return {"ok": True, "email": target,
            "status": oa._STATUS_WORDS.get(str(data.get("status")), "other"), **counts,
            "deleted": data.get("deleted") is True, "reversible": data.get("reversible") is True,
            "note": (f"{counts['tasks']} tasks: {counts['handed_over']} handed over, "
                     f"{counts['sealed']} sealed, not deleted. This is a read, and nothing "
                     "changed. remove_member removes the member after one card.")}


def _seal_counts(data: dict[str, Any]) -> dict[str, int]:
    """The numbers of a seal preview (``members.py`` ``get_seal_preview``)."""
    return {k: _count(data.get(k)) for k in ("projects", "tasks", "handed_over", "sealed")}


# ── The group writes ────────────────────────────────────────────────────────


def _group_line(slug: str, row: dict[str, Any] | None = None, name: str | None = None) -> str:
    shown = name if name is not None else (row or {}).get("display_name")
    return f"Group: {slug} {_whole(shown)}"


async def _group_still(slug: str, row: dict[str, Any], decision: str) -> dict[str, Any] | None:
    """None when the group is as the card showed it, else a refusal."""
    again, problem = await _group(slug)
    if problem is not None and problem.get("status") == 404:
        return oa._refusal(oa._CHANGED.format(what="group"), decision=decision)
    if problem is not None or again is None:
        return problem or oa._refusal(oa._SHAPE)
    if _group_snap(again) != _group_snap(row):
        return oa._refusal(oa._CHANGED.format(what="group"), decision=decision)
    return None


@annotate(read_only=False, idempotent=False, open_world=False)
async def create_org_group(slug: str, display_name: str = "",
                           description: str = "") -> dict[str, Any]:
    """Make a new group in your organization, after one card."""
    key = _slug(slug, "-")
    if key is None:
        return oa._refusal(_BAD_GROUP)
    if key in RESERVED_GROUP_SLUGS:
        return oa._refusal(_RESERVED_GROUP.format(slug=key), status=400)
    name, problem = _text(display_name or "", "display_name", MAX_NAME_CHARS, flat=True)
    if problem is not None:
        return problem
    desc, problem = _text(description or "", "description", MAX_DESC_CHARS)
    if problem is not None:
        return problem
    try:
        _caller, problem = await _me(MANAGE_RIGHT)
        if problem is not None:
            return problem
        rows, problem = await _groups()
        if problem is not None or rows is None:
            return problem or oa._refusal(oa._SHAPE)
        if any(g["slug"] == key for g in rows):
            return oa._refusal(_GROUP_EXISTS.format(slug=key), status=409)
        decision, _ = await oa._decide(
            f"Make the group {key}?", key,
            "Approve to make this group, with no members. Reject to make nothing.",
            [("group", _group_line(key, name=name or key)),
             ("description", f"Description: {_fence_desc(desc)}")])
        if decision != "approved":
            return oa._not_done(decision)
        rows, problem = await _groups()
        if problem is not None or rows is None:
            return problem or oa._refusal(oa._SHAPE)
        if any(g["slug"] == key for g in rows):
            return oa._refusal(oa._CHANGED.format(what="group list"), decision=decision)
        sent = await oa._send("POST", GROUPS_PATH,
                              {"slug": key, "display_name": name or key, "description": desc or ""},
                              act="group create")
    except oa._Refused as exc:
        return oa._refusal(str(exc))
    if not sent.ok:
        return oa._fail(sent, decision=decision)
    return {"ok": True, "decision": decision, "slug": key, "note": "The group exists now."}


def _edit_args(
    slug: Any, display_name: Any, description: Any,
) -> tuple[str, str | None, str | None] | dict[str, Any]:
    """``(slug, name, description)`` of :func:`edit_org_group`, or a refusal."""
    key = _slug(slug, "-")
    if key is None:
        return oa._refusal(_BAD_GROUP)
    name, problem = _text(display_name, "display_name", MAX_NAME_CHARS, flat=True)
    if problem is not None:
        return problem
    desc, problem = _text(description, "description", MAX_DESC_CHARS)
    if problem is not None:
        return problem
    if not name and desc is None:
        return oa._refusal(_NO_CHANGE_ASKED)
    return key, name, desc


def _edit_lines(key: str, row: dict[str, Any], name: str | None,
                desc: str | None) -> list[tuple[str, str]]:
    lines = [("group", _group_line(key, row))]
    if name:
        lines.append(("name", f"Name after: {_whole(name)}"))
    if desc is not None:
        # Both lines are long text, so card_body may cut either and marks the cut.
        lines += [("description", f"Description now: {_fence_desc(row.get('description'))}"),
                  ("description", f"Description after: {_fence_desc(desc)}")]
    return lines


@annotate(read_only=False, idempotent=False, open_world=False)
async def edit_org_group(slug: str, display_name: str | None = None,
                         description: str | None = None) -> dict[str, Any]:
    """Change the name or the description of one group of your organization, after one card."""
    args = _edit_args(slug, display_name, description)
    if isinstance(args, dict):
        return args
    key, name, desc = args
    try:
        _caller, problem = await _me(MANAGE_RIGHT)
        if problem is not None:
            return problem
        row, problem = await _group(key)
        if problem is not None or row is None:
            return problem or oa._refusal(oa._SHAPE)
        if ((not name or name == row.get("display_name"))
                and (desc is None or desc == (row.get("description") or ""))):
            return {"ok": True, "slug": key, "note": _SAME_GROUP}
        decision, _ = await oa._decide(
            f"Change the group {key}?", key,
            "Approve to change the name or the description. The short name stays. Reject to "
            "leave the group as it is.", _edit_lines(key, row, name, desc))
        if decision != "approved":
            return oa._not_done(decision)
        changed = await _group_still(key, row, decision)
        if changed is not None:
            return changed
        body: dict[str, Any] = {}
        if name:
            body["display_name"] = name
        if desc is not None:
            body["description"] = desc
        sent = await oa._send("PATCH", f"/admin/groups/{_seg(key)}", body, act="group change")
    except oa._Refused as exc:
        return oa._refusal(str(exc))
    if not sent.ok:
        return oa._fail(sent, decision=decision)
    return {"ok": True, "decision": decision, "slug": key, "note": "The group changed."}


@annotate(read_only=False, idempotent=False, open_world=False)
async def delete_org_group(slug: str) -> dict[str, Any]:
    """Delete one empty group of your organization, after one card."""
    key = _slug(slug, "-")
    if key is None:
        return oa._refusal(_BAD_GROUP)
    try:
        _caller, problem = await _me(MANAGE_RIGHT)
        if problem is not None:
            return problem
        row, problem = await _group(key)
        if problem is not None or row is None:
            return problem or oa._refusal(oa._SHAPE)
        if row.get("is_center") is True:
            return oa._refusal(_CENTER_GROUP.format(slug=key), status=403)
        count = len(_group_members(row))
        if count:
            return oa._refusal(_GROUP_NOT_EMPTY.format(count=count, slug=key), status=409)
        decision, _ = await oa._decide(
            f"Delete the group {key}?", key,
            f"Approve to delete this group. A room or a grant that names group:{key} then "
            "reaches nobody. Reject to keep the group.",
            [("group", _group_line(key, row)), ("members", f"Members: {count}"),
             ("description", f"Description: {_fence_desc(row.get('description'))}")])
        if decision != "approved":
            return oa._not_done(decision)
        changed = await _group_still(key, row, decision)
        if changed is not None:
            return changed
        sent = await oa._send("DELETE", f"/admin/groups/{_seg(key)}", act="group delete")
    except oa._Refused as exc:
        return oa._refusal(str(exc))
    if not sent.ok:
        return oa._fail(sent, decision=decision)
    return {"ok": True, "decision": decision, "slug": key, "note": "The group is deleted."}


def _center_grant_problem(caller: dict[str, Any], key: str) -> dict[str, Any] | None:
    """The refusal of a Center grant that the caller cannot give, or None."""
    caps = _strs(caller.get("capabilities"))
    if ACCESS_RIGHT not in caps:
        return oa._refusal(oa._NO_RIGHT.format(what=_RIGHT_WHAT[ACCESS_RIGHT],
                                               right=ACCESS_RIGHT), status=403)
    return _beyond(caller, [f"feature:center.{key}"], "a member")


async def _center_still(key: str, decision: str) -> dict[str, Any] | None:
    """After ``approved``: None when the caller can still give the Center
    grant, else a refusal. It reads ``GET /auth/me`` again."""
    again, problem = await _me(MANAGE_RIGHT)
    if problem is not None or again is None:
        return problem or oa._refusal(oa._SHAPE)
    problem = _center_grant_problem(again, key)
    return None if problem is None else {**problem, "decision": decision}


def _member_lines(key: str, row: dict[str, Any], target: str, person: dict[str, Any],
                  now: dict[str, Any] | None, want: str, grant: bool) -> list[tuple[str, str]]:
    lines = [("group", _group_line(key, row)),
             ("member", f"Member: {target} {oa._fence(person.get('display_name'))}"),
             ("role", f"Role in the group: {now.get('role') if now else 'not in it'}. "
                      f"After: {want}.")]
    if want == "lead":
        lines.append(("lead", _LEAD_WORDS))
    if grant:
        lines.append(("grant", f"Also gives the member the right feature:center.{key}."))
    return lines


@annotate(read_only=False, idempotent=False, open_world=False)
async def add_org_group_member(
    slug: str, email: str, role: Literal["member", "lead"] = "member",
    grant_center_access: bool = False,
) -> dict[str, Any]:
    """Add one member to a group of your organization, or change the member's role in it, after one card."""
    key = _slug(slug, "-")
    if key is None:
        return oa._refusal(_BAD_GROUP)
    target = oa._email_arg(email)
    if target is None:
        return oa._refusal(oa._BAD_EMAIL)
    want = str(role or "member").strip().lower()
    if want not in GROUP_ROLES:
        return oa._refusal(_BAD_GROUP_ROLE)
    try:
        caller, problem = await _me(MANAGE_RIGHT)
        if problem is not None or caller is None:
            return problem or oa._refusal(oa._SHAPE)
        row, problem = await _group(key)
        if problem is not None or row is None:
            return problem or oa._refusal(oa._SHAPE)
        grant = grant_center_access is True and row.get("is_center") is True
        if grant:
            problem = _center_grant_problem(caller, key)
            if problem is not None:
                return problem
        person, problem = await oa._member(target)
        if problem is not None or person is None:
            return problem or oa._refusal(oa._SHAPE)
        now = _in_group(row, target)
        if now is not None and now.get("role") == want and not grant:
            return {"ok": True, "slug": key, "email": target,
                    "note": _SAME_GROUP_ROLE.format(email=target, slug=key, role=want)}
        lines = _member_lines(key, row, target, person, now, want, grant)
        decision, _ = await oa._decide(
            f"Add {target} to the group {key}?", f"{key}: {want}",
            "Approve to put this member in the group. The member then shares what is shared "
            "with the group. Reject to leave the group as it is.", lines)
        if decision != "approved":
            return oa._not_done(decision)
        changed = await _group_still(key, row, decision)
        if changed is not None:
            return changed
        problem = await _center_still(key, decision) if grant else None
        if problem is not None:
            return problem
        sent = await oa._send("POST", f"/admin/groups/{_seg(key)}/members",
                              {"email": target, "role": want, "grant_center_access": grant},
                              act="group member add")
    except oa._Refused as exc:
        return oa._refusal(str(exc))
    if not sent.ok:
        return oa._fail(sent, decision=decision)
    data = sent.data if isinstance(sent.data, dict) else {}
    return {"ok": True, "decision": decision, "slug": key, "email": target, "role": want,
            "center_access_granted": data.get("center_access_granted") is True,
            "note": "The member is in the group now."}


@annotate(read_only=False, idempotent=False, open_world=False)
async def remove_org_group_member(slug: str, email: str) -> dict[str, Any]:
    """Take one member out of a group of your organization, after one card."""
    key = _slug(slug, "-")
    if key is None:
        return oa._refusal(_BAD_GROUP)
    target = oa._email_arg(email)
    if target is None:
        return oa._refusal(oa._BAD_EMAIL)
    try:
        _caller, problem = await _me(MANAGE_RIGHT)
        if problem is not None:
            return problem
        row, problem = await _group(key)
        if problem is not None or row is None:
            return problem or oa._refusal(oa._SHAPE)
        now = _in_group(row, target)
        if now is None:
            return oa._refusal(_NOT_IN_GROUP.format(email=target, slug=key), status=404)
        decision, _ = await oa._decide(
            f"Take {target} out of the group {key}?", key,
            "Approve to take this member out of the group. The member stops seeing what is "
            "shared with the group. A Center right that the member holds stays. Reject to "
            "leave the group as it is.",
            [("group", _group_line(key, row)),
             ("member", f"Member: {target} {oa._fence(now.get('display_name'))}"),
             ("role", f"Role in the group: {now.get('role')}")])
        if decision != "approved":
            return oa._not_done(decision)
        changed = await _group_still(key, row, decision)
        if changed is not None:
            return changed
        sent = await oa._send("DELETE", f"/admin/groups/{_seg(key)}/members/{oa._path_email(target)}",
                              act="group member removal")
    except oa._Refused as exc:
        return oa._refusal(str(exc))
    if not sent.ok:
        return oa._fail(sent, decision=decision)
    return {"ok": True, "decision": decision, "slug": key, "email": target,
            "note": "The member is out of the group now."}


# ── The role writes ─────────────────────────────────────────────────────────


def _right_lines(rights: list[str], label: str) -> list[tuple[str, str]]:
    lines = [(f"r{i}", f"{label}: {r}") for i, r in enumerate(rights)]
    wide = [r for r in rights if _wide(r)]
    if wide:
        lines.append(("wide", "Warning: this gives an admin right or every feature: "
                              + ", ".join(wide) + "."))
    return lines


async def _role_still(key: str, rights: list[str], decision: str) -> dict[str, Any] | None:
    """After ``approved``: None when the caller still holds each right and no
    role took the short name, else a refusal."""
    caller, problem = await _me(ROLES_RIGHT)
    if problem is not None or caller is None:
        return problem or oa._refusal(oa._SHAPE)
    problem = _beyond(caller, rights, "a role")
    if problem is not None:
        return problem
    roles, problem = await _roles()
    if problem is not None or roles is None:
        return problem or oa._refusal(oa._SHAPE)
    if any(r["slug"] == key for r in roles):
        return oa._refusal(oa._CHANGED.format(what="role list"), decision=decision)
    return None


@annotate(read_only=False, idempotent=False, open_world=False)
async def create_org_role(slug: str, display_name: str = "", description: str = "",
                          permissions: list[str] | None = None) -> dict[str, Any]:
    """Make a new role in your organization with a set of rights, after one card; you can give only rights that you hold."""
    key = _slug(slug, "_")
    if key is None:
        return oa._refusal(_BAD_ROLE)
    name, problem = _text(display_name or "", "display_name", MAX_NAME_CHARS, flat=True)
    if problem is not None:
        return problem
    desc, problem = _text(description or "", "description", MAX_DESC_CHARS)
    if problem is not None:
        return problem
    rights, problem = _rights_arg(permissions)
    if problem is not None or rights is None:
        return problem or oa._refusal(_BAD_RIGHTS.format(limit=MAX_RIGHTS))
    try:
        caller, problem = await _me(ROLES_RIGHT)
        if problem is not None or caller is None:
            return problem or oa._refusal(oa._SHAPE)
        problem = _beyond(caller, rights, "a role")
        if problem is not None:
            return problem
        roles, problem = await _roles()
        if problem is not None or roles is None:
            return problem or oa._refusal(oa._SHAPE)
        if any(r["slug"] == key for r in roles):
            return oa._refusal(_ROLE_EXISTS.format(slug=key), status=409)
        lines = [("role", f"Role: {key} {_whole(name or key)}"),
                 ("description", f"Description: {_fence_desc(desc)}"),
                 ("count", f"Rights: {len(rights)}"), *_right_lines(rights, "Right")]
        decision, _ = await oa._decide(
            f"Make the role {key}?", f"{len(rights)} rights",
            "Approve to make this role. Nobody holds it until an admin gives it to a member. "
            "Reject to make nothing.", lines)
        if decision != "approved":
            return oa._not_done(decision)
        problem = await _role_still(key, rights, decision)
        if problem is not None:
            return problem
        sent = await oa._send("POST", ROLES_PATH,
                              {"slug": key, "display_name": name or key,
                               "description": desc or "", "permissions": rights},
                              act="role create")
    except oa._Refused as exc:
        return oa._refusal(str(exc))
    if not sent.ok:
        return oa._fail(sent, decision=decision)
    data = sent.data if isinstance(sent.data, dict) else {}
    return {"ok": True, "decision": decision, "slug": key,
            "rights": _strs(data.get("permissions")), "note": "The role exists now."}


@annotate(read_only=False, idempotent=False, open_world=False)
async def delete_org_role(slug: str) -> dict[str, Any]:
    """Delete one custom role of your organization that no member holds, after one card."""
    key = _slug(slug, "_")
    if key is None:
        return oa._refusal(_BAD_ROLE)
    try:
        _caller, problem = await _me(ROLES_RIGHT)
        if problem is not None:
            return problem
        roles, problem = await _roles()
        if problem is not None or roles is None:
            return problem or oa._refusal(oa._SHAPE)
        row = next((r for r in roles if r["slug"] == key), None)
        if row is None:
            return oa._refusal(_NO_ROLE.format(slug=key), status=404)
        if row.get("is_system") is True:
            return oa._refusal(_SYSTEM_ROLE.format(slug=key), status=403)
        holders = _count(row.get("member_count"))
        if holders:
            return oa._refusal(_ROLE_HELD.format(count=holders, slug=key), status=409)
        rights = _strs(row.get("permissions"))
        decision, _ = await oa._decide(
            f"Delete the role {key}?", key,
            "Approve to delete this role. No member holds it. Reject to keep the role.",
            [("role", f"Role: {key} {_whole(row.get('display_name'))}"),
             ("members", f"Members who hold it: {holders}"),
             ("rights", f"Rights: {', '.join(rights) or 'none'}")])
        if decision != "approved":
            return oa._not_done(decision)
        again, problem = await _roles()
        if problem is not None or again is None:
            return problem or oa._refusal(oa._SHAPE)
        now = next((r for r in again if r["slug"] == key), None)
        if now is None or _role_snap(now) != _role_snap(row):
            return oa._refusal(oa._CHANGED.format(what="role"), decision=decision)
        sent = await oa._send("DELETE", f"/admin/roles/{_seg(key)}", act="role delete")
    except oa._Refused as exc:
        return oa._refusal(str(exc))
    if not sent.ok:
        return oa._fail(sent, decision=decision)
    return {"ok": True, "decision": decision, "slug": key, "note": "The role is deleted."}


# ── The overrides write ─────────────────────────────────────────────────────


def _pairs(rows: list[dict[str, str]]) -> set[tuple[str, str]]:
    return {(o["permission"], o["effect"]) for o in rows}


def _override_snap(rows: list[dict[str, str]]) -> tuple[Any, ...]:
    return tuple(sorted((o["permission"], o["effect"], o["reason"]) for o in rows))


def _raises(new: list[dict[str, str]], now: list[dict[str, str]]) -> list[str]:
    """Each right that the write gives: each NEW allow, and each deny that it
    lifts. An allow that the member holds already as an allow gives nothing. An
    override of the member with an effect that is not ``allow`` never covers an
    allow, so a doubt counts as a grant (fail closed)."""
    held = {o["permission"] for o in now if o["effect"] == "allow"}
    denied_after = {o["permission"] for o in new if o["effect"] == "deny"}
    lifted = [o["permission"] for o in now
              if o["effect"] == "deny" and o["permission"] not in denied_after]
    added = [o["permission"] for o in new
             if o["effect"] == "allow" and o["permission"] not in held]
    out: list[str] = []
    for right in [*added, *lifted]:
        if right not in out:
            out.append(right)
    return out


def _override_lines(new: list[dict[str, str]],
                    now: list[dict[str, str]]) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for i, o in enumerate(new):
        why = f" {_whole(o['reason'])}" if o["reason"] else ""
        lines.append((f"o{i}", f"{o['effect'].title()}: {o['permission']}{why}"))
    after = _pairs(new)
    gone = [o for o in now if (o["permission"], o["effect"]) not in after]
    for i, o in enumerate(gone):
        lines.append((f"g{i}", f"Removed: {o['effect']} {o['permission']}"))
    wide = [o["permission"] for o in new if o["effect"] == "allow" and _wide(o["permission"])]
    if wide:
        lines.append(("wide", "Warning: this gives an admin right or every feature: "
                              + ", ".join(wide) + "."))
    return lines


def _denies_own_access(target: str, new: list[dict[str, str]]) -> bool:
    """Does the caller deny their own right to manage access? The route
    refuses it too (409). ``acb_auth`` holds the match rule."""
    if target != oa._current_user_email().strip().lower():
        return False
    from acb_auth.permissions import matched_by

    return any(o["effect"] == "deny" and matched_by([o["permission"]], ACCESS_RIGHT)
               for o in new)


async def _overrides_still(target: str, new: list[dict[str, str]], now: list[dict[str, str]],
                           decision: str) -> dict[str, Any] | None:
    """After ``approved``: None when the overrides are as the card showed them
    and the caller still holds each right that the write gives."""
    caller, problem = await _me(ACCESS_RIGHT)
    if problem is not None or caller is None:
        return problem or oa._refusal(oa._SHAPE)
    again, problem = await _access(target)
    if problem is not None or again is None:
        return problem or oa._refusal(oa._SHAPE)
    if _override_snap(_override_rows(again)) != _override_snap(now):
        return oa._refusal(oa._CHANGED.format(what="member"), decision=decision)
    return _beyond(caller, _raises(new, now), "a member")


@annotate(read_only=False, idempotent=False, open_world=False)
async def set_member_overrides(email: str, overrides: list[dict[str, str]]) -> dict[str, Any]:
    """Replace the allow and deny overrides of one member of your organization, after one card; you can allow only rights that you hold."""
    target = oa._email_arg(email)
    if target is None:
        return oa._refusal(oa._BAD_EMAIL)
    new, problem = _overrides_arg(overrides)
    if problem is not None or new is None:
        return problem or oa._refusal(_BAD_OVERRIDES.format(limit=MAX_RIGHTS))
    if _denies_own_access(target, new):
        return oa._refusal(_SELF_ACCESS, status=409)
    try:
        caller, problem = await _me(ACCESS_RIGHT)
        if problem is not None or caller is None:
            return problem or oa._refusal(oa._SHAPE)
        data, problem = await _access(target)
        if problem is not None or data is None:
            return problem or oa._refusal(oa._SHAPE)
        now = _override_rows(data)
        if _override_snap(now) == _override_snap(new):
            return {"ok": True, "email": target, "note": _SAME_OVERRIDES}
        problem = _beyond(caller, _raises(new, now), "a member")
        if problem is not None:
            return problem
        lines = [("member", f"Member: {target} {oa._fence(data.get('display_name'))}"),
                 ("roles", f"Roles: {oa._roles_text(data.get('roles'))}"),
                 ("count", f"Overrides after: {len(new)}. Now: {len(now)}."),
                 *_override_lines(new, now)]
        decision, _ = await oa._decide(
            f"Change the access of {target}?", f"{len(new)} overrides",
            "Approve to replace all overrides of this member with this list. An allow gives "
            "a right, and a deny takes it away. Reject to leave them as they are.", lines)
        if decision != "approved":
            return oa._not_done(decision)
        problem = await _overrides_still(target, new, now, decision)
        if problem is not None:
            return problem
        sent = await oa._send("PUT", f"/admin/members/{oa._path_email(target)}/overrides",
                              {"overrides": new}, act="access change")
    except oa._Refused as exc:
        return oa._refusal(str(exc))
    if not sent.ok:
        return oa._fail(sent, decision=decision)
    return {"ok": True, "decision": decision, "email": target,
            "overrides": [{"right": o["permission"], "effect": o["effect"]}
                          for o in _override_rows(sent.data)],
            "note": "The access changed. The member gets it at the next request."}


# ── The organization ────────────────────────────────────────────────────────


def _org_name(caller: dict[str, Any]) -> str:
    org = caller.get("organization")
    name = org.get("display_name") if isinstance(org, dict) else None
    return name if isinstance(name, str) else ""


@annotate(read_only=False, idempotent=False, open_world=False)
async def rename_org(display_name: str) -> dict[str, Any]:
    """Change the display name of your organization, after one card."""
    raw = " ".join(display_name.split()) if isinstance(display_name, str) else None
    name, problem = _text(raw, "display_name", MAX_ORG_NAME_CHARS)
    if problem is not None:
        return problem
    if not name:
        return oa._refusal(_BAD_TEXT.format(field="display_name", limit=MAX_ORG_NAME_CHARS))
    try:
        caller, problem = await _me(SETTINGS_RIGHT)
        if problem is not None or caller is None:
            return problem or oa._refusal(oa._SHAPE)
        was = _org_name(caller)
        if was == name:
            return {"ok": True, "display_name": name, "note": _SAME_ORG_NAME}
        decision, _ = await oa._decide(
            "Rename your organization?", name,
            "Approve to change the name that each member sees. The short name and the sign-in "
            "domain stay. Reject to keep the name.",
            [("now", f"Name now: {_whole(was)}"), ("after", f"Name after: {_whole(name)}")])
        if decision != "approved":
            return oa._not_done(decision)
        again, problem = await _me(SETTINGS_RIGHT)
        if problem is not None or again is None:
            return problem or oa._refusal(oa._SHAPE)
        if _org_name(again) != was:
            return oa._refusal(oa._CHANGED.format(what="organization name"), decision=decision)
        sent = await oa._send("PATCH", ORG_PATH, {"display_name": name}, act="rename")
    except oa._Refused as exc:
        return oa._refusal(str(exc))
    if not sent.ok:
        return oa._fail(sent, decision=decision)
    data = sent.data if isinstance(sent.data, dict) else {}
    return {"ok": True, "decision": decision, "display_name": oa._name(data.get("display_name")),
            "note": "The organization has the new name."}


# ── Remove and purge a member (PAR-4c, task 3935) ───────────────────────────
#
# The owner approved this design on 2026-11-24 (answer ca808c74 on task 3107).
# A removal is the soft off-boarding of the Members tab: the row stays, the
# roles go, and the personal tasks are sealed. A purge deletes the member for
# good. So a purge needs the member's OWN typed address of this turn, and it
# refuses each run that no person typed in.

#: What a purge deletes, in words, by the key of ``members._PURGE_DELETES``.
#: ``acb_skills`` cannot import the gateway, so this is a copy, and
#: ``test_org_admin_more_tools.py`` fails when a key of the route has no group.
_PURGE_LOSS_WORDS: dict[str, str] = {
    "identity": "the member record, the roles, the overrides, the group places and the "
                "sign-in requests",
    "mail": "each connected mailbox, WhatsApp account and task account, with its stored "
            "keys, its copy of the mail and messages, and each send that waits",
    "grants": "each grant to an app, to an app tool and to a mailbox, and each private app "
              "install",
    "chats": "the private chats, the places in shared rooms, the chat topics, the cards and "
             "the questions that wait",
    "crm": "the private CRM rules, changes and views",
    "studio": "the private Studio skills, skill runs and views",
    "own": "the settings, reminders, briefs, goals, push devices, Knowledge favourites, "
           "calendar entries and connection asks of the member",
}
_PURGE_LOSSES: dict[str, str] = {
    "role_grants": "identity", "permission_overrides": "identity",
    "group_memberships": "identity", "sign_in_requests": "identity",
    "member_record": "identity",
    "email_accounts": "mail", "whatsapp_accounts": "mail", "task_accounts": "mail",
    "scheduled_sends": "mail", "mailbox_member_prefs": "mail",
    "app_grants": "grants", "app_tool_grants": "grants", "mailbox_grants": "grants",
    "private_app_installs": "grants",
    "room_participations": "chats", "chat_member_rows": "chats", "chat_card_rows": "chats",
    "chat_join_rows": "chats", "failure_event_rows": "chats",
    "private_chat_sessions": "chats", "chat_topics": "chats", "chat_pending_asks": "chats",
    "crm_member_rules": "crm", "crm_private_changes": "crm", "crm_private_views": "crm",
    "studio_skill_runs": "studio", "studio_skills": "studio", "studio_private_views": "studio",
    "knowledge_favorites": "own", "reminders": "own", "brief_runs": "own",
    "goal_records": "own", "member_settings": "own", "push_devices": "own",
    "calendar_entries": "own", "calendar_attendances": "own", "connection_asks": "own",
}
#: The keys of ``members._PURGE_KEEPS``: what a purge keeps. The same fence.
_PURGE_KEEPS: frozenset[str] = frozenset({
    "audit_entries", "audit_events", "agent_runs", "shared_rooms", "apps", "workflows",
    "meetings", "knowledge_proposals", "agent_run_strands", "cycle_capacity",
    "shared_app_installs", "shared_private_app_installs", "studio_shared_views",
    "studio_shared_skills",
})
#: The losses that ``purge_member_rows`` adds outside ``_PURGE_DELETES``.
_PURGE_MORE_LOSSES = ("the Drive sources of the member, with their keys, and the calendar "
                      "connections")
_PURGE_KEPT_WORDS = ("Kept: the audit trail, the agent runs, the shared rooms, and the apps, "
                     "workflows, meetings, Knowledge proposals and shared Studio items that "
                     "the member made.")

_SELF_REMOVE = ("You cannot remove yourself. Ask another owner or admin to do it. Nothing "
                "changed.")
_SELF_PURGE = ("You cannot permanently delete yourself. Ask another owner or admin to do it. "
               "Nothing changed.")
_ALREADY_REMOVED = "{email} is removed already. Nothing changed."
_PURGE_UNTYPED = ("A purge deletes {email} for good, so the chat needs that exact address in "
                  "your own words of this message. Type the address, and ask again. No card "
                  "shows and nothing changed.")
_PURGE_NO_PERSON = ("A purge deletes a member for good, so only a member who types the "
                    "request in the chat can start it. This run has no such member. No card "
                    "shows and nothing changed.")

#: The characters that can stand before an address and make it a longer one.
_LOCAL_CHAR = re.compile(r"[\w.!#$%&'*+/=?^`{|}~@-]")
#: The characters that can follow an address and make its domain a longer one.
_DOMAIN_CHAR = re.compile(r"[\w@-]")


def _typed_the_address(address: str) -> bool:
    """True when the member's OWN typed words of this turn hold *address*, whole.

    The words come from ``chat_constraints._typed_words`` only, never from a
    tool argument. Both sides are case-folded. The match is whole: a letter of
    a longer address before it (``diana@``) or a part of a longer domain after
    it (``.evil``) does not count. A dot that ends a sentence does count."""
    from acb_skills.chat_constraints import _typed_words

    said = str(_typed_words() or "").casefold()
    want = address.casefold()
    if not want:
        return False
    at = said.find(want)
    while at != -1:
        end = at + len(want)
        before = said[at - 1] if at > 0 else ""
        after = said[end:end + 2]
        joined_before = bool(before) and bool(_LOCAL_CHAR.match(before))
        joined_after = bool(after) and (
            bool(_DOMAIN_CHAR.match(after[0]))
            or (after[0] == "." and len(after) > 1 and bool(_DOMAIN_CHAR.match(after[1]))))
        if not joined_before and not joined_after:
            return True
        at = said.find(want, at + 1)
    return False


def _delegated() -> bool:
    """True in a run that another run started: a delegated agent or a spawned
    child. The typed words of the parent turn copy into such a run, but the
    member did not ask that run for anything (``agent_tools`` reads the same)."""
    try:
        from acb_skills.agent_tools import _delegation_chain
        from acb_skills.write_artifact import artifact_context

        return bool(_delegation_chain.get()) or artifact_context().get("sub_run") is True
    except Exception:
        return True


async def _seal_preview(target: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``(the seal preview, None)`` of *target*, or ``(None, refusal)``."""
    reply = await oa._get(f"/admin/members/{oa._path_email(target)}/seal-preview")
    if reply.status == 404:
        return None, oa._refusal(oa._NOT_IN_ORG.format(email=target), status=404)
    if not reply.ok:
        return None, oa._fail(reply)
    if not isinstance(reply.data, dict):
        return None, oa._refusal(oa._SHAPE)
    return reply.data, None


async def _member_still(target: str, row: dict[str, Any], decision: str) -> dict[str, Any] | None:
    """After ``approved``: None when the member is as the card showed it."""
    again, problem = await oa._member(target)
    if problem is not None and problem.get("status") == 404:
        return oa._refusal(oa._CHANGED.format(what="member"), decision=decision)
    if problem is not None or again is None:
        return problem or oa._refusal(oa._SHAPE)
    if oa._member_snap(again) != oa._member_snap(row):
        return oa._refusal(oa._CHANGED.format(what="member"), decision=decision)
    return None


def _is_caller(target: str) -> bool:
    return target == oa._current_user_email().strip().lower()


@annotate(read_only=False, idempotent=False, open_world=False)
async def remove_member(email: str) -> dict[str, Any]:
    """Remove one member of your organization, after one card that shows what the removal seals; the record stays."""
    target = oa._email_arg(email)
    if target is None:
        return oa._refusal(oa._BAD_EMAIL)
    if _is_caller(target):
        return oa._refusal(_SELF_REMOVE, status=409)
    try:
        _caller, problem = await _me(MANAGE_RIGHT)
        if problem is not None:
            return problem
        row, problem = await oa._member(target)
        if problem is not None or row is None:
            return problem or oa._refusal(oa._SHAPE)
        now = oa._STATUS_WORDS.get(str(row.get("status")), "other")
        if now == "removed":
            return {"ok": True, "email": target, "status": now,
                    "note": _ALREADY_REMOVED.format(email=target)}
        seal, problem = await _seal_preview(target)
        if problem is not None or seal is None:
            return problem or oa._refusal(oa._SHAPE)
        n = _seal_counts(seal)
        decision, _ = await oa._decide(
            f"Remove {target}?", f"{now} to removed",
            "Approve to remove this member. The member cannot sign in, and loses each role. "
            "The member record stays, and nothing is deleted. Reject to keep the member.",
            [("member", f"Member: {target} {oa._fence(row.get('display_name'))}"),
             ("status", f"Status now: {now}. After: removed."),
             ("roles", f"Roles now: {oa._roles_text(row.get('roles'))}. After: none."),
             ("tasks", f"Personal tasks: {n['tasks']} in {n['projects']} projects. "
                       f"{n['handed_over']} handed over, {n['sealed']} sealed, not deleted.")])
        if decision != "approved":
            return oa._not_done(decision)
        changed = await _member_still(target, row, decision)
        if changed is not None:
            return changed
        sent = await oa._send("DELETE", f"/admin/members/{oa._path_email(target)}",
                              act="removal")
    except oa._Refused as exc:
        return oa._refusal(str(exc))
    if not sent.ok:
        return oa._fail(sent, decision=decision)
    return {"ok": True, "decision": decision, "email": target, "status": "removed",
            "handed_over": n["handed_over"], "sealed": n["sealed"],
            "note": (f"The member is removed. {n['sealed']} personal tasks are sealed, not "
                     "deleted.")}


def _purge_loss_lines() -> list[tuple[str, str]]:
    lines = [(f"l{i}", f"Lost: {words}.") for i, words in enumerate(_PURGE_LOSS_WORDS.values())]
    lines.append(("l-more", f"Lost: {_PURGE_MORE_LOSSES}."))
    lines.append(("kept", _PURGE_KEPT_WORDS))
    return lines


def _counts(raw: Any) -> dict[str, int]:
    """The non-zero counts of a purge answer, by key."""
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if _count(v) > 0}


@annotate(read_only=False, idempotent=False, open_world=False)
async def purge_member(email: str) -> dict[str, Any]:
    """Delete one member of your organization for good, after one card; it cannot be undone, and you must type the exact address in your message."""
    target = oa._email_arg(email)
    if target is None:
        return oa._refusal(oa._BAD_EMAIL)
    if _is_caller(target):
        return oa._refusal(_SELF_PURGE, status=409)
    if _delegated():
        return oa._refusal(_PURGE_NO_PERSON, status=403)
    if not _typed_the_address(target):
        return oa._refusal(_PURGE_UNTYPED.format(email=target), status=403)
    try:
        _caller, problem = await _me(MANAGE_RIGHT)
        if problem is not None:
            return problem
        row, problem = await oa._member(target)
        if problem is not None or row is None:
            return problem or oa._refusal(oa._SHAPE)
        now = oa._STATUS_WORDS.get(str(row.get("status")), "other")
        decision, _ = await oa._decide(
            f"Delete {target} for good?", "one-way, cannot be undone",
            "This is one-way and cannot be undone. Approve to delete this member for good. "
            "Nobody can bring the member or the lost rows back. Reject to keep the member.",
            [("member", f"Member: {target} {oa._fence(row.get('display_name'))}"),
             ("status", f"Status now: {now}. Roles now: {oa._roles_text(row.get('roles'))}."),
             *_purge_loss_lines()])
        if decision != "approved":
            return oa._not_done(decision)
        changed = await _member_still(target, row, decision)
        if changed is not None:
            return changed
        sent = await oa._send("DELETE", f"/admin/members/{oa._path_email(target)}/purge",
                              act="purge")
    except oa._Refused as exc:
        return oa._refusal(str(exc))
    if not sent.ok:
        return oa._fail(sent, decision=decision)
    data = sent.data if isinstance(sent.data, dict) else {}
    deleted, kept = _counts(data.get("deleted")), _counts(data.get("kept"))
    gone, stay = sum(deleted.values()), sum(kept.values())
    return {"ok": True, "decision": decision, "email": target, "status": "purged",
            "deleted": deleted, "kept": kept, "deleted_total": gone, "kept_total": stay,
            "note": (f"The member is deleted for good. {gone} rows went, and {stay} rows stay. "
                     "This cannot be undone.")}


#: Each tool of PAR-4a, in the order of the skill family after the four of PAR-2a.
#: PAR-4c (task 3935) adds the removal and the purge at the end.
ORG_ADMIN_MORE_TOOLS = (
    list_org_groups, create_org_group, edit_org_group, delete_org_group,
    add_org_group_member, remove_org_group_member, list_roles_and_rights, create_org_role,
    delete_org_role, read_member_access, set_member_overrides, preview_member_removal,
    rename_org, remove_member, purge_member,
)
