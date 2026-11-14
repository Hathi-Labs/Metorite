"""Golden trajectory: the chat builds an app into a draft that the "New app"
button made (task 3227, STU-FIX).

Production, session 36762da6, 2026-11-14. The member asked for an app. The
model sent a patch with no ops, then saved under another id than the draft's.
The route refused, the model changed its id, the plan no longer matched, and the
member approved TWO cards for one app. The answer opened with working notes.

This replays the tool calls of that chat over the in-memory gateway of
``tests/unit/test_studio_package_tools.py``, offline:

1. ``studio_patch`` with a manifest and no ops checks the manifest.
2. One plan card, one save into the draft with another id, one publish.
3. The member saw one card, and the draft kept its own id.
4. The pack rules tell the model to keep working notes out of the answer.
"""
from __future__ import annotations

from acb_skills import studio_tools as st
from acb_skills.tool_packs import _STUDIO_RULES

from tests.unit.test_studio_package_tools import (  # noqa: F401 - pytest fixtures
    _plan,
    _save,
    _visit,
    card,
    gw,
    mark,
    redis,
    who,
)


async def test_the_build_into_a_new_app_draft_takes_one_card_and_one_id(
    gw, card, redis,  # noqa: F811
) -> None:
    draft = await _save(_visit("new-app-nufdl", name="New app"))
    pid = draft["package_id"]
    mine = _visit("event-inquiry-tracker", name="Event inquiry tracker")

    assert (await st.studio_patch(mine))["ok"], "a manifest alone is a valid call"
    await _plan(mine)
    saved = await st.studio_save(mine, pid, draft["based_on"])
    assert saved["ok"], saved
    published = await st.studio_publish(pid)

    assert published["ok"] and published["version"] == 1, published
    assert len(card.shown) == 1, "one approval card for one app"
    assert gw.packages[pid]["manifest"]["id"] == "new-app-nufdl"
    assert "id cannot change" not in str(saved)


def test_the_pack_rules_keep_working_notes_out_of_the_answer() -> None:
    assert "working notes" in _STUDIO_RULES and "ONE ID, ONE CARD" in _STUDIO_RULES
