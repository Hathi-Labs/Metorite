"""Org access control — permission model and guard behaviour.

Spec: project-docs/specs/org_access_control.md

These cover the pure resolution layer (acb_auth.permissions) and the FastAPI
guards, which is where a mistake is silent: a permission that matches nothing
is invisible rather than loud, and a wildcard that matches too much looks
identical to one that matches correctly until someone reaches data they
shouldn't.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from acb_auth import (
    ASSIGNABLE_SYSTEM_ROLES,
    CAPABILITIES,
    FEATURES,
    SYSTEM_ROLES,
    EffectiveAccess,
    InvalidPermission,
    UserContext,
    UserRole,
    agent_run_permission,
    assert_can_run_agent,
    build_access,
    feature_permission,
    get_current_user,
    permission_matches,
    require_any_permission,
    require_permission,
    validate_permission,
)
from acb_auth.access import SERVICE_ACCESS, legacy_access
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

# ── Matching ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("pattern", "required", "expected"),
    [
        ("feature:whatsapp", "feature:whatsapp", True),
        ("feature:whatsapp", "feature:email", False),
        ("*", "anything:at:all", True),
        ("feature:*", "feature:whatsapp", True),
        ("feature:*", "feature:build.apps", True),
        ("feature:*", "agents:run:x", False),
        ("agents:run:*", "agents:run:agent-sales", True),
        ("agents:*", "agents:run:agent-sales", True),
        ("agents:run:*", "agents:manage", False),
        # A prefix wildcard must not match the bare prefix itself: holding
        # "feature:*" is not holding a permission literally named "feature:".
        ("feature:*", "feature:", False),
        # Prefix must land on a segment boundary, or "feature:*" would cover an
        # unrelated "featureflags:x".
        ("feature:*", "featureflags:x", False),
    ],
)
def test_permission_matches(pattern: str, required: str, expected: bool) -> None:
    assert permission_matches(pattern, required) is expected


# ── Validation ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw",
    ["feature:whatsapp", "*", "agents:run:*", "AGENTS:RUN:X", " feature:chat "],
)
def test_validate_accepts(raw: str) -> None:
    assert validate_permission(raw) == raw.strip().lower()


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "feature:*:read",        # inward wildcard — unauditable, refused
        "*:read",
        "feature:what sapp",     # whitespace inside a segment
        "feature:" + "x" * 200,  # over the length ceiling
    ],
)
def test_validate_rejects(raw: str) -> None:
    with pytest.raises(InvalidPermission):
        validate_permission(raw)


# ── Resolution ──────────────────────────────────────────────────────────────

def test_default_is_deny() -> None:
    access = build_access(["feature:chat"])
    assert access.has("feature:chat")
    assert not access.has("feature:whatsapp")
    assert not access.has("admin:members:read")


def test_deny_override_beats_a_wildcard_role_grant() -> None:
    """The headline case: `admin`-style blanket access, minus two things."""
    access = build_access(
        ["feature:*", "agents:run:*"],
        [("feature:whatsapp", "deny"), ("feature:build.apps", "deny")],
        roles=["admin"],
    )
    assert access.can_use_feature("email")
    assert not access.can_use_feature("whatsapp")
    assert not access.can_use_feature("build.apps")
    assert access.can_run_agent("agent-sales")


def test_allow_override_adds_a_single_agent_on_top_of_a_denied_wildcard() -> None:
    """"No agents except this one" — deny the blanket, allow one by name."""
    access = build_access(
        ["feature:chat", "agents:run:*"],
        [
            ("agents:run:*", "deny"),
            ("agents:run:email-assistant", "allow"),
        ],
        roles=["member"],
    )
    assert access.can_run_agent("email-assistant")
    assert not access.can_run_agent("whatsapp-assistant")
    assert not access.can_run_agent("app-builder")


def test_deny_wins_when_two_overrides_are_equally_specific() -> None:
    access = build_access(
        ["feature:whatsapp"],
        [("feature:whatsapp", "allow"), ("feature:whatsapp", "deny")],
    )
    assert not access.can_use_feature("whatsapp")


def test_a_wildcard_deny_override_beats_an_exact_role_grant() -> None:
    """Specificity orders overrides against each other — never against roles.

    If a role's exact `feature:chat` could out-specify an admin's explicit
    `feature:*` deny, "switch everything off for this person" would silently
    leave holes.
    """
    access = build_access(
        ["feature:chat", "feature:email"],
        [("feature:*", "deny")],
        roles=["member"],
    )
    assert not access.can_use_feature("chat")
    assert not access.can_use_feature("email")
    assert access.allowed_features() == ()


def test_more_specific_deny_beats_a_broader_allow_override() -> None:
    access = build_access(
        [],
        [("agents:run:*", "allow"), ("agents:run:app-builder", "deny")],
    )
    assert access.can_run_agent("email-assistant")
    assert not access.can_run_agent("app-builder")


def test_bare_star_is_the_least_specific_pattern() -> None:
    access = build_access([], [("*", "deny"), ("feature:chat", "allow")])
    assert access.can_use_feature("chat")
    assert not access.can_use_feature("email")


def test_granted_exposes_roles_and_allow_overrides_together() -> None:
    """`granted` is for display/export; the decision uses the layers."""
    access = build_access(["feature:chat"], [("feature:email", "allow")])
    assert access.granted == frozenset({"feature:chat", "feature:email"})
    assert access.role_granted == frozenset({"feature:chat"})
    assert access.allowed == frozenset({"feature:email"})


def test_suspended_member_resolves_to_nothing() -> None:
    access = build_access(["*"], roles=["owner"], is_active=False)
    assert not access.has("feature:chat")
    assert not access.has("admin:members:read")
    assert access.allowed_features() == ()


def test_malformed_stored_permissions_are_skipped_not_fatal() -> None:
    """A bad row written before validation existed must not lock anyone out."""
    access = build_access(["feature:chat", "feature:*:read", ""])
    assert access.can_use_feature("chat")


def test_decision_explains_its_provenance() -> None:
    access = build_access(
        ["feature:*"], [("feature:whatsapp", "deny")], roles=["admin"]
    )
    granted = access.decide("feature:email")
    assert granted.allowed and granted.source == "role"
    assert granted.pattern == "feature:*"

    denied = access.decide("feature:whatsapp")
    assert not denied.allowed and denied.source == "deny-override"
    assert denied.pattern == "feature:whatsapp"

    missing = access.decide("admin:roles:manage")
    assert not missing.allowed and missing.source == "default-deny"


def test_allowed_features_follows_catalog_order() -> None:
    """Catalog order, not grant order — the access editor renders this list.

    Written against literals because the obvious form,
    ``build_access(["feature:*"]).allowed_features() == FEATURES``, compares
    the tuple with itself: ``allowed_features()`` is built by filtering
    FEATURES, so that assertion is green for any contents, in any order, and
    stayed green under a mutation that emptied the Centers half.
    """
    access = build_access(
        ["feature:center.sales", "feature:notes", "feature:chat"]
    )
    assert access.allowed_features() == ("chat", "notes", "center.sales")


# ── Agent inheritance ───────────────────────────────────────────────────────

def test_intersect_never_widens_access() -> None:
    """An agent run inherits the caller's set; it can only narrow."""
    caller = build_access(["feature:chat", "agents:run:email-assistant"])
    agent = build_access(["*"])
    combined = agent.intersect(caller)
    assert combined.can_run_agent("email-assistant")
    assert not combined.can_run_agent("app-builder")
    assert not combined.has("admin:members:read")


def test_intersect_unions_denials() -> None:
    a = build_access(["feature:*"], [("feature:whatsapp", "deny")])
    b = build_access(["feature:*"], [("feature:email", "deny")])
    combined = a.intersect(b)
    assert not combined.can_use_feature("whatsapp")
    assert not combined.can_use_feature("email")
    assert combined.can_use_feature("chat")


# ── Vocabulary invariants ───────────────────────────────────────────────────

def test_every_declared_permission_is_valid() -> None:
    for slug in FEATURES:
        validate_permission(feature_permission(slug))
    for cap in CAPABILITIES:
        validate_permission(cap)


#: The Centers the product commits to — department_centers.md §1, seeded in
#: that order by infra/postgres/140_center_features.sql.
#:
#: Retyped here ON PURPOSE, and it is the only list in this file that is. Every
#: other copy (``CENTER_GROUP_SLUGS``, ``FEATURES``, ``lib/centers.ts``) is a
#: *derivation source* for the checks below, and a check written only against
#: its own source goes green when that source empties. That is not
#: hypothetical: the first version of ``test_every_center_has_a_feature_slug``
#: iterated ``CENTER_GROUP_SLUGS``, so emptying that tuple to ``()`` turned the
#: test into a no-op that still passed. This literal is the anchor that makes
#: shrinking any of the three loud. Changing the set of Centers therefore means
#: editing this line — deliberately — and department_centers.md §4 lists every
#: other place that same edit has to land.
EXPECTED_CENTER_SLUGS: tuple[str, ...] = (
    "sales", "marketing", "finance", "operations", "people", "company",
)

#: The frontend Center registry — the file `lib/nav.ts` and `lib/access.ts`
#: both derive from, i.e. the list that decides what actually renders.
CENTERS_TS = (
    Path(__file__).resolve().parents[2]
    / "workbench" / "control_plane" / "src" / "lib" / "centers.ts"
)

#: Matches ``feature: "center.sales",`` — an assignment whose value is a string
#: literal, so the ``feature: string;`` line in the `Center` type is not a hit.
#: That every Center *has* the field is TypeScript's job (`npx tsc --noEmit`);
#: this only reads what each one says.
_CENTER_TS_FEATURE_RE = re.compile(r'^\s*feature:\s*"([^"]+)"\s*,?\s*$', re.MULTILINE)


def _centers_ts_features() -> list[str]:
    """Every ``feature:`` value declared in `lib/centers.ts`, in file order.

    Parsed, never retyped: a hand-copied list drifts in exactly the way the
    thing it is meant to catch drifts.
    """
    assert CENTERS_TS.is_file(), (
        f"{CENTERS_TS} is missing — the Center registry the UI reads has moved "
        "or been deleted. This is a failure, not a skip: the pairing it is "
        "pinned to is precisely the thing that breaks silently, so an "
        "unenforceable invariant must be loud. Point CENTERS_TS at the new "
        "location."
    )
    declared = _CENTER_TS_FEATURE_RE.findall(CENTERS_TS.read_text(encoding="utf-8"))
    assert declared, (
        f"No `feature: \"...\"` declarations found in {CENTERS_TS}. Either the "
        "registry is empty or its shape changed and this parser no longer "
        "reads it — both leave the vocabulary unpinned."
    )
    return declared


def test_every_center_has_a_feature_slug() -> None:
    """A Center seeded in SQL but missing from FEATURES is unreachable.

    ``allowed_features()`` iterates FEATURES, not ``feature_catalog``, and
    ``/auth/me`` returns exactly that list — so a slug absent here is invisible
    to the nav and to AccessGate for EVERY principal, including an owner
    holding ``*`` (the wildcard is only matched against these literals). That
    was the live defect: migration 140 seeded six ``center.*`` catalog rows and
    ``routes/admin/groups.py`` has been writing ``allow feature:center.<slug>``
    overrides the product could never display.

    Pinned against ``EXPECTED_CENTER_SLUGS`` first, because iterating
    ``CENTER_GROUP_SLUGS`` alone makes the check evaporate along with its
    source — dropping a slug from that tuple would silently narrow what is
    being checked instead of failing.
    """
    from gateway.routes.admin.groups import CENTER_GROUP_SLUGS

    assert CENTER_GROUP_SLUGS == EXPECTED_CENTER_SLUGS, (
        f"CENTER_GROUP_SLUGS is {CENTER_GROUP_SLUGS}, expected "
        f"{EXPECTED_CENTER_SLUGS}. Group slug = Center slug, 1:1 "
        "(department_centers.md §1). If the set of Centers really changed, "
        "update EXPECTED_CENTER_SLUGS here and every other registration point "
        "in department_centers.md §4."
    )

    missing = [
        feature_permission(f"center.{slug}")
        for slug in EXPECTED_CENTER_SLUGS
        if f"center.{slug}" not in FEATURES
    ]
    assert not missing, (
        f"Center features seeded/granted but absent from FEATURES: {missing}. "
        "They would be unreachable for every member, owners included."
    )


def test_centers_registry_matches_the_feature_vocabulary() -> None:
    """`lib/centers.ts` is the registry the UI renders — pin it to FEATURES.

    `lib/nav.ts` builds the Centers nav section from `CENTERS` and
    `lib/access.ts` builds the ``/centers/<slug>`` → ``center.<slug>`` route
    map from it, so a Center declared there whose feature is absent from
    FEATURES is dropped from the nav and 403s at AccessGate — for every
    principal, owner included. That is this branch's whole defect, and until
    this check existed the documented recipe for adding a Center reproduced it
    with a fully green suite.

    Closed in both directions: a `center.*` entry in FEATURES with no Center in
    the registry is equally wrong (a grantable feature that reaches nothing).
    """
    from gateway.routes.admin.groups import CENTER_GROUP_SLUGS

    declared = _centers_ts_features()

    malformed = [f for f in declared if not f.startswith("center.")]
    assert not malformed, (
        f"Centers declaring a non-`center.*` feature in {CENTERS_TS.name}: "
        f"{malformed}. The slug↔feature naming rule is department_centers.md §1."
    )

    unreachable = [f for f in declared if f not in FEATURES]
    assert not unreachable, (
        f"Centers in {CENTERS_TS.name} whose feature is absent from FEATURES: "
        f"{unreachable}. The nav pane is dropped and /centers/<slug> denies "
        "everyone, owners included. Add the slug to FEATURES (and to the "
        "feature_catalog migration) — department_centers.md §4."
    )

    in_features = {f for f in FEATURES if f.startswith("center.")}
    assert in_features == set(declared), (
        f"FEATURES lists {sorted(in_features)} but {CENTERS_TS.name} declares "
        f"{sorted(declared)}. A grantable `center.*` feature with no Center "
        "behind it reaches nothing."
    )

    slugs = {f.removeprefix("center.") for f in declared}
    assert slugs == set(CENTER_GROUP_SLUGS), (
        f"Center slugs {sorted(slugs)} do not match the groups "
        f"{sorted(CENTER_GROUP_SLUGS)}. Group slug = Center slug, 1:1 — a "
        "Center with no group cannot be granted as one admin action."
    )


# ── FEATURES ⟷ feature_catalog ──────────────────────────────────────────────
#
# The two Center checks above are `center.*`-only by construction: they parse
# `lib/centers.ts`, so they fence nothing for an `apps`-category slug like
# `tasks` or `crm`. This pair closes that gap by deriving the catalog's slugs
# from the migrations themselves — the `_schema_cascade.py` technique — rather
# than from a hand-kept list, and pinning them against FEATURES BOTH ways.
#
# The two directions fail for different reasons and both are silent in
# production:
#   catalog → FEATURES: `allowed_features()` iterates FEATURES, so a seeded row
#     with no tuple entry is invisible to /auth/me for EVERY principal,
#     owners included. That is the live defect migration 140 shipped.
#   FEATURES → catalog: a grantable feature with no catalog row never appears
#     in the admin access editor, which renders from the table — so nobody can
#     grant it through the product.

#: ``INSERT INTO feature_catalog (…) VALUES`` up to its ``ON CONFLICT`` tail.
#: Cut at ON CONFLICT so the `(slug)` conflict target is not read as a row.
_CATALOG_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+feature_catalog\b(.*?)(?:ON\s+CONFLICT|;)", re.I | re.S
)
#: The opening of one VALUES tuple: ``('crm',`` → ``crm``. Every row in this
#: table starts with its slug, so the first quoted literal after a ``(`` is it.
_CATALOG_SLUG_RE = re.compile(r"\(\s*'([^']+)'\s*,")

MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"


def _catalog_slugs() -> set[str]:
    """Every ``feature_catalog`` slug seeded anywhere in ``infra/postgres/``.

    Derived, never retyped. ``schema.generated.sql`` is skipped for the same
    reason ``_schema_cascade.py`` skips it: that dump is ~43 migrations stale
    and predates several of these rows, so trusting it would make this check
    agree with a snapshot instead of with the ladder that actually runs.

    The parser is small and syntactic. If a future migration writes the insert
    in a shape it cannot read, the derived set SHRINKS — and a shrunken set
    fails the FEATURES → catalog direction below rather than passing quietly.
    Failing that way round is the point.
    """
    slugs: set[str] = set()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name == "schema.generated.sql":
            continue
        sql = path.read_text(encoding="utf-8", errors="replace")
        for insert in _CATALOG_INSERT_RE.finditer(sql):
            body = insert.group(1)
            values = body.split("VALUES", 1)[-1] if "VALUES" in body.upper() else ""
            slugs.update(_CATALOG_SLUG_RE.findall(values))
    return slugs


#: Slugs allowed to exist on exactly one side. **Empty, and that is a
#: measurement**: as of 2026-08-05 the 23 catalog rows across migrations 130,
#: 132, 140 and 144 match the 23 FEATURES entries exactly. It is a commented literal
#: rather than a filter inside the assertion so that adding an exception is an
#: edit somebody has to justify in review — a silent `if slug.startswith(...)`
#: would have hidden the migration-140 defect that motivated this file's Center
#: checks in the first place.
CATALOG_DRIFT_EXCEPTIONS: frozenset[str] = frozenset()


def test_the_parser_actually_reads_the_catalog() -> None:
    """A derivation that returns nothing makes both directions below vacuous."""
    slugs = _catalog_slugs()
    assert len(slugs) >= 20, (
        f"Only {len(slugs)} feature_catalog slugs parsed out of {MIGRATIONS} — "
        "the INSERT shape has changed and the pinning below is now vacuous. "
        "Teach _CATALOG_INSERT_RE / _CATALOG_SLUG_RE the new shape."
    )
    # Anchors from three different migrations, so a parser that only reads
    # 130's multi-row insert cannot pass.
    for anchor in ("chat", "workflows", "center.sales", "crm"):
        assert anchor in slugs, f"'{anchor}' not parsed out of the migrations"


def test_every_seeded_feature_is_in_the_features_tuple() -> None:
    """Catalog → FEATURES. A row absent here is unreachable, owners included."""
    unreachable = _catalog_slugs() - set(FEATURES) - CATALOG_DRIFT_EXCEPTIONS
    assert not unreachable, (
        f"feature_catalog seeds {sorted(unreachable)} but acb_auth.FEATURES "
        "does not list them. `allowed_features()` iterates FEATURES, so "
        "/auth/me never emits these and the nav drops them for every "
        "principal — including an owner holding `*`, since the wildcard is "
        "only ever matched against those literals."
    )


def test_every_feature_has_a_catalog_row() -> None:
    """FEATURES → catalog. A grantable feature nobody can grant is not one."""
    ungrantable = set(FEATURES) - _catalog_slugs() - CATALOG_DRIFT_EXCEPTIONS
    assert not ungrantable, (
        f"acb_auth.FEATURES lists {sorted(ungrantable)} with no "
        "feature_catalog row in infra/postgres/. The admin access editor "
        "renders from that table, so an admin cannot grant these through the "
        "product. Add the row in a migration (next free number)."
    )


def test_crm_is_registered_on_both_sides() -> None:
    """WS-26a's own registration, named rather than left to the pair above.

    The generic checks pass when BOTH sides are missing a slug; this one says
    which slug this change was supposed to add.
    """
    assert "crm" in FEATURES
    assert "crm" in _catalog_slugs()


def test_projects_is_registered_on_both_sides() -> None:
    """WS-27a's registration, which was left to the generic pair and should not
    have been — for exactly the reason stated above: those pass when BOTH sides
    are missing a slug, so they cannot catch a feature nobody registered."""
    assert "projects" in FEATURES
    assert "projects" in _catalog_slugs()


def test_people_is_registered_on_both_sides() -> None:
    """WS-28b's registration (`people_center_app.md` §6, step 4, by name).

    The People Center's directory needs its OWN slug rather than riding
    `feature:tasks`: a manager who needs the org chart and the assignee picker
    should not have to be handed the personal GTD task manager to get them.
    """
    assert "people" in FEATURES
    assert "people" in _catalog_slugs()


def test_knowledge_is_registered_on_both_sides() -> None:
    """WS-61 KB-0's registration (`knowledge_base.md` §5.2), by name.

    The generic pair above passes when BOTH sides miss a slug, so this test
    names the slug that KB-0 adds.
    """
    assert "knowledge" in FEATURES
    assert "knowledge" in _catalog_slugs()


def test_knowledge_is_a_default_feature_in_its_migration() -> None:
    """§5.2 says `is_default true`. The row is found by content, not number.

    A `false` here would hide the app from a member whose role has no
    `feature:*`, and nothing else in this file reads the flag.
    """
    rows = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name == "schema.generated.sql":
            continue
        sql = path.read_text(encoding="utf-8", errors="replace")
        for insert in _CATALOG_INSERT_RE.finditer(sql):
            rows += re.findall(r"\(\s*'knowledge'\s*,[^)]*\)", insert.group(1))
    assert len(rows) == 1, rows
    assert re.search(r",\s*true\s*\)$", rows[0], re.I), rows[0]


def test_knowledge_manage_is_a_capability_that_no_feature_grants() -> None:
    """`knowledge:manage` (§5.2): the owner holds it, a feature grant does not.

    The owner holds it through `*`. Migration 207 gives `feature:*` to
    member and manager, and that grant must not reach this verb, or every
    member could change the grants of every space.
    """
    assert "knowledge:manage" in CAPABILITIES
    assert "knowledge:manage" not in FEATURES
    validate_permission("knowledge:manage")
    assert permission_matches("*", "knowledge:manage")
    assert not permission_matches("feature:*", "knowledge:manage")
    assert not permission_matches("feature:knowledge", "knowledge:manage")
    assert build_access(["*"]).has("knowledge:manage")
    assert not build_access(["feature:*"]).has("knowledge:manage")


def test_agent_service_is_not_assignable_to_people() -> None:
    assert "agent_service" in SYSTEM_ROLES
    assert "agent_service" not in ASSIGNABLE_SYSTEM_ROLES


def test_legacy_roles_map_onto_the_new_model() -> None:
    """Deploying the migration must not change anyone's access."""
    exec_access = legacy_access("executive")
    assert exec_access.can_use_feature("whatsapp")
    assert exec_access.has("admin:members:manage")

    employee = legacy_access("employee")
    assert employee.can_use_feature("chat")
    assert not employee.has("admin:members:manage")
    # The seeded `member` role deliberately withholds these.
    assert not employee.can_use_feature("whatsapp")
    assert not employee.can_use_feature("build.apps")


def test_unknown_legacy_role_falls_back_to_member() -> None:
    assert legacy_access("nonsense").roles == frozenset({"member"})


# ── UserContext ─────────────────────────────────────────────────────────────

def test_user_context_defaults_to_no_access() -> None:
    """Any construction path that skips resolution must not grant anything."""
    user = UserContext(email="a@fracktal.in", role=UserRole.EXECUTIVE)
    assert not user.has_permission("feature:chat")
    assert user.permissions == frozenset()
    assert user.roles == frozenset()


def test_with_access_preserves_identity() -> None:
    user = UserContext(email="a@fracktal.in", role=UserRole.EMPLOYEE)
    enriched = user.with_access(
        build_access(["feature:chat"], roles=["member"]),
        user_id="u-1",
        organization_id="o-1",
    )
    assert enriched.email == "a@fracktal.in"
    assert enriched.user_id == "u-1"
    assert enriched.organization_id == "o-1"
    assert enriched.can_use_feature("chat")
    # Frozen dataclass: the original is untouched.
    assert not user.can_use_feature("chat")


# ── Guards ──────────────────────────────────────────────────────────────────

def _client(access: EffectiveAccess, *, email: str | None = "a@fracktal.in") -> TestClient:
    app = FastAPI()

    async def _fake_user() -> UserContext:
        return UserContext(email=email, role=UserRole.EMPLOYEE, access=access)

    @app.get("/whatsapp", dependencies=[require_permission("feature:whatsapp")])
    async def _whatsapp() -> dict[str, bool]:
        return {"ok": True}

    @app.get(
        "/either",
        dependencies=[require_any_permission("feature:whatsapp", "admin:members:read")],
    )
    async def _either() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/run/{name}")
    async def _run(name: str, user: UserContext = Depends(_fake_user)) -> dict[str, bool]:
        assert_can_run_agent(user, name)
        return {"ok": True}

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


def test_require_permission_allows_the_holder() -> None:
    client = _client(build_access(["feature:whatsapp"]))
    assert client.get("/whatsapp").status_code == 200


def test_require_permission_403s_without_it() -> None:
    client = _client(build_access(["feature:chat"]))
    res = client.get("/whatsapp")
    assert res.status_code == 403
    # The body names what is missing so an access bug is self-diagnosing.
    assert "feature:whatsapp" in res.json()["detail"]


def test_require_permission_401s_when_anonymous() -> None:
    """401 and 403 are different problems: sign in vs. ask an admin."""
    client = _client(build_access(["feature:whatsapp"]), email=None)
    assert client.get("/whatsapp").status_code == 401


def test_require_any_permission_needs_only_one() -> None:
    client = _client(build_access(["admin:members:read"]))
    assert client.get("/either").status_code == 200
    assert client.get("/whatsapp").status_code == 403


def test_agent_run_gate() -> None:
    client = _client(
        build_access(
            ["agents:run:*"], [("agents:run:app-builder", "deny")]
        )
    )
    assert client.get("/run/email-assistant").status_code == 200
    res = client.get("/run/app-builder")
    assert res.status_code == 403
    assert "app-builder" in res.json()["detail"]


def test_service_principal_runs_any_agent() -> None:
    """Internal-token callers already hold total authority; no theatre."""
    user = UserContext(
        email="system:internal", role=UserRole.AGENT, access=SERVICE_ACCESS
    )
    assert_can_run_agent(user, "anything")  # must not raise
    assert user.has_permission(agent_run_permission("anything"))


# ── Migration 159 — one address is one person, case-insensitively ───────────
#
# WS-29/D-MT-1. `multi_tenancy.md` §1.1 rested the whole tenant model on
# `app_user.email` being unique, and it is — BYTE-EXACT, while every lookup in
# this codebase matches `lower(email)` (R10). A live run proved the gap real:
# `Casey@Alpha.Example` and `casey@alpha.example` are two rows, and under
# D-MT-1 they can sit in two organizations, which makes a person's tenant
# whichever row the planner returns.

_MIGRATION_159 = Path("infra/postgres/162_app_user_email_case.sql")


def test_the_unique_index_is_on_lower_email_not_the_raw_column() -> None:
    """⚠️ The claim the tenant model rests on. A `UNIQUE (email)` here agrees
    with itself and disagrees with every query in the codebase."""
    sql = _MIGRATION_159.read_text(encoding="utf-8")
    assert "CREATE UNIQUE INDEX IF NOT EXISTS app_user_email_lower_key" in sql
    assert "ON app_user (lower(email))" in sql


def test_the_byte_exact_constraint_is_retired_not_left_beside_it() -> None:
    """Two unique indexes on one column state two different things about the
    same fact, and the weaker one is what somebody later 'fixes' a violation
    against."""
    sql = _MIGRATION_159.read_text(encoding="utf-8")
    assert "DROP CONSTRAINT IF EXISTS app_user_email_key" in sql
    # Order is load-bearing: the replacement must exist before the original is
    # dropped, or there is a window with no uniqueness at all.
    assert sql.index("CREATE UNIQUE INDEX") < sql.index("DROP CONSTRAINT")


def test_the_migration_is_idempotent_like_every_other() -> None:
    sql = _MIGRATION_159.read_text(encoding="utf-8")
    assert "IF NOT EXISTS" in sql
    assert "IF EXISTS" in sql


def test_it_does_not_rewrite_anybody_s_stored_address() -> None:
    """⚠️ `created_by`, `assignee`, `subject` and `updated_by` are bare address
    strings across a dozen tables (D-PM-4) and none of them are foreign keys, so
    a normalising UPDATE here would silently orphan them."""
    sql = _MIGRATION_159.read_text(encoding="utf-8")
    assert "UPDATE app_user" not in sql
    assert "SET email" not in sql


def test_it_is_not_CONCURRENTLY_which_cannot_run_in_a_transaction() -> None:
    """`CREATE INDEX CONCURRENTLY` inside `BEGIN` is an error, and this file is
    one transaction on purpose.

    Comments are stripped before the check: the migration *explains* why it is
    not concurrent, and a structural test that trips on the prose justifying
    the very rule it enforces is a test somebody deletes. (The same trap caught
    `lib/timeline.ts` earlier in this session, for the same reason.)
    """
    statements = "\n".join(
        line for line in _MIGRATION_159.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    )
    assert "CONCURRENTLY" not in statements
    assert statements.count("BEGIN;") == 1 and statements.count("COMMIT;") == 1
# ── Tenant predicate on the org_group joins (MT-1i) ─────────────────────────
#
# saas_multitenancy.md §6.4/§6.5 + tenancy_and_visibility.md §2. Decision D15
# made the tenant boundary a ROW, so `org_group`'s slug — unique only per
# `UNIQUE (organization_id, slug)` (138_groups_and_session_participants.sql:49)
# — is a cross-organization match whenever it is joined on alone, and the two
# org-wide expansions serve every user on the box.
#
# These assertions are on the SQL *strings* deliberately: tenancy_and_visibility.md
# §2 done-when 2 notes the DB-backed tests for this area open with
# `pytest.mark.skipif(not _db_ready(), …)` and so skip green with no Postgres,
# which makes "verified red" unsatisfiable there. This file carries no such
# guard, so these cannot skip.

def _squash(sql: str) -> str:
    """Whitespace-insensitive view of a query, so formatting is not the test."""
    return re.sub(r"\s+", " ", sql)


def test_my_groups_query_is_a_module_constant() -> None:
    """done-when 2: anchor (a) must be reachable from a hermetic test.

    It lived inline inside `_load_room`, where no string assertion could see it.
    """
    from gateway import rooms

    assert isinstance(rooms.MY_GROUPS_SQL, str)
    assert "org_group" in rooms.MY_GROUPS_SQL


def _org_group_joins() -> list[tuple[str, str]]:
    """The three queries that join `org_group`, by name, for the assertion below."""
    from acb_auth.access import _GROUP_MEMBER_SQL
    from gateway.rooms import MY_GROUPS_SQL, SESSION_VISIBLE_SQL

    return [
        ("gateway.rooms.MY_GROUPS_SQL", MY_GROUPS_SQL),
        ("gateway.rooms.SESSION_VISIBLE_SQL", SESSION_VISIBLE_SQL),
        ("acb_auth.access._GROUP_MEMBER_SQL", _GROUP_MEMBER_SQL),
    ]


@pytest.mark.parametrize(
    ("label", "sql"),
    _org_group_joins(),
    ids=[label for label, _ in _org_group_joins()],
)
def test_org_group_joins_carry_a_derived_org_predicate(
    label: str, sql: str,
) -> None:
    """done-when 1: the group's org must be tied to the acting user's org.

    A slug-only join matches the identically-slugged group in every other
    tenant. The predicate must be *derived* from the row being authorised —
    a literal `slug = 'default'` swaps one wrong constant for another.
    """
    sql = _squash(sql)
    assert "org_group" in sql, label
    assert "g.organization_id =" in sql, (
        f"{label} joins org_group without tying g.organization_id to the "
        f"acting user's organization: {sql}"
    )
    assert "'default'" not in sql, (
        f"{label} hardcodes an org slug instead of deriving one: {sql}"
    )


def test_org_subject_does_not_expand_to_every_user_on_the_box() -> None:
    """§6.4: `_ORG_MEMBER_SQL` had no org filter at all."""
    from acb_auth import access

    sql = _squash(access._ORG_MEMBER_SQL)
    assert "organization_id" in sql, (
        f"the `org` subject expands to every active user on the box: {sql}"
    )


def test_owner_bootstrap_guard_is_per_organization() -> None:
    """§6.4 site 9: a lockout, not a leak — RLS does not fix it.

    Unfiltered, one owner anywhere makes `ensure_owner_bootstrap()` a permanent
    no-op, so an ownerless organization stays ownerless with no inviter.
    """
    from acb_auth import access

    sql = _squash(access._HAS_OWNER_SQL)
    assert "organization_id" in sql, (
        f"_HAS_OWNER_SQL sees owners in every organization: {sql}"
    )
