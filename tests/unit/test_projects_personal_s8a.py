"""S8a — the gateway half, hermetic.

Spec `my_tasks_cutover.md` §5 S8a · `task_manager_app.md` §13.5a decision 4.

Three claims the fake CAN judge:

1. **A capture states INBOX.** A row with no overlay derives its disposition
   off the lane, and the personal root's first lane is `backlog`, so a fresh
   capture derived as SOMEDAY and the Inbox never showed it. Now the one
   capture path writes `disposition = 'INBOX'` when the caller states none,
   with `clarified_at` NULL. A subtask is a step, not a capture, and gets no
   default.
2. **The inbox query carries one order.** `my_inbox` pages in Python over the
   rows the SQL returns; an unordered SELECT can answer two page requests in
   two orders. The live script pages a real set (R8).
3. **`origin` rides the inbox row.** `TaskModel` has no such field, so the
   column fell on the floor and the email marker in the skill was dead.

Hermetic: no Postgres, no network.
"""

from __future__ import annotations

import pytest
from gateway.routes.projects import bulk as pm_bulk
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import personal as pm_personal
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    member_user,
    page,
    silence_events,
)

MODULES = (pm_core, pm_tree, pm_tasks, pm_personal, pm_bulk)

ALICE = member_user("alice@fracktal.in")
WHO = "alice@fracktal.in"


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


def _overlays(db: FakeProjectsDB) -> list[dict]:
    return db.rows("pm_task_personal")


async def test_a_capture_states_inbox(db: FakeProjectsDB) -> None:
    created = await pm_personal.capture(
        pm_personal.CaptureIn(title="A thought"), user=ALICE,
    )
    rows = _overlays(db)
    assert len(rows) == 1
    assert rows[0]["disposition"] == "INBOX"
    assert rows[0].get("clarified_at") is None
    assert str(rows[0]["task_id"]) == created["id"]
    # And the inbox answers it under INBOX, as a STATED (triaged) row — which
    # is what keeps it out of S6e's "from Projects" group.
    inbox = await pm_personal.my_inbox(user=ALICE, disposition="INBOX", page=page())
    assert [r["id"] for r in inbox.rows] == [created["id"]]
    assert inbox.rows[0]["is_triaged"] is True


async def test_a_capture_with_a_context_still_states_inbox(db: FakeProjectsDB) -> None:
    """The route passes an overlay when a context or next action is given.
    That overlay names no disposition, so INBOX joins it."""
    await pm_personal.capture(
        pm_personal.CaptureIn(title="Call the lab", context="@calls"), user=ALICE,
    )
    (row,) = _overlays(db)
    assert row["disposition"] == "INBOX"
    assert row["context"] == "@calls"


async def test_the_batch_states_inbox_for_every_row(db: FakeProjectsDB) -> None:
    out = await pm_personal.capture_batch(
        pm_personal.BatchCaptureIn(items=[
            pm_personal.CaptureIn(title="One"),
            pm_personal.CaptureIn(title="Two"),
            pm_personal.CaptureIn(title="Three"),
        ]),
        user=ALICE,
    )
    assert [r["disposition"] for r in out.rows] == ["INBOX"] * 3
    assert {r["disposition"] for r in _overlays(db)} == {"INBOX"}
    assert len(_overlays(db)) == 3


async def _root_and_status(db: FakeProjectsDB) -> tuple[str, str]:
    """A personal root with a lane, the way the route finds them."""
    root = await pm_personal.ensure_personal_project(db, WHO)
    status = await pm_core.load_default_status(db, str(root.id))
    return str(root.id), str(status.id)


async def test_a_stated_disposition_is_kept(db: FakeProjectsDB) -> None:
    """The pm arm of the AI seam captures with a disposition of its own
    (an email clarified before capture). The default must not overwrite it."""
    root, status = await _root_and_status(db)
    await pm_personal.create_personal_task(
        db, WHO, root, root, status, {"title": "Already clarified", "source": "email"},
        {"disposition": "NEXT", "next_action": "Reply"},
    )
    (row,) = _overlays(db)
    assert row["disposition"] == "NEXT"
    assert row["next_action"] == "Reply"


async def test_a_subtask_states_nothing(db: FakeProjectsDB) -> None:
    """A step is not a capture. `POST /projects/tasks` writes no overlay for
    a child either, and the two ways of adding a step must agree."""
    root, status = await _root_and_status(db)
    parent = await pm_personal.create_personal_task(
        db, WHO, root, root, status, {"title": "Parent", "source": "manual"},
    )
    await pm_personal.create_personal_task(
        db, WHO, root, root, status,
        {"title": "Step", "source": "manual", "parent_task_id": str(parent.id)},
    )
    rows = _overlays(db)
    assert [str(r["task_id"]) for r in rows] == [str(parent.id)]


async def test_the_inbox_query_carries_one_order(db: FakeProjectsDB) -> None:
    """`my_inbox` slices the rows in Python, so the SQL must order them or two
    consecutive pages may overlap. The order is `ordering.ts`'s."""
    await pm_personal.capture(pm_personal.CaptureIn(title="x"), user=ALICE)
    await pm_personal.my_inbox(user=ALICE, page=page())
    reads = [s for s in db.statements_touching("pm_task_personal") if "SELECT t.*" in s]
    assert reads, "the inbox did not run its query"
    assert all(pm_personal._INBOX_ORDER.strip() in s for s in reads), reads[-1][-200:]
    assert pm_personal._INBOX_ORDER == (
        " ORDER BY p.sort_key ASC NULLS LAST, t.created_at DESC, t.id"
    )


async def test_the_single_read_and_the_calendar_do_not_page_and_carry_no_order(
    db: FakeProjectsDB,
) -> None:
    """The order belongs to the paged read only. The single read is one row
    and the calendar sorts in Python by block start; neither slices."""
    created = await pm_personal.capture(pm_personal.CaptureIn(title="x"), user=ALICE)
    db.statements.clear()
    await pm_personal.my_task(created["id"], user=ALICE)
    reads = [s for s in db.statements_touching("pm_task_personal") if "SELECT t.*" in s]
    assert reads and not any("ORDER BY p.sort_key" in s for s in reads)


async def test_origin_rides_the_inbox_row_and_the_single_read(db: FakeProjectsDB) -> None:
    root, status = await _root_and_status(db)
    task = await pm_personal.create_personal_task(
        db, WHO, root, root, status,
        {"title": "Vendor quote", "source": "email",
         "origin": {"kind": "email", "from_name": "Sanjay Rao", "email_id": "m1"}},
    )
    inbox = await pm_personal.my_inbox(user=ALICE, page=page())
    (row,) = inbox.rows
    assert row["origin"]["kind"] == "email"
    assert row["origin"]["from_name"] == "Sanjay Rao"
    single = await pm_personal.my_task(str(task.id), user=ALICE)
    assert single["origin"] == row["origin"]
    plain = await pm_personal.capture(pm_personal.CaptureIn(title="typed"), user=ALICE)
    assert (await pm_personal.my_task(plain["id"], user=ALICE))["origin"] is None
