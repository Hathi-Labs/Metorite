"""WS-61 KB-1 — the Knowledge kill switch, hermetic.

Spec: ``project-docs/specs/knowledge_base.md`` §13. ``KNOWLEDGE_ENABLED`` is
default OFF and ``KNOWLEDGE_ORGS`` default empty. Off, or with the caller's
organization off the list, every ``/knowledge`` route answers 404, before
the feature gate, so a dark app does not announce itself. The organization
comes from the server-side ``UserContext``, never from the request.
"""
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("gateway.routes.knowledge", reason="gateway not installed")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from acb_common.env_guard import is_platform_env
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes import knowledge as kb_package
from gateway.routes.knowledge import flags as kb_flags

ORG = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"

#: Each route of KB-1, KB-2f, KB-2g, KB-3 and KB-5a, with a body that passes validation where one
#: is needed.
CALLS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/knowledge/spaces", None),
    ("POST", "/knowledge/spaces", {"name": "Ops"}),
    ("PATCH", f"/knowledge/spaces/{OTHER}", {"name": "Ops"}),
    ("PUT", f"/knowledge/spaces/{OTHER}/grants", {"grants": []}),
    ("GET", "/knowledge/pages", None),
    ("GET", f"/knowledge/pages/{OTHER}", None),
    ("POST", "/knowledge/pages", {"title": "A page"}),
    ("PATCH", f"/knowledge/pages/{OTHER}", {"title": "A page"}),
    ("POST", f"/knowledge/pages/{OTHER}/publish", None),
    ("POST", f"/knowledge/pages/{OTHER}/move", {"parent_id": None}),
    ("POST", f"/knowledge/pages/{OTHER}/archive", None),
    ("POST", f"/knowledge/pages/{OTHER}/restore", None),
    ("POST", f"/knowledge/pages/{OTHER}/revert", {"version": 1}),
    ("GET", f"/knowledge/pages/{OTHER}/versions/2", None),
    ("GET", f"/knowledge/pages/{OTHER}/backlinks", None),
    ("GET", "/knowledge/search?q=refund+window", None),
    # KB-2f: the home lists and the favourites.
    ("GET", "/knowledge/recent", None),
    ("GET", "/knowledge/favorites", None),
    ("POST", f"/knowledge/pages/{OTHER}/favorite", None),
    ("DELETE", f"/knowledge/pages/{OTHER}/favorite", None),
    # KB-5a: the proposals.
    ("POST", "/knowledge/proposals", {"kind": "page", "title": "A page"}),
    ("GET", "/knowledge/proposals", None),
    ("POST", f"/knowledge/proposals/{OTHER}/accept", None),
    ("POST", f"/knowledge/proposals/{OTHER}/reject", None),
]


def _user(org: str | None = ORG, *perms: str) -> UserContext:
    return UserContext(
        email="m@kb.test", role=UserRole.EMPLOYEE,
        access=build_access(list(perms)), organization_id=org,
    )


def _client(user: UserContext) -> TestClient:
    app = FastAPI()
    app.include_router(kb_package.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _codes(user: UserContext) -> set[int]:
    client = _client(user)
    return {
        client.request(method, path, json=body).status_code
        for method, path, body in CALLS
    }


def _switch(monkeypatch: pytest.MonkeyPatch, enabled: bool, orgs: str) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_enabled", enabled, raising=False)
    monkeypatch.setattr(settings, "knowledge_orgs", orgs, raising=False)


def test_the_settings_default_is_off_and_empty() -> None:
    from acb_common.settings import Settings

    fields = Settings.model_fields
    assert fields["knowledge_enabled"].default is False
    assert fields["knowledge_orgs"].default == ""
    assert fields["knowledge_embed"].default == ""


def _template(path: str) -> str:
    """The route template of a concrete path in ``CALLS``."""
    if "/proposals/" in path:
        name = "{proposal_id}"
    else:
        name = "{page_id}" if "/pages/" in path else "{space_id}"
    out = path.split("?")[0].replace(OTHER, name)
    return out.replace("/versions/2", "/versions/{version}")


def test_the_router_carries_every_route() -> None:
    found = {
        (sorted(r.methods)[0], r.path) for r in kb_package.router.routes
    }
    assert found == {(m, _template(p)) for m, p, _ in CALLS}


def test_switch_off_answers_404_on_each_route(monkeypatch: pytest.MonkeyPatch) -> None:
    _switch(monkeypatch, False, "*")
    assert _codes(_user(ORG, "feature:knowledge", "knowledge:manage")) == {404}


def test_an_org_off_the_list_gets_404_not_403(monkeypatch: pytest.MonkeyPatch) -> None:
    _switch(monkeypatch, True, OTHER)
    assert _codes(_user(ORG, "feature:knowledge")) == {404}
    assert _codes(_user(ORG)) == {404}


def test_an_empty_list_allows_no_org(monkeypatch: pytest.MonkeyPatch) -> None:
    _switch(monkeypatch, True, "")
    assert _codes(_user(ORG, "feature:knowledge")) == {404}


def test_no_organization_gets_404_also_with_star(monkeypatch: pytest.MonkeyPatch) -> None:
    _switch(monkeypatch, True, "*")
    assert _codes(_user(None, "feature:knowledge")) == {404}


def test_a_listed_org_without_the_feature_gets_403(monkeypatch: pytest.MonkeyPatch) -> None:
    _switch(monkeypatch, True, f" {ORG.upper()} , {OTHER}")
    assert _codes(_user(ORG)) == {403}


def test_the_reader_reads_ids_trimmed_and_in_any_case(monkeypatch: pytest.MonkeyPatch) -> None:
    _switch(monkeypatch, True, f"  {ORG.upper()} ")
    assert kb_flags.knowledge_org_allowed(ORG)
    assert not kb_flags.knowledge_org_allowed(OTHER)
    _switch(monkeypatch, True, "*")
    assert kb_flags.knowledge_org_allowed(OTHER)
    assert not kb_flags.knowledge_org_allowed("")


def test_the_switch_runs_before_the_feature_gate() -> None:
    deps = kb_package.router.dependencies
    assert deps[0].dependency is kb_flags.require_knowledge_enabled


@pytest.mark.parametrize("name", ["KNOWLEDGE_ENABLED", "KNOWLEDGE_ORGS", "KNOWLEDGE_EMBED",
                                  "KNOWLEDGE_EMBED_SWEEP"])
def test_both_names_are_platform_env(name: str) -> None:
    """A tenant cannot set the switch through an Integrations write."""
    assert is_platform_env(name)


# ── KB-3: KNOWLEDGE_EMBED ───────────────────────────────────────────────────

def _embed(monkeypatch: pytest.MonkeyPatch, orgs: str) -> None:
    monkeypatch.setattr(get_settings(), "knowledge_embed", orgs, raising=False)


def test_embed_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _switch(monkeypatch, True, "*")
    _embed(monkeypatch, "")
    assert not kb_flags.knowledge_embed_allowed(ORG)


def test_embed_reads_the_org_list_and_the_star(monkeypatch: pytest.MonkeyPatch) -> None:
    _switch(monkeypatch, True, "*")
    _embed(monkeypatch, f" {ORG.upper()} ")
    assert kb_flags.knowledge_embed_allowed(ORG)
    assert not kb_flags.knowledge_embed_allowed(OTHER)
    _embed(monkeypatch, "*")
    assert kb_flags.knowledge_embed_allowed(OTHER)
    assert not kb_flags.knowledge_embed_allowed(None)


def test_embed_needs_knowledge_on_for_the_org(monkeypatch: pytest.MonkeyPatch) -> None:
    """No embed spend for an org that Knowledge does not serve."""
    _embed(monkeypatch, "*")
    _switch(monkeypatch, False, "*")
    assert not kb_flags.knowledge_embed_allowed(ORG)
    _switch(monkeypatch, True, OTHER)
    assert not kb_flags.knowledge_embed_allowed(ORG)
