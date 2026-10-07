"""GET /shell/search — the command bar's tier 1, "Find" (NS-4a).

Spec: ``project-docs/specs/navigation_shell.md`` §6.2, §6.3 and ticket NS-4a.

One route, one provider per app that has a search today. Each provider calls
its app's OWN search function on this request's member. So:

* **No new data path.** No provider writes SQL, opens a connection or builds a
  visibility predicate. The task search, the email search and the directory
  already do that, and their R8 suites already run them on a real database.
* **The app's feature gate, again, here.** ⚠️ Each app checks its feature on
  its ROUTER (``require_feature_router("projects")`` and so on), not inside the
  search function. Called from here, the function would skip that gate. So
  every provider checks ``feature:<app>`` first, with the router's own test,
  and a member without the app gets no group at all. Fence:
  ``tests/unit/test_shell_search.py``, one negative case per provider.
* **One app at a time.** The providers run one after another, never at once,
  because the pool budget is small (7 + 2 async sessions, Supavisor cap 15).
  Each has a time limit. A provider that fails, refuses or runs out of time is
  left out. It never fails the bar.

The answer is plain words (§6.7 rule 5): each item says what it is, where it
opens, and nothing else. No id reaches the member's eye. The id travels only
inside ``href``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

from acb_auth import UserContext, get_current_user
from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/shell", tags=["shell"])

#: Items per app. The bar shows a handful, and "Show all" goes to the app.
PER_APP = 5
#: Seconds one provider may take.
PROVIDER_TIMEOUT_S = 1.0
#: Seconds for the whole answer, all providers together. ⚠️ One deadline, not
#: three: a request whose words are already stale must not hold three pool
#: sessions for three seconds (security review of NS-4a, 2026-10-08). Each
#: provider gets what is left. The spec's §6.3 records this ceiling.
TOTAL_BUDGET_S = 1.5
#: Shorter words match too much and say too little.
MIN_CHARS = 2


def _item(kind: str, title: str, hint: str, href: str) -> dict[str, str]:
    return {"kind": kind, "title": (title or "").strip() or "(untitled)", "hint": hint, "href": href}


async def _tasks(user: UserContext, q: str) -> list[dict[str, str]]:
    from gateway.routes.projects.search import search_tasks

    answer = await search_tasks(
        q=q, limit=PER_APP, exclude_relatives_of=None, include_triage=False, user=user,
    )
    return [
        _item(
            "task",
            r.get("title") or "",
            f"Task · {r.get('project_name') or 'Projects'}",
            f"/projects?task={quote(str(r['id']))}",
        )
        for r in (answer.get("rows") or [])[:PER_APP]
    ]


async def _email(user: UserContext, q: str) -> list[dict[str, str]]:
    from gateway.routes.email.transport.search import search_messages

    # ⚠️ Every parameter, by name. The route declares them with `Query(...)`
    # defaults, and a direct call that left one out would pass the `Query`
    # object itself. `test_shell_search.py` fails if the route gains one.
    answer = await search_messages(
        q=q, account_id=None, folder="all", label=None, labels=None,
        uncategorized=False, from_addr=None, to_addr=None,
        received_after=None, received_before=None, is_read=None,
        is_starred=None, has_attachments=None, sender_category=None,
        importance=None, hybrid=False, light=True, page=1, page_size=PER_APP,
        user=user,
    )
    out = []
    for m in (answer.get("emails") or [])[:PER_APP]:
        row = m if isinstance(m, dict) else m.model_dump()
        sender = row.get("from_address") or {}
        who = sender.get("name") or sender.get("email") or "someone"
        out.append(_item(
            "email", row.get("subject") or "(no subject)", f"Email · from {who}",
            f"/email?email={quote(str(row['id']))}",
        ))
    return out


async def _people(user: UserContext, q: str) -> list[dict[str, str]]:
    from gateway.routes.people.directory import list_directory

    answer = await list_directory(
        user=user, q=q, department=None, team=None, status=None, skill=None,
        has_capacity=False,
    )
    out = []
    for p in (answer.rows or [])[:PER_APP]:
        what = p.get("title") or p.get("role") or p.get("department") or "Colleague"
        out.append(_item(
            "person", p.get("preferred_name") or p.get("name") or "",
            f"Person · {what}", f"/people/{quote(str(p['id']))}",
        ))
    return out


#: app key → (the feature its router demands, the group's name, the provider).
PROVIDERS: dict[str, tuple[str, str, Callable[[UserContext, str], Awaitable[list[dict[str, str]]]]]] = {
    "tasks": ("projects", "Tasks", _tasks),
    "email": ("email", "Email", _email),
    "people": ("people", "People", _people),
}

#: The pane each scope token names (`?scope=` from the bar's "in Email").
SCOPE_APPS: dict[str, str] = {
    "/email": "email",
    "/projects": "tasks",
    "/tasks": "tasks",
    "/people": "people",
}


def _order(scope: str | None) -> list[str]:
    """The app the member is in goes first (§6.4 rule 3)."""
    first = SCOPE_APPS.get(scope or "")
    keys = list(PROVIDERS)
    return [first, *[k for k in keys if k != first]] if first else keys


@router.get("/search")
async def shell_search(
    q: str = "",
    scope: str | None = None,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Records that match the words, grouped by app, from the apps this member holds."""
    words = " ".join((q or "").split())[:200]
    if not user.email:
        raise HTTPException(status_code=401, detail="Authentication required")
    if len(words) < MIN_CHARS:
        return {"query": words, "groups": []}

    groups: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + TOTAL_BUDGET_S
    for key in _order(scope):
        feature, label, provider = PROVIDERS[key]
        # The gate the app's router would have applied (see the module note).
        if not user.has_permission(f"feature:{feature}"):
            continue
        left = deadline - loop.time()
        if left <= 0.05:
            break
        try:
            items = await asyncio.wait_for(provider(user, words), min(PROVIDER_TIMEOUT_S, left))
        except HTTPException:
            # The app's own refusal: a query too short for it, a scope it
            # will not serve. Its group is left out.
            continue
        except TimeoutError:
            logger.info("shell.search provider timed out", extra={"provider": key})
            continue
        except Exception as exc:
            # The error's TYPE only. A database error's text can carry the
            # query's parameters, which hold the member's words and address.
            logger.warning(
                "shell.search provider failed",
                extra={"provider": key, "error": type(exc).__name__},
            )
            continue
        if items:
            groups.append({"app": key, "label": label, "items": items})
    return {"query": words, "groups": groups}
