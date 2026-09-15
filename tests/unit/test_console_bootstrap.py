"""The INBOUND Console bootstrap — the tenant plane catches up to the registry.

Spec: ``project-docs/specs/customer_console.md`` §6 CP-2c ·
``saas_multitenancy.md`` §11 MT-1j · ``user_management_contract.md`` R11.

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
    start_console_bootstrap,
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
