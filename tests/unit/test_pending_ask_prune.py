"""Task 3435 — the retention of ``chat_pending_ask``, hermetic.

Spec: ``project-docs/specs/chat_run_continuity.md`` §4.12.4 item 1 (the
retention of a row). Code: ``orchestrator.pending_ask`` (``prune``,
``prune_hook``, ``register_prune``) and the purge clause
``chat_pending_asks`` (``gateway/routes/admin/members.py``). The R8 twin is
``tests/unit/test_pending_ask_prune_r8.py``.

The checks here:

1. The policy constants: 7 days of grace, and a bound on each run.
2. The SQL: one statement. Each of its four parts names the bound org beside
   RLS. Only a row past ``expires_at`` changes, and only a row past the grace
   goes. The CTEs read the key only, never the stored call or the payload.
3. :func:`prune` sends the one statement, with the bound and the grace.
4. The hook never raises, and its log names the error class only.
5. The hook is in the job worker's list once, from the import on, and the
   gateway's start imports the module.
6. Only the prune and the purge delete from the table.
7. The R8 twin is in the ``must_run`` list.

**R7.** M1: drop the org term of a part. Check 2 goes red, and the R8 tests of
the bypass go red. M2: let the prune take a row that has not expired. Check 2
and R8 test c go red. M3: drop ``stored_call = NULL``. Check 2 and R8 test a
go red. M4: drop the org term of the purge clause. The clause test of
``test_admin_member_purge.py`` and R8 test e go red.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

import pytest

pending_ask = pytest.importorskip("orchestrator.pending_ask")

_ROOT = Path(__file__).resolve().parents[2]
_TODAY = date(2026, 11, 16)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ── 1. The policy ─────────────────────────────────────────────────────────


def test_the_policy_constants() -> None:
    assert pending_ask.PENDING_ASK_KEEP_DAYS == 7
    assert 0 < pending_ask.PENDING_ASK_PRUNE_BATCH <= 1000


def test_the_bound_org_is_the_one_text_of_the_gateway() -> None:
    from gateway.routes.tasks.settings import _BOUND_ORG

    assert pending_ask._BOUND_ORG == _BOUND_ORG


# ── 2. The SQL ────────────────────────────────────────────────────────────


def _part(name: str) -> str:
    """The body of one CTE of the prune statement."""
    sql = pending_ask._PRUNE_SQL
    m = re.search(rf"\b{name} AS (?:MATERIALIZED )?\((.*?)\)(?:, \w+ AS \(| SELECT)", sql, re.S)
    assert m, name
    return m.group(1)


def test_each_part_names_the_bound_org() -> None:
    org = f"organization_id = {pending_ask._BOUND_ORG}"
    for name in ("doomed", "stale", "deleted", "cleared"):
        assert org in _part(name), name
    assert pending_ask._PRUNE_SQL.count(org) == 4


def test_only_an_expired_row_changes() -> None:
    sql = pending_ask._PRUNE_SQL
    assert "expires_at >" not in sql
    grace = "expires_at <= now() - make_interval(days => :keep)"
    assert grace in _part("doomed") and grace in _part("deleted")
    assert "expires_at <= now() AND" in _part("stale")
    assert "expires_at <= now() AND" in _part("cleared")
    # The state of a row plays no part: an expired row moves in no state, so
    # each state goes at the grace.
    assert "state" not in sql


def test_the_clear_sets_the_stored_call_to_null_and_the_two_sets_stay_apart() -> None:
    cleared = _part("cleared")
    assert "SET stored_call = NULL" in cleared
    assert "stored_call IS NOT NULL" in cleared
    assert "NOT EXISTS (SELECT 1 FROM doomed" in _part("stale")
    assert "DELETE FROM chat_pending_ask" in _part("deleted")


def test_each_part_is_bounded() -> None:
    # Two parts read ``doomed``, so it is MATERIALIZED: one LIMIT, one set.
    assert "doomed AS MATERIALIZED (" in pending_ask._PRUNE_SQL
    assert _part("doomed").rstrip().endswith("LIMIT :batch")
    assert _part("stale").rstrip().endswith("LIMIT :batch")


def test_the_prune_reads_the_key_only() -> None:
    sql = pending_ask._PRUNE_SQL
    for name in ("doomed", "stale"):
        assert re.match(r"\s*SELECT request_id\s+FROM chat_pending_ask", _part(name)), name
    assert "payload" not in sql
    assert "RETURNING 1" in _part("deleted") and "RETURNING 1" in _part("cleared")


# ── 3. The call ───────────────────────────────────────────────────────────


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _Db:
    """An async session that records each statement."""

    def __init__(self, row: Any = (3, 5), fail: BaseException | None = None) -> None:
        self.row = row
        self.fail = fail
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.nested = 0

    @asynccontextmanager
    async def begin_nested(self) -> Any:
        self.nested += 1
        yield self

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        self.sent.append((str(stmt), dict(params or {})))
        if self.fail is not None:
            raise self.fail
        return _Result(self.row)


def test_the_prune_sends_one_statement_with_the_bound_and_the_grace() -> None:
    db = _Db()
    assert _run(pending_ask.prune(db)) == (3, 5)
    assert db.sent == [(pending_ask._PRUNE_SQL, {
        "keep": pending_ask.PENDING_ASK_KEEP_DAYS,
        "batch": pending_ask.PENDING_ASK_PRUNE_BATCH})]


def test_no_row_back_is_no_change() -> None:
    assert _run(pending_ask.prune(_Db(row=None))) == (0, 0)


def test_the_hook_runs_in_a_savepoint() -> None:
    db = _Db()
    _run(pending_ask.prune_hook(db, _TODAY))
    assert db.nested == 1 and len(db.sent) == 1


# ── 4. Never raises, and logs the class only ──────────────────────────────


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def warning(self, event: str, **kw: Any) -> None:
        self.lines.append(("warning", event, kw))

    def info(self, event: str, **kw: Any) -> None:
        self.lines.append(("info", event, kw))


class _Secret(Exception):
    pass


@pytest.mark.parametrize("db", [
    _Db(fail=_Secret("DETAIL: Key (stored_call)=(lead@x.test) failed")),
    _Db(fail=RuntimeError("phone +91 90000 00000")),
    object(),
])
def test_the_hook_never_raises_and_logs_the_class_only(db: Any, monkeypatch) -> None:
    log = _Log()
    monkeypatch.setattr(pending_ask, "_log", log)
    _run(pending_ask.prune_hook(db, _TODAY))
    assert [(lvl, ev) for lvl, ev, _ in log.lines] == [("warning", "pending_ask.prune_failed")]
    [(_, _, kw)] = log.lines
    assert set(kw) == {"error"}
    assert kw["error"] in {"_Secret", "RuntimeError", "AttributeError"}
    dumped = json.dumps(log.lines, default=str)
    assert "lead@x.test" not in dumped and "90000" not in dumped


def test_a_quiet_prune_logs_nothing_and_a_busy_one_logs_the_counts(monkeypatch) -> None:
    log = _Log()
    monkeypatch.setattr(pending_ask, "_log", log)
    _run(pending_ask.prune_hook(_Db(row=(0, 0)), _TODAY))
    assert log.lines == []
    _run(pending_ask.prune_hook(_Db(row=(2, 1)), _TODAY))
    assert log.lines == [("info", "pending_ask.pruned", {"deleted": 2, "cleared": 1})]


# ── 5. The registration ───────────────────────────────────────────────────


def test_the_hook_is_registered_once_from_the_import_on() -> None:
    from gateway.jobs import worker

    assert pending_ask.prune_hook in worker.prune_hooks
    assert pending_ask.register_prune() is True
    assert pending_ask.register_prune() is True
    assert worker.prune_hooks.count(pending_ask.prune_hook) == 1


def test_the_gateway_start_imports_the_module() -> None:
    """A route imports ``pending_ask`` late. The start imports it before the
    job worker starts, so the hook is in place from the first tick."""
    src = (_ROOT / "apps/services/gateway/gateway/main.py").read_text(encoding="utf-8")
    at = src.index("from orchestrator import pending_ask as _pending_ask")
    assert at < src.index("from gateway.jobs.worker import start_job_worker")


# ── 6. The only deletes ───────────────────────────────────────────────────


_DELETE = re.compile(r"DELETE\s+FROM\s+chat_pending_ask\b", re.I)


def test_only_the_prune_deletes_from_the_table_in_code() -> None:
    hits = []
    for top in ("apps", "packages"):
        for path in (_ROOT / top).rglob("*.py"):
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            if _DELETE.search(path.read_text(encoding="utf-8", errors="replace")):
                hits.append(path.relative_to(_ROOT).as_posix())
    assert hits == ["apps/services/orchestrator/orchestrator/pending_ask.py"]


def test_the_purge_names_the_table_once_and_keeps_none() -> None:
    from gateway.routes.admin.members import _PURGE_DELETES, _PURGE_KEEPS

    assert [r.key for r in _PURGE_DELETES if r.table == "chat_pending_ask"] == [
        "chat_pending_asks"]
    assert not [r for r in _PURGE_KEEPS if r.table == "chat_pending_ask"]


# ── 7. The R8 twin runs ───────────────────────────────────────────────────


def test_the_r8_twin_is_in_the_must_run_list() -> None:
    fences = json.loads((_ROOT / "tests/fences.json").read_text(encoding="utf-8"))
    must_run = fences["r8"]["must_run"]
    assert "tests/unit/test_pending_ask_prune_r8.py" in must_run
    twin = (_ROOT / "tests/unit/test_pending_ask_prune_r8.py").read_text(encoding="utf-8")
    assert "pytestmark = _DB_GATE" in twin
