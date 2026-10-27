"""No move files a mail in Sent or Drafts (§21 fix round 1, P1-a).

A held run keeps ``manage_inbox(action="move")``. On Outlook and IMAP a move
used to set ``folder='sent'`` locally, even when the provider push failed. A
stranger's mail in Sent then read as mail the member sent, and the held card
classed its Cc as ``sent``: ticked, unmarked and a known domain.

R7: let ``local_folder_after_move`` or the base ``folder_after_move`` return
``sent`` or ``drafts`` again, and this turns red.
"""
from __future__ import annotations

import pytest
from email_ingestion.providers import base

PROVIDERS = ("gmail", "microsoft", "imap", "none")


def _provider(kind: str):
    if kind == "none":
        return None
    from email_ingestion.providers.gmail import GmailProvider
    from email_ingestion.providers.imap import IMAPProvider
    from email_ingestion.providers.outlook import OutlookProvider as MicrosoftProvider

    cls = {"gmail": GmailProvider, "microsoft": MicrosoftProvider, "imap": IMAPProvider}[kind]
    return cls.__new__(cls)


@pytest.mark.parametrize("kind", PROVIDERS)
@pytest.mark.parametrize("name", ["Sent", "Sent Items", "SENT", "sent", "Drafts", "draft"])
def test_a_move_into_sent_or_drafts_is_refused(kind: str, name: str) -> None:
    assert base.local_folder_after_move(_provider(kind), name) is None


@pytest.mark.parametrize("kind", PROVIDERS)
def test_a_move_to_the_inbox_or_archive_still_works(kind: str) -> None:
    assert base.local_folder_after_move(_provider(kind), "Inbox") == "inbox"
    assert base.local_folder_after_move(_provider(kind), "Archive") == "archive"


async def test_the_patch_route_refuses_the_move_with_400(monkeypatch) -> None:
    from fastapi import HTTPException
    from gateway.routes.email.transport import messages

    async def provider(*_a, **_k):
        return None

    monkeypatch.setattr(messages, "_provider_for_message", provider)
    with pytest.raises(HTTPException) as e:
        await messages._folder_for_move(None, "m", "jane@acme.test", "Sent Items")
    assert e.value.status_code == 400
