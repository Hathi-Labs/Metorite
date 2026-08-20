"""CP-2e — the signup Console-mirror reconciler (WS-31).

Spec: ``project-docs/specs/customer_console.md`` §6 CP-2e (done-when 1-5) ·
``user_management_contract.md`` R11 · CP-2c done-when 5 (the dark window this
closes).

The reconciler is the out-of-band closer for CP-2c's named gap: a TRANSIENT
Console failure during signup leaves an org live on the TENANT plane but never
mirrored to the Customer Console, un-metered until reconciled, with step 0a's
``AlreadyMember`` short-circuit blocking any literal route resubmit. This suite
proves one ``reconcile`` pass finds those orgs, reconstructs the Console call from
tenant-plane data alone, re-drives the SAME create-only-guarded arm, and marks
what it mirrors — and that it can neither re-drive a mirrored org nor hijack one
owned by a stranger.

⚠️ **R8 on BOTH ladders, 0 skips.** Every clause runs against a REAL replayed
tenant ladder (the ``organization`` row, its owner join, migration 181's marker +
GST columns) AND the REAL Customer Console app over an in-process ASGI transport
(the deployment-key ``/orgs/provision`` arm on its own replayed ladder, so the
Console row genuinely appears and the create-only guard genuinely refuses). The
DB gate's reason names BOTH ladder variables — the exact substrings
``pr-check.yml``'s skip-guard greps for — so a disarmed gate fails the job rather
than passing green. A skip is not a pass.

Run::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant
    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_signup_reconciler.py -q -rs
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
    ConsoleProvisionUnavailable,
    ReconcileSummary,
    provision_org_on_console,
    reconcile,
)
from acb_auth.roles import UserContext, UserRole
from acb_common.provisioning import (
    mark_console_mirrored,
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

# A valid GSTIN + the registered state signup persists (customer_console.md CP-2c
# item 4). The reconciler reconstructs BOTH from the tenant org row.
VALID_GSTIN = "27AAPFU0939F1ZV"
STATE = "KA"

_ROOT = Path(__file__).resolve().parents[2]


def _person(email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE)


class _Req:
    """The one thing ``provision_signup`` reads off the request: ``json()``."""

    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


# ── The hand-list defends itself (no database; must always run) ──────────────

class TestThisSuiteIsRegistered:
    """A two-ladder R8 suite whose DB-gated classes SKIP without a database — so
    a suite absent from pr-check.yml's skip-guard would skip there silently and
    leave the job green. The §7 standing rule requires it in BOTH the workflow
    and the owning spec's verify block, in the same PR."""

    def test_named_in_the_ci_skip_guard(self):
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_signup_reconciler.py" in workflow

    def test_named_in_the_owning_spec_verify_block(self):
        spec = (_ROOT / "project-docs/specs/customer_console.md").read_text(
            encoding="utf-8")
        assert "tests/unit/test_signup_reconciler.py" in spec


# ── DB gates (the pr-check skip-guard greps for these exact substrings) ───────

_TENANT_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_TENANT_URL = (
    _TENANT_SNAPSHOT
    if _TENANT_SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()
_CONSOLE_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

#: One combined gate — CP-2e is R8 on BOTH ladders. The reason names BOTH
#: variables so that whenever it skips (either unset), the ``-rs`` output carries
#: both substrings and pr-check.yml's two greps each fire; a disarmed gate fails
#: the job rather than passing green.
_R8 = pytest.mark.skipif(
    not (_TENANT_URL and _CONSOLE_URL),
    reason=(
        "TENANT_LADDER_DATABASE_URL unset or CUSTOMER_CONSOLE_DATABASE_URL "
        "unset — CP-2e is R8 on BOTH ladders (tenant plane + Console plane). A "
        "skip here is not a pass; CI must set both."
    ),
)


# ── Ship-dark: the whole thing is inert unwired (no database, always runs) ────

class TestUnwiredIsANoOp:
    """Ship-dark, fail-closed. With the box unwired, ``reconcile`` is a logged
    no-op that builds NO HTTP client and touches neither plane — which is what
    makes the ``scripts/`` CLI inert until the owner deploys the Console and wires
    a ``{provision}`` key (§8 gates 2 + 8)."""

    async def test_reconcile_unwired_returns_empty_and_never_calls_the_console(
        self, monkeypatch
    ):
        from acb_common.settings import get_settings

        monkeypatch.delenv("CUSTOMER_CONSOLE_URL", raising=False)
        monkeypatch.delenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", raising=False)
        get_settings.cache_clear()

        def _boom():
            raise AssertionError("no HTTP client may be built when unwired")

        monkeypatch.setattr(
            "acb_auth.console_resolve._new_http_client", _boom
        )
        try:
            summary = await reconcile()
        finally:
            get_settings.cache_clear()

        assert summary == ReconcileSummary()
        assert summary.selected == 0 and summary.mirrored == 0


# ── The two ladders ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def tenant_db():
    if not _TENANT_URL:
        yield None
        return
    eng = create_engine(_TENANT_URL, future=True)
    # Applied twice: every migration is idempotent, and a second pass proves it
    # against a real server (R8) rather than against our reading of the DDL.
    with eng.begin() as conn:
        apply_tenant_ladder(conn)
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


# ── Wiring the ONE Console client to the REAL Console app over ASGI ──────────

class _CountingASGITransport(httpx.ASGITransport):
    """An ASGI transport that counts the requests it dispatches.

    This is the ``console_resolve._new_http_client`` seam the spec names for the
    request-count assertions (done-when 2): a reconcile pass that selects zero
    rows makes ZERO Console calls, and ``count`` is how that is proved. It routes
    to the REAL Customer Console app, so the same transport also proves the
    Console org row genuinely appears (done-when 1) and the create-only guard
    genuinely refuses a hijack (done-when 4)."""

    def __init__(self, app):
        super().__init__(app=app)
        self.count = 0

    async def handle_async_request(self, request):
        self.count += 1
        return await super().handle_async_request(request)


class _Wired:
    def __init__(self, transport: _CountingASGITransport):
        self.transport = transport

    @property
    def count(self) -> int:
        return self.transport.count

    def reset(self) -> None:
        self.transport.count = 0


@pytest.fixture
def wired(console_db, monkeypatch):
    """Wire ``provision_org_on_console`` to the REAL Console app over ASGI.

    Mints a ``{resolve, provision}`` deployment key on a fresh box (fixture-only;
    issuing a real one is §8 gate 8 — this is a scratch DB, not that act), sets
    the box's URL/key so ``is_wired()`` is True, and replaces the module's HTTP
    client factory with a counting ASGI transport to the Console app. The Console
    writes to its OWN engine (``CUSTOMER_CONSOLE_DATABASE_URL``) — a different DB
    from the tenant plane the reconciler reads — so the two planes never alias.
    """
    from acb_common.settings import get_settings

    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", "op-token")
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", "internal")
    from customer_console import auth
    from customer_console.main import app as console_app

    label = f"cp2e-{uuid.uuid4().hex[:8]}"
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

    def _factory():
        # A fresh client each call (as the real factory does), but the SAME
        # transport instance, so `count` accumulates across provisions.
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(
        "acb_auth.console_resolve._new_http_client", _factory
    )
    yield _Wired(transport)
    get_settings.cache_clear()


@pytest.fixture
def clean_sweep(tenant_db):
    """Stamp every existing non-first-party org mirrored so the sweep starts
    empty for THIS test.

    The module-scoped ``tenant_db`` is shared across tests and ``reconcile``
    sweeps ALL unmirrored non-first-party orgs — so without this a leftover from
    another test (e.g. the hijack test's deliberately-unmirrored org) would be
    swept here and break the request-count and ``selected`` assertions. Uses the
    sync engine directly, so it never disturbs the async shared engine the
    reconciler binds inside ``tenant_engine_scope``."""
    if tenant_db is None:
        yield
        return
    with tenant_db.begin() as c:
        c.execute(text(
            "UPDATE organization SET console_mirrored_at = now() "
            "WHERE console_mirrored_at IS NULL AND first_party = false"
        ))
    yield


# ── Tenant-plane and Console-plane readers ───────────────────────────────────

def _tenant_marker(tenant_db, slug: str):
    with tenant_db.connect() as c:
        return c.execute(
            text("SELECT console_mirrored_at FROM organization "
                 " WHERE slug = :s"),
            {"s": slug},
        ).scalar_one_or_none()


def _tenant_profile(tenant_db, slug: str) -> dict | None:
    with tenant_db.connect() as c:
        row = c.execute(
            text("SELECT gstin, billing_state FROM organization "
                 " WHERE slug = :s"),
            {"s": slug},
        ).mappings().first()
    return dict(row) if row else None


def _console_org(console_db, slug: str) -> dict | None:
    with console_db.begin() as c:
        row = c.execute(
            text("SELECT gstin, billing_state FROM organization "
                 " WHERE slug = :s"),
            {"s": slug},
        ).mappings().first()
    return dict(row) if row else None


def _console_owner_emails(console_db, slug: str) -> list[str]:
    with console_db.begin() as c:
        return sorted(
            r[0]
            for r in c.execute(
                text(
                    "SELECT i.email FROM org_membership m "
                    "  JOIN organization o ON o.id = m.organization_id "
                    "  JOIN user_identity i ON i.id = m.user_identity_id "
                    " WHERE o.slug = :s AND m.role = 'owner'"
                ),
                {"s": slug},
            )
        )


def _new_org() -> tuple[str, str]:
    tag = uuid.uuid4().hex[:8]
    return f"cp2e-{tag}", f"owner-{tag}@acme.example"


# ══ done-when 1 · one pass mirrors and marks ═════════════════════════════════

@_R8
class TestOnePassMirrorsAndMarks:
    """Given an unmirrored, non-first-party tenant org with NO Console row, ONE
    pass provisions it on the Console with the RECONSTRUCTED tuple and stamps the
    marker. Red-first by asserting the Console row absent + marker NULL before and
    present + set after; the reconstructed ``gstin``/``billing_state`` are asserted
    to equal what signup persisted (proving the tenant-side persistence carried
    them, since the Console row is exactly what was missing)."""

    async def test_one_pass_mirrors_and_marks(
        self, tenant_db, console_db, wired, clean_sweep
    ):
        slug, owner = _new_org()
        async with tenant_engine_scope(_TENANT_URL):
            await provision_local_organization(slug, "Acme Inc", owner)
            await persist_org_billing_profile(
                slug, gstin=VALID_GSTIN, billing_state=STATE
            )

            # BEFORE: no Console row, marker NULL, no Console call yet.
            assert _tenant_marker(tenant_db, slug) is None
            assert _console_org(console_db, slug) is None
            assert wired.count == 0

            summary = await reconcile()

        # AFTER: exactly one mirror, the Console row exists on the slug, the
        # reconstructed GST fields landed, the marker is set.
        assert isinstance(summary, ReconcileSummary)
        assert summary.mirrored >= 1
        assert wired.count == 1
        assert _tenant_marker(tenant_db, slug) is not None
        assert _console_org(console_db, slug) == {
            "gstin": VALID_GSTIN, "billing_state": STATE,
        }
        assert _console_owner_emails(console_db, slug) == [owner]


# ══ done-when 2 · a second pass is a no-op ═══════════════════════════════════

@_R8
class TestSecondPassIsANoOp:
    """After clause 1 the marker excludes the org, so a second pass selects zero
    rows and makes NO Console call — asserted by the transport request count
    (the marker is the primary excluder; idempotent-on-slug is only the
    backstop)."""

    async def test_second_pass_selects_nothing_and_calls_the_console_zero_times(
        self, tenant_db, console_db, wired, clean_sweep
    ):
        slug, owner = _new_org()
        async with tenant_engine_scope(_TENANT_URL):
            await provision_local_organization(slug, "Acme Inc", owner)
            await persist_org_billing_profile(
                slug, gstin=VALID_GSTIN, billing_state=STATE
            )

            first = await reconcile()
            assert first.mirrored >= 1
            assert wired.count == 1

            wired.reset()               # zero the spy between the two passes
            second = await reconcile()

        assert second.selected == 0     # the marker excluded it
        assert second.mirrored == 0
        assert wired.count == 0          # NO Console call on pass 2
        assert _tenant_marker(tenant_db, slug) is not None


# ══ done-when 3 · a mirrored org is never re-driven ══════════════════════════

@_R8
class TestMarkedOrgIsNeverReDriven:
    """A row whose ``console_mirrored_at`` is already non-NULL is not selected and
    ``provision_org_on_console`` is never called for it — proved by marking an org
    WITHOUT ever provisioning it on the Console, then showing the sweep selects
    zero, calls the Console zero times, and leaves the Console row absent."""

    async def test_a_marked_org_is_not_selected_and_the_console_is_untouched(
        self, tenant_db, console_db, wired, clean_sweep
    ):
        slug, owner = _new_org()
        async with tenant_engine_scope(_TENANT_URL):
            await provision_local_organization(slug, "Acme Inc", owner)
            # Mark it mirrored WITHOUT any Console provision — the marker alone
            # must exclude it.
            assert await mark_console_mirrored(slug) is True
            assert _tenant_marker(tenant_db, slug) is not None

            summary = await reconcile()

        assert summary.selected == 0
        assert wired.count == 0
        assert _console_org(console_db, slug) is None   # never provisioned


# ══ done-when 4 · the reconciler cannot hijack ═══════════════════════════════

@_R8
class TestReconcilerCannotHijack:
    """The reconciler mints no second org/owner and moves no ownership. On the
    Console it re-drives the SAME create-only-guarded arm: seeded with the slug
    owned by a STRANGER, a forced pass is refused (``store.org_owned_by_other``,
    the Console twin of migration 180's guard), writes no second owner, and LEAVES
    THE MARKER NULL.

    ⚠️ Mutation-proved: disabling ``store.org_owned_by_other`` (making it return
    False) re-opens the hijack — the reconciler's owner would join the stranger's
    org and the marker would be stamped — and this test goes red. Demonstrated in
    the build's red-first evidence; the guard-ON contract is what is asserted
    here."""

    async def test_a_stranger_owned_console_slug_is_refused_marker_stays_null(
        self, tenant_db, console_db, wired, clean_sweep
    ):
        slug, tenant_owner = _new_org()
        stranger = f"stranger-{uuid.uuid4().hex[:8]}@x.example"

        # Seed the CONSOLE with the slug owned by a STRANGER, via the real seam.
        await provision_org_on_console(slug, "Stranger Co", stranger)
        assert _console_owner_emails(console_db, slug) == [stranger]

        # The tenant plane owns the SAME slug under a DIFFERENT identity.
        async with tenant_engine_scope(_TENANT_URL):
            await provision_local_organization(slug, "Acme Inc", tenant_owner)
            await persist_org_billing_profile(
                slug, gstin=VALID_GSTIN, billing_state=STATE
            )
            summary = await reconcile()

        # Refused: no mirror, marker NULL, and the Console still has ONLY the
        # stranger as owner — no second org, no moved ownership.
        assert summary.mirrored == 0
        assert summary.unavailable >= 1
        assert _console_owner_emails(console_db, slug) == [stranger]
        assert _tenant_marker(tenant_db, slug) is None


# ══ done-when 5 · signup marker on success; NULL on failure, then swept ══════

@_R8
class TestSignupSuccessSetsMarker:
    """A clean signup leaves NOTHING for the sweep: the route stamps the marker on
    step-2 success, so ``reconcile`` selects zero. Runs the REAL route end-to-end
    against both planes."""

    async def test_a_clean_signup_marks_and_the_sweep_finds_nothing(
        self, tenant_db, console_db, wired, clean_sweep, monkeypatch
    ):
        from acb_common.settings import get_settings

        monkeypatch.setenv("SELF_SERVE_SIGNUP_ENABLED", "true")
        get_settings.cache_clear()
        from gateway.routes import signup as route

        slug, owner = _new_org()
        async with tenant_engine_scope(_TENANT_URL):
            out = await route.provision_signup(
                _Req({"slug": slug, "display_name": "Acme Inc",
                      "registered_state": STATE, "gstin": VALID_GSTIN}),
                _person(owner),
            )
            assert out == {"admit": True, "code": None, "slug": slug}
            # The marker is set by the clean signup itself (step 2.5).
            assert _tenant_marker(tenant_db, slug) is not None

            summary = await reconcile()

        get_settings.cache_clear()
        assert summary.selected == 0    # nothing left for the sweep
        assert _console_org(console_db, slug) == {
            "gstin": VALID_GSTIN, "billing_state": STATE,
        }
        assert _console_owner_emails(console_db, slug) == [owner]


@_R8
class TestSignupConsoleFailureLeavesMarkerNull:
    """A signup whose step-2 raises ``ConsoleProvisionUnavailable`` leaves the
    marker NULL AND the ``gstin``/``billing_state`` persisted, so the sweep picks
    it up and re-drives faithfully — the end-to-end proof that the CP-2c dark
    window is closed.

    ⚠️ Red-first / mutation: removing the route's step-2 marker write leaves a
    CLEAN signup reading NULL too, so the sweep would re-drive an already-mirrored
    org (the marker would no longer distinguish success from failure). Here the
    marker is proved NULL after a FAILED step 2 and non-NULL only after the sweep
    re-drives."""

    async def test_a_step2_failure_leaves_marker_null_and_the_sweep_redrives(
        self, tenant_db, console_db, wired, clean_sweep, monkeypatch
    ):
        from acb_common.settings import get_settings

        monkeypatch.setenv("SELF_SERVE_SIGNUP_ENABLED", "true")
        get_settings.cache_clear()
        from gateway.routes import signup as route

        slug, owner = _new_org()

        # Force step 2 to fail transiently — the ROUTE's Console reference only,
        # so `reconcile` (which calls console_resolve's own binding, wired to the
        # real Console) still re-drives for real.
        async def _raise(*_a, **_k):
            raise ConsoleProvisionUnavailable("503")

        monkeypatch.setattr(route, "provision_org_on_console", _raise)

        async with tenant_engine_scope(_TENANT_URL):
            out = await route.provision_signup(
                _Req({"slug": slug, "display_name": "Acme Inc",
                      "registered_state": STATE, "gstin": VALID_GSTIN}),
                _person(owner),
            )
            assert out == {"admit": False, "code": "ConsoleUnavailable"}

            # Marker NULL (step 2.5 not reached) BUT the GST profile persisted
            # (step 1.5 ran) — exactly the rows the sweep needs.
            assert _tenant_marker(tenant_db, slug) is None
            assert _tenant_profile(tenant_db, slug) == {
                "gstin": VALID_GSTIN, "billing_state": STATE,
            }

            # The sweep picks it up and re-drives faithfully against the real
            # Console.
            summary = await reconcile()

        get_settings.cache_clear()
        assert summary.mirrored >= 1
        assert _tenant_marker(tenant_db, slug) is not None
        assert _console_org(console_db, slug) == {
            "gstin": VALID_GSTIN, "billing_state": STATE,
        }
        assert _console_owner_emails(console_db, slug) == [owner]
