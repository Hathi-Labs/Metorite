"""CP-2d slice 2 — the email-OTP verification token, its limits, and its table.

Spec: ``project-docs/specs/customer_console.md`` §CP-2d clauses 7, 8, 11, 12 ·
owner answers **B1** and **B3** of 2026-08-23 · migration
``infra/postgres/186_auth_email_otp_token.sql`` ·
``packages/acb_auth/acb_auth/email_otp.py`` ·
``apps/services/gateway/gateway/routes/email_otp.py``.
R7 name: ``email-otp-token-limits``.

⚠️ **R8, and a hermetic version of this file would be worse than nothing.**
Every subject here is a SQL property: a ``SUM`` over a time window, an
``ON CONFLICT`` target, an advisory lock, three predicates in a ``WHERE``. A
Python fake agrees with whatever SQL it is handed — which is precisely how MT-1j
slice 6's ``ON CONFLICT (email)`` shipped green and took out the owner bootstrap.
The rate limiter is the **whole** defence a 6-digit code has (``@auth/core``
stores only a salted SHA-256 of it, so the database never holds the code), so
"the SQL was written" is not the question worth answering.

Two halves, and they fail at different times:

* **The routes** (``TestTheRouteShape``) — DB-free, the
  ``test_signin_resolve_route.py`` idiom: the router mounted the way
  ``gateway/main.py`` mounts it, under an app-wide ``require_authenticated`` and
  never in ``PUBLIC_ROUTES``, with the store stubbed. These assert the R11 shape
  rules, which have nothing to do with Postgres and would otherwise SKIP on a
  machine with no database — and a structural fence that skips is a fence that
  was never there.
* **The store** (the ``_DB_GATE`` classes) — a REAL Postgres with the tenant
  ladder replayed, driving ``acb_auth.email_otp``'s own call path.

⚠️ **The store tests COMMIT.** ``email_otp`` takes its own session from the
shared engine (``get_session_factory()``, R5(b) — deliberately not ``get_db()``,
whose site ratchet may only go down), so it cannot ride a rolled-back
connection. Every identifier therefore carries a uuid and rows stay in the
scratch ladder database; nothing here reads another test's rows, because every
query in the module under test is keyed on the identifier.

The gate is ``TENANT_LADDER_DATABASE_URL`` — **never** ``DATABASE_URL``, for the
reason ``test_org_provisioning.py`` records at length. A skip is not a pass.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant \\
        uv run pytest tests/unit/test_email_otp_token.py -v -rs
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from acb_auth import get_current_user
from acb_auth.deps import require_authenticated
from acb_auth.email_otp import (
    SEND_DUPLICATE,
    SEND_MAX_PER_WINDOW,
    SEND_RATE_LIMITED,
    VERIFY_MAX_ATTEMPTS,
    VERIFY_RATE_LIMITED,
    OtpRateLimited,
    canonical_identifier,
    consume_token,
    record_token,
    reserve_send,
)
from acb_auth.roles import UserContext, UserRole
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes import email_otp as route
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope

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
        "DATABASE_URL — see test_org_provisioning.py's docstring."
    ),
)

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "infra" / "postgres" / "186_auth_email_otp_token.sql"

PERSON = UserContext(email="ada@customer.example", role=UserRole.EMPLOYEE)
ANON = UserContext(email=None, role=UserRole.EMPLOYEE)


def _who(prefix: str) -> str:
    """A fresh identifier, so no test can be affected by another's counters."""
    return f"{prefix}-{uuid.uuid4().hex[:10]}@otp.example"


def _soon(seconds: int = 600) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)


# ==========================================================================
# The ROUTE shape — DB-free, so it never skips.
# ==========================================================================


def _client(user: UserContext = PERSON) -> TestClient:
    """The router mounted exactly as ``gateway/main.py`` mounts it."""
    app = FastAPI(dependencies=[require_authenticated(public=())])
    app.include_router(route.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


class TestTheRouteShape:
    """R11 and the posture, asserted on the routes rather than about them."""

    def test_the_routes_are_not_public(self) -> None:
        """Never in ``PUBLIC_ROUTES`` — root ``AGENTS.md`` constraint 10.

        Read from ``gateway.main`` rather than transcribed: a copied list would
        keep passing after somebody added an entry to the real one, which is the
        exact edit this forbids.
        """
        from gateway.main import PUBLIC_ROUTES

        for path in ("/signin/otp/send", "/signin/otp/token", "/signin/otp/consume"):
            assert path not in PUBLIC_ROUTES

    def test_a_body_that_names_an_address_is_refused_on_shape(self, monkeypatch) -> None:
        """R11: the address is the authenticated context's, never the body.

        Refused **before** any value is read, so naming a real address and
        naming a nonsense one are one refusal — the rule ``signup.py`` applies
        to ``email``/``org``/``deployment_label``.
        """
        called: list[str] = []

        async def _boom(*a, **k):  # pragma: no cover — must never run
            called.append("store")
            raise AssertionError("the store was reached past the shape gate")

        monkeypatch.setattr(route, "reserve_send", _boom)
        monkeypatch.setattr(route, "record_token", _boom)
        monkeypatch.setattr(route, "consume_token", _boom)

        client = _client()
        for path, body in (
            ("/signin/otp/send", {"expires": _soon().isoformat(), "email": "x@y.z"}),
            ("/signin/otp/token", {"token": "h", "expires": _soon().isoformat(),
                                   "identifier": "x@y.z"}),
            ("/signin/otp/consume", {"token": "h", "org": "acme"}),
        ):
            res = client.post(path, json=body)
            assert res.status_code == 400, path
            assert res.json()["code"] == route.INVALID_BODY
        assert called == []

    def test_a_caller_with_no_address_never_reaches_the_store(
        self, monkeypatch
    ) -> None:
        """Fail closed. An OTP act with no identifier has no budget to charge.

        ⚠️ Measured, and it changed what this asserts: the **app-wide**
        ``require_authenticated`` refuses an address-less caller with a 401
        before the handler runs, so the route's own ``NoIdentifier`` arm is not
        reachable through the mounted app at all. That is the right outcome and
        the honest assertion — pinning a 400 here would have been pinning a
        branch nothing can enter. The arm stays as defence in depth (the
        handler must not assume its guard), and it is exercised directly below
        rather than pretended at through HTTP.
        """

        async def _boom(*a, **k):  # pragma: no cover
            raise AssertionError("the store was reached with no identifier")

        monkeypatch.setattr(route, "reserve_send", _boom)
        res = _client(ANON).post(
            "/signin/otp/send", json={"expires": _soon().isoformat()}
        )
        assert res.status_code == 401

    def test_the_handler_refuses_an_empty_identifier_on_its_own(self) -> None:
        """Defence in depth for the arm the app-wide guard makes unreachable."""
        refusal = route._shape_refusal({"expires": _soon().isoformat()}, "")
        assert refusal is not None
        assert refusal.status_code == 400
        assert route.NO_IDENTIFIER in refusal.body.decode()

    def test_the_send_requires_the_expires_that_pairs_the_two_legs(self) -> None:
        """``expires`` is required, and it is not a convenience.

        ``(identifier, expires)`` is what makes ``@auth/core``'s two concurrent
        legs agree that they are ONE send. Accept a missing one and every send
        becomes two, and the hourly budget is wrong by a factor of two.
        """
        client = _client()
        assert client.post("/signin/otp/send", json={}).status_code == 400
        assert (
            client.post("/signin/otp/send", json={"expires": "not-a-date"}).status_code
            == 400
        )

    def test_it_parses_the_timestamp_JAVASCRIPT_actually_sends(
        self, monkeypatch
    ) -> None:
        """⚠️ The wire crosses a language boundary, so the FORMAT is the risk.

        The adapter sends ``Date.prototype.toISOString()``, which ends in a
        literal ``Z`` — and a Python parser that rejects it 400s every send with
        a message about a field the caller demonstrably supplied. Asserting the
        two refusals above without ever asserting the ACCEPTED shape is how that
        ships green. The instant must arrive parsed and timezone-aware, because
        it is half of the ``(identifier, expires)`` key both legs match on.
        """
        seen: list[datetime] = []

        async def _capture(identifier, expires):
            seen.append(expires)
            return {"identifier": identifier, "token": None,
                    "expires": expires.isoformat()}

        monkeypatch.setattr(route, "reserve_send", _capture)
        res = _client().post(
            "/signin/otp/send", json={"expires": "2026-08-23T21:00:00.000Z"}
        )
        assert res.status_code == 200
        assert len(seen) == 1
        assert seen[0].tzinfo is not None, (
            "a naive instant compares wrongly against a TIMESTAMPTZ column"
        )
        assert seen[0] == datetime(2026, 8, 23, 21, 0, tzinfo=UTC)

    def test_a_rate_limit_is_a_429_carrying_its_code(self, monkeypatch) -> None:
        """The adapter turns a 429 into a throw; a 200 would look like success."""

        async def _limited(*a, **k):
            raise OtpRateLimited(SEND_RATE_LIMITED, "too many")

        monkeypatch.setattr(route, "reserve_send", _limited)
        res = _client().post("/signin/otp/send", json={"expires": _soon().isoformat()})
        assert res.status_code == 429
        assert res.json()["code"] == SEND_RATE_LIMITED

    def test_a_miss_is_one_404_for_wrong_used_and_expired(self, monkeypatch) -> None:
        """One shape for all three: telling them apart tells a guesser whether a
        live code exists for the address."""

        async def _miss(*a, **k):
            return None

        monkeypatch.setattr(route, "consume_token", _miss)
        res = _client().post("/signin/otp/consume", json={"token": "nope"})
        assert res.status_code == 404
        assert res.json()["code"] == route.INVALID_TOKEN
        # …and the copy names no state.
        assert "expired" not in res.json()["detail"]
        assert "used" not in res.json()["detail"]


# ==========================================================================
# The MIGRATION — the collision guard (clause 8, ruling H-C).
# ==========================================================================


@_DB_GATE
class TestTheMigrationRefusesAForeignTable:
    """A name clash must fail the deploy, not be absorbed by ``IF NOT EXISTS``.

    The Langfuse collision is real on the staging box's database and migrations
    run on both boxes. An idempotent create against a stranger's relation is a
    **silent** no-op: the migration reports success, the ledger records it, and
    the adapter then reads and writes somebody else's table.

    Runs against its OWN throwaway database — the migration has no dependencies,
    so there is no ladder to replay, and planting a foreign table in the shared
    scratch database would break every other suite in the job.
    """

    @pytest.fixture()
    def scratch(self):
        admin_url = make_url(_URL)
        dedicated = f"{admin_url.database}_otpguard"
        maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with maint.connect() as c:
            c.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :d AND pid <> pg_backend_pid()"
                ),
                {"d": dedicated},
            )
            c.execute(text(f'DROP DATABASE IF EXISTS "{dedicated}"'))
            c.execute(text(f'CREATE DATABASE "{dedicated}"'))
        eng = create_engine(admin_url.set(database=dedicated), future=True)
        try:
            yield eng
        finally:
            eng.dispose()
            with maint.connect() as c:
                c.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :d AND pid <> pg_backend_pid()"
                    ),
                    {"d": dedicated},
                )
                c.execute(text(f'DROP DATABASE IF EXISTS "{dedicated}"'))
            maint.dispose()

    @staticmethod
    def _apply(eng) -> None:
        sql = _MIGRATION.read_text(encoding="utf-8")
        with eng.begin() as conn, conn.connection.dbapi_connection.cursor() as cur:
            cur.execute(sql)

    def test_it_applies_cleanly_and_is_replay_safe(self, scratch) -> None:
        """The deploy replays the whole ladder every time."""
        self._apply(scratch)
        self._apply(scratch)
        with scratch.connect() as c:
            cols = {
                r[0]
                for r in c.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'auth_email_otp_token'"
                    )
                )
            }
        # The counters B3 lives on, named here so removing one is a red test
        # rather than a limiter that silently counts nothing.
        assert {"identifier", "token", "expires", "attempts", "last_attempt_at",
                "consumed_at", "invalidated_at", "created_at"} <= cols

    def test_it_RAISES_on_a_foreign_relation_of_the_same_name(self, scratch) -> None:
        """The half that would otherwise be a silent no-op.

        Deleting the ``DO`` block from the migration turns this **RED** —
        measured 2026-08-23, which is what makes it a fence rather than a
        docstring. The prefixed name is the other half of the same defence.

        ⚠️ Caught as a bare ``Exception`` and asserted on the MESSAGE, not on a
        SQLAlchemy type: :meth:`_apply` runs the file through the raw DBAPI
        cursor (``_tenant_ladder._exec_file``'s reason — one file, many
        statements, no client-side ``%`` pass), so psycopg's own
        ``RaiseException`` arrives unwrapped and a ``DBAPIError`` expectation
        fails for the wrong reason. Measured 2026-08-23.
        """
        with scratch.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE auth_email_otp_token "
                    "(id TEXT PRIMARY KEY, something_else TEXT)"
                )
            )
        with pytest.raises(Exception) as caught:
            self._apply(scratch)
        assert "is NOT the CP-2d table" in str(caught.value)


# ==========================================================================
# The STORE — R8, against the replayed tenant ladder.
# ==========================================================================


@pytest.fixture(scope="module")
def replayed():
    """The tenant schema, applied twice — the deploy replays it on every push."""
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


def _rows(eng, identifier: str) -> list[dict]:
    """Every row for *identifier*, OLDEST first.

    Ordered by ``(created_at, id)`` rather than ``created_at`` alone: two rows
    written in the same microsecond would otherwise come back in an arbitrary
    order, and the P2 assertions below are about WHICH of two rows was consumed.
    """
    with eng.connect() as c:
        return [
            dict(r)
            for r in c.execute(
                text(
                    "SELECT token, attempts, consumed_at, invalidated_at, "
                    "       expires, reserved_at "
                    "  FROM auth_email_otp_token WHERE identifier = :i "
                    " ORDER BY created_at, id"
                ),
                {"i": identifier},
            )
            .mappings()
            .all()
        ]


@_DB_GATE
class TestTheTokenRoundTrip:
    """Create, consume, and the two ways a consume must fail."""

    async def test_a_code_round_trips_and_is_single_use(self, replayed) -> None:
        who = _who("round")
        expires = _soon()
        hashed = f"hash-{uuid.uuid4().hex}"

        async with tenant_engine_scope(_URL):
            await reserve_send(who, expires)
            await record_token(who, hashed, expires)
            first = await consume_token(who, hashed)
            second = await consume_token(who, hashed)

        assert first is not None, "a live token did not verify"
        assert first["identifier"] == who
        assert first["token"] == hashed
        # ⚠️ Single use. `consumed_at` is in the consume statement's own WHERE,
        # so this is the STATEMENT's property, not a branch that can be
        # reordered away — and a replayable code is a code an email forward
        # hands to somebody else.
        assert second is None
        assert _rows(replayed, who)[0]["consumed_at"] is not None

    async def test_the_two_legs_of_one_send_write_ONE_row(self, replayed) -> None:
        """``(identifier, expires)`` is the send's identity.

        ``@auth/core`` starts ``sendVerificationRequest`` and
        ``createVerificationToken`` concurrently and only the second carries the
        hash. If they did not converge on one row, every send would consume two
        of the hourly budget of three.
        """
        who = _who("onerow")
        expires = _soon()
        hashed = f"hash-{uuid.uuid4().hex}"

        async with tenant_engine_scope(_URL):
            await reserve_send(who, expires)
            await record_token(who, hashed, expires)

        rows = _rows(replayed, who)
        assert len(rows) == 1
        # …and the reserve leg's NULL must not wipe a hash already written.
        assert rows[0]["token"] == hashed

    async def test_the_reserve_leg_may_land_second_without_losing_the_hash(
        self, replayed
    ) -> None:
        """Order-independent, because the two legs genuinely race.

        ⚠️ This is also the half of the P1b repair that could have broken the
        ordinary flow. The send leg now CLAIMS its slot and is refused when the
        claim finds it taken — so a token leg that landed first must leave the
        row *unclaimed*, or every send whose token leg won the race would be
        refused as a duplicate and no mail would ever go out.
        """
        who = _who("reorder")
        expires = _soon()
        hashed = f"hash-{uuid.uuid4().hex}"

        async with tenant_engine_scope(_URL):
            await record_token(who, hashed, expires)
            await reserve_send(who, expires)

        rows = _rows(replayed, who)
        assert len(rows) == 1
        assert rows[0]["token"] == hashed
        # The token leg wrote the row but claimed nothing; the send leg claimed.
        assert rows[0]["reserved_at"] is not None

    async def test_the_token_leg_alone_claims_no_send_slot(self, replayed) -> None:
        """A row is not a mail.

        The budget counts CLAIMED rows, so a caller driving ``/signin/otp/token``
        alone (the internal-bearer residual clause 12 names) cannot spend a
        victim's send budget — it can only fill rows, which its own gate bounds.
        """
        who = _who("noclaim")
        async with tenant_engine_scope(_URL):
            await record_token(who, f"hash-{uuid.uuid4().hex}", _soon())

        assert _rows(replayed, who)[0]["reserved_at"] is None

    async def test_an_expired_code_does_not_verify(self) -> None:
        """Short expiry is one of the three bounds on the accepted residual
        (clause 12), so it is asserted rather than assumed."""
        who = _who("expired")
        hashed = f"hash-{uuid.uuid4().hex}"
        past = datetime.now(UTC) - timedelta(seconds=1)

        async with tenant_engine_scope(_URL):
            await record_token(who, hashed, past)
            assert await consume_token(who, hashed) is None

    async def test_a_wrong_code_does_not_verify(self) -> None:
        who = _who("wrong")
        expires = _soon()
        async with tenant_engine_scope(_URL):
            await record_token(who, f"hash-{uuid.uuid4().hex}", expires)
            assert await consume_token(who, "not-the-hash") is None

    async def test_two_live_codes_with_the_SAME_hash_consume_ONE_row(
        self, replayed
    ) -> None:
        """⚠️ A 6-digit code collision must not 500 sign-in (review finding P2).

        ``(identifier, token)`` is deliberately not unique — two live codes for
        one address can land on the same six digits (≈1e-6 per pair), and a
        unique violation would present to the person as "the code is broken".
        But the consume statement then matched TWO rows, and the result was read
        with ``.one_or_none()``, which raises ``MultipleResultsFound``: a **500
        on sign-in for somebody whose code was correct**.

        The statement now selects one id (``ORDER BY created_at DESC, id DESC
        LIMIT 1``) and updates that, so the newest matching token is consumed and
        the other is left alone.

        Measured RED by restoring the bare ``UPDATE … WHERE identifier AND
        token`` form: this raises ``MultipleResultsFound`` instead of returning.
        """
        who = _who("collide")
        # The SAME hash on two live sends — what a digit collision looks like
        # by the time it reaches this table.
        hashed = f"hash-{uuid.uuid4().hex}"

        async with tenant_engine_scope(_URL):
            await record_token(who, hashed, _soon(600))
            await record_token(who, hashed, _soon(601))
            found = await consume_token(who, hashed)

        assert found is not None, "a correct code did not verify"
        rows = _rows(replayed, who)
        assert len(rows) == 2
        # Exactly one consumed, and it is the NEWEST — the code the person was
        # most recently sent; the other is about to expire anyway.
        assert [r["consumed_at"] is not None for r in rows] == [False, True]

    async def test_the_identifier_is_canonicalised_on_both_sides(self) -> None:
        """A case variant must not be a second address with a second budget."""
        who = _who("Case").upper()
        expires = _soon()
        hashed = f"hash-{uuid.uuid4().hex}"

        async with tenant_engine_scope(_URL):
            await record_token(who, hashed, expires)
            found = await consume_token(who.lower(), hashed)

        assert found is not None
        assert found["identifier"] == canonical_identifier(who)


@_DB_GATE
class TestTheVerificationLimit:
    """Owner answer B3: 5 attempts per identifier per 10 minutes."""

    async def test_the_sixth_attempt_is_refused_EVEN_WITH_the_correct_code(
        self, replayed
    ) -> None:
        """⚠️ The clause that makes the limiter real.

        A limiter that lets the correct code through after N failures is not a
        limiter — an attacker's last guess is, by construction, the one that is
        correct. So the budget is checked BEFORE the submitted hash is looked
        at, and this is the assertion that says so.
        """
        who = _who("sixth")
        expires = _soon()
        hashed = f"hash-{uuid.uuid4().hex}"

        async with tenant_engine_scope(_URL):
            await record_token(who, hashed, expires)
            for i in range(VERIFY_MAX_ATTEMPTS):
                assert await consume_token(who, f"wrong-{i}") is None
            with pytest.raises(OtpRateLimited) as caught:
                await consume_token(who, hashed)

        assert caught.value.code == VERIFY_RATE_LIMITED
        row = _rows(replayed, who)[0]
        assert row["attempts"] == VERIFY_MAX_ATTEMPTS
        # …and the outstanding token is INVALIDATED, not merely throttled: a
        # code somebody has been guessing at must not come back when the window
        # rolls off.
        assert row["invalidated_at"] is not None
        assert row["consumed_at"] is None

    async def test_the_window_stays_shut_for_a_freshly_sent_code(
        self, replayed
    ) -> None:
        """Exhaustion is per IDENTIFIER, not per token.

        Otherwise the budget resets every time the person asks for a new code —
        three sends an hour would buy fifteen guesses in ten minutes instead of
        five, and the limiter would be advertising its own bypass.
        """
        who = _who("window")
        first = _soon()
        second = _soon(601)
        good = f"hash-{uuid.uuid4().hex}"

        async with tenant_engine_scope(_URL):
            await record_token(who, f"hash-{uuid.uuid4().hex}", first)
            for i in range(VERIFY_MAX_ATTEMPTS):
                await consume_token(who, f"wrong-{i}")
            # A NEW code, inside the same window.
            await record_token(who, good, second)
            with pytest.raises(OtpRateLimited) as caught:
                await consume_token(who, good)

        assert caught.value.code == VERIFY_RATE_LIMITED
        # The fresh token is invalidated too — a code minted during a lockout is
        # dead on arrival rather than a way out of it.
        assert all(r["invalidated_at"] is not None for r in _rows(replayed, who))

    async def test_one_guess_costs_one_attempt_even_with_two_live_codes(
        self, replayed
    ) -> None:
        """The charge lands on ONE row, not on every outstanding one.

        Charging all of them would make a single guess cost two attempts for
        anybody who asked for a second code, and the limit would fire at three.
        """
        who = _who("charge")
        async with tenant_engine_scope(_URL):
            await record_token(who, f"hash-{uuid.uuid4().hex}", _soon())
            await record_token(who, f"hash-{uuid.uuid4().hex}", _soon(601))
            await consume_token(who, "one-guess")

        assert sum(r["attempts"] for r in _rows(replayed, who)) == 1

    async def test_a_correct_code_inside_the_budget_still_works(self) -> None:
        """The limiter must not be the reason ordinary people cannot sign in."""
        who = _who("typo")
        expires = _soon()
        hashed = f"hash-{uuid.uuid4().hex}"

        async with tenant_engine_scope(_URL):
            await record_token(who, hashed, expires)
            for i in range(VERIFY_MAX_ATTEMPTS - 1):
                assert await consume_token(who, f"typo-{i}") is None
            assert await consume_token(who, hashed) is not None


@_DB_GATE
class TestTheSendLimit:
    """Owner answer B3: 3 code sends per identifier per hour."""

    async def test_the_fourth_send_is_refused(self, replayed) -> None:
        who = _who("sends")
        async with tenant_engine_scope(_URL):
            for _ in range(SEND_MAX_PER_WINDOW):
                await reserve_send(who, _soon(600 + _))
            with pytest.raises(OtpRateLimited) as caught:
                await reserve_send(who, _soon(999))

        assert caught.value.code == SEND_RATE_LIMITED
        assert len(_rows(replayed, who)) == SEND_MAX_PER_WINDOW

    async def test_the_token_leg_applies_the_SAME_budget(self, replayed) -> None:
        """Not merely belt-and-braces.

        The two legs are concurrent and a caller holding the internal bearer can
        invoke the token leg alone, so a budget enforced only on the send leg is
        a budget with a documented bypass (clause 12: the limit applies to EVERY
        caller).
        """
        who = _who("tokenleg")
        async with tenant_engine_scope(_URL):
            for i in range(SEND_MAX_PER_WINDOW):
                await record_token(who, f"hash-{i}-{uuid.uuid4().hex}", _soon(600 + i))
            with pytest.raises(OtpRateLimited) as caught:
                await record_token(who, f"hash-x-{uuid.uuid4().hex}", _soon(999))

        assert caught.value.code == SEND_RATE_LIMITED
        assert len(_rows(replayed, who)) == SEND_MAX_PER_WINDOW

    async def test_both_legs_of_the_SAME_send_agree(self, replayed) -> None:
        """⚠️ The off-by-one this design exists to remove.

        The send count EXCLUDES the send in flight (``expires <> :expires``), so
        the third send's two legs both see two others and both pass — whichever
        lands first. Count the row itself and the limit is nondeterministically
        two or three, which is a test that passes most of the time.
        """
        who = _who("agree")
        async with tenant_engine_scope(_URL):
            for i in range(SEND_MAX_PER_WINDOW):
                expires = _soon(600 + i)
                await reserve_send(who, expires)
                await record_token(who, f"hash-{i}-{uuid.uuid4().hex}", expires)

        assert len(_rows(replayed, who)) == SEND_MAX_PER_WINDOW

    async def test_two_sends_sharing_an_EXPIRES_MILLISECOND_mail_once(
        self, replayed
    ) -> None:
        """⚠️ The bypass the exclusion above opened, closed (review finding P1b).

        ``expires`` is ``Date.now() + maxAge``, computed per send. Two sends
        started in the same millisecond therefore carry the SAME instant — and
        the in-flight exclusion made each of them invisible to the other's
        count, so both passed a budget of three, both upserted the one row, and
        **two emails went out for one slot**. Repeat per millisecond and the
        limit is decorative.

        This drives exactly that shape: one ``expires``, two sends. The second
        must be refused — ``SendDuplicate``, distinct from the budget refusal
        because the budget was not the thing that stopped it — and the refusal is
        what keeps ``sendVerificationRequest`` from reaching Resend.

        Measured RED by removing the ``WHERE auth_email_otp_token.reserved_at IS
        NULL`` from ``_CLAIM_SEND_SQL``: the second send is then permitted and a
        second code is mailed.
        """
        who = _who("burst")
        expires = _soon()  # ONE instant, shared by both sends

        async with tenant_engine_scope(_URL):
            await reserve_send(who, expires)
            with pytest.raises(OtpRateLimited) as caught:
                await reserve_send(who, expires)

        assert caught.value.code == SEND_DUPLICATE
        # …and it coalesced onto ONE row rather than silently making two.
        assert len(_rows(replayed, who)) == 1

    async def test_the_burst_spends_exactly_one_of_the_three(
        self, replayed
    ) -> None:
        """The other half of P1b: the budget must COUNT the burst once.

        A refusal that also failed to charge the slot would be the same hole
        wearing a 429 — the attacker would burst, be refused, and still have
        three sends left. So: burst, then prove only two remain.
        """
        who = _who("burstbudget")
        shared = _soon()

        async with tenant_engine_scope(_URL):
            await reserve_send(who, shared)
            for _ in range(2):
                with pytest.raises(OtpRateLimited):
                    await reserve_send(who, shared)
            # Two left, and only two.
            await reserve_send(who, _soon(601))
            await reserve_send(who, _soon(602))
            with pytest.raises(OtpRateLimited) as fourth:
                await reserve_send(who, _soon(603))

        assert fourth.value.code == SEND_RATE_LIMITED
        assert len(_rows(replayed, who)) == SEND_MAX_PER_WINDOW


class TestThisSuiteIsArmed:
    """The hand-maintained CI skip guard discovers nothing (pr-check.yml's own
    warning), so an R8 suite absent from it skips silently and leaves the job
    green — the exact failure that step exists to catch. Deliberately NOT
    ``_DB_GATE``d: a check that the gate is armed must run when it is not."""

    def test_this_suite_is_named_in_the_ci_skip_guard(self) -> None:
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8"
        )
        assert "tests/unit/test_email_otp_token.py" in workflow

    def test_the_owning_spec_names_this_suite(self) -> None:
        spec = (_ROOT / "project-docs/specs/customer_console.md").read_text(
            encoding="utf-8"
        )
        assert "test_email_otp_token.py" in spec


@_DB_GATE
class TestTheLadderDsnDoesNotLeak:
    """``tenant_engine_scope`` restores ``DATABASE_URL`` in a ``finally``.

    The same assertion ``test_org_provisioning.py`` carries, for the same
    reason: a suite that left the ladder DSN behind would silently re-point
    every later suite in the process at the scratch database.
    """

    def test_the_ladder_dsn_does_not_leak_out_of_this_suite(self) -> None:
        assert os.environ.get("DATABASE_URL", "") != _URL
