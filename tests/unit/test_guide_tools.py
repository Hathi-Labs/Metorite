"""``find_guide`` and ``show_guide``: the chat finds a guide and shows it (WS-OB GD-7).

Spec: ``project-docs/specs/assistant_guide.md`` §5.4. The tools are the
orchestrator's own (``orchestrator/agents.py``), and the logic is
``orchestrator/guide_tools.py``. They list only when ``GUIDE_ORGS`` opens the
org, and they never call a model.

Each refusal has a test: a pane the member lacks, an admin guide for a member,
a preview pane (through a fixture guide), an unknown id, a nested run, and a
second page move in one turn, in both orders with ``show_in_app``.

The member's features and admin right come from the database in production.
These tests replace them with a fixed set, as ``test_show_in_app.py`` does.

Run::

    uv run pytest tests/unit/test_guide_tools.py -q
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from acb_common.app_manifests.guide_manifest import Guide, GuideStep
from orchestrator import guide_tools as gt
from orchestrator import in_app_path as iap

ORG = "org-guide-test"
#: The member holds the CRM and the tour. Not Projects.
HELD = frozenset({"crm", "email", "chat"})


class _Queue:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def put(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.lines.append((event, kw))


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> _Queue:
    """A run with GUIDE_ORGS open, a queue, and a member who holds ``HELD``."""
    pytest.importorskip("agent_framework", reason="MAF not installed")
    from acb_common import get_settings
    from orchestrator import executor

    queue = _Queue()
    monkeypatch.setattr(get_settings(), "guide_orgs", "*", raising=False)
    monkeypatch.setattr(gt, "run_org", lambda: ORG)
    monkeypatch.setattr(executor, "resolve_run_queue", lambda key=None: queue)

    async def _features() -> frozenset[str]:
        return HELD

    async def _admin() -> bool:
        return False

    monkeypatch.setattr(iap, "member_features", _features)
    monkeypatch.setattr(gt, "member_is_admin", _admin)
    return queue


def _find(question: str) -> dict[str, Any]:
    from orchestrator.agents import find_guide

    return asyncio.run(find_guide(question))


def _show(guide_id: str) -> dict[str, Any]:
    from orchestrator.agents import show_guide

    return asyncio.run(show_guide(guide_id))


def _open(path: str) -> dict[str, Any]:
    from orchestrator.agents import show_in_app

    return asyncio.run(show_in_app(path, "Here it is."))


def _guide_events(queue: _Queue) -> list[dict[str, Any]]:
    return [e for e in queue.events if e.get("name") == "guide"]


def _fixture_guide(**over: Any) -> Guide:
    base: dict[str, Any] = {
        "id": "fix.thing", "app": "fix", "feature": None, "admin_only": False,
        "title": "Do the thing", "asks": ("do the thing", "thing", "make a thing"),
        "steps": (GuideStep("open", "Open it.", path="/crm"),), "do_tool": None,
    }
    base.update(over)
    return Guide(**base)


# ── find_guide ──────────────────────────────────────────────────────────────

def test_find_ranks_add_a_deal_first(run: _Queue) -> None:
    out = _find("how do I add a deal")
    assert out["guides"][0]["id"] == "crm.add-deal"
    assert out["guides"][0]["steps"].startswith("1. ")
    assert set(out["guides"][0]) == {"id", "title", "app", "steps"}


def test_find_returns_at_most_three(run: _Queue) -> None:
    out = _find("add a deal or a lead or a note, find a contact, move a deal, see the pipeline")
    assert 1 <= len(out["guides"]) <= 3


def test_find_makes_no_page_move(run: _Queue) -> None:
    _find("how do I add a deal")
    assert run.events == []


def test_find_no_match_hints_and_logs_without_the_question(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _Log()
    monkeypatch.setattr(gt, "_log", log)
    question = "how do I fold the secretmoon"
    assert _find(question) == {"guides": [], "hint": "No guide matches. Offer to open the app."}
    assert log.lines == [("guide.find.no_match", {"org": ORG, "words": 6})]
    assert "secretmoon" not in repr(log.lines)


def test_find_hides_a_guide_of_a_pane_the_member_lacks(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _chat_only() -> frozenset[str]:
        return frozenset({"chat"})

    monkeypatch.setattr(iap, "member_features", _chat_only)
    out = _find("how do I add a deal")
    assert all(g["app"] != "crm" for g in out["guides"])


def test_find_hides_an_admin_guide_from_a_member(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gt, "all_guides", lambda: (_fixture_guide(admin_only=True),))
    assert _find("do the thing")["guides"] == []


def test_find_shows_an_admin_guide_to_an_admin(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _admin() -> bool:
        return True

    monkeypatch.setattr(gt, "member_is_admin", _admin)
    monkeypatch.setattr(gt, "all_guides", lambda: (_fixture_guide(admin_only=True),))
    assert _find("do the thing")["guides"][0]["id"] == "fix.thing"


def test_find_hides_a_guide_that_opens_a_preview_pane(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    preview = _fixture_guide(steps=(GuideStep("open", "Open it.", path="/dashboard"),))
    monkeypatch.setattr(gt, "all_guides", lambda: (preview,))
    assert _find("do the thing")["guides"] == []


# ── show_guide ──────────────────────────────────────────────────────────────

def test_show_emits_one_guide_event(run: _Queue) -> None:
    out = _show("crm.add-deal")
    assert out["started"] == "crm.add-deal"
    assert run.events == [{"type": "CUSTOM", "name": "guide", "value": {"guide_id": "crm.add-deal"}}]


def test_show_refuses_an_unknown_id(run: _Queue) -> None:
    assert _show("crm.nope") == {"error": "no guide has that id"}
    assert run.events == []


def test_show_refuses_a_pane_the_member_lacks(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _chat_only() -> frozenset[str]:
        return frozenset({"chat"})

    monkeypatch.setattr(iap, "member_features", _chat_only)
    assert _show("crm.add-deal") == {"error": "the member may not run that guide"}
    assert run.events == []


def test_show_refuses_an_admin_guide_for_a_member(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gt, "all_guides", lambda: (_fixture_guide(admin_only=True),))
    assert _show("fix.thing") == {"error": "the member may not run that guide"}
    assert run.events == []


def test_show_refuses_a_guide_that_opens_a_preview_pane(
    run: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    preview = _fixture_guide(steps=(GuideStep("open", "Open it.", path="/dashboard"),))
    monkeypatch.setattr(gt, "all_guides", lambda: (preview,))
    assert _show("fix.thing") == {"error": "the member may not run that guide"}
    assert run.events == []


def test_show_refuses_a_nested_run(run: _Queue) -> None:
    from acb_skills.agent_tools import _delegation_chain

    token = _delegation_chain.set(("email-assistant",))
    try:
        assert "error" in _show("crm.add-deal")
    finally:
        _delegation_chain.reset(token)
    assert run.events == []
    assert _show("crm.add-deal")["started"] == "crm.add-deal"


def test_show_then_show_in_app_is_one_move(run: _Queue) -> None:
    assert _show("crm.add-deal")["started"] == "crm.add-deal"
    assert _open("/email") == {"error": "already opened a page this turn"}
    assert [e["name"] for e in run.events] == ["guide"]


def test_show_in_app_then_show_is_one_move(run: _Queue) -> None:
    assert _open("/email")["opened"] == "/email"
    assert _show("crm.add-deal") == {"error": "already moved the page this turn"}
    assert [e["name"] for e in run.events] == ["navigate"]


def test_a_second_show_in_one_turn_is_refused(run: _Queue) -> None:
    assert _show("crm.add-deal")["started"] == "crm.add-deal"
    assert _show("crm.add-lead") == {"error": "already moved the page this turn"}
    assert len(_guide_events(run)) == 1


def test_a_refused_show_does_not_spend_the_move(run: _Queue) -> None:
    assert "error" in _show("crm.nope")
    assert _open("/email")["opened"] == "/email"


def test_show_fails_closed_with_the_flag_off(run: _Queue, monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "guide_orgs", "", raising=False)
    assert _show("crm.add-deal") == {"error": "no guides here"}
    assert _find("how do I add a deal") == {"error": "no guides here"}
    assert run.events == []


# ── the tool list ───────────────────────────────────────────────────────────

def _tool_names(monkeypatch: pytest.MonkeyPatch, guide_orgs: str, org: str | None) -> list[str]:
    from acb_common import get_settings
    from orchestrator import agents as pkg

    mod = pkg._mod

    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(mod, "Agent", _FakeAgent)
    monkeypatch.setattr(mod, "_make_openai_client", lambda *a, **k: object())
    monkeypatch.setattr(mod, "_load_specialist_agents_as_tools", lambda: [])
    monkeypatch.setattr(gt, "run_org", lambda: None)
    monkeypatch.setattr(get_settings(), "guide_orgs", guide_orgs, raising=False)
    agent = mod.build_orchestrator_agent(with_history=False, organization_id=org)
    return [getattr(t, "__name__", "") for t in agent.kwargs["tools"]]


def test_the_flag_off_lists_neither_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("agent_framework", reason="MAF not installed")
    for orgs, org in (("", ORG), ("other-org", ORG), ("*", None)):
        names = _tool_names(monkeypatch, orgs, org)
        assert "find_guide" not in names and "show_guide" not in names, (orgs, org)


def test_the_flag_on_lists_both_after_the_page_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("agent_framework", reason="MAF not installed")
    off = _tool_names(monkeypatch, "", ORG)
    for orgs in (ORG, "*", f"x, {ORG.upper()}"):
        on = _tool_names(monkeypatch, orgs, ORG)
        assert "find_guide" in on and "show_guide" in on
        assert [n for n in on if n not in ("find_guide", "show_guide")] == off
        assert on.index("show_guide") == on.index("find_guide") + 1


def test_find_guide_stays_on_whatsapp_and_show_guide_does_not() -> None:
    from acb_skills import whatsapp_ui

    assert "show_guide" in whatsapp_ui.WITHHELD_TOOLS
    assert "find_guide" not in whatsapp_ui.WITHHELD_TOOLS
