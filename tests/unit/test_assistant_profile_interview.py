"""The interview of the assistant profile (WS-63 PZ-26, personalization.md §14.3 and §14.4).

The pure half: the parser, the open areas, the due line and its limits, the
undo of an area, and the block in the executor. The clock is always an
argument or a patched seam, so no test sleeps. The R8 half is
``tests/unit/test_assistant_profile_interview_r8.py``.

R7 fences named here:

* ``pz26-parser``: a valid area round-trips, an unknown area, state or time
  drops, and a profile with no ``interview`` renders the same bytes.
* ``pz26-limits``: the 24 hours, the 14 days, ``never`` and a decline each
  have a boundary pair that fails when the number moves.
* ``pz26-room``: no due line in a shared room, in a run with writes closed,
  on WhatsApp with writes off, or with ``learning`` off.
* ``pz26-cache``: the line is computed on each turn, inside the 60 second
  TTL, and a mark ends the cache early.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from acb_common import assistant_profile as ap
from gateway.routes.admin import assistant_profile as routes
from orchestrator import executor as ex
from pydantic import ValidationError

from tests.unit import test_executor_profile_block as epb
from tests.unit.test_executor_profile_block import run  # noqa: F401

NOW = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)


def _t(delta: timedelta) -> str:
    return (NOW - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iv(last: timedelta | None = timedelta(hours=30), started: bool = True,
        **areas: tuple[str, timedelta]) -> dict[str, Any]:
    """An interview. Each area is ``state, age``."""
    out: dict[str, Any] = {}
    if started:
        out["started_at"] = _t(timedelta(days=40))
    if last is not None:
        out["last_asked_at"] = _t(last)
    if areas:
        out["areas"] = {a: {"state": st, "at": _t(age)} for a, (st, age) in areas.items()}
    return out


# ── pz26-parser ────────────────────────────────────────────────────────────


def test_the_table_is_the_ten_areas_of_the_spec_in_order() -> None:
    assert list(ap.AREAS) == ["role", "goals", "good_day", "decisions", "detail",
                              "rhythm", "channels", "energy", "peeves", "people"]
    assert all(q.endswith("?") for q in ap.AREAS.values())
    assert ap.INTERVIEW_STATES == ("asked", "answered", "skipped", "never")
    assert (ap.DUE_HOURS, ap.WAIT_DAYS) == (24, 14)


def test_a_valid_interview_round_trips() -> None:
    raw = {"v": 1, "interview": _iv(goals=("answered", timedelta(days=1)))}
    assert ap.stored_profile(raw)["interview"] == raw["interview"]
    assert ap.stored_profile(copy.deepcopy(raw)) == ap.stored_profile(raw)


def test_a_fact_keeps_a_known_area_and_drops_an_unknown_one() -> None:
    raw = {"facts": [{"id": "f1", "text": "a", "kind": "work", "area": "goals"},
                     {"id": "f2", "text": "b", "kind": "work", "area": "hobbies"},
                     {"id": "f3", "text": "c", "kind": "work"}]}
    facts = ap.stored_profile(raw)["facts"]
    assert [f.get("area") for f in facts] == ["goals", None, None]
    assert len(facts) == 3


@pytest.mark.parametrize("bad", [
    {"areas": {"hobbies": {"state": "asked", "at": "2026-10-25T10:00:00Z"}}},
    {"areas": {"goals": {"state": "maybe", "at": "2026-10-25T10:00:00Z"}}},
    {"areas": {"goals": {"state": "asked", "at": "yesterday"}}},
    {"started_at": "soon", "last_asked_at": 5},
    {"areas": ["goals"]},
    {},
    "text",
    None,
])
def test_an_unknown_area_state_or_time_drops(bad: Any) -> None:
    assert "interview" not in ap.stored_profile({"interview": bad})


def test_one_bad_entry_drops_alone() -> None:
    raw = {"interview": {
        "started_at": "2026-10-25T10:00:00Z", "last_asked_at": "not a time",
        "areas": {"goals": {"state": "answered", "at": "2026-10-25T10:00:00Z"},
                  "hobbies": {"state": "answered", "at": "2026-10-25T10:00:00Z"},
                  "role": {"state": "weird", "at": "2026-10-25T10:00:00Z"}}}}
    assert ap.stored_profile(raw)["interview"] == {
        "started_at": "2026-10-25T10:00:00Z",
        "areas": {"goals": {"state": "answered", "at": "2026-10-25T10:00:00Z"}}}


def test_the_block_is_byte_equal_with_and_without_an_interview() -> None:
    plain = copy.deepcopy(epb.EXAMPLE)
    withiv = {**copy.deepcopy(epb.EXAMPLE), "interview": _iv(goals=("asked", timedelta(days=1)))}
    assert ap.render_block(plain) == ap.render_block(withiv) == epb.GOLDEN
    assert ap.render_export(plain) == ap.render_export(withiv)


def test_the_empty_profile_is_unchanged() -> None:
    assert "interview" not in ap.empty_profile()


# ── pz26-limits: the pure functions ────────────────────────────────────────


def test_a_mark_sets_the_times_and_never_mutates_its_input() -> None:
    before = _iv(last=None, started=False)
    first = ap.apply_interview_mark(None, "goals", "asked", NOW)
    assert first == {"started_at": _t(timedelta(0)), "last_asked_at": _t(timedelta(0)),
                     "areas": {"goals": {"state": "asked", "at": _t(timedelta(0))}}}
    later = NOW + timedelta(hours=1)
    second = ap.apply_interview_mark(first, "goals", "answered", later)
    assert second["started_at"] == first["started_at"]
    assert second["last_asked_at"] == first["last_asked_at"]  # only `asked` moves it
    assert second["areas"]["goals"]["state"] == "answered"
    assert ap.apply_interview_mark(before, "role", "skipped", NOW) is not before
    assert "areas" not in before


def test_a_mark_refuses_an_unknown_area_or_state() -> None:
    with pytest.raises(ValueError):
        ap.apply_interview_mark(None, "hobbies", "asked", NOW)
    with pytest.raises(ValueError):
        ap.apply_interview_mark(None, "goals", "maybe", NOW)


def test_start_sets_started_at_once_and_no_last_asked() -> None:
    first = ap.start_interview(None, NOW)
    assert set(first) == {"started_at"}
    again = ap.start_interview(first, NOW + timedelta(days=3))
    assert again["started_at"] == first["started_at"]


def test_open_areas_follow_the_table_and_the_limit() -> None:
    assert ap.open_areas(None, NOW) == list(ap.AREAS)
    assert ap.open_areas(None, NOW, 5) == list(ap.AREAS)[:5]
    assert ap.open_areas(None, NOW, 1) == ["role"]
    assert ap.open_areas(_iv(role=("answered", timedelta(days=1))), NOW, 2) == ["goals", "good_day"]


@pytest.mark.parametrize("state,days,open_", [
    ("skipped", 13, False), ("skipped", 15, True),
    ("asked", 13, False), ("asked", 15, True),
    ("answered", 400, False), ("never", 365, False), ("never", 3650, False),
])
def test_the_wait_of_an_area_is_fourteen_days(state: str, days: int, open_: bool) -> None:
    iv = _iv(role=(state, timedelta(days=days)))
    assert ("role" in ap.open_areas(iv, NOW)) is open_


def test_the_due_line_waits_more_than_twenty_four_hours() -> None:
    quiet = ap.interview_due_line(_iv(last=timedelta(hours=23, minutes=59)), NOW)
    due = ap.interview_due_line(_iv(last=timedelta(hours=24, minutes=1)), NOW)
    exact = ap.interview_due_line(_iv(last=timedelta(hours=24)), NOW)
    assert quiet == "" and exact == ""
    assert due.startswith("Interview (one question is due): role: «")
    assert ap.AREAS["role"] in due
    assert 'profile_interview("asked", "role")' in due


def test_the_line_names_the_first_open_area() -> None:
    iv = _iv(role=("answered", timedelta(days=2)), goals=("skipped", timedelta(days=2)))
    assert "good_day: «" in ap.interview_due_line(iv, NOW)


def test_a_decline_sets_started_at_only_and_no_line_ever_comes() -> None:
    declined = ap.start_interview(None, NOW - timedelta(days=400))
    assert ap.interview_due_line(declined, NOW) == ""
    assert ap.interview_due_line(_iv(last=timedelta(days=400), started=False), NOW) == ""
    assert ap.interview_due_line(None, NOW) == ""
    assert ap.interview_due_line("junk", NOW) == ""


def test_never_is_closed_after_a_year_and_no_open_area_gives_no_line() -> None:
    every = {a: ("never", timedelta(days=365)) for a in ap.AREAS}
    assert ap.open_areas(_iv(**every), NOW) == []
    assert ap.interview_due_line(_iv(**every), NOW) == ""


def test_the_line_is_short_and_one_line() -> None:
    line = ap.interview_due_line(_iv(), NOW)
    assert "\n" not in line and len(line) < 400
    for sentence in line.replace("?» ", "?». ").split(". "):
        assert len(sentence.split()) <= 25


def test_the_write_tool_names_hold_the_interview() -> None:
    assert "profile_interview" in ap.WRITE_TOOL_NAMES


# ── pz26-undo ──────────────────────────────────────────────────────────────


def _post_record(fact: dict[str, Any], at: float) -> dict[str, Any]:
    return {"op": "post", "list": "facts", "id": fact["id"], "after": fact, "at": at}


def test_undo_of_a_fact_with_an_area_opens_the_area_again() -> None:
    fact = {"id": "f1", "text": "Ship Q4", "kind": "work", "source": "chat",
            "at": "2026-10-25T10:00:00Z", "area": "goals"}
    raw = {"v": 1, "facts": [fact],
           "interview": _iv(goals=("answered", timedelta(0)), role=("skipped", timedelta(0))),
           "last_change": _post_record(fact, 1000.0)}
    out, why = routes.undo_last_change(raw, 1010.0)
    assert why == "" and out is not None and out["facts"] == []
    assert "goals" not in out["interview"]["areas"]
    assert out["interview"]["areas"]["role"]["state"] == "skipped"
    assert "last_change" not in out
    assert "goals" in ap.open_areas(out["interview"], NOW)


def test_undo_keeps_an_area_that_is_not_answered() -> None:
    fact = {"id": "f1", "text": "Ship Q4", "kind": "work", "area": "goals"}
    raw = {"facts": [fact], "interview": _iv(goals=("never", timedelta(0))),
           "last_change": _post_record(fact, 1000.0)}
    out, _ = routes.undo_last_change(raw, 1010.0)
    assert out is not None and out["interview"]["areas"]["goals"]["state"] == "never"


def test_undo_of_a_fact_without_an_area_leaves_the_interview() -> None:
    fact = {"id": "f1", "text": "Ship Q4", "kind": "work"}
    iv = _iv(goals=("answered", timedelta(0)))
    raw = {"facts": [fact], "interview": iv, "last_change": _post_record(fact, 1000.0)}
    out, _ = routes.undo_last_change(raw, 1010.0)
    assert out is not None and out["interview"] == iv


def test_the_budget_ignores_the_interview() -> None:
    """A full profile of 40 facts at the budget: a mark must still pass."""
    size = 140
    while True:
        full = {"v": 1, "facts": [{"id": f"f{n:03d}", "text": "x" * size, "kind": "work"}
                                  for n in range(40)]}
        if ap.profile_size(full) <= ap.PROFILE_MAX:
            break
        size -= 1
    marked = {**full, "interview": _iv(**{a: ("asked", timedelta(days=1)) for a in ap.AREAS})}
    assert ap.profile_size(full) > ap.PROFILE_MAX - 150
    assert ap.profile_size(marked) > ap.PROFILE_MAX  # it would fail if it counted
    routes._check_caps(marked)  # must not raise
    over = {**full, "facts": [*full["facts"][:-1], {"id": "z", "text": "y" * 300, "kind": "work"}]}
    with pytest.raises(Exception, match="exceeds"):
        routes._check_caps(over)  # the budget still binds the member's words


def test_the_route_model_takes_only_a_known_area() -> None:
    assert routes.FactIn(text="a", kind="work", area="goals").area == "goals"
    assert routes.FactIn(text="a", kind="work").area is None
    with pytest.raises(ValidationError):
        routes.FactIn(text="a", kind="work", area="hobbies")
    with pytest.raises(ValidationError):
        routes.InterviewMark(state="maybe")
    assert routes.FactPatch.model_fields.keys() == {"text", "kind"}


# ── The block in the executor ──────────────────────────────────────────────


def _raw(**iv: Any) -> dict[str, Any]:
    return {**copy.deepcopy(epb.EXAMPLE), "interview": iv or _iv()}


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch):
    state = {"now": NOW}
    monkeypatch.setattr(ex, "_interview_now", lambda: state["now"])
    ex._INTERVIEW_SHOWN.clear()
    yield state
    ex._INTERVIEW_SHOWN.clear()


@pytest.fixture()
def writes():
    token = ap.bind_writes(True)
    try:
        yield
    finally:
        ap.release_writes(token)


def _entry() -> str:
    return ex._member_profile_entry("orchestrator")[0]


def test_a_due_line_follows_the_block(run, clock, writes) -> None:  # noqa: F811
    run["raw"] = _raw()
    block = _entry()
    assert block.startswith(epb.GOLDEN)
    lines = block.splitlines()
    assert lines[-2] == ap.BLOCK_FOOTER
    assert lines[-1].startswith("Interview (one question is due): role: «")


def test_no_interview_key_leaves_the_block_unchanged(run, clock, writes) -> None:  # noqa: F811
    assert _entry() == epb.GOLDEN


def test_a_shared_room_gets_no_block_and_no_line(run, clock, writes) -> None:  # noqa: F811
    run["raw"] = _raw()
    token = ex._RUN_SOLO_ROOM.set(False)
    try:
        assert ex._member_profile_entry("orchestrator") == ("", "", "")
    finally:
        ex._RUN_SOLO_ROOM.reset(token)


def test_a_run_with_writes_closed_gets_no_line(run, clock) -> None:  # noqa: F811
    run["raw"] = _raw()
    assert _entry() == epb.GOLDEN  # no holder bound: closed
    token = ap.bind_writes(False)
    try:
        assert _entry() == epb.GOLDEN
    finally:
        ap.release_writes(token)


def test_a_run_that_read_other_text_loses_the_line(run, clock, writes) -> None:  # noqa: F811
    run["raw"] = _raw()
    assert "Interview" in _entry()
    ap.close_writes()
    assert _entry() == epb.GOLDEN


def test_whatsapp_with_writes_off_gets_no_line(run, clock, writes) -> None:  # noqa: F811
    run["raw"] = _raw()
    with ap.profile_channel("whatsapp", writes=False):
        assert _entry() == epb.GOLDEN
    with ap.profile_channel("whatsapp", writes=True):
        assert "Interview (one question" in _entry()


def test_learning_off_gets_no_line(run, clock, writes) -> None:  # noqa: F811
    run["raw"] = {**_raw(), "learning": False}
    assert "Interview" not in _entry()
    assert _entry().startswith(ap.BLOCK_HEADER)


def test_a_decline_gets_no_line(run, clock, writes) -> None:  # noqa: F811
    run["raw"] = {**copy.deepcopy(epb.EXAMPLE),
                  "interview": ap.start_interview(None, NOW - timedelta(days=90))}
    assert _entry() == epb.GOLDEN


def test_twenty_three_hours_gets_no_line_and_twenty_five_does(run, clock, writes) -> None:  # noqa: F811
    run["raw"] = _raw(**_iv(last=timedelta(hours=23, minutes=59)))
    assert _entry() == epb.GOLDEN
    run["raw"] = _raw(**_iv(last=timedelta(hours=24, minutes=1)))
    ex._PROFILE_CACHE.clear()
    assert "Interview" in _entry()


def test_a_line_that_would_overflow_the_block_is_left_out(run, clock, writes,  # noqa: F811
                                                          monkeypatch) -> None:
    run["raw"] = _raw()
    plain = _entry().rsplit("\n", 1)[0]
    monkeypatch.setattr(ap, "RENDER_MAX", len(plain) + 5)
    ex._PROFILE_CACHE.clear()
    assert _entry() == plain


# ── pz26-cache ─────────────────────────────────────────────────────────────


def test_the_line_is_computed_each_turn_inside_the_ttl(run, clock, writes) -> None:  # noqa: F811
    run["raw"] = _raw(**_iv(last=timedelta(hours=23, minutes=59, seconds=30)))
    assert "Interview" not in _entry()
    assert len(run["reads"]) == 1
    clock["now"] = NOW + timedelta(seconds=45)  # still inside the 60 s TTL
    assert "Interview (one question" in _entry()
    assert len(run["reads"]) == 1, "the cache must have served the second turn"


def test_a_mark_ends_the_cache_through_mark_changed(run, clock, writes) -> None:  # noqa: F811
    run["raw"] = _raw()
    assert "Interview" in _entry()
    run["raw"] = _raw(**ap.apply_interview_mark(
        _iv(), "role", "asked", NOW))
    ex._INTERVIEW_SHOWN.clear()
    assert "Interview" in _entry()  # stale: the cache still holds the old read
    ap.mark_changed(epb.MEMBER)
    assert "Interview" not in _entry()
    assert len(run["reads"]) == 2


def test_every_interview_write_goes_through_the_one_write_helper() -> None:
    """``_write`` calls ``mark_changed`` after the commit, so each interview
    route ends the executor cache. Here: no route writes around it."""
    import inspect

    assert "mark_changed(uid)" in inspect.getsource(routes._write)
    assert 'await _write(uid, "interview", _run)' in inspect.getsource(routes._interview_write)
    assert 'await _write(uid, "interview_reset", _run)' in inspect.getsource(
        routes.reset_assistant_interview)


# ── Fix round: once a day in this process, the sitting, stop and start ──────


def test_the_line_shows_once_in_twenty_four_hours_in_this_process(run, clock, writes) -> None:  # noqa: F811
    """The model may ask and never mark `asked`. The line must not repeat."""
    run["raw"] = _raw()
    assert "Interview (one question" in _entry()
    clock["now"] = NOW + timedelta(hours=1)
    assert _entry() == epb.GOLDEN
    clock["now"] = NOW + timedelta(hours=23, minutes=59)
    assert _entry() == epb.GOLDEN
    clock["now"] = NOW + timedelta(hours=24, minutes=1)
    assert "Interview (one question" in _entry()


def test_the_once_a_day_map_is_per_member(run, clock, writes) -> None:  # noqa: F811
    run["raw"] = _raw()
    assert "Interview" in _entry()
    run["member"] = "bo@alpha.example"
    ex._PROFILE_CACHE.clear()
    assert "Interview" in _entry()


def test_the_line_needs_a_solo_room_inside_its_own_function(writes) -> None:
    """Narrow fence: the guard sits in `_with_interview_line` itself."""
    token = ex._RUN_SOLO_ROOM.set(False)
    try:
        assert ex._with_interview_line(epb.GOLDEN, _iv(), ("o", "m")) == epb.GOLDEN
    finally:
        ex._RUN_SOLO_ROOM.reset(token)


def test_a_sitting_lasts_two_hours() -> None:
    iv = ap.start_interview(None, NOW - timedelta(hours=1, minutes=59), sitting=True)
    assert ap.sitting_active(iv, NOW) and ap.may_ask(iv, NOW)
    old = ap.start_interview(None, NOW - timedelta(hours=2, minutes=1), sitting=True)
    assert not ap.sitting_active(old, NOW) and not ap.may_ask(old, NOW)
    assert "sitting_at" not in ap.start_interview(None, NOW)
    assert ap.stored_profile({"interview": iv})["interview"]["sitting_at"] == iv["sitting_at"]


def test_may_ask_for_a_due_question_but_not_after_a_decline() -> None:
    assert ap.may_ask(_iv(), NOW)  # due
    assert not ap.may_ask(ap.start_interview(None, NOW - timedelta(days=9)), NOW)
    assert not ap.may_ask(None, NOW)
    assert ap.due_area(_iv(), NOW) == "role"
