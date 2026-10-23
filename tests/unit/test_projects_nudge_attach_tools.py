"""WS-64 OP-6c: the nudge, the export and the attach tools of the Projects chat.

Spec: ``project-docs/specs/one_projects_app.md`` §5a (D-OP-9) and §9 OP-6c.

Three tools, one fence each:

1. ``nudge_task`` (class C). The card names the person and the task. A decline
   writes nothing. The tool sends one nudge per call and never loops. A route
   refusal reaches the model in the route's own words.
2. ``export_tasks`` (class A, no card). The filters go to the route, the CSV
   goes into the chat through ``documents.save_outputs`` (the H-236 safe write),
   and a 422 over the row cap says the limit and a next step.
3. ``attach_to_task`` (class C). The bytes come from the thread-private upload
   folder through ``read_attachment_bytes``. The card names the file, its size
   and the task. A file over the route's cap is refused before the card.

The R8 half, against the real routes, is ``test_projects_nudge_attach_r8.py``.
No database here: the client is patched at httpx.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
import skill_projects.client as client
from skill_projects import guarded as G
from skill_projects import views as V

from tests.unit import test_projects_agent_writes as tw
from tests.unit._projects_agent_fakes import (
    FakeResponse,
    approve,
    deny,
    fake_gateway,
    writes,
)

from acb_skills import attachment_tools as _real_tools

#: The accessor itself, kept before the fixture below puts a stand-in in its place.
REAL_ACCESSOR = _real_tools.read_attachment_bytes

UUID, WAITER, OTHER = tw.UUID, tw.WAITER, tw.OTHER
CAP_WORDS = (
    "5200 tasks match these filters and the export cap is 5000. Narrow the filter "
    "(a status, an assignee, a single project) and export again — this endpoint "
    "will not hand back a partial file."
)


@pytest.fixture(autouse=True)
def _a_chat_upload(monkeypatch) -> None:
    tw.stub_chat_upload(monkeypatch)


@pytest.fixture(autouse=True)
def _an_open_run() -> Any:
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    with artifact_context_scope():
        bind_artifact_context(agent_name="projects-assistant", no_egress=False)
        yield


def _nudges(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c["method"] == "POST" and c["path"].endswith("/nudge")]


def _waiting(overrides: dict[str, Any] | None = None, *, mine: dict[str, Any] | None = None):
    """A responder for a task the member waits on a colleague for."""

    def answer(call: dict) -> Any:
        if call["path"] == f"/projects/my/tasks/{WAITER}" and mine is not None:
            return mine
        for key, value in (overrides or {}).items():
            if call["path"] == key:
                return value
        return tw.responder(call)

    return answer


# ── 1. nudge_task ───────────────────────────────────────────────────────────


async def test_the_nudge_card_names_the_person_and_the_task(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, tw.responder)
    out = await skill_projects.nudge_task(WAITER)
    [card] = asked
    assert "«Priya»" in card["detail"] and "#7" in card["detail"]
    assert "«Priya»" in card["context"] and "«Fix the extruder»" in card["context"]
    lines = card["context"].split("\n")
    assert lines[1].startswith("impact: 1 notification to «Priya»")
    assert "no email" in lines[1]
    assert len(_nudges(calls)) == 1
    assert out.startswith("Nudged «Priya» about #7")
    assert "I sent no email" in out


async def test_a_declined_nudge_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = fake_gateway(monkeypatch, tw.responder)
    assert await skill_projects.nudge_task(WAITER) == tw.W.CANCELLED
    assert writes(calls) == []


async def test_one_call_is_one_nudge_and_a_list_of_tasks_is_refused(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, tw.responder)
    out = await skill_projects.nudge_task(f"{WAITER},{OTHER}")
    assert "ONE" in out
    assert asked == [] and calls == []
    await skill_projects.nudge_task(WAITER)
    assert len(_nudges(calls)) == 1


async def test_the_card_says_when_the_person_was_last_nudged(monkeypatch) -> None:
    asked = approve(monkeypatch)
    mine = {"waiting_on": {"name": "Priya", "email": "priya@x.io"},
            "last_nudged_at": "2026-10-20T09:00:00+00:00"}
    fake_gateway(monkeypatch, _waiting(mine=mine))
    await skill_projects.nudge_task(WAITER)
    assert "last nudged:" in asked[0]["context"]


@pytest.mark.parametrize(
    ("mine", "task", "words"),
    [
        ({"waiting_on": None}, {}, "You are not waiting on anybody for this task."),
        ({}, {}, "You are not waiting on anybody for this task."),
        (
            {"waiting_on": {"name": "Bot", "email": "agent:coder"}},
            {},
            "An agent has no inbox to nudge.",
        ),
        (
            {"waiting_on": {"name": "Priya", "email": "priya@x.io"}},
            {"completed_at": "2026-10-01T00:00:00+00:00"},
            "This task is done. There is nobody to chase.",
        ),
    ],
    ids=["not-waiting", "no-overlay", "an-agent", "done"],
)
async def test_a_task_nobody_can_be_chased_on_is_refused_in_the_routes_words_before_the_card(
    mine: dict, task: dict, words: str, monkeypatch
) -> None:
    asked = approve(monkeypatch)

    def answer(call: dict) -> Any:
        if call["path"] == f"/projects/tasks/{WAITER}":
            return {**tw.TASK, "id": WAITER, "archived_at": None, **task}
        return _waiting(mine=mine)(call)

    calls = fake_gateway(monkeypatch, answer)
    out = await skill_projects.nudge_task(WAITER)
    assert out.startswith(words), out
    assert asked == [] and writes(calls) == []


async def test_a_task_outside_my_lists_holds_no_chase(monkeypatch) -> None:
    """``GET /my/tasks/{id}`` answers 404 for a task that is not mine."""
    asked = approve(monkeypatch)

    def answer(call: dict) -> Any:
        if call["path"] == f"/projects/my/tasks/{WAITER}":
            return FakeResponse({"detail": "Not found"}, status_code=404)
        return tw.responder(call)

    calls = fake_gateway(monkeypatch, answer)
    out = await skill_projects.nudge_task(WAITER)
    assert out.startswith("You are not waiting on anybody for this task.")
    assert asked == [] and writes(calls) == []


async def test_the_routes_refusal_after_the_card_reaches_the_model(monkeypatch) -> None:
    approve(monkeypatch)

    def answer(call: dict) -> Any:
        if call["path"].endswith("/nudge"):
            return FakeResponse(
                {"detail": "You are not waiting on anybody for this task."}, status_code=409
            )
        return tw.responder(call)

    fake_gateway(monkeypatch, answer)
    with pytest.raises(client.GatewayRefusal) as raised:
        await skill_projects.nudge_task(WAITER)
    assert raised.value.status == 409
    assert "You are not waiting on anybody for this task." in raised.value.detail


async def test_a_nudge_that_reached_nobody_says_so(monkeypatch) -> None:
    approve(monkeypatch)

    def answer(call: dict) -> Any:
        if call["path"].endswith("/nudge"):
            return {"notified": [], "skipped": ["priya@x.io"], "last_nudged_at": None}
        return tw.responder(call)

    fake_gateway(monkeypatch, answer)
    out = await skill_projects.nudge_task(WAITER)
    assert out.startswith("Nobody was told.")
    assert "Nudged" not in out


# ── 2. export_tasks ─────────────────────────────────────────────────────────

CSV = "#,Title,Status\r\n7,Fix the extruder,To do\r\n8,Order the nozzle,Done\r\n".encode()


def _csv_gateway(monkeypatch, *, status: int = 200, body: Any = None) -> list[dict]:
    def answer(call: dict) -> Any:
        if call["path"] == "/projects/export/tasks.csv":
            if status != 200:
                return FakeResponse(body, status_code=status)
            resp = FakeResponse(None)
            resp.content = CSV
            resp.headers = {
                "Content-Disposition": 'attachment; filename="projects-tasks.csv"',
                "X-Export-Rows": "2",
            }
            return resp
        return tw.responder(call)

    return fake_gateway(monkeypatch, answer)


def _saved(monkeypatch) -> list[dict]:
    """Stand in for the H-236 safe write, and record what it was given."""
    from acb_skills import documents

    seen: list[dict] = []

    async def save_outputs(stem, suffixes, contents, *, announce):
        seen.append({
            "stem": stem, "suffixes": suffixes, "announce": announce,
            "bytes": contents({".csv": f"outputs/{stem}.csv"})[".csv"],
        })
        return {"files": {".csv": {"path": f"outputs/{stem}.csv", "size": len(CSV)}}}

    monkeypatch.setattr(documents, "save_outputs", save_outputs)
    return seen


async def test_the_export_sends_the_filters_and_saves_the_file_as_a_chat_document(
    monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    calls = _csv_gateway(monkeypatch)
    saved = _saved(monkeypatch)
    out = await skill_projects.export_tasks(
        project_id=UUID, status_category="todo", assignee="priya@x.io", overdue=True,
        tags="urgent", query="extruder", shown_fields="status,tags",
    )
    [call] = [c for c in calls if c["path"] == "/projects/export/tasks.csv"]
    assert call["method"] == "GET"
    assert call["params"] == {
        "include_subtree": True, "shown_fields": "status,tags", "project_id": UUID,
        "status_category": "todo", "assignees": "priya@x.io", "overdue": True,
        "tags": "urgent", "q": "extruder",
    }
    assert asked == [], "a read takes no card"
    assert saved == [{
        "stem": "projects-tasks", "suffixes": (".csv",), "announce": True, "bytes": CSV,
    }]
    assert out.startswith("Exported 2 tasks to outputs/projects-tasks.csv")
    assert "paste no link" in out
    assert writes(calls) == []


async def test_an_export_with_no_columns_asks_for_the_usual_set(monkeypatch) -> None:
    calls = _csv_gateway(monkeypatch)
    _saved(monkeypatch)
    await skill_projects.export_tasks()
    [call] = [c for c in calls if c["path"] == "/projects/export/tasks.csv"]
    assert call["params"]["shown_fields"] == V.DEFAULT_EXPORT_FIELDS
    assert "project_id" not in call["params"]


async def test_an_export_over_the_cap_says_the_limit_and_a_next_step(monkeypatch) -> None:
    _csv_gateway(monkeypatch, status=422, body={"detail": CAP_WORDS})
    saved = _saved(monkeypatch)
    out = await skill_projects.export_tasks()
    assert "Nothing was exported." in out
    assert "5200" in out and "5000" in out
    assert "Ask the member which filter to add" in out
    assert saved == [], "no file for a refused export"


async def test_another_refusal_of_the_export_still_raises(monkeypatch) -> None:
    _csv_gateway(monkeypatch, status=404, body={"detail": "Not found"})
    _saved(monkeypatch)
    with pytest.raises(client.GatewayRefusal):
        await skill_projects.export_tasks(project_id=UUID)


async def test_a_file_that_cannot_be_saved_is_said_in_words(monkeypatch) -> None:
    from acb_skills import documents

    _csv_gateway(monkeypatch)

    async def full(*_a, **_k):
        return {"error": "The working dir of the chat is over its quota, so nothing was written."}

    monkeypatch.setattr(documents, "save_outputs", full)
    out = await skill_projects.export_tasks()
    assert "the file was not saved" in out and "over its quota" in out


def test_the_default_columns_are_columns_of_the_route() -> None:
    from gateway.routes.projects.export import FIELD_LABELS
    from gateway.routes.projects.filters import SHOWN_FIELDS

    wanted = V.DEFAULT_EXPORT_FIELDS.split(",")
    assert set(wanted) <= set(SHOWN_FIELDS)
    assert set(wanted) <= set(FIELD_LABELS)


# ── 3. attach_to_task ───────────────────────────────────────────────────────


def _answer_with(files: list[dict]):
    def answer(call: dict) -> Any:
        if call["path"] == f"/projects/tasks/{UUID}/attachments" and call["method"] == "GET":
            return {"rows": files, "total": len(files)}
        return tw.responder(call)

    return answer


async def test_the_attach_card_names_the_file_its_size_and_the_task(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, tw.responder)
    out = await skill_projects.attach_to_task(UUID, "brief.pdf")
    [card] = asked
    assert "«brief.pdf»" in card["detail"] and "#7" in card["detail"]
    assert "0.0 MB" in card["detail"]
    lines = card["context"].split("\n")
    assert lines[1].startswith("impact: 1 file of 0.0 MB attached to #7")
    assert "everyone who can open the task" in lines[1]
    assert "file: «brief.pdf»" in card["context"]
    [post] = [c for c in calls if c["method"] == "POST"]
    assert post["path"] == f"/projects/tasks/{UUID}/attachments"
    name, content, mime = post["files"]["file"]
    assert (name, content, mime) == ("brief.pdf", tw.UPLOAD[1], "application/pdf")
    assert "Content-Type" not in post["headers"], "a multipart body sets its own"
    assert post["json"] is None
    assert out.startswith("Attached «brief.pdf» to #7")


async def test_a_declined_attach_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = fake_gateway(monkeypatch, tw.responder)
    assert await skill_projects.attach_to_task(UUID, "brief.pdf") == tw.W.CANCELLED
    assert writes(calls) == []


async def test_a_file_that_the_member_did_not_upload_passes_the_accessors_words(
    monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, tw.responder)
    out = await skill_projects.attach_to_task(UUID, "salaries.xlsx")
    assert out.startswith("No file named salaries.xlsx was attached in this chat.")
    assert asked == [] and writes(calls) == []


@pytest.mark.parametrize(
    ("upload", "words"),
    [
        (("empty.txt", b""), "is empty, so there is nothing to attach."),
        (("setup.exe", b"MZ"), "File type not allowed: «setup.exe»."),
        (
            ("big.zip", b"x" * (G.TASK_FILE_BUDGET + 1)),
            "one task may hold 10.0 MB in total. Attach a smaller file.",
        ),
    ],
    ids=["empty", "blocked-type", "over-the-cap"],
)
async def test_a_file_the_route_would_refuse_is_refused_before_the_card(
    upload: tuple[str, bytes], words: str, monkeypatch
) -> None:
    tw.stub_chat_upload(monkeypatch, upload)
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, tw.responder)
    out = await skill_projects.attach_to_task(UUID, upload[0])
    assert words in out, out
    assert asked == [] and writes(calls) == []


async def test_a_file_that_would_overfill_the_task_is_refused_before_the_card(
    monkeypatch,
) -> None:
    tw.stub_chat_upload(monkeypatch, ("part.bin", b"x" * (3 * 1024 * 1024)))
    asked = approve(monkeypatch)
    held = [{"id": tw.FILE, "name": "old.pdf", "size": 8 * 1024 * 1024}]
    calls = fake_gateway(monkeypatch, _answer_with(held))
    out = await skill_projects.attach_to_task(UUID, "part.bin")
    assert "would hold 11.0 MB" in out and "It holds 8.0 MB now" in out
    assert "Detach a file first." in out
    assert asked == [] and writes(calls) == []


async def test_the_routes_refusal_after_the_card_reaches_the_model_for_an_upload(
    monkeypatch,
) -> None:
    approve(monkeypatch)

    def answer(call: dict) -> Any:
        if call["method"] == "POST":
            return FakeResponse({"detail": "This task is archived."}, status_code=409)
        return tw.responder(call)

    fake_gateway(monkeypatch, answer)
    with pytest.raises(client.GatewayRefusal) as raised:
        await skill_projects.attach_to_task(UUID, "brief.pdf")
    assert raised.value.detail == "This task is archived."


async def test_a_list_of_tasks_is_refused_before_any_read(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, tw.responder)
    out = await skill_projects.attach_to_task(f"{UUID},{OTHER}", "brief.pdf")
    assert "ONE" in out
    assert asked == [] and calls == []


def test_the_limits_the_tool_says_are_the_routes_limits() -> None:
    from gateway.routes.projects.attachments import _TASK_ATTACHMENT_BUDGET
    from gateway.routes.tasks.attachments import _BLOCKED_EXT, _MAX_BYTES

    assert G.TASK_FILE_BUDGET == _TASK_ATTACHMENT_BUDGET
    assert G.TASK_FILE_BUDGET <= _MAX_BYTES
    assert G.BLOCKED_FILE_TYPES == frozenset(_BLOCKED_EXT)


# ── The accessor: only this thread's folder, any kind of file ───────────────

ORG, AGENT = "org-op6c", "agent-op6c"
SID_A, SID_B = "thread-alice-op6c-0001", "thread-bob-op6c-0002"


@pytest.fixture
def ws(tmp_path, monkeypatch) -> Path:
    from acb_common import get_settings
    from acb_skills.agent_paths import ensure_state_dir, tenant_instance

    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(tmp_path / "agents"))
    return ensure_state_dir(AGENT, tenant_instance(ORG))


def _bytes_of(root: Path | None, sid: str | None, name: str) -> Any:
    from acb_skills.agent_paths import tenant_instance
    from acb_skills.write_artifact import bind_artifact_context

    async def go() -> Any:
        bind_artifact_context(
            session_id=sid, agent_name=AGENT, run_id="r-op6c",
            workspace_root=str(root) if root else None, instance=tenant_instance(ORG),
        )
        return await REAL_ACCESSOR(name)

    return asyncio.run(go())


def _put(root: Path, sid: str, name: str, data: bytes) -> None:
    from acb_skills.agent_paths import tenant_instance, upload_dir_rel

    path = root / upload_dir_rel(tenant_instance(ORG), sid) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_the_accessor_gives_the_bytes_of_a_kind_that_read_attachment_refuses(ws) -> None:
    _put(ws, SID_A, "photo.png", b"\x89PNG\r\n\x1a\n-not-text")
    assert _bytes_of(ws, SID_A, "photo.png") == ("photo.png", b"\x89PNG\r\n\x1a\n-not-text")


def test_the_accessor_reads_no_other_thread_and_no_path(ws) -> None:
    _put(ws, SID_A, "salaries.docx", b"alice only")
    _put(ws, SID_B, "bob.txt", b"bob")
    from acb_skills.agent_paths import thread_slug

    alices = f"inputs/{thread_slug(SID_A)}/salaries.docx"
    for name in ("salaries.docx", "../x/salaries.docx", "inputs/salaries.docx", alices):
        got = _bytes_of(ws, SID_B, name)
        assert isinstance(got, str) and got.startswith("No file named salaries.docx"), got
        assert "alice" not in got
    assert _bytes_of(ws, SID_B, "bob.txt") == ("bob.txt", b"bob")


def test_the_accessor_with_no_workspace_or_no_thread_gives_a_sentence(ws) -> None:
    _put(ws, SID_A, "a.txt", b"hello")
    assert _bytes_of(None, SID_A, "a.txt").startswith("This chat has no workspace")
    assert _bytes_of(ws, None, "a.txt").startswith("This chat has no workspace")


def test_the_accessor_refuses_a_file_over_the_read_cap(ws, monkeypatch) -> None:
    monkeypatch.setattr(_real_tools, "MAX_FILE_BYTES", 8)
    _put(ws, SID_A, "big.bin", b"123456789")
    got = _bytes_of(ws, SID_A, "big.bin")
    assert isinstance(got, str) and "at most 8 bytes" in got
