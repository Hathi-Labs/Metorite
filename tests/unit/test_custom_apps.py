"""Custom Apps (App Workshop) — pure helper coverage, no DB/app required.

Locks the Phase-0 kernel in ``gateway.routes.apps._common``: slug rules,
workspace path containment (traversal / absolute / dotfile / symlink escapes),
the can_view/can_edit visibility matrix (incl. AGENT principals and the
``agents:*`` grant), the AI-budget math, and the bundle-size cap.
RFC: docs/app-workshop/README.md §4.1 / §7.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from acb_auth import UserContext, UserRole
from fastapi import HTTPException
from gateway.routes.apps._common import (
    DEFAULT_AI_BUDGET_TOKENS,
    MAX_AI_BUDGET_TOKENS,
    can_edit,
    can_view,
    check_bundle_size,
    generate_slug,
    is_valid_slug,
    resolve_ai_budget,
    resolve_ai_tier,
    role_for,
    scope_set_hash,
)

OWNER = UserContext(email="owner@fracktal.in", role=UserRole.EMPLOYEE)
TEAMMATE = UserContext(email="mate@fracktal.in", role=UserRole.EMPLOYEE)
AGENT = UserContext(email="system:internal", role=UserRole.AGENT)


def _app(**overrides: Any) -> dict[str, Any]:
    row = {
        "owner_email": "owner@fracktal.in",
        "visibility": "private",
        "status": "draft",
    }
    row.update(overrides)
    return row


# ── Slugs ────────────────────────────────────────────────────────────────────

def test_slug_validation() -> None:
    assert is_valid_slug("filament-tracker")
    assert is_valid_slug("abc")
    assert is_valid_slug("a" * 48)
    assert not is_valid_slug("ab")               # too short
    assert not is_valid_slug("a" * 49)           # too long
    assert not is_valid_slug("-abc")             # leading dash
    assert not is_valid_slug("abc-")             # trailing dash
    assert not is_valid_slug("ABC")              # uppercase
    assert not is_valid_slug("a_bc")             # underscore
    assert not is_valid_slug("")


def test_slug_generation_normalizes_names() -> None:
    assert generate_slug("Filament Tracker!") == "filament-tracker"
    assert generate_slug("  Quote -- Calculator  ") == "quote-calculator"


def test_slug_generation_pads_short_and_empty_names() -> None:
    assert generate_slug("Xy") == "xy-app"
    assert generate_slug("!!!") == "app"
    assert is_valid_slug(generate_slug("!!!"))


def test_slug_generation_uniqueness_suffix() -> None:
    taken = {"filament-tracker", "filament-tracker-2"}
    assert generate_slug("Filament Tracker", taken) == "filament-tracker-3"


def test_slug_generation_stays_within_48_chars() -> None:
    long_name = "x" * 80
    slug = generate_slug(long_name)
    assert is_valid_slug(slug)
    collided = generate_slug(long_name, {slug})
    assert is_valid_slug(collided)
    assert collided.endswith("-2")


# ── Path containment ─────────────────────────────────────────────────────────

def test_clean_app_rel_allows_workspace_paths(tmp_path: Path) -> None:
    from acb_skills import safe_open
    from gateway.routes.apps._common import clean_app_rel

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.jsx").write_text("ok")
    rel = clean_app_rel("src/app.jsx")
    assert rel == "src/app.jsx"
    assert safe_open.read_bytes(tmp_path, rel) == b"ok"


@pytest.mark.parametrize("bad", [
    "../evil.txt",
    "a/../../evil.txt",
    "/etc/passwd",
    ".git/config",
    ".env",
    "src/.hidden",
    "..",
    "",
])
def test_clean_app_rel_blocks_bad_paths(tmp_path: Path, bad: str) -> None:
    from gateway.routes.apps._common import clean_app_rel

    with pytest.raises(HTTPException) as exc:
        clean_app_rel(bad)
    assert exc.value.status_code == 400


def test_a_symlink_escape_is_refused(tmp_path: Path) -> None:
    from acb_skills import safe_open
    from gateway.routes.apps._common import clean_app_rel

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        (workspace / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this box cannot make a symlink")
    with pytest.raises(safe_open.UnsafePath):
        safe_open.read_bytes(workspace, clean_app_rel("link/secret.txt"))


# ── Visibility matrix ────────────────────────────────────────────────────────

def test_owner_edits_and_views() -> None:
    app = _app()
    assert can_edit(app, OWNER)
    assert can_view(app, OWNER)
    assert role_for(app, OWNER) == "own"


def test_private_draft_hidden_from_others() -> None:
    app = _app()
    assert not can_view(app, TEAMMATE)
    assert not can_edit(app, TEAMMATE)


def test_org_live_viewable_by_all_but_not_editable() -> None:
    app = _app(visibility="org", status="live")
    assert can_view(app, TEAMMATE)
    assert not can_edit(app, TEAMMATE)
    assert role_for(app, TEAMMATE) == "use"


def test_org_draft_not_viewable_by_others() -> None:
    app = _app(visibility="org", status="draft")
    assert not can_view(app, TEAMMATE)


def test_email_grant_gives_view_and_edit_roles() -> None:
    app = _app()
    use = [("mate@fracktal.in", "use")]
    edit = [("mate@fracktal.in", "edit")]
    own = [("mate@fracktal.in", "own")]
    assert can_view(app, TEAMMATE, use)
    assert not can_edit(app, TEAMMATE, use)
    assert can_edit(app, TEAMMATE, edit)
    assert can_edit(app, TEAMMATE, own)
    assert role_for(app, TEAMMATE, edit) == "edit"
    assert role_for(app, TEAMMATE, own) == "own"


def test_grant_for_someone_else_does_not_leak() -> None:
    app = _app()
    assert not can_view(app, TEAMMATE, [("other@fracktal.in", "own")])


def test_agent_sees_org_live_only_by_default() -> None:
    assert can_view(_app(visibility="org", status="live"), AGENT)
    assert not can_view(_app(visibility="org", status="draft"), AGENT)
    assert not can_view(_app(), AGENT)


def test_agent_wildcard_grant_gives_view_not_edit() -> None:
    app = _app()
    grants = [("agents:*", "use")]
    assert can_view(app, AGENT, grants)
    assert not can_edit(app, AGENT, grants)
    # Even an edit-role grant never makes an AGENT an editor in Phase 0.
    assert not can_edit(app, AGENT, [("system:internal", "edit")])


def test_anonymous_sees_nothing() -> None:
    assert not can_view(_app(visibility="org", status="live"), None)
    assert not can_edit(_app(), None)


# ── AI budget + tier math ────────────────────────────────────────────────────

def test_ai_budget_defaults_and_clamps() -> None:
    assert resolve_ai_budget(None) == DEFAULT_AI_BUDGET_TOKENS
    assert resolve_ai_budget({}) == DEFAULT_AI_BUDGET_TOKENS
    assert resolve_ai_budget({"ai_budget_tokens": 1000}) == 1000
    assert (resolve_ai_budget({"ai_budget_tokens": 50_000_000})
            == MAX_AI_BUDGET_TOKENS)
    assert resolve_ai_budget({"ai_budget_tokens": -5}) == 0
    assert (resolve_ai_budget({"ai_budget_tokens": "lots"})
            == DEFAULT_AI_BUDGET_TOKENS)


def test_ai_tier_resolution() -> None:
    manifest = {"scopes": ["identity:read", "ai:tier-2"]}
    # A known requested alias wins; unknown falls back to the manifest scope.
    assert resolve_ai_tier("tier-powerful", manifest) == "tier-powerful"
    assert resolve_ai_tier("tier-3", manifest) == "tier-powerful"
    assert resolve_ai_tier("gpt-4o", manifest) == "tier-balanced"
    assert resolve_ai_tier(None, manifest) == "tier-balanced"
    # No ai scope at all → the cheapest alias.
    assert resolve_ai_tier(None, {"scopes": ["storage:app"]}) == "tier-fast"
    assert resolve_ai_tier(None, None) == "tier-fast"


def test_scope_set_hash_is_order_insensitive() -> None:
    a = scope_set_hash(["storage:app", "ai:tier-1"])
    b = scope_set_hash(["ai:tier-1", "storage:app"])
    assert a == b
    assert a != scope_set_hash(["ai:tier-1"])


# ── Bundle size cap ──────────────────────────────────────────────────────────

def test_bundle_size_cap() -> None:
    assert check_bundle_size(b"<html></html>") == b"<html></html>"
    with pytest.raises(HTTPException) as exc:
        check_bundle_size(b"x" * 11, cap=10)
    assert exc.value.status_code == 413


# ── Conformance scan (RFC §4.0 layer 3) ──────────────────────────────────────

from gateway.routes.apps._conformance import scan_bundle  # noqa: E402


def test_conformance_clean_platform_native_app() -> None:
    html = (
        "<html><head><style>body{background:hsl(220 13% 8%)}</style></head>"
        "<body><script>"
        "cc.storage.table('spools').list().then(render);"
        "cc.ai.complete('summarize').then(show);"
        "</script></body></html>"
    )
    errors, warnings = scan_bundle(html)
    assert errors == []
    assert warnings == []


def test_conformance_blocks_external_script_and_style() -> None:
    errors, _ = scan_bundle('<script src="https://cdn.example.com/react.js">')
    assert any("external script" in e for e in errors)
    errors, _ = scan_bundle('<link rel="stylesheet" href="//fonts.example.com/a.css">')
    assert any("external script" in e for e in errors)
    errors, _ = scan_bundle("<style>.x{background:url(https://x.com/i.png)}</style>")
    assert any("CSS url()" in e for e in errors)


def test_conformance_blocks_direct_network_calls() -> None:
    for snippet in (
        "fetch('https://api.example.com/v1')",
        'fetch("//api.example.com")',
        "new WebSocket('wss://x')",
        "new XMLHttpRequest()",
        "navigator.serviceWorker.register('/sw.js')",
    ):
        errors, _ = scan_bundle(f"<script>{snippet}</script>")
        assert errors, snippet


def test_conformance_blocks_credential_shaped_strings() -> None:
    errors, _ = scan_bundle("<script>var k='sk-abcDEF1234567890abcd';</script>")
    assert any("credential-shaped" in e for e in errors)
    errors, _ = scan_bundle("<script>var a='AKIAABCDEFGHIJKLMNOP';</script>")
    assert any("credential-shaped" in e for e in errors)


def test_conformance_warns_on_browser_storage_and_bare_urls() -> None:
    _, warnings = scan_bundle("<script>localStorage.setItem('a','b')</script>")
    assert any("cc.storage" in w for w in warnings)
    # A bare URL in text content warns but does not block.
    errors, warnings = scan_bundle("<p>Docs: https://wiki.internal/page</p>")
    assert errors == []
    assert any("external URL" in w for w in warnings)


def test_conformance_bare_url_warning_suppressed_when_error_fired() -> None:
    errors, warnings = scan_bundle('<script src="https://cdn.x.com/a.js"></script>')
    assert errors
    assert not any("external URL" in w for w in warnings)
