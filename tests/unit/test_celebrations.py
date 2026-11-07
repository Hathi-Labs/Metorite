"""Celebrations (WS-63 PZ-43, D-PZ-41, ``personalization.md`` §16.6 and §16.7).

Fence of the PZ-43 row: the birthday holds no year. The chat greets once on
the day and not a second time that day. A member with no pick gets no
festival greeting. No code reads the name, the region, the language or mail
to pick a festival. Forget and export cover the new fields.

The festival list ships EMPTY (§16.9 Q3), so the tests that need a festival
put one test row into ``celebrations.FESTIVALS``.

Mutations, each red here: keep a year in the birthday; greet when an assistant
reply came since local midnight; read the member's name to pick a festival;
let a member with no pick get a festival; drop the flag gate of the prime step.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import UTC, date, datetime
from typing import Any

import pytest
from acb_common import assistant_profile as ap
from acb_common import celebrations as c
from acb_common import get_settings
from acb_common.settings import install_settings
from acb_skills.write_artifact import artifact_context, bind_artifact_context
from orchestrator import executor as ex

ORG = "22222222-2222-4222-8222-222222222222"
OTHER_ORG = "33333333-3333-4333-8333-333333333333"
MEMBER = "ana@alpha.example"
SESSION = "chat-pz43"
RUN = "run-pz43-1"
ZONE = "Asia/Kolkata"

DIWALI = c.Festival(id="test-festival", name="Test Festival",
                    dates=(date(2026, 11, 8), date(2027, 10, 29)))


def _at(day: date, hour: int = 10) -> datetime:
    """*hour* o'clock in Asia/Kolkata on *day*, as UTC."""
    from zoneinfo import ZoneInfo

    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=ZoneInfo(ZONE)).astimezone(UTC)


@pytest.fixture()
def festival(monkeypatch: pytest.MonkeyPatch) -> c.Festival:
    monkeypatch.setattr(c, "FESTIVALS", (DIWALI,))
    return DIWALI


# ── The shape of the key ───────────────────────────────────────────────────


def test_the_list_ships_empty_and_the_catalogue_is_empty() -> None:
    assert c.FESTIVALS == ()
    assert c.catalogue() == []


def test_the_birthday_holds_a_month_and_a_day_and_no_year() -> None:
    assert c.clean_celebrations({"birthday": {"month": 3, "day": 12}})["birthday"] == {
        "month": 3, "day": 12}
    for bad in ({"month": 3, "day": 12, "year": 1990}, {"year": 1990, "month": 3, "day": 12},
                {"date": "1990-03-12"}, "1990-03-12", {"month": 3}, {"month": "3", "day": 12},
                {"month": True, "day": 1}):
        with pytest.raises(ValueError):
            c.clean_celebrations({"birthday": bad})


def test_an_impossible_date_is_refused_and_29_february_is_valid() -> None:
    for month, day in ((2, 30), (4, 31), (13, 1), (0, 5), (1, 0)):
        with pytest.raises(ValueError):
            c.clean_celebrations({"birthday": {"month": month, "day": day}})
    assert c.clean_celebrations({"birthday": {"month": 2, "day": 29}})["birthday"] == {
        "month": 2, "day": 29}


def test_the_stored_profile_keeps_no_year_even_when_a_year_was_stored() -> None:
    stored = ap.stored_profile({"v": 1, "celebrations": {
        "birthday": {"month": 3, "day": 12, "year": 1990}}})
    assert "celebrations" not in stored or "birthday" not in stored["celebrations"]
    ok = ap.stored_profile({"v": 1, "celebrations": {"birthday": {"month": 3, "day": 12}}})
    assert ok["celebrations"]["birthday"] == {"month": 3, "day": 12}
    assert "1990" not in json.dumps(ok)


def test_a_member_who_set_nothing_keeps_the_old_shape() -> None:
    assert "celebrations" not in ap.stored_profile({"v": 1, "address": {"name": "Ana"}})
    assert "celebrations" not in ap.stored_profile(None)


def test_unknown_festival_ids_are_dropped(festival: c.Festival) -> None:
    out = c.clean_celebrations({"festivals": ["test-festival", "nope", "test-festival", 5]})
    assert out["festivals"] == ["test-festival"]
    got = ap.stored_profile({"celebrations": {"festivals": ["nope", "test-festival"]}})
    assert got["celebrations"]["festivals"] == ["test-festival"]


def test_with_the_empty_list_every_pick_drops() -> None:
    assert c.clean_celebrations({"festivals": ["anything"]})["festivals"] == []


def test_the_anniversary_switch_defaults_to_off_and_takes_a_bool() -> None:
    assert c.clean_celebrations({})["work_anniversary"] is False
    assert c.stored_celebrations(None)["work_anniversary"] is False
    with pytest.raises(ValueError):
        c.clean_celebrations({"work_anniversary": "yes"})
    with pytest.raises(ValueError):
        c.clean_celebrations({"religion": "x"})


# ── What is celebrated ─────────────────────────────────────────────────────


def test_the_birthday_is_celebrated_on_the_day_and_not_the_day_after() -> None:
    stored = {"birthday": {"month": 3, "day": 12}}
    assert c.celebrated_today(stored, date(2026, 3, 12)).birthday is True
    assert not c.celebrated_today(stored, date(2026, 3, 13))
    assert not c.celebrated_today(stored, date(2026, 3, 11))


def test_29_february_is_greeted_on_28_february_in_a_year_with_no_29() -> None:
    stored = {"birthday": {"month": 2, "day": 29}}
    assert c.celebrated_today(stored, date(2027, 2, 28)).birthday is True
    assert not c.celebrated_today(stored, date(2027, 3, 1))
    # A leap year: the day itself, and not the 28th.
    assert c.celebrated_today(stored, date(2028, 2, 29)).birthday is True
    assert not c.celebrated_today(stored, date(2028, 2, 28))


def test_a_member_with_no_pick_gets_no_festival_greeting(festival: c.Festival) -> None:
    assert not c.celebrated_today({"festivals": []}, date(2026, 11, 8))
    assert not c.celebrated_today({}, date(2026, 11, 8))
    assert c.celebrated_today({"festivals": []}, date(2026, 11, 8)).festivals == ()


def test_a_picked_festival_is_celebrated_on_its_date_for_each_year(festival: c.Festival) -> None:
    stored = {"festivals": ["test-festival"]}
    assert c.celebrated_today(stored, date(2026, 11, 8)).festivals == ("Test Festival",)
    assert c.celebrated_today(stored, date(2027, 10, 29)).festivals == ("Test Festival",)
    assert not c.celebrated_today(stored, date(2027, 11, 8))


def test_the_work_anniversary_needs_the_switch_and_a_join_date() -> None:
    join = date(2023, 5, 17)
    on = {"work_anniversary": True}
    assert c.celebrated_today(on, date(2026, 5, 17), join).anniversary_years == 3
    assert not c.celebrated_today(on, date(2026, 5, 17), None)
    assert not c.celebrated_today({"work_anniversary": False}, date(2026, 5, 17), join)
    assert not c.celebrated_today({}, date(2026, 5, 17), join)
    # The day of joining is not an anniversary.
    assert not c.celebrated_today(on, date(2023, 5, 17), join)
    assert not c.celebrated_today(on, date(2026, 5, 18), join)


# ── Once a day ─────────────────────────────────────────────────────────────

BDAY = {"birthday": {"month": 3, "day": 12}}


def test_the_chat_greets_once_on_the_day_and_not_a_second_time() -> None:
    day = date(2026, 3, 12)
    morning = _at(day, 9)
    first = c.celebration_line(BDAY, now=morning, zone=ZONE, join_date=None, last_reply_at=None)
    assert "birthday" in first
    # Later that day: the reply of the morning exists, so no line.
    afternoon = _at(day, 15)
    assert c.celebration_line(BDAY, now=afternoon, zone=ZONE, join_date=None,
                              last_reply_at=morning) == ""
    # A reply of the day before does not stop the greeting.
    assert c.celebration_line(BDAY, now=afternoon, zone=ZONE, join_date=None,
                              last_reply_at=_at(date(2026, 3, 11), 22)) != ""


def test_the_day_after_gets_no_greeting() -> None:
    day_after = _at(date(2026, 3, 13), 9)
    assert c.celebration_line(BDAY, now=day_after, zone=ZONE, join_date=None,
                              last_reply_at=None) == ""
    assert c.celebration_line(BDAY, now=day_after, zone=ZONE, join_date=None,
                              last_reply_at=_at(date(2026, 3, 12), 9)) == ""


def test_local_midnight_decides_the_day_not_utc() -> None:
    # 23:30 UTC on 11 March is 05:00 on 12 March in Kolkata.
    now = datetime(2026, 3, 11, 23, 30, tzinfo=UTC)
    assert "birthday" in c.celebration_line(BDAY, now=now, zone=ZONE, join_date=None,
                                            last_reply_at=None)
    # A reply at 22:00 UTC on 11 March (03:30 on 12 March, local) counts as today.
    assert c.celebration_line(BDAY, now=now, zone=ZONE, join_date=None,
                              last_reply_at=datetime(2026, 3, 11, 22, 0, tzinfo=UTC)) == ""
    # A reply at 18:00 UTC on 11 March (23:30 on 11 March, local) is yesterday.
    assert c.celebration_line(BDAY, now=now, zone=ZONE, join_date=None,
                              last_reply_at=datetime(2026, 3, 11, 18, 0, tzinfo=UTC)) != ""


def test_a_member_with_nothing_set_gets_no_line_any_day() -> None:
    for stored in (None, {}, {"festivals": []}, {"work_anniversary": False}, "junk"):
        assert c.celebration_line(stored, now=_at(date(2026, 3, 12)), zone=ZONE, join_date=None,
                                  last_reply_at=None) == ""


def test_the_line_follows_the_house_voice() -> None:
    text = c.line(c.Today(birthday=True, festivals=("Test Festival",), anniversary_years=1))
    assert chr(0x2014) not in text and chr(0x2013) not in text
    assert "1 year at" in text
    assert "Test Festival" in text
    assert c.line(c.Today()) == ""


# ── No guess of a religion or a community (DPDP) ───────────────────────────


def test_the_pure_function_takes_no_name_region_language_or_mail() -> None:
    for fn in (c.celebrated_today, c.celebration_line):
        names = set(inspect.signature(fn).parameters)
        assert not names & {"name", "region", "language", "lang", "email", "mail", "locale",
                            "country", "text", "message", "messages", "display_name"}
    assert set(inspect.signature(c.celebrated_today).parameters) == {
        "celebrations", "today", "join_date"}
    assert set(inspect.signature(c.celebration_line).parameters) == {
        "celebrations", "now", "zone", "join_date", "last_reply_at"}


def test_the_module_imports_nothing_that_reads_a_name_or_mail() -> None:
    src = inspect.getsource(c)
    for word in ("people", "preferred_name", "display_name", "email_ingestion", "mailbox",
                 "religion_of", "infer", "guess("):
        # "people" appears once, in READ_SQL, for the join date and nothing else.
        if word == "people":
            assert src.count("FROM people") == 1
            assert "p.start_date" in src
            continue
        assert word not in src.replace("Never guess", "").replace("guess a religion", ""), word
    assert "p.preferred_name" not in c.READ_SQL
    assert "display_name" not in c.READ_SQL


# ── The flag ───────────────────────────────────────────────────────────────


def _flag(orgs: str, *, profile: bool = True) -> None:
    install_settings(get_settings().model_copy(update={
        "celebrations_orgs": orgs, "assistant_profile_enabled": profile,
        "assistant_profile_orgs": ""}))


@pytest.fixture()
def settings():
    original = get_settings()
    try:
        yield
    finally:
        install_settings(original)


def test_the_flag_is_off_by_default_and_reads_an_org_list(settings) -> None:
    _flag("")
    assert c.celebrations_for(ORG) is False
    _flag(ORG)
    assert c.celebrations_for(ORG) is True
    assert c.celebrations_for(OTHER_ORG) is False
    assert c.celebrations_for(None) is False
    _flag("*")
    assert c.celebrations_for(OTHER_ORG) is True
    _flag("*", profile=False)
    assert c.celebrations_for(ORG) is False


# ── The executor: prime, line, flag ────────────────────────────────────────

NOW = _at(date(2026, 3, 12), 9)


@pytest.fixture()
def run(monkeypatch: pytest.MonkeyPatch, settings):
    state: dict[str, Any] = {
        "org": ORG, "member": MEMBER, "reads": [],
        "row": ({"birthday": {"month": 3, "day": 12}}, None, None),
    }

    async def read(org: str, member: str) -> tuple[Any, Any, Any]:
        state["reads"].append((org, member))
        return state["row"]

    monkeypatch.setattr(ex, "_current_run_org", lambda: state["org"])
    monkeypatch.setattr(ex, "_verified_run_member", lambda: state["member"])
    monkeypatch.setattr(ex, "_celebration_read", read)
    monkeypatch.setattr(ex, "_briefing_now", lambda: NOW)
    token = ex._RUN_SOLO_ROOM.set(True)
    _flag(ORG)
    try:
        yield state
    finally:
        ex._RUN_SOLO_ROOM.reset(token)


def _turn(state: dict[str, Any], **kw: Any) -> str:
    async def go() -> str:
        bind_artifact_context(session_id=SESSION, run_id=RUN, member=MEMBER,
                              batch_thread=kw.get("batch_thread", False))
        await ex._prime_celebrations(
            kw.get("agent", "orchestrator"), organization_id=kw.get("org", state["org"]),
            thread_id=SESSION, delegated=kw.get("delegated", False),
            shows_cards=kw.get("shows_cards", True),
            batch_thread=kw.get("batch_thread", False))
        return ex._celebration_line("orchestrator", ZONE)

    return asyncio.run(go())


def test_a_run_on_the_day_gets_the_line(run) -> None:
    assert "birthday" in _turn(run)
    assert run["reads"] == [(ORG, MEMBER)]


def test_the_second_reply_of_the_day_gets_no_line(run) -> None:
    run["row"] = (run["row"][0], _at(date(2026, 3, 12), 7), None)
    assert _turn(run) == ""


def test_the_flag_off_gives_no_line_and_no_read(run) -> None:
    _flag("")
    assert _turn(run) == ""
    assert run["reads"] == []


def test_the_flag_turned_off_after_the_prime_drops_the_line(run) -> None:
    async def go() -> tuple[str, str]:
        bind_artifact_context(session_id=SESSION, run_id=RUN, member=MEMBER)
        await ex._prime_celebrations("orchestrator", organization_id=ORG, thread_id=SESSION,
                                     delegated=False, shows_cards=True, batch_thread=False)
        before = ex._celebration_line("orchestrator", ZONE)
        _flag("")
        return before, ex._celebration_line("orchestrator", ZONE)

    before, after = asyncio.run(go())
    assert "birthday" in before and after == ""


@pytest.mark.parametrize("kw", [
    {"delegated": True}, {"shows_cards": False}, {"batch_thread": True},
    {"agent": "crm_agent"}, {"org": OTHER_ORG},
])
def test_a_run_that_fails_a_gate_gets_no_line_and_no_read(run, kw) -> None:
    assert _turn(run, **kw) == ""
    assert run["reads"] == []


def test_a_shared_room_gets_no_line(run) -> None:
    token = ex._RUN_SOLO_ROOM.set(False)
    try:
        assert _turn(run) == ""
    finally:
        ex._RUN_SOLO_ROOM.reset(token)
    assert run["reads"] == []


def test_a_read_fault_gives_no_line_and_never_raises(run, monkeypatch) -> None:
    async def broken(org: str, member: str) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(ex, "_celebration_read", broken)
    assert _turn(run) == ""


def test_a_member_with_no_pick_gets_no_line_from_the_run(run) -> None:
    run["row"] = ({"festivals": [], "work_anniversary": False}, None, None)
    assert _turn(run) == ""
    run["row"] = (None, None, None)
    assert _turn(run) == ""


def test_the_child_of_a_run_gets_none(run) -> None:
    from acb_skills.write_artifact import derive_artifact_context

    async def go() -> tuple[str, str]:
        bind_artifact_context(session_id=SESSION, run_id=RUN, member=MEMBER)
        await ex._prime_celebrations("orchestrator", organization_id=ORG, thread_id=SESSION,
                                     delegated=False, shows_cards=True, batch_thread=False)
        parent = ex._celebration_line("orchestrator", ZONE)
        derive_artifact_context(run_id="child-run")
        return parent, ex._celebration_line("orchestrator", ZONE)

    parent, child = asyncio.run(go())
    assert "birthday" in parent and child == ""
    assert artifact_context() is not None


def test_the_read_sql_takes_the_member_from_the_run_and_names_the_org() -> None:
    sql = c.READ_SQL
    assert sql.count("organization_id = CAST(:org AS uuid)") == 3
    assert ":m" in sql and "celebrations" in sql
    # Only the celebrations key of the profile, never the rest of it.
    assert "assistant_profile -> 'celebrations'" in sql
    assert "assistant_profile FROM" not in sql and "assistant_profile," not in sql


# ── Forget and export ──────────────────────────────────────────────────────


def test_forget_clears_the_celebrations_with_the_whole_column() -> None:
    from gateway.routes.admin import assistant_profile as route

    sql = route._FORGET_SQL
    # Forget keeps ``apps`` only. The celebrations key is not copied.
    assert "'apps'" in sql
    assert "celebrations" not in sql


def test_the_export_holds_the_celebrations(festival: c.Festival) -> None:
    raw = {"v": 1, "celebrations": {"birthday": {"month": 3, "day": 12},
                                    "festivals": ["test-festival"], "work_anniversary": True}}
    text = ap.render_export(raw)
    assert "Your celebrations" in text
    assert "day 12, month 3 (no year)" in text
    assert "test-festival" in text
    assert "Work anniversary" in text
    assert ap.stored_profile(raw)["celebrations"]["work_anniversary"] is True
    # A member with only a birthday still has an export.
    assert ap.render_export({"celebrations": {"birthday": {"month": 3, "day": 12}}}) != (
        ap.EXPORT_EMPTY)


def test_the_block_of_the_turn_holds_no_celebration() -> None:
    raw = {"v": 1, "address": {"name": "Ana"}, "celebrations": {
        "birthday": {"month": 3, "day": 12}, "work_anniversary": True}}
    block = ap.render_block(raw, "Ana")
    assert "birthday" not in block.lower() and "12" not in block


def test_the_profile_route_validates_and_gates_the_key(settings) -> None:
    from gateway.routes.admin import assistant_profile as route

    put = route.ProfilePut(key="celebrations", value={"birthday": {"month": 3, "day": 12}})
    assert put.value["birthday"] == {"month": 3, "day": 12}
    with pytest.raises(ValueError):
        route.ProfilePut(key="celebrations", value={"birthday": {"month": 3, "day": 12,
                                                                  "year": 1990}})
    assert "celebrations" not in route.SETTINGS_ONLY_KEYS  # the chat may write it too


# ── Repair round: no query when nothing is set, and a clear with the flag off ──


def _profile_read(monkeypatch, raw):
    ex._PROFILE_CACHE.clear()
    ex._CELEBRATIONS_SET.clear()
    monkeypatch.setattr(ex, "_read_member_profile", lambda org, m: (raw, "", "", ""))
    monkeypatch.setattr(ex, "_read_member_display_name", lambda org, m: "Ana")


def test_a_member_with_nothing_set_costs_no_query(run, monkeypatch) -> None:
    for raw in ({"v": 1, "address": {"name": "Ana"}},
                {"v": 1, "celebrations": {"festivals": [], "work_anniversary": False}}):
        _profile_read(monkeypatch, raw)
        run["reads"].clear()
        assert _turn(run) == ""
        assert run["reads"] == []


def test_a_member_with_a_birthday_still_reads_and_greets(run, monkeypatch) -> None:
    _profile_read(monkeypatch, {"v": 1, "address": {"name": "Ana"},
                                "celebrations": {"birthday": {"month": 3, "day": 12}}})
    assert "birthday" in _turn(run)
    assert run["reads"] == [(ORG, MEMBER)]


def test_is_clear_and_a_null_write_clear() -> None:
    assert c.clean_celebrations(None) == c.empty()
    assert c.is_clear(None) and c.is_clear({}) and c.is_clear(c.empty())
    assert not c.is_clear({"birthday": {"month": 3, "day": 12}})
    assert not c.is_clear({"work_anniversary": True})


def test_the_gateway_with_the_flag_off_accepts_a_clear_and_refuses_a_set(settings) -> None:
    from gateway.routes.admin import assistant_profile as route

    _flag("")
    seen: list[Any] = []

    async def fake_write(uid, op, fn):
        seen.append(op)
        return {}, None

    class U:
        email = MEMBER
        organization_id = ORG

    async def go(value):
        return await route.put_assistant_profile(
            body={"key": "celebrations", "value": value}, user=U(), x_profile_via=None)

    import unittest.mock as m

    with m.patch.object(route, "_write", fake_write), m.patch.object(route, "_member",
                                                                    lambda u: MEMBER):
        asyncio.run(go(None))
        asyncio.run(go({"festivals": [], "work_anniversary": False}))
        assert seen == ["write", "write"]
        with pytest.raises(route.HTTPException) as err:
            asyncio.run(go({"birthday": {"month": 3, "day": 12}}))
        assert err.value.status_code == 422
