"""``/agent-guidance`` — the org's written rules for agents (WS-MCP SW-31a).

Spec: ``project-docs/specs/agent_coordination.md`` SW-31 (D140.6), and
``project-docs/specs/project_alignment.md`` §4.2 (D160), which extends the
store that this module writes.

Guidance is the org's own text, at three levels: the **org**, a **space** (a
ROOT ``pm_projects`` row) and a **project** below it. The agent that works a
task gets the blocks that apply, **nearest first**: the task's project, each
parent up to the space, then the org. The server does not merge the text. The
nearest block wins on a conflict, and the instructions of the MCP edge say so.

Four routes, all dark behind ``AGENT_GUIDANCE_ORGS`` (404 while the org is
not listed). The router checks the flag first, then ``feature:projects``
(``require_feature_router("projects")``, as the Projects router does):

* ``POST /agent-guidance`` — save a new version. Append only: each save is a
  new row of ``pm_agent_guidance``, and the trigger ``pm_agent_guidance_guard``
  refuses an UPDATE. The history is the rows.
* ``GET /agent-guidance/history`` — the versions of one scope, newest first,
  with ``written_by`` and ``written_at``.
* ``GET /agent-guidance/tasks/{task_id}`` — the blocks that apply to a task.
  This is the route of the MCP edge (``get_task_context`` and ``claim_task``).
* ``GET /agent-guidance/refs`` — for ``my_work``: the id and version of the
  nearest block for each of up to 25 projects (``project_ids``, one comma
  list). No body. A project that the caller cannot see gets null, the same
  answer as an unknown id (the 404 rule of ``load_visible_project``).

**Placement.** The routes are not under ``/projects``. Org guidance is not a
project thing, the editor is a Settings screen (SW-31b), and a route under
``/projects`` needs a row in the chat manifest (``test_projects_chat_coverage``),
which is a chat tool that SW-31a does not build. The chat tool is a follow-up.

**Who writes.** A project or a space: :func:`authority.assert_may_write_guidance`,
which is the write floor of a grant (a lead of the project or of an ancestor,
or a ``projects:settings:write`` holder), and it refuses a personal tree. The
org: ``core.ORG_VOCABULARY_WRITE`` (``admin:settings:manage``), the house rule
for a write that applies to every project. The owner and the admin roles hold
it. A ``manager`` does not, although it holds ``projects:settings:write``
(migrations 196 and 200), so a manager cannot write org guidance.
``require_admin_user`` is not used: it checks ``admin:members:read``, a read
floor for the admin package. An agent connection never writes.

**Who reads.** Any member who can open the task. The body of a space or an
ancestor shows even when the member cannot see that project. Its source is
then the bare stub ``{"hidden": true}``, with no name and no project id, as
EA-15 shows a hidden parent (owner question 3 default).

**Caps.** A block holds at most :data:`BLOCK_MAX` characters (422 above). An
answer holds at most :data:`ANSWER_MAX`, and it drops the FARTHEST blocks first.

The tenant comes from the session (R5). The version comes from the database,
with a retry on the unique index.

Fence: ``tests/unit/test_agent_guidance.py`` and, on a real database,
``tests/unit/test_agent_guidance_r8.py``.
"""

from __future__ import annotations

import uuid
from typing import Any

from acb_auth import UserContext, agent_oauth, get_current_user, require_feature_router
from acb_common import get_logger, get_settings
from acb_common.db import current_tenant
from acb_playbook import PlaybookError, load_topic
from fastapi import APIRouter, Depends, HTTPException, Query
from gateway.db import tenant_session as _tenant_session
from gateway.routes.projects.authority import assert_may_write_guidance
from gateway.routes.projects.core import (
    MAX_DEPTH,
    ORG_VOCABULARY_WRITE,
    actor,
    load_visible_project,
    load_visible_task,
    resolve_visibility,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

_log = get_logger("gateway.agent_guidance")

#: The longest block of guidance, in characters. 8,001 gives 422.
BLOCK_MAX = 8000
#: The longest answer, in characters of body. The farthest blocks drop first.
ANSWER_MAX = 20000
#: The most projects of one ``refs`` read (the ``my_work`` limit).
REFS_MAX = 25
#: The most versions of one history read.
HISTORY_MAX = 200
#: A save retries this many times on a lost race for the next version.
SAVE_ATTEMPTS = 5

#: ``kind`` of the rows that SW-31a reads and writes.
KIND = "guidance"

ORG_WRITE_REFUSED = (
    "Only an organization admin can change the agent guidance of the whole organization."
)
AGENT_WRITE_REFUSED = "An agent connection cannot change guidance. A member does it in Settings."
ARCHIVED_REFUSED = "This project is archived, so its agent guidance cannot change."
SAVE_BUSY = "Another save of this guidance was in progress. Try again."

LEVELS = ("project", "space", "company", "default")

#: PB-5 (D186, ``orchestration_playbook.md`` §4, §7). The Metorite default
#: level: one read-only block at the end of the answer, below the org. Its text
#: is the playbook topic :data:`DEFAULT_TOPIC`, and its version is the version
#: of that topic. No route writes it, and the history route never lists it.
#: It shows only for an org that ``playbook_orgs`` names. It is the farthest
#: block, so the cap of the answer drops it first.
DEFAULT_TOPIC = "default-guidance"
DEFAULT_LEVEL = "default"
#: A fixed id, so a client can tell the block from a row of the table.
DEFAULT_BLOCK_ID = str(uuid.uuid5(uuid.NAMESPACE_OID, "metorite.guidance.default"))
DEFAULT_SOURCE: dict[str, Any] = {
    "kind": "default", "project_id": None, "name": None, "hidden": False}

#: The source of a block whose project the member cannot see. It names no
#: project, so the answer cannot show which hidden project holds the rule.
HIDDEN_SOURCE: dict[str, bool] = {"hidden": True}


# ── The flag ────────────────────────────────────────────────────────────────


def guidance_allowed(organization_id: str | None) -> bool:
    """True when ``AGENT_GUIDANCE_ORGS`` holds the org, or holds ``*``. Default OFF.

    The org comes from the session or the bound tenant, never from request
    input (R5). It reads ``get_settings()`` on each call, so a flag reload
    applies a change with no restart.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "agent_guidance_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed


async def require_guidance(user: UserContext = Depends(get_current_user)) -> None:
    """404 while ``AGENT_GUIDANCE_ORGS`` does not hold the caller's org."""
    if not guidance_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Not Found")


#: The flag first, so a dark route answers 404 for every caller. Then the
#: feature gate of the Projects router (``routes/projects/core.py``), so a
#: member who cannot open Projects cannot read or write its guidance.
router = APIRouter(
    prefix="/agent-guidance", tags=["agent-guidance"],
    dependencies=[Depends(require_guidance), require_feature_router("projects")],
)


# ── The wire ────────────────────────────────────────────────────────────────


class GuidanceIn(BaseModel):
    """One save. ``project_id`` null is the org level."""

    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    body: str = Field(max_length=BLOCK_MAX)

    @field_validator("body")
    @classmethod
    def _no_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("The guidance holds a control character that cannot be saved.")
        return value


def _uuid_or_404(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"{what} not found") from exc


def _version_out(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id) if row.project_id is not None else None,
        "version": int(row.version),
        "body": row.body,
        "written_by": row.written_by,
        "written_at": row.written_at.isoformat() if row.written_at is not None else None,
    }


# ── The chain ───────────────────────────────────────────────────────────────

#: The projects above each origin, the origin first. ``depth`` 0 is the origin,
#: and the row with a NULL ``parent_project_id`` is the space (the root). The
#: walk is bounded by :data:`MAX_DEPTH` so a bad parent loop cannot spin.
_CHAINS_SQL = f"""
WITH RECURSIVE up AS (
    SELECT p.id AS origin, p.id, p.parent_project_id, p.name, 0 AS depth
      FROM pm_projects p
     WHERE p.id = ANY(CAST(:origins AS uuid[]))
    UNION ALL
    SELECT u.origin, p.id, p.parent_project_id, p.name, u.depth + 1
      FROM pm_projects p
      JOIN up u ON p.id = u.parent_project_id
     WHERE u.depth < {MAX_DEPTH}
)
SELECT origin, id, parent_project_id, name, depth FROM up
 ORDER BY origin, depth
"""

#: The newest version of each scope in a set. The org level is the NULL group.
_LATEST_SQL = """
SELECT DISTINCT ON (g.project_id)
       g.id, g.project_id, g.version, g.body, g.written_by, g.written_at
  FROM pm_agent_guidance g
 WHERE g.kind = 'guidance'
   AND (g.project_id IS NULL OR g.project_id = ANY(CAST(:ids AS uuid[])))
 ORDER BY g.project_id, g.version DESC
"""


async def _chains(db: Any, origins: list[str]) -> dict[str, list[Any]]:
    rows = (await db.execute(text(_CHAINS_SQL), {"origins": origins})).fetchall()
    out: dict[str, list[Any]] = {}
    for r in rows:
        out.setdefault(str(r.origin), []).append(r)
    return out


async def _visible_ids(db: Any, vis: Any, ids: list[str]) -> set[str]:
    """The ids of *ids* that the caller may see, through the ONE visibility
    seam (``Visibility.project_clause``, the rule of ``load_visible_project``).
    An unknown id is not in the set, so "no such project" and "not yours"
    get the same answer."""
    if not ids:
        return set()
    rows = (await db.execute(
        text(
            "SELECT id FROM pm_projects WHERE id = ANY(CAST(:ids AS uuid[])) "
            f"AND {vis.project_clause('id')}"
        ),
        {"ids": ids, **vis.params},
    )).fetchall()
    return {str(r.id) for r in rows}


async def _latest(db: Any, project_ids: list[str]) -> dict[str | None, Any]:
    """``{project id or None for the org: newest row}`` of the scopes asked."""
    rows = (await db.execute(text(_LATEST_SQL), {"ids": project_ids})).fetchall()
    return {(str(r.project_id) if r.project_id is not None else None): r for r in rows}


def default_block(organization_id: str | None) -> dict[str, Any] | None:
    """The Metorite default block for an org, or ``None``.

    ``None`` while ``playbook_orgs`` does not name the org, or when the topic
    cannot be read. The org is the bound tenant, never request input (R5)."""
    if not agent_oauth.playbook_allowed(organization_id):
        return None
    try:
        topic = load_topic(DEFAULT_TOPIC)
    except PlaybookError:
        return None
    if topic is None or not topic.body.strip():
        return None
    return {"id": DEFAULT_BLOCK_ID, "level": DEFAULT_LEVEL, "source": dict(DEFAULT_SOURCE),
            "version": topic.version, "body": topic.body}


def _has_text(row: Any) -> bool:
    """A scope that was cleared (an empty body) applies no block."""
    return bool(row is not None and str(row.body).strip())


def cut_to_answer(blocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Keep the nearest blocks whose bodies fit :data:`ANSWER_MAX`.

    *blocks* are nearest first. The FARTHEST drop first, so the answer is the
    longest nearest-first run that fits. Returns the kept blocks and the number
    dropped.
    """
    kept = list(blocks)
    total = sum(len(b["body"]) for b in kept)
    while kept and total > ANSWER_MAX:
        total -= len(kept.pop()["body"])
    return kept, len(blocks) - len(kept)


async def blocks_for_project(db: Any, vis: Any, project_id: str) -> tuple[list[dict[str, Any]], int]:
    """The blocks that apply to a task in *project_id*, nearest first, and the
    number dropped by the cap of the answer."""
    chain = (await _chains(db, [project_id])).get(project_id, [])
    ids = [str(r.id) for r in chain]
    if not ids:
        floor = default_block(current_tenant())
        return cut_to_answer([floor] if floor else [])
    seen = await _visible_ids(db, vis, ids)
    latest = await _latest(db, ids)
    blocks: list[dict[str, Any]] = []
    for r in chain:
        row = latest.get(str(r.id))
        if not _has_text(row):
            continue
        blocks.append({
            "id": str(row.id),
            "level": "space" if r.parent_project_id is None else "project",
            # A project that the member cannot see is the bare stub: no name
            # and no project id, as EA-15 shows a hidden parent.
            "source": (
                {"kind": "project", "project_id": str(r.id), "name": r.name, "hidden": False}
                if str(r.id) in seen else dict(HIDDEN_SOURCE)
            ),
            "version": int(row.version),
            "body": row.body,
        })
    org = latest.get(None)
    if _has_text(org):
        blocks.append({
            "id": str(org.id), "level": "company",
            "source": {"kind": "company", "project_id": None, "name": None, "hidden": False},
            "version": int(org.version), "body": org.body,
        })
    floor = default_block(current_tenant())
    if floor is not None:
        blocks.append(floor)
    return cut_to_answer(blocks)


# ── The routes ──────────────────────────────────────────────────────────────


def may_write_org_guidance(user: UserContext | None) -> bool:
    """True when *user* may write the guidance of the whole org.

    The house rule for an org-wide write, :data:`core.ORG_VOCABULARY_WRITE`
    (``admin:settings:manage``): org guidance reaches every project, also the
    projects that the writer cannot see. ``projects:settings:write`` is not
    enough, because a ``manager`` holds it."""
    return user is not None and user.has_permission(ORG_VOCABULARY_WRITE)


@router.post("", status_code=201)
async def save_guidance(
    payload: GuidanceIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Save a new version of one scope. Append only.

    403 for an agent connection and for a member without the write floor.
    404 for a project the member cannot see. 409 for an archived project.
    422 for a body over :data:`BLOCK_MAX`.
    """
    if getattr(user, "agent", None) is not None:
        raise HTTPException(status_code=403, detail=AGENT_WRITE_REFUSED)
    project_id = _uuid_or_404(payload.project_id, "Project") if payload.project_id else None
    me = actor(user).strip().lower()
    for _attempt in range(SAVE_ATTEMPTS):
        try:
            async with _tenant_session() as db:
                if project_id is None:
                    if not may_write_org_guidance(user):
                        raise HTTPException(status_code=403, detail=ORG_WRITE_REFUSED)
                else:
                    vis = await resolve_visibility(db, user)
                    project = await load_visible_project(db, vis, project_id)
                    if project.archived_at is not None or project.status == "archived":
                        raise HTTPException(status_code=409, detail=ARCHIVED_REFUSED)
                    await assert_may_write_guidance(db, user, project)
                # One save of a scope at a time: the lock lasts to the end of
                # this transaction. The unique index and the retry below stay
                # as the backstop (a lock does not cross a failover).
                await db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                    {"scope": f"agent_guidance:{current_tenant()}:{project_id or 'org'}"},
                )
                row = (await db.execute(
                    text(
                        "INSERT INTO pm_agent_guidance "
                        "(project_id, kind, version, body, written_by) "
                        "SELECT CAST(:pid AS uuid), 'guidance', "
                        " COALESCE((SELECT max(g.version) FROM pm_agent_guidance g "
                        "            WHERE g.kind = 'guidance' "
                        "              AND g.project_id IS NOT DISTINCT FROM CAST(:pid AS uuid)), 0) + 1, "
                        " :body, :me "
                        "RETURNING id, project_id, version, body, written_by, written_at"
                    ),
                    {"pid": project_id, "body": payload.body, "me": me},
                )).fetchone()
                return _version_out(row)
        except IntegrityError:
            # Two saves read the same newest version. The unique index kept
            # one. The next attempt reads the newest version again.
            _log.info("agent_guidance.save_retry")
            continue
    raise HTTPException(status_code=409, detail=SAVE_BUSY)


@router.get("/history")
async def guidance_history(
    project_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=HISTORY_MAX),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The versions of one scope, newest first. No ``project_id`` is the org."""
    pid = _uuid_or_404(project_id, "Project") if project_id else None
    async with _tenant_session() as db:
        if pid is not None:
            await load_visible_project(db, await resolve_visibility(db, user), pid)
        rows = (await db.execute(
            text(
                "SELECT id, project_id, version, body, written_by, written_at "
                "FROM pm_agent_guidance "
                "WHERE kind = 'guidance' "
                "  AND project_id IS NOT DISTINCT FROM CAST(:pid AS uuid) "
                "ORDER BY version DESC LIMIT :lim"
            ),
            {"pid": pid, "lim": limit},
        )).fetchall()
    return {"rows": [_version_out(r) for r in rows]}


@router.get("/tasks/{task_id}")
async def task_guidance(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The blocks that apply to a task, nearest first. 404 for a task the
    caller cannot open. ``dropped`` counts the farthest blocks that the cap of
    the answer cut."""
    tid = _uuid_or_404(task_id, "Task")
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, tid)
        blocks, dropped = await blocks_for_project(db, vis, str(task.project_id))
    return {"blocks": blocks, "dropped": dropped}


@router.get("/refs")
async def guidance_refs(
    project_ids: str = Query(default="", max_length=REFS_MAX * 37),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """For ``my_work``: ``{project id: {id, version} or null}``, the NEAREST
    block that applies to a project. No body leaves.

    *project_ids* is one comma list of at most :data:`REFS_MAX` ids. A signed
    query holds each key once, so the edge cannot repeat the key.

    A project that the caller cannot see gets null, the answer of an unknown
    id, so the refs cannot show which hidden projects hold guidance or how
    many versions they have. The ids go through :func:`_visible_ids`, the seam
    of the other routes. A task assigned to the member in a project that the
    member cannot see gets null here too. Its ``get_task_context`` still reads
    the blocks, through the task.
    """
    wanted = [p.strip() for p in project_ids.split(",") if p.strip()]
    if len(wanted) > REFS_MAX:
        raise HTTPException(status_code=422, detail=f"At most {REFS_MAX} projects.")
    ids = [_uuid_or_404(p, "Project") for p in dict.fromkeys(wanted)]
    refs: dict[str, Any] = {p: None for p in ids}
    if not ids:
        return {"refs": refs}
    async with _tenant_session() as db:
        visible = await _visible_ids(db, await resolve_visibility(db, user), ids)
        origins = [p for p in ids if p in visible]
        chains = await _chains(db, origins) if origins else {}
        every = sorted({str(r.id) for rows in chains.values() for r in rows})
        latest = await _latest(db, every) if every else {}
        for origin in origins:
            scopes: list[str | None] = [str(r.id) for r in chains.get(origin, [])]
            scopes.append(None)
            for scope in scopes:
                row = latest.get(scope)
                if _has_text(row):
                    refs[origin] = {"id": str(row.id), "version": int(row.version)}
                    break
    return {"refs": refs}
