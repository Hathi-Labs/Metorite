"""The AI tier policy — WS-45 (D90). S1 part: the flag and its one reader.

Spec: ``project-docs/specs/ai_tier_routing.md`` §9 and §10.

``AI_TIER_ROUTING`` names the agents that the policy covers: a comma list,
or ``*``. Empty means OFF. ``tier_policy.tier_routing_on`` is the ONE reader,
and it fails closed. S2 adds the table, the tool hints and the middleware to
this file.

Hermetic: no SQL runs on this path, so R8 binds nothing here.

Mutations this file catches (R7):

* ``tier_routing_on`` returns ``True`` on a settings fault
  -> ``test_a_broken_settings_read_reads_as_off``;
* ``*`` stops covering every agent -> ``test_a_star_covers_every_agent``;
* an empty name reads as covered under ``*`` -> ``test_no_name_is_never_covered``;
* a threshold moves -> ``test_the_thresholds_are_the_owners``.
"""
from __future__ import annotations

import pytest
from acb_common.settings import get_settings
from acb_skills import tier_policy


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


def test_the_flag_ships_off(monkeypatch) -> None:
    _flag(monkeypatch, None)
    assert get_settings().ai_tier_routing == ""
    assert tier_policy.covered_agents() == frozenset()
    for name in ("projects-assistant", "email-assistant", "orchestrator"):
        assert tier_policy.tier_routing_on(name) is False


def test_a_list_covers_exactly_the_agents_it_names(monkeypatch) -> None:
    _flag(monkeypatch, " projects-assistant , crm-assistant ,, ")
    assert tier_policy.covered_agents() == frozenset({"projects-assistant", "crm-assistant"})
    assert tier_policy.tier_routing_on("projects-assistant") is True
    assert tier_policy.tier_routing_on("crm-assistant") is True
    assert tier_policy.tier_routing_on("email-assistant") is False
    # A name is matched whole, never as a prefix.
    assert tier_policy.tier_routing_on("projects") is False


def test_a_star_covers_every_agent(monkeypatch) -> None:
    _flag(monkeypatch, "*")
    for name in ("projects-assistant", "email-assistant", "task-manager"):
        assert tier_policy.tier_routing_on(name) is True


def test_no_name_is_never_covered(monkeypatch) -> None:
    _flag(monkeypatch, "*")
    assert tier_policy.tier_routing_on(None) is False
    assert tier_policy.tier_routing_on("  ") is False


def test_a_broken_settings_read_reads_as_off(monkeypatch) -> None:
    """The flag fails closed (§10). A settings fault covers no agent."""
    _flag(monkeypatch, "*")
    import acb_common

    def boom():
        raise RuntimeError("settings are broken")

    monkeypatch.setattr(acb_common, "get_settings", boom)
    assert tier_policy.covered_agents() == frozenset()
    assert tier_policy.tier_routing_on("projects-assistant") is False


def test_the_thresholds_are_the_owners() -> None:
    """§5 and Q7: 0.70, 0.80 and 0.90 by effort, and an unknown mode is Auto."""
    assert tier_policy.SYSTEM_ONE_THRESHOLDS == {"auto": 0.70, "thinking": 0.80, "max": 0.90}
    assert tier_policy.system_one_threshold("auto") == 0.70
    assert tier_policy.system_one_threshold("Thinking") == 0.80
    assert tier_policy.system_one_threshold("max") == 0.90
    assert tier_policy.system_one_threshold(None) == 0.70
    assert tier_policy.system_one_threshold("turbo") == 0.70


def test_system_one_runs_on_the_fast_tier() -> None:
    """§5: a decision stays on ``tier-fast`` in every mode."""
    assert tier_policy.SYSTEM_ONE_TIER == "tier-fast"
