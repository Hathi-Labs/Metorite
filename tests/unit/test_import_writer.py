"""WS-41 I-3 — the writer retries a transient lock error, and nothing else.

Spec: ``project-docs/specs/project_import.md`` §7.3. The writes themselves are
proved on a real Postgres by ``tests/live/live_ws41_writer.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from gateway.routes.projects import import_writer


class _Driver(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class _Wrapped(Exception):
    """SQLAlchemy's shape: `.orig` holds an adapter error whose cause is the
    driver's exception."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__("wrapped")
        adapter = Exception("adapter")
        adapter.__cause__ = _Driver(sqlstate)
        self.orig = adapter


@pytest.mark.parametrize("code", ["40P01", "40001", "55P03"])
def test_a_deadlock_serialization_or_lock_timeout_is_transient(code: str) -> None:
    assert import_writer.is_transient(_Wrapped(code))


@pytest.mark.parametrize("code", ["23505", "23503", "42P01", "22P02"])
def test_a_constraint_or_schema_error_is_not(code: str) -> None:
    assert not import_writer.is_transient(_Wrapped(code))
    assert not import_writer.is_transient(ValueError("x"))


async def test_a_transient_error_is_retried_until_it_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_writer.asyncio, "sleep", _no_sleep)
    attempts: list[int] = []

    async def batch() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise _Wrapped("40P01")
        return "committed"

    assert await import_writer._retrying("batch 1", "run", batch) == "committed"
    assert len(attempts) == 3


async def test_a_real_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(import_writer.asyncio, "sleep", _no_sleep)
    attempts: list[int] = []

    async def batch() -> None:
        attempts.append(1)
        raise _Wrapped("23505")

    with pytest.raises(_Wrapped):
        await import_writer._retrying("batch 1", "run", batch)
    assert len(attempts) == 1


async def test_the_retries_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(import_writer.asyncio, "sleep", _no_sleep)
    attempts: list[int] = []

    async def batch() -> None:
        attempts.append(1)
        raise _Wrapped("40P01")

    with pytest.raises(_Wrapped):
        await import_writer._retrying("batch 1", "run", batch)
    assert len(attempts) == import_writer.RETRIES


async def _no_sleep(_seconds: Any) -> None:
    return None


def test_the_writer_never_emits_or_notifies() -> None:
    """§6.10 — an import of two thousand tasks runs no workflow, fires no
    automation and pings nobody. The routes emit and notify AFTER calling the
    shared helpers; the writer calls the helpers and nothing else. The live
    test proves the result (no notification row); this proves the cause."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(import_writer.__file__).read_text(encoding="utf-8"))
    called = {
        getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    for name in ("emit", "notify", "emit_event", "fan_out"):
        assert name not in called and name not in imported, name


class _TagDB:
    """Answers the two statements `_fit_tags` sends: the registry rows and
    the registry count."""

    def __init__(self, registered: list[str], count: int) -> None:
        self.registered = registered
        self.count = count

    async def execute(self, statement: Any, params: Any = None) -> Any:
        from types import SimpleNamespace

        sql = str(statement)
        rows = [
            SimpleNamespace(name=n, project_id="r", organization_id=None) for n in self.registered
        ]

        class _R:
            def fetchall(_self) -> list[Any]:
                return rows

            def scalar(_self) -> int:
                return self.count

        _ = sql
        return _R()


async def test_tags_that_break_a_cap_are_dropped_not_fatal() -> None:
    """The reviewer's P1: a tag cap failed a confirmed run mid-way. Now the
    writer fits the tags and counts what it drops."""
    from gateway.routes.projects.tags import MAX_TAG, MAX_TAGS_PER_PROJECT, MAX_TAGS_PER_TASK

    long_tag = "x" * (MAX_TAG + 1)
    many = [f"t{i}" for i in range(MAX_TAGS_PER_TASK + 5)]
    fitted, dropped = await import_writer._fit_tags(_TagDB([], 0), "r", [long_tag, *many])
    assert len(fitted) == MAX_TAGS_PER_TASK and long_tag not in fitted
    assert dropped == 1 + 5

    # A full registry keeps the tags it knows and drops the new ones.
    full = _TagDB(["known"], MAX_TAGS_PER_PROJECT)
    fitted, dropped = await import_writer._fit_tags(full, "r", ["Known", "fresh"])
    assert fitted == ["Known"] and dropped == 1
