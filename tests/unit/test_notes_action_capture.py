"""WS-39 S8c — a meeting action becomes a task in the ONE store.

Spec ``project-docs/specs/my_tasks_cutover.md`` §5 S8c · board WS-39.

The approve route and the ``task`` dispatch used to ``INSERT INTO gtd_items``.
Production has served ``pm_tasks`` since 2026-09-23, so an approved action
landed in a store no screen reads. These pin the replacement:

1. The capture goes through ``item_source().insert_capture`` as an INBOX
   capture, with the meeting origin.
2. The task id is recorded in ``action_item.dispatch_ref``, never in
   ``resulting_task_id``: that column carries a foreign key to the legacy
   ``task`` table (01_schema.sql), which refuses a ``pm_tasks`` id.
3. Approving twice writes one task, even when the row lost its ref.

The SQL is proven against Postgres in ``tests/live/live_ws39_s8c.py`` (R8).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from gateway.routes.notes import actions, dispatch
from gateway.routes.tasks import item_source as seam

ALICE = "alice@fracktal.in"


class _Source:
    name = "recording"

    def __init__(self) -> None:
        self.captures: list[SimpleNamespace] = []
        self.lookups: list[tuple[str, str, str]] = []

    async def find_by_origin(self, db, uid, key, value, *, commitment=False):
        self.lookups.append((uid, key, str(value)))
        return next((
            c for c in self.captures
            if c.uid == uid and str(c.origin.get(key)) == str(value)
        ), None)

    async def insert_capture(self, db, uid, fields, origin):
        cap = SimpleNamespace(id=f"task-{len(self.captures) + 1}", uid=uid,
                              fields=dict(fields), origin=dict(origin or {}))
        self.captures.append(cap)
        return cap.id


def _action(**kw) -> SimpleNamespace:
    base = dict(
        id="a-1", meeting_id="m-1", description="Send Priya the quote",
        confidence=0.92, status="draft", due_hint="Friday",
        segment_ids=["s-1", "s-2"], resulting_task_id=None, kind="task",
        payload={}, dispatch_ref=None, dispatch_error=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Db:
    """Answers the action loader and records every statement."""

    def __init__(self, action: SimpleNamespace) -> None:
        self.action = action
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        sql, params = str(stmt), dict(params or {})
        self.statements.append((sql, params))
        rows = [self.action] if "FROM action_item" in sql else []
        return SimpleNamespace(fetchone=lambda: rows[0] if rows else None,
                               fetchall=lambda: list(rows))

    async def commit(self) -> None:
        return None

    async def __aenter__(self) -> _Db:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    def updates(self) -> list[tuple[str, dict]]:
        return [(s, p) for s, p in self.statements
                if s.lstrip().upper().startswith("UPDATE ACTION_ITEM")]


@pytest.fixture
def source(monkeypatch) -> _Source:
    src = _Source()
    monkeypatch.setattr(seam, "item_source", lambda: src)
    return src


def _install(monkeypatch, module, db) -> None:
    @asynccontextmanager
    async def _tenant_session(organization_id=None):
        yield db

    async def _get_db():
        return db

    if hasattr(module, "_tenant_session"):
        monkeypatch.setattr(module, "_tenant_session", _tenant_session)
    if hasattr(module, "_get_db"):
        monkeypatch.setattr(module, "_get_db", _get_db)


def _user(email: str = ALICE):
    return SimpleNamespace(email=email)


# ── 1. The capture ──────────────────────────────────────────────────────────

async def test_the_capture_is_an_inbox_capture_with_the_meeting_origin(
    source,
) -> None:
    task_id = await actions._create_task_from_action(None, ALICE, _action())
    assert task_id == "task-1"
    (cap,) = source.captures
    assert cap.uid == ALICE
    assert cap.fields["title"] == "Send Priya the quote"
    assert cap.fields["disposition"] == "INBOX"
    assert "Confidence 92%" in cap.fields["description"]
    assert "Due (as stated): Friday." in cap.fields["description"]
    assert cap.origin == {
        "kind": "meeting", "meeting_id": "m-1", "action_item_id": "a-1",
        "segment_ids": ["s-1", "s-2"],
    }


async def test_a_second_capture_of_one_action_finds_the_first(source) -> None:
    """``_dispatch`` commits the task and marks the row in two sessions. A
    failure between them left a draft whose retry wrote a second task."""
    first = await actions._create_task_from_action(None, ALICE, _action())
    again = await actions._create_task_from_action(None, ALICE, _action())
    assert first == again == "task-1"
    assert len(source.captures) == 1
    assert source.lookups[-1] == (ALICE, "action_item_id", "a-1")


def test_the_lookup_key_is_an_allowed_origin_key() -> None:
    assert seam.origin_key_sql("action_item_id") == "origin->>'action_item_id'"


def test_no_notes_module_writes_the_retiring_store() -> None:
    import inspect

    src = inspect.getsource(actions._create_task_from_action)
    assert "gtd_items" not in src and "INSERT INTO" not in src


# ── 2. The link back is dispatch_ref ────────────────────────────────────────

async def test_approve_records_the_task_in_dispatch_ref(
    monkeypatch, source,
) -> None:
    db = _Db(_action())
    _install(monkeypatch, actions, db)
    out = await actions.approve_action("a-1", user=_user())
    assert out.status == "created" and out.resulting_task_id == "task-1"
    (sql, params), = db.updates()
    assert "dispatch_ref=:tid" in sql
    assert "resulting_task_id" not in sql, (
        "resulting_task_id FKs the legacy task table and refuses a pm id")
    assert params == {"tid": "task-1", "id": "a-1"}


async def test_approve_twice_returns_the_same_task(monkeypatch, source) -> None:
    db = _Db(_action(status="created", dispatch_ref="task-1"))
    _install(monkeypatch, actions, db)
    out = await actions.approve_action("a-1", user=_user())
    assert out.resulting_task_id == "task-1"
    assert not source.captures and not db.updates()


async def test_approve_with_a_lost_ref_still_writes_one_task(
    monkeypatch, source,
) -> None:
    """The row says draft, but the capture exists: find it, do not add one."""
    await actions._create_task_from_action(None, ALICE, _action())
    db = _Db(_action())
    _install(monkeypatch, actions, db)
    out = await actions.approve_action("a-1", user=_user())
    assert out.resulting_task_id == "task-1"
    assert len(source.captures) == 1


async def test_reject_refuses_an_item_already_captured(monkeypatch, source) -> None:
    from fastapi import HTTPException

    db = _Db(_action(status="created", dispatch_ref="task-9"))
    _install(monkeypatch, actions, db)
    with pytest.raises(HTTPException) as exc:
        await actions.reject_action("a-1", user=_user())
    assert exc.value.status_code == 409


def test_task_ref_reads_both_columns_and_only_a_tasks_ref() -> None:
    assert actions.task_ref(_action(resulting_task_id="legacy-1")) == "legacy-1"
    assert actions.task_ref(_action(dispatch_ref="task-3")) == "task-3"
    assert actions.task_ref(_action(kind="email", dispatch_ref="sent:x")) is None
    assert actions.task_ref(
        _action(kind="document", dispatch_ref="artifact:a/b")) is None
    assert actions.task_ref(_action()) is None


# ── 3. The dispatch path writes the same way ────────────────────────────────

async def test_task_dispatch_never_writes_resulting_task_id(
    monkeypatch, source,
) -> None:
    db = _Db(_action())
    _install(monkeypatch, dispatch, db)
    marks: list[dict] = []

    async def _mark(action_id, *, ref, task_id, error):
        marks.append({"ref": ref, "task_id": task_id, "error": error})

    async def _audit(*a, **k):
        return None

    monkeypatch.setattr(dispatch, "_mark", _mark)
    monkeypatch.setattr(dispatch, "_audit", _audit)
    meeting = SimpleNamespace(owner_email=ALICE)
    ref, error = await dispatch._dispatch(_action(), meeting, ALICE)
    assert error is None and ref == "task-1"
    assert marks == [{"ref": "task-1", "task_id": None, "error": None}]
    (cap,) = source.captures
    assert cap.uid == ALICE and cap.origin["kind"] == "meeting"


def test_the_action_list_reads_the_task_ref() -> None:
    """The meeting page links "In My Tasks" off ``resulting_task_id``. The
    list reader fills it from ``dispatch_ref`` for a task captured since S8c."""
    import inspect

    from gateway.routes.notes import summaries

    assert "task_ref(r)" in inspect.getsource(summaries)
