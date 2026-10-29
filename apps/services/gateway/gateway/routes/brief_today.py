"""``GET /brief/today?kind=brief|closeout`` — the member's stored brief of
today (WS-BRIEF BR-2c-1, ``core_brief.md`` §5 item 3).

The route answers the run and its claims as the store holds them. It runs
no plan, reads no feed and calls no model. It enqueues no job: no route
starts a brief run (§4.1 item 4).

**The answers.**

* **200** ``{kind, local_date, state, lead, lead_source, ready_at,
  claims}``. Each claim is ``{position, section, text, source_app,
  source_kind, href}``, in position order. ``text`` and ``href`` are the
  stored strings as written. Code built each ``href`` at gather time.
* **404** when ``BRIEF_IN_APP`` is off for the org (``flags.in_app``). The
  dark route looks like no route.
* **422** for a ``kind`` that is not ``brief`` or ``closeout``.
* **404** with a plain detail when the member has no run today, or when the
  run is ``planned``, ``gathered``, ``phrasing``, ``empty`` or ``failed``.

**Delivered.** The FIRST read that serves a ``ready`` run moves it to
``delivered`` (``store.set_state`` with ``from_state="ready"``), and the
move stamps ``delivered_at``. The move runs in the same request, after the
read, in its own transaction. A lost guard (another read delivered it
first) still serves the run. A ``delivered`` run serves unchanged, and its
stamp stays. A failed stamp is logged, and the read still serves: the next
read stamps it. A render of the card sets nothing, because only this read
moves the state.

**Scope** (R5, contract R11). The member is ``user.email`` of the session.
The tenant is the bound organization of the session (``gateway.db.
tenant_session``). The request names no member, no org and no date. The
local date is the member's own (``chat_brief._local_today``). The store
scopes each statement to the member and the bound org, so the run of
another member or of another org is not found, and the answer is 404.

**The gate.** ``feature:chat``, as the chat's first turn (``routes/
chat_brief.py``) and Reach have it: the brief is the assistant's word to
the member.

Fences: ``tests/unit/test_brief_today.py`` (pure) and
``tests/unit/test_brief_today_r8.py`` (the real database).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router
from acb_common import get_logger
from fastapi import APIRouter, Depends, HTTPException, Query
from gateway.brief import flags, store

_log = get_logger("gateway.brief_today")

router = APIRouter(
    prefix="/brief", tags=["brief"],
    dependencies=[require_feature_router("chat")],
)

#: The states that the route serves. Every other state answers 404.
SERVED_STATES: frozenset[str] = frozenset({"ready", "delivered"})
#: The detail of a day with no brief to serve.
NO_BRIEF = "No brief for today."


def _now() -> datetime:
    """The route's one clock. The R8 suite pins it."""
    return datetime.now(UTC)


def run_body(run: store.Run, claims: list[store.Claim], *, state: str) -> dict[str, Any]:
    """The 200 body of *run* and *claims*, with the state after the read.
    Pure. The text and the href of each claim are the stored strings,
    unchanged."""
    return {
        "kind": run.kind,
        "local_date": run.local_date.isoformat(),
        "state": state,
        "lead": run.lead,
        "lead_source": run.lead_source,
        "ready_at": run.ready_at.isoformat() if run.ready_at is not None else None,
        "claims": [
            {"position": c.position, "section": c.section, "text": c.text,
             "source_app": c.source_app, "source_kind": c.source_kind, "href": c.href}
            for c in sorted(claims, key=lambda c: c.position)
        ],
    }


async def _read(member: str, kind: str, local_date: Any) -> tuple[store.Run, list[store.Claim]] | None:
    """The served run of *member* and its claims, or None."""
    from gateway.db import tenant_session

    async with tenant_session() as db:
        run = await store.read_run(db, member, kind=kind, local_date=local_date)
        if run is None or run.state not in SERVED_STATES:
            return None
        return run, await store.read_claims(db, member, run.id)


async def _deliver(member: str, run: store.Run, now: datetime) -> str:
    """Move a ``ready`` run to ``delivered``, guarded. Answers the state
    after the move. A lost guard means that another read delivered the run
    first, so the answer is ``delivered``. A failed stamp is logged, the
    answer stays ``ready``, and the caller still serves the run."""
    from gateway.db import tenant_session

    try:
        async with tenant_session() as db:
            moved = await store.set_state(db, member, run.id, "delivered", now=now,
                                          from_state="ready")
    except Exception as exc:
        _log.warning("brief.deliver_failed", kind=run.kind, error=type(exc).__name__)
        return "ready"
    _log.info("brief.delivered" if moved else "brief.deliver_lost", kind=run.kind)
    return "delivered"


@router.get("/today", summary="The member's stored brief of today (BR-2c-1)")
async def get_today(
    kind: str = Query("brief"),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """See the module docstring. 200 with the run and its claims, else 404."""
    org = str(getattr(user, "organization_id", None) or "")
    member = (user.email or "").strip()
    if not org or not member or not flags.in_app(org):
        raise HTTPException(status_code=404, detail="Not Found")
    if kind not in store.KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {list(store.KINDS)}")

    from gateway.routes.chat_brief import _local_today

    now = _now()
    today = await _local_today(org, user, now)
    found = await _read(member, kind, today)
    if found is None:
        raise HTTPException(status_code=404, detail=NO_BRIEF)
    run, claims = found
    state = await _deliver(member, run, now) if run.state == "ready" else run.state
    return run_body(run, claims, state=state)
