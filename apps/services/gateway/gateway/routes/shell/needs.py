"""GET /shell/needs — the one feed of "what needs me" (NS-3 slice A).

Spec: ``project-docs/specs/navigation_shell.md`` §7.2, §4.5 and ticket NS-3,
done-when 2 to 4. The bell and My Day's "Needs you" card read this one feed.

The pattern is ``search.py``'s, on the same router:

* **No new data path.** Each provider calls its app's OWN authorized
  function on this request's member. No provider writes SQL, opens a
  connection or builds a visibility predicate (§5.2 rules 6 and 7).
* **The app's feature gate, again, here.** Each app checks its feature on its
  ROUTER, so a direct call skips that check. Every provider checks
  ``feature:<app>`` first. A member without the app gets no row from it, and
  its source reads ``absent``. Fence: ``tests/unit/test_shell_needs.py``.
* **One app at a time, with a time limit.** The providers run one after
  another (the pool budget is small). A provider that fails, refuses or runs
  out of time is left out, and its source reads ``failed``. It never fails
  the feed.

The four providers, and the function each one calls:

=========  ====================  ==========================================
Source     Feature               Function
=========  ====================  ==========================================
tasks      ``feature:projects``  ``projects.personal.my_due_tasks``: one
                                 bounded read of the lens, due today and
                                 overdue, the oldest deadline first.
approvals  ``feature:approvals`` ``action_broker.read_pending``: the queue
           and an org admin      of the bound tenant, the longest wait
                                 first, 50 rows at most (NS-3 slice C).
projects   ``feature:projects``  ``projects.notifications.list_notifications``,
                                 unread only.
email      ``feature:email``     ``email.transport.accounts.feed_mailboxes``,
                                 then ``email.digest.needs_reply_threads``
                                 for each mailbox, the longest wait first,
                                 and ``needs_reply_count`` for a mailbox
                                 whose list reached the cap.
=========  ====================  ==========================================

⚠️ **Only an org admin gets approval rows** (owner decision, 2026-10-10).
The router of ``routes/actions.py`` demands ``feature:approvals``, and that
gate alone is too wide: migration 207 grants ``feature:*`` to ``member`` and
``manager`` in every org. So the provider ALSO demands ``ADMIN_PERMISSION``,
the admin test of ``routes/shell/intent.py``. ``GET /auth/me`` reports the
same test as ``is_admin``. Any other member keeps the Approvals app if they
hold it, and their feed holds only their own work. ``pending_actions`` has
no approver column. A finer Approver role is a later option.

``GET /actions/pending`` also demands ``require_internal_auth``. That check is
the transport: the BFF sends the internal token on every call, and on this
call too. The broker binds the tenant itself (H-201).

⚠️ **The feed reads ``read_pending``, not ``list_pending``.** ``list_pending``
answers ``[]`` when the database fails, so the source would read a false
``ok``. ``read_pending`` raises, so the source reads ``failed``. It is also
bounded: 50 rows, the oldest first.

⚠️ **Approvals never push the member's own work out of the feed.** An
approval takes only the room that the other rows leave inside ``limit``
(``YIELDING_KINDS``). The approvals provider also runs LAST, so a slow sync
pool cannot spend the time budget of the member's own sources.

⚠️ **An approval row has no act.** Approving runs an outward write: a mail, a
CRM push, a broadcast. The member reads the proposal in Approvals first, so
My Day never approves in one click.

⚠️ **My Tasks needs ``feature:projects``, not ``feature:tasks``.** The lens
routes live on the Projects router, and that router demands ``projects``.
The ``tasks`` feature only shows the pane.

⚠️ **A Someday or Reference task is left out, even with a due date.** The
lens read leaves it out (``personal.ACTIONABLE_CLAUSE``), and the provider
checks the row's ``disposition`` again (``HIDDEN_DISPOSITIONS``). A WAITING
task stays.

⚠️ **A separate mailbox is left out** (D-EM-30). The feed reads more than one
mailbox, and a mailbox that the member keeps separate leaves every such read.

⚠️ **The email rows are the digest's live threads.** ``needs_reply_threads``
reads the rows that the email app's Needs-reply count counts
(``digest._LIVE_THREAD``). So a thread whose last message is in trash, junk
or the archive, or is snoozed, stays out. Snooze is the member's "not now",
and archive is "dealt with", as in Reply Zero. The read starts no backfill.

⚠️ **One mailbox that fails costs only its own rows.** Each mailbox has its
own time limit. The source reads ``failed`` only when every mailbox failed.

**Where each row opens** (``href``), and the app code it copies:

* a task: ``/projects?task=<task id>``, the deep link that Projects reads
  (``app/projects/lib/card.ts`` ``taskDeepLink``). The command bar's Find
  tier and ``app/tasks/lib/searchHit.ts`` link a task the same way.
* a notification: ``/projects?task=<task id>``, the bell's own link
  (``app/projects/lib/notifications.ts`` ``linkTo``).
* an email: ``/email?email=<message id>&account=<account id>``
  (``app/email/lib/emailLink.ts`` ``emailLink``).
* an approval: ``/approvals``. The row opens the queue, because nothing
  in the bell changes (``my_day_feed.md`` §4.4). The feed's approval card
  opens the one proposal, ``/approvals?action=<id>`` (``approval_href``,
  MD-3a).

**The one-click acts** (``act``), each through the OWNING app's route:

* ``done``: ``POST /projects/tasks/{act_ref}/complete``. My Tasks completes a
  task through it (``app/tasks/lib/lens.ts``).
* ``read``: ``POST /projects/notifications/read`` with
  ``{"ids": [act_ref]}``. The Projects bell marks a row read through it
  (``app/projects/lib/api.ts`` ``notificationsApi.markRead``).

An email row has no act. A reply needs the app. An approval row has no act
either, for the reason above.

The answer is plain words: a row says what it is, where it opens, and
nothing else. An id travels only inside ``id``, ``href`` and ``act_ref``.

**One registry, two views** (``my_day_feed.md`` §4.4, WS-58 MD-1a).
``REGISTRY`` holds every provider once. Each entry names its source, its
features, the admin test, the kinds it gives, and whether it serves the
needs view. ``PROVIDERS`` is the needs subset, in the order above, and
``GET /shell/needs`` runs only that subset. ``GET /shell/feed``
(``feed.py``) runs the same subset first, on the same budget, and then the
feed-only providers. A provider gives its needs rows AND a thunk for its
feed signals. The needs route never calls the thunk, so the needs answer
runs no feed code. Fence: ``tests/unit/test_shell_feed.py::TestOneRegistry``.

**Handled** (``my_day_feed.md`` MD-2c). The tasks, email and approvals
entries also carry a ``handled`` read: what the app closed for the member
since an instant, through a new function in the app that owns the table.
Only the feed calls it (``handled.py``). ``closed_by`` is the ``by`` table.

**The seam of the new sources** (``my_day_feed.md`` §5.8 items 1, 3, 4 and
6, WS-58 MD-3b). Four rules, each for the feed-only providers that MD-3 adds:

* A provider with ``gated=True`` runs only when its source id is in
  ``MY_DAY_PROVIDERS``. :func:`listed_sources` is the one reader, and
  :func:`feed_only_providers` applies it. A source that is not listed has no
  entry in ``sources``. So with the list empty, the feed answer is the same
  as before MD-3b. The Calendar provider is not gated.
* The feed-only providers run two at a time at most (``run_providers`` with
  ``slots``), so a feed read holds two pool connections at most for them.
  Each one's time limit starts when it takes its slot, so a slow one fails
  alone. A ceiling from the start fails each provider that is still queued
  or running. The needs providers still run one after another.
* ``source_app`` names the app of each source, for ``source_apps`` in the
  feed answer. The client maps a failed or capped source to its app.
* A provider raises :class:`SourceAbsent` for a refusal that is not a
  failure: CRM off, CRM access refused, no People row. Its source reads
  ``absent``. The read cache keeps such an answer, and keeps no answer with a
  ``failed`` source.
* ``Provider.permission`` names one more grant, such as
  ``admin:members:read``. A member without it gets the source ``absent``, and
  the provider never runs.

Fences: ``tests/unit/test_shell_feed.py`` (``TestTheAnswerBeforeMD3b``,
``TestTheProviderList``, ``TestTheFeedOnlyGather``, ``TestSourceAbsent`` and
``TestThePermissionGate``).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote, urlencode

from acb_auth import UserContext, get_current_user
from acb_common.priority import task_cell
from fastapi import Depends, HTTPException
from gateway.routes.projects.personal import NOT_NOW_DISPOSITIONS
from gateway.routes.shell.intent import ADMIN_PERMISSION
from gateway.routes.shell.search import router

logger = logging.getLogger(__name__)

#: Rows per source, so one app cannot fill the feed.
PER_APP = 15
#: The feed's default length, and its ceiling.
DEFAULT_LIMIT = 30
MAX_LIMIT = 50
#: Seconds one provider may take. The email provider makes one read per
#: mailbox, so this is wider than search's.
PROVIDER_TIMEOUT_S = 2.0
#: Seconds one mailbox may take inside the email provider.
MAILBOX_TIMEOUT_S = 1.0
#: Seconds for the whole answer, all providers together. Each provider gets
#: what is left, the rule of ``search.TOTAL_BUDGET_S``.
TOTAL_BUDGET_S = 4.0
#: The lens dispositions that never reach the feed, even with a due date:
#: SOMEDAY ("not now") and REFERENCE (information, not an action). WAITING
#: stays, because an overdue waiting-for is a cue to chase someone.
#: ⚠️ The Projects lens OWNS this set (``personal.NOT_NOW_DISPOSITIONS``).
#: ``my_due_tasks`` leaves these out in its SQL, and the provider checks the
#: effective ``disposition`` of each row again, so a change to the lens can
#: only narrow the feed.
HIDDEN_DISPOSITIONS = frozenset(NOT_NOW_DISPOSITIONS)

#: The order of the feed, by kind (§7.2 contract). An approval comes right
#: after an overdue task, because an agent's work waits on it.
KIND_ORDER = {"overdue": 0, "approval": 1, "due_today": 2, "notification": 3,
              "needs_reply": 4}
#: The kinds that take only the room the member's own rows leave inside the
#: limit. An admin's approvals must never push out their own work.
YIELDING_KINDS = frozenset({"approval"})
#: Within a kind: True is newest first, False is oldest first.
NEWEST_FIRST = {"overdue": False, "approval": False, "due_today": False,
                "notification": True, "needs_reply": False}

Item = dict[str, Any]
Act = dict[str, Any]


@dataclass(frozen=True)
class Signal:
    """One fact about one entity, for the feed (``my_day_feed.md`` §4.1).

    ``signal_id`` is the identity of the ENTITY, never of the kind:
    ``tasks:<task id>``, ``email:<account id>:<thread id>``,
    ``approvals:<id>`` or ``calendar:<local date>``. So two kinds about one
    task share one id, and the feed folds them into one card (§4.5).
    ``facts`` holds typed values only. The feed's templates and its rank
    read nothing else. Each time is an aware ISO text, or None.
    """

    signal_id: str
    app: str
    kind: str
    entity: dict[str, Any]
    title: str
    facts: dict[str, Any]
    href: str
    acts: tuple[Act, ...]
    at: str | None = None
    urgent_at: str | None = None
    expires_at: str | None = None
    #: The id of the source that gave the signal (MD-3c, ``my_day_feed.md``
    #: §5.9 item 1). The feed tags each signal after its provider ran, so a
    #: provider leaves it None. The card copies the source of its main
    #: signal, for the carry of a failed source.
    source: str | None = None


def tap(act_id: str, label: str, act: str, act_ref: str,
        act_refs: list[str] | None = None) -> Act:
    """An act that runs in the card (§11.1). In MD-1 only ``done`` and
    ``read`` are taps, and each runs through its owning app's route.
    ``act_refs`` names every id the act covers, ``act_ref`` first: a task's
    ``read`` marks each of its unread notifications read (§14.1a)."""
    out: Act = {"id": act_id, "label": label, "class": "A", "way": "tap",
                "act": act, "act_ref": act_ref}
    if act_refs is not None:
        out["act_refs"] = list(act_refs)
    return out


def link(act_id: str, label: str, href: str, cls: str = "A") -> Act:
    """An act that opens the owning app (§11.1, way ``open``). Every A* act
    is a link until MD-8."""
    return {"id": act_id, "label": label, "class": cls, "way": "open", "href": href}


@dataclass(frozen=True)
class Found:
    """What one provider found: its rows, and how many need the member.

    ``total`` is the true count when the app's read already knows it (the
    Projects bell's unread count, a mailbox's Needs-reply count). Else it is
    the rows found. ``capped`` says that ``total`` is a floor: the read
    stopped at ``PER_APP``, so the source holds ``total`` OR MORE. The
    sidebar then prints "15+" (``navigation_shell.md`` §3.2c).

    ``signals`` builds the feed signals from the same rows. Only the feed
    calls it, so a needs answer runs no feed code. ``zone`` is the member's
    zone when the app's read reports it.

    ``next_edge`` is the next instant at which the clock changes what the
    provider gives, beyond the times its signals carry: an aware ISO text, or
    None. The Calendar provider sets it to the next block start of today
    minus 30 minutes, for a block outside the window of ``block_soon``. The
    feed's read cache reads it (``my_day_feed.md`` §8.4 item 2, MD-5a).
    """

    rows: list[Item]
    total: int
    capped: bool
    signals: Callable[[], list[Signal]] | None = None
    zone: str | None = None
    next_edge: str | None = None


class SourceAbsent(Exception):
    """A provider's refusal that is NOT a failure (``my_day_feed.md`` §5.8
    item 4): CRM off, CRM access refused, or no People row. The run reads
    the source as ``absent``, as for a member who does not hold the app. So
    the read cache keeps the answer, which it never does for ``failed``."""


def _now() -> datetime:
    """The one clock of the feed: it splits an overdue task from one due today.

    A test freezes THIS, so the rows it builds and the split agree
    (``test_shell_needs.py`` ``TestTheClock``). It is an instant, so it has
    no zone. The member's zone sets only the read's bound, in ``my_due_tasks``.
    """
    return datetime.now(UTC)


def _instant(value: Any) -> datetime | None:
    """A stored time as an aware datetime, or None. A naive one is UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        at = value
    else:
        try:
            at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)


def _iso(value: Any) -> str | None:
    at = _instant(value)
    return at.isoformat() if at is not None else None


def _item(*, id: str, app: str, kind: str, title: Any, detail: Any, href: str,
          at: Any, act: str | None = None, act_ref: str | None = None) -> Item:
    return {
        "id": id, "app": app, "kind": kind,
        "title": str(title or "").strip() or "(untitled)",
        "detail": (str(detail).strip() or None) if detail else None,
        "href": href, "at": _iso(at), "act": act, "act_ref": act_ref,
    }


# ── My Tasks: due today and overdue, through the lens ───────────────────────


async def _tasks(user: UserContext) -> Found:
    from gateway.routes.projects.personal import my_due_tasks

    # One bounded read: due before the member's tomorrow, in the member's
    # zone, the oldest deadline first, at most PER_APP rows.
    answer = await my_due_tasks(user, limit=PER_APP)
    now = _now()
    found = answer.get("rows") or []

    overdue: list[Item] = []
    due_today: list[Item] = []
    kept: list[tuple[str, dict[str, Any]]] = []
    for task in found:
        due = _instant(task.get("due_at"))
        if due is None or task.get("completed_at"):
            continue
        if task.get("disposition") in HIDDEN_DISPOSITIONS:
            continue  # Someday is "not now", see HIDDEN_DISPOSITIONS
        if due < now:
            kind, bucket = "overdue", overdue
        else:
            # The read holds nothing due after the member's today.
            kind, bucket = "due_today", due_today
        task_id = str(task["id"])
        kept.append((kind, task))
        bucket.append(_item(
            id=f"tasks:{task_id}", app="tasks", kind=kind,
            title=task.get("title"), detail=task.get("project_name"),
            href=f"/projects?task={quote(task_id)}", at=due,
            act="done", act_ref=task_id,
        ))
    rows = (_sort(overdue) + _sort(due_today))[:PER_APP]
    zone = answer.get("timezone")

    # "Block time" opens the Calendar pane, which needs `feature:tasks`.
    # The lens read needs only `feature:projects`, so check it here.
    calendar = user.has_permission("feature:tasks")

    def signals() -> list[Signal]:
        return _task_signals(kept, zone, answer.get("today"), calendar=calendar)

    # The lens read stops at PER_APP and runs no count, so a full read is a
    # floor: the member has this many due tasks or more.
    return Found(rows, len(rows), capped=len(found) >= PER_APP,
                 signals=signals, zone=zone)


def task_href(task_id: str) -> str:
    """The task deep link (``app/projects/lib/card.ts`` ``taskDeepLink``)."""
    return f"/projects?task={quote(task_id)}"


def day_href(day: date, at: datetime | None = None) -> str:
    """The Calendar's link to one day, and to a time on it (MD-3a, §5.8 item
    9): ``/calendar?date=YYYY-MM-DD&at=HH:MM``. ``at`` is a local time, so
    the caller converts it to the member's zone first. The page reads the two
    names back in ``app/calendar/lib/calendarLink.ts``."""
    href = f"/calendar?date={day.isoformat()}"
    return href if at is None else f"{href}&at={at.hour:02d}:{at.minute:02d}"


def approval_href(action_id: str) -> str:
    """The link to one proposal in the queue (MD-3a, ``my_day_feed.md`` §5.8
    item 9). The page reads the name back in ``app/approvals/lib/
    approvalLink.ts``. Only the feed's signal uses it: the needs row keeps
    ``/approvals``."""
    return f"/approvals?action={quote(action_id, safe='')}"


def task_entity(task_id: str, title: Any) -> dict[str, Any]:
    return {"type": "task", "id": task_id, "label": str(title or "").strip() or "(untitled)"}


def _task_signals(kept: list[tuple[str, dict[str, Any]]], zone: Any,
                  today: Any, *, calendar: bool) -> list[Signal]:
    """The feed signals of the due rows: one for each task, keyed on it.

    ``calendar`` says that the member holds the Calendar pane
    (``feature:tasks``). Without it, no act links to the Calendar. With it,
    "Block time" opens the member's today (``day_href``).

    ``days_late`` counts the member's own dates, in the zone the lens read
    reports. ``priority`` is the D78 level, from the one server matrix
    (``acb_common.priority.task_cell``), and the old-path weight reads it.
    """
    # ``state`` imports this module, so the import waits for the call.
    from gateway.routes.shell.state import zone_of

    tz = zone_of(zone)  # an unknown zone reads as UTC, as the lens does
    try:
        local_today = date.fromisoformat(str(today))
    except ValueError:
        local_today = _now().astimezone(tz).date()
    out: list[Signal] = []
    for kind, task in kept[:PER_APP]:
        task_id = str(task["id"])
        due = _instant(task.get("due_at"))
        facts: dict[str, Any] = {
            "due_at": _iso(due),
            "priority": task_cell(task),
            "project": task.get("project_name"),
        }
        acts: list[Act] = [tap("done", "Done", "done", task_id)]
        if kind == "overdue":
            late = (local_today - due.astimezone(tz).date()).days if due else 0
            facts["days_late"] = max(0, late)
            acts.append(link("move-tomorrow", "Move to tomorrow", task_href(task_id)))
        elif calendar:
            # "Find time today": the Calendar's day view on the member's
            # today (MD-3a review), never a bare `/calendar`.
            acts.append(link("block-time", "Block time", day_href(local_today)))
        out.append(Signal(
            signal_id=f"tasks:{task_id}", app="tasks", kind=kind,
            entity=task_entity(task_id, task.get("title")),
            title=task_entity(task_id, task.get("title"))["label"],
            facts=facts, href=task_href(task_id), acts=tuple(acts),
            at=_iso(due), urgent_at=_iso(due),
        ))
    return out


async def _tasks_handled(user: UserContext, since: datetime) -> list[Closed]:
    """My Tasks' Handled read: the lens tasks closed since ``since``, each
    with who closed it (``personal.my_completed_since``)."""
    from gateway.routes.projects.personal import my_completed_since

    answer = await my_completed_since(user, since, limit=PER_APP)
    out: list[Closed] = []
    for row in answer.get("rows") or []:
        task_id = str(row["id"])
        out.append(Closed(
            key=f"tasks:{task_id}", app="tasks",
            title=task_entity(task_id, row.get("title"))["label"],
            href=task_href(task_id), by=closed_by(row.get("closed_by"), user.email or ""),
            at=_iso(row.get("completed_at")),
        ))
    return out


# ── Projects: unread notifications, through the bell's read ─────────────────

#: The bell's verbs (`app/projects/lib/notifications.ts` `VERB`).
_VERB = {"assigned": "assigned you", "mention": "mentioned you on",
         "comment": "commented on", "nudge": "nudged you about"}


def _who(actor: Any) -> str:
    """The bell's ``actorLabel``: an address's local part, an agent's name."""
    raw = str(actor or "").strip()
    if not raw:
        return "Somebody"
    if raw.startswith("agent:"):
        return raw[len("agent:"):] or raw
    return raw.split("@")[0] or raw


def closed_by(actor: Any, member: str) -> dict[str, str | None]:
    """The ``by`` of a Handled entry (``my_day_feed.md`` MD-2c, the ``by``
    table), from ``created_by`` or ``reviewed_by``.

    * The member's own address is ``you``.
    * Another address is ``person``, named by its local part, as ``_who``
      names it.
    * ``agent:<name>`` is ``agent``, named by the agent's name.
    * Every other value is ``system``: ``system``, ``system:sync``,
      ``system:workflow:<id>``, ``operator``, or nothing.

    An email rule has no actor value. Its read writes ``rules`` itself. The
    client writes the words ("You", "Ravi", "Metorite").
    """
    raw = str(actor or "").strip()
    if raw.startswith("agent:") and raw[len("agent:"):]:
        return {"kind": "agent", "name": raw[len("agent:"):]}
    if "@" in raw:
        if raw.lower() == str(member or "").strip().lower():
            return {"kind": "you", "name": None}
        return {"kind": "person", "name": _who(raw)}
    return {"kind": "system", "name": None}


#: The ``by`` of a row that an email rule closed (the ``by`` table).
BY_RULES: dict[str, str | None] = {"kind": "rules", "name": None}


@dataclass(frozen=True)
class Closed:
    """One thing that an app closed for the member (``my_day_feed.md``
    MD-2c). ``key`` is the entity, so the Handled group keeps one entry for
    each. :meth:`entry` is the shape of the answer, and ``key`` is not in it."""

    key: str
    app: str
    title: str
    href: str
    by: dict[str, str | None]
    at: str | None

    def entry(self) -> dict[str, Any]:
        return {"app": self.app, "title": self.title, "href": self.href,
                "by": dict(self.by), "at": self.at}


def _sentence(row: dict[str, Any]) -> str:
    title = str(row.get("task_title") or "").strip() or "a task"
    task = f"#{row['task_number']} {title}" if row.get("task_number") else title
    verb = _VERB.get(str(row.get("kind") or ""), "updated")
    return f"{_who(row.get('actor'))} {verb} {task}"


async def _projects(user: UserContext) -> Found:
    from gateway.routes.projects.core import Page
    from gateway.routes.projects.notifications import list_notifications

    answer = await list_notifications(
        unread_only=True, page=Page(page=1, page_size=PER_APP), user=user,
    )
    out = []
    for row in (answer.get("rows") or [])[:PER_APP]:
        note_id, task_id = str(row["id"]), str(row["task_id"])
        out.append(_item(
            id=f"projects:{note_id}", app="projects", kind="notification",
            title=_sentence(row), detail=row.get("excerpt"),
            href=f"/projects?task={quote(task_id)}", at=row.get("created_at"),
            act="read", act_ref=note_id,
        ))
    rows = _sort(out)[:PER_APP]
    notes = list((answer.get("rows") or [])[:PER_APP])

    def signals() -> list[Signal]:
        return _note_signals(notes)

    # The bell's read counts the unread rows already, with the same
    # visibility clause, so the true number costs no query of our own.
    unread = (answer.get("unread") or {}).get("total")
    if isinstance(unread, int) and not isinstance(unread, bool):
        return Found(rows, max(unread, len(rows)), capped=False, signals=signals)
    return Found(rows, len(rows), capped=len(answer.get("rows") or []) >= PER_APP,
                 signals=signals)


#: The notification kinds that ask the member for something. One of them
#: sets the card's reason over a ``comment`` (§4.3, §14.1a).
ASKING_NOTIFICATIONS = frozenset({"assigned", "mention", "nudge"})


def _note_signals(notes: list[dict[str, Any]]) -> list[Signal]:
    """The feed signals of the unread notifications: ONE for each task.

    ⚠️ **In the feed a notification keys on its TASK**, ``tasks:<task id>``,
    so it folds into the task's one card (``my_day_feed.md`` §4.1). The
    needs view keeps one row for each, ``projects:<notification id>``.

    ⚠️ **A task's unread notifications are one signal, with one ``read``
    act** (§14.1a). ``act_refs`` lists every unread id on the task, the
    newest first, and ``act_ref`` is the first of them. The reason is the
    newest ASKING notification (assigned, mention, nudge) when there is one,
    else the newest one. So a later comment never hides an earlier mention,
    and the mention never stays unread behind it (review round 1, P1).
    """
    def stamp(row: dict[str, Any]) -> datetime:
        return _instant(row.get("created_at")) or datetime.min.replace(tzinfo=UTC)

    by_task: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(notes, key=stamp, reverse=True):
        by_task.setdefault(str(row["task_id"]), []).append(row)
    out: list[Signal] = []
    for task_id, rows in by_task.items():
        ids = [str(r["id"]) for r in rows]
        asking = [r for r in rows if str(r.get("kind") or "") in ASKING_NOTIFICATIONS]
        reason = (asking or rows)[0]
        entity = task_entity(task_id, reason.get("task_title"))
        out.append(Signal(
            signal_id=f"tasks:{task_id}", app="projects", kind="notification",
            entity=entity, title=entity["label"],
            facts={"notification": str(reason.get("kind") or ""),
                   "who": _who(reason.get("actor")), "excerpt": reason.get("excerpt"),
                   "at": _iso(reason.get("created_at")), "note_ids": ids,
                   "unread": len(ids),
                   # The newest note, which may not be the reason. The state
                   # join reads it to tell another person's note from the
                   # member's own act (my_day_feed.md §6.8, MD-2a).
                   "last_note_at": _iso(rows[0].get("created_at"))},
            href=task_href(task_id),
            acts=(tap("read", "Mark read", "read", ids[0], act_refs=ids),
                  link("open", "Open", task_href(task_id))),
            at=_iso(rows[0].get("created_at")),
        ))
    return out


# ── Email: needs reply, for each mailbox the member owns ────────────────────


def _email_href(message_id: str, account_id: str) -> str:
    """``emailLink`` in ``app/email/lib/emailLink.ts``, the same two names."""
    return "/email?" + urlencode({"email": message_id.lower(), "account": account_id.lower()})


def _in_all_inboxes(row: dict[str, Any]) -> bool:
    """Whether the feed reads this mailbox. A mailbox that the member keeps
    separate leaves every read of more than one mailbox (D-EM-30). The needs
    rows and the Handled rows apply this one rule."""
    return bool(row.get("in_all_inboxes", True))


async def _email(user: UserContext) -> Found:
    from gateway.routes.email.digest import needs_reply_count, needs_reply_threads
    from gateway.routes.email.transport.accounts import feed_mailboxes

    # The light read: the ids and the pool flag, and no unread count.
    accounts = await feed_mailboxes(user)
    out: list[Item] = []
    #: The last message of each thread, by row id: the state fingerprint
    #: reads it (``my_day_feed.md`` §6.8 item 1).
    last_message: dict[str, str] = {}
    asked = failed = 0
    total = 0
    capped = False
    for row in accounts:
        if not _in_all_inboxes(row):
            continue  # D-EM-30, see the module note
        account_id = str(row["id"])
        asked += 1
        try:
            threads = await asyncio.wait_for(
                needs_reply_threads(user, account_id, PER_APP), MAILBOX_TIMEOUT_S,
            )
        except Exception as exc:
            # This mailbox only. The type only, for the reason in shell_needs.
            failed += 1
            logger.warning(
                "shell.needs mailbox failed",
                extra={"provider": "email", "error": type(exc).__name__},
            )
            continue
        shown = [t for t in threads if t.get("message_id")]
        if len(threads) >= PER_APP:
            # The list reached the cap. The digest's own count says the real
            # number, through one indexed COUNT on this mailbox.
            try:
                total += max(len(shown), await asyncio.wait_for(
                    needs_reply_count(user, account_id), MAILBOX_TIMEOUT_S,
                ))
            except Exception as exc:
                capped = True
                total += len(shown)
                logger.warning(
                    "shell.needs mailbox count failed",
                    extra={"provider": "email", "error": type(exc).__name__},
                )
        else:
            total += len(shown)
        for thread in threads:
            if not thread.get("message_id"):
                continue  # nothing to open
            who = thread.get("who") or ""
            last_message[f"email:{account_id}:{thread['thread_id']}"] = str(
                thread["message_id"])
            out.append(_item(
                id=f"email:{account_id}:{thread['thread_id']}", app="email",
                kind="needs_reply", title=thread.get("subject") or "(no subject)",
                detail=f"From {who}" if who else None,
                href=_email_href(str(thread["message_id"]), account_id),
                at=thread.get("last_message_at"),
            ))
    if asked and failed == asked:
        raise RuntimeError("every mailbox failed")
    rows = _sort(out)[:PER_APP]

    def signals() -> list[Signal]:
        return [_thread_signal(row, last_message.get(row["id"])) for row in rows]

    # A mailbox that failed costs its rows, so the total can only be a floor.
    return Found(rows, max(total, len(rows)), capped=capped or failed > 0,
                 signals=signals)


def _thread_signal(row: Item, message_id: str | None = None) -> Signal:
    """A needs-reply row as a feed signal. It keeps the row's id,
    ``email:<account id>:<thread id>``, and its link. ``message_id`` is the
    thread's last message, which the state fingerprint reads."""
    _, account_id, thread_id = row["id"].split(":", 2)
    detail = row.get("detail") or ""
    who = detail[len("From "):] if detail.startswith("From ") else None
    return Signal(
        signal_id=row["id"], app="email", kind="needs_reply",
        entity={"type": "thread", "id": thread_id, "label": row["title"]},
        title=row["title"],
        facts={"who": who, "waiting_since": row.get("at"), "account": account_id,
               "message_id": message_id},
        href=row["href"], acts=(link("open-thread", "Open thread", row["href"]),),
        at=row.get("at"),
    )


#: Where a rule row with no message opens. The email app has no link to its
#: rules history (``app/email`` reads only ``?email=`` and ``?account=``), so
#: the row opens the app.
EMAIL_HOME = "/email"


async def _email_handled(user: UserContext, since: datetime) -> list[Closed]:
    """Email's Handled read: the rules applied since ``since`` on the
    mailboxes that the needs rows read (``runner.applied_rules_since``).
    The title is the subject, or else the rule name. The link opens the
    message, or else the email app."""
    from gateway.routes.email.automation.runner import applied_rules_since
    from gateway.routes.email.transport.accounts import feed_mailboxes

    accounts = [str(r["id"]) for r in await feed_mailboxes(user) if _in_all_inboxes(r)]
    if not accounts:
        return []
    out: list[Closed] = []
    for row in await applied_rules_since(user, accounts, since, limit=PER_APP):
        message = row.get("message_id")
        title = (str(row.get("subject") or "").strip()
                 or str(row.get("rule_name") or "").strip() or "(no subject)")
        out.append(Closed(
            key=f"email:{message}" if message else f"email:rule:{row['id']}",
            app="email", title=title,
            href=_email_href(str(message), str(row["account_id"])) if message else EMAIL_HOME,
            by=BY_RULES, at=_iso(row.get("created_at")),
        ))
    return out


# ── Approvals: the pending actions of the member's organization ─────────────

#: An action's plain words, by its name. The names are the ones each
#: registered handler takes (``register_action_handler``).
_ACTION_WORDS = {
    "app.publish_review": "Review an app before it publishes",
    "workflow.resume_run": "Resume a paused workflow",
    "whatsapp.broadcast": "Send a WhatsApp broadcast",
    "crm.zoho_create": "Create a record in Zoho CRM",
    "crm.zoho_update": "Change a record in Zoho CRM",
    "crm.zoho_delete": "Delete a record in Zoho CRM",
}
#: The proposers whose name says nothing to a member.
_PROPOSER_WORDS = {"crm:zoho-sync": "the Zoho CRM sync"}
#: An id, which a title never prints.
_LOOKS_LIKE_ID = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$",
                            re.IGNORECASE)


def _words(name: str) -> str:
    return " ".join(re.split(r"[._\-\s]+", name)).strip()


def _action_title(row: dict[str, Any]) -> str:
    """What the action does, in plain words. It prints no id."""
    action = str(row.get("action") or "").strip()
    title = _ACTION_WORDS.get(action)
    if title is None:
        if action.startswith("app.") and len(action) > len("app."):
            # An app's own tool (``routes/apps/tools.py``).
            title = f"Run {_words(action[len('app.'):])} for an app"
        else:
            words = _words(action)
            title = (words[:1].upper() + words[1:]) if words else "An action"
    if action == "whatsapp.broadcast":
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        targets = payload.get("targets")
        if isinstance(targets, list) and targets:
            title += f" to {len(targets)} chat" + ("" if len(targets) == 1 else "s")
    return title


def _proposer(actor: Any) -> str:
    """Who proposed the action, as the Approvals queue names the actor."""
    raw = str(actor or "").strip()
    if raw in _PROPOSER_WORDS:
        return _PROPOSER_WORDS[raw]
    kind, _, rest = raw.partition(":")
    name = rest.split(":")[0].strip()
    if kind == "agent" and name:
        return f"the {_words(name)} agent"
    if kind == "app" and name:
        return f"the {name} app"
    if kind == "workflow" and name:
        return "a workflow" if _LOOKS_LIKE_ID.match(name) else f"the {name} workflow"
    if kind == "user" and name:
        return _who(name)
    return _who(raw)


async def _approvals(user: UserContext) -> Found:
    import action_broker

    # The broker's bounded read of the queue, which raises on a database
    # failure (the module note). The broker binds the tenant this request
    # bound (H-201). The read is sync, so it runs in a thread, which takes
    # this context and its tenant with it.
    rows = await asyncio.to_thread(action_broker.read_pending, PER_APP)
    out = []
    for row in rows:
        action_id = str(row["id"])
        out.append(_item(
            id=f"approvals:{action_id}", app="approvals", kind="approval",
            title=_action_title(row), detail=f"Proposed by {_proposer(row.get('actor'))}",
            href="/approvals", at=row.get("created_at"),
        ))
    shown = _sort(out)[:PER_APP]
    actions = {str(r["id"]): str(r.get("action") or "") for r in rows}

    def signals() -> list[Signal]:
        return [Signal(
            signal_id=item["id"], app="approvals", kind="approval",
            entity={"type": "action", "id": item["id"].split(":", 1)[1],
                    "label": item["title"]},
            title=item["title"],
            facts={"action": actions.get(item["id"].split(":", 1)[1], ""),
                   "proposer": (item["detail"] or "")[len("Proposed by "):]},
            # The feed's card opens the one proposal (MD-3a). The needs row
            # above keeps the queue, because nothing in the bell changes (§4.4).
            href=approval_href(item["id"].split(":", 1)[1]),
            # Class C: to approve is an outward write, so it always opens
            # the app (§11.4). The member reads the proposal there first.
            acts=(link("open-approvals", "Open Approvals",
                       approval_href(item["id"].split(":", 1)[1]), cls="C"),),
            at=item.get("at"),
        ) for item in shown]

    # The queue read stops at PER_APP and runs no count.
    return Found(shown, len(out[:PER_APP]), capped=len(rows) >= PER_APP,
                 signals=signals)


async def _approvals_handled(user: UserContext, since: datetime) -> list[Closed]:
    """Approvals' Handled read: the actions that a person approved or
    rejected since ``since`` (``action_broker.read_decided``). It runs in a
    thread, as ``read_pending`` does, and the thread takes the tenant."""
    import action_broker

    rows = await asyncio.to_thread(action_broker.read_decided, since, PER_APP)
    return [Closed(
        key=f"approvals:{row['id']}", app="approvals", title=_action_title(row),
        href="/approvals", by=closed_by(row.get("reviewed_by"), user.email or ""),
        at=_iso(row.get("reviewed_at")),
    ) for row in rows]


# ── The one registry (``my_day_feed.md`` §4.4) ─────────────────────────────


@dataclass(frozen=True)
class Provider:
    """One entry of the registry.

    ``features`` are the gates the app's routers demand: the member must
    hold each one. ``admin`` adds the admin test (``ADMIN_PERMISSION``).
    ``kinds`` are the kinds the provider may give. ``needs`` says if it
    serves the needs view of ``GET /shell/needs``, and a needs provider may
    give only the kinds of ``KIND_ORDER``.

    ``handled`` is the app's Handled read (``my_day_feed.md`` MD-2c): what
    the app closed for the member since an instant, through the app's own
    authorized read. Only the feed calls it (``handled.handled_run``), with
    the same gate as ``run``. The needs view never does.

    ``permission`` names one more grant that the member must hold, such as
    ``admin:members:read`` (MD-3b, §5.8 item 4). A member without it gets
    the source ``absent``, and the provider never runs. ``gated`` says that
    the provider runs only when ``MY_DAY_PROVIDERS`` lists its source id
    (§5.8 item 1). Only a feed-only provider may be gated.
    """

    source: str
    features: tuple[str, ...]
    run: Callable[[UserContext], Awaitable[Found]]
    kinds: frozenset[str]
    admin: bool = False
    needs: bool = True
    handled: Callable[[UserContext, datetime], Awaitable[list[Closed]]] | None = None
    permission: str | None = None
    gated: bool = False
    #: The app of the cards, when the source id is not the app id (MD-3b,
    #: §5.8 item 6). ``source_app`` reads it.
    app: str | None = None

    def held_by(self, user: UserContext) -> bool:
        """The gate the app's router would have applied (the module note),
        and the provider's own ``permission``."""
        if not all(user.has_permission(f"feature:{f}") for f in self.features):
            return False
        if self.permission and not user.has_permission(self.permission):
            return False
        return not self.admin or user.has_permission(ADMIN_PERMISSION)


#: Every provider, once, by source. The order is the order they run in.
REGISTRY: dict[str, Provider] = {}


def register(provider: Provider) -> Provider:
    """Add one provider. A second entry for a source is a defect, and a
    needs provider that names a kind outside ``KIND_ORDER`` is one too."""
    if provider.source in REGISTRY:
        raise ValueError(f"the provider {provider.source!r} registers twice")
    if not provider.features or not provider.kinds:
        raise ValueError(f"the provider {provider.source!r} names no feature or no kind")
    if provider.needs and not provider.kinds <= KIND_ORDER.keys():
        raise ValueError(
            f"the needs provider {provider.source!r} gives a kind outside KIND_ORDER: "
            f"{sorted(provider.kinds - KIND_ORDER.keys())}")
    if provider.needs and any(not p.needs for p in REGISTRY.values()):
        raise ValueError("a needs provider must register before every feed-only one")
    if provider.needs and provider.gated:
        # MD-3b: the list gates the feed only. The bell never changes.
        raise ValueError(f"the needs provider {provider.source!r} is gated")
    if provider.gated and provider.handled is not None:
        # MD-3b review: `handled.handled_providers` reads the whole registry,
        # so an unlisted gated provider would still run its Handled read.
        # The slice that needs both must first filter that read by the list.
        raise ValueError(
            f"the gated provider {provider.source!r} carries a Handled read, and "
            "handled.handled_providers does not filter by MY_DAY_PROVIDERS yet")
    REGISTRY[provider.source] = provider
    return provider


# Approvals runs LAST of the needs providers (the module note). Three of them
# also carry a Handled read (MD-2c). Projects has none: a closed task is
# My Tasks' row, and a read notification closed nothing.
register(Provider("tasks", ("projects",), _tasks, frozenset({"overdue", "due_today"}),
                  handled=_tasks_handled))
register(Provider("projects", ("projects",), _projects, frozenset({"notification"})))
register(Provider("email", ("email",), _email, frozenset({"needs_reply"}),
                  handled=_email_handled))
register(Provider("approvals", ("approvals",), _approvals, frozenset({"approval"}),
                  admin=True, handled=_approvals_handled))


def needs_providers() -> list[Provider]:
    """The needs subset, in the order it runs."""
    return [p for p in REGISTRY.values() if p.needs]


def listed_sources() -> frozenset[str]:
    """The ONE reader of ``MY_DAY_PROVIDERS`` (``my_day_feed.md`` §5.8 item
    1): the source ids of the gated providers that may run. Ids are trimmed
    and in lower case, with a comma between them. Empty by default. It is
    read on each call, but ``get_settings`` caches the Settings for the life
    of the process. So a change of the list takes effect only when the
    gateway restarts, and the restart is the act of the flip. The read
    cache's ``features_hash`` covers the list through this function, so no
    stored answer from before the restart is served after it."""
    from acb_common.settings import get_settings

    raw = str(get_settings().my_day_providers or "")
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def source_app(source: str) -> str:
    """The app id of a source, for the ``source_apps`` map of the feed answer
    (MD-3b, §14.1a). A source with no ``app`` is its own app."""
    provider = REGISTRY.get(source)
    if provider is None:
        return source
    return provider.app or provider.source


def feed_only_providers() -> list[Provider]:
    """The providers that serve the feed only. They run after the needs ones.

    A gated provider is here only when ``MY_DAY_PROVIDERS`` lists its source
    (MD-3b). Each kind stays in ``REGISTRY``, so the prefs and the state POST
    accept it while the provider is dark."""
    listed = listed_sources()
    return [p for p in REGISTRY.values()
            if not p.needs and (not p.gated or p.source in listed)]


#: source → (the feature its app's router demands, the provider), for the
#: needs view. A view of ``REGISTRY``, kept by name for its readers. The
#: order here is the order the providers run in, not the order of the feed.
PROVIDERS: dict[str, tuple[str, Callable[[UserContext], Awaitable[Found]]]] = {
    p.source: (p.features[0], p.run) for p in needs_providers()
}
#: The sources that also demand the admin test (owner decision, 2026-10-10).
ADMIN_SOURCES = frozenset(p.source for p in REGISTRY.values() if p.admin)


def _sort(items: list[Item]) -> list[Item]:
    """The feed's order: by kind, then by time in the kind's direction.

    A row with no time goes last in its kind.
    """
    def key(item: Item) -> tuple[int, int, float]:
        at = _instant(item.get("at"))
        if at is None:
            return (KIND_ORDER[item["kind"]], 1, 0.0)
        stamp = at.timestamp()
        return (KIND_ORDER[item["kind"]], 0,
                -stamp if NEWEST_FIRST[item["kind"]] else stamp)

    return sorted(items, key=key)


@router.get("/needs")
async def shell_needs(
    limit: int = DEFAULT_LIMIT,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """What needs this member now, from the apps the member holds."""
    if not user.email:
        raise HTTPException(status_code=401, detail="Authentication required")
    size = max(1, min(int(limit), MAX_LIMIT))
    return needs_answer(await needs_run(user), size)


@dataclass
class Run:
    """One run of some providers: the needs rows and what each source did."""

    items: list[Item] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)
    capped: list[str] = field(default_factory=list)
    #: What each source that read ``ok`` found, by source.
    found: dict[str, Found] = field(default_factory=dict)


async def needs_run(user: UserContext) -> Run:
    """The needs providers, in today's order, on today's budget. The needs
    route and the feed both call this, so they make one run of each."""
    return await run_providers(user, needs_providers(), budget_s=TOTAL_BUDGET_S,
                               timeout_s=PROVIDER_TIMEOUT_S, event="shell.needs")


async def _attempt(provider: Provider, user: UserContext, timeout: float,
                   event: str) -> Found | str:
    """One provider's run: what it found, or ``absent`` or ``failed``. It
    never raises, except a cancel of the whole read.

    Each run writes one log line with ``elapsed_ms``, the time from the start
    of the run to its end (MD-3c, ``my_day_feed.md`` §5.9 item 7). The rule
    for a flip of ``MY_DAY_PROVIDERS`` takes the p95 of each provider from
    these lines. A run that ends well writes ``provider answered``. A run
    that the ceiling cancels writes ``provider passed the ceiling``
    (:func:`_run_bounded`). Each field is in the TEXT of the line
    (:func:`_log_run`)."""
    key = provider.source
    began = time.monotonic()

    def _ms() -> int:
        return int((time.monotonic() - began) * 1000)

    try:
        found = await asyncio.wait_for(provider.run(user), timeout)
    except SourceAbsent:
        # A refusal, not a failure (MD-3b, §5.8 item 4).
        _log_run(_answer_level(event), event, "answered", key, _ms(), outcome="absent")
        return "absent"
    except TimeoutError:
        _log_run(logging.INFO, event, "timed out", key, _ms())
        return "failed"
    except Exception as exc:
        # The error's TYPE only. A database error's text can carry the
        # query's parameters, which hold the member's address. An app's
        # own refusal (HTTPException) lands here too.
        _log_run(logging.WARNING, event, "failed", key, _ms(), error=type(exc).__name__)
        return "failed"
    _log_run(_answer_level(event), event, "answered", key, _ms(), outcome="ok")
    return found


#: The event of the feed-only providers (``feed.feed_cards``). Their run
#: logs each answer at INFO, for the measure of §5.9 item 7.
FEED_EVENT = "shell.feed"


def _answer_level(event: str) -> int:
    """INFO for a feed-only provider, DEBUG for a needs provider. The needs
    providers run on each poll of the bell, and the measure needs only the
    feed-only ones (MD-3c fix round 1)."""
    return logging.INFO if event == FEED_EVENT else logging.DEBUG


def _log_run(level: int, event: str, what: str, key: str, ms: int, **fields: str) -> None:
    """One line of a provider run, with every field in its TEXT.

    ⚠️ The stdlib handler of the gateway prints ``%(message)s`` alone
    (``acb_common._log.configure_logging``), so a field in ``extra`` never
    reaches the journal. The p95 of §5.9 item 7 comes from these lines, so
    ``provider=``, ``elapsed_ms=`` and the other fields go in the text."""
    tail = "".join(f" {name}={value}" for name, value in fields.items())
    logger.log(level, "%s provider %s provider=%s%s elapsed_ms=%d",
               event, what, key, tail, ms)


def _record(run: Run, key: str, outcome: Found | str) -> None:
    """Put one provider's outcome in ``run``."""
    if isinstance(outcome, str):
        run.sources[key] = outcome
        return
    found = outcome
    run.sources[key] = "ok"
    run.found[key] = found
    run.items.extend(found.rows[:PER_APP])
    run.totals[key] = max(found.total, len(found.rows[:PER_APP]))
    if found.capped:
        run.capped.append(key)


#: Seconds that a cancelled run waits for its cancelled providers, so each
#: one closes its session before the read unwinds. It is short, so a stuck
#: release cannot hang the cancel. It also runs when the ceiling fires, so
#: it counts in the feed's worst case (``feed.FEED_TOTAL_S``).
CANCEL_WAIT_S = 0.1


async def _run_bounded(user: UserContext, providers: list[Provider], *, limit: float,
                       slots: int, ceiling_s: float | None, event: str) -> Run:
    """The feed-only providers (MD-3b, §5.8 item 3): ``slots`` run at once at
    most, so a feed read holds ``slots`` pool connections at most for them.
    Each provider's ``limit`` starts when it takes a slot, not when the run
    starts. At ``ceiling_s`` from the start, a provider that still waits for
    a slot or still runs is cancelled, and reads ``failed``."""
    run = Run()
    held = [p for p in providers if p.held_by(user)]
    gate = asyncio.Semaphore(max(1, slots))

    #: When each provider took its slot, for the line of a cancelled run.
    started: dict[str, float] = {}
    cut: float | None = None

    async def _slot(provider: Provider) -> Found | str:
        async with gate:
            # The limit starts here, when the provider holds its slot.
            started[provider.source] = time.monotonic()
            return await _attempt(provider, user, limit, event)

    tasks = {p.source: asyncio.ensure_future(_slot(p)) for p in held}
    if tasks:
        try:
            await asyncio.wait(tasks.values(), timeout=ceiling_s)
        finally:
            # Past the ceiling, or a cancel of the whole read: stop the rest,
            # and wait briefly so each one closes its session first.
            cut = time.monotonic()
            late = [t for t in tasks.values() if not t.done()]
            for task in late:
                task.cancel()
            if late:
                await asyncio.wait(late, timeout=CANCEL_WAIT_S)
    for provider in providers:
        task = tasks.get(provider.source)
        outcome: Found | str
        if task is None:
            outcome = "absent"
        elif not task.done() or task.cancelled() or task.exception() is not None:
            # A cancel skips the handlers of `_attempt`, so the measure of a
            # run cut at the ceiling is here: its time from its slot to the
            # cut. Without it the p95 would drop the slowest runs. A provider
            # that never got a slot ran for 0 ms.
            began = started.get(provider.source)
            ms = 0 if began is None or cut is None else int((cut - began) * 1000)
            _log_run(logging.INFO, event, "passed the ceiling", provider.source, ms,
                     started="yes" if began is not None else "no")
            outcome = "failed"
        else:
            outcome = task.result()
        _record(run, provider.source, outcome)
    return run


async def run_providers(user: UserContext, providers: list[Provider], *,
                        budget_s: float, timeout_s: float, event: str,
                        slots: int | None = None, ceiling_s: float | None = None) -> Run:
    """Run ``providers`` one after another, each inside what is left of
    ``budget_s``. A provider the member does not hold reads ``absent``, and
    so does one that raises :class:`SourceAbsent`. One that fails, refuses
    or runs out of time reads ``failed``, and never fails the run.

    With ``slots`` set (the feed-only providers, MD-3b, §5.8 item 3), up to
    ``slots`` providers run at the same time. Each gets its own limit,
    ``min(timeout_s, budget_s)``, from the moment it starts, so a slow one
    fails alone. ``ceiling_s`` bounds the whole run (:func:`_run_bounded`).
    The outcomes go into ``run`` in the order of ``providers``, as before."""
    if slots is not None:
        return await _run_bounded(user, providers, limit=min(timeout_s, budget_s),
                                  slots=slots, ceiling_s=ceiling_s, event=event)
    run = Run()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_s
    for provider in providers:
        key = provider.source
        if not provider.held_by(user):
            run.sources[key] = "absent"
            continue
        left = deadline - loop.time()
        if left <= 0.05:
            run.sources[key] = "failed"
            continue
        _record(run, key, await _attempt(provider, user, min(timeout_s, left), event))
    return run


def needs_answer(run: Run, size: int) -> dict[str, Any]:
    """The answer of ``GET /shell/needs`` for one run (§7.2 contract).

    ``totals`` is how many need the member in each source that answered:
    the true count where the app's read knows it, else the rows found.
    ``capped`` names the sources whose total is a floor (the read stopped
    at PER_APP). The sidebar reads both, because the cut to ``size`` can
    drop one app's rows from ``items`` entirely (the replies sort last).
    A source that failed or is absent has no entry in either.
    """
    return {**_fit(run.items, size), "totals": run.totals, "capped": run.capped,
            "sources": run.sources}


def _fit(items: list[Item], size: int) -> dict[str, Any]:
    """The first ``size`` rows. A yielding kind takes only the room left.

    ``count`` is the rows sent, as before. ``total`` is every row the
    sources gave before the cut to ``size``, so the shell's bell can say 45
    when it shows 30 (NS-6). Each source gives ``PER_APP`` at most, so
    ``total`` is a floor, not a full count of what waits.
    """
    own = _sort([i for i in items if i["kind"] not in YIELDING_KINDS])[:size]
    rest = _sort([i for i in items if i["kind"] in YIELDING_KINDS])[:size - len(own)]
    feed = _sort(own + rest)
    return {"count": len(feed), "total": len(items), "items": feed}
