"""Corrections that teach the classifier, kept apart from ones that bypass it.

Until now a Fix could only produce a learned PATTERN — a hard sender→rule pin
that short-circuits the model. So correcting a mistake never made the assistant
better at anything: it removed one sender from the AI's reach and left the same
misunderstanding in place for every other sender. Every one of the live
account's 21 patterns was machine-inferred; not one encoded something the user
had actually taught.

    "Correcting a classification should enhance future AI classifications.
     I am not focusing on learned patterns." — 2026-07-21

Guidance is the other half. Free text attached to a rule and injected into the
classification prompt, so "vendor product digests are Newsletter, not Cold
Email" generalises to every vendor rather than exempting the one that was wrong.

The two are stored apart on purpose — that separation is what lets the Learned
Patterns screen split into "improves the AI" and "replaces the AI", which is
the whole point of the distinction:

    guidance  → changes how the model REASONS   (every sender, costs a call)
    pattern   → REPLACES the model for a sender (one sender, costs nothing)
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from gateway.routes.email.automation import engine as e


def _db(rows: list | None = None) -> AsyncMock:
    db = AsyncMock()
    db.execute.return_value = MagicMock(
        fetchall=MagicMock(return_value=rows or []))
    return db


# ── loading ─────────────────────────────────────────────────────────────────


async def test_guidance_is_grouped_by_rule() -> None:
    db = _db([
        SimpleNamespace(rule_id="r1", guidance="Zoho digests count."),
        SimpleNamespace(rule_id="r1", guidance="So do vendor changelogs."),
        SimpleNamespace(rule_id="r2", guidance="Invoices only."),
    ])
    out = await e._load_rule_guidance(db, "acc-1")
    assert out == {"r1": ["Zoho digests count.", "So do vendor changelogs."],
                   "r2": ["Invoices only."]}


async def test_rule_less_guidance_lands_under_the_empty_key() -> None:
    """Guidance that isn't about one rule ("our own domain is never Cold
    Email") applies to the whole prompt, so it needs somewhere to live that is
    not a rule id."""
    db = _db([SimpleNamespace(rule_id=None, guidance="Own domain is internal.")])
    out = await e._load_rule_guidance(db, "acc-1")
    assert out == {"": ["Own domain is internal."]}


async def test_only_active_guidance_is_loaded() -> None:
    db = _db([])
    await e._load_rule_guidance(db, "acc-1")
    assert "active" in str(db.execute.call_args[0][0])


async def test_a_broken_guidance_table_degrades_to_no_corrections() -> None:
    """Classification must still work. Silently returning {} would hide the
    failure, so the loader logs — the same contract as _load_rule_patterns."""
    db = AsyncMock()
    db.execute.side_effect = RuntimeError("no such table")
    assert await e._load_rule_guidance(db, "acc-1") == {}


async def test_blank_guidance_is_dropped() -> None:
    db = _db([SimpleNamespace(rule_id="r1", guidance="   ")])
    assert await e._load_rule_guidance(db, "acc-1") == {}


# ── rendering into the prompt ───────────────────────────────────────────────


def test_a_correction_is_labelled_as_one() -> None:
    """Merged into the rule's own description it would read as more generic
    blurb. The model should weigh "the user told me this specific thing" more
    heavily than the preset sentence shipped with the rule."""
    line = e._rule_lines(
        [{"id": "r1", "name": "Newsletter", "instructions": "newsletters"}],
        {"r1": ["Zoho digests count."]})
    assert "0. Newsletter: newsletters" in line
    assert "correction from the user: Zoho digests count." in line


def test_rules_without_guidance_are_unchanged() -> None:
    plain = e._rule_lines([{"id": "r1", "name": "N", "instructions": "x"}], {})
    assert plain == "0. N: x"
    assert plain == e._rule_lines(
        [{"id": "r1", "name": "N", "instructions": "x"}], None)


def test_numbering_is_the_index_the_model_replies_with() -> None:
    """The picker maps the returned index straight back into the rules list, so
    a mismatch here files mail under the wrong rule entirely."""
    line = e._rule_lines([
        {"id": "a", "name": "A", "instructions": "1"},
        {"id": "b", "name": "B", "instructions": "2"},
    ], {"b": ["note"]})
    assert line.startswith("0. A: 1")
    assert "\n1. B: 2" in line


def test_account_wide_guidance_is_its_own_block() -> None:
    out = e._global_guidance_block({"": ["Own-domain mail is never cold."]})
    assert "Own-domain mail is never cold." in out
    assert e._global_guidance_block({}) == ""
    assert e._global_guidance_block(None) == ""
    assert e._global_guidance_block({"r1": ["per-rule only"]}) == ""


# ── wiring ──────────────────────────────────────────────────────────────────


def test_both_matchers_pass_guidance_to_the_model() -> None:
    """A loader nothing consults is worse than none — the UI would show the
    user's corrections while the classifier quietly ignored them.

    EM-T4a-2 PR-B1: both matchers run ONE read step and ONE ask step. The
    read step loads the guidance, and the ask step passes it to the model
    in each mode (`_llm_pick_rule` and `_llm_pick_rules`)."""
    import inspect
    for fn in (e._match_email_to_rule, e._match_email_to_rules_multi):
        src = inspect.getsource(fn)
        assert "read_rule_match(" in src and "ask_rule_match(" in src, fn.__name__
    assert "_load_rule_guidance" in inspect.getsource(e.read_rule_match)
    ask = inspect.getsource(e.ask_rule_match)
    assert ask.count("guidance=read.guidance") == 2
    assert "_llm_pick_rule(" in ask and "_llm_pick_rules(" in ask


def test_guidance_is_loaded_after_the_pattern_short_circuit() -> None:
    """A pinned sender never reaches the LLM, so building its prompt context
    would be wasted work on the hot path. The read step holds both (EM-T4a-2
    PR-B1)."""
    import inspect
    src = inspect.getsource(e.read_rule_match)
    assert src.index("_patterns_included_rule") < src.index(
        "_load_rule_guidance")
