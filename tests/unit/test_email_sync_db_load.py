"""Two fences on the load the email sync puts on the database (2026-10-28).

1. ``_write_messages`` reads the stored categories of a batch in ONE query.
   Production ran the per-message read 32 million times in nine days.
2. The sync loop runs the new-mail pipeline only when the cycle wrote a row,
   or when ``PIPELINE_IDLE_SECS`` passed. It ran on every cycle that listed
   any mail before, so a quiet mailbox ran the sweep every five minutes.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from email_ingestion import scheduler as sched

FLOOR = datetime(2000, 1, 1, tzinfo=UTC)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None


class _Db:
    """Records each statement. Answers the categories reads from ``stored``."""

    def __init__(self, stored: dict[str, list[str]]) -> None:
        self.stored = stored
        self.sql: list[str] = []

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = str(stmt)
        self.sql.append(sql)
        if "ANY(:pids)" in sql and "categories" in sql:
            return _Result([SimpleNamespace(provider_message_id=p, categories=c)
                            for p, c in self.stored.items() if p in params["pids"]])
        if "categories" in sql:
            c = self.stored.get(params["pid"])
            return _Result([SimpleNamespace(categories=c)] if c is not None else [])
        return _Result([])


def _msg(pid: str) -> Any:
    return SimpleNamespace(provider_message_id=pid, subject="s", received_at=None)


@pytest.fixture
def upserts(monkeypatch) -> list[str]:
    seen: list[str] = []

    async def _upsert(db: Any, account_id: str, msg: Any, **_kw: Any) -> bool:
        seen.append(msg.provider_message_id)
        # "b" is current: the guard skips it.
        return msg.provider_message_id != "b"

    monkeypatch.setattr(sched, "upsert_message", _upsert)
    return seen


async def test_one_categories_read_covers_the_batch(upserts) -> None:
    db = _Db({"a": ["News"], "b": []})
    kept, changes, changed = await sched._write_messages(
        db, "acc", [_msg("a"), _msg("b"), _msg("new")], FLOOR,
        learn_labels=True, reclaim=False)

    reads = [s for s in db.sql if "categories" in s]
    assert len(reads) == 1 and "ANY(:pids)" in reads[0]
    assert [(m.provider_message_id, old) for m, old in changes] == [
        ("a", ["News"]), ("b", [])]
    assert len(kept) == 3
    assert changed == 2  # "b" was current


async def test_a_repeated_id_reads_its_row_again(upserts) -> None:
    db = _Db({"a": ["News"]})
    await sched._write_messages(db, "acc", [_msg("a"), _msg("a")], FLOOR,
                                learn_labels=True, reclaim=False)
    reads = [s for s in db.sql if "categories" in s]
    assert len(reads) == 2 and "ANY(:pids)" not in reads[1]


async def test_without_learn_labels_no_categories_read(upserts) -> None:
    db = _Db({"a": ["News"]})
    await sched._write_messages(db, "acc", [_msg("a")], FLOOR, reclaim=False)
    assert not [s for s in db.sql if "categories" in s]


def test_the_pipeline_runs_on_a_change() -> None:
    assert sched._pipeline_due({"synced": 100, "changed": 1}, time.monotonic())


def test_a_relist_with_no_change_skips_until_the_idle_window() -> None:
    now = time.monotonic()
    assert not sched._pipeline_due({"synced": 100, "changed": 0}, now)
    old = now - sched.PIPELINE_IDLE_SECS - 1
    assert sched._pipeline_due({"synced": 100, "changed": 0}, old)
    assert sched._pipeline_due({"synced": 100, "changed": 0}, None)


def test_nothing_listed_or_an_old_result_shape() -> None:
    assert not sched._pipeline_due({"synced": 0, "changed": 0}, None)
    assert not sched._pipeline_due({"error": "x"}, None)
    assert sched._pipeline_due({"synced": 3}, time.monotonic())
