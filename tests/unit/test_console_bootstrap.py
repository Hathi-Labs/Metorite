"""The INBOUND Console bootstrap — the tenant plane catches up to the registry.

Spec: ``project-docs/specs/customer_console.md`` §6 **CP-2i** (the owning
section) · ``user_management_contract.md`` R11. Board: ``work_plan.md`` §2.0
row **M2.3b**.

⚠️ **The defect this suite exists for. An operator-created customer could not
use the product.** ``POST /orgs/provision``'s OPERATOR arm writes the Console
plane only — organization, placement, seats, owner membership, trial
subscription. Nothing has ever written the TENANT plane for it:
``provision_local_organization`` has exactly one production caller and it is the
self-serve signup route.

So the owner signed in, ``resolve_for_signin`` admitted them off a perfectly good
registry answer, ``_record_answer`` found no local ``organization`` row and
logged ``unprovisioned_org``, and ``GET /me/access`` — which reads the TENANT
plane — returned no organization. They landed on ``AccessGate``'s *"No
organization is linked to this email"*, while the Operator Console's success
panel was telling the operator they could sign in with no invite needed.

``bootstrap_placed_orgs`` is the repair, and the exact MIRROR IMAGE of
``reconcile()``: that one pushes tenant-born orgs UP to the Console, this pulls
Console-born orgs DOWN to the tenant. Between them the planes converge from
either direction.

⚠️ **R8 on BOTH ladders, 0 skips.** Every clause runs against a REAL replayed
tenant ladder AND the REAL Customer Console app over an in-process ASGI
transport, so ``POST /registry/orgs`` genuinely authenticates a deployment key
and genuinely returns rows the Console genuinely wrote. The DB gate's reason
names BOTH ladder variables — the substrings ``pr-check.yml``'s skip-guard greps
for — so a disarmed gate fails the job rather than passing green. A skip is not
a pass.

Run::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant
    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_console_bootstrap.py -q -rs
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

import httpx
from acb_auth.console_resolve import (
    BootstrapSummary,
    bootstrap_placed_orgs,
    reconcile,
    start_console_bootstrap,
)
from acb_common.provisioning import (
    persist_org_billing_profile,
    provision_local_organization,
)
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import (
    apply_ladder as apply_console_ladder,
)
from tests.unit._customer_console_ladder import (
    ensure_deployment,
    mint_deployment_key,
)
from tests.unit._tenant_ladder import (
    apply_ladder as apply_tenant_ladder,
)
from tests.unit._tenant_ladder import tenant_engine_scope

VALID_GSTIN = "27AAPFU0939F1ZV"
STATE = "KA"

_ROOT = Path(__file__).resolve().parents[2]


# ── The hand-list defends itself (no database; must always run) ──────────────

class TestThisSuiteIsRegistered:
    """A two-ladder R8 suite whose DB-gated classes SKIP without a database — so
    a suite absent from pr-check.yml's skip-guard would skip there silently and
    leave the job green."""

    def test_named_in_the_ci_skip_guard(self):
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_console_bootstrap.py" in workflow


_TENANT_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_TENANT_URL = (
    _TENANT_SNAPSHOT
    if _TENANT_SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()
_CONSOLE_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

_R8 = pytest.mark.skipif(
    not (_TENANT_URL and _CONSOLE_URL),
    reason=(
        "TENANT_LADDER_DATABASE_URL unset or CUSTOMER_CONSOLE_DATABASE_URL "
        "unset — the inbound bootstrap is R8 on BOTH ladders (tenant plane + "
        "Console plane). A skip here is not a pass; CI must set both."
    ),
)


# ── Ship-dark: inert unwired and inert unflagged (no database) ────────────────

class TestItShipsDark:
    """Two independent no-ops, and they must BOTH hold: the flag is off by
    default, and an unwired box has no Console to ask even when it is on."""

    async def test_unwired_returns_empty_and_never_builds_a_client(
        self, monkeypatch
    ):
        from acb_common.settings import get_settings

        monkeypatch.delenv("CUSTOMER_CONSOLE_URL", raising=False)
        monkeypatch.delenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", raising=False)
        get_settings.cache_clear()

        def _boom():
            raise AssertionError("no HTTP client may be built when unwired")

        monkeypatch.setattr("acb_auth.console_resolve._new_http_client", _boom)
        try:
            summary = await bootstrap_placed_orgs()
        finally:
            get_settings.cache_clear()

        assert summary == BootstrapSummary()
        assert summary.provisioned == 0

    def test_the_loop_does_not_start_without_the_flag(self, monkeypatch):
        # Default OFF. `start_console_bootstrap` returning None IS the dark
        # position — the gateway lifespan calls it unconditionally, so the flag
        # having exactly one reader is what keeps the two from disagreeing.
        from acb_common.settings import get_settings

        monkeypatch.delenv("CONSOLE_BOOTSTRAP_ENABLED", raising=False)
        get_settings.cache_clear()
        try:
            assert start_console_bootstrap() is None
        finally:
            get_settings.cache_clear()

    @pytest.mark.parametrize("value", ["false", "TRUE", "1", "yes", ""])
    def test_only_the_exact_string_true_arms_it(self, monkeypatch, value):
        """`=== "true"` EXACTLY, never truthiness — the `auth.ts:163` idiom.
        An operator who writes `CONSOLE_BOOTSTRAP_ENABLED=false` while debugging
        must get OFF, and every truthy-string reading would arm it instead."""
        from acb_common.settings import get_settings

        monkeypatch.setenv("CONSOLE_BOOTSTRAP_ENABLED", value)
        get_settings.cache_clear()
        try:
            assert start_console_bootstrap() is None
        finally:
            get_settings.cache_clear()


# ── The two ladders ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def tenant_db():
    if not _TENANT_URL:
        yield None
        return
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_tenant_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def console_db():
    if not _CONSOLE_URL:
        yield None
        return
    eng = create_engine(_CONSOLE_URL, future=True)
    with eng.begin() as conn:
        apply_console_ladder(conn)
    yield eng
    eng.dispose()


class _CountingASGITransport(httpx.ASGITransport):
    def __init__(self, app):
        super().__init__(app=app)
        self.count = 0

    async def handle_async_request(self, request):
        self.count += 1
        return await super().handle_async_request(request)


class _Wired:
    def __init__(self, transport, deployment_id, console_app, token):
        self.transport = transport
        self.deployment_id = deployment_id
        self.console_app = console_app
        self.token = token

    @property
    def count(self) -> int:
        return self.transport.count

    def reset(self) -> None:
        self.transport.count = 0


@pytest.fixture
def wired(console_db, monkeypatch):
    """Wire the ONE Console client to the REAL Console app over ASGI.

    The deployment is FRESH per test (a new label, a new key), which matters
    more here than in the reconciler suite: this sweep reads *everything placed
    on the calling deployment*, so a shared box would make one test's orgs
    visible to the next and every count assertion meaningless.
    """
    from acb_common.settings import get_settings

    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", "op-token")
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", "internal")
    from customer_console import auth
    from customer_console.main import app as console_app

    label = f"boot-{uuid.uuid4().hex[:8]}"
    with console_db.begin() as c:
        dep = ensure_deployment(c, label=label)
        token = mint_deployment_key(
            c, deployment_id=dep,
            capabilities=[auth.RESOLVE_CAPABILITY, auth.PROVISION_CAPABILITY],
        )

    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "http://console.invalid")
    monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", token)
    get_settings.cache_clear()

    transport = _CountingASGITransport(console_app)
    monkeypatch.setattr(
        "acb_auth.console_resolve._new_http_client",
        lambda: httpx.AsyncClient(transport=transport),
    )
    yield _Wired(transport, dep, console_app, token)
    get_settings.cache_clear()


async def _console_only_customer(wired, slug, owner, **extra):
    """Create a customer on the CONSOLE PLANE ONLY, and assert it landed.

    This is the state the whole suite is about, and it is not contrived: it is
    exactly what the Operator Console's "New customer" button produces — the
    registry knows the organization, its placement, its seats, its owner and its
    trial, and the tenant plane has never heard of it.

    Driven over the same ASGI transport with this box's deployment key, so the
    Console genuinely authenticates and genuinely writes its own database.

    ⚠️ It deliberately does NOT go through ``provision_org_on_console``: that
    helper is the gateway's client, and using it here would make the fixture
    depend on the code path under test.
    """
    payload = {"slug": slug, "name": "Acme Inc", "owner_email": owner, **extra}
    async with httpx.AsyncClient(
        transport=wired.transport, base_url="http://console.invalid"
    ) as client:
        response = await client.post(
            "/orgs/provision",
            headers={"Authorization": f"Bearer {wired.token}"},
            json=payload,
        )
    assert response.status_code == 200, response.text
    # The fixture's own requests must not pollute the sweep's call count.
    wired.reset()
    return response.json()


def _tenant_slugs(tenant_db) -> set[str]:
    with tenant_db.connect() as c:
        return {r[0] for r in c.execute(text("SELECT slug FROM organization"))}


def _tenant_owner(tenant_db, slug: str) -> str | None:
    with tenant_db.connect() as c:
        return c.execute(
            text(
                "SELECT u.email FROM app_user u "
                "  JOIN organization o ON o.id = u.organization_id "
                " WHERE o.slug = :s AND u.status = 'active' "
                " ORDER BY u.created_at LIMIT 1"
            ),
            {"s": slug},
        ).scalar()


def _tenant_profile(tenant_db, slug: str) -> dict | None:
    with tenant_db.connect() as c:
        row = c.execute(
            text(
                "SELECT gstin, billing_state, console_mirrored_at "
                "  FROM organization WHERE slug = :s"
            ),
            {"s": slug},
        ).mappings().first()
    return dict(row) if row else None


def _new_org() -> tuple[str, str]:
    tag = uuid.uuid4().hex[:8]
    return f"boot-{tag}", f"owner-{tag}@acme.example"


# ══ 1 · one pass provisions the tenant half ══════════════════════════════════

@_R8
class TestOnePassProvisionsWhatTheConsolePlaces:
    """The repair, end to end on two real databases.

    Red-first by construction: the tenant plane is asserted NOT to know the slug
    before the pass, which is precisely the state an operator-created customer
    was permanently stuck in.
    """

    async def test_a_console_only_customer_gets_its_tenant_organization(
        self, tenant_db, console_db, wired
    ):
        slug, owner = _new_org()
        await _console_only_customer(
            wired, slug, owner, gstin=VALID_GSTIN, billing_state=STATE
        )

        # BEFORE: the registry knows them and the tenant plane does not. This is
        # the bug — the owner signs in here and is told that no organization is
        # linked to their email.
        assert slug not in _tenant_slugs(tenant_db)

        async with tenant_engine_scope(_TENANT_URL):
            summary = await bootstrap_placed_orgs()

        assert isinstance(summary, BootstrapSummary)
        assert summary.provisioned >= 1
        # AFTER: the organization exists locally, with its owner, so sign-in
        # resolves to a real tenant.
        assert slug in _tenant_slugs(tenant_db)
        assert _tenant_owner(tenant_db, slug) == owner

    async def test_it_carries_the_GST_profile_and_marks_the_org_mirrored(
        self, tenant_db, console_db, wired
    ):
        slug, owner = _new_org()
        await _console_only_customer(
            wired, slug, owner, gstin=VALID_GSTIN, billing_state=STATE
        )

        async with tenant_engine_scope(_TENANT_URL):
            await bootstrap_placed_orgs()

        profile = _tenant_profile(tenant_db, slug)
        assert profile is not None
        assert profile["gstin"] == VALID_GSTIN
        assert profile["billing_state"] == STATE
        # The marker is what stops `reconcile()` pushing this org straight back
        # up to the Console it just came from. Without it the two sweeps hand
        # the same organization to each other for ever.
        assert profile["console_mirrored_at"] is not None


# ══ 2 · a second pass is a no-op ═════════════════════════════════════════════

@_R8
class TestASecondPassProvisionsNothing:
    """Idempotent twice over: the local-slug read excludes it, and migration 179
    would converge anyway. The first is what makes the steady state free."""

    async def test_the_second_pass_reports_already_local(
        self, tenant_db, console_db, wired
    ):
        slug, owner = _new_org()
        await _console_only_customer(wired, slug, owner)

        async with tenant_engine_scope(_TENANT_URL):
            first = await bootstrap_placed_orgs()
            assert first.provisioned >= 1

            second = await bootstrap_placed_orgs()

        assert second.provisioned == 0
        assert second.already_local >= 1
        assert second.placed == first.placed

    async def test_the_steady_state_costs_exactly_one_console_read(
        self, tenant_db, console_db, wired
    ):
        """One HTTP call per pass, however many orgs are already local — which
        is what makes a 60-second interval affordable."""
        slug, owner = _new_org()
        await _console_only_customer(wired, slug, owner)

        async with tenant_engine_scope(_TENANT_URL):
            await bootstrap_placed_orgs()
            wired.reset()
            await bootstrap_placed_orgs()

        assert wired.count == 1


# ══ 3 · what it refuses to do ════════════════════════════════════════════════

@_R8
class TestItRefusesRatherThanForces:
    """The sweep writes the tenant plane, so the interesting cases are the ones
    where it must NOT."""

    async def test_an_org_with_no_owner_yet_is_skipped_not_provisioned(
        self, tenant_db, console_db, wired
    ):
        """The Console's own crash-resume shape: an organization created before
        its owner membership landed.

        Provisioning it here without an owner would leave a tenant organization
        nobody can sign in to — a WORSE state than the one being repaired, and
        one no later pass would fix, because the slug would then look local.
        """
        slug, _owner = _new_org()
        placeholder = "tmp-" + uuid.uuid4().hex[:6] + "@x.example"
        await _console_only_customer(wired, slug, placeholder)
        # Strip the owner membership, leaving the org and its placement behind.
        with console_db.begin() as c:
            c.execute(
                text(
                    "DELETE FROM org_membership WHERE organization_id = "
                    "(SELECT id FROM organization WHERE slug = :s)"
                ),
                {"s": slug},
            )

        async with tenant_engine_scope(_TENANT_URL):
            summary = await bootstrap_placed_orgs()

        assert summary.skipped_no_owner >= 1
        assert slug not in _tenant_slugs(tenant_db)

    async def test_it_never_writes_the_console(
        self, tenant_db, console_db, wired
    ):
        """It reads the registry and writes only the tenant plane.

        A sweep that could write the Console could mint a customer, and the
        registry is the authority for who exists and what they pay.
        """
        slug, owner = _new_org()
        await _console_only_customer(wired, slug, owner)

        def _console_rows():
            with console_db.connect() as c:
                return (
                    c.execute(
                        text("SELECT count(*) FROM organization")
                    ).scalar(),
                    c.execute(
                        text("SELECT count(*) FROM org_membership")
                    ).scalar(),
                    c.execute(
                        text("SELECT count(*) FROM seat_grant")
                    ).scalar(),
                )

        before = _console_rows()
        async with tenant_engine_scope(_TENANT_URL):
            await bootstrap_placed_orgs()
        assert _console_rows() == before

    async def test_an_unreachable_console_provisions_nothing(
        self, tenant_db, console_db, wired, monkeypatch
    ):
        """The ordinary failure this must survive. Nothing is provisioned,
        nothing is wrong locally, and the next pass converges."""

        def _broken():
            raise RuntimeError("connection refused")

        monkeypatch.setattr(
            "acb_auth.console_resolve._new_http_client", _broken
        )
        before = _tenant_slugs(tenant_db)

        async with tenant_engine_scope(_TENANT_URL):
            summary = await bootstrap_placed_orgs()

        assert summary == BootstrapSummary()
        assert _tenant_slugs(tenant_db) == before


# ══ 4 · the Console door itself ══════════════════════════════════════════════

@_R8
class TestThePlacedOrgsDoor:
    """POST /registry/orgs — deployment-key only, and bounded by placement."""

    async def _ask(self, wired, token=None):
        async with httpx.AsyncClient(
            transport=wired.transport, base_url="http://console.invalid"
        ) as client:
            return await client.post(
                "/registry/orgs",
                headers={"Authorization": "Bearer " + (token or wired.token)},
                json={},
            )

    async def test_it_answers_the_orgs_placed_on_the_calling_deployment(
        self, console_db, wired
    ):
        slug, owner = _new_org()
        await _console_only_customer(wired, slug, owner)

        response = await self._ask(wired)

        assert response.status_code == 200
        rows = response.json()["organizations"]
        mine = [r for r in rows if r["slug"] == slug]
        assert len(mine) == 1
        assert mine[0]["owner_email"] == owner

    async def test_a_deployment_never_sees_another_boxs_customers(
        self, console_db, wired
    ):
        """Placement is the boundary. The door answers *which customers am I
        serving*, and that answer must never include somebody else's — a
        cross-deployment read here would be a tenancy leak."""
        from customer_console import auth

        mine_slug, mine_owner = _new_org()
        await _console_only_customer(wired, mine_slug, mine_owner)

        # A SECOND box, with its own key and its own customer.
        other_slug, other_owner = _new_org()
        with console_db.begin() as c:
            other_dep = ensure_deployment(
                c, label="other-" + uuid.uuid4().hex[:8]
            )
            other_token = mint_deployment_key(
                c,
                deployment_id=other_dep,
                capabilities=[
                    auth.RESOLVE_CAPABILITY,
                    auth.PROVISION_CAPABILITY,
                ],
            )
        async with httpx.AsyncClient(
            transport=wired.transport, base_url="http://console.invalid"
        ) as client:
            created = await client.post(
                "/orgs/provision",
                headers={"Authorization": "Bearer " + other_token},
                json={
                    "slug": other_slug,
                    "name": "Other Co",
                    "owner_email": other_owner,
                },
            )
        assert created.status_code == 200

        answer = await self._ask(wired)
        slugs = {r["slug"] for r in answer.json()["organizations"]}
        assert mine_slug in slugs
        assert other_slug not in slugs

    async def test_an_operator_token_is_refused(self, console_db, wired):
        """The door answers FOR the calling deployment, and an operator token
        names none — so the question has no subject. Refused on shape."""
        response = await self._ask(wired, token="op-token")
        assert response.status_code == 400
        assert "deployment" in response.json()["detail"]

    async def test_an_unauthenticated_caller_is_refused(
        self, console_db, wired
    ):
        async with httpx.AsyncClient(
            transport=wired.transport, base_url="http://console.invalid"
        ) as client:
            response = await client.post("/registry/orgs", json={})
        assert response.status_code in (401, 403)


def _console_seat_quantity(console_db, slug: str):
    """The Core seats the Console granted this org, or None if it has no row.

    A SUM, because `seat_grant` is append-only and signed — a reduction is a
    negative grant, never an UPDATE (`001_customer_console.sql:174`). Reading a
    single row would be right today only because provisioning grants once.
    """
    with console_db.connect() as c:
        return c.execute(
            text(
                "SELECT sum(g.quantity_purchased) FROM seat_grant g "
                "  JOIN organization o ON o.id = g.organization_id "
                " WHERE o.slug = :s AND g.plan_slug = 'core'"
            ),
            {"s": slug},
        ).scalar()


# ══ 5 · the review findings, each with the failure it closes ═════════════════

@_R8
class TestACancelledCustomerGetsNoWorkspace:
    """Review finding P1. The sweep built a live tenant for a dead customer.

    A lifecycle transition updates ``organization.status`` and the subscription
    and touches NEITHER ``org_placement`` NOR ``org_membership``. Only a purge
    removes those, and purge is reachable from ``deleted`` alone. So a cancelled
    customer stays in the door's answer indefinitely — and the sweep would give
    them a brand-new EMPTY workspace with an ACTIVE owner, months after they
    left. Arming the flag on an existing box would do it for the whole
    historical backlog in one pass.
    """

    async def _set_status(self, console_db, slug, status):
        with console_db.begin() as c:
            c.execute(
                text("UPDATE organization SET status = :st WHERE slug = :s"),
                {"st": status, "s": slug},
            )

    @pytest.mark.parametrize("status", ["cancelled", "deleted"])
    async def test_a_customer_who_is_not_being_served_is_skipped(
        self, tenant_db, console_db, wired, status
    ):
        slug, owner = _new_org()
        await _console_only_customer(wired, slug, owner)
        await self._set_status(console_db, slug, status)

        async with tenant_engine_scope(_TENANT_URL):
            summary = await bootstrap_placed_orgs()

        assert summary.skipped_not_serving >= 1, status
        assert slug not in _tenant_slugs(tenant_db), status

    @pytest.mark.parametrize(
        "status", ["trial", "active", "past_due", "suspended"]
    )
    async def test_a_customer_who_IS_being_served_is_provisioned(
        self, tenant_db, console_db, wired, status
    ):
        """The other half, and the one that makes the case above non-vacuous: a
        gate that refused everything would pass every parametrisation there."""
        slug, owner = _new_org()
        await _console_only_customer(wired, slug, owner)
        await self._set_status(console_db, slug, status)

        async with tenant_engine_scope(_TENANT_URL):
            await bootstrap_placed_orgs()

        assert slug in _tenant_slugs(tenant_db), status

    async def test_the_verdict_travels_as_a_BOOLEAN(
        self, console_db, wired
    ):
        """§6(d): the box must never branch on a lifecycle word, or it holds a
        second copy of the Console's state machine spelled as an ``if``."""
        slug, owner = _new_org()
        await _console_only_customer(wired, slug, owner)

        async with httpx.AsyncClient(
            transport=wired.transport, base_url="http://console.invalid"
        ) as client:
            response = await client.post(
                "/registry/orgs",
                headers={"Authorization": "Bearer " + wired.token},
                json={},
            )
        row = next(
            r for r in response.json()["organizations"] if r["slug"] == slug
        )
        assert row["provisionable"] is True

    async def test_a_console_that_omits_the_field_provisions_NOTHING(
        self, tenant_db, monkeypatch
    ):
        """Fail CLOSED on silence. A Console predating the field tells us
        nothing, and building a workspace on nothing is the wrong direction."""
        async def _answer():
            return [{"slug": "no-verdict-" + uuid.uuid4().hex[:6],
                     "owner_email": "x@y.example", "display_name": "X"}]

        monkeypatch.setattr(
            "acb_auth.console_resolve.is_wired", lambda: True
        )
        monkeypatch.setattr(
            "acb_auth.console_resolve._post_placed_orgs", _answer
        )

        async with tenant_engine_scope(_TENANT_URL):
            summary = await bootstrap_placed_orgs()

        assert summary.provisioned == 0
        assert summary.skipped_not_serving == 1


@_R8
class TestTheTeamSizeSurvivesTheRECOVERYPath:
    """Review finding P1. The seat repair undid itself on its own retry path.

    Step 0a's ``AlreadyMember`` blocks every resubmit once step 1 has committed,
    so the CP-2e reconciler is the ONLY repair for a signup whose step 2 failed.
    It rebuilds the Console call from tenant columns alone — and the team size
    was persisted nowhere, so it re-drove with none, the Console applied its
    default of ONE, and ``grant_seats`` runs once only. The organization was
    stuck at one seat for ever: the exact dead end this branch exists to close,
    reached through its own recovery.
    """

    async def test_signup_persists_the_team_size_on_the_tenant_row(
        self, tenant_db
    ):
        slug, owner = _new_org()
        async with tenant_engine_scope(_TENANT_URL):
            await provision_local_organization(slug, "Acme Inc", owner)
            await persist_org_billing_profile(
                slug, gstin=None, billing_state=STATE, core_seats=12
            )

        with tenant_db.connect() as c:
            assert c.execute(
                text(
                    "SELECT signup_core_seats FROM organization WHERE slug = :s"
                ),
                {"s": slug},
            ).scalar() == 12

    async def test_the_reconciler_re_drives_the_ASKED_seat_count(
        self, tenant_db, console_db, wired
    ):
        """The whole finding, end to end: a signup whose Console mirror failed
        is repaired with the seats the founder ASKED for, not with one."""
        slug, owner = _new_org()
        async with tenant_engine_scope(_TENANT_URL):
            # Step 1 committed; step 2 never ran (the transient failure).
            await provision_local_organization(slug, "Acme Inc", owner)
            await persist_org_billing_profile(
                slug, gstin=None, billing_state=STATE, core_seats=12
            )
            assert _console_seat_quantity(console_db, slug) is None

            await reconcile()

        assert _console_seat_quantity(console_db, slug) == 12

    async def test_an_org_predating_the_column_still_reconciles(
        self, tenant_db, console_db, wired
    ):
        """NULL means 'pre-dates the question'. The client omits the field, the
        Console applies its own default, and nothing raises — R6's whole point,
        since old rows meet new code here."""
        slug, owner = _new_org()
        async with tenant_engine_scope(_TENANT_URL):
            await provision_local_organization(slug, "Acme Inc", owner)
            await persist_org_billing_profile(
                slug, gstin=None, billing_state=STATE, core_seats=None
            )

            summary = await reconcile()

        assert summary.mirrored >= 1
        assert _console_seat_quantity(console_db, slug) == 1


class TestTheTeamSizeGateRefusesWhatIntRefuses:
    """Review finding P2. ``isdigit()`` admits characters ``int()`` rejects, so
    a shape violation escaped as a 500 instead of the documented 400."""

    def test_a_superscript_digit_is_a_400_and_not_a_crash(self):
        from gateway.routes import signup as route

        # "²".isdigit() is True and int("²") raises ValueError.
        assert "²".isdigit() and not "²".isdecimal()
        answer = route._team_size("²")
        assert not isinstance(answer, int)
        assert answer.status_code == 400

    def test_an_arabic_indic_digit_is_refused_rather_than_read_as_three(self):
        from gateway.routes import signup as route

        # int("٣") == 3. A signup form has no business reading that as a
        # seat count, and isdecimal() is what stops it.
        answer = route._team_size("٣")
        assert not isinstance(answer, int)
        assert answer.status_code == 400

    def test_blank_and_whitespace_are_the_SAME_answer(self):
        """Both say "I did not answer". One rule, one outcome — treating "" as
        absent and "  " as malformed was one rule with two answers."""
        from gateway.routes import signup as route

        assert route._team_size("") == route.DEFAULT_TEAM_SIZE
        assert route._team_size("   ") == route.DEFAULT_TEAM_SIZE
        assert route._team_size(None) == route.DEFAULT_TEAM_SIZE


class TestTheLoopTaskIsHeld:
    """Review finding P2. The lifespan discarded the task handle, against this
    repo's own two precedents — and an unreferenced task may be collected
    mid-flight, with nothing able to cancel or inspect it."""

    def test_the_module_holds_the_task_and_hands_it_back(self, monkeypatch):
        import asyncio

        from acb_auth import console_resolve
        from acb_common.settings import get_settings

        monkeypatch.setenv("CONSOLE_BOOTSTRAP_ENABLED", "true")
        get_settings.cache_clear()

        async def _run():
            task = console_resolve.start_console_bootstrap()
            assert task is not None
            assert console_resolve.console_bootstrap_task() is task
            # A second start must not add a second loop — two would double
            # every pass and race each other's `local` read.
            assert console_resolve.start_console_bootstrap() is task
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        try:
            asyncio.run(_run())
        finally:
            console_resolve._bootstrap_task = None
            get_settings.cache_clear()


class TestAPermanentConsoleRefusalIsNotRetriedForever:
    """Review finding P2. ``_post_provision`` called every non-200 transient.

    That was true until ``ProvisionRequest`` grew a slug validator. A tenant org
    whose slug it refuses now answers 422 on EVERY pass — logged as an outage to
    retry, retried a minute later, indefinitely, with the real cause buried.
    """

    async def test_a_422_raises_REFUSED_rather_than_UNAVAILABLE(
        self, monkeypatch
    ):
        from acb_auth import console_resolve

        class _Resp:
            status_code = 422
            text = "slug must be DNS-label-safe"

            def json(self):
                return {"detail": "nope"}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                return _Resp()

        monkeypatch.setattr(
            console_resolve, "_new_http_client", lambda: _Client()
        )
        monkeypatch.setattr(console_resolve, "is_wired", lambda: True)

        with pytest.raises(console_resolve.ConsoleProvisionRefused):
            await console_resolve.provision_org_on_console(
                "bad slug", "Bad", "a@b.example"
            )

    async def test_a_500_is_still_UNAVAILABLE(self, monkeypatch):
        """Non-vacuity: the split must not swallow the transient case it was
        carved out of."""
        from acb_auth import console_resolve

        class _Resp:
            status_code = 503
            text = "down"

            def json(self):
                return {}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                return _Resp()

        monkeypatch.setattr(
            console_resolve, "_new_http_client", lambda: _Client()
        )
        monkeypatch.setattr(console_resolve, "is_wired", lambda: True)

        with pytest.raises(console_resolve.ConsoleProvisionUnavailable):
            await console_resolve.provision_org_on_console(
                "fine", "Fine", "a@b.example"
            )


# ══ 6 · the SECOND review's findings ═════════════════════════════════════════

class TestTheTeamSizeGateCannotBeCrashed:
    """Review round 2. The ASCII fix still let `int()` raise, so a shape
    violation still escaped as a 500 rather than the documented 400."""

    def test_a_very_long_digit_string_is_a_400_and_not_a_crash(self):
        from gateway.routes import signup as route

        # CPython refuses to PARSE an integer literal past
        # `sys.get_int_max_str_digits()` (4300) and raises ValueError — so the
        # regex accepted this and `int()` blew up underneath it.
        answer = route._team_size("1" * 4301)
        assert not isinstance(answer, int)
        assert answer.status_code == 400

    def test_the_digit_bound_is_DERIVED_from_the_range(self):
        """Two constants that must agree are one constant. `MAX_TEAM_SIZE` is
        the rule; the digit bound is read off it."""
        from gateway.routes import signup as route

        assert route._MAX_TEAM_SIZE_DIGITS == len(str(route.MAX_TEAM_SIZE))

    def test_every_value_in_range_still_passes(self):
        """Non-vacuity: a length bound set one digit too tight would refuse
        real answers, and every case above would still pass."""
        from gateway.routes import signup as route

        for n in (1, 9, 10, 49, 50):
            assert route._team_size(str(n)) == n
            assert route._team_size(n) == n


class TestTheLifecycleGateIsItsOwnQuestion:
    """Review round 2. `can_write_seats` stranded a SUSPENDED customer.

    They can pay — `can_pay` is True and the module's own note says a suspended
    customer must keep the route to paying — but the checkout lives INSIDE the
    tenant app. So a suspended customer with no workspace cannot reach the page
    that would un-suspend them, and the sweep would have skipped them for ever.
    """

    def test_the_four_served_states_may_be_provisioned(self):
        from customer_console.lifecycle import capabilities_of

        for state in ("trial", "active", "past_due", "suspended"):
            assert capabilities_of(state).can_be_provisioned is True, state

    def test_the_two_departed_states_may_NOT(self):
        from customer_console.lifecycle import capabilities_of

        for state in ("cancelled", "deleted"):
            assert capabilities_of(state).can_be_provisioned is False, state

    def test_it_is_a_synonym_for_NO_other_field(self):
        """If it matched one, it should BE that one — and each near-miss here
        is a bug that was actually written."""
        from customer_console.lifecycle import STATES

        mine = {s: c.can_be_provisioned for s, c in STATES.items()}
        for other in (
            "can_sign_in", "can_use_ai", "can_write_seats",
            "data_retained", "can_pay",
        ):
            theirs = {s: getattr(c, other) for s, c in STATES.items()}
            assert mine != theirs, other

    def test_an_unknown_state_fails_CLOSED(self):
        # `capabilities_of` maps anything unrecognised to `deleted`, so a
        # hand-edit or a future state cannot read as "build them a workspace".
        from customer_console.lifecycle import capabilities_of

        assert capabilities_of("banana").can_be_provisioned is False
        assert capabilities_of("").can_be_provisioned is False


@_R8
class TestASuspendedCustomerIsStillGivenAWorkspace:
    """The round-2 finding, end to end on both databases."""

    async def test_suspended_is_provisioned_so_they_can_reach_the_checkout(
        self, tenant_db, console_db, wired
    ):
        slug, owner = _new_org()
        await _console_only_customer(wired, slug, owner)
        with console_db.begin() as c:
            c.execute(
                text(
                    "UPDATE organization SET status = 'suspended' "
                    " WHERE slug = :s"
                ),
                {"s": slug},
            )

        async with tenant_engine_scope(_TENANT_URL):
            await bootstrap_placed_orgs()

        assert slug in _tenant_slugs(tenant_db)

    async def test_the_door_ships_NO_lifecycle_word(self, console_db, wired):
        """§6(d): the box must not branch on a lifecycle word, and a field it
        should not read is an invitation to read it. It travels as one boolean
        or not at all."""
        slug, owner = _new_org()
        await _console_only_customer(wired, slug, owner)

        async with httpx.AsyncClient(
            transport=wired.transport, base_url="http://console.invalid"
        ) as client:
            response = await client.post(
                "/registry/orgs",
                headers={"Authorization": "Bearer " + wired.token},
                json={},
            )
        row = next(
            r for r in response.json()["organizations"] if r["slug"] == slug
        )
        assert "status" not in row
        assert row["provisionable"] is True
        # And no lifecycle word rides any other field.
        words = {"trial", "active", "past_due", "suspended", "cancelled",
                 "deleted"}
        assert not (words & {str(v) for v in row.values()})


class TestTheLoopIsStoppable:
    """Review round 2. The lifespan started the sweep and never stopped it, so
    a pending task outlived its event loop and then poisoned the guard."""

    def test_a_task_from_a_DEAD_loop_does_not_block_a_restart(
        self, monkeypatch
    ):
        """The concrete failure: one process running the lifespan twice — which
        a `TestClient` context manager does — left a task pending on a loop that
        had CLOSED. `done()` stays False on such a task, so the guard handed it
        back as if running, the sweep never ran again, and nothing said so.

        Built with explicit loops rather than `asyncio.run`, which cancels
        pending tasks on its way out and so cannot reproduce it.
        """
        import asyncio

        from acb_auth import console_resolve
        from acb_common.settings import get_settings

        monkeypatch.setenv("CONSOLE_BOOTSTRAP_ENABLED", "true")
        get_settings.cache_clear()

        loop_one = asyncio.new_event_loop()
        loop_two = asyncio.new_event_loop()
        try:
            async def _start():
                return console_resolve.start_console_bootstrap()

            first = loop_one.run_until_complete(_start())
            assert first is not None
            # Close WITHOUT cancelling — the shape a lifespan with no stop
            # leaves behind.
            loop_one.close()
            assert not first.done(), (
                "premise: a task abandoned on a closed loop stays not-done"
            )

            second = loop_two.run_until_complete(_start())
            assert second is not first, (
                "a task from a closed loop was handed back as if running — "
                "the sweep would never run again"
            )
            loop_two.run_until_complete(
                console_resolve.stop_console_bootstrap()
            )
        finally:
            console_resolve._bootstrap_task = None
            for loop in (loop_one, loop_two):
                if not loop.is_closed():
                    loop.close()
            get_settings.cache_clear()

    def test_stop_is_idempotent_and_safe_when_nothing_started(self):
        import asyncio

        from acb_auth import console_resolve

        async def _run():
            await console_resolve.stop_console_bootstrap()
            await console_resolve.stop_console_bootstrap()

        console_resolve._bootstrap_task = None
        asyncio.run(_run())
        assert console_resolve.console_bootstrap_task() is None

    def test_the_gateway_lifespan_STOPS_it_as_well_as_starting_it(self):
        """Every sibling loop in that file has a stop after `yield`. This one
        had none, which is what left the task behind."""
        from pathlib import Path

        main = (
            Path(__file__).resolve().parents[2]
            / "apps/services/gateway/gateway/main.py"
        ).read_text(encoding="utf-8")
        assert "start_console_bootstrap" in main
        assert "stop_console_bootstrap" in main
        assert main.index("start_console_bootstrap") < main.index(
            "stop_console_bootstrap"
        )
