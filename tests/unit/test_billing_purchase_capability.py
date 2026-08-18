"""SC-4a B7 clause 2 — the `billing:purchase` seed, against a real ladder.

Spec: ``project-docs/specs/subscription_console.md`` SC-4a's B7 block (the role
set, the org scope and the `manager` exclusion) · §5's command block · §3's
note that the two write proxies sit above the read floor. The slug's argued
registration is ``user_management_contract.md`` §3.

⚠️ **R8 against the TENANT database, and it answers to
``TENANT_LADDER_DATABASE_URL`` — never to ``DATABASE_URL``.** The two are
deliberately different names, for the reason
``test_deployment_resolve_cache.py`` records at length: ``pr-check.yml``'s
``test`` job runs the whole ``tests/unit/`` directory, and ``DATABASE_URL``
there would arm ``test_tenant_coverage.py``'s two DB-gated tests, which fail
**by construction** against a freshly-replayed ladder (one wants FORCE-RLS
policies that live only in ``infra/postgres/generated/04_policies.sql``, never
replayed; the other wants a non-superuser application role, and a service
container's ``POSTGRES_USER`` *is* the superuser). Both are WS-29 MT-1b/MT-1c's
gates and neither is this console's to make pass.

The gate reads the **launch snapshot** taken beside ``tests/conftest.py``'s
first one, never a live ``os.environ``: ``import litellm`` calls
``load_dotenv()`` mid-collection, and a dev machine's ``.env`` must not be able
to point an R8 gate at a local database.

⚠️ **Why a real Postgres for what looks like two INSERTs.** The seed is a
``DO $$`` block whose every statement is conditional — on the `default`
organization existing, on each role row existing, on ``ON CONFLICT DO NOTHING``
against a composite primary key. A hermetic fake agrees with whatever SQL it is
handed (R8), and the specific failures this file is here to catch — a seed that
silently no-ops because it named a role that does not exist, or one that is not
replay-safe — are invisible without a server replaying the ladder twice.

Run::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant
    uv run pytest tests/unit/test_billing_purchase_capability.py
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import CAPABILITIES
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder, ladder

#: The launch snapshot (``tests/conftest.py``), not the live variable. Falls
#: back to the live one only when the snapshot was never taken, i.e. when this
#: module is imported outside pytest.
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

CAPABILITY = "billing:purchase"

#: Roles the seed must reach, and roles it must deliberately leave alone.
#: ``owner`` is in neither list: it holds ``'*'`` from 130 and needs no row, so
#: asserting either way about a literal row for it would pin the wrong thing.
GRANTED_ROLES = ("admin", "agent_service")
UNGRANTED_ROLES = ("manager", "member", "guest")

_ROOT = Path(__file__).resolve().parents[2]


def _seed_migration() -> Path:
    """Find the seed BY CONTENT, never by its number.

    R1: the number is taken at build time and re-checked at merge, so a test
    that hard-codes ``178`` fails the day this file is renumbered in a merge —
    reporting a renumber as a broken seed. The content is the fact.
    """
    matches = [
        path
        for path in sorted((_ROOT / "infra" / "postgres").glob("*.sql"))
        if re.match(r"^\d+_", path.name)
        and f"'{CAPABILITY}'" in path.read_text(encoding="utf-8")
    ]
    assert len(matches) == 1, (
        f"expected exactly one tenant migration seeding {CAPABILITY!r}, "
        f"found {[p.name for p in matches]}"
    )
    return matches[0]


# ── The vocabulary half: no database needed ─────────────────────────────────

class TestTheVocabulary:
    """The slug has to exist in the closed tuple, or the seed is inert.

    ``acb_auth.permissions.CAPABILITIES`` is what ``/auth/me`` iterates
    (``admin/me.py:164-166``) and what the member access editor offers. A
    permission row whose string is absent from that tuple resolves to nothing
    for every principal — including an owner holding ``'*'``, because the
    wildcard is matched against those literals and nothing else.
    """

    def test_the_slug_is_in_the_capability_vocabulary(self):
        assert CAPABILITY in CAPABILITIES

    def test_the_seed_and_the_vocabulary_name_the_same_string(self):
        """A typo in either half is a capability nobody can ever hold."""
        assert f"'{CAPABILITY}'" in _seed_migration().read_text(encoding="utf-8")

    def test_the_seed_is_on_the_replayable_ladder(self):
        """A file the ladder does not pick up is a seed that never runs."""
        assert str(_seed_migration()) in {str(Path(p)) for p in ladder()}

    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        """The hand-list discovers NOTHING (pr-check.yml's own warning).

        An R8 suite absent from it still runs in the directory step, still
        skips silently there without a database, and still leaves the job
        green — the exact failure that step exists to catch, which is why the
        suite asserts its own membership rather than trusting a reviewer.
        """
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_billing_purchase_capability.py" in workflow

    def test_the_owning_spec_names_this_suite(self):
        """§5's command block, in the same change that creates the file."""
        spec = (_ROOT / "project-docs/specs/subscription_console.md").read_text(
            encoding="utf-8")
        assert "tests/unit/test_billing_purchase_capability.py" in spec


# ── The seed half: R8, against the replayed ladder ──────────────────────────

@pytest.fixture(scope="module")
def replayed():
    """Build the tenant schema from ``infra/postgres/``, then replay it.

    Applied twice on purpose and that is half of this file's subject: the
    numbered ladder is idempotent by construction, the deploy replays the
    whole thing every time, and a replay-safety claim that holds only by
    reading the DDL is unenforceable.
    """
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


def _permissions_for(conn, role_slug: str) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            text(
                "SELECT p.permission FROM org_role_permission p "
                "JOIN org_role r ON r.id = p.role_id "
                "JOIN organization o ON o.id = r.organization_id "
                "WHERE o.slug = 'default' AND r.slug = :slug"
            ),
            {"slug": role_slug},
        )
    ]


@_DB_GATE
class TestTheSeed:
    def test_admin_holds_it_after_the_ladder_replays(self, replayed):
        """B7 clause 2's role set, the granted half."""
        with replayed.begin() as conn:
            for role in GRANTED_ROLES:
                assert CAPABILITY in _permissions_for(conn, role), (
                    f"{role!r} does not hold {CAPABILITY!r} — the seed either "
                    "did not run or named a role that does not exist"
                )

    def test_manager_and_member_do_not(self, replayed):
        """The exclusion is the ARGUMENT, not an omission.

        133 seeded admin+manager because publishing an automation is an
        operational act a manager owns. This gates spending the company's
        money, and the whole case for minting the slug is that money authority
        is narrower than the `admin:members:read` floor — which `manager`
        holds. Seeding it would make clause 1 decorative.
        """
        with replayed.begin() as conn:
            for role in UNGRANTED_ROLES:
                held = _permissions_for(conn, role)
                assert CAPABILITY not in held, (
                    f"{role!r} holds {CAPABILITY!r} — B7 clause 2 excludes it"
                )

    def test_the_owner_needs_no_row_because_it_holds_the_wildcard(self, replayed):
        """133's `-- owner already holds '*'; nothing to add`, asserted.

        Not "owner must not hold the literal row" — an admin may legitimately
        add one later. What is pinned is the PREMISE the seed skipped owner
        on: the wildcard is there, so `has()` resolves the capability to yes
        without a row (``admin/me.py:164-166`` relies on exactly this).
        """
        with replayed.begin() as conn:
            assert "*" in _permissions_for(conn, "owner")

    def test_a_re_replay_grants_nothing_twice(self, replayed):
        """R6/idempotency: the deploy replays the whole ladder every time.

        ``ON CONFLICT DO NOTHING`` over the composite primary key is what makes
        that safe, and a duplicate row here would be a second grant nobody
        issued — invisible in the UI and permanent in the table.
        """
        before = {}
        with replayed.begin() as conn:
            for role in GRANTED_ROLES:
                before[role] = _permissions_for(conn, role).count(CAPABILITY)
                assert before[role] == 1

        with replayed.begin() as conn:
            apply_ladder(conn)

        with replayed.begin() as conn:
            for role in GRANTED_ROLES:
                assert _permissions_for(conn, role).count(CAPABILITY) == 1

    def test_the_ladder_dsn_does_not_leak_out_of_this_suite(self):
        """``DATABASE_URL`` must be untouched by anything above.

        This suite opens its own engine and never goes through
        ``acb_common.db``, so it has no reason to set that variable — and if a
        later edit gives it one, ``test_tenant_coverage.py``'s two DB-gated
        tests re-arm and fail by construction, which would read as this seed's
        fault.
        """
        assert os.environ.get("DATABASE_URL", "") != _URL
