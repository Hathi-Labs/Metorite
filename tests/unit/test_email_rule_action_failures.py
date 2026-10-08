"""A rule that changed nothing must not report that it did.

Found by the Analytics trust panel the day it shipped: **138 rule applications
in 30 days returned 404 from Microsoft Graph**, every one of them logged
``status='APPLIED'`` with ``actions_taken = []``. The rule matched, the mailbox
was never touched, and nothing anywhere said so.

Two separate causes, both fixed here.

**1. We invalidated our own message id.** Outlook re-keys a message when it
moves: ``/move`` returns a new id and the old one 404s. ``_apply_and_log_match``
took its id from a row fetched ONCE, before the per-match loop — so with
``multi_rule_execution`` on (it is, on the live account) the second matching
rule called Graph with the id the first rule's ``MOVE_FOLDER`` had just
invalidated. Live: 46 of the 138 had a sibling rule that moved the message in
the same run. Traced end to end on one message — Cold Email moved it at
03:57:43, Marketing 404'd on the stale id in the same second.

The remaining ~92 are the provider racing us: Outlook's own junk/delete
filtering re-keys or removes a message between our sync and our call. We cannot
prevent that one, which is exactly why the STATUS has to be honest about it.

**2. APPLIED was load-bearing, and it was a lie.** It is what the auto-learn
gate counts as evidence and what Analytics counts as work done. So three failed
runs against one sender were three votes for pinning that sender to a rule
forever — a fourth defect in the same gate audited in #97, and this one could
entrench a pattern off a mailbox that was never modified.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gateway.routes.email.automation import runner as m

_ACC = "acc-fail"
_RULE = {"id": "rule-1", "name": "Newsletter", "actions": [{"type": "LABEL"}]}
_MATCH = {"rule": _RULE, "reason": "because", "source": "ai"}


def _row(pmid: str = "OLD-ID") -> SimpleNamespace:
    return SimpleNamespace(id="msg-1", provider_message_id=pmid,
                           thread_id="t1", subject="s")


def _db(current_pmid: str = "NEW-ID") -> AsyncMock:
    db = AsyncMock()
    db.execute.return_value = MagicMock(
        scalar=MagicMock(return_value=current_pmid),
        fetchall=MagicMock(return_value=[]),
        fetchone=MagicMock(return_value=None),
    )
    return db


async def _run(db: AsyncMock, taken: list[str], errors: list[dict],
               *, apply: bool = True) -> None:
    async def fake_apply(_db, _prov, _mid, pmid, *a, **kw):
        fake_apply.seen_pmid = pmid
        kw["errors_out"].extend(errors)
        return list(taken)
    fake_apply.seen_pmid = None
    with patch.object(m, "_apply_rule_actions", fake_apply), \
         patch.object(m, "_sender_is_a_correspondent",
                      AsyncMock(return_value=False)), \
         patch.object(m, "_sender_consistent_for_rule",
                      AsyncMock(return_value=True)), \
         patch.object(m, "_ai_confirms_sender_pattern",
                      AsyncMock(return_value=True)), \
         patch.object(m, "_upsert_rule_pattern", AsyncMock()) as upsert:
        await m._apply_and_log_match(
            db, object(), _row(), {"email": "n@x.com"}, {}, _MATCH, apply,
            "", "", "me@x.com", _ACC, sole_match=True)
    _run.pmid = fake_apply.seen_pmid
    _run.upsert = upsert


def _logged(db: AsyncMock) -> dict:
    """The params of the INSERT into email_executed_rules."""
    for call in reversed(db.execute.call_args_list):
        if "email_executed_rules" in str(call[0][0]):
            return call[0][1]
    raise AssertionError("no executed-rules row was logged")


# ── the id we call with ─────────────────────────────────────────────────────


async def test_the_current_message_id_is_read_before_acting() -> None:
    """The row was fetched before the loop; by the time rule 2 runs, rule 1's
    move has re-keyed the message. Re-reading is what makes rule 2 reach it."""
    db = _db(current_pmid="NEW-ID")
    await _run(db, taken=["LABEL"], errors=[])
    assert _run.pmid == "NEW-ID", (
        "acted on the id the row was READ with, which a sibling rule's "
        "MOVE_FOLDER had already invalidated — this is the 404"
    )


async def test_a_missing_current_id_falls_back_to_the_row() -> None:
    """Never act on None. If the lookup returns nothing, the row's own id is
    still the best guess and a 404 is better than a crash."""
    db = _db(current_pmid=None)
    await _run(db, taken=["LABEL"], errors=[])
    assert _run.pmid == "OLD-ID"


async def test_the_log_records_the_id_actually_called() -> None:
    """When the two differ the row was re-keyed mid-run; the history should show
    what we called, or the next person debugging this chases the wrong id."""
    db = _db(current_pmid="NEW-ID")
    await _run(db, taken=["LABEL"], errors=[])
    assert _logged(db)["pmid"] == "NEW-ID"


def test_the_id_is_reread_inside_the_apply_branch() -> None:
    """A dry run must not touch the database for this — it changes nothing, so
    there is no re-key to catch up with."""
    src = inspect.getsource(m._apply_and_log_match)
    head = src[:src.index("else:")]
    assert "SELECT provider_message_id FROM email_messages" in head


# ── what the status is allowed to claim ─────────────────────────────────────


async def test_every_action_failing_is_not_APPLIED() -> None:
    """138 live rows said APPLIED with actions_taken=[]. The mailbox was never
    modified; the log said the assistant had handled the email."""
    db = _db()
    await _run(db, taken=[], errors=[{"type": "LABEL", "error": "404"}])
    assert _logged(db)["status"] == "FAILED"


async def test_partial_success_stays_APPLIED() -> None:
    """Something did happen. Demoting the whole row would lose the label that
    was genuinely written; the failure rides along in action_errors."""
    db = _db()
    await _run(db, taken=["LABEL"],
               errors=[{"type": "MOVE_FOLDER", "error": "404"}])
    assert _logged(db)["status"] == "APPLIED"


async def test_a_clean_run_is_APPLIED() -> None:
    db = _db()
    await _run(db, taken=["LABEL"], errors=[])
    assert _logged(db)["status"] == "APPLIED"


async def test_a_dry_run_is_still_PENDING() -> None:
    """Unchanged: a preview reports what it WOULD do and has no errors."""
    db = _db()
    await _run(db, taken=[], errors=[], apply=False)
    assert _logged(db)["status"] == "PENDING"


# ── failures teach nothing ──────────────────────────────────────────────────


async def test_a_failed_run_never_teaches_a_pattern() -> None:
    """The gate audited in #97 asked only whether a rule MATCHED. Three 404s
    against a re-keyed message were three votes for pinning that sender to a
    rule forever, off a mailbox that was never touched."""
    db = _db()
    await _run(db, taken=[], errors=[{"type": "LABEL", "error": "404"}])
    _run.upsert.assert_not_called()


async def test_a_successful_run_still_teaches() -> None:
    """The tightening must not switch auto-learning off."""
    db = _db()
    await _run(db, taken=["LABEL"], errors=[])
    _run.upsert.assert_called_once()


def test_the_learn_gate_reads_the_status_it_just_computed() -> None:
    """Guards against the status being demoted to FAILED while the learn block
    keeps running off the old unconditional `if apply:`."""
    src = inspect.getsource(m._apply_and_log_match)
    assert 'if (status == "APPLIED"' in src
    assert src.index('status = "FAILED"') < src.index('if (status == "APPLIED"')


# ── a rule draft whose file fails (WS-17 EM-T9 item 11) ─────────────────────


def _graph_answer(status: int, body: dict) -> SimpleNamespace:
    return SimpleNamespace(status_code=status, headers={}, json=lambda: body,
                           raise_for_status=lambda: None)


@pytest.mark.parametrize("kind", ["REPLY", "FORWARD"])
async def test_a_rule_draft_with_a_failed_file_is_deleted(kind: str) -> None:
    """EM-T9 item 11 (``email_app_master_plan.md`` §10.4.10). REPLY makes its
    draft with ``createReply`` and FORWARD makes a new one. Before EM-T9 a
    failed file was dropped, and the rule left a draft with no file. Now the
    draft is deleted, the action is not done, and ``errors_out`` holds the
    file name and nothing else, never a URL."""
    from email_ingestion.providers.outlook import OutlookProvider
    from gateway.routes.email.automation import actions

    client = AsyncMock()

    async def _post(url: str, **_kw: object) -> SimpleNamespace:
        if url.endswith("/attachments"):
            return _graph_answer(500, {"error": {"code": "ErrorInternal"}})
        return _graph_answer(201, {"id": "rule-draft-1"})

    client.post.side_effect = _post
    client.patch.return_value = _graph_answer(200, {})
    client.delete.return_value = _graph_answer(204, {})
    provider = OutlookProvider({"access_token": "t", "refresh_token": "r"})
    provider._http = client
    action = {"type": kind, "content": "Please see the quote.",
              "to_address": "lead@em-t9.test",
              "attachments": [{"path": "quote.pdf", "name": "quote.pdf"}]}
    errors: list[dict] = []

    with patch.object(actions, "_load_action_attachments", return_value=[
            {"filename": "quote.pdf", "content": b"%PDF-1.7",
             "mime_type": "application/pdf"}]), \
         patch.object(actions, "_skip_for_paired_mailbox",
                      AsyncMock(return_value=False)), \
         patch.object(actions, "_upsert_local_draft", AsyncMock()) as upsert:
        done = await actions._apply_rule_actions(
            _db(), provider, "msg-1", "AAMk-parent", [action],
            email={"from": "n@x.com", "subject": "Quote"},
            account_id=_ACC, errors_out=errors)

    assert done == []
    assert errors == [{"type": kind,
                       "error": "The file quote.pdf could not be attached."}]
    client.delete.assert_awaited_once_with("/me/messages/rule-draft-1")
    upsert.assert_not_awaited()


# ── a rule reply with a typed To (WS-17 EM-T10 review round 1) ──────────────


@pytest.mark.parametrize(("to_address", "to_patch"), [
    ("ops@em-t10.test", [{"emailAddress": {"address": "ops@em-t10.test"}}]),
    (None, None),
], ids=["typed-to", "default-to"])
async def test_a_rule_reply_writes_a_typed_to_at_outlook(
        to_address: str | None, to_patch: list | None) -> None:
    """A rule REPLY with a To that the member typed must reach Graph. Outlook
    makes the draft with ``createReply``, which addresses the sender, so the
    PATCH must carry the typed To. With no typed To, the PATCH carries no To,
    and ``createReply`` keeps its To, which honours a Reply-To."""
    from email_ingestion.providers.outlook import OutlookProvider
    from gateway.routes.email.automation import actions

    client = AsyncMock()
    client.post.return_value = _graph_answer(201, {"id": "rule-draft-2"})
    client.patch.return_value = _graph_answer(200, {})
    provider = OutlookProvider({"access_token": "t", "refresh_token": "r"})
    provider._http = client
    action = {"type": "REPLY", "content": "Thanks, we have it."}
    if to_address:
        action["to_address"] = to_address

    with patch.object(actions, "_load_action_attachments", return_value=[]), \
         patch.object(actions, "_skip_for_paired_mailbox",
                      AsyncMock(return_value=False)), \
         patch.object(actions, "_upsert_local_draft", AsyncMock()):
        await actions._apply_rule_actions(
            _db(), provider, "msg-2", "AAMk-parent-2", [action],
            email={"from": "ravi@x.com", "subject": "Order"},
            account_id=_ACC, errors_out=[])

    (patch_call,) = client.patch.await_args_list
    sent = patch_call.kwargs["json"]
    assert sent.get("toRecipients") == to_patch
