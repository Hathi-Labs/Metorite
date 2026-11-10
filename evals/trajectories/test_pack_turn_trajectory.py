"""Golden trajectory: a chat keeps a Projects tool that it just used (task 1747).

Production, thread a398b43e, 2026-11-05 UTC. The member saw 'Function
"create_task" not found.' This locks the decision and its effect end to end,
offline, over the turns of one chat:

1. A tool of the core Projects pack runs.
2. The words of the next turn name email and the sales pipeline, and the cap
   moves the core pack out. The context of the turn says so, and it tells the
   model to load the pack first.
3. The model loads the pack. A pack that came in only by the words gives way
   (D169 item 8, as amended on 2026-11-06), and no hand-off runs.
4. The turn after that still holds ``create_task``, because a load is sticky.

The full fence is ``tests/unit/test_pack_turn_keeps_used_packs.py``.
"""
from __future__ import annotations

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import tool_packs as tp
from orchestrator import _tool_injection  # noqa: F401 - it makes ``tests`` importable here

from tests.unit.test_load_skill_tools import _call, _names
from tests.unit.test_pack_room import _frame, _stub_owners
from tests.unit.test_pack_turn_keeps_used_packs import (
    CORE,
    EMAIL_CRM,
    LIVE_1622,
    PA,
    _begin,
    _choose,
    _held_card_writes_on,  # noqa: F401 - a pytest fixture
    _row_run,
    _seed,
    _tools,
    store,  # noqa: F401 - a pytest fixture
)


async def test_the_chat_keeps_create_task_over_the_turns_of_the_report(
    store, monkeypatch,  # noqa: F811
) -> None:
    """Mutation: drop the words packs from ``PackRun.spare``, or the
    ``note_used`` call from ``LoadSkillTools._load`` -> red."""
    _stub_owners(monkeypatch)
    await _seed((CORE,))

    turn = await _begin(EMAIL_CRM)
    run, live, loader, plan = await _row_run(turn, LIVE_1622)
    assert CORE not in run.holds() and run.gone == (CORE,)
    assert PA in plan.via_loader and "load_skill_tools" in tp.left_line(run)

    with _frame(run, loader.run_key):
        answer, _passed = await _call(loader, CORE, live)
    assert answer.startswith("Loaded the Projects tools"), answer
    assert "create_task" in _names(live) and len(live) <= tp.MAX_REQUEST_TOOLS
    assert "load_skill_tools" in (run.refusal(PA) or ""), "the hand-off stays shut"

    later = _choose(await _begin("one more for Ravi"))
    assert CORE in later.slugs and "create_task" in _tools(later), later
