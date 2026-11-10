"""WS-57 TR-3 — the chat tools for a task's URL references.

Spec: ``project-docs/specs/task_references.md`` §6 TR-3.

``add_task_ref`` and ``remove_task_ref`` (``skill_projects/writes.py``) write
through the TR-1 route, so the chat agent and a person write one path. Done
when this file shows that ``add_task_ref`` posts ``{url, title}`` to the TR-1
route for the resolved task, and that ``remove_task_ref`` deletes the
matching row. ``task_detail`` lists the references with their ref ids.

The client is patched at httpx (``_projects_agent_fakes.fake_gateway``), so
the real headers and the real manifest check run. The manifest and parity
fences (``test_projects_agent_writes.py``, ``test_projects_chat_coverage.py``,
``test_projects_field_parity.py``) hold both tools to their routes as well.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import manifest as m
from skill_projects import writes as W

from tests.unit._projects_agent_fakes import approve, deny, fake_gateway, writes

TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
REF_ID = "d1f8fad5-d9cb-469f-a165-70867728950e"
OTHER_REF = "e1f8fad5-d9cb-469f-a165-70867728950e"
PR_URL = "https://github.com/acme/widgets/pull/12"
TASK = {"id": TASK_ID, "title": "Fix the extruder", "task_number": 7, "project_id": TASK_ID}
ROWS = [
    {
        "id": REF_ID, "task_id": TASK_ID, "kind": "pull_request", "provider": "github",
        "ref": "acme/widgets#12", "url": PR_URL, "label": "widgets#12",
        "title": "Fix the login", "state": "merged",
    },
    {
        "id": OTHER_REF, "task_id": TASK_ID, "kind": "url", "provider": "other",
        "ref": "https://example.com/spec", "url": "https://example.com/spec",
        "label": "example.com", "title": None, "state": None,
    },
]


@pytest.fixture(autouse=True)
def _an_open_run() -> Any:
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    with artifact_context_scope():
        bind_artifact_context(agent_name="projects-assistant", no_egress=False)
        yield


def _gateway(rows: list[dict] | None = None, *, refs_fail: bool = False):
    from tests.unit._projects_agent_fakes import FakeResponse

    listed = ROWS if rows is None else rows

    def responder(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if path == f"/projects/tasks/{TASK_ID}" and method == "GET":
            return TASK
        if path == f"/projects/tasks/{TASK_ID}/refs" and method == "GET":
            if refs_fail:
                return FakeResponse({"detail": "boom"}, status_code=500)
            return {"rows": listed}
        if path == f"/projects/tasks/{TASK_ID}/refs" and method == "POST":
            body = call["json"] or {}
            return {**ROWS[0], "url": body.get("url"), "title": body.get("title")}
        if method == "DELETE":
            return {"deleted": path.rsplit("/", 1)[-1]}
        return {"rows": [], "total": 0}

    return responder


# ── add_task_ref ────────────────────────────────────────────────────────────


async def test_add_posts_url_and_title_to_the_route_for_the_resolved_task(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    # The id as a member might paste it: upper case. The path carries the
    # canonical id that `_task` resolved, never the raw argument.
    out = await skill_projects.add_task_ref(TASK_ID.upper(), PR_URL, "Fix the login")
    sent = writes(calls)
    assert sent == [
        {**sent[0], "method": "POST", "path": f"/projects/tasks/{TASK_ID}/refs",
         "json": {"url": PR_URL, "title": "Fix the login"}},
    ]
    assert m.route_for("POST", sent[0]["path"]).tool == "add_task_ref"
    # No GitHub read from the chat: the tool stays closed-world.
    assert sent[0]["params"] == {"refresh": False}
    assert skill_projects.add_task_ref.__tool_risk__["open_world"] is False
    assert sent[0]["headers"].get("X-Actor-Via") == "chat:projects-assistant"
    assert len(asked) == 1 and "#7" in asked[0]["detail"]
    assert "Linked" in out and f"full_id: {TASK_ID}" in out


async def test_add_without_a_title_sends_the_url_only(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.add_task_ref(TASK_ID, "https://docs.google.com/document/d/1AbCdEfGhIjK")
    assert [c["json"] for c in writes(calls)] == [
        {"url": "https://docs.google.com/document/d/1AbCdEfGhIjK"},
    ]


@pytest.mark.parametrize("url", ["", "javascript:alert(1)", "ftp://x.test/a", "acme/widgets#12"])
async def test_add_refuses_a_non_http_link_before_any_call(url: str, monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.add_task_ref(TASK_ID, url)
    assert calls == [] and asked == []
    assert "link" in out.lower()


async def test_a_declined_add_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    assert await skill_projects.add_task_ref(TASK_ID, PR_URL) == W.CANCELLED
    assert writes(calls) == []


# ── remove_task_ref ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "given",
    [PR_URL, PR_URL + "/", "HTTPS://GitHub.com/acme/widgets/pull/12", "acme/widgets#12", REF_ID],
)
async def test_remove_deletes_the_matching_row(given: str, monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.remove_task_ref(TASK_ID, given)
    sent = writes(calls)
    assert [(c["method"], c["path"]) for c in sent] == [
        ("DELETE", f"/projects/tasks/{TASK_ID}/refs/{REF_ID}"),
    ]
    assert m.route_for("DELETE", sent[0]["path"]).tool == "remove_task_ref"
    # The list is read before the card, and the card names both the task
    # and the link.
    assert calls[1]["method"] == "GET" and calls[1]["path"].endswith("/refs")
    assert "#7" in asked[0]["detail"] and "widgets#12" in asked[0]["detail"]
    assert "Removed the link" in out


async def test_remove_with_no_match_writes_nothing_and_says_so(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.remove_task_ref(TASK_ID, "https://example.com/other")
    assert writes(calls) == [] and asked == []
    assert "has no link" in out


async def test_remove_with_two_matches_asks_for_the_ref_id(monkeypatch) -> None:
    twin = [ROWS[0], {**ROWS[0], "id": OTHER_REF, "kind": "comment"}]
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(twin))
    out = await skill_projects.remove_task_ref(TASK_ID, PR_URL)
    assert writes(calls) == [] and asked == []
    assert "ref id" in out


async def test_a_declined_remove_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    assert await skill_projects.remove_task_ref(TASK_ID, PR_URL) == W.CANCELLED
    assert writes(calls) == []


# ── task_detail ─────────────────────────────────────────────────────────────


async def test_task_detail_lists_the_references_with_their_ref_ids(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.task_detail(TASK_ID)
    assert ("GET", f"/projects/tasks/{TASK_ID}/refs") in {(c["method"], c["path"]) for c in calls}
    assert "References (2):" in out
    assert f"«widgets#12» «Fix the login» («merged») «{PR_URL}» (ref id {REF_ID})" in out
    assert f"(ref id {OTHER_REF})" in out


async def test_task_detail_says_when_the_references_could_not_be_read(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway(refs_fail=True))
    out = await skill_projects.task_detail(TASK_ID)
    assert "References: unknown" in out


# ── The manifest ────────────────────────────────────────────────────────────


def test_the_three_routes_are_mapped_to_the_tools() -> None:
    assert m.route_for("GET", f"/projects/tasks/{TASK_ID}/refs").tool == "task_detail"
    assert m.route_for("POST", f"/projects/tasks/{TASK_ID}/refs").tool == "add_task_ref"
    row = m.route_for("DELETE", f"/projects/tasks/{TASK_ID}/refs/{REF_ID}")
    assert row.tool == "remove_task_ref" and row.cls == "B"
    assert m.tool_class("add_task_ref") == "B"


def test_the_names_stay_clear_of_link_tasks() -> None:
    """`link_tasks` links two TASKS. A URL is a reference, a different act."""
    assert {"add_task_ref", "remove_task_ref"} <= set(skill_projects.__all__)
    assert "link_tasks" in skill_projects.__all__
    assert m.route_for("POST", f"/projects/tasks/{TASK_ID}/links").tool == "link_tasks"
