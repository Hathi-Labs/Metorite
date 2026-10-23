"""OP-7 / D-OP-10 — ``GET /projects/tasks/{id}/subtree``.

The delete dialog and the chat card read their subtasks here. The route shows
the descendants the caller can see, archived ones too, and counts the hidden
ones without a title. Both facts must match what ``delete_task_in`` takes with
``delete_all``. Hermetic: ``load_subtree`` is the real SQL's seam, and R8 holds
it in ``test_mcp_edge_delete.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

from gateway.routes.projects import tasks


class _Session:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


def _row(tid, parent, title, *, visible=True, archived=None):
    return SimpleNamespace(
        id=tid, parent_task_id=parent, title=title, status_name="To do",
        status_category="todo", visible=visible, archived_at=archived,
    )


async def test_the_route_shows_visible_rows_and_counts_the_hidden(monkeypatch) -> None:
    checked: list[str] = []

    async def resolve_visibility(db, user):
        return SimpleNamespace(params={})

    async def load_visible_task(db, vis, task_id):
        checked.append(task_id)
        return SimpleNamespace(id=task_id)

    async def load_subtree(db, vis, task_id):
        return [
            _row("a", "T", "Write the notes"),
            _row("b", "T", "Book the demo", archived="2026-09-01"),
            _row("secret", "T", "Hidden", visible=False),
            _row("a1", "a", "Proofread"),
        ]

    monkeypatch.setattr(tasks, "_tenant_session", _Session)
    monkeypatch.setattr(tasks, "resolve_visibility", resolve_visibility)
    monkeypatch.setattr(tasks, "load_visible_task", load_visible_task)
    monkeypatch.setattr(tasks, "load_subtree", load_subtree)

    got = await tasks.task_subtree("T", user=SimpleNamespace())

    assert checked == ["T"]  # the caller must see the task itself
    assert [r["id"] for r in got["subtasks"]] == ["a", "b", "a1"]
    assert [r["archived"] for r in got["subtasks"]] == [False, True, False]
    assert got["subtasks"][2]["parent_task_id"] == "a"
    assert got["hidden"] == 1
    # No title of the hidden row leaves the route.
    assert "Hidden" not in repr(got)
