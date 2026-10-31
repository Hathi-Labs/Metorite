"""The "used your profile" mark, server half (WS-63 PZ-20b).

Spec: ``project-docs/specs/personalization.md`` §13.3 item 1 and §13.4. The
owner decided on 2026-10-30 that the mark shows only when the member's own
stored profile holds something. A block of the directory name draws none.

R7 fences named here:

* ``pz20b-event``: a solo orchestrator run with a block from a stored profile
  that holds something yields one ``assistant_profile_used`` event, and the
  value is ``{"v": 1}``. A name-only block, a shared room, a run with no
  member, the flag off and another agent yield none.
* ``pz20b-peek``: the helper does not use up the due line of the interview.
* ``pz20b-reload``: ``fold_run_events`` keeps the event, so a reload draws it.
* ``pz20b-place``: the stream yields the mark after ``_assert_may_run_agent``.
* ``pz20b-const``: the Python name and the web name agree.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from acb_common import assistant_profile as ap
from orchestrator import executor as ex

from tests.unit import test_executor_profile_block as epb
from tests.unit.test_assistant_profile_interview import NOW, _iv, clock, writes  # noqa: F401
from tests.unit.test_executor_profile_block import run  # noqa: F401

REPO = Path(__file__).resolve().parents[2]


def _events(agent: str = "orchestrator") -> list[dict[str, Any]]:
    frame = ex._profile_used_sse(agent)
    if frame is None:
        return []
    body = frame.split("data:", 1)[1].strip().splitlines()[0]
    return [json.loads(body)]


def test_a_stored_profile_with_content_yields_one_event_with_no_text(run) -> None:  # noqa: F811
    (event,) = _events()
    assert event["type"] == "CUSTOM"
    assert event["name"] == ap.PROFILE_USED_EVENT == "assistant_profile_used"
    assert event["value"] == {"v": 1}


@pytest.mark.parametrize("raw", [
    {"v": 1, "address": {"greeting": "brief"}},
    {"v": 1, "style": {"length": "short"}},
    {"v": 1, "aliases": [{"id": "a1", "alias": "Raj", "email": "raj@fracktal.in"}]},
    {"v": 1, "facts": [{"id": "f1", "text": "Travels", "kind": "routine"}]},
])
def test_any_one_kind_of_stored_content_counts(run, raw) -> None:  # noqa: F811
    run["raw"] = raw
    assert len(_events()) == 1


@pytest.mark.parametrize("raw", [None, {}, {"v": 1, "learning": True}])
def test_a_name_only_block_yields_none(run, raw) -> None:  # noqa: F811
    run["raw"] = raw
    run["preferred"] = "Ana"
    run["display"] = "Ana Rao"
    # The block holds the directory name, so the model gets it.
    assert ex._member_profile_entry("orchestrator")[0]
    ex._PROFILE_CACHE.clear()
    assert _events() == []


def test_a_shared_room_yields_none(run) -> None:  # noqa: F811
    token = ex._RUN_SOLO_ROOM.set(False)
    try:
        assert _events() == []
    finally:
        ex._RUN_SOLO_ROOM.reset(token)


def test_a_run_with_no_member_yields_none(run) -> None:  # noqa: F811
    run["member"] = ""
    assert _events() == []


def test_the_flag_off_yields_none(run, monkeypatch) -> None:  # noqa: F811
    epb._flag(monkeypatch, False)
    assert _events() == []


def test_another_agent_yields_none(run) -> None:  # noqa: F811
    assert _events("email-assistant") == []


def test_a_cached_turn_yields_the_event_again(run) -> None:  # noqa: F811
    assert len(_events()) == 1
    assert len(_events()) == 1
    assert len(run["reads"]) == 1


def test_the_helper_leaves_the_due_line_for_the_model(run, clock, writes) -> None:  # noqa: F811
    run["raw"] = {**copy.deepcopy(epb.EXAMPLE), "interview": _iv()}
    assert len(_events()) == 1
    # The model's build comes after the helper, and it still gets the line.
    block = ex._member_profile_entry("orchestrator")[0]
    assert "Interview (one question is due)" in block


def test_the_helper_runs_inside_the_peek() -> None:
    import inspect

    src = inspect.getsource(ex._profile_used_sse)
    assert "with _interview_peek():" in src
    assert src.index("with _interview_peek():") < src.index("_member_profile_entry(")


def test_the_stream_yields_the_mark_after_the_access_check() -> None:
    import inspect

    src = inspect.getsource(ex.run_agent_stream)
    assert src.index("await _assert_may_run_agent(agent_name)") < src.index(
        "_profile_used_sse(agent_name)")
    assert src.index('"RUN_STARTED"') < src.index("_profile_used_sse(agent_name)")


def test_a_reload_keeps_the_event() -> None:
    from gateway.chat_fold import fold_run_events

    folded = fold_run_events([
        {"type": "RUN_STARTED", "runId": "r1", "threadId": "t1"},
        {"type": "CUSTOM", "name": ap.PROFILE_USED_EVENT, "value": {"v": 1}},
        {"type": "TEXT_MESSAGE_CONTENT", "delta": "Hello."},
        {"type": "RUN_FINISHED"},
    ]) or {}
    kept = [e for e in folded["custom_events"] if e["name"] == ap.PROFILE_USED_EVENT]
    assert len(kept) == 1 and kept[0]["value"] == {"v": 1}


def test_the_web_constant_matches() -> None:
    ts = (REPO / "workbench/control_plane/src/lib/profileMark.ts").read_text(encoding="utf-8")
    assert f'PROFILE_USED_EVENT = "{ap.PROFILE_USED_EVENT}"' in ts
