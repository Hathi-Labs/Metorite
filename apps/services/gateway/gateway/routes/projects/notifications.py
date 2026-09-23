"""Projects · notifications — somebody hears about it (WS-27j).

Spec: ``project-docs/specs/project_management_app.md`` §11.2 item 2.

    GET  /projects/notifications          → {rows, total, unread}
    POST /projects/notifications/read     → mark some, or all, read

Producers live at the write sites that cause them — ``set_assignees`` and
``add_comment`` call :func:`notify` — rather than in a listener over the event
bus. The bus is best-effort by construction (``core.emit`` swallows failures so
a broken workflow can never fail a task edit), and "you were assigned this" is
not something we may drop on the floor. Agent dispatch is on the bus because a
missed run is recoverable; a missed notification is invisible.

**Three rules, each of which is the whole point of a rule:**

1. **Never notify the actor.** A tool that pings you about your own click is a
   tool people mute, and a muted bell notifies nobody about anything.
2. **Never notify an agent.** Agents are handed work by the dispatch sink,
   which starts a run. A row addressed to ``agent:<name>`` would sit unread
   forever and inflate a badge nobody could clear.
3. **Never notify somebody who cannot open it.** The notification carries the
   task's title, so delivering one to a person outside the project's grant
   closure leaks that title — and lands them on a 404 if they click. The
   comment still posts; the response names who was skipped, because silently
   dropping a mention would let the author believe a colleague was pulled in.
"""

from __future__ import annotations

import re
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, Query
from gateway.routes.projects.core import (
    Page,
    _tenant_session,
    actor,
    insert_row,
    resolve_visibility,
    resolve_visibility_for,
    router,
    task_visibility_clause,
)

# `watchers` imports only from `core`, so this direction adds no cycle.
from gateway.routes.projects.watchers import project_chain_watchers
from pydantic import BaseModel
from sqlalchemy import text

#: The kinds a row may carry, mirrored from the CHECK on `pm_notifications.kind`
#: and pinned by `test_projects_notifications`. `assigned` is the one that
#: matters most — §11.2's complaint is literally "assignment is silent".
#:
#: ⚠️ **This tuple and the CHECK are ONE rule in two places.** A kind added here
#: alone raises `IntegrityError` on the insert, which reads as a 500 rather than
#: as a missing migration. The CHECK started in 152 and migration 214 widened it
#: for `nudge`. `test_projects_nudge.py` reads BOTH files and fails when they
#: disagree, so the pair cannot drift again.
#:
#: `nudge` is the in-app half of the follow-up (§9.12.9, WS-27bk wave 6): I am
#: waiting on you, and I am telling you so, once, on purpose. An OUTWARD nudge —
#: mail or WhatsApp — is Action-Broker work and owner-gated, and is not this.
NOTIFICATION_KINDS: tuple[str, ...] = ("assigned", "mention", "comment", "nudge")

#: How much of a comment a notification quotes. Long enough to know whether it
#: needs you, short enough that the bell is a list rather than a reading task.
EXCERPT_CHARS = 160

#: An @mention is an @ followed by an ADDRESS, not by a name.
#:
#: Names stopped being unique in migration 148 — deliberately, because two real
#: people share one. So `@Priya` has no answer, and picking one of the two
#: Priyas would notify the wrong person about work that is not theirs. The
#: address is the join key everywhere else in this app (`pm_task_assignees`,
#: the People Center's `lower(email)` index), and it is the join key here.
#:
#: The UI's picker inserts the address, so nobody types this by hand.
_MENTION = re.compile(r"@([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")


class NotificationModel(BaseModel):
    id: str
    recipient: str
    kind: str
    task_id: str
    activity_id: str | None = None
    actor: str
    excerpt: str | None = None
    created_at: Any = None
    read_at: Any = None
    # Joined for the list, so the bell can render without N round trips.
    task_title: str | None = None
    task_number: int | None = None
    project_id: str | None = None


class ReadIn(BaseModel):
    """Which rows to mark read.

    ``all`` rather than an unbounded id list for the common case: "clear the
    bell" is one action, and making the client enumerate every unread row turns
    it into a paginated loop that silently misses whatever arrived mid-scroll.
    """
    ids: list[str] = []
    all: bool = False


# ── Producing ───────────────────────────────────────────────────────────────

def mention_targets(body: str | None) -> list[str]:
    """Addresses @mentioned in a comment, lowercased, in first-seen order.

    Order is preserved and duplicates dropped so a comment that names somebody
    twice produces one notification, and so the *skipped* list a caller gets
    back reads in the order they wrote it.
    """
    seen: dict[str, None] = {}
    for match in _MENTION.finditer(body or ""):
        seen.setdefault(match.group(1).lower(), None)
    return list(seen)


def new_mentions(old_body: str | None, new_body: str | None) -> list[str]:
    """Only the addresses an edit ADDED, in first-seen order (WS-27v).

    Editing a comment or description must notify the *newly named* people and
    nobody twice: without this diff, fixing a typo in a comment that mentions
    three colleagues re-pings all three, and a bell that repeats itself is a
    bell people mute. Set-differenced on the folded address, the same identity
    every other mention read uses (R10).
    """
    before = set(mention_targets(old_body))
    return [who for who in mention_targets(new_body) if who not in before]


def excerpt_of(body: str | None) -> str | None:
    """A single-line snippet, ellipsised.

    Newlines collapse because the bell renders one line per row: a comment
    whose first line is blank would otherwise show as an empty notification.
    """
    text_ = " ".join((body or "").split())
    if not text_:
        return None
    if len(text_) <= EXCERPT_CHARS:
        return text_
    return text_[: EXCERPT_CHARS - 1].rstrip() + "…"


def notifiable(recipients: object, *, exclude: str) -> list[str]:
    """The people a notification may be addressed to, lowercased and sorted.

    Applies rules 1 and 2 — drop the actor, drop agents — and nothing else.
    Pure, because these are the two filters most likely to be quietly broken by
    a later edit and the cheapest to prove.
    """
    skip = (exclude or "").strip().lower()
    out = {
        clean
        for raw in (recipients or ())
        if (clean := (raw or "").strip().lower())
        and not clean.startswith("agent:")
        and clean != skip
    }
    return sorted(out)


async def deliverable(db: Any, recipients: list[str], task_id: str) -> list[str]:
    """Of these people, the ones who can actually open this task (rule 3).

    Uses ``task_visibility_clause`` — the SAME predicate ``list_tasks`` and
    ``load_visible_task`` use — and that is the whole point rather than a tidy
    detail. There are **two** ways to see a task: a grant on its project, or
    being an assignee of it. This function originally checked only the first,
    so anybody assigned work in a project they hold no grant on was judged
    undeliverable — they could open the task, and the assignment that put them
    on it notified nobody, while the response told the assigner they could not
    see it. That is precisely the silent assignment WS-27j exists to end, still
    open for the most common case in a grant-scoped app: delegating outward.

    One visibility resolution per recipient. That is N queries for N people,
    and it is the right trade: the alternative is a single clever join that
    re-implements the grant closure, and the closure already has exactly one
    implementation. Comments name two or three people, not two hundred.
    """
    allowed: list[str] = []
    for who in recipients:
        vis = await resolve_visibility_for(db, who)
        row = (await db.execute(
            text(
                "SELECT 1 FROM pm_tasks t "
                "WHERE t.id = CAST(:tid AS uuid) "
                f"AND {task_visibility_clause(vis)} LIMIT 1"
            ),
            {"tid": task_id, **vis.params},
        )).fetchone()
        if row is not None:
            allowed.append(who)
    return allowed


async def notify(
    db: Any,
    *,
    recipients: object,
    kind: str,
    task_id: str,
    actor_id: str,
    activity_id: str | None = None,
    excerpt: str | None = None,
) -> dict[str, list[str]]:
    """Write one notification per deliverable recipient.

    Returns ``{"notified": [...], "skipped": [...]}`` — skipped being people who
    were named but cannot see the task. The caller surfaces that rather than
    swallowing it: a mention that silently went nowhere leaves the author
    believing a colleague was pulled in.

    Does **not** commit. It is called inside the write it belongs to, so a task
    edit and the notifications it causes land together or not at all — an
    assignment that committed while its notification did not is the silent
    assignment this ticket exists to end.
    """
    if kind not in NOTIFICATION_KINDS:
        raise ValueError(f"Unknown notification kind '{kind}'.")
    wanted = notifiable(recipients, exclude=actor_id)
    if not wanted:
        return {"notified": [], "skipped": []}
    allowed = await deliverable(db, wanted, task_id)
    for who in allowed:
        await insert_row(db, "pm_notifications", {
            "recipient": who,
            "kind": kind,
            "task_id": task_id,
            "activity_id": activity_id,
            "actor": actor_id,
            "excerpt": excerpt,
        })
    return {
        "notified": allowed,
        "skipped": [w for w in wanted if w not in set(allowed)],
    }


async def task_audience(db: Any, task_id: str) -> list[str]:
    """Who an event on this task concerns: watchers ∪ assignees (WS-27v).

    The watcher table arrived exactly as WS-27j predicted, seeded from the
    audience it replaces (migration 165 subscribes every task's author), so the
    author keeps hearing without being a special case here. Assignees stay in
    the audience in their OWN right rather than through a watcher row: holding
    the work is the claim, and unwatching must not silence an assignment.

    This is the audience, not the delivery list — ``notify`` still drops the
    actor and agents (rules 1 and 2) and filters every recipient through
    ``resolve_visibility_for`` (rule 3). A watcher who has lost the project's
    grant keeps their row and hears nothing, which is the deliberate divergence
    from Plane's membership-only check.
    """
    rows = (await db.execute(
        text(
            "SELECT watcher AS who FROM pm_task_watchers "
            "WHERE task_id = CAST(:tid AS uuid) "
            "UNION "
            "SELECT assignee AS who FROM pm_task_assignees "
            "WHERE task_id = CAST(:tid AS uuid)"
        ),
        {"tid": task_id},
    )).fetchall()
    direct = [r.who for r in rows if getattr(r, "who", None)]

    # ── WS-27bk §9.12.2(b): PLUS the watchers of the task's PROJECT CHAIN ──
    #
    # Resolved HERE, per event, rather than expanded into task watcher rows
    # when somebody subscribes. That is the whole design of migration 203: a
    # task created after the subscription is covered by it, and one click
    # never writes thousands of rows.
    #
    # A personal task has no project, and `project_chain_watchers` answers
    # empty for that rather than raising — a notification for a personal task
    # must still be delivered.
    row = (await db.execute(
        text("SELECT project_id FROM pm_tasks WHERE id = CAST(:tid AS uuid)"),
        {"tid": task_id},
    )).fetchone()
    chain = await project_chain_watchers(db, getattr(row, "project_id", None))

    # Deduped, and the union is deliberately NOT filtered here: `notify` still
    # drops the actor and agents and runs every recipient through
    # `resolve_visibility_for`. A project watcher who cannot see the task hears
    # nothing, exactly like a task watcher who lost the grant.
    seen: dict[str, None] = {}
    for who in (*direct, *chain):
        if who:
            seen.setdefault(who, None)
    return list(seen)


# ── Reading ─────────────────────────────────────────────────────────────────

#: The list. Joined to `pm_tasks` so the bell renders in one round trip, and
#: filtered by the READER's visibility — the audience was fixed when the event
#: happened, but a person who has since lost the project stops seeing the row.
#: A notification is a pointer to a task, and a pointer that outlives the
#: permission is a title leak.
_LIST_SQL = """
SELECT n.*, t.title AS task_title, t.task_number AS task_number,
       t.project_id AS project_id
  FROM pm_notifications n
  JOIN pm_tasks t ON t.id = n.task_id
 WHERE n.recipient = :me
   AND {visible}
   {unread}
 ORDER BY n.created_at DESC
 LIMIT :limit OFFSET :offset
"""


@router.get("/notifications")
async def list_notifications(
    unread_only: bool = Query(False),
    page: Page = Depends(),
    user: UserContext = Depends(get_current_user),
) -> dict:
    """This caller's notifications, newest first, plus the badge count.

    There is no ``?recipient=``. The recipient is the authenticated caller and
    nothing else — a parameter would be an inbox you could read by guessing an
    address (R3).
    """
    me = actor(user).lower()
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        clause = vis.project_clause("t.root_project_id")
        sql = _LIST_SQL.format(
            visible=clause,
            unread="AND n.read_at IS NULL" if unread_only else "",
        )
        rows = (await db.execute(
            text(sql),
            {"me": me, "limit": page.limit, "offset": page.offset, **vis.params},
        )).fetchall()
        # Counted with the same visibility clause, so the badge can never
        # promise more than the list can show. WS-27v splits the count in the
        # SAME query rather than a second one: `mentions` is a subset of
        # `total` by construction here, so the two numbers can never disagree
        # about which rows they describe.
        unread = (await db.execute(
            text(
                "SELECT count(*) AS total, "
                "count(*) FILTER (WHERE n.kind = 'mention') AS mentions "
                "FROM pm_notifications n "
                "JOIN pm_tasks t ON t.id = n.task_id "
                f"WHERE n.recipient = :me AND n.read_at IS NULL AND {clause}"
            ),
            {"me": me, **vis.params},
        )).fetchone()
    return {
        "rows": [_row(r) for r in rows],
        "total": len(rows),
        # `{total, mentions}`, not a bare number: "you were named" is a
        # stronger claim on attention than "something you follow moved", and
        # the bell renders the two distinctly (P-20).
        "unread": {
            "total": int(getattr(unread, "total", 0) or 0),
            "mentions": int(getattr(unread, "mentions", 0) or 0),
        },
    }


@router.post("/notifications/read")
async def mark_read(
    payload: ReadIn, user: UserContext = Depends(get_current_user),
) -> dict:
    """Mark rows read. Idempotent, and scoped to the caller's own rows.

    ``read_at IS NULL`` in the WHERE is not an optimisation: without it,
    re-marking a row would move its timestamp, so "when did I first see this"
    would be rewritten by every page refresh that cleared the bell.

    An id that is not yours, or does not exist, is not an error — it is simply
    not updated. Answering 404 would turn this endpoint into an oracle for
    which notification ids exist.
    """
    me = actor(user).lower()
    if not payload.all and not payload.ids:
        return {"marked": 0}
    async with _tenant_session() as db:
        if payload.all:
            sql = (
                "UPDATE pm_notifications SET read_at = now() "
                "WHERE recipient = :me AND read_at IS NULL"
            )
            params: dict[str, Any] = {"me": me}
        else:
            sql = (
                "UPDATE pm_notifications SET read_at = now() "
                "WHERE recipient = :me AND read_at IS NULL "
                "AND id = ANY(CAST(:ids AS uuid[]))"
            )
            params = {"me": me, "ids": list(payload.ids)}
        result = await db.execute(text(sql), params)
    return {"marked": int(getattr(result, "rowcount", 0) or 0)}


def _row(row: Any) -> dict:
    out = {
        field: getattr(row, field, None)
        for field in NotificationModel.model_fields
    }
    for key in ("id", "task_id", "activity_id"):
        if out.get(key) is not None:
            out[key] = str(out[key])
    return out
