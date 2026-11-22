"""ARC-2b: an MCP write into an archived project shows the sentence, and the chat
says what to do next. One source for the words: ``acb_common.tool_refusals``.

Spec: ``project-docs/specs/one_projects_app.md`` 9b.6 rule 2.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from acb_common import tool_refusals
from agent_edge import tools
from agent_edge.gateway_client import Answer
ToolError = tools.ToolError

SENTENCE = "This project is archived. Restore it to change its work."


def test_the_gateway_the_chat_and_the_edge_share_one_sentence() -> None:
    from gateway.routes.projects import core

    assert tool_refusals.ARCHIVED_PROJECT_REFUSAL == SENTENCE
    assert core.ARCHIVED_PROJECT_REFUSAL == SENTENCE
    assert tools.TEXT_PROJECT_ARCHIVED == SENTENCE


@pytest.mark.parametrize("per_tool_texts", [
    None,
    {409: "The task changed since you read it."},
])
async def test_a_409_with_the_archived_detail_returns_the_sentence(
    monkeypatch: pytest.MonkeyPatch, per_tool_texts,
) -> None:
    """Every write tool goes through ``_write``. Its own 409 text, if it has one
    (update_task, set_task_status, move_tasks, add_comment), must not hide the
    sentence."""
    async def refused(*_a, **_k):
        return Answer(status=409, body={"detail": SENTENCE})

    monkeypatch.setattr(tools, "gateway_write", refused)
    ctx = SimpleNamespace(key="k", caller=SimpleNamespace(lookup=None), session=None)
    with pytest.raises(ToolError) as err:
        await tools._write(
            ctx, "PATCH", "/projects/tasks/{task_id}", "Task not found.",
            json_body={"title": "x"}, path_params={"task_id": "t"},
            texts=per_tool_texts,
        )
    assert str(err.value) == SENTENCE


async def test_another_409_keeps_its_own_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refused(*_a, **_k):
        return Answer(status=409, body={"detail": "Something else."})

    monkeypatch.setattr(tools, "gateway_write", refused)
    ctx = SimpleNamespace(key="k", caller=SimpleNamespace(lookup=None), session=None)
    with pytest.raises(ToolError) as err:
        await tools._write(
            ctx, "PATCH", "/projects/tasks/{task_id}", "Task not found.",
            json_body={"title": "x"}, path_params={"task_id": "t"},
            texts={409: "The task changed since you read it."},
        )
    assert str(err.value) == "The task changed since you read it."


def _refusal(detail: str):
    return tool_refusals.GatewayRefusal(
        "Projects PATCH /x: Failed (409).", status=409, detail=detail, fields=(), route="")


def test_the_chat_next_line_for_the_archived_refusal_says_what_works() -> None:
    from skill_projects.refusals import refusal_text

    text = refusal_text(_refusal(SENTENCE))
    assert f"Gateway said: «{SENTENCE}»" in text or SENTENCE in text
    assert "restore the project first" in text
    assert "Read the row again" not in text


def test_every_other_409_keeps_the_generic_next_line() -> None:
    from skill_projects.refusals import refusal_text

    text = refusal_text(_refusal("The task changed."))
    assert "Read the row again, then call the tool once more." in text
    assert "restore the project" not in text
