"""Org access control — per-user integration credentials and org memory.

Spec: ``project-docs/specs/org_access_control.md`` §5 seam 3.

Two things are being protected here, and they fail in opposite directions:

* **Credentials must not over-reach.** An agent declaring an integration in
  its ``config.json`` must not thereby hand a member a service they are not
  authorised for.
* **Credentials must not under-reach.** A background run (cron, reconciler,
  webhook) has no acting member, and must keep every credential it had before
  org access control existed. A filter that quietly starves those runs is a
  worse outage than the leak it prevents, because nothing 403s — the work just
  silently stops happening.
"""
from __future__ import annotations

import contextvars

import pytest
from acb_auth import build_access, integration_use_permission, validate_permission
from acb_skills.integrations import build_integrations


def _authorizer(access):
    return lambda service: access.has(integration_use_permission(service))


# ── Vocabulary ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "service", ["zoho-crm", "clickup", "google-sheets", "google-maps", "anymailfinder"]
)
def test_service_names_produce_valid_permissions(service: str) -> None:
    """Service names carry dashes; the segment grammar must accept them."""
    assert validate_permission(integration_use_permission(service)) == (
        f"integrations:use:{service}"
    )


# ── The filter ──────────────────────────────────────────────────────────────

def test_no_predicate_means_no_filtering() -> None:
    """The background-run path: behaviour identical to before this feature."""
    _, unavailable = build_integrations(["zoho-crm"], [], None)
    # Unresolvable here (no env), but NOT for an authorisation reason.
    assert "not have access" not in unavailable.get("zoho-crm", "")


def test_unauthorized_service_is_reported_not_resolved() -> None:
    access = build_access(["integrations:use:clickup"])
    _, unavailable = build_integrations(
        ["zoho-crm"], [], None, is_authorized=_authorizer(access)
    )
    assert "zoho-crm" in unavailable
    assert "integrations:use:zoho-crm" in unavailable["zoho-crm"]


def test_agent_cannot_widen_access_by_declaring_more() -> None:
    """The core property: config.json states a want, not an entitlement."""
    access = build_access([])          # member holds nothing
    resolved, unavailable = build_integrations(
        ["zoho-crm", "clickup", "apollo"], ["serpapi"], None,
        is_authorized=_authorizer(access),
    )
    assert resolved == {}
    assert set(unavailable) == {"zoho-crm", "clickup", "apollo", "serpapi"}
    assert all("integrations:use:" in reason for reason in unavailable.values())


def test_wildcard_grant_authorizes_every_service() -> None:
    access = build_access(["integrations:use:*"])
    predicate = _authorizer(access)
    assert predicate("zoho-crm") and predicate("clickup") and predicate("apollo")


def test_deny_one_service_on_top_of_the_wildcard() -> None:
    """The realistic admin action: everything except Zoho."""
    access = build_access(
        ["integrations:use:*"], [("integrations:use:zoho-crm", "deny")]
    )
    predicate = _authorizer(access)
    assert predicate("clickup")
    assert not predicate("zoho-crm")


def test_allow_one_service_against_a_denied_wildcard() -> None:
    """Specificity again: 'only ClickUp' is expressible."""
    access = build_access(
        ["integrations:use:*"],
        [("integrations:use:*", "deny"), ("integrations:use:clickup", "allow")],
    )
    predicate = _authorizer(access)
    assert predicate("clickup")
    assert not predicate("zoho-crm")


def test_optional_integrations_are_filtered_too() -> None:
    access = build_access(["integrations:use:clickup"])
    _, unavailable = build_integrations(
        [], ["zoho-crm"], None, is_authorized=_authorizer(access)
    )
    assert "zoho-crm" in unavailable


def test_suspended_member_authorizes_nothing() -> None:
    access = build_access(["integrations:use:*"], is_active=False)
    predicate = _authorizer(access)
    assert not predicate("clickup")


# ── The executor's authorizer ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_authorizer_is_none_without_an_acting_member() -> None:
    """Cron / reconciler / webhook runs must not be filtered."""
    from orchestrator.executor import _integration_authorizer

    assert await _integration_authorizer(None) is None
    assert await _integration_authorizer({}) is None
    assert await _integration_authorizer({"user_email": ""}) is None


@pytest.mark.asyncio
async def test_authorizer_ignores_synthetic_principals() -> None:
    """`system:internal` is the platform, not a member."""
    from orchestrator.executor import _integration_authorizer

    assert await _integration_authorizer({"user_email": "system:internal"}) is None


@pytest.mark.asyncio
async def test_authorizer_does_not_filter_an_unresolvable_address(
    monkeypatch,
) -> None:
    """An address that is not an active member leaves the run unfiltered.

    Deliberate: this feature restricts a KNOWN member's reach. Silently
    starving a run because the payload carried an address with no `app_user`
    row would be a regression with no error message attached to it.
    """
    import orchestrator.executor as executor
    import acb_auth

    async def _inactive(email, **kwargs):
        return build_access(["integrations:use:*"], is_active=False)

    monkeypatch.setattr(acb_auth, "resolve_access", _inactive)
    assert await executor._integration_authorizer(
        {"user_email": "ghost@fracktal.in"}
    ) is None


@pytest.mark.asyncio
async def test_authorizer_filters_an_active_member(monkeypatch) -> None:
    import orchestrator.executor as executor
    import acb_auth

    async def _active(email, **kwargs):
        return build_access(
            ["integrations:use:*"], [("integrations:use:zoho-crm", "deny")]
        )

    monkeypatch.setattr(acb_auth, "resolve_access", _active)
    predicate = await executor._integration_authorizer(
        {"user_email": "priya@fracktal.in"}
    )
    assert predicate is not None
    assert predicate("clickup")
    assert not predicate("zoho-crm")


# ── Org memory gate ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_org_memory_write_is_refused_without_permission() -> None:
    """Set the var in THIS context and await here.

    `ctx.run(lambda: coro())` only builds the coroutine inside the context —
    it is awaited outside it, so the ContextVar would not be visible. The
    executor sets it on the same task that runs the agent, which is what this
    reproduces (token/reset rather than a copied context).
    """
    from acb_skills import memory_tools

    token = memory_tools._memory_permission.set(
        lambda p: p != "memory:write_org"
    )
    try:
        result = await memory_tools.save_org_memory("Net 30 payment terms")
    finally:
        memory_tools._memory_permission.reset(token)

    assert "do not have permission" in result
    assert "memory:write_org" in result


@pytest.mark.asyncio
async def test_personal_memory_is_unaffected_by_the_org_gate() -> None:
    """Only the org scope is gated; personal memory is already user-keyed."""
    from acb_skills import memory_tools

    token = memory_tools._memory_permission.set(lambda _p: False)
    try:
        result = await memory_tools.save_memory("I prefer concise replies")
    finally:
        memory_tools._memory_permission.reset(token)
    assert "do not have permission" not in result


@pytest.mark.asyncio
async def test_org_memory_gate_is_open_when_no_member_is_attached() -> None:
    """Background runs keep writing org memory as before."""
    from acb_skills import memory_tools

    result = await memory_tools.save_org_memory("Fiscal year runs April-March")
    # Either it worked or the memory backend is absent — but it must NOT be
    # refused on permission grounds.
    assert "do not have permission" not in result


def test_broken_permission_predicate_fails_closed() -> None:
    """A predicate that raises must not be read as 'allowed'."""
    from acb_skills import memory_tools

    def _boom(_permission: str) -> bool:
        raise RuntimeError("resolver exploded")

    def _check() -> bool:
        memory_tools._set_memory_permission(_boom)
        return memory_tools._may("memory:write_org")

    assert contextvars.copy_context().run(_check) is False
