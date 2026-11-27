"""WS-PZ MM-1 — the act record: the one writer, no text, the gates (hermetic).

Spec: ``personalization.md`` §19.4 and §19.10. Code:
``packages/acb_common/acb_common/member_actions.py``. The R8 twin on a real
database is ``tests/unit/test_member_action_r8.py``.

a. One writer: no other source runs an INSERT on ``member_action``.
b. No text: a ``subject`` or ``body`` key, an unknown key, and a text value
   above 120 characters are refused. The INSERT names no text column.
c. A closed set of acts. A rule act is written and never counts as a choice.
d. The gates: the flag off, no org, or ``learning`` off writes nothing, and a
   failure of the write answers False and never raises.
e. Each seam of MM-1 calls the writer, with the right act.
f. The send seams write the row only after the provider took the mail, and
   only with the recipients that the server took. The send-later sweep is a
   job, and it writes no row.

Mutation (R7): add a second ``INSERT INTO member_action`` to any source, and
(a) goes red. Add ``subject`` to ``FEATURE_KEYS``, and (b) goes red.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_common import member_actions as ma

_ROOT = Path(__file__).resolve().parents[2]
_WRITER = _ROOT / "packages" / "acb_common" / "acb_common" / "member_actions.py"
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"
ORG = "11111111-1111-1111-1111-111111111111"
ME = "Ana@Example.com"


def _kw(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(member=ME, app="email", act="forward", via="app",
                                item_kind="email", item_id="m1")
    base.update(over)
    return base


# ── a. One writer ──────────────────────────────────────────────────────────

def test_only_the_writer_inserts_into_the_table() -> None:
    insert = re.compile(r"INSERT\s+INTO\s+member_action\b", re.I)
    found = []
    for base in ("apps", "packages"):
        for path in (_ROOT / base).rglob("*.py"):
            if path == _WRITER or "node_modules" in path.parts:
                continue
            if insert.search(path.read_text(encoding="utf-8", errors="replace")):
                found.append(str(path.relative_to(_ROOT)))
    assert found == []
    assert len(insert.findall(_WRITER.read_text(encoding="utf-8"))) == 1


# ── b. No text ─────────────────────────────────────────────────────────────

def test_the_insert_names_no_text_column() -> None:
    cols = ma.INSERT_SQL.split("(", 1)[1].split(")", 1)[0]
    assert "organization_id" not in cols
    for banned in ("subject", "body", "snippet", "text"):
        assert banned not in cols


@pytest.mark.parametrize("key", ["subject", "body", "snippet", "text", "unknown"])
def test_a_feature_key_outside_the_list_is_refused(key: str) -> None:
    with pytest.raises(ma.ActError):
        ma.build_row(**_kw(features={key: "x"}))


def test_a_long_text_value_is_refused_and_a_short_one_kept() -> None:
    with pytest.raises(ma.ActError):
        ma.build_row(**_kw(features={"sender_domain": "a" * 121}))
    row = ma.build_row(**_kw(features={"sender_domain": "ge.com", "edit_distance": 4,
                                       "is_reply": False, "outcome": None}))
    assert '"sender_domain": "ge.com"' in row["features"]
    assert "outcome" not in row["features"]


def test_no_feature_key_can_hold_a_subject() -> None:
    assert not {"subject", "body", "text", "snippet", "preview"} & ma.FEATURE_KEYS


def test_a_value_that_is_not_a_scalar_is_refused() -> None:
    with pytest.raises(ma.ActError):
        ma.build_row(**_kw(features={"sender_domain": ["a"]}))
    with pytest.raises(ma.ActError):
        ma.build_row(**_kw(features={"edit_distance": float("nan")}))


# ── c. The closed set ──────────────────────────────────────────────────────

def test_the_acts_are_the_closed_set_of_the_spec() -> None:
    assert ma.ACTS["email"] >= {"reply", "forward", "send", "mark_done", "archive",
                                "snooze", "label", "delegate", "draft_sent"}
    assert ma.ACTS["chat"] == {"card_approve", "card_reject", "card_revise"}
    assert {"card_acted", "kind_mute"} <= ma.ACTS["my_day"]
    assert ma.ACTS["reach"] == {"alert_useful", "alert_not_useful"}


@pytest.mark.parametrize("over", [
    {"app": "nope"}, {"act": "nope"}, {"app": "chat", "act": "forward"},
    {"via": "robot"}, {"item_id": ""}, {"item_kind": "x" * 41},
    {"member": "anonymous"}, {"member": ""},
])
def test_a_bad_act_is_refused(over: dict[str, Any]) -> None:
    with pytest.raises(ma.ActError):
        ma.build_row(**_kw(**over))


def test_the_member_is_lowered_and_the_targets_are_clean() -> None:
    row = ma.build_row(**_kw(targets=["Veena@GE.com", "veena@ge.com", "Kiran@ge.com"]))
    assert row["m"] == "ana@example.com"
    assert row["targets"] == ["veena@ge.com", "kiran@ge.com"]
    with pytest.raises(ma.ActError):
        ma.build_row(**_kw(targets=["not-an-address"]))
    with pytest.raises(ma.ActError):
        ma.build_row(**_kw(targets=[f"a{i}@x.com" for i in range(21)]))


def test_only_app_and_card_count_as_a_choice() -> None:
    assert ma.counts_as_choice("app") and ma.counts_as_choice("card")
    assert not ma.counts_as_choice("rule")
    assert ma.build_row(**_kw(via="rule"))["via"] == "rule"


def test_domain_of() -> None:
    assert ma.domain_of("Bob <bob@GE.com>") == "ge.com"
    assert ma.domain_of("nobody") is None and ma.domain_of(None) is None


# ── d. The gates ───────────────────────────────────────────────────────────

class _Nested:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *a: Any) -> bool:
        return False


class _Db:
    def __init__(self, learning: Any = True, boom: bool = False, shared: bool = False) -> None:
        self.shared = shared
        self.sql: list[str] = []
        self.learning = learning
        self.boom = boom

    def begin_nested(self) -> _Nested:
        return _Nested()

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        sql = str(stmt)
        self.sql.append(sql)
        if sql.startswith("INSERT") and self.boom:
            raise RuntimeError("secret value in message")
        if "email_accounts" in sql:
            return SimpleNamespace(first=lambda: (1,) if self.shared else None)
        if sql.startswith("SELECT"):
            return SimpleNamespace(first=lambda: None if self.learning is None else
                                   ({"v": 1, "learning": self.learning},))
        return SimpleNamespace(rowcount=1)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "member_actions_orgs", value, raising=False)


def test_the_flag_is_off_by_default_and_reads_star(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common import get_settings

    assert get_settings().member_actions_orgs == ""
    assert not ma.enabled_for(ORG)
    _flag(monkeypatch, ORG.upper())
    assert ma.enabled_for(ORG) and not ma.enabled_for("other") and not ma.enabled_for(None)
    _flag(monkeypatch, "*")
    assert ma.enabled_for(ORG) and not ma.enabled_for("")


def test_the_flag_off_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _Db()
    assert _run(ma.record_act(db, org=ORG, **_kw())) is False
    assert db.sql == []


def test_a_row_is_written_when_the_flag_holds_the_org(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, ORG)
    db = _Db()
    assert _run(ma.record_act(db, org=ORG, **_kw())) is True
    assert [s.split()[0] for s in db.sql] == ["SELECT", "INSERT"]
    assert _run(ma.record_act(db, org="", **_kw())) is False


def test_learning_off_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, ORG)
    db = _Db(learning=False)
    assert _run(ma.record_act(db, org=ORG, **_kw())) is False
    assert not any(s.startswith("INSERT") for s in db.sql)


def test_a_shared_mailbox_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, ORG)
    mb = "22222222-2222-2222-2222-222222222222"
    db = _Db(shared=True)
    assert _run(ma.record_act(db, org=ORG, **_kw(mailbox_id=mb))) is False
    assert not any(s.startswith("INSERT") for s in db.sql)
    assert _run(ma.record_act(_Db(shared=False), org=ORG, **_kw(mailbox_id=mb))) is True
    # A name that is not an id runs no mailbox read.
    plain = _Db(shared=True)
    assert _run(ma.record_act(plain, org=ORG, **_kw(mailbox_id="mb-1"))) is True
    assert not any("email_accounts" in s for s in plain.sql)


def test_no_profile_row_counts_as_learning_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, ORG)
    assert _run(ma.record_act(_Db(learning=None), org=ORG, **_kw())) is True


def test_a_failed_write_answers_false_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, ORG)
    assert _run(ma.record_act(_Db(boom=True), org=ORG, **_kw())) is False
    assert _run(ma.record_act(_Db(), org=ORG, **_kw(act="nope"))) is False


def test_the_writer_never_names_the_profile_column() -> None:
    src = _WRITER.read_text(encoding="utf-8")
    assert "SELECT assistant_profile" not in src
    assert "FROM user_settings" not in src


# ── e. The seams ───────────────────────────────────────────────────────────

_SEAMS = {
    "routes/email/automation/replyzero.py": ['act="mark_done"'],
    "routes/email/automation/drafting.py": ['act="reply" if drow.thread_id else "send"'],
    "routes/email/transport/send.py": ['act="reply" if req.reply_to_message_id else "send"'],
    "routes/email/transport/forward.py": ['act="forward"'],
    "routes/email/transport/messages.py": ['act="archive"', 'act="label"', 'act="snooze"'],
}


@pytest.mark.parametrize("rel", sorted(_SEAMS))
def test_each_seam_calls_the_one_writer(rel: str) -> None:
    src = (_GATEWAY / rel).read_text(encoding="utf-8")
    assert "member_actions" in src
    for needle in _SEAMS[rel]:
        assert needle in src, (rel, needle)
    # A seam passes the org of the session, never a request value.
    assert "org=member_actions.org_of(user)" in src
    assert "req.organization" not in src


def test_the_prune_hook_is_registered_and_the_purge_names_the_table() -> None:
    from gateway import member_action_prune
    from gateway.jobs import worker
    from gateway.routes.admin.members import _PURGE_DELETES

    assert member_action_prune.prune_hook in worker.prune_hooks
    [rows] = [r for r in _PURGE_DELETES if r.table == "member_action"]
    assert rows.where.startswith("lower(member_email) = :email AND organization_id = ")
    assert "180" not in ma.PRUNE_SQL and ma.RETENTION_DAYS == 180


# ── f. The send seams: after the provider, the recipients it took, no job ──

def test_sent_to_drops_each_refused_address_and_each_non_address() -> None:
    to = ["Ravi@Contoso.test", "kim@contoso.test", "nobody", "", "Gone@X.test "]
    assert ma.sent_to(to, frozenset({"gone@x.test", "kim@contoso.test"})) == [
        "Ravi@Contoso.test"]
    assert ma.sent_to(to) == ["Ravi@Contoso.test", "kim@contoso.test", "Gone@X.test "]
    assert ma.sent_to(None, None) == []


def test_a_job_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: drop the ``_IN_JOB`` check of ``record_act`` -> red."""
    _flag(monkeypatch, ORG)
    db = _Db()
    with ma.job_acts():
        assert _run(ma.record_act(db, org=ORG, **_kw())) is False
    assert db.sql == []
    assert _run(ma.record_act(db, org=ORG, **_kw())) is True, "the mark ends with the block"


BOX = "a0000000-0000-4000-8000-000000000001"
DRAFT = "00000000-0000-4000-8000-0000000000d1"
MEMBER = "dana@fracktal.test"


class _SeamDb(_Db):
    """The ``_Db`` of (d), with the draft row of ``POST /email/drafts/send``.
    ``rows`` holds the bind parameters of each INSERT of the act record."""

    def __init__(self, drow: Any = None) -> None:
        super().__init__()
        self.drow = drow
        self.rows: list[dict[str, Any]] = []

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        sql = str(stmt)
        if sql.startswith("INSERT INTO member_action"):
            self.rows.append(dict(params))
        if "FROM email_messages WHERE id = :id" in sql:
            return SimpleNamespace(fetchone=lambda: self.drow)
        return await super().execute(stmt, params)


class _DraftProvider:
    """A provider of a draft send. ``native=False`` is IMAP: no ``send_draft``,
    so the route sends a new mail, and the SMTP server can refuse an address."""

    def __init__(self, *, native: bool = True, fail: bool = False,
                 refused: tuple[str, ...] = ()) -> None:
        self.native, self.fail, self.refused = native, fail, refused
        self.calls: list[str] = []

    async def get_draft_recipients(self, _pid: str) -> dict[str, list[str]]:
        return {"to": ["ravi@contoso.test", "kim@contoso.test"], "cc": [], "bcc": []}

    async def send_draft(self, _pid: str) -> None:
        if not self.native:
            raise NotImplementedError
        if self.fail:
            raise RuntimeError("provider down")
        self.calls.append("send_draft")

    async def send_message(self, **_kw: Any) -> str:
        self.calls.append("send_message")
        self.last_refused = frozenset(self.refused)
        return "new-1"

    async def trash_message(self, _pid: str) -> None:
        self.calls.append("trash")


def _drow(thread_id: str | None = "t-1") -> SimpleNamespace:
    return SimpleNamespace(
        provider_message_id="pm-1", subject="Re: Quote",
        to_addresses=[{"email": "Ravi@Contoso.test"}, {"email": "kim@contoso.test"}],
        cc_addresses=[], bcc_addresses=[], body_text="Hi", thread_id=thread_id,
        internet_message_id=None)  # task 4100: the trash reads it


def _member() -> Any:
    from acb_auth.roles import UserContext, UserRole

    return UserContext(email=MEMBER, role=UserRole.EMPLOYEE, organization_id=ORG)


def _patch_draft_send(monkeypatch: pytest.MonkeyPatch, db: _SeamDb,
                      provider: _DraftProvider) -> Any:
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from gateway.routes.email.automation import drafting as d

    from tests.unit._email_fakes import bind_db

    @asynccontextmanager
    async def provider_session(*_a: Any, **_k: Any):
        yield SimpleNamespace(provider=provider)

    _flag(monkeypatch, ORG)
    monkeypatch.setattr(d, "_tenant_session", bind_db(db))
    monkeypatch.setattr(d, "_assert_account_owner", AsyncMock())
    monkeypatch.setattr(d, "provider_session", provider_session)
    monkeypatch.setattr(d, "resolve_signature", AsyncMock(return_value=""))
    # The chat checks of ``expect`` read the provider text and the files.
    # Each has its own fence. Here they pass.
    monkeypatch.setattr(d, "_check_chat_send", AsyncMock())
    monkeypatch.setattr(d, "check_draft_files", AsyncMock())
    return d


_NO_TASKS = SimpleNamespace(add_task=lambda *a, **k: None)


async def test_the_members_send_of_a_draft_records_one_reply(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The composer and the inline reply send through ``POST /email/drafts/send``.
    Mutation: remove the ``record_act`` call of ``send_draft_endpoint`` -> red."""
    db, provider = _SeamDb(_drow()), _DraftProvider()
    d = _patch_draft_send(monkeypatch, db, provider)
    out = await d.send_draft_endpoint(
        d.DraftSendRequest(account_id=BOX, draft_id=DRAFT), _NO_TASKS, user=_member())
    assert out == {"sent": True} and provider.calls == ["send_draft"]
    [row] = db.rows
    assert (row["m"], row["act"], row["via"], row["item_id"], row["mailbox"]) == (
        MEMBER, "reply", "app", DRAFT, BOX)
    assert row["targets"] == ["ravi@contoso.test", "kim@contoso.test"]
    assert row["features"] == '{"is_reply": true, "new_thread": false}'


async def test_a_new_draft_records_a_send_without_the_refused_address(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """IMAP sends a new mail, and the SMTP server refused one address.
    Mutation: pass the To of the row with no refused filter -> red."""
    db = _SeamDb(_drow(thread_id=None))
    provider = _DraftProvider(native=False, refused=("kim@contoso.test",))
    d = _patch_draft_send(monkeypatch, db, provider)
    await d.send_draft_endpoint(
        d.DraftSendRequest(account_id=BOX, draft_id=DRAFT), _NO_TASKS, user=_member())
    assert provider.calls == ["send_message", "trash"]
    [row] = db.rows
    assert row["act"] == "send" and row["targets"] == ["ravi@contoso.test"]
    assert row["features"] == '{"is_reply": false, "new_thread": true}'


async def test_a_send_that_the_provider_refused_records_nothing(
        monkeypatch: pytest.MonkeyPatch) -> None:
    db, provider = _SeamDb(_drow()), _DraftProvider(fail=True)
    d = _patch_draft_send(monkeypatch, db, provider)
    with pytest.raises(RuntimeError):
        await d.send_draft_endpoint(
            d.DraftSendRequest(account_id=BOX, draft_id=DRAFT), _NO_TASKS, user=_member())
    assert db.rows == []


async def test_the_send_later_sweep_is_no_act_of_the_member(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The sweep sends through the REAL ``send_draft_endpoint`` as the member.
    Mutation: remove ``member_actions.job_acts()`` from ``_send_one`` -> red."""
    from gateway.routes.email.automation import send_later as sl

    db, provider = _SeamDb(_drow()), _DraftProvider()
    _patch_draft_send(monkeypatch, db, provider)
    member = _member()

    async def _resolved(_owner: str, _org: str) -> Any:
        return member

    monkeypatch.setattr(sl, "_member", _resolved)
    row = SimpleNamespace(
        owner_email=MEMBER, account_id=BOX, draft_id=DRAFT,
        expect={"to": ["ravi@contoso.test", "kim@contoso.test"], "cc": [], "bcc": []})
    why, tasks = await sl._send_one(ORG, row)
    assert why is None and tasks is not None
    assert provider.calls == ["send_draft"], "the sweep sent the mail"
    assert db.rows == [], "a job is not the member's own act"


async def test_the_send_route_records_no_refused_address(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """``POST /email/send`` on SMTP. Mutation: pass ``req.to`` with no refused
    filter -> red."""
    from contextlib import asynccontextmanager

    from fastapi import BackgroundTasks
    from gateway.routes.email import signature
    from gateway.routes.email.transport import send

    db = _SeamDb()

    class _Smtp:
        last_refused = frozenset({"kim@contoso.test"})

        async def send_message(self, **_kw: Any) -> str:
            return "m-1"

    @asynccontextmanager
    async def _session():
        yield db

    @asynccontextmanager
    async def _provider_session(*_a: Any, **_k: Any):
        yield SimpleNamespace(provider=_Smtp())

    async def _no_signature(*_a: Any, **_k: Any) -> str:
        return ""

    _flag(monkeypatch, ORG)
    monkeypatch.setattr(send, "_tenant_session", _session)
    monkeypatch.setattr(send, "provider_session", _provider_session)
    monkeypatch.setattr(signature, "resolve_signature_for_send", _no_signature)
    req = send.SendEmailRequest(account_id=BOX, to=["Ravi@Contoso.test", "kim@contoso.test"],
                                subject="Quote", body_text="Hi")
    out = await send.send_email(req, BackgroundTasks(), user=_member())
    assert out["refused"] == ["kim@contoso.test"]
    [row] = db.rows
    assert row["act"] == "send" and row["targets"] == ["ravi@contoso.test"]
