"""WS-47 WAC-10c — the review round: the cases the first build got wrong.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §13.6.

R7 fences named here:

* ``wac10c-menu-tap``: a tap on a menu row ("Title (description)") runs the
  view with no AI call.
* ``wac10c-no-false-clear``: a failed or capped read never says ✅.
* ``wac10c-link-org``: a view runs only in the link's org.
* ``wac10c-ai-buttons``: a tap on the AI's own button goes to the AI.
* ``wac10c-no-records``: the model never reads element records, and a
  record it types becomes the real element (the owner's screenshots).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from acb_skills import whatsapp_ui as wui
from gateway.routes.whatsapp_channel import views

from tests.unit.test_wac_bot_run import _message, _post_and_run, _World
from tests.unit.test_wac_views import (  # noqa: F401 - fixtures
    ALL,
    NOW,
    ORG,
    _Ctx,
    _native,
    _Prov,
    prov,
    reads,
    world,
)

# ── Matching ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("tap", "name"), [
    ("My day (What needs you now, from every app)", "my_day"),
    ("Due today (Your tasks due today)", "due_today"),
    ("Calendar (Today's plan on a timeline)", "calendar"),
    ("Approvals (Agent actions waiting for you)", "approvals"),
])
def test_a_menu_row_tap_matches_on_its_title(tap: str, name: str) -> None:
    assert views.match(tap) == name


def test_a_row_tap_of_another_list_is_not_a_command() -> None:
    assert views.match("Fix the extruder (Snowflake X1 · 3 days ago)") is None


async def test_a_menu_tap_runs_the_view_with_no_ai(
    world: _World, reads: dict, prov: _Prov, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.unit.test_wac_native_ui import _tap

    _native(monkeypatch)
    await _post_and_run(_tap({"id": "r1", "title": "My day",
                              "description": "What needs you now, from every app"},
                             "list_reply"))
    assert world.agent.calls == []
    assert world.sent[0][1].startswith("*My day*")


# ── The views ───────────────────────────────────────────────────────────────


async def test_rows_with_one_title_are_kept_apart_not_refused(reads: dict) -> None:
    reads["feed"]["items"] = [
        {"kind": "notification", "title": "vjvarada commented on #12 Fix extruder",
         "detail": "P", "at": NOW.isoformat()},
        {"kind": "notification", "title": "vjvarada commented on #13 Order nozzles",
         "detail": "P", "at": NOW.isoformat()},
        {"kind": "overdue", "title": "vjvarada commented on #14 Weekly check",
         "detail": "P", "at": NOW.isoformat()},
    ]
    v = await views.run("my_day", reads["ctx"])
    titles = [r["title"] for s in v.ui[0].interactive["action"]["sections"] for r in s["rows"]]
    assert len(titles) == 3 and len({t.casefold() for t in titles}) == 3
    assert all(len(t) <= 24 for t in titles)


async def test_a_failed_source_never_reads_as_all_clear(reads: dict) -> None:
    reads["feed"] = {"items": [], "sources": {"tasks": "failed", "email": "ok"}}
    v = await views.run("my_day", reads["ctx"])
    assert "✅" not in v.text and "may not be all" in v.text
    assert "✅" not in repr([m.interactive for m in v.ui])


async def test_a_failed_approvals_source_hands_over_to_the_assistant(reads: dict) -> None:
    reads["feed"] = {"items": [], "sources": {"approvals": "failed"}}
    with pytest.raises(RuntimeError):
        await views.run("approvals", reads["ctx"])


async def test_a_full_source_shows_a_plus(reads: dict) -> None:
    reads["feed"] = {"items": [
        {"kind": "overdue", "title": f"Task {i}", "detail": "P",
         "at": (NOW - timedelta(days=1)).isoformat()} for i in range(15)], "sources": {}}
    v = await views.run("my_day", reads["ctx"])
    assert "15+ overdue" in v.text
    assert any(m.interactive["type"] == "cta_url" for m in v.ui), "no link to the rest"


async def test_due_today_at_the_read_cap_never_says_none(reads: dict) -> None:
    reads["due"] = {"rows": [
        {"title": f"Old {i}", "due_at": (NOW - timedelta(days=30)).isoformat()}
        for i in range(views.DUE_READ)], "timezone": "UTC"}
    with pytest.raises(RuntimeError):
        await views.run("due_today", reads["ctx"])
    late = await views.run("overdue", reads["ctx"])
    assert f"{views.DUE_READ}+ tasks" in late.text


async def test_a_context_in_another_org_is_refused(reads: dict) -> None:
    reads["ctx"] = _Ctx(set(ALL), org="22222222-2222-4222-8222-222222222222")
    with pytest.raises(PermissionError):
        await views.member_context("alice@fracktal.in", ORG)


async def test_the_menu_shows_only_what_the_member_can_open(reads: dict) -> None:
    ctx = _Ctx({"admin:settings:manage"})  # an admin with no apps
    menu = await views.run("menu", ctx)
    titles = [r["title"] for r in menu.ui[0].interactive["action"]["sections"][0]["rows"]]
    assert titles == ["My day"]


async def test_a_view_link_carries_the_links_org(reads: dict) -> None:
    with wui.whatsapp_run("orchestrator", org=ORG):
        v = await views.run("calendar", reads["ctx"])
    url = v.ui[-1].interactive["action"]["parameters"]["url"]
    assert url == f"https://app.metorite.com/calendar?org={ORG}"


async def test_a_view_that_breaks_tells_the_ai_why(
    reads: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gateway.routes.tasks.calendar as calendar

    async def _broken(date: Any, user: Any) -> dict:
        raise KeyError("day")

    monkeypatch.setattr(calendar, "day_summary", _broken)
    with wui.whatsapp_run("orchestrator", views=views.runner("alice@fracktal.in", ORG)):
        out = await wui.whatsapp_ui("view", {"name": "calendar"})
        unknown = await wui.whatsapp_ui("view", {"name": "weather"})
    assert out["ok"] is False and "could not be built" in out["error"]
    assert unknown["ok"] is False and "Views:" in unknown["error"]


# ── The run ─────────────────────────────────────────────────────────────────


def test_a_tap_on_the_ais_own_button_goes_to_the_ai() -> None:
    """The AI asked "Today or this week?". A tap on "Today" answers it."""
    from gateway.routes.whatsapp_channel import bot_run

    history = [{"role": "assistant", "content":
                "Which one?\n\n" + wui.RENDITION_MARK + "Which?\n[Buttons: Today | This week]"}]
    assert bot_run._offered_ai_choice(history) is True
    view_turn = [{"role": "assistant", "content": wui.VIEW_MARK + "*Metorite*\n\n"
                  + wui.RENDITION_MARK + "Tap one\n[List \"Menu\":\n- My day]"}]
    assert bot_run._offered_ai_choice(view_turn) is False
    assert bot_run._offered_ai_choice([]) is False


async def test_after_the_ais_buttons_a_command_word_runs_the_ai(
    world: _World, reads: dict, prov: _Prov, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.routes.whatsapp_channel import bot_run

    _native(monkeypatch)

    async def _turns(sid: str, mid: str) -> tuple[str, list]:
        return "Today", [{"role": "assistant", "content": "Which?\n\n" + wui.RENDITION_MARK
                          + "Which?\n[Buttons: Today | This week]"}]

    monkeypatch.setattr(bot_run, "_thread_turns", _turns)
    await _post_and_run(_message("Today"))
    assert len(world.agent.calls) == 1 and "needs" not in reads["calls"]


async def test_a_view_reply_is_marked_and_the_model_never_reads_records(
    world: _World, reads: dict, prov: _Prov, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.routes.whatsapp_channel import bot_run

    _native(monkeypatch)
    await _post_and_run(_message("Due today"))
    (thread,) = world.store.threads.values()
    record = thread[-1]["content"]
    assert record.startswith(wui.VIEW_MARK) and wui.RENDITION_MARK in record
    seen = bot_run.model_turn({"role": "assistant", "content": record})["content"]
    assert wui.RENDITION_MARK not in seen and "[List" not in seen
    assert seen.startswith("*Due today*")
    only = bot_run.model_turn({"role": "assistant",
                               "content": wui.RENDITION_MARK + "[Table: x]\na | b"})
    assert only["content"] == "(I sent the member a WhatsApp card.)"
    member = {"role": "user", "content": "[Table: mine]"}
    assert bot_run.model_turn(member) == member
    assert wui.resend_text(record).startswith("*Due today*")


async def test_a_typed_record_becomes_the_real_element(
    world: _World, reads: dict, prov: _Prov, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owner's screenshot: the model typed the record. The guard draws it,
    and the empty "tap" promise becomes a real list of the bullets."""
    pytest.importorskip("pymupdf")
    _native(monkeypatch)

    class _Typist:
        calls: list = []

        async def __call__(self, agent: str, payload: dict, **kw: Any) -> Any:
            return {"result": "Due today:\n[Table: Sougata - due today]\n# | Task | Priority\n"
                              "80 | Update drawings | Urgent\n194 | Enclosure | Urgent\n\n"
                              "Tap any of these to dig in:\n- *FDM PRINTS* - 3 batches on hold\n"
                              "- *Fracktory Operations* - #14 in Backlog"}

    world.agent = _Typist()
    await _post_and_run(_message("what is due for sougata today please"))
    parts = [s for _to, s in world.sent]
    assert "[Table" not in parts[0] and "80 | Update" not in parts[0]
    assert ("image", "media-1") in parts and ("interactive", "list") in parts


# ── The guard alone ─────────────────────────────────────────────────────────


def test_a_tap_promise_with_no_bullets_loses_the_sentence() -> None:
    text, out = wui.polish("Two tasks are late. Tap any of these to dig in.", [])
    assert text == "Two tasks are late." and out == []


def test_a_tap_promise_with_real_buttons_stays() -> None:
    msg = wui.build("buttons", {"body": "More?", "buttons": ["Yes"]})
    text, out = wui.polish("Tap Yes for more.", [msg])
    assert text == "Tap Yes for more." and out == [msg]


def test_a_bullet_list_becomes_rows_with_heads_and_details() -> None:
    text, out = wui.polish("Worth a look:\n- *FDM PRINTS* — 3 batches on hold\n"
                           "- #238 (MDS): update CAD\nTap one to dig in.", [])
    (lst,) = out
    rows = lst.interactive["action"]["sections"][0]["rows"]
    assert rows[0] == {"id": "r1", "title": "FDM PRINTS", "description": "3 batches on hold"}
    assert rows[1]["title"] == "#238 (MDS)" and rows[1]["description"] == "update CAD"


def test_the_guard_never_raises() -> None:
    text, out = wui.polish("[Chart, bar: x] a: not-a-number", [])
    assert "[Chart" not in text and out == []


def test_a_link_carries_the_runs_org_and_drops_the_models() -> None:
    url = wui.safe_link("https://app.metorite.com/projects?task=7&org=evil", org=ORG)
    assert url == f"https://app.metorite.com/projects?task=7&org={ORG}"
    assert wui.safe_link("https://app.metorite.com/tasks") == "https://app.metorite.com/tasks"
