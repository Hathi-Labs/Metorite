"""WS-29 MT-1j slices 1+2+3 — provisioning a tenant-plane organization.

Spec: ``project-docs/specs/saas_multitenancy.md`` §11 MT-1j (slices 1, 2, 3) ·
Decision **D43-A** (one SQL function, not a role-template table) · D43-C (this
builds and R8-tests freely; EXECUTING it against a real second organization is
the §6 owner gate, because H3's RLS promotion has not happened).

**Why this file exists.** Four specs and one migration each disclaimed per-org
role seeding to a fifth ticket that did not exist, so a second organization was
born with *no roles, no owner and no placement* — discoverable only at the
checkout button. ``178_billing_purchase_permission.sql:39-48`` states the defect
in-code and names its owner as "the org-provisioning ticket that parameterises
role seeding". This is that ticket's fence.

Three subjects, one suite, because they are one act:

* **slice 1** — ``provision_org_roles(org_id)`` replays 130/131/133/178's grants
  as *data* for any organization;
* **slice 2** — an owner path that takes an **explicit** organization, so
  ``_BOOTSTRAP_ORG_SLUG`` stops being the only one. ⚠️ **D36.3: the constant is
  NOT re-pointed** — ``default`` stays the fresh-box first-run path and a
  customer organization never routes through it. That is asserted here, in the
  negative, so a later agent cannot "fix" org #2 by aiming the literal at it;
* **slice 3** — ``organization`` + ``tenant_placement`` as **one idempotent
  act**, keyed on the slug. An organization without a placement is a tenant
  whose data plane is unresolvable and ``placement.resolve_placement`` refuses
  rather than guessing.

⚠️ **R8, and a hermetic version of this file would be worse than nothing.**
Every subject here is a plpgsql function, a conflict target, a CHECK constraint
or a set-difference. A fake agrees with whatever SQL it is handed — which is
precisely how slice 6's ``ON CONFLICT (email)`` shipped green and took out the
owner bootstrap entirely. ``saas_multitenancy_implementation.md`` §8's traps are
this exact class.

The gate is ``TENANT_LADDER_DATABASE_URL`` — **never** ``DATABASE_URL``, for the
reason ``test_billing_purchase_capability.py`` records at length (that variable
arms ``test_tenant_coverage.py``'s two DB-gated tests, which fail by
construction against a freshly-replayed ladder). It reads
``tests/conftest.py``'s launch snapshot, because ``import litellm`` calls
``load_dotenv()`` mid-collection and a dev machine's ``.env`` must not be able
to aim an R8 gate at a local database.

Run::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant
    uv run pytest tests/unit/test_org_provisioning.py -v -rs
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

#: The constant is IMPORTED, never transcribed — D36.3's assertion is about the
#: shipped value, and a copy here would keep passing after somebody moved it.
from acb_auth.access import _BOOTSTRAP_ORG_SLUG
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder, ladder, tenant_engine_scope

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector (infra/postgres/01_schema.sql needs uuid-ossp AND vector). "
        "A skip here is not a pass; CI must set it. ⚠️ Do NOT export it as "
        "DATABASE_URL — see this module's docstring."
    ),
)

_ROOT = Path(__file__).resolve().parents[2]
_LADDER_DIR = _ROOT / "infra" / "postgres"

#: The six rows migration 130 seeds — the five assignable roles **plus** the
#: service principal. The fence below asserts this SET rather than a count,
#: which is MT-1j's own warning: its name says "five system roles" and the
#: table has always carried six.
SYSTEM_ROLES = frozenset(
    {"owner", "admin", "manager", "member", "guest", "agent_service"}
)

#: The three callables migration 179 introduces. Named once here so every
#: structural test below asks the same question of the same set.
PROVISIONING_FUNCTIONS = (
    "provision_org_roles",
    "provision_org_owner",
    "provision_organization",
)


def _numbered_migrations() -> list[Path]:
    return [
        path
        for path in sorted(_LADDER_DIR.glob("*.sql"))
        if re.match(r"^\d+_", path.name)
    ]


#: The SEEDING-doctrine callable carries D43-A's *one* seeding doctrine — exactly
#: one file may define it, so a competing copy fails rather than silently winning
#: by sort order. The other two are EXEMPT because each is redefined forward-only
#: in a LATER migration (R6, last-body-wins replay, not a scattered doctrine):
#: ``provision_org_owner`` in 180 (slice 7's create-only guard) and
#: ``provision_organization`` in 185 (WS-29 H6's app.tenant_id RLS bind). Only the
#: grant-set seed carries the single-definition invariant.
SINGLY_DEFINED_FUNCTIONS = ("provision_org_roles",)


def _defining_migrations(function_name: str) -> list[Path]:
    """Every migration defining *function_name* BY CONTENT, low→high by number.

    R1: migration numbers are taken at build time and re-checked at merge, so
    this scans CONTENT and never a hard-coded number. R6: a plpgsql function may
    be REDEFINED forward-only in a LATER migration — slice 7
    (``saas_multitenancy.md`` §11) redefines ``provision_org_owner`` in 180 to
    add the create-only guard, leaving 179 byte-for-byte untouched. The deploy
    replays the ladder low→high before restarting services, so the AUTHORITATIVE
    body is the highest-numbered one.
    """
    pattern = re.compile(
        rf"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+{function_name}\s*\(", re.IGNORECASE
    )
    matches = [
        path
        for path in _numbered_migrations()
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert matches, (
        f"no tenant migration defines {function_name}() — a callable the ladder "
        "never creates cannot be provisioned"
    )
    matches.sort(key=lambda p: int(p.name.split("_", 1)[0]))
    return matches


def _defining_migration(function_name: str) -> Path:
    """The AUTHORITATIVE (highest-numbered) migration defining *function_name*.

    Under R6 forward-only redefinition the last-replayed body wins, so this is
    the definition whose behaviour the R8 tests below actually observe.
    """
    return _defining_migrations(function_name)[-1]


# ── The structural half: no database, and it must never skip ────────────────

#: ⚠️ **Whitespace-proof, and that is not cosmetic.** ``slug='default'`` and
#: ``slug = 'default'`` are the same predicate to Postgres; the spaced literal
#: is what MT-1j *measured* with, not what may fence it. Two unspaced hits
#: already exist (161's comments) and a literal search would miss both.
_DEFAULT_SLUG = re.compile(r"slug\s*=\s*'default'", re.IGNORECASE)

#: Baseline measured 2026-08-19 on this branch's parent, with the regex above:
#: **31 lines across 8 ladder files**. MT-1j's spec quotes **29** for the
#: *spaced* command it measured with (``grep -rn "slug = 'default'"``); the two
#: extra are 161's unspaced comment hits, which is exactly why the fence uses
#: the wider regex and the wider number.
#:
#: ⚠️ **Scoped to the LADDER.** ``infra/postgres/generated/02_backfill.sql``
#: carries ~140 more by construction — one per scoped table — and is
#: regenerated rather than edited, so counting it would make this ratchet
#: measure the generator instead of the doctrine.
DEFAULT_SLUG_BASELINE = 31


def _default_slug_hits() -> dict[str, list[int]]:
    """Every ladder line whose predicate pins the ``default`` organization."""
    found: dict[str, list[int]] = {}
    for path in _numbered_migrations():
        lines = [
            n
            for n, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            )
            if _DEFAULT_SLUG.search(line)
        ]
        if lines:
            found[path.name] = lines
    return found


class TestTheDefaultSlugRatchet:
    """Seeding scoped to one hard-coded organization only goes DOWN.

    The same bank-your-progress discipline as ``_SYNC_ENGINE_ALLOWED`` and
    ``test_db_engine_seam.py``'s H2 counts. MT-1j does not *retire*
    130/131/133/178 — existing migrations are history and R6 is expand-only —
    so the baseline does not drop on this branch. What it must never do is
    **grow**: a fifth ``WHERE slug = 'default'`` seed would be the second
    seeding doctrine 178's own comment refuses, written past the callable that
    exists to replace it.
    """

    def test_the_ladder_carries_no_new_default_scoped_predicate(self):
        hits = _default_slug_hits()
        total = sum(len(v) for v in hits.values())
        assert total <= DEFAULT_SLUG_BASELINE, (
            f"{total} ladder lines pin the `default` organization, baseline "
            f"{DEFAULT_SLUG_BASELINE}. A new one is a per-org seed written as "
            "a single-org seed — call provision_org_roles(org_id) instead "
            f"(saas_multitenancy.md §11 MT-1j, D43-A). Sites: {hits}"
        )

    def test_the_provisioning_migration_pins_no_organization_at_all(self):
        """The callable's whole value is that it names no organization.

        A ``slug = 'default'`` anywhere in EITHER the introducing migration or a
        forward-only redefinition (slice 7's 180) would mean the extraction
        copied the defect it was extracted from.
        """
        for name in PROVISIONING_FUNCTIONS:
            for path in _defining_migrations(name):
                offending = [
                    n
                    for n, line in enumerate(
                        path.read_text(encoding="utf-8").splitlines(), start=1
                    )
                    if _DEFAULT_SLUG.search(line)
                ]
                assert not offending, (
                    f"{path.name} pins the `default` organization at lines "
                    f"{offending} — the provisioning callable is parameterised "
                    "on organization_id by construction"
                )

    def test_the_baseline_is_not_stale(self):
        """A ratchet whose baseline drifted far above reality stops ratcheting.

        If somebody legitimately retires one of the four seeds, this fails and
        the baseline comes DOWN in the same change — which is the mechanism,
        not an inconvenience.
        """
        total = sum(len(v) for v in _default_slug_hits().values())
        assert total == DEFAULT_SLUG_BASELINE, (
            f"the ladder now carries {total} default-scoped predicates, not "
            f"{DEFAULT_SLUG_BASELINE}. If that is a retirement, lower the "
            "baseline here in the same commit."
        )


class TestTheMigrationIsOnTheLadder:
    def test_every_definition_is_replayable_on_the_ladder(self):
        """A file the ladder does not pick up is a function that never exists.

        Every definition — the introducing 179 AND slice 7's forward-only 180
        redefinition of ``provision_org_owner`` — must be a migration
        ``ladder()`` replays, or the last-body-wins guarantee is empty.

        ⚠️ Compared through ``os.path.normcase`` for the reason
        ``test_billing_purchase_capability.py`` records: this side resolves the
        drive letter to ``C:`` and ``ladder()`` inherits whatever case the
        launching shell used (``c:`` from Git Bash) — green from PowerShell,
        red from bash, for a file on the ladder either way. It is still a whole
        **path** comparison, and a no-op on POSIX and in CI.
        """
        found = {os.path.normcase(str(Path(p))) for p in ladder()}
        for name in PROVISIONING_FUNCTIONS:
            for path in _defining_migrations(name):
                assert os.path.normcase(str(path)) in found

    def test_the_seeding_callables_are_defined_exactly_once(self):
        """D43-A: ONE seeding doctrine, so a competing copy fails here.

        Two callables are EXEMPT because each is redefined forward-only in a
        later migration (R6, last-body-wins replay, not a scattered doctrine):
        ``provision_org_owner`` in 180 (slice 7's create-only guard) and
        ``provision_organization`` in 185 (WS-29 H6's app.tenant_id RLS bind).
        Only the grant-set seed carries the single-definition invariant.
        """
        for name in SINGLY_DEFINED_FUNCTIONS:
            homes = _defining_migrations(name)
            assert len(homes) == 1, (
                f"{name}() is defined in {[p.name for p in homes]} — D43-A's "
                "whole point is ONE statement of the seeding doctrine"
            )

    def test_all_three_callables_are_introduced_in_one_migration(self):
        """They are one act; their INTRODUCTION is one file.

        A later forward-only redefinition (slice 7's 180) is permitted — it
        redefines an EXISTING callable, it does not scatter the introduction —
        so the invariant is on the LOWEST-numbered definition of each.
        """
        homes = {_defining_migrations(n)[0].name for n in PROVISIONING_FUNCTIONS}
        assert len(homes) == 1, f"the provisioning callables are scattered: {homes}"

    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        """The hand-list discovers NOTHING (pr-check.yml's own warning).

        An R8 suite absent from it still runs in the directory step, still
        skips silently there without a database, and still leaves the job
        green — the exact failure that step exists to catch.
        """
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_org_provisioning.py" in workflow

    def test_the_owning_spec_names_this_suite(self):
        spec = (_ROOT / "project-docs/specs/saas_multitenancy.md").read_text(
            encoding="utf-8")
        assert "tests/unit/test_org_provisioning.py" in spec


class TestD363TheBootstrapConstantIsNotRepointed:
    """slice 2's *negative* half, and it needs no database.

    D36.3 is explicit: ``default`` stays the fresh-box first-run path and
    *"provisioning a customer organization never routes through that
    constant"*. The cheap wrong fix — make ``_BOOTSTRAP_ORG_SLUG``
    configurable and aim it at the customer — is the first-party-bypass shape
    D36.2 forbids, and it would look like a feature in a diff.
    """

    def test_the_constant_still_names_the_default_organization(self):
        assert _BOOTSTRAP_ORG_SLUG == "default", (
            "_BOOTSTRAP_ORG_SLUG was re-pointed. D36.3: the fresh-box bootstrap "
            "org is pinned; a provisioned organization names its own owner "
            "through provision_organization(), which is what slice 2 added."
        )

    def test_the_constant_is_not_read_from_the_environment(self):
        """Configurable-by-env is the same re-point wearing a deploy hat."""
        body = (
            _ROOT / "packages/acb_auth/acb_auth/access.py"
        ).read_text(encoding="utf-8")
        assignment = re.search(r"^_BOOTSTRAP_ORG_SLUG\s*=.*$", body, re.MULTILINE)
        assert assignment is not None
        assert "environ" not in assignment.group(0)
        assert "getenv" not in assignment.group(0)


# ── The R8 half: the callables, against the replayed ladder ─────────────────

@pytest.fixture(scope="module")
def replayed():
    """Build the tenant schema from ``infra/postgres/``, then replay it.

    Applied twice for the reason ``test_billing_purchase_capability.py`` does
    it: the deploy replays the whole ladder every time, and ``CREATE OR
    REPLACE FUNCTION`` has to survive that — a definition that only works on a
    virgin database is a deploy failure, not a test detail.
    """
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def conn(replayed):
    """One rolled-back transaction per test — writes never outlive a test.

    The ladder database is shared and module-scoped; the rows are not. Rolling
    back rather than deleting means a test that fails mid-way still leaves
    nothing behind for the next one to trip over — and it is also what makes
    the atomicity assertion below honest, since the function under test is
    supposed to be atomic *within* the caller's transaction.
    """
    with replayed.connect() as connection:
        trans = connection.begin()
        try:
            yield connection
        finally:
            trans.rollback()


def _default_org(conn) -> str:
    return str(
        conn.execute(
            text("SELECT id FROM organization WHERE slug = 'default'")
        ).scalar_one()
    )


def _provision(conn, slug: str, name: str, owner: str | None = None) -> str:
    return str(
        conn.execute(
            text(
                "SELECT provision_organization(:slug, :name, :owner)"
            ),
            {"slug": slug, "name": name, "owner": owner},
        ).scalar_one()
    )


def _grants(conn, org_id: str) -> dict[str, set[str]]:
    """``{role_slug: {permission, …}}`` for one organization."""
    out: dict[str, set[str]] = {}
    for slug, permission in conn.execute(
        text(
            "SELECT r.slug, p.permission FROM org_role r "
            "  LEFT JOIN org_role_permission p ON p.role_id = r.id "
            " WHERE r.organization_id = CAST(:org AS uuid)"
        ),
        {"org": org_id},
    ):
        out.setdefault(slug, set())
        if permission is not None:
            out[slug].add(permission)
    return out


def _owner_emails(conn, org_id: str) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            text(
                "SELECT u.email FROM user_role ur "
                "  JOIN app_user u ON u.id = ur.user_id "
                "  JOIN org_role r ON r.id = ur.role_id AND r.slug = 'owner' "
                " WHERE r.organization_id = CAST(:org AS uuid)"
            ),
            {"org": org_id},
        )
    }


# ── Slice 1 · the parameterised role seed ───────────────────────────────────

@_DB_GATE
class TestSliceOneTheParameterisedRoleSeed:
    def test_a_second_organization_gets_the_system_roles_as_a_SET(self, conn):
        """MT-1j's own warning, honoured: assert the SET, not a count.

        130 seeds **six** rows — the five assignable roles plus
        ``agent_service`` — so ``len(...) == 5`` passes on the wrong six and
        fails on the right ones.
        """
        org = str(
            conn.execute(
                text(
                    "INSERT INTO organization (slug, display_name) "
                    "VALUES ('mt1j-roles-only', 'Roles Only') RETURNING id"
                )
            ).scalar_one()
        )
        conn.execute(text("SELECT provision_org_roles(CAST(:o AS uuid))"), {"o": org})
        assert set(_grants(conn, org)) == SYSTEM_ROLES

    def test_org_twos_admin_holds_billing_purchase(self, conn):
        """The capability 178 could only seed into ``default``.

        ``subscription_console.md`` records it as born UNHELD in every other
        organization; that is the sentence this assertion retires.
        """
        org = _provision(conn, "mt1j-billing", "Billing Org")
        assert "billing:purchase" in _grants(conn, org)["admin"]

    def test_org_twos_owner_holds_the_wildcard(self, conn):
        org = _provision(conn, "mt1j-wildcard", "Wildcard Org")
        assert _grants(conn, org)["owner"] == {"*"}

    def test_the_callable_reproduces_the_default_orgs_grant_set_exactly(self, conn):
        """The regression the extraction can actually cause (slice 1's done-when).

        MT-1j does not re-point ``default``'s historical seed — existing
        migrations are history under R6 — so "byte-identical before and after"
        is asserted the stronger way available: the callable's output for a
        fresh organization must equal, role for role and permission for
        permission, what **130 + 131 + 133 + 178** actually produced for
        ``default`` on this same replayed ladder. Drift in *either* statement
        fails here, which a text diff of the migration could not see.
        """
        fresh = _provision(conn, "mt1j-parity", "Parity Org")
        assert _grants(conn, fresh) == _grants(conn, _default_org(conn))

    def test_seeding_is_idempotent_and_grants_nothing_twice(self, conn):
        """The deploy replays the whole ladder, and a retried signup re-calls
        this. A duplicate row would be a second grant nobody issued."""
        org = _provision(conn, "mt1j-idem", "Idempotent Org")
        before = _grants(conn, org)
        for _ in range(2):
            conn.execute(
                text("SELECT provision_org_roles(CAST(:o AS uuid))"), {"o": org}
            )
        assert _grants(conn, org) == before
        rows = conn.execute(
            text(
                "SELECT count(*) FROM org_role WHERE organization_id = CAST(:o AS uuid)"
            ),
            {"o": org},
        ).scalar_one()
        assert rows == len(SYSTEM_ROLES)

    def test_seeding_org_two_leaves_the_default_orgs_grants_untouched(self, conn):
        default = _default_org(conn)
        before = _grants(conn, default)
        _provision(conn, "mt1j-noleak", "No Leak Org")
        assert _grants(conn, default) == before

    def test_the_roles_are_marked_system_and_ranked_like_130s(self, conn):
        """``is_system`` is what makes them non-deletable from the admin UI —
        130 calls them "the floor the bootstrap path depends on" — and ``rank``
        is what stops an admin assigning a role above their own. A seed that
        got the grants right and these wrong is a silently different org."""
        org = _provision(conn, "mt1j-ranks", "Ranks Org")
        rows = dict(
            conn.execute(
                text(
                    "SELECT r.slug, r.rank FROM org_role r "
                    " WHERE r.organization_id = CAST(:o AS uuid) AND r.is_system"
                ),
                {"o": org},
            ).all()
        )
        default_rows = dict(
            conn.execute(
                text(
                    "SELECT r.slug, r.rank FROM org_role r "
                    " WHERE r.organization_id = CAST(:o AS uuid) AND r.is_system"
                ),
                {"o": _default_org(conn)},
            ).all()
        )
        assert rows == default_rows

    def test_the_callable_refuses_an_organization_that_does_not_exist(self, conn):
        """Fail closed. Seeding roles against a dangling id would create rows
        the FK then rejects — or, worse, succeed against a typo."""
        from sqlalchemy.exc import DBAPIError

        with pytest.raises(DBAPIError), conn.begin_nested():
            conn.execute(
                text("SELECT provision_org_roles(CAST(:o AS uuid))"),
                {"o": "11111111-1111-1111-1111-111111111111"},
            )


# ── Slice 2 · an owner path that is not _BOOTSTRAP_ORG_SLUG ─────────────────

@_DB_GATE
class TestSliceTwoTheExplicitOwnerPath:
    def test_a_named_owner_holds_owner_in_the_provisioned_organization(self, conn):
        org = _provision(conn, "mt1j-owned", "Owned Org", "chief@second.example")
        assert _owner_emails(conn, org) == {"chief@second.example"}

    def test_the_default_organizations_owner_set_is_untouched(self, conn):
        """Asserted against a ``default`` that HAS an owner, not an empty one.

        On a freshly replayed ladder ``app_user`` is empty, so ``default``'s
        owner set is ``set()`` and "unchanged" would be trivially true whatever
        the function did. Seeding a real owner first is what makes the
        assertion cost something.
        """
        default = _default_org(conn)
        conn.execute(
            text("SELECT provision_org_owner(CAST(:o AS uuid), :e)"),
            {"o": default, "e": "boss@fracktal.in"},
        )
        before = _owner_emails(conn, default)
        assert before == {"boss@fracktal.in"}

        _provision(conn, "mt1j-elsewhere", "Elsewhere", "chief@second.example")

        assert _owner_emails(conn, default) == before

    def test_the_owner_row_lands_in_the_right_tenant(self, conn):
        """The grant is only half of it — an owner row carrying another
        tenant's ``organization_id`` is S1-1's write leak with a role attached."""
        org = _provision(conn, "mt1j-tenanted", "Tenanted", "chief@second.example")
        row = conn.execute(
            text(
                "SELECT organization_id::text, status FROM app_user "
                " WHERE lower(email) = 'chief@second.example'"
            )
        ).one()
        assert row[0] == org
        assert row[1] == "active"

    def test_a_differently_cased_owner_address_lands_on_one_row(self, conn):
        """Migration 162's whole point, and slice 6's repair, on the new path.

        ``app_user`` is unique on ``lower(email)``; an upsert naming the bare
        column raises 42P10 at PLAN time. This is the R8 fence for that on the
        provisioning callable — stronger than a grep, because it asks the live
        catalog.
        """
        org = _provision(conn, "mt1j-cased", "Cased", "Casey@Alpha.Example")
        conn.execute(
            text("SELECT provision_org_owner(CAST(:o AS uuid), :e)"),
            {"o": org, "e": "casey@alpha.example"},
        )
        rows = conn.execute(
            text(
                "SELECT email FROM app_user WHERE lower(email) = 'casey@alpha.example'"
            )
        ).all()
        assert len(rows) == 1, f"one address, {len(rows)} rows: {rows}"
        assert rows[0][0] == "Casey@Alpha.Example", (
            "the stored spelling was rewritten — 162 deliberately does not "
            "normalise, because audit rows name the address as stored"
        )

    def test_re_provisioning_grants_the_owner_role_exactly_once(self, conn):
        org = _provision(conn, "mt1j-reowned", "Reowned", "chief@second.example")
        _provision(conn, "mt1j-reowned", "Reowned", "chief@second.example")
        held = conn.execute(
            text(
                "SELECT count(*) FROM user_role ur "
                "  JOIN org_role r ON r.id = ur.role_id AND r.slug = 'owner' "
                " WHERE r.organization_id = CAST(:o AS uuid)"
            ),
            {"o": org},
        ).scalar_one()
        assert held == 1

    def test_an_address_already_in_another_tenant_is_refused(self, conn):
        """Fail closed, and the refusal is the point.

        ``app_user`` is unique on ``lower(email)`` **globally**, so one address
        cannot be a member of two organizations. Adopting it silently would
        move a person between tenants — S1-1's leak — and attaching org #2's
        ``owner`` role to a row that stays in org #1 would be a cross-tenant
        grant. Refusing is the only answer that is not one of those two.
        """
        from sqlalchemy.exc import DBAPIError

        default = _default_org(conn)
        conn.execute(
            text("SELECT provision_org_owner(CAST(:o AS uuid), :e)"),
            {"o": default, "e": "boss@fracktal.in"},
        )
        with pytest.raises(DBAPIError) as caught, conn.begin_nested():
            _provision(conn, "mt1j-poach", "Poacher", "Boss@Fracktal.in")
        assert "already belongs to organization" in str(caught.value.orig)

    def test_a_refused_owner_rolls_the_whole_act_back(self, conn):
        """Atomicity, from the failure side (slice 3's "one act, one
        transaction"). An organization that exists with no owner is the
        2026-07-30 lockout shape: no owner means no inviter."""
        from sqlalchemy.exc import DBAPIError

        conn.execute(
            text("SELECT provision_org_owner(CAST(:o AS uuid), :e)"),
            {"o": _default_org(conn), "e": "boss@fracktal.in"},
        )
        with pytest.raises(DBAPIError), conn.begin_nested():
            _provision(conn, "mt1j-rollback", "Rolled Back", "boss@fracktal.in")

        assert conn.execute(
            text("SELECT count(*) FROM organization WHERE slug = 'mt1j-rollback'")
        ).scalar_one() == 0

    def test_the_owner_role_must_exist_before_an_owner_can_hold_it(self, conn):
        """``provision_org_owner`` refuses rather than writing a member row
        with no grant — which would read as "provisioned" and hold nothing."""
        from sqlalchemy.exc import DBAPIError

        org = str(
            conn.execute(
                text(
                    "INSERT INTO organization (slug, display_name) "
                    "VALUES ('mt1j-roleless', 'Roleless') RETURNING id"
                )
            ).scalar_one()
        )
        with pytest.raises(DBAPIError) as caught, conn.begin_nested():
            conn.execute(
                text("SELECT provision_org_owner(CAST(:o AS uuid), :e)"),
                {"o": org, "e": "nobody@roleless.example"},
            )
        assert "has no owner role" in str(caught.value.orig)


# ── Slice 3 · organization + tenant_placement as one idempotent act ─────────

@_DB_GATE
class TestSliceThreeTheOneIdempotentAct:
    def test_provisioning_creates_the_organization_and_its_placement(self, conn):
        org = _provision(conn, "mt1j-placed", "Placed Org", "chief@placed.example")
        row = conn.execute(
            text(
                "SELECT tier, target, region FROM tenant_placement "
                " WHERE organization_id = CAST(:o AS uuid)"
            ),
            {"o": org},
        ).one()
        assert tuple(row) == ("pool", "primary", "ap-south-1")

    def test_re_running_for_the_same_slug_yields_one_org_and_one_placement(self, conn):
        """Idempotent **on the slug**, for the reason
        ``customer_console/main.py:588-591`` already argues: the natural key is
        what a retrying signup form resends."""
        first = _provision(conn, "mt1j-retry", "Retry Org", "chief@retry.example")
        second = _provision(conn, "mt1j-retry", "Retry Org", "chief@retry.example")
        assert first == second
        assert conn.execute(
            text("SELECT count(*) FROM organization WHERE slug = 'mt1j-retry'")
        ).scalar_one() == 1
        assert conn.execute(
            text(
                "SELECT count(*) FROM tenant_placement "
                " WHERE organization_id = CAST(:o AS uuid)"
            ),
            {"o": first},
        ).scalar_one() == 1

    def test_every_organization_has_a_placement(self, conn):
        """MT-1j's named fence — a set-difference over the whole tenant plane.

        Not "the row I just wrote exists": the invariant is that **no**
        organization is unplaced, so a future provisioning path that skips the
        placement fails here even though its own tests pass.
        ``resolve_placement`` refuses rather than guessing, so an unplaced
        tenant is a tenant whose data plane cannot be resolved at all.
        """
        _provision(conn, "mt1j-fence", "Fence Org", "chief@fence.example")
        unplaced = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT o.slug FROM organization o "
                    "  LEFT JOIN tenant_placement p ON p.organization_id = o.id "
                    " WHERE p.organization_id IS NULL ORDER BY o.slug"
                )
            )
        ]
        assert unplaced == [], (
            f"organizations with no tenant_placement row: {unplaced} — each is "
            "a tenant whose data plane is unresolvable"
        )

    def test_a_freshly_provisioned_org_has_the_five_system_roles_and_an_owner(
        self, conn
    ):
        """MT-1j's other named fence, kept at the spec's own name.

        ⚠️ The name says *five*; the table has always carried **six** — the
        five assignable roles plus ``agent_service``. The spec's fences table
        flags exactly this and instructs asserting the SET, which is what
        happens here. The name is the contract; the assertion is the truth.
        """
        org = _provision(conn, "mt1j-complete", "Complete Org", "chief@complete.example")
        assert set(_grants(conn, org)) == SYSTEM_ROLES
        assert _owner_emails(conn, org) == {"chief@complete.example"}
        assert conn.execute(
            text(
                "SELECT count(*) FROM tenant_placement "
                " WHERE organization_id = CAST(:o AS uuid)"
            ),
            {"o": org},
        ).scalar_one() == 1

    def test_an_explicit_tier_and_region_are_honoured(self, conn):
        """§1.5's priced tiers. Day one every row is ``(pool, primary)``, and
        the indirection is what makes a later move a data move — but a
        provisioning act that could not express ``bridge`` would make the
        column decorative."""
        org = str(
            conn.execute(
                text(
                    "SELECT provision_organization("
                    "  :slug, :name, :owner, NULL, 'bridge', 'acme-db', 'eu-west-1')"
                ),
                {
                    "slug": "mt1j-bridge",
                    "name": "Bridge Org",
                    "owner": "chief@bridge.example",
                },
            ).scalar_one()
        )
        row = conn.execute(
            text(
                "SELECT tier, target, region FROM tenant_placement "
                " WHERE organization_id = CAST(:o AS uuid)"
            ),
            {"o": org},
        ).one()
        assert tuple(row) == ("bridge", "acme-db", "eu-west-1")

    def test_an_unknown_tier_is_refused_by_the_check_constraint(self, conn):
        """159's CHECK is the fence; this asserts the function does not route
        around it (a defaulted tier would place a tenant nowhere real)."""
        from sqlalchemy.exc import DBAPIError

        with pytest.raises(DBAPIError), conn.begin_nested():
            conn.execute(
                text(
                    "SELECT provision_organization("
                    "  'mt1j-badtier', 'Bad Tier', NULL, NULL, 'gold')"
                )
            )

    def test_an_organization_may_be_provisioned_without_an_owner(self, conn):
        """The Console-side half (slice 4) may create the organization before
        it knows the owner. Roles and placement still land — an org with roles
        and no owner is recoverable; one with neither is not."""
        org = _provision(conn, "mt1j-ownerless", "Ownerless Org", None)
        assert set(_grants(conn, org)) == SYSTEM_ROLES
        assert _owner_emails(conn, org) == set()
        assert conn.execute(
            text(
                "SELECT count(*) FROM tenant_placement "
                " WHERE organization_id = CAST(:o AS uuid)"
            ),
            {"o": org},
        ).scalar_one() == 1

    def test_a_blank_slug_is_refused(self, conn):
        from sqlalchemy.exc import DBAPIError

        with pytest.raises(DBAPIError), conn.begin_nested():
            conn.execute(text("SELECT provision_organization('   ', 'Blank')"))

    def test_the_ladder_dsn_does_not_leak_out_of_this_suite(self):
        """``DATABASE_URL`` must be untouched by anything above — setting it
        re-arms ``test_tenant_coverage.py``'s two DB-gated tests, which fail by
        construction against a freshly-replayed ladder.

        ⚠️ Slice 4b's tests below DO set it, inside
        ``tenant_engine_scope`` — which restores it in a ``finally``. That is
        the same discipline ``test_deployment_resolve_cache.py`` runs under and
        the same context manager, which is why this assertion still holds
        whichever order the classes run in.
        """
        assert os.environ.get("DATABASE_URL", "") != _URL


# ── Slice 4b · the seam caller, through its own call path ───────────────────

def _org_row(conn, slug: str) -> dict | None:
    row = conn.execute(
        text("SELECT id::text AS id, display_name FROM organization "
             " WHERE slug = :s"),
        {"s": slug},
    ).mappings().first()
    return dict(row) if row else None


def _placement_count(conn, org_id: str) -> int:
    return conn.execute(
        text("SELECT count(*) FROM tenant_placement "
             " WHERE organization_id = CAST(:o AS uuid)"),
        {"o": org_id},
    ).scalar_one()


@_DB_GATE
class TestSliceFourTheSeamCaller:
    """``acb_common.provisioning.provision_local_organization`` — slice 4b.

    Spec: ``saas_multitenancy.md`` §11 MT-1j slice 4 (done-when 4b).

    **Asserted through this function's own call path, never delegated.** Slice
    3's suite above already proves what ``provision_organization`` does when
    ``psql`` calls it; that says nothing about whether a Python caller reaches
    it with the right arguments, commits, and hands back the id. The audit
    killed the version of this clause that leaned on the class above, so every
    assertion here follows a call to the seam.

    ⚠️ **These tests COMMIT.** The seam takes its own session from the shared
    engine (``get_session_factory()``, R5(b) — deliberately NOT ``get_db()``,
    whose ratchet may only go down; see ``provisioning.py``'s docstring) and
    cannot ride the rolled-back ``conn``
    fixture — so slugs and owner addresses carry a uuid and the rows stay in
    the scratch ladder database. That is safe for the one whole-plane invariant
    in this file (``test_every_organization_has_a_placement``): every row these
    tests leave behind has a placement by construction, which is the property
    it asserts.
    """

    async def test_the_seam_provisions_an_organization_end_to_end(self, conn):
        """One call → one org, one placement, six roles, one owner."""
        from acb_common.provisioning import provision_local_organization

        slug = f"mt1j-seam-{uuid.uuid4().hex[:8]}"
        owner = f"chief-{uuid.uuid4().hex[:8]}@seam.example"

        async with tenant_engine_scope(_URL):
            org_id = await provision_local_organization(slug, "Seam Org", owner)

        row = _org_row(conn, slug)
        assert row is not None, "the seam wrote no organization row"
        assert row["id"] == org_id
        assert row["display_name"] == "Seam Org"
        assert _placement_count(conn, org_id) == 1
        assert set(_grants(conn, org_id)) == SYSTEM_ROLES
        assert _owner_emails(conn, org_id) == {owner}

    async def test_calling_it_twice_converges_on_one_organization(self, conn):
        """Idempotent on the slug — the natural key a retrying signup resends.

        Twice through the SEAM, not twice through the SQL: two Python calls are
        two transactions, so this also asserts the commit in the first one did
        not leave the second one inserting a duplicate.
        """
        from acb_common.provisioning import provision_local_organization

        slug = f"mt1j-seam-twice-{uuid.uuid4().hex[:8]}"
        owner = f"chief-{uuid.uuid4().hex[:8]}@twice.example"

        async with tenant_engine_scope(_URL):
            first = await provision_local_organization(slug, "Twice Org", owner)
            second = await provision_local_organization(slug, "Twice Org", owner)

        assert first == second
        assert conn.execute(
            text("SELECT count(*) FROM organization WHERE slug = :s"),
            {"s": slug},
        ).scalar_one() == 1
        assert _placement_count(conn, first) == 1
        assert set(_grants(conn, first)) == SYSTEM_ROLES
        assert _owner_emails(conn, first) == {owner}

    async def test_an_organization_may_be_provisioned_without_an_owner(self, conn):
        """The Console-side half may create the org before it knows the owner.

        ``owner_email`` is optional here for the same reason it is optional in
        179: an organization with roles and no owner is recoverable, one with
        neither is the lockout shape.
        """
        from acb_common.provisioning import provision_local_organization

        slug = f"mt1j-seam-ownerless-{uuid.uuid4().hex[:8]}"

        async with tenant_engine_scope(_URL):
            org_id = await provision_local_organization(slug)

        # The display name defaults to the slug — in SQL, not in Python.
        assert _org_row(conn, slug) == {"id": org_id, "display_name": slug}
        assert _placement_count(conn, org_id) == 1
        assert set(_grants(conn, org_id)) == SYSTEM_ROLES
        assert _owner_emails(conn, org_id) == set()

    async def test_a_blank_slug_surfaces_179s_refusal(self):
        """The refusal is the DATABASE's, and it arrives unchanged.

        There is deliberately no client-side slug check: a second guard here
        could drift from the one that actually enforces, and a test asserting
        it would be asserting this module rather than the contract.
        """
        from acb_common.provisioning import provision_local_organization
        from sqlalchemy.exc import DBAPIError

        async with tenant_engine_scope(_URL):
            with pytest.raises(DBAPIError) as caught:
                await provision_local_organization("   ", "Blank")

        assert "slug is required" in str(caught.value.orig)


# ── Slice 7 · the tenant-plane create-only guard ────────────────────────────
#
# `saas_multitenancy.md` §11 slice 7. Migration 180 (`CREATE OR REPLACE
# provision_org_owner`, forward-only R6) refuses a FRESH email claiming an
# EXISTING owned slug — the tenant-plane twin of the slice-1 Console P0
# (`store.org_owned_by_other`). Two caller-recoverable refusals now carry
# DEDICATED, DISTINCT SQLSTATEs so `acb_common.provisioning` classifies on the
# code alone (never Postgres prose); the two NON-recoverable raises stay generic
# P0001 and pass through raw.


@_DB_GATE
class TestSliceSevenTheCreateOnlyGuardSql:
    """The SQL guard, on the SYNC shell (the `conn` fixture).

    R8 against the real replayed ladder — a fake would agree with whatever SQL
    it is handed, which is the class of trap slice 6 shipped green through.
    """

    def test_a_fresh_email_claiming_an_owned_slug_is_refused_and_writes_nothing(
        self, conn
    ):
        """The hijack fence, RED-FIRST: reverting the guard re-opens the 200.

        With `alice` owning the slug, a FRESH `carol` (no app_user row) claiming
        it must be refused with the dedicated create-only SQLSTATE and leave the
        database exactly as it was — the tenant mirror of slice 1's
        `hijack-refused-writes-nothing`.
        """
        from acb_common.provisioning import SQLSTATE_SLUG_OWNED_BY_ANOTHER
        from sqlalchemy.exc import DBAPIError

        org = _provision(conn, "mt1j-s7-hijack", "Alice Org", "alice@a.example")
        assert _owner_emails(conn, org) == {"alice@a.example"}

        with pytest.raises(DBAPIError) as caught, conn.begin_nested():
            _provision(conn, "mt1j-s7-hijack", "Alice Org", "carol@c.example")
        assert caught.value.orig.sqlstate == SQLSTATE_SLUG_OWNED_BY_ANOTHER

        # Writes nothing: still owned by alice only, carol got no app_user row.
        assert _owner_emails(conn, org) == {"alice@a.example"}
        assert conn.execute(
            text(
                "SELECT count(*) FROM app_user "
                "WHERE lower(email) = 'carol@c.example'"
            )
        ).scalar_one() == 0

    def test_a_no_owner_org_is_completed_by_any_email(self, conn):
        """The crash-resume shape: an org created before its owner was known
        (roles + placement, no owner) may be completed by ANY email — a
        no-owner org is not a conflict."""
        org = _provision(conn, "mt1j-s7-noowner", "No Owner Org", None)
        assert _owner_emails(conn, org) == set()

        conn.execute(
            text("SELECT provision_org_owner(CAST(:o AS uuid), :e)"),
            {"o": org, "e": "anyone@x.example"},
        )
        assert _owner_emails(conn, org) == {"anyone@x.example"}

    def test_the_same_owner_is_idempotent_case_insensitively(self, conn):
        """The same owner re-affirming (in a DIFFERENT case) is not a conflict.

        This is the `Carol`/`carol` idiom from the other side: a case-SENSITIVE
        guard (`au.email <> v_email`) would refuse this idempotent re-affirm, so
        this fences the `lower(email)` comparison the guard is built on.
        """
        org = _provision(conn, "mt1j-s7-idem", "Idem Org", "owner@o.example")
        conn.execute(
            text("SELECT provision_org_owner(CAST(:o AS uuid), :e)"),
            {"o": org, "e": "Owner@O.Example"},
        )
        held = conn.execute(
            text(
                "SELECT count(*) FROM user_role ur "
                "  JOIN org_role r ON r.id = ur.role_id AND r.slug = 'owner' "
                " WHERE r.organization_id = CAST(:o AS uuid)"
            ),
            {"o": org},
        ).scalar_one()
        assert held == 1
        assert _owner_emails(conn, org) == {"owner@o.example"}

    def test_the_two_refusals_carry_distinct_dedicated_sqlstates(self, conn):
        """Constraint A, at the SQL level: the cross-tenant and create-only
        raises carry DISTINCT, non-P0001 SQLSTATEs — so a caller can tell them
        apart, and from the two generic raises, by the CODE.

        Mutation: give both raises one code and `c1 != c2` goes red; leave the
        cross-tenant raise generic and `c1 != 'P0001'` goes red.
        """
        from acb_common.provisioning import (
            SQLSTATE_OWNER_BELONGS_ELSEWHERE,
            SQLSTATE_SLUG_OWNED_BY_ANOTHER,
        )
        from sqlalchemy.exc import DBAPIError

        default = _default_org(conn)
        conn.execute(
            text("SELECT provision_org_owner(CAST(:o AS uuid), :e)"),
            {"o": default, "e": "boss@fracktal.in"},
        )
        with pytest.raises(DBAPIError) as cross, conn.begin_nested():
            _provision(conn, "mt1j-s7-cross", "Cross", "boss@fracktal.in")

        _provision(conn, "mt1j-s7-distinct", "Distinct", "alice@d.example")
        with pytest.raises(DBAPIError) as slug_owned, conn.begin_nested():
            _provision(conn, "mt1j-s7-distinct", "Distinct", "carol@d.example")

        c1 = cross.value.orig.sqlstate
        c2 = slug_owned.value.orig.sqlstate
        assert c1 == SQLSTATE_OWNER_BELONGS_ELSEWHERE
        assert c2 == SQLSTATE_SLUG_OWNED_BY_ANOTHER
        assert c1 != c2
        assert c1 != "P0001" and c2 != "P0001"

    def test_a_no_owner_role_refusal_stays_untranslated(self, conn):
        """Constraints A/B on a REAL error: the no-owner-role raise is generic
        P0001, so the production classifier leaves it untranslated — it is NOT
        OwnerBelongsElsewhere. Mutation: give that raise a dedicated code and
        both assertions go red.
        """
        from acb_common.provisioning import _translate_refusal
        from sqlalchemy.exc import DBAPIError

        org = str(
            conn.execute(
                text(
                    "INSERT INTO organization (slug, display_name) "
                    "VALUES ('mt1j-s7-roleless', 'Roleless') RETURNING id"
                )
            ).scalar_one()
        )
        with pytest.raises(DBAPIError) as caught, conn.begin_nested():
            conn.execute(
                text("SELECT provision_org_owner(CAST(:o AS uuid), :e)"),
                {"o": org, "e": "nobody@roleless.example"},
            )
        assert caught.value.orig.sqlstate == "P0001"
        assert _translate_refusal(caught.value) is None


@_DB_GATE
class TestSliceSevenTheTypedRefusals:
    """The seam raises TWO distinct catchable types, on the ASYNC shell.

    Mirrors `TestSliceFourTheSeamCaller`: the exception-translation path lives in
    `provision_local_organization`, which goes through the shared async engine
    (asyncpg). These COMMIT (uuid-suffixed slugs stay in the scratch ladder),
    and every row a refusal leaves behind is unchanged — the refused call rolled
    back having written nothing.
    """

    async def test_a_slug_owned_by_another_raises_SlugOwnedByAnother(self, conn):
        """The seam's translation of 180's create-only guard (SQLSTATE P1002).

        RED-FIRST: without the guard the second call would 200 and make carol a
        co-owner, so `pytest.raises(SlugOwnedByAnother)` fails.
        """
        from acb_common.provisioning import (
            SlugOwnedByAnother,
            provision_local_organization,
        )

        slug = f"mt1j-s7-seam-slug-{uuid.uuid4().hex[:8]}"
        alice = f"alice-{uuid.uuid4().hex[:8]}@seam.example"
        carol = f"carol-{uuid.uuid4().hex[:8]}@seam.example"

        async with tenant_engine_scope(_URL):
            org_id = await provision_local_organization(slug, "Alice Seam", alice)
            with pytest.raises(SlugOwnedByAnother):
                await provision_local_organization(slug, "Carol Seam", carol)

        # Writes nothing: still alice's, and carol never got a row.
        assert _owner_emails(conn, org_id) == {alice}
        assert conn.execute(
            text("SELECT count(*) FROM app_user WHERE lower(email) = :e"),
            {"e": carol.lower()},
        ).scalar_one() == 0

    async def test_an_email_in_another_tenant_raises_OwnerBelongsElsewhere(
        self, conn
    ):
        """The seam's translation of the cross-tenant guard (SQLSTATE P1001).

        RED-FIRST: on origin/main that raise is generic P0001, so the classifier
        would re-raise a bare DBAPIError, not OwnerBelongsElsewhere.
        """
        from acb_common.provisioning import (
            OwnerBelongsElsewhere,
            provision_local_organization,
        )

        slug_a = f"mt1j-s7-seam-a-{uuid.uuid4().hex[:8]}"
        slug_b = f"mt1j-s7-seam-b-{uuid.uuid4().hex[:8]}"
        alice = f"alice-{uuid.uuid4().hex[:8]}@seam.example"

        async with tenant_engine_scope(_URL):
            await provision_local_organization(slug_a, "A Org", alice)
            with pytest.raises(OwnerBelongsElsewhere):
                await provision_local_organization(slug_b, "B Org", alice)

        # The whole act rolled back — org B was never created.
        assert _org_row(conn, slug_b) is None


class TestSliceSevenTheRefusalTypes:
    """The type hierarchy — no database. `distinct catchable types` (done-when)."""

    def test_the_two_refusals_are_distinct_catchable_types(self):
        from acb_common.provisioning import (
            OwnerBelongsElsewhere,
            ProvisioningRefused,
            SlugOwnedByAnother,
        )

        assert OwnerBelongsElsewhere is not SlugOwnedByAnother
        assert not issubclass(OwnerBelongsElsewhere, SlugOwnedByAnother)
        assert not issubclass(SlugOwnedByAnother, OwnerBelongsElsewhere)
        assert issubclass(OwnerBelongsElsewhere, ProvisioningRefused)
        assert issubclass(SlugOwnedByAnother, ProvisioningRefused)


class _FakeOrig:
    """A driver error carrying a SQLSTATE and a message, and nothing else."""

    def __init__(self, sqlstate: str | None, message: str) -> None:
        self.sqlstate = sqlstate
        self._message = message

    def __str__(self) -> str:
        return self._message


class _FakeDBAPIError(Exception):
    """A `DBAPIError` shape: `.orig` is the driver error, `str()` is its text."""

    def __init__(self, sqlstate: str | None, message: str) -> None:
        self.orig = _FakeOrig(sqlstate, message)
        super().__init__(message)


class TestSliceSevenClassificationIsPgcodeOnly:
    """Constraint C: classification dispatches on SQLSTATE ALONE, never message.

    No database, by construction: the SAME message classifies differently by
    CODE, and a misleading message never overrides the code. A message-substring
    classifier — the gap slice 7 closes — fails every assertion here.
    """

    def test_a_dedicated_code_classifies_regardless_of_message(self):
        from acb_common.provisioning import (
            SQLSTATE_OWNER_BELONGS_ELSEWHERE,
            SQLSTATE_SLUG_OWNED_BY_ANOTHER,
            OwnerBelongsElsewhere,
            SlugOwnedByAnother,
            _translate_refusal,
        )

        # Empty messages, yet each dedicated code still classifies.
        assert isinstance(
            _translate_refusal(_FakeDBAPIError(SQLSTATE_OWNER_BELONGS_ELSEWHERE, "")),
            OwnerBelongsElsewhere,
        )
        assert isinstance(
            _translate_refusal(_FakeDBAPIError(SQLSTATE_SLUG_OWNED_BY_ANOTHER, "")),
            SlugOwnedByAnother,
        )

    def test_a_misleading_message_on_a_generic_code_is_not_translated(self):
        """The killer: a P0001 error whose MESSAGE carries the cross-tenant prose
        must NOT become OwnerBelongsElsewhere. A substring classifier mis-maps
        it; a code classifier ignores the message entirely."""
        from acb_common.provisioning import _translate_refusal

        misleading = _FakeDBAPIError(
            "P0001",
            "provision_org_owner: x already belongs to organization y — "
            "refusing to move a member between tenants",
        )
        assert _translate_refusal(misleading) is None

    def test_an_unknown_code_and_a_missing_code_are_not_translated(self):
        from acb_common.provisioning import _translate_refusal

        assert _translate_refusal(_FakeDBAPIError("42P10", "some other error")) is None
        assert _translate_refusal(_FakeDBAPIError(None, "no sqlstate at all")) is None


@_DB_GATE
class TestSliceSevenOrgOwnerOf:
    """`acb_auth.access.org_owner_of(slug)` — the pre-flight identity read.

    The read sibling of `resolve_identity`, for CP-2c step 0's `SlugTaken`
    classification (the analogue of the Console's `membership_of`). Exercised
    through its own call path against the real ladder (the shared async engine,
    redirected by `tenant_engine_scope`).
    """

    async def test_it_returns_the_owner_email_for_an_owned_slug(self):
        from acb_auth.access import org_owner_of
        from acb_common.provisioning import provision_local_organization

        slug = f"mt1j-s7-owner-{uuid.uuid4().hex[:8]}"
        owner = f"owner-{uuid.uuid4().hex[:8]}@read.example"
        async with tenant_engine_scope(_URL):
            await provision_local_organization(slug, "Owned", owner)
            assert await org_owner_of(slug) == owner

    async def test_it_returns_none_for_a_slug_with_no_owner(self):
        """The crash-resume shape — an org created before its owner is known —
        is NOT a taken slug, so the pre-flight lets it be completed."""
        from acb_auth.access import org_owner_of
        from acb_common.provisioning import provision_local_organization

        slug = f"mt1j-s7-noowner-read-{uuid.uuid4().hex[:8]}"
        async with tenant_engine_scope(_URL):
            await provision_local_organization(slug)
            assert await org_owner_of(slug) is None

    async def test_it_returns_none_for_an_unknown_slug(self):
        from acb_auth.access import org_owner_of

        async with tenant_engine_scope(_URL):
            assert await org_owner_of(f"never-{uuid.uuid4().hex[:8]}") is None

    async def test_a_blank_slug_reads_none(self):
        """Never a false 'free': a blank in resolves to None like the other
        identity reads, so a caller cannot mistake it for an available slug."""
        from acb_auth.access import org_owner_of

        assert await org_owner_of("") is None
        assert await org_owner_of(None) is None
