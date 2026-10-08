"""The chat's placement map agrees with the Projects manifest classes.

Spec: ``project-docs/specs/projects_ai_chat.md`` §24 (the placement rule of
record, owner 2026-10-08). The chat draws a READ's receipt inside its step in
the working trail, and a WRITE's receipt in the flow after the answer. The
client cannot see a tool's annotations, so ``PLACEMENT`` in
``workbench/control_plane/src/lib/chatPlacement.ts`` is written out. This file
holds it to the truth on the server side:

- every ``skill_projects`` export is in the map;
- a class A tool (a read, H-236) is ``evidence``, or ``answer`` for a view
  that draws a template;
- a class B or C tool (a write) is ``write``.

Mutations this file catches (R7): drop a Projects tool from the map; move a
read into ``WRITE``; move a write into ``EVIDENCE``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import manifest

ROOT = Path(__file__).resolve().parents[2]
MAP = ROOT / "workbench/control_plane/src/lib/chatPlacement.ts"

#: The class A tools that draw a template: an answer card, not evidence.
VIEWS = {"render_timeline", "render_board", "render_tasks", "render_report", "status_report"}


def _placement() -> dict[str, str]:
    """``PLACEMENT`` as the TypeScript source declares it, list by list."""
    src = MAP.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for const, kind in (("ASK", "ask"), ("ANSWER", "answer"), ("EVIDENCE", "evidence"),
                        ("WRITE", "write")):
        m = re.search(rf"const {const}: readonly string\[\] = \[(.*?)\];", src, re.S)
        assert m, f"chatPlacement.ts has no {const} list"
        body = re.sub(r"//[^\n]*", "", m.group(1))
        for name in re.findall(r'"([a-z_]+)"', body):
            assert name not in out, f"{name} is both {out[name]} and {kind}"
            out[name] = kind
    return out


def test_the_map_parses_and_holds_all_four_kinds() -> None:
    placement = _placement()
    assert set(placement.values()) == {"ask", "answer", "evidence", "write"}
    assert len(placement) > 150


def test_every_projects_tool_is_classified() -> None:
    placement = _placement()
    missing = sorted(t for t in skill_projects.__all__ if t not in placement)
    assert not missing, f"chatPlacement.ts does not name {missing}"


def test_a_read_is_evidence_and_a_write_is_a_write() -> None:
    placement = _placement()
    wrong: list[str] = []
    for tool in sorted(skill_projects.__all__):
        cls = manifest.tool_class(tool)
        want = ("answer" if tool in VIEWS else "evidence") if cls == "A" else "write"
        if placement.get(tool) != want:
            wrong.append(f"{tool}: class {cls} wants {want}, the map says {placement.get(tool)}")
    assert not wrong, "\n".join(wrong)


def test_the_views_are_class_a() -> None:
    """A view is a read that draws a card. If one turned into a write, the
    receipt of that write would hide as an answer."""
    assert {manifest.tool_class(v) for v in VIEWS} == {"A"}


EMAIL_AGENT = ROOT / "apps/agents/agent-email-assistant/agents.py"

#: Email tools that write and say ``open_world=False``. The annotations cannot
#: tell them from a read, so they are named here (review round 1).
EMAIL_QUIET_WRITES = {"generate_writing_style"}


def _email_annotations() -> dict[str, str]:
    """Each email tool's ``_annotate_risk`` hints, by tool name."""
    src = EMAIL_AGENT.read_text(encoding="utf-8")
    return {m.group(2): m.group(1)
            for m in re.finditer(r"@_annotate_risk\(([^)]*)\)\s*\n\s*async def (\w+)", src)}


def test_an_email_tool_that_sends_or_destroys_is_never_evidence() -> None:
    """A send, a provider write or a destructive act keeps its receipt in the flow.

    Review round 1 found ``digest`` (it can send the digest) in EVIDENCE, so a
    sent mail's receipt hid in a closed step. Mutation: move ``digest`` back.
    """
    placement = _placement()
    hints = _email_annotations()
    assert len(hints) > 40
    loud = {name for name, h in hints.items()
            if "open_world=True" in h or "destructive=True" in h} | EMAIL_QUIET_WRITES
    wrong = sorted(n for n in loud if placement.get(n) == "evidence")
    assert not wrong, f"these email tools write, and the map draws them as reads: {wrong}"


CRM_AGENT = ROOT / "apps/agents/agent-crm/agents.py"


def test_a_crm_read_is_evidence_and_a_crm_write_is_a_write() -> None:
    """The CRM reads draw in their step, and every CRM write keeps its row.

    Follow-up of #716 and #735 (spec ``projects_ai_chat.md`` §24.8): the CRM
    reads had no receipt, so the step opened to the raw output. Each tool the
    agent annotates ``read_only=True`` is evidence, and every other one is a
    write. Mutation: move ``get_record`` to WRITE, or ``create_lead`` to
    EVIDENCE.
    """
    placement = _placement()
    src = CRM_AGENT.read_text(encoding="utf-8")
    hints = {m.group(2): m.group(1)
             for m in re.finditer(r"@_annotate_risk\(([^)]*)\)\s*\n\s*async def (\w+)", src)}
    assert len(hints) == 8
    wrong = []
    for name, hint in sorted(hints.items()):
        want = "evidence" if "read_only=True" in hint else "write"
        if placement.get(name) != want:
            wrong.append(f"{name}: wants {want}, the map says {placement.get(name)}")
    assert not wrong, "\n".join(wrong)
