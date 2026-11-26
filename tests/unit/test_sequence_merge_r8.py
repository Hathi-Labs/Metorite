"""R8 — SW-27c2: a merged pull request marks the task's reserved numbers used.

Spec: ``project-docs/specs/agent_coordination.md``, the SW-27 row (SW-27c2),
and ``github_integration.md`` §12. The owner's decision 3 on task 3894
(answer eaa5ee55): a merge marks used the numbers of the task that are still
``reserved`` and were reserved at or before the merge. A closed pull request
that did not merge marks nothing.

A real Postgres (the promoted phase-4 catalog, as the NON-privileged role)
and the real signed webhook, through ``github_sync.apply_pull_request``. The
fixtures are those of ``test_github_merge_r8.py``.

* **U1** a merge marks the task's reserved numbers used, with ``used_at``.
* **U2** a close with no merge marks nothing.
* **U3** a number reserved after the merge stays reserved.
* **U3b** the bound is GitHub's ``merged_at``, not the time of the read.
* **U4** a released number stays released, and the number of another task
  does not move.
* **U5** a second delivery does not change ``used_at``.
* **U6** a task that is already done is still marked. ``on_merged`` skips such
  a task, so the mark must not live inside it.

R7, the mutations. Each one turns a named test red:

* U-M1: drop the ``mark_reservations_used`` call -> ``test_u1_*``.
* U-M2: call it for ``closed`` too -> ``test_u2_*``.
* U-M3: drop the ``reserved_at <= merged_at`` predicate -> ``test_u3_*``.
* U-M4: drop ``state = 'reserved'`` -> ``test_u4_*``.
* U-M5: bind ``now()`` in place of GitHub's ``merged_at`` -> ``test_u3b_*``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from tests.unit.test_github_merge_r8 import (  # noqa: F401
    REPO,
    _close,
    _ref,
    _task,
    p,
    promoted,
    world,
)
from tests.unit.test_h3_rls_promotion_rehearsal import _DB_GATE

pytestmark = _DB_GATE


def _sequence(w: Any, name: str) -> str:
    with w.p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO pm_project_sequences (organization_id, project_id, name, last_value)"
            " VALUES (CAST(:o AS uuid), CAST(:p AS uuid), :n, 100) RETURNING id"),
            {"o": w.p.org_a, "p": w.project, "n": name}).scalar_one())


def _reserve(w: Any, seq: str, task: str, value: int, *, state: str = "reserved",
             at: str = "now() - interval '1 hour'") -> str:
    used = "now()" if state == "used" else "NULL"
    released = "now()" if state == "released" else "NULL"
    with w.p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO pm_sequence_reservations (organization_id, sequence_id, value,"
            " task_id, state, reserved_at, used_at, released_at) VALUES (CAST(:o AS uuid),"
            f" CAST(:s AS uuid), :v, CAST(:t AS uuid), :st, {at}, {used}, {released})"
            " RETURNING id"),
            {"o": w.p.org_a, "s": seq, "v": value, "t": task, "st": state}).scalar_one())


def _row(w: Any, rid: str) -> Any:
    with w.p.admin_engine.begin() as c:
        return c.execute(text(
            "SELECT state, used_at, released_at FROM pm_sequence_reservations"
            " WHERE id = CAST(:i AS uuid)"), {"i": rid}).one()


async def test_u1_a_merge_marks_the_reserved_numbers_used(world) -> None:  # noqa: F811
    w = world
    task = _task(w)
    seq = _sequence(w, "u1")
    a, b = _reserve(w, seq, task, 1), _reserve(w, seq, task, 2)
    _ref(w, task, 9101)
    await _close(w, 9101, merged=True)
    for rid in (a, b):
        row = _row(w, rid)
        assert row.state == "used" and row.used_at is not None, row


async def test_u2_a_close_with_no_merge_marks_nothing(world) -> None:  # noqa: F811
    w = world
    task = _task(w)
    rid = _reserve(w, _sequence(w, "u2"), task, 1)
    _ref(w, task, 9102)
    resp = await _close(w, 9102, merged=False)
    assert resp.status_code == 200
    assert _row(w, rid).state == "reserved"


async def test_u3_a_number_reserved_after_the_merge_stays_reserved(world) -> None:  # noqa: F811
    w = world
    task = _task(w)
    seq = _sequence(w, "u3")
    before = _reserve(w, seq, task, 1)
    after = _reserve(w, seq, task, 2, at="now() + interval '1 hour'")
    _ref(w, task, 9103)
    await _close(w, 9103, merged=True)
    assert _row(w, before).state == "used"
    assert _row(w, after).state == "reserved"


async def test_u3b_the_bound_is_githubs_merge_time_not_the_read_time(world) -> None:  # noqa: F811
    """Review P3. A late poll or lazy read writes ``merged`` hours after the
    real merge. A number reserved between the two stays reserved."""
    w = world
    task = _task(w)
    rid = _reserve(w, _sequence(w, "u3b"), task, 1, at="now() - interval '1 hour'")
    _ref(w, task, 9107)
    two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    resp = await _close(w, 9107, merged=True, merged_at=two_hours_ago)
    assert resp.status_code == 200
    assert _row(w, rid).state == "reserved"


async def test_u4_a_released_number_and_another_tasks_number_do_not_move(world) -> None:  # noqa: F811
    w = world
    task, other = _task(w), _task(w)
    seq = _sequence(w, "u4")
    released = _reserve(w, seq, task, 1, state="released")
    theirs = _reserve(w, seq, other, 2)
    # The positive control: the plain reserved number of the task moves, so
    # the webhook ran.
    control = _reserve(w, seq, task, 3)
    _ref(w, task, 9104)
    resp = await _close(w, 9104, merged=True)
    assert resp.status_code == 200
    assert _row(w, control).state == "used"
    row = _row(w, released)
    assert row.state == "released" and row.used_at is None, row
    assert _row(w, theirs).state == "reserved"


async def test_u5_a_second_delivery_does_not_change_used_at(world) -> None:  # noqa: F811
    w = world
    task = _task(w)
    rid = _reserve(w, _sequence(w, "u5"), task, 1)
    _ref(w, task, 9105)
    await _close(w, 9105, merged=True, delivery=str(uuid.uuid4()))
    first = _row(w, rid).used_at
    await _close(w, 9105, merged=True, delivery=str(uuid.uuid4()))
    assert _row(w, rid).used_at == first


async def test_u6_a_task_that_is_already_done_is_still_marked(world) -> None:  # noqa: F811
    w = world
    task = _task(w, "done")
    rid = _reserve(w, _sequence(w, "u6"), task, 1)
    _ref(w, task, 9106)
    await _close(w, 9106, merged=True)
    assert _row(w, rid).state == "used"


def test_the_repo_constant_is_the_merge_suites() -> None:
    """The events go to the repo that the merge suite's install row knows."""
    assert REPO == "acme/widgets"
