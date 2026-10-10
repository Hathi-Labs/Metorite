"""Views and quick commands for the WhatsApp channel (WS-47 WAC-10c).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §13.

**A common question costs no AI call.** A member who sends "today", "due
today", "overdue", "calendar", "approvals" or "menu", or who taps one of
those rows of the menu, gets an answer that code builds. The text, the list
and the agenda image come from the SAME reads the web app shows:

* the My Day feed (``routes/shell/needs.shell_needs``, ``navigation_shell.md``
  §7.2) for "my day" and "approvals";
* the member's due work (``routes/projects/personal.my_due_tasks``) for "due
  today" and "overdue";
* the day summary (``routes/tasks/calendar.day_summary``) for "calendar".

**The same views serve the AI.** ``whatsapp_ui(kind="view", data={"name"})``
runs one of them, so a model that sees "what's on my plate?" sends the view
by name and writes no rows itself (:func:`runner`).

**Each read is the member's own.** :func:`member_context` is the request
path's resolve (``acb_auth.deps.member_context``). A route function called
in-process skips its router's feature gate, so each view checks the feature
itself, as the router would (:data:`FEATURE`).

Fence: ``tests/unit/test_wac_views.py``.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from acb_common import get_logger

_log = get_logger(__name__)

APP_URL = "https://app.metorite.com"

# ── The commands ────────────────────────────────────────────────────────────

#: What a member types (or taps) → the view. Matched on the whole message,
#: after :func:`normalize`. A longer question goes to the AI, which can send
#: the same view by name.
COMMANDS: dict[str, str] = {}
for _view, _words in {
    "menu": ("menu", "help", "hi", "hello", "hey", "start", "options", "commands"),
    "my_day": ("my day", "today", "what's on today", "whats on today", "what needs me",
               "needs me", "my day today", "what's today", "whats today"),
    "due_today": ("due today", "what's due today", "whats due today", "what is due today",
                  "tasks due today", "due", "my tasks due today"),
    "overdue": ("overdue", "what's overdue", "whats overdue", "overdue tasks", "late tasks",
                "my overdue tasks"),
    "calendar": ("calendar", "my calendar", "schedule", "my schedule", "agenda",
                 "calendar today", "today's schedule", "todays schedule", "my day plan"),
    "approvals": ("approvals", "pending approvals", "my approvals", "approvals waiting"),
}.items():
    for _w in _words:
        COMMANDS[_w] = _view

#: The feature each view reads, as its router would check it.
FEATURE = {"my_day": None, "due_today": "projects", "overdue": "projects",
           "calendar": "tasks", "approvals": None, "menu": None}

#: How deep the due views read the member's due work (oldest first).
DUE_READ = 200

_PUNCT = re.compile(r"[^\w\s']")


def normalize(text: str) -> str:
    """Lower case, no emoji or punctuation (but the apostrophe), one space."""
    folded = unicodedata.normalize("NFKC", text or "").replace("’", "'").lower()
    return " ".join(_PUNCT.sub(" ", folded).split())


#: A tap on a list row arrives as "Title (description)" (``inbound.tap_text``).
_TAP = re.compile(r"\A(.{1,24}) \((.*)\)\Z", re.DOTALL)


def match(text: str) -> str | None:
    """The view a whole message asks for, or None. Short messages only.

    A list tap ("My day (What needs you now…)") matches on its title, so a
    tap on the menu costs no AI call (WAC-10c review).
    """
    if not text or len(text) > 140:
        return None
    tap = _TAP.match(text.strip())
    if tap:
        return COMMANDS.get(normalize(tap.group(1)))
    if len(text) > 60:
        return None
    return COMMANDS.get(normalize(text))


#: A thanks that needs no answer gets a reaction from code, with no AI call
#: and no text (WAC-10e). Only a thanks: an "ok" or a 👍 can answer a
#: question the AI asked, so the AI hears those.
THANKS = frozenset({
    "thanks", "thank you", "thanks a lot", "thank you so much", "thanks so much",
    "thx", "ty", "tysm", "thank u", "many thanks", "cheers", "thanks again",
})
THANKS_EMOJI = frozenset({"🙏", "🙏🏻", "🙏🏼", "🙏🏽", "🙏🏾", "🙏🏿"})
#: The reaction a thanks gets.
THANKS_REACTION = "👍"


def thanks(text: str) -> bool:
    """True when the whole message is a thanks (:data:`THANKS`)."""
    raw = (text or "").strip()
    if not raw or len(raw) > 40:
        return False
    return raw in THANKS_EMOJI or normalize(raw) in THANKS


# ── The result ──────────────────────────────────────────────────────────────


@dataclass
class View:
    """What one view sends: a text, then up to 3 elements."""

    name: str
    text: str
    ui: list[Any] = field(default_factory=list)


async def member_context(email: str, organization_id: str) -> Any:
    """The member's request context, IN THE LINK'S ORG only.

    ``acb_auth.deps.member_context`` finds the org from the email. The run's
    org comes from the link row (D-WAC-3), so a context in any other org is
    refused, never used (WAC-10c review).
    """
    from acb_auth.deps import member_context as _ctx

    ctx = await _ctx(email)
    if str(getattr(ctx, "organization_id", "") or "") != str(organization_id):
        raise PermissionError("the member's context is not in the link's org")
    return ctx


def _allowed(ctx: Any, name: str) -> bool:
    feature = FEATURE.get(name)
    return feature is None or bool(ctx.has_permission(f"feature:{feature}"))


async def _build(kind: str, data: dict[str, Any]) -> Any:
    """One checked element, built off the event loop (it may draw)."""
    from acb_skills.whatsapp_ui import build

    return await asyncio.to_thread(build, kind, data)


# ── Small text helpers ──────────────────────────────────────────────────────


def _when(at: Any, now: datetime) -> str:
    """"3 days late", "in 2 h", "2 days ago": no zone needed."""
    try:
        moment = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    secs = (moment - now).total_seconds()
    mins = abs(secs) / 60
    if mins < 60:
        span = f"{max(int(mins), 1)} min"
    elif mins < 48 * 60:
        span = f"{int(mins // 60)} h"
    else:
        span = f"{int(mins // 1440)} days"
    return f"in {span}" if secs > 0 else f"{span} ago"


def _aware(moment: datetime) -> datetime:
    """A stored time with no zone is UTC, as the stores write it."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _row(title: Any, *parts: str) -> dict[str, str]:
    desc = " · ".join(p for p in parts if p)
    return {"title": str(title or "Untitled"), **({"description": desc} if desc else {})}


async def _link(body: str, label: str, path: str) -> Any:
    return await _build("link", {"body": body, "label": label, "url": APP_URL + path})


async def _list(body: str, button: str, sections: list[tuple[str, list[dict]]],
                total: int, more_path: str, more_label: str) -> list[Any]:
    """A list of up to 10 rows over sections, and a link when more exist."""
    shown: list[dict[str, Any]] = []
    room = 10
    for title, rows in sections:
        if rows and room:
            shown.append({"title": title, "rows": rows[:room]})
            room -= len(shown[-1]["rows"])
    if len(shown) == 1:
        shown[0]["title"] = ""
    out = [await _build("list", {"body": body, "button": button, "sections": shown}
                        if len(shown) > 1 else
                        {"body": body, "button": button, "rows": shown[0]["rows"]})]
    rows_shown = sum(len(s["rows"]) for s in shown)
    if total > rows_shown:
        out.append(await _link(f"{total - rows_shown} more in Metorite.", more_label, more_path))
    return out


# ── The views ───────────────────────────────────────────────────────────────

#: The feed's kinds, in its order, with the label a section shows (24 chars).
_KINDS = (("overdue", "🔴 Overdue"), ("approval", "✋ Approvals"),
          ("due_today", "📅 Due today"), ("notification", "🔔 From your projects"),
          ("needs_reply", "💬 Needs your reply"))


def _kind_words(kind: str, n: int) -> str:
    if kind == "overdue":
        return f"{n} overdue"
    if kind == "due_today":
        return f"{n} due today"
    word = {"approval": "approval", "notification": "update", "needs_reply": "reply"}[kind]
    return _plural(n, word).replace("replys", "replies")


async def _feed(ctx: Any) -> dict[str, Any]:
    from gateway.routes.shell.needs import MAX_LIMIT, shell_needs

    return await shell_needs(limit=MAX_LIMIT, user=ctx)


def _admin_approvals(ctx: Any) -> bool:
    """The feed's own gate for approvals: the feature AND the admin right."""
    from gateway.routes.shell.needs import ADMIN_PERMISSION

    return bool(ctx.has_permission("feature:approvals")
                and ctx.has_permission(ADMIN_PERMISSION))


async def view_menu(ctx: Any) -> View:
    """Only the rows that this member can open."""
    rows = [_row("My day", "What needs you now, from every app")]
    for name, title, desc in (("due_today", "Due today", "Your tasks due today"),
                              ("overdue", "Overdue", "Your tasks past their due date"),
                              ("calendar", "Calendar", "Today's plan on a timeline")):
        if _allowed(ctx, name):
            rows.append(_row(title, desc))
    if _admin_approvals(ctx):
        rows.append(_row("Approvals", "Agent actions waiting for you"))
    el = await _build("list", {"body": "Tap one, or ask me anything about your work.",
                               "button": "Menu", "rows": rows})
    return View("menu", "*Metorite* on WhatsApp 👋", [el])


async def view_my_day(ctx: Any, *, only: str | None = None) -> View:
    if only == "approval" and not _admin_approvals(ctx):
        return View("approvals", "Approvals are for the admins of your organization.")
    feed = await _feed(ctx)
    sources = feed.get("sources") or {}
    failed = sorted(k for k, v in sources.items() if v == "failed")
    if only == "approval" and "approvals" in failed:
        # The one source this view needs did not answer: no false "none".
        raise RuntimeError("the approvals source failed")
    items = [i for i in feed.get("items") or [] if only is None or i.get("kind") == only]
    now = datetime.now(UTC)
    head = "*Approvals*" if only == "approval" else "*My day*"
    note = (f"\n_{', '.join(failed).capitalize()} did not answer in time, so this may "
            "not be all._") if failed and only is None else ""
    if not items:
        if failed and only is None:
            el = await _link("Open My Day to see the rest.", "Open My Day", "/")
            return View("my_day", f"{head} — nothing so far.{note}", [el])
        quiet = ("No approvals wait for you. ✅" if only == "approval"
                 else "Nothing needs you right now. ✅")
        el = await _build("buttons", {"body": quiet, "buttons": ["Calendar", "Menu"]})
        return View(only or "my_day", head, [el])
    from gateway.routes.shell.needs import PER_APP

    counts: dict[str, int] = {}
    for i in items:
        counts[i["kind"]] = counts.get(i["kind"], 0) + 1
    # A source gives at most PER_APP rows, so a full source may hold more.
    capped = {k for k, n in counts.items() if n >= PER_APP}
    summary = " · ".join(_kind_words(k, n).replace(str(n), f"{n}+", 1) if k in capped
                         else _kind_words(k, n)
                         for k, n in counts.items() if k in dict(_KINDS))
    sections = []
    for kind, label in _KINDS:
        rows = [_row(i.get("title"), str(i.get("detail") or ""), _when(i.get("at"), now))
                for i in items if i.get("kind") == kind]
        sections.append((label, rows))
    total = len(items) + (1 if capped else 0)
    ui = await _list("Tap one to ask about it.", "View", sections, total, "/", "Open My Day")
    return View(only or "my_day", f"{head} — {summary}{note}", ui)


async def view_due(ctx: Any, *, kind: str) -> View:
    """``kind`` is "due_today" or "overdue": the member's own due work."""
    from gateway.routes.projects.personal import my_due_tasks
    from gateway.routes.shell.needs import HIDDEN_DISPOSITIONS

    # The read is oldest deadline first, so a short limit fills with overdue
    # work and hides what is due later today (WAC-10c review). One member's
    # due work is small, so read deep, and never say "none" at the cap.
    answer = await my_due_tasks(ctx, limit=DUE_READ)
    at_cap = len(answer.get("rows") or []) >= DUE_READ
    now = datetime.now(UTC)
    rows: list[tuple[datetime, dict]] = []
    for t in answer.get("rows") or []:
        if t.get("completed_at") or t.get("disposition") in HIDDEN_DISPOSITIONS:
            continue
        try:
            due = datetime.fromisoformat(str(t.get("due_at")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        if (due < now) != (kind == "overdue"):
            continue
        rows.append((due, t))
    rows.sort(key=lambda p: p[0])
    title = "*Overdue*" if kind == "overdue" else "*Due today*"
    if not rows and at_cap:
        raise RuntimeError("the due read reached its cap")  # the assistant answers
    if not rows:
        quiet = "Nothing is overdue. ✅" if kind == "overdue" else "Nothing is due today. ✅"
        el = await _build("buttons", {"body": quiet, "buttons": ["My day", "Calendar"]})
        return View(kind, title, [el])
    tz = ZoneInfo(answer.get("timezone") or "UTC")
    list_rows = []
    for due, t in rows:
        when = (_when(due, now).replace(" ago", " late") if kind == "overdue"
                else f"due {due.astimezone(tz):%H:%M}")
        list_rows.append(_row(t.get("title"), str(t.get("project_name") or ""), when))
    by_project: dict[str, int] = {}
    for _due, t in rows:
        name = str(t.get("project_name") or "No project")
        by_project[name] = by_project.get(name, 0) + 1
    top = ", ".join(f"{n} in {p}" for p, n in sorted(by_project.items(),
                                                     key=lambda kv: -kv[1])[:3])
    count = _plural(len(rows), "task")
    if at_cap and kind == "overdue":
        count = count.replace(str(len(rows)), f"{len(rows)}+", 1)
    text = f"{title} — {count}\n{top}"
    ui = await _list("Tap a task to ask about it.", "View tasks", [("", list_rows)],
                     len(rows), "/projects", "Open Projects")
    return View(kind, text, ui)


async def view_calendar(ctx: Any) -> View:
    from gateway.routes.projects.personal import my_today
    from gateway.routes.tasks.calendar import day_summary

    summary = await day_summary(date=None, user=ctx)
    try:
        zone = (await my_today(user=ctx)).get("timezone") or "UTC"
    except Exception:
        zone = "UTC"
    tz = ZoneInfo(zone)
    day = datetime.fromisoformat(summary["day"]).date()
    head = f"*{day:%A %d %b}*"
    extras = []
    one = (summary.get("one_thing") or {}).get("title")
    if one:
        extras.append(f"★ One thing: {one}")
    if summary.get("overdue_count"):
        extras.append(f"🔴 {_plural(summary['overdue_count'], 'overdue task')}")
    if summary.get("unscheduled_count"):
        extras.append(f"🗂 {summary['unscheduled_count']} not planned yet")
    items = []
    busy = 0
    for b in summary.get("scheduled") or []:
        try:
            s = _aware(datetime.fromisoformat(b["start"])).astimezone(tz)
            e = _aware(datetime.fromisoformat(b["end"])).astimezone(tz)
        except (TypeError, ValueError, KeyError):
            continue
        if e.date() != s.date() or e <= s:
            continue
        busy += int((e - s).total_seconds() // 60)
        items.append({"start": f"{s:%H:%M}", "end": f"{e:%H:%M}", "title": b.get("title"),
                      "kind": "event" if b.get("fixed") else "task",
                      **({"tone": "green", "detail": "done"} if b.get("done") else {})})
    if not items:
        text = "\n".join([f"{head} — nothing is planned yet.", *extras])
        el = await _link("Plan your day in the Calendar.", "Open Calendar", "/calendar")
        return View("calendar", text, [el])
    free = max(int(summary.get("capacity_mins") or 0) - busy, 0)
    sub = f"{_plural(len(items), 'block')} · {free // 60} h {free % 60:02d} m free"
    try:
        el = await _build("agenda", {"title": f"{day:%A %d %b}", "subtitle": sub,
                                     "items": items[:16]})
    except ValueError:  # a day too long for one image: send the text only
        el = None
    text = "\n".join([f"{head} — {sub}", *extras])
    ui = [el] if el is not None else []
    ui.append(await _link("Move or plan blocks in the Calendar.", "Open Calendar",
                          "/calendar"))
    return View("calendar", text, ui)


_VIEWS: dict[str, Callable[[Any], Awaitable[View]]] = {
    "menu": view_menu,
    "my_day": view_my_day,
    "due_today": lambda ctx: view_due(ctx, kind="due_today"),
    "overdue": lambda ctx: view_due(ctx, kind="overdue"),
    "calendar": view_calendar,
    "approvals": lambda ctx: view_my_day(ctx, only="approval"),
}

#: The names the AI may ask for (``whatsapp_ui`` kind "view").
NAMES = tuple(_VIEWS)


async def run(name: str, ctx: Any) -> View:
    """Build the view *name* for the member of *ctx*. Raises KeyError for an
    unknown name and PermissionError for a feature the member does not hold."""
    if name not in _VIEWS:
        raise KeyError(name)
    if not _allowed(ctx, name):
        raise PermissionError(name)
    return await _VIEWS[name](ctx)


def runner(email: str, organization_id: str) -> Callable[[str], Awaitable[View]]:
    """The view runner a WhatsApp run hands to ``whatsapp_ui``: one member's
    context in the link's org, resolved once, on first use. ``names`` lists
    the views, so the tool can refuse an unknown name before any read."""
    ctx_box: list[Any] = []

    async def _run(name: str) -> View:
        if not ctx_box:
            ctx_box.append(await member_context(email, organization_id))
        return await run(name, ctx_box[0])

    _run.names = NAMES  # type: ignore[attr-defined]
    return _run
