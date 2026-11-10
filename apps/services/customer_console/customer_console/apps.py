"""Apps an organization can have beside Metorite: today, Aroh. Pure rules only.

Spec: ``project-docs/specs/customer_console.md`` §6 "CP-APP · The Aroh app for
an organization" (Metorite task 1105). Migration ``038_app_seats.sql``.

**What an app is here.** The operator gives an organization the Aroh app with
a number of seats. Aroh runs on its own box, which holds ONE deployment key
with ``{resolve, seat_admin, serve}``. A member of the organization signs in
to Aroh with Metorite, and Aroh takes a seat for them at once. Aroh's AI use
is metered by the Router and charged to the organization's own credits.

**Why an app reuses the seat tables.** An app seat is a ``seat_assignment``
row on the plan whose slug is the app's name, and the count is the
``seat_grant`` sum for that plan. So :mod:`customer_console.seats` decides the
cap, :func:`customer_console.store.lock_seat_capacity` serialises it, and the
partial unique index keeps one seat for each person. A second seat model for
apps would be a second answer to "how many seats are free".

This module holds the names and the decisions that have no SQL in them, so a
test can read them without a database. ``store.py`` fetches, ``main.py`` and
``auth.py`` apply.
"""
from __future__ import annotations

__all__ = [
    "APP_DEFAULT_BASE_URLS",
    "APP_KEY_CAPABILITIES",
    "APP_NAMES",
    "APP_NOT_ENTITLED",
    "APP_SEAT_SOURCE",
    "AROH_APP",
    "KNOWN_APPS",
    "app_attribution",
    "is_app_plan",
    "may_act_on_app_seat",
    "not_entitled_detail",
]

#: The one app today. The string is the app's name, the slug of its seat plan
#: (``plan_catalog.slug``) and the value of ``deployment.app`` on its box.
AROH_APP = "aroh"

#: Every app the Console knows. A plan or a ``deployment.app`` outside this
#: set is refused at the door with a 400, before any SQL runs.
KNOWN_APPS: frozenset[str] = frozenset({AROH_APP})

#: The words the Operator Console shows for each app.
APP_NAMES: dict[str, str] = {AROH_APP: "Aroh"}

#: Where each app's hub answers when the operator has not typed another URL
#: ("Connect Aroh", owner request 2026-10-30).
APP_DEFAULT_BASE_URLS: dict[str, str] = {AROH_APP: "https://aroh.dev.metorite.com"}

#: The capability set of the deployment key an app box holds, and no more.
#: Aroh: ``resolve`` at sign-in, ``seat_admin`` for the member's own seat,
#: ``serve`` for the Router. Plain strings, because this module imports
#: nothing: ``test_customer_console_apps.py`` holds them equal to the names in
#: ``auth.py``.
APP_KEY_CAPABILITIES: dict[str, tuple[str, ...]] = {
    AROH_APP: ("resolve", "seat_admin", "serve"),
}

#: ``seat_assignment.source`` for a seat that an app door wrote (migration
#: 038 adds it to the CHECK). It says WHICH door put the seat there.
APP_SEAT_SOURCE = "app"

#: The refusal reason when a person's organization does not have the app, or
#: has it turned off. Named once: the Aroh hub branches on this string.
APP_NOT_ENTITLED = "app_not_entitled"

#: The membership roles that may take or free an app seat for ANOTHER member.
#: A member may always take or free their OWN seat: that is the whole flow,
#: because Aroh takes a seat at the first sign-in with no second approval.
_APP_SEAT_ADMIN_ROLES: frozenset[str] = frozenset({"owner", "admin"})


def is_app_plan(plan_slug: str | None) -> bool:
    """True when this plan is the seat plan of an app."""
    return plan_slug in KNOWN_APPS


def not_entitled_detail(app: str) -> dict[str, str]:
    """The ``detail`` body of the 403 that says "this organization has no app".

    A dict, not a sentence, so a caller branches on ``reason`` and never on
    words. The same shape as the seat cap's ``{"reason": ...}`` detail.
    """
    return {"reason": APP_NOT_ENTITLED, "app": app}


def may_act_on_app_seat(
    *,
    actor_identity_id: str,
    target_identity_id: str,
    actor_role: str | None,
    actor_status: str | None,
) -> bool:
    """May this actor take or free this target's app seat?

    * A member acts on their OWN seat with any role. The box vouches for the
      actor, as it does at sign-in.
    * An ACTIVE owner or admin acts on any member's seat.
    * Nobody else does.

    The identities are compared, never the emails, so two spellings of one
    address cannot pass as two people.
    """
    if actor_identity_id == target_identity_id:
        return True
    return actor_status == "active" and actor_role in _APP_SEAT_ADMIN_ROLES


def app_attribution(
    app: str, *, agent: str | None, module_slug: str | None
) -> tuple[str, str]:
    """The ``(agent, module_slug)`` a call through an app box is metered under.

    A header the box sends wins. A header it leaves out takes the app's name,
    so each call of the app is attributable in the meter (``X-CC-Agent: aroh``)
    and shows as its own app in the usage breakdown, even when the box sends
    neither header. An empty or blank header counts as absent.
    """
    agent_out = (agent or "").strip() or app
    module_out = (module_slug or "").strip() or app
    return agent_out, module_out
