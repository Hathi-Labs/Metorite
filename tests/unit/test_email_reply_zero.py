"""Unit tests for Reply Zero — now a PROJECTION of the rules pipeline.

Reply Zero no longer runs a parallel needs-reply classifier. Instead:
  * the deterministic pre-filter (``_is_reply_candidate`` / ``_gate_conversation_rules``)
    keeps no-reply / mass / broadcast mail out of the conversation-status rules;
  * the rule engine's match is projected to a stored status
    (``project_reply_status_from_matches``);
  * ``_maybe_classify_threads`` is a best-effort BACKFILL that reuses the engine.
DB + engine mocked.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.routes import email as m

from tests.unit._email_fakes import bind_db

_eng = m.automation.engine
_rz = m.automation.replyzero


def _result(*, fetchone=None, fetchall=None):
    res = MagicMock()
    res.fetchone.return_value = fetchone
    res.fetchall.return_value = fetchall if fetchall is not None else []
    return res


def test_settings_has_follow_up_days_default_off() -> None:
    s = m.AssistantSettingsModel(account_id="acc-1")
    assert s.follow_up_days == 0


# ── Deterministic reply-candidate gate ───────────────────────────────────────

async def test_gate_blocks_no_reply_sender_without_db() -> None:
    db = AsyncMock()
    allowed, why = await _eng._is_reply_candidate(
        db, "acc-1", {"from": "noreply@shop.com"})
    assert allowed is False
    assert why == "no_reply_sender"
    db.execute.assert_not_called()  # short-circuits before any DB hit


async def test_gate_blocks_broadcast_sender_never_replied() -> None:
    db = AsyncMock()
    db.execute.side_effect = [
        _result(fetchone=None),                  # no List-Unsubscribe
        _result(fetchone=SimpleNamespace(c=10)),  # 10 received
        _result(fetchone=None),                  # never replied
    ]
    allowed, why = await _eng._is_reply_candidate(
        db, "acc-1", {"from": "team@company.com"})
    assert allowed is False
    assert why == "reply_history_threshold"


async def test_gate_allows_sender_the_user_has_replied_to() -> None:
    db = AsyncMock()
    db.execute.side_effect = [
        _result(fetchone=None),                  # no List-Unsubscribe
        _result(fetchone=SimpleNamespace(c=10)),  # 10 received
        _result(fetchone=SimpleNamespace(x=1)),   # has replied before
    ]
    allowed, _why = await _eng._is_reply_candidate(
        db, "acc-1", {"from": "colleague@company.com"})
    assert allowed is True


async def test_gate_allows_low_volume_sender() -> None:
    db = AsyncMock()
    db.execute.side_effect = [
        _result(fetchone=None),                 # no List-Unsubscribe
        _result(fetchone=SimpleNamespace(c=2)),  # only 2 received → replied query skipped
    ]
    allowed, _why = await _eng._is_reply_candidate(
        db, "acc-1", {"from": "person@company.com"})
    assert allowed is True


def test_gate_drops_only_conversation_rules_when_blocked() -> None:
    rules = [
        {"id": "1", "name": "Reply", "system_type": None},
        {"id": "2", "name": "Awaiting Reply", "system_type": None},
        {"id": "3", "name": "Newsletter", "system_type": None},
        {"id": "4", "name": "Custom rule", "system_type": None},
    ]
    kept = {r["name"] for r in _eng._gate_conversation_rules(rules, allowed=False)}
    assert "Reply" not in kept and "Awaiting Reply" not in kept
    assert "Newsletter" in kept and "Custom rule" in kept
    # allowed → untouched
    assert _eng._gate_conversation_rules(rules, allowed=True) == rules


# ── Projecting a rule match to a stored Reply Zero status ─────────────────────

async def test_project_status_maps_rule_to_status_with_priority() -> None:
    recorded: list[tuple[str, str]] = []
    db = AsyncMock()
    row = SimpleNamespace(thread_id="t1", id="m1", received_at=None)

    def rec(_db, _aid, tid, status, *_a, **_kw):
        recorded.append((tid, status))

    with patch.object(_rz, "_upsert_thread_status", AsyncMock(side_effect=rec)):
        # Reply rule → NEEDS_REPLY
        await _rz.project_reply_status_from_matches(
            db, "acc-1", row, [{"rule": {"name": "Reply"}, "reason": "asks"}])
        # No conversation rule (Newsletter) → FYI (kept out of Reply)
        await _rz.project_reply_status_from_matches(
            db, "acc-1", row, [{"rule": {"name": "Newsletter"}}])
        # No match at all → FYI
        await _rz.project_reply_status_from_matches(db, "acc-1", row, [])
        # REPLY beats AWAITING_REPLY/FYI when several match
        await _rz.project_reply_status_from_matches(
            db, "acc-1", row,
            [{"rule": {"name": "FYI"}}, {"rule": {"name": "Reply"}}])

    assert recorded == [
        ("t1", "NEEDS_REPLY"), ("t1", "FYI"), ("t1", "FYI"), ("t1", "NEEDS_REPLY")]


async def test_reconcile_thread_labels_enforces_single_status() -> None:
    rows = [
        SimpleNamespace(id="m1", provider_message_id="p1", folder="inbox",
                        categories=["Reply", "Follow-up"]),
        SimpleNamespace(id="m2", provider_message_id="p2", folder="inbox",
                        categories=["Awaiting Reply"]),
        SimpleNamespace(id="m3", provider_message_id="p3", folder="sent",
                        categories=[]),
    ]
    db = AsyncMock()
    db.execute.return_value = _result(fetchall=rows)
    calls: list[tuple[str, tuple, tuple]] = []

    async def set_labels(pmid, add=None, remove=None):
        calls.append((pmid, tuple(add or []), tuple(remove or [])))

    provider = AsyncMock()
    provider.set_labels.side_effect = set_labels

    await _rz._reconcile_thread_labels(db, provider, "acc", "t1", "Done")

    removed = {pmid: rem for pmid, _add, rem in calls if rem}
    added = {pmid: add for pmid, add, _rem in calls if add}
    # Every OTHER conversation label + Follow-up cleared (keep != Awaiting Reply).
    assert "Reply" in removed["p1"] and "Follow-up" in removed["p1"]
    assert "Awaiting Reply" in removed["p2"]
    # The new status label lands on the latest inbound message (m2).
    assert added["p2"] == ("Done",)
    # Sent message untouched.
    assert "p3" not in removed and "p3" not in added


async def test_reconcile_thread_labels_keeps_follow_up_while_awaiting() -> None:
    rows = [
        SimpleNamespace(id="m1", provider_message_id="p1", folder="inbox",
                        categories=["Reply", "Follow-up"]),
    ]
    db = AsyncMock()
    db.execute.return_value = _result(fetchall=rows)
    calls: list[tuple[str, tuple, tuple]] = []

    async def set_labels(pmid, add=None, remove=None):
        calls.append((pmid, tuple(add or []), tuple(remove or [])))

    provider = AsyncMock()
    provider.set_labels.side_effect = set_labels

    await _rz._reconcile_thread_labels(
        db, provider, "acc", "t1", "Awaiting Reply")

    removed = {pmid: rem for pmid, _add, rem in calls if rem}
    # "Reply" cleared, but "Follow-up" KEPT because the thread is awaiting.
    assert "Reply" in removed["p1"]
    assert "Follow-up" not in removed.get("p1", ())


async def test_project_status_respects_system_type_over_name() -> None:
    recorded: list[tuple[str, str]] = []
    db = AsyncMock()
    row = SimpleNamespace(thread_id="t9", id="m9", received_at=None)

    def rec(_d, _a, tid, st, *_rest, **_kw):
        recorded.append((tid, st))

    with patch.object(_rz, "_upsert_thread_status", AsyncMock(side_effect=rec)):
        await _rz.project_reply_status_from_matches(
            db, "acc-1", row,
            [{"rule": {"name": "Renamed rule", "system_type": "AWAITING_REPLY"}}])
    assert recorded == [("t9", "AWAITING")]


# ── Full-thread conversation-status determination (inbox-zero parity) ────────

async def test_resolve_passthrough_for_bulk_threads() -> None:
    """Bulk mail keeps per-message classification, and spends no model call.

    The resolver may PEEK at thread state (that peek is how it now catches
    conversations the per-message match missed — the whole 2026-07-22 fix),
    but for a thread with no status row and no back-and-forth it must return
    the matches untouched and never reach the LLM."""
    db = AsyncMock()
    row = SimpleNamespace(thread_id="t1")
    matches = [{"rule": {"name": "Newsletter"}, "reason": "x"}]
    llm = AsyncMock()
    with patch.object(_rz, "_thread_is_conversation",
                      AsyncMock(return_value=False)), \
            patch.object(_rz, "_llm_determine_thread_status", llm):
        out = await _rz.resolve_conversation_status_matches(
            db, "acc", row, matches)
    assert out == matches
    llm.assert_not_called()


async def test_resolve_uses_full_thread_status_over_per_message_pick() -> None:
    db = AsyncMock()
    db.execute.side_effect = [
        _result(fetchone=SimpleNamespace(email_address="me@x.com")),  # acc email
        _result(fetchone=None),                            # org_domains (none)
        _result(fetchall=[SimpleNamespace(
            id="m1", from_address={"email": "a@b.com"}, subject="s",
            body_text="thanks", snippet="", folder="inbox",
            received_at=None)]),                           # thread messages
        _result(fetchall=[]),                              # attachments (none)
    ]
    row = SimpleNamespace(thread_id="t1")
    actioned = {"id": "r1", "name": "Done", "system_type": None,
                "enabled": True}
    # Per-message pick said Reply; full-thread says the thread is concluded.
    matches = [{"rule": {"name": "Reply"}, "reason": "picked"}]
    with patch.object(_rz, "_load_assistant_about",
                      AsyncMock(return_value=("", ""))), \
            patch.object(_rz, "_llm_determine_thread_status",
                         AsyncMock(return_value=("DONE", True))), \
            patch.object(_rz, "_conversation_rule_for_status",
                         AsyncMock(return_value=actioned)):
        out = await _rz.resolve_conversation_status_matches(
            db, "acc", row, matches)
    assert len(out) == 1
    assert out[0]["rule"]["name"] == "Done"
    assert out[0]["source"] == "thread_status"


async def test_resolve_keeps_original_when_no_rule_for_status() -> None:
    db = AsyncMock()
    db.execute.side_effect = [
        _result(fetchone=SimpleNamespace(email_address="me@x.com")),
        _result(fetchone=None),                            # org_domains (none)
        _result(fetchall=[SimpleNamespace(
            id="m1", from_address={"email": "a@b.com"}, subject="s",
            body_text="b", snippet="", folder="inbox", received_at=None)]),
        _result(fetchall=[]),                              # attachments (none)
    ]
    row = SimpleNamespace(thread_id="t1")
    matches = [{"rule": {"name": "Reply"}, "reason": "picked"}]
    with patch.object(_rz, "_load_assistant_about",
                      AsyncMock(return_value=("", ""))), \
            patch.object(_rz, "_llm_determine_thread_status",
                         AsyncMock(return_value=("DONE", True))), \
            patch.object(_rz, "_conversation_rule_for_status",
                         AsyncMock(return_value=None)):  # no enabled Done rule
        out = await _rz.resolve_conversation_status_matches(
            db, "acc", row, matches)
    assert out == matches  # degrade to the per-message pick


# ── Backfill (reuses the engine, no parallel classifier) ─────────────────────

def _backfill_db(latest, existing):
    db = AsyncMock()
    db.execute.side_effect = [
        _result(fetchall=latest),
        _result(fetchall=existing),
        _result(fetchone=SimpleNamespace(email_address="me@x.com")),
        _result(fetchone=None),  # resolve_org_domains (none configured)
        _result(fetchall=[]),    # _attachment_summaries for inbound-gap rows
        # Provider lookup for the label reconcile. None → no provider, so the
        # reconcile corrects the local mirror only. These tests are about which
        # STATUS gets recorded; the label collapse has its own coverage in
        # test_email_thread_status_parity.py.
        _result(fetchone=None),
    ]
    return db


def _row(thread_id, mid, frm, folder, body=""):
    return SimpleNamespace(
        thread_id=thread_id, id=mid, subject="Subject",
        from_address={"email": frm}, to_addresses=[], cc_addresses=[],
        body_text=body, snippet="", folder=folder, received_at=None)


async def test_backfill_handles_outbound_reply_and_engine_for_inbound() -> None:
    latest = [
        _row("t1", "m1", "me@x.com", "sent"),
        _row("t2", "m2", "a@b.com", "inbox", body="Can you help?"),
    ]
    db = _backfill_db(latest, [])
    recorded: list[tuple[str, str]] = []

    def rec(_db, _aid, tid, status, *_a, **_kw):
        recorded.append((tid, status))

    to_reply_match = {"rule": {"name": "Reply", "system_type": None},
                      "reason": "asks a question", "source": "ai"}
    mark = AsyncMock()
    with patch.object(_rz, "_tenant_session", bind_db(db)), \
            patch.object(_rz, "_load_assistant_about",
                         AsyncMock(return_value=("", ""))), \
            patch.object(_rz, "_mark_thread_replied", mark), \
            patch.object(_rz, "resolve_conversation_status_matches",
                         AsyncMock(side_effect=lambda _d, _a, _r, ms,
                                   **_kw: ms)), \
            patch.object(_rz, "_upsert_thread_status",
                         AsyncMock(side_effect=rec)), \
            patch.object(_eng, "_match_email_to_rule",
                         AsyncMock(return_value=to_reply_match)), \
            patch.object(_rz, "_reconcile_thread_labels", AsyncMock()):
        await m._maybe_classify_threads("acc-1")

    # Sent-last thread → outbound-reply handling (AI status + label swap), the
    # SAME path as a CC reply — this is what gives native-client replies parity.
    mark.assert_awaited_once_with("acc-1", "t1")
    # Inbound thread → engine match → NEEDS_REPLY.
    assert dict(recorded)["t2"] == "NEEDS_REPLY"


async def test_backfill_leaves_overflow_unwritten_for_retry() -> None:
    n = _rz._REPLY_DETERMINE_CAP + 2
    latest = [_row(f"t{i}", f"m{i}", "me@x.com", "sent") for i in range(n)]
    db = _backfill_db(latest, [])
    writes: list[tuple[str, str]] = []
    mark = AsyncMock()
    with patch.object(_rz, "_tenant_session", bind_db(db)), \
            patch.object(_rz, "_load_assistant_about",
                         AsyncMock(return_value=("", ""))), \
            patch.object(_rz, "_mark_thread_replied", mark), \
            patch.object(_rz, "_upsert_thread_status",
                         AsyncMock(side_effect=lambda _d, _a, tid, st, *x, **k:
                                   writes.append((tid, st)))), \
            patch.object(_eng, "_match_email_to_rule", AsyncMock(return_value=None)):
        await m._maybe_classify_threads("acc-1")

    # Newest _REPLY_DETERMINE_CAP sent threads get the full AI determination; the
    # rest are LEFT UNWRITTEN (no blind AWAITING) so they retry next cycle — the
    # fix for "concluded replies stuck showing Awaiting".
    assert mark.await_count == _rz._REPLY_DETERMINE_CAP
    assert writes == []


async def test_backfill_reprocesses_provisional_auto_thread() -> None:
    # Unchanged latest message, but the stored status is a provisional LLM
    # fallback ("· auto") — it must be re-determined, not left stuck on a guess.
    latest = [_row("t5", "m5", "me@x.com", "sent")]
    existing = [SimpleNamespace(
        thread_id="t5", last_message_id="m5",
        reason="Replied — AWAITING_REPLY · auto")]
    db = _backfill_db(latest, existing)
    mark = AsyncMock()
    with patch.object(_rz, "_tenant_session", bind_db(db)), \
            patch.object(_rz, "_load_assistant_about",
                         AsyncMock(return_value=("", ""))), \
            patch.object(_rz, "_mark_thread_replied", mark), \
            patch.object(_rz, "_upsert_thread_status", AsyncMock()), \
            patch.object(_eng, "_match_email_to_rule", AsyncMock(return_value=None)):
        await m._maybe_classify_threads("acc-1")
    mark.assert_awaited_once_with("acc-1", "t5")


# ── Determiner hardening: tail-clip, confident-flag, outbound detection ───────

def test_clip_thread_keeps_tail_with_last_message() -> None:
    sep = "\n\n---\n\n"
    older = [f"From: a\nSubject: s\n{'x' * 1000}" for _ in range(10)]
    last = "From: me@x.com (you sent)\nSubject: s\nTHE_LAST_REPLY"
    thread = sep.join([*older, last])
    clipped = _rz._clip_thread_for_prompt(thread, limit=3000)
    # The closing reply (what decides Awaiting vs Done) survives the clip…
    assert "THE_LAST_REPLY" in clipped
    # …and the OLDEST messages are the ones dropped, with an elision marker.
    assert clipped.startswith("[… earlier messages omitted …]")
    assert len(clipped) <= 3000 + 64
    # Short threads pass through untouched.
    assert _rz._clip_thread_for_prompt("short thread") == "short thread"


def test_fmt_thread_msg_marks_outbound_by_sender_not_just_folder() -> None:
    # A sent reply mirrored under a non-'sent' folder is still "you sent" when
    # the sender is the connected account — so the determiner sees the reply.
    mine = SimpleNamespace(from_address={"email": "me@x.com"}, subject="s",
                           body_text="hi", snippet="", folder="archive")
    assert "(you sent)" in _rz._fmt_thread_msg(mine, "me@x.com")
    theirs = SimpleNamespace(from_address={"email": "a@b.com"}, subject="s",
                             body_text="hi", snippet="", folder="inbox")
    assert "(you sent)" not in _rz._fmt_thread_msg(theirs, "me@x.com")
    # Without self_email, falls back to folder-only (unchanged legacy behaviour).
    assert "(you sent)" not in _rz._fmt_thread_msg(mine)


async def test_upsert_preserve_done_guards_status_in_sql() -> None:
    db = AsyncMock()
    await _rz._upsert_thread_status(
        db, "a", "t", "FYI", "m", None, "r", preserve_done=True)
    sql = str(db.execute.call_args[0][0])
    assert "CASE WHEN email_thread_status.status = 'DONE'" in sql
    assert "EXCLUDED.status <> 'NEEDS_REPLY'" in sql
    # Default (user actions / own reply) overwrites unconditionally.
    db2 = AsyncMock()
    await _rz._upsert_thread_status(db2, "a", "t", "FYI", "m", None, "r")
    sql2 = str(db2.execute.call_args[0][0])
    assert "CASE WHEN email_thread_status.status = 'DONE'" not in sql2
    assert "status = EXCLUDED.status" in sql2


async def test_project_status_preserves_done_on_automated_path() -> None:
    # The inbound re-projection must pass preserve_done=True so a trailing
    # notification can't silently re-open a thread the user marked Done.
    captured: dict[str, object] = {}
    db = AsyncMock()
    row = SimpleNamespace(thread_id="t1", id="m1", received_at=None)

    async def fake_upsert(_db, _aid, tid, status, *_a, **kw):
        captured["status"] = status
        captured["preserve_done"] = kw.get("preserve_done", False)

    with patch.object(_rz, "_upsert_thread_status", AsyncMock(side_effect=fake_upsert)):
        await _rz.project_reply_status_from_matches(
            db, "acc-1", row, [{"rule": {"name": "FYI"}}])
    assert captured["preserve_done"] is True


async def test_backfill_marks_fyi_when_no_conversation_rule_matches() -> None:
    latest = [_row("t3", "m3", "noreply@shop.com", "inbox", body="Thanks!")]
    db = _backfill_db(latest, [])
    recorded: list[tuple[str, str]] = []

    def rec(_db, _aid, tid, status, *_a, **_kw):
        recorded.append((tid, status))

    with patch.object(_rz, "_tenant_session", bind_db(db)), \
            patch.object(_rz, "_load_assistant_about",
                         AsyncMock(return_value=("", ""))), \
            patch.object(_rz, "_upsert_thread_status",
                         AsyncMock(side_effect=rec)), \
            patch.object(_eng, "_match_email_to_rule",
                         AsyncMock(return_value=None)), \
            patch.object(_rz, "_thread_is_conversation",
                         AsyncMock(return_value=False)), \
            patch.object(_rz, "_reconcile_thread_labels", AsyncMock()):
        # Nothing matched, and the thread is not a conversation (the gate is
        # patched False — a single noreply@ message IS the bulk case, and the
        # gate has its own tests) → recorded as FYI.
        await m._maybe_classify_threads("acc-1")
    assert dict(recorded)["t3"] == "FYI"


async def test_backfill_skips_unchanged_threads() -> None:
    latest = [_row("t4", "m4", "a@b.com", "inbox")]
    existing = [SimpleNamespace(
        thread_id="t4", last_message_id="m4", reason="Auto-classified")]
    db = _backfill_db(latest, existing)
    match = AsyncMock()
    with patch.object(_rz, "_tenant_session", bind_db(db)), \
            patch.object(_rz, "_load_assistant_about",
                         AsyncMock(return_value=("", ""))), \
            patch.object(_rz, "_upsert_thread_status", AsyncMock()), \
            patch.object(_eng, "_match_email_to_rule", match):
        await m._maybe_classify_threads("acc-1")
    match.assert_not_awaited()  # latest message unchanged → no engine cost


# ── Phase 2: thread-status authority (context builder + recompute) ───────────

def _msg(folder="inbox", email="a@b.com", body="hi", mid="m1"):
    return SimpleNamespace(
        id=mid, from_address={"email": email}, subject="s", body_text=body,
        snippet="", folder=folder, received_at=None)


def test_msg_scope_direction() -> None:
    # folder='sent' is authoritative for 'self'; otherwise by sender identity.
    assert _rz._msg_scope(_msg(folder="sent"), "me@acme.com") == "self"
    assert _rz._msg_scope(_msg(email="me@acme.com"), "me@acme.com") == "self"
    assert _rz._msg_scope(_msg(email="sales@acme.com"), "me@acme.com") == "internal"
    assert _rz._msg_scope(_msg(email="ext@other.com"), "me@acme.com") == "external"


def test_fmt_thread_msg_annotates_org() -> None:
    assert "(your organisation sent)" in _rz._fmt_thread_msg(
        _msg(email="sales@acme.com"), "me@acme.com")
    assert "(you sent)" in _rz._fmt_thread_msg(_msg(folder="sent"), "me@acme.com")
    assert "sent)" not in _rz._fmt_thread_msg(_msg(email="x@other.com"), "me@acme.com")


def test_fmt_thread_msg_renders_to_cc_date_attachments() -> None:
    import datetime as _dt
    inbound = SimpleNamespace(
        id="m1", from_address={"email": "cust@other.com", "name": "Cust"},
        to_addresses=[{"email": "me@acme.com", "name": "Me"}],
        cc_addresses=[{"email": "team@acme.com"}],
        subject="Q", body_text="hi", snippet="", folder="inbox",
        received_at=_dt.datetime(2026, 6, 30, tzinfo=_dt.timezone.utc))
    out = _rz._fmt_thread_msg(
        inbound, "me@acme.com", frozenset(),
        "Attachments: invoice.pdf (application/pdf)")
    assert "To: Me <me@acme.com>" in out
    assert "Cc: team@acme.com" in out
    assert "Date: 2026-06-30" in out
    assert "Attachments: invoice.pdf" in out
    # An OUTBOUND (you sent) message does NOT render To/Cc — only inbound does.
    sent = SimpleNamespace(
        id="m2", from_address={"email": "me@acme.com"},
        to_addresses=[{"email": "cust@other.com"}], cc_addresses=[],
        subject="Re", body_text="ok", snippet="", folder="sent", received_at=None)
    out2 = _rz._fmt_thread_msg(sent, "me@acme.com")
    assert "(you sent)" in out2 and "To:" not in out2


def test_core_email_context_helpers() -> None:
    from gateway.routes.email.core import _fmt_addr_list
    assert _fmt_addr_list([{"name": "A", "email": "a@x.com"}]) == "A <a@x.com>"
    assert _fmt_addr_list([{"email": "b@x.com"}]) == "b@x.com"
    assert _fmt_addr_list(None) == ""
    assert _fmt_addr_list("not-json") == ""


async def test_build_thread_context_marks_our_side_last() -> None:
    db = AsyncMock()
    db.execute.return_value = _result(fetchall=[
        _msg(folder="inbox", email="cust@other.com", mid="m1"),
        _msg(folder="sent", email="me@acme.com", mid="m2"),
    ])
    ctx = await _rz.build_thread_context(db, "acc", "t1", "me@acme.com")
    assert ctx is not None
    assert ctx.our_side_last is True       # last message is owner-sent
    assert ctx.has_external is True        # an external message is present
    assert ctx.last_message_id == "m2"
    assert "(you sent)" in ctx.thread_text


async def test_build_thread_context_external_last_and_org_counts() -> None:
    db = AsyncMock()
    # Last message is a TEAMMATE (same org) — our side acted, not external.
    db.execute.return_value = _result(fetchall=[
        _msg(folder="inbox", email="cust@other.com", mid="m1"),
        _msg(folder="inbox", email="sales@acme.com", mid="m2"),
    ])
    ctx = await _rz.build_thread_context(db, "acc", "t1", "me@acme.com")
    assert ctx.our_side_last is True       # org reply counts as our side
    assert "(your organisation sent)" in ctx.thread_text

    db.execute.return_value = _result(fetchall=[
        _msg(folder="inbox", email="cust@other.com", mid="m1")])
    ctx2 = await _rz.build_thread_context(db, "acc", "t1", "me@acme.com")
    assert ctx2.our_side_last is False


async def test_build_thread_context_pending_reply_and_empty() -> None:
    db = AsyncMock()
    db.execute.return_value = _result(fetchall=[
        _msg(folder="inbox", email="cust@other.com", mid="m1")])
    ctx = await _rz.build_thread_context(
        db, "acc", "t1", "me@acme.com",
        pending_reply=("Thanks, all set.", "Re: s"))
    assert ctx.our_side_last is True
    assert "Thanks, all set." in ctx.thread_text

    db.execute.return_value = _result(fetchall=[])
    assert await _rz.build_thread_context(db, "acc", "t1", "me@acme.com") is None


async def test_recompute_outbound_writes_and_may_move_done() -> None:
    db = AsyncMock()
    db.execute.return_value = _result(fetchall=[
        _msg(folder="sent", email="me@acme.com", mid="m2")])
    cap: dict[str, object] = {}

    async def fake_upsert(_db, _aid, tid, status, _mid, _mat, reason, **kw):
        cap.update(status=status, reason=reason,
                   preserve_done=kw.get("preserve_done"))

    det = AsyncMock(return_value=("DONE", True))
    with patch.object(_rz, "_llm_determine_thread_status", det), \
            patch.object(_rz, "_upsert_thread_status",
                         AsyncMock(side_effect=fake_upsert)):
        out = await _rz.recompute_thread_status(
            db, "acc", "t1", trigger="outbound", acc_email="me@acme.com")
    assert out == ("DONE", "Done")
    assert cap["status"] == "DONE"
    assert cap["reason"] == "Replied — DONE"
    assert cap["preserve_done"] is False        # owner reply may move DONE
    # user_sent_last derived True (last message is owner-sent).
    assert det.await_args.kwargs["user_sent_last"] is True


async def test_recompute_inbound_preserves_done_and_flags_fallback() -> None:
    db = AsyncMock()
    db.execute.return_value = _result(fetchall=[
        _msg(folder="inbox", email="cust@other.com", mid="m1")])
    cap: dict[str, object] = {}

    async def fake_upsert(_db, _aid, tid, status, _mid, _mat, reason, **kw):
        cap.update(reason=reason, preserve_done=kw.get("preserve_done"))

    det = AsyncMock(return_value=("AWAITING_REPLY", False))  # low confidence
    with patch.object(_rz, "_llm_determine_thread_status", det), \
            patch.object(_rz, "_upsert_thread_status",
                         AsyncMock(side_effect=fake_upsert)):
        await _rz.recompute_thread_status(
            db, "acc", "t1", trigger="inbound", acc_email="me@acme.com")
    assert cap["preserve_done"] is True          # inbound must not clobber DONE
    assert str(cap["reason"]).endswith("· auto")  # fallback tagged for re-check
    assert det.await_args.kwargs["user_sent_last"] is False


async def test_backfill_holds_no_session_across_a_model_call() -> None:
    """The backfill must not sit `idle in transaction` across an LLM call.

    `_mark_thread_replied` opens its OWN sessions and spends a full LLM
    determination in one. A session of the backfill held open across that call
    sits `idle in transaction` — holding ACCESS SHARE on every table it touched
    — for the length of the call, times up to `_REPLY_DETERMINE_CAP` sent
    threads.

    That is the shape that took production down on 2026-08-06: a parked session,
    a migration's ALTER TABLE queued behind its lock, and Postgres's FIFO lock
    queue then stalling every later reader of the table — including the one the
    send path makes.

    EM-T1b-2 replaced the commit before each call with phases: every
    `_tenant_session()` block closes before the first sent thread goes to
    `_mark_thread_replied`. So this asserts that NO block is open during each
    model call, which is stronger than "a commit came first".
    """
    latest = [
        _row("t1", "m1", "me@x.com", "sent"),
        _row("t2", "m2", "me@x.com", "sent"),
        _row("t3", "m3", "a@b.com", "archive"),
    ]
    db = _backfill_db(latest, [])
    state = {"open": 0, "opens": 0}
    seen: list[int] = []

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ts():
        state["open"] += 1
        state["opens"] += 1
        try:
            yield db
        finally:
            state["open"] -= 1

    async def _mark(*_a, **_kw) -> None:
        seen.append(state["open"])

    with patch.object(_rz, "_tenant_session", _ts), \
            patch.object(_rz, "_load_assistant_about",
                         AsyncMock(return_value=("", ""))), \
            patch.object(_rz, "_mark_thread_replied",
                         AsyncMock(side_effect=_mark)), \
            patch.object(_rz, "_upsert_thread_status", AsyncMock()), \
            patch.object(_rz, "_reconcile_thread_labels", AsyncMock()):
        await m._maybe_classify_threads("acc-1")

    assert seen == [0, 0], (
        f"a session was open during a model call: open counts {seen}"
    )
    # The read phase and the filed-FYI phase each opened a block of their own.
    assert state["opens"] == 2 and state["open"] == 0
