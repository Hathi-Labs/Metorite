"""The action door of AI Studio (STU-B2a, B2b-1, B2c-1 and B2c-2a, ``ai_studio.md`` §3.2).

``POST /studio/apps/{app_id}/actions/{action}`` runs one action that a Studio
package declares. The route registers on the router of ``actions.py``, as
``skills.py`` does, so ``main.py`` mounts no second router. The gate
``require_feature_router("studio")`` and the prefix ``/studio`` hold here (J1).

**The order of the checks.** Each check runs before the next one:

1. The switch. An org outside ``STUDIO_ORGS`` gets 404 ``Not found``, for
   each body. The route takes the raw request, so FastAPI parses no body
   before this check.
2. The shape of the action. An ``action`` that is not an A2 action id
   (``OP_PATTERN``, at most 48 characters) names nothing, so it gets 404
   ``Not found`` before any read.
3. The package. ``packages_store.get_package`` reads it for the caller. A
   package of another member or of another org, an App Workshop app and an id
   that names nothing all get 404 ``Not found``. The answer names nothing.
   J2a: while ``PACKAGE_HOST_ORGS`` holds the org, a member who owns no such
   package reads the INSTALLED version that the member sees
   (``package_host/viewer.py``), and the declarations are those of that
   version. A draft, a paused, an archived and an expired install get the
   same 404.
   J3a: a package of the caller that an admin paused (``apps.paused_at``)
   gets 423 ``package_paused`` for each action, before the declaration. The
   row of the call holds the code. A viewer of a paused package reads no
   install of it, so a viewer gets the same 404.
4. The declaration. The path names an ``actions[].id`` of the manifest. An
   id that the manifest does not declare gets 403 ``not_declared``.
5. The class. An ``object:<name>.<op>`` action takes its class from
   :data:`~acb_common.studio_manifest.OBJECT_OP_CLASSES`. Each other source
   takes its class from the B1 catalog, ``actions.catalog_rows``. Those two
   are the only sources of a class. Class X gets 403 ``class_x``, and an id
   that the catalog does not serve gets 403 ``not_in_catalog``. A catalog
   that fails gets 503 ``catalog_unavailable``.
6. The approval. Class B and class C get 403 ``approval_required``. Two
   exceptions open the create, the update and the delete of an object
   (:data:`DIRECT_EXECUTORS`). A DIRECT call of a member runs them at once
   (B2c-1). A DELEGATED call waits for the member's card (B2c-2a, below). A
   call is delegated only while ``APP_REQUESTS_ORGS`` holds the org AND its
   query holds ``ask=1`` or a ``request_id``. Each other call gets
   ``approval_required`` at the first check, a run with no person present
   too. A delegated call of a run with no person present gets 403
   ``no_person_present`` (AUTO-7a) after the first check, before the body.
   A viewer (J2a) gets 403 ``read_only`` for each class B and C action, with
   a proof, an ``ask=1`` or a ``preview=1`` (G4) too, before the body.
7. The body. Only a call that an open or a direct executor runs, or that
   asks for a card, reads the body. So a non-owner and a refused action read
   no byte. The resend of an approved request reads no byte either. No session
   is open during the read, because a wait on a slow client must not hold a
   pool connection. The read stops past :data:`MAX_BODY_BYTES`, and the
   answer is 413 ``body_too_large``.
8. The second check, in a new session. :func:`_check` runs again: it reads
   the package as this member, then checks the declaration and the class on
   that fresh manifest. A slow client can pass the first check and hold the
   read open. Without this check, it reads after it lost access or after the
   package changed. A viewer of J2a can lose access during the read: an
   admin pauses the install, or the member leaves the group. C8 (row rules)
   builds on this door too. A miss gets the same 404, and a
   declaration that is gone gets the same 403. An action that now names
   another source gets 403 ``source_not_open``. A read of a viewer takes a
   slot of :data:`READERS_PER_ORG` before this session, and a call past the
   cap gets 503 ``store_busy`` with ``Retry-After`` and opens no session. A
   read of the owner takes no slot.
9. The executor, in that session. The door opens two: ``object:<name>.get``
   (B2a) and ``object:<name>.list`` (B2b-1). For a direct call it also runs
   the create, the update and the delete of an object (B2c-1). Each one
   parses the body here, and only here. An object name that the package does not hold gets 404
   ``object_missing``, and a record id that names nothing gets 404 ``Not
   found``. Each other class A action gets 403 ``source_not_open`` (B2b-2 and
   B2b-3 name the next executors).

**The list.** An empty body means ``{}``. The door accepts only the keys of
:data:`LIST_INPUT_KEYS`, and ``limit`` and ``offset`` must each be an ``int``.
So a refused body reads nothing, and ``Infinity`` never reaches
``paging.bounds``, which raises ``OverflowError`` on it. Then
``objects_store.query_records`` checks the rest, and it is the one grammar. A
:class:`~acb_common.studio_query.QueryError` gets 422 ``{code, message,
field}``. ``query_timeout`` gets 503 with ``Retry-After``. The store leaves
the session usable after each refusal.

**Tenancy and the member (R5).** The org is the bound session
(``_tenant_session()``), and the member is ``user.email``. No check reads an
org, a member or a package from the path, the query or the body. A key such
as ``organization_id`` in a body changes nothing.

**The audit.** Each call that reaches a package of the caller, and that the
door serves or refuses, writes one ``app_audit`` row (kind ``action``) through
``record_app_audit_bound``. The row holds the action id, its ``uses``, its
class and the outcome, and no record data. The row of a direct write also
names its record by ``record_id`` (B2c-1). The row of a viewer (J2a) holds
``viewer: true`` and the installed ``version``. Each text in it has at most
:data:`MAX_AUDIT_TEXT` characters. A 404 at either read of the package writes
no row. A refused query gives the row its code in ``reason``, and never its
input. A raw
fault, such as a failed statement, gets 500 and writes no row, because the
session is not safe to write on.

⚠️ **The audit is best-effort, on purpose, for a class A read only.** It
opens its own session after the read, and a failed audit logs and never
fails the read. A read changes nothing, so a lost row loses no change. B2c
must not copy this: it writes its audit row in the same transaction as the
write, and a failed audit fails the write.

**The lines (C13).** The data of a create or an update can hold the key of a
lines field of the object: a list of lines, each ``{data}`` for a new line or
``{id, data}`` for a changed line. :func:`_split_lines` takes them out of the
record data before any write, and refuses more than
:data:`~acb_common.studio_rules.MAX_LINES_PER_SAVE` lines (``too_many_lines``)
or a line of another shape (``bad_lines``), each with a 422. The ask of a
delegated write runs the same check, so it opens no card for such a list. The
record is write 0 of ONE ``apply_writes`` call, and each line is a write
linked to it, so the record and its lines are saved all or none. A refusal of
a line names its place. A line is not removed through its record, because a
delete is class C, and the save of a record is class B.

**A delegated write (B2c-2a).** The approval store of MCP-P1b-1
(``gateway/app_requests.py``) holds the card, and the door builds no second
store.

* Only an explicit call is delegated (fix round 1). The query must hold the
  ask marker ``ask=1`` (:data:`ASK_MARKER`), or name a ``request_id``. Each
  other call that is not direct keeps 403 ``approval_required``. So an
  unsigned Save of the member's own screen never gets a 202 that reads as
  saved. The marker is in no digest and no binding.
* The ask. A delegated call with ``ask=1`` reads the body, checks its keys,
  and calls ``open_request`` in the session of the second check. The source
  and the app are ``studio``, the subject is the package id, the op is the
  action id, and the class comes from ``OBJECT_OP_CLASSES``. The call is
  :func:`_door_call`: the door path, the ``uses`` of the action in the query,
  and the body. The answer is 202 ``{state, request_id, expires_at}``, and
  nothing runs.
* The open request (fix round 2). ``app_requests.open_studio_request``
  takes the lock of ``open_request`` for the member and the app, then reads
  ONCE: the newest open request (awaiting, or approved and not spent, and
  live) of this binding and digest. A found request is the answer, so a
  retry gives it. Else the door opens a new request with a fresh key
  (:func:`_ask_key`). So an ask after a close opens ONE new request, also
  when two asks race: the second waits on the lock, then finds the first
  one. The cost is one lock and one read, whatever the history.
* The resend. A delegated call with ``request_id`` reads no byte of its body.
  In ONE session it reads the stored call with ``read_request``, computes the
  digest of :func:`_door_call` from it, calls ``spend``, runs the stored write
  with :func:`_do_object_write`, then writes the audit row with
  ``record_app_audit_in``. The spend and the write sit in one SAVEPOINT. A
  write that does not run rolls the spend back, so the request stays
  approved. A failed audit rolls the whole transaction back.
* A refused spend answers the code of ``classify_refusal``
  (:data:`SPEND_REFUSALS`). A refused write answers 422 ``{code, message,
  rule, fieldErrors}``, as a direct write does.

**The preview (G4, ``ai_studio.md`` §6.2 step 4).** A call with the query
mark ``preview=1`` (:data:`PREVIEW_MARKER`) changes nothing outside its
answer. Any other value of ``preview`` gets 422 ``bad_preview``, so a typo
never falls through to a live write.

* A read runs as before, with its audit row. A read changes nothing.
* A create, an update or a delete of an object runs the SAME write path
  (:func:`_do_object_write`, the checks of each field and rule, and the
  lines) in a SAVEPOINT that ALWAYS rolls back (:func:`_run_preview_write`).
  So the answer shows what the write would give, and no row of the object
  store changes. Each other session sees nothing at any time, because the
  write never commits. The call needs no proof and no card: it opens no
  request and spends none.
* A class B or C action of another source (a workflow, a door op such as a
  send, an integration) runs NO executor. The answer is 200
  ``{preview, simulated: {action, uses, class}}``. No sender, no workflow
  and no event hook is called.
* The audit row of a preview write or send has the outcome ``simulated`` and
  the key ``preview``. So no row says that a write ran. A preview create
  names no record, because its id was never stored.
* The call runs inside ``studio.preview.preview_scope``. A record event (C3)
  goes out only through ``studio.preview.emit_record_event``, which emits
  nothing in that scope.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import re
import uuid
from collections.abc import Iterator
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_auth import app_requests as store
from acb_auth import direct_proof as _direct_proof
from acb_common import get_logger, studio_query
from acb_common.app_manifests.base import OP_PATTERN
from acb_common.no_person import no_person_present
from acb_common.studio_fields import FieldError, FieldSpec, RecordError
from acb_common.studio_manifest import OBJECT_OP_CLASSES, UsesRef, parse_uses
from acb_common.studio_rules import (
    COMPUTED_FIELD,
    MAX_LINES_PER_SAVE,
    FormulaError,
    RuleViolation,
)
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from gateway import app_requests
from gateway.db import tenant_session as _tenant_session
from gateway.package_host import viewer as _viewer
from gateway.package_host.flags import package_host_allowed
from gateway.routes.apps._common import record_app_audit_bound, record_app_audit_in
from gateway.routes.studio import actions, flags
from gateway.routes.studio.actions import NOT_FOUND, router
from gateway.studio import objects_store, packages_store
from gateway.studio.preview import preview_scope

__all__ = ["ASK_MARKER", "DELEGATED_EXECUTORS", "DIRECT_EXECUTORS", "DOOR_TEMPLATE",
           "LIST_INPUT_KEYS", "MAX_AUDIT_TEXT", "MAX_BODY_BYTES",
           "OPEN_EXECUTORS", "PACKAGE_PAUSED", "PAUSED_STATUS", "PAUSED_TEXT",
           "PREVIEW_MARKER", "READERS_PER_ORG", "READERS_TOTAL", "REFUSALS", "REQUEST_APP",
           "REQUEST_VIA", "SPEND_REFUSALS", "WRITERS_PER_ORG", "WRITERS_TOTAL", "router"]

_log = get_logger("gateway.studio.invoke")

#: The executors that the door opens, as (source, op): the object ``get`` of
#: B2a and the object ``list`` of B2b-1.
OPEN_EXECUTORS: frozenset[tuple[str, str | None]] = frozenset(
    {("object", "get"), ("object", "list")})

#: The executors that run for a DIRECT call only (STU-B2c-1): the create, the
#: update and the delete of an object. A call is direct when the BFF signed it
#: for the member (``acb_auth.direct_proof``). Any other call gets
#: ``approval_required`` for these, as before.
DIRECT_EXECUTORS: frozenset[tuple[str, str | None]] = frozenset(
    {("object", "create"), ("object", "update"), ("object", "delete")})

#: The executors that a DELEGATED call runs, after the member's card
#: (STU-B2c-2a): the same three writes of an object. A call that is not direct
#: is delegated. It asks first, and it runs only the stored call of a request
#: that the member approved.
DELEGATED_EXECUTORS: frozenset[tuple[str, str | None]] = DIRECT_EXECUTORS

#: The app, the client stamp and the path template of a Studio request
#: (B2c-2a). The spend binds a request to ``session:<member>:studio:<package>``.
REQUEST_APP = "studio"
REQUEST_VIA = "studio:door"
DOOR_TEMPLATE = "/studio/apps/{app_id}/actions/{action}"

#: The modes of a delegated call: ``ask`` opens a card, and ``spend`` runs the
#: stored call of an approved request.
_DELEGATED_MODES = ("ask", "spend")

#: The query marker of an ask (fix round 1): ``ask=1``. A call with no proof
#: opens a card only with it. B2c-2b's tools send it, and the member's own
#: screen never does. It is in no digest and no binding.
ASK_MARKER = ("ask", "1")

#: The query marker of a preview (G4): ``preview=1``. A preview call commits
#: no write and runs no send. Any other value of the key is 422.
PREVIEW_MARKER = ("preview", "1")
BAD_PREVIEW = "bad_preview"
BAD_PREVIEW_TEXT = "Send preview=1 to preview this action, or send no preview key."

#: The most writes in flight for one org, and in the process (B2c-1). The door
#: counts them BEFORE it opens a session, so a call past the cap holds no
#: connection. The gateway runs one uvicorn worker, so a count in the process
#: is the count of the box. ⚠️ The total binds only the second and later
#: writers of an org. The first writer of an org always gets a slot, so two
#: busy orgs cannot shut out every other org (fix round 1).
WRITERS_PER_ORG = 3
WRITERS_TOTAL = 6

#: The most reads of a viewer in flight for one org, and in the process (WS-STU
#: J2a). A viewer reads an app that another member owns, through an install.
#: Each read can hold a pool connection for up to 5 seconds
#: (``studio_query.TIMEOUT_MS``), so the door counts them BEFORE it opens the
#: session of the read, in the shape of the writers. The first reader of an
#: org always gets a slot. The reads of the owner take no slot, so the answers
#: to the owner do not change. A default, and the owner confirms the numbers
#: at the preview of J2a.
READERS_PER_ORG = 3
READERS_TOTAL = 6

#: The keys of the body of a list. The one input schema is their only source.
LIST_INPUT_KEYS: frozenset[str] = frozenset(studio_query.list_input_schema()["properties"])

#: The A2 action id (``ActionDef.id``). A path ``action`` of another shape
#: names nothing, so the door reads nothing for it.
_ACTION_ID = re.compile(OP_PATTERN)

#: The most characters of one text in an audit row.
MAX_AUDIT_TEXT = 200

#: The most bytes of one body (1 MiB). The grammar caps a list body far below
#: this. B2c may raise it for writes.
MAX_BODY_BYTES = 1024 * 1024

NOT_DECLARED = "not_declared"
CLASS_X = "class_x"
NOT_IN_CATALOG = "not_in_catalog"
APPROVAL_REQUIRED = "approval_required"
SOURCE_NOT_OPEN = "source_not_open"
CATALOG_UNAVAILABLE = "catalog_unavailable"
OBJECT_MISSING = "object_missing"
BODY_TOO_LARGE = "body_too_large"
#: AUTO-7a: the named error of a run with no person present.
NO_PERSON = "no_person_present"
#: J2a: a write of a viewer, a member who reads an app through an install.
READ_ONLY = "read_only"

#: The code of each 403 and one sentence that a model can act on.
REFUSALS: dict[str, str] = {
    NOT_DECLARED: "This app declares no action with this id, so use an id from its manifest.",
    CLASS_X: "This action is class X, so no app can run it.",
    NOT_IN_CATALOG: (
        "The catalog does not serve this action now, so change the action in the manifest."),
    APPROVAL_REQUIRED: (
        "This action writes data and needs an approval, which this door cannot ask for yet."),
    SOURCE_NOT_OPEN: (
        "This door runs only the actions of an object: get and list, and create, update "
        "and delete from the member's own screen."),
    NO_PERSON: "This run has no person present, so it cannot ask a member to approve a write.",
    READ_ONLY: "This app is shared with you to read, so you cannot change its records.",
}

#: J3a: the one code of an action of a paused package, its status and its one
#: sentence. The door refuses each action and each class with it, before the
#: declaration, and a resend of an approved card too. 423 is not a 403,
#: because no right of the caller is missing: the package is stopped.
PACKAGE_PAUSED = "package_paused"
PAUSED_STATUS = 423
PAUSED_TEXT = "This app is paused, so it runs no action until someone resumes it."

#: The text of ``approval_required`` for a write of an object while
#: ``APP_REQUESTS_ORGS`` holds the org (fix round 2): an ask works there.
APPROVAL_ASK = (
    "This action writes data and needs an approval, so send the action again with ask=1 "
    "to ask the member.")

#: The answer of each refused spend (B2c-2a): the status, the code and one
#: sentence that a model can act on. The key is the reason that
#: ``acb_auth.app_requests.classify_refusal`` gives.
SPEND_REFUSALS: dict[str, tuple[int, str, str]] = {
    "not_found": (404, "request_not_found",
                  "This door finds no request of yours with this id, so send the action "
                  "again with ask=1 for a new approval."),
    "declined": (403, "request_declined",
                 "The member declined this change, so send the action again with ask=1 "
                 "only if the member wants it now."),
    "awaiting": (409, "request_awaiting",
                 "The member did not approve this change yet, so send it again with its "
                 "request_id after the approval."),
    "spent": (409, "request_spent",
              "This approval ran once already, so send the action again with ask=1 for a "
              "new approval."),
    "expired": (409, "request_expired",
                "This approval expired, so send the action again with ask=1 for a new "
                "approval."),
    "mismatch": (409, "request_mismatch",
                 "This approval does not match this action, or its change cannot run as "
                 "stored (a value of 1e16 or more), so change the input."),
}

#: The answer of a refusal of ``open_request`` that names no code of its own.
ASK_REFUSED = "request_refused"
ASK_AGAIN = "The door cannot open this request now, so send the change again."

#: The title of the card of each delegated write. The server writes it from
#: the object name, so the card shows no text that the caller chose.
CARD_TITLES: dict[str, str] = {
    "create": "Add a record to {object}.",
    "update": "Change a record of {object}.",
    "delete": "Delete a record of {object}.",
}

#: The 503 of a catalog that fails, and the 404 of an object that the package
#: does not hold. Each is one sentence that a model can act on.
CATALOG_DOWN = "The action catalog is not available now, so try the action again later."
NO_OBJECT = "This package holds no object for this action yet, so publish the package."

BODY_OVER_CAP = "This body is too large for this door, so send a smaller body."

BAD_RECORD_ID = "Send the record id as a UUID in the key id of a JSON object."
BAD_LIST_INPUT = (
    "Send a JSON object with only filter, sort, limit and offset, "
    "and whole numbers for limit and offset.")

#: The header value of a 503 ``query_timeout``, in seconds.
RETRY_AFTER = "2"

#: The header value of a 503 ``store_busy``, in seconds (B2c-1).
BUSY_RETRY_AFTER = "1"

STORE_BUSY = "store_busy"
FIELD_INVALID = "field_invalid"
RULE_REFUSED = "rule_refused"
RECORD_INVALID = "record_invalid"
BAD_INPUT = "bad_input"

BUSY_MESSAGE = "This app is busy now, so try the write again in a moment."
#: J2a: the 503 of a viewer read past the cap of :data:`READERS_PER_ORG`.
READ_BUSY_MESSAGE = "This app is busy now, so try the read again in a moment."
BAD_WRITE_INPUT = (
    "Send a JSON object with only data for a create, id and data for an update, "
    "and id for a delete.")
STORE_REFUSED = "The store refuses this write, so check the id and the data."

#: C13. The codes and the sentences of a refused list of lines.
TOO_MANY_LINES = "too_many_lines"
BAD_LINES = "bad_lines"
LINES_OVER_CAP = (
    "A save holds at most {cap} lines, and this save holds {n}. Save at most {cap} lines now, "
    "and add the rest in a second save.")
BAD_LINES_INPUT = (
    "Send the lines of `{key}` as a list. A new line is {{\"data\": {{...}}}}, and a changed "
    "line is {{\"id\": \"<line id>\", \"data\": {{...}}}}.")
NEW_RECORD_LINES = (
    "A new record has no lines yet, so each line of `{key}` takes data only, and no id.")
NO_SUCH_LINE = "This app holds no such line, so send the line with no id to add it."


@dataclasses.dataclass
class _Fail:
    """A refusal or a fault of a direct write that the door answers: the
    outcome and reason of its audit row, and the HTTP error. It is built
    inside the session and RAISED after the session closes, because an
    ``HTTPException`` inside the block would roll back the audit row."""

    outcome: str
    reason: str | None
    error: HTTPException


@dataclasses.dataclass
class _Outcome:
    """What one call did. The audit row and the answer come from it."""

    action: str
    uses: str | None = None
    cls: str | None = None
    refusal: str | None = None
    record: objects_store.Record | None = None
    page: objects_store.RecordPage | None = None
    query_error: studio_query.QueryError | None = None
    missing: bool = False
    object_missing: bool = False
    invalid: bool = False
    catalog_down: bool = False
    too_large: bool = False
    #: The id of the record that a direct delete removed.
    deleted: str | None = None
    #: The record of a direct write, for its audit row: the new id of a
    #: create that ran, or the id in the body of an update or a delete when
    #: it is a UUID. It is an id and holds no record data.
    record_id: str | None = None
    #: The answer of a direct write that failed or was refused (B2c-1).
    fail: _Fail | None = None
    #: The request that a delegated call opened or found again (B2c-2a).
    asked: app_requests.Opened | None = None
    #: The request of a delegated call, for its audit row: a canonical UUID,
    #: or None.
    request_id: str | None = None
    #: True when an ask works for this call (fix round 2): the org flag holds,
    #: the action is a write of an object, and a person is present.
    can_ask: bool = False
    #: True when the call wrote its own audit row, in the session of the write,
    #: or wrote none because it held no connection.
    audited: bool = False
    #: The ids of the lines that a write saved with its record (C13), by the key
    #: of their lines field, each list in the order of the body. The audit row
    #: holds their count only.
    lines: dict[str, list[str]] | None = None
    #: J2a. True when the last run of :func:`_check` read the app as a
    #: viewer, through an install, and not as its owner.
    viewer: bool = False
    #: J2a. The installed version that a viewer read, for the audit row.
    version: int | None = None
    #: G4: True when the call is a preview write or a preview send. Its audit
    #: row says ``simulated`` and holds ``preview``.
    preview: bool = False
    #: G4: True when a preview of a class B or C action of another source
    #: answered a simulated result, and no executor ran.
    simulated: bool = False


#: What the door reads an action of: the package of its owner, or the
#: installed version that a viewer reads (J2a). Both hold ``id`` and
#: ``manifest``. A viewer's app reaches only the read executors.
_App = packages_store.Package | _viewer.InstalledApp


def _app_id(raw: str) -> str:
    try:
        return str(uuid.UUID(raw))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=422, detail="The app id must be a UUID") from None


def _cap(value: str | None) -> str | None:
    """A free text of an audit row, cut to :data:`MAX_AUDIT_TEXT`."""
    return None if value is None else value[:MAX_AUDIT_TEXT]


def _declared(manifest: dict[str, Any], action: str) -> dict[str, Any] | None:
    """The ``actions[]`` entry of *manifest* whose id is *action*, or None."""
    found = manifest.get("actions")
    if not isinstance(found, list):
        return None
    for entry in found:
        if isinstance(entry, dict) and entry.get("id") == action:
            return entry
    return None


def refusal_for(ref: UsesRef, cls: str | None, direct: bool = False,
                delegated: bool = False, viewer: bool = False,
                preview: bool = False) -> str | None:
    """The refusal code of an action of *ref* and class *cls*, or None when
    an executor runs it. *cls* is None when no source gives the action a
    class. A class B or C object action runs for a *direct* call when
    :data:`DIRECT_EXECUTORS` holds it (B2c-1). For a *delegated* call it asks
    the member first, when :data:`DELEGATED_EXECUTORS` holds it (B2c-2a). A
    *viewer* (J2a) runs no class B or C action, whatever the proof, and a
    preview does not change that.
    For a *preview* (G4), each class B or C action passes: an object write
    runs and rolls back, and each other source gets a simulated result.
    Class X and an id outside the catalog stay refused."""
    if cls is None:
        return NOT_IN_CATALOG
    if cls == "X":
        return CLASS_X
    if cls in ("B", "C") and viewer:
        return READ_ONLY
    if cls in ("B", "C"):
        key = (ref.source, ref.op)
        if preview and (ref.source != "object" or key in DIRECT_EXECUTORS):
            return None
        if (direct and key in DIRECT_EXECUTORS) or (delegated and key in DELEGATED_EXECUTORS):
            return None
        return APPROVAL_REQUIRED
    if cls != "A" or (ref.source, ref.op) not in OPEN_EXECUTORS:
        return SOURCE_NOT_OPEN
    return None


async def _catalog_class(uses: str) -> str | None:
    """The class that the B1 catalog gives *uses*, or None. It opens its own
    session, so the caller runs it after its own session closes."""
    for row in await actions.catalog_rows():
        if row.get("id") == uses:
            return str(row.get("class"))
    return None


async def _read_body(request: Request) -> bytes | None:
    """The bytes of the body, or None past :data:`MAX_BODY_BYTES`. The read
    stops at the first chunk past the cap, so the door holds no more than one
    chunk over the cap. The executor parses the bytes."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _json_object(raw: bytes) -> dict[str, Any] | None:
    """The JSON object of the body *raw*, or None. The door parses the body
    here, after the switch and the package. A body that is not JSON, or that
    nests too deep to parse, gives None."""
    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None


def _record_id(raw: bytes) -> str | None:
    """The record id of the body *raw*, or None."""
    found = (_json_object(raw) or {}).get("id")
    return found if isinstance(found, str) else None


def _list_input(raw: bytes) -> dict[str, Any]:
    """The input of a list, from the body *raw*. An empty body means ``{}``.

    It refuses a key outside :data:`LIST_INPUT_KEYS`, and a ``limit`` or an
    ``offset`` that is not an ``int``. So a bool, a null, a float and
    ``Infinity`` stop here. The sentence never echoes a key or a value.
    """
    if not raw:
        return {}
    payload = _json_object(raw)
    if (payload is None or not LIST_INPUT_KEYS.issuperset(payload)
            or any(type(payload[k]) is not int for k in ("limit", "offset") if k in payload)):
        raise studio_query.QueryError(studio_query.VALUE_INVALID, BAD_LIST_INPUT)
    return payload


def _installed_object(package: _App, name: str) -> bool:
    """True for the owner's package. For a viewer (J2a), True only when the
    installed version declares the object *name*."""
    return not isinstance(package, _viewer.InstalledApp) or name in package.object_names


async def _run_object_get(db: Any, package: _App, ref: UsesRef,
                          raw: bytes, out: _Outcome) -> None:
    """Read one record of the object *ref* of *package*. The id comes from
    the body, checked by the id rule of C1."""
    rid = _record_id(raw)
    if rid is None:
        out.invalid = True
        return
    try:
        # The store checks each id before its statement, so a refusal here
        # leaves the session usable.
        obj = await objects_store.get_object_by_name(
            db, str(ref.object), package_id=package.id)
        if obj is None or not _installed_object(package, obj.name):
            out.object_missing = True
            return
        rec = await objects_store.get_record(db, obj.id, rid, package_id=package.id)
    except objects_store.ObjectStoreError:
        out.invalid = True
        return
    if rec is None:
        out.missing = True
    elif isinstance(package, _viewer.InstalledApp):
        # J2a fix round 1: the values of the installed fields only, and no writer.
        out.record = package.project_record(obj.name, rec)
    else:
        out.record = rec


async def _run_object_list(db: Any, package: _App, ref: UsesRef,
                           raw: bytes, out: _Outcome) -> None:
    """Read one page of the records of the object *ref* of *package*. The body
    is checked first, so a refused body reads nothing."""
    try:
        body = _list_input(raw)
        obj = await objects_store.get_object_by_name(
            db, str(ref.object), package_id=package.id)
        if obj is None or not _installed_object(package, obj.name):
            out.object_missing = True
            return
        if isinstance(package, _viewer.InstalledApp):
            # J2a fix round 1: a viewer filters and sorts on the fields of the
            # installed version only. The one grammar refuses another key with
            # ``query_field_unknown``, before any read.
            studio_query.parse_query(package.project(obj).fields, body.get("filter"),
                                     body.get("sort"))
        # The store checks each id and the input before its first statement,
        # and a timeout rolls back its SAVEPOINT only. So a refusal here
        # leaves the session usable.
        page = await objects_store.query_records(
            db, package_id=package.id, object_id=obj.id, filter=body.get("filter"),
            sort=body.get("sort"), limit=body.get("limit"), offset=body.get("offset"))
        if page is not None and isinstance(package, _viewer.InstalledApp):
            page = dataclasses.replace(page, records=tuple(
                package.project_record(obj.name, r) for r in page.records))
    except studio_query.QueryError as exc:
        out.query_error = exc
        return
    except objects_store.ObjectStoreError as exc:
        # The body named no id, so this is a stored name or id that the store
        # refuses. The package holds no object that the action can read.
        _log.warning("studio.invoke.stored_value_refused", error=type(exc).__name__)
        out.object_missing = True
        return
    if page is None:
        out.object_missing = True
    else:
        out.page = page


async def _check(db: Any, member: str, pid: str, action: str, out: _Outcome,
                 direct: bool = False, delegated: bool = False,
                 preview: bool = False, *,
                 org: str | None = None) -> tuple[_App, UsesRef | None]:
    """One run of the checks on the session *db*: the package as *member*, the
    declaration, then the class of an object action. Each other source takes
    its class from the catalog, outside the session.

    The door runs this function twice: before the body read, and again after
    it, on the fresh manifest. So it sets each field of *out* that it decides.
    A package that *member* cannot read raises 404.

    J2a: when *member* owns no such package and ``PACKAGE_HOST_ORGS`` holds
    *org*, the member reads the INSTALLED version through
    ``package_host/viewer.py``, and the declarations are those of that
    version. A viewer runs no class B or C action (``read_only``). A draft, a
    paused, an archived and an expired install raise the same 404, at each run.

    J3a: a paused package of *member* refuses each action with
    :data:`PACKAGE_PAUSED`, before the declaration, at each run. The answer
    names no ``uses``, so no later step of the door reads a class for it.
    """
    out.uses, out.cls, out.refusal = None, None, None
    out.viewer, out.version = False, None
    owned = await packages_store.get_package(db, member, pid)
    if owned is not None and owned.paused_at is not None:
        out.refusal = PACKAGE_PAUSED
        return owned, None
    package: _App | None = owned
    if package is None and package_host_allowed(org):
        package = await _viewer.installed_app(db, viewer=member, app_id=pid)
        if package is not None:
            out.viewer, out.version = True, package.version
    if package is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    entry = _declared(package.manifest, action)
    ref = None if entry is None else parse_uses(entry.get("uses"))
    if entry is None or ref is None:
        out.refusal = NOT_DECLARED
        return package, ref
    out.uses = str(entry["uses"])
    if ref.source == "object":
        out.cls = OBJECT_OP_CLASSES.get(str(ref.op))
        out.refusal = refusal_for(ref, out.cls, direct, delegated, out.viewer, preview)
    return package, ref


#: The writes in flight, by org. An org with none has no entry.
_IN_FLIGHT: dict[str, int] = {}
#: J2a. The reads of a viewer in flight, by org, in the same shape.
_READS_IN_FLIGHT: dict[str, int] = {}

#: The keys that the body of each direct write takes, and no other.
_WRITE_KEYS: dict[str, frozenset[str]] = {
    "create": frozenset({"data"}),
    "update": frozenset({"id", "data"}),
    "delete": frozenset({"id"}),
}


@contextlib.contextmanager
def _slot(counts: dict[str, int], org: str, wanted: bool, per_org: int,
          total: int) -> Iterator[bool]:
    """Take one slot of *counts* for *org*, when *wanted*. Yields True when
    the call holds a slot. The count runs with no ``await``, so no other call
    interleaves. The door takes the slot BEFORE it opens a session, so a call
    past the cap holds no connection. The slot goes back in ``finally``, and an
    org with no call in flight has no entry.

    The first call of an org always gets a slot. A second or later call needs
    fewer than *per_org* of its org, and fewer than *total* in all."""
    mine = counts.get(org, 0)
    if not wanted or mine >= per_org or (mine > 0 and sum(counts.values()) >= total):
        yield False
        return
    counts[org] = counts.get(org, 0) + 1
    try:
        yield True
    finally:
        left = counts.get(org, 1) - 1
        if left > 0:
            counts[org] = left
        else:
            counts.pop(org, None)


def _writer_slot(org: str, wanted: bool) -> contextlib.AbstractContextManager[bool]:
    """One slot of the writers of *org* (B2c-1): :data:`WRITERS_PER_ORG` and
    :data:`WRITERS_TOTAL`."""
    return _slot(_IN_FLIGHT, org, wanted, WRITERS_PER_ORG, WRITERS_TOTAL)


def _reader_slot(org: str, wanted: bool) -> contextlib.AbstractContextManager[bool]:
    """One slot of the reads of a viewer of *org* (J2a):
    :data:`READERS_PER_ORG` and :data:`READERS_TOTAL`."""
    return _slot(_READS_IN_FLIGHT, org, wanted, READERS_PER_ORG, READERS_TOTAL)


def _busy(message: str = BUSY_MESSAGE) -> _Fail:
    return _Fail("failed", STORE_BUSY, HTTPException(
        status_code=503, detail={"code": STORE_BUSY, "message": message},
        headers={"Retry-After": BUSY_RETRY_AFTER}))


def _refused(code: str, message: str, rule: str | None = None,
             field: str | None = None) -> _Fail:
    """A 422 with the one shape: ``code``, ``message``, ``rule`` and
    ``fieldErrors``. The text never echoes a value."""
    errors = [] if field is None else [{"field": field, "message": message}]
    return _Fail("refused", code, HTTPException(status_code=422, detail={
        "code": code, "message": message, "rule": rule, "fieldErrors": errors}))


@dataclasses.dataclass(frozen=True)
class _Line:
    """One line of a save (C13): its lines field, its place in that list (from
    1), the id of a changed line, and its data."""

    key: str
    to: str
    place: int
    id: str | None
    data: dict[str, Any]


def _at(line: _Line | None, message: str) -> str:
    """*message*, with the place of *line* first when the error is of a line."""
    return message if line is None else f"Line {line.place} of `{line.key}`: {message}"


def _set_write_error(out: _Outcome, exc: Exception, line: _Line | None = None) -> bool:
    """Set the answer of *out* for the error *exc* of a direct write, and say
    whether it did. A fault that it does not know stays a fault (500). An error
    of a *line* (C13) names its place, and its field error names the lines field.

    ⚠️ The order is load-bearing. ``StoreBusy`` is a subclass of
    ``ObjectStoreError``, so it comes first. A busy org is a 503 that can work
    later, and not a refused write."""
    if isinstance(exc, objects_store.StoreBusy):
        out.fail = _busy()
    elif isinstance(exc, objects_store.RecordNotFound) and line is None:
        out.missing = True
    elif isinstance(exc, objects_store.RecordNotFound):
        out.fail = _refused(BAD_LINES, _at(line, NO_SUCH_LINE), field=line.key)
    elif isinstance(exc, FieldError):
        out.fail = _refused(FIELD_INVALID, _at(line, exc.message),
                            field=exc.field if line is None else line.key)
    elif isinstance(exc, RuleViolation):
        computed = isinstance(exc, FormulaError) and exc.code == COMPUTED_FIELD
        field = getattr(exc, "field", None)
        out.fail = _refused(COMPUTED_FIELD if computed else RULE_REFUSED, _at(line, exc.message),
                            rule=exc.rule, field=field if line is None or field is None
                            else line.key)
    elif isinstance(exc, studio_query.QueryError):
        out.fail = _Fail("failed" if exc.code == studio_query.TIMEOUT else "refused", exc.code,
                         _query_refusal(exc))
    elif isinstance(exc, RecordError):
        out.fail = _refused(RECORD_INVALID, _at(line, str(exc)))
    elif isinstance(exc, objects_store.ObjectStoreError):
        out.fail = _refused(RECORD_INVALID, _at(line, STORE_REFUSED))
    else:
        return False
    return True


def _uuid_or_none(value: str) -> str | None:
    """*value* as a canonical UUID, or None. An audit row names a record by
    its id only, so a text that is not a UUID never reaches the row."""
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _write_input(op: str, raw: bytes) -> dict[str, Any] | None:
    """The body of a direct write, or None. It takes exactly the keys of
    :data:`_WRITE_KEYS`, ``data`` is an object and ``id`` is a string. The
    store checks the id rule of C1."""
    body = _json_object(raw)
    if body is None or set(body) != _WRITE_KEYS[op]:
        return None
    if "data" in body and not isinstance(body["data"], dict):
        return None
    if "id" in body and not isinstance(body["id"], str):
        return None
    return body


def _split_lines(op: str, obj: objects_store.StudioObject,
                 body: dict[str, Any]) -> tuple[Any, list[_Line]] | _Fail:
    """The record data of *body* and its lines (C13), or the refusal of the
    lines. Each key of a lines field of *obj* holds a list of lines, and the
    record data keeps every other key. A new line is ``{data}``, and a
    changed line is ``{id, data}``. A new record takes new lines only. At most
    :data:`MAX_LINES_PER_SAVE` lines in all, so the record and its lines fit
    one batch. Pure: it runs no statement, and the text echoes no value."""
    if op == "delete":
        return None, []
    data = dict(body["data"])
    lists: list[tuple[FieldSpec, list[Any]]] = []
    for f in obj.fields:
        if f.lines is None or f.key not in data:
            continue
        items = data.pop(f.key)
        if not isinstance(items, list):
            return _refused(BAD_LINES, BAD_LINES_INPUT.format(key=f.key), field=f.key)
        lists.append((f, items))
    total = sum(len(items) for _f, items in lists)
    if total > MAX_LINES_PER_SAVE:
        return _refused(TOO_MANY_LINES, LINES_OVER_CAP.format(cap=MAX_LINES_PER_SAVE, n=total))
    lines: list[_Line] = []
    for f, items in lists:
        for place, item in enumerate(items, start=1):
            keys = set(item) if isinstance(item, dict) else set()
            if (keys not in ({"data"}, {"id", "data"}) or not isinstance(item["data"], dict)
                    or not isinstance(item.get("id", ""), str)):
                return _refused(BAD_LINES, BAD_LINES_INPUT.format(key=f.key), field=f.key)
            if op == "create" and "id" in item:
                return _refused(BAD_LINES, NEW_RECORD_LINES.format(key=f.key), field=f.key)
            lines.append(_Line(f.key, str(f.to), place, item.get("id"), item["data"]))
    return data, lines


async def _line_writes(db: Any, package: packages_store.Package,
                       lines: list[_Line]) -> list[objects_store.Write] | None:
    """The writes of *lines*, each linked to the record write at place 0. None
    when the package holds no object that a lines field names."""
    ids: dict[str, str] = {}
    writes: list[objects_store.Write] = []
    for line in lines:
        if line.to not in ids:
            try:
                child = await objects_store.get_object_by_name(db, line.to, package_id=package.id)
            except objects_store.ObjectStoreError:
                child = None
            if child is None:
                return None
            ids[line.to] = child.id
        writes.append(objects_store.Write(
            op="create" if line.id is None else "update", object_id=ids[line.to],
            record_id=line.id, data=line.data, parent=0))
    return writes


def _saved_lines(lines: list[_Line], answers: list[Any]) -> dict[str, list[str]]:
    """The ids of the saved lines, by the key of their lines field, each list
    in the order of the body (C13)."""
    out: dict[str, list[str]] = {}
    for line, saved in zip(lines, answers, strict=True):
        if isinstance(saved, objects_store.Record):
            out.setdefault(line.key, []).append(saved.id)
    return out


async def _do_object_write(db: Any, member: str, package: packages_store.Package,
                           ref: UsesRef, raw: bytes, out: _Outcome) -> None:
    """Run one create, update or delete as *member*, through ONE
    ``apply_writes`` call. The record is the first write, and each of its
    lines (C13) follows it, so the record and its lines are saved all or none.
    It sets *out* and never raises an ``HTTPException``. The caller writes the
    audit row."""
    op = str(ref.op)
    body = _write_input(op, raw)
    if body is None:
        out.fail = _refused(BAD_INPUT, BAD_WRITE_INPUT)
        return
    if "id" in body:
        out.record_id = _uuid_or_none(body["id"])
    try:
        obj = await objects_store.get_object_by_name(
            db, str(ref.object), package_id=package.id)
    except objects_store.ObjectStoreError:
        obj = None
    if obj is None:
        out.object_missing = True
        return
    split = _split_lines(op, obj, body)
    if isinstance(split, _Fail):
        out.fail = split
        return
    data, lines = split
    line_writes = await _line_writes(db, package, lines)
    if line_writes is None:
        out.object_missing = True
        return
    write = objects_store.Write(op=op, object_id=obj.id, record_id=body.get("id"), data=data)
    try:
        answers = await objects_store.apply_writes(
            db, member, package_id=package.id, writes=[write, *line_writes])
    except objects_store.BatchError as exc:
        # The SAVEPOINT of the batch rolled back, so the session is usable.
        line = lines[exc.index - 1] if 0 < exc.index <= len(lines) else None
        if not _set_write_error(out, exc.cause, line):
            raise
        return
    except Exception as exc:
        # A refusal before the batch, such as an id that is not a UUID.
        if not _set_write_error(out, exc):
            raise
        return
    done = answers[0]
    if op == "delete":
        out.deleted = str(body["id"])
    elif isinstance(done, objects_store.Record):
        out.record = done
        out.record_id = done.id
        if lines:
            out.lines = _saved_lines(lines, answers[1:])
            # A roll-up over the lines (C7b) changed the record after its own
            # write, in the same batch. The answer reads it once, so it shows
            # the sum that the batch committed.
            out.record = await objects_store.get_record(
                db, obj.id, done.id, package_id=package.id) or done


async def _run_object_write(db: Any, member: str, package: packages_store.Package,
                            ref: UsesRef, raw: bytes, out: _Outcome) -> None:
    """The write and its audit row, in the ONE session *db*. The audit row is
    written for each outcome that leaves the session usable. A failed audit
    raises, so ``tenant_session`` rolls the write back (B2c-1)."""
    await _do_object_write(db, member, package, ref, raw, out)
    await record_app_audit_in(db, app_id=package.id, user_email=member, kind="action",
                              detail=_audit_detail(out))
    out.audited = True


class _Undo(Exception):
    """Roll a SAVEPOINT back. A spend raises it when the stored write did not
    run, so the request stays approved (B2c-2a). A preview raises it after
    each write, so the write never commits (G4)."""


async def _run_preview_write(db: Any, member: str, package: packages_store.Package,
                             ref: UsesRef, raw: bytes, out: _Outcome) -> None:
    """The preview of one write (G4), in the ONE session *db*.

    It runs the same write path as a direct write, :func:`_do_object_write`,
    so each check of a field, a rule and a line runs for real. The write sits
    in a SAVEPOINT that ALWAYS rolls back, also when the write ran. So the
    answer shows what the write would give, and no row of the object store
    changes. The audit row comes after the rollback, with the outcome
    ``simulated``. A raw fault rolls the SAVEPOINT back as it leaves, and
    ``tenant_session`` rolls back the rest."""
    try:
        async with db.begin_nested():
            await _do_object_write(db, member, package, ref, raw, out)
            raise _Undo
    except _Undo:
        pass
    out.preview = True
    if ref.op == "create":
        # The id of a preview create was never stored, so no row names it.
        out.record_id = None
    await record_app_audit_in(db, app_id=package.id, user_email=member, kind="action",
                              detail=_audit_detail(out))
    out.audited = True


# ── The delegated writes (B2c-2a) ─────────────────────────────────────────


def _door_call(pid: str, action: str, uses: str | None, body: Any) -> dict[str, Any]:
    """The call of a delegated write, as the request stores it and as the
    digest reads it. The query holds the ``uses`` of the action, so a request
    spends only while the action names the same object and verb."""
    return {
        "method": "POST",
        "template": DOOR_TEMPLATE,
        "path": DOOR_TEMPLATE.format(app_id=pid, action=action),
        "query": [["uses", str(uses or "")]],
        "body": body,
        "if_match": None,
    }


def _ask_key(digest: str) -> str:
    """The idempotency key of a NEW request of one change (fix round 2): the
    digest and a fresh nonce. The lock and the one read of
    ``open_studio_request``, not the key, make a retry give the open request.
    The replay of ``open_request`` does not change for its MCP callers."""
    return f"{digest}:{uuid.uuid4().hex}"


async def _open_or_find(db: Any, *, org: str, member: str, package: packages_store.Package,
                        ref: UsesRef, call: dict[str, Any], binding: str,
                        out: _Outcome) -> app_requests.Opened:
    """The open request of this exact change, or a new one. ONE lock and ONE
    read, then at most one ``open_request``, so the cost does not grow with
    the closed requests of the change."""
    digest = store.call_digest(call, binding)
    found = await app_requests.open_studio_request(
        db, org=org, member=member, app=REQUEST_APP, binding=binding, digest=digest)
    if found is not None:
        return found
    return await app_requests.open_request(
        db, org=org, member=member, source="studio", app=REQUEST_APP,
        subject=package.id, op=out.action, cls=str(out.cls), call=call,
        idempotency_key=_ask_key(digest), connection_id=None,
        via=REQUEST_VIA, title=_card_title(ref), app_name=package.name)


def _card_title(ref: UsesRef) -> str:
    """The title of the card, from the verb and the object name."""
    return CARD_TITLES[str(ref.op)].format(object=ref.object)


def _ask_refused(exc: HTTPException) -> _Fail:
    """A refusal of ``open_request`` in the shape of the door: ``code`` and
    ``message``. A call over 32 KiB of JSON is 413 ``payload_too_large``."""
    detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
    code = str(detail.get("error") or ASK_REFUSED)
    message = str(detail.get("message") or ASK_AGAIN)
    return _Fail("refused", code, HTTPException(
        status_code=exc.status_code, detail={"code": code, "message": message}))


def _spend_refused(why: str) -> _Fail:
    """The answer of a refused spend, by the reason of ``classify_refusal``."""
    status, code, message = SPEND_REFUSALS.get(why, SPEND_REFUSALS["not_found"])
    return _Fail("refused", code, HTTPException(
        status_code=status, detail={"code": code, "message": message}))


def _wrote(out: _Outcome) -> bool:
    """True when the write of *out* ran: a record or a delete, and no refusal."""
    if out.fail is not None or out.missing or out.object_missing:
        return False
    return out.record is not None or out.deleted is not None


async def _run_ask(db: Any, org: str, member: str, package: packages_store.Package,
                   ref: UsesRef, raw: bytes, out: _Outcome) -> None:
    """Open the card of a delegated write, in the session of the second check,
    and run nothing. A body of the wrong keys, or an object that the package
    does not hold, opens no card. The audit row commits with the request."""
    body = _write_input(str(ref.op), raw)
    obj = None
    if body is not None:
        try:
            obj = await objects_store.get_object_by_name(
                db, str(ref.object), package_id=package.id)
        except objects_store.ObjectStoreError:
            obj = None
    split = None if body is None or obj is None else _split_lines(str(ref.op), obj, body)
    if body is None:
        out.fail = _refused(BAD_INPUT, BAD_WRITE_INPUT)
    elif obj is None:
        out.object_missing = True
    elif isinstance(split, _Fail):
        # C13: a list of lines that the resend would refuse opens no card.
        out.fail = split
    else:
        call = _door_call(package.id, out.action, out.uses, body)
        binding = store.studio_binding(member, REQUEST_APP, package.id)
        try:
            out.asked = await _open_or_find(db, org=org, member=member, package=package,
                                            ref=ref, call=call, binding=binding, out=out)
        except HTTPException as exc:
            # Each refusal of the store comes before its first write.
            out.fail = _ask_refused(exc)
        else:
            out.request_id = out.asked.request_id
    await record_app_audit_in(db, app_id=package.id, user_email=member, kind="action",
                              detail=_audit_detail(out))
    out.audited = True


async def _run_spend(db: Any, org: str, member: str, package: packages_store.Package,
                     ref: UsesRef, request_id: str, out: _Outcome) -> None:
    """Run the STORED call of an approved request, in ONE session *db*.

    The order binds: read the stored call, compute its digest, spend, run the
    write, then write the audit row. The spend and the write sit in one
    SAVEPOINT. A write that does not run (a refusal, a missing record, a busy
    store) rolls the spend back, so the request stays approved. A failed audit
    raises, so ``tenant_session`` rolls back the spend and the write."""
    out.request_id = _uuid_or_none(request_id)
    binding = store.studio_binding(member, REQUEST_APP, package.id)
    stored = await app_requests.read_request(db, org=org, member=member, request_id=request_id)
    body = ((stored or {}).get("call") or {}).get("body")
    digest = store.call_digest(_door_call(package.id, out.action, out.uses, body), binding)
    spent: store.Spent | None = None
    try:
        async with db.begin_nested():
            spent = await store.spend(db, org=org, request_id=request_id, app=REQUEST_APP,
                                      member=member, binding=binding, digest=digest)
            if spent is None:
                raise _Undo
            await _do_object_write(db, member, package, ref,
                                   store.body_bytes(spent.call.get("body")), out)
            if not _wrote(out):
                raise _Undo
    except _Undo:
        pass
    if spent is None:
        why = await store.classify_refusal(db, org=org, request_id=request_id, app=REQUEST_APP,
                                           member=member, binding=binding, digest=digest)
        out.fail = _spend_refused(why)
    await record_app_audit_in(db, app_id=package.id, user_email=member, kind="action",
                              detail=_audit_detail(out))
    out.audited = True


def _mode(first: UsesRef | None, direct: bool, delegated: bool,
          request_id: str | None, preview: bool = False) -> str:
    """How the door runs the action of the first check: ``read``,
    ``preview``, ``direct``, ``ask``, ``spend`` or ``none``. A *preview*
    write runs and rolls back (G4), and wins over each other mode. A write
    runs at once for a direct call (B2c-1). Each other write is delegated
    (B2c-2a): with no *request_id* it asks, and with one it spends."""
    if first is None or (first.source, first.op) not in DIRECT_EXECUTORS:
        return "read"
    if preview:
        return "preview"
    if direct:
        return "direct"
    if delegated:
        return "ask" if request_id is None else "spend"
    return "none"


def _request_id(request: Request) -> str | None:
    """The ``request_id`` of the query, or None. It names a request, never an
    org or a member: the spend finds only a request of the session's member,
    in the session's org, under the binding of this package."""
    found = request.query_params.get("request_id")
    return None if found is None else str(found)


def _asks(request: Request) -> bool:
    """True when the query holds the ask marker ``ask=1`` exactly (fix round
    1). Any other value is no ask."""
    key, value = ASK_MARKER
    return request.query_params.get(key) == value


def _previews(request: Request) -> bool:
    """True when the query holds the preview marker ``preview=1`` (G4). No
    key is False. Any other value is 422 ``bad_preview``, because a client
    that means a preview must never get a live write."""
    key, value = PREVIEW_MARKER
    found = request.query_params.get(key)
    if found is None:
        return False
    if found != value:
        raise HTTPException(status_code=422, detail={
            "code": BAD_PREVIEW, "message": BAD_PREVIEW_TEXT})
    return True


async def _run_executor(request: Request, user: UserContext, pid: str, action: str,
                        out: _Outcome, direct: bool, delegated: bool, mode: str,
                        request_id: str | None, preview: bool = False) -> None:
    """Read the body, check again, then run the executor. No session is
    open during the read, so a slow client holds no pool connection. The read
    can last as long as the client wants, so the first check proves nothing
    after it. The check and the executor share one session.

    A direct write takes its writer slot BEFORE the session (B2c-1), and the
    same session holds the write and its audit row. A spend does the same, and
    it reads no byte of the body (B2c-2a). An ask opens its card in the
    session of the check. A preview write takes a slot too, because it runs
    the write and holds the org lock until its SAVEPOINT rolls back (G4)."""
    member = str(user.email)
    org = str(user.organization_id or "")
    if mode == "spend":
        # The door runs the STORED call, so the body of a resend is not read.
        raw: bytes | None = b""
    else:
        raw = await _read_body(request)
    if raw is None:
        out.too_large = True
        return
    wants = mode in ("direct", "spend", "preview")
    # J2a: a read of a viewer takes a reader slot. A read of the owner takes none.
    reads = mode == "read" and out.viewer
    with _writer_slot(org, wants) as admitted, _reader_slot(org, reads) as read_admitted:
        if wants and not admitted:
            # No session was opened, so no audit row can be written.
            out.fail, out.audited = _busy(), True
            return
        if reads and not read_admitted:
            out.fail, out.audited = _busy(READ_BUSY_MESSAGE), True
            return
        async with _tenant_session() as db:
            package, ref = await _check(db, member, pid, action, out, direct, delegated,
                                        preview, org=org)
            if out.refusal is not None or ref is None:
                return
            if ref.source != "object":
                # The action named an object at the first check. The catalog gives
                # no class in this session, and no other source has an executor.
                out.refusal = SOURCE_NOT_OPEN
                return
            if (ref.source, ref.op) in DIRECT_EXECUTORS:
                if not isinstance(package, packages_store.Package):
                    # J2a: a viewer never reaches a write, whatever the class says.
                    # A preview write of a viewer stops here too.
                    out.refusal = READ_ONLY
                    return
                if mode == "preview" and admitted:
                    # G4: the write runs and rolls back. It never commits.
                    await _run_preview_write(db, member, package, ref, raw, out)
                    return
                if mode == "ask":
                    await _run_ask(db, org, member, package, ref, raw, out)
                    return
                if mode == "spend" and admitted:
                    await _run_spend(db, org, member, package, ref, str(request_id), out)
                    return
                if not (direct and admitted):
                    # The manifest changed during the read to name a write.
                    out.refusal = SOURCE_NOT_OPEN
                    return
                await _run_object_write(db, member, package, ref, raw, out)
                return
            if mode in (*_DELEGATED_MODES, "preview"):
                # The manifest changed during the read to name a read.
                out.refusal = SOURCE_NOT_OPEN
                return
            run = _run_object_list if ref.op == "list" else _run_object_get
            await run(db, package, ref, raw, out)


def _is_direct(request: Request, user: UserContext) -> bool:
    """True when the call is a DIRECT call of a member (B2c-1).

    All of these hold: the proof of the BFF verifies for this member, this
    method and this path, the call is no outside agent, and the user is a
    member with an address. Anything else is False, and the call gets today's
    ``approval_required``. The proof is read once, before the body."""
    email = str(user.email or "")
    if user.agent is not None or user.is_agent or "@" not in email \
            or email.strip().lower().startswith("system:"):
        return False
    return bool(_direct_proof.is_direct_proof(
        _direct_proof.header_value(request.headers), email, request.method, request.url.path))


def _query_refusal(exc: studio_query.QueryError) -> HTTPException:
    """The answer to a refused query. A timeout is 503, because the same call
    can work later. Each other code is 422."""
    if exc.code == studio_query.TIMEOUT:
        return HTTPException(status_code=503, detail={"code": exc.code, "message": exc.message},
                             headers={"Retry-After": RETRY_AFTER})
    return HTTPException(status_code=422, detail={
        "code": exc.code, "message": exc.message, "field": exc.field})


def _outcome(out: _Outcome) -> tuple[str, str | None]:
    """The outcome and the reason of the audit row of *out*. A preview write
    or send that ran changed nothing, so its outcome is ``simulated`` (G4)."""
    outcome, reason = _plain_outcome(out)
    if out.preview and outcome == "ran":
        return "simulated", reason
    return outcome, reason


def _plain_outcome(out: _Outcome) -> tuple[str, str | None]:
    """The outcome and the reason of *out*, as if it were no preview."""
    reason: str | None
    if out.catalog_down:
        outcome, reason = "failed", CATALOG_UNAVAILABLE
    elif out.refusal is not None:
        outcome, reason = "refused", out.refusal
    elif out.too_large:
        outcome, reason = "refused", BODY_TOO_LARGE
    elif out.fail is not None:
        outcome, reason = out.fail.outcome, out.fail.reason
    elif out.query_error is not None:
        timed_out = out.query_error.code == studio_query.TIMEOUT
        outcome, reason = "failed" if timed_out else "refused", out.query_error.code
    elif out.invalid:
        outcome, reason = "refused", "bad_input"
    elif out.object_missing:
        outcome, reason = "not_found", OBJECT_MISSING
    elif out.missing:
        outcome, reason = "not_found", None
    elif out.asked is not None:
        outcome, reason = "asked", None
    else:
        outcome, reason = "ran", None
    return outcome, reason


def _audit_detail(out: _Outcome) -> dict[str, Any]:
    """The audit row of *out*: the action, its class and the outcome. It holds
    no record data and no value of the body, and each text is capped."""
    outcome, reason = _outcome(out)
    detail: dict[str, Any] = {"action": _cap(out.action), "uses": _cap(out.uses),
                              "class": _cap(out.cls), "outcome": outcome}
    if out.preview:
        detail["preview"] = True
    if reason is not None:
        detail["reason"] = reason
    if out.record_id is not None:
        detail["record_id"] = out.record_id
    if out.request_id is not None:
        detail["request_id"] = out.request_id
    if out.lines:
        detail["lines"] = sum(len(ids) for ids in out.lines.values())
    if out.viewer:
        # J2a: the row of a viewer names the installed version that it read.
        detail["viewer"] = True
        detail["version"] = out.version
    return detail


def _answer(out: _Outcome) -> dict[str, Any] | JSONResponse:
    """The answer of the call *out*, or the refusal that it raises. A card
    that a delegated call opened is 202 (B2c-2a)."""
    if out.catalog_down:
        raise HTTPException(status_code=503, detail={
            "code": CATALOG_UNAVAILABLE, "message": CATALOG_DOWN})
    if out.refusal == PACKAGE_PAUSED:
        raise HTTPException(status_code=PAUSED_STATUS, detail={
            "code": PACKAGE_PAUSED, "message": PAUSED_TEXT})
    if out.refusal is not None:
        ask = out.refusal == APPROVAL_REQUIRED and out.can_ask
        raise HTTPException(status_code=403, detail={
            "code": out.refusal, "message": APPROVAL_ASK if ask else REFUSALS[out.refusal]})
    if out.too_large:
        raise HTTPException(status_code=413, detail={
            "code": BODY_TOO_LARGE, "message": BODY_OVER_CAP})
    if out.fail is not None:
        raise out.fail.error
    if out.query_error is not None:
        raise _query_refusal(out.query_error)
    if out.invalid:
        raise HTTPException(status_code=422, detail=BAD_RECORD_ID)
    if out.object_missing:
        raise HTTPException(status_code=404, detail={
            "code": OBJECT_MISSING, "message": NO_OBJECT})
    if out.asked is not None:
        # The request in its state now: a retry of the ask gives the first one.
        return JSONResponse(status_code=202, content={
            "state": out.asked.state, "request_id": out.asked.request_id,
            "expires_at": out.asked.expires_at})
    if out.simulated:
        # G4: no executor ran. The answer names what a live call would run.
        return {"preview": True, "simulated": {
            "action": _cap(out.action), "uses": _cap(out.uses), "class": _cap(out.cls)}}
    if out.page is not None:
        return dataclasses.asdict(out.page)
    mark: dict[str, Any] = {"preview": True} if out.preview else {}
    if out.deleted is not None:
        return {"deleted": True, "id": out.deleted, **mark}
    if out.missing or out.record is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if out.lines:
        return {"record": dataclasses.asdict(out.record), "lines": out.lines, **mark}
    return {"record": dataclasses.asdict(out.record), **mark}


@router.post("/apps/{app_id}/actions/{action}", response_model=None)
async def invoke_action(
    app_id: str,
    action: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any] | JSONResponse:
    """Run the action *action* of the caller's package *app_id*. See the
    module docstring for the order of the checks."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if not _ACTION_ID.fullmatch(action):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    pid = _app_id(app_id)
    # G4: the preview mark, from the query only. A bad value is 422 here,
    # before any read, so it never falls through to a live write.
    preview = _previews(request)
    out = _Outcome(action=action)
    # The proof is read once, here, before any body read (B2c-1).
    direct = _is_direct(request, user)
    # B2c-2a: a call that is not direct is delegated only when it asks for a
    # card with ask=1, or names a request (fix round 1). While APP_REQUESTS_ORGS
    # leaves the org out, or with no marker, it gets today's 403. So an
    # unsigned Save of the member's own screen never gets a 202. A preview
    # (G4) asks for no card and spends none.
    allowed = not direct and not preview and app_requests.requests_allowed(
        user.organization_id)
    request_id = _request_id(request) if allowed else None
    delegated = allowed and (request_id is not None or _asks(request))
    with preview_scope(preview):
        async with _tenant_session() as db:
            package, ref = await _check(db, user.email, pid, action, out, direct, delegated,
                                        preview, org=user.organization_id)
        # Fix round 2: where an ask works (the org flag, an object write, a person
        # present), the text of approval_required names the step: ask=1.
        out.can_ask = (allowed and ref is not None and not out.viewer
                       and (ref.source, ref.op) in DELEGATED_EXECUTORS
                       and not no_person_present())
        if ref is not None and out.uses is not None and ref.source != "object":
            try:
                out.cls = await _catalog_class(out.uses)
            except Exception as exc:
                _log.warning("studio.invoke.catalog_failed", error=type(exc).__name__)
                out.catalog_down = True
            else:
                # J2a: a viewer gets read_only for a class B or C action, also
                # in a preview (G4), so a viewer never gets a simulated send.
                out.refusal = refusal_for(ref, out.cls, viewer=out.viewer, preview=preview)
                if out.refusal is None and not preview:
                    # No executor of another source exists. Fail closed.
                    out.refusal = SOURCE_NOT_OPEN
                elif out.refusal is None:
                    # A preview of a class B or C action: no executor of
                    # another source runs, and nothing is sent (G4).
                    out.preview = out.simulated = True
        elif ref is not None and out.refusal is None:
            mode = _mode(ref, direct, delegated, request_id, preview)
            if mode in _DELEGATED_MODES and no_person_present():
                # AUTO-7a: nobody sees the card of a run with no person present,
                # so it opens none and spends none. The mark is a ContextVar: it
                # binds an in-process caller, and an HTTP call does not carry it.
                out.refusal = NO_PERSON
            else:
                await _run_executor(request, user, pid, action, out, direct, delegated, mode,
                                    request_id, preview)
        if not out.audited:
            await record_app_audit_bound(app_id=package.id, user_email=user.email,
                                         kind="action", detail=_audit_detail(out))
    return _answer(out)
