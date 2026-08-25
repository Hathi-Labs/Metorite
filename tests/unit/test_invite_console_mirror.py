"""CP-2f — the gateway half: ``invite_member`` mirrors onto the Customer Console.

Spec: ``project-docs/specs/customer_console.md`` CP-2f done-when 8 ·
``work_plan.md`` §3 **D50.2** · ``user_management_contract.md`` R11.

Two halves, and they fence different failures:

1. **Structural (AST).** The Console call sits **outside** ``invite_member``'s
   ``async with _tenant_session()`` block — POST-COMMIT, like the two H6 mirrors
   beside it. Inside the block it would run before the authoritative ``app_user``
   write is durably committed, so a rollback would leave a Console membership for
   a member who does not exist, and a slow Console would hold a tenant
   transaction open. That is a property of the SHAPE of the function, which no
   behavioural test on a fake session can see.
2. **Behavioural.** The wire the ONE Console client builds — ``actor_email`` is
   the AUTHENTICATED admin, no ``org_slug``, no ``role`` — plus the unwired
   no-op, driven through an ``httpx.MockTransport`` Console.

⚠️ **DB-free on purpose**, like ``test_seat_admin_proxy_route.py`` and
``test_console_dependency_boundary.py``: the unwired path returns before any hop
and the wired path talks to a MockTransport. A DB gate leaking in would disarm
this fence silently, so it is named in ``pr-check.yml``'s hand-list beside the
suites that must actually run.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("httpx")

import httpx
from acb_auth.console_resolve import (
    ConsoleMemberWriteUnavailable,
    invite_member_on_console,
)

_ROOT = Path(__file__).resolve().parents[2]
_ROUTE = _ROOT / "apps/services/gateway/gateway/routes/admin/members.py"

CONSOLE_URL = "https://console.invalid"
DEPLOYMENT_KEY = "cc_depl_fixture_notarealsecret"


# ── 1. The structural half — POST-COMMIT, by the shape of the function ───────

def _invite_member_node() -> ast.AsyncFunctionDef:
    tree = ast.parse(_ROUTE.read_text(encoding="utf-8-sig"), filename=str(_ROUTE))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "invite_member"
        ):
            return node
    raise AssertionError(
        f"{_ROUTE.name} has no `invite_member` — this fence is now vacuous"
    )


def _calls_named(node: ast.AST) -> set[str]:
    """Every plain function name called anywhere under *node*."""
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            names.add(sub.func.id)
    return names


def test_the_console_mirror_is_called_at_all() -> None:
    """Guards the two assertions below from passing vacuously."""
    assert "invite_member_on_console" in _calls_named(_invite_member_node()), (
        "`invite_member` no longer calls `invite_member_on_console`. Without it "
        "an invited colleague has NO Console membership: invisible to "
        "`GET /me/members`, a 404 at the seat door, and — with resolve armed — "
        "resolving `console-empty` into the self-serve funnel that creates them "
        "an organization of their own (customer_console.md CP-2f)."
    )


def test_the_console_mirror_runs_OUTSIDE_the_tenant_transaction() -> None:
    """POST-COMMIT, exactly like the H6 shadow mirrors beside it.

    Inside the ``async with _tenant_session()`` block the hop would run before
    the authoritative ``app_user`` write commits — so a caller rollback would
    leave a committed Console membership orphan, the shape the H6
    orphan-closure slice was spent removing — and a slow Console would hold a
    tenant transaction open for the length of an HTTP round trip.
    """
    node = _invite_member_node()
    inside: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.AsyncWith):
            for item in sub.items:
                call = item.context_expr
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "_tenant_session"
                ):
                    for stmt in sub.body:
                        inside |= _calls_named(stmt)
    assert "invite_member_on_console" not in inside, (
        "the Console mirror is INSIDE `invite_member`'s `_tenant_session` "
        "block. It must run after the authoritative commit, best-effort, in "
        "its own session — the posture `mirror_identity_membership` / "
        "`mirror_membership_status` already use two lines above it."
    )


def test_a_console_failure_cannot_escape_the_route() -> None:
    """The mirror is best-effort: it never changes the invite's answer.

    Structural rather than behavioural because the failure it guards is an
    exception TYPE that was not anticipated — a ``try`` that catches only
    ``ConsoleMemberWriteUnavailable`` would let, say, a ``TypeError`` from a
    changed signature turn a committed invite into a 500. So both the specific
    handler and the broad one are required.
    """
    node = _invite_member_node()
    handlers: list[ast.ExceptHandler] = [
        h
        for sub in ast.walk(node)
        if isinstance(sub, ast.Try)
        and "invite_member_on_console" in _calls_named(sub)
        for h in sub.handlers
    ]
    assert handlers, (
        "`invite_member_on_console` is not wrapped in a `try` — a Console "
        "outage would then fail an invite whose tenant-plane write has already "
        "committed, which is the one thing a best-effort mirror must not do"
    )
    caught = {
        h.type.id for h in handlers
        if isinstance(h.type, ast.Name)
    }
    assert "ConsoleMemberWriteUnavailable" in caught, caught
    assert "Exception" in caught, (
        f"only {sorted(caught)} is caught. A best-effort mirror on a shipped "
        "write path needs the broad handler too: any other exception type "
        "turns a committed invite into a 500."
    )


# ── 2. The behavioural half — the wire, through a MockTransport Console ──────

@pytest.fixture
def unwired(monkeypatch):
    monkeypatch.delenv("CUSTOMER_CONSOLE_URL", raising=False)
    monkeypatch.delenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", raising=False)
    from acb_common.settings import get_settings

    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
    monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", DEPLOYMENT_KEY)
    from acb_common.settings import get_settings

    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def _mock_console(monkeypatch, handler):
    """Drive the ONE Console client through an httpx.MockTransport."""
    def _new_client(*_a, **_kw):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(
        "acb_auth.console_resolve._new_http_client", _new_client
    )


async def test_an_unwired_box_never_opens_a_socket(unwired, monkeypatch):
    """Ships dark by reach: no URL and no key means no hop at all."""
    def _explode(*_a, **_kw):
        raise AssertionError("an unwired box must not build an HTTP client")

    monkeypatch.setattr(
        "acb_auth.console_resolve._new_http_client", _explode
    )
    with pytest.raises(ConsoleMemberWriteUnavailable):
        await invite_member_on_console(
            actor_email="admin@customer.example",
            member_email="new@customer.example",
        )


async def test_the_body_names_the_actor_and_NO_org_and_NO_role(
    wired, monkeypatch
):
    """R11 at this hop, and D12 beside it."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"created": True, "status": "invited"})

    _mock_console(monkeypatch, handler)
    status, body = await invite_member_on_console(
        actor_email="admin@customer.example",
        member_email="New@Customer.Example",
        display_name="Ada",
    )

    assert (status, body) == (200, {"created": True, "status": "invited"})
    assert seen["url"] == f"{CONSOLE_URL}/registry/members"
    assert seen["auth"] == f"Bearer {DEPLOYMENT_KEY}"
    # The acting admin is the SESSION's, and the org is the ANSWER — derived
    # Console-side from placement ∩ membership, never asserted here.
    assert seen["body"]["actor_email"] == "admin@customer.example"
    assert "org_slug" not in seen["body"]
    assert "organization_id" not in seen["body"]
    # D12: the tenant's role vocabulary is not the registry's, and this wire
    # mints no mapping between them.
    assert "role" not in seen["body"]
    assert "roles" not in seen["body"]
    assert seen["body"]["member_email"] == "New@Customer.Example"
    assert seen["body"]["display_name"] == "Ada"


@pytest.mark.parametrize("status", [500, 502, 401, 408, 429])
async def test_a_no_answer_status_is_an_outage_not_a_verdict(
    wired, monkeypatch, status
):
    """The line `_post_resolve` draws (finding P1-1), applied here."""
    _mock_console(monkeypatch, lambda _r: httpx.Response(status))
    with pytest.raises(ConsoleMemberWriteUnavailable):
        await invite_member_on_console(
            actor_email="admin@customer.example",
            member_email="new@customer.example",
        )


@pytest.mark.parametrize("status", [400, 403, 409])
async def test_a_door_refusal_is_RETURNED_not_raised(wired, monkeypatch, status):
    """A verdict is an answer. Only the caller decides it does not matter."""
    _mock_console(
        monkeypatch, lambda _r: httpx.Response(status, json={"detail": "no"})
    )
    got, body = await invite_member_on_console(
        actor_email="admin@customer.example",
        member_email="new@customer.example",
    )
    assert got == status
    assert body == {"detail": "no"}


async def test_an_unreadable_body_is_an_empty_dict_not_a_crash(
    wired, monkeypatch
):
    _mock_console(monkeypatch, lambda _r: httpx.Response(200, text="<html>"))
    status, body = await invite_member_on_console(
        actor_email="admin@customer.example",
        member_email="new@customer.example",
    )
    assert (status, body) == (200, {})
