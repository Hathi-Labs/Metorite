"""EM-T13b-1 (email_app_master_plan.md §10.4.15): the unsubscribe card and the
draft card name the target.

``unsubscribe_sender`` takes no link from the model. It reads the stored link
through ``GET /email/unsubscribe/target``, shows the host of a one-click link
or the address of a ``mailto:`` link, and posts that exact link. ``send_draft``
reads the draft first, names each To, Cc and Bcc on its card, and sends
``expect``. ``POST /email/drafts/send`` answers 409 when the row changed.

The agent half fakes the gateway at the ``_get`` / ``_post`` seam and the card
at ``acb_skills.ask_tools.request_confirmation`` (the idiom of
``test_email_rule_confirm.py``). The R8 class runs the target route on a real
database under FORCE RLS.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_send_cards.py -v -rs
"""
from __future__ import annotations

import contextlib
import importlib.util
import inspect
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from agent_framework import FunctionTool
from fastapi import HTTPException
from gateway.routes.email.automation import drafting as d
from gateway.routes.email.automation import senders as s
from sqlalchemy import text

from tests.unit._email_fakes import bind_db
from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_AGENT = (
    Path(__file__).resolve().parents[2]
    / "apps" / "agents" / "agent-email-assistant" / "agents.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("ea_send_cards", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load()

BOX = "acc-1"
OTHER_BOX = "acc-2"
SENDER = "news@list.example"
ONE_CLICK = {"kind": "one-click", "link": "https://list.example.com/u?id=42",
             "host": "list.example.com", "address": None}
MAILTO = {"kind": "mailto", "link": "mailto:unsub@list.example?subject=stop",
          "host": None, "address": "unsub@list.example", "subject": "stop",
          "body": "Please unsubscribe me from this list."}
BLOCK = {"kind": "block", "link": None, "host": None, "address": None}
DRAFT = {
    "id": "d1", "account_id": BOX, "folder": "drafts", "subject": "Re: Quote",
    "to_addresses": [{"name": "Ravi", "email": "ravi@contoso.test"}],
    "cc_addresses": [{"name": "", "email": "kim@contoso.test"}],
    "bcc_addresses": [{"name": "", "email": "records@evil.test"}],
}


class Gateway:
    """Records each GET, each write and each card.

    ``answer`` is the reply of the card, and ``channel`` says whether a live
    chat could show it. ``fail`` maps a path to ``(status, detail)``."""

    def __init__(self) -> None:
        self.target: dict[str, Any] = dict(ONE_CLICK)
        self.draft: dict[str, Any] = json.loads(json.dumps(DRAFT))
        self.gets: list[tuple[str, dict[str, Any]]] = []
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.cards: list[dict[str, Any]] = []
        self.answer = True
        self.channel = True
        self.fail: dict[str, tuple[int, str]] = {}

    def _raise(self, verb: str, path: str) -> None:
        if path in self.fail:
            status, detail = self.fail[path]
            raise agents.GatewayError(
                f"Email {verb} {path} failed ({status}): {detail}", status)


@pytest.fixture()
def gw(monkeypatch) -> Gateway:
    g = Gateway()

    async def fake_get(path, params=None):
        g.gets.append((path, dict(params or {})))
        g._raise("GET", path)
        if path == "/email/accounts":
            return [{"id": BOX, "email_address": "dana@fracktal.test",
                     "display_label": "Fracktal"},
                    {"id": OTHER_BOX, "email_address": "dana@outlook.test",
                     "display_label": "Personal"}]
        if path == "/email/unsubscribe/target":
            return dict(g.target)
        if path == "/email/messages/d1":
            return json.loads(json.dumps(g.draft))
        return {}

    async def fake_post(path, body):
        g._raise("POST", path)
        g.posts.append((path, body))
        if path == "/email/unsubscribe":
            return {"ok": True, "method": "one-click", "archived": 3}
        if path == "/email/newsletters":
            return {"ok": True, "status": body.get("status"), "archived": 2}
        return {}

    async def card(**kw):
        g.cards.append(kw)
        return g.answer

    monkeypatch.setattr(agents, "_get", fake_get)
    monkeypatch.setattr(agents, "_post", fake_post)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open",
                        lambda: g.channel)
    return g


async def _as_the_model(fn: Any, **arguments: Any) -> str:
    """Call a tool the way the MAF agent calls it, with the JSON arguments of
    the model. MAF drops an argument that the tool does not declare."""
    tool = FunctionTool(func=fn, name=fn.__name__)
    [content] = await tool.invoke(arguments=arguments)
    return str(content.text)


# ── The unsubscribe tool takes no link from the model (scope 2) ─────────────

def test_the_tool_has_no_link_parameter() -> None:
    assert "unsubscribe_link" not in inspect.signature(
        agents.unsubscribe_sender).parameters


async def test_the_tool_posts_the_link_of_the_target_route(gw: Gateway) -> None:
    """A mail can ask the model to pass its own link. The tool posts the
    stored link that the target route names, and nothing else."""
    out = await _as_the_model(
        agents.unsubscribe_sender, account_id=BOX, email=SENDER,
        unsubscribe_link="https://evil.test/collect")
    assert gw.gets[-1] == ("/email/unsubscribe/target",
                           {"account_id": BOX, "email": SENDER})
    [(path, body)] = gw.posts
    assert path == "/email/unsubscribe"
    assert body["unsubscribe_link"] == ONE_CLICK["link"]
    assert "evil.test" not in json.dumps(gw.posts)
    assert out.startswith("Unsubscribed from")


# ── The unsubscribe card names the target (scope 3) ─────────────────────────

async def test_the_card_names_the_host_of_a_one_click_link(gw: Gateway) -> None:
    await agents.unsubscribe_sender(BOX, SENDER)
    [card] = gw.cards
    assert "host: list.example.com" in card["detail"]
    assert "host: list.example.com" in card["context"]
    assert card["title"] == f"Unsubscribe from {SENDER}?"


async def test_the_card_names_the_address_of_a_mailto_link(gw: Gateway) -> None:
    gw.target = dict(MAILTO)
    await agents.unsubscribe_sender(BOX, SENDER)
    [card] = gw.cards
    assert "mail to: unsub@list.example" in card["detail"]
    assert "Fracktal · dana@fracktal.test" in card["detail"]
    assert "mail to: unsub@list.example" in card["context"]
    [(path, body)] = gw.posts
    assert (path, body["unsubscribe_link"]) == ("/email/unsubscribe", MAILTO["link"])


@pytest.mark.parametrize("address", [
    "a@list.example,b@evil.test",  # a list
    "Evil <e@evil.test>",  # a name
    "unsub@ex" + chr(0x430) + "mple.com",  # a Cyrillic letter in the domain
    "evil%40evil.test",  # no plain address
    "unsub@list.example" + chr(0x200B),  # a hidden character
    "",
    None,
])
async def test_a_mailto_address_that_the_check_refuses_sends_nothing(
    gw: Gateway, address: Any,
) -> None:
    gw.target = {**MAILTO, "address": address}
    out = await agents.unsubscribe_sender(BOX, SENDER)
    assert out.startswith("Nothing changed.")
    assert gw.cards == [] and gw.posts == []


@pytest.mark.parametrize("link", [
    "https://list.example.com@evil.test/u",  # a user name before the host
    "https://ex" + chr(0x430) + "mple.com/u",  # a Cyrillic letter in the host
    "https://list.example.com\\@evil.test/",  # a backslash
    "httpx://list.example.com/u",  # not http or https
    "https:///u",  # no host
])
async def test_a_one_click_link_that_the_check_refuses_sends_nothing(
    gw: Gateway, link: str,
) -> None:
    gw.target = {**ONE_CLICK, "link": link}
    out = await agents.unsubscribe_sender(BOX, SENDER)
    assert out.startswith("Nothing changed.")
    assert gw.cards == [] and gw.posts == []


async def test_a_block_card_says_the_sender_has_no_link(gw: Gateway) -> None:
    """A block posts no link, so a link stored after the card is never used."""
    gw.target = dict(BLOCK)
    out = await agents.unsubscribe_sender(BOX, SENDER)
    [card] = gw.cards
    assert "no unsubscribe link" in card["detail"]
    assert gw.posts == [("/email/newsletters", {
        "account_id": BOX, "email": SENDER, "name": None,
        "status": "AUTO_ARCHIVED"})]
    assert "blocked it instead" in out


async def test_an_unknown_target_kind_sends_nothing(gw: Gateway) -> None:
    gw.target = {"kind": "redirect", "link": "https://x.test/"}
    out = await agents.unsubscribe_sender(BOX, SENDER)
    assert out.startswith("Nothing changed.")
    assert gw.cards == [] and gw.posts == []


@pytest.mark.parametrize(("answer", "channel", "lead"), [
    (False, True, "Cancelled"),
    (False, False, "Nothing changed. This needs the member's approval"),
])
async def test_a_refusal_or_a_headless_call_unsubscribes_nothing(
    gw: Gateway, answer: bool, channel: bool, lead: str,
) -> None:
    gw.answer, gw.channel = answer, channel
    out = await agents.unsubscribe_sender(BOX, SENDER)
    assert out.startswith(lead)
    assert len(gw.cards) == 1 and gw.posts == []


# ── An old gateway (scope 4) ────────────────────────────────────────────────

@pytest.mark.parametrize("status", [404, 405])
async def test_an_old_gateway_sends_nothing(gw: Gateway, status: int) -> None:
    gw.fail["/email/unsubscribe/target"] = (status, "Not Found")
    out = await agents.unsubscribe_sender(BOX, SENDER)
    assert out == agents._UNSUBSCRIBE_NOT_READY
    assert gw.cards == [] and gw.posts == []


async def test_an_unknown_mailbox_is_not_an_old_gateway(gw: Gateway) -> None:
    """The route answers 404 for a mailbox of another member too. That is an
    error, and the tool never says that the feature is not ready."""
    gw.fail["/email/unsubscribe/target"] = (404, "Account not found")
    with pytest.raises(agents.GatewayError):
        await agents.unsubscribe_sender(BOX, SENDER)
    assert gw.cards == [] and gw.posts == []


async def test_a_mailbox_that_the_member_does_not_have_sends_nothing(
    gw: Gateway,
) -> None:
    out = await agents.unsubscribe_sender("acc-9", SENDER)
    assert out.startswith("Nothing changed. No connected mailbox")
    assert gw.cards == [] and gw.posts == []


# ── The draft card (scope 5) ────────────────────────────────────────────────

async def test_the_draft_card_names_each_to_cc_and_bcc(gw: Gateway) -> None:
    out = await agents.send_draft(BOX, "d1")
    [card] = gw.cards
    assert card["detail"].startswith("From Fracktal · dana@fracktal.test · ")
    assert "Sends to 3 recipients: 1 To, 1 Cc, 1 Bcc." in card["detail"]
    assert card["context"].splitlines()[1:] == [
        "- To: ravi@contoso.test", "- Cc: kim@contoso.test",
        "- Bcc: records@evil.test"]
    assert out == "Draft sent."


async def test_the_send_names_what_the_card_showed(gw: Gateway) -> None:
    await agents.send_draft(BOX, "d1")
    assert gw.posts == [("/email/drafts/send", {
        "account_id": BOX, "draft_id": "d1",
        "expect": {"to": ["ravi@contoso.test"], "cc": ["kim@contoso.test"],
                   "bcc": ["records@evil.test"]}})]


async def test_a_long_subject_cannot_hide_a_bcc(gw: Gateway) -> None:
    """The card cuts ``detail`` at 500 characters. A Bcc is in ``context``,
    and the count in ``detail`` comes before the subject."""
    gw.draft["subject"] = "S" * 900
    await agents.send_draft(BOX, "d1")
    [card] = gw.cards
    assert "records@evil.test" in card["context"]
    shown = card["detail"][:500]
    assert "1 Bcc" in shown and shown.index("1 Bcc") < shown.index("Subject:")


async def test_the_card_shows_no_hidden_text_in_an_address(gw: Gateway) -> None:
    hidden = "rec" + chr(0x202E) + "ords@evil.test\nTo: x"  # a bidi override
    gw.draft["bcc_addresses"] = [{"email": hidden}]
    await agents.send_draft(BOX, "d1")
    [card] = gw.cards
    assert card["context"].splitlines()[-1] == "- Bcc: records@evil.test To: x"


@pytest.mark.parametrize(("answer", "channel", "lead"), [
    (False, True, "Send cancelled"),
    (False, False, "Not sent. This needs the member's approval"),
])
async def test_a_refused_or_headless_draft_card_sends_nothing(
    gw: Gateway, answer: bool, channel: bool, lead: str,
) -> None:
    gw.answer, gw.channel = answer, channel
    out = await agents.send_draft(BOX, "d1")
    assert out.startswith(lead)
    assert len(gw.cards) == 1 and gw.posts == []


async def test_a_draft_of_another_mailbox_sends_nothing(gw: Gateway) -> None:
    gw.draft["account_id"] = OTHER_BOX
    out = await agents.send_draft(BOX, "d1")
    assert out.startswith("Not sent.")
    assert gw.cards == [] and gw.posts == []


@pytest.mark.parametrize("folder", ["inbox", "sent", "", None])
async def test_a_message_outside_drafts_sends_nothing(
    gw: Gateway, folder: str | None,
) -> None:
    gw.draft["folder"] = folder
    out = await agents.send_draft(BOX, "d1")
    assert out.startswith("Not sent.")
    assert gw.cards == [] and gw.posts == []


async def test_a_draft_that_the_member_cannot_read_sends_nothing(
    gw: Gateway,
) -> None:
    gw.fail["/email/messages/d1"] = (404, "Message not found")
    out = await agents.send_draft(BOX, "d1")
    assert out.startswith("Not sent.")
    assert gw.cards == [] and gw.posts == []


async def test_a_draft_with_no_recipient_sends_nothing(gw: Gateway) -> None:
    for key in ("to_addresses", "cc_addresses", "bcc_addresses"):
        gw.draft[key] = []
    out = await agents.send_draft(BOX, "d1")
    assert out.startswith("Not sent.")
    assert gw.cards == [] and gw.posts == []


async def test_a_list_too_long_for_the_card_sends_nothing(gw: Gateway) -> None:
    gw.draft["bcc_addresses"] = [
        {"email": f"r{i}-{'x' * 60}@evil.test"} for i in range(80)]
    out = await agents.send_draft(BOX, "d1")
    assert out.startswith("Not sent.")
    assert gw.cards == [] and gw.posts == []


async def test_a_409_from_the_send_says_not_sent(gw: Gateway) -> None:
    gw.fail["/email/drafts/send"] = (409, d.DRAFT_RECIPIENTS_CHANGED_DETAIL)
    out = await agents.send_draft(BOX, "d1")
    assert out.startswith("Not sent. The draft changed")


async def test_another_send_error_still_raises(gw: Gateway) -> None:
    gw.fail["/email/drafts/send"] = (502, "provider down")
    with pytest.raises(agents.GatewayError):
        await agents.send_draft(BOX, "d1")


# ── The route sends what the card showed (scope 6) ──────────────────────────

USER = SimpleNamespace(email="dana@fracktal.test")


def _drow(**over: Any) -> SimpleNamespace:
    row = {
        "provider_message_id": "pm-1", "subject": "Re: Quote",
        "to_addresses": json.dumps([{"email": "Ravi@Contoso.test"}]),
        "cc_addresses": [{"email": "kim@contoso.test"}],
        "bcc_addresses": [{"email": "records@evil.test"}],
        "body_text": "Hi", "thread_id": None,
    }
    row.update(over)
    return SimpleNamespace(**row)


class _Reached(Exception):
    """The route reached the provider."""


async def _send(expect: dict[str, list[str]] | None, drow: SimpleNamespace):
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(fetchone=lambda: drow)
    reached: list[str] = []

    @asynccontextmanager
    async def provider_session(*_a: Any, **_k: Any):
        reached.append("provider")
        raise _Reached
        yield  # pragma: no cover

    req = d.DraftSendRequest(account_id=BOX, draft_id="d1", expect=expect)
    with patch.object(d, "_tenant_session", bind_db(db)), \
            patch.object(d, "_assert_account_owner", AsyncMock()), \
            patch.object(d, "provider_session", provider_session), \
            contextlib.suppress(_Reached):
        await d.send_draft_endpoint(
            req, SimpleNamespace(add_task=lambda *a: None), user=USER)
    return reached


SHOWN = {"to": ["ravi@contoso.test"], "cc": ["kim@contoso.test"],
         "bcc": ["records@evil.test"]}


@pytest.mark.parametrize("expect", [
    {**SHOWN, "bcc": []},  # the card showed no Bcc
    {**SHOWN, "to": ["ravi@contoso.test", "x@evil.test"]},
    {**SHOWN, "cc": ["other@contoso.test"]},
    {"to": [], "cc": [], "bcc": []},
], ids=["bcc-added", "to-removed", "cc-changed", "all-empty"])
async def test_an_expect_that_does_not_match_answers_409_and_calls_no_provider(
    expect: dict[str, list[str]],
) -> None:
    with pytest.raises(HTTPException) as exc:
        await _send(expect, _drow())
    assert exc.value.status_code == 409
    assert exc.value.detail == d.DRAFT_RECIPIENTS_CHANGED_DETAIL


async def test_an_expect_in_another_case_and_order_sends() -> None:
    drow = _drow(to_addresses=[{"email": "b@x.test"}, {"email": "A@x.test"}])
    shown = {**SHOWN, "to": ["a@X.test", "B@x.test"]}
    assert await _send(shown, drow) == ["provider"]


async def test_no_expect_checks_nothing() -> None:
    """The UI and an older agent send no ``expect``, as before."""
    assert await _send(None, _drow(bcc_addresses=[{"email": "new@evil.test"}])) == [
        "provider"]


# ── The target route (scope 1) ──────────────────────────────────────────────

async def _target(link: str | None) -> dict[str, Any]:
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(
        fetchone=lambda: SimpleNamespace(link=link))
    with patch.object(s, "_tenant_session", bind_db(db)), \
            patch.object(s, "_assert_account_owner", AsyncMock()):
        return await s.unsubscribe_target(account_id=BOX, email=SENDER, user=USER)


_NO_TEXT = {"subject": None, "body": None}
_DEFAULT_BODY = "Please unsubscribe me from this list."


@pytest.mark.parametrize(("link", "answer"), [
    ("https://List.Example.com/u?id=1",
     {"kind": "one-click", "host": "list.example.com", "address": None, **_NO_TEXT}),
    ("mailto:unsub@list.example?subject=stop",
     {"kind": "mailto", "host": None, "address": "unsub@list.example",
      "subject": "stop", "body": _DEFAULT_BODY}),
    ("mailto:unsub@list.example?subject=Change%20my%20account&body=Pay%20IBAN%20X",
     {"kind": "mailto", "host": None, "address": "unsub@list.example",
      "subject": "Change my account", "body": "Pay IBAN X"}),
    ("MAILTO: a@list.example , b@evil.test",
     {"kind": "mailto", "host": None, "address": "a@list.example , b@evil.test",
      "subject": "unsubscribe", "body": _DEFAULT_BODY}),
    (None, {"kind": "block", "host": None, "address": None, **_NO_TEXT}),
    ("javascript:alert(1)",
     {"kind": "block", "host": None, "address": None, **_NO_TEXT}),
])
async def test_the_target_route_names_the_host_or_the_address(
    link: str | None, answer: dict[str, Any],
) -> None:
    """For a mailto link the route also names the subject and the body that
    the send uses (review round 1, P2)."""
    assert await _target(link) == {**answer, "link": link}


async def test_the_post_reads_the_link_through_the_one_helper() -> None:
    """The target route and the POST read one stored link, so the card shows
    the link that a POST with no link uses."""
    stored = AsyncMock(return_value="mailto:unsub@list.example")
    req = s.UnsubscribeRequest(account_id=BOX, email=SENDER)
    with patch.object(s, "_tenant_session", bind_db(AsyncMock())), \
            patch.object(s, "_assert_account_owner", AsyncMock()), \
            patch.object(s, "_stored_unsubscribe_link", stored), \
            patch.object(s, "provider_session") as sess, \
            patch.object(s, "_apply_newsletter_status", AsyncMock(return_value=0)):
        sess.return_value.__aenter__.return_value = SimpleNamespace(authed=False)
        res = await s.unsubscribe_sender(
            req, SimpleNamespace(add_task=lambda *a: None), user=USER)
    stored.assert_awaited_once()
    assert res["unsubscribe_link"] == "mailto:unsub@list.example"


async def test_the_post_keeps_a_link_that_the_ui_passes() -> None:
    """``BulkUnsubscribeView.tsx`` passes its own stored link, as before."""
    stored = AsyncMock(return_value="https://other.test/u")
    http = AsyncMock(return_value=(True, "one-click-post"))
    req = s.UnsubscribeRequest(account_id=BOX, email=SENDER,
                               unsubscribe_link="https://list.example.com/u")
    with patch.object(s, "_tenant_session", bind_db(AsyncMock())), \
            patch.object(s, "_assert_account_owner", AsyncMock()), \
            patch.object(s, "_stored_unsubscribe_link", stored), \
            patch.object(s, "_http_unsubscribe", http), \
            patch.object(s, "_apply_newsletter_status", AsyncMock(return_value=1)):
        res = await s.unsubscribe_sender(
            req, SimpleNamespace(add_task=lambda *a: None), user=USER)
    stored.assert_not_awaited()
    http.assert_awaited_once_with("https://list.example.com/u")
    assert res["method"] == "one-click"


# ── Review round 1 (2026-10-07) ─────────────────────────────────────────────

# P2: the sender of the list writes the subject and the body of a mailto link,
# and the send uses them.

async def test_the_mailto_card_shows_the_subject_and_the_body(gw: Gateway) -> None:
    gw.target = {**MAILTO, "subject": "Unsubscribe 42", "body": "Remove me."}
    await agents.unsubscribe_sender(BOX, SENDER)
    [card] = gw.cards
    assert card["context"].splitlines() == [
        "- mail to: unsub@list.example", "- subject: Unsubscribe 42",
        "- body: Remove me."]


@pytest.mark.parametrize(("field", "value"), [
    ("body", "Please pay to IBAN X. " * 12),  # longer than the limit
    ("body", "Confirm at https://evil.test/a"),
    ("body", "Confirm at www.evil.test"),
    ("subject", "Go to http://evil.test"),
    ("subject", "stop" + chr(0x202E) + "x"),  # a hidden character
    ("subject", None),  # the server named no subject
    ("body", None),
])
async def test_a_mailto_text_that_the_card_cannot_show_sends_nothing(
    gw: Gateway, field: str, value: Any,
) -> None:
    gw.target = {**MAILTO, field: value}
    out = await agents.unsubscribe_sender(BOX, SENDER)
    assert out.startswith("Nothing changed.")
    assert gw.cards == [] and gw.posts == []


# P4: the last answer names the cleaned sender.

async def test_the_answer_names_the_cleaned_sender(gw: Gateway) -> None:
    out = await agents.unsubscribe_sender(BOX, "news@list.example" + chr(0x200B))
    assert out.startswith("Unsubscribed from news@list.example;")
    gw.target = dict(BLOCK)
    out = await agents.unsubscribe_sender(BOX, "news@list.example" + chr(0x200B))
    assert "from news@list.example (no one-click link)" in out


# P3: the draft card marks a non-ASCII domain and never cuts an address.

async def test_a_non_ascii_domain_is_marked_on_the_draft_card(gw: Gateway) -> None:
    gw.draft["cc_addresses"] = [{"email": "kim@b" + chr(0xFC) + "cher.test"}]
    await agents.send_draft(BOX, "d1")
    [card] = gw.cards
    assert ("- Cc: kim@b" + chr(0xFC) + "cher.test "
            "(non-ASCII domain: xn--bcher-kva.test)") in card["context"]
    assert gw.posts  # an IDN address is legitimate, so the send goes on


async def test_an_address_too_long_for_the_card_sends_nothing(gw: Gateway) -> None:
    gw.draft["bcc_addresses"] = [{"email": "r" * 250 + "@evil.test"}]
    out = await agents.send_draft(BOX, "d1")
    assert out.startswith("Not sent.")
    assert gw.cards == [] and gw.posts == []


# P1: the provider draft holds EXACTLY the recipients that the card showed.

class _OutlookDraft:
    """A provider draft with Outlook's PATCH rule: a list replaces, and None
    leaves the list that the provider holds."""

    def __init__(self, **held: list[str]) -> None:
        self.held = {"to": [], "cc": [], "bcc": [], **held}
        self.calls: list[str] = []
        self.sent: dict[str, list[str]] | None = None

    async def update_draft(self, draft_id: str, to: Any = None, subject: Any = None,
                           body_text: Any = None, body_html: Any = None,
                           thread_id: Any = None, cc: Any = None, bcc: Any = None,
                           attachments: Any = None) -> str:
        self.calls.append("update")
        for key, value in (("to", to), ("cc", cc), ("bcc", bcc)):
            if value is not None:
                self.held[key] = list(value)
        return draft_id

    async def send_draft(self, draft_id: str) -> None:
        self.calls.append("send")
        self.sent = {k: list(v) for k, v in self.held.items()}


async def _send_to(provider: _OutlookDraft, *, expect: Any, drow: SimpleNamespace,
                   signature: str = "") -> None:
    db = AsyncMock()

    async def execute(stmt: Any, params: Any = None) -> SimpleNamespace:
        if "email_assistant_settings" in str(stmt):
            return SimpleNamespace(
                fetchone=lambda: SimpleNamespace(signature=signature))
        return SimpleNamespace(fetchone=lambda: drow)

    db.execute.side_effect = execute

    @asynccontextmanager
    async def provider_session(*_a: Any, **_k: Any):
        yield SimpleNamespace(provider=provider)

    req = d.DraftSendRequest(account_id=BOX, draft_id="d1", expect=expect)
    with patch.object(d, "_tenant_session", bind_db(db)), \
            patch.object(d, "_assert_account_owner", AsyncMock()), \
            patch.object(d, "provider_session", provider_session):
        await d.send_draft_endpoint(
            req, SimpleNamespace(add_task=lambda *a: None), user=USER)


_REPLY_ROW = _drow(to_addresses=[{"email": "billing@vendor.test"}],
                   cc_addresses=[], bcc_addresses=[])
_REPLY_SHOWN = {"to": ["billing@vendor.test"], "cc": [], "bcc": []}


@pytest.mark.parametrize("signature", ["", "Dana"], ids=["unsigned", "signed"])
async def test_a_reply_to_draft_sends_to_the_to_of_the_row(signature: str) -> None:
    """``createReply`` put the Reply-To of an attacker mail on the Outlook
    draft. The card showed the From of the row, so the send goes there."""
    provider = _OutlookDraft(to=["pay@vendor-billing.test"])
    await _send_to(provider, expect=_REPLY_SHOWN, drow=_REPLY_ROW,
                   signature=signature)
    assert provider.calls == ["update", "send"]
    assert provider.sent == {"to": ["billing@vendor.test"], "cc": [], "bcc": []}


@pytest.mark.parametrize("signature", ["", "Dana"], ids=["unsigned", "signed"])
async def test_a_provider_only_cc_and_bcc_are_cleared_before_the_send(
    signature: str,
) -> None:
    provider = _OutlookDraft(to=["billing@vendor.test"], cc=["cc@evil.test"],
                             bcc=["spy@evil.test"])
    await _send_to(provider, expect=_REPLY_SHOWN, drow=_REPLY_ROW,
                   signature=signature)
    assert provider.sent == {"to": ["billing@vendor.test"], "cc": [], "bcc": []}


async def test_with_no_expect_the_unsigned_send_does_not_update() -> None:
    """The UI sends no ``expect``, and its unsigned send is as before."""
    provider = _OutlookDraft(to=["billing@vendor.test"])
    await _send_to(provider, expect=None, drow=_REPLY_ROW)
    assert provider.calls == ["send"]


# ── R8: the target route on a real database ─────────────────────────────────

def _seed_account(admin_engine, *, org: str, owner: str) -> str:
    with admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, initial_sync_done, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'x', true, CAST(:o AS uuid)) "
            "RETURNING id"),
            {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@em-t13b.test",
             "o": org}).scalar_one())


def _seed_mail(admin_engine, *, org: str, account_id: str, sender: str,
               link: str | None) -> None:
    with admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, from_address, to_addresses, subject, "
            "received_at, unsubscribe_link, organization_id) VALUES "
            "(CAST(:a AS uuid), :pm, :t, 'inbox', CAST(:f AS jsonb), "
            "'[]'::jsonb, 'news', :r, :link, CAST(:o AS uuid))"),
            {"a": account_id, "pm": f"pm-{uuid.uuid4().hex[:12]}",
             "t": f"t-{uuid.uuid4().hex[:8]}",
             "f": json.dumps({"email": sender, "name": "News"}),
             "r": datetime.now(UTC), "link": link, "o": org})


@pytest.fixture()
def target_db(promoted, app_engine):  # noqa: F811
    with app_engine.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )
    p = promoted
    owner = f"owner-{uuid.uuid4().hex[:8]}@em-t13b.test"
    # Two mailboxes of ONE member. The second one holds a link that sorts
    # after the first, so a read with no mailbox filter returns it.
    box_a = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
    box_b = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
    _seed_mail(p.admin_engine, org=p.org_b, account_id=box_a, sender=SENDER,
               link="https://a.list.example/u")
    _seed_mail(p.admin_engine, org=p.org_b, account_id=box_a, sender=SENDER,
               link=None)
    _seed_mail(p.admin_engine, org=p.org_b, account_id=box_b, sender=SENDER,
               link="https://z.list.example/u")
    app_dsn = p.app_url.render_as_string(hide_password=False)

    async def _call(account_id: str, email: str, *, member: str = owner):
        user = UserContext(email=member, role=UserRole.EMPLOYEE,
                           organization_id=p.org_b)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                return await s.unsubscribe_target(
                    account_id=account_id, email=email, user=user)
        finally:
            release_tenant(token)

    try:
        yield SimpleNamespace(call=_call, box_a=box_a, box_b=box_b)
    finally:
        with p.admin_engine.begin() as c:
            for box in (box_a, box_b):
                c.execute(text("DELETE FROM email_messages WHERE account_id = "
                               "CAST(:a AS uuid)"), {"a": box})
                c.execute(text("DELETE FROM email_accounts WHERE id = "
                               "CAST(:a AS uuid)"), {"a": box})


@_DB_GATE
class TestTheTargetRouteOnARealDatabase:

    async def test_it_returns_the_link_of_the_named_mailbox_only(self, target_db):
        t = target_db
        answer = await t.call(t.box_a, "NEWS@list.example")
        assert answer == {"kind": "one-click", "link": "https://a.list.example/u",
                          "host": "a.list.example", "address": None, **_NO_TEXT}
        answer = await t.call(t.box_b, SENDER)
        assert answer["link"] == "https://z.list.example/u"

    async def test_a_sender_with_no_link_is_a_block(self, target_db):
        t = target_db
        answer = await t.call(t.box_a, "other@list.example")
        assert answer == {"kind": "block", "link": None, "host": None,
                          "address": None, **_NO_TEXT}

    async def test_another_member_gets_404(self, target_db):
        t = target_db
        with pytest.raises(HTTPException) as exc:
            await t.call(t.box_a, SENDER, member="mallory@em-t13b.test")
        assert exc.value.status_code == 404
