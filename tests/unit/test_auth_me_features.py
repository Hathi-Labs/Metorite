"""``/auth/me``'s feature resolution — the answer the sidebar renders.

Filed after a real report: *"I do not have access to this in the UI."* Two
recently-registered panes were absent from the sidebar and there was no way,
from inside the product, to tell which of four causes it was — missing grant,
deny override, unregistered slug, or a code mirror that had gone stale against
the database. Every one of them renders as the same nothing.

What is locked here:

1. **The catalog is the registry, and the code list is a mirror.** A slug
   seeded by a migration is reachable even when the deployed ``acb_auth``
   predates it. That is the failure mode with no symptom: nothing errors, the
   pane is simply not there.
2. **The union, both ways.** A slug in code but not yet in the database is
   still reported, so a box mid-deploy does not lose panes either.
3. **This grants nothing.** The gate is ``require_feature_router`` on each
   router; everything here is a report about that same decision.
4. **Absence is explained.** ``features_denied`` names the permission each
   missing pane needs, which is what turns "the app doesn't have that" into
   something a person can act on.

Hermetic: pure function, real ``build_access``. No Postgres, no TestClient.
"""

from __future__ import annotations

import pytest
from acb_auth import build_access
from acb_auth.permissions import FEATURES
from gateway.routes.admin.me import resolve_features

OWNER = build_access(["*"])
ADMIN = build_access(["feature:*", "admin:members:read"])
MEMBER = build_access(["feature:chat", "feature:tasks"])


def slugs(access, catalog):
    return resolve_features(access, catalog)["features"]


def denied(access, catalog):
    return {d["slug"]: d["permission"] for d in resolve_features(access, catalog)["features_denied"]}


# ── The catalog is the registry ─────────────────────────────────────────────

def test_a_slug_the_code_has_never_heard_of_is_still_reachable():
    """The whole point. `feature_catalog` is what a migration seeds — 130's own
    header calls it "the nav-pane registry ... so adding a pane is a seeded row,
    not a code change in the gateway AND the frontend". Resolving against the
    code tuple alone makes that promise false, silently."""
    assert "brandnew" not in FEATURES
    assert "brandnew" in slugs(OWNER, ["brandnew"])


def test_a_slug_only_in_code_survives_an_unseeded_database():
    """The other direction: a fresh box whose migrations have not run yet must
    not lose every pane."""
    assert "chat" in slugs(OWNER, [])


def test_the_two_sources_are_unioned_not_intersected():
    both = slugs(OWNER, ["brandnew"])
    assert "brandnew" in both
    assert "chat" in both


def test_catalog_order_is_preserved_ahead_of_the_code_mirror():
    """The catalog carries `sort_order`; the code tuple does not. Reading the
    catalog first is what lets the sidebar's order be a data decision."""
    got = slugs(OWNER, ["zulu", "alpha"])
    assert got[:2] == ["zulu", "alpha"]


def test_a_slug_in_both_appears_once():
    got = slugs(OWNER, ["chat", "chat", "tasks"])
    assert got.count("chat") == 1


# ── This grants nothing ─────────────────────────────────────────────────────

def test_a_member_does_not_reach_a_catalog_slug_they_lack():
    """Widening the SOURCE of the list must not widen the ANSWER. If this ever
    goes green-by-accident the endpoint has become a grant."""
    assert "projects" not in slugs(MEMBER, ["projects"])
    assert "people" not in slugs(MEMBER, ["people"])


def test_a_deny_override_still_wins():
    access = build_access(["feature:*"], [("feature:projects", "deny")])
    assert "projects" not in slugs(access, ["projects"])
    assert "chat" in slugs(access, ["chat"])


def test_an_inactive_member_reaches_nothing():
    access = build_access(["*"], is_active=False)
    assert slugs(access, ["projects"]) == []


@pytest.mark.parametrize("who,expected", [(OWNER, True), (ADMIN, True), (MEMBER, False)])
def test_the_two_panes_that_prompted_this(who, expected):
    """`projects` and `people` are `is_default false`, so they reach a `*` or
    `feature:*` holder and nobody else until an admin grants them."""
    got = slugs(who, ["projects", "people"])
    assert ("projects" in got) is expected
    assert ("people" in got) is expected


# ── Absence is explained ────────────────────────────────────────────────────

def test_every_missing_pane_names_the_permission_it_needs():
    """Without this the UI can only say "not available", and four different
    causes are indistinguishable."""
    assert denied(MEMBER, ["projects"])["projects"] == "feature:projects"


def test_allowed_and_denied_are_disjoint_and_cover_everything():
    result = resolve_features(MEMBER, ["projects", "people", "chat"])
    allowed = set(result["features"])
    refused = {d["slug"] for d in result["features_denied"]}
    assert not (allowed & refused)
    assert allowed | refused >= {"projects", "people", "chat"}


def test_an_owner_is_denied_nothing():
    assert resolve_features(OWNER, ["projects", "people"])["features_denied"] == []


# ── AI Studio (WS-STU J1) ───────────────────────────────────────────────────

GUEST = build_access(["feature:chat", "apps:use:*"])
WORKFLOWS_ONLY = build_access(["feature:workflows"])


@pytest.mark.parametrize("who,expected", [
    (OWNER, True), (ADMIN, True), (build_access(["feature:*"]), True),
    (GUEST, False), (WORKFLOWS_ONLY, False),
    (build_access(["feature:*"], [("feature:studio", "deny")]), False),
])
def test_studio_reaches_feature_holders_from_the_code_mirror_alone(who, expected):
    """`studio` is in FEATURES, so `/auth/me` lists it also on a box whose
    catalog has no `studio` row yet. The interim gate of B1 is gone: a member
    with only `feature:workflows` does not reach Studio."""
    assert "studio" in FEATURES
    assert ("studio" in slugs(who, [])) is expected


def test_a_member_without_studio_is_told_which_permission_it_needs():
    assert denied(WORKFLOWS_ONLY, ["studio"])["studio"] == "feature:studio"
