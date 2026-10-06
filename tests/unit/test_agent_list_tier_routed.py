"""The agent list tells the chat which agents the tier policy covers.

WS-45 S3 (D90). Spec: ``project-docs/specs/ai_tier_routing.md`` §9 and S3.

The chat drops its model picker for an agent that ``AI_TIER_ROUTING``
covers. The client must not hard-code that list, so ``GET /agent`` stamps
``tier_routed`` on each entry from ``tier_policy.tier_routing_on``, the one
reader of the flag. ``workbench/control_plane/src/lib/tierRouting.ts`` reads
the field.

Hermetic: no SQL runs on this path, so R8 binds nothing here.

Mutations this file catches (R7), each run red before the change:

* ``_stamp_tier_routing`` reads its own copy of the flag, or marks an agent
  the flag does not name -> ``test_the_stamp_follows_the_one_reader``;
* a fault arms the UI -> ``test_a_broken_reader_marks_no_agent``;
* ``list_agents`` stops calling the stamp, or calls it after the access
  filter on a copy -> ``test_list_agents_carries_the_field``.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from acb_common.settings import get_settings
from acb_skills import tier_policy

from gateway.routes import agent as agent_routes


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _flag(monkeypatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("AI_TIER_ROUTING", raising=False)
    else:
        monkeypatch.setenv("AI_TIER_ROUTING", value)
    get_settings.cache_clear()


def _entries() -> list[dict]:
    return [{"name": "projects-assistant"}, {"name": "email-assistant"}, {"name": ""}]


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        (None, [False, False, False]),
        ("", [False, False, False]),
        ("projects-assistant", [True, False, False]),
        # A name is matched whole, and an empty name is never covered.
        ("projects", [False, False, False]),
        ("*", [True, True, False]),
    ],
)
def test_the_stamp_follows_the_one_reader(monkeypatch, flag, expected) -> None:
    _flag(monkeypatch, flag)
    agents = _entries()
    agent_routes._stamp_tier_routing(agents)
    assert [a["tier_routed"] for a in agents] == expected
    # Each value is the reader's own answer, so the chat and the executor agree.
    assert [a["tier_routed"] for a in agents] == [
        tier_policy.tier_routing_on(a["name"]) for a in agents
    ]


def test_a_broken_reader_marks_no_agent(monkeypatch) -> None:
    _flag(monkeypatch, "*")

    def boom(_name):
        raise RuntimeError("settings fault")

    monkeypatch.setattr(tier_policy, "tier_routing_on", boom)
    agents = _entries()
    agent_routes._stamp_tier_routing(agents)
    assert [a["tier_routed"] for a in agents] == [False, False, False]


def test_list_agents_carries_the_field(monkeypatch) -> None:
    """The real route, with its loaders replaced, stamps every entry."""
    _flag(monkeypatch, "projects-assistant")
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(agent_routes, "_declared_runtime", lambda *_a, **_k: None)
    monkeypatch.setattr(agent_routes, "_load_agent_aliases", lambda: {})
    user = SimpleNamespace(has_permission=lambda _p: True, can_run_agent=lambda _n: True)

    listed = asyncio.run(agent_routes.list_agents(user=user))

    by_name = {a["name"]: a for a in listed}
    assert by_name["projects-assistant"]["tier_routed"] is True
    assert all("tier_routed" in a for a in listed)
    assert [n for n, a in by_name.items() if a["tier_routed"]] == ["projects-assistant"]


def test_list_agents_marks_no_agent_with_the_flag_unset(monkeypatch) -> None:
    """Ship dark: with ``AI_TIER_ROUTING`` unset, no entry says covered."""
    _flag(monkeypatch, None)
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(agent_routes, "_declared_runtime", lambda *_a, **_k: None)
    monkeypatch.setattr(agent_routes, "_load_agent_aliases", lambda: {})
    user = SimpleNamespace(has_permission=lambda _p: True, can_run_agent=lambda _n: True)

    listed = asyncio.run(agent_routes.list_agents(user=user))

    assert listed
    assert not any(a["tier_routed"] for a in listed)
