"""Turn the ``decide`` tool ON for one test module (WS-31 CP-13d).

``DECIDE_ENABLED`` ships OFF, and with it off the injection chain does not
inject ``decide`` and the addendum does not name it
(``acb_skills.decide_tools.decide_tool_enabled``). The registry and schema
fences describe the tool set a box CAN inject, so they run with the flag on.
``tests/unit/test_decide_tool.py`` fences the OFF state.

Use it as a module-scoped autouse fixture::

    from tests.unit._decide_flag import decide_tool_on  # noqa: F401

Module scope matters. ``test_tool_schema_diet.py`` collects its tools in a
module-scoped fixture, and a function-scoped flag would arm too late.
"""
from __future__ import annotations

import pytest


def _clear_caches() -> None:
    from acb_common.settings import get_settings

    get_settings.cache_clear()
    try:
        import orchestrator._tool_injection as ti

        ti._build_injected_tools_addendum.cache_clear()
    except ImportError:  # pragma: no cover - orchestrator is a test dep
        pass
    try:
        from acb_skills import skill_families as sf

        sf.clear_cost_cache()
    except ImportError:  # pragma: no cover
        pass


@pytest.fixture(autouse=True, scope="module")
def decide_tool_on():
    """``DECIDE_ENABLED=true`` for the whole module, and the caches cleared."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DECIDE_ENABLED", "true")
        _clear_caches()
        yield
    _clear_caches()
