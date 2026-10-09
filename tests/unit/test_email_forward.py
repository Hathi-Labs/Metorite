"""Forward an email with its original files, and link to an email in chat.

Owner report, 2026-10-09: the owner asked the assistant to forward a BQ email
with its PDF to a colleague. It answered that its send tool "has no forward
operation that re-attaches the original PDF", and that there is "no shareable
link to the original email, only an internal id". Spec:
``project-docs/specs/email_app_master_plan.md`` §15.

R7 fences named here, each a test class:

* ``email-forward-route`` (:class:`TestTheForwardRoute`). The bytes of each
  file reach ``send_message``, through the one owned fetch of a file. The
  subject is normalised. The body holds the note, the forwarded header and the
  original, and the original HTML is kept. A mail of another member is 404
  before any fetch. A named file of another mail is 404. A named mailbox that
  does not hold the mail is 404. The size cap holds before the fetch and after
  it. A file with no bytes stops the forward. An IMAP mailbox forwards no file.
* ``email-forward-subject`` (:class:`TestTheSubject`). ``Fwd:``, once.
* ``email-forward-tool`` (:class:`TestTheForwardTool`). ``forward_email`` is
  registered and in ``own_tool_scope``, shows a card that names each file and
  each hidden recipient before it posts, sends nothing on a "no", and refuses
  a mailbox that does not hold the mail. ``instructions.md`` says when to
  forward and how to cite an email.
* ``email-chat-links`` (:class:`TestTheLinks`). The list and read tools print
  a ready ``link=``, in the shape of ``emailLink`` in the workbench, and the
  orchestrator keeps a sub-agent's link as it is.

The R8 half is ``test_email_forward_r8.py``.

Run::

    uv run pytest tests/unit/test_email_forward.py -v
"""
from __future__ import annotations

import importlib.util
import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from acb_auth.roles import UserContext, UserRole
from fastapi import HTTPException
from gateway.routes.email.transport import attachments as att_mod
from gateway.routes.email.transport import forward as fwd

from tests.unit._sql_match import hits

REPO = Path(__file__).resolve().parents[2]
OWNER = "dana@em-fwd.test"
OTHER = "mallory@em-fwd.test"
MAIL = "0f8fad5b-d9cb-469f-a165-70867728950e"
BOX = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
PDF = "33333333-3333-4333-8333-333333333333"
SHEET = "44444444-4444-4444-8444-444444444444"
ELSEWHERE = "55555555-5555-4555-8555-555555555555"
MB = 1024 * 1024


# ── The hermetic harness ────────────────────────────────────────────────────


class _Harness:
    """Patches the session, the two provider sessions and nothing else.

    ``_fetch_owned_attachment`` runs for real, so the bytes that reach the send
    are the bytes of the ONE owned fetch. The fake database honours each owner
    predicate as text: a row goes back only to its owner.
    """

    def __init__(  # noqa: C901 - the fakes close over one harness
        self, monkeypatch: pytest.MonkeyPatch, *, files: dict[str, bytes] | None = None,
        sizes: dict[str, int | None] | None = None, provider: str = "microsoft",
        body_html: str | None = "<html><body><p>The <b>BQ</b> quote.</p></body></html>",
        body_text: str = "The BQ quote.", signature: str = "",
        native: bool = False, send_error: BaseException | None = None,
    ) -> None:
        self.files = files if files is not None else {PDF: b"%PDF-1.7 quote", SHEET: b"PK sheet"}
        self.sizes = sizes or {}
        self.sent: list[dict[str, Any]] = []
        self.forwarded: list[dict[str, Any]] = []
        self.fetched: list[str] = []
        self.body_reads = 0
        self.row = SimpleNamespace(
            id=MAIL, account_id=BOX, provider_message_id="pm-1",
            subject="Re: Subject: BQ quote for the extruder",
            from_address={"name": "Ravi", "email": "ravi@contoso.test"},
            to_addresses=[{"name": "Dana", "email": OWNER}],
            cc_addresses=[],
            received_at=datetime(2026, 10, 8, 10, 0, tzinfo=UTC),
            body_text=body_text, body_html=body_html, snippet="The BQ quote.",
            provider=provider,
        )
        names = {PDF: "quote.pdf", SHEET: "rates.xlsx"}
        self.att_rows = {
            aid: SimpleNamespace(
                id=aid, filename=names.get(aid, "file.bin"), mime_type="application/pdf",
                size_bytes=self.sizes.get(aid, len(data)),
                provider_attachment_id=f"prov-{aid[:4]}", storage_path=None,
                provider_message_id="pm-1", account_id=BOX, provider=provider,
            )
            for aid, data in self.files.items()
        }
        self.signature = signature
        h = self

        class _Result:
            def __init__(self, one: Any = None, many: list[Any] | None = None) -> None:
                self._one, self._many = one, many or []

            def fetchone(self) -> Any:
                return self._one

            def fetchall(self) -> list[Any]:
                return self._many

        class _Db:
            async def execute(self, statement: Any, params: dict[str, Any]) -> Any:
                sql = str(statement)
                if hits(sql, "FROM email_messages em") and "ea.user_id = :uid" in sql:
                    return _Result(h.row if params["uid"] == OWNER and params["mid"] == MAIL else None)
                if hits(sql, "FROM email_attachments") and "WHERE message_id" in sql:
                    return _Result(many=list(h.att_rows.values()) if params["mid"] == MAIL else [])
                if hits(sql, "FROM email_attachments ea") and "p.user_id = :user_id" in sql:
                    row = h.att_rows.get(params["aid"])
                    return _Result(row if params["user_id"] == OWNER else None)
                if hits(sql, "FROM email_assistant_settings"):
                    return _Result(SimpleNamespace(signature=h.signature) if h.signature else None)
                raise AssertionError(f"unexpected SQL: {sql}")

        @asynccontextmanager
        async def _tenant_session():
            yield _Db()

        class _AttProvider:
            async def get_attachment(self, _msg: str, att: str) -> bytes:
                aid = next(a for a, r in h.att_rows.items() if r.provider_attachment_id == att)
                h.fetched.append(aid)
                return h.files[aid]

        @asynccontextmanager
        async def _att_session(*_a: Any, **_k: Any):
            yield SimpleNamespace(provider=_AttProvider())

        class _SendProvider:
            forwards_natively = native

            async def send_message(self, **kw: Any) -> str:
                if send_error is not None:
                    raise send_error
                h.sent.append(kw)
                return "sent-1"

            async def forward_message(self, pmid: str, to: list[str], **kw: Any) -> None:
                if send_error is not None:
                    raise send_error
                h.forwarded.append({"pmid": pmid, "to": to, **kw})

            async def get_message_body(self, _pmid: str) -> Any:
                h.body_reads += 1
                return SimpleNamespace(body_text="Body from the provider.",
                                       body_html="<p>HTML from the provider.</p>")

        @asynccontextmanager
        async def _send_session(db: Any, user_email: str, account_id: str):
            assert user_email == OWNER and account_id == BOX
            yield SimpleNamespace(provider=_SendProvider())

        monkeypatch.setattr(fwd, "_tenant_session", _tenant_session)
        monkeypatch.setattr(fwd, "provider_session", _send_session)
        monkeypatch.setattr(att_mod, "provider_session", _att_session)


def _user(email: str = OWNER) -> UserContext:
    # No organization: the owned fetch then reads and writes no cache.
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=None)


async def _forward(user: UserContext | None = None, **body: Any) -> dict[str, Any]:
    req = fwd.ForwardEmailRequest(**{"message_id": MAIL, "to": ["geo@fracktal.test"], **body})
    return await fwd.forward_email(req, user=user or _user())


# ── The route ───────────────────────────────────────────────────────────────


class TestTheForwardRoute:
    async def test_the_bytes_of_each_file_reach_the_send(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        out = await _forward(note="Geo, see the quote below.")
        assert out["ok"] is True and out["id"] == "sent-1"
        [call] = h.sent
        assert [(a["filename"], a["content"]) for a in call["attachments"]] == [
            ("quote.pdf", b"%PDF-1.7 quote"), ("rates.xlsx", b"PK sheet"),
        ]
        assert sorted(h.fetched) == sorted([PDF, SHEET]), "each file went through the owned fetch"
        assert call["to"] == ["geo@fracktal.test"]
        assert call["reply_to_message_id"] is None and call["thread_id"] is None
        assert out["attachments"] == ["quote.pdf", "rates.xlsx"]
        assert out["bytes"] == len(b"%PDF-1.7 quote") + len(b"PK sheet")

    async def test_the_subject_and_the_body_are_a_forward(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        await _forward(note="Geo, see the quote below.")
        [call] = h.sent
        assert call["subject"] == "Fwd: BQ quote for the extruder"
        text = call["body_text"]
        assert text.index("Geo, see the quote below.") < text.index("---------- Forwarded message ---------")
        assert "From: Ravi <ravi@contoso.test>" in text
        assert "Subject: Re: Subject: BQ quote for the extruder" in text
        assert f"To: Dana <{OWNER}>" in text
        assert "Date: Thu, 08 Oct 2026 at 10:00" in text
        assert text.rstrip().endswith("The BQ quote.")
        html = call["body_html"]
        assert "<p>The <b>BQ</b> quote.</p>" in html, "the original HTML is kept"
        assert "<html>" not in html and "<body>" not in html
        assert 'class="gmail_quote"' in html
        assert "From: Ravi &lt;ravi@contoso.test&gt;" in html, "a header value is escaped"

    async def test_the_signature_goes_under_the_note(self, monkeypatch) -> None:
        h = _Harness(monkeypatch, signature="Dana\nFracktal Works")
        await _forward(note="See below.")
        text = h.sent[0]["body_text"]
        assert text.index("See below.") < text.index("Fracktal Works") < text.index("Forwarded message")

    async def test_a_mail_of_another_member_is_404_before_any_fetch(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            await _forward(user=_user(OTHER))
        assert exc.value.status_code == 404
        assert h.fetched == [] and h.sent == []

    async def test_a_named_file_of_another_mail_is_404(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            await _forward(attachment_ids=[PDF, ELSEWHERE])
        assert exc.value.status_code == 404
        assert h.fetched == [] and h.sent == []

    async def test_a_mailbox_that_does_not_hold_the_mail_is_404(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            await _forward(account_id="11111111-2222-4333-8444-555555555555")
        assert exc.value.status_code == 404
        assert exc.value.detail == fwd.FORWARD_NOT_IN_MAILBOX
        assert h.sent == []

    async def test_named_files_go_alone_and_no_files_go_when_asked(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        await _forward(attachment_ids=[SHEET.upper()])
        assert [a["filename"] for a in h.sent[-1]["attachments"]] == ["rates.xlsx"]
        await _forward(include_attachments=False)
        assert h.sent[-1]["attachments"] is None
        assert h.fetched == [SHEET], "a forward without files fetches no file"

    async def test_the_stored_size_over_the_cap_is_413_before_any_fetch(self, monkeypatch) -> None:
        h = _Harness(monkeypatch, sizes={PDF: 20 * MB, SHEET: 6 * MB})
        with pytest.raises(HTTPException) as exc:
            await _forward()
        assert exc.value.status_code == 413
        assert "25.0 MB" in exc.value.detail
        assert h.fetched == [] and h.sent == []

    async def test_bytes_over_the_cap_are_413_after_the_fetch(self, monkeypatch) -> None:
        big = b"x" * (fwd.MAX_FORWARD_BYTES + 1)
        h = _Harness(monkeypatch, files={PDF: big}, sizes={PDF: None})
        with pytest.raises(HTTPException) as exc:
            await _forward()
        assert exc.value.status_code == 413
        assert h.sent == []

    async def test_a_file_with_no_bytes_stops_the_forward(self, monkeypatch) -> None:
        h = _Harness(monkeypatch, files={PDF: b""}, sizes={PDF: None})
        with pytest.raises(HTTPException) as exc:
            await _forward()
        assert exc.value.status_code == 422
        assert "quote.pdf" in exc.value.detail
        assert h.sent == []

    async def test_an_imap_mailbox_forwards_no_file(self, monkeypatch) -> None:
        h = _Harness(monkeypatch, provider="imap")
        with pytest.raises(HTTPException) as exc:
            await _forward()
        assert exc.value.status_code == 422
        assert h.fetched == [] and h.sent == []
        await _forward(include_attachments=False)
        assert len(h.sent) == 1

    async def test_a_cold_mail_takes_its_html_from_the_provider(self, monkeypatch) -> None:
        h = _Harness(monkeypatch, body_html=None)
        await _forward()
        assert h.body_reads == 1
        assert "<p>HTML from the provider.</p>" in h.sent[0]["body_html"]

    async def test_a_hot_mail_reads_no_body_from_the_provider(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        await _forward()
        assert h.body_reads == 0

    async def test_a_malformed_id_is_404_and_no_recipient_is_422(self, monkeypatch) -> None:
        _Harness(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            await fwd.forward_email(
                fwd.ForwardEmailRequest(message_id="../admin", to=["a@b.test"]), user=_user())
        assert exc.value.status_code == 404
        with pytest.raises(HTTPException) as exc:
            await _forward(to=["  "])
        assert exc.value.status_code == 422

    def test_the_route_is_mounted_under_the_email_feature(self) -> None:
        from gateway.routes.email import router
        assert "/email/forward" in [r.path for r in router.routes]


def _graph_error(status: int, code: str) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://graph.microsoft.com/v1.0/me/sendMail?secret=1")
    resp = httpx.Response(status, json={"error": {"code": code, "message": "secret text"}},
                          request=req)
    return httpx.HTTPStatusError("boom https://graph.microsoft.com/v1.0/me", request=req,
                                 response=resp)


class TestTheOutlookPath:
    """P1-b of review round 1. Graph's sendMail takes files inline and refuses
    a body over about 4 MB, so an Outlook forward of a real PDF failed AFTER
    the member approved it, with a 500."""

    async def test_every_file_goes_by_graph_forward_with_no_fetch_and_no_cap(self, monkeypatch) -> None:
        h = _Harness(monkeypatch, native=True, sizes={PDF: 40 * MB, SHEET: 1 * MB})
        out = await _forward(note="See below.", cc=["c@fracktal.test"], bcc=["b@fracktal.test"])
        assert h.sent == [] and h.fetched == [], "no byte of a file passed through Metorite"
        [call] = h.forwarded
        assert call["pmid"] == "pm-1" and call["to"] == ["geo@fracktal.test"]
        assert call["cc"] == ["c@fracktal.test"] and call["bcc"] == ["b@fracktal.test"]
        assert call["comment"] == "See below."
        assert call["subject"] == "Fwd: BQ quote for the extruder"
        assert out["attachments"] == ["quote.pdf", "rates.xlsx"]

    async def test_a_subset_of_the_files_on_outlook_is_422(self, monkeypatch) -> None:
        h = _Harness(monkeypatch, native=True)
        with pytest.raises(HTTPException) as exc:
            await _forward(attachment_ids=[PDF])
        assert exc.value.status_code == 422
        assert exc.value.detail == fwd.OUTLOOK_SUBSET
        assert h.sent == [] and h.forwarded == [] and h.fetched == []

    async def test_every_file_named_by_id_is_still_the_native_path(self, monkeypatch) -> None:
        h = _Harness(monkeypatch, native=True)
        await _forward(attachment_ids=[SHEET, PDF])
        assert len(h.forwarded) == 1 and h.sent == []

    async def test_no_files_on_outlook_rebuilds_the_mail_without_files(self, monkeypatch) -> None:
        h = _Harness(monkeypatch, native=True)
        await _forward(include_attachments=False)
        assert h.forwarded == [] and h.sent[0]["attachments"] is None

    @pytest.mark.parametrize(("status", "code", "answer"), [
        (400, "ErrorInvalidRecipients", 422),
        (403, "ErrorAccessDenied", 422),
        (404, "ErrorItemNotFound", 404),
        (429, "ApplicationThrottled", 429),
        (503, "ServiceUnavailable", 502),
    ])
    async def test_a_provider_error_is_a_reason_never_a_500(
        self, monkeypatch, status: int, code: str, answer: int,
    ) -> None:
        _Harness(monkeypatch, native=True, send_error=_graph_error(status, code))
        with pytest.raises(HTTPException) as exc:
            await _forward()
        assert exc.value.status_code == answer
        assert "graph.microsoft.com" not in exc.value.detail
        assert "secret" not in exc.value.detail
        if answer == 422:
            assert code in exc.value.detail

    async def test_a_gmail_refusal_on_the_rebuild_is_a_reason_too(self, monkeypatch) -> None:
        _Harness(monkeypatch, send_error=_graph_error(400, "failedPrecondition"))
        with pytest.raises(HTTPException) as exc:
            await _forward()
        assert exc.value.status_code == 422

    async def test_a_network_failure_is_502(self, monkeypatch) -> None:
        _Harness(monkeypatch, send_error=httpx.ConnectError("down"))
        with pytest.raises(HTTPException) as exc:
            await _forward()
        assert exc.value.status_code == 502

    async def test_too_large_at_graph_is_413(self, monkeypatch) -> None:
        from email_ingestion.providers.outlook import OutlookMailTooLarge
        _Harness(monkeypatch, native=True, send_error=OutlookMailTooLarge(0, None))
        with pytest.raises(HTTPException) as exc:
            await _forward()
        assert exc.value.status_code == 413


class TestTheGraphForward:
    """``OutlookProvider.forward_message`` against a stubbed Graph."""

    @staticmethod
    def _provider(handler: Any) -> Any:
        from email_ingestion.providers.outlook import GRAPH_API_BASE, OutlookProvider
        p = OutlookProvider({"access_token": "a", "refresh_token": "r"})
        p._http = httpx.AsyncClient(base_url=GRAPH_API_BASE, transport=httpx.MockTransport(handler))
        return p

    async def test_it_posts_the_native_forward_with_every_recipient(self) -> None:
        seen: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return httpx.Response(202)

        p = self._provider(handler)
        assert p.forwards_natively is True
        out = await p.forward_message("AAMk=1", ["geo@f.test"], cc=["c@f.test"],
                                      bcc=["b@f.test"], comment="See below.", subject="Fwd: X")
        assert out is None
        [req] = seen
        assert req.method == "POST"
        assert req.url.path == "/v1.0/me/messages/AAMk=1/forward"
        body = json.loads(req.content)
        assert body["comment"] == "See below."
        msg = body["message"]
        assert [r["emailAddress"]["address"] for r in msg["toRecipients"]] == ["geo@f.test"]
        assert [r["emailAddress"]["address"] for r in msg["ccRecipients"]] == ["c@f.test"]
        assert [r["emailAddress"]["address"] for r in msg["bccRecipients"]] == ["b@f.test"]
        assert msg["subject"] == "Fwd: X"
        assert "attachments" not in msg, "Graph keeps the files at the server"

    async def test_a_413_raises_the_typed_too_large_error(self) -> None:
        from email_ingestion.providers.base import ProviderMailTooLarge
        p = self._provider(lambda req: httpx.Response(413))
        with pytest.raises(ProviderMailTooLarge):
            await p.forward_message("m", ["geo@f.test"])

    async def test_a_send_that_graph_refuses_as_too_large_is_typed_too(self) -> None:
        from email_ingestion.providers.outlook import OutlookMailTooLarge
        p = self._provider(lambda req: httpx.Response(
            400, json={"error": {"code": "ErrorMessageSizeExceeded"}}))
        with pytest.raises(OutlookMailTooLarge):
            await p.send_message(["geo@f.test"], "S", "body",
                                 attachments=[{"filename": "a.pdf", "content": b"x"}])

    async def test_another_refusal_stays_an_http_error(self) -> None:
        p = self._provider(lambda req: httpx.Response(
            400, json={"error": {"code": "ErrorInvalidRecipients"}}))
        with pytest.raises(httpx.HTTPStatusError):
            await p.forward_message("m", ["geo@f.test"])


class TestTheSubject:
    @pytest.mark.parametrize(("subject", "expected"), [
        ("BQ quote", "Fwd: BQ quote"),
        ("Fwd: BQ quote", "Fwd: BQ quote"),
        ("FW: fwd: BQ quote", "Fwd: BQ quote"),
        ("Re: Subject: BQ quote", "Fwd: BQ quote"),
        ("RE[2]: Re: BQ quote", "Fwd: BQ quote"),
        ("  ", "Fwd: (no subject)"),
        (None, "Fwd: (no subject)"),
        ("Rework of the BQ quote", "Fwd: Rework of the BQ quote"),
    ])
    def test_fwd_once(self, subject: str | None, expected: str) -> None:
        assert fwd.forward_subject(subject) == expected


# ── The agent tool ──────────────────────────────────────────────────────────

_AGENT_DIR = REPO / "apps" / "agents" / "agent-email-assistant"


def _load_agents() -> Any:
    spec = importlib.util.spec_from_file_location("ea_forward", _AGENT_DIR / "agents.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load_agents()

_ACCOUNTS = [
    {"id": BOX, "email_address": "dana@fracktal.in", "label": "Fracktal"},
    {"id": "box-b", "email_address": "dana@outlook.com", "label": "Personal"},
]


@pytest.fixture()
def chat(monkeypatch):
    rec = SimpleNamespace(posts=[], cards=[], answer=True)

    async def fake_get(path, params=None):
        if path == "/email/accounts":
            return _ACCOUNTS
        if path == f"/email/messages/{MAIL}":
            return {
                "id": MAIL, "account_id": BOX, "subject": "Re: BQ quote",
                "from_address": {"name": "Ravi", "email": "ravi@contoso.test"},
                "attachments": [
                    {"id": PDF, "filename": "quote.pdf", "size_bytes": 2 * MB},
                    {"id": SHEET, "filename": "rates.xlsx", "size_bytes": 30_000},
                ],
            }
        return {}

    async def fake_post(path, body):
        rec.posts.append((path, body))
        return {"id": "sent-1", "ok": True, "subject": "Fwd: BQ quote",
                "attachments": ["quote.pdf", "rates.xlsx"]}

    async def confirm(**kw):
        rec.cards.append(kw)
        return rec.answer

    monkeypatch.setattr(agents, "_get", fake_get)
    monkeypatch.setattr(agents, "_post", fake_post)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", confirm)
    return rec


def _shown(card: dict[str, Any]) -> tuple[str, str]:
    """What the member sees: ``request_confirmation`` keeps 500 characters of
    ``detail`` and 4,000 of ``context`` (``acb_skills/ask_tools.py``)."""
    return str(card["detail"]).strip()[:500], str(card.get("context") or "").strip()[:4000]


class TestTheForwardTool:
    def test_it_is_registered_annotated_and_in_scope(self) -> None:
        assert agents.forward_email in agents._TOOLS
        config = json.loads((_AGENT_DIR / "config.json").read_text(encoding="utf-8"))
        assert "forward_email" in config["own_tool_scope"]
        # A forward leaves the company and cannot be undone: it fails closed
        # without a human (root AGENTS.md, harness rule 2).
        src = (_AGENT_DIR / "agents.py").read_text(encoding="utf-8")
        assert re.search(
            r"@_annotate_risk\(destructive=True, open_world=True\)\nasync def forward_email", src)

    async def test_the_card_names_each_target_in_context_and_the_files_in_detail(self, chat) -> None:
        out = await agents.forward_email(MAIL, ["geo@fracktal.test"], bcc=["boss@fracktal.test"],
                                         note="See the quote.")
        [card] = chat.cards
        assert card["title"] == "Forward this email?"
        detail, context = _shown(card)
        assert detail.startswith("From Fracktal · dana@fracktal.in · To geo@fracktal.test · Subject: ")
        assert detail.endswith("Attachments: quote.pdf (2.0 MB), rates.xlsx (29 KB)")
        # Every target is in ``context``, which the 500-character cut never
        # reaches, and the note follows them (verifier F1).
        assert context == (
            "The mailbox and each recipient:\n- From: Fracktal · dana@fracktal.in\n"
            "- To: geo@fracktal.test\n- Bcc: boss@fracktal.test\n\n"
            "Attachments:\n- quote.pdf (2.0 MB)\n- rates.xlsx (29 KB)\n\nSee the quote."
        )
        [(path, body)] = chat.posts
        assert path == "/email/forward"
        assert body == {
            "message_id": MAIL, "to": ["geo@fracktal.test"], "bcc": ["boss@fracktal.test"],
            "note": "See the quote.", "include_attachments": True, "account_id": BOX,
        }
        assert "with 2 attachment(s)" in out and "Fracktal · dana@fracktal.in" in out

    async def test_eight_long_file_names_cannot_hide_a_recipient(self, chat, monkeypatch) -> None:
        """Verifier F1: the sender of the mail names its files. Eight long
        names filled the 500 characters of ``detail`` and pushed From and To
        off the card."""
        files = [{"id": f"{i:08d}-0000-4000-8000-000000000000", "size_bytes": 1000,
                  "filename": f"invoice-{i}-" + "x" * 70 + ".pdf"} for i in range(8)]
        real_get = agents._get

        async def get(path, params=None):
            got = await real_get(path, params)
            return {**got, "attachments": files} if path == f"/email/messages/{MAIL}" else got

        monkeypatch.setattr(agents, "_get", get)
        await agents.forward_email(MAIL, ["geo@fracktal.test"], cc=["cc@fracktal.test"],
                                   bcc=["records@evil.test"])
        detail, context = _shown(chat.cards[0])
        for target in ("- From: Fracktal · dana@fracktal.in", "- To: geo@fracktal.test",
                       "- Cc: cc@fracktal.test", "- Bcc: records@evil.test"):
            assert target in context
        assert len(chat.cards[0]["detail"]) <= 500
        # The card clips each name a sender chose to 60 characters.
        shown = sum(1 for i in range(8) if f"invoice-{i}-" in detail)
        assert 0 < shown < 8
        assert detail.endswith(f", +{8 - shown} more")
        # ``context`` names each of the eight, one line each, between the
        # targets and the note (follow-up 2 of #766). ``detail`` keeps the
        # short form above.
        head, files = context.split("\n\nAttachments:\n", 1)
        assert "- Bcc: records@evil.test" in head
        lines = files.splitlines()
        assert len(lines) == 8 and "more" not in files
        for i, line in enumerate(lines):
            assert line.startswith(f"- invoice-{i}-") and line.endswith("(1 KB)")

    def test_a_bcc_comes_before_a_long_cc_list(self) -> None:
        """Follow-up 1 of #766: the card draws a ``context`` with a note in a
        box that scrolls, so a Bcc after 22 Cc lines sat below the fold."""
        cc = [f"cc{i:02d}@fracktal.test" for i in range(22)]
        context = agents._card_context(
            agents._card_targets("Box", to=["geo@fracktal.test"], cc=cc,
                                 bcc=["records@evil.test"]),
            "See the quote.")
        lines = context.splitlines()
        bcc_at = lines.index("- Bcc: records@evil.test")
        first_cc = next(i for i, ln in enumerate(lines) if ln.startswith("- Cc:"))
        assert lines[:bcc_at] == [
            "The mailbox and each recipient:", "- From: Box", "- To: geo@fracktal.test"]
        assert bcc_at < first_cc
        # The cap of ten and the count still hold for the Cc list.
        assert sum(1 for ln in lines if ln.startswith("- Cc: cc")) == agents._CARD_TARGET_CAP
        assert "- Cc: +12 more" in lines
        assert context.endswith("\n\nSee the quote.")

    def test_the_files_block_caps_at_twenty_and_says_how_many_more(self) -> None:
        files = [f"file-{i:02d}.pdf (1 KB)" for i in range(23)]
        head = agents._card_head("Box", to=["a@b.test"], files=files)
        block = head.split("\n\nAttachments:\n", 1)[1].splitlines()
        assert block == [f"- {f}" for f in files[:agents._CARD_FILE_CAP]] + ["- +3 more"]

    def test_long_targets_and_long_files_fit_the_budget_together(self) -> None:
        """The targets take what they need, and the files keep their count."""
        long_addrs = [("a" * 60) + f"{i}@" + ("b" * 180) + ".test" for i in range(10)]
        files = [f"report-{i}-" + "z" * 180 + ".pdf" for i in range(20)]
        head = agents._card_head("Box", to=long_addrs, cc=long_addrs, bcc=long_addrs,
                                 files=files)
        assert len(head) <= agents._CARD_TARGET_BUDGET
        targets, block = head.split("\n\nAttachments:\n", 1)
        for line in targets.splitlines()[2:]:
            value = line.split(": ", 1)[1]
            assert value in long_addrs or value.endswith(" more"), "an address was cut"
        lines = block.splitlines()
        assert lines[-1].startswith("- +") and lines[-1].endswith(" more")
        for line in lines[:-1]:
            assert line[2:] in files, "a file name was cut"

    def test_a_hidden_mark_in_a_file_name_does_not_reach_the_card(self) -> None:
        shown = agents._forward_files([{"filename": "invoice‮fdp.exe", "size_bytes": 2048}])
        assert shown == ["invoicefdp.exe (2 KB)"]

    def test_a_long_file_name_keeps_its_extension(self) -> None:
        """Follow-up 5 of #766: a plain cut at 60 showed "revision.pd"."""
        name = "invoice-2026-10-00-fracktal-works-extruder-quote-revision.pdf.exe"
        [shown] = agents._forward_files([{"filename": name, "size_bytes": 2048}])
        assert shown.endswith("revision.pdf.exe (2 KB)")
        assert shown.startswith("invoice-2026-10-00-")
        assert len(shown.removesuffix(" (2 KB)")) == agents._CARD_FILE_NAME_LIMIT
        assert "…" in shown

    async def test_six_workspace_files_cannot_hide_the_to_of_a_send(self, chat) -> None:
        paths = [f"outputs/report-{i}-" + "y" * 80 + ".pdf" for i in range(6)]
        await agents.send_email(BOX, body="See attached.", to=["kim@contoso.test"],
                                subject="Reports", attachments=paths)
        detail, context = _shown(chat.cards[0])
        assert "- To: kim@contoso.test" in context
        assert context.endswith("\n\nSee attached.")
        # Each workspace file is named in ``context`` too.
        for p in paths:
            assert f"\n- {p}" in context
        assert detail.startswith("From Fracktal · dana@fracktal.in · To kim@contoso.test")
        assert "more" in detail

    def test_a_list_over_the_cap_says_how_many_more(self) -> None:
        to = [f"person{i:02d}@fracktal.test" for i in range(13)]
        block = agents._card_targets("Box", to=to, bcc=["b@evil.test"])
        lines = block.splitlines()
        assert [ln for ln in lines if ln.startswith("- To: person")] == [
            f"- To: {t}" for t in to[:agents._CARD_TARGET_CAP]]
        assert f"- To: +{13 - agents._CARD_TARGET_CAP} more" in lines
        assert lines[-1] == "- Bcc: b@evil.test", "a long To list never hides the Bcc"

    def test_the_target_block_fits_and_never_cuts_an_address(self) -> None:
        long_addrs = [("a" * 60) + f"{i}@" + ("b" * 180) + ".test" for i in range(10)]
        block = agents._card_targets("Box", to=long_addrs, cc=long_addrs, bcc=long_addrs)
        assert len(block) <= agents._CARD_TARGET_BUDGET
        for line in block.splitlines()[2:]:
            value = line.split(": ", 1)[1]
            assert value in long_addrs or value.endswith(" more"), "an address was cut"
        for head in ("To", "Cc", "Bcc"):
            assert f"- {head}: +" in block, f"the {head} count shows"

    def test_a_card_that_fits_says_nothing_more(self) -> None:
        assert agents._card_detail("Box", to=["a@b.test", "c@b.test"], subject="Hi") == (
            "From Box · To a@b.test +1 more · Subject: Hi")

    async def test_a_no_on_the_card_sends_nothing(self, chat) -> None:
        chat.answer = False
        out = await agents.forward_email(MAIL, ["geo@fracktal.test"])
        assert chat.posts == []
        assert "not forwarded" in out.lower()

    async def test_without_files_the_card_says_so(self, chat) -> None:
        await agents.forward_email(MAIL, ["geo@fracktal.test"], include_attachments=False)
        assert "Attachments: none" in chat.cards[0]["detail"]
        assert chat.cards[0]["context"].endswith("\n\nAttachments:\n- none")
        assert chat.posts[0][1]["include_attachments"] is False

    async def test_a_mailbox_that_does_not_hold_the_mail_is_refused_before_the_card(self, chat) -> None:
        out = await agents.forward_email(MAIL, ["geo@fracktal.test"], account_id="box-b")
        assert chat.cards == [] and chat.posts == []
        assert out.startswith("Not forwarded.")
        assert f"(account_id {BOX})" in out

    async def test_an_id_that_is_not_an_email_id_is_refused(self, chat) -> None:
        out = await agents.forward_email("../admin", ["geo@fracktal.test"])
        assert out.startswith("Not forwarded.") and chat.posts == [] and chat.cards == []

    def test_the_instructions_say_when_to_forward_and_how_to_cite(self) -> None:
        text = (_AGENT_DIR / "instructions.md").read_text(encoding="utf-8")
        assert "forward_email" in text
        assert "/email?email=" in text


# ── The links ───────────────────────────────────────────────────────────────


class TestTheLinks:
    def test_the_link_has_the_shape_of_the_workbench_helper(self) -> None:
        assert agents._email_link(MAIL) == f"/email?email={MAIL}"
        assert agents._email_link(MAIL.upper(), BOX) == f"/email?email={MAIL}&account={BOX}"
        assert agents._email_link("../admin") == ""
        helper = (REPO / "workbench/control_plane/src/app/email/lib/emailLink.ts").read_text(encoding="utf-8")
        assert 'EMAIL_PARAM = "email"' in helper and 'ACCOUNT_PARAM = "account"' in helper
        assert "`/email?${params.toString()}`" in helper

    async def test_the_list_tools_print_a_link_after_the_id(self, monkeypatch) -> None:
        async def fake_get(path, params=None):
            if path == "/email/messages":
                return {"emails": [{"id": MAIL, "account_id": BOX, "subject": "BQ quote",
                                    "from_address": {"name": "Ravi", "email": "r@c.test"},
                                    "snippet": "the quote"}], "total": 1}
            if path == "/email/accounts":
                return _ACCOUNTS[:1]
            return {}

        monkeypatch.setattr(agents, "_get", fake_get)
        for out in (await agents.query_inbox(BOX), await agents.search_emails("quote"),
                    await agents.find_urgent(BOX)):
            line = next(ln for ln in out.splitlines() if "id=" in ln)
            assert f"id={MAIL} link_md=[BQ quote](/email?email={MAIL}) |" in line, line

    async def test_read_email_prints_the_link(self, monkeypatch) -> None:
        async def fake_get(path, params=None):
            return {"id": MAIL, "account_id": BOX, "subject": "BQ quote",
                    "from_address": {"name": "Ravi", "email": "r@c.test"}, "body_text": "Hi"}

        monkeypatch.setattr(agents, "_get", fake_get)
        out = await agents.read_email(MAIL)
        assert f"Link: [BQ quote](/email?email={MAIL}&account={BOX})" in out

    def test_a_crafted_subject_cannot_plant_a_link(self) -> None:
        """Review round 1, P2-a: a sender chooses the subject."""
        md = agents._md_link("Click [here](https://evil.example) `x` a|b", MAIL)
        assert md == (
            r"[Click \[here\]\(https://evil.example\) \`x\` a\|b]"
            f"(/email?email={MAIL})"
        )
        assert agents._md_link("x", "../admin") == ""
        assert agents._md_link("", MAIL) == f"[email](/email?email={MAIL})"

    def test_the_orchestrator_keeps_a_link_and_refuses_a_bare_uuid(self) -> None:
        prompt = (REPO / "apps/services/orchestrator/orchestrator/agents.py").read_text(encoding="utf-8")
        assert "Keep each in-app link in the response verbatim" in prompt
        assert "Do not keep a link to another site" in prompt
        instructions = (REPO / "apps/agents/agent-orchestrator/instructions.md").read_text(encoding="utf-8")
        assert "/email?email=" in instructions
