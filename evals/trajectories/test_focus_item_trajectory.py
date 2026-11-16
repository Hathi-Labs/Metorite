"""Golden trajectory: "it" and "mark it done" find the item that the chat read
last (HRD-11a, task 2990).

Spec ``project-docs/specs/chat_failure_modes.md`` §4.5 E1 and §6 row HRD-11a,
done-when 8. A synthetic chat. No customer data is in it, and no model is
called: the model wire is scripted, so the eval costs no spend.

The REAL orchestrator runs each turn through ``evals.routing.harness``, and
it delegates to the REAL specialist factory. Only the model wire and the
gateway routes of the specialist are fakes, and the item lives in the
in-memory tenant Redis of the harness.

1. Email. Turn 1: the member asks for the mail from Dr Sagar. The
   email-assistant sub-run reads it (``read_email``). Turn 2: "Reply to it."
   The orchestrator's model request holds the focus block, with kind
   ``email`` and the ``email_id`` of the read.
2. Task. Turn 1: the member asks for the quote task. The projects-assistant
   sub-run reads it (``my_task``). Turn 2: "Mark it done." The request holds
   the block, with kind ``task`` and the ``task_id`` of the read. A list read
   before it (``find_tasks``) sets nothing.
3. The flag off: the same chat holds no block, and Redis sees no read and no
   write of the item.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import chat_focus as cf

from evals.routing import dataset as ds
from evals.routing.harness import (
    ORCHESTRATOR,
    ReplyModel,
    RoutingHarness,
    RunRecord,
    specialist_tool,
    text_turn,
    tool_turn,
)

EA = "email-assistant"
PA = "projects-assistant"
PAGE = "## Where the member is now\nThe member is in the chat app (/chat)."

MAIL = "e-sagar-1"
TASK = "5a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"


def _request(model: ReplyModel, index: int) -> str:
    """The messages of one model request, as text."""
    return json.dumps(model.bodies[index].get("messages") or [], ensure_ascii=False)


def _turn(harness: RoutingHarness, words: str, thread: str | None) -> RunRecord:
    record = asyncio.run(harness.drive(
        words, PAGE, member=ds.MEMBER, org=ds.ORG, thread_id=thread, timeout=60.0,
        typed=words,
    ))
    assert record.error is None, record.error
    return record


def _focus_calls(harness: RoutingHarness) -> list[tuple[str, str]]:
    from acb_skills import egress

    store = egress.get_tenant_redis()
    client = getattr(store, "_client", None)
    return [c for c in getattr(client, "calls", []) if ":chat-focus:" in c[1]]


class _EmailGateway:
    """The one email route that ``read_email`` reaches."""

    async def get(self, path: str, params: dict | None = None) -> Any:
        if path == f"/email/messages/{MAIL}":
            return {"id": MAIL, "subject": "Quote for the clinic",
                    "from_address": {"name": "Dr Sagar", "email": "sagar@clinic.test"},
                    "body_text": "Please send the quote by Friday."}
        return {}


async def _projects_get(path: str, params: dict | None = None) -> Any:
    """The Projects routes that ``find_tasks`` and ``my_task`` reach."""
    if path == f"/projects/my/tasks/{TASK}":
        return {"id": TASK, "task_number": 7, "title": "Send the quote"}
    if path.startswith("/projects/tasks/search") or path.startswith("/projects/search"):
        return {"rows": [], "total": 0}
    return {}


@pytest.fixture
def flag(monkeypatch: pytest.MonkeyPatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_focus_orgs", ds.ORG, raising=False)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch):
    from tests.unit._native_maf_harness import load_agent_module

    with RoutingHarness() as h:
        mod = load_agent_module(h._registry[EA]["local_path"])
        h._modules[EA] = mod
        monkeypatch.setattr(mod, "_get", _EmailGateway().get)
        from skill_projects import reads

        monkeypatch.setattr(reads, "get", _projects_get)
        yield h


def _email_chat(harness: RoutingHarness) -> ReplyModel:
    orch = ReplyModel([
        # Turn 1: delegate the read.
        tool_turn(specialist_tool(EA), {"task": "Read the mail from Dr Sagar."}),
        text_turn("Dr Sagar asks for the quote by Friday."),
        # Turn 2: "Reply to it." The model only answers.
        text_turn("I will draft the reply to that mail."),
    ])
    harness.models = {
        ORCHESTRATOR: orch,
        EA: ReplyModel([
            tool_turn("read_email", {"email_id": MAIL}),
            text_turn("Dr Sagar asks for the quote by Friday."),
        ]),
    }
    return orch


def test_reply_to_it_holds_the_email_in_focus(flag, harness) -> None:
    orch = _email_chat(harness)
    first = _turn(harness, "What did Dr Sagar send me?", None)
    assert cf.BLOCK_HEAD not in _request(orch, 0), "no item before the read"
    _turn(harness, "Reply to it.", first.thread_id)
    second = _request(orch, 2)
    assert cf.BLOCK_HEAD in second, second
    assert "- kind: email" in second and f"- email_id: {MAIL}" in second
    assert f"- in-app link: /email?email={MAIL}" in second
    assert '\\"It\\", \\"this\\" and \\"mark it done\\" mean this item.' in second
    assert "Quote for the clinic" not in second.split(cf.BLOCK_HEAD)[1].split("##")[0], \
        "the block holds no title"
    assert harness.sent == []


def test_mark_it_done_holds_the_task_in_focus(flag, harness) -> None:
    orch = ReplyModel([
        # Turn 1: delegate the read.
        tool_turn(specialist_tool(PA), {"task": "Show me the quote task."}),
        text_turn("Task #7 Send the quote is open."),
        # Turn 2: "Mark it done." The model only answers here, so the eval
        # proves what the request holds, not a card.
        text_turn("I will mark task #7 done."),
    ])
    harness.models = {
        ORCHESTRATOR: orch,
        PA: ReplyModel([
            tool_turn("find_tasks", {"query": "quote"}),
            tool_turn("my_task", {"task_id": TASK}, call_id="call_2"),
            text_turn("Task #7 Send the quote is open."),
        ]),
    }
    first = _turn(harness, "Show me the quote task.", None)
    assert cf.BLOCK_HEAD not in _request(orch, 0)
    _turn(harness, "Mark it done.", first.thread_id)
    second = _request(orch, 2)
    assert cf.BLOCK_HEAD in second, second
    assert "- kind: task" in second and f"- task_id: {TASK}" in second
    assert f"- in-app link: /projects?task={TASK}" in second
    assert harness.sent == []


def test_the_flag_off_shows_nothing_and_touches_no_item(harness) -> None:
    orch = _email_chat(harness)
    first = _turn(harness, "What did Dr Sagar send me?", None)
    _turn(harness, "Reply to it.", first.thread_id)
    assert not any(cf.BLOCK_HEAD in _request(orch, i) for i in range(len(orch.bodies)))
    assert _focus_calls(harness) == []
