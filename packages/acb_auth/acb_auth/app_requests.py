"""The approval store of the app door: the half that branch 0 can import (MCP-P1b-1).

Spec: ``project-docs/specs/external_agent_access.md`` §19.5, AD-6, AD-16 and
AD-17 of §19.12, and row MCP-P1b-1 of §19.13.

An app request waits for its member on an agent run that names an app and
no task (AD-5, migration ``pm_agent_runs_app_subject``). The gateway half,
``gateway/app_requests.py``, opens a request and writes the member's
decision. This module holds the three parts that branch 0 of
``acb_auth.deps`` must call in MCP-P1b-2, and that the Studio door calls in
B2c. ``acb_auth`` cannot import the gateway, so they live here.

* :func:`digest_of` is the ONE digest of a call (AD-17, as amended by the
  MCP-P1b audit). Its lines are the version, the method, the template, the
  path, the sorted query, the body hash, ``if-match`` and the binding.
* :func:`spend` spends an approved request in ONE ``UPDATE ... RETURNING``
  (AD-6, D5 of the audit). The move of the run from ``in_progress`` to
  ``completed`` IS the spend: the WHERE guards it, and row locking gives one
  winner. It writes the ``action`` activity of the spend in the same
  transaction, and it returns the STORED call.
* :func:`classify_refusal` says why :func:`spend` found no row. It reads the
  request of this member and this connection only, so it leaks nothing.

**The binding (D3 of the audit).** The last line of a digest names who may
spend: ``conn:<connection id>`` for the MCP door, and
``session:<member>:<app>:<subject>`` for a Studio write. A request made under
one binding never spends under another.

**Pure SQL on a session that the caller opened.** No function here opens a
session or a connection, takes the tenant from input, or imports the
gateway (R5). Each statement names the org, the app, the member and the
connection, and FORCE RLS binds the org once more.

Fences: ``tests/unit/test_app_requests_r8.py`` (R8, each rule red under a
named mutation), and ``test_digest_lines_are_pinned`` and
``test_only_app_requests_spends`` in the same file.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import text

#: The first line of a digest. A change of the lines takes a new version.
DIGEST_VERSION = "v1"

#: The ``kind`` of an app request in ``content_metadata.request``.
REQUEST_KIND = "app_write"

#: The two decisions of the member, and the option ids of the request.
APPROVE = "approve"
DECLINE = "decline"

#: Who opened a request. ``mcp`` is the MCP door, ``studio`` a Studio write.
SOURCES: tuple[str, ...] = ("mcp", "studio")

#: What :func:`classify_refusal` gives. ``awaiting`` is a request that the
#: member has not decided yet.
REFUSALS: tuple[str, ...] = (
    "not_found", "declined", "spent", "expired", "awaiting", "mismatch",
)

#: The action of the activity that a spend writes (D7 of the audit).
SPEND_ACTION = "app_request_spent"

#: How many characters of the digest the spend activity keeps.
DIGEST_SHOWN = 12

_HEX64 = re.compile(r"[0-9a-f]{64}")
#: A line of a digest holds no control character, so no value can add a line.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


# ── The digest (AD-17) ──────────────────────────────────────────────────────


def canonical_query(query: Iterable[tuple[str, str]]) -> str:
    """The query pairs, sorted by key and then by value, as ``urlencode``
    writes them. The form of ``agent_signing.canonical_target``."""
    return urlencode(sorted((str(k), str(v)) for k, v in query))


def body_bytes(body: Any) -> bytes:
    """The body of a write as ``app_manifests.build_call`` writes it: compact
    JSON with sorted keys. ``None`` is a call with no body."""
    if body is None:
        return b""
    return json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest_lines(
    *,
    binding: str,
    method: str,
    template: str,
    path: str,
    query: Iterable[tuple[str, str]],
    body_sha256: str,
    if_match: str | None,
) -> tuple[str, ...]:
    """The lines of the digest of one call, in their fixed order.

    ``if-match`` is a line, because a PATCH with another ``If-Match`` is
    another change. The binding is the last line (D3). A part that holds a
    control character raises ``ValueError``, so no value can forge a line.
    """
    if not _HEX64.fullmatch(body_sha256 or ""):
        raise ValueError("body_sha256 is not a SHA-256 in hex")
    lines = (
        DIGEST_VERSION,
        method.upper(),
        template,
        path,
        canonical_query(query),
        body_sha256,
        f"if-match:{if_match or ''}",
        binding,
    )
    for line in lines:
        if _CONTROL.search(line):
            raise ValueError("a part of the call holds a control character")
    if not binding:
        raise ValueError("a digest needs a binding")
    return lines


def digest_of(
    *,
    binding: str,
    method: str,
    template: str,
    path: str,
    query: Iterable[tuple[str, str]],
    body_sha256: str,
    if_match: str | None,
) -> str:
    """The SHA-256 in hex of :func:`digest_lines`, one line each."""
    lines = digest_lines(binding=binding, method=method, template=template, path=path,
                         query=query, body_sha256=body_sha256, if_match=if_match)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def call_digest(call: Mapping[str, Any], binding: str) -> str:
    """The digest of a stored call (``method``, ``template``, ``path``,
    ``query``, ``body`` and ``if_match``) under *binding*."""
    return digest_of(
        binding=binding,
        method=str(call["method"]),
        template=str(call["template"]),
        path=str(call["path"]),
        query=[(str(k), str(v)) for k, v in call.get("query") or ()],
        body_sha256=hashlib.sha256(body_bytes(call.get("body"))).hexdigest(),
        if_match=call.get("if_match"),
    )


# ── The binding (D3) ────────────────────────────────────────────────────────


def mcp_binding(connection_id: str) -> str:
    """The binding of a request of the MCP door: its connection."""
    return f"conn:{uuid.UUID(str(connection_id))}"


def studio_binding(member: str, app: str, subject: str) -> str:
    """The binding of a Studio request: the member, the app and the package."""
    return f"session:{member.strip().lower()}:{app}:{subject}"


def binding_connection(binding: str) -> str | None:
    """The connection of a ``conn:`` binding, or ``None`` for a ``session:``
    binding. Any other text raises ``ValueError``."""
    if binding.startswith("conn:"):
        return str(uuid.UUID(binding.removeprefix("conn:")))
    if binding.startswith("session:"):
        return None
    raise ValueError("unknown binding")


# ── The spend (AD-6, D5) ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Spent:
    """A request that :func:`spend` spent, with the call that it stored."""

    request_id: str
    run_id: str
    app: str
    op: str
    cls: str
    source: str
    subject: str
    call: dict[str, Any]
    digest: str


#: ONE statement, ONE winner. The move of the run from ``in_progress`` to
#: ``completed`` is the spend, and each guard sits in the WHERE:
#:
#: * the org, the app, the member and the connection of the request;
#: * the whole binding of the request (fix round 1, review P3-1). The
#:   connection part alone let a Studio request of one package spend under
#:   the binding of another package;
#: * ``status = 'in_progress'``: the member approved it, and nobody spent it;
#: * the newest prompt of the run answers THIS elicitation with ``approve``;
#: * the digest of the call is the digest of the request;
#: * the request is live by the clock of the database.
#:
#: Two sessions that race both reach the row. Row locking makes the second
#: UPDATE read the row again: it is ``completed`` now, so it gets no row. A
#: miss changes nothing, so a wrong digest leaves the request approved.
SPEND_SQL = (
    "UPDATE pm_agent_runs r"
    "   SET status = 'completed', ended_at = clock_timestamp(),"
    "       last_activity_at = clock_timestamp(), session_key = NULL"
    "  FROM pm_agent_run_activities e"
    " WHERE e.id = CAST(:rid AS uuid) AND e.type = 'elicitation'"
    "   AND r.id = e.run_id"
    "   AND r.organization_id = CAST(:org AS uuid)"
    "   AND r.app IS NOT NULL AND r.app = :app"
    "   AND r.member_email = :member"
    "   AND r.connection_id IS NOT DISTINCT FROM CAST(:conn AS uuid)"
    "   AND r.status = 'in_progress'"
    "   AND e.content_metadata->'request'->>'kind' = 'app_write'"
    "   AND e.content_metadata->'request'->>'binding' = :binding"
    "   AND e.content_metadata->'request'->>'digest' = :digest"
    "   AND CAST(e.content_metadata->'request'->>'expires_at' AS timestamptz)"
    "       > clock_timestamp()"
    "   AND (SELECT p.in_reply_to = e.id AND p.content->'selected'->>0 = 'approve'"
    "          FROM pm_agent_run_activities p"
    "         WHERE p.run_id = r.id AND p.type = 'prompt'"
    "         ORDER BY p.seq DESC LIMIT 1)"
    " RETURNING r.id AS run_id, r.app, r.client_via, e.content_metadata->'request' AS request"
)

#: The record of a spend (D7): the client, the app, the op, the class, the
#: first characters of the digest and the request id. No argument value.
_SPEND_ACTIVITY_SQL = (
    "INSERT INTO pm_agent_run_activities"
    " (run_id, task_id, app, type, content, ephemeral, actor, actor_via)"
    " VALUES (CAST(:run AS uuid), NULL, :app, 'action', CAST(:content AS jsonb),"
    "         false, :member, :via)"
)


def _uuid_or_none(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _request_of(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, Mapping) else {}


async def spend(
    db: Any,
    *,
    org: str,
    request_id: str,
    app: str,
    member: str,
    binding: str,
    digest: str,
) -> Spent | None:
    """Spend the approved request *request_id* once, or return ``None``.

    *db* is a session of the tenant *org* that the caller opened. The spend
    and its ``action`` activity are one transaction: the caller's. ``None``
    changes nothing. Call :func:`classify_refusal` to say why.
    """
    rid = _uuid_or_none(request_id)
    org_id = _uuid_or_none(org)
    if rid is None or org_id is None:
        return None
    row = (await db.execute(text(SPEND_SQL), {
        "rid": rid, "org": org_id, "app": app, "member": member.strip().lower(),
        "conn": binding_connection(binding), "binding": binding, "digest": digest,
    })).fetchone()
    if row is None:
        return None
    request = _request_of(row.request)
    content = {
        "body": None,
        "action": SPEND_ACTION,
        "parameters": {
            "client": str(row.client_via),
            "app": str(row.app),
            "op": str(request.get("op") or ""),
            "class": str(request.get("cls") or ""),
            "digest": digest[:DIGEST_SHOWN],
            "request_id": rid,
        },
    }
    await db.execute(text(_SPEND_ACTIVITY_SQL), {
        "run": str(row.run_id), "app": str(row.app),
        "content": json.dumps(content, ensure_ascii=False),
        "member": member.strip().lower(), "via": str(row.client_via),
    })
    return Spent(
        request_id=rid,
        run_id=str(row.run_id),
        app=str(row.app),
        op=str(request.get("op") or ""),
        cls=str(request.get("cls") or ""),
        source=str(request.get("source") or ""),
        subject=str(request.get("subject") or ""),
        call=dict(request.get("call") or {}),
        digest=str(request.get("digest") or ""),
    )


#: The request of THIS member, app, connection and binding only, with its
#: decision.
_CLASSIFY_SQL = (
    "SELECT r.status, e.content_metadata->'request' AS request,"
    "       (CAST(e.content_metadata->'request'->>'expires_at' AS timestamptz)"
    "          > clock_timestamp()) AS live,"
    "       (SELECT p.content->'selected'->>0 FROM pm_agent_run_activities p"
    "         WHERE p.run_id = r.id AND p.type = 'prompt' AND p.in_reply_to = e.id"
    "         ORDER BY p.seq DESC LIMIT 1) AS decision"
    "  FROM pm_agent_run_activities e"
    "  JOIN pm_agent_runs r ON r.id = e.run_id"
    " WHERE e.id = CAST(:rid AS uuid) AND e.type = 'elicitation'"
    "   AND r.organization_id = CAST(:org AS uuid)"
    "   AND r.app IS NOT NULL AND r.app = :app"
    "   AND r.member_email = :member"
    "   AND r.connection_id IS NOT DISTINCT FROM CAST(:conn AS uuid)"
    "   AND e.content_metadata->'request'->>'kind' = 'app_write'"
    "   AND e.content_metadata->'request'->>'binding' = :binding"
)


async def classify_refusal(
    db: Any,
    *,
    org: str,
    request_id: str,
    app: str,
    member: str,
    binding: str,
    digest: str,
) -> str:
    """Why :func:`spend` found no row: one of :data:`REFUSALS`.

    Call it only after :func:`spend` returned ``None``. A request of another
    member, another app, another connection or another binding (a Studio
    request of another package) reads ``not_found``, so the
    answer says nothing about it. The order of the checks is fixed: a
    decline, then a spend, then the expiry, then a request with no
    decision, then a digest that differs.
    """
    rid = _uuid_or_none(request_id)
    org_id = _uuid_or_none(org)
    if rid is None or org_id is None:
        return "not_found"
    row = (await db.execute(text(_CLASSIFY_SQL), {
        "rid": rid, "org": org_id, "app": app, "member": member.strip().lower(),
        "conn": binding_connection(binding), "binding": binding,
    })).fetchone()
    if row is None:
        return "not_found"
    if row.decision == DECLINE:
        return "declined"
    if row.decision == APPROVE and row.status == "completed":
        return "spent"
    if not row.live:
        return "expired"
    if row.decision != APPROVE:
        return "awaiting"
    if _request_of(row.request).get("digest") != digest:
        return "mismatch"
    # Approved, live and of this digest, but the run is not `in_progress`:
    # another writer closed it. It cannot be spent.
    return "spent"


__all__ = [
    "APPROVE",
    "DECLINE",
    "DIGEST_SHOWN",
    "DIGEST_VERSION",
    "REFUSALS",
    "REQUEST_KIND",
    "SOURCES",
    "SPEND_ACTION",
    "SPEND_SQL",
    "Spent",
    "binding_connection",
    "body_bytes",
    "call_digest",
    "canonical_query",
    "classify_refusal",
    "digest_lines",
    "digest_of",
    "mcp_binding",
    "spend",
    "studio_binding",
]
