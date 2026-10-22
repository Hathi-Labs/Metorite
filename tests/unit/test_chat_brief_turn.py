"""WS-62 CF-5 — the day's brief as the first turn of a fresh chat, the pure
half (``chat_first_surface.md`` §4.6, D-CF-6; ``routes/chat_brief.py``).

What this holds without a database:

* ``plan.brief_parts`` is the one composition. ``compose_brief`` draws its
  text from the same parts, byte for byte as before, so WhatsApp and the chat
  never disagree on a line (D-CF-6).
* The lead line comes from the counts, and the card is a ``reportCard`` whose
  ``pills`` are the lines, in the catalog's shape.
* The route module creates its row through the one minter and names
  ``mint=True`` nowhere (``test_rooms.py`` keeps that literal in
  ``routes/agent.py`` alone), and it is registered in ``main.py``.

The database half is ``test_chat_brief_turn_r8.py``.
"""
from __future__ import annotations

import inspect
import pathlib
import re
from datetime import UTC, date, datetime, timedelta

from gateway.reach import plan
from gateway.routes import chat_brief

NOW = datetime(2026, 10, 22, 4, 30, tzinfo=UTC)  # 10:00 in Kolkata, a Thursday
TODAY = date(2026, 10, 22)


def card(sid: str, kind: str = "needs_reply", *, group: str = "needs_you",
         title: str = "Q3 pricing", facts: dict | None = None, score: int = 50) -> dict:
    if facts is None:
        facts = ({"who": "Priya Shah", "waiting_since": (NOW - timedelta(minutes=40)).isoformat()}
                 if kind == "needs_reply" else {})
    return {"signal_id": sid, "app": sid.split(":")[0], "kind": kind, "group": group,
            "score": score, "title": title, "facts": facts, "fingerprint": "fp000001",
            "state": "new", "at": None, "why": "Why line."}


class TestOneComposition:
    def test_the_parts_are_the_lines_of_the_text(self):
        cards = [card("email:a:t1"), card("tasks:1", "overdue", title="Send the deck",
                                          facts={"days_late": 2}, score=40)]
        parts = plan.brief_parts(cards, TODAY, now=NOW)
        assert parts is not None
        assert parts.title == "Your day, Thu 22 Oct"
        assert parts.lines == ['Reply to Priya Shah: "Q3 pricing" (waiting 40 min)',
                               "Overdue: Send the deck (2 days late)"]
        assert (parts.rest, parts.total) == (0, 2)
        text = plan.compose_brief(cards, "https://app.metorite.com", TODAY, now=NOW)
        assert text == ("*Your day, Thu 22 Oct*\n"
                        '• Reply to Priya Shah: "Q3 pricing" (waiting 40 min)\n'
                        "• Overdue: Send the deck (2 days late)\n\n"
                        "My Day: https://app.metorite.com/")

    def test_the_rest_is_counted_and_the_text_still_says_more(self):
        cards = [card(f"tasks:{i}", "overdue", title=f"Task {i}", score=60 - i,
                      facts={"days_late": 1}) for i in range(9)]
        parts = plan.brief_parts(cards, TODAY, now=NOW)
        assert parts is not None
        assert len(parts.lines) == plan.BRIEF_LINES and parts.rest == 2 and parts.total == 9
        text = plan.compose_brief(cards, "https://app.metorite.com", TODAY, now=NOW)
        assert text is not None and "+2 more. My Day: https://app.metorite.com/" in text

    def test_no_parts_with_nothing_in_now_or_needs_you(self):
        assert plan.brief_parts([card("tasks:1", "overdue", group="fyi")], TODAY, now=NOW) is None
        assert plan.brief_parts([], TODAY, now=NOW) is None


class TestTheTurn:
    def test_the_lead_counts_the_items(self):
        one = plan.BriefParts("Your day, Thu 22 Oct", ["Approve: refund"], 0)
        many = plan.BriefParts("Your day, Thu 22 Oct", ["a", "b"], 3)
        assert plan.brief_lead(one) == "Your day, Thu 22 Oct. 1 thing needs you."
        assert plan.brief_lead(many) == "Your day, Thu 22 Oct. 5 things need you."

    def test_the_card_is_a_report_card_with_the_lines_as_pills(self):
        parts = plan.BriefParts("Your day, Thu 22 Oct", ["Approve: refund", "Due today: deck"], 0)
        assert chat_brief.brief_card(parts) == {
            "type": "template",
            "props": {"name": "reportCard",
                      "data": {"title": "Your day, Thu 22 Oct",
                               "pills": ["Approve: refund", "Due today: deck"]}},
        }
        more = plan.BriefParts("Your day, Thu 22 Oct", ["a"], 2)
        assert chat_brief.brief_card(more)["props"]["data"]["note"] == "+2 more on My Day"
        event = chat_brief.brief_event(parts)
        assert (event["name"], event["segmentCutoff"]) == ("generative_ui", 0)
        assert event["value"] == chat_brief.brief_card(parts)

    def test_the_card_keys_are_in_the_catalog_shape(self):
        """The TSX catalog and the tool's copy both name ``pills`` and
        ``note`` on ``reportCard`` (``test_genui_catalog_lockstep.py`` holds
        the two to one string)."""
        root = pathlib.Path(__file__).resolve().parents[2]
        tsx = (root / "workbench" / "control_plane" / "src" / "components"
               / "genUITemplates.tsx").read_text(encoding="utf-8")
        shape = re.search(r'name: "reportCard",.*?data: "((?:[^"\\]|\\.)*)"', tsx, re.S)
        assert shape is not None
        for key in ("title", "pills?:[string]", "note?"):
            assert key in shape.group(1), key

    def test_the_row_id_is_one_a_day(self):
        assert chat_brief.brief_message_id(TODAY) == "brief-2026-10-22"


class TestTheSeams:
    def test_the_row_goes_through_the_one_minter_and_the_one_message_seam(self):
        src = inspect.getsource(chat_brief._post_row)
        assert "_mint_run_row(" in src
        assert "_upsert_messages(" in src
        module = pathlib.Path(chat_brief.__file__).read_text(encoding="utf-8")
        # `test_rooms.py` keeps these two keywords in their one caller each.
        assert "mint=True" not in module
        assert "author_from_run" not in module

    def test_the_router_is_registered_and_gated_on_chat(self):
        main = pathlib.Path(chat_brief.__file__).resolve().parents[1] / "main.py"
        assert "from gateway.routes.chat_brief import router" in main.read_text(encoding="utf-8")
        routes = {r.path for r in chat_brief.router.routes}
        assert routes == {"/chat/sessions/{session_id}/brief"}
        assert any(
            "require_feature" in repr(d.dependency) or "feature" in repr(d.dependency)
            for d in chat_brief.router.dependencies
        )

    def test_no_model_call_on_the_path(self):
        """No model writes a line of the brief (§4.6): the module names no
        tier, no LiteLLM and no decide."""
        module = pathlib.Path(chat_brief.__file__).read_text(encoding="utf-8")
        for word in ("litellm", "tier_", "decide(", "complete(", "acompletion"):
            assert word not in module, word
