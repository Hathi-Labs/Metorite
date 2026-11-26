"""``setup_status(org, member)``: the one status function of the setup steps (OB-1a).

Spec: ``project-docs/specs/onboarding.md`` §4, §5, §7.1 and §7.2.
``personalization.md`` §12.5 names the same function. PZ-16 adds the profile
items to the same registry later. A second "setup status" or "onboarding
status" function is a defect. Fence: ``tests/unit/test_setup_status_source.py``.

**One registry row per step.** A :class:`Step` holds the id, the audience (a
permission and a feature, both optional), the app, and the "done when" query.
Each query reads the REAL row, under the bound tenant, and gives one row:
``(n, done_at)``. A step is ``done`` when ``n`` is above 0. A click never
makes a step done.

**The answer per step** is ``done``, ``open`` or ``skipped``:

* ``done`` wins. A member who skipped a step and then did it reads ``done``.
* ``skipped`` comes from ``user_settings.my_day_prefs`` key ``setup``, key
  ``skipped`` (spec §7.2). This module only reads it. OB-1b writes it.
* ``done_at`` is the time of the real row, or None where the row has no
  timestamp that we can read.

**Tenancy (R5).** The session is ``tenant_session(org)``, so FORCE row level
security binds each query. Each statement also names the bound tenant, so a
role that bypasses RLS still reads one org only. The caller gives the org and
the member from its own session, and never from request input.

**The audience.** A step shows to a member when the member holds its
permission and its feature. With no checks given, every step shows. The route
passes the session's own checks. ``connect_calendar`` waits on CE-10
(``calendar_events_reminders.md``) and is not in the registry yet.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_common.db import tenant_session
from sqlalchemy import text

#: The tenant that the session binds. Each statement names it beside RLS.
_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: The three answers of a step.
DONE, OPEN, SKIPPED = "done", "open", "skipped"

#: The key of ``my_day_prefs`` that holds the card's state (spec §7.2).
SETUP_PREFS_KEY = "setup"


@dataclass(frozen=True)
class Step:
    """One row of the registry."""

    id: str
    #: The app that the step's act opens (a nav slug).
    app: str
    #: The permission the member needs to see the step, or None.
    permission: str | None
    #: The feature the member needs to see the step, or None.
    feature: str | None
    #: "Done when": a statement that gives one row ``(n, done_at)``. It takes
    #: ``:m``, the member's address, and names the bound tenant itself.
    done_sql: str


#: The member's own settings rows, in any spelling of the address (a row keys
#: on the spelling of the session that wrote it).
_MY_SETTINGS = (
    f"FROM user_settings WHERE organization_id = {_BOUND_ORG} "
    "AND lower(user_id) = lower(:m)"
)

#: The steps, in the order of the card (spec §4, with D153's order in §12.3).
REGISTRY: tuple[Step, ...] = (
    Step(
        "invite_team", "admin", "admin:members:invite", None,
        # A member other than this one, invited or active, in the bound org.
        "SELECT count(*) AS n, min(COALESCE(invited_at, joined_at, created_at)) AS done_at "
        f"FROM app_user WHERE organization_id = {_BOUND_ORG} "
        "AND status IN ('invited', 'active') AND lower(email) <> lower(:m)",
    ),
    Step(
        "connect_email", "email", None, "email",
        # The member's own mailbox rows. A shared grant is not a connection.
        "SELECT count(*) AS n, min(created_at) AS done_at "
        f"FROM email_accounts WHERE organization_id = {_BOUND_ORG} "
        "AND lower(user_id) = lower(:m)",
    ),
    Step(
        "first_project", "projects", None, "projects",
        # A project of the org. A personal project has `personal_owner` set.
        "SELECT count(*) AS n, min(created_at) AS done_at "
        f"FROM pm_projects WHERE organization_id = {_BOUND_ORG} "
        "AND personal_owner IS NULL",
    ),
    Step(
        "ask_assistant", "chat", None, "chat",
        "SELECT count(*) AS n, min(stamp) AS done_at FROM ("
        "SELECT NULLIF(shell_prefs->>'chat_first_message_at', '') AS stamp "
        f"{_MY_SETTINGS}) s WHERE stamp IS NOT NULL",
    ),
    Step(
        "org_profile", "settings", "admin:settings:manage", None,
        # The org's 'branding' row holds a logo (`routes/settings.py`).
        "SELECT count(*) AS n, min(updated_at) AS done_at "
        f"FROM org_settings WHERE organization_id = {_BOUND_ORG} "
        "AND key = 'branding' AND jsonb_typeof(value->'logo') = 'object'",
    ),
    Step(
        "phone_push", "settings", None, None,
        "SELECT count(*) AS n, min(created_at) AS done_at "
        f"FROM push_subscriptions WHERE organization_id = {_BOUND_ORG} "
        "AND lower(member_email) = lower(:m)",
    ),
    Step(
        "know_me", "chat", None, "chat",
        "SELECT count(*) AS n, min(stamp) AS done_at FROM ("
        "SELECT NULLIF(assistant_profile->'interview'->>'started_at', '') AS stamp "
        f"{_MY_SETTINGS}) s WHERE stamp IS NOT NULL",
    ),
)

#: The skip marks of the member (spec §7.2). ``->`` gives NULL on a value
#: that is not an object, so a stray scalar reads as no mark.
_SKIPPED_SQL = (
    f"SELECT my_day_prefs->'{SETUP_PREFS_KEY}'->'skipped' AS skipped {_MY_SETTINGS}"
)


def _iso(value: Any) -> str | None:
    """An aware ISO time for *value*, or None when it reads as no time.

    A column gives a ``datetime``. A JSON stamp gives text. A stamp that does
    not parse gives None, and the step is still ``done``.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return datetime.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        return None


def _skipped_ids(rows: list[Any]) -> set[str]:
    """The step ids that the member skipped, over every spelling row."""
    out: set[str] = set()
    for value in rows:
        if isinstance(value, dict):
            out.update(k for k, v in value.items() if isinstance(k, str) and v)
    return out


def visible_steps(
    *,
    permission: Callable[[str], bool] | None = None,
    feature: Callable[[str], bool] | None = None,
) -> list[Step]:
    """The steps whose audience holds. A missing check lets the step through."""
    return [
        s for s in REGISTRY
        if (s.permission is None or permission is None or permission(s.permission))
        and (s.feature is None or feature is None or feature(s.feature))
    ]


async def setup_status(
    org: str,
    member: str,
    *,
    permission: Callable[[str], bool] | None = None,
    feature: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    """The state of each setup step of *member* in *org*.

    Each item is ``{id, app, status, done_at}``. *org* and *member* come from
    the caller's session (R5). *permission* and *feature* are the session's
    own checks, for example ``user.has_permission`` and ``user.can_use_feature``.
    """
    steps = visible_steps(permission=permission, feature=feature)
    answers: list[dict[str, Any]] = []
    async with tenant_session(org) as db:
        marks = _skipped_ids([
            r[0] for r in (await db.execute(text(_SKIPPED_SQL), {"m": member})).all()
        ])
        for step in steps:
            row = (await db.execute(text(step.done_sql), {"m": member})).first()
            n, done_at = (row[0], row[1]) if row else (0, None)
            if n and int(n) > 0:
                status = DONE
            elif step.id in marks:
                status = SKIPPED
            else:
                status = OPEN
            answers.append({
                "id": step.id,
                "app": step.app,
                "status": status,
                "done_at": _iso(done_at) if status == DONE else None,
            })
    return answers
