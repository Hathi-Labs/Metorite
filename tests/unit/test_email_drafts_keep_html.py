"""WS-17 EM-G3a-f7 PR-C1a and PR-C1b — a Gmail-web draft keeps its HTML.

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.12.5. PR-C1a, the
save: the decisions D-G3a-f7-2, -5, -6, -6a, -8 and -10, and the acceptance
items 5, 6, 12 and 14 with the save half of 9 and 13. PR-C1b, the send: the
decisions D-G3a-f7-6, -6a, -7, -10, -11 and -11a, and the acceptance items 7,
8, 10 and 11 with the send half of 9 and 13.

* ``PUT /email/drafts`` takes ``keep_html``. For a Gmail mailbox the save
  sends the HTML of the draft: from the row, else from Gmail through
  ``get_message_body``. The read runs inside the one ``_tenant_session`` of the
  route. A failed read sends the text only and answers ``html: "lost"``.
* A Gmail save with no ``keep_html`` sets the row ``body_html`` to NULL, so no
  stale HTML stays (D-G3a-f7-8). Only the upsert route asks for the clear. The
  mirror of the AI drafter and ``/drafts/save`` write the row of main.
* An Outlook save calls the provider with the arguments of main, answers the
  keys of main and leaves ``body_html`` as main does.
* A signed send of a Gmail draft signs the HTML of the draft with
  ``sign_html``: from the row, else from Gmail. With no HTML anywhere it signs
  as main does. A failed read with ``allow_text_only: false`` answers 409 and
  sends nothing. With ``true``, or with no value, it sends the text only. An
  Outlook signed send calls the provider as main does.

**R8.** Each case runs the real routes and the real SQL against the
phase-4-promoted two-org catalog of ``test_h3_rls_promotion_rehearsal``,
under FORCE RLS, as the role ``acb_app_h3rls`` (NOSUPERUSER, NOBYPASSRLS).
Gmail is the REAL ``GmailProvider`` on the fake Gmail of
``test_gmail_send_and_drafts.py``. CI without a ladder database skips the
whole file.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_drafts_keep_html.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from email import message_from_bytes
from email.mime.text import MIMEText
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from email_ingestion.persist import TRUNCATION_MARKER
from email_ingestion.providers.gmail import GmailProvider
from email_ingestion.providers.outlook import OutlookProvider
from fastapi import BackgroundTasks, HTTPException
from gateway.routes.email.automation import drafting
from gateway.routes.email.signature import build_signed_bodies
from sqlalchemy import text

from tests.unit.test_email_scheduler_tenancy import _assert_non_priv, _purge

# ``fake``, ``promoted`` and ``app_engine`` are fixtures, used by name, so the
# imports are load-bearing even though they read as unused.
from tests.unit.test_gmail_send_and_drafts import (  # noqa: F401
    CID,
    PNG,
    RAVI,
    _account,
    _as_member,
    _decode,
    _Gmail,
    _gmail_web_draft,
    _provider,
    _shape,
    _sign,
    fake,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

#: The HTML of the Gmail-web draft, as the sync stores it in the row.
STORED_HTML = f'<div>Please find the layout.<img src="cid:{CID}"></div>'
TEXT = "Please find the new layout below."


def _seed_draft(admin: Any, *, org: str, account_id: str, pmid: str,
                body_html: str | None) -> str:
    """One draft row of the mailbox, as the sync of a Gmail-web draft writes
    it. ``body_html`` NULL stands for a row that the hot window left bare."""
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, subject, body_text, body_html, from_address, "
            "to_addresses, organization_id) VALUES (CAST(:a AS uuid), :p, "
            "'t-c', 'drafts', 'New layout', :bt, :bh, '{}'::jsonb, "
            "CAST(:to AS jsonb), CAST(:o AS uuid)) RETURNING id::text"),
            {"a": account_id, "p": pmid, "bt": TEXT, "bh": body_html,
             "to": json.dumps([{"name": "", "email": RAVI}]),
             "o": org}).scalar_one())


def _seed_mail(admin: Any, *, org: str, account_id: str, pmid: str) -> str:
    """One received mail of the mailbox, the target of a reply."""
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, subject, from_address, to_addresses, "
            "organization_id) VALUES (CAST(:a AS uuid), :p, 't-r', 'inbox', "
            "'Quote', CAST(:f AS jsonb), '[]'::jsonb, CAST(:o AS uuid)) "
            "RETURNING id::text"),
            {"a": account_id, "p": pmid, "o": org,
             "f": json.dumps({"name": "Ravi", "email": RAVI})}).scalar_one())


def _row(admin: Any, row_id: str) -> Any:
    with admin.connect() as c:
        return c.execute(text(
            "SELECT provider_message_id, folder, subject, body_text, body_html "
            "FROM email_messages WHERE id = CAST(:i AS uuid)"),
            {"i": row_id}).fetchone()


def _full_reads(service: _Gmail, mid: str) -> int:
    """The reads of the whole message ``mid`` (``get_message_body``)."""
    return sum(1 for m, p, q in service.seen
               if (m, p) == ("GET", f"/messages/{mid}")
               and q.get("format") == "full")


def _box(p: Any, monkeypatch: pytest.MonkeyPatch, provider: str,
         make: Any) -> SimpleNamespace:
    """One mailbox of one member in org B. ``make`` builds the provider of
    each request, as ``provider_session`` does."""
    owner = f"keep-{uuid.uuid4().hex[:8]}@em-g3a-f7.test"
    aid = _account(p.admin_engine, org=p.org_b, owner=owner, provider=provider)
    m = SimpleNamespace(
        p=p, admin=p.admin_engine, org=p.org_b, aid=aid, owner=owner,
        providers=[],
        me=UserContext(email=owner, role=UserRole.EMPLOYEE,
                       organization_id=p.org_b))

    @asynccontextmanager
    async def _provider_session(*_a: Any, **_kw: Any) -> AsyncIterator[Any]:
        made = make()
        m.providers.append(made)
        yield SimpleNamespace(provider=made, owner_email=owner, account_id=aid,
                              authed=True, provider_message_id="i-1")

    monkeypatch.setattr(drafting, "provider_session", _provider_session)
    return m


@pytest.fixture()
def gmail(promoted, app_engine, fake, monkeypatch):  # noqa: F811
    """A Gmail mailbox, with the REAL Gmail provider on the fake, and one
    Gmail-web draft: ``multipart/related`` with an inline image."""
    _assert_non_priv(app_engine)
    m = _box(promoted, monkeypatch, "gmail", _provider)
    m.fake = fake
    fake.add_raw_draft("r-c", "m-c", _gmail_web_draft(), thread="t-c")
    try:
        yield m
    finally:
        _drop(m)


def _outlook() -> OutlookProvider:
    """The real Outlook class, so ``isinstance`` sees Outlook. Each draft
    call records its arguments."""
    outlook = OutlookProvider({"access_token": "x", "refresh_token": "y"})
    outlook.update_draft = AsyncMock(return_value="AAMk-1")  # type: ignore[method-assign]
    outlook.create_draft = AsyncMock(return_value="AAMk-1")  # type: ignore[method-assign]
    outlook.get_message_body = AsyncMock()  # type: ignore[method-assign]
    outlook.send_draft = AsyncMock(return_value=None)  # type: ignore[method-assign]
    return outlook


@pytest.fixture()
def outlook(promoted, app_engine, monkeypatch):  # noqa: F811
    _assert_non_priv(app_engine)
    m = _box(promoted, monkeypatch, "outlook", _outlook)
    try:
        yield m
    finally:
        _drop(m)


def _drop(m: SimpleNamespace) -> None:
    with m.admin.begin() as c:
        c.execute(text("DELETE FROM email_messages WHERE account_id = "
                       "CAST(:a AS uuid)"), {"a": m.aid})
    _purge(m.admin, [m.aid])


async def _save(m: SimpleNamespace, **fields: Any) -> dict[str, Any]:
    """One PUT /email/drafts of the member: the real route, the real SQL."""
    req = drafting.DraftUpsertRequest(account_id=m.aid, **fields)
    async with _as_member(m):
        return await drafting.upsert_draft(req, user=m.me)


def _gmail_draft(m: SimpleNamespace, body_html: str | None) -> str:
    return _seed_draft(m.admin, org=m.org, account_id=m.aid, pmid="m-c",
                       body_html=body_html)


def _sent(m: SimpleNamespace) -> Any:
    return _decode(m.fake.saved[-1]["message"]["raw"])


_RELATED = ("multipart/related", [
    ("multipart/alternative", ["text/plain", "text/html"]), "image/png"])


# ── acceptance 5: the HTML comes from the row, or from Gmail ────────────────


async def test_keep_html_with_no_stored_html_reads_gmail(gmail):
    """Acceptance 5, D-G3a-f7-6. The hot window left the row with no HTML.
    The save reads the HTML from Gmail once, sends it with its inline image,
    and answers ``kept``. The row stays with no HTML (acceptance 6)."""
    m = gmail
    local = _gmail_draft(m, None)

    saved = await _save(m, draft_id=local, to=[RAVI], subject="New layout",
                        body=TEXT, keep_html=True)

    assert saved["html"] == drafting.HTML_KEPT == "kept"
    assert saved["id"] == local
    assert _full_reads(m.fake, "m-c") == 1
    msg = _sent(m)
    assert _shape(msg) == _RELATED
    assert msg.get_payload()[1]["Content-ID"] == f"<{CID}>"
    assert _row(m.admin, local).body_html is None


async def test_keep_html_with_stored_html_reads_no_gmail(gmail):
    """D-G3a-f7-6. The row HTML wins, so the save reads nothing more from
    Gmail. The update still puts the image back, because the stored HTML
    names its cid."""
    m = gmail
    local = _gmail_draft(m, STORED_HTML)

    saved = await _save(m, draft_id=local, to=[RAVI], subject="New layout",
                        body=TEXT, keep_html=True)

    assert saved["html"] == "kept"
    assert _full_reads(m.fake, "m-c") == 0
    msg = _sent(m)
    assert _shape(msg) == _RELATED
    html = msg.get_payload()[0].get_payload()[1].get_payload(decode=True).decode()
    assert html == STORED_HTML
    assert _row(m.admin, local).body_html == STORED_HTML


async def test_a_failed_html_read_saves_text_and_answers_lost(gmail, monkeypatch):
    """Acceptance 5, M5. The read of the HTML fails. The save goes on with
    the text only, and answers ``lost``, so the card can tell the member."""
    m = gmail
    local = _gmail_draft(m, None)

    async def _fails(self: GmailProvider, _mid: str) -> Any:
        raise RuntimeError("the read failed")

    monkeypatch.setattr(GmailProvider, "get_message_body", _fails)

    saved = await _save(m, draft_id=local, to=[RAVI], subject="New layout",
                        body=TEXT, keep_html=True)

    assert saved["html"] == drafting.HTML_LOST == "lost"
    assert _shape(_sent(m)) == "text/plain"
    assert _row(m.admin, local).body_text.startswith(TEXT)


async def test_a_cut_row_html_reads_gmail(gmail):
    """Review round 1, P2. The sync cuts a large body and ends it with
    ``TRUNCATION_MARKER``. Such a row HTML counts as absent, so the save
    reads the whole HTML from Gmail. The cut HTML never goes back into
    Gmail, and the image after the cut stays."""
    m = gmail
    cut = STORED_HTML[:20] + TRUNCATION_MARKER
    local = _gmail_draft(m, cut)

    saved = await _save(m, draft_id=local, to=[RAVI], subject="New layout",
                        body=TEXT, keep_html=True)

    assert saved["html"] == "kept"
    assert _full_reads(m.fake, "m-c") == 1
    msg = _sent(m)
    assert _shape(msg) == _RELATED
    html = msg.get_payload()[0].get_payload()[1].get_payload(decode=True).decode()
    assert TRUNCATION_MARKER not in html
    assert f'src="cid:{CID}"' in html


async def test_a_slow_html_read_answers_lost(gmail, monkeypatch):
    """Review round 1, P3. The Gmail read waits ``BODY_FETCH_TIMEOUT_S`` at
    most. A read past it answers ``lost``, and the save sends the text."""
    m = gmail
    local = _gmail_draft(m, None)
    real_read = GmailProvider.get_message_body

    async def _slow(self: GmailProvider, mid: str) -> Any:
        await asyncio.sleep(1.0)
        return await real_read(self, mid)

    monkeypatch.setattr(GmailProvider, "get_message_body", _slow)
    monkeypatch.setattr(drafting, "BODY_FETCH_TIMEOUT_S", 0.05)

    saved = await _save(m, draft_id=local, to=[RAVI], subject="New layout",
                        body=TEXT, keep_html=True)

    assert saved["html"] == "lost"
    assert _shape(_sent(m)) == "text/plain"


async def test_a_keep_html_save_with_no_html_anywhere_answers_none(gmail):
    """D-G3a-f7-10, the save half. A draft made in Metorite is
    ``text/plain``, so neither the row nor Gmail holds HTML. The save sends
    the text, as main does, and answers ``none``. It costs one Gmail read."""
    m = gmail
    first = await _save(m, to=[RAVI], subject="Quote", body="v1")
    mid = first["provider_message_id"]

    saved = await _save(m, draft_id=first["id"], to=[RAVI], subject="Quote",
                        body="v1", keep_html=True)

    assert saved["html"] == drafting.HTML_NONE == "none"
    assert _full_reads(m.fake, mid) == 1
    assert _shape(_sent(m)) == "text/plain"


# ── acceptance 6: a text save clears the stored HTML ────────────────────────


async def test_a_gmail_text_save_clears_the_stored_html(gmail):
    """Acceptance 6, D-G3a-f7-8. A Gmail save with no ``keep_html`` sends
    the text only, so the row keeps no HTML of the text from before. A save
    with ``keep_html`` leaves the stored HTML."""
    m = gmail
    local = _gmail_draft(m, STORED_HTML)

    kept = await _save(m, draft_id=local, to=[RAVI], subject="New layout",
                       body=TEXT, keep_html=True)
    assert kept["html"] == "kept"
    assert _row(m.admin, local).body_html == STORED_HTML

    edited = await _save(m, draft_id=local, to=[RAVI], subject="New layout",
                         body="An edited text.")

    assert edited["html"] == "none"
    assert edited["body_html"] is None
    row = _row(m.admin, local)
    assert row.body_html is None
    assert row.body_text.startswith("An edited text.")
    assert _shape(_sent(m)) == "text/plain"


# ── acceptance 9 and 12: Outlook behaves as on main ─────────────────────────

OUTLOOK_HTML = "<p>The HTML that Outlook gave.</p>"


@pytest.mark.parametrize("keep_html", [False, True], ids=["plain", "keep-html"])
async def test_an_outlook_draft_calls_the_provider_as_on_main(
        outlook, keep_html):
    """Acceptance 9, the save half, D-G3a-f7-5. The gateway reads
    ``keep_html`` for a Gmail mailbox only. So an Outlook update gets the
    arguments of main, with no ``body_html``, and no read of the body."""
    m = outlook
    local = _seed_draft(m.admin, org=m.org, account_id=m.aid, pmid="AAMk-1",
                        body_html=OUTLOOK_HTML)

    await _save(m, draft_id=local, to=[RAVI], cc=["lead@em-g3a-f7.test"],
                subject="Quote", body="v2", keep_html=keep_html)

    [provider] = m.providers
    provider.update_draft.assert_awaited_once_with(
        "AAMk-1", to=[RAVI], subject="Quote", body_text="v2", thread_id="t-c",
        cc=["lead@em-g3a-f7.test"], bcc=[], attachments=None)
    provider.get_message_body.assert_not_awaited()


async def test_an_outlook_save_leaves_body_html_as_on_main(outlook):
    """Acceptance 12. On main the upsert never writes ``body_html``. An
    Outlook save, with and without ``keep_html``, leaves it as it was."""
    m = outlook
    local = _seed_draft(m.admin, org=m.org, account_id=m.aid, pmid="AAMk-1",
                        body_html=OUTLOOK_HTML)

    await _save(m, draft_id=local, to=[RAVI], subject="Quote", body="v2")
    assert _row(m.admin, local).body_html == OUTLOOK_HTML
    await _save(m, draft_id=local, to=[RAVI], subject="Quote", body="v3",
                keep_html=True)

    row = _row(m.admin, local)
    assert row.body_html == OUTLOOK_HTML
    assert row.body_text.startswith("v3")


def _gmail_creating(pmid: str) -> GmailProvider:
    """A Gmail provider whose new draft gets ``pmid``, the key of a row that
    already holds HTML. So the upsert of the caller updates that row."""
    gmail = _provider()
    gmail.create_draft = AsyncMock(return_value=pmid)  # type: ignore[method-assign]
    return gmail


async def test_the_ai_mirror_and_drafts_save_never_clear_html(
        promoted, app_engine, monkeypatch):  # noqa: F811
    """Acceptance 12, M9, D-G3a-f7-8. The mirror of the AI drafter and
    ``/drafts/save`` pass no ``clear_html``, so they write the row of main,
    also for a Gmail mailbox. Each upsert lands on a row that holds HTML, and
    the HTML stays."""
    _assert_non_priv(app_engine)
    m = _box(promoted, monkeypatch, "gmail", lambda: _gmail_creating("m-x"))
    try:
        mail = _seed_mail(m.admin, org=m.org, account_id=m.aid, pmid="i-1")
        local = _seed_draft(m.admin, org=m.org, account_id=m.aid, pmid="m-x",
                            body_html=STORED_HTML)

        async def _context(*_a: Any, **_kw: Any) -> dict[str, Any]:
            return {"subject": "Quote", "from": RAVI, "thread_id": "t-r",
                    "self": m.owner, "body": "Can you check?"}

        async def _about(*_a: Any, **_kw: Any) -> tuple[str, str]:
            return "", ""

        async def _draft(*_a: Any, **_kw: Any) -> str:
            return "The AI reply."

        async def _keep(*_a: Any, **_kw: Any) -> None:
            return None

        monkeypatch.setattr(drafting, "_build_reply_context", _context)
        monkeypatch.setattr(drafting, "_load_assistant_about", _about)
        monkeypatch.setattr(drafting, "_agent_draft_reply", _draft)
        monkeypatch.setattr(drafting, "_store_ai_draft", _keep)
        async with _as_member(m):
            ai = await drafting.draft_reply_smart(drafting.DraftReplyRequest(
                account_id=m.aid, message_id=mail, create_draft=True),
                user=m.me)

        assert ai["created"] is True
        row = _row(m.admin, local)
        assert (row.provider_message_id, row.folder, row.subject) == (
            "m-x", "drafts", "Re: Quote")
        assert row.body_text.startswith("The AI reply.")
        assert row.body_html == STORED_HTML, "the AI mirror cleared the HTML"

        async with _as_member(m):
            saved = await drafting.save_draft(drafting.SaveDraftRequest(
                account_id=m.aid, message_id=mail, body="The card text."),
                user=m.me)

        assert saved == {"created": True, "id": local}
        row = _row(m.admin, local)
        assert row.body_text.startswith("The card text.")
        assert row.body_html == STORED_HTML, "/drafts/save cleared the HTML"
    finally:
        _drop(m)


# ── acceptance 13: the read runs inside the one session of the route ───────


async def test_the_html_read_runs_inside_the_route_session(gmail, monkeypatch):
    """Acceptance 13, the save half, D-G3a-f7-6a. The route opens ONE
    ``_tenant_session``, as on main, and the Gmail read runs inside it."""
    m = gmail
    local = _gmail_draft(m, None)
    real_session = drafting._tenant_session
    real_read = GmailProvider.get_message_body
    opened: list[str] = []
    depth = [0]
    read_at: list[int] = []

    @asynccontextmanager
    async def _counted(*a: Any, **kw: Any) -> AsyncIterator[Any]:
        opened.append("open")
        async with real_session(*a, **kw) as db:
            depth[0] += 1
            try:
                yield db
            finally:
                depth[0] -= 1

    async def _read(self: GmailProvider, mid: str) -> Any:
        read_at.append(depth[0])
        return await real_read(self, mid)

    monkeypatch.setattr(drafting, "_tenant_session", _counted)
    monkeypatch.setattr(GmailProvider, "get_message_body", _read)

    saved = await _save(m, draft_id=local, to=[RAVI], subject="New layout",
                        body=TEXT, keep_html=True)

    assert saved["html"] == "kept"
    assert opened == ["open"], "the save opened a second session"
    assert read_at == [1], "the Gmail read ran outside the session of the route"


# ── acceptance 14: only a Gmail save answers ``html`` ───────────────────────


async def test_only_a_gmail_save_answers_html(gmail, monkeypatch):
    """Acceptance 14, D-G3a-f7-2. A Gmail save answers ``html``: ``none``
    with no ``keep_html``, ``kept`` with it. An Outlook save answers the keys
    of main, with no ``html``."""
    m = gmail
    local = _gmail_draft(m, None)

    plain = await _save(m, draft_id=local, to=[RAVI], subject="New layout",
                        body=TEXT)
    new = await _save(m, to=[RAVI], subject="Hello", body="v1")
    assert (plain["html"], new["html"]) == ("none", "none")

    m.fake.add_raw_draft("r-d", "m-d", _gmail_web_draft(), thread="t-d")
    other = _seed_draft(m.admin, org=m.org, account_id=m.aid, pmid="m-d",
                        body_html=None)
    kept = await _save(m, draft_id=other, to=[RAVI], subject="New layout",
                       body=TEXT, keep_html=True)
    assert kept["html"] == "kept"

    m.providers.clear()
    outlook_box = _box(m.p, monkeypatch, "outlook", _outlook)
    try:
        answer = await _save(outlook_box, to=[RAVI], subject="Quote", body="v1",
                             keep_html=True)
    finally:
        _drop(outlook_box)

    assert "html" not in answer
    assert set(answer) == set(plain) - {"html"}


# ── PR-C1b: the signed send keeps the HTML (acceptance 7 to 11, 13) ─────────

#: An HTML signature. Its text is ``Asha | Contoso``.
SIG = "<b>Asha</b> | Contoso"
SIG_TEXT = "Asha | Contoso"
#: The HTML of a Gmail-web reply, as the sync stores it in the row.
QUOTED_HTML = (STORED_HTML + '<div class="gmail_quote"><div>On Mon, Ravi '
               "wrote:</div><blockquote>Can you send it?</blockquote></div>")


async def _send(m: SimpleNamespace, local_id: str, **fields: Any) -> Any:
    """One POST /email/drafts/send of the member: the real route."""
    req = drafting.DraftSendRequest(account_id=m.aid, draft_id=local_id,
                                    **fields)
    async with _as_member(m):
        return await drafting.send_draft_endpoint(
            req, BackgroundTasks(), user=m.me)


def _parts(msg: Any) -> tuple[str, str]:
    """The text and the HTML of a sent mail, at any depth."""
    found = {p.get_content_type(): p.get_payload(decode=True).decode()
             for p in msg.walk() if p.get_content_type() in
             ("text/plain", "text/html")}
    return found.get("text/plain", ""), found.get("text/html", "")


def _spy_updates(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The keyword arguments of each Gmail ``update_draft``."""
    seen: list[dict[str, Any]] = []
    real = GmailProvider.update_draft

    async def _update(self: GmailProvider, draft_id: str, **kw: Any) -> Any:
        seen.append(kw)
        return await real(self, draft_id, **kw)

    monkeypatch.setattr(GmailProvider, "update_draft", _update)
    return seen


def _web_draft_with_html(html: str) -> bytes:
    """The Gmail-web draft of the fixture, with ``html`` as its HTML part."""
    mail = message_from_bytes(_gmail_web_draft())
    for part in mail.walk():
        if part.is_multipart():
            part.set_payload([
                MIMEText(html, "html", "utf-8")
                if sub.get_content_type() == "text/html" else sub
                for sub in part.get_payload()])
    return mail.as_bytes()


def _fail_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fails(self: GmailProvider, _mid: str) -> Any:
        raise RuntimeError("the read failed")

    monkeypatch.setattr(GmailProvider, "get_message_body", _fails)


@pytest.mark.parametrize("stored", [QUOTED_HTML, None],
                         ids=["row-html", "gmail-html"])
async def test_a_signed_send_keeps_the_html_and_the_inline_parts(gmail, stored):
    """Acceptance 7, M3, D-G3a-f7-7. The signed send takes the HTML of the
    draft, from the row or from Gmail, and signs it with ``sign_html``. The
    update keeps the inline image with its ``Content-ID``. The signature
    shows once, above the quote."""
    m = gmail
    _sign(m, SIG)
    # Gmail holds the quoted HTML too, so the Gmail-read case has a quote.
    m.fake.add_raw_draft("r-c", "m-c", _web_draft_with_html(QUOTED_HTML),
                         thread="t-c")
    local = _gmail_draft(m, stored)

    assert await _send(m, local) == {"sent": True}

    msg = _sent(m)
    assert _shape(msg) == _RELATED
    image = msg.get_payload()[1]
    assert image["Content-ID"] == f"<{CID}>"
    assert image.get_payload(decode=True) == PNG
    body_text, html = _parts(msg)
    assert f'src="cid:{CID}"' in html
    assert html.count(SIG) == 1
    assert body_text.count(SIG_TEXT) == 1
    assert html.index(SIG) < html.index("gmail_quote"), "the signature is below the quote"
    assert _full_reads(m.fake, "m-c") == (0 if stored else 1)
    assert m.fake.sent_drafts == ["r-c"]
    assert _row(m.admin, local) is None, "the sent draft kept its row"


async def test_a_kept_draft_goes_out_with_the_signature_once(gmail):
    """The note of review round 1 of PR-C1a. A save with ``keep_html`` puts
    the signature in the text only, so the kept HTML has none. The send adds
    it to the HTML, and the text keeps its one copy."""
    m = gmail
    _sign(m, SIG)
    local = _gmail_draft(m, None)
    saved = await _save(m, draft_id=local, to=[RAVI], subject="New layout",
                        body=TEXT, keep_html=True)
    assert saved["html"] == "kept"
    assert SIG not in _parts(_sent(m))[1]

    await _send(m, local)

    body_text, html = _parts(_sent(m))
    assert html.count(SIG) == 1
    assert body_text.count(SIG_TEXT) == 1
    assert _shape(_sent(m)) == _RELATED


async def test_a_signed_send_after_a_text_edit_sends_the_new_text(gmail):
    """Acceptance 8, M4. A text save clears the stored HTML, and Gmail holds
    a text draft. So the signed send sends the new text, and no HTML of the
    text from before."""
    m = gmail
    _sign(m, SIG)
    local = _gmail_draft(m, STORED_HTML)
    await _save(m, draft_id=local, to=[RAVI], subject="New layout",
                body="An edited text.")

    await _send(m, local)

    msg = _sent(m)
    assert _shape(msg) == ("multipart/alternative", ["text/plain", "text/html"])
    body_text, html = _parts(msg)
    assert "An edited text." in html and "An edited text." in body_text
    assert "Please find the layout" not in html
    assert "cid:" not in html
    assert html.count(SIG) == 1


async def test_a_signed_send_with_no_html_anywhere_is_main(gmail, monkeypatch):
    """Acceptance 10, M12, D-G3a-f7-10. A draft made in Metorite is
    ``text/plain``, so neither the row nor Gmail holds HTML. The send signs
    with ``build_signed_bodies(signature, body_text, None)``, as main does.
    It costs one more Gmail read."""
    m = gmail
    _sign(m, SIG)
    first = await _save(m, to=[RAVI], subject="Quote", body="v1")
    mid = first["provider_message_id"]
    row_text = _row(m.admin, first["id"]).body_text
    updates = _spy_updates(monkeypatch)

    await _send(m, first["id"])

    want_text, want_html = build_signed_bodies(SIG, row_text, None)
    [update] = updates
    assert (update["body_text"], update["body_html"]) == (want_text, want_html)
    assert _full_reads(m.fake, mid) == 1
    assert len(m.fake.sent_drafts) == 1


async def test_a_failed_read_at_the_send_answers_409_and_sends_nothing(
        gmail, monkeypatch):
    """Acceptance 11, M8, D-G3a-f7-11. The HTML read fails, and the client
    says ``allow_text_only: false``. The send answers 409 with the reason
    ``html_unavailable``. Gmail gets no update and no send, and the row
    stays."""
    m = gmail
    _sign(m, SIG)
    local = _gmail_draft(m, None)
    _fail_reads(monkeypatch)

    with pytest.raises(HTTPException) as caught:
        await _send(m, local, allow_text_only=False)

    assert caught.value.status_code == 409
    assert caught.value.detail == {"reason": "html_unavailable"}
    assert m.fake.saved == [] and m.fake.updated == []
    assert m.fake.sent_drafts == []
    assert _row(m.admin, local).provider_message_id == "m-c"


async def _sends_the_text(m: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
                          **fields: Any) -> None:
    _sign(m, SIG)
    local = _gmail_draft(m, None)
    _fail_reads(monkeypatch)
    updates = _spy_updates(monkeypatch)

    assert await _send(m, local, **fields) == {"sent": True}

    want_text, want_html = build_signed_bodies(SIG, TEXT, None)
    [update] = updates
    assert (update["body_text"], update["body_html"]) == (want_text, want_html)
    assert _shape(_sent(m)) == ("multipart/alternative",
                                ["text/plain", "text/html"])
    assert m.fake.sent_drafts == ["r-c"]


async def test_allow_text_only_sends_the_text(gmail, monkeypatch):
    """Acceptance 11, D-G3a-f7-11. On the next press the client says
    ``allow_text_only: true``, and the send goes on with the text only."""
    await _sends_the_text(gmail, monkeypatch, allow_text_only=True)


async def test_an_old_client_send_with_a_failed_read_sends_the_text(
        gmail, monkeypatch):
    """Acceptance 11, D-G3a-f7-11a. A client before PR-C2, and the assistant
    tool ``send_draft``, send no ``allow_text_only``. A failed read then sends
    the text only, as main does."""
    await _sends_the_text(gmail, monkeypatch)


@pytest.mark.parametrize("allow_text_only", [None, False, True],
                         ids=["old-client", "first-press", "next-press"])
async def test_an_outlook_signed_send_calls_the_provider_as_on_main(
        outlook, allow_text_only):
    """Acceptance 9, the send half, D-G3a-f7-5. An Outlook signed send reads
    no HTML, and signs the text as main does, whatever the client says. The
    row HTML does not go to Outlook."""
    m = outlook
    _sign(m, SIG)
    local = _seed_draft(m.admin, org=m.org, account_id=m.aid, pmid="AAMk-1",
                        body_html=OUTLOOK_HTML)

    await _send(m, local, allow_text_only=allow_text_only)

    [provider] = m.providers
    want_text, want_html = build_signed_bodies(SIG, TEXT, None)
    provider.update_draft.assert_awaited_once_with(
        "AAMk-1", to=[RAVI], cc=None, bcc=None, subject="New layout",
        body_text=want_text, body_html=want_html, thread_id="t-c")
    provider.send_draft.assert_awaited_once_with("AAMk-1")
    provider.get_message_body.assert_not_awaited()


async def test_the_html_read_runs_inside_the_send_session(gmail, monkeypatch):
    """Acceptance 13, the send half, D-G3a-f7-6a. The Gmail read runs inside
    the session of the route. The route opens the sessions of main: its own,
    and the one that moves the row before the send (E-A1)."""
    m = gmail
    _sign(m, SIG)
    local = _gmail_draft(m, None)
    real_session = drafting._tenant_session
    real_read = GmailProvider.get_message_body
    opened: list[int] = []
    depth = [0]
    read_at: list[int] = []

    @asynccontextmanager
    async def _counted(*a: Any, **kw: Any) -> AsyncIterator[Any]:
        opened.append(depth[0])
        async with real_session(*a, **kw) as db:
            depth[0] += 1
            try:
                yield db
            finally:
                depth[0] -= 1

    async def _read(self: GmailProvider, mid: str) -> Any:
        read_at.append(depth[0])
        return await real_read(self, mid)

    monkeypatch.setattr(drafting, "_tenant_session", _counted)
    monkeypatch.setattr(GmailProvider, "get_message_body", _read)

    await _send(m, local)

    assert opened == [0, 1], "the send opened a session that main does not"
    assert read_at == [1], "the Gmail read ran outside the session of the route"
