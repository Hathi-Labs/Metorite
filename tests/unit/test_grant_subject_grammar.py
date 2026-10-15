"""WS-61 KB-1 — the ONE grant subject grammar lives in ``acb_auth.permissions``.

Spec: ``project-docs/specs/knowledge_base.md`` §5.1 (the subject spelling,
settled 2026-10-15): ``org``, ``group:<slug>`` or a bare email, the grammar of
Projects. ``normalize_grant_subject`` is pure and raises ``ValueError``.
``routes/projects/core.validate_grant_subject`` is its 422 wrapper, and each
caller (Projects grants, the CRM access rule, Knowledge) uses that wrapper.
"""
from __future__ import annotations

import pytest
from acb_auth.permissions import normalize_grant_subject

pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from fastapi import HTTPException
from gateway.routes.projects import core as projects_core


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("org", "org"),
        ("  org  ", "org"),
        ("group:Sales", "group:sales"),
        ("group:  ops ", "group:ops"),
        ("Ravi@Example.COM", "ravi@example.com"),
    ],
)
def test_the_grammar_normalises(raw: str, stored: str) -> None:
    assert normalize_grant_subject(raw) == stored


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "A grant subject is required."),
        ("   ", "A grant subject is required."),
        ("group:", "A group subject must name a group."),
        ("group:   ", "A group subject must name a group."),
        ("user:ravi@example.com", None),
        ("project:1234", "Unknown grant subject 'project:1234'. One of: 'org', 'group:<slug>', or an email address."),
        ("everyone", "Unknown grant subject 'everyone'. One of: 'org', 'group:<slug>', or an email address."),
    ],
)
def test_the_grammar_refuses_with_value_error(raw: str, message: str | None) -> None:
    if message is None:
        # `user:<email>` holds an @, so the Projects grammar reads it as an
        # email. Pinned so a change to that reading is a decision, not drift.
        assert normalize_grant_subject(raw) == raw.lower()
        return
    with pytest.raises(ValueError) as exc:
        normalize_grant_subject(raw)
    assert str(exc.value) == message


@pytest.mark.parametrize("raw", ["", "group:", "everyone"])
def test_the_wrapper_answers_422_with_the_same_words(raw: str) -> None:
    with pytest.raises(ValueError) as pure:
        normalize_grant_subject(raw)
    with pytest.raises(HTTPException) as http:
        projects_core.validate_grant_subject(raw)
    assert http.value.status_code == 422
    assert http.value.detail == str(pure.value)


def test_the_wrapper_returns_the_normalised_subject() -> None:
    assert projects_core.validate_grant_subject("group:Sales") == "group:sales"


def test_knowledge_validates_through_the_same_wrapper() -> None:
    from gateway.routes.knowledge import core as kb_core

    assert kb_core.validate_grant_subject is projects_core.validate_grant_subject


def test_permissions_holds_no_fastapi() -> None:
    """The module is pure (its docstring). The grammar must not pull FastAPI in."""
    import re
    from pathlib import Path

    import acb_auth.permissions as perms

    source = Path(perms.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+(fastapi|starlette)\b", source, re.M)
