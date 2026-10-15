"""WS-17 EM-G11c2 (PR-G4) — no background write without a member choice.

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.13.7.

R7 fence named here: ``email-background-writes``. One test (or one class) for
each gap of the slice:

* G1 ``test_notes_dispatch_on_gmail_only_drafts`` — ``auto_dispatch`` through
  a Gmail mailbox makes a draft and sends nothing. Outlook and the manual
  route send as on main (Q-GM-12 stays open).
* G2 ``test_missing_settings_row_is_off_for_gmail`` and
  ``test_missing_settings_row_keeps_outlook_on`` — ``rules.auto_run_on`` is
  the one reader. A missing row reads OFF for Gmail and ON for Outlook, for
  the rule run of a sync cycle and for ``GET /email/assistant/settings``.
* G3 ``test_reply_zero_obeys_auto_run`` — with Auto-run OFF the backfill
  writes status rows and no label, on each provider.
* G4 ``test_cleanup_sweep_obeys_auto_run`` — with Auto-run OFF the sweep of a
  sync cycle builds no provider and writes no label. A member sweep runs.
* G5 ``test_a_manual_rule_is_pending`` — a rule with ``automated=false``
  logs a PENDING row and changes nothing, and the approval applies it.
* G7 ``test_draft_cleanup_keeps_a_member_draft`` — a send trashes only the
  unchanged AI draft of the thread, never a draft that the member wrote.

**Hermetic halves** use ``tests/unit/_sql_match.py`` for each fake.
**R8 halves** run the real SQL on the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, as the role ``acb_app_h3rls``
(NOSUPERUSER, NOBYPASSRLS), under FORCE RLS.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_background_writes.py -v -rs
"""
from __future__ import annotations

import ast
import inspect
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

import acb_llm.key_store as key_store_mod
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.email import scheduler_hooks
from gateway.routes.email.automation import (
    assistant,
    cleanup,
    drafting,
    engine,
    replyzero,
    rules,
    runner,
)
from gateway.routes.email.core import _tenant_session
from gateway.routes.notes import dispatch
from sqlalchemy import text

from tests.unit._email_fakes import bind_db
from tests.unit._sql_match import hits
from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are fixtures, used by name, so the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
OWNER = "owner@em-g11c2.test"
ACC = "acc-g11c2"

#: The one reader's SQL, named by its join so no other read of
#: ``email_accounts`` answers it.
AUTO_RUN_SQL = "LEFT JOIN email_assistant_settings"


# ── hermetic doubles ─────────────────────────────────────────────────────────


class _Res:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def scalar(self) -> Any:
        row = self.fetchone()
        return row[0] if isinstance(row, tuple) else row


class _FakeDb:
    """Answers each statement by the first clause it names, through
    ``_sql_match.hits``, and records every statement with its parameters."""

    def __init__(self, answers: list[tuple[str, list[Any]]]) -> None:
        self.answers = answers
        self.sql: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, clause: Any, params: Any = None) -> _Res:
        sql = str(clause)
        self.sql.append((sql, dict(params or {})))
        for frag, rows in self.answers:
            if hits(sql, frag):
                return _Res(rows)
        return _Res([])

    def ran(self, frag: str) -> list[dict[str, Any]]:
        return [p for s, p in self.sql if hits(s, frag)]

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def close(self) -> None: ...


def _auto_run_row(provider: str, stored: bool | None) -> list[Any]:
    """The answer of ``rules.auto_run_on``. ``stored=None`` is a missing
    settings row."""
    return [SimpleNamespace(
        provider=provider, auto_run=bool(stored),
        settings_row=None if stored is None else ACC)]


# ── G2: the one reader ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("provider", "stored", "expected"), [
    ("gmail", None, False),       # a new Gmail mailbox: OFF until chosen
    ("microsoft", None, True),    # Outlook keeps the old read
    ("imap", None, True),
    ("gmail", True, True),
    ("gmail", False, False),
    ("microsoft", False, False),  # a stored OFF stops Outlook too
    ("microsoft", True, True),
])
async def test_auto_run_on_reads_the_stored_choice(provider, stored, expected) -> None:
    db = _FakeDb([(AUTO_RUN_SQL, _auto_run_row(provider, stored))])
    assert await rules.auto_run_on(db, ACC) is expected
    assert db.ran(AUTO_RUN_SQL) == [{"aid": ACC}]


async def test_auto_run_on_is_off_for_a_missing_mailbox() -> None:
    assert await rules.auto_run_on(_FakeDb([]), ACC) is False


def _calls_auto_run_on(fn: Any) -> bool:
    tree = ast.parse(inspect.getsource(fn).lstrip())
    return any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "auto_run_on"
               for n in ast.walk(tree))


def test_every_reader_goes_through_the_one_helper() -> None:
    """The rule run, the Reply Zero backfill, the sweep of a sync cycle and
    the settings GET read Auto-run through ``rules.auto_run_on`` only."""
    for fn in (scheduler_hooks.auto_run_rules_for_account,
               replyzero._maybe_classify_threads,
               cleanup._sweep_provider,
               assistant.get_assistant_settings):
        assert _calls_auto_run_on(fn), fn.__qualname__
    fn = ast.parse(inspect.getsource(
        scheduler_hooks.auto_run_rules_for_account).lstrip()).body[0]
    sql = [n.value for n in ast.walk(ast.Module(body=fn.body[1:], type_ignores=[]))
           if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not any("email_assistant_settings" in s for s in sql), (
        "the rule run reads auto_run with SQL of its own again")


async def test_a_new_gmail_mailbox_runs_no_rule_action(monkeypatch) -> None:
    """The sync-cycle rule run, hermetic: a Gmail mailbox with no settings
    row and an enabled rule runs nothing. Outlook runs as on main."""
    ran: list[str] = []

    async def _run(account_id, *_a):
        ran.append(account_id)

    monkeypatch.setattr(runner, "_run_rules_job", _run)
    for provider, expected in (("gmail", []), ("microsoft", [ACC])):
        ran.clear()
        db = _FakeDb([(AUTO_RUN_SQL, _auto_run_row(provider, None)),
                      ("FROM email_rules", [SimpleNamespace(x=1)])])
        monkeypatch.setattr(scheduler_hooks, "_tenant_session", bind_db(db))
        await scheduler_hooks.auto_run_rules_for_account(ACC)
        assert ran == expected, provider


# ── G1: the meeting-note dispatch ────────────────────────────────────────────


class _Mailbox:
    """A provider that records each send and each draft."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.drafts: list[dict[str, Any]] = []

    async def send_message(self, **kw: Any) -> str:
        self.sent.append(kw)
        return "msg-1"

    async def create_draft(self, **kw: Any) -> str:
        self.drafts.append(kw)
        return "draft-1"


def _notes_rig(monkeypatch, provider: str) -> _Mailbox:
    import gateway.routes.email.core as email_core

    box = _Mailbox()
    db = _FakeDb([
        ("FROM email_accounts WHERE user_id", [SimpleNamespace(id=ACC)]),
        ("SELECT provider FROM email_accounts", [SimpleNamespace(provider=provider)]),
        ("FROM email_assistant_settings", [SimpleNamespace(signature="")]),
    ])

    async def _db() -> _FakeDb:
        return db

    @asynccontextmanager
    async def _session(_db, _user, *, account_id=None, **_kw):
        assert account_id == ACC
        yield SimpleNamespace(provider=box)

    async def _draft(*_a):
        return {"subject": "The deck", "body": "Here is the deck."}

    async def _segments(*_a):
        return []

    monkeypatch.setattr(email_core, "_get_db", _db)
    monkeypatch.setattr(email_core, "provider_session", _session)
    monkeypatch.setattr(dispatch, "_draft_email", _draft)
    monkeypatch.setattr(dispatch, "_segment_texts", _segments)
    return box


def _note_action() -> Any:
    return SimpleNamespace(
        id="a-1", meeting_id="m-1", description="Send Sarah the deck",
        segment_ids=[], kind="email", payload={"email_to": "sarah@acme.test"})


def _note_meeting() -> Any:
    return SimpleNamespace(
        id="m-1", owner_email=OWNER, title="Weekly", start_at=None,
        summary_md="", attendees=[])


async def test_notes_dispatch_on_gmail_only_drafts(monkeypatch) -> None:
    """Acceptance: a meeting note with an action item for a Gmail user makes
    a Gmail draft and sends nothing."""
    box = _notes_rig(monkeypatch, "gmail")
    ref = await dispatch._dispatch_email(
        _note_action(), _note_meeting(), OWNER, OWNER, background=True)
    assert box.sent == [], "a background dispatch SENT from a Gmail mailbox"
    assert ref == "draft:draft-1"
    [draft] = box.drafts
    assert draft["to"] == ["sarah@acme.test"]
    assert draft["subject"] == "The deck"


@pytest.mark.parametrize(("provider", "background"), [
    ("microsoft", True),   # Outlook stays as on main (Q-GM-12)
    ("gmail", False),      # the manual route: the member clicked
])
async def test_the_other_dispatch_paths_send_as_on_main(
    monkeypatch, provider, background,
) -> None:
    box = _notes_rig(monkeypatch, provider)
    ref = await dispatch._dispatch_email(
        _note_action(), _note_meeting(), OWNER, OWNER, background=background)
    assert ref == "sent:msg-1"
    assert len(box.sent) == 1 and box.drafts == []


def _calls(fn: Any, name: str) -> list[ast.Call]:
    tree = ast.parse(inspect.getsource(fn).lstrip())
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", "")) == name]


def _background_kw(call: ast.Call) -> Any:
    for kw in call.keywords:
        if kw.arg == "background":
            return ast.literal_eval(kw.value) if isinstance(
                kw.value, ast.Constant) else ast.unparse(kw.value)
    return None


def test_only_auto_dispatch_is_a_background_dispatch() -> None:
    """``auto_dispatch`` passes ``background=True``. The manual route passes
    nothing, so it keeps the default False (the audit's scope line)."""
    [auto] = _calls(dispatch.auto_dispatch, "_dispatch")
    assert _background_kw(auto) is True
    for call in _calls(dispatch.dispatch_action, "_dispatch"):
        assert _background_kw(call) is None
    [email] = _calls(dispatch._dispatch, "_dispatch_email")
    assert _background_kw(email) == "background"


# ── G3: Reply Zero ───────────────────────────────────────────────────────────


def _rz_row(thread_id: str, folder: str) -> Any:
    return SimpleNamespace(
        thread_id=thread_id, id=f"m-{thread_id}", subject="S",
        from_address={"email": "a@b.test"}, to_addresses=[], cc_addresses=[],
        body_text="Can you help?", snippet="", folder=folder, received_at=None)


async def _run_backfill(monkeypatch, provider: str, stored: bool | None):
    """Drive ``_maybe_classify_threads`` over one sent and one inbox thread.
    Each step that asks a model is a double. The SQL is a ``_FakeDb``."""
    db = _FakeDb([
        (AUTO_RUN_SQL, _auto_run_row(provider, stored)),
        ("SELECT provider, credentials_encrypted FROM email_accounts",
         [SimpleNamespace(provider=provider, credentials_encrypted="blob")]),
        ("WITH latest AS", [_rz_row("t-sent", "sent"), _rz_row("t-in", "inbox")]),
    ])
    seen: dict[str, list[Any]] = {"mark": [], "built": [], "reconcile": [],
                                  "project": []}

    async def _mark(account_id, thread_id, *a, **kw):
        seen["mark"].append(kw.get("labels", True))

    class _Prov:
        async def authenticate(self) -> bool:
            return True

        def credentials_dirty(self) -> bool:
            return False

    def _build(name, creds):
        seen["built"].append(name)
        return _Prov()

    async def _reconcile(*a, **kw):
        seen["reconcile"].append(a[3])

    async def _project(_db, _aid, row, _matches):
        seen["project"].append(row.thread_id)
        return "Reply"

    plan = SimpleNamespace(first=None, match=None, resolve=True)
    match = [{"rule": {"name": "Reply", "automated": True}, "reason": "r"}]
    monkeypatch.setattr(replyzero, "_tenant_session", bind_db(db))
    monkeypatch.setattr(replyzero, "_load_assistant_about",
                        AsyncMock(return_value=("", "")))
    monkeypatch.setattr(replyzero, "resolve_self", AsyncMock(
        return_value=SimpleNamespace(address="me@x.test", self_addresses=[])))
    monkeypatch.setattr(replyzero, "resolve_org_domains",
                        AsyncMock(return_value=set()))
    monkeypatch.setattr(replyzero, "_status_backoff_on", lambda: False)
    monkeypatch.setattr(replyzero, "_mark_thread_replied", _mark)
    monkeypatch.setattr(replyzero, "_attachment_summaries",
                        AsyncMock(return_value={}))
    monkeypatch.setattr(replyzero, "_instantiate_provider", _build)
    monkeypatch.setattr(replyzero, "_reconcile_thread_labels", _reconcile)
    monkeypatch.setattr(replyzero, "project_reply_status_from_matches", _project)
    monkeypatch.setattr(replyzero, "skip_rule_match", lambda *a, **k: False)
    monkeypatch.setattr(replyzero, "status_ask_needed", lambda *a, **k: False)
    monkeypatch.setattr(key_store_mod, "get_key_store", lambda: SimpleNamespace(
        decrypt=lambda blob: "{}"))
    monkeypatch.setattr(engine, "email_dict_from_row", lambda *a, **k: {})
    monkeypatch.setattr(engine, "read_classification", AsyncMock(return_value=plan))
    monkeypatch.setattr(engine, "ask_rule_match", AsyncMock(return_value=match))
    monkeypatch.setattr(engine, "resolve_classification",
                        AsyncMock(return_value=match))
    await replyzero._maybe_classify_threads(ACC)
    return seen


@pytest.mark.parametrize("provider", ["gmail", "microsoft"])
async def test_reply_zero_obeys_auto_run(monkeypatch, provider) -> None:
    """Acceptance: with ``auto_run`` OFF, a sync cycle writes no label from
    Reply Zero, on Gmail and on Outlook. The status row is still written."""
    seen = await _run_backfill(monkeypatch, provider, stored=False)
    assert seen["project"] == ["t-in"], "the status for the UI was not written"
    assert seen["mark"] == [False], "the sent thread may still swap its labels"
    assert seen["built"] == [], "a provider was built with Auto-run OFF"
    assert seen["reconcile"] == [], "Reply Zero wrote a label with Auto-run OFF"


async def test_reply_zero_still_writes_labels_with_auto_run_on(monkeypatch) -> None:
    """The control: the same rig with Auto-run ON writes the label, so the
    test above can fail."""
    seen = await _run_backfill(monkeypatch, "microsoft", stored=True)
    assert seen["mark"] == [True]
    assert seen["built"] == ["microsoft"]
    assert seen["reconcile"] == ["t-in"]


async def test_a_gmail_mailbox_with_no_row_gets_no_reply_zero_label(monkeypatch) -> None:
    seen = await _run_backfill(monkeypatch, "gmail", stored=None)
    assert seen["reconcile"] == [] and seen["built"] == []


async def test_mark_thread_replied_without_labels_builds_no_provider(monkeypatch) -> None:
    db = _FakeDb([("SELECT email_address, provider, credentials_encrypted",
                   [SimpleNamespace(email_address="me@x.test", provider="gmail",
                                    credentials_encrypted="blob")])])
    built: list[str] = []
    monkeypatch.setattr(replyzero, "_tenant_session", bind_db(db))
    monkeypatch.setattr(replyzero, "_load_assistant_about",
                        AsyncMock(return_value=("", "")))
    monkeypatch.setattr(replyzero, "read_thread_status", AsyncMock(
        return_value=SimpleNamespace(self_only=True)))
    monkeypatch.setattr(replyzero, "write_thread_status", AsyncMock(
        return_value=("AWAITING_REPLY", "Awaiting Reply")))
    monkeypatch.setattr(replyzero, "_instantiate_provider",
                        lambda name, creds: built.append(name))
    monkeypatch.setattr(key_store_mod, "get_key_store", lambda: SimpleNamespace(
        decrypt=lambda blob: "{}"))
    await replyzero._mark_thread_replied(ACC, "t-1", labels=False)
    assert built == []
    await replyzero._mark_thread_replied(ACC, "t-1")
    assert built == ["gmail"], "the send route lost its label swap"


# ── G4: the cleanup sweep ────────────────────────────────────────────────────


def _sweep_rig(monkeypatch, provider: str, stored: bool | None):
    db = _FakeDb([(AUTO_RUN_SQL, _auto_run_row(provider, stored))])
    built: list[str] = []
    pushed: list[str] = []

    class _Prov:
        async def authenticate(self) -> bool:
            return True

    async def _load(_db, account_id):
        built.append(account_id)
        return _Prov(), MagicMock(), OWNER

    async def _page(_db, _aid, limit, offset=0, internal=frozenset()):
        return [] if pushed else [SimpleNamespace(
            id="m-1", provider_message_id="p-1", from_address={"email": "n@s.test"},
            subject="Hi", unsubscribe_link=None)]

    async def _push(_p, mid, _pmid, label):
        pushed.append(mid)
        return label

    monkeypatch.setattr(cleanup, "_tenant_session", bind_db(db))
    monkeypatch.setattr(cleanup, "_provider_for_account_any", _load)
    monkeypatch.setattr(cleanup, "_persist_rotated_creds", AsyncMock())
    monkeypatch.setattr(cleanup, "_uncategorized_inbox", _page)
    monkeypatch.setattr(cleanup, "_load_rule_patterns", AsyncMock(return_value={}))
    monkeypatch.setattr(cleanup, "_rule_label_by_id", AsyncMock(return_value={}))
    monkeypatch.setattr(cleanup, "_internal_domains", AsyncMock(return_value=set()))
    monkeypatch.setattr(cleanup, "_label_tallies", AsyncMock(
        return_value=({"n@s.test": {"Newsletter": 6}}, {})))
    monkeypatch.setattr(runner, "push_label", _push)
    monkeypatch.setattr(runner, "mirror_label", AsyncMock())
    return built, pushed


@pytest.mark.parametrize("provider", ["gmail", "microsoft"])
async def test_cleanup_sweep_obeys_auto_run(monkeypatch, provider) -> None:
    """Acceptance: with ``auto_run`` OFF, a sync cycle writes no label from
    the sweep, on Gmail and on Outlook."""
    built, pushed = _sweep_rig(monkeypatch, provider, stored=False)
    res = await cleanup.sweep_uncategorized(
        ACC, 100, dry_run=False, owner="scheduler", obey_auto_run=True)
    assert res.get("skipped") == "auto_run_off"
    assert built == [], "the sweep built a provider with Auto-run OFF"
    assert pushed == [], "the sweep wrote a label with Auto-run OFF"


async def test_the_sweep_runs_with_auto_run_on_and_for_a_member(monkeypatch) -> None:
    """The controls: Auto-run ON sweeps, and a member's own sweep (no
    ``obey_auto_run``) sweeps with Auto-run OFF, because the click chose."""
    built, pushed = _sweep_rig(monkeypatch, "gmail", stored=True)
    await cleanup.sweep_uncategorized(
        ACC, 100, dry_run=False, owner="scheduler", obey_auto_run=True)
    assert built == [ACC] and pushed == ["m-1"]
    built, pushed = _sweep_rig(monkeypatch, "gmail", stored=False)
    await cleanup.sweep_uncategorized(ACC, 100, dry_run=False, owner="member")
    assert built == [ACC] and pushed == ["m-1"]


def test_the_sync_cycle_sweep_obeys_auto_run() -> None:
    [call] = _calls(scheduler_hooks.process_new_mail, "sweep_uncategorized")
    kws = {k.arg: k.value for k in call.keywords}
    assert isinstance(kws.get("obey_auto_run"), ast.Constant)
    assert kws["obey_auto_run"].value is True
    for fn in (cleanup._sweep_job, cleanup._backfill_and_clean_job,
               cleanup.auto_categorize_inbox):
        for c in _calls(fn, "sweep_uncategorized"):
            assert "obey_auto_run" not in {k.arg for k in c.keywords}, fn.__name__


# ── G5: a rule with automated=false proposes ────────────────────────────────


def _rule(automated: bool) -> dict[str, Any]:
    return {"id": "r-1", "name": "Receipts", "automated": automated,
            "actions": [{"type": "LABEL", "label": "Receipt"},
                        {"type": "ARCHIVE"}]}


def _msg_row() -> Any:
    return SimpleNamespace(id="m-1", provider_message_id="p-1",
                           thread_id="t-1", subject="Your receipt")


@pytest.mark.parametrize("automated", [False, True])
async def test_a_manual_rule_is_pending(monkeypatch, automated) -> None:
    """Acceptance: a rule with ``automated=false`` writes a PENDING row, and
    Gmail does not change. An automated rule applies as on main."""
    applied: list[Any] = []

    async def _apply(*a, **kw):
        applied.append(a[4])
        return ["LABEL", "ARCHIVE"]

    monkeypatch.setattr(runner, "_apply_rule_actions", _apply)
    db = _FakeDb([("SELECT provider_message_id FROM email_messages", [("p-1",)])])
    await runner._apply_and_log_match(
        db, MagicMock(), _msg_row(), {"email": "s@shop.test"}, {},
        {"rule": _rule(automated), "reason": "a receipt", "source": "ai"},
        True, "", "", OWNER, ACC, sole_match=False)
    [row] = db.ran("INSERT INTO email_executed_rules")
    if automated:
        assert applied and row["status"] == "APPLIED" and row["automated"] is True
    else:
        assert applied == [], "a manual rule changed the mailbox"
        assert row["status"] == "PENDING"
        assert row["automated"] is False
        assert json.loads(row["acts"]) == ["LABEL", "ARCHIVE"]


def test_a_manual_conversation_rule_writes_no_label() -> None:
    """The projection of a manual conversation rule keeps its label for the
    approval too (each caller asks ``conversation_match_proposes``)."""
    reply = {"rule": {"name": "Reply", "system_type": "TO_REPLY", "automated": False}}
    auto = {"rule": {"name": "Reply", "system_type": "TO_REPLY", "automated": True}}
    receipt = {"rule": {"name": "Receipt", "automated": False}}
    assert replyzero.conversation_match_proposes([reply]) is True
    assert replyzero.conversation_match_proposes([auto, receipt]) is False
    assert replyzero.conversation_match_proposes([]) is False
    for fn in (runner._run_rules_job, runner._project_thread_status_for_backfill,
               replyzero._maybe_classify_threads):
        assert "conversation_match_proposes(" in inspect.getsource(fn), fn.__name__


# ── G7: the draft cleanup after a send ──────────────────────────────────────

AI_TEXT = "Thanks, I will send the deck on Monday."


def _drafts_rig(monkeypatch, ai_text: str | None, drafts: list[Any]):
    db = _FakeDb([
        ("FROM email_ai_drafts", [SimpleNamespace(draft_text=ai_text)] if ai_text else []),
        ("FROM email_messages", drafts),
        ("FROM email_assistant_settings", [SimpleNamespace(signature="")]),
    ])
    trashed: list[str] = []

    class _Prov:
        async def trash_message(self, pmid: str) -> None:
            trashed.append(pmid)

    @asynccontextmanager
    async def _session(*_a, **_kw):
        yield SimpleNamespace(authed=True, provider=_Prov())

    monkeypatch.setattr(drafting, "_tenant_session", bind_db(db))
    monkeypatch.setattr(drafting, "provider_session", _session)
    return db, trashed


def _draft(pmid: str, body: str) -> Any:
    return SimpleNamespace(id=f"row-{pmid}", provider_message_id=pmid, body_text=body)


async def test_draft_cleanup_keeps_a_member_draft(monkeypatch) -> None:
    """Acceptance: a send keeps a draft that the member wrote in Gmail in the
    same thread. Only the unchanged AI draft goes."""
    db, trashed = _drafts_rig(monkeypatch, AI_TEXT, [
        _draft("p-ai", "  Thanks, I will send the deck   on Monday.\n"),
        _draft("p-own", "My own notes for the reply, not done yet."),
    ])
    await drafting._cleanup_thread_drafts(ACC, "t-1")
    assert trashed == ["p-ai"], "the cleanup trashed a draft the member wrote"
    assert [p["id"] for p in db.ran("DELETE FROM email_messages")] == ["row-p-ai"]


async def test_no_stored_ai_draft_trashes_nothing(monkeypatch) -> None:
    db, trashed = _drafts_rig(monkeypatch, None, [_draft("p-own", "Mine.")])
    await drafting._cleanup_thread_drafts(ACC, "t-1")
    assert trashed == [] and db.ran("DELETE FROM email_messages") == []


async def test_an_edited_ai_draft_stays(monkeypatch) -> None:
    _db, trashed = _drafts_rig(monkeypatch, AI_TEXT, [
        _draft("p-ai", AI_TEXT + " Also, the budget is attached.")])
    await drafting._cleanup_thread_drafts(ACC, "t-1")
    assert trashed == []


def test_both_send_routes_clean_before_learning() -> None:
    """``_learn_from_sent`` deletes the ``email_ai_drafts`` row, so the
    cleanup must run first. Starlette runs the tasks in order."""
    send_src = (_ROOT / "apps/services/gateway/gateway/routes/email/transport/send.py"
                ).read_text(encoding="utf-8")
    draft_src = inspect.getsource(drafting)
    for src in (send_src, draft_src):
        tail = src[src.index("_cleanup_thread_drafts, req.account_id") - 200:]
        first = tail.index("_cleanup_thread_drafts, req.account_id")
        assert first < tail.index("_learn_from_sent, req.account_id")


# ── fix round 1, P1: a first settings row keeps the effective Auto-run ────

_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"


def _settings_inserts() -> list[tuple[str, str]]:
    """Each SQL string in the gateway code that inserts a settings row."""
    found: list[tuple[str, str]] = []
    for path in sorted(_GATEWAY.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str)                     and "INSERT INTO email_assistant_settings" in " ".join(
                        node.value.split()):
                found.append((path.name, node.value))
    return found


def test_every_settings_insert_names_auto_run() -> None:
    """The column default of ``auto_run`` is false. An INSERT that omits it
    makes a first row that turns OFF the rules, the Reply Zero labels and the
    sweep of an Outlook mailbox. So each INSERT must name ``auto_run``, and
    the writer must pass what ``rules.auto_run_on`` reads (R7 fence)."""
    inserts = _settings_inserts()
    assert len(inserts) >= 2, inserts  # the PUT and the writing style
    for name, sql in inserts:
        cols = sql[sql.index("(") + 1:sql.index(")")]
        assert "auto_run" in cols, f"{name} inserts a settings row without auto_run"
    gen = inspect.getsource(assistant.generate_writing_style)
    assert '"auto": await auto_run_on(db, account_id)' in gen
    assert "auto_run = EXCLUDED" not in gen, "the style save must keep a stored choice"


# ── R8: the real SQL under FORCE RLS ─────────────────────────────────────────


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed")


def _seed_account(admin, *, org: str, owner: str, provider: str) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, is_default, organization_id) "
            "VALUES (:u, :p, :m, 'blob-r8', true, CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "p": provider,
             "m": f"box-{uuid.uuid4().hex[:8]}@em-g11c2.test",
             "o": org}).scalar_one())


def _seed_settings(admin, *, org: str, account_id: str, auto_run: bool) -> None:
    with admin.begin() as c:
        c.execute(text(
            "INSERT INTO email_assistant_settings (account_id, auto_run, "
            "organization_id) VALUES (CAST(:a AS uuid), :r, CAST(:o AS uuid))"),
            {"a": account_id, "r": auto_run, "o": org})


def _seed_rule(admin, *, org: str, account_id: str, automated: bool = True) -> str:
    with admin.begin() as c:
        rid = str(c.execute(text(
            "INSERT INTO email_rules (account_id, name, enabled, automated, "
            "organization_id) VALUES (CAST(:a AS uuid), 'Receipts', true, :au, "
            "CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "au": automated, "o": org}).scalar_one())
        c.execute(text(
            "INSERT INTO email_actions (rule_id, type, label, organization_id) "
            "VALUES (CAST(:r AS uuid), 'LABEL', 'Receipt', CAST(:o AS uuid))"),
            {"r": rid, "o": org})
        return rid


def _seed_message(admin, *, org: str, account_id: str, thread: str,
                  folder: str = "inbox", body: str = "hello") -> tuple[str, str]:
    pmid = f"pm-{uuid.uuid4().hex[:12]}"
    with admin.begin() as c:
        mid = str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, body_text, from_address, to_addresses, subject, "
            "organization_id) VALUES (CAST(:a AS uuid), :pm, :t, :f, :b, "
            "CAST(:fr AS jsonb), CAST(:to AS jsonb), 'hello', CAST(:o AS uuid)) "
            "RETURNING id"),
            {"a": account_id, "pm": pmid, "t": thread, "f": folder, "b": body,
             "fr": json.dumps({"email": "s@sender.test"}),
             "to": json.dumps([{"email": "me@em-g11c2.test"}]), "o": org},
        ).scalar_one())
    return mid, pmid


def _admin_read(admin, sql: str, **params: Any) -> list[Any]:
    with admin.connect() as c:
        return list(c.execute(text(sql), params).fetchall())


def _drop_owner(admin, owner: str) -> None:
    with admin.begin() as c:
        c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"), {"u": owner})


@asynccontextmanager
async def _as_tenant(p):
    token = bind_tenant(p.org_b)
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            yield
    finally:
        release_tenant(token)


@_DB_GATE
class TestTheOneReaderUnderForceRLS:

    async def test_missing_settings_row_is_off_for_gmail(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Acceptance: a new Gmail mailbox with no settings row runs no rule
        action. The GET answers OFF too, so a PUT of its body stores no ON."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-g11c2.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner, provider="gmail")
        _seed_rule(p.admin_engine, org=p.org_b, account_id=acc)
        ran: list[str] = []

        async def _run(account_id, *_a):
            ran.append(account_id)

        monkeypatch.setattr(runner, "_run_rules_job", _run)
        user = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        try:
            async with _as_tenant(p):
                async with _tenant_session() as db:
                    assert await rules.auto_run_on(db, acc) is False
                    assert await rules._account_provider(db, acc) == "gmail"
                await scheduler_hooks.auto_run_rules_for_account(acc)
                got = await assistant.get_assistant_settings(account_id=acc, user=user)
            assert ran == [], "a new Gmail mailbox ran its rules with no choice"
            assert got["auto_run"] is False
            _seed_settings(p.admin_engine, org=p.org_b, account_id=acc, auto_run=True)
            async with _as_tenant(p):
                await scheduler_hooks.auto_run_rules_for_account(acc)
                got = await assistant.get_assistant_settings(account_id=acc, user=user)
            assert ran == [acc], "the member's ON did not run the rules"
            assert got["auto_run"] is True
        finally:
            _drop_owner(p.admin_engine, owner)

    async def test_missing_settings_row_keeps_outlook_on(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Outlook acceptance item: with ``auto_run`` ON (here, the old
        missing-row ON), an Outlook mailbox runs its rules as on main. A
        stored OFF stops it."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-g11c2.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner,
                            provider="microsoft")
        _seed_rule(p.admin_engine, org=p.org_b, account_id=acc)
        ran: list[str] = []

        async def _run(account_id, *_a):
            ran.append(account_id)

        monkeypatch.setattr(runner, "_run_rules_job", _run)
        try:
            async with _as_tenant(p):
                async with _tenant_session() as db:
                    assert await rules.auto_run_on(db, acc) is True
                await scheduler_hooks.auto_run_rules_for_account(acc)
            assert ran == [acc]
            _seed_settings(p.admin_engine, org=p.org_b, account_id=acc, auto_run=False)
            async with _as_tenant(p):
                async with _tenant_session() as db:
                    assert await rules.auto_run_on(db, acc) is False
                await scheduler_hooks.auto_run_rules_for_account(acc)
            assert ran == [acc], "a stored OFF did not stop Outlook"
        finally:
            _drop_owner(p.admin_engine, owner)

    async def test_another_orgs_mailbox_reads_off(
        self, promoted, app_engine,  # noqa: F811
    ):
        """FORCE RLS: a mailbox of org A reads as missing for org B, so the
        helper answers OFF and nothing runs."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-g11c2.test"
        acc = _seed_account(p.admin_engine, org=p.org_a, owner=owner,
                            provider="microsoft")
        try:
            async with _as_tenant(p), _tenant_session() as db:
                assert await rules.auto_run_on(db, acc) is False
        finally:
            _drop_owner(p.admin_engine, owner)

    async def test_the_sync_cycle_sweep_stops_on_gmail_with_no_row(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-g11c2.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner, provider="gmail")
        built: list[str] = []

        async def _load(_db, account_id):
            built.append(account_id)
            raise AssertionError("the sweep built a provider")

        monkeypatch.setattr(cleanup, "_provider_for_account_any", _load)
        try:
            async with _as_tenant(p):
                res = await cleanup.sweep_uncategorized(
                    acc, 100, dry_run=False, owner="scheduler", obey_auto_run=True)
            assert res.get("skipped") == "auto_run_off", res
            assert built == []
        finally:
            _drop_owner(p.admin_engine, owner)


@_DB_GATE
class TestAManualRuleUnderForceRLS:

    async def test_the_proposal_is_pending_and_the_approval_applies_it(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-g11c2.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner, provider="gmail")
        rid = _seed_rule(p.admin_engine, org=p.org_b, account_id=acc, automated=False)
        mid, pmid = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                                  thread="t-r8")
        applied: list[Any] = []

        async def _apply(*a, **kw):
            applied.append([x["type"] for x in a[4]])
            return ["LABEL"]

        class _Prov:
            async def authenticate(self) -> bool:
                return True

            def credentials_dirty(self) -> bool:
                return False

        monkeypatch.setattr(runner, "_apply_rule_actions", _apply)
        monkeypatch.setattr(runner, "_instantiate_provider", lambda n, c: _Prov())
        monkeypatch.setattr(key_store_mod, "get_key_store", lambda: SimpleNamespace(
            decrypt=lambda blob: "{}"))
        user = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        try:
            async with _as_tenant(p), _tenant_session() as db:
                [rule] = await rules._load_rules(db, acc)
                assert rule["automated"] is False
                row = SimpleNamespace(id=mid, provider_message_id=pmid,
                                      thread_id="t-r8", subject="hello")
                await runner._apply_and_log_match(
                    db, _Prov(), row, {"email": "s@sender.test"}, {},
                    {"rule": rule, "reason": "a receipt", "source": "ai"},
                    True, "", "", owner, acc)
            assert applied == [], "a manual rule changed the mailbox"
            [(eid, status, automated, acts)] = _admin_read(
                p.admin_engine,
                "SELECT id, status, automated, actions_taken FROM "
                "email_executed_rules WHERE account_id = CAST(:a AS uuid)", a=acc)
            assert (status, automated, acts) == ("PENDING", False, ["LABEL"])
            async with _as_tenant(p):
                res = await runner.approve_execution(str(eid), user=user)
            assert res["status"] == "APPLIED" and applied == [["LABEL"]]
            [(status,)] = _admin_read(
                p.admin_engine, "SELECT status FROM email_executed_rules "
                "WHERE id = CAST(:e AS uuid)", e=str(eid))
            assert status == "APPLIED"
            assert rid
        finally:
            _drop_owner(p.admin_engine, owner)


@_DB_GATE
class TestTheDraftCleanupUnderForceRLS:

    async def test_a_send_keeps_the_members_own_draft(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-g11c2.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner, provider="gmail")
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc, thread="t-d")
        ai_id, ai_pmid = _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                                       thread="t-d", folder="drafts", body=AI_TEXT)
        own_id, _own_pmid = _seed_message(
            p.admin_engine, org=p.org_b, account_id=acc, thread="t-d",
            folder="drafts", body="My own words, written in Gmail.")
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO email_ai_drafts (account_id, thread_id, draft_text, "
                "organization_id) VALUES (CAST(:a AS uuid), 't-d', :t, "
                "CAST(:o AS uuid))"), {"a": acc, "t": AI_TEXT, "o": p.org_b})
        trashed: list[str] = []

        class _Prov:
            async def trash_message(self, pid: str) -> None:
                trashed.append(pid)

        @asynccontextmanager
        async def _session(*_a, **_kw):
            yield SimpleNamespace(authed=True, provider=_Prov())

        monkeypatch.setattr(drafting, "provider_session", _session)
        try:
            async with _as_tenant(p):
                await drafting._cleanup_thread_drafts(acc, "t-d")
            assert trashed == [ai_pmid]
            left = {str(r[0]) for r in _admin_read(
                p.admin_engine, "SELECT id FROM email_messages WHERE "
                "account_id = CAST(:a AS uuid) AND folder = 'drafts'", a=acc)}
            assert left == {own_id}, "the member's own draft was removed"
            assert ai_id not in left
        finally:
            _drop_owner(p.admin_engine, owner)


@_DB_GATE
class TestTheWritingStyleKeepsAutoRun:

    async def test_generating_a_writing_style_keeps_outlook_on(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Fix round 1 (P1): an Outlook mailbox with no settings row, then
        "Generate writing style". The new row stores ON, so the rules, the
        Reply Zero labels and the sweep go on as before."""
        import acb_memory

        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-g11c2.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner,
                            provider="microsoft")
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc, thread="t-s",
                      folder="sent", body="Thanks, see you on Monday. Ravi")
        monkeypatch.setattr(assistant, "_llm_writing_style",
                            AsyncMock(return_value="Short and warm."))
        monkeypatch.setattr(acb_memory, "add_memories_background", AsyncMock())
        user = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        try:
            async with _as_tenant(p):
                async with _tenant_session() as db:
                    assert await rules.auto_run_on(db, acc) is True
                await assistant.generate_writing_style(account_id=acc, user=user)
                async with _tenant_session() as db:
                    assert await rules.auto_run_on(db, acc) is True, (
                        "Generate writing style turned Auto-run OFF")
            [(stored, style)] = _admin_read(
                p.admin_engine, "SELECT auto_run, writing_style FROM "
                "email_assistant_settings WHERE account_id = CAST(:a AS uuid)", a=acc)
            assert stored is True and style == "Short and warm."
        finally:
            _drop_owner(p.admin_engine, owner)

    async def test_a_stored_off_stays_off(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        import acb_memory

        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-g11c2.test"
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=owner,
                            provider="microsoft")
        _seed_settings(p.admin_engine, org=p.org_b, account_id=acc, auto_run=False)
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc, thread="t-s",
                      folder="sent", body="Thanks. Ravi")
        monkeypatch.setattr(assistant, "_llm_writing_style",
                            AsyncMock(return_value="Short."))
        monkeypatch.setattr(acb_memory, "add_memories_background", AsyncMock())
        user = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        try:
            async with _as_tenant(p):
                await assistant.generate_writing_style(account_id=acc, user=user)
            [(stored,)] = _admin_read(
                p.admin_engine, "SELECT auto_run FROM email_assistant_settings "
                "WHERE account_id = CAST(:a AS uuid)", a=acc)
            assert stored is False
        finally:
            _drop_owner(p.admin_engine, owner)
