"""WS-STU J2 / WS-PKG PKG-11b-2 — the approval writer, with no database (hermetic).

Spec: ``project-docs/specs/package_host.md`` §6.2 ("The approval record") and
the PKG-11b-2 row. Code: ``gateway/studio/packages_store.py``
(``request_org_approval``, ``approve_version``, ``reject_version``). The R8
twin is ``tests/unit/test_studio_package_approval_r8.py``.

What this suite holds:

a. The moves: ``auto`` to ``pending``, then ``pending`` to ``approved`` or
   ``rejected``. A second ask of a ``pending`` version writes nothing.
b. The refusals: a wrong state, the publisher, the owner, no right, and an
   unknown version. Each writes nothing.
c. The SQL: a locked read, then one UPDATE guarded by the org, the package,
   the version and the expected state. The UPDATE sets ``review_status`` only.
d. The audit row: one row, kind ``action``, with ``review``, ``by`` and
   ``requested_by``, after the UPDATE.
e. A move names one version, and never another.

The mutations (R7) are in ``MUTATIONS`` below.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.studio import packages_store as store

PID = str(uuid.uuid4())
OWNER = "olga@x.test"
PUBLISHER = "olga@x.test"
APPROVER = "abe@x.test"

MUTATIONS = """
Each result was run on 2026-11-07 with this suite and the R8 twin. Commit
first, and restore with git after.

* M-1. ``approve_version`` drops the self rule. ``test_the_publisher_cannot_approve``
  and ``test_the_owner_cannot_approve`` go red, and so does
  ``test_r8_the_publisher_and_the_owner_cannot_approve``.
* M-2. ``_REVIEW_MOVE_SQL`` drops ``AND review_status = :expected``.
  ``test_the_update_is_guarded_by_the_org_and_the_expected_state`` and the
  writer fence of ``test_studio_packages_store.py`` go red.
* M-3. ``_REVIEW_MOVE_SQL`` drops ``organization_id``.
  ``test_the_update_is_guarded_by_the_org_and_the_expected_state`` and the
  writer fence go red.
* M-4. ``_move_review`` skips ``_audit``. ``test_an_approval_writes_one_audit_row_after_the_update``
  and ``test_r8_request_then_approve_round_trip`` go red.
* M-5. ``approve_version`` skips ``_right``. ``test_no_right_refuses_before_any_statement``
  goes red.
* M-6. ``_REVIEW_MOVE_SQL`` also sets ``release_notes``. The writer fence of
  ``test_studio_packages_store.py`` and ``test_the_update_sets_review_status_only``
  go red.
"""


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeDb:
    """Records each statement. Answers by the kind of statement."""

    def __init__(self, *, state: str | None = "pending", published_by: str = PUBLISHER,
                 owner: str = OWNER, requester: str | None = OWNER,
                 update_rows: int = 1) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.state = state
        self.published_by = published_by
        self.owner = owner
        self.requester = requester
        self.update_rows = update_rows

    def _version_row(self, review: str) -> tuple[Any, ...]:
        return (PID, 3, {"id": "quotes"}, "a" * 64, "", self.published_by,
                datetime.now(UTC), review)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        p = dict(params or {})
        self.calls.append((sql, p))
        if sql == store._REVIEW_READ_SQL:
            if self.state is None:
                return _Result([])
            return _Result([(p["v"], self.published_by, self.state, self.owner)])
        if sql == store._REVIEW_MOVE_SQL:
            return _Result([self._version_row(p["to"])] * self.update_rows)
        if sql == store._REVIEW_REQUESTER_SQL:
            return _Result([] if self.requester is None else [(self.requester,)])
        if sql == store._VERSION_GET_SQL:
            return _Result([self._version_row(self.state or "auto")])
        return _Result([])

    def sqls(self) -> list[str]:
        return [c[0] for c in self.calls]

    def writes(self) -> list[tuple[str, dict[str, Any]]]:
        return [c for c in self.calls if c[0].lstrip().startswith(("UPDATE", "INSERT", "DELETE"))]


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _approve(db: _FakeDb, who: str = APPROVER, can: Any = True, v: int = 3) -> Any:
    return _run(store.approve_version(db, who, PID, v, can_publish=can))


def _reject(db: _FakeDb, who: str = APPROVER, can: Any = True) -> Any:
    return _run(store.reject_version(db, who, PID, 3, can_publish=can))


def _request(db: _FakeDb, who: str = OWNER) -> Any:
    return _run(store.request_org_approval(db, who, PID, 3))


# ── a. The moves ──────────────────────────────────────────────────────────


def test_the_move_table_is_closed() -> None:
    assert dict(store.REVIEW_MOVES) == {
        "request": ("auto", "pending"),
        "approve": ("pending", "approved"),
        "reject": ("pending", "rejected"),
    }


def test_a_request_moves_auto_to_pending() -> None:
    db = _FakeDb(state="auto")
    got = _request(db)
    assert got is not None and got.review_status == "pending"
    [(_sql, p)] = [c for c in db.calls if c[0] == store._REVIEW_MOVE_SQL]
    assert (p["expected"], p["to"], p["v"], p["id"]) == ("auto", "pending", 3, PID)


def test_a_second_request_of_a_pending_version_writes_nothing() -> None:
    db = _FakeDb(state="pending")
    got = _request(db)
    assert got is not None and got.review_status == "pending"
    assert db.writes() == []


@pytest.mark.parametrize("state", ["approved", "rejected"])
def test_a_request_of_a_decided_version_is_a_conflict(state: str) -> None:
    db = _FakeDb(state=state)
    with pytest.raises(store.ApprovalConflict) as exc:
        _request(db)
    assert exc.value.current == state and state in str(exc.value)
    assert db.writes() == []


def test_a_request_by_another_member_finds_nothing() -> None:
    db = _FakeDb(state="auto")
    assert _request(db, who=APPROVER) is None
    assert db.writes() == []


def test_an_approval_moves_pending_to_approved() -> None:
    db = _FakeDb(state="pending")
    got = _approve(db)
    assert got is not None and got.review_status == "approved" and got.version == 3
    [(_sql, p)] = [c for c in db.calls if c[0] == store._REVIEW_MOVE_SQL]
    assert (p["expected"], p["to"]) == ("pending", "approved")


def test_a_reject_moves_pending_to_rejected() -> None:
    db = _FakeDb(state="pending")
    got = _reject(db)
    assert got is not None and got.review_status == "rejected"
    [(_sql, p)] = [c for c in db.calls if c[0] == store._REVIEW_MOVE_SQL]
    assert (p["expected"], p["to"]) == ("pending", "rejected")


@pytest.mark.parametrize("who", [PUBLISHER, OWNER])
def test_the_publisher_or_the_owner_can_reject(who: str) -> None:
    db = _FakeDb(state="pending")
    got = _reject(db, who=who)
    assert got is not None and got.review_status == "rejected"


# ── b. The refusals ───────────────────────────────────────────────────────


@pytest.mark.parametrize("state", ["auto", "approved", "rejected"])
def test_an_approval_of_a_version_that_is_not_pending_is_a_conflict(state: str) -> None:
    db = _FakeDb(state=state)
    with pytest.raises(store.ApprovalConflict) as exc:
        _approve(db)
    assert exc.value.current == state
    assert db.writes() == []


@pytest.mark.parametrize("state", ["auto", "approved", "rejected"])
def test_a_reject_of_a_version_that_is_not_pending_is_a_conflict(state: str) -> None:
    db = _FakeDb(state=state)
    with pytest.raises(store.ApprovalConflict):
        _reject(db)
    assert db.writes() == []


def test_the_publisher_cannot_approve() -> None:
    db = _FakeDb(state="pending", published_by="pat@x.test", owner=OWNER)
    with pytest.raises(store.ApprovalRefused) as exc:
        _approve(db, who=" Pat@X.test ")
    assert str(exc.value) == store.SELF_APPROVAL
    assert db.writes() == []


def test_the_owner_cannot_approve() -> None:
    db = _FakeDb(state="pending", published_by="pat@x.test", owner=OWNER)
    with pytest.raises(store.ApprovalRefused):
        _approve(db, who=OWNER.upper())
    assert db.writes() == []


@pytest.mark.parametrize("can", [False, None, 1, "yes", object()])
def test_no_right_refuses_before_any_statement(can: Any) -> None:
    for call in (_approve, _reject):
        db = _FakeDb(state="pending")
        with pytest.raises(store.ApprovalRefused) as exc:
            call(db, can=can)
        assert str(exc.value) == store.NO_PUBLISH_RIGHT
        assert db.calls == []


def test_an_unknown_version_answers_none_and_writes_nothing() -> None:
    for call in (_approve, _reject, _request):
        db = _FakeDb(state=None)
        assert call(db) is None
        assert db.writes() == []


@pytest.mark.parametrize("bad", [0, -1, 2**31, True, "3", 1.0])
def test_a_bad_version_number_is_refused_before_any_statement(bad: Any) -> None:
    db = _FakeDb()
    with pytest.raises(store.PackageStoreError):
        _approve(db, v=bad)
    assert db.calls == []


def test_an_update_that_changes_no_row_is_a_conflict_with_no_audit() -> None:
    db = _FakeDb(state="pending", update_rows=0)
    with pytest.raises(store.ApprovalConflict) as exc:
        _approve(db)
    assert exc.value.current is None
    assert not any("app_audit" in sql and sql.startswith("INSERT") for sql in db.sqls())


# ── c. The SQL ─────────────────────────────────────────────────────────────


def test_the_update_is_guarded_by_the_org_and_the_expected_state() -> None:
    sql = store._REVIEW_MOVE_SQL
    where = sql[sql.index(" WHERE "):]
    assert f"organization_id = {store._BOUND_ORG}" in where
    assert "app_id = CAST(:id AS uuid)" in where
    assert "AND version = :v" in where
    assert "AND review_status = :expected" in where


def test_the_update_sets_review_status_only() -> None:
    sql = store._REVIEW_MOVE_SQL
    set_clause = sql[sql.index(" SET ") + 5:sql.index(" WHERE ")]
    assert set_clause == "review_status = :to"
    for column in ("manifest", "bundle_html", "bundle_sha256", "release_notes",
                   "scope_set_hash", "published_by", "published_at", "version ="):
        assert column not in set_clause


def test_the_read_locks_the_version_row_in_the_bound_org() -> None:
    sql = store._REVIEW_READ_SQL
    assert sql.endswith("FOR UPDATE OF v")
    assert f"a.organization_id = {store._BOUND_ORG}" in sql
    assert f"v.organization_id = {store._BOUND_ORG}" in sql
    assert "a.format = 'manifest'" in sql
    # The approver is not the owner, so the read does not filter on a member.
    assert "owner_email = :m" not in sql


def test_each_move_reads_the_locked_row_before_the_update() -> None:
    for call, state in ((_approve, "pending"), (_reject, "pending"), (_request, "auto")):
        db = _FakeDb(state=state)
        call(db)
        sqls = db.sqls()
        assert sqls[0] == store._REVIEW_READ_SQL
        assert sqls.index(store._REVIEW_READ_SQL) < sqls.index(store._REVIEW_MOVE_SQL)


def test_no_session_site_and_no_org_parameter() -> None:
    import inspect
    for fn in (store.request_org_approval, store.approve_version, store.reject_version):
        params = inspect.signature(fn).parameters
        assert "org" not in params and "organization_id" not in params
    for sql in (store._REVIEW_READ_SQL, store._REVIEW_MOVE_SQL, store._REVIEW_REQUESTER_SQL):
        assert ":org" not in sql


# ── d. The audit row ───────────────────────────────────────────────────────


def _audit_rows(db: _FakeDb) -> list[dict[str, Any]]:
    return [p for sql, p in db.calls if sql == store._AUDIT_SQL]


def test_an_approval_writes_one_audit_row_after_the_update() -> None:
    db = _FakeDb(state="pending", requester="rita@x.test")
    _approve(db)
    sqls = db.sqls()
    assert sqls.index(store._REVIEW_MOVE_SQL) < sqls.index(store._AUDIT_SQL)
    [row] = _audit_rows(db)
    assert (row["id"], row["v"], row["m"], row["kind"]) == (PID, 3, APPROVER, "action")
    assert json.loads(row["detail"]) == {
        "op": store.REVIEW_OP, "review": "approved", "by": APPROVER,
        "requested_by": "rita@x.test"}


def test_a_request_writes_one_audit_row_with_the_owner_as_requester() -> None:
    db = _FakeDb(state="auto")
    _request(db)
    [row] = _audit_rows(db)
    assert row["m"] == OWNER and row["kind"] == "action"
    assert json.loads(row["detail"]) == {
        "op": store.REVIEW_OP, "review": "pending", "by": OWNER, "requested_by": OWNER}


def test_a_reject_writes_one_audit_row() -> None:
    db = _FakeDb(state="pending")
    _reject(db)
    [row] = _audit_rows(db)
    assert json.loads(row["detail"])["review"] == "rejected"


def test_with_no_request_row_the_requester_is_the_owner() -> None:
    db = _FakeDb(state="pending", requester=None)
    _approve(db)
    [row] = _audit_rows(db)
    assert json.loads(row["detail"])["requested_by"] == OWNER


def test_the_kind_is_one_that_the_audit_check_allows() -> None:
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[2] / "infra" / "postgres"
           / "114_custom_apps.sql").read_text(encoding="utf-8")
    check = re.search(r"kind TEXT NOT NULL\s+CHECK \(kind IN \(([^)]*)\)\)", sql)
    assert check is not None and "'action'" in check.group(1)


# ── e. One version only ─────────────────────────────────────────────────────


def test_a_move_names_one_version_and_writes_one_row() -> None:
    db = _FakeDb(state="pending")
    _approve(db)
    writes = db.writes()
    assert [w[0] for w in writes] == [store._REVIEW_MOVE_SQL, store._AUDIT_SQL]
    assert writes[0][1]["v"] == 3
    assert writes[1][1]["v"] == 3
