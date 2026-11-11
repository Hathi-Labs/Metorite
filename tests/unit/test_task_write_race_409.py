"""Task 2427: a task write that loses a deadlock answers 409, never 500.

Hermetic: the guard is a pure wrapper. The source fence names the five
routes that lock several tasks, so a new one cannot drop the guard.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.routes.projects import core

ROUTES = Path(__file__).resolve().parents[2] / "apps/services/gateway/gateway/routes/projects"


class _Pg(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def _run(route):
    return asyncio.run(core.transient_as_409(route)())


@pytest.mark.parametrize("code", sorted(core.TRANSIENT_SQLSTATES))
def test_transient_sqlstate_answers_409(code):
    async def route():
        raise _Pg(code)

    with pytest.raises(HTTPException) as info:
        _run(route)
    assert info.value.status_code == 409
    assert info.value.detail == core.TASK_RACE_DETAIL


def test_wrapped_driver_error_answers_409():
    async def route():
        try:
            raise _Pg("40P01")
        except _Pg as inner:
            outer = RuntimeError("wrapped")
            outer.orig = inner
            raise outer

    with pytest.raises(HTTPException) as info:
        _run(route)
    assert info.value.status_code == 409


def test_other_errors_and_http_errors_pass_through():
    async def boom():
        raise ValueError("x")

    async def refuse():
        raise HTTPException(status_code=404, detail="no")

    with pytest.raises(ValueError):
        _run(boom)
    with pytest.raises(HTTPException) as info:
        _run(refuse)
    assert info.value.status_code == 404


@pytest.mark.parametrize(
    ("file", "decorator_line"),
    [
        ("move.py", '@router.post("/tasks/move")'),
        ("bulk.py", '@router.post("/tasks/bulk")'),
        ("merge.py", '@router.post("/tasks/{task_id}/merge")'),
        ("tasks.py", '@router.delete("/tasks/{task_id}")'),
        ("tasks.py", '@router.post("/tasks/{task_id}/agent-delete")'),
        # H-409 item 1 (task 748): a close that ends an agent run refuses a
        # run that an agent write holds (NOWAIT, 55P03), and answers 409.
        ("tasks.py", '@router.patch("/tasks/{task_id}")'),
        ("tasks.py", '@router.post("/tasks/{task_id}/archive")'),
        ("personal.py", '@router.delete("/my/tasks/{task_id}")'),
        ("imports.py", '@router.post("/import/runs/{run_id}/discard"'),
    ],
)
def test_route_carries_the_guard(file, decorator_line):
    text = (ROUTES / file).read_text(encoding="utf-8")
    i = text.index(decorator_line)
    assert "@transient_as_409" in text[i : text.index("async def", i)]


def test_import_discard_locks_in_id_order():
    text = (ROUTES / "import_discard.py").read_text(encoding="utf-8")
    assert len(re.findall(r"ORDER BY id FOR UPDATE", text)) == 2
