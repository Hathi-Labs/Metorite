"""``/agent-box-health`` — the heartbeat of an agent box (WS-MCP SW-77).

Spec: ``project-docs/specs/agent_coordination.md`` §3.11.4 and SW-77, and
``project-docs/specs/external_agent_access.md`` §10.9 (box tokens).

The runner on a box (SW-78) posts a heartbeat each minute through the MCP
tool ``report_box_health``. The Machines tab (SW-81) reads it. Two routes,
both dark behind ``AGENT_BOX_HEALTH_ORGS`` (404 while the org is not listed).
The router checks the flag first, then ``feature:projects``.

* ``POST /agent-box-health`` — the write of the tool. It is a pair of
  ``agent_oauth.MCP_WRITE_ROUTES`` and of ``agent_oauth.BOX_WRITE_ROUTES``.
  ⚠️ **Only a box writes.** Branch 0 admits the pair only for a connection of
  credential ``box`` that holds ``box:runner``, and this route checks again:
  the caller is a signed agent, its stamp is a box stamp, and its scopes hold
  ``box:runner``. A person, an OAuth connection and a box with no runner
  scope get 403 (:data:`BOX_ONLY`). So no person can write a heartbeat
  through the REST API.
* ``GET /agent-box-health`` — the read of a member. A signed agent gets 403,
  and branch 0 refuses the pair anyway, because it is not in ``MCP_ROUTES``.

**One row for each box.** A heartbeat is an upsert on
``(organization_id, connection_id)``. The connection is the signed one
(``user.agent.connection_id``), the member is the connection's member
(``user.email``, from the connection row), and the org is the bound tenant.
No body names any of them (``extra="forbid"``, R5).

**Who reads which box ("may see").** A member reads the boxes of the
member's own connections. A holder of ``integrations:manage`` reads every box
of the org, as the "All agent connections" table of Integrations, AI agents
lists every connection (EA-4b). A box of a revoked or an expired connection
is not listed. Everything else fails closed: a caller with no email reads no
row.

**Untrusted display data.** Each value comes from the box. The model holds
each number to a range and each state to a closed list (422 otherwise). The
note holds at most :data:`NOTE_MAX` characters, and the route strips each
control character first, NUL too. The lease counts are at most
:data:`LEASE_KINDS_MAX` keys of a fixed shape. The account state is best
effort (§3.11.10). The server time stamps the row, never a time of the box.

**Offline.** A heartbeat older than :data:`OFFLINE_AFTER_S` seconds reads
``offline``. The read computes it on the database clock.

**No rate cap of its own.** The write cap of a connection (``agent_oauth.
WRITE_CAP``) covers the heartbeat, and the upsert keeps one row for each box.

Fence: ``tests/unit/test_agent_box_health_r8.py`` (R8, two orgs).
"""

from __future__ import annotations

import json
import unicodedata
from typing import Annotated, Any, Literal

from acb_auth import UserContext, agent_oauth, get_current_user, require_feature_router
from acb_common import get_logger, get_settings
from acb_common.db import current_tenant
from fastapi import APIRouter, Depends, HTTPException
from gateway.db import tenant_session as _tenant_session
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator
from sqlalchemy import text

_log = get_logger("gateway.agent_box_health")

#: A heartbeat older than this many seconds reads ``offline`` (SW-77).
OFFLINE_AFTER_S = 180
#: The longest account note, in characters, after the strip.
NOTE_MAX = 200
#: The most kinds of lease in one heartbeat, and the largest count of one.
LEASE_KINDS_MAX = 8
LEASE_COUNT_MAX = 10000
#: The shape of a lease kind.
LEASE_KIND_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
#: The most boxes of one read. A member holds at most 20 box connections, and
#: an org at most 100 (``agent_tokens.BOX_ORG_CAP``).
LIST_MAX = 100

#: The permission that reads every box of the org (EA-4b).
MANAGE_PERMISSION = "integrations:manage"

SIGN_IN_STATES = ("signed_in", "signed_out", "unknown")
ACCOUNT_STATES = ("ok", "rate_limited", "unknown")

BOX_ONLY = "Only an agent box with the scope box:runner can post a heartbeat."
READ_BY_MEMBER = "A member reads the heartbeat of a box in Metorite."


# ── The flag ────────────────────────────────────────────────────────────────


def box_health_allowed(organization_id: str | None) -> bool:
    """True when ``AGENT_BOX_HEALTH_ORGS`` holds the org, or holds ``*``.
    Default OFF.

    The org comes from the session or the bound tenant, never from request
    input (R5). It reads ``get_settings()`` on each call, so a flag reload
    applies a change with no restart.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "agent_box_health_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed


async def require_box_health(user: UserContext = Depends(get_current_user)) -> None:
    """404 while ``AGENT_BOX_HEALTH_ORGS`` does not hold the caller's org."""
    if not box_health_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Not Found")


#: The flag first, so a dark route answers 404 for every caller. Then the
#: feature gate of the Projects router.
router = APIRouter(
    prefix="/agent-box-health", tags=["agent-box-health"],
    dependencies=[Depends(require_box_health), require_feature_router("projects")],
)


# ── The body ────────────────────────────────────────────────────────────────


def clean_note(value: str | None) -> str | None:
    """*value* with each control character (Unicode ``Cc``, NUL too) taken
    out, trimmed and cut to :data:`NOTE_MAX`. An empty result is ``None``."""
    if value is None:
        return None
    kept = "".join(c for c in value if unicodedata.category(c) != "Cc").strip()
    return kept[:NOTE_MAX] or None


class BoxHealthIn(BaseModel):
    """One heartbeat. ⚠️ ``extra="forbid"``: a body cannot name an org, a
    member or a connection. Each field is optional, so a heartbeat with no
    field still says that the box is alive."""

    model_config = ConfigDict(extra="forbid")

    mem_free_mb: StrictInt | None = Field(default=None, ge=0, le=16777216)
    load_1m: float | None = Field(default=None, ge=0, le=10000, allow_inf_nan=False)
    sessions: StrictInt | None = Field(default=None, ge=0, le=10000)
    sign_in: Literal["signed_in", "signed_out", "unknown"] = "unknown"
    account: Literal["ok", "rate_limited", "unknown"] = "unknown"
    account_note: str | None = Field(default=None, max_length=4 * NOTE_MAX)
    ci_lane_busy: StrictBool | None = None
    leases: dict[
        Annotated[str, Field(pattern=LEASE_KIND_PATTERN)],
        Annotated[StrictInt, Field(ge=0, le=LEASE_COUNT_MAX)],
    ] = Field(default_factory=dict, max_length=LEASE_KINDS_MAX)

    @field_validator("account_note", mode="after")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return clean_note(value)


# ── The write ───────────────────────────────────────────────────────────────


def _box_caller(user: UserContext) -> tuple[str, str]:
    """``(connection_id, member)`` of a box that holds ``box:runner``, else
    the 403 :data:`BOX_ONLY`.

    The second lock after branch 0 (``deps._claim_call``). A person has no
    ``agent``. An OAuth connection never has a box stamp, because
    ``agent_oauth.actor_via_for`` gives a box stamp only for credential
    ``box``. A box with no runner scope lacks ``box:runner``.
    """
    agent = getattr(user, "agent", None)
    if (
        agent is None
        or not str(agent.actor_via or "").startswith(agent_oauth.BOX_STAMP_PREFIX)
        or agent_oauth.BOX_RUNNER not in tuple(agent.scopes or ())
        or not agent.connection_id
        or not user.email
    ):
        raise HTTPException(status_code=403, detail=BOX_ONLY)
    return str(agent.connection_id), str(user.email).strip().lower()


_UPSERT_SQL = """
INSERT INTO agent_box_health
       (connection_id, member_email, reported_at, mem_free_mb, load_1m, sessions,
        sign_in_state, account_state, account_note, ci_lane_busy, leases)
VALUES (CAST(:connection_id AS uuid), :member, now(), :mem, :load, :sessions,
        :sign_in, :account, :note, :ci, CAST(:leases AS jsonb))
ON CONFLICT (organization_id, connection_id) DO UPDATE SET
       member_email  = EXCLUDED.member_email,
       reported_at   = EXCLUDED.reported_at,
       mem_free_mb   = EXCLUDED.mem_free_mb,
       load_1m       = EXCLUDED.load_1m,
       sessions      = EXCLUDED.sessions,
       sign_in_state = EXCLUDED.sign_in_state,
       account_state = EXCLUDED.account_state,
       account_note  = EXCLUDED.account_note,
       ci_lane_busy  = EXCLUDED.ci_lane_busy,
       leases        = EXCLUDED.leases
RETURNING reported_at
"""


@router.post("", status_code=200)
async def report_box_health(
    payload: BoxHealthIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Store the heartbeat of the calling box. 403 for any other caller."""
    connection_id, member = _box_caller(user)
    async with _tenant_session() as db:
        row = (await db.execute(text(_UPSERT_SQL), {
            "connection_id": connection_id,
            "member": member,
            "mem": payload.mem_free_mb,
            "load": payload.load_1m,
            "sessions": payload.sessions,
            "sign_in": payload.sign_in,
            "account": payload.account,
            "note": payload.account_note,
            "ci": payload.ci_lane_busy,
            "leases": json.dumps(dict(sorted(payload.leases.items()))),
        })).one()
    _log.info("agent_box_health.reported", connection_id=connection_id)
    return {"reported_at": row.reported_at.isoformat(), "offline_after_s": OFFLINE_AFTER_S}


# ── The read ────────────────────────────────────────────────────────────────

#: The boxes of the bound org that the caller may see. ``:all`` is true for a
#: holder of :data:`MANAGE_PERMISSION`. The org is named in the WHERE as well
#: as by RLS, so the read holds for a role that bypasses RLS too.
_LIST_SQL = """
SELECT CAST(h.connection_id AS TEXT) AS connection_id,
       c.client_name                 AS name,
       h.member_email,
       h.reported_at,
       h.reported_at < now() - make_interval(secs => CAST(:offline_s AS INTEGER)) AS offline,
       h.mem_free_mb, h.load_1m, h.sessions, h.sign_in_state, h.account_state,
       h.account_note, h.ci_lane_busy, h.leases
  FROM agent_box_health h
  JOIN agent_connection c
    ON c.id = h.connection_id
   AND c.organization_id = h.organization_id
 WHERE h.organization_id = CAST(:org AS uuid)
   AND c.credential = 'box'
   AND c.revoked_at IS NULL
   AND c.expires_at > now()
   AND (CAST(:all AS BOOLEAN) OR h.member_email = CAST(:me AS TEXT))
 ORDER BY c.client_name, h.connection_id
 LIMIT :limit
"""


def _box_out(row: Any) -> dict[str, Any]:
    """The answer shape of one box. Each value is display data of the box."""
    return {
        "connection_id": row.connection_id,
        "name": row.name,
        "member": row.member_email,
        "reported_at": row.reported_at.isoformat(),
        "state": "offline" if row.offline else "online",
        "mem_free_mb": row.mem_free_mb,
        "load_1m": float(row.load_1m) if row.load_1m is not None else None,
        "sessions": row.sessions,
        "sign_in": row.sign_in_state,
        "account": row.account_state,
        "account_note": row.account_note,
        "ci_lane_busy": row.ci_lane_busy,
        "leases": dict(row.leases or {}),
    }


@router.get("")
async def list_box_health(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The boxes that the member may see, with ``online`` or ``offline``."""
    if getattr(user, "agent", None) is not None:
        raise HTTPException(status_code=403, detail=READ_BY_MEMBER)
    me = str(user.email or "").strip().lower()
    see_all = bool(me) and user.has_permission(MANAGE_PERMISSION)
    if not me:
        return {"boxes": [], "offline_after_s": OFFLINE_AFTER_S}
    async with _tenant_session() as db:
        rows = (await db.execute(text(_LIST_SQL), {
            "org": str(current_tenant()), "all": see_all, "me": me,
            "offline_s": OFFLINE_AFTER_S, "limit": LIST_MAX,
        })).all()
    return {"boxes": [_box_out(r) for r in rows], "offline_after_s": OFFLINE_AFTER_S}


__all__ = [
    "ACCOUNT_STATES",
    "BOX_ONLY",
    "LEASE_KINDS_MAX",
    "LIST_MAX",
    "MANAGE_PERMISSION",
    "NOTE_MAX",
    "OFFLINE_AFTER_S",
    "SIGN_IN_STATES",
    "BoxHealthIn",
    "box_health_allowed",
    "clean_note",
    "list_box_health",
    "report_box_health",
    "router",
]
