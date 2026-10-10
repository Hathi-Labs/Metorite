"""WS-47 WAC-10c — views and quick commands, database-free.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §13.

The REAL views, the REAL ``bot_run`` and the REAL ``whatsapp_ui`` run here.
The three reads the views call (the My Day feed, the member's due work, the
day summary) and the member's context are fakes.

R7 fences named here:

* ``wac10c-quick-no-ai``: a quick command is answered with NO agent run.
* ``wac10c-quick-falls-back``: a view that fails hands the message to the
  assistant, so the member still gets an answer.
* ``wac10c-feature-gate``: a view checks the feature its router would check.
* ``wac10c-view-tool``: the AI sends a view by name and writes no rows.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from acb_common import get_settings
from acb_skills import whatsapp_ui as wui
from gateway.routes.whatsapp_channel import views

from tests.unit.test_wac_bot_run import (  # noqa: F401 - `world` is a fixture
    PHONE,
    _message,
    _post_and_run,
    _World,
    world,
)

NOW = datetime.now(UTC)


ORG = "11111111-1111-4111-8111-111111111111"


class _Ctx:
    def __init__(self, perms: set[str], org: str = ORG) -> None:
        self.perms = perms
        self.email = "alice@fracktal.in"
        self.organization_id = org

    def has_permission(self, p: str) -> bool:
        return p in self.perms


ALL = {"feature:projects", "feature:tasks", "feature:email", "feature:approvals",
       "admin:members:read",
       "admin:settings:manage"}


@pytest.fixture()
def reads(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The three reads, faked, and a record of who asked."""
    state: dict[str, Any] = {
        "feed": {"items": [
            {"kind": "overdue", "title": "Fix the extruder", "detail": "Snowflake X1",
             "at": (NOW - timedelta(days=3)).isoformat()},
            {"kind": "approval", "title": "Send the vendor email", "detail": "email agent",
             "at": (NOW - timedelta(hours=5)).isoformat()},
            {"kind": "due_today", "title": "Order nozzles", "detail": "Snowflake X1",
             "at": (NOW + timedelta(hours=3)).isoformat()},
        ], "sources": {"tasks": "ok", "email": "failed"}},
        "due": {"rows": [
            {"title": "Fix the extruder", "project_name": "Snowflake X1",
             "due_at": (NOW - timedelta(days=3)).isoformat()},
            {"title": "Order nozzles", "project_name": "Snowflake X1",
             "due_at": (NOW + timedelta(hours=3)).isoformat()},
            {"title": "Someday thing", "disposition": "SOMEDAY",
             "due_at": (NOW + timedelta(hours=1)).isoformat()},
        ], "timezone": "Asia/Kolkata"},
        "day": {"day": NOW.date().isoformat(), "capacity_mins": 480, "scheduled": [
            {"title": "Stand-up", "start": NOW.replace(hour=3, minute=30).isoformat(),
             "end": NOW.replace(hour=4, minute=0).isoformat(), "fixed": True},
            {"title": "Firmware review", "start": NOW.replace(hour=4, minute=30).isoformat(),
             "end": NOW.replace(hour=6, minute=0).isoformat(), "fixed": False},
        ], "overdue_count": 1, "unscheduled_count": 4,
            "one_thing": {"id": "t1", "title": "Ship firmware"}},
        "calls": [],
    }

    import gateway.routes.projects.personal as personal
    import gateway.routes.shell.needs as needs
    import gateway.routes.tasks.calendar as calendar

    async def _needs(limit: int, user: Any) -> dict:
        state["calls"].append("needs")
        return state["feed"]

    async def _due(user: Any, *, limit: int) -> dict:
        state["calls"].append("due")
        return state["due"]

    async def _day(date: Any, user: Any) -> dict:
        state["calls"].append("day")
        return state["day"]

    async def _today(user: Any) -> dict:
        return {"today": NOW.date().isoformat(), "timezone": "Asia/Kolkata"}

    monkeypatch.setattr(needs, "shell_needs", _needs)
    monkeypatch.setattr(personal, "my_due_tasks", _due)
    monkeypatch.setattr(personal, "my_today", _today)
    monkeypatch.setattr(calendar, "day_summary", _day)
    state["ctx"] = _Ctx(set(ALL))

    import acb_auth.deps as deps

    async def _member(email: str) -> _Ctx:
        state["calls"].append(f"ctx:{email}")
        return state["ctx"]

    # The REAL views.member_context runs, with its org check; the request
    # path's resolve under it is the fake.
    monkeypatch.setattr(deps, "member_context", _member)
    return state


# ── Matching ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("text", "name"), [
    ("today", "my_day"), ("Today!", "my_day"), ("  My Day ", "my_day"),
    ("What’s due today?", "due_today"), ("Due today", "due_today"),
    ("overdue", "overdue"), ("📅 Calendar", "calendar"), ("my schedule", "calendar"),
    ("Approvals", "approvals"), ("hi", "menu"), ("Menu", "menu"),
])
def test_a_short_command_matches_its_view(text: str, name: str) -> None:
    assert views.match(text) == name


@pytest.mark.parametrize("text", [
    "what is due today for the extruder project", "today I want to plan the launch",
    "calendar for next week", "", "x" * 70,
])
def test_a_real_question_goes_to_the_assistant(text: str) -> None:
    assert views.match(text) is None


# ── The views ───────────────────────────────────────────────────────────────


async def test_my_day_lists_the_feed_by_kind(reads: dict) -> None:
    v = await views.run("my_day", reads["ctx"])
    assert v.text.startswith("*My day* — 1 overdue · 1 approval · 1 due today")
    assert "Email did not answer in time" in v.text
    (lst,) = v.ui
    sections = lst.interactive["action"]["sections"]
    assert [s["title"] for s in sections] == ["🔴 Overdue", "✋ Approvals", "📅 Due today"]
    assert sections[0]["rows"][0]["description"].startswith("Snowflake X1 · 3 days ago")


async def test_due_today_and_overdue_split_the_due_work(reads: dict) -> None:
    due = await views.run("due_today", reads["ctx"])
    late = await views.run("overdue", reads["ctx"])
    assert due.text.startswith("*Due today* — 1 task")
    assert late.text.startswith("*Overdue* — 1 task")
    row = late.ui[0].interactive["action"]["sections"][0]["rows"][0]
    assert row["title"] == "Fix the extruder" and "late" in row["description"]
    assert "Someday thing" not in repr(due.ui[0].interactive), "a Someday task showed"


async def test_calendar_draws_the_day_in_the_members_zone(reads: dict) -> None:
    v = await views.run("calendar", reads["ctx"])
    assert "★ One thing: Ship firmware" in v.text and "4 not planned yet" in v.text
    agenda, link = v.ui
    assert agenda.kind == "image" and "09:00-09:30 Stand-up" in agenda.rendition
    assert link.interactive["action"]["parameters"]["url"] == "https://app.metorite.com/calendar"


async def test_an_empty_day_says_so_with_buttons(reads: dict) -> None:
    reads["feed"] = {"items": [], "sources": {}}
    v = await views.run("my_day", reads["ctx"])
    (buttons,) = v.ui
    assert "Nothing needs you" in buttons.interactive["body"]["text"]


async def test_more_than_ten_rows_adds_a_link(reads: dict) -> None:
    reads["feed"]["items"] = [
        {"kind": "due_today", "title": f"Task {i}", "detail": "P",
         "at": (NOW + timedelta(hours=1)).isoformat()} for i in range(14)]
    v = await views.run("my_day", reads["ctx"])
    lst, link = v.ui
    assert len(lst.interactive["action"]["sections"][0]["rows"]) == 10
    assert link.interactive["body"]["text"] == "4 more in Metorite."


async def test_a_view_checks_the_feature_its_router_would(reads: dict) -> None:
    ctx = _Ctx({"feature:projects"})
    with pytest.raises(PermissionError):
        await views.run("calendar", ctx)
    approvals = await views.run("approvals", ctx)
    assert "for the admins" in approvals.text
    menu = await views.run("menu", ctx)
    titles = [r["title"] for r in menu.ui[0].interactive["action"]["sections"][0]["rows"]]
    assert "Approvals" not in titles


# ── The quick path in the run ───────────────────────────────────────────────


def _native(monkeypatch: pytest.MonkeyPatch, on: bool = True) -> None:
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_native_ui", on, raising=False)


class _Prov:
    def __init__(self, world: _World) -> None:
        self.world = world

    def _sent(self, item: Any) -> str:
        self.world.sent.append((PHONE, item))
        return f"wamid.out.{len(self.world.sent)}"

    async def send_text(self, to: str, body: str, **_quote: Any) -> str:
        return self._sent(body)

    async def send_reaction(self, to: str, wamid: str, emoji: str) -> str:
        return self._sent(("reaction", emoji))

    async def send_interactive(self, to: str, inter: dict, **_quote: Any) -> str:
        return self._sent(("interactive", inter["type"]))

    async def upload_media(self, data: bytes, mime: str, name: str) -> str:
        return "media-1"

    async def send_image(self, to: str, media_id: str, *, caption: str | None = None,
                         **_quote: Any) -> str:
        return self._sent(("image", media_id))

    async def show_typing(self, wamid: str) -> bool:
        return True


@pytest.fixture()
def prov(monkeypatch: pytest.MonkeyPatch, world: _World) -> _Prov:
    from whatsapp_ingestion.providers import factory

    p = _Prov(world)
    monkeypatch.setattr(factory, "build_provider", lambda name, creds: p)
    return p


async def test_a_quick_command_answers_with_no_agent_run(
    world: _World, reads: dict, prov: _Prov, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _native(monkeypatch)
    await _post_and_run(_message("Due today"))
    assert world.agent.calls == [], "a quick command ran the assistant"
    assert [s for _to, s in world.sent] == [
        world.sent[0][1], ("interactive", "list")]
    assert world.sent[0][1].startswith("*Due today* — 1 task")
    assert world.store.inbound_rows()[0]["state"] == "replied"
    assert "ctx:alice@fracktal.in" in reads["calls"]


async def test_a_failing_view_hands_the_message_to_the_assistant(
    world: _World, reads: dict, prov: _Prov, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _native(monkeypatch)
    import gateway.routes.projects.personal as personal

    async def _broken(user: Any, *, limit: int) -> dict:
        raise RuntimeError("db down")

    monkeypatch.setattr(personal, "my_due_tasks", _broken)
    await _post_and_run(_message("overdue"))
    assert len(world.agent.calls) == 1
    assert world.store.inbound_rows()[0]["state"] == "replied"


async def test_with_the_switch_off_a_command_goes_to_the_assistant(
    world: _World, reads: dict, prov: _Prov, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _native(monkeypatch, False)
    await _post_and_run(_message("today"))
    assert len(world.agent.calls) == 1 and "needs" not in reads["calls"]


# ── The AI's view kind ──────────────────────────────────────────────────────


async def test_the_ai_sends_a_view_by_name(reads: dict) -> None:
    with wui.whatsapp_run("orchestrator", views=views.runner("alice@fracktal.in", ORG)) as run:
        out = await wui.whatsapp_ui("view", {"name": "overdue"})
        bad = await wui.whatsapp_ui("view", {"name": "nope"})
    assert out["ok"] is True and out["sent"].startswith("*Overdue*")
    assert bad["ok"] is False and "no view" in bad["error"]
    assert [m.kind for m in run.outbox] == ["text", "interactive"]
    assert reads["calls"].count("ctx:alice@fracktal.in") == 1, "the context resolved twice"


async def test_a_view_the_member_may_not_open_is_refused(reads: dict) -> None:
    reads["ctx"] = _Ctx({"feature:projects"})
    with wui.whatsapp_run("orchestrator", views=views.runner("alice@fracktal.in", ORG)):
        out = await wui.whatsapp_ui("view", {"name": "calendar"})
    assert out["ok"] is False and "cannot open" in out["error"]


async def test_a_run_with_no_views_refuses_the_kind() -> None:
    with wui.whatsapp_run("orchestrator"):
        out = await wui.whatsapp_ui("view", {"name": "my_day"})
    assert out["ok"] is False
