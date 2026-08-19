"""CP-2c slice 2 — ``POST /signup/provision``: the self-serve signup route.

Spec: ``project-docs/specs/customer_console.md`` §6 CP-2c (items 3-7,
done-when 2-5) · ``user_management_contract.md`` R11 · MT-1j slice 7 (the tenant
create-only guard the route relies on).

Three layers, each with its own reason for existing:

* **The outcome/shape layer (no database).** The four outcome codes
  (``SignupDisabled`` / ``ConsoleUnavailable`` / ``AlreadyMember`` /
  ``SlugTaken``) and the two 400 shape classes (a body that asserts a
  tenant/identity; a missing/invalid GST field) are branch logic over faked
  collaborators, so the route's mapping is pinned deterministically and every
  auth/tenancy clause is mutation-testable. Uses a ``TestClient`` mounted the
  way ``gateway/main.py`` mounts it, in ``test_signin_resolve_route.py``'s
  idiom.

* **The two-org tenant layer (R8, tenant ladder).** ``AlreadyMember`` names
  only the caller's OWN org; ``SlugTaken`` names nothing; the step-1 typed
  raises are the TOCTOU-safe backstop and write nothing. Driven through the
  route handler against a real replayed tenant ladder.

* **The Console layer (R8, Console ladder).** The client threads
  ``gstin``/``billing_state`` and NO ``deployment_label`` (the deployment-key
  arm shape, R11); those values LAND on the Console org row; both planes are
  idempotent on the slug.

⚠️ **R8 on BOTH ladders, 0 skips.** The tenant classes gate on
``TENANT_LADDER_DATABASE_URL`` and the Console classes on
``CUSTOMER_CONSOLE_DATABASE_URL`` — the exact strings ``pr-check.yml``'s
skip-guard greps for, so a disarmed gate fails the job rather than passing
green. A skip is not a pass.

Run::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant
    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_signup_provision_route.py -q -rs
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

import httpx
from acb_auth import get_current_user
from acb_auth.console_resolve import ConsoleProvisionUnavailable
from acb_auth.deps import require_authenticated
from acb_auth.roles import UserContext, UserRole
from acb_common.provisioning import (
    OwnerBelongsElsewhere,
    SlugOwnedByAnother,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes import signup as route
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

# A valid GSTIN and a structurally broken one (customer_console.md CP-2c item 4).
VALID_GSTIN = "27AAPFU0939F1ZV"
BAD_GSTIN = "not-a-gstin"
STATE = "KA"


def _person(email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE)


ANON = UserContext(email=None, role=UserRole.EMPLOYEE)


_ROOT = Path(__file__).resolve().parents[2]


# ── The hand-list defends itself (no database; must always run) ──────────────

class TestThisSuiteIsRegistered:
    """This is a two-ladder R8 suite, so a run without a database SKIPS its
    DB-gated classes — and a suite absent from pr-check.yml's skip-guard would
    skip there silently and leave the job green. The §7 standing rule requires
    it in BOTH the workflow and the spec verify block, in the same PR."""

    def test_named_in_the_ci_skip_guard(self):
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_signup_provision_route.py" in workflow

    def test_named_in_the_owning_spec_verify_block(self):
        spec = (_ROOT / "project-docs/specs/customer_console.md").read_text(
            encoding="utf-8")
        assert "tests/unit/test_signup_provision_route.py" in spec


# ── DB gates (the pr-check skip-guard greps for these exact substrings) ───────

_TENANT_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_TENANT_URL = (
    _TENANT_SNAPSHOT
    if _TENANT_SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()
_CONSOLE_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

_TENANT_GATE = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it."
    ),
)
_CONSOLE_GATE = pytest.mark.skipif(
    not _CONSOLE_URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)


# ── Fakes for the outcome/shape layer ────────────────────────────────────────

def _areturn(value):
    async def _f(*_a, **_k):
        return value
    return _f


def _araise(exc):
    async def _f(*_a, **_k):
        raise exc
    return _f


def _forbid_console(monkeypatch):
    """Assert the Console step is NEVER reached (pre-flight short-circuited)."""
    async def _boom(*_a, **_k):
        raise AssertionError("the Console plane must not be reached here")
    monkeypatch.setattr(route, "provision_org_on_console", _boom)


class _CaptureConsole:
    """A fake Console client that records the arguments it was called with."""

    def __init__(self, result=None, exc=None):
        self.result = result if result is not None else {"organization_id": "x"}
        self.exc = exc
        self.calls: list[tuple] = []

    async def __call__(self, slug, name, owner_email, *, gstin=None,
                       billing_state=None):
        self.calls.append((slug, name, owner_email, gstin, billing_state))
        if self.exc is not None:
            raise self.exc
        return self.result


def _client(user: UserContext = None) -> TestClient:
    app = FastAPI(dependencies=[require_authenticated(public=())])
    app.include_router(route.router)
    app.dependency_overrides[get_current_user] = lambda: (
        user or _person("ada@customer.example")
    )
    return TestClient(app)


@pytest.fixture
def flag_on(monkeypatch):
    from acb_common.settings import get_settings

    monkeypatch.setenv("SELF_SERVE_SIGNUP_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def flag_off(monkeypatch):
    from acb_common.settings import get_settings

    monkeypatch.delenv("SELF_SERVE_SIGNUP_ENABLED", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _body(**extra):
    body = {"slug": "acme", "display_name": "Acme Inc",
            "registered_state": STATE}
    body.update(extra)
    return body


# ══ done-when 2 · every outcome code + both 400 shape classes ════════════════

class TestTheOutcomeCodes:
    """The four 200 outcome refusals and the success shape.

    Fakes the tenant reads and the Console client so each branch is reached
    deterministically — the mapping is the subject, not the I/O.
    """

    def test_the_flag_off_answers_SignupDisabled_and_creates_nothing(
        self, flag_off, monkeypatch
    ):
        """Mutation: delete the flag check and this goes green on the admit path
        instead — the fail-closed gate is the whole point of the code."""
        _forbid_console(monkeypatch)
        # If the flag were (wrongly) ignored, these fakes would run; the
        # AssertionError console proves the route returned before step 2.
        monkeypatch.setattr(route, "membership_of", _areturn(None))
        monkeypatch.setattr(route, "org_owner_of", _areturn(None))

        r = _client().post("/signup/provision", json=_body())

        assert r.status_code == 200
        assert r.json() == {"admit": False, "code": "SignupDisabled"}

    def test_already_a_member_answers_AlreadyMember_with_the_own_org_name(
        self, flag_on, monkeypatch
    ):
        _forbid_console(monkeypatch)
        monkeypatch.setattr(
            route, "membership_of", _areturn(("acme", "Acme Inc"))
        )

        r = _client().post("/signup/provision", json=_body())

        assert r.status_code == 200
        assert r.json() == {
            "admit": False, "code": "AlreadyMember", "org_name": "Acme Inc",
        }

    def test_a_slug_owned_by_another_answers_SlugTaken_naming_nothing(
        self, flag_on, monkeypatch
    ):
        _forbid_console(monkeypatch)
        monkeypatch.setattr(route, "membership_of", _areturn(None))
        monkeypatch.setattr(
            route, "org_owner_of", _areturn("someone-else@x.example")
        )

        r = _client().post("/signup/provision", json=_body())

        assert r.status_code == 200
        assert r.json() == {"admit": False, "code": "SlugTaken"}

    def test_a_transient_Console_failure_answers_ConsoleUnavailable(
        self, flag_on, monkeypatch
    ):
        monkeypatch.setattr(route, "membership_of", _areturn(None))
        monkeypatch.setattr(route, "org_owner_of", _areturn(None))
        monkeypatch.setattr(
            route, "provision_local_organization", _areturn("org-id")
        )
        monkeypatch.setattr(
            route, "provision_org_on_console",
            _araise(ConsoleProvisionUnavailable("503")),
        )

        r = _client().post("/signup/provision", json=_body())

        assert r.status_code == 200
        assert r.json() == {"admit": False, "code": "ConsoleUnavailable"}

    def test_the_happy_path_admits(self, flag_on, monkeypatch):
        monkeypatch.setattr(route, "membership_of", _areturn(None))
        monkeypatch.setattr(route, "org_owner_of", _areturn(None))
        monkeypatch.setattr(
            route, "provision_local_organization", _areturn("org-id")
        )
        monkeypatch.setattr(
            route, "provision_org_on_console",
            _areturn({"organization_id": "cid", "slug": "acme"}),
        )

        r = _client().post("/signup/provision", json=_body())

        assert r.status_code == 200
        assert r.json() == {"admit": True, "code": None, "slug": "acme"}


class TestStepOnesTypedRaisesAreTheBackstop:
    """The TOCTOU-safe backstop: a signup that raced its own pre-flight is
    mapped to the SAME codes, never a raw-prose parse.

    ⚠️ **The org_name-on-backstop clarification, ruled by the auditor and
    resolved here.** On ``OwnerBelongsElsewhere``, ``membership_of`` is re-read
    to populate ``org_name`` so the wire shape matches the 0a path; if the
    racing write landed elsewhere (re-read → None), ``org_name`` is OMITTED.
    """

    def test_OwnerBelongsElsewhere_maps_to_AlreadyMember_with_reread_name(
        self, flag_on, monkeypatch
    ):
        """Backstop re-read returns the org → ``org_name`` present.

        ``membership_of`` returns None at 0a (nothing pre-flighted) and the org
        on the backstop re-read — the racing write landed in the caller's own
        org between the two reads.

        Mutation: map ``OwnerBelongsElsewhere`` to ``SlugTaken`` and this goes
        red; drop the backstop re-read and ``org_name`` disappears.
        """
        _forbid_console(monkeypatch)
        calls = {"n": 0}

        async def _membership(_email):
            calls["n"] += 1
            return None if calls["n"] == 1 else ("acme", "Acme Inc")

        monkeypatch.setattr(route, "membership_of", _membership)
        monkeypatch.setattr(route, "org_owner_of", _areturn(None))
        monkeypatch.setattr(
            route, "provision_local_organization",
            _araise(OwnerBelongsElsewhere("raced")),
        )

        r = _client().post("/signup/provision", json=_body())

        assert r.json() == {
            "admit": False, "code": "AlreadyMember", "org_name": "Acme Inc",
        }

    def test_OwnerBelongsElsewhere_omits_org_name_when_reread_is_None(
        self, flag_on, monkeypatch
    ):
        """The racing write landed ELSEWHERE — re-read is None, so ``org_name``
        is omitted rather than invented."""
        _forbid_console(monkeypatch)
        monkeypatch.setattr(route, "membership_of", _areturn(None))
        monkeypatch.setattr(route, "org_owner_of", _areturn(None))
        monkeypatch.setattr(
            route, "provision_local_organization",
            _araise(OwnerBelongsElsewhere("raced")),
        )

        r = _client().post("/signup/provision", json=_body())

        assert r.json() == {"admit": False, "code": "AlreadyMember"}
        assert "org_name" not in r.json()

    def test_SlugOwnedByAnother_maps_to_SlugTaken(self, flag_on, monkeypatch):
        """Mutation: map ``SlugOwnedByAnother`` to ``ConsoleUnavailable`` and
        this goes red."""
        _forbid_console(monkeypatch)
        monkeypatch.setattr(route, "membership_of", _areturn(None))
        monkeypatch.setattr(route, "org_owner_of", _areturn(None))
        monkeypatch.setattr(
            route, "provision_local_organization",
            _araise(SlugOwnedByAnother("raced")),
        )

        r = _client().post("/signup/provision", json=_body())

        assert r.json() == {"admit": False, "code": "SlugTaken"}

    def test_any_other_raise_is_ConsoleUnavailable_never_a_false_SlugTaken(
        self, flag_on, monkeypatch
    ):
        """A raise that is NEITHER typed cause is a genuine error → the
        transient code, never a false ``SlugTaken``."""
        _forbid_console(monkeypatch)
        monkeypatch.setattr(route, "membership_of", _areturn(None))
        monkeypatch.setattr(route, "org_owner_of", _areturn(None))
        monkeypatch.setattr(
            route, "provision_local_organization",
            _araise(RuntimeError("connection reset")),
        )

        r = _client().post("/signup/provision", json=_body())

        assert r.json() == {"admit": False, "code": "ConsoleUnavailable"}


class TestTheShapeClasses:
    """400 for shape violations — R11 body claims and the GST fields."""

    @pytest.mark.parametrize("key", ["email", "org", "deployment_label"])
    def test_a_body_that_asserts_a_tenant_or_identity_is_400(
        self, flag_on, monkeypatch, key
    ):
        """R11, refused on SHAPE before any value is read.

        Mutation: make the route trust ``raw['email']`` for the owner and this
        400 becomes a 200 — the identity would be taken from the body.
        """
        _forbid_console(monkeypatch)
        monkeypatch.setattr(route, "membership_of", _areturn(None))
        monkeypatch.setattr(route, "org_owner_of", _areturn(None))

        r = _client().post("/signup/provision", json=_body(**{key: "x"}))

        assert r.status_code == 400
        assert r.json()["code"] == "InvalidBody"

    def test_a_missing_registered_state_is_400_MissingState(self, flag_on):
        body = _body()
        del body["registered_state"]
        r = _client().post("/signup/provision", json=body)
        assert r.status_code == 400
        assert r.json()["code"] == "MissingState"

    def test_a_blank_registered_state_is_400_MissingState(self, flag_on):
        r = _client().post("/signup/provision", json=_body(registered_state=""))
        assert r.status_code == 400
        assert r.json()["code"] == "MissingState"

    def test_a_structurally_invalid_gstin_is_400_InvalidGstin(self, flag_on):
        r = _client().post("/signup/provision", json=_body(gstin=BAD_GSTIN))
        assert r.status_code == 400
        assert r.json()["code"] == "InvalidGstin"

    def test_a_valid_gstin_passes_the_shape_check(self, flag_on, monkeypatch):
        """A well-formed GSTIN is NOT a 400 — it reaches the planes."""
        monkeypatch.setattr(route, "membership_of", _areturn(None))
        monkeypatch.setattr(route, "org_owner_of", _areturn(None))
        monkeypatch.setattr(
            route, "provision_local_organization", _areturn("org-id")
        )
        capture = _CaptureConsole(result={"organization_id": "c", "slug": "acme"})
        monkeypatch.setattr(route, "provision_org_on_console", capture)

        r = _client().post("/signup/provision", json=_body(gstin=VALID_GSTIN))

        assert r.json()["admit"] is True
        assert capture.calls[0][3] == VALID_GSTIN  # gstin threaded through


class TestThePosture:
    def test_an_anonymous_caller_never_reaches_the_handler(self, flag_on):
        r = _client(ANON).post("/signup/provision", json=_body())
        assert r.status_code == 401

    def test_the_owner_is_the_session_email_never_the_body(
        self, flag_on, monkeypatch
    ):
        """R11: the owner threaded to the tenant plane is the SESSION email.

        Mutation: source the owner from ``raw['email']`` (were it not refused at
        400) and this assertion goes red.
        """
        seen = {}

        async def _provision(slug, display_name=None, *, owner_email=None):
            seen["owner"] = owner_email
            return "org-id"

        monkeypatch.setattr(route, "membership_of", _areturn(None))
        monkeypatch.setattr(route, "org_owner_of", _areturn(None))
        monkeypatch.setattr(route, "provision_local_organization", _provision)
        monkeypatch.setattr(
            route, "provision_org_on_console",
            _areturn({"organization_id": "c", "slug": "acme"}),
        )

        _client(_person("owner@self.example")).post(
            "/signup/provision", json=_body()
        )

        assert seen["owner"] == "owner@self.example"


# ══ The Console-provision CLIENT — shape of the outbound request (no DB) ══════

class TestTheConsoleProvisionClient:
    """``console_resolve.provision_org_on_console`` — the ONE Console client.

    Driven with an ``httpx.MockTransport`` so the request it BUILDS is the
    subject: the deployment-key arm (no ``deployment_label``), the GST fields
    threaded, the bearer, and the transient-failure classification.
    """

    @pytest.fixture
    def wired(self, monkeypatch):
        from acb_common.settings import get_settings

        monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "https://console.invalid")
        monkeypatch.setenv(
            "CUSTOMER_CONSOLE_DEPLOYMENT_KEY", "cc_depl_fixture_secret"
        )
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def _mock(self, monkeypatch, handler):
        def _new_client():
            return httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(
            "acb_auth.console_resolve._new_http_client", _new_client
        )

    async def test_it_posts_the_deployment_key_arm_with_no_deployment_label(
        self, wired, monkeypatch
    ):
        from acb_auth.console_resolve import provision_org_on_console

        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            import json as _json
            seen["body"] = _json.loads(request.content)
            return httpx.Response(200, json={"organization_id": "c", "slug": "s"})

        self._mock(monkeypatch, handler)

        out = await provision_org_on_console(
            "acme", "Acme Inc", "o@acme.example",
            gstin=VALID_GSTIN, billing_state=STATE,
        )

        assert out == {"organization_id": "c", "slug": "s"}
        assert seen["url"].endswith("/orgs/provision")
        assert seen["auth"] == "Bearer cc_depl_fixture_secret"
        assert seen["body"] == {
            "slug": "acme", "name": "Acme Inc", "owner_email": "o@acme.example",
            "gstin": VALID_GSTIN, "billing_state": STATE,
        }
        # R11 — the key IS the deployment, so the body never names one.
        assert "deployment_label" not in seen["body"]

    async def test_a_5xx_becomes_ConsoleProvisionUnavailable(
        self, wired, monkeypatch
    ):
        from acb_auth.console_resolve import provision_org_on_console

        self._mock(monkeypatch, lambda req: httpx.Response(503))
        with pytest.raises(ConsoleProvisionUnavailable):
            await provision_org_on_console("s", "N", "o@x.example")

    async def test_a_network_error_becomes_ConsoleProvisionUnavailable(
        self, wired, monkeypatch
    ):
        from acb_auth.console_resolve import provision_org_on_console

        def handler(_req):
            raise httpx.ConnectError("refused")
        self._mock(monkeypatch, handler)
        with pytest.raises(ConsoleProvisionUnavailable):
            await provision_org_on_console("s", "N", "o@x.example")

    async def test_an_unwired_box_raises_without_a_hop(self, monkeypatch):
        """No URL/key ⇒ ``ConsoleProvisionUnavailable`` with no HTTP call."""
        from acb_auth.console_resolve import provision_org_on_console
        from acb_common.settings import get_settings

        monkeypatch.delenv("CUSTOMER_CONSOLE_URL", raising=False)
        monkeypatch.delenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", raising=False)
        get_settings.cache_clear()

        def _boom():
            raise AssertionError("no client should be built when unwired")
        monkeypatch.setattr(
            "acb_auth.console_resolve._new_http_client", _boom
        )
        try:
            with pytest.raises(ConsoleProvisionUnavailable):
                await provision_org_on_console("s", "N", "o@x.example")
        finally:
            get_settings.cache_clear()


# ══ done-when 3 · the two-org classification on the TENANT plane (R8) ═════════

@pytest.fixture(scope="module")
def tenant_db():
    if not _TENANT_URL:
        yield None
        return
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_tenant_ladder(conn)
    with eng.begin() as conn:
        apply_tenant_ladder(conn)
    yield eng
    eng.dispose()


def _owner_emails(conn, slug: str) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            text(
                "SELECT u.email FROM user_role ur "
                "  JOIN org_role r ON r.id = ur.role_id AND r.slug = 'owner' "
                "  JOIN app_user u ON u.id = ur.user_id "
                "  JOIN organization o ON o.id = r.organization_id "
                " WHERE o.slug = :s"
            ),
            {"s": slug},
        )
    }


def _app_user_count(conn, email: str) -> int:
    return conn.execute(
        text("SELECT count(*) FROM app_user WHERE lower(email) = :e"),
        {"e": email.lower()},
    ).scalar_one()


def _org_count(conn, slug: str) -> int:
    return conn.execute(
        text("SELECT count(*) FROM organization WHERE slug = :s"),
        {"s": slug},
    ).scalar_one()


class _Req:
    """The one thing ``provision_signup`` reads off the request: ``json()``."""

    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


@_TENANT_GATE
class TestTheTwoOrgClassification:
    """done-when 3: ``AlreadyMember`` names only the caller's OWN org;
    ``SlugTaken`` names nothing; neither leaks third-party existence.

    Driven through the route handler against the real tenant ladder — the
    reads (``membership_of`` / ``org_owner_of``) run against the schema the
    ladder produces, which no fake can answer for.
    """

    async def test_own_membership_is_AlreadyMember_and_a_stranger_gets_SlugTaken(
        self, tenant_db, flag_on, monkeypatch
    ):
        # The Console must NEVER be reached — both refusals classify on the
        # tenant plane before any write.
        async def _boom(*_a, **_k):
            raise AssertionError("Console reached on a pre-flight refusal")
        monkeypatch.setattr(route, "provision_org_on_console", _boom)

        slug = f"cp2c3-{uuid.uuid4().hex[:8]}"
        alice = f"alice-{uuid.uuid4().hex[:8]}@a.example"
        carol = f"carol-{uuid.uuid4().hex[:8]}@c.example"

        async with tenant_engine_scope(_TENANT_URL):
            from acb_common.provisioning import provision_local_organization

            await provision_local_organization(slug, "Acme Inc", alice)

            # A — alice is already a member: names her OWN org, nothing else.
            own = await route.provision_signup(
                _Req({"slug": slug, "display_name": "Whatever",
                      "registered_state": STATE}),
                _person(alice),
            )
            # C — a stranger requests alice's slug: SlugTaken, naming nothing.
            stranger = await route.provision_signup(
                _Req({"slug": slug, "display_name": "Mine",
                      "registered_state": STATE}),
                _person(carol),
            )

        assert own == {
            "admit": False, "code": "AlreadyMember", "org_name": "Acme Inc",
        }
        assert stranger == {"admit": False, "code": "SlugTaken"}

        with tenant_db.connect() as c:
            # Nothing was written for the stranger — the pre-flight caught it.
            assert _owner_emails(c, slug) == {alice}
            assert _app_user_count(c, carol) == 0


@_TENANT_GATE
class TestTheStepOneBackstopWritesNothing:
    """done-when 5(b): a PERMANENT refusal leaves nothing on the tenant plane.

    Forces slice 7's typed raise on step 1 by monkeypatching the pre-flight
    read to ``None`` — the deterministic stand-in for the TOCTOU race the
    backstop exists for — and asserts the refused act wrote nothing.
    Parametrised over BOTH directions.
    """

    async def test_slug_owned_by_another_refuses_and_writes_nothing(
        self, tenant_db, flag_on, monkeypatch
    ):
        async def _boom(*_a, **_k):
            raise AssertionError("Console reached on a permanent refusal")
        monkeypatch.setattr(route, "provision_org_on_console", _boom)
        # Pre-flight MISSES (race); step 1's real guard is the backstop.
        monkeypatch.setattr(route, "membership_of", _areturn(None))
        monkeypatch.setattr(route, "org_owner_of", _areturn(None))

        slug = f"cp2c5a-{uuid.uuid4().hex[:8]}"
        alice = f"alice-{uuid.uuid4().hex[:8]}@a.example"
        carol = f"carol-{uuid.uuid4().hex[:8]}@c.example"

        async with tenant_engine_scope(_TENANT_URL):
            from acb_common.provisioning import provision_local_organization

            await provision_local_organization(slug, "Acme Inc", alice)
            out = await route.provision_signup(
                _Req({"slug": slug, "display_name": "Mine",
                      "registered_state": STATE}),
                _person(carol),
            )

        assert out == {"admit": False, "code": "SlugTaken"}
        with tenant_db.connect() as c:
            assert _owner_emails(c, slug) == {alice}   # unchanged
            assert _app_user_count(c, carol) == 0      # no new tenant owner

    async def test_email_elsewhere_refuses_and_the_new_org_is_rolled_back(
        self, tenant_db, flag_on, monkeypatch
    ):
        async def _boom(*_a, **_k):
            raise AssertionError("Console reached on a permanent refusal")
        monkeypatch.setattr(route, "provision_org_on_console", _boom)
        monkeypatch.setattr(route, "membership_of", _areturn(None))
        monkeypatch.setattr(route, "org_owner_of", _areturn(None))

        home = f"cp2c5b-home-{uuid.uuid4().hex[:8]}"
        newco = f"cp2c5b-new-{uuid.uuid4().hex[:8]}"
        bob = f"bob-{uuid.uuid4().hex[:8]}@b.example"

        async with tenant_engine_scope(_TENANT_URL):
            from acb_common.provisioning import provision_local_organization

            await provision_local_organization(home, "Bob Home", bob)
            out = await route.provision_signup(
                _Req({"slug": newco, "display_name": "New Co",
                      "registered_state": STATE}),
                _person(bob),
            )

        assert out["code"] == "AlreadyMember"
        with tenant_db.connect() as c:
            assert _org_count(c, newco) == 0           # rolled back
            assert _owner_emails(c, home) == {bob}     # still only his home


@_TENANT_GATE
class TestTheTenantPlaneConverges:
    """done-when 5(a), tenant half: a transient Console failure leaves the
    tenant org created, and a resubmit converges (idempotent on the slug)."""

    async def test_a_transient_console_failure_leaves_the_tenant_org_and_converges(
        self, tenant_db, flag_on, monkeypatch
    ):
        # Step 2 fails transiently AFTER the tenant commit.
        monkeypatch.setattr(
            route, "provision_org_on_console",
            _araise(ConsoleProvisionUnavailable("503")),
        )

        slug = f"cp2c5conv-{uuid.uuid4().hex[:8]}"
        carol = f"carol-{uuid.uuid4().hex[:8]}@c.example"

        async with tenant_engine_scope(_TENANT_URL):
            from acb_common.provisioning import provision_local_organization

            out = await route.provision_signup(
                _Req({"slug": slug, "display_name": "Carol Co",
                      "registered_state": STATE}),
                _person(carol),
            )
            assert out == {"admit": False, "code": "ConsoleUnavailable"}

            # Resubmit — both planes are idempotent on the slug, so a re-run of
            # the tenant provision converges to ONE org / ONE owner.
            await provision_local_organization(slug, "Carol Co", carol)

        with tenant_db.connect() as c:
            assert _org_count(c, slug) == 1
            assert _owner_emails(c, slug) == {carol}


# ══ The Console ladder — GST landing + Console-plane idempotence (R8) ═════════

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


@pytest.fixture
def console_client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", "op-token")
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", "internal")
    from customer_console.main import app

    return TestClient(app)


@pytest.fixture
def wide_key(console_db):
    """A ``{resolve, provision}`` deployment key on a fresh box (fixture-only)."""
    from customer_console import auth

    label = f"cp2c-{uuid.uuid4().hex[:8]}"
    with console_db.begin() as c:
        dep = ensure_deployment(c, label=label)
        token = mint_deployment_key(
            c, deployment_id=dep,
            capabilities=[auth.RESOLVE_CAPABILITY, auth.PROVISION_CAPABILITY],
        )
    return {"label": label, "deployment_id": dep, "token": token}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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


@_CONSOLE_GATE
class TestGstLandsOnTheConsoleOrgRow:
    """done-when 4, Console half: the GST fields the client sends LAND on the
    Console org row (R8, the deployment-key arm on the real ladder)."""

    def test_gstin_and_billing_state_land_on_the_org_row(
        self, console_client, console_db, wide_key
    ):
        slug = f"gst-{uuid.uuid4().hex[:8]}"
        r = console_client.post(
            "/orgs/provision", headers=_bearer(wide_key["token"]),
            json={"slug": slug, "name": "GST Co",
                  "owner_email": f"o@{slug}.example",
                  "gstin": VALID_GSTIN, "billing_state": STATE},
        )
        assert r.status_code == 200, r.text
        assert _console_org(console_db, slug) == {
            "gstin": VALID_GSTIN, "billing_state": STATE,
        }


@_CONSOLE_GATE
class TestTheConsolePlaneConverges:
    """done-when 5(a), Console half: the deployment-key arm is idempotent on
    the slug — a resubmit converges to one org / one owner."""

    def test_reprovisioning_the_same_slug_converges_to_one_org_one_owner(
        self, console_client, console_db, wide_key
    ):
        slug = f"conv-{uuid.uuid4().hex[:8]}"
        owner = f"o@{slug}.example"
        body = {"slug": slug, "name": "Conv Co", "owner_email": owner}

        first = console_client.post(
            "/orgs/provision", headers=_bearer(wide_key["token"]), json=body)
        second = console_client.post(
            "/orgs/provision", headers=_bearer(wide_key["token"]), json=body)

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        with console_db.begin() as c:
            assert c.execute(
                text("SELECT count(*) FROM organization WHERE slug = :s"),
                {"s": slug},
            ).scalar_one() == 1
        assert _console_owner_emails(console_db, slug) == [owner]
