"""Fakes for the held-card fences (``email_app_master_plan.md`` §21).

One home for the fake tenant Redis, the fake database session and the member,
so each fence file reads the same world. No fake sends a mail. The R8 halves
use the real database and do not import this file.

WS-67 OA-7b (``one_assistant_context.md`` §4.2.8): a fence file that imports
:func:`email_tool_mode` runs each test twice. In the ``owner`` mode a tool is
the email module's own function, as before. In the ``pack`` mode each email
tool is the tool that the pack builder
(``orchestrator._tool_injection._pack_tools``) builds from the run context
that the test bound, so the call crosses the scope of the owner, the
permission gate, the session mark and the working set. A tool that a held
pack does not hold raises :class:`NotInPack`.
"""
from __future__ import annotations

import contextlib
import functools
import importlib.util
import json
import types
from pathlib import Path
from typing import Any

import pytest
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


#: The mode of the email tools: "owner" or "pack" (:func:`email_tool_mode`).
_MODE: dict[str, str] = {"value": "owner"}


class NotInPack(AssertionError):
    """The pack that the test's run context builds does not hold the tool."""


@pytest.fixture(params=["owner", "pack"], autouse=True)
def email_tool_mode(request: pytest.FixtureRequest) -> Any:
    """Run each test of a file that imports this fixture once in each mode."""
    _MODE["value"] = request.param
    yield request.param
    _MODE["value"] = "owner"


def _pack_slug(name: str) -> str | None:
    from acb_skills import skill_families as sf

    for slug in sf.pack_slugs():
        if name in sf.SKILL_FAMILIES[slug]["tools"]:
            return slug
    return None


def pack_mode() -> bool:
    """True while the email tools come from the pack builder."""
    return _MODE["value"] == "pack"


#: The run id of the orchestrator's run that the view binds (see ``_call``).
PACK_TEST_RUN_ID = "pack-test-run"


async def _from_the_pack(module: Any, slug: str, name: str, args: Any, kwargs: Any) -> Any:
    from acb_skills.write_artifact import artifact_context
    from orchestrator import _tool_injection as ti

    build = await ti._pack_tools(slug, artifact_context(), module=module)
    tool = next((t for t in build.tools if t.__name__ == name), None)
    if tool is None:
        raise NotInPack(f"{name} is not in the {slug} pack (held={build.held})")
    return await tool(*args, **kwargs)


def _orchestrator_run(module: Any) -> tuple[dict[str, Any], bool]:
    """The orchestrator's run for one call, and whether the test meant a held
    run. A test binds the owner's own run (``agent_name="email-assistant"``,
    ``no_egress``), or binds nothing and patches ``_egress_held``. The pack
    runs in the orchestrator's run, which is never held on its own frame
    (``no_egress=False``). So only the builder and ``owner_scope`` can give
    the tool body the held answer (OA-7b fix round 1, F1)."""
    from acb_skills.tool_packs import PACK_RUN_KEY
    from acb_skills.write_artifact import artifact_context

    bound = dict(artifact_context())
    held = (bound.get("no_egress", False) is not False if bound
            else module._egress_held() is not False)
    run_id = bound.get("run_id") or PACK_TEST_RUN_ID
    # OA-7c: the run check keys on the value that the server mints for each
    # run, never on the run id. This view stands in for that server.
    run = {**bound, "agent_name": "orchestrator", "no_egress": False,
           "run_id": run_id, PACK_RUN_KEY: bound.get(PACK_RUN_KEY) or f"key-{run_id}"}
    if not held and not run.get("session_id"):
        # An open chat needs a key for its mark. A new one for each call, so
        # the mark of one call does not hold the next (``test_pack_guards.py``
        # tests the hold within one turn).
        import uuid

        run["session_id"] = f"pack-test-chat-{uuid.uuid4().hex[:8]}"
    return run, held


def _pack_entry(module: Any, slug: str, name: str, own: Any) -> Any:
    """The pack form of the email tool *name*. Each call binds the
    orchestrator's run (:func:`_orchestrator_run`), builds the pack from it,
    then calls the tool that the builder made. ``functools.wraps`` keeps the
    owner's name and signature.

    A held run of the test becomes the reason that holds a pack: the chat
    read mail. A run with a session gets a mark of that session, and a run
    with no session is held because no mark can be read for it."""

    @functools.wraps(own)
    async def _call(*args: Any, **kwargs: Any) -> Any:
        from acb_common.db import bind_tenant, current_tenant, release_tenant
        from acb_skills.egress import mark_untrusted_answer
        from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

        run, held = _orchestrator_run(module)
        token = None if current_tenant() else bind_tenant(ORG)
        try:
            with artifact_context_scope():
                bind_artifact_context(**run)
                if held and run.get("session_id"):
                    assert await mark_untrusted_answer(), "the held stand-in did not mark"
                return await _from_the_pack(module, slug, name, args, kwargs)
        finally:
            if token is not None:
                release_tenant(token)

    return _call


class EmailAgentView:
    """The email module as a fence reads it. A read, a write and a delete of
    an attribute reach the module, so ``monkeypatch.setattr`` patches the
    module itself. In the pack mode an email tool comes from the builder."""

    def __init__(self, module: Any) -> None:
        object.__setattr__(self, "_module", module)

    def __getattr__(self, name: str) -> Any:
        module = object.__getattribute__(self, "_module")
        value = getattr(module, name)
        slug = _pack_slug(name) if _MODE["value"] == "pack" else None
        if slug is None or not callable(value):
            return value
        return _pack_entry(module, slug, name, value)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_module"), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(object.__getattribute__(self, "_module"), name)


def load_email_agent(name: str = "ea_held_card"):
    """The email assistant's module, loaded from its file, as a view.

    Outside the pack mode the view gives the module's own attributes."""
    spec = importlib.util.spec_from_file_location(name, _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return EmailAgentView(mod)


def user(email: str = MEMBER, org: str = ORG) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


class FakeRedis:
    """The commands that the two records use, under the real tenant wrapper."""

    def __init__(self) -> None:
        self.keys: dict[str, str] = {}

    async def set(self, k: str, v: str, nx: bool = False, ex: int | None = None,
                  xx: bool = False, keepttl: bool = False) -> bool:
        if nx and k in self.keys:
            return False
        if xx and k not in self.keys:  # §21.15: a Bcc joins only a live record
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
