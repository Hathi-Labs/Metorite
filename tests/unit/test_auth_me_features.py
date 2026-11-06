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

from types import SimpleNamespace

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


# ── WS-PKG PKG-7: the slugs that an install declares ────────────────────────
#
# `package_host.md` §10, the PKG-7 row. The R8 twin is
# `test_auth_me_install_features_r8.py`.

#: A frozen copy of `FEATURES`. PKG-7 adds a third source of slugs, and the
#: code list must not grow with it. A change to `FEATURES` is a change of
#: its own, and it changes this copy in the same PR.
FROZEN_FEATURES = (
    "chat", "email", "whatsapp", "memory", "tasks", "notes", "crm", "projects",
    "people", "knowledge", "dashboard", "observability", "artifacts", "models",
    "agents", "approvals", "integrations", "workflows", "build.agents",
    "build.apps", "center.sales", "center.marketing", "center.finance",
    "center.operations", "center.people", "center.company",
)
GUEST = build_access(["feature:chat"])


def _body(*entries: dict) -> dict:
    """A resolve answer with *entries* at the point `jobs`."""
    return {"version": "0" * 16, "contributions": {"jobs": list(entries)}}


def _claim(feature, key="k", source="org"):
    return {"key": key, "source": source, "install_id": "i-1",
            "needs_setup": False, "feature": feature}


def test_pkg7_features_equals_a_frozen_copy():
    assert FEATURES == FROZEN_FEATURES


def test_pkg7_the_helper_gives_each_slug_once_in_order():
    from gateway.package_host.resolve import install_feature_slugs

    body = {"version": "0" * 16, "contributions": {
        "jobs": [_claim("fieldvisits"), _claim("zeta", key="z"),
                 _claim("fieldvisits", key="again")],
        "nav": [_claim("alpha", key="/x"), _claim("zeta", key="/y")],
    }}
    assert install_feature_slugs(body) == ["fieldvisits", "zeta", "alpha"]


def test_pkg7_the_helper_skips_a_system_entry_and_a_claim_with_no_feature():
    from gateway.package_host.resolve import install_feature_slugs

    system = {"key": "chat", "source": "system", "install_id": None,
              "needs_setup": False, "feature": "sysonly", "package": "p"}
    open_claim = {"key": "o", "source": "org", "install_id": "i-2", "needs_setup": False}
    assert install_feature_slugs(_body(system, open_claim, _claim("ok"))) == ["ok"]


@pytest.mark.parametrize("bad", [
    None, 7, ["x"], {"a": 1}, "", "   ", "Field", "field visits", "*", "a:b",
    "feature:x", "-lead", "x" * 200, "ä", "a/b", "a*",
])
def test_pkg7_a_bad_slug_is_dropped_by_the_helper(bad):
    from gateway.package_host.resolve import install_feature_slugs

    assert install_feature_slugs(_body(_claim(bad), _claim("ok", key="k2"))) == ["ok"]


@pytest.mark.parametrize("body", [None, {}, {"contributions": None},
                                  {"contributions": {"jobs": None}},
                                  {"contributions": {"jobs": ["x", 3]}}])
def test_pkg7_a_malformed_answer_gives_no_slug(body):
    from gateway.package_host.resolve import install_feature_slugs

    assert install_feature_slugs(body) == []


def test_pkg7_an_installed_slug_follows_the_access_set():
    """`*` and `feature:*` see it, a guest is told what it needs."""
    for who in (OWNER, ADMIN):
        assert "fieldvisits" in resolve_features(who, [], ["fieldvisits"])["features"]
    got = resolve_features(GUEST, [], ["fieldvisits"])
    assert "fieldvisits" not in got["features"]
    assert {"slug": "fieldvisits", "permission": "feature:fieldvisits"} in got["features_denied"]


def test_pkg7_an_installed_system_slug_shows_once():
    got = resolve_features(OWNER, ["crm"], ["crm"])["features"]
    assert got.count("crm") == 1


def test_pkg7_no_installed_slug_is_the_old_answer():
    assert resolve_features(MEMBER, ["projects"], []) == resolve_features(MEMBER, ["projects"])
    assert resolve_features(MEMBER, ["projects"], None) == resolve_features(MEMBER, ["projects"])


# ── PKG-7: the route, with the flag on and off ──────────────────────────────


class _Rows:
    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _Savepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeDB:
    def __init__(self):
        self.statements = []

    async def execute(self, stmt, params=None):
        self.statements.append(str(stmt))
        return _Rows({"slug": "acme", "display_name": "Acme",
                      "registry_status": None, "registry_trial_ends_at": None})

    def begin_nested(self):
        return _Savepoint()


@pytest.fixture
def route(monkeypatch):
    """`get_me` with a fake session, a fixed org and a fixed catalog."""
    from contextlib import asynccontextmanager

    from acb_common import get_settings
    from gateway.package_host import resolve as resolve_mod
    from gateway.routes.admin import me

    db = _FakeDB()
    calls = []

    @asynccontextmanager
    async def _session(organization_id=None):
        yield db

    async def _org(_db, _user):
        return ORG_ID

    async def _catalog(_db):
        return ["chat"]

    async def _resolve_for(_db, *, viewer, can_use):
        calls.append(viewer)
        return _body(_claim("fieldvisits"))

    monkeypatch.setattr(me, "_tenant_session", _session)
    monkeypatch.setattr(me, "get_org_id", _org)
    monkeypatch.setattr(me, "_catalog_slugs", _catalog)
    monkeypatch.setattr(me, "_agent_names", lambda: [])
    monkeypatch.setattr(resolve_mod, "resolve_for", _resolve_for)
    monkeypatch.setattr(get_settings(), "package_host_orgs", "")
    return SimpleNamespace(me=me, db=db, calls=calls, settings=get_settings(),
                           resolve=resolve_mod, monkeypatch=monkeypatch)


ORG_ID = "00000000-0000-0000-0000-00000000a001"


def _user(access=OWNER):
    from acb_auth import UserContext, UserRole

    return UserContext(email="ana@acme.test", role=UserRole.EMPLOYEE, access=access,
                       organization_id=ORG_ID)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_pkg7_flag_off_never_calls_the_resolve(route):
    body = _run(route.me.get_me(user=_user()))
    assert route.calls == []
    assert "fieldvisits" not in body["features"]
    assert not any("package_install" in s for s in route.db.statements)


def test_pkg7_flag_on_adds_the_slug_for_the_session_member(route):
    route.monkeypatch.setattr(route.settings, "package_host_orgs", ORG_ID)
    body = _run(route.me.get_me(user=_user()))
    assert route.calls == ["ana@acme.test"]
    assert "fieldvisits" in body["features"]


def test_pkg7_a_failed_resolve_still_answers_the_system_lists(route):
    async def _boom(_db, *, viewer, can_use):
        raise RuntimeError("relation does not exist")

    route.monkeypatch.setattr(route.settings, "package_host_orgs", "*")
    route.monkeypatch.setattr(route.resolve, "resolve_for", _boom)
    body = _run(route.me.get_me(user=_user()))
    off = resolve_features(OWNER, ["chat"])
    assert body["features"] == off["features"]
    assert body["features_denied"] == off["features_denied"]
    assert body["organization"]["slug"] == "acme"


def test_pkg7_the_resolve_keeps_each_claim_for_the_split():
    """The resolve gets a `can_use` that keeps each claim. The access set
    splits the slugs after, so a guest can see the slug in the denied list."""
    from gateway.routes.admin.me import _every_feature

    assert _every_feature("anything") is True


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_pkg7_the_r8_suite_is_named_in_the_must_run_list(workflow):
    """A skip of the R8 twin must fail CI, so both lists name it."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    text = (root / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", text)
    assert block, "the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_auth_me_install_features_r8\.py(?: \\)?$",
                     block.group(1), re.M)


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
