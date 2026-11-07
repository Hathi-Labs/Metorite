"""The delete of a Studio package — the hermetic half (STU-A5c-1).

Spec: ``project-docs/specs/ai_studio.md`` §14 A5c-1. Code:
``gateway/routes/studio/packages.py`` (the two routes),
``gateway/studio/packages_store.py`` (``delete_preview``, ``delete_package``,
``confirm_token``) and ``gateway/studio/objects_store.py`` (``lock_org``,
``package_counts``, ``purge_records``). The R8 half, on a real Postgres, is
``test_studio_package_delete_r8.py``. It proves the rows, the locks and the
dead end that the delete frees.

This file proves:

- a. the store step: the lock order (the ``apps`` row ``FOR UPDATE``, the org
  lock, then the purge), the token check under the locks, a full chunk that
  answers ``deleted=False`` with no delete and no audit, and a last step that
  deletes the row and writes one audit row in the same session;
- b. the object store: the purge stops at its limit across objects, the
  counts stop at the cap, and a timeout is ``StoreBusy``;
- c. the routes: the switch first, the order of the checks (the id, the body
  cap, the owner, then the parse), 422 for a token that is missing or
  malformed, 409 ``counts_changed`` with the new preview, one session for
  each step, 202 past the budget with a new token, 503 ``store_busy`` only on
  the first step, and a body that names another owner changes nothing;
- d. the fences: only ``delete_package`` deletes a package from ``apps``, the
  versions go by the cascade, the audit is in the store, and the R8 suite is
  in the ``--must-run`` list.

**The mutations (R7).** Each was run on 2026-11-05 against both halves, one
at a time, and each turned the tests below red. The R8 half holds the ones
that need a database. The R8 failure of each was read, and each failed for
the reason of its mutation.

* M1. Drop ``for_update=True`` from ``delete_package``. Red here:
  ``test_a_step_locks_the_apps_row_then_the_org_then_purges``,
  ``test_another_member_or_a_bad_token_reads_nothing_and_locks_nothing``,
  ``test_a_busy_org_lock_raises_store_busy_and_purges_nothing`` and
  ``test_the_lock_order_is_in_the_source``. Red in R8:
  ``test_r8_a_delete_waits_for_a_publish_that_holds_the_apps_row`` (the
  delete purged 20 of 25 records under the holder of the row).
* M2. Swap the lock order (``lock_org`` before ``get_package``). Red here:
  the same four tests as M1. Red in R8:
  ``test_r8_a_holder_of_the_apps_row_takes_the_org_lock_while_a_delete_waits``
  (Postgres reports 40P01, a deadlock between the row and the org lock).
* M3. Drop the switch check from the route ``delete_package``. Red here:
  ``test_with_the_switch_off_both_routes_are_404_and_read_no_byte`` (three
  cases) and ``test_each_delete_handler_checks_the_switch_first``. Red in
  R8: ``test_r8_with_the_switch_off_both_routes_are_404_and_write_nothing``.
* M4. Take the owner from the body (``body.get("owner_email")``). Red here:
  ``test_a_stranger_with_a_bad_body_gets_404_and_not_422`` and
  ``test_a_body_that_names_another_owner_or_org_changes_nothing``. Red in
  R8: ``test_r8_a_body_that_names_the_owner_does_not_let_another_member_delete``.
* M5. Write the audit through ``record_app_audit_bound`` after the session,
  in place of ``_audit`` in the store. Red here:
  ``test_the_last_step_deletes_the_row_and_writes_one_audit_row``,
  ``test_a_failed_audit_raises_after_the_delete_in_the_same_session``,
  ``test_the_lock_order_is_in_the_source`` and
  ``test_the_audit_is_written_in_the_store_and_not_by_the_route``. Red in
  R8: ``test_r8_a_failed_audit_keeps_the_package_and_its_rows``.
* M6. Write the audit in a second session after the delete session. Red
  here: the four tests of M5, and the route tests that reach a last step.
  Red in R8: ``test_r8_a_failed_audit_keeps_the_package_and_its_rows`` (the
  ``apps`` row was gone when the audit failed).
* M7. Drop the token check of the first step. Red here:
  ``test_a_step_with_a_stale_token_raises_before_the_purge``. The route
  tests run on a fake store, so they stay green. Red in R8:
  ``test_r8_a_stale_token_is_409_and_deletes_nothing``.
* M8. Run every step in one session (no commit between steps). Red here:
  ``test_each_step_runs_in_its_own_session`` and
  ``test_the_route_runs_one_session_for_each_step``. Red in R8:
  ``test_r8_the_org_lock_is_free_between_two_steps`` (no write of the org
  came in during the delete).
* M9. Delete the ``apps`` row on the first step, with no purge loop. Red
  here: ``test_a_step_locks_the_apps_row_then_the_org_then_purges`` and
  ``test_a_full_chunk_answers_not_deleted_and_writes_nothing_else``. Red in
  R8: ``test_r8_1300_records_with_a_chunk_of_100_give_202_and_then_200``
  (the first call answered 200, not 202).

**Fix round 1 (2026-11-05).** Each mutation below takes one fix out again.
Each was run alone against both halves, and its R8 failure was read.

* M10 (P2-1). Write no partial row in ``_end_call``. Red here:
  ``test_a_full_chunk_past_the_budget_ends_the_call_with_a_partial_row``,
  ``test_a_failed_partial_audit_raises_in_the_step_of_its_purge``,
  ``test_a_later_step_that_finds_a_new_object_ends_the_call_with_changed``,
  ``test_store_busy_on_a_later_step_ends_the_call_with_a_partial_row`` (two
  cases) and
  ``test_a_busy_preview_at_the_end_keeps_the_partial_row_and_gives_no_token``.
  Red in R8:
  ``test_r8_a_202_call_writes_one_partial_row_in_the_step_of_its_purge``
  (no partial row) and
  ``test_r8_a_failed_partial_audit_rolls_back_the_purge_of_its_step``.
* M11 (P2-1). Write the partial row in a second session after the step.
  Red here: the six tests of M10 and five route tests that end a call with
  records left. Red in R8:
  ``test_r8_a_failed_partial_audit_rolls_back_the_purge_of_its_step`` (15
  records left, not 25: the purge had committed).
* M12 (P2-2). Set the budget back to 20 s. Red here:
  ``test_the_worst_call_ends_before_the_chat_tool_gives_up`` and
  ``test_the_route_passes_the_end_of_its_budget_to_the_store``.
* M13 (P2-2). Let the purge ignore the end of the call. Red here:
  ``test_the_purge_stops_at_the_end_of_the_call``.
* M14 (P3-1). Drop the name check of a later step. Red here:
  ``test_a_later_step_that_finds_a_new_object_ends_the_call_with_changed``.
  The route tests run on a fake store, so they stay green. Red in R8:
  ``test_r8_a_publish_between_two_steps_that_adds_an_object_ends_the_call_with_409``
  (200: the call deleted ``invoices`` too).
* M15 (P3-2). Count the installs with the old rule of a private install.
  Red here: ``test_the_other_member_installs_use_the_one_share_rule``. Red in
  R8: ``test_r8_a_private_install_that_the_owner_shared_counts_as_another_members``
  (a shared install counted 0, not 1).
* M16 (the noted 404). Read the counts with ``.one()``. Red here:
  ``test_a_preview_of_a_package_that_a_concurrent_delete_removed_is_none``.
* M17 (found by the live 202 check). Purge with ``id IN (subselect)`` in
  place of ``id = ANY(ARRAY(subselect))``. Red in R8:
  ``test_r8_a_purge_after_a_bulk_load_reads_a_bounded_number_of_rows``
  (10,035,297 buffers for one purge of 500 records, against a bound of
  30,000).

**Fix round 2 (2026-11-05).** Each mutation below takes one fix out again.
Each was run alone, and its R8 failure was read.

* M18 (item 1). A busy final count of a later step raises. Red here:
  ``test_a_busy_final_count_of_a_later_step_ends_the_call_with_a_partial_row``.
  Red in R8:
  ``test_r8_a_busy_final_count_of_a_later_step_keeps_the_partial_row`` (5
  records left, not 0: the step rolled back).
* M19 (item 1). A later step that finds the package gone writes no row.
  Red here: ``test_a_later_step_that_finds_the_package_gone_writes_the_partial_row``.
  Red in R8:
  ``test_r8_a_call_whose_package_another_call_deletes_writes_its_partial_row``
  (no partial row).
* M20 (item 1). The route writes no partial row after a busy row wait. Red
  here: ``test_a_busy_row_wait_of_a_later_step_is_503_and_writes_the_partial_row``.
* M21 (item 2). The 409 of a later step keeps "Nothing was deleted" and no
  count. Red here:
  ``test_a_new_object_between_two_steps_is_409_with_the_new_preview``. Red
  in R8: ``test_r8_a_publish_between_two_steps_that_adds_an_object_ends_the_call_with_409``
  (no ``records_removed``).
* M22 (item 3). No bound on the wait for the ``apps`` row. Red here:
  ``test_each_step_waits_for_the_apps_row_with_a_bound`` and
  ``test_the_lock_order_is_in_the_source``. Red in R8:
  ``test_r8_a_held_apps_row_gives_store_busy_in_about_one_second`` (200
  after the holder let go, not 503).
* M23 (item 3). The step bound leaves the row wait out. Red here:
  ``test_the_worst_call_ends_before_the_chat_tool_gives_up``.
* M24 (the bulk fence). The test binds org B in place of org A. Red in R8:
  ``test_r8_a_purge_after_a_bulk_load_reads_a_bounded_number_of_rows`` (the
  purge found 0 rows, not 500).
"""

from __future__ import annotations

import ast
import asyncio
import copy
import inspect
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from gateway.routes.studio import actions, packages
from gateway.studio import objects_store
from gateway.studio import packages_store as store
from sqlalchemy.exc import DBAPIError

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
ANA = "ana@example.test"
BEN = "ben@example.test"
NOT_FOUND = {"detail": "Not found"}
_ROOT = Path(__file__).resolve().parents[2]
_PACKAGES_PY = _ROOT / "apps/services/gateway/gateway/routes/studio/packages.py"
_STORE_PY = _ROOT / "apps/services/gateway/gateway/studio/packages_store.py"
_JSON = {"content-type": "application/json"}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _package(pid: str, owner: str = ANA, live: int | None = 2) -> store.Package:
    now = datetime.now(UTC)
    return store.Package(
        id=pid, slug=f"visits-{pid[:8]}", name="Visits", description="Track visits.",
        owner_email=owner, visibility="private", status="live",
        manifest={"studio": 1, "id": "visits", "name": "Visits", "purpose": "Secret purpose."},
        live_version=live, created_at=now, updated_at=now)


def _counts(records: dict[str, int], cap: int = objects_store.COUNT_CAP
            ) -> list[objects_store.ObjectCount]:
    return [objects_store.ObjectCount(name=n, fields=3, records=min(r, cap), capped=r > cap)
            for n, r in sorted(records.items())]


# ── a. The store step ──────────────────────────────────────────────────────


class _Res:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None, rowcount: int = 0) -> None:
        self.rows = list(rows or [])
        self.rowcount = rowcount

    def first(self) -> Any:
        return self.rows[0] if self.rows else None

    def one(self) -> Any:
        assert len(self.rows) == 1, self.rows
        return self.rows[0]

    def all(self) -> list[Any]:
        return list(self.rows)

    def scalar_one(self) -> Any:
        return self.rows[0][0]


class _StoreDb:
    """The session of a store step. It answers the four statements of
    ``packages_store`` and writes each one to *log*. ``counts`` of None
    plays a row that a concurrent delete removed."""

    def __init__(self, log: list[str], *, counts: tuple[int, int, int] | None = (2, 1, 0),
                 audit_fails: bool = False, earlier: int = 0) -> None:
        self.log = log
        self.counts = counts
        self.audit_fails = audit_fails
        self.earlier = earlier
        self.audits: list[dict[str, Any]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Res:
        sql, p = str(stmt), dict(params or {})
        if sql == store._DELETE_COUNTS_SQL:
            self.log.append("counts")
            return _Res([] if self.counts is None else [self.counts])
        if sql == store._PARTIAL_TOTAL_SQL:
            self.log.append("earlier")
            assert p["op"] == store.PARTIAL_OP
            return _Res([(self.earlier,)])
        if sql == store._DELETE_SQL:
            self.log.append("delete")
            return _Res([(p["id"],)])
        if sql == store._AUDIT_SQL:
            self.log.append("audit")
            if self.audit_fails:
                raise RuntimeError("the audit insert failed")
            self.audits.append({**p, "detail": json.loads(p["detail"])})
            return _Res()
        raise AssertionError(f"an unexpected statement: {sql[:60]}")


_NAMES = ("site", "visit")


@pytest.fixture
def step(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The package of Ana, with records, behind fakes of the object store.
    The object store clock reads ``w.now``."""
    pid = str(uuid.uuid4())
    w = SimpleNamespace(pid=pid, log=[], records={"visit": 700, "site": 300}, busy=False,
                        busy_purge=False, busy_counts=False, extra=[], now=100.0,
                        deadlines=[], gone=False, row_busy=False, row_waits=[],
                        counts_calls=0, busy_counts_from=None)

    async def get_package(db: Any, member: str, package_id: str, *,
                          for_update: bool = False) -> store.Package | None:
        w.log.append(f"get(for_update={for_update})")
        if w.gone:
            return None
        return _package(pid) if (package_id, member) == (pid, ANA) else None

    @asynccontextmanager
    async def bounded_wait(db: Any, ms: int) -> Any:
        w.row_waits.append(ms)
        if w.row_busy:
            raise objects_store.StoreBusy("This app is busy. Try again in a moment.")
        yield

    async def lock_org(db: Any) -> None:
        w.log.append("lock_org")
        if w.busy:
            raise objects_store.StoreBusy("This app is busy. Try again in a moment.")

    async def package_counts(db: Any, package_id: str, *, cap: int = 100_000) -> Any:
        w.log.append("package_counts")
        w.counts_calls += 1
        if w.busy_counts or (w.busy_counts_from is not None
                             and w.counts_calls >= w.busy_counts_from):
            raise objects_store.StoreBusy("This app is busy. Try again in a moment.")
        return _counts({**w.records, **dict.fromkeys(w.extra, 0)})

    async def object_names(db: Any, package_id: str) -> list[str]:
        w.log.append("object_names")
        return sorted([*w.records, *w.extra])

    async def purge_records(db: Any, package_id: str, *, limit: int = 500,
                            deadline: float | None = None) -> int:
        w.log.append(f"purge({limit})")
        w.deadlines.append(deadline)
        if w.busy_purge:
            raise objects_store.StoreBusy("This app is busy. Try again in a moment.")
        gone = 0
        for name in sorted(w.records):
            take = min(limit - gone, w.records[name])
            w.records[name] -= take
            gone += take
        return gone

    monkeypatch.setattr(store, "get_package", get_package)
    monkeypatch.setattr(objects_store, "lock_org", lock_org)
    monkeypatch.setattr(objects_store, "package_counts", package_counts)
    monkeypatch.setattr(objects_store, "object_names", object_names)
    monkeypatch.setattr(objects_store, "purge_records", purge_records)
    monkeypatch.setattr(objects_store, "_clock", lambda: w.now)
    monkeypatch.setattr(objects_store, "bounded_wait", bounded_wait)
    return w


def _token(w: SimpleNamespace, counts: tuple[int, int, int] = (2, 1, 0)) -> str:
    return store.confirm_token(w.pid, _counts(w.records), *counts)


def test_a_step_locks_the_apps_row_then_the_org_then_purges(step: SimpleNamespace) -> None:
    db = _StoreDb(step.log)
    got = _run(store.delete_package(db, ANA, step.pid, confirm=_token(step)))
    assert got == store.DeleteStep(deleted=False, removed=500, names=_NAMES)
    assert step.log == ["get(for_update=True)", "lock_org", "package_counts", "counts",
                        "purge(500)"]


def test_a_later_step_checks_the_names_and_not_the_token(step: SimpleNamespace) -> None:
    got = _run(store.delete_package(_StoreDb(step.log), ANA, step.pid, names=_NAMES,
                                    removed=500))
    assert got == store.DeleteStep(deleted=False, removed=500, names=_NAMES)
    assert step.log == ["get(for_update=True)", "lock_org", "object_names", "purge(500)"]


def test_a_full_chunk_answers_not_deleted_and_writes_nothing_else(step: SimpleNamespace) -> None:
    db = _StoreDb(step.log)
    got = _run(store.delete_package(db, ANA, step.pid, names=_NAMES, chunk=1000,
                                    end_at=step.now + 100))
    assert got == store.DeleteStep(deleted=False, removed=1000, names=_NAMES)
    assert "delete" not in step.log and "audit" not in step.log and db.audits == []


def test_a_full_chunk_past_the_budget_ends_the_call_with_a_partial_row(
        step: SimpleNamespace) -> None:
    """P2-1. No more step fits before the end of the call, so this step is
    the last. It writes the partial row, then the preview of the 202, in its
    own session."""
    db = _StoreDb(step.log)
    end = step.now + store.STEP_SECONDS - 0.1
    got = _run(store.delete_package(db, ANA, step.pid, names=_NAMES, removed=1500, end_at=end))
    assert (got.deleted, got.removed, got.changed, got.busy) == (False, 500, False, False)
    assert got.left is not None and got.left.records == 500
    assert got.left.confirm == _token(step)
    assert step.log[-4:] == ["purge(500)", "audit", "package_counts", "counts"]
    assert step.deadlines == [end]
    [audit] = db.audits
    assert audit["detail"] == {
        "op": "package.delete.partial", "by": "owner", "owner": ANA, "name": "Visits",
        "slug": f"visits-{step.pid[:8]}", "records_this_call": 2000, "reason": "budget"}
    assert "delete" not in step.log


def test_a_failed_partial_audit_raises_in_the_step_of_its_purge(step: SimpleNamespace) -> None:
    """P2-1. The partial row is in the session of the purge, so a failed
    insert rolls that purge back with the session (R8 proves the rollback)."""
    with pytest.raises(RuntimeError):
        _run(store.delete_package(_StoreDb(step.log, audit_fails=True), ANA, step.pid,
                                  names=_NAMES, end_at=step.now))
    assert step.log[-2:] == ["purge(500)", "audit"]


def test_a_later_step_that_finds_a_new_object_ends_the_call_with_changed(
        step: SimpleNamespace) -> None:
    """P3-1. A publish between two steps added ``invoices``. The step purges
    nothing, writes the partial row of the earlier steps, and answers the
    preview of now."""
    step.extra = ["invoices"]
    db = _StoreDb(step.log)
    got = _run(store.delete_package(db, ANA, step.pid, names=_NAMES, removed=500))
    assert (got.deleted, got.removed, got.changed) == (False, 0, True)
    assert [o.name for o in got.left.objects] == ["invoices", "site", "visit"]
    assert not any(e.startswith("purge") for e in step.log)
    assert db.audits[0]["detail"]["records_this_call"] == 500
    assert db.audits[0]["detail"]["reason"] == "changed"


def test_a_name_that_went_is_not_a_change(step: SimpleNamespace) -> None:
    """No publish removes an object, and a smaller set takes nothing that the
    member did not see."""
    got = _run(store.delete_package(_StoreDb(step.log), ANA, step.pid,
                                    names=("old", *_NAMES), removed=1))
    assert got.changed is False and got.removed == 500


@pytest.mark.parametrize("where", ["lock", "purge"])
def test_store_busy_on_a_later_step_ends_the_call_with_a_partial_row(
        step: SimpleNamespace, where: str) -> None:
    if where == "lock":
        step.busy = True
    else:
        step.busy_purge = True
    db = _StoreDb(step.log)
    got = _run(store.delete_package(db, ANA, step.pid, names=_NAMES, removed=700))
    assert (got.deleted, got.removed, got.busy, got.changed) == (False, 0, True, False)
    assert got.left is not None
    assert db.audits[0]["detail"]["records_this_call"] == 700
    assert db.audits[0]["detail"]["reason"] == "busy"


def test_a_busy_preview_at_the_end_keeps_the_partial_row_and_gives_no_token(
        step: SimpleNamespace) -> None:
    step.busy_counts = True
    db = _StoreDb(step.log)
    got = _run(store.delete_package(db, ANA, step.pid, names=_NAMES, end_at=step.now))
    assert got.left is None and got.busy is True and got.removed == 500
    assert db.audits[0]["detail"]["op"] == store.PARTIAL_OP


def test_a_busy_final_count_of_a_later_step_ends_the_call_with_a_partial_row(
        step: SimpleNamespace) -> None:
    """Fix round 2, item 1. A later step drains the last records, and the
    count of the final row is busy. The earlier steps and this purge hold, so
    the step writes the partial row with both, and the package stays."""
    step.records = {"visit": 120}
    step.busy_counts = True
    db = _StoreDb(step.log)
    got = _run(store.delete_package(db, ANA, step.pid, names=("visit",), removed=1500,
                                    end_at=step.now + 100))
    assert (got.deleted, got.removed, got.busy, got.left) == (False, 120, True, None)
    [audit] = db.audits
    assert audit["detail"]["op"] == store.PARTIAL_OP
    assert (audit["detail"]["records_this_call"], audit["detail"]["reason"]) == (1620, "busy")
    assert "delete" not in step.log


def test_a_busy_final_count_of_the_first_step_raises(step: SimpleNamespace) -> None:
    """The first step removed nothing that holds, so it rolls back whole."""
    step.records = {"visit": 120}
    step.busy_counts_from = 2
    with pytest.raises(objects_store.StoreBusy):
        _run(store.delete_package(_StoreDb(step.log), ANA, step.pid,
                                  confirm=store.confirm_token(step.pid, _counts(step.records),
                                                              2, 1, 0)))


def test_a_later_step_that_finds_the_package_gone_writes_the_partial_row(
        step: SimpleNamespace) -> None:
    """Fix round 2, item 1. Another call deleted the package between two steps
    of this call. ``app_audit`` has no foreign key, so the step still writes
    the partial row of its earlier steps, and it locks nothing."""
    step.gone = True
    db = _StoreDb(step.log)
    got = _run(store.delete_package(db, ANA, step.pid, names=_NAMES, removed=700))
    assert (got.deleted, got.removed, got.gone) == (False, 0, True)
    [audit] = db.audits
    assert (audit["id"], audit["m"], audit["v"]) == (step.pid, ANA, None)
    assert audit["detail"] == {"op": store.PARTIAL_OP, "by": "owner", "owner": ANA,
                               "records_this_call": 700, "reason": "gone"}
    assert step.log == ["get(for_update=True)", "audit"]


def test_a_first_step_that_finds_the_package_gone_writes_nothing(step: SimpleNamespace) -> None:
    step.gone = True
    db = _StoreDb(step.log)
    assert _run(store.delete_package(db, ANA, step.pid, confirm=_token(step))) is None
    assert db.audits == []


def test_each_step_waits_for_the_apps_row_with_a_bound(step: SimpleNamespace) -> None:
    """Fix round 2, item 3. The row read of a step runs under the bounded
    wait, and a busy wait raises before any other statement."""
    _run(store.delete_package(_StoreDb(step.log), ANA, step.pid, names=_NAMES))
    assert step.row_waits == [store.ROW_LOCK_WAIT_MS] == [1000]
    step.log.clear()
    step.row_busy = True
    for kw in ({"names": _NAMES, "removed": 500}, {"confirm": _token(step)}):
        with pytest.raises(objects_store.StoreBusy):
            _run(store.delete_package(_StoreDb(step.log), ANA, step.pid, **kw))
    assert step.log == []


def test_store_busy_on_the_first_step_raises(step: SimpleNamespace) -> None:
    """The first step removed nothing, so the call has nothing to audit."""
    step.busy_purge = True
    with pytest.raises(objects_store.StoreBusy):
        _run(store.delete_package(_StoreDb(step.log), ANA, step.pid, confirm=_token(step)))


def test_the_last_step_deletes_the_row_and_writes_one_audit_row(step: SimpleNamespace) -> None:
    step.records = {"visit": 120}
    db = _StoreDb(step.log, counts=(4, 2, 1), earlier=300)
    got = _run(store.delete_package(db, ANA, step.pid, names=("visit",), removed=1500))
    assert got == store.DeleteStep(deleted=True, removed=120, names=("visit",))
    assert step.log[-5:] == ["package_counts", "counts", "earlier", "delete", "audit"]
    [audit] = db.audits
    assert (audit["id"], audit["m"], audit["kind"], audit["v"]) == (step.pid, ANA, "action", 2)
    detail = audit["detail"]
    assert detail == {
        "op": "package.delete", "by": "owner", "owner": ANA, "name": "Visits",
        "slug": f"visits-{step.pid[:8]}", "objects": 1, "fields": 3,
        "records_this_call": 1620, "records_total": 1920,
        "versions": 4, "installs": 2, "other_member_installs": 1}
    assert "Secret purpose" not in json.dumps(detail)


def test_a_step_with_a_stale_token_raises_before_the_purge(step: SimpleNamespace) -> None:
    stale = _token(step)
    step.records["visit"] += 1
    with pytest.raises(store.CountsChanged) as exc:
        _run(store.delete_package(_StoreDb(step.log), ANA, step.pid, confirm=stale))
    assert not any(e.startswith("purge") for e in step.log)
    assert exc.value.preview.confirm == _token(step) != stale
    assert exc.value.preview.records == 1001
    assert str(exc.value) == store.COUNTS_CHANGED_TEXT


def test_another_member_or_a_bad_token_reads_nothing_and_locks_nothing(
        step: SimpleNamespace) -> None:
    assert _run(store.delete_package(_StoreDb(step.log), BEN, step.pid,
                                     confirm=_token(step))) is None
    assert step.log == ["get(for_update=True)"]
    step.log.clear()
    for bad in ("A" * 64, "a" * 63, "g" * 64, 7, "a" * 65):
        with pytest.raises(store.PackageStoreError):
            _run(store.delete_package(_StoreDb(step.log), ANA, step.pid, confirm=bad))
    for kw in ({"chunk": 0}, {"chunk": True}, {"removed": -1}, {"removed": 1.5},
               {"names": "visit"}, {"names": [1]}, {}, {"names": (), "confirm": "a" * 64}):
        kw = {"names": _NAMES, **kw} if "names" not in kw and kw else kw
        with pytest.raises(store.PackageStoreError):
            _run(store.delete_package(_StoreDb(step.log), ANA, step.pid, **kw))
    assert step.log == []


def test_a_busy_org_lock_raises_store_busy_and_purges_nothing(step: SimpleNamespace) -> None:
    step.busy = True
    with pytest.raises(objects_store.StoreBusy):
        _run(store.delete_package(_StoreDb(step.log), ANA, step.pid, confirm=_token(step)))
    assert step.log == ["get(for_update=True)", "lock_org"]


def test_a_failed_audit_raises_after_the_delete_in_the_same_session(step: SimpleNamespace) -> None:
    """The audit runs in the session of the delete, so its failure rolls the
    delete back with the session (the R8 half proves the rollback)."""
    step.records = {"visit": 3}
    with pytest.raises(RuntimeError):
        _run(store.delete_package(_StoreDb(step.log, audit_fails=True), ANA, step.pid,
                                  names=("visit",)))
    assert step.log[-2:] == ["delete", "audit"]


def test_the_preview_is_a_read_with_no_lock(step: SimpleNamespace) -> None:
    found = _run(store.delete_preview(_StoreDb(step.log, counts=(3, 2, 1)), ANA, step.pid))
    assert step.log == ["get(for_update=False)", "package_counts", "counts"]
    assert (found.records, found.fields, found.versions, found.installs,
            found.other_member_installs, found.capped) == (1000, 6, 3, 2, 1, False)
    assert found.confirm == _token(step, (3, 2, 1))
    assert _run(store.delete_preview(_StoreDb(step.log), BEN, step.pid)) is None


def test_a_preview_of_a_package_that_a_concurrent_delete_removed_is_none(
        step: SimpleNamespace) -> None:
    """The noted fix. The row went between the read of the package and the
    count, so the count gives no row. That is a 404, not a 500."""
    assert _run(store.delete_preview(_StoreDb(step.log, counts=None), ANA, step.pid)) is None


def test_the_other_member_installs_use_the_one_share_rule() -> None:
    """P3-2. A private install of the owner that the owner shared counts. The
    rule is the one of ``package_host/sharing.py``, and it names
    ``package_install`` with no alias."""
    from gateway.package_host.sharing import OTHERS_SEE_THE_INSTALL
    sql = store._DELETE_COUNTS_SQL
    assert sql.count(OTHERS_SEE_THE_INSTALL) == 1
    third = sql[sql.index("(SELECT count(*) FROM package_install WHERE"):]
    assert f"AND NOT {OTHERS_SEE_THE_INSTALL}" in third
    assert "package_install.scope_subject = a.owner_email" in third


def test_the_token_is_the_hash_of_the_id_the_names_and_the_counts() -> None:
    pid = str(uuid.uuid4())
    objs = _counts({"a": 1, "b": 2})
    token = store.confirm_token(pid, objs, 1, 0, 0)
    assert re.fullmatch(r"[0-9a-f]{64}", token)
    assert store.confirm_token(pid, list(reversed(objs)), 1, 0, 0) == token
    changed = [
        store.confirm_token(str(uuid.uuid4()), objs, 1, 0, 0),
        store.confirm_token(pid, _counts({"a": 1, "c": 2}), 1, 0, 0),
        store.confirm_token(pid, _counts({"a": 1, "b": 3}), 1, 0, 0),
        store.confirm_token(pid, objs, 2, 0, 0),
        store.confirm_token(pid, objs, 1, 1, 0),
        store.confirm_token(pid, objs, 1, 0, 1),
    ]
    assert token not in changed and len(set(changed)) == len(changed)


def test_the_store_signatures_hold_no_admin_switch() -> None:
    """A5c-1 has no admin path: no store function takes a flag that drops the
    owner filter, and the route passes only the member of the session."""
    assert list(inspect.signature(store.delete_package).parameters) == [
        "db", "member", "package_id", "confirm", "names", "chunk", "removed", "end_at"]
    assert list(inspect.signature(store.delete_preview).parameters) == [
        "db", "member", "package_id"]


# ── b. The object store ────────────────────────────────────────────────────


class _Nested:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _Orig(Exception):
    sqlstate = "57014"


class _ObjDb:
    """A session of the object store: the deadline statements, the object
    ids and the purge of each object."""

    def __init__(self, records: dict[str, int], *, cancel: bool = False,
                 counts: list[tuple[Any, ...]] | None = None) -> None:
        self.records = dict(records)
        self.cancel = cancel
        self.counts = counts or []
        self.purges: list[tuple[str, int]] = []
        self.timeouts: list[str] = []
        self.names: list[str] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Res:
        sql, p = str(stmt), dict(params or {})
        if sql == objects_store._TIMEOUT_READ_SQL:
            return _Res([("0",)])
        if sql == objects_store._TIMEOUT_SQL:
            self.timeouts.append(p["ms"])
            return _Res()
        if self.cancel:
            raise DBAPIError(sql, p, _Orig())
        if sql == objects_store._PACKAGE_OBJECT_IDS_SQL:
            return _Res([(o,) for o in sorted(self.records)])
        if sql == objects_store._PACKAGE_OBJECT_NAMES_SQL:
            return _Res([(n,) for n in self.names])
        if sql == objects_store._PURGE_SQL:
            take = min(int(p["n"]), self.records[p["oid"]])
            self.records[p["oid"]] -= take
            self.purges.append((p["oid"], int(p["n"])))
            return _Res(rowcount=take)
        if sql == objects_store._PACKAGE_COUNTS_SQL:
            assert p["cap"] == objects_store.COUNT_CAP + 1
            return _Res(self.counts)
        raise AssertionError(f"an unexpected statement: {sql[:60]}")

    def begin_nested(self) -> _Nested:
        return _Nested()


_O1, _O2, _O3 = (f"0000000{i}-0000-0000-0000-000000000000" for i in (1, 2, 3))
_PID = str(uuid.uuid4())


def test_the_purge_stops_at_its_limit_across_objects() -> None:
    db = _ObjDb({_O1: 300, _O2: 300, _O3: 50})
    assert _run(objects_store.purge_records(db, _PID, limit=500)) == 500
    assert db.records == {_O1: 0, _O2: 100, _O3: 50}
    assert db.purges == [(_O1, 500), (_O2, 200)]
    # A short answer means that nothing is left.
    assert _run(objects_store.purge_records(db, _PID, limit=500)) == 150
    assert db.records == {_O1: 0, _O2: 0, _O3: 0}


def test_each_purge_statement_runs_under_the_deadline() -> None:
    db = _ObjDb({_O1: 10})
    _run(objects_store.purge_records(db, _PID, limit=500))
    # One LOCAL timeout before each statement, and the old value at the end.
    assert len(db.timeouts) == 3 and db.timeouts[-1] == "0"
    assert all(0 < int(ms) <= 5000 for ms in db.timeouts[:-1])


def test_the_purge_stops_at_the_end_of_the_call() -> None:
    """P2-2. A *deadline* in the past leaves the purge no time: it runs no
    delete statement and raises ``StoreBusy``. A later one ends the LOCAL
    timeout at it, before the 5 s of the purge."""
    db = _ObjDb({_O1: 10})
    now = objects_store._clock()
    with pytest.raises(objects_store.StoreBusy):
        _run(objects_store.purge_records(db, _PID, deadline=now - 1))
    assert db.purges == [] and db.records == {_O1: 10}
    db = _ObjDb({_O1: 10})
    _run(objects_store.purge_records(db, _PID, deadline=objects_store._clock() + 1.0))
    assert db.purges and all(int(ms) <= 1000 for ms in db.timeouts[:-1])


def test_object_names_reads_the_names_under_the_deadline() -> None:
    db = _ObjDb({})
    db.names = ["site", "visit"]
    assert _run(objects_store.object_names(db, _PID)) == ["site", "visit"]
    with pytest.raises(objects_store.StoreBusy):
        _run(objects_store.object_names(_ObjDb({}, cancel=True), _PID))


def test_a_timeout_of_the_purge_or_the_counts_is_store_busy() -> None:
    with pytest.raises(objects_store.StoreBusy):
        _run(objects_store.purge_records(_ObjDb({_O1: 10}, cancel=True), _PID))
    with pytest.raises(objects_store.StoreBusy):
        _run(objects_store.package_counts(_ObjDb({}, cancel=True), _PID))


def test_the_counts_stop_at_the_cap() -> None:
    cap = objects_store.COUNT_CAP
    db = _ObjDb({}, counts=[("lines", 4, cap + 1), ("visit", 2, 17)])
    got = _run(objects_store.package_counts(db, _PID))
    assert got == [objects_store.ObjectCount("lines", 4, cap, True),
                   objects_store.ObjectCount("visit", 2, 17, False)]


def test_a_bad_id_or_limit_is_refused_before_any_statement() -> None:
    db = _ObjDb({_O1: 1})
    for call in (objects_store.purge_records(db, "nope"),
                 objects_store.purge_records(db, _PID, limit=0),
                 objects_store.purge_records(db, _PID, limit=True),
                 objects_store.package_counts(db, _PID, cap=0)):
        with pytest.raises(objects_store.ObjectStoreError):
            _run(call)
    assert db.purges == [] and db.timeouts == []


def test_the_new_statements_bind_the_org_in_each_select() -> None:
    """RLS is the backstop, and it hides a missing org filter from each R8
    test, so a source fence holds it."""
    bound = f"organization_id = {objects_store._BOUND_ORG}"
    for sql, binds in ((objects_store._PACKAGE_COUNTS_SQL, 3),
                       (objects_store._PACKAGE_OBJECT_IDS_SQL, 1),
                       (objects_store._PURGE_SQL, 2), (store._DELETE_COUNTS_SQL, 1),
                       (store._DELETE_SQL, 1), (objects_store._PACKAGE_OBJECT_NAMES_SQL, 1),
                       (store._PARTIAL_TOTAL_SQL, 1)):
        assert sql.count(bound) == binds, sql[:60]
    assert "format = 'manifest'" in store._DELETE_SQL and "owner_email = :m" in store._DELETE_SQL
    assert "LIMIT :n" in objects_store._PURGE_SQL and "LIMIT :cap" in (
        objects_store._PACKAGE_COUNTS_SQL)


# ── c. The routes ──────────────────────────────────────────────────────────


class _DB:
    def __init__(self, org: str | None) -> None:
        assert org, "a store session opened with no bound org"
        self.org = org


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:  # noqa: C901 - one fake world
    """A fake store of packages with records. Each step removes at most
    ``chunk`` records, and a session rolls the world back on an error."""
    w = SimpleNamespace(packages={}, org=None, sessions=0, steps=[], busy_steps=set(),
                        row_busy_steps=set(),
                        chunk=500, audits=[], preview_busy=False, clock=0.0, tick=0.0)

    def add(owner: str = ANA, org: str = ORG, records: int = 1200) -> str:
        pid = str(uuid.uuid4())
        w.packages[pid] = {"org": org, "owner": owner, "records": records}
        return pid

    w.add = add

    def mine(db: _DB, member: str, pid: str) -> dict[str, Any] | None:
        row = w.packages.get(pid)
        if row and row["org"] == db.org and row["owner"] == member.strip().lower():
            return row
        return None

    def preview(pid: str, row: dict[str, Any]) -> store.DeletePreview:
        objs = tuple(_counts({"visit": row["records"], **dict.fromkeys(row.get("extra", []), 0)}))
        return store.DeletePreview(
            id=pid, name="Visits", slug=f"visits-{pid[:8]}", objects=objs, versions=2,
            installs=1, other_member_installs=0,
            confirm=store.confirm_token(pid, objs, 2, 1, 0))

    w.preview = lambda pid: preview(pid, w.packages[pid])

    @asynccontextmanager
    async def _session(*args: Any) -> Any:
        w.sessions += 1
        snapshot = copy.deepcopy(w.packages)
        try:
            yield _DB(w.org)
        except BaseException:
            w.packages = snapshot
            raise

    async def get_package(db: _DB, member: str, pid: str, *, for_update: bool = False) -> Any:
        row = mine(db, member, pid)
        return None if row is None else _package(pid, row["owner"])

    async def delete_preview(db: _DB, member: str, pid: str) -> Any:
        if w.preview_busy:
            raise objects_store.StoreBusy("This app is busy. Try again in a moment.")
        row = mine(db, member, pid)
        return None if row is None else preview(pid, row)

    async def delete_package(db: _DB, member: str, pid: str, *, confirm: str | None = None,
                             names: Any = None, chunk: int | None = None, removed: int = 0,
                             end_at: float | None = None) -> Any:
        """The contract of ``store.delete_package``, on the fake world."""
        w.steps.append({"member": member, "confirm": confirm, "names": names,
                        "removed": removed, "session": w.sessions, "end_at": end_at})
        assert (confirm is None) != (names is None)
        if names is not None and len(w.steps) in w.row_busy_steps:
            raise objects_store.StoreBusy("This app is busy. Try again in a moment.")
        row = mine(db, member, pid)
        if row is None:
            if names is not None and removed > 0:
                w.audits.append({"op": store.PARTIAL_OP, "member": member,
                                 "records_this_call": removed, "reason": "gone"})
                return store.DeleteStep(deleted=False, removed=0, names=tuple(names), gone=True)
            return None
        now = preview(pid, row)
        seen = tuple(sorted(o.name for o in now.objects)) if names is None else tuple(names)

        def end(this_call: int, reason: str, gone: int = 0) -> store.DeleteStep:
            w.audits.append({"op": store.PARTIAL_OP, "member": member,
                             "records_this_call": this_call, "reason": reason})
            left = None if w.preview_busy else preview(pid, row)
            return store.DeleteStep(deleted=False, removed=gone, names=seen, left=left,
                                    changed=reason == "changed",
                                    busy=reason == "busy" or left is None)

        if confirm is not None:
            if confirm != now.confirm:
                raise store.CountsChanged(now)
            if len(w.steps) in w.busy_steps:
                raise objects_store.StoreBusy("This app is busy. Try again in a moment.")
        else:
            if set(row.get("extra", [])) - set(names):
                return end(removed, "changed")
            if len(w.steps) in w.busy_steps:
                return end(removed, "busy")
        gone = min(w.chunk, row["records"])
        row["records"] -= gone
        w.clock += w.tick
        if gone >= w.chunk:
            if end_at is not None and objects_store._clock() + store.STEP_SECONDS > end_at:
                return end(removed + gone, "budget", gone)
            return store.DeleteStep(deleted=False, removed=gone, names=seen)
        del w.packages[pid]
        w.audits.append({"op": store.DELETE_OP, "member": member,
                         "records_this_call": removed + gone})
        return store.DeleteStep(deleted=True, removed=gone, names=seen)

    monkeypatch.setattr(packages, "_tenant_session", _session)
    monkeypatch.setattr(objects_store, "_clock", lambda: w.clock)
    monkeypatch.setattr(store, "get_package", get_package)
    monkeypatch.setattr(store, "delete_preview", delete_preview)
    monkeypatch.setattr(store, "delete_package", delete_package)

    async def record_partial(db: _DB, member: str, pid: str, this_call: int,
                             reason: str) -> None:
        w.audits.append({"op": store.PARTIAL_OP, "member": member,
                         "records_this_call": this_call, "reason": reason, "session": w.sessions})

    monkeypatch.setattr(store, "record_partial", record_partial)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    return w


def _client(world: SimpleNamespace, org: str | None = ORG, email: str = ANA) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)

    def _user() -> UserContext:
        world.org = org
        return UserContext(email=email, role=UserRole.EMPLOYEE,
                           access=build_access(["feature:studio"]), organization_id=org)

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


def _delete(c: TestClient, pid: str, body: Any = None, raw: bytes | None = None) -> Any:
    content = raw if raw is not None else json.dumps(body).encode("utf-8")
    return c.request("DELETE", f"/studio/packages/{pid}", content=content, headers=_JSON)


class _Request:
    """The stream of a body, with a count of the reads."""

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.reads = 0

    async def stream(self) -> Any:
        self.reads += 1
        yield self.raw


def _user(org: str = ORG, email: str = ANA) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:studio"]), organization_id=org)


@pytest.mark.parametrize("value", ["", "other-org", OTHER])
def test_with_the_switch_off_both_routes_are_404_and_read_no_byte(
        world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    pid = world.add()
    monkeypatch.setattr(get_settings(), "studio_orgs", value)
    req = _Request(b"{not json")

    async def go() -> list[int]:
        out = []
        for call in (packages.delete_preview(pid, user=_user()),
                     packages.delete_package(pid, request=req, response=Response(),
                                             user=_user())):
            try:
                await call
                out.append(200)
            except HTTPException as exc:
                out.append(exc.status_code)
        return out

    assert _run(go()) == [404, 404]
    assert req.reads == 0 and world.sessions == 0 and pid in world.packages


def test_a_bad_id_is_404_on_both_routes(world: SimpleNamespace) -> None:
    c = _client(world)
    assert c.get("/studio/packages/nope/delete-preview").json() == NOT_FOUND
    r = _delete(c, "nope", {"confirm": "a" * 64})
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.sessions == 0


def test_the_preview_names_counts_and_a_token_and_no_manifest(world: SimpleNamespace) -> None:
    pid = world.add(records=150_000)
    r = _client(world).get(f"/studio/packages/{pid}/delete-preview")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["package"] == {"id": pid, "name": "Visits", "slug": f"visits-{pid[:8]}"}
    assert got["objects"] == [{"name": "visit", "fields": 3, "records": 100_000,
                               "capped": True}]
    assert got["totals"] == {"objects": 1, "fields": 3, "records": 100_000,
                             "records_capped": True, "versions": 2, "installs": 1,
                             "other_member_installs": 0}
    assert got["confirm"] == world.preview(pid).confirm
    assert "Secret purpose" not in r.text and "manifest" not in got


def test_another_member_and_org_b_get_404_on_both_routes(world: SimpleNamespace) -> None:
    pid = world.add()
    token = world.preview(pid).confirm
    for c in (_client(world, email=BEN), _client(world, org=OTHER)):
        assert c.get(f"/studio/packages/{pid}/delete-preview").status_code == 404
        r = _delete(c, pid, {"confirm": token})
        assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.packages[pid]["records"] == 1200 and world.steps == []


def test_a_stranger_with_a_bad_body_gets_404_and_not_422(world: SimpleNamespace) -> None:
    """The owner read comes before the parse, so a bad body cannot tell a
    stranger that the package exists."""
    pid = world.add()
    assert _delete(_client(world, email=BEN), pid, raw=b"{not json").status_code == 404
    assert _delete(_client(world), pid, raw=b"{not json").status_code == 422


def test_a_body_past_4_kib_is_413_before_any_session(world: SimpleNamespace) -> None:
    pid = world.add()
    raw = json.dumps({"confirm": world.preview(pid).confirm, "pad": "x" * 5000}).encode()
    r = _delete(_client(world, email=BEN), pid, raw=raw)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "body_too_large"
    assert world.sessions == 0
    assert packages.MAX_DELETE_BODY_BYTES == 4096


@pytest.mark.parametrize("body", [{}, {"confirm": None}, {"confirm": 7}, {"confirm": "abc"},
                                  {"confirm": "z" * 64}, {"token": "a" * 64}, []])
def test_a_missing_or_malformed_token_is_422_and_deletes_nothing(
        world: SimpleNamespace, body: Any) -> None:
    pid = world.add()
    r = _delete(_client(world), pid, body)
    assert r.status_code == 422, r.text
    if isinstance(body, dict):
        assert r.json()["detail"]["errors"][0]["path"] == "$.confirm"
    assert world.steps == [] and world.packages[pid]["records"] == 1200


def test_an_empty_body_is_422(world: SimpleNamespace) -> None:
    pid = world.add()
    assert _delete(_client(world), pid, raw=b"").status_code == 422
    assert world.steps == []


def test_a_stale_token_is_409_with_the_new_preview_and_deletes_nothing(
        world: SimpleNamespace) -> None:
    pid = world.add()
    stale = world.preview(pid).confirm
    world.packages[pid]["records"] += 1
    r = _delete(_client(world), pid, {"confirm": stale})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "counts_changed" and detail["message"] == store.COUNTS_CHANGED_TEXT
    assert "Nothing was deleted" in detail["message"] and detail["records_removed"] == 0
    assert detail["preview"]["totals"]["records"] == 1201
    assert detail["preview"]["confirm"] == world.preview(pid).confirm != stale
    assert world.packages[pid]["records"] == 1201


def test_one_call_deletes_a_package_in_three_steps(world: SimpleNamespace) -> None:
    pid = world.add(records=1200)
    r = _delete(_client(world), pid, {"confirm": world.preview(pid).confirm.upper()})
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True, "package_id": pid, "records_removed": 1200}
    assert pid not in world.packages
    assert [s["confirm"] is not None for s in world.steps] == [True, False, False]
    assert [s["names"] for s in world.steps] == [None, ("visit",), ("visit",)]
    assert [s["removed"] for s in world.steps] == [0, 500, 1000]
    assert world.audits == [{"op": "package.delete", "member": ANA, "records_this_call": 1200}]


def test_each_step_runs_in_its_own_session(world: SimpleNamespace) -> None:
    """No lock stays between two steps: a step commits when its session
    closes, and the next step opens a new one."""
    pid = world.add(records=1200)
    _delete(_client(world), pid, {"confirm": world.preview(pid).confirm})
    sessions = [s["session"] for s in world.steps]
    assert len(set(sessions)) == len(sessions) == 3
    # The owner read, then one session for each step.
    assert world.sessions == 4


def test_past_the_budget_the_call_answers_202_with_a_new_token(world: SimpleNamespace) -> None:
    pid = world.add(records=1200)
    world.tick = packages.DELETE_BUDGET_SECONDS
    c = _client(world)
    r = _delete(c, pid, {"confirm": world.preview(pid).confirm})
    assert r.status_code == 202, r.text
    got = r.json()
    assert got == {"deleted": False, "package_id": pid, "remaining": 700,
                   "remaining_capped": False, "records_removed": 500,
                   "confirm": world.preview(pid).confirm}
    assert len(world.steps) == 1
    # The same DELETE with the new token goes on, and an old token is 409.
    assert _delete(c, pid, {"confirm": got["confirm"]}).status_code == 202
    assert _delete(c, pid, {"confirm": got["confirm"]}).status_code == 409
    world.tick = 0.0
    last = _delete(c, pid, {"confirm": world.preview(pid).confirm})
    assert last.status_code == 200 and pid not in world.packages
    # A repeat after the 200 names nothing.
    assert _delete(c, pid, {"confirm": got["confirm"]}).status_code == 404


def test_store_busy_on_the_first_step_is_503_with_retry_after(world: SimpleNamespace) -> None:
    pid = world.add()
    world.busy_steps = {1}
    r = _delete(_client(world), pid, {"confirm": world.preview(pid).confirm})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "store_busy"
    assert r.headers["Retry-After"] == packages.RETRY_AFTER
    assert world.packages[pid]["records"] == 1200


def test_store_busy_on_a_later_step_answers_202_because_the_earlier_steps_hold(
        world: SimpleNamespace) -> None:
    pid = world.add()
    world.busy_steps = {2}
    r = _delete(_client(world), pid, {"confirm": world.preview(pid).confirm})
    assert r.status_code == 202, r.text
    assert (r.json()["remaining"], r.json()["records_removed"]) == (700, 500)
    assert world.packages[pid]["records"] == 700


def test_the_route_passes_the_end_of_its_budget_to_the_store(world: SimpleNamespace) -> None:
    """P2-2. Each step gets the same end: the start of the call plus the
    budget, on the clock of the object store."""
    pid = world.add(records=1200)
    world.clock = 50.0
    _delete(_client(world), pid, {"confirm": world.preview(pid).confirm})
    assert {s["end_at"] for s in world.steps} == {50.0 + packages.DELETE_BUDGET_SECONDS}
    assert packages.DELETE_BUDGET_SECONDS == 8.0


def test_a_call_that_ends_with_records_left_writes_one_partial_row_and_logs_it(
        world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """P2-1. A 202 call writes one partial row (in its last step) and one
    log line, with the records of this call."""
    lines: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(packages, "_log", SimpleNamespace(
        info=lambda event, **kw: lines.append((event, kw)),
        warning=lambda event, **kw: lines.append((event, kw))))
    pid = world.add(records=1200)
    world.tick = packages.DELETE_BUDGET_SECONDS
    r = _delete(_client(world), pid, {"confirm": world.preview(pid).confirm})
    assert r.status_code == 202
    assert world.audits == [{"op": store.PARTIAL_OP, "member": ANA, "records_this_call": 500,
                             "reason": "budget"}]
    assert lines == [("studio.packages.delete_partial",
                      {"package_id": pid, "records": 500, "changed": False, "busy": False})]


def test_a_new_object_between_two_steps_is_409_with_the_new_preview(
        world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """P3-1. A publish between the first and the second step added
    ``invoices``. The call stops with 409, and the earlier step holds."""
    pid = world.add(records=1200)
    real = store.delete_package

    async def publish_after_one(db: Any, member: str, p: str, **kw: Any) -> Any:
        got = await real(db, member, p, **kw)
        world.packages[p].setdefault("extra", ["invoices"])
        return got

    monkeypatch.setattr(store, "delete_package", publish_after_one)
    r = _delete(_client(world), pid, {"confirm": world.preview(pid).confirm})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "counts_changed"
    # Fix round 2, item 2: the text and the key name the 500 that went.
    assert detail["records_removed"] == 500
    assert detail["message"] == (
        "The package changed during this delete. The delete stopped after it removed 500 "
        "records. Read the new preview, then confirm it to delete the rest.")
    assert "Nothing was deleted" not in detail["message"]
    assert [o["name"] for o in detail["preview"]["objects"]] == ["invoices", "visit"]
    assert world.packages[pid]["records"] == 700
    assert world.audits[-1]["reason"] == "changed"
    assert world.audits[-1]["records_this_call"] == 500


def test_a_busy_preview_at_the_end_of_a_call_is_503_and_the_partial_row_stays(
        world: SimpleNamespace) -> None:
    pid = world.add(records=1200)
    token = world.preview(pid).confirm
    world.tick = packages.DELETE_BUDGET_SECONDS
    world.preview_busy = True
    r = _delete(_client(world), pid, {"confirm": token})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "store_busy"
    assert world.audits[0]["op"] == store.PARTIAL_OP
    assert world.packages[pid]["records"] == 700


def test_a_busy_preview_is_503(world: SimpleNamespace) -> None:
    pid = world.add()
    world.preview_busy = True
    r = _client(world).get(f"/studio/packages/{pid}/delete-preview")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "store_busy"


def test_a_package_that_goes_during_the_call_is_404(world: SimpleNamespace,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    pid = world.add()
    real = store.delete_package

    async def gone_after_one(db: Any, member: str, p: str, **kw: Any) -> Any:
        got = await real(db, member, p, **kw)
        world.packages.pop(p, None)
        return got

    monkeypatch.setattr(store, "delete_package", gone_after_one)
    r = _delete(_client(world), pid, {"confirm": world.preview(pid).confirm})
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    # Fix round 2, item 1: the partial row names the 500 of the first step.
    assert world.audits == [{"op": store.PARTIAL_OP, "member": ANA, "records_this_call": 500,
                             "reason": "gone"}]


def test_a_busy_row_wait_of_a_later_step_is_503_and_writes_the_partial_row(
        world: SimpleNamespace) -> None:
    """Fix round 2, items 1 and 3. The row wait of step 2 is busy, and it ended
    the transaction of that step. The route writes the partial row of step 1
    in a new session, then answers 503."""
    pid = world.add(records=1200)
    world.row_busy_steps = {2}
    r = _delete(_client(world), pid, {"confirm": world.preview(pid).confirm})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "store_busy"
    assert r.headers["Retry-After"] == packages.RETRY_AFTER
    [row] = world.audits
    assert (row["records_this_call"], row["reason"]) == (500, "busy")
    assert row["session"] == world.steps[-1]["session"] + 1
    assert world.packages[pid]["records"] == 700


def test_a_busy_row_wait_of_the_first_step_is_503_and_writes_nothing(
        world: SimpleNamespace) -> None:
    pid = world.add(records=1200)
    world.busy_steps = {1}
    r = _delete(_client(world), pid, {"confirm": world.preview(pid).confirm})
    assert r.status_code == 503 and world.audits == []


def test_a_body_that_names_another_owner_or_org_changes_nothing(world: SimpleNamespace) -> None:
    pid = world.add(owner=ANA)
    token = world.preview(pid).confirm
    body = {"confirm": token, "owner_email": ANA, "member": ANA, "organization_id": ORG,
            "as_admin": True}
    r = _delete(_client(world, email=BEN), pid, body)
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert world.steps == [] and world.packages[pid]["records"] == 1200
    r = _delete(_client(world), pid, {**body, "owner_email": BEN})
    assert r.status_code == 200
    assert {s["member"] for s in world.steps} == {ANA}


# ── d. The fences (R7) ─────────────────────────────────────────────────────


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
    [fn] = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name]
    return fn


def test_each_delete_handler_checks_the_switch_first() -> None:
    tree = _tree(_PACKAGES_PY)
    for name in ("delete_preview", "delete_package"):
        body = list(_function(tree, name).body)
        if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        assert ast.unparse(body[0].test) == "not flags.studio_allowed(user.organization_id)"
        assert "404" in ast.unparse(body[0].body[0]), name


_APPS_DELETE = re.compile(r"\bDELETE\s+FROM\s+apps\b", re.I)


def _apps_delete_sites() -> dict[str, list[str]]:
    """Each string constant of apps/ and packages/ that deletes from ``apps``."""
    found: dict[str, list[str]] = {}
    for top in ("apps", "packages"):
        for path in (_ROOT / top).rglob("*.py"):
            parts = path.relative_to(_ROOT).parts
            if {".venv", "__pycache__", "node_modules", "tests"} & set(parts):
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
            if not _APPS_DELETE.search(source):
                continue
            for node in ast.walk(ast.parse(source)):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and _APPS_DELETE.search(node.value)):
                    found.setdefault(path.relative_to(_ROOT).as_posix(), []).append(node.value)
    return found


def test_only_delete_package_deletes_a_package() -> None:
    """R7 for "the one DELETE FROM apps of a package is in delete_package".
    The App Workshop delete reaches its row through ``load_app``, which skips a
    package, and the member purge keeps the ``apps`` rows of a leaver."""
    sites = _apps_delete_sites()
    assert set(sites) == {"apps/services/gateway/gateway/studio/packages_store.py",
                          "apps/services/gateway/gateway/routes/apps/lifecycle.py"}, sites
    tree = _tree(_STORE_PY)
    users = sorted(fn.name for fn in ast.walk(tree)
                   if isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef)
                   and any(isinstance(n, ast.Name) and n.id == "_DELETE_SQL"
                           for n in ast.walk(fn)))
    assert users == ["_finish_delete"]
    finishers = sorted(fn.name for fn in ast.walk(tree)
                       if isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef)
                       and any(isinstance(n, ast.Call) and ast.unparse(n.func) == "_finish_delete"
                               for n in ast.walk(fn)))
    assert finishers == ["delete_package"]
    from gateway.routes.admin import members
    from gateway.routes.apps import _common, lifecycle
    assert "format IS DISTINCT FROM 'manifest'" in inspect.getsource(_common.load_app)
    assert "get_app_or_404" in inspect.getsource(lifecycle.delete_app)
    assert "apps" not in {rows.table for rows in members._PURGE_DELETES}
    assert "apps" in {rows.table for rows in members._PURGE_KEEPS}


def test_the_versions_go_by_the_cascade_and_no_statement_names_them() -> None:
    """The A4 fence stays as it is: the store has no DELETE or UPDATE of
    ``app_versions``. The cascade from ``apps`` removes the versions."""
    source = _STORE_PY.read_text(encoding="utf-8")
    assert not re.search(r"DELETE\s+FROM\s+app_versions|UPDATE\s+app_versions", source, re.I)
    assert store._DELETE_SQL.startswith("DELETE FROM apps ")
    assert "app_versions" not in store._DELETE_SQL


def test_the_lock_order_is_in_the_source() -> None:
    """The ``apps`` row FOR UPDATE, then the org lock, then the purge, then the
    DELETE, then the audit, in one function and so one session."""
    src = inspect.getsource(store.delete_package)
    marks = ["_read_for_delete(db, m, pid)", "objects_store.lock_org(db)",
             "objects_store.purge_records(", "_finish_delete("]
    where = [src.find(m) for m in marks]
    assert -1 not in where and where == sorted(where), dict(zip(marks, where, strict=True))
    read = inspect.getsource(store._read_for_delete)
    assert "bounded_wait(db, ROW_LOCK_WAIT_MS)" in read
    assert "get_package(db, m, pid, for_update=True)" in read
    finish = inspect.getsource(store._finish_delete)
    assert -1 < finish.find("text(_DELETE_SQL)") < finish.find("await _audit(")


def test_the_audit_is_written_in_the_store_and_not_by_the_route() -> None:
    """The route writes no audit row of a delete: a row after the session
    could land without the delete, or the delete without the row."""
    tree = _tree(_PACKAGES_PY)
    for name in ("delete_preview", "delete_package"):
        names = {n.id for n in ast.walk(_function(tree, name)) if isinstance(n, ast.Name)}
        assert not {"record_app_audit", "record_app_audit_bound"} & names, name
    assert "await _audit(" in inspect.getsource(store._finish_delete)


def test_the_route_runs_one_session_for_each_step() -> None:
    """``store.delete_package`` runs inside an ``async with`` inside the loop,
    so each step commits before the next one."""
    fn = _function(_tree(_PACKAGES_PY), "delete_package")
    [loop] = [n for n in ast.walk(fn) if isinstance(n, ast.While)]
    withs = [n for n in ast.walk(loop) if isinstance(n, ast.AsyncWith)]
    assert any("store.delete_package" in ast.unparse(w) and "_tenant_session" in ast.unparse(w)
               for w in withs)
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "store.delete_package"]
    assert len(calls) == 1


def _client_timeout() -> float:
    """The timeout of the gateway client of the chat tools, read from the
    source of ``studio_tools._send``: the ``timeout`` of its
    ``httpx.AsyncClient``."""
    import acb_skills.studio_tools as tools
    tree = ast.parse(inspect.getsource(tools))
    [send] = [n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_send"]
    [call] = [n for n in ast.walk(send) if isinstance(n, ast.Call)
              and ast.unparse(n.func) == "httpx.AsyncClient"]
    [kw] = [k for k in call.keywords if k.arg == "timeout"]
    return float(ast.literal_eval(kw.value))


def test_the_worst_call_ends_before_the_chat_tool_gives_up() -> None:
    """P2-2. The next caller of the DELETE is the chat tool of A5c-2, and its
    client gives up at the timeout of ``studio_tools._send``.

    No step starts unless one more step fits before the end of the budget,
    and each purge stops at that end. So only the last step runs past the
    budget, with one count: the preview of a 202, or the counts of the final
    audit row. The first step also counts (the token check) before its
    purge, so the wait for the org lock and that count fit in the budget."""
    budget = packages.DELETE_BUDGET_SECONDS
    count = objects_store.COUNT_TIMEOUT_MS / 1000
    lock = objects_store.ORG_LOCK_WAIT_MS / 1000
    row = store.ROW_LOCK_WAIT_MS / 1000
    assert budget + count < _client_timeout()
    # Fix round 2, item 3: a step also waits for the apps row, with a bound.
    assert row + lock + objects_store.PURGE_TIMEOUT_MS / 1000 == store.STEP_SECONDS
    assert budget > store.STEP_SECONDS
    assert row + lock + count < budget


def test_the_route_module_still_runs_no_sql() -> None:
    for node in ast.walk(_tree(_PACKAGES_PY)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not re.search(r"\bDELETE\s+FROM\b", node.value, re.I), node.value[:60]


def test_the_r8_suite_is_named_in_the_ci_skip_guard() -> None:
    workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", workflow)
    assert block, "the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_studio_package_delete_r8\.py(?: \\)?$",
                     block.group(1), re.M)
