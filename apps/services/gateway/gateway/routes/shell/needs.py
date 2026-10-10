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
tasks      ``feature:projects``  ``projects.personal.my_due_tasks``: one
                                 bounded read of the lens, due today and
                                 overdue, the oldest deadline first.
projects   ``feature:projects``  ``projects.notifications.list_notifications``,
                                 unread only.
email      ``feature:email``     ``email.transport.accounts.list_accounts``,
                                 then ``email.digest.needs_reply_threads``
                                 for each mailbox, the longest wait first.
=========  ===================  ===========================================

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
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.personal import NOT_NOW_DISPOSITIONS
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
    from gateway.routes.projects.personal import my_due_tasks

    # One bounded read: due before the member's tomorrow, in the member's
    # zone, the oldest deadline first, at most PER_APP rows.
    answer = await my_due_tasks(user, limit=PER_APP)
    now = datetime.now(UTC)

    overdue: list[Item] = []
    due_today: list[Item] = []
    for task in answer.get("rows") or []:
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
    from gateway.routes.email.digest import needs_reply_threads
    from gateway.routes.email.transport.accounts import list_accounts

    accounts = await list_accounts(user=user)
    out: list[Item] = []
    asked = failed = 0
    for account in accounts:
        row = account if isinstance(account, dict) else account.model_dump()
        if not row.get("in_all_inboxes", True):
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
        for thread in threads:
            if not thread.get("message_id"):
                continue  # nothing to open
            who = thread.get("who") or ""
            out.append(_item(
                id=f"email:{account_id}:{thread['thread_id']}", app="email",
                kind="needs_reply", title=thread.get("subject") or "(no subject)",
                detail=f"From {who}" if who else None,
                href=_email_href(str(thread["message_id"]), account_id),
                at=thread.get("last_message_at"),
            ))
    if asked and failed == asked:
        raise RuntimeError("every mailbox failed")
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
    # ``count`` is the rows sent, as before. ``total`` is every row the
    # sources gave before the cut to ``size``, so the shell's bell can say 45
    # when it shows 30 (NS-6). Each source gives ``PER_APP`` at most, so
    # ``total`` is a floor, not a full count of what waits.
    return {"count": len(feed), "total": len(items), "items": feed, "sources": sources}
