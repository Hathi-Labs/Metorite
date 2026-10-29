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
* **404** with a plain detail when the member has no run to serve, or when
  the newest run is ``planned``, ``gathered``, ``phrasing``, ``empty`` or
  ``failed``. ``kind=closeout`` answers this 404 until BR-2b writes a
  close-out run.

**A pure read** (``core_brief.md`` §5). The route writes nothing. It
serves a ``ready`` run and a ``delivered`` run alike, and ``state`` in the
body is the stored state. ``delivered`` means that the run is on an in-app
surface. The run sets it (``brief.run``), and this read never does. A
future "seen" signal is a separate ``seen_at``, which only an explicit POST
from the card writes. A GET never writes it.

**Scope** (R5, contract R11). The member is ``user.email`` of the session.
The tenant is the bound organization of the session (``gateway.db.
tenant_session``). The request names no member, no org and no date. The
store scopes each statement to the member and the bound org, so the run of
another member or of another org is not found, and the answer is 404.

**The date** (:func:`brief_local_date`, :func:`read_today`). A run's
``local_date`` is the member's CALENDAR date in the planner's zone, never
the feed day (``planner.slot``). The zone is ``planner.member_zone`` of the
member's ``Day`` (``my_day_loop._read_day``) and the org's default zone.
``Day.feed_day`` moves with the day-start hour, so at 07:30 with a 09:00
start it is still yesterday, and a read by it would miss the brief.

The read takes the newest run (``store.read_latest_run``) whose ``slot_at``
has passed, dated today or the day before by that zone at the time of the
read. Two cases need the day before. The member changed the zone after the
run, so the run has the date of the old zone. Or the brief time is near
midnight, and the planner's look-back wrote the run on the day before. The
route serves the newest run only. So an ``empty`` or ``failed`` run of
today is 404, and the run of the day before does not come back.

**The gate.** ``feature:chat``, as the chat's first turn (``routes/
chat_brief.py``) and Reach have it: the brief is the assistant's word to
the member.

Fences: ``tests/unit/test_brief_today.py`` (pure) and
``tests/unit/test_brief_today_r8.py`` (the real database).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router
from fastapi import APIRouter, Depends, HTTPException, Query
from gateway.brief import flags, planner, store

router = APIRouter(
    prefix="/brief", tags=["brief"],
    dependencies=[require_feature_router("chat")],
)

#: The states that the route serves. Every other state answers 404.
SERVED_STATES: frozenset[str] = frozenset({"ready", "delivered"})
#: The detail of a day with no brief to serve.
NO_BRIEF = "No brief for today."
#: How far back the read looks, in calendar days (module docstring, "The
#: date").
LOOK_BACK_DAYS = 1


def _now() -> datetime:
    """The route's one clock. The R8 suite pins it."""
    return datetime.now(UTC)


def run_body(run: store.Run, claims: list[store.Claim]) -> dict[str, Any]:
    """The 200 body of *run* and *claims*. Pure. ``state`` is the stored
    state. The text and the href of each claim are the stored strings,
    unchanged."""
    return {
        "kind": run.kind,
        "local_date": run.local_date.isoformat(),
        "state": run.state,
        "lead": run.lead,
        "lead_source": run.lead_source,
        "ready_at": run.ready_at.isoformat() if run.ready_at is not None else None,
        "claims": [
            {"position": c.position, "section": c.section, "text": c.text,
             "source_app": c.source_app, "source_kind": c.source_kind, "href": c.href}
            for c in sorted(claims, key=lambda c: c.position)
        ],
    }


async def brief_local_date(org: str, user: UserContext, now: datetime) -> date:
    """The member's calendar date now, in the zone that the planner gives
    the member (``planner.member_zone`` of the member's ``Day`` and the
    org's default zone). The planner writes a run on the date of this zone
    at the slot."""
    from gateway import my_day_loop
    from gateway.db import tenant_session

    day = await my_day_loop._read_day(org, user)
    async with tenant_session() as db:
        org_zone = await planner._org_zone(db)
    return now.astimezone(planner.member_zone(day.tz, org_zone)).date()


async def read_today(org: str, user: UserContext, member: str, kind: str,
                     now: datetime) -> tuple[store.Run, list[store.Claim]] | None:
    """The run of *member* to serve now, with its claims, or None. A pure
    read, in the bound tenant.

    The run is the newest run of *kind* whose slot has passed, dated today
    or the day before by :func:`brief_local_date` (module docstring, "The
    date"). It is served only when it is ``ready`` or ``delivered``. The
    chat's first turn reads through this function too (``chat_brief``).
    """
    from gateway.db import tenant_session

    today = await brief_local_date(org, user, now)
    async with tenant_session() as db:
        run = await store.read_latest_run(
            db, member, kind=kind, first_date=today - timedelta(days=LOOK_BACK_DAYS),
            last_date=today, now=now)
        if run is None or run.state not in SERVED_STATES:
            return None
        return run, await store.read_claims(db, member, run.id)


@router.get("/today", summary="The member's stored brief of today (BR-2c-1)")
async def get_today(
    kind: str = Query("brief"),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """See the module docstring. 200 with the run and its claims, else 404.
    ``kind=closeout`` answers 404 until BR-2b writes a close-out run."""
    org = str(getattr(user, "organization_id", None) or "")
    member = (user.email or "").strip()
    if not org or not member or not flags.in_app(org):
        raise HTTPException(status_code=404, detail="Not Found")
    if kind not in store.KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {list(store.KINDS)}")

    found = await read_today(org, user, member, kind, _now())
    if found is None:
        raise HTTPException(status_code=404, detail=NO_BRIEF)
    run, claims = found
    return run_body(run, claims)
