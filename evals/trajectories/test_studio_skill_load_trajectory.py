"""Golden trajectory: a saved Studio skill loads into a chat turn (TOF-1e,
task 3793; root ``AGENTS.md`` harness rule 3).

The run path is ``_turn_with_skills`` (the read of the skills the member may
load, then the match) and ``_apply_tool_packs`` (the code packs that a skill
brings). The store and the tool builders are stubs, so the eval is offline.
Four turns, one skill each:

1. A load by name: "use the lead desk" brings the CRM pack of ``tool:create_lead``.
2. A load by description: the turn names no skill and shares 2 stems with its
   description, and the same pack loads.
3. A refusal: on a full org a skill with two packs gets ``pack_cap``, and the
   run holds no skill and no pack.
4. The flag off: the skill fails with ``chat_tools_off``, and no pack builds.

Each case reads the log lines the executor writes (``executor.skill_matched``
and ``executor.skill_refused``), never a word of the skill text.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp

from tests.unit.test_skill_packs import (  # noqa: F401 - pytest fixtures and helpers
    _ID_D,
    _body,
    _events,
    _named,
    _run_apply,
    ti,
)

_DESC = "Use this when a customer asks for a refund of a delayed shipment."


def _stub_run(ti: Any, monkeypatch: pytest.MonkeyPatch, *, actions: list[str],  # noqa: F811
              description: str, flag: bool) -> dict[str, Any]:
    """One store with one published skill ``Lead desk``, and the stubs of the
    apply. Returns the packs built and the log lines."""
    import gateway.studio as studio_pkg

    head = SimpleNamespace(id=_ID_D, name="Lead desk", slug="lead-desk", description=description)
    version = SimpleNamespace(version=3, content_hash="0" * 64,
                              body=_body(description=description, actions=actions))

    class _Store:
        async def list_loadable(self, _db: Any, _member: str) -> list[Any]:
            return [head]

        async def loadable_versions(self, _db: Any, _member: str, ids: Any) -> list[Any]:
            return [(head, version)] if _ID_D in ids else []

    store = _Store()
    monkeypatch.setattr(studio_pkg, "skills_store", store, raising=False)
    monkeypatch.setitem(sys.modules, "gateway.studio.skills_store", store)
    monkeypatch.setattr(ti, "_chat_tools_open", lambda: flag)
    out: dict[str, Any] = {"built": [], "lines": []}

    async def _tools(slug: str, *_a: Any, **_k: Any) -> Any:
        out["built"].append(slug)
        return ti.PackBuild(slug=slug, owner="crm-assistant", held=False,
                            tools=tuple(_named(n) for n in sf.SKILL_FAMILIES[slug]["tools"]))

    monkeypatch.setattr(ti, "_pack_tools", _tools)
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())
    monkeypatch.setattr(ti, "_pack_base_count", lambda *_a, **_k: 40)
    monkeypatch.setattr(ti, "_pack_base_names", lambda *_a, **_k: set())
    for level in ("info", "warning"):
        monkeypatch.setattr(ti._log, level, lambda event, **kw: out["lines"].append((event, kw)))
    return out


async def test_a_skill_loads_by_its_name(ti, monkeypatch) -> None:  # noqa: F811
    run = _stub_run(ti, monkeypatch, actions=["tool:create_lead"], description="", flag=True)
    applied, _agent = await _run_apply(ti, "use the lead desk", allowed={"crm-records"})
    assert applied is not None and run["built"] == ["crm-records"]
    assert applied.record.skills == (f"skill:{_ID_D}",)
    assert _events(run["lines"], "executor.skill_matched") == [], "a name needs no description pass"


async def test_a_skill_loads_by_its_description(ti, monkeypatch) -> None:  # noqa: F811
    run = _stub_run(ti, monkeypatch, actions=["tool:create_lead"], description=_DESC, flag=True)
    applied, _agent = await _run_apply(ti, "my customer wants a refund", allowed={"crm-records"})
    assert applied is not None and run["built"] == ["crm-records"]
    assert applied.record.skills == (f"skill:{_ID_D}",)
    assert _events(run["lines"], "executor.skill_matched") == [{"skill": _ID_D, "hits": 2}]


async def test_a_skill_with_two_packs_on_a_full_org_gets_pack_cap(ti, monkeypatch) -> None:  # noqa: F811
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "")
    run = _stub_run(ti, monkeypatch, actions=["tool:create_lead", "tool:send_email"],
                    description="", flag=True)
    applied, _agent = await _run_apply(ti, "use the lead desk", allowed=frozenset(tp.PACKS))
    assert applied is None and run["built"] == []
    assert {"skill": _ID_D, "version": 3, "reason": "pack_cap",
            "packs": ["crm-records", "email"]} in _events(run["lines"], "executor.skill_refused")


async def test_with_the_flag_off_the_skill_fails_with_chat_tools_off(ti, monkeypatch) -> None:  # noqa: F811
    run = _stub_run(ti, monkeypatch, actions=["tool:create_lead"], description=_DESC, flag=False)
    applied, _agent = await _run_apply(ti, "use the lead desk", allowed={"crm-records"})
    assert applied is None and run["built"] == []
    assert {"skill": _ID_D, "version": 3, "reason": "chat_tools_off"} in _events(
        run["lines"], "executor.skill_refused")
    assert _events(run["lines"], "executor.skill_matched") == [], "the pass is off with the flag"
