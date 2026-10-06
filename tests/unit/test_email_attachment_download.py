"""The attachment download goes through the ONE provider dance.

Found in the 2026-07-22 post-Phase-2 review: ``download_attachment`` was the
last provider call site that instantiated the provider RAW — it never called
``authenticate()`` (an expired access token just 401'd the download) and never
persisted rotated credentials (a refreshed token was silently dropped, so the
next request re-authed from a stale refresh token). These tests pin the
conversion to ``provider_session`` so the seam can't quietly regress.

Since WS-17 EM-T11 the download route and the text route share ONE owned
fetch, ``_fetch_owned_attachment``. So these fences read that helper, with the
same strings, and pin that each route calls it.
"""
from __future__ import annotations

import inspect

from gateway.routes.email.transport import attachments as m


def test_download_uses_provider_session_not_a_raw_instantiate() -> None:
    src = inspect.getsource(m._fetch_owned_attachment)
    assert "provider_session(" in src
    assert "_instantiate_provider" not in src, (
        "the attachment fetch went back to a raw provider instantiate — that "
        "skips authenticate() and drops rotated credentials"
    )
    # No hand-rolled credential handling either: the session owns the dance.
    assert "credentials_encrypted" not in src
    assert "decrypt" not in src


def test_download_commits_so_the_rotated_creds_land() -> None:
    """provider_session only STAGES the credential UPDATE; the commit boundary
    is now the tenant session's clean-exit commit (H2). A download that opened
    its session any other way would silently re-drop the token."""
    src = inspect.getsource(m._fetch_owned_attachment)
    assert "_tenant_session(" in src, (
        "the attachment fetch left the tenant-bound seam — its clean-exit "
        "commit is what lands the rotated-cred persist"
    )
    assert "_get_db(" not in src


def test_download_requires_auth() -> None:
    """require_auth defaults to True (HTTP 401 on auth failure) — an interactive
    download must not fall through to a None-content response. Pin that the call
    doesn't opt out."""
    src = inspect.getsource(m._fetch_owned_attachment)
    assert "require_auth=False" not in src


def test_both_routes_fetch_through_the_one_helper() -> None:
    """EM-T11: no second copy of the ownership query, the cache or the
    provider call. Each route calls the helper, and neither route body holds
    SQL, a provider session or a Redis call of its own."""
    for route in (m.download_attachment, m.attachment_text):
        src = inspect.getsource(route)
        assert "_fetch_owned_attachment(" in src, route.__name__
        for own in ("provider_session(", "_tenant_session(", "get_tenant_redis(",
                    "email_attachments"):
            assert own not in src, (route.__name__, own)
