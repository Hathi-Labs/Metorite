"""The chat tools of the ``org-admin`` pack (PAR-2a, Metorite task 3102).

Spec: ``project-docs/specs/chat_parity.md`` section 6.3, rung 1. Four tools
let an admin do from the chat what the Members tab of Organisation does:

* :func:`list_members` reads the roster (``GET /admin/members``) and the
  sign-in requests that wait (``GET /admin/members/requests``). No card.
* :func:`set_member_roles` sets the roles of one member
  (``PUT /admin/members/{email}/roles``), after ``GET /admin/roles`` checks
  the slugs. ONE card first.
* :func:`set_member_status` suspends or reactivates one member
  (``PATCH /admin/members/{email}``). ONE card first.
* :func:`answer_access_request` approves or denies one sign-in request
  (``POST /admin/members/requests/{email}/approve`` or ``/deny``). ONE card
  first. An approve can name the roles.

**The gateway decides.** Each tool calls the gateway over HTTP as the member
of the run (the internal bearer and ``X-User-Email`` from the run binding),
as ``studio_tools`` does. The admin guards of the routes stay the only check
(``require_admin_user``, ``admin:members:manage`` and ``admin:members:invite``).
No tool takes an org or a member argument (R5): ``email`` is the address of
the TARGET member, never the caller.

**No card for a member without the right.** Each write reads ``GET /auth/me``
first, the resolved rights of the server, as the workflow writes of
``studio_tools`` do. A member without the right of the route gets a plain
refusal and no card. A failed read refuses too, with no card. After the card,
a 401 or a 403 of the route still comes back as plain words.

**The race rule.** A write reads the member, or the request, before the card
and again after ``approved``. It sends nothing when the row changed while the
card was open. Only ``approved`` sends, and it sends ONE request.

**Text of other people is data.** A display name is the text of a member, and
the name on a sign-in request is the text of a person outside the org. The
list puts each row in ONE ``UntrustedFrame`` block, and a card fences each
such value in « ».

Fence: ``tests/unit/test_org_admin_tools.py``, and the R8 suite
``tests/unit/test_org_admin_tools_r8.py``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from textwrap import wrap
from typing import Any, Literal
from urllib.parse import quote

import httpx
from acb_common.card_body import CardWontFit, card_body
from acb_common.paging import paged
from acb_common.settings import internal_auth_headers

from acb_skills.hidden_text import strip_hidden
from acb_skills.memory_tools import run_member_address
from acb_skills.tool_annotations import annotate

__all__ = [
    "ORG_ADMIN_TOOLS",
    "answer_access_request",
    "list_members",
    "set_member_roles",
    "set_member_status",
]

#: The gateway routes. A package must not import the gateway, so the paths
#: are text here. ``test_chat_parity.py`` reads them from this source, so a
#: path with an address in it is a literal f-string at its call.
MEMBERS_PATH = "/admin/members"
REQUESTS_PATH = "/admin/members/requests"
ROLES_PATH = "/admin/roles"
#: The read of the member's own resolved rights (``routes/admin/me.py``).
ME_PATH = "/auth/me"

#: The right of the role and status routes (``members.py``).
MANAGE_RIGHT = "admin:members:manage"
#: The right of the approve and deny routes (``access_requests.py``).
INVITE_RIGHT = "admin:members:invite"

#: The statuses that :func:`set_member_status` sets. ``removed`` is the
#: remove of the roster, a separate act that this pack does not do.
STATUSES = ("active", "suspended")
#: The answers of :func:`answer_access_request`. The route verb is ``deny``.
DECISIONS = ("approve", "deny")

#: The most rows of one page of the roster, and of the requests.
MAX_ROWS = 50
#: The most characters of a display name in a result or on a card.
MAX_NAME_CHARS = 80
#: The most characters of an address. RFC 5321 gives 254.
MAX_EMAIL_CHARS = 254
#: The most characters of the text of a route in a refusal.
MAX_DETAIL_CHARS = 400
#: The most roles in one change.
MAX_ROLES = 20

_NO_MEMBER = "There is no member for this run, so the admin tools cannot act."
_SHARED_ROOM = "A shared room cannot use the admin tools. Ask in your own chat."
_BAD_EMAIL = ("Send the email address of one member, as the Members tab shows it. "
              "Nothing changed.")
_BAD_STATUS = ("status is active (reactivate the member) or suspended (the member cannot "
               "sign in). Nothing changed.")
_BAD_DECISION = "decision is approve or deny. Nothing changed."
_BAD_ROLES = ("roles is a list of role slugs, as the role list shows them, with at least one. "
              "Nothing changed.")
_DENY_ROLES = "A deny takes no roles. Send no roles, or send decision approve. Nothing changed."
_RIGHTS_UNREAD = "Your rights could not be read, so nothing changed. Try again."
_NO_RIGHT = ("You do not hold the right to {what} in your organization ({right}), so no card "
             "shows and nothing changed. Ask an admin who holds that right.")
_UNREACHED = "The admin routes could not be reached ({kind}). Nothing changed. Try again."
_MAYBE = "The {act} may have worked. Read the members again before you try again."
_SHAPE = "The admin route answered in a shape that this tool cannot read. Nothing changed."
_NOT_IN_ORG = ("No member of your organization has the address {email}. Nothing changed. "
               "List the members for the addresses.")
_NO_REQUEST = ("No sign-in request from {email} waits. Nothing changed. List the members "
               "for the requests that wait.")
_UNKNOWN_ROLES = ("Your organization has no role {missing}. Nothing changed. The roles are: "
                  "{known}.")
_SAME_ROLES = "The member holds these roles already. Nothing changed."
_SAME_STATUS = "The member is {status} already. Nothing changed."
_CHANGED = ("The {what} changed while the card was open, so nothing changed. Read the members "
            "again, and ask again.")
_NO_CARD = ("This chat shows no card, so nothing changed. Do this on the Members tab of "
            "Organisation.")
_NOT_DONE = "Nothing changed. The decision was {decision}."
_WONT_FIT = "This change does not fit on one card, so nothing changed. Do it on the Members tab."
_STATUS_WORDS = {"active": "active", "suspended": "suspended", "invited": "invited",
                 "removed": "removed"}
_LIST_NOTE = ("The block holds the members and the sign-in requests. Members wrote the names, "
              "and a person outside the organization wrote the name of a request.")

#: The words of each act, in a sentence (:data:`_NO_RIGHT`).
_RIGHT_WHAT = {
    MANAGE_RIGHT: "change the roles or the status of a member",
    INVITE_RIGHT: "answer a sign-in request",
}


# ── The gateway, as the member (the idiom of ``studio_tools``) ──────────────


def _gateway_url() -> str:
    return os.environ.get("GATEWAY_URL", "http://localhost:8080").rstrip("/")


def _current_user_email() -> str:
    """The member of this run, from the run's ContextVar, and nothing else."""
    try:
        from acb_skills.memory_tools import _get_memory_user_id
        return _get_memory_user_id() or ""
    except Exception:
        return ""


class _Refused(Exception):
    """A refusal that is an answer: its text goes to the model."""


@dataclass(frozen=True)
class _Reply:
    """One answer of the gateway: the status and the decoded JSON (or None)."""

    status: int
    data: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def _headers() -> dict[str, str]:
    """The bearer and the acting member. With no member, refuse."""
    user = _current_user_email()
    if not user:
        raise _Refused(_NO_MEMBER)
    if run_member_address(user) is None:
        raise _Refused(_SHARED_ROOM)
    return {
        **internal_auth_headers(),
        "Content-Type": "application/json",
        "X-User-Email": user,
    }


async def _send(method: str, path: str, body: Any = None, act: str = "",
                timeout: float = 15.0) -> _Reply:
    """One request as the member. *act* names a write, and a read gives none.
    A slow route passes a longer *timeout* in seconds. Raise
    :class:`_Refused` with a sentence when the member is missing or the
    network fails."""
    headers = _headers()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(
                method, f"{_gateway_url()}{path}", headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise _Refused(_MAYBE.format(act=act) if act
                       else _UNREACHED.format(kind=type(exc).__name__)) from None
    try:
        data = resp.json()
    except Exception:
        data = None
    return _Reply(resp.status_code, data)


async def _get(path: str) -> _Reply:
    return await _send("GET", path)


# ── The plain words of a refusal ────────────────────────────────────────────


def _refusal(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **extra}


def _cut(text: str, limit: int) -> str:
    """*text* as one line of at most *limit* characters. A cut line ends with
    "...". A long word is cut too, so a name never vanishes."""
    lines = wrap(" ".join(text.split()), limit - 3)
    if not lines:
        return ""
    return lines[0] + ("..." if len(lines) > 1 else "")


def _detail(reply: _Reply) -> str:
    raw = reply.data.get("detail") if isinstance(reply.data, dict) else None
    if isinstance(raw, str):
        return _cut(raw, MAX_DETAIL_CHARS)
    return ""


def _fail(reply: _Reply, **extra: Any) -> dict[str, Any]:
    """The refusal of an admin route in plain words. A missing right gives
    one sentence, and each other refusal gives the text of the route."""
    detail = _detail(reply)
    if reply.status == 401:
        return _refusal("Your session is not signed in, so nothing changed.",
                        status=401, **extra)
    if reply.status == 403 and (not detail or detail.startswith("Forbidden")):
        return _refusal("You do not hold the admin right that this needs in your "
                        "organization, so nothing changed. Ask an admin of your organization.",
                        status=403, **extra)
    if detail:
        if "Nothing changed" not in detail:
            detail = f"{detail} Nothing changed."
        return _refusal(detail, status=reply.status, **extra)
    return _refusal(f"The admin route answered with status {reply.status}. Nothing changed.",
                    status=reply.status, **extra)


async def _right_refusal(right: str) -> dict[str, Any] | None:
    """The plain refusal of a member without *right*, or None.

    It reads ``GET /auth/me``, the resolved rights of the server, so the
    tool keeps no rule of its own. A failed read refuses, so it fails closed."""
    reply = await _get(ME_PATH)
    caps = reply.data.get("capabilities") if isinstance(reply.data, dict) else None
    if not reply.ok or not isinstance(caps, list):
        return _refusal(_RIGHTS_UNREAD)
    if right not in caps:
        return _refusal(_NO_RIGHT.format(what=_RIGHT_WHAT[right], right=right), status=403)
    return None


# ── The arguments ───────────────────────────────────────────────────────────


def _email_arg(raw: Any) -> str | None:
    """*raw* as one address in lower case, or None. A path never holds free
    text, and the route reads the address case-insensitively."""
    if not isinstance(raw, str):
        return None
    text = raw.strip().lower()
    if not text or len(text) > MAX_EMAIL_CHARS or text.count("@") != 1:
        return None
    if any(ch <= " " or ch in "/\\?#%" for ch in text):
        return None
    local, _, domain = text.partition("@")
    return text if local and domain else None


def _path_email(email: str) -> str:
    """The address as one path segment. Each reserved character is encoded."""
    return quote(email, safe="")


#: A role slug, as ``routes/admin/roles.py::_clean_slug`` makes one: 1 to 48 (``_SLUG_MAX``)
#: characters, each ``isalnum()`` or ``_`` or ``-``. The real check against
#: ``GET /admin/roles`` follows, so this is the shape check only.
def _slug_shape(slug: str) -> bool:
    return 1 <= len(slug) <= 48 and all(c.isalnum() or c in "_-" for c in slug)


def _roles_arg(raw: Any) -> list[str] | None:
    """The role slugs of *raw*, each once, in order, or None."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip().startswith("[") else [raw]
        except ValueError:
            return None
    if not isinstance(raw, list) or not raw or len(raw) > MAX_ROLES:
        return None
    out: list[str] = []
    for item in raw:
        slug = item.strip().lower() if isinstance(item, str) else ""
        if not _slug_shape(slug):
            return None
        if slug not in out:
            out.append(slug)
    return out


# ── The text of other people ────────────────────────────────────────────────


def _name(value: Any) -> str:
    """A display name as one line with no hidden character, cut."""
    text = strip_hidden(value) if isinstance(value, str) else ""
    return _cut(text, MAX_NAME_CHARS)


def _fence(value: Any) -> str:
    """A value of another person on a card, between « and »."""
    return "«" + _name(value).replace("«", "").replace("»", "") + "»"


def _roles_text(roles: Any) -> str:
    slugs = [str(r) for r in roles] if isinstance(roles, list) else []
    return ", ".join(slugs) if slugs else "none"


# ── The reads ───────────────────────────────────────────────────────────────


def _rows(reply: _Reply) -> list[dict[str, Any]] | None:
    if not isinstance(reply.data, list):
        return None
    return [r for r in reply.data if isinstance(r, dict) and isinstance(r.get("email"), str)]


def _member_row(r: dict[str, Any]) -> dict[str, Any]:
    roles = r.get("roles")
    return {
        "email": str(r.get("email"))[:MAX_EMAIL_CHARS],
        "name": _name(r.get("display_name")),
        "status": _STATUS_WORDS.get(str(r.get("status")), "other"),
        "roles": [str(x)[:64] for x in roles] if isinstance(roles, list) else [],
    }


def _request_row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "email": str(r.get("email"))[:MAX_EMAIL_CHARS],
        "name": _name(r.get("display_name")),
        "attempts": r.get("attempt_count") if isinstance(r.get("attempt_count"), int) else 0,
        "last_seen_at": str(r.get("last_seen_at") or "")[:40],
        "outside_company_domain": r.get("is_external") is True,
    }


def _find(rows: list[dict[str, Any]], email: str) -> dict[str, Any] | None:
    return next((r for r in rows if str(r.get("email", "")).strip().lower() == email), None)


async def _member(email: str, *, include_removed: bool = False,
                  ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``(row, None)`` for the member *email*, or ``(None, refusal)``.

    The route drops a removed member unless the read asks for it. PAR-2a's
    status change does not ask, so a removed member stays "no member" there.
    PAR-4c's removal and purge ask, as the Members page does
    (``rosterView.ts``), so a purge after a removal finds the member."""
    reply = await _get(f"{MEMBERS_PATH}?include_removed=true" if include_removed
                       else MEMBERS_PATH)
    if not reply.ok:
        return None, _fail(reply)
    rows = _rows(reply)
    if rows is None:
        return None, _refusal(_SHAPE)
    row = _find(rows, email)
    if row is None:
        return None, _refusal(_NOT_IN_ORG.format(email=email), status=404)
    return row, None


async def _request(email: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``(row, None)`` for the sign-in request of *email*, or ``(None, refusal)``."""
    reply = await _get(REQUESTS_PATH)
    if not reply.ok:
        return None, _fail(reply)
    rows = _rows(reply)
    if rows is None:
        return None, _refusal(_SHAPE)
    row = _find(rows, email)
    if row is None:
        return None, _refusal(_NO_REQUEST.format(email=email), status=404)
    return row, None


async def _roles() -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    """``({slug: display name}, None)`` of ``GET /admin/roles``, or ``(None, refusal)``."""
    reply = await _get(ROLES_PATH)
    if not reply.ok:
        return None, _fail(reply)
    if not isinstance(reply.data, list):
        return None, _refusal(_SHAPE)
    found = {str(r["slug"]): _name(r.get("display_name")) or str(r["slug"])
             for r in reply.data if isinstance(r, dict) and isinstance(r.get("slug"), str)}
    return found, None


def _role_problem(wanted: list[str], known: dict[str, str]) -> dict[str, Any] | None:
    missing = [s for s in wanted if s not in known]
    if not missing:
        return None
    return _refusal(_UNKNOWN_ROLES.format(missing=", ".join(missing),
                                          known=", ".join(sorted(known)) or "none"), status=400)


# ── The card ────────────────────────────────────────────────────────────────


async def _decide(title: str, detail: str, note: str,
                  rows: list[tuple[str, str]]) -> tuple[str, str]:
    """Show ONE card and wait. ``(decision, context)``: the decision is
    ``approved``, ``revised`` (the member wrote in the chat), ``stopped`` or
    ``no_card``. Raise :class:`_Refused` when the card does not fit."""
    from acb_skills import ask_tools

    try:
        context = str(card_body(note, rows))
    except CardWontFit:
        raise _Refused(_WONT_FIT) from None
    # The run's own member may answer, and no other (PZ-32). The member is the
    # run's, never an input.
    answer = await ask_tools.request_confirmation(
        title, detail, context, non_interactive_default="deny", close_on_steer=True,
        responder=_current_user_email() or None, fenced=True)
    if answer is True:
        return "approved", context
    if answer is ask_tools.REVISED:
        return "revised", context
    if not ask_tools.confirmation_channel_open():
        return "no_card", context
    return "stopped", context


def _not_done(decision: str) -> dict[str, Any]:
    if decision == "no_card":
        return _refusal(_NO_CARD, decision=decision)
    return _refusal(_NOT_DONE.format(decision=decision), decision=decision)


def _member_snap(row: dict[str, Any]) -> tuple[Any, ...]:
    """What the card of a member must still match when the member answers."""
    roles = row.get("roles")
    return (row.get("status"), tuple(roles) if isinstance(roles, list) else ())


def _request_snap(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("status"), row.get("is_external") is True)


# ── The tools ───────────────────────────────────────────────────────────────


@annotate(read_only=True, idempotent=True, open_world=False)
async def list_members(offset: int = 0, limit: int = MAX_ROWS,
                       requests_offset: int = 0) -> dict[str, Any]:
    """List the members of your organization with their roles and status, and the sign-in requests that wait."""
    try:
        members = await _get(MEMBERS_PATH)
        if not members.ok:
            return _fail(members)
        rows = _rows(members)
        if rows is None:
            return _refusal(_SHAPE)
        requests = await _get(REQUESTS_PATH)
        if not requests.ok:
            return _fail(requests)
        waiting = _rows(requests)
        if waiting is None:
            return _refusal(_SHAPE)
    except _Refused as exc:
        return _refusal(str(exc))
    from acb_skills.untrusted import UntrustedFrame

    page = paged(rows, offset, limit, default=MAX_ROWS, maximum=MAX_ROWS, noun="members")
    asks = paged(waiting, requests_offset, MAX_ROWS, default=MAX_ROWS, maximum=MAX_ROWS,
                 noun="requests", param="requests_offset")
    shown = asks.items
    frame = UntrustedFrame.new()
    block = json.dumps({"members": [_member_row(r) for r in page.items],
                        "requests": [_request_row(r) for r in shown]},
                       ensure_ascii=False, separators=(",", ":"))
    return {
        "ok": True,
        "rows": frame.wrap("ORGANIZATION MEMBERS", block),
        "members_total": page.total,
        "members_shown": len(page.items),
        "page": page.line,
        "requests_total": len(waiting),
        "requests_shown": len(shown),
        "requests_page": asks.line,
        "note": frame.note(_LIST_NOTE),
    }


@annotate(read_only=False, idempotent=False, open_world=False)
async def set_member_roles(email: str, roles: list[str]) -> dict[str, Any]:
    """Set the roles of one member of your organization, by role slug, after one card."""
    target = _email_arg(email)
    if target is None:
        return _refusal(_BAD_EMAIL)
    wanted = _roles_arg(roles)
    if wanted is None:
        return _refusal(_BAD_ROLES)
    try:
        refused = await _right_refusal(MANAGE_RIGHT)
        if refused is not None:
            return refused
        known, problem = await _roles()
        if problem is not None or known is None:
            return problem or _refusal(_SHAPE)
        problem = _role_problem(wanted, known)
        if problem is not None:
            return problem
        row, problem = await _member(target)
        if problem is not None or row is None:
            return problem or _refusal(_SHAPE)
        now = [str(r) for r in row.get("roles") or []]
        if sorted(now) == sorted(wanted):
            return {"ok": True, "email": target, "roles": now, "note": _SAME_ROLES}
        rows = [
            ("member", f"Member: {target} {_fence(row.get('display_name'))}"),
            ("now", f"Roles now: {_roles_text(now)}"),
            ("new", "Roles after: " + ", ".join(f"{s} ({known[s]})" for s in wanted)),
        ]
        decision, _context = await _decide(
            f"Set the roles of {target}?", f"{_roles_text(now)} to {', '.join(wanted)}",
            "Approve to replace the roles of this member. Reject to leave them as they are.",
            rows)
        if decision != "approved":
            return _not_done(decision)
        again, problem = await _member(target)
        if problem is not None or again is None:
            return problem or _refusal(_SHAPE)
        if _member_snap(again) != _member_snap(row):
            return _refusal(_CHANGED.format(what="member"), decision=decision)
        sent = await _send("PUT", f"/admin/members/{_path_email(target)}/roles",
                           {"roles": wanted}, act="role change")
    except _Refused as exc:
        return _refusal(str(exc))
    if not sent.ok:
        return _fail(sent, decision=decision)
    data = sent.data if isinstance(sent.data, dict) else {}
    return {"ok": True, "decision": decision, "email": target,
            "roles": [str(r) for r in data.get("roles") or []],
            "note": "The roles changed. The member gets the new rights at the next request."}


@annotate(read_only=False, idempotent=False, open_world=False)
async def set_member_status(
    email: str, status: Literal["active", "suspended"],
) -> dict[str, Any]:
    """Suspend one member of your organization, or make a suspended member active again, after one card."""
    target = _email_arg(email)
    if target is None:
        return _refusal(_BAD_EMAIL)
    want = str(status or "").strip().lower()
    if want not in STATUSES:
        return _refusal(_BAD_STATUS)
    try:
        refused = await _right_refusal(MANAGE_RIGHT)
        if refused is not None:
            return refused
        row, problem = await _member(target)
        if problem is not None or row is None:
            return problem or _refusal(_SHAPE)
        now = _STATUS_WORDS.get(str(row.get("status")), "other")
        if now == want:
            return {"ok": True, "email": target, "status": now,
                    "note": _SAME_STATUS.format(status=now)}
        invited = now == "invited"
        verb = ("Suspend" if want == "suspended"
                else "Activate" if invited else "Reactivate")
        note = ("Approve to suspend this member. The member cannot sign in until an admin "
                "makes the member active again. Reject to leave the member as now."
                if want == "suspended" else
                "Approve to activate this invited person, who has not signed in yet. "
                "The member can then sign in. Reject to leave the member as now."
                if invited else
                "Approve to make this member active again. The member can sign in. Reject to "
                "leave the member as now.")
        rows = [
            ("member", f"Member: {target} {_fence(row.get('display_name'))}"),
            ("status", f"Status now: {now}. After: {want}."),
            ("roles", f"Roles: {_roles_text(row.get('roles'))}"),
        ]
        decision, _context = await _decide(f"{verb} {target}?", f"{now} to {want}", note, rows)
        if decision != "approved":
            return _not_done(decision)
        again, problem = await _member(target)
        if problem is not None or again is None:
            return problem or _refusal(_SHAPE)
        if _member_snap(again) != _member_snap(row):
            return _refusal(_CHANGED.format(what="member"), decision=decision)
        sent = await _send("PATCH", f"/admin/members/{_path_email(target)}", {"status": want},
                           act="status change")
    except _Refused as exc:
        return _refusal(str(exc))
    if not sent.ok:
        return _fail(sent, decision=decision)
    data = sent.data if isinstance(sent.data, dict) else {}
    return {"ok": True, "decision": decision, "email": target,
            "status": _STATUS_WORDS.get(str(data.get("status")), "other"),
            "note": f"The member is {want} now."}


def _answer_args(
    email: Any, decision: Any, roles: Any,
) -> tuple[str, str, list[str]] | dict[str, Any]:
    """``(address, act, roles)`` of :func:`answer_access_request`, or a refusal."""
    target = _email_arg(email)
    if target is None:
        return _refusal(_BAD_EMAIL)
    act = str(decision or "").strip().lower()
    if act not in DECISIONS:
        return _refusal(_BAD_DECISION)
    if not roles:
        return target, act, []
    if act == "deny":
        return _refusal(_DENY_ROLES)
    parsed = _roles_arg(roles)
    return (target, act, parsed) if parsed is not None else _refusal(_BAD_ROLES)


def _request_card(
    target: str, act: str, row: dict[str, Any], wanted: list[str], known: dict[str, str],
) -> tuple[str, str, list[tuple[str, str]]]:
    """``(title, note, rows)`` of the card of a sign-in request."""
    rows = [("person", f"Person: {target} {_fence(row.get('display_name'))}")]
    if row.get("is_external") is True:
        rows.append(("outside", "This address is outside the company domain."))
    tries = row.get("attempt_count") if isinstance(row.get("attempt_count"), int) else 0
    rows.append(("tries", f"Sign-in tries: {tries}"))
    if act == "deny":
        return (f"Deny the sign-in request of {target}?",
                "Approve to deny this request. Nobody is added. Reject to leave the "
                "request waiting.", rows)
    given = ", ".join(f"{s} ({known[s]})" for s in wanted) if wanted else "member (the default)"
    rows.append(("roles", f"Roles: {given}"))
    return (f"Let {target} into the organization?",
            "Approve to make this person an active member now, with these roles. "
            "Reject to leave the request waiting.", rows)


async def _request_still(target: str, row: dict[str, Any], answer: str) -> dict[str, Any] | None:
    """None when the request still waits as the card showed it, else a refusal."""
    again, problem = await _request(target)
    if problem is not None and problem.get("status") == 404:
        # Another admin answered it while the card was open.
        return _refusal(_CHANGED.format(what="request"), decision=answer)
    if problem is not None or again is None:
        return problem or _refusal(_SHAPE)
    if _request_snap(again) != _request_snap(row):
        return _refusal(_CHANGED.format(what="request"), decision=answer)
    return None


def _answered(act: str, answer: str, target: str, sent: _Reply) -> dict[str, Any]:
    """The result of a sent answer: the route's refusal, or what changed."""
    if not sent.ok:
        return _fail(sent, decision=answer)
    if act == "deny":
        return {"ok": True, "decision": answer, "email": target, "request": "denied",
                "note": "The request is denied. Nobody was added."}
    data = sent.data if isinstance(sent.data, dict) else {}
    # The route says so when the person was a member already (``ApproveResult.detail``).
    said = _detail(_Reply(200, data))
    return {
        "ok": True, "decision": answer, "email": target, "request": "approved",
        "status": _STATUS_WORDS.get(str(data.get("status")), "other"),
        "roles": [str(r) for r in data.get("roles") or []],
        "note": said or "The person is an active member now.",
    }


@annotate(read_only=False, idempotent=False, open_world=False)
async def answer_access_request(
    email: str, decision: Literal["approve", "deny"], roles: list[str] | None = None,
) -> dict[str, Any]:
    """Approve or deny one sign-in request that waits, after one card. An approve can name the roles."""
    args = _answer_args(email, decision, roles)
    if isinstance(args, dict):
        return args
    target, act, wanted = args
    try:
        refused = await _right_refusal(INVITE_RIGHT)
        if refused is not None:
            return refused
        known: dict[str, str] = {}
        if wanted:
            found, problem = await _roles()
            if problem is not None or found is None:
                return problem or _refusal(_SHAPE)
            known = found
            problem = _role_problem(wanted, known)
            if problem is not None:
                return problem
        row, problem = await _request(target)
        if problem is not None or row is None:
            return problem or _refusal(_SHAPE)
        title, note, rows = _request_card(target, act, row, wanted, known)
        answer, _context = await _decide(title, f"{act} the request", note, rows)
        if answer != "approved":
            return _not_done(answer)
        changed = await _request_still(target, row, answer)
        if changed is not None:
            return changed
        body = {"roles": wanted} if act == "approve" else None
        sent = await _send("POST", f"/admin/members/requests/{_path_email(target)}/{act}",
                           body, act=act)
    except _Refused as exc:
        return _refusal(str(exc))
    return _answered(act, answer, target, sent)


#: Each tool of the ``org-admin`` pack, in the order of its skill family.
ORG_ADMIN_TOOLS = (list_members, set_member_roles, set_member_status, answer_access_request)
