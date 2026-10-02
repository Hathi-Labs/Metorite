"""The correction a user types must actually arrive in the classifier's prompt.

The rest of the guidance tests check the pieces: the loader groups rows, the
renderer formats a line, the matchers mention the right helper names. All of
that can pass while the feature is dead — ``test_both_matchers_pass_guidance``
asserts on *source strings*, so it would still be green if the prompt came out
empty, or if guidance were rendered into a variable nobody sent anywhere.

So these drive the real matcher with a real DB row and read the prompt that
would have gone to the model. If a correction is not in that string, the user's
Fix did nothing, no matter what the UI says it learned.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gateway.routes.email.automation import engine as e

_RULES = [
    {"id": "r-news", "name": "Newsletter", "enabled": True,
     "instructions": "bulk mail you subscribed to", "conditions": {}},
    {"id": "r-cold", "name": "Cold Email", "enabled": True,
     "instructions": "unsolicited sales outreach", "conditions": {}},
]

_EMAIL = {"from": "digest@vendor.com", "subject": "Your weekly product digest",
          "body": "Here is what shipped this week.", "to": "u@example.com"}


def _db_returning(rows: list) -> AsyncMock:
    """A DB whose only consulted table is email_rule_guidance."""
    db = AsyncMock()
    db.execute.return_value = MagicMock(
        fetchall=MagicMock(return_value=rows))
    return db


async def _prompt_for(guidance_rows: list, *, multi: bool = False,
                      history: list | None = None) -> str:
    """Run a matcher and return the user prompt the classifier would get.

    ``multi`` selects the multi-rule matcher, which is the one that actually
    runs on the live account (multi_rule_execution is ON). Testing only the
    single-rule path would have proved the feature works on a code path this
    mailbox never takes.

    ``history`` is what ``_fetch_sender_history`` returns: the rule name and
    the count for each row. Since EM-T5b-1 the matchers call it, and no
    longer ``_fetch_classification_hints``.
    """
    seen: dict[str, str] = {}

    async def fake_llm_json(model, messages, **kw):
        seen["system"] = messages[0]["content"]
        seen["user"] = messages[1]["content"]
        return ({"matches": [{"index": 0, "reason": "ok", "primary": True}]}
                if multi else {"index": 0, "reason": "ok"}), "", model

    with (
        patch.object(e, "_load_rules", AsyncMock(return_value=_RULES)),
        patch.object(e, "_load_rule_patterns", AsyncMock(return_value={})),
        patch.object(e, "_is_reply_candidate",
                     AsyncMock(return_value=(False, ""))),
        patch.object(e, "_fetch_sender_history",
                     AsyncMock(return_value=list(history or []))),
        patch.object(e, "_llm_json", fake_llm_json),
    ):
        fn = (e._match_email_to_rules_multi if multi
              else e._match_email_to_rule)
        await fn(_db_returning(guidance_rows), "acc-1", _EMAIL)
    return seen.get("user", "")


async def test_a_correction_reaches_the_model() -> None:
    """The end-to-end claim the Fix dialog makes to the user."""
    prompt = await _prompt_for([
        SimpleNamespace(rule_id="r-news",
                        guidance="Vendor product digests are Newsletter."),
    ])
    assert "Vendor product digests are Newsletter." in prompt


async def test_the_correction_sits_under_the_rule_it_is_about() -> None:
    """Position is the mechanism, not decoration.

    Appended at the end of the prompt it reads as trailing advice about the
    email. Printed beneath its rule, it is part of what that rule MEANS — which
    is what makes it change the decision rather than merely be present."""
    prompt = await _prompt_for([
        SimpleNamespace(rule_id="r-news", guidance="Digests count."),
    ])
    news = prompt.index("Newsletter: bulk mail")
    note = prompt.index("Digests count.")
    cold = prompt.index("Cold Email: unsolicited")
    assert news < note < cold, (
        "guidance must render between its own rule and the next one")


async def test_it_is_labelled_as_the_users_correction() -> None:
    """Merged into the rule's blurb the model cannot tell a preset description
    from something the human explicitly told it."""
    prompt = await _prompt_for([
        SimpleNamespace(rule_id="r-news", guidance="Digests count."),
    ])
    assert "correction from the user: Digests count." in prompt


async def test_account_wide_guidance_overrides_by_name() -> None:
    """A correction with no single rule ("this isn't Cold Email") has nowhere to
    sit in the list, so it needs a block that says what it is."""
    prompt = await _prompt_for([
        SimpleNamespace(rule_id=None,
                        guidance="Mail from our own domain is never cold."),
    ])
    assert "CORRECTIONS THE USER HAS MADE BEFORE" in prompt
    assert "Mail from our own domain is never cold." in prompt


@pytest.mark.parametrize("multi", [False, True])
async def test_the_sender_history_reaches_the_prompt(multi: bool) -> None:
    """The rows of `_fetch_sender_history` become the old advisory hint, in
    both matchers. With no rows, no hint block appears."""
    rows = [{"rule": "Newsletter", "count": 4}, {"rule": "FYI", "count": 1}]
    prompt = await _prompt_for([], multi=multi, history=rows)
    assert "CLASSIFICATION HISTORY" in prompt
    assert "Newsletter (x4), FYI (x1)" in prompt
    assert "CLASSIFICATION HISTORY" not in await _prompt_for([], multi=multi)


async def test_no_guidance_leaves_the_prompt_untouched() -> None:
    """Nothing taught must not mean stray headings or empty bullets."""
    prompt = await _prompt_for([])
    assert "correction from the user" not in prompt
    assert "CORRECTIONS THE USER HAS MADE BEFORE" not in prompt


async def test_every_correction_for_a_rule_is_sent_not_just_the_first() -> None:
    """Corrections accumulate; keeping only the newest would silently discard
    what the user taught last week."""
    prompt = await _prompt_for([
        SimpleNamespace(rule_id="r-news", guidance="Digests count."),
        SimpleNamespace(rule_id="r-news", guidance="So do changelogs."),
    ])
    assert "Digests count." in prompt
    assert "So do changelogs." in prompt


@pytest.mark.parametrize("bad", ["", "   "])
async def test_blank_corrections_never_reach_the_prompt(bad: str) -> None:
    prompt = await _prompt_for([SimpleNamespace(rule_id="r-news", guidance=bad)])
    assert "correction from the user" not in prompt


# ── the path this mailbox actually takes ─────────────────────────────────────


async def test_corrections_reach_the_multi_rule_classifier_too() -> None:
    """multi_rule_execution is ON for the live account, so this — not the
    single-rule matcher — is the code path a real correction has to survive.
    Proving the feature on the other branch would have proved nothing here."""
    prompt = await _prompt_for(
        [SimpleNamespace(rule_id="r-news",
                         guidance="Vendor product digests are Newsletter.")],
        multi=True,
    )
    assert "correction from the user: Vendor product digests are Newsletter." \
        in prompt


async def test_account_wide_corrections_reach_the_multi_classifier() -> None:
    prompt = await _prompt_for(
        [SimpleNamespace(rule_id=None, guidance="Own-domain mail is internal.")],
        multi=True,
    )
    assert "CORRECTIONS THE USER HAS MADE BEFORE" in prompt
    assert "Own-domain mail is internal." in prompt
