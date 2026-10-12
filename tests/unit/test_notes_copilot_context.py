"""Tests for the copilot's meeting context pack.

Context is what separates a copilot that says "someone raised a concern" from
one that says "Acme churned over pricing last year — ask about their renewal
date". Three independent layers feed it, and the load-bearing property is that
**each one degrades on its own**: no brief, no history, an agent that times out,
Zoho not connected — every combination must still yield a usable copilot.
"""
from __future__ import annotations

import asyncio

from gateway.routes.notes import copilot_context as cc

# ── rendering ────────────────────────────────────────────────────────────────

def test_prompt_leads_with_what_the_human_said() -> None:
    """The user's briefing is the most trustworthy context — usually the only
    source that knows what the meeting is FOR — so it goes first."""
    pack = cc.ContextPack(
        brief="Pricing negotiation with Acme. They churned in 2024.",
        attendees=["Priya", "Alex"],
        past=["Q3 review (12 Mar): agreed to revisit pricing"],
        open_actions=["Send the revised quote"],
        systems={"crm": "Acme — Enterprise, renewal due in 6 weeks."},
    )
    out = pack.as_prompt()
    assert out.index("WHY THIS MEETING") < out.index("PREVIOUSLY")
    assert out.index("PREVIOUSLY") < out.index("CRM")
    assert "Priya, Alex" in out


def test_missing_layers_are_simply_absent() -> None:
    """No brief and no connectors must not produce empty headings or noise."""
    pack = cc.ContextPack(past=["Kickoff (01 Jan): scope agreed"])
    out = pack.as_prompt()
    assert "PREVIOUSLY" in out
    assert "WHY THIS MEETING" not in out
    assert "CRM" not in out


def test_empty_pack_is_detectable_and_renders_to_nothing() -> None:
    """The console tells the user their copilot is flying blind — that needs a
    truthful signal, not an empty string that looks like context."""
    assert cc.ContextPack().is_empty is True
    assert cc.ContextPack().as_prompt() == ""
    assert cc.ContextPack(brief="x").is_empty is False


def test_attendees_alone_do_not_count_as_context() -> None:
    """Knowing who is in the room says nothing about the meeting."""
    assert cc.ContextPack(attendees=["Priya"]).is_empty is True


# ── attendee parsing ─────────────────────────────────────────────────────────

def test_attendee_names_prefer_names_and_fall_back_to_email() -> None:
    raw = [{"name": "Priya", "email": "p@acme.com"}, {"email": "alex@acme.com"}]
    assert cc.attendee_names(raw) == ["Priya", "alex@acme.com"]


def test_attendee_emails_are_lowercased_and_deduped() -> None:
    raw = [{"email": "P@Acme.com"}, {"email": "p@acme.com"}, {"name": "no email"}]
    assert cc.attendee_emails(raw) == ["p@acme.com"]


def test_malformed_attendees_are_survivable() -> None:
    for bad in (None, "not a list", [None, 42, {}]):
        assert cc.attendee_names(bad) == []
        assert cc.attendee_emails(bad) == []


# ── past-meeting summarisation ───────────────────────────────────────────────

def test_past_meeting_renders_as_one_jogging_line() -> None:
    import datetime as dt

    line = cc.summarise_past(
        "Q3 review", dt.datetime(2026, 3, 12), "# Notes\nAgreed to revisit pricing"
    )
    assert "Q3 review" in line and "12 Mar" in line
    assert "#" not in line          # markdown stripped
    assert "\n" not in line         # stays one line


def test_past_meeting_survives_missing_pieces() -> None:
    assert "Untitled meeting" in cc.summarise_past(None, None, None)


def test_long_summary_is_clipped_so_history_cant_bloat_the_prompt() -> None:
    line = cc.summarise_past("T", None, "word " * 500)
    assert len(line) < 250


# ── caps ─────────────────────────────────────────────────────────────────────

def test_the_pack_is_capped_on_every_axis() -> None:
    """It rides in EVERY copilot prompt, so it's recurring cost — an
    uncapped history would quietly raise the price of every suggestion."""
    assert cc._MAX_PAST_MEETINGS <= 5
    assert cc._MAX_OPEN_ACTIONS <= 10
    assert cc._MAX_BRIEF_CHARS <= 2000
    assert cc._MAX_SYSTEM_CHARS <= 1500


def test_brief_is_clipped_to_its_cap() -> None:
    assert len(cc._clip("x" * 9999, cc._MAX_BRIEF_CHARS)) <= cc._MAX_BRIEF_CHARS + 1


# ── Layer 3: agent lookups must never be able to hurt ────────────────────────

def test_a_missing_agent_contributes_nothing(monkeypatch) -> None:
    """call_agent raising (unregistered agent, connector down) must not fail the
    pack — that section is just absent."""
    async def boom(*_a, **_k):
        raise RuntimeError("no such agent")

    monkeypatch.setattr("acb_skills.call_agent", boom, raising=False)
    label, body = asyncio.run(cc._ask_agent("tasks", "task-manager", "q"))
    assert (label, body) == ("tasks", "")


def test_an_agent_saying_NONE_is_dropped(monkeypatch) -> None:
    """Agents are told to answer NONE rather than narrate an empty result —
    otherwise "I couldn't find anything" would eat prompt budget every meeting."""
    async def none(*_a, **_k):
        return "NONE"

    monkeypatch.setattr("acb_skills.call_agent", none, raising=False)
    assert asyncio.run(cc._ask_agent("crm", "a", "q"))[1] == ""


def test_an_agents_answer_is_clipped(monkeypatch) -> None:
    async def chatty(*_a, **_k):
        return "detail " * 5000

    monkeypatch.setattr("acb_skills.call_agent", chatty, raising=False)
    _, body = asyncio.run(cc._ask_agent("crm", "a", "q"))
    assert 0 < len(body) <= cc._MAX_SYSTEM_CHARS + 1


def test_no_attendees_means_no_agent_fan_out() -> None:
    """Without people to ask about, the lookups would burn tokens for nothing."""
    assert asyncio.run(cc._system_context([])) == {}


def test_agent_queries_ask_the_registered_agents() -> None:
    agents = {agent for _label, agent, _p in cc._AGENT_QUERIES}
    assert "task-manager" in agents
    for _label, _agent, prompt in cc._AGENT_QUERIES:
        assert "{people}" in prompt, "query must be scoped to the attendees"
        assert "NONE" in prompt, "must define the empty answer"


def test_the_copilot_fan_out_names_no_crm_agent() -> None:
    """WS-53 CRM-C7: ``call_agent`` here runs with no member, and a CRM tool
    refuses such a run. A CRM row would put a refusal in the prompt."""
    rows = [(label, agent) for label, agent, _p in cc._AGENT_QUERIES]
    rows += list(cc._CONSULT_AGENTS)
    assert rows, "the fan-out lost every row"
    for label, agent in rows:
        assert "crm" not in label.lower(), (label, agent)
        assert "crm" not in agent.lower(), (label, agent)
        assert "sales" not in agent.lower(), (label, agent)


# ── cache ────────────────────────────────────────────────────────────────────

def test_pack_is_cached_and_forgettable() -> None:
    mid = "ctx-cache"
    cc._CACHE[mid] = cc.ContextPack(brief="cached")
    assert asyncio.run(cc.get(mid)).brief == "cached"
    cc.forget(mid)
    assert mid not in cc._CACHE
