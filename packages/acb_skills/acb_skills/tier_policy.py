"""The AI tier policy — which Router tier serves each step. WS-45, D90.

Spec: ``project-docs/specs/ai_tier_routing.md``.

S1 builds two parts of this module, and S2 adds the rest (the table of §4.1,
``TOOL_HINTS`` and the per-request middleware):

* :func:`tier_routing_on`, the ONE reader of ``AI_TIER_ROUTING`` (§9). It
  names the agents that the policy covers. It fails closed.
* The System-1 tier and its threshold per effort (§5, Q7).

🔴 **One home for the policy.** §4.1 puts the table here, and nothing else
holds a second copy. Add a rung here, never at a call site.
"""
from __future__ import annotations

from acb_common import get_logger

_log = get_logger("acb_skills.tier_policy")

#: The tier that serves every System-1 request, in every effort mode (§5).
#: No setting moves it. The owner put System 1 on the fast tier.
SYSTEM_ONE_TIER = "tier-fast"

#: The confidence under which System 1 hands a decision back to the main
#: model (§5, Q7). A higher effort hands back more decisions. That is how
#: effort reaches a decision, because the tier of a decision never changes.
SYSTEM_ONE_THRESHOLDS: dict[str, float] = {
    "auto": 0.70,
    "thinking": 0.80,
    "max": 0.90,
}

#: The value of ``AI_TIER_ROUTING`` that covers every agent.
ALL_AGENTS = "*"


def covered_agents() -> frozenset[str]:
    """The agent names that ``AI_TIER_ROUTING`` lists. Empty means OFF.

    A broken settings read reads as empty, so it covers no agent.
    """
    try:
        from acb_common import get_settings

        raw = str(get_settings().ai_tier_routing or "")
    except Exception:  # a broken settings read must not arm the policy
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def tier_routing_on(agent: str | None) -> bool:
    """True when the tier policy covers *agent*. ONE reader, and it fails closed.

    The value is a comma list of agent names, or ``*`` for every agent. An
    empty value, an empty agent name and any error read as OFF. The idiom is
    ``executor._native_sessions_enabled``.
    """
    name = str(agent or "").strip()
    if not name:
        return False
    try:
        names = covered_agents()
        return ALL_AGENTS in names or name in names
    except Exception:  # never arm the policy on a fault
        _log.warning("tier_policy.flag_read_failed")
        return False


def system_one_threshold(effort: str | None) -> float:
    """The System-1 threshold for the run's effort mode (§5).

    *effort* is the run's ``think_mode``: ``auto``, ``thinking`` or ``max``.
    An unknown or absent value reads as ``auto``, as the gateway reads it.
    """
    key = str(effort or "").strip().lower()
    return SYSTEM_ONE_THRESHOLDS.get(key, SYSTEM_ONE_THRESHOLDS["auto"])
