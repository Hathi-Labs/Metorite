"""Outlook drafts must carry Cc/Bcc, so a Cc'd reply saves as a draft.

Before this, the draft write-path carried only To — a Cc/Bcc reply had to detour
through a full send (which starts a fresh, unthreaded message), the three-way
branch every composer had to special-case. Graph stores ccRecipients /
bccRecipients on the draft message, so these pin that create_draft/update_draft
put them there.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from email_ingestion.providers.base import BaseEmailProvider
from email_ingestion.providers.gmail import GmailProvider
from email_ingestion.providers.imap import IMAPProvider
from email_ingestion.providers.outlook import OutlookProvider

_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes"


def _provider() -> OutlookProvider:
    return OutlookProvider({"access_token": "t", "refresh_token": "r"})


def _resp(json_body: dict | None = None):
    return SimpleNamespace(
        status_code=200, headers={},
        json=lambda: (json_body or {"id": "draft-1"}),
        raise_for_status=lambda: None)


def _addrs(recips: list[dict]) -> list[str]:
    return [r["emailAddress"]["address"] for r in recips]


async def test_create_standalone_draft_sets_cc_and_bcc() -> None:
    p = _provider()
    client = AsyncMock()
    client.post.return_value = _resp()
    p._http = client
    await p.create_draft(
        to=["a@x.com"], subject="hi", body_text="b",
        cc=["c@x.com"], bcc=["d@x.com"])
    # POST /me/messages with the recipients on the message body.
    body = client.post.await_args.kwargs["json"]
    assert _addrs(body["toRecipients"]) == ["a@x.com"]
    assert _addrs(body["ccRecipients"]) == ["c@x.com"]
    assert _addrs(body["bccRecipients"]) == ["d@x.com"]


async def test_create_reply_draft_patches_cc_onto_the_reply() -> None:
    p = _provider()
    client = AsyncMock()
    client.post.return_value = _resp({"id": "reply-1"})
    client.patch.return_value = _resp()
    p._http = client
    await p.create_draft(
        to=[], subject="", body_text="b",
        reply_to_message_id="m-1", cc=["c@x.com"])
    # createReply first, then a PATCH carrying the body AND the Cc.
    patch_body = client.patch.await_args.kwargs["json"]
    assert _addrs(patch_body["ccRecipients"]) == ["c@x.com"]
    assert "body" in patch_body


async def test_update_draft_sets_cc_and_bcc() -> None:
    p = _provider()
    client = AsyncMock()
    client.patch.return_value = _resp()
    p._http = client
    await p.update_draft("draft-1", to=["a@x.com"],
                         cc=["c@x.com"], bcc=["d@x.com"])
    patch_body = client.patch.await_args.kwargs["json"]
    assert _addrs(patch_body["ccRecipients"]) == ["c@x.com"]
    assert _addrs(patch_body["bccRecipients"]) == ["d@x.com"]


async def test_update_draft_leaves_cc_untouched_when_not_given() -> None:
    # cc=None must NOT emit ccRecipients — so a body-only autosave can't wipe a
    # Cc the draft already has.
    p = _provider()
    client = AsyncMock()
    client.patch.return_value = _resp()
    p._http = client
    await p.update_draft("draft-1", body_text="new body")
    patch_body = client.patch.await_args.kwargs["json"]
    assert "ccRecipients" not in patch_body
    assert "bccRecipients" not in patch_body


# ── EM-T10 item 6: the To of a reply that the member typed (C8) ──────────────
#
# Outlook's createReply sets the To of a reply to the sender only. The reply
# path used to PATCH the body, the Cc and the Bcc, and never the To. So a
# reply-all draft held only the sender at Graph, and the next sync wrote that
# To over the row. Only the composers pass ``exact_to=True``. The AI drafts and
# the rule actions keep the default, so a Reply-To address stays.

_ALL = ["ravi@x.com", "asha@x.com"]


async def _reply_patch(**kwargs) -> dict:
    p = _provider()
    client = AsyncMock()
    client.post.return_value = _resp({"id": "reply-1"})
    client.patch.return_value = _resp()
    p._http = client
    await p.create_draft(
        to=_ALL, subject="", body_text="b",
        reply_to_message_id="m-1", cc=["c@x.com"], **kwargs)
    client.patch.assert_awaited_once()
    return client.patch.await_args.kwargs["json"]


async def test_a_reply_with_exact_to_patches_each_to_address() -> None:
    patch_body = await _reply_patch(exact_to=True)
    assert _addrs(patch_body["toRecipients"]) == _ALL
    # The Cc and the body still go in the same PATCH.
    assert _addrs(patch_body["ccRecipients"]) == ["c@x.com"]
    assert "body" in patch_body


async def test_a_reply_with_the_default_keeps_the_to_of_create_reply() -> None:
    patch_body = await _reply_patch()
    assert "toRecipients" not in patch_body
    assert _addrs(patch_body["ccRecipients"]) == ["c@x.com"]


async def test_a_new_draft_sets_the_to_with_or_without_exact_to() -> None:
    for kwargs in ({}, {"exact_to": True}):
        p = _provider()
        client = AsyncMock()
        client.post.return_value = _resp()
        p._http = client
        await p.create_draft(to=_ALL, subject="hi", body_text="b", **kwargs)
        assert _addrs(client.post.await_args.kwargs["json"]["toRecipients"]) == _ALL
        client.patch.assert_not_awaited()


@pytest.mark.parametrize(
    "cls", [BaseEmailProvider, OutlookProvider, GmailProvider, IMAPProvider])
def test_each_provider_takes_exact_to_as_a_keyword_that_defaults_off(cls) -> None:
    param = inspect.signature(cls.create_draft).parameters["exact_to"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is False


def _create_draft_calls(path: Path) -> dict[str, list[ast.Call]]:
    """Each ``create_draft(...)`` call of a module, by its enclosing function."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, list[ast.Call]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "create_draft"):
                out.setdefault(fn.name, []).append(node)
    return out


def _exact_to(call: ast.Call) -> object:
    for kw in call.keywords:
        if kw.arg == "exact_to":
            return kw.value.value if isinstance(kw.value, ast.Constant) else kw.value
    return None


def test_only_the_composer_save_passes_exact_to() -> None:
    drafting = _create_draft_calls(_GATEWAY / "email" / "automation" / "drafting.py")
    # PUT /email/drafts: the update fallback, the reply and the new draft.
    assert len(drafting["upsert_draft"]) == 3
    assert [_exact_to(c) for c in drafting["upsert_draft"]] == [True, True, True]
    reply = [c for c in drafting["upsert_draft"]
             if any(kw.arg == "reply_to_message_id" for kw in c.keywords)]
    assert len(reply) == 1
    # Each other caller keeps the To of createReply, so a Reply-To stays.
    others = {
        "drafting.py": {k: v for k, v in drafting.items() if k != "upsert_draft"},
        "actions.py": _create_draft_calls(
            _GATEWAY / "email" / "automation" / "actions.py"),
        "followups.py": _create_draft_calls(
            _GATEWAY / "email" / "automation" / "followups.py"),
        "dispatch.py": _create_draft_calls(_GATEWAY / "notes" / "dispatch.py"),
    }
    seen = 0
    for name, by_fn in others.items():
        for fn, calls in by_fn.items():
            for call in calls:
                seen += 1
                assert _exact_to(call) is None, f"{name}:{fn}"
    # The AI draft, the chat card, two rule actions, the nudge and Notes.
    assert seen == 6
