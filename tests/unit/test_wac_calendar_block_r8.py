"""WS-47 WAC-4b, R8: a capture with a block on a REAL Postgres.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §14.9.

The WhatsApp bot blocks time with ``create_personal_task``, which sends
``POST /projects/my/tasks`` with ``scheduled_start`` and ``scheduled_end``.
A fake agrees with any SQL. So this suite runs the REAL route on the
phase-4-promoted catalog, as the NON-privileged role with RLS forced
(``_as_member``), and reads the rows back:

* The block lands on the member's own overlay row, in the same transaction
  as the task, as NEXT and clarified (a scheduled task is not an inbox
  thought).
* ``GET /projects/my/calendar`` answers it in the block's window, so the
  Calendar shows it.
* A block that ends before it starts is a 422 with no row. Migration 187's
  CHECK would answer a 500 for it.
* The batch capture takes the same block.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from fastapi import HTTPException
from sqlalchemy import text

from tests.unit.test_email_keep_separate import (  # noqa: F401  (fixtures by name)
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ME = f"wac4b-{TAG}@wac.test"
START = "2026-10-16T08:30:00+05:30"
END = "2026-10-16T10:30:00+05:30"


@pytest.fixture(scope="module")
def member(promoted):  # noqa: F811
    p = promoted
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
            {"e": ME, "o": p.org_b})
    return p.org_b


def _user(org: str) -> UserContext:
    return UserContext(email=ME, role=UserRole.EMPLOYEE, organization_id=org,
                       access=EffectiveAccess(role_granted=frozenset(
                           {"feature:projects", "feature:tasks"})))


def _overlay(p, task_id: str):
    with p.admin_engine.begin() as c:
        return c.execute(text(
            "SELECT scheduled_start, scheduled_end, disposition, clarified_at, "
            "organization_id::text AS org FROM pm_task_personal "
            "WHERE task_id = CAST(:t AS uuid) AND member_email = :e"),
            {"t": task_id, "e": ME}).mappings().one()


def _tasks_named(p, title: str) -> int:
    with p.admin_engine.begin() as c:
        return c.execute(text("SELECT count(*) FROM pm_tasks WHERE title = :t"),
                         {"t": title}).scalar_one()


async def test_a_capture_with_a_block_lands_on_my_calendar(promoted, member) -> None:  # noqa: F811
    from gateway.routes.projects import personal

    title = f"Sharath and Mr. Manoj {TAG}"
    async with _as_member(promoted, member):
        task = await personal.capture(personal.CaptureIn(
            title=title, scheduled_start=START, scheduled_end=END), user=_user(member))
        window = await personal.my_calendar(
            start="2026-10-16T00:00:00+05:30", end="2026-10-17T00:00:00+05:30",
            user=_user(member))

    row = _overlay(promoted, task["id"])
    assert row["scheduled_start"] == datetime(2026, 10, 16, 3, 0, tzinfo=UTC)
    assert row["scheduled_end"] == datetime(2026, 10, 16, 5, 0, tzinfo=UTC)
    assert row["disposition"] == "NEXT" and row["clarified_at"] is not None
    assert row["org"] == member, "the overlay row carries the tenant"
    (shown,) = [r for r in window.rows if r["id"] == task["id"]]
    assert shown["scheduled_start"].startswith("2026-10-16T03:00")


async def test_a_block_that_ends_before_it_starts_is_a_422_and_writes_nothing(
        promoted, member) -> None:  # noqa: F811
    from gateway.routes.projects import personal

    title = f"Backwards {TAG}"
    async with _as_member(promoted, member):
        with pytest.raises(HTTPException) as refused:
            await personal.capture(personal.CaptureIn(
                title=title, scheduled_start=END, scheduled_end=START), user=_user(member))
    assert refused.value.status_code == 422
    assert _tasks_named(promoted, title) == 0


async def test_the_batch_capture_takes_the_same_block(promoted, member) -> None:  # noqa: F811
    from gateway.routes.projects import personal

    async with _as_member(promoted, member):
        out = await personal.capture_batch(personal.BatchCaptureIn(items=[
            personal.CaptureIn(title=f"Manani visit {TAG}", scheduled_start=START,
                               scheduled_end=END),
            personal.CaptureIn(title=f"No block {TAG}"),
        ]), user=_user(member))
    blocked, plain = out.rows
    assert _overlay(promoted, blocked["id"])["disposition"] == "NEXT"
    assert _overlay(promoted, plain["id"])["disposition"] == "INBOX"
    assert _overlay(promoted, plain["id"])["scheduled_start"] is None
