"""A conversation has ONE classification, re-evaluated on every new message.

The live account's RFQ thread (2026-07-21) is the whole motivation, so it is
the recurring fixture here. One thread_id, three chips:

    11:46  suresh@fracktal.in          {FYI}
    12:21  chimexsales@chime.co.in     {Receipt}   ← moved OUT of the inbox
    12:30  suresh@fracktal.in          {Done}

Three defects conspired, and each has a test wall here:

1. The thread arbitration ran only when the per-message match happened to pick
   a conversation rule — exactly backwards, because the messages that most
   need thread context are the ones that DON'T look conversational in
   isolation (the invoice copy; a bare "ok noted" that matches nothing).
   Now the trigger is the THREAD's state (``_thread_is_conversation``).

2. Even when arbitration won, the losing cleanup matches ran their actions
   anyway (``[determined, *non_conv]``) — that is what moved one bubble of a
   live conversation into the Receipt folder. Now they are returned flagged
   ``suppressed``, logged as SKIPPED for the History view, and the guard sits
   in ``_apply_and_log_match`` itself — the one choke point every apply path
   crosses — so no future caller can run one by accident.

3. Nothing repaired the damage: stale cleanup chips stayed on old messages and
   the moved message stayed moved. Reconciliation now sheds cleanup chips from
   statused threads, and ``_restore_conversation_messages`` undoes OUR OWN
   moves (and only ours — a user's re-filing always wins).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.routes.email.automation import replyzero as _rz
from gateway.routes.email.automation import runner as _rn


def _result(fetchone=None, fetchall=None, scalar=None):
    r = MagicMock()
    r.fetchone.return_value = fetchone
    r.fetchall.return_value = fetchall if fetchall is not None else []
    r.scalar.return_value = scalar
    return r


def _db(seq):
    db = AsyncMock()
    db.execute.side_effect = list(seq)
    return db


# ── the gate: what counts as a conversation ──────────────────────────────────


async def test_a_statused_thread_is_a_conversation_no_more_queries() -> None:
    """A NEEDS_REPLY/AWAITING/DONE row is a judgement already made; it outlives
    any one message. This is the cheapest exit, so it must not go on to count
    messages."""
    db = _db([_result(fetchone=(1,))])
    assert await _rz._thread_is_conversation(db, "acc", "t1") is True
    assert db.execute.await_count == 1
    assert "NEEDS_REPLY" in str(db.execute.await_args_list[0][0][0])


async def test_an_fyi_row_alone_proves_nothing() -> None:
    """FYI is ALSO the default stamp for "nothing matched" — 3,226 of the live
    account's 3,535 threads carry one, newsletters included. Treating it as
    proof of conversation made the gate classify virtually the whole mailbox
    as conversations: an LLM determination per repeat newsletter, and every
    Newsletter/Receipt label suppressed in favour of FYI. Caught in prod
    within minutes of deploy (0 messages harmed); pinned here so nobody
    "simplifies" the status filter back to EXISTS.

    The SQL filters FYI out, so an FYI-only thread returns NO row — and must
    fall through to the participation test, where a blast (external, no our-
    side message) stays bulk."""
    db = _db([
        _result(fetchone=None),  # FYI row filtered out by the status IN (…)
        _result(fetchone=SimpleNamespace(email_address="me@fracktal.in")),
        _result(fetchone=None),
        _result(fetchone=SimpleNamespace(n=3, ours=False)),  # blast thread
    ])
    assert await _rz._thread_is_conversation(db, "acc", "t1") is False


async def test_back_and_forth_with_our_side_is_a_conversation() -> None:
    db = _db([
        _result(fetchone=None),                                # no status row
        _result(fetchone=SimpleNamespace(email_address="me@fracktal.in")),
        _result(fetchone=None),                                # org domains
        _result(fetchone=SimpleNamespace(n=4, ours=True)),     # participation
    ])
    assert await _rz._thread_is_conversation(db, "acc", "t1") is True


async def test_a_single_message_is_not_a_conversation() -> None:
    """Every newsletter blast is a 1-message thread — the common case must
    stay on the cheap per-message path."""
    db = _db([
        _result(fetchone=None),
        _result(fetchone=SimpleNamespace(email_address="me@fracktal.in")),
        _result(fetchone=None),
        _result(fetchone=SimpleNamespace(n=1, ours=False)),
    ])
    assert await _rz._thread_is_conversation(db, "acc", "t1") is False


async def test_external_only_threads_are_not_conversations() -> None:
    """A cold-email drip shares a thread id and has ≥2 messages — but our side
    never wrote, so it is bulk. Otherwise every persistent salesperson would
    earn conversation treatment by following up with themselves."""
    db = _db([
        _result(fetchone=None),
        _result(fetchone=SimpleNamespace(email_address="me@fracktal.in")),
        _result(fetchone=None),
        _result(fetchone=SimpleNamespace(n=3, ours=False)),
    ])
    assert await _rz._thread_is_conversation(db, "acc", "t1") is False


# ── the authority: whoever spoke last is a fact, not a judgment ──────────────


def _authority_env(status="REPLY", our_side_last=True, confident=True):
    """Patches for a bare recompute_thread_status run."""
    ctx = SimpleNamespace(thread_id="t1", last_message_id="m9",
                          last_message_at=None, our_side_last=our_side_last,
                          has_external=True, thread_text="thread…")
    return (
        # The thread has a participant outside the member's own mailboxes, so
        # the authority asks the status (D-EM-27, EM-T8e-1 review round 1).
        patch.multiple(_rz, build_thread_context=AsyncMock(return_value=ctx),
                       _thread_is_self_only=AsyncMock(return_value=False)),
        patch.object(_rz, "_status_corrections_block",
                     AsyncMock(return_value="")),
        patch.object(_rz, "_llm_determine_thread_status",
                     AsyncMock(return_value=(status, confident))),
        patch.object(_rz, "_upsert_thread_status", AsyncMock()),
    )


async def test_replying_can_never_leave_you_owing_a_reply() -> None:
    """THE stale-backlog regression: 10 of 29 live NEEDS_REPLY rows were
    threads the user had ALREADY answered — the determiner said REPLY and the
    authority wrote it, so the digest repeated "you owe a reply" every morning.
    When our side sent last, the only honest open state is AWAITING, and the
    reason discloses the coercion."""
    env = _authority_env(status="REPLY", our_side_last=True)
    with env[0], env[1], env[2], env[3] as upsert:
        rz_status, label = await _rz.recompute_thread_status(
            AsyncMock(), "acc", "t1", trigger="outbound")
    assert (rz_status, label) == ("AWAITING", "Awaiting Reply")
    written_status = upsert.call_args[0][3]
    written_reason = upsert.call_args[0][6]
    assert written_status == "AWAITING"
    assert "we sent last" in written_reason


async def test_their_message_last_still_determines_reply() -> None:
    """The clamp is keyed on the REAL last speaker, not the trigger: when the
    other side spoke last, a REPLY determination stands."""
    env = _authority_env(status="REPLY", our_side_last=False)
    with env[0], env[1], env[2], env[3] as upsert:
        rz_status, _label = await _rz.recompute_thread_status(
            AsyncMock(), "acc", "t1", trigger="inbound")
    assert rz_status == "NEEDS_REPLY"
    assert upsert.call_args[0][3] == "NEEDS_REPLY"
    assert "we sent last" not in upsert.call_args[0][6]


async def test_done_after_our_last_word_is_not_coerced() -> None:
    """The clamp only forbids the impossible state — a DONE verdict on a thread
    we closed out stays DONE."""
    env = _authority_env(status="DONE", our_side_last=True)
    with env[0], env[1], env[2], env[3] as upsert:
        rz_status, _label = await _rz.recompute_thread_status(
            AsyncMock(), "acc", "t1", trigger="outbound")
    assert rz_status == "DONE"
    assert "we sent last" not in upsert.call_args[0][6]


# ── the resolver: single classification ──────────────────────────────────────


_DONE_RULE = {"id": "r-done", "name": "Done", "system_type": "DONE",
              "enabled": True}


def _resolver_env(status="DONE", target=_DONE_RULE, conversation=True):
    """Patches for a resolver run: gate answer, thread context, LLM, rule."""
    ctx = SimpleNamespace(thread_id="t1", last_message_id="m9",
                          last_message_at=None, our_side_last=True,
                          has_external=True, thread_text="thread…")
    return (
        patch.object(_rz, "_thread_is_conversation",
                     AsyncMock(return_value=conversation)),
        patch.object(_rz, "_load_assistant_about",
                     AsyncMock(return_value=("", ""))),
        patch.object(_rz, "build_thread_context",
                     AsyncMock(return_value=ctx)),
        patch.object(_rz, "_status_corrections_block",
                     AsyncMock(return_value="")),
        patch.object(_rz, "_llm_determine_thread_status",
                     AsyncMock(return_value=(status, True))),
        patch.object(_rz, "_conversation_rule_for_status",
                     AsyncMock(return_value=target)),
        patch.object(_rz, "_restore_conversation_messages", AsyncMock()),
    )


async def _run_resolver(matches, **kw):
    db = AsyncMock()
    db.execute.return_value = _result(
        fetchone=SimpleNamespace(email_address="me@x.com"))
    row = SimpleNamespace(thread_id="t1")
    env = _resolver_env(**{k: v for k, v in kw.items()
                           if k in ("status", "target", "conversation")})
    with env[0], env[1], env[2], env[3], env[4], env[5], env[6]:
        return await _rz.resolve_conversation_status_matches(
            db, "acc", row, matches, provider=kw.get("provider"))


async def test_an_invoice_inside_a_conversation_is_not_a_receipt() -> None:
    """THE regression. The per-message match said Receipt; the thread is a
    known conversation; the thread's status is the classification and Receipt
    comes back flagged, not live."""
    receipt = {"rule": {"id": "r-rec", "name": "Receipt"}, "reason": "invoice"}
    out = await _run_resolver([receipt])
    assert out[0]["rule"]["name"] == "Done"
    assert out[0]["source"] == "thread_status"
    assert out[1]["rule"]["name"] == "Receipt"
    assert out[1]["suppressed"] == "conversation"


async def test_a_message_matching_nothing_still_refreshes_the_thread() -> None:
    """A bare "ok noted" matches no rule. Before, that meant no arbitration and
    a thread status frozen at AWAITING forever; now the conversation is
    re-evaluated and the determined rule comes back as the (only) match."""
    out = await _run_resolver([])
    assert len(out) == 1
    assert out[0]["source"] == "thread_status"


async def test_bulk_mail_never_pays_for_a_thread_determination() -> None:
    receipt = {"rule": {"id": "r-rec", "name": "Receipt"}, "reason": "invoice"}
    out = await _run_resolver([receipt], conversation=False)
    assert out == [receipt]
    assert "suppressed" not in out[0]


async def test_determination_failure_degrades_to_per_message() -> None:
    """The LLM going down must not stop mail from being filed at all."""
    receipt = {"rule": {"id": "r-rec", "name": "Receipt"}, "reason": "invoice"}
    db = AsyncMock()
    db.execute.return_value = _result(
        fetchone=SimpleNamespace(email_address="me@x.com"))
    env = _resolver_env()
    with env[0], env[1], env[2], env[3], \
            patch.object(_rz, "_llm_determine_thread_status",
                         AsyncMock(side_effect=RuntimeError("llm down"))), \
            env[5], env[6]:
        out = await _rz.resolve_conversation_status_matches(
            db, "acc", SimpleNamespace(thread_id="t1"), [receipt])
    assert out == [receipt]


async def test_no_enabled_rule_for_the_status_degrades_too() -> None:
    receipt = {"rule": {"id": "r-rec", "name": "Receipt"}, "reason": "invoice"}
    out = await _run_resolver([receipt], target=None)
    assert out == [receipt]


# ── the choke-point guard: suppressed matches are logged, never applied ──────


def _msg_row():
    return SimpleNamespace(id="m1", provider_message_id="pm1", thread_id="t1",
                           subject="RFQ", from_address={"email": "v@x.com"})


async def test_a_suppressed_match_is_logged_skipped_and_runs_nothing() -> None:
    db = AsyncMock()
    actions = AsyncMock()
    match = {"rule": {"id": "r-rec", "name": "Receipt", "actions": []},
             "reason": "invoice", "suppressed": "conversation"}
    with patch.object(_rn, "_apply_rule_actions", actions):
        await _rn._apply_and_log_match(
            db, MagicMock(), _msg_row(), {"email": "v@x.com"}, {},
            match, True, "", "", "user", "acc-1")
    actions.assert_not_called()
    sql = str(db.execute.call_args[0][0])
    params = db.execute.call_args[0][1]
    assert "'SKIPPED'" in sql
    assert params["rname"] == "Receipt"
    assert "conversation" in params["reason"]


async def test_the_guard_is_in_the_choke_point_not_the_caller() -> None:
    """The runner loop could forget to filter; _apply_and_log_match must refuse
    on its own — the same shape as the pattern-write gate in rules._teach."""
    db = AsyncMock()
    match = {"rule": {"id": "r", "name": "R", "actions": [{}]},
             "suppressed": "conversation"}
    # Even asked to apply, with a real-looking provider, it must return after
    # the SKIPPED insert: one execute call, no action machinery touched.
    await _rn._apply_and_log_match(
        db, MagicMock(), _msg_row(), {}, {}, match, True, "", "", "u", "a")
    assert db.execute.await_count == 1


# ── move-back: undo OUR moves, never the user's ─────────────────────────────


def _moved_row(folder="receipt", labels=("Receipt",)):
    return SimpleNamespace(id="m2", provider_message_id="pm2",
                           folder=folder, move_labels=list(labels))


async def test_our_mid_conversation_move_is_undone() -> None:
    provider = AsyncMock()
    provider.move_to_folder.return_value = "pm2-new"
    db = _db([_result(fetchall=[_moved_row()]), _result()])
    await _rz._restore_conversation_messages(db, provider, "acc", "t1")
    provider.move_to_folder.assert_awaited_once_with("pm2", "inbox")
    upd = db.execute.await_args_list[1]
    assert "folder = 'inbox'" in str(upd[0][0])
    # Outlook re-keys on move — the new id must be persisted (the #100 lesson).
    assert upd[0][1]["pid"] == "pm2-new"


async def test_a_message_the_user_refiled_is_left_alone() -> None:
    """Current folder ≠ any folder our rules move to → the user put it there,
    and their filing beats our repair."""
    provider = AsyncMock()
    db = _db([_result(fetchall=[_moved_row(folder="suppliers")])])
    await _rz._restore_conversation_messages(db, provider, "acc", "t1")
    provider.move_to_folder.assert_not_called()


async def test_no_provider_means_no_move() -> None:
    """A local-only 'move' would be re-broken by the next sync — with no
    provider the restore must not even query."""
    db = AsyncMock()
    await _rz._restore_conversation_messages(db, None, "acc", "t1")
    db.execute.assert_not_called()


async def test_one_failed_restore_does_not_abort_the_rest() -> None:
    provider = AsyncMock()
    provider.move_to_folder.side_effect = [RuntimeError("404"), "pm3-new"]
    rows = [_moved_row(),
            SimpleNamespace(id="m3", provider_message_id="pm3",
                            folder="receipt", move_labels=["Receipt"])]
    db = _db([_result(fetchall=rows), _result()])
    await _rz._restore_conversation_messages(db, provider, "acc", "t1")
    assert provider.move_to_folder.await_count == 2


# ── reconciliation: a statused conversation sheds its cleanup chips ─────────


async def test_conversation_thread_sheds_stale_cleanup_chips() -> None:
    """The 11:46 {FYI} / 12:21 {Receipt} / 12:30 {Done} splinter, repaired at
    the same place the status labels are already made mutually exclusive."""
    rows = [
        SimpleNamespace(id="m1", provider_message_id="p1",
                        categories=["Receipt"], folder="inbox"),
        SimpleNamespace(id="m2", provider_message_id="p2",
                        categories=["FYI"], folder="inbox"),
        SimpleNamespace(id="m3", provider_message_id="p3",
                        categories=["Done"], folder="inbox"),
    ]
    db = _db([_result(fetchall=rows), _result(), _result()])
    provider = AsyncMock()
    await _rz._reconcile_thread_labels(db, provider, "acc", "t1", "Done")
    removed = [c[0][1]["rm"] for c in db.execute.await_args_list[1:]]
    assert ["Receipt"] in removed
    assert ["FYI"] in removed


async def test_bulk_threads_keep_their_cleanup_chips() -> None:
    """keep_label=None (no conversation status) must not start stripping
    Receipt chips off genuine receipt threads."""
    rows = [SimpleNamespace(id="m1", provider_message_id="p1",
                            categories=["Receipt"], folder="inbox")]
    db = _db([_result(fetchall=rows)])
    await _rz._reconcile_thread_labels(db, AsyncMock(), "acc", "t1", None)
    # one SELECT, no UPDATE — Receipt is not stale for a non-conversation
    assert db.execute.await_count == 1


# ── corrections reach the status determiner ─────────────────────────────────


async def test_corrections_are_rendered_for_the_status_prompt() -> None:
    guidance = {"": ["Finance threads from our own domain are FYI."],
                "r-reply": ["Vendors asking for POs need a reply."],
                "r-news": ["Zoho digests are newsletters."]}
    rules = [
        {"id": "r-reply", "name": "Reply", "system_type": "REPLY",
         "enabled": True},
        {"id": "r-news", "name": "Newsletter", "system_type": None,
         "enabled": True},
    ]
    db = AsyncMock()
    with patch("gateway.routes.email.automation.engine._load_rule_guidance",
               AsyncMock(return_value=guidance)), \
            patch("gateway.routes.email.automation.rules._load_rules",
                  AsyncMock(return_value=rules)):
        block = await _rz._status_corrections_block(db, "acc")
    assert "Finance threads from our own domain are FYI." in block
    assert "[Reply] Vendors asking for POs need a reply." in block
    # Cleanup-rule guidance has nothing to tell REPLY-vs-DONE — kept out.
    assert "Zoho" not in block
    assert block.startswith("\n\n")  # separates cleanly from the criteria


async def test_no_guidance_means_an_empty_block() -> None:
    db = AsyncMock()
    with patch("gateway.routes.email.automation.engine._load_rule_guidance",
               AsyncMock(return_value={})):
        assert await _rz._status_corrections_block(db, "acc") == ""


async def test_corrections_land_in_the_determiners_system_prompt() -> None:
    """End to end into the prompt string — the guidance-wiring lesson: a
    loader nothing consults would pass every unit test above and do nothing."""
    seen = {}

    async def fake_llm_json(model, messages, **kw):
        seen["sys"] = messages[0]["content"]
        return {"status": "DONE", "rationale": "x"}, "", model

    with patch.object(_rz, "_llm_json", fake_llm_json):
        await _rz._llm_determine_thread_status(
            "thread text", "me@x.com", "",
            user_sent_last=True,
            corrections="\n\nCORRECTIONS THE USER HAS MADE BEFORE:\n- FYI it.")
    assert "CORRECTIONS THE USER HAS MADE BEFORE" in seen["sys"]
    assert "- FYI it." in seen["sys"]


async def test_status_call_failures_never_block_determination() -> None:
    db = AsyncMock()
    with patch("gateway.routes.email.automation.engine._load_rule_guidance",
               AsyncMock(side_effect=RuntimeError("no table"))):
        assert await _rz._status_corrections_block(db, "acc") == ""
