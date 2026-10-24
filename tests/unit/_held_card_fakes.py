"""Fakes for the held-card fences (``email_app_master_plan.md`` §21).

One home for the fake tenant Redis, the fake database session and the member,
so each fence file reads the same world. No fake sends a mail. The R8 halves
use the real database and do not import this file.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import types
from pathlib import Path
from typing import Any

from acb_auth.roles import UserContext, UserRole

REPO = Path(__file__).resolve().parents[2]
_AGENT = REPO / "apps" / "agents" / "agent-email-assistant" / "agents.py"

ORG = "org-held-1"
MEMBER = "jane@acme.test"
OTHER = "omar@acme.test"
BOX = "0a0a0a0a-0000-4000-8000-00000000000a"
DRAFT = "0d0d0d0d-0000-4000-8000-00000000000d"
THREAD = "th-held-1"
MAIL = "0c0c0c0c-0000-4000-8000-00000000000c"
FILE_PDF = "0f0f0f0f-0000-4000-8000-00000000000f"
FILE_XLS = "0e0e0e0e-0000-4000-8000-00000000000e"

KNOWN = "kim@contoso.test"       # the member wrote to Kim: ``sent``
COLLEAGUE = "geo@acme.test"      # a member of the organization: ``org``
STRANGER = "x@evil.example"      # never seen: ``new``
LOOKALIKE = "ravi@contos0.test"  # one letter off a known domain: ``new``, flagged


def load_email_agent(name: str = "ea_held_card"):
    """The email assistant's module, loaded from its file."""
    spec = importlib.util.spec_from_file_location(name, _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def user(email: str = MEMBER, org: str = ORG) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


class FakeRedis:
    """The commands that the two records use, under the real tenant wrapper."""

    def __init__(self) -> None:
        self.keys: dict[str, str] = {}

    async def set(self, k: str, v: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and k in self.keys:
            return False
        self.keys[k] = v
        return True

    async def getdel(self, k: str) -> str | None:
        return self.keys.pop(k, None)

    async def get(self, k: str) -> str | None:
        return self.keys.get(k)


def install_redis(monkeypatch) -> FakeRedis:
    """One fake Redis behind both records: the answer and the card."""
    from acb_common.tenant_redis import TenantRedis
    from acb_skills import reply_card as rcard
    from gateway.routes.email.automation import held_card as hc

    fake = FakeRedis()
    monkeypatch.setattr(rcard, "_tenant_redis", lambda: TenantRedis(fake))
    monkeypatch.setattr(hc, "_tenant_redis", lambda: TenantRedis(fake))
    return fake


def _settings() -> Any:
    from acb_common import get_settings

    return get_settings()  # cached: each reader sees this one object


def flag_on(monkeypatch) -> None:
    """``EMAIL_HELD_CARD_SEND`` on, for this test only."""
    monkeypatch.setattr(_settings(), "email_held_card_send", True)


def flag_off(monkeypatch) -> None:
    monkeypatch.setattr(_settings(), "email_held_card_send", False)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class FakeDb:
    """A session that answers each statement of the held card by its text."""

    def __init__(self) -> None:
        self.draft: dict[str, Any] | None = {
            "id": DRAFT, "account_id": BOX, "subject": "Quote",
            "to_addresses": json.dumps([{"email": KNOWN}]),
            "cc_addresses": json.dumps([{"email": COLLEAGUE}, {"email": STRANGER}]),
            "bcc_addresses": "[]", "body_text": "Hi Kim,\n\nThe quote is attached.",
            "thread_id": THREAD, "has_attachments": False,
        }
        self.classes: dict[str, tuple[bool, bool]] = {
            KNOWN: (False, True), COLLEAGUE: (True, False)}
        self.domains = ["contoso.test", "acme.test"]
        self.senders: list[str] = [KNOWN]
        #: Each To and Cc of a received mail of the thread (open question 1).
        self.participants: list[str] = []
        self.org_domains: list[str] = ["acme.test"]
        #: A reply of the thread that went out in the last day (item 6).
        self.recent: list[Any] = []
        self.files: list[Any] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        #: The seal of the draft's text NOW (``held_card.seal_of``). A test
        #: changes it to stand for an edit after the card.
        self.seal = "a" * 64
        self.seals: list[str] = []
        #: The mail to forward, and the files of the member's own mailbox.
        self.mail: dict[str, Any] | None = {"id": MAIL, "account_id": BOX, "subject": "Quote"}
        self.mail_files: list[Any] = [
            types.SimpleNamespace(id=FILE_PDF, filename="quote.pdf", size_bytes=4096,
                                  message_id=MAIL, subject="Quote"),
            types.SimpleNamespace(id=FILE_XLS, filename="rates.xlsx", size_bytes=2048,
                                  message_id=MAIL, subject="Quote"),
        ]

    async def execute(self, sql: Any, params: dict[str, Any] | None = None) -> _Result:
        from gateway.routes.email.automation import held_card as hc

        q = str(sql)
        params = dict(params or {})
        self.calls.append((q, params))
        if q == hc.DRAFT_ROW_SQL:
            return _Result([types.SimpleNamespace(**self.draft)] if self.draft else [])
        if q == hc.CLASSIFY_SQL:
            rows = []
            for a in params["wanted"]:
                org, sent = self.classes.get(a, (False, False))
                rows.append(types.SimpleNamespace(addr=a, is_org=org, is_sent=sent))
            return _Result(rows)
        if q == hc.KNOWN_DOMAINS_SQL:
            return _Result([types.SimpleNamespace(d=d) for d in self.domains])
        if q == hc.THREAD_SENDERS_SQL:
            return _Result([types.SimpleNamespace(addr=a) for a in self.senders])
        if getattr(hc, "THREAD_SENDER_ROWS_SQL", None) == q:
            # The senders in received order, the first mail first.
            return _Result([types.SimpleNamespace(addr=a) for a in self.senders])
        if q == hc.THREAD_PARTICIPANTS_SQL:
            # Each row names its mail's sender. A bare address stands for a
            # mail from KNOWN, a person the member wrote to.
            return _Result([types.SimpleNamespace(addr=a, sender=KNOWN) if isinstance(a, str)
                            else types.SimpleNamespace(addr=a[0], sender=a[1])
                            for a in self.participants])
        if q == hc.ORG_DOMAINS_SQL:
            return _Result([types.SimpleNamespace(d=d) for d in self.org_domains])
        from gateway.routes.email.automation import reply_card as rc

        if q == rc.RECENT_REPLY_SQL:
            return _Result(list(self.recent))
        if q == hc.DRAFT_FILES_SQL:
            return _Result(self.files)
        if q == hc.FORWARD_MAIL_SQL:
            return _Result([types.SimpleNamespace(**self.mail)] if self.mail else [])
        if q == hc.MAIL_FILES_SQL:
            return _Result(self.mail_files if params["mid"] == MAIL else [])
        if q == hc.OWNED_FILES_SQL:
            return _Result([f for f in self.mail_files if f.id in params["ids"]])
        raise AssertionError(f"unexpected SQL: {q[:80]}")


def install_db(monkeypatch, db: FakeDb | None = None) -> FakeDb:
    from gateway.routes.email.automation import held_card as hc

    db = db or FakeDb()

    @contextlib.asynccontextmanager
    async def session():
        yield db

    async def me(_db: Any, _aid: str) -> Any:
        return types.SimpleNamespace(label="Work", address=MEMBER, self_addresses={MEMBER})

    async def owner(_db: Any, account_id: str, who: str, **_k: Any) -> None:
        assert account_id == BOX and who == MEMBER

    from gateway.routes.email import core

    async def seal(owner: str, account_id: str, draft_id: str) -> str:
        db.seals.append(draft_id)
        return db.seal

    monkeypatch.setattr(hc, "_tenant_session", session)
    monkeypatch.setattr(hc, "resolve_self", me)
    monkeypatch.setattr(hc, "seal_of", seal)
    monkeypatch.setattr(core, "_assert_account_owner", owner)
    return db


class Sends:
    """A stand-in for each act that can leave: it records, and sends nothing."""

    def __init__(self) -> None:
        self.sent: list[Any] = []
        self.scheduled: list[Any] = []
        self.narrowed: list[dict[str, list[str]]] = []
        self.forwards: list[Any] = []
        self.mails: list[Any] = []
        self.fetched: list[str] = []


def install_sends(monkeypatch) -> Sends:
    from gateway.routes.email.automation import held_card as hc

    s = Sends()

    async def send(req: Any, background: Any, user: Any) -> dict[str, Any]:
        s.sent.append(req)
        return {"ok": True}

    async def narrow(user: Any, rec: dict[str, Any], ticked: dict[str, list[str]]) -> str:
        s.narrowed.append(ticked)
        return str(rec["draft_id"])

    async def forward(req: Any, user: Any) -> dict[str, Any]:
        s.forwards.append(req)
        return {"id": "fwd-1"}

    async def mail(req: Any, background: Any, user: Any) -> dict[str, Any]:
        s.mails.append(req)
        return {"id": "new-1"}

    async def payloads(user: Any, ids: Any) -> list[dict[str, Any]]:
        s.fetched.extend(ids)
        return [{"filename": f"{i}.pdf", "mime_type": "application/pdf", "content_b64": "eA=="}
                for i in ids]

    from gateway.routes.email.transport import forward as fwd
    from gateway.routes.email.transport import send as snd

    monkeypatch.setattr(hc, "send_draft_endpoint", send)
    monkeypatch.setattr(hc, "_narrow_draft", narrow)
    monkeypatch.setattr(fwd, "forward_email", forward)
    monkeypatch.setattr(snd, "send_email", mail)
    monkeypatch.setattr(hc, "file_payloads", payloads)
    return s


async def record_answer(card_id: str, answer: str, *, who: str = MEMBER,
                        thread: str = "chat-thread-1") -> bool:
    """What ``POST /agent/respond-input`` records when the browser posts."""
    from acb_skills.reply_card import record_reply_answer

    return await record_reply_answer(ORG, card_id, who, answer, thread)


def rows_answer(*ids: str) -> str:
    return "APPROVE " + json.dumps({"rows": list(ids)})
