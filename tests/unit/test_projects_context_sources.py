"""WS-46 P9 - a task made from an email says where it came from (G10, G11).

Spec: ``project-docs/specs/projects_agent_parity.md`` §9.4, §10 and §12 (P9).
HANDOFF H-382. Q2 (owner, 2026-10-06): no ``source_ref`` on ``pm_tasks``, so
the chat sends ``source`` for a task and both words for an intake capture.

Each rule names the mutation that turns it red (R7):

1. The vocabulary is the route's. Mutation: add a value to ``TASK_SOURCES``
   -> ``test_the_vocabulary_is_the_routes``.
2. ``create_task`` and each row of ``create_tasks`` send ``source``, and a
   task with none sends no key. Mutation: drop the key from the payload ->
   ``test_create_task_sends_source``.
3. A card for mail text starts with the mailbox line (§10.2 step 6).
   Mutation: remove the line -> ``test_the_card_starts_with_the_mailbox_line``.
4. ``capture_intake`` sends ``source`` and ``source_ref`` -> the intake tests.
5. No memory write carries the callee's text (§10.3 rule 4). The eval proves
   it in ``test_projects_ops_eval.py`` (``no_memory_of_the_callee``).

No gateway and no database. The client is patched at httpx (the shared fakes).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import manifest as m
from skill_projects import writes as W

from tests.unit import test_projects_agent_writes as tw
from tests.unit._projects_agent_fakes import approve, fake_gateway, writes

UUID = tw.UUID


@pytest.fixture(autouse=True)
def _an_open_run() -> Any:
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    with artifact_context_scope():
        bind_artifact_context(agent_name="projects-assistant", no_egress=False)
        yield


def _posts(calls: list[dict], path: str) -> list[dict]:
    return [c for c in writes(calls) if c["method"] == "POST" and c["path"] == path]


# ── 1. the vocabulary ───────────────────────────────────────────────────────


def test_the_vocabulary_is_the_routes() -> None:
    from gateway.routes.projects import core

    assert set(W.TASK_SOURCES) == set(core.CLIENT_TASK_SOURCES)
    assert "mcp" not in W.TASK_SOURCES, "the server stamps mcp, never a body"


async def test_a_source_outside_the_vocabulary_is_refused_before_the_card(monkeypatch) -> None:
    from skill_projects.refusals import refusals_as_text

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, tw.responder)
    for word in ("mcp", "slack"):
        out = await refusals_as_text(skill_projects.create_task)(UUID, "A", source=word)
        assert "source is one of" in out, out
    assert asked == [] and writes(calls) == []


# ── 2. G10: create_task and create_tasks send source ────────────────────────


async def test_create_task_sends_source(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, tw.responder)
    await skill_projects.create_task(UUID, "Pack 40 kits", source="email")
    assert _posts(calls, "/projects/tasks")[0]["json"]["source"] == "email"


async def test_a_task_with_no_source_sends_none(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, tw.responder)
    await skill_projects.create_task(UUID, "Pack 40 kits")
    assert "source" not in _posts(calls, "/projects/tasks")[0]["json"]


async def test_create_tasks_sends_source_for_each_row(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, tw.responder)
    rows = [{"title": f"Task {n}", "source": "email"} for n in range(3)]
    await skill_projects.create_tasks(UUID, json.dumps(rows))
    posts = _posts(calls, "/projects/tasks")
    assert [p["json"].get("source") for p in posts] == ["email"] * 3
    assert "source email" in asked[0]["rows"][0]["hint"]


# ── 3. the mailbox line ─────────────────────────────────────────────────────


def _body_lines(context: str) -> list[str]:
    """The card lines after the fixed note."""
    return [ln for ln in context.splitlines() if ln.strip()][1:]


async def test_the_card_starts_with_the_mailbox_line(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, tw.responder)
    await skill_projects.create_task(
        UUID, "Pack 40 kits", source="email",
        description="From email: Launch kits, 2026-10-20. Pack 40 kits by Friday.",
    )
    context = asked[0]["context"]
    assert _body_lines(context)[0].startswith("mailbox"), context
    assert "This text leaves your mailbox" in context
    assert "Everyone who can see" in context
    assert "From email: Launch kits" in context, "the card shows the description whole"


async def test_a_batch_card_starts_with_the_mailbox_line(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, tw.responder)
    await skill_projects.create_tasks(
        UUID, json.dumps([{"title": "A", "source": "email"}, {"title": "B", "source": "email"}])
    )
    assert _body_lines(asked[0]["context"])[0].startswith("mailbox")
    assert "This text leaves your mailbox" in asked[0]["context"]


async def test_a_task_not_from_mail_has_no_mailbox_line(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, tw.responder)
    await skill_projects.create_task(UUID, "A", source="agent")
    assert "mailbox" not in asked[0]["context"]


# ── 4. G11: capture_intake ──────────────────────────────────────────────────


async def test_capture_intake_sends_source_and_source_ref(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, tw.responder)
    await skill_projects.capture_intake(
        "Vendor called", project_id=UUID, source="email", source_ref="msg-123"
    )
    body = _posts(calls, "/projects/intake")[0]["json"]
    assert body["source"] == "email" and body["source_ref"] == "msg-123"
    assert "This text leaves your mailbox" in asked[0]["context"]


async def test_capture_intake_sends_neither_when_not_given(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, tw.responder)
    await skill_projects.capture_intake("Vendor called", project_id=UUID)
    body = _posts(calls, "/projects/intake")[0]["json"]
    assert "source" not in body and "source_ref" not in body


# ── the manifest ────────────────────────────────────────────────────────────


def test_g10_and_g11_are_closed_and_a_node_takes_no_source() -> None:
    assert "G10" not in m.FIELD_GAPS and "G11" not in m.FIELD_GAPS
    assert {g for names in m.FIELD_PLANNED.values() for g in names.values()} == {"G23"}
    assert m.SENDS[("POST", "/projects/tasks")]["source"] == (
        "create_task.source", "create_tasks.tasks.source",
    )
    assert set(m.SENDS[("POST", "/projects/intake")]) >= {"source", "source_ref"}
    assert "source" in m.FIELD_EXEMPT[("POST", "/projects/nodes")]
