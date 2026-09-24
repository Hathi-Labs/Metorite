"""Every SQL statement on the SIGN-IN path, executed on the driver production uses.

⚠️ **This is H-114's second module, and it is the one that decides who you are.**

`test_projects_sql_asyncpg.py` closed the same gap for Projects, and its header
carries the argument in full. The short version: `scripts/dev_db.sh` hands every
suite a ``postgresql+psycopg`` DSN, and `acb_common.db.database_url()` rewrites
every production DSN onto ``postgresql+asyncpg``. So "verified against a real
database" has meant "verified against a real database through the wrong driver"
for the whole life of this tree, and `/projects/analytics/stuck` answered **500
at every scope** from the day it merged because of it.

**Why this module next, ahead of the route modules H-114 names.** A broken route
breaks one pane. A broken statement in `acb_auth` breaks *sign-in*, and every
request in the product runs `resolve_access` and `resolve_identity` through
`access.py` before it reaches any route at all. The blast radius is the
difference between one endpoint and the whole box, so it goes first.

**What this suite does NOT do.** It asserts almost nothing about the answers.
`test_h6_identity_shadow.py`, `test_org_access_control.py` and the OTP suites own
correctness. This file owns one question: *does the driver production runs accept
the SQL we ship, with the parameters the callers actually bind?*

⚠️ **The parameter TYPES are the whole point.** Every entry below is typed the way
its real call site types it — a `datetime` where the caller passes a `datetime`, a
`float` where it passes `float(...)`, a JSON **string** where it passes
`json.dumps(...)`. Retyping one of them to something convenient would make this
suite pass while production fails, which is the exact defect being fenced.

⚠️ **Three casts here are the family that is NOT linted.**
`test_sql_interval_shape.py` refuses ``CAST(:param AS interval)`` anywhere under
`gateway/` or `packages/`, and it cannot refuse ``CAST(:x AS TIMESTAMPTZ)`` or
``CAST(:x AS JSONB)`` — binding a real `datetime` or a real JSON string through
those is correct, and a lint there would fail correct code and grow an allowlist.
`email_otp`'s ``:expires``, `console_resolve`'s ``:trial_ends_at`` and its
``:caps`` are exactly that shape. Running them is the only fence available, and
running them is what this file is.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("asyncpg")

from acb_auth import access as A
from acb_auth import console_resolve as C
from acb_auth import email_otp as O
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import apply_ladder

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset. ⚠️ This suite and"
        " test_projects_sql_asyncpg.py are the ONLY ones that run our SQL on"
        " asyncpg, the driver production uses. Skipping it leaves the gap that"
        " shipped /analytics/stuck broken — on the sign-in path this time."
    ),
)


def _async_url() -> str:
    """The same rewrite `acb_common.db` performs on every production DSN.

    ⚠️ Not a convenience. Using the suite's psycopg URL here would make this
    file a slower copy of the tests beside it, and prove nothing.
    """
    url = _TENANT_URL
    if "postgresql+psycopg" in url:
        return url.replace("postgresql+psycopg", "postgresql+asyncpg")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest.fixture(scope="module")
def built():
    """The tenant schema, replayed twice like the deploy does.

    Synchronous on purpose: `apply_ladder` takes a DBAPI cursor, and building
    the schema is not what this suite is testing.
    """
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
async def async_engine(built):
    """One engine per test, and the scope is not negotiable.

    ⚠️ A module-scoped engine binds its pool to the FIRST test's event loop, and
    `asyncio_mode = "auto"` gives each test its own. Every test after the first
    then fails with *"cannot perform operation: another operation is in
    progress"* — which looks exactly like a SQL fault and is not one. Measured
    while writing the Projects suite; recorded here so the next person copying
    this pattern does not spend the same afternoon.
    """
    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    try:
        yield eng
    finally:
        await eng.dispose()


#: A syntactically valid UUID that matches nothing. Every statement below is
#: executed for its SHAPE, never for its effect, so a row it cannot find is the
#: outcome we want — and the writes are rolled back regardless.
NOWHERE = "00000000-0000-0000-0000-000000000000"
WHO = "nobody@example.invalid"
SOON = datetime.now(UTC) + timedelta(minutes=10)

#: Sentinels replaced with the ids of rows seeded inside the test's own
#: transaction.
#:
#: ⚠️ **Only where a foreign key demands it.** Two statements insert into
#: `org_membership`, which references `organization` and `user_identity`, so
#: `NOWHERE` raises `ForeignKeyViolationError` there. That violation actually
#: PROVES what this suite asks — the server accepted the statement and objected
#: to the data — but a suite that is red for a data reason teaches people to
#: read past it, and it would also never reach the `ON CONFLICT … DO UPDATE`
#: arm, which is the half most likely to break.
ORG = "<seeded-org>"
UID = "<seeded-identity>"

_SEED_ORG = text(
    "INSERT INTO organization (slug, display_name)"
    " VALUES (:s, :s) RETURNING id::text AS id"
)
_SEED_IDENTITY = text(
    "INSERT INTO user_identity (email, display_name)"
    " VALUES (:e, :e) RETURNING id::text AS id"
)


async def _seed(conn) -> dict[str, str]:
    """One organization and one identity, for the statements that need them.

    Inside the caller's transaction, and therefore rolled back with it.
    """
    org = (
        await conn.execute(_SEED_ORG, {"s": f"asyncpg-fence-{os.getpid()}"})
    ).scalar_one()
    uid = (await conn.execute(_SEED_IDENTITY, {"e": WHO})).scalar_one()
    return {ORG: org, UID: uid}


def _cases() -> list[tuple[str, str, dict]]:
    """Each statement, with parameters shaped the way its CALLER binds them.

    The call site is named on every line that is not obvious, because the
    binding is the thing under test and a reader has to be able to check it
    against the source without trusting this comment.
    """
    return [
        # ── acb_auth.access — every request runs the first two of these ──────
        ("access._ACCESS_SQL", A._ACCESS_SQL, {"email": WHO}),
        ("access._IDENTITY_LEG_SQL", A._IDENTITY_LEG_SQL, {"email": WHO}),
        (
            "access._ACCESS_REQUEST_UPSERT_SQL",
            A._ACCESS_REQUEST_UPSERT_SQL,
            {"email": WHO, "name": "Nobody"},
        ),
        # H-118 — the domain → organization lookup that decides WHICH tenant a
        # knock joins. In production it runs on an UNBOUND session, because the
        # knock has no tenant yet; that is legal only because `organization` is
        # the one table carrying no row-level security. A domain nobody claims
        # is the ordinary answer, so the zero-row path is what this exercises.
        (
            "access._ORG_FOR_DOMAIN_SQL",
            A._ORG_FOR_DOMAIN_SQL,
            {"domain": "no-such-domain.invalid"},
        ),
        (
            "access._MIRROR_ORG_BY_SLUG_SQL",
            A._MIRROR_ORG_BY_SLUG_SQL,
            {"slug": "no-such-org"},
        ),
        (
            "access._MIRROR_IDENTITY_SQL",
            A._MIRROR_IDENTITY_SQL,
            {"email": WHO, "name": ""},
        ),
        (
            "access._MIRROR_MEMBERSHIP_SQL",
            A._MIRROR_MEMBERSHIP_SQL,
            # `str(resolved_org)` and `identity["id"]` at the call site — both
            # text, never a UUID object.
            {"org": ORG, "uid": UID, "status": "active"},
        ),
        (
            "access._MIRROR_STATUS_SQL",
            A._MIRROR_STATUS_SQL,
            {"status": "active", "org": NOWHERE, "email": WHO},
        ),
        (
            "access._INVITED_MEMBERSHIPS_SQL",
            A._INVITED_MEMBERSHIPS_SQL,
            {"email": WHO},
        ),
        ("access._PROMOTE_APP_USER_SQL", A._PROMOTE_APP_USER_SQL, {"email": WHO}),
        (
            "access._PURGE_MEMBERSHIP_SQL",
            A._PURGE_MEMBERSHIP_SQL,
            {"email": WHO, "org": NOWHERE},
        ),
        ("access._ORG_OWNER_SQL", A._ORG_OWNER_SQL, {"slug": "no-such-org"}),
        ("access._MEMBERSHIP_SQL", A._MEMBERSHIP_SQL, {"email": WHO}),
        ("access._PARTICIPANT_SQL", A._PARTICIPANT_SQL, {"sid": NOWHERE}),
        (
            "access._GROUP_MEMBER_SQL",
            A._GROUP_MEMBER_SQL,
            {"slug": "no-such-group", "actor_email": WHO},
        ),
        ("access._ORG_MEMBER_SQL", A._ORG_MEMBER_SQL, {"actor_email": WHO}),
        ("access._HAS_OWNER_SQL", A._HAS_OWNER_SQL, {"org_slug": "no-such-org"}),
        (
            # ⚠️ The statement migration 162 broke at PLAN time, so it took out
            # the fresh-insert path too and `ensure_owner_bootstrap()`'s
            # catch-all turned it into a silent `ownership_bootstrap_failed` —
            # an ownerless box that never says so. It is a CTE with an
            # `ON CONFLICT (lower(email))` target and a LEFT JOIN, which is the
            # most plan-fragile statement in the package.
            "access._BOOTSTRAP_OWNER_SQL",
            A._BOOTSTRAP_OWNER_SQL,
            {"email": WHO, "org_slug": "no-such-org"},
        ),
        # ── acb_auth.console_resolve — the registry mirror ───────────────────
        (
            "console_resolve._SELECT_UNMIRRORED_ORGS_SQL",
            C._SELECT_UNMIRRORED_ORGS_SQL,
            {},
        ),
        ("console_resolve._LOCAL_SLUGS_SQL", C._LOCAL_SLUGS_SQL, {}),
        ("console_resolve._READ_SQL", C._READ_SQL, {"email": WHO}),
        (
            "console_resolve._ORG_BY_SLUG_SQL",
            C._ORG_BY_SLUG_SQL,
            {"slug": "no-such-org"},
        ),
        (
            "console_resolve._UPSERT_IDENTITY_SQL",
            C._UPSERT_IDENTITY_SQL,
            {"email": WHO, "name": ""},
        ),
        (
            # ⚠️ TWO unlinted casts in one statement, and this is the reason the
            # suite exists rather than a wider lint: `caps` is `json.dumps(...)`
            # — a STRING through `CAST(… AS JSONB)` — and `trial_ends_at` is a
            # real `datetime` through `CAST(… AS TIMESTAMPTZ)` (or None). Both
            # are correct, and neither can be proved correct by reading.
            "console_resolve._WRITE_ORG_SQL",
            C._WRITE_ORG_SQL,
            {
                "status": "trial",
                "caps": json.dumps({"sign_in": True}),
                "trial_ends_at": SOON,
                "org": NOWHERE,
            },
        ),
        (
            "console_resolve._WRITE_ORG_SQL/no trial end",
            C._WRITE_ORG_SQL,
            # A perpetual org binds NULL here. asyncpg types a `None` from the
            # value alone, so the null path is a different bind, not the same
            # one with a different value.
            {
                "status": "active",
                "caps": json.dumps({"sign_in": True}),
                "trial_ends_at": None,
                "org": NOWHERE,
            },
        ),
        (
            "console_resolve._WRITE_REFUSAL_SQL",
            C._WRITE_REFUSAL_SQL,
            {"caps": json.dumps({"sign_in": False}), "org": NOWHERE},
        ),
        (
            "console_resolve._UPSERT_MEMBERSHIP_SQL",
            C._UPSERT_MEMBERSHIP_SQL,
            {"org": ORG, "uid": UID},
        ),
        (
            "console_resolve._RESOLVED_ORGS_SQL",
            C._RESOLVED_ORGS_SQL,
            {"email": WHO},
        ),
        ("console_resolve._FORGET_SQL", C._FORGET_SQL, {"email": WHO, "org": NOWHERE}),
        (
            "console_resolve._TOUCH_MEMBERSHIP_SQL",
            C._TOUCH_MEMBERSHIP_SQL,
            {"email": WHO, "org": NOWHERE},
        ),
        # ── acb_auth.email_otp — the sign-in code ────────────────────────────
        ("email_otp._LOCK_SQL", O._LOCK_SQL, {"identifier": WHO}),
        (
            # ⚠️ `window_s` is a FLOAT through `make_interval(secs => …)`, which
            # is the shape that works. The shape that does NOT is
            # `CAST(:x AS interval)` bound with a string — psycopg accepts it,
            # asyncpg refuses it, and that is what took `/analytics/stuck` down.
            # `expires` is a real `datetime` through the unlinted TIMESTAMPTZ.
            "email_otp._CLAIMED_SEND_COUNT_SQL",
            O._CLAIMED_SEND_COUNT_SQL,
            {"identifier": WHO, "window_s": float(O.SEND_WINDOW_S), "expires": SOON},
        ),
        (
            "email_otp._ROW_COUNT_SQL",
            O._ROW_COUNT_SQL,
            {"identifier": WHO, "window_s": float(O.SEND_WINDOW_S), "expires": SOON},
        ),
        (
            "email_otp._CLAIM_SEND_SQL",
            O._CLAIM_SEND_SQL,
            {"identifier": WHO, "token": "hash", "expires": SOON},
        ),
        (
            "email_otp._UPSERT_SQL",
            O._UPSERT_SQL,
            {"identifier": WHO, "token": "hash", "expires": SOON},
        ),
        (
            "email_otp._ATTEMPT_COUNT_SQL",
            O._ATTEMPT_COUNT_SQL,
            {"identifier": WHO, "window_s": float(O.VERIFY_WINDOW_S)},
        ),
        ("email_otp._CHARGE_ATTEMPT_SQL", O._CHARGE_ATTEMPT_SQL, {"identifier": WHO}),
        (
            "email_otp._CONSUME_SQL",
            O._CONSUME_SQL,
            {"identifier": WHO, "token": "hash"},
        ),
        ("email_otp._INVALIDATE_SQL", O._INVALIDATE_SQL, {"identifier": WHO}),
    ]


@pytest.mark.parametrize(
    "name,sql,params", _cases(), ids=[c[0] for c in _cases()]
)
async def test_the_statement_runs_on_asyncpg(async_engine, name, sql, params):
    """⚠️ The test that was missing, on the path that matters most.

    It asserts nothing about the answer. Either asyncpg accepts the statement
    and its bound parameters, or it raises.

    ⚠️ **Rolled back, never committed.** Half of these are writes, and a suite
    that left rows behind would poison the very tables the correctness suites
    beside it read. `begin()` as a context manager COMMITS on a clean exit,
    which is why the transaction is driven by hand here.
    """
    async with async_engine.connect() as conn:
        trans = await conn.begin()
        try:
            if ORG in params.values() or UID in params.values():
                seeded = await _seed(conn)
                params = {k: seeded.get(v, v) for k, v in params.items()}
            await conn.execute(text(sql), params)
        finally:
            await trans.rollback()


def test_every_SQL_constant_in_these_modules_is_covered():
    """⚠️ The fence on the fence: a statement added tomorrow is covered too.

    Without this, the suite silently stops being complete the first time
    somebody adds a constant — and a partial fence reads exactly like a whole
    one. Discovery beats a hand-written list for the same reason the list it
    replaces was going to go stale.

    A new constant fails here with its own name, and the fix is one line in
    `_cases()` with parameters typed the way its caller types them.
    """
    covered = {name.split("/")[0] for name, _, _ in _cases()}
    missing: list[str] = []
    for module, short in ((A, "access"), (C, "console_resolve"), (O, "email_otp")):
        for attr in dir(module):
            if not attr.endswith("_SQL") or not attr.startswith("_"):
                continue
            if not isinstance(getattr(module, attr), str):
                continue
            if f"{short}.{attr}" not in covered:
                missing.append(f"{short}.{attr}")
    assert not missing, (
        "these statements reach production on asyncpg and nothing here runs"
        f" them: {sorted(missing)}"
    )
