"""GET /shell/waiting — what waits on the member, across the apps (WS-AP AP-1a).

Spec: ``project-docs/specs/waiting_on_me.md`` §7.3 and the row AP-1 of §8.
The page "Waiting" (AP-2), My Day (AP-3) and the sidebar count read this one
seam. AP-1a serves ``direction=for_you`` with four kinds:

===============  ======================  ======================================
Kind             Gate                    The owning app's function
===============  ======================  ======================================
``held_write``   ``feature:approvals``   ``action_broker.read_pending``: the
                 and an org admin        queue of the bound tenant, the longest
                                         wait first (``needs._approvals``).
``chat_ask``     ``feature:chat`` and    ``routes.chat._my_waiting_asks``, then
                 ``CHAT_DURABLE_ASKS``   the room check of ``feed._chat``.
``app_request``  ``APP_REQUESTS_ORGS``   ``routes.agent_requests.
                 and agent access        list_app_requests``: the member's own.
``decision``     ``feature:projects``    ``routes.projects.decisions.
                 and ``DECISIONS_ORGS``  list_decisions(task_id=None)``.
===============  ======================  ======================================

The rules:

* **Dark by org.** :func:`waiting_allowed` is the ONE reader of
  ``WAITING_ORGS`` (default empty). With the org off, both routes answer 404
  and run no provider. The org is the session's, never request input (R5).
* **A person's list.** A signed agent gets 404. The member is ``user.email``.
* **No new data path.** Each provider calls its app's own authorized read.
  This module writes no SQL, opens no session and builds no visibility
  predicate. ``list_decisions`` keeps only the decisions on a task that the
  member can open (V2). Every other kind of AP-1a has no task.
* **The providers stay here.** They use ``Provider``, ``Found``,
  ``SourceAbsent`` and ``run_providers`` of ``needs.py``, and they do NOT
  register in ``needs.REGISTRY``. A provider there would change the bell
  and My Day.
* **A failed source reads ``failed``, never empty.** A refusal that is not a
  failure, such as a flag that is off, reads ``absent``.
* **Only an org admin gets ``held_write`` rows** (the rule of ``needs.py``).
  ``pending_actions`` has no requester column, so a member sees no held write
  of their own agent until AP-7.
* **No question text in a ``chat_ask`` row.** A model wrote it
  (``feed._chat``). The row has a fixed title.
* **Each act is a link** (``needs.link``, way ``open``). No act runs a write.
* **No score for any person** (V6, ALN-Q5). Nothing here groups, ranks or
  totals by person. The only total is the caller's own rows, by
  ``len``. Fence: ``tests/unit/test_decisions.py``
  ``test_the_table_keeps_no_score_for_any_person``, which scans this file.

Each row is ``{id, kind, title, waiter, href, since, acts}``. ``title`` and
``waiter`` are plain text, which the client draws as text and never as
markup. ``since`` is an aware ISO time or None.

Fences: ``tests/unit/test_waiting_seam_r8.py`` (V1, V2, V3, the flag, a
failed source, the admin rule), ``tests/unit/test_flag_reload.py``.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common.settings import get_settings
from fastapi import Depends, HTTPException
from gateway.routes.shell import state
from gateway.routes.shell.feed import CHAT_THREADS, _chat_href
from gateway.routes.shell.needs import (
    PER_APP,
    PROVIDER_TIMEOUT_S,
    TOTAL_BUDGET_S,
    Found,
    Provider,
    SourceAbsent,
    _action_title,
    _instant,
    _iso,
    _proposer,
    _who,
    approval_href,
    link,
    run_providers,
    task_href,
)
from gateway.routes.shell.search import router

#: The kinds of AP-1a, in the order their providers run. ``held_write`` runs
#: last, as approvals do in ``needs.py``.
KINDS = ("decision", "chat_ask", "app_request", "held_write")

#: The longest title or waiter text, in characters.
TEXT_MAX = 200

#: The log event of a run.
EVENT = "shell.waiting"

#: Where an app request opens: the AI agents view of Integrations
#: (``app/integrations/lib/views.ts`` ``viewHref("agents")``).
APP_REQUESTS_HREF = "/integrations?view=agents"

#: The fixed title of a chat question. The question text stays out.
CHAT_ASK_TITLE = "An agent asked you a question in a chat"

#: Who asked a decision that no member asked, by its source.
_DECISION_SOURCE_WORDS = {"align": "The alignment check", "auto": "An unattended run"}


def waiting_allowed(organization_id: str | None) -> bool:
    """True when ``WAITING_ORGS`` holds the org, or holds ``*``. Default OFF.

    The one reader of the flag. The org comes from the session, never from
    request input (R5). It reads ``get_settings()`` on each call, so a flag
    reload applies a change with no restart.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "waiting_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed


def _plain(value: Any, fallback: str) -> str:
    """One line of plain text: no control or format character, one space
    between words, at most ``TEXT_MAX`` characters."""
    raw = "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in str(value or ""))
    line = re.sub(r"\s+", " ", raw).strip()
    return line[:TEXT_MAX] or fallback


def _row(*, kind: str, ref: str, title: Any, waiter: Any, href: str, since: Any,
         acts: list[dict[str, Any]], title_fallback: str = "(untitled)") -> dict[str, Any]:
    return {
        "id": f"{kind}:{ref}", "kind": kind,
        "title": _plain(title, title_fallback),
        "waiter": _plain(waiter, "Somebody"),
        "href": href, "since": _iso(since), "acts": acts,
    }


def _found(rows: list[dict[str, Any]], *, total: int | None = None,
           capped: bool = False) -> Found:
    """The first ``PER_APP`` rows, the oldest first. ``total`` is the true
    number of the caller's rows when the app's read knows it."""
    shown = _oldest_first(rows)[:PER_APP]
    known = len(rows) if total is None else max(total, len(rows))
    return Found(shown, known, capped=capped or len(rows) > PER_APP)


# ── decision: one named person decides (``pm_decisions``) ──────────────────


async def _decisions(user: UserContext) -> Found:
    from gateway.routes.projects import decisions

    if not decisions.decisions_allowed(getattr(user, "organization_id", None)):
        raise SourceAbsent("DECISIONS_ORGS does not hold the org")
    # The decider's own open decisions. The app drops each one on a task
    # that the decider cannot open (V2).
    answer = await decisions.list_decisions(task_id=None, user=user)
    rows = list(answer.get("decisions") or [])
    out = []
    for row in rows:
        task_id = str(row["subject_id"])
        href = task_href(task_id)
        asked_by = str(row.get("asked_by") or "")
        waiter = row.get("asked_by_name") or (
            _who(asked_by) if "@" in asked_by
            else _DECISION_SOURCE_WORDS.get(str(row.get("source") or ""), _who(asked_by)))
        out.append(_row(kind="decision", ref=str(row["id"]), title=row.get("question"),
                        waiter=waiter, href=href, since=row.get("created_at"),
                        acts=[link("answer", "Answer", href)]))
    return _found(out, capped=len(rows) >= decisions.LIST_LIMIT)


# ── chat_ask: a chat card that waits on the member (``chat_pending_ask``) ──


async def _chat_asks(user: UserContext) -> Found:
    from gateway.routes.agent import _resolve_room
    from gateway.routes.chat import _my_waiting_asks
    from orchestrator import pending_ask

    if not pending_ask.durable_asks_enabled():
        raise SourceAbsent("CHAT_DURABLE_ASKS is off")
    org = str(getattr(user, "organization_id", None) or "").strip()
    email = str(user.email or "").strip()
    if not org or not email:
        raise SourceAbsent("no tenant or no member")
    me = email.lower()
    asks = await _my_waiting_asks(org, me)
    if asks is None:
        # A database error. An empty list would hide each question.
        raise RuntimeError("the waiting questions could not be read")
    threads = [(tid, row) for tid, row in asks.items()
               if re.fullmatch(state.SIGNAL_ID_PATTERN, f"chat:{tid}")
               and str(row.get("actor_email") or "").strip().lower() == me]
    out = []
    # One room check for each thread, so the provider checks CHAT_THREADS
    # threads at most, as the feed does.
    for tid, row in threads[:CHAT_THREADS]:
        room = await asyncio.to_thread(_resolve_room, tid, email, org)
        if room is None or not getattr(room, "can_send", False):
            continue
        href = _chat_href(tid)
        out.append(_row(
            kind="chat_ask", ref=tid, title=CHAT_ASK_TITLE,
            waiter=(row.get("newest_agent_name") or row.get("agent_name") or "An agent"),
            href=href, since=row.get("asked_at"),
            acts=[link("open-chat", "Open chat", href)]))
    return _found(out, capped=len(threads) > CHAT_THREADS)


# ── app_request: an app asks the member to approve one call ────────────────


async def _app_requests(user: UserContext) -> Found:
    from acb_auth import agent_oauth
    from gateway import app_requests
    from gateway.routes.agent_requests import list_app_requests

    # The gate of the route's own dependency (`_require_access`).
    if not agent_oauth.enabled() or not app_requests.requests_allowed(
            getattr(user, "organization_id", None)):
        raise SourceAbsent("APP_REQUESTS_ORGS does not hold the org")
    answer = await list_app_requests(user=user)
    rows = list(answer.get("rows") or [])
    more = int(answer.get("more") or 0)
    out = []
    for row in rows:
        # The card holds the server's words, from the manifest.
        card = row.get("card") if isinstance(row.get("card"), dict) else {}
        cls = row.get("class") if row.get("class") in ("B", "C") else "C"
        out.append(_row(
            kind="app_request", ref=str(row["request_id"]),
            title=card.get("title") or row.get("op"),
            waiter=card.get("client") or card.get("app_name") or "An app",
            href=APP_REQUESTS_HREF, since=row.get("created_at"),
            acts=[link("review", "Review", APP_REQUESTS_HREF, cls=cls)]))
    return _found(out, total=len(rows) + more, capped=more > 0)


# ── held_write: a write that waits for an admin (``pending_actions``) ──────


async def _held_writes(user: UserContext) -> Found:
    import action_broker

    # The broker's bounded read, which raises on a database failure. The
    # thread takes this context and its bound tenant (H-201).
    rows = await asyncio.to_thread(action_broker.read_pending, PER_APP + 1)
    out = []
    for row in rows:
        action_id = str(row["id"])
        href = approval_href(action_id)
        out.append(_row(
            kind="held_write", ref=action_id, title=_action_title(row),
            waiter=_proposer(row.get("actor")), href=href, since=row.get("created_at"),
            # Class C: to approve is an outward write, so the row opens the
            # exact payload in Approvals (D124.2).
            acts=[link("review", "Review", href, cls="C")]))
    return _found(out)


#: The providers of AP-1a. They do NOT register in ``needs.REGISTRY``.
PROVIDERS: tuple[Provider, ...] = (
    Provider("decision", ("projects",), _decisions, frozenset({"decision"}), needs=False),
    Provider("chat_ask", ("chat",), _chat_asks, frozenset({"chat_ask"}), needs=False),
    # The app request routes need no feature. The flag and agent access are
    # the gate, inside the provider, and the list is the member's own.
    Provider("app_request", (), _app_requests, frozenset({"app_request"}), needs=False),
    Provider("held_write", ("approvals",), _held_writes, frozenset({"held_write"}),
             admin=True, needs=False),
)


def _oldest_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The oldest wait first. A row with no time goes last."""
    def key(row: dict[str, Any]) -> tuple[int, float, str]:
        at = _instant(row.get("since"))
        return (1, 0.0, row["id"]) if at is None else (0, at.timestamp(), row["id"])

    return sorted(rows, key=key)


def _caller(user: UserContext) -> None:
    """404 for a signed agent, for an org off ``WAITING_ORGS``, and for a
    request with no member. 404, so a dark route says nothing."""
    if getattr(user, "agent", None) is not None or not user.email:
        raise HTTPException(status_code=404, detail="Not Found")
    if not waiting_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Not Found")


async def waiting_run(user: UserContext) -> dict[str, Any]:
    """One run of the providers: the rows, oldest first, and each source."""
    run = await run_providers(user, list(PROVIDERS), budget_s=TOTAL_BUDGET_S,
                              timeout_s=PROVIDER_TIMEOUT_S, event=EVENT)
    return {
        "items": _oldest_first(run.items),
        "sources": run.sources,
        "totals": run.totals,
        "capped": run.capped,
    }


@router.get("/waiting")
async def shell_waiting(
    direction: str = "for_you",
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """What waits on this member, in every app the member holds."""
    _caller(user)
    if direction != "for_you":
        # Checked after the flag, so an org that is off still sees 404.
        raise HTTPException(status_code=422, detail="direction must be for_you")
    return {"direction": direction, **await waiting_run(user)}


@router.get("/waiting/count")
async def shell_waiting_total(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """How many waits are addressed to this member. Each row of AP-1a is
    addressed to the caller, so the total is the caller's rows in each source
    that answered. ``capped`` names a source whose total is a floor."""
    _caller(user)
    answer = await waiting_run(user)
    total = 0
    for source in answer["totals"]:
        total += answer["totals"][source]
    return {"count": total, "capped": answer["capped"], "sources": answer["sources"]}
