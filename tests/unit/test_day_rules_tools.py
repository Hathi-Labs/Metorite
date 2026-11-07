"""CAL-3 (task 1677): the day rule tools of the ``calendar-day`` pack.

The four tools read and write ``day_templates`` of ``/tasks/settings``. Each
write proposes first, refuses a bad rule in text, and sends the token it read in
``If-Match``. Each case names the mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_day_rules_tools.py -q``.
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
import skill_my_tasks
from acb_skills import chat_parity as cp
from acb_skills import egress as eg
from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp
from skill_my_tasks import core

from tests.unit.test_calendar_day_pack import DAY_RULES, SLUG

LIST, ADD, EDIT, DELETE = (
    "my_tasks_list_day_rules", "my_tasks_add_day_rule",
    "my_tasks_edit_day_rule", "my_tasks_delete_day_rule")

DEEP = {"days": [0, 1, 2, 3, 4], "start_hour": 8, "end_hour": 11, "kind": "focus",
        "label": "Deep work", "theme": ""}
NOMEET = {"days": [4], "start_hour": 15, "end_hour": 17, "kind": "block",
          "label": "", "theme": "admin"}


class Gateway:
    """The gateway, as ``PUT /tasks/settings`` answers: it compares ``If-Match``."""

    def __init__(self, rules: list[dict[str, Any]]) -> None:
        self.rules = [dict(r) for r in rules]
        self.calls: list[tuple[str, str, Any, Any]] = []
        self.change_before_write: list[dict[str, Any]] | None = None

    async def __call__(self, method: str, path: str, **kw: Any) -> Any:
        headers = kw.get("headers") or {}
        self.calls.append((method, path, kw.get("json"), headers))
        if method == "PUT":
            if self.change_before_write is not None:
                self.rules, self.change_before_write = self.change_before_write, None
            sent = headers.get("If-Match", "").strip('"')
            if sent and sent != core.day_rules_token(self.rules):
                raise core._GatewayError(
                    "Tasks PUT /tasks/settings failed (412): changed", 412)
            self.rules = kw["json"]["day_templates"]
        return {"day_templates": [dict(r) for r in self.rules]}

    @property
    def writes(self) -> list[Any]:
        return [j for m, _, j, _ in self.calls if m == "PUT"]


@pytest.fixture
def gw(monkeypatch: pytest.MonkeyPatch) -> Gateway:
    g = Gateway([DEEP, NOMEET])
    monkeypatch.setattr(core, "_request", g)
    return g


async def test_the_list_is_numbered_from_one(gw: Gateway) -> None:
    """Mutation: number from 0, or drop the label quotes -> red."""
    text = await core.my_tasks_list_day_rules()
    assert "1. Mon-Fri 8-11 focus 'Deep work'" in text
    assert "2. Fri 15-17 block (theme admin)" in text
    assert gw.writes == []


async def test_an_empty_list_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "_request", Gateway([]))
    assert await core.my_tasks_list_day_rules() == "No day rules are set."


@pytest.mark.parametrize("days, text", [
    ([0, 1, 2, 3, 4, 5, 6], "every day"), ([5, 6], "Sat-Sun"),
    ([0, 2, 4], "Mon, Wed, Fri"), ([2], "Wed"),
])
def test_days_read_as_a_run_or_a_list(days: list[int], text: str) -> None:
    assert core._days_text(days) == text


async def test_add_proposes_first_and_writes_nothing(gw: Gateway) -> None:
    """The example of the rules text: "keep mornings for deep work". Mutation:
    write when ``confirmed`` is False -> red."""
    text = await core.my_tasks_add_day_rule(
        days=[0, 1, 2, 3, 4], start_hour=8, end_hour=11, kind="focus", label="Deep work")
    assert text.startswith("Not saved yet") and "number 3" in text
    assert "Mon-Fri 8-11 focus 'Deep work'" in text
    assert gw.writes == []


async def test_add_confirmed_writes_the_whole_list_with_the_token_it_read(gw: Gateway) -> None:
    """Mutation: send the one rule, or no ``If-Match`` -> red."""
    token = core.day_rules_token([DEEP, NOMEET])
    text = await core.my_tasks_add_day_rule(
        days=[6, 5], start_hour=9, end_hour=10, label=" Walk ", theme="Health", confirmed=True)
    new = {"days": [5, 6], "start_hour": 9, "end_hour": 10, "kind": "focus",
           "label": "Walk", "theme": "health"}
    assert gw.writes == [{"day_templates": [DEEP, NOMEET, new]}]
    assert gw.calls[-1][3] == {"If-Match": f'"{token}"'}
    assert text.startswith("Saved.") and "rule 3 added" in text


@pytest.mark.parametrize("kw, word", [
    ({"days": [], "start_hour": 8, "end_hour": 11}, "days"),
    ({"days": [7], "start_hour": 8, "end_hour": 11}, "days"),
    ({"days": [-1], "start_hour": 8, "end_hour": 11}, "days"),
    ({"days": [True], "start_hour": 8, "end_hour": 11}, "days"),
    ({"days": [0], "start_hour": 11, "end_hour": 8}, "start_hour < end_hour"),
    ({"days": [0], "start_hour": 8, "end_hour": 8}, "start_hour < end_hour"),
    ({"days": [0], "start_hour": 8, "end_hour": 25}, "start_hour < end_hour"),
    ({"days": [0], "start_hour": -1, "end_hour": 5}, "start_hour < end_hour"),
    ({"days": [0], "start_hour": 8, "end_hour": 11, "kind": "party"}, "block or focus"),
    ({"days": [0], "start_hour": 8, "end_hour": 11, "label": "x" * 41}, "label"),
    ({"days": [0], "start_hour": 8, "end_hour": 11, "theme": "y" * 41}, "theme"),
])
async def test_add_refuses_what_the_server_would_drop(
        gw: Gateway, kw: dict[str, Any], word: str) -> None:
    """The server drops a malformed rule without a word. The tool says why.
    Mutation: drop a check -> the write goes out -> red."""
    text = await core.my_tasks_add_day_rule(confirmed=True, **kw)
    assert word in text and "I changed nothing" in text, text
    assert gw.writes == []


async def test_add_refuses_when_the_list_is_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """40 rules fit, and the 41st does not. Mutation: use ``> 40`` -> red."""
    full = Gateway([{**DEEP, "start_hour": i % 8, "end_hour": 9} for i in range(40)])
    monkeypatch.setattr(core, "_request", full)
    text = await core.my_tasks_add_day_rule(days=[0], start_hour=1, end_hour=2, confirmed=True)
    assert "full" in text and "40" in text and full.writes == []
    almost = Gateway(full.rules[:39])
    monkeypatch.setattr(core, "_request", almost)
    assert (await core.my_tasks_add_day_rule(
        days=[0], start_hour=1, end_hour=2, confirmed=True)).startswith("Saved.")


async def test_edit_changes_the_given_fields_of_the_numbered_rule(gw: Gateway) -> None:
    """Partial edit. Mutation: replace the rule with the given fields only -> red."""
    text = await core.my_tasks_edit_day_rule(number=1, start_hour=9, label="Focus", confirmed=True)
    assert gw.writes == [{"day_templates": [
        {**DEEP, "start_hour": 9, "label": "Focus"}, NOMEET]}]
    assert "Rule 1: Mon-Fri 8-11 focus 'Deep work' -> Mon-Fri 9-11 focus 'Focus'" in text


async def test_edit_proposes_first_and_clears_a_label(gw: Gateway) -> None:
    """An empty label is a value that clears. Mutation: treat "" as not given -> red."""
    text = await core.my_tasks_edit_day_rule(number=1, label="")
    assert text.startswith("Not saved yet") and "focus 'Deep work' -> Mon-Fri 8-11 focus\n" in text
    assert gw.writes == []
    await core.my_tasks_edit_day_rule(number=1, label="", confirmed=True)
    assert gw.writes[0]["day_templates"][0]["label"] == ""


@pytest.mark.parametrize("number", [0, 3, -1, True])
async def test_edit_and_delete_refuse_a_number_out_of_range(gw: Gateway, number: Any) -> None:
    """Mutation: use a 0-based index, or let a negative index through -> red."""
    for out in (await core.my_tasks_edit_day_rule(number=number, start_hour=9, confirmed=True),
                await core.my_tasks_delete_day_rule(number=number, confirmed=True)):
        assert "no day rule number" in out and "numbered 1 to 2" in out, out
    assert gw.writes == []


async def test_edit_refuses_a_bad_result_and_an_empty_edit(gw: Gateway) -> None:
    """The merged rule is checked, not the field alone: a start of 12 on the 8-11
    rule is past its end. Mutation: check the given fields only -> red."""
    text = await core.my_tasks_edit_day_rule(number=1, start_hour=12, confirmed=True)
    assert "start_hour < end_hour" in text and gw.writes == []
    assert "No field was given" in await core.my_tasks_edit_day_rule(number=1, confirmed=True)
    same = await core.my_tasks_edit_day_rule(number=1, start_hour=8, confirmed=True)
    assert "Nothing changes" in same and gw.writes == []


async def test_delete_names_the_rule_and_applies_only_when_confirmed(gw: Gateway) -> None:
    """Mutation: delete on the proposal, or name the wrong rule -> red."""
    text = await core.my_tasks_delete_day_rule(number=1)
    assert "Delete rule 1: Mon-Fri 8-11 focus 'Deep work'" in text
    assert gw.writes == []
    done = await core.my_tasks_delete_day_rule(number=1, confirmed=True)
    assert gw.writes == [{"day_templates": [NOMEET]}]
    assert done.startswith("Deleted rule 1")


@pytest.mark.parametrize("call", ["add", "edit", "delete"])
async def test_a_412_answers_with_the_reread_text_and_writes_nothing(
        gw: Gateway, call: str) -> None:
    """Another change lands between the read and the write. Mutation: let the
    412 raise, or retry the write -> red."""
    gw.change_before_write = [DEEP]
    if call == "add":
        out = await core.my_tasks_add_day_rule(days=[1], start_hour=1, end_hour=2, confirmed=True)
    elif call == "edit":
        out = await core.my_tasks_edit_day_rule(number=2, start_hour=16, confirmed=True)
    else:
        out = await core.my_tasks_delete_day_rule(number=2, confirmed=True)
    assert out == "The day rules changed since I read them. Read the list again."
    assert gw.rules == [DEEP]
    assert len([c for c in gw.calls if c[0] == "PUT"]) == 1


async def test_another_error_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only 412 is the stale answer. Mutation: catch every error -> red."""
    async def boom(method: str, path: str, **kw: Any) -> Any:
        if method == "PUT":
            raise core._GatewayError("Tasks PUT failed (500)", 500)
        return {"day_templates": []}

    monkeypatch.setattr(core, "_request", boom)
    with pytest.raises(core._GatewayError):
        await core.my_tasks_add_day_rule(days=[0], start_hour=1, end_hour=2, confirmed=True)


def test_the_token_matches_the_server() -> None:
    """The tool hashes the list the server returned. The server hashes the list
    it normalises. They must agree. Mutation: change one of the two forms
    (separators, sort_keys) -> red."""
    from gateway.routes.tasks.settings import _day_templates, day_templates_token

    for rules in ([], [DEEP], [DEEP, NOMEET]):
        assert core.day_rules_token(rules) == day_templates_token(rules)
    messy = [{"days": [4, 0, 0], "start_hour": "8", "end_hour": 11, "kind": "x",
              "label": " a ", "theme": "T"}, {"bad": 1}]
    clean = _day_templates(messy)
    assert len(clean) == 1
    assert day_templates_token(messy) == core.day_rules_token(clean)


def test_the_signatures_and_the_risk() -> None:
    """The read is read-only and the three writes are not. Mutation: drop
    ``read_only`` or add it to a write -> red."""
    assert core.my_tasks_list_day_rules.__tool_risk__["read_only"] is True
    for fn in (core.my_tasks_add_day_rule, core.my_tasks_edit_day_rule,
               core.my_tasks_delete_day_rule):
        assert not fn.__tool_risk__.get("read_only")
        assert inspect.signature(fn).parameters["confirmed"].default is False
    assert set(inspect.signature(core.my_tasks_edit_day_rule).parameters) == {
        "number", "days", "start_hour", "end_hour", "kind", "label", "theme", "confirmed"}


def test_the_writes_are_held_off_and_the_read_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: drop a write from ``HELD_OFF_PACK_WRITES`` or add the read -> red."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "*")
    for name in (ADD, EDIT, DELETE):
        assert name in eg.HELD_OFF_PACK_WRITES[SLUG]
        assert eg.is_held_off_pack_write(getattr(skill_my_tasks, name), SLUG)
    assert LIST not in eg.HELD_OFF_PACK_WRITES[SLUG]
    assert not eg.is_held_off_pack_write(skill_my_tasks.my_tasks_list_day_rules, SLUG)


def test_the_family_lists_them_and_the_pack_does_not_load_on_a_bare_word() -> None:
    """Mutation: add "rule", "block" or "calendar" to the words -> red."""
    assert set(DAY_RULES) <= set(sf.SKILL_FAMILIES[SLUG]["tools"])
    words = tp.PACKS[SLUG].words
    for bare in ("rule", "block", "calendar", "focus", "day", "mornings"):
        assert bare not in words
    for phrase in ("day rule", "keep mornings", "focus block", "protect my mornings"):
        assert phrase in words
        assert tp.matched_layer(SLUG, f"please {phrase} for me")
    for mail in ("a rule in my inbox", "block that sender", "the calendar invite"):
        assert not tp.matched_layer(SLUG, mail), mail


def test_the_rules_text_has_the_example_and_the_confirm_step() -> None:
    flat = " ".join(tp.PACKS[SLUG].rules.split())
    assert "Keep mornings for deep work" in flat and "[0, 1, 2, 3, 4]" in flat
    assert "start_hour 8, end_hour 11, kind focus" in flat
    for name in (LIST, ADD, EDIT, DELETE):
        assert name in flat, name


def test_the_reach_test_names_the_pack_not_a_count() -> None:
    """14 tools are more than the 11 of ``projects-reports``, so a count alone
    would let the pack count as Projects work. Mutation: empty
    ``REACH_NOT_WORK`` -> red."""
    assert SLUG in tp.REACH_NOT_WORK
    assert len(sf.SKILL_FAMILIES[SLUG]["tools"]) >= tp.REACH_MIN_TOOLS
    assert len(sf.SKILL_FAMILIES["projects-reports"]["tools"]) >= tp.REACH_MIN_TOOLS


def test_parity_still_covers_the_settings_route() -> None:
    """Mutation: move the PUT route back to DEBT -> red."""
    assert cp.COVERED["PUT /api/tasks/settings"] == "my_tasks_update_calendar_settings"
    assert "PUT /api/tasks/settings" not in cp.DEBT


async def test_the_routes_read_if_match_and_return_the_etag(monkeypatch: pytest.MonkeyPatch) -> None:
    """``PUT`` takes ``If-Match`` as a header (not a query), and ``GET`` and ``PUT``
    answer the token in ``ETag``. Mutation: drop the ``Header`` default or the
    ``ETag`` line -> red."""
    from fastapi import Response
    from gateway.routes.tasks import settings as st

    route = next(r for r in st.router.routes
                 if getattr(r, "path", "").endswith("/settings") and "PUT" in r.methods)
    assert [p.alias for p in route.dependant.header_params] == ["if-match"]
    assert not any(p.name == "if_match" for p in route.dependant.query_params)

    model = st.UserSettingsModel(day_templates=[DEEP])

    async def fake_get(user: Any) -> Any:
        return model

    async def fake_put(patch: Any, user: Any, *, if_match: Any = None) -> Any:
        assert if_match == '"abc"'
        return model

    monkeypatch.setattr(st, "get_user_settings", fake_get)
    monkeypatch.setattr(st, "put_user_settings", fake_put)
    want = f'"{core.day_rules_token([DEEP])}"'
    got = Response()
    await st.read_user_settings(got, user=None)
    assert got.headers["etag"] == want
    sent = Response()
    await st.write_user_settings(st.UserSettingsPatch(), sent, user=None, if_match='"abc"')
    assert sent.headers["etag"] == want
