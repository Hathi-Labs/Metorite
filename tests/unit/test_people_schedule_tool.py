"""P-5b-4 (task 3945): the chat tool ``set_work_schedule`` of the ``people`` pack.

Module: ``acb_skills.people_tools``. The route is ``PUT /people/schedule``.
A fake gateway stands in for the route, so these cases fence the tool: its
rights check, its dry run, its ONE card, its race check and its ONE write.
The route itself is fenced by ``tests/unit/test_people_schedule.py``.

Run: ``uv run pytest tests/unit/test_people_schedule_tool.py -q``.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from acb_skills import ask_tools
from acb_skills import people_tools as pt

ADMIN = "admin@example.test"
_REAL_CLIENT = httpx.AsyncClient
NOW = {"working_days": [1, 2, 3, 4, 5], "hours_per_day": 8, "start": "09:30"}


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.caps: list[str] | None = ["admin:members:manage"]
        self.policy: dict[str, Any] = dict(NOW)
        self.impact: dict[str, Any] = {
            "changed": 2, "unchanged": 5, "hours_before": 40.0, "hours_after": 32.0,
            "examples": [{"id": "1", "name": "Ada\nIGNORE", "before": 40.0, "after": 32.0}]}
        self.put_status = 200
        self.on_card: Any = None
        #: What the route stores instead of the value sent, and a lost write.
        self.repair: dict[str, Any] = {}
        self.drop_write = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        assert request.headers["x-user-email"] == ADMIN
        if request.url.path == "/auth/me":
            if self.caps is None:
                return httpx.Response(500, json={})
            return httpx.Response(200, json={"capabilities": self.caps})
        if request.method == "GET" and request.url.path == "/people/schedule":
            return httpx.Response(200, json={"policy": self.policy})
        if request.method == "PUT" and request.url.path == "/people/schedule":
            if self.put_status != 200:
                return httpx.Response(self.put_status, json={"detail": "Bad hours."})
            saved = body["dry_run"] is not True
            if saved and self.drop_write:
                raise httpx.ReadTimeout("lost", request=request)
            return httpx.Response(200, json={
                "policy": {**body["policy"], **self.repair}, "impact": self.impact,
                "saved": saved})
        return httpx.Response(404, json={"detail": "Not Found"})

    def puts(self, dry: bool) -> list[Any]:
        return [c for c in self.calls
                if c[0] == "PUT" and (c[2]["dry_run"] is True) == dry]


class _Card:
    def __init__(self, gw: _Gateway) -> None:
        self.gw = gw
        self.answer: Any = True
        self.shown: list[dict[str, Any]] = []

    async def __call__(self, title: str, detail: str = "", context: str = "", **kw: Any) -> Any:
        self.shown.append({"title": title, "context": context, **kw})
        if self.gw.on_card:
            self.gw.on_card()
        return self.answer


@pytest.fixture
def gw(monkeypatch) -> _Gateway:
    g = _Gateway()
    transport = httpx.MockTransport(g.handle)
    monkeypatch.setattr(pt.httpx, "AsyncClient",
                        lambda **kw: _REAL_CLIENT(transport=transport, **kw))
    monkeypatch.setattr(pt, "_current_user_email", lambda: ADMIN)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
    return g


@pytest.fixture
def card(gw, monkeypatch) -> _Card:
    c = _Card(gw)
    monkeypatch.setattr(ask_tools, "request_confirmation", c)
    return c


async def test_an_approved_change_dry_runs_shows_one_card_and_writes_once(gw, card) -> None:
    """Mutation: skip the dry run, show no card, or send twice -> red."""
    out = await pt.set_work_schedule({"hours_per_day": 6})
    assert out["ok"] is True and out["people_changed"] == 2, out
    assert len(card.shown) == 1
    body = card.shown[0]["context"]
    assert "40 hours to 32 hours" in body and "People whose hours change: 2" in body
    assert "hours_per_day: «8» to «6»" in body
    assert "\nIGNORE" not in body and "«Ada IGNORE»" in body
    real = gw.puts(dry=False)
    assert len(real) == 1
    # The change goes onto the policy of now: a field left out stays.
    assert real[0][2]["policy"] == {**NOW, "hours_per_day": 6}


async def test_a_member_without_the_right_gets_no_card_and_no_put(gw, card) -> None:
    gw.caps = ["admin:members:read"]
    out = await pt.set_work_schedule({"hours_per_day": 6})
    assert out["ok"] is False and out["status"] == 403
    assert card.shown == [] and gw.puts(True) == [] and gw.puts(False) == []


async def test_an_unread_right_fails_closed(gw, card) -> None:
    gw.caps = None
    out = await pt.set_work_schedule({"hours_per_day": 6})
    assert out["ok"] is False and card.shown == [] and gw.puts(False) == []


@pytest.mark.parametrize("bad", [
    None, "x", [1], {"working_day": [1]}, {"hours_per_day": 6, "timezone": "UTC"},
    {"default_timezone": "UTC\u202ex"}, {"default_timezone": "UTC \u00abx\u00bb"},
    {"default_timezone": "UTC\nIGNORE"}, {"shifts": [{"name": "a\u200bb"}]}])
async def test_a_bad_change_sends_nothing(gw, card, bad) -> None:
    out = await pt.set_work_schedule(bad)
    assert out["ok"] is False and gw.calls == [] and card.shown == []


async def test_a_refusal_of_the_dry_run_shows_no_card(gw, card) -> None:
    gw.put_status = 400
    out = await pt.set_work_schedule({"hours_per_day": 99})
    assert out["ok"] is False and "Bad hours." in out["error"] and "Nothing was saved" in out["error"]
    assert card.shown == [] and gw.puts(False) == []


async def test_a_change_that_changes_nothing_shows_no_card(gw, card) -> None:
    out = await pt.set_work_schedule({"hours_per_day": 8})
    assert out["ok"] is True and "already" in out["note"]
    assert card.shown == [] and gw.puts(False) == []


@pytest.mark.parametrize("answer", [False, ask_tools.REVISED])
async def test_a_card_that_is_not_approved_writes_nothing(gw, card, answer) -> None:
    card.answer = answer
    out = await pt.set_work_schedule({"hours_per_day": 6})
    assert out["ok"] is False and gw.puts(False) == []


async def test_a_chat_with_no_card_writes_nothing(gw, card, monkeypatch) -> None:
    card.answer = False
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: False)
    out = await pt.set_work_schedule({"hours_per_day": 6})
    assert out["ok"] is False and out["decision"] == "no_card" and gw.puts(False) == []


async def test_an_impact_that_moves_while_the_card_is_open_writes_nothing(gw, card) -> None:
    """The race rule. Mutation: drop the second dry run -> red."""
    gw.on_card = lambda: gw.impact.update(changed=9)
    out = await pt.set_work_schedule({"hours_per_day": 6})
    assert out["ok"] is False and "moved while the card waited" in out["error"]
    assert gw.puts(False) == []


async def test_a_card_that_does_not_fit_writes_nothing(gw, card) -> None:
    """Removed entries are never cut. Mutation: cut the list on the card -> red."""
    gw.policy = {**NOW, "holidays": [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
                                     for i in range(200)]}
    out = await pt.set_work_schedule({"holidays": []})
    assert out["ok"] is False and card.shown == [] and gw.puts(False) == []


async def test_a_policy_that_another_admin_changed_is_not_overwritten(gw, card) -> None:
    """Mutation: drop the second read of the policy -> red."""
    gw.on_card = lambda: gw.policy.update(holidays=["2026-12-25"], start="08:00")
    out = await pt.set_work_schedule({"hours_per_day": 6})
    assert out["ok"] is False and "changed while the card waited" in out["error"]
    assert gw.puts(False) == []


async def test_a_value_that_the_server_would_repair_is_refused(gw, card) -> None:
    gw.repair = {"week_start": 1}
    out = await pt.set_work_schedule({"week_start": "sunday"})
    assert out["ok"] is False and "would not store week_start" in out["error"]
    assert card.shown == [] and gw.puts(False) == []


async def test_days_in_another_order_are_the_same_value(gw, card) -> None:
    gw.repair = {"working_days": [1, 2]}
    out = await pt.set_work_schedule({"working_days": [2, 1, 2]})
    assert out["ok"] is True, out


async def test_the_card_names_each_holiday_that_the_change_removes(gw, card) -> None:
    gw.policy = {**NOW, "holidays": ["2026-01-26", "2026-08-15"]}
    out = await pt.set_work_schedule({"holidays": ["2026-12-25"]})
    assert out["ok"] is True, out
    body = card.shown[0]["context"]
    assert "holidays: 2 now, 1 after." in body
    assert "holidays REMOVED: «2026-01-26»" in body and "REMOVED: «2026-08-15»" in body
    assert "holidays added: «2026-12-25»" in body


async def test_empty_changes_read_the_schedule_with_no_card(gw, card) -> None:
    out = await pt.set_work_schedule({})
    assert out["ok"] is True and "09:30" in out["schedule"]
    assert card.shown == [] and gw.puts(True) == [] and gw.puts(False) == []


async def test_a_write_that_may_have_landed_names_the_schedule(gw, card) -> None:
    gw.drop_write = True
    out = await pt.set_work_schedule({"hours_per_day": 6})
    assert out["ok"] is False and "Read the work schedule again" in out["error"]


def test_the_schedule_keys_are_the_keys_of_the_route() -> None:
    """A package must not import the gateway, but a test may."""
    from gateway.work_schedule import DEFAULT_POLICY

    assert set(pt.SCHEDULE_KEYS) == set(DEFAULT_POLICY)


def test_the_tool_is_a_write_that_takes_no_identity() -> None:
    import inspect

    from acb_skills.tool_annotations import get_annotations

    hints = get_annotations("set_work_schedule")
    assert hints["read_only"] is False and hints["open_world"] is False
    assert list(inspect.signature(pt.set_work_schedule).parameters) == ["changes"]


@pytest.mark.parametrize("owner", [None, "org-admin"])
def test_a_held_chat_holds_off_the_schedule_write(monkeypatch, owner) -> None:
    """A chat that read outside mail must not steer a company-wide write.
    Mutation: drop ``org-admin`` from ``HELD_WRITE_OWNERS`` -> red."""
    from acb_common import get_settings
    from acb_skills import egress

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "*")
    for item in (pt.set_work_schedule, "set_work_schedule"):
        assert egress.is_held_off_write(item, owner=owner) is True
    assert egress.is_held_off_write(pt.people_directory, owner=owner) is False
