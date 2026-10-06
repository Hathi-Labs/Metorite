"""The attachment download goes through the ONE provider dance.

Found in the 2026-07-22 post-Phase-2 review: ``download_attachment`` was the
last provider call site that instantiated the provider RAW — it never called
``authenticate()`` (an expired access token just 401'd the download) and never
persisted rotated credentials (a refreshed token was silently dropped, so the
next request re-authed from a stale refresh token). These tests pin the
conversion to ``provider_session`` so the seam can't quietly regress.

Since WS-17 EM-T11 the download route and the text route share ONE owned
fetch, ``_fetch_owned_attachment``. So these fences read that helper, with the
same strings, and pin that each route calls it. Since review round 1 each
route opens the tenant session itself, OUTSIDE its ``try``, as the download
route did before EM-T11, and passes it to the helper.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

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
    for route in (m.download_attachment, m.attachment_text):
        src = inspect.getsource(route)
        assert "_tenant_session(" in src, (
            f"{route.__name__} left the tenant-bound seam — its clean-exit "
            "commit is what lands the rotated-cred persist"
        )
        assert "_get_db(" not in src
    helper = inspect.getsource(m._fetch_owned_attachment)
    assert "_get_db(" not in helper
    assert "_tenant_session(" not in helper, (
        "the helper opens its own session again, so session faults land "
        "inside the route's try (review round 1)"
    )


def _session_blocks(fn: object) -> list[tuple[ast.AsyncWith, bool]]:
    """Each ``async with _tenant_session()`` of *fn*, and whether a ``try``
    holds it."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    found: list[tuple[ast.AsyncWith, bool]] = []

    def walk(node: ast.AST, in_try: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AsyncWith) and any(
                isinstance(item.context_expr, ast.Call)
                and getattr(item.context_expr.func, "id", "") == "_tenant_session"
                for item in child.items
            ):
                found.append((child, in_try))
            walk(child, in_try or (isinstance(child, ast.Try) and bool(child.handlers)))

    walk(tree, False)
    return found


def test_each_route_opens_its_session_outside_its_try() -> None:
    """Review round 1, P1. A ``try`` with ``except Exception`` around the
    session turned ``TenantUnbound`` and a refused connect into this route's
    500 or 502. The behaviour fence is
    ``test_email_attachment_text.py::TestTheSessionFaultsReachTheHandlers``."""
    for route in (m.download_attachment, m.attachment_text):
        blocks = _session_blocks(route)
        assert len(blocks) == 1, route.__name__
        assert blocks[0][1] is False, f"{route.__name__} opens its session inside a try"


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
        for own in ("provider_session(", "get_tenant_redis(", "email_attachments"):
            assert own not in src, (route.__name__, own)
