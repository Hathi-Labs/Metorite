"""WS-17 EM-G1 — the reclaim gate (D-EM-34, GM-2).

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.1, decision
D-EM-34 in §12.2, defect GM-2 in §12.1.

The re-key reclaim of ``persist.upsert_message`` moves the one row that holds
a Message-ID to a new provider id. Outlook needs it, because Graph gives a
message a new id when it moves to another folder. Gmail never changes an id,
and two Gmail messages can hold one Message-ID. Without the gate, those two
fold into one row, and the row swaps its id at each sync. So the reclaim now
runs only when the caller passes ``reclaim=True``, and each update-path caller
passes the ``REKEYS_MESSAGE_IDS`` attribute of its provider.

R7 fences named here:

* ``email-reclaim-gate`` (R8): two Gmail messages with one Message-ID keep two
  rows over two syncs, and an Outlook move keeps its one row.
* ``email-reclaim-default-off`` (R8 and hermetic): an upsert with no keyword
  does not reclaim, each keyword defaults to false, and only Outlook sets the
  attribute.
* ``email-reclaim-callers`` (hermetic, AST): each call of ``upsert_message``,
  ``_write_messages`` or ``_upsert_message`` in ``email_ingestion`` and
  ``gateway/routes/email`` names ``reclaim=``. Only an insert-only call
  (``on_conflict="nothing"``) is exempt. A companion test proves that the
  fence can fail.
* ``email-reclaim-caller-values`` (R8): ``_sync_account`` (the first import
  and the recurring sweep) and the "Load older" route pass the attribute of
  their provider, for a provider flagged true and for one flagged false.
* ``email-reclaim-every-outlook-path`` (R8): the REAL factory builds an
  Outlook provider (only the class it imports is swapped for one with no
  network). The first import, a recurring sweep, a deep sync with ``since``
  (Process past, cleanup, runner), a Resync and "Load older" each send
  ``reclaim=True``, and the moved mail keeps one row. It fails when a later
  edit wraps the provider, or branches the deep path to ``False``.

⚠️ Known limit EM-G1-f1, recorded in §12.3.1 and NOT proved correct here: the
Outlook reclaim folds a mail that a member sends to their own address. The
Sent Items copy and the Inbox copy have two Graph ids and one Message-ID, so
each sweep moves the one row between ``sent`` and ``inbox``. No test here
writes that case as correct.

**R8.** The real SQL against the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, as the role ``acb_app_h3rls``
(NOSUPERUSER, NOBYPASSRLS). The admin engine seeds and reads the rows.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_rekey_reclaim.py -v -rs
"""
from __future__ import annotations

import ast
import inspect
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest

pytest.importorskip("sqlalchemy")

import email_ingestion.persist as persist
import email_ingestion.scheduler as sched
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, clear_tenant, release_tenant
from email_ingestion.persist import upsert_message
from email_ingestion.providers.base import (
    BaseEmailProvider,
    EmailAddress,
    EmailMessage,
    SyncResult,
)
from email_ingestion.providers.gmail import GmailProvider
from email_ingestion.providers.imap import IMAPProvider
from email_ingestion.providers.outlook import OutlookProvider
from gateway.routes.email import core
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_scheduler_tenancy import (
    _assert_non_priv,
    _purge,
    _Store,
)

# ``promoted`` and ``app_engine`` are fixtures, used by name, so the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]

#: The two trees that hold the email ingest path. ``whatsapp_ingestion`` has
#: its own ``upsert_message`` with no reclaim, so the fence does not read it.
_SCAN_ROOTS = (
    _ROOT / "apps/services/email_ingestion",
    _ROOT / "apps/services/gateway/gateway/routes/email",
)

#: The three names of the update path. Each call names ``reclaim=``.
_UPSERT_NAMES = frozenset({"upsert_message", "_write_messages", "_upsert_message"})

_SCHED = "apps/services/email_ingestion/email_ingestion/scheduler.py"
_CORE = "apps/services/gateway/gateway/routes/email/core.py"
_FOLDERS = "apps/services/gateway/gateway/routes/email/transport/folders.py"
_INBOUND = "apps/services/email_ingestion/email_ingestion/inbound.py"

#: The update-path calls of today: (file, enclosing function, callee). The
#: fence must find each one, so a scan that reads nothing cannot pass.
_KNOWN_UPDATE_CALLS = {
    (_SCHED, "_write_messages", "upsert_message"),
    (_SCHED, "_run_import", "_write_messages"),
    (_SCHED, "_sync_cycle", "_write_messages"),
    (_CORE, "_upsert_message", "upsert_message"),
    (_FOLDERS, "backfill_folder", "_upsert_message"),
}

#: The insert-only call of today. It is exempt, and the fence must see it.
_KNOWN_INSERT_ONLY_CALLS = {(_INBOUND, "_persist_message", "upsert_message")}


# ── hermetic: the attribute and the defaults ─────────────────────────────────


def test_only_outlook_rekeys_its_ids():
    """D-EM-34. The base is false, so Gmail and IMAP inherit false. Only
    Outlook, whose ids change on a move, sets it."""
    assert BaseEmailProvider.REKEYS_MESSAGE_IDS is False
    assert GmailProvider.REKEYS_MESSAGE_IDS is False
    assert IMAPProvider.REKEYS_MESSAGE_IDS is False
    assert OutlookProvider.REKEYS_MESSAGE_IDS is True
    # Gmail and IMAP do not set their own value. They read the base.
    assert "REKEYS_MESSAGE_IDS" not in vars(GmailProvider)
    assert "REKEYS_MESSAGE_IDS" not in vars(IMAPProvider)


@pytest.mark.parametrize("fn", [
    persist.upsert_message, sched._write_messages, core._upsert_message,
], ids=["persist.upsert_message", "scheduler._write_messages",
        "core._upsert_message"])
def test_each_reclaim_keyword_is_keyword_only_and_false_by_default(fn):
    param = inspect.signature(fn).parameters["reclaim"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is False


# ── hermetic: the AST fence over the callers ─────────────────────────────────


def _callee(call: ast.Call) -> str:
    fn = call.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def _insert_only(call: ast.Call) -> bool:
    return any(
        kw.arg == "on_conflict" and isinstance(kw.value, ast.Constant)
        and kw.value.value == "nothing"
        for kw in call.keywords)


def _names_reclaim(call: ast.Call) -> bool:
    return any(kw.arg == "reclaim" for kw in call.keywords)


def _upsert_calls(source: str) -> list[tuple[str, str, ast.Call]]:
    """Each call of the three names, with its enclosing function."""
    found: list[tuple[str, str, ast.Call]] = []

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AsyncFunctionDef | ast.FunctionDef):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Call) and _callee(child) in _UPSERT_NAMES:
                found.append((owner, _callee(child), child))
            visit(child, owner)

    visit(ast.parse(source), "<module>")
    return found


def _offenders(source: str, label: str) -> list[str]:
    """The update-path calls in ``source`` that do not name ``reclaim=``."""
    return [
        f"{label}:{call.lineno} {owner} calls {name} without reclaim="
        for owner, name, call in _upsert_calls(source)
        if not _insert_only(call) and not _names_reclaim(call)
    ]


def _scanned_files() -> list[Path]:
    out: list[Path] = []
    for root in _SCAN_ROOTS:
        for path in root.rglob("*.py"):
            if set(path.parts) & {"tests", "__pycache__", ".venv"}:
                continue
            out.append(path)
    return sorted(out)


def test_each_upsert_caller_names_reclaim():
    """``email-reclaim-callers``. A caller that forgets the keyword gets the
    default, false, and an Outlook move then writes a second row. So every
    update-path call names it, and the reviewer sees the choice."""
    offenders: list[str] = []
    update_calls: set[tuple[str, str, str]] = set()
    insert_only: set[tuple[str, str, str]] = set()
    for path in _scanned_files():
        source = path.read_text(encoding="utf-8")
        label = path.relative_to(_ROOT).as_posix()
        offenders += _offenders(source, label)
        for owner, name, call in _upsert_calls(source):
            target = insert_only if _insert_only(call) else update_calls
            target.add((label, owner, name))
    assert offenders == [], offenders
    # The scan read the real callers, so an empty result is not a pass.
    assert update_calls >= _KNOWN_UPDATE_CALLS, update_calls
    assert insert_only == _KNOWN_INSERT_ONLY_CALLS, insert_only


def test_the_caller_fence_can_fail():
    planted = (
        "async def a(db, m):\n"
        "    await upsert_message(db, 'x', m)\n"
        "async def b(db, m, p):\n"
        "    await core._upsert_message(db, 'x', m)\n"
        "async def c(db, ms, f):\n"
        "    await _write_messages(db, 'x', ms, f, learn_labels=True)\n"
        "async def d(db, m, p):\n"
        "    await upsert_message(db, 'x', m, reclaim=p.REKEYS_MESSAGE_IDS)\n"
        "async def e(db, m):\n"
        "    await upsert_message(db, 'x', m, on_conflict='nothing')\n"
        "async def f(db, m):\n"
        "    await upsert_message(db, 'x', m, on_conflict='update')\n"
        "async def g(db, m, kw):\n"
        "    await upsert_message(db, 'x', m, **kw)\n"
    )
    assert _offenders(planted, "planted.py") == [
        "planted.py:2 a calls upsert_message without reclaim=",
        "planted.py:4 b calls _upsert_message without reclaim=",
        "planted.py:6 c calls _write_messages without reclaim=",
        "planted.py:12 f calls upsert_message without reclaim=",
        "planted.py:14 g calls upsert_message without reclaim=",
    ]


# ── R8 helpers ───────────────────────────────────────────────────────────────


def _dsn(p) -> str:
    return p.app_url.render_as_string(hide_password=False)


def _account(admin, *, org: str, owner: str, provider: str) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, sync_enabled, sync_interval_secs, "
            "sync_status, organization_id) "
            "VALUES (:u, :prov, :m, 'x', true, 300, 'idle', CAST(:o AS uuid)) "
            "RETURNING id"),
            {"u": owner, "prov": provider,
             "m": f"box-{uuid.uuid4().hex[:8]}@em-g1.test",
             "o": org}).scalar_one())


def _message(pmid: str, imid: str | None, *, folder: str = "inbox",
             days_ago: int = 0, subject: str = "Quote") -> EmailMessage:
    return EmailMessage(
        provider_message_id=pmid,
        thread_id=f"t-{pmid}",
        folder=folder,
        internet_message_id=imid,
        from_address=EmailAddress(name="Ravi", email="ravi@contoso-em-g1.test"),
        subject=subject,
        body_text="Please send it.",
        snippet="Please send it.",
        received_at=datetime.now(UTC) - timedelta(days=days_ago, minutes=5),
    )


def _rows(admin, account_id: str) -> dict[str, tuple[str, str]]:
    """Each row of the mailbox: its id, then its provider id and folder."""
    with admin.connect() as c:
        got = c.execute(text(
            "SELECT id::text AS id, provider_message_id, folder "
            "FROM email_messages WHERE account_id = CAST(:a AS uuid)"),
            {"a": account_id}).fetchall()
    return {r.id: (r.provider_message_id, r.folder) for r in got}


def _wipe(admin, account_ids: list[str]) -> None:
    with admin.begin() as c:
        for aid in account_ids:
            c.execute(text("DELETE FROM email_messages WHERE account_id = "
                           "CAST(:a AS uuid)"), {"a": aid})
    _purge(admin, account_ids)


async def _sync(org: str, account_id: str, messages: list[EmailMessage],
                **kw: Any) -> None:
    """One sync of ``messages``: one block, the real upsert for each."""
    async with core._tenant_session(org) as db:
        for msg in messages:
            await upsert_message(db, account_id, msg, **kw)


# ── R8: the gate on the real upsert ──────────────────────────────────────────


@_DB_GATE
class TestTheReclaimGate:
    """``email-reclaim-gate`` and ``email-reclaim-default-off``."""

    async def test_two_gmail_messages_with_one_message_id_keep_two_rows(
        self, promoted, app_engine,  # noqa: F811
    ):
        """GM-2. Two Gmail messages hold one Message-ID, as a list copy and
        a direct copy of one mail do. Each keeps its own row, and neither row
        swaps its id on the second sync."""
        _assert_non_priv(app_engine)
        p = promoted
        tag = uuid.uuid4().hex[:8]
        x = _account(p.admin_engine, org=p.org_b, provider="gmail",
                     owner=f"g-{tag}@em-g1.test")
        shared = f"<shared-{tag}@contoso-em-g1.test>"
        mail = [_message(f"g-a-{tag}", shared, subject="Direct"),
                _message(f"g-b-{tag}", shared, subject="Via the list")]
        reclaim = GmailProvider.REKEYS_MESSAGE_IDS
        try:
            async with tenant_engine_scope(_dsn(p)):
                await _sync(p.org_b, x, mail, reclaim=reclaim)
                first = _rows(p.admin_engine, x)
                await _sync(p.org_b, x, mail, reclaim=reclaim)
                second = _rows(p.admin_engine, x)
            assert len(first) == 2, (
                "two Gmail messages with one Message-ID folded into one row")
            assert sorted(pmid for pmid, _ in first.values()) == [
                f"g-a-{tag}", f"g-b-{tag}"]
            assert second == first, "a row swapped its provider id on a sync"
        finally:
            _wipe(p.admin_engine, [x])

    async def test_an_outlook_rekey_still_reclaims_its_row(
        self, promoted, app_engine,  # noqa: F811
    ):
        """An Outlook move gives the message a new Graph id. The upsert moves
        its one row to the new id, so the row id, and each value that rides
        on it, stays. This is a move of ONE mail between two folders. It is
        not the self-sent case, which is the known limit EM-G1-f1."""
        _assert_non_priv(app_engine)
        p = promoted
        tag = uuid.uuid4().hex[:8]
        x = _account(p.admin_engine, org=p.org_b, provider="microsoft",
                     owner=f"o-{tag}@em-g1.test")
        imid = f"<moved-{tag}@contoso-em-g1.test>"
        reclaim = OutlookProvider.REKEYS_MESSAGE_IDS
        try:
            async with tenant_engine_scope(_dsn(p)):
                await _sync(p.org_b, x, [_message(f"o-1-{tag}", imid)],
                            reclaim=reclaim)
                before = _rows(p.admin_engine, x)
                await _sync(p.org_b, x,
                            [_message(f"o-2-{tag}", imid, folder="archive")],
                            reclaim=reclaim)
                after = _rows(p.admin_engine, x)
            [row_id] = before
            assert after == {row_id: (f"o-2-{tag}", "archive")}, (
                "the Outlook move wrote a second row, or a new row id")
        finally:
            _wipe(p.admin_engine, [x])

    async def test_the_default_is_no_reclaim(
        self, promoted, app_engine,  # noqa: F811
    ):
        """An upsert that does not name ``reclaim`` never moves a row. A new
        provider, or a caller that forgets the keyword, gets two rows and
        never a fold of two messages."""
        _assert_non_priv(app_engine)
        p = promoted
        tag = uuid.uuid4().hex[:8]
        x = _account(p.admin_engine, org=p.org_b, provider="microsoft",
                     owner=f"d-{tag}@em-g1.test")
        imid = f"<default-{tag}@contoso-em-g1.test>"
        try:
            async with tenant_engine_scope(_dsn(p)):
                await _sync(p.org_b, x, [_message(f"d-1-{tag}", imid)])
                await _sync(p.org_b, x,
                            [_message(f"d-2-{tag}", imid, folder="archive")])
                rows = _rows(p.admin_engine, x)
            assert sorted(rows.values()) == [
                (f"d-1-{tag}", "inbox"), (f"d-2-{tag}", "archive")], (
                "an upsert with no keyword moved a row")
        finally:
            _wipe(p.admin_engine, [x])


# ── R8: each caller passes the attribute of its provider ────────────────────


class _Flagged:
    """A provider that does not subclass the base, as five fakes of the
    suite do. ``REKEYS_MESSAGE_IDS`` is the value under test.

    The first import yields one message. The recurring sweep then returns the
    same mail under a new id in another folder, as an Outlook move does.
    "Load older" returns one older message."""

    def __init__(self, flag: bool, tag: str) -> None:
        self.REKEYS_MESSAGE_IDS = flag
        moved = f"<moved-{tag}@contoso-em-g1.test>"
        self.imported = _message(f"imp-{tag}", moved)
        self.swept = _message(f"swp-{tag}", moved, folder="archive")
        self.older = _message(f"old-{tag}", f"<old-{tag}@contoso-em-g1.test>",
                              days_ago=10)

    async def authenticate(self) -> bool:
        return True

    def credentials_dirty(self) -> bool:
        return False

    def export_credentials(self) -> dict:
        return {"access_token": "at"}

    async def import_batches(self, **_kw):
        yield [self.imported]

    async def sync_messages(self, **_kw) -> SyncResult:
        return SyncResult(messages=[self.swept], new_history_id="h-1")

    async def get_message(self, provider_message_id):
        raise RuntimeError("no body backfill in this test")

    async def list_folders(self):
        return []

    async def list_messages(self, *, folder, max_results, page_token,
                            canonical_override):
        return [self.older], None


@_DB_GATE
class TestEachCallerPassesTheAttribute:
    """``email-reclaim-caller-values``."""

    @pytest.mark.parametrize("flag", [True, False], ids=["rekeys", "keeps_ids"])
    async def test_each_caller_passes_the_attribute_of_its_provider(
        self, promoted, app_engine, monkeypatch, flag,  # noqa: F811
    ):
        """One ``_sync_account`` of a new mailbox runs the first import
        (``_import_in_batches``, ``scheduler.py`` ``_run_import``) and then the
        recurring sweep (``_sync_cycle``). "Load older" then runs
        ``backfill_folder`` through ``core._upsert_message``. A recording
        upsert wraps the real one at both seams, so each call must carry the
        flag of the provider, and the rows show the real result."""
        from acb_llm import key_store
        from gateway.routes.email.transport import folders

        _assert_non_priv(app_engine)
        p = promoted
        tag = uuid.uuid4().hex[:8]
        owner = f"v-{tag}@em-g1.test"
        x = _account(p.admin_engine, org=p.org_b, provider="microsoft",
                     owner=owner)
        provider = _Flagged(flag, tag)
        real_upsert = persist.upsert_message
        seen: list[tuple[str, bool]] = []

        async def _recording(db, account_id, msg, *, on_conflict="update",
                             reclaim=False):
            seen.append((msg.provider_message_id, reclaim))
            await real_upsert(db, account_id, msg, on_conflict=on_conflict,
                              reclaim=reclaim)

        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        monkeypatch.setattr(folders, "_instantiate_provider",
                            lambda name, creds: provider)
        # The scheduler holds its own name. ``core._upsert_message`` imports
        # the name from ``persist`` at call time.
        monkeypatch.setattr(sched, "upsert_message", _recording)
        monkeypatch.setattr(persist, "upsert_message", _recording)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE,
                         organization_id=p.org_b)
        cleared = clear_tenant()
        try:
            async with tenant_engine_scope(_dsn(p)):
                res = await sched._sync_account(x, organization_id=p.org_b)
                token = bind_tenant(p.org_b)
                try:
                    older = await folders.backfill_folder(
                        x, folders.BackfillRequest(folder="inbox"), user=me)
                finally:
                    release_tenant(token)
            assert "error" not in res, res
            assert older["synced"] == 1, older
            assert seen == [(f"imp-{tag}", flag), (f"swp-{tag}", flag),
                            (f"old-{tag}", flag)], seen
            pmids = sorted(pmid for pmid, _ in _rows(p.admin_engine, x).values())
            if flag:
                assert pmids == [f"old-{tag}", f"swp-{tag}"], (
                    "the sweep wrote a second row for the moved mail")
            else:
                assert pmids == [f"imp-{tag}", f"old-{tag}", f"swp-{tag}"], (
                    "a provider that keeps its ids had its row moved")
        finally:
            release_tenant(cleared)
            _wipe(p.admin_engine, [x])


class _NoNetOutlook(OutlookProvider):
    """An Outlook provider with no network. It inherits
    ``REKEYS_MESSAGE_IDS`` from ``OutlookProvider`` and sets nothing, so the
    flag that reaches the upsert is the one the real class carries."""

    built: ClassVar[list[_NoNetOutlook]] = []
    count = 0
    tag = ""
    imid = ""

    def __init__(self, credentials, *, app=None):
        super().__init__(credentials, app=app)
        _NoNetOutlook.built.append(self)

    async def authenticate(self):
        return True

    def _next(self, kind: str, **kw) -> EmailMessage:
        _NoNetOutlook.count += 1
        return _message(f"{kind}{_NoNetOutlook.count}-{self.tag}", self.imid, **kw)

    async def import_batches(self, **_kw):
        yield [self._next("imp", folder="inbox")]

    async def sync_messages(self, **_kw):
        return SyncResult(messages=[self._next("swp", folder="archive")],
                          new_history_id=None)

    async def message_exists(self, _imid):
        return True

    async def get_message(self, _pmid):
        raise RuntimeError("no body backfill in this test")

    async def list_folders(self):
        return []

    async def list_messages(self, *, folder, max_results, page_token,
                            canonical_override):
        return [self._next("old", folder="inbox", days_ago=10)], None


@_DB_GATE
class TestEveryOutlookPathThroughTheRealFactory:
    """``email-reclaim-every-outlook-path`` (review of EM-G1, findings 1 and 2)."""

    async def test_every_outlook_path_passes_reclaim_true(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """The verifier found two gaps in ``email-reclaim-caller-values``.
        It runs no deep sync, and it patches the factory. Here the real
        ``build_provider`` and ``_instantiate_provider`` run each Outlook path.
        Each of the eight upserts must carry ``reclaim=True``, and the one
        mail that Outlook re-keys at each step keeps one row."""
        import email_ingestion.providers.outlook as outlook_mod
        from acb_llm import key_store
        from gateway.routes.email.transport import folders

        _assert_non_priv(app_engine)
        p = promoted
        tag = uuid.uuid4().hex[:8]
        owner = f"every-{tag}@em-g1.test"
        x = _account(p.admin_engine, org=p.org_b, provider="microsoft",
                     owner=owner)
        _NoNetOutlook.built = []
        _NoNetOutlook.tag = tag
        _NoNetOutlook.imid = f"<every-{tag}@contoso-em-g1.test>"
        real_upsert = persist.upsert_message
        seen: list[tuple[str, bool]] = []

        async def _recording(db, account_id, msg, *, on_conflict="update",
                             reclaim=False):
            seen.append((msg.provider_message_id, reclaim))
            await real_upsert(db, account_id, msg, on_conflict=on_conflict,
                              reclaim=reclaim)

        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        # The REAL factory runs. Only the class that it imports is swapped.
        monkeypatch.setattr(outlook_mod, "OutlookProvider", _NoNetOutlook)
        monkeypatch.setattr(sched, "upsert_message", _recording)
        monkeypatch.setattr(persist, "upsert_message", _recording)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE,
                         organization_id=p.org_b)
        cleared = clear_tenant()
        try:
            async with tenant_engine_scope(_dsn(p)):
                org = p.org_b
                steps = [
                    await sched._sync_account(x, organization_id=org),
                    await sched._sync_account(x, organization_id=org,
                                              if_busy="skip"),
                    await sched._sync_account(
                        x, organization_id=org, deep=True,
                        since=datetime.now(UTC) - timedelta(days=30)),
                    await sched._sync_account(x, organization_id=org, deep=True,
                                              if_busy="skip", reset_cursor=True),
                ]
                token = bind_tenant(org)
                try:
                    older = await folders.backfill_folder(
                        x, folders.BackfillRequest(folder="inbox"), user=me)
                finally:
                    release_tenant(token)
            assert all("error" not in step for step in steps), steps
            assert older["synced"] == 1, older
            assert len(_NoNetOutlook.built) == 5, _NoNetOutlook.built
            assert all(isinstance(b, OutlookProvider) for b in _NoNetOutlook.built)
            assert [flag for _, flag in seen] == [True] * 8, seen
            assert len(_rows(p.admin_engine, x)) == 1, (
                "an Outlook path wrote a second row for the re-keyed mail")
        finally:
            release_tenant(cleared)
            _wipe(p.admin_engine, [x])
