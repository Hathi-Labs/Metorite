"""Projects · the references of a task (WS-57 TR-1).

Spec: ``project-docs/specs/task_references.md`` §5 and §6 TR-1.

    GET    /projects/tasks/{task_id}/refs            → {rows}
    POST   /projects/tasks/{task_id}/refs            {url, title?}
    DELETE /projects/tasks/{task_id}/refs/{ref_id}   → {deleted}

A reference is one URL on one task: a pull request, an issue, a commit, a
branch, a comment, a document or a plain link. ``refs_classify.classify``
decides which, and these routes are the only writer of ``pm_task_refs``
(migration 255). A person, the chat tools (``add_task_ref`` and
``remove_task_ref`` in ``skill_projects/writes.py``) and later an outside
agent (EA-6) all write through them.

Five rules, each one stated in the spec:

* **Access is the task's.** Each route loads the task through
  ``core.load_visible_task``, the check the POST and DELETE ``/links`` routes
  use. A task the caller cannot see answers 404, never 403.
* **An add is idempotent.** The unique key is ``(organization_id, task_id,
  kind, provider, ref)``. A second add of the same reference returns the first
  row with status 200 and writes nothing. A new row answers 201.
* **One timeline row for each change.** An add that creates a row and a remove
  that deletes one each write one ``link`` activity. A second add of the same
  reference changes nothing, so it writes no row.
* **The GitHub read runs outside the transaction.** The route reads the rows,
  closes the session, calls GitHub, then opens a new session to write the
  title and the state. Ten slow reads must not hold a connection.
* **The GitHub host is fixed.** The read calls ``api.github.com`` only, with
  the owner and the repository checked again against ``[A-Za-z0-9._-]``. It
  does not use ``outbound_guard``, which is for a URL a caller chooses. With
  no token, or on any failure, a row keeps what it has, and the route still
  answers. The token never leaves this process.

**Disclosure.** The read uses the organization's token, so each member who can
see the task sees the title and the state of a private repository the token
can reach. That is the reach the organization chose when it connected GitHub
(WS-54 IN-7). No member sees the token.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import Depends, HTTPException, Response
from gateway.routes.projects.core import (
    ACTOR_VIA,
    _tenant_session,
    actor,
    load_visible_task,
    record_activity,
    resolve_visibility,
    router,
    wire,
)
from gateway.routes.projects.refs_classify import NAME_RE, Classified, RefRefused, classify
from pydantic import BaseModel, Field
from sqlalchemy import text

_log = get_logger("gateway.projects.refs")

#: A read older than this is read again when the task's references are listed.
STALE_AFTER = timedelta(minutes=15)
#: At most this many GitHub reads for one call.
MAX_READS = 10
#: Seconds for one GitHub read, from the connect to the last byte.
READ_TIMEOUT = 3.0
#: The longest title a member may type. A provider title is cut to it too.
MAX_TITLE = 300
#: The kinds the GitHub read knows. A comment, a commit and a branch carry no
#: state the card shows (§6 TR-2), so the route does not read them.
READABLE_KINDS: frozenset[str] = frozenset({"pull_request", "issue"})

GITHUB_API = "https://api.github.com"

_COLUMNS = (
    "id, task_id, kind, provider, ref, url, title, state, fetched_at, via, "
    "created_by, created_at, updated_at, "
    # WS-54 GH-5 (migration 282): what the webhook and the poll write.
    "head_sha, head_ref, checks_state, checks_summary, checks_fetched_at, merged_at"
)

#: A pending checks state older than this is read again by the list of an
#: org on the GitHub App (WS-54 GH-5, §11).
CHECKS_STALE_AFTER = timedelta(minutes=5)


class RefIn(BaseModel):
    """``POST /projects/tasks/{id}/refs``. The tenant, the author and ``via``
    never come from this body (R11): they come from the session and the
    ``X-Actor-Via`` header."""

    url: str = Field(min_length=1, max_length=4096)
    title: str | None = Field(default=None, max_length=MAX_TITLE)


def _via() -> str:
    """Through what the member acted, from the bound ``X-Actor-Via`` header.

    ``chat:<agent>`` is ``chat`` and ``mcp:<client>`` stays as it is. Every
    other value, and no header, is ``ui``. ``text`` belongs to TR-5, which
    does not call this route: it calls :func:`insert_ref` itself.
    """
    value = ACTOR_VIA.get("")
    if value.startswith("mcp:") and len(value) > 4:
        return value
    if value.startswith("chat"):
        return "chat"
    return "ui"


def _clean_title(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join("".join(ch if ch.isprintable() else " " for ch in value).split())
    return cleaned[:MAX_TITLE] or None


def _label(row: Any) -> str:
    """The short name the card shows. Derived from the URL each time, so a
    change to the label rule needs no migration."""
    try:
        return classify(str(row.url)).label
    except RefRefused:
        return str(row.url)


def _row(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "task_id": str(row.task_id),
        "kind": row.kind,
        "provider": row.provider,
        "ref": row.ref,
        "url": row.url,
        "label": _label(row),
        "title": row.title,
        "state": row.state,
        "fetched_at": wire(row.fetched_at),
        "via": row.via,
        "created_by": row.created_by,
        "created_at": wire(row.created_at),
        "updated_at": wire(row.updated_at),
        # WS-54 GH-5: the head and the CI state. EA-13d draws the PR chip.
        "head_sha": getattr(row, "head_sha", None),
        "head_ref": getattr(row, "head_ref", None),
        "checks_state": getattr(row, "checks_state", None),
        "checks_summary": _summary(getattr(row, "checks_summary", None)),
        "checks_fetched_at": wire(getattr(row, "checks_fetched_at", None)),
        "merged_at": wire(getattr(row, "merged_at", None)),
    }


def _summary(value: Any) -> dict[str, int] | None:
    """The checks counts, as a plain object, or ``None``."""
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict):
        return None
    return {str(k): int(v) for k, v in value.items() if isinstance(v, int)}


def _parse_uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"{what} not found") from exc


# ── The GitHub read ─────────────────────────────────────────────────────────


def _needs_read(row: dict[str, Any], now: datetime) -> bool:
    if row["provider"] != "github" or row["kind"] not in READABLE_KINDS:
        return False
    fetched = row.get("fetched_at")
    if not fetched:
        return True
    try:
        stamp = datetime.fromisoformat(str(fetched))
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return now - stamp >= STALE_AFTER


def github_target(ref: str, kind: str) -> str | None:
    """The ``api.github.com`` path for one stored ref, or ``None``.

    Checked again here, and not only in the classifier: a row is data, and
    the path is built from it.
    """
    owner_repo, sep, number = ref.partition("#")
    owner, slash, repo = owner_repo.partition("/")
    if not (sep and slash and number.isdigit() and len(number) <= 10):
        return None
    if not all(NAME_RE.match(part) and part not in (".", "..") for part in (owner, repo)):
        return None
    collection = "pulls" if kind == "pull_request" else "issues"
    return f"/repos/{owner}/{repo}/{collection}/{int(number)}"


def github_state(kind: str, body: dict[str, Any]) -> str | None:
    """``open``, ``draft``, ``merged`` or ``closed`` from a GitHub answer."""
    state = str(body.get("state") or "").lower()
    if kind == "pull_request":
        if body.get("merged") or body.get("merged_at"):
            return "merged"
        if state == "open":
            return "draft" if body.get("draft") else "open"
        return "closed" if state == "closed" else None
    return state if state in ("open", "closed") else None


#: One read's result: ``(id, title, state)``, or ``(id, None)`` for an
#: attempt that failed. A failed attempt is stamped too (see
#: :func:`refresh_from_github`), and it keeps its title and state.
_Read = tuple[str, str | None, str | None]


async def _read_one(
    client: httpx.AsyncClient, token: str, row: dict[str, Any],
) -> _Read | tuple[str, None]:
    """``(id, title, state)`` for one row, or ``(id, None)`` on any failure."""
    failed = (row["id"], None)
    path = github_target(row["ref"], row["kind"])
    if path is None:
        return failed
    try:
        resp = await asyncio.wait_for(
            client.get(
                GITHUB_API + path,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            ),
            timeout=READ_TIMEOUT,
        )
    except Exception as exc:  # a timeout, a refused connection, anything
        _log.info("projects.refs.github_unreachable", error=type(exc).__name__)
        return failed
    if resp.status_code != 200:
        # A 404 (no access, or an issue number under /pull/), a 301 (a renamed
        # repository, and redirects are off), a 410, a 403 or 429 rate limit.
        _log.info("projects.refs.github_refused", status=resp.status_code)
        return failed
    try:
        body = resp.json()
    except ValueError:
        return failed
    if not isinstance(body, dict):
        return failed
    title = _clean_title(str(body.get("title") or "")) or None
    return row["id"], title, github_state(row["kind"], body)


async def _github_token() -> str | None:
    """The bound organization's GitHub token, or ``None``. Never raises."""
    from gateway.connections import github as github_connection
    from gateway.db import current_tenant

    try:
        org = current_tenant()
        return await github_connection.token_or_none(str(org)) if org else None
    except Exception as exc:
        _log.warning("projects.refs.token_unreadable", error=type(exc).__name__)
        return None


#: The marker of an App read that failed (a timeout, a 5xx, a rate limit).
_FAILED = object()


async def _app_installation() -> tuple[str, int] | None:
    """The bound org and its live App installation, or ``None``. Never raises.

    ``installation_of`` answers ``None`` while ``github_app_enabled`` is off,
    so the kill switch sends every read back to the token path (F5).
    """
    from gateway.connections import github as github_connection
    from gateway.db import current_tenant

    try:
        org = current_tenant()
        if not org:
            return None
        iid = github_connection.installation_of(await github_connection.connection(str(org)))
    except Exception as exc:
        _log.warning("projects.refs.connection_unreadable", error=type(exc).__name__)
        return None
    return (str(org), iid) if iid is not None else None


async def _read_pull_as_app(installation: int, row: dict[str, Any]) -> Any:
    """One pull request as the App: its body, ``None`` for a refusal, or
    :data:`_FAILED`."""
    from gateway import github_sync

    parsed = github_sync.split_ref(str(row["ref"]))
    if parsed is None:
        return _FAILED
    try:
        return await asyncio.wait_for(
            github_sync.read_pull_request(installation, parsed[0], parsed[1]),
            timeout=READ_TIMEOUT,
        )
    except Exception as exc:  # a timeout, a rate limit, a 5xx, no App
        _log.info("projects.refs.github_app_read_failed", error=type(exc).__name__)
        return _FAILED


async def _refresh_pulls_as_app(org: str, installation: int, pulls: list[dict[str, Any]]) -> None:
    """The lazy read of an App org's pull requests (fix round 1, item 1).

    It writes through ``github_sync.apply_pull_request``, the ONE write path of
    the webhook and the poll. So a read that finds ``merged`` runs
    merge-to-Done too: a ``closed`` delivery that GitHub dropped during a
    restart still closes the agent's task when a member opens it. The events
    go out after the commit. GitHub is called with no session open.
    """
    from gateway import github_sync

    results = await asyncio.gather(*(_read_pull_as_app(installation, r) for r in pulls))
    outcomes: list[Any] = []
    try:
        async with _tenant_session() as db:
            for row, got in zip(pulls, results, strict=True):
                ref = str(row["ref"])
                outcome = None
                if got is _FAILED:
                    # Stamped, as a failed TR-1 read is (see refresh_from_github).
                    await db.execute(text(
                        "UPDATE pm_task_refs SET fetched_at = now() "
                        " WHERE id = CAST(:rid AS uuid)"), {"rid": row["id"]})
                elif got is None:
                    outcome = await github_sync.record_refusal(db, org, ref)
                else:
                    written, outcome = await github_sync.apply_pull_request(db, org, ref, got)
                    if not written:
                        await github_sync.record_read(db, org, ref)
                if outcome is not None and outcome.events:
                    outcomes.append(outcome)
    except Exception as exc:
        _log.warning("projects.refs.save_failed", error=type(exc).__name__)
        return
    for outcome in outcomes:
        await outcome.emit_all()


async def _reload(task_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """*rows* again from the database, in their order. A row deleted in the
    meantime keeps what the caller had."""
    ids = [r["id"] for r in rows]
    try:
        async with _tenant_session() as db:
            found = {
                str(r.id): _row(r) for r in (await db.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM pm_task_refs "
                        " WHERE task_id = CAST(:tid AS uuid) "
                        "   AND id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"tid": task_id, "ids": ids},
                )).fetchall()
            }
    except Exception as exc:
        _log.warning("projects.refs.reload_failed", error=type(exc).__name__)
        return rows
    return [found.get(r["id"], r) for r in rows]


async def refresh_from_github(
    task_id: str, rows: list[dict[str, Any]], *, now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Read the title and the state of the rows that need it, and save them.

    Called with NO session open. It opens one only to write what it read.

    ⚠️ **Every ATTEMPT stamps ``fetched_at``, a failed one too.** A failed
    row keeps its title and its state, and it is not tried again for
    :data:`STALE_AFTER`. Two reasons:

    * a row that GitHub refuses (a 404, a 301, a 410, a 403 or 429 rate
      limit) would otherwise be read again by every list, every
      ``task_detail`` and every remove, and each would wait for it;
    * the due rows sort never-read first. An unstamped failure stays
      never-read, so ten of them would fill :data:`MAX_READS` on every call
      and no readable row would ever refresh.

    A timeout and a connection error are stamped as well, for the second
    reason: a stuck row must not hold a slot. The cost is that a GitHub
    outage leaves a state up to 15 minutes old, which is what a successful
    read costs too. With no token nothing is attempted and nothing is
    stamped, because nothing waited.
    """
    clock = now or datetime.now(UTC)
    due = sorted(
        (r for r in rows if _needs_read(r, clock)),
        key=lambda r: (r.get("fetched_at") is not None, str(r.get("fetched_at") or "")),
    )[:MAX_READS]
    if not due:
        return rows
    # An org on the GitHub App reads its pull requests through the one write
    # path of the webhook (fix round 1, item 1). Its issues keep TR-1 below.
    pulls = [r for r in due if r["kind"] == "pull_request"]
    app = await _app_installation() if pulls else None
    if app is not None:
        await _refresh_pulls_as_app(app[0], app[1], pulls)
        rows = await _reload(task_id, rows)
        due = [r for r in due if r["kind"] != "pull_request"]
        if not due:
            return rows
    token = await _github_token()
    if not token:
        return rows
    async with httpx.AsyncClient(timeout=READ_TIMEOUT, follow_redirects=False) as client:
        results = await asyncio.gather(*(_read_one(client, token, r) for r in due))
    try:
        async with _tenant_session() as db:
            for result in results:
                if len(result) == 3:
                    ref_id, title, state = result
                    await db.execute(
                        text(
                            "UPDATE pm_task_refs "
                            "   SET title = COALESCE(:title, title), state = :state, "
                            "       fetched_at = now(), updated_at = now() "
                            " WHERE id = CAST(:rid AS uuid) AND task_id = CAST(:tid AS uuid)"
                        ),
                        {"title": title, "state": state, "rid": ref_id, "tid": task_id},
                    )
                else:
                    await db.execute(
                        text(
                            "UPDATE pm_task_refs SET fetched_at = now() "
                            " WHERE id = CAST(:rid AS uuid) AND task_id = CAST(:tid AS uuid)"
                        ),
                        {"rid": result[0], "tid": task_id},
                    )
    except Exception as exc:
        _log.warning("projects.refs.save_failed", error=type(exc).__name__)
        return rows
    stamp = wire(clock)
    by_id = {result[0]: result for result in results}
    out: list[dict[str, Any]] = []
    for row in rows:
        got = by_id.get(row["id"])
        if got is not None and len(got) == 3:
            _, title, state = got
            row = {**row, "title": title or row.get("title"), "state": state, "fetched_at": stamp}
        elif got is not None:
            row = {**row, "fetched_at": stamp}
        out.append(row)
    return out


# ── The routes ──────────────────────────────────────────────────────────────


@router.get("/tasks/{task_id}/refs")
async def list_refs(
    task_id: str, refresh: bool = True, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """One task's references, oldest first. A GitHub pull request or issue
    whose last read is older than 15 minutes is read again.

    ``refresh=false`` returns the stored rows and calls nothing outside. The
    chat's reads (``task_detail``, ``remove_task_ref``) send it, so those
    tools stay ``open_world=False``. The card's list reads GitHub.
    """
    task_id = _parse_uuid(task_id, "Task")
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        rows = [
            _row(r) for r in (await db.execute(
                text(
                    f"SELECT {_COLUMNS} FROM pm_task_refs "
                    " WHERE task_id = CAST(:tid AS uuid) "
                    " ORDER BY created_at, id"
                ),
                {"tid": task_id},
            )).fetchall()
        ]
    if not refresh:
        return {"rows": rows}
    refreshed = await refresh_from_github(task_id, rows)
    await _refresh_checks(refreshed)
    return {"rows": refreshed}


#: The checks reads that a list started, held so the loop does not collect them.
_CHECK_READS: set[asyncio.Task[None]] = set()


def _checks_due(row: dict[str, Any], now: datetime) -> bool:
    if row.get("provider") != "github" or row.get("kind") != "pull_request":
        return False
    if not row.get("head_sha") or row.get("state") not in ("open", "draft"):
        return False
    if row.get("checks_state") not in (None, "pending"):
        return False
    stamp = row.get("checks_fetched_at")
    if not stamp:
        return True
    try:
        at = datetime.fromisoformat(str(stamp))
    except ValueError:
        return True
    return now - (at if at.tzinfo else at.replace(tzinfo=UTC)) >= CHECKS_STALE_AFTER


async def _refresh_checks(rows: list[dict[str, Any]], *, now: datetime | None = None) -> int:
    """WS-54 GH-5 (§11): an org on the GitHub App reads the checks too.

    A pull request with a head and a pending (or never read) CI state gets a
    checks read in the BACKGROUND, so the list answers at once and the next
    list shows the result. A ``token`` row keeps the lazy read of TR-1 only.
    Answers how many reads it started. Never raises.
    """
    clock = now or datetime.now(UTC)
    due = [r for r in rows if _checks_due(r, clock)][:MAX_READS]
    if not due:
        return 0
    from gateway import github_sync
    from gateway.connections import github as github_connection
    from gateway.db import current_tenant

    try:
        org = current_tenant()
        # `installation_of` is None for a token row, a dead App row, and while
        # `github_app_enabled` is off (the kill switch, F5).
        if not org or github_connection.installation_of(
                await github_connection.connection(org)) is None:
            return 0
    except Exception as exc:
        _log.warning("projects.refs.checks_unreadable", error=type(exc).__name__)
        return 0
    started = 0
    for row in due:
        parsed = github_sync.split_ref(str(row["ref"]))
        if parsed is None:
            continue
        task = asyncio.get_running_loop().create_task(
            github_sync.read_checks_joined(str(org), parsed[0], str(row["head_sha"])))
        _CHECK_READS.add(task)
        task.add_done_callback(_CHECK_READS.discard)
        started += 1
    return started


async def insert_ref(
    db: Any, task_id: str, found: Classified, *, title: str | None, via: str, by: str,
) -> tuple[dict[str, Any] | None, bool]:
    """Write one reference inside the caller's transaction: ``(row, made)``.

    The ONE insert of ``pm_task_refs``. ``POST …/refs`` calls it, and so do
    the links from text (TR-5, ``text_links.py``, with ``via = 'text'``). A
    new row writes one ``link`` activity. A row that already holds this
    reference is returned with ``made`` false and writes nothing. ``row`` is
    ``None`` only when that row was deleted between the two statements.

    The caller has checked that the task is visible. This calls no network:
    a new row has no ``fetched_at``, so the next list reads GitHub.
    """
    made = (await db.execute(
        text(
            "INSERT INTO pm_task_refs "
            "(task_id, kind, provider, ref, url, title, via, created_by) "
            "VALUES (CAST(:tid AS uuid), :kind, :provider, :ref, :url, :title, "
            "        :via, :who) "
            "ON CONFLICT (organization_id, task_id, kind, provider, ref) DO NOTHING "
            f"RETURNING {_COLUMNS}"
        ),
        {
            "tid": task_id, "kind": found.kind, "provider": found.provider,
            "ref": found.ref, "url": found.url, "title": title,
            "via": via, "who": by,
        },
    )).fetchone()
    if made is None:
        existing = (await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM pm_task_refs "
                " WHERE task_id = CAST(:tid AS uuid) AND kind = :kind "
                "   AND provider = :provider AND ref = :ref"
            ),
            {"tid": task_id, "kind": found.kind, "provider": found.provider,
             "ref": found.ref},
        )).fetchone()
        return (_row(existing) if existing is not None else None), False
    row = _row(made)
    await record_activity(
        db, activity_type="link", created_by=by, task_id=task_id,
        body=f"Added a reference: {found.label}",
        meta={
            "ref_action": "added", "ref_id": row["id"], "kind": found.kind,
            "provider": found.provider, "ref": found.ref, "url": found.url,
        },
    )
    return row, True


@router.post("/tasks/{task_id}/refs", status_code=201)
async def add_ref(
    task_id: str, payload: RefIn, response: Response, refresh: bool = True,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Attach one URL to a task. 201 with the new row, or 200 with the row
    that already holds this reference.

    ``refresh=false`` stores the row and calls nothing outside. The chat's
    ``add_task_ref`` sends it, so every Projects chat tool stays
    ``open_world=False`` and a no-egress run keeps it. ``fetched_at`` stays
    NULL, so the card's next GET reads the title and state."""
    task_id = _parse_uuid(task_id, "Task")
    try:
        found = classify(payload.url)
    except RefRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    title = _clean_title(payload.title)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        row, made = await insert_ref(
            db, task_id, found, title=title, via=_via(), by=actor(user),
        )
        if row is None:  # deleted between the two statements
            raise HTTPException(status_code=409, detail="Add the link again.")
        if not made:
            response.status_code = 200
    if not refresh:
        return row
    rows = await refresh_from_github(task_id, [row])
    return rows[0]


@router.delete("/tasks/{task_id}/refs/{ref_id}")
async def delete_ref(
    task_id: str, ref_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Remove one reference from a task. Writes one ``link`` activity."""
    task_id = _parse_uuid(task_id, "Task")
    rid = _parse_uuid(ref_id, "Reference")
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        gone = (await db.execute(
            text(
                "DELETE FROM pm_task_refs "
                " WHERE id = CAST(:rid AS uuid) AND task_id = CAST(:tid AS uuid) "
                f"RETURNING {_COLUMNS}"
            ),
            {"rid": rid, "tid": task_id},
        )).fetchone()
        if gone is None:
            raise HTTPException(status_code=404, detail="Reference not found")
        await record_activity(
            db, activity_type="link", created_by=actor(user), task_id=task_id,
            body=f"Removed a reference: {_label(gone)}",
            meta={
                "ref_action": "removed", "ref_id": rid, "kind": gone.kind,
                "provider": gone.provider, "ref": gone.ref, "url": gone.url,
            },
        )
    return {"deleted": rid}


__all__ = [
    "GITHUB_API",
    "MAX_READS",
    "READ_TIMEOUT",
    "STALE_AFTER",
    "RefIn",
    "github_state",
    "github_target",
    "insert_ref",
    "refresh_from_github",
]
