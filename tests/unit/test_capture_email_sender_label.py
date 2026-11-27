"""Task 4016: a planted display name never shows as the sender of a capture.

``"<ceo@company.test>" <mallory@evil.test>`` stores the name
``<ceo@company.test>`` at ``mallory@evil.test``. The capture of an email
showed the name alone, so the draft, the card and the task origin read
``From: <ceo@company.test>``. ``capture_email._sender_label`` is the one
helper of each site that shows a stored sender.
"""
from __future__ import annotations

import ast
import contextlib
import inspect
import json
import textwrap
import types
from typing import Any

import pytest
from gateway.routes.tasks import capture_email as ce

REAL = "mallory@evil.test"


@pytest.mark.parametrize(("name", "email", "label"), [
    ("<ceo@company.test>", REAL, REAL),
    ("ceo@company.test", REAL, REAL),
    ("ceo\uff20company.test", REAL, REAL),          # a fullwidth at sign
    ("\uff1cceo", REAL, REAL),                      # a fullwidth less-than
    ("ceo\ufe6bcompany.test", REAL, REAL),          # a small at sign
    ("<ceo@company.test>", "", ""),
    ("john@x.com", "John@X.com", "john@x.com"),     # the address itself
    ("Asha Rao", "asha@example.com", "Asha Rao"),
    ("Doe, John", "j@x.com", "Doe, John"),
    ("", "asha@example.com", "asha@example.com"),
    ("", "", ""),
])
def test_the_label_never_shows_a_planted_address(
    name: str, email: str, label: str,
) -> None:
    assert ce._sender_label({"name": name, "email": email}) == label


SITES = ["_fetch_thread", "_fmt_addrs", "_route_and_persist",
         "capture_from_email", "preview_capture_from_email",
         "enhance_capture_from_email"]


@pytest.mark.parametrize("site", SITES)
def test_each_site_shows_the_sender_through_the_helper(site: str) -> None:
    """No site reads ``get("name") or get("email")`` by itself."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(ce, site))))
    calls = [n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "_sender_label" in calls
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            keys = [a.args[0].value for a in node.values
                    if isinstance(a, ast.Call)
                    and isinstance(a.func, ast.Attribute)
                    and a.func.attr == "get" and a.args
                    and isinstance(a.args[0], ast.Constant)]
            assert keys[:2] != ["name", "email"], site


async def test_the_preview_shows_the_real_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = types.SimpleNamespace(
        id="e1", subject="Wire the funds", snippet="today",
        thread_id="t1",
        from_address=json.dumps({"name": "<ceo@company.test>", "email": REAL}))

    @contextlib.asynccontextmanager
    async def session() -> Any:
        yield object()

    async def load(*_a: Any) -> Any:
        return row

    async def none(*_a: Any) -> Any:
        return None

    async def nothing(*_a: Any) -> list[Any]:
        return []

    monkeypatch.setattr(ce, "_tenant_session", session)
    monkeypatch.setattr(ce, "_load_email", load)
    monkeypatch.setattr(ce, "_find_existing_capture", none)
    monkeypatch.setattr(ce, "_find_similar_tasks", nothing)
    out = await ce.preview_capture_from_email(
        ce.CaptureFromEmailRequest(account_id="a1", email_id="e1"),
        user=types.SimpleNamespace(email="member@company.test"))
    assert out.from_name == REAL
    assert out.draft.title == f"Email from {REAL}: Wire the funds"
    assert "ceo@company.test" not in out.model_dump_json()


async def test_the_card_shows_the_real_address_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chain of task 3973: the preview feeds the approval card of the
    chat. For a planted mail, the card names the real address once and never
    the planted one. Mutation: show the stored name in the preview -> red."""
    pytest.importorskip("agent_framework", reason="MAF not installed")
    from skill_my_tasks import core

    row = types.SimpleNamespace(
        id="e1", subject="Pay", snippet="today", thread_id="t1",
        from_address=json.dumps({"name": "ceo@company.test", "email": REAL}))

    @contextlib.asynccontextmanager
    async def session() -> Any:
        yield object()

    async def load(*_a: Any) -> Any:
        return row

    async def none(*_a: Any) -> Any:
        return None

    async def nothing(*_a: Any) -> list[Any]:
        return []

    monkeypatch.setattr(ce, "_tenant_session", session)
    monkeypatch.setattr(ce, "_load_email", load)
    monkeypatch.setattr(ce, "_find_existing_capture", none)
    monkeypatch.setattr(ce, "_find_similar_tasks", nothing)
    preview = await ce.preview_capture_from_email(
        ce.CaptureFromEmailRequest(account_id="a1", email_id="e1"),
        user=types.SimpleNamespace(email="member@company.test"))

    async def request(method: str, path: str, **_k: Any) -> Any:
        assert (method, path) == ("POST", core._CAPTURE_EMAIL_PREVIEW)
        return json.loads(preview.model_dump_json())

    monkeypatch.setattr(core, "_request", request)
    got = await core._source_row({"account_id": "a1", "email_id": "e1"})
    assert isinstance(got, tuple)
    assert got[1] == f"source mail: from «{REAL}», subject: «Pay»"
