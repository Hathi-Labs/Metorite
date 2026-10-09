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

The three providers, and the function each one calls:

=========  ===================  ===========================================
Source     Feature              Function
=========  ===================  ===========================================
tasks      ``feature:projects``  ``projects.personal.my_today``, then
                                 ``my_inbox``. Due today and overdue only.
projects   ``feature:projects``  ``projects.notifications.list_notifications``,
                                 unread only.
email      ``feature:email``     ``email.transport.accounts.list_accounts``,
                                 then ``automation.replyzero.reply_zero``
                                 (``type=needs_reply``) for each mailbox.
=========  ===================  ===========================================

⚠️ **My Tasks needs ``feature:projects``, not ``feature:tasks``.** The lens
routes live on the Projects router, and that router demands ``projects``.
The ``tasks`` feature only shows the pane.

⚠️ **A Someday task is left out, even with a due date** (``HIDDEN_DISPOSITIONS``).
A WAITING task stays.

⚠️ **A separate mailbox is left out** (D-EM-30). The feed reads more than one
mailbox, and a mailbox that the member keeps separate leaves every such read.

⚠️ **The email read never starts a backfill.** ``reply_zero`` schedules one
on a mailbox with no classified thread. The provider hands it a fresh
``BackgroundTasks`` and never runs it, so a glance at the bell starts no work.

**Where each row opens** (``href``), and the app code it copies:

* a task: ``/projects?task=<task id>``, the deep link that Projects reads
  (``app/projects/lib/card.ts`` ``taskDeepLink``). The command bar's Find
  tier and ``app/tasks/lib/searchHit.ts`` link a task the same way.
* a notification: ``/projects?task=<task id>``, the bell's own link
  (``app/projects/lib/notifications.ts`` ``linkTo``).
* an email: ``/email?email=<message id>&account=<account id>``
  (``app/email/lib/emailLink.ts`` ``emailLink``).

**The one-click acts** (``act``), each through the OWNING app's route:

* ``done``: ``POST /projects/tasks/{act_ref}/complete``. My Tasks completes a
  task through it (``app/tasks/lib/lens.ts``).
* ``read``: ``POST /projects/notifications/read`` with
  ``{"ids": [act_ref]}``. The Projects bell marks a row read through it
  (``app/projects/lib/api.ts`` ``notificationsApi.markRead``).

An email row has no act. A reply needs the app.

The answer is plain words: a row says what it is, where it opens, and
nothing else. An id travels only inside ``id``, ``href`` and ``act_ref``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.shell.search import router

logger = logging.getLogger(__name__)

#: Rows per source, so one app cannot fill the feed.
PER_APP = 15
#: The feed's default length, and its ceiling.
DEFAULT_LIMIT = 30
MAX_LIMIT = 50
#: Seconds one provider may take. The email provider makes one read per
#: mailbox, and the tasks provider makes two, so this is wider than search's.
PROVIDER_TIMEOUT_S = 2.0
#: Seconds for the whole answer, all providers together. Each provider gets
#: what is left, the rule of ``search.TOTAL_BUDGET_S``.
TOTAL_BUDGET_S = 4.0
#: Lens pages the tasks provider reads at most. ``my_inbox`` caps a page at
#: ``MAX_PAGE_SIZE`` (100), so the provider sees the first 300 open tasks.
TASK_PAGES = 3
#: Threads each mailbox gives. The route lists the newest first, and the
#: feed shows the oldest first, so the provider asks for more than it shows.
EMAIL_FETCH = 50

#: The lens dispositions that never reach the feed, even with a due date.
#: Someday is the member's deliberate "not now", and a nag about it teaches
#: people to stop using Someday. WAITING stays: an overdue waiting-for is a
#: cue to chase someone. `my_inbox` filters to ONE disposition and cannot
#: leave one out, so the provider narrows the lens's own rows by the
#: effective ``disposition`` the lens sets on each.
HIDDEN_DISPOSITIONS = frozenset({"SOMEDAY"})

#: The order of the feed, by kind (§7.2 contract).
KIND_ORDER = {"overdue": 0, "due_today": 1, "notification": 2, "needs_reply": 3}
#: Within a kind: True is newest first, False is oldest first.
NEWEST_FIRST = {"overdue": False, "due_today": False, "notification": True,
                "needs_reply": False}

Item = dict[str, Any]


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


async def _tasks(user: UserContext) -> list[Item]:
    from gateway.routes.projects.core import MAX_PAGE_SIZE, Page
    from gateway.routes.projects.personal import my_inbox, my_today

    # The member's own date and zone, the lens's one read of them.
    day = await my_today(user=user)
    zone = ZoneInfo(str(day.get("timezone") or "UTC"))
    today = date.fromisoformat(str(day["today"]))
    now = datetime.now(UTC)

    rows: list[dict[str, Any]] = []
    for number in range(1, TASK_PAGES + 1):
        # ⚠️ Every parameter, by name. The route declares `page` with
        # `Depends()`, and a direct call that left one out would pass the
        # marker itself. `test_shell_needs.py` fails if the route gains one.
        answer = await my_inbox(
            user=user, disposition=None, context=None, include_deferred=False,
            include_done=False, include_archived=False, untriaged=False,
            page=Page(page=number, page_size=MAX_PAGE_SIZE),
        )
        rows.extend(answer.rows)
        if number * MAX_PAGE_SIZE >= answer.total:
            break

    overdue: list[Item] = []
    due_today: list[Item] = []
    for task in rows:
        due = _instant(task.get("due_at"))
        if due is None or task.get("completed_at"):
            continue
        if task.get("disposition") in HIDDEN_DISPOSITIONS:
            continue  # Someday is "not now", see HIDDEN_DISPOSITIONS
        if due < now:
            kind, bucket = "overdue", overdue
        elif due.astimezone(zone).date() == today:
            kind, bucket = "due_today", due_today
        else:
            continue
        task_id = str(task["id"])
        bucket.append(_item(
            id=f"tasks:{task_id}", app="tasks", kind=kind,
            title=task.get("title"), detail=task.get("project_name"),
            href=f"/projects?task={quote(task_id)}", at=due,
            act="done", act_ref=task_id,
        ))
    return (_sort(overdue) + _sort(due_today))[:PER_APP]


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


def _sentence(row: dict[str, Any]) -> str:
    title = str(row.get("task_title") or "").strip() or "a task"
    task = f"#{row['task_number']} {title}" if row.get("task_number") else title
    verb = _VERB.get(str(row.get("kind") or ""), "updated")
    return f"{_who(row.get('actor'))} {verb} {task}"


async def _projects(user: UserContext) -> list[Item]:
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
    return _sort(out)[:PER_APP]


# ── Email: needs reply, for each mailbox the member owns ────────────────────


def _email_href(message_id: str, account_id: str) -> str:
    """``emailLink`` in ``app/email/lib/emailLink.ts``, the same two names."""
    return "/email?" + urlencode({"email": message_id.lower(), "account": account_id.lower()})


async def _email(user: UserContext) -> list[Item]:
    from fastapi import BackgroundTasks
    from gateway.routes.email.automation.replyzero import reply_zero
    from gateway.routes.email.transport.accounts import list_accounts

    accounts = await list_accounts(user=user)
    out: list[Item] = []
    for account in accounts:
        row = account if isinstance(account, dict) else account.model_dump()
        if not row.get("in_all_inboxes", True):
            continue  # D-EM-30, see the module note
        account_id = str(row["id"])
        answer = await reply_zero(
            background=BackgroundTasks(), account_id=account_id,
            type="needs_reply", limit=EMAIL_FETCH, user=user,
        )
        for thread in answer.get("threads") or []:
            sender = thread.get("from") or thread.get("from_email") or ""
            out.append(_item(
                id=f"email:{account_id}:{thread['thread_id']}", app="email",
                kind="needs_reply", title=thread.get("subject") or "(no subject)",
                detail=f"From {sender}" if sender else None,
                href=_email_href(str(thread["message_id"]), account_id),
                at=thread.get("received_at"),
            ))
    return _sort(out)[:PER_APP]


#: source → (the feature its app's router demands, the provider).
PROVIDERS: dict[str, tuple[str, Callable[[UserContext], Awaitable[list[Item]]]]] = {
    "tasks": ("projects", _tasks),
    "projects": ("projects", _projects),
    "email": ("email", _email),
}


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

    items: list[Item] = []
    sources: dict[str, str] = {}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + TOTAL_BUDGET_S
    for key, (feature, provider) in PROVIDERS.items():
        # The gate the app's router would have applied (see the module note).
        if not user.has_permission(f"feature:{feature}"):
            sources[key] = "absent"
            continue
        left = deadline - loop.time()
        if left <= 0.05:
            sources[key] = "failed"
            continue
        try:
            rows = await asyncio.wait_for(provider(user), min(PROVIDER_TIMEOUT_S, left))
        except TimeoutError:
            logger.info("shell.needs provider timed out", extra={"provider": key})
            sources[key] = "failed"
            continue
        except Exception as exc:
            # The error's TYPE only. A database error's text can carry the
            # query's parameters, which hold the member's address. An app's
            # own refusal (HTTPException) lands here too.
            logger.warning(
                "shell.needs provider failed",
                extra={"provider": key, "error": type(exc).__name__},
            )
            sources[key] = "failed"
            continue
        sources[key] = "ok"
        items.extend(rows[:PER_APP])

    feed = _sort(items)[:size]
    return {"count": len(feed), "items": feed, "sources": sources}
