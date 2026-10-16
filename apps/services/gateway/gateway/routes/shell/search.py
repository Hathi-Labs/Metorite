"""GET /shell/search — the command bar's tier 1, "Find" (NS-4a).

Spec: ``project-docs/specs/navigation_shell.md`` §6.2, §6.3 and ticket NS-4a.

One route, one provider per app that has a search today. Each provider calls
its app's OWN search function on this request's member. ``PROVIDERS`` is a
projection of the company retrieval registry (``gateway/retrieval/sources.py``,
WS-61 KR-1): the source does the call, and the bar keeps only the four keys of
an item. So:

* **No new data path.** No provider writes SQL, opens a connection or builds a
  visibility predicate. The task search, the email search, the directory and
  the CRM's ``search_records`` already do that, and their R8 suites run them
  on a real database (the CRM's is ``test_shell_search_crm_r8.py``).
* **The app's feature gate, again, here.** ⚠️ Each app checks its feature on
  its ROUTER (``require_feature_router("projects")`` and so on), not inside the
  search function. Called from here, the function would skip that gate. So
  every provider checks ``feature:<app>`` first, with the router's own test,
  and a member without the app gets no group at all. Fence:
  ``tests/unit/test_shell_search.py``, one negative case per provider. The
  CRM router has two more gates, the kill switch and the access rule, and
  the CRM source applies both (WS-53 CRM-X2).
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

from acb_auth import UserContext, get_current_user
from fastapi import APIRouter, Depends, HTTPException
from gateway.retrieval.sources import REGISTRY

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


#: The apps of the bar, in the bar's order. Artifacts and WhatsApp are sources
#: of the registry, and they are not in the bar (``knowledge_base.md`` §20.3).
BAR_APPS: tuple[str, ...] = ("tasks", "email", "people", "crm")


def _projected(key: str) -> Callable[[UserContext, str], Awaitable[list[dict[str, str]]]]:
    """The bar's provider for one source: its hits, as the bar's four keys.

    ``PER_APP`` is read at call time, so a test that changes it changes the
    call. The source checks its feature again, which costs nothing.
    """

    async def provider(user: UserContext, q: str) -> list[dict[str, str]]:
        hits = await REGISTRY[key].find(user, q, PER_APP)
        return [_item(h.kind, h.title, h.hint, h.href) for h in hits]

    provider.__name__ = f"_{key}"
    return provider


#: app key → (the feature its router demands, the group's name, the provider).
PROVIDERS: dict[str, tuple[str, str, Callable[[UserContext, str], Awaitable[list[dict[str, str]]]]]] = {
    key: (REGISTRY[key].feature, REGISTRY[key].label, _projected(key)) for key in BAR_APPS
}

#: The pane each scope token names (`?scope=` from the bar's "in Email").
SCOPE_APPS: dict[str, str] = {
    "/email": "email",
    "/projects": "tasks",
    "/tasks": "tasks",
    "/people": "people",
    "/crm": "crm",
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
