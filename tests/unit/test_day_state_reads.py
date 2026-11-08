"""CAL-6a (task 1680): the two reads of the saved day, with a fake ``_request``.

``my_tasks_get_day_state`` reads ``GET /tasks/calendar/day-state`` and
``my_tasks_rollover_log`` reads ``GET /tasks/calendar/rollover/log``.

Run: ``uv run pytest tests/unit/test_day_state_reads.py -q``.
"""
from __future__ import annotations

import pytest

pytest.importorskip("skill_my_tasks", reason="skill-my-tasks not installed")
from skill_my_tasks import core

ONE = "11111111-1111-1111-1111-111111111111"


def _fake(monkeypatch, answers: dict[str, object]) -> list[tuple[str, str, dict]]:
    calls: list[tuple[str, str, dict]] = []

    async def fake(method, path, **kw):
        calls.append((method, path, kw))
        out = answers[path.split("/")[-1] if path.startswith("/projects") else path]
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(core, "_request", fake)
    return calls


async def test_day_state_shows_the_one_thing_and_the_seeds(monkeypatch) -> None:
    calls = _fake(monkeypatch, {
        "/tasks/calendar/day-state": {"day": "2026-11-08", "one_thing_id": ONE,
                                      "seed_ids": ["s1", "s2"]},
        ONE: {"title": "Ship «the» report"}})
    text = await core.my_tasks_get_day_state("2026-11-08")
    assert calls[0] == ("GET", "/tasks/calendar/day-state", {"params": {"day": "2026-11-08"}})
    assert "Day state for 2026-11-08:" in text
    assert "★ One Thing: «Ship the report»" in text and f"id={ONE}" in text
    assert "Seeded tasks (2):" in text and "id=s1" in text and "id=s2" in text


async def test_day_state_without_a_date_asks_for_today(monkeypatch) -> None:
    calls = _fake(monkeypatch, {"/tasks/calendar/day-state": {"day": "x", "one_thing_id": None,
                                                              "seed_ids": []}})
    text = await core.my_tasks_get_day_state()
    day = calls[0][2]["params"]["day"]
    assert len(day) == 10 and day[4] == "-"
    assert "One Thing: not set." in text and "Seeded tasks: none." in text


async def test_day_state_pages_a_long_seed_list(monkeypatch) -> None:
    _fake(monkeypatch, {"/tasks/calendar/day-state": {
        "day": "2026-11-08", "one_thing_id": None, "seed_ids": [f"s{i}" for i in range(12)]}})
    text = await core.my_tasks_get_day_state("2026-11-08")
    assert "id=s7" in text and "id=s8" not in text
    assert "4 more seeded task(s) exist." in text


async def test_day_state_refuses_a_bad_date_and_calls_nothing(monkeypatch) -> None:
    calls = _fake(monkeypatch, {})
    assert "not a day" in await core.my_tasks_get_day_state("tomorrow")
    assert calls == []


async def test_day_state_keeps_the_id_when_the_title_is_unreadable(monkeypatch) -> None:
    _fake(monkeypatch, {
        "/tasks/calendar/day-state": {"one_thing_id": ONE, "seed_ids": []},
        ONE: core._GatewayError("gone", 404)})
    text = await core.my_tasks_get_day_state("2026-11-08")
    assert "not readable now" in text and f"id={ONE}" in text


async def test_day_state_passes_a_gateway_error_on(monkeypatch) -> None:
    _fake(monkeypatch, {"/tasks/calendar/day-state": core._GatewayError("boom", 500)})
    with pytest.raises(core._GatewayError):
        await core.my_tasks_get_day_state("2026-11-08")


async def test_rollover_log_lists_entries_newest_first(monkeypatch) -> None:
    calls = _fake(monkeypatch, {"/tasks/calendar/rollover/log": [
        {"title": "Write «spec»", "rolled_from": "2026-11-06", "rolled_to": "2026-11-07",
         "at": "2026-11-07T06:30:00+00:00"},
        {"title": "Call Ravi", "rolled_from": "2026-11-05", "rolled_to": "2026-11-06", "at": None}]})
    text = await core.my_tasks_rollover_log()
    assert calls[0][:2] == ("GET", "/tasks/calendar/rollover/log")
    assert calls[0][2]["params"] == {"limit": 21}  # one more than the page, to see a next page
    assert "• «Write spec»: 2026-11-06 → 2026-11-07 (at 2026-11-07 06:30)" in text
    assert "• «Call Ravi»: 2026-11-05 → 2026-11-06" in text and text.index("spec") < text.index("Ravi")
    assert "Showing" not in text


async def test_rollover_log_pages_with_a_hint(monkeypatch) -> None:
    rows = [{"title": f"t{i}", "rolled_from": "a", "rolled_to": "b"} for i in range(4)]
    _fake(monkeypatch, {"/tasks/calendar/rollover/log": rows})
    text = await core.my_tasks_rollover_log(offset=0, limit=2)
    assert "«t0»" in text and "«t1»" in text and "«t2»" not in text
    assert "offset=2" in text


async def test_rollover_log_empty_says_so(monkeypatch) -> None:
    _fake(monkeypatch, {"/tasks/calendar/rollover/log": []})
    assert "empty" in await core.my_tasks_rollover_log()


async def test_rollover_log_passes_a_gateway_error_on(monkeypatch) -> None:
    _fake(monkeypatch, {"/tasks/calendar/rollover/log": core._GatewayError("boom", 500)})
    with pytest.raises(core._GatewayError):
        await core.my_tasks_rollover_log()


def test_both_reads_are_read_only() -> None:
    for fn in (core.my_tasks_get_day_state, core.my_tasks_rollover_log):
        assert fn.__tool_risk__["read_only"] is True
