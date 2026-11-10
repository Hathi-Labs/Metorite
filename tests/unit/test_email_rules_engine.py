"""Unit tests for the rule-action dispatcher (`_apply_rule_actions`).

These verify that each rule action maps to the correct provider call and that
reply/draft actions never auto-send (they create provider drafts). The provider
and DB session are mocked, so no mailbox or network is touched.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

from gateway.routes import email as m

_EMAIL = {
    "from": "sender@example.com",
    "subject": "Quote request",
    "body": "Can you send pricing?",
    "thread_id": "thread-1",
}


async def test_archive_moves_to_archive() -> None:
    db, provider = AsyncMock(), AsyncMock()
    done = await m._apply_rule_actions(
        db, provider, "mid", "pmid", [{"type": "ARCHIVE"}], _EMAIL
    )
    assert done == ["ARCHIVE"]
    provider.move_to_folder.assert_awaited_once_with("pmid", "archive")


async def test_mark_read_sets_flag() -> None:
    db, provider = AsyncMock(), AsyncMock()
    done = await m._apply_rule_actions(
        db, provider, "mid", "pmid", [{"type": "MARK_READ"}], _EMAIL
    )
    assert done == ["MARK_READ"]
    provider.apply_flags.assert_awaited_once_with("pmid", is_read=True)


async def test_label_adds_label() -> None:
    db, provider = AsyncMock(), AsyncMock()
    done = await m._apply_rule_actions(
        db, provider, "mid", "pmid",
        [{"type": "LABEL", "label": "Work"}], _EMAIL,
    )
    assert done == ["LABEL"]
    provider.set_labels.assert_awaited_once_with("pmid", add=["Work"], remove=[])


async def test_draft_with_static_content_creates_draft_without_llm() -> None:
    db, provider = AsyncMock(), AsyncMock()
    done = await m._apply_rule_actions(
        db, provider, "mid", "pmid",
        [{"type": "DRAFT_EMAIL", "content": "Thanks, pricing attached.",
          "to_address": "sender@example.com"}],
        _EMAIL,
    )
    assert done == ["DRAFT_EMAIL"]
    provider.create_draft.assert_awaited_once()
    kwargs = provider.create_draft.await_args.kwargs
    # Static template is used verbatim — the LLM drafter is not invoked.
    assert kwargs["body_text"] == "Thanks, pricing attached."
    assert kwargs["to"] == ["sender@example.com"]


async def test_draft_skipped_when_no_recipient() -> None:
    db, provider = AsyncMock(), AsyncMock()
    no_sender = {**_EMAIL, "from": ""}
    done = await m._apply_rule_actions(
        db, provider, "mid", "pmid",
        [{"type": "DRAFT_EMAIL", "content": "Hi"}], no_sender,
    )
    assert done == []
    provider.create_draft.assert_not_awaited()


async def test_forward_creates_draft_never_sends() -> None:
    db, provider = AsyncMock(), AsyncMock()
    done = await m._apply_rule_actions(
        db, provider, "mid", "pmid",
        [{"type": "FORWARD", "to_address": "boss@example.com"}], _EMAIL,
    )
    assert done == ["FORWARD"]
    provider.create_draft.assert_awaited_once()
    # Forward must go out as a DRAFT, not a live send.
    assert not provider.send_message.called


async def test_unknown_action_is_ignored() -> None:
    db, provider = AsyncMock(), AsyncMock()
    done = await m._apply_rule_actions(
        db, provider, "mid", "pmid", [{"type": "NONSENSE"}], _EMAIL
    )
    assert done == []


async def test_multiple_actions_all_applied_in_order() -> None:
    db, provider = AsyncMock(), AsyncMock()
    done = await m._apply_rule_actions(
        db, provider, "mid", "pmid",
        [{"type": "MARK_READ"}, {"type": "ARCHIVE"}], _EMAIL,
    )
    assert done == ["MARK_READ", "ARCHIVE"]


async def test_a_refused_provider_call_leaves_no_local_folder_change() -> None:
    """Provider-first ordering: if the mail server refuses a TRASH (Outlook
    re-keyed or deleted the message), the local folder must NOT be rewritten to
    'trash'. The old order committed a phantom folder that analytics read as
    truth and that made a failed TRASH exclude itself from its own repair."""
    db, provider = AsyncMock(), AsyncMock()
    provider.trash_message.side_effect = RuntimeError("404 — message re-keyed")
    errors: list[dict] = []
    done = await m._apply_rule_actions(
        db, provider, "mid", "pmid", [{"type": "TRASH"}], _EMAIL,
        errors_out=errors,
    )
    # The action failed: not reported as done, and surfaced in errors_out.
    assert done == []
    assert errors and errors[0]["type"] == "TRASH"
    # No UPDATE ... folder='trash' was executed — the provider call raised first.
    folder_writes = [
        c for c in db.execute.call_args_list
        if "folder='trash'" in str(c[0][0])
    ]
    assert folder_writes == [], "a refused TRASH still wrote a phantom folder"


# ── H-404: a rule draft carries the Cc and the Bcc that the rule names ───────
#
# The rules UI and the chat save ``cc_address`` and ``bcc_address`` on a reply
# or a forward action. The engine dropped them, so the card named people that
# the draft never held. Mutation: drop ``cc=`` or ``bcc=`` from a
# ``create_draft`` call of ``_apply_rule_actions`` → red.


async def test_a_forward_draft_carries_the_cc_and_the_bcc_of_the_rule() -> None:
    db, provider = AsyncMock(), AsyncMock()
    done = await m._apply_rule_actions(
        db, provider, "mid", "pmid",
        [{"type": "FORWARD", "to_address": "boss@example.com",
          "cc_address": "ops@example.com", "bcc_address": "records@example.com"}], _EMAIL,
    )
    assert done == ["FORWARD"]
    kwargs = provider.create_draft.await_args.kwargs
    assert kwargs["to"] == ["boss@example.com"]
    assert kwargs["cc"] == ["ops@example.com"]
    assert kwargs["bcc"] == ["records@example.com"]


async def test_a_reply_draft_carries_the_cc_and_the_bcc_of_the_rule() -> None:
    db, provider = AsyncMock(), AsyncMock()
    done = await m._apply_rule_actions(
        db, provider, "mid", "pmid",
        [{"type": "DRAFT_EMAIL", "content": "Thanks.", "to_address": "a@example.com",
          "cc_address": "b@example.com", "bcc_address": "c@example.com"}], _EMAIL,
    )
    assert done == ["DRAFT_EMAIL"]
    kwargs = provider.create_draft.await_args.kwargs
    assert kwargs["cc"] == ["b@example.com"] and kwargs["bcc"] == ["c@example.com"]


async def test_a_rule_with_no_cc_drafts_with_none() -> None:
    db, provider = AsyncMock(), AsyncMock()
    await m._apply_rule_actions(
        db, provider, "mid", "pmid",
        [{"type": "FORWARD", "to_address": "boss@example.com"}], _EMAIL,
    )
    kwargs = provider.create_draft.await_args.kwargs
    assert kwargs.get("cc") is None and kwargs.get("bcc") is None
