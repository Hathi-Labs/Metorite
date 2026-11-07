"""No CRM record write in a Mirror run (WS-53 CRM-C7 item 4, done-when 3).

Spec: ``project-docs/specs/crm_platform.md`` §20.10 item 4. At run assembly,
after the own tool scope, ``_drop_mirror_record_writes`` reads
``GET /crm/mode`` as the member. In Mirror mode the run loses
``create_lead``, ``update_deal_status`` and ``convert_lead``, and keeps
``log_activity``, because a Metorite-only note saves in Mirror mode (CRM-Z6).
A failed read keeps the tools, and the 409 relay is the fallback.

The real ``crm-assistant`` is built from its file, and the fake sits at the
transport of ``acb_skills.crm_tools``, so the member header is real too.
"""

from __future__ import annotations

import ast
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("agent_framework")

import acb_skills.crm_tools as crm_tools
import orchestrator._tool_injection as ti

from tests.unit._crm_agent_fakes import (
    REPO_ROOT,
    FakeClient,
    FakeResponse,
    load_agent_module,
)

_M = load_agent_module("crm_assistant_mirror_test")
_WRITES = {"create_lead", "update_deal_status", "convert_lead"}


def _names(agents: list[Any]) -> set[str]:
    return {
        ti._tool_name(t)
        for agent in agents for _attr, pool in ti._own_tool_pools(agent) for t in pool
    }


def _gateway(monkeypatch, answer: Any, status: int = 200) -> list[dict]:
    calls: list[dict] = []

    class _Client(FakeClient):
        async def request(self, method: str, url: str, **kwargs: Any):
            calls.append({"method": method, "url": url,
                          "headers": kwargs.get("headers") or {}})
            return FakeResponse(answer, status)

    monkeypatch.setattr(crm_tools, "httpx", SimpleNamespace(
        AsyncClient=lambda **_kw: _Client(calls, None)))
    monkeypatch.setattr(crm_tools, "_current_user_email", lambda: "rep@fracktal.in")
    return calls


async def test_a_mirror_run_lacks_the_three_record_writes(monkeypatch) -> None:
    calls = _gateway(monkeypatch, {"mode": "mirror"})
    agents = _M.build_agents()
    assert _names(agents) >= _WRITES
    await ti._drop_mirror_record_writes(agents)
    held = _names(agents)
    assert not held & _WRITES
    assert "log_activity" in held
    assert "crm_list_records" in held and "search_crm" in held
    assert [c["url"].split("8080")[-1] for c in calls] == ["/crm/mode"]
    assert calls[0]["headers"]["X-User-Email"] == "rep@fracktal.in"


async def test_a_native_run_keeps_every_tool(monkeypatch) -> None:
    _gateway(monkeypatch, {"mode": "native"})
    # CRM-M3a: an org on CRM_PIPELINES_ORGS keeps crm_create_pipeline too.
    # The flag drop has its own fence (test_crm_pipelines_native.py).
    monkeypatch.setattr(ti, "_crm_pipelines_open", lambda: True)
    agents = _M.build_agents()
    before = _names(agents)
    await ti._drop_mirror_record_writes(agents)
    assert _names(agents) == before


@pytest.mark.parametrize("status", [403, 404, 500])
async def test_a_failed_read_keeps_the_tools(monkeypatch, status: int) -> None:
    _gateway(monkeypatch, {"detail": "Not Found"}, status=status)
    agents = _M.build_agents()
    await ti._drop_mirror_record_writes(agents)
    assert _names(agents) >= _WRITES


async def test_a_run_with_no_member_keeps_the_tools(monkeypatch) -> None:
    """No member means no read at all, never a read as the platform."""
    calls = _gateway(monkeypatch, {"mode": "mirror"})
    monkeypatch.setattr(crm_tools, "_current_user_email", lambda: "")
    agents = _M.build_agents()
    await ti._drop_mirror_record_writes(agents)
    assert _names(agents) >= _WRITES
    assert calls == []


async def test_an_agent_with_no_record_write_reads_nothing(monkeypatch) -> None:
    calls = _gateway(monkeypatch, {"mode": "mirror"})
    agent = SimpleNamespace(tools=[crm_tools.crm_list_records])
    await ti._drop_mirror_record_writes([agent])
    assert calls == []
    assert _names([agent]) == {"crm_list_records"}


def test_each_assembly_site_runs_the_filter_after_the_own_scope() -> None:
    """The three sites of ``executor.py``: the sub-agent, the batch run and
    the stream run. Each awaits the filter right after
    ``_apply_own_tool_scope``, so no site can build a Mirror run with the
    record writes."""
    tree = ast.parse(
        (REPO_ROOT / "apps" / "services" / "orchestrator" / "orchestrator"
         / "executor.py").read_text(encoding="utf-8"),
    )
    pairs = 0
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for first, second in pairwise(body):
            if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)):
                continue
            func = first.value.func
            if getattr(func, "id", None) != "_apply_own_tool_scope":
                continue
            call = getattr(second, "value", None)
            assert isinstance(call, ast.Await), (
                f"line {first.lineno}: the own scope is not followed by the "
                "Mirror filter"
            )
            assert getattr(call.value.func, "id", None) == "_drop_mirror_record_writes"
            pairs += 1
    assert pairs == 3


def test_the_dropped_set_keeps_log_activity() -> None:
    assert frozenset(_WRITES) == ti.MIRROR_RECORD_WRITES
    assert "log_activity" not in ti.MIRROR_RECORD_WRITES
    assert Path(crm_tools.__file__).name == "crm_tools.py"


async def test_a_run_in_a_covered_chain_reads_nothing(monkeypatch) -> None:
    """H-236: a covered run sends no request it does not need. Its egress
    control drops the three writes there anyway. Mutation: read the mode in a
    covered chain -> ``calls`` is not empty -> red."""
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    calls = _gateway(monkeypatch, {"mode": "mirror"})
    agents = _M.build_agents()
    with artifact_context_scope():
        bind_artifact_context(no_egress=True, covered_chain=True)
        await ti._drop_mirror_record_writes(agents)
    assert calls == []


async def test_a_held_run_reads_the_mode_because_it_keeps_the_writes(monkeypatch) -> None:
    """RX-1 (D169): a held chat keeps the record writes behind their cards. So
    the Mirror drop runs in a held run too, or a write would reach Zoho. Each
    write still says open_world=True, because the list keeps it, never a
    claim. Mutation: skip the read in a no_egress run -> the writes stay in
    Mirror mode -> red."""
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    calls = _gateway(monkeypatch, {"mode": "mirror"})
    agents = _M.build_agents()
    with artifact_context_scope():
        bind_artifact_context(no_egress=True)
        await ti._drop_mirror_record_writes(agents)
    assert len(calls) == 1
    assert not _names(agents) & _WRITES
    assert all(TOOL_ANNOTATIONS[name]["open_world"] for name in _WRITES)


@pytest.fixture(autouse=True)
def _held_card_writes_on(monkeypatch) -> None:
    """RX-1: this file tests the rule with the switch on. The switch off has its
    own test in ``test_held_card_writes.py``."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "*")
