"""HRD-2 (task 2981): every id a write tool needs is printed by a read tool.

Spec: ``project-docs/specs/chat_failure_modes.md`` §4.2 B3 and §7 rule 1.

On 2026-11-11 ``mark_thread_done`` needed a ``thread_id`` that no read tool
printed. Production also refused ``task_id must be a UUID … Got '244'``. The
model cannot give an id it never read. This file holds three claims:

1. **Printed.** Each id parameter of a Projects tool and of a CRM tool is in
   ``PRINTED`` with a read formatter whose source holds the label, or in
   ``ALLOWED`` with a reason. A new id parameter that is in neither fails.
2. **Named.** A refusal of an id names a read tool that gives it.
3. **Near forms.** A ``kind:`` prefix of the tool's own kind is stripped, and a
   prefix of another kind is refused with a clear text.

Scope: the Projects pack and the CRM agent. The email reads are named in DEFERRED
of the HRD-2 report.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest
import skill_projects
from acb_common.tool_refusals import GatewayRefusal
from skill_projects.client import uuid_of

from tests.unit._crm_agent_fakes import load_agent_module

REPO = Path(__file__).resolve().parents[2]
PROJECTS_DIR = REPO / "apps" / "skills" / "skill-projects" / "skill_projects"

#: ``param → (label, [module:function that prints it])``. The label must be in
#: the source of each named formatter. The formatter prints the id of a row the
#: member can see, so a label here means the model can read the id.
_TASK = ("full_id:", ["skill_projects.reads:_task_line"])
_PROJECT = ("full_id:", ["skill_projects.reads:_project_line"])
PRINTED: dict[str, tuple[str, list[str]]] = {
    # Tasks. A task number also resolves (``resolve_task_refs``).
    "task_id": _TASK,
    "task_ids": _TASK,
    "source_task_ids": _TASK,
    "add_task_ids": _TASK,
    "remove_task_ids": _TASK,
    "parent_task_id": _TASK,
    "other_task_id": _TASK,
    "target_task_id": _TASK,
    "root_task_id": _TASK,
    # Projects.
    "project_id": _PROJECT,
    "parent_project_id": _PROJECT,
    "destination_project_id": _PROJECT,
    # The task_detail blocks (HRD-2).
    "comment_id": ("comment id", ["skill_projects.reads:_timeline_block"]),
    "reply_to": ("comment id", ["skill_projects.reads:_timeline_block"]),
    "activity_id": ("activity id", ["skill_projects.reads:_timeline_block"]),
    "attachment_id": ("attachment id", ["skill_projects.reads:_attachments_block"]),
    "link_id": ("link id", ["skill_projects.reads:_relations_block"]),
    # Other Projects reads.
    "cycle_id": ("cycle_id", ["skill_projects.cycles:list_cycles"]),
    "reminder_id": ("reminder_id", ["skill_projects.reads:list_reminders"]),
    "report_id": ("full_id:", ["skill_projects.reads:report_list"]),
    "decision_id": ("decision_id", ["skill_projects.inbox:my_decisions"]),
    "update_id": ("update_id", ["skill_projects.inbox:list_project_updates"]),
    "view_id": ("view_id", ["skill_projects.inbox:project_views"]),
    "template_id": ("template_id", ["skill_projects.inbox:list_templates"]),
    "page_id": ("page_id", ["skill_projects.inbox:project_authorities"]),
    "pin_id": ("pin_id", ["skill_projects.inbox:project_authorities"]),
}

#: Ids that have their own resolver or a second reader. Each entry has a reason.
ALLOWED: dict[str, str] = {
    "target_id": "watchers, watch and open_in_app take a task or project id by `kind`; both full_id readers print it",
    "subject_id": "an internal pair of a decision; the model never gives it",
    "parent_id": "an internal helper argument, not a tool argument",
    "account_id": "an email account resolves by name or address (spec §4.2 allowlist)",
    "request_id": "a Studio request resolves through its own list (spec §4.2 allowlist)",
}

#: CRM ``param → (label, [function names of the CRM module that print it])``.
CRM_PRINTED: dict[str, tuple[str, list[str]]] = {
    "record_id": ("(id=", ["_row_line"]),
    "deal_id": ("(id=", ["_row_line"]),
    "lead_id": ("(id=", ["_row_line"]),
    "activity_id": ("activity id", ["get_timeline"]),
    "pipeline_id": ("(id=", ["acb_skills.crm_tools:_pipeline_lines"]),
}


def _source_of(ref: str) -> str:
    mod, _, name = ref.partition(":")
    return inspect.getsource(getattr(importlib.import_module(mod), name))


def _id_params(fn: object) -> list[str]:
    return [
        p for p in inspect.signature(fn).parameters
        if re.search(r"(_id|_ids|reply_to)$", p)
    ]


def test_every_projects_id_parameter_has_a_printing_read_tool() -> None:
    failures: list[str] = []
    seen: set[str] = set()
    for name in skill_projects.__all__:
        for param in _id_params(getattr(skill_projects, name)):
            seen.add(param)
            if param in ALLOWED:
                continue
            if param not in PRINTED:
                failures.append(f"{name}({param}): no read tool is registered for it")
                continue
            label, readers = PRINTED[param]
            for ref in readers:
                if label not in _source_of(ref):
                    failures.append(
                        f"{name}({param}): {ref} does not print {label!r}"
                    )
    assert not failures, "\n".join(sorted(set(failures)))
    stale = (set(PRINTED) | set(ALLOWED)) - seen - {"request_id", "account_id", "parent_id", "subject_id"}
    assert not stale, f"registry entries no tool uses: {sorted(stale)}"


def test_crm_activity_id_is_printed_by_the_timeline() -> None:
    crm = load_agent_module("crm_agent_id_provenance")
    failures = []
    for fn in crm._TOOLS:
        for param in _id_params(fn):
            if param not in CRM_PRINTED:
                failures.append(f"{fn.__name__}({param}): no read tool is registered")
                continue
            label, readers = CRM_PRINTED[param]
            for reader in readers:
                text = _source_of(reader) if ":" in reader else inspect.getsource(getattr(crm, reader))
                if label not in text:
                    failures.append(f"{fn.__name__}({param}): {reader} does not print {label!r}")
    assert not failures, "\n".join(failures)


def _uuid_of_labels() -> set[str]:
    """Every literal ``what`` that a Projects module gives to ``uuid_of``."""
    found: set[str] = set()
    for path in PROJECTS_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "uuid_of"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                found.add(node.args[1].value)
    return found


@pytest.mark.parametrize("what", sorted(_uuid_of_labels() | {"id"}))
def test_each_uuid_refusal_names_a_read_tool(what: str) -> None:
    with pytest.raises(GatewayRefusal) as caught:
        uuid_of("not-an-id", what)
    words = set(re.findall(r"[a-z_]+", str(caught.value)))
    assert words & set(skill_projects.__all__), (
        f"the refusal for {what!r} names no read tool: {caught.value}"
    )


def test_uuid_of_strips_its_own_kind_and_refuses_another() -> None:
    real = "3f2b8f7e-1c1d-4f55-9a3e-0f6f1b7a2c11"
    assert uuid_of(f"task:{real}", "task_id") == real
    with pytest.raises(GatewayRefusal) as caught:
        uuid_of(f"deal:{real}", "task_id")
    assert "deal" in str(caught.value) and "task" in str(caught.value)
    with pytest.raises(GatewayRefusal):
        uuid_of(f"task:{real}", "project_id")


def test_crm_record_uuid_strips_its_own_kind_and_refuses_another() -> None:
    crm = load_agent_module("crm_agent_kind_prefix")
    real = "3f2b8f7e-1c1d-4f55-9a3e-0f6f1b7a2c11"
    assert crm._record_uuid(f"deal:{real}") == real
    assert crm._record_uuid(f"lead:{real}") == real
    from acb_skills import crm_tools

    assert crm_tools._record_uuid(f"deal:{real}") == real
    with pytest.raises(GatewayRefusal):
        crm_tools._record_uuid(f"email:{real}")
    with pytest.raises(GatewayRefusal) as caught:
        crm._record_uuid(f"task:{real}")
    assert "task" in str(caught.value)


def test_email_reads_strip_their_kind_and_refuse_another() -> None:
    from acb_common.tool_refusals import strip_kind_prefix

    real = "3f2b8f7e-1c1d-4f55-9a3e-0f6f1b7a2c11"
    assert strip_kind_prefix(f"email:{real}", "email") == real
    assert strip_kind_prefix(real, "email") == real
    with pytest.raises(GatewayRefusal) as caught:
        strip_kind_prefix(f"task:{real}", "email", read_tool="query_inbox")
    assert "task" in str(caught.value) and "query_inbox" in str(caught.value)
    source = (REPO / "apps/agents/agent-email-assistant/agents.py").read_text(encoding="utf-8")
    assert source.count("_email_ref(") >= 4  # the def and the three reads


@pytest.mark.asyncio
async def test_the_tool_wrapper_resolves_a_task_number_through_the_search_route(monkeypatch) -> None:
    """``244`` and ``#244`` reach the tool as the id the member's search finds."""
    from skill_projects import client
    from skill_projects.refusals import refusals_as_text

    real = "3f2b8f7e-1c1d-4f55-9a3e-0f6f1b7a2c11"
    calls: list[tuple[str, dict]] = []

    async def fake_get(path, params=None):
        calls.append((path, params or {}))
        rows = [{"task_number": 2441, "id": "x"}, {"task_number": 244, "id": real}]
        return {"rows": rows} if params and params["q"] == "#244" else {"rows": []}

    monkeypatch.setattr(client, "get", fake_get)

    async def tool(task_id: str, task_ids: str = "", note: str = "") -> str:
        return f"{task_id}|{task_ids}|{note}"

    wrapped = refusals_as_text(tool)
    assert await wrapped("#244", task_ids=f"244, {real}", note="7") == f"{real}|{real}, {real}|7"
    assert calls[0][0] == "/projects/search"  # the existing route, member-scoped
    missed = await wrapped("999")
    assert missed.startswith("Refused:") and "find_tasks" in missed
