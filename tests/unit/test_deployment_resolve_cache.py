"""CP-2b — the DEPLOYMENT half: the resolve cache, the projection, the refusals.

Spec: ``project-docs/specs/customer_console.md`` §6 CP-2b clauses 6, 7, 8's
``resolved_at`` half, 9's deployment half, 10's tenant half and 12's cache half
· §6(c) failure semantics · §6(i) this suite's DSN discipline · §6(j) the four
outcomes · §6(k) the join key.

⚠️ **R8 against the TENANT database, and it answers to
``TENANT_LADDER_DATABASE_URL`` — never to ``DATABASE_URL``.** The two are
deliberately different names. ``pr-check.yml``'s ``test`` job runs the whole
``tests/unit/`` directory, and ``DATABASE_URL`` there would arm
``test_tenant_coverage.py``'s two DB-gated tests, which fail **by construction**
against a freshly-replayed ladder: one demands FORCE-RLS policies that live only
in ``infra/postgres/generated/04_policies.sql`` (never replayed — that file's own
header calls promoting it *a cliff*), the other demands a non-superuser
application role, and a service container's ``POSTGRES_USER`` *is* the
superuser. Both are WS-29 MT-1b/MT-1c's gates and neither is CP-2b's to make
pass. They skip in CI today and must keep skipping exactly as today.

The gate reads the **launch snapshot** taken beside ``tests/conftest.py``'s
first one, never a live ``os.environ``: ``import litellm`` calls
``load_dotenv()`` mid-collection, and a dev machine's ``.env`` must not be able
to point an R8 gate at a local database.

⚠️ **The fixture then sets ``DATABASE_URL`` IN-PROCESS**, because
``console_resolve``'s projection write goes through ``acb_common.db``, whose
``async_database_url()`` reads that variable first. That does **not** re-arm
``test_tenant_coverage.py``, for two independent reasons: its gate reads
``_ACB_DATABASE_URL_AT_LAUNCH``, which ``conftest.py`` set to ``""`` before any
test module imported, and its ``_needs_db`` is a module-scope ``skipif``
evaluated at collection. Fenced here by
``test_the_ladder_dsn_does_not_leak_out_of_this_suite``.

⚠️ **Hermetic fakes agree with whatever SQL they are handed.** Every clause
below reads or writes an expression unique index, a three-table join, a JSONB
column and an ``ON CONFLICT`` upsert — so this suite skips loudly rather than
passing without a server, and ``pr-check.yml``'s hand-list names it (with its
own skip string) so CI cannot go back to skipping it while reporting green.

The HTTP hop is **not** faked either: the four outcomes are driven through a
real ``httpx.MockTransport``, so the URL, the header and the body this module
builds are under test rather than stubbed away.
"""
from __future__ import annotations

import contextlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("httpx")
pytest.importorskip("sqlalchemy")

import httpx
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder

#: The launch snapshot (``tests/conftest.py``), not the live variable. Falls
#: back to the live one only when the snapshot was never taken, i.e. when this
#: module is imported outside pytest.
_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector (infra/postgres/01_schema.sql needs uuid-ossp AND vector). "
        "A skip here is not a pass; CI must set it. ⚠️ Do NOT export it as "
        "DATABASE_URL — see this module's docstring and §6(i)(2)."
    ),
)

CONSOLE_URL = "https://console.invalid"
DEPLOYMENT_KEY = "cc_depl_fixture_notarealsecret"


# ── The process-singleton scope ─────────────────────────────────────────────

@contextlib.asynccontextmanager
async def tenant_engine_scope(url: str):
    """Point ``acb_common.db`` at the tenant ladder DSN for one test.

    ⚠️ **The singletons are reset on SETUP as well as teardown.** ``_ENGINE``
    and ``_SESSION_FACTORY`` are module-level and process-wide; an earlier
    suite in the same process can have built one against a *different* DSN, and
    a fixture that only cleaned up after itself would then run every assertion
    below against that other database and pass or fail for reasons nothing here
    controls.

    The engine is disposed **inside the test's own event loop** — asyncpg
    connections belong to the loop that opened them, so a synchronous teardown
    running after the loop closed would leak them for the length of the run.

    ⚠️ **Setup DISPOSES what it drops** *(review note 2, 2026-08-18)*. It used
    to set ``_ENGINE = None`` and walk away, orphaning an engine an earlier
    suite had built with its pool still holding live sockets — once per suite,
    for the length of the run. Teardown always disposed; setup only forgot,
    which is the harder half to notice. Fence:
    ``test_the_scope_disposes_the_engine_it_finds_ALREADY_built``.
    """
    from acb_common import db as shared

    prior = os.environ.get("DATABASE_URL")
    stranded = shared._ENGINE
    shared._ENGINE = None
    shared._SESSION_FACTORY = None
    if stranded is not None:
        await stranded.dispose()
    os.environ["DATABASE_URL"] = url
    try:
        yield
    finally:
        engine = shared._ENGINE
        shared._ENGINE = None
        shared._SESSION_FACTORY = None
        if engine is not None:
            await engine.dispose()
        if prior is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prior


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _schema():
    """Build the tenant schema from ``infra/postgres/``, then replay it.

    Applied twice on purpose: the numbered ladder is idempotent by
    construction, the deploy replays the whole thing every time, and a claim of
    replay-safety that holds only by reading the DDL is unenforceable.
    """
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def db():
    eng = create_engine(_URL, future=True)
    yield eng
    eng.dispose()


class FakeConsole:
    """A real ``httpx`` client over a mock transport, so the request is tested.

    Holds every request it saw, so a fence can assert what crossed the wire —
    the bearer, the body, and the absence of an ``org_slug`` — rather than
    trusting the module's docstring about it.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._answer = self._empty

    # -- scripted answers, one per outcome ---------------------------------
    def _empty(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"organizations": []})

    def answers(self, *orgs: dict) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "identity_id": str(uuid.uuid4()),
                "organizations": list(orgs),
            })
        self._answer = responder

    def answers_empty(self) -> None:
        self._answer = self._empty

    def refuses(self, state: str) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"detail": f"organization is {state}"})
        self._answer = responder

    def is_at_cap(self) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={
                "reason": "seat_cap_exceeded",
                "buy_more": {"plan_slug": "core", "purchased": 1,
                             "assigned": 1, "additional_seats_required": 1,
                             "price_inr": 1000},
            })
        self._answer = responder

    def goes_dark(self) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("the Console is unreachable")
        self._answer = responder

    def answers_status(self, status: int, **body) -> None:
        """Any status at all — the shapes a REVERSE PROXY produces, not a route.

        A 502/503/504 is nginx's HTML error page, and a 401 is a rotated
        `cc_depl_` key: neither is the Console *deciding* anything about the
        person, and both were previously read as one. Bodies are deliberately
        not JSON by default, because a gateway's error page is not.
        """
        def responder(request: httpx.Request) -> httpx.Response:
            if body:
                return httpx.Response(status, json=body)
            return httpx.Response(status, text="<html>502 Bad Gateway</html>")
        self._answer = responder

    # -- plumbing -----------------------------------------------------------
    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._answer(request)

    def client(self):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self._handle), timeout=5.0
        )


@pytest.fixture
async def wired(monkeypatch):
    """A deployment wired to a Customer Console, pointed at the tenant ladder."""
    from acb_auth import console_resolve
    from acb_common.settings import get_settings

    console = FakeConsole()
    async with tenant_engine_scope(_URL):
        monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
        monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", DEPLOYMENT_KEY)
        get_settings.cache_clear()
        console_resolve.invalidate()
        monkeypatch.setattr(console_resolve, "_new_http_client", console.client)
        try:
            yield console
        finally:
            console_resolve.invalidate()
            get_settings.cache_clear()


class Recorder:
    """Captures structured log calls so a fence can assert one fired ONCE."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def warning(self, event: str, **kw) -> None:
        self.events.append((event, kw))

    def error(self, event: str, **kw) -> None:
        self.events.append((event, kw))

    def info(self, event: str, **kw) -> None:
        self.events.append((event, kw))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


# ── Fixture helpers — the tenant side, built the way PROVISIONING would ─────

def _provision_org(db, slug: str) -> str:
    """Create the local ``organization`` row an operator's provisioning would.

    ⚠️ **Explicitly, and never by the code under test.** §6(k) names creating
    that row out of scope for CP-2b — it belongs to provisioning — so a fixture
    that let the resolve path create it would be testing a path this ticket
    refuses to build.
    """
    with db.begin() as c:
        return str(c.execute(
            text("INSERT INTO organization (slug, display_name) "
                 "VALUES (:s, :d) RETURNING id"),
            {"s": slug, "d": slug.upper()},
        ).scalar_one())


def _org_row(db, slug: str) -> dict:
    with db.begin() as c:
        row = c.execute(
            text("SELECT id::text AS id, registry_status, "
                 "       registry_capabilities "
                 "  FROM organization WHERE slug = :s"),
            {"s": slug},
        ).mappings().first()
    return dict(row) if row else {}


def _membership(db, email: str) -> list[dict]:
    with db.begin() as c:
        return [
            dict(r) for r in c.execute(
                text("SELECT o.slug AS slug, m.resolved_at AS resolved_at "
                     "  FROM user_identity ui "
                     "  JOIN org_membership m ON m.user_id = ui.id "
                     "  JOIN organization o ON o.id = m.organization_id "
                     " WHERE lower(ui.email) = :e ORDER BY o.slug"),
                {"e": email.lower()},
            ).mappings()
        ]


def _identity_rows(db, email: str) -> int:
    with db.begin() as c:
        return c.execute(
            text("SELECT count(*) FROM user_identity WHERE lower(email) = :e"),
            {"e": email.lower()},
        ).scalar_one()


def _backdate(db, email: str, seconds: int) -> None:
    """Move ``resolved_at`` into the past.

    The subject of these tests is the freshness ARITHMETIC, not the clock, so
    the clock is set rather than waited on — and never slept past, which would
    make the suite slow AND flaky at once.
    """
    stamp = datetime.now(UTC) - timedelta(seconds=seconds)
    with db.begin() as c:
        c.execute(
            text("UPDATE org_membership m SET resolved_at = :t "
                 "  FROM user_identity ui "
                 " WHERE m.user_id = ui.id AND lower(ui.email) = :e "
                 "   AND m.resolved_at IS NOT NULL"),
            {"t": stamp, "e": email.lower()},
        )


def _expire(db, email: str) -> None:
    """Age the cached record just past the TTL, so the next call CONSULTS.

    ⚠️ Needed far more often than it looks, and that is the design working:
    inside ``CUSTOMER_CONSOLE_RESOLVE_TTL_SECONDS`` the box does not ask the
    Console at all, so a test that changes the Console's answer and calls again
    would keep getting the cached one. Every fence below that wants a NEW
    answer has to earn it by expiring the old one.
    """
    from acb_common.settings import get_settings

    _backdate(db, email, get_settings().customer_console_resolve_ttl_seconds + 60)


def _future_seconds() -> float:
    """Far enough into the future to be a broken clock, not jitter.

    Read from the module's own named tolerance rather than hard-coded, so
    changing that number changes this suite's INPUT and not its meaning.
    """
    from acb_auth import console_resolve

    return console_resolve._CLOCK_SKEW_TOLERANCE_SECONDS + 60


def _email() -> str:
    return f"person-{uuid.uuid4().hex[:10]}@customer.example"


def _slug() -> str:
    return f"acme-{uuid.uuid4().hex[:10]}"


def _answer(slug: str, *, status: str = "active", sign_in: bool = True,
            write_seats: bool = True, use_ai: bool = True,
            organization_id: str | None = None) -> dict:
    """One entry of the Console's 200 body, in the shipped shape (clause 12)."""
    return {
        "organization_id": organization_id or str(uuid.uuid4()),
        "slug": slug,
        "placement": "primary",
        "status": status,
        "seat": "allocated",
        "capabilities": {
            "sign_in": sign_in, "write_seats": write_seats, "use_ai": use_ai,
        },
    }


# ══ Clause 6 — fail closed, degrade bounded, both TTLs pinned ════════════════

class TestFailClosedDegradeBounded:
    async def test_an_uncached_person_is_refused_when_the_console_is_unreachable(
        self, wired, db
    ):
        """No cached resolution → REFUSE. Fail closed.

        And with the *service-unavailable* code, never "access denied": the
        person has done nothing wrong, and a wrong-looking denial generates a
        support ticket and a password reset that fix nothing (D33.1).
        """
        from acb_auth import console_resolve

        wired.goes_dark()
        decision = await console_resolve.resolve_for_signin(_email())

        assert decision.admit is False
        assert decision.code == console_resolve.CONSOLE_UNAVAILABLE

    async def test_a_cached_person_proceeds_inside_the_ttl(self, wired, db):
        """Inside the TTL the Console is not even asked."""
        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))
        assert (await console_resolve.resolve_for_signin(email)).admit
        asked_once = len(wired.requests)

        console_resolve.invalidate()          # drop the per-process dict only
        wired.goes_dark()
        decision = await console_resolve.resolve_for_signin(email)

        assert decision.admit is True
        assert decision.source == "cache-fresh"
        assert len(wired.requests) == asked_once, "it asked inside the TTL"

    async def test_a_cached_person_is_refused_past_the_staleness_ceiling(
        self, wired, db
    ):
        """Past the ceiling, sign-in fails closed even for a cached person.

        The input is READ from the named setting, so changing the default
        changes this test's input and not its meaning.
        """
        from acb_auth import console_resolve
        from acb_common.settings import get_settings

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))
        assert (await console_resolve.resolve_for_signin(email)).admit

        ceiling = get_settings().customer_console_resolve_max_staleness_seconds
        _backdate(db, email, ceiling + 60)
        console_resolve.invalidate()
        wired.goes_dark()

        decision = await console_resolve.resolve_for_signin(email)
        assert decision.admit is False
        assert decision.code == console_resolve.CONSOLE_UNAVAILABLE

    async def test_a_cached_person_proceeds_between_the_ttl_and_the_ceiling(
        self, wired, db
    ):
        """The middle of the pair, which is the whole reason it IS a pair.

        Past the TTL the box wants to re-consult; unable to, it degrades on the
        cache up to the ceiling rather than locking a paying customer out
        because a third-party service is down.
        """
        from acb_auth import console_resolve
        from acb_common.settings import get_settings

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))
        assert (await console_resolve.resolve_for_signin(email)).admit

        settings = get_settings()
        _backdate(db, email, settings.customer_console_resolve_ttl_seconds + 60)
        console_resolve.invalidate()
        wired.goes_dark()

        decision = await console_resolve.resolve_for_signin(email)
        assert decision.admit is True
        assert decision.source == "cache-stale"

    async def test_a_cached_dead_org_refuses_without_asking_the_console(
        self, wired, db
    ):
        """A cached dead state is a FACT, not a stale hint.

        Driven by a **403** — the only outcome that can produce
        ``sign_in: false`` — and then asserted inside *and* outside the TTL,
        with the Console unreachable, with no further request made.

        ⚠️ **Bounded by the staleness ceiling since the P1-2 repair
        (2026-08-18)**, so the ages swept here stop just short of it. Past the
        ceiling the record is re-consulted rather than believed forever — see
        ``TestTheDeadStateIsBoundedByTheCeiling`` for why that is not a
        relaxation.
        """
        from acb_auth import console_resolve
        from acb_common.settings import get_settings

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))
        assert (await console_resolve.resolve_for_signin(email)).admit

        wired.refuses("deleted")
        _expire(db, email)
        console_resolve.invalidate()
        assert (await console_resolve.resolve_for_signin(email)).admit is False
        asked = len(wired.requests)

        settings = get_settings()
        wired.goes_dark()
        for age in (
            0,
            settings.customer_console_resolve_ttl_seconds + 60,
            settings.customer_console_resolve_max_staleness_seconds - 60,
        ):
            _backdate(db, email, age)
            console_resolve.invalidate()
            decision = await console_resolve.resolve_for_signin(email)
            assert decision.admit is False, f"admitted at age {age}"
            assert decision.code == console_resolve.ACCESS_DENIED
            assert decision.source == "cache-dead"
        assert len(wired.requests) == asked, "it asked about a dead record"

    async def test_a_cached_suspended_org_keeps_login_and_loses_write_seats(
        self, wired, db
    ):
        """A 200 with ``sign_in: true`` and the two locks false.

        ``suspended`` keeps the door open deliberately — a customer who cannot
        log in cannot pay you — and what it caches is the LOCKS, which change
        no sign-in decision in this ticket.
        """
        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug, status="suspended", sign_in=True,
                              write_seats=False, use_ai=False))

        decision = await console_resolve.resolve_for_signin(email)

        assert decision.admit is True
        assert decision.capabilities == {
            "sign_in": True, "write_seats": False, "use_ai": False}
        stored = _org_row(db, slug)["registry_capabilities"]
        assert stored == {"sign_in": True, "write_seats": False, "use_ai": False}

    async def test_staleness_never_relaxes_a_cached_state(self, wired, db):
        """Expiry may only make the cache MORE restrictive, never less.

        Two ages, because the P1-2 repair (2026-08-18) changed the WORD without
        changing the direction, and pretending otherwise would make this fence
        a lie:

        * **inside the ceiling** the dead record still refuses with the
          dead-state code, without asking anybody; and
        * **past the ceiling** the record is re-consulted — but the Console is
          unreachable and the record is beyond the ceiling, so the uncached path
          refuses anyway, now with ``ConsoleUnavailable``.

        Either way **nothing is admitted by getting older**, which is what this
        fence has always been about. *(It previously asserted ``AccessDenied``
        at ten times the ceiling. That assertion is what pinned an unrecoverable
        state in place: the short-circuit ran ahead of every freshness bound at
        any age forever, and the only thing that could clear it was a 200 the
        short-circuit itself prevented — see the class below.)*
        """
        from acb_auth import console_resolve
        from acb_common.settings import get_settings

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))
        await console_resolve.resolve_for_signin(email)
        wired.refuses("deleted")
        _expire(db, email)
        console_resolve.invalidate()
        await console_resolve.resolve_for_signin(email)

        ceiling = get_settings().customer_console_resolve_max_staleness_seconds
        wired.goes_dark()

        _backdate(db, email, ceiling - 60)
        console_resolve.invalidate()
        inside = await console_resolve.resolve_for_signin(email)
        assert inside.admit is False
        assert inside.code == console_resolve.ACCESS_DENIED

        _backdate(db, email, ceiling * 10)
        console_resolve.invalidate()
        past = await console_resolve.resolve_for_signin(email)
        assert past.admit is False, "age alone admitted a refused person"
        assert past.code == console_resolve.CONSOLE_UNAVAILABLE

    async def test_a_403_records_only_the_fact_it_proved(self, wired, db):
        """``{"sign_in": false}`` and nothing else.

        ``write_seats`` and ``use_ai`` are **absent**, not ``false``: the 403
        body does not carry them and a value the Console never sent is minted
        information. A missing key means *not observed*.

        ``registry_status`` is **untouched** — it keeps whatever the last 200
        wrote. The 403 body is ``{"detail": "organization is <state>"}``, a
        human sentence rather than a field, and parsing a word out of it to
        populate a typed column would couple this box to the Console's message
        wording.
        """
        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug, status="active"))
        await console_resolve.resolve_for_signin(email)
        assert _org_row(db, slug)["registry_status"] == "active"

        wired.refuses("deleted")
        _expire(db, email)
        console_resolve.invalidate()
        await console_resolve.resolve_for_signin(email)

        row = _org_row(db, slug)
        assert row["registry_capabilities"] == {"sign_in": False}
        assert "write_seats" not in row["registry_capabilities"]
        assert "use_ai" not in row["registry_capabilities"]
        assert row["registry_status"] == "active", "the 403 rewrote the word"


# ══ §6(j) — the four outcomes, each handled ══════════════════════════════════

class TestTheFourOutcomes:
    @pytest.mark.parametrize(
        "outcome",
        ["200-with-one", "403", "200-with-many", "200-with-none"],
    )
    async def test_the_four_resolve_outcomes_are_each_handled(
        self, wired, db, outcome
    ):
        """§6(j)'s table, parametrised. A FIFTH outcome has to be named here.

        ⚠️ **No case feeds a 200 body with ``sign_in: false``.** That input
        cannot occur — the Console's arm partitions on ``can_sign_in``, 403s
        when nothing is admissible and builds its array from the admissible
        alone — and a test that manufactured it would be testing its own
        fixture.
        """
        from acb_auth import console_resolve

        email = _email()
        one, two = _slug(), _slug()
        _provision_org(db, one)
        _provision_org(db, two)

        if outcome == "200-with-one":
            wired.answers(_answer(one))
        elif outcome == "403":
            wired.refuses("deleted")
        elif outcome == "200-with-many":
            wired.answers(_answer(one), _answer(two))
        else:
            wired.answers_empty()

        decision = await console_resolve.resolve_for_signin(email)

        if outcome == "200-with-one":
            assert decision.admit is True
            assert decision.slug == one
            assert _membership(db, email), "the whole record was not cached"
        elif outcome == "200-with-many":
            assert decision.admit is False
            assert decision.code == console_resolve.WORKSPACE_CHOOSER_REQUIRED
            assert _membership(db, email) == [], "it cached an ambiguous answer"
        else:
            assert decision.admit is False
            assert decision.code == console_resolve.ACCESS_DENIED

    async def test_a_multi_org_refusal_does_not_poison_the_cache(
        self, wired, db
    ):
        """Refusing for AMBIGUITY must not revoke an admission already earned.

        The answer still lists organizations this deployment serves, so §6(c)'s
        invalidate trigger does not fire. Stated honestly rather than glossed:
        a person who has SINCE become multi-org keeps their older single-org
        row, so on a later Console outage they are admitted into the one
        organization this box last resolved them into. That is deliberate — it
        admits them somewhere they demonstrably belong, never somewhere they do
        not, and the alternative locks a paying user out on an outage.
        """
        from acb_auth import console_resolve

        email = _email()
        one, two = _slug(), _slug()
        _provision_org(db, one)
        _provision_org(db, two)
        wired.answers(_answer(one))
        assert (await console_resolve.resolve_for_signin(email)).admit

        wired.answers(_answer(one), _answer(two))
        _expire(db, email)
        console_resolve.invalidate()
        refused = await console_resolve.resolve_for_signin(email)
        assert refused.code == console_resolve.WORKSPACE_CHOOSER_REQUIRED

        rows = _membership(db, email)
        assert [r["slug"] for r in rows] == [one]
        assert rows[0]["resolved_at"] is not None, "the cache was poisoned"

    async def test_a_seat_cap_refusal_fails_closed_and_caches_nothing(
        self, wired, db
    ):
        """The FIFTH outcome the ticket's table does not name: a 409 at the cap.

        ⚠️ **A decision this build made and the owner may overrule.** §6(j)
        enumerates four outcomes and clause 12 documents the 409 as the
        Console's shape without saying what the box does with it. It is a
        refusal — the Console did not admit this person — it caches nothing,
        because a status the box cannot read proves nothing worth recording,
        and it carries ``AccessDenied`` because at the cap the person genuinely
        holds no seat and *"ask your admin"* is exactly the remedy. A third
        refusal code is deliberately NOT minted: the ticket names two.
        """
        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.is_at_cap()

        decision = await console_resolve.resolve_for_signin(email)

        assert decision.admit is False
        assert decision.code == console_resolve.ACCESS_DENIED
        assert _membership(db, email) == []


# ══ The dead state is bounded by the CEILING, and self-heals ═════════════════
#
# Repair of review finding **P1-2** (2026-08-18). The 403 writes a PERSON fact
# (`{"sign_in": false}`) onto an ORG row — the org this box previously resolved
# that person into, which may be an unrelated, perfectly live organization —
# and `_READ_SQL` then serves that org-scoped refusal to EVERY member with a
# non-NULL `resolved_at`. The short-circuit ran ahead of every freshness bound
# at ANY age, forever, and the only thing that could clear it was a successful
# 200 the short-circuit itself prevented. Recovery was a manual UPDATE on the
# tenant database. (Amplifier: `lifecycle.capabilities_of` answers
# `STATES["deleted"]` for any UNRECOGNISED status string, so a typo in the
# Console's column 403s a live customer.)
#
# The bound: the short-circuit applies while the record is inside
# MAX_STALENESS. Past it the record is re-consulted like any other. That is not
# a relaxation — with the Console unreachable a past-ceiling record refuses on
# the uncached path anyway, and a genuinely dead organization 403s again and
# re-arms the record. What it buys is that a wrong one heals within 24 hours
# instead of never.

class TestTheDeadStateIsBoundedByTheCeiling:
    async def _make_dead(self, console, db, email: str, slug: str) -> None:
        """Admit once, then take a 403 — the only path that writes the state."""
        from acb_auth import console_resolve

        console.answers(_answer(slug))
        assert (await console_resolve.resolve_for_signin(email)).admit
        console.refuses("deleted")
        _expire(db, email)
        console_resolve.invalidate()
        assert (await console_resolve.resolve_for_signin(email)).admit is False
        assert _org_row(db, slug)["registry_capabilities"] == {"sign_in": False}

    async def test_a_dead_record_past_the_ceiling_is_re_consulted_and_heals(
        self, wired, db
    ):
        """The 403 was wrong (or the organization came back). 24h, not never.

        Console reachable and answering 200 → the person is admitted and the
        org row's capabilities are rewritten from the wire, so the next member
        through is not refused either.
        """
        from acb_auth import console_resolve
        from acb_common.settings import get_settings

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        await self._make_dead(wired, db, email, slug)

        ceiling = get_settings().customer_console_resolve_max_staleness_seconds
        _backdate(db, email, ceiling + 60)
        console_resolve.invalidate()
        wired.answers(_answer(slug))
        asked = len(wired.requests)

        decision = await console_resolve.resolve_for_signin(email)

        assert len(wired.requests) == asked + 1, "it never asked again"
        assert decision.admit is True
        assert decision.source == "console"
        assert _org_row(db, slug)["registry_capabilities"]["sign_in"] is True

    async def test_a_dead_record_past_the_ceiling_still_refuses_with_no_console(
        self, wired, db
    ):
        """Re-consulting is not admitting. A box that cannot ask still refuses.

        This is what keeps "staleness only ever restricts" true after the
        bound: past the ceiling the record buys nothing at all, so the uncached
        path answers — and it fails closed.
        """
        from acb_auth import console_resolve
        from acb_common.settings import get_settings

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        await self._make_dead(wired, db, email, slug)

        ceiling = get_settings().customer_console_resolve_max_staleness_seconds
        _backdate(db, email, ceiling + 60)
        console_resolve.invalidate()
        wired.goes_dark()

        decision = await console_resolve.resolve_for_signin(email)
        assert decision.admit is False
        assert decision.code == console_resolve.CONSOLE_UNAVAILABLE

    async def test_a_dead_record_past_the_ceiling_re_arms_on_a_second_403(
        self, wired, db
    ):
        """A genuinely dead organization is refused again, and recorded again.

        The bound does not forgive anything; it only stops the box believing an
        unverifiable fact forever.
        """
        from acb_auth import console_resolve
        from acb_common.settings import get_settings

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        await self._make_dead(wired, db, email, slug)

        ceiling = get_settings().customer_console_resolve_max_staleness_seconds
        _backdate(db, email, ceiling + 60)
        console_resolve.invalidate()

        decision = await console_resolve.resolve_for_signin(email)
        assert decision.admit is False
        assert decision.code == console_resolve.ACCESS_DENIED
        assert decision.source == "console-refused"
        assert _org_row(db, slug)["registry_capabilities"] == {"sign_in": False}

    async def test_one_persons_403_does_not_lock_their_COLLEAGUES_out_forever(
        self, wired, db
    ):
        """The finding's actual shape: a person fact written on an ORG row.

        Two members of one organization. One takes a 403 — which writes
        ``{"sign_in": false}`` onto the organization — and the other, who was
        admitted minutes earlier and has a live ``resolved_at``, is refused by
        it. Before the bound that was permanent for **every** member and
        recoverable only by hand; now it clears on the first re-consult past the
        ceiling.
        """
        from acb_auth import console_resolve
        from acb_common.settings import get_settings

        colleague, refused, slug = _email(), _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))
        assert (await console_resolve.resolve_for_signin(colleague)).admit
        await self._make_dead(wired, db, refused, slug)

        # The colleague is collateral: their own row is fresh, the org's is not.
        console_resolve.invalidate()
        assert (
            await console_resolve.resolve_for_signin(colleague)
        ).source == "cache-dead"

        ceiling = get_settings().customer_console_resolve_max_staleness_seconds
        _backdate(db, colleague, ceiling + 60)
        console_resolve.invalidate()
        wired.answers(_answer(slug))

        decision = await console_resolve.resolve_for_signin(colleague)
        assert decision.admit is True, (
            "a colleague of a refused person is locked out permanently"
        )


# ══ A backwards clock must not manufacture freshness ═════════════════════════
#
# Repair of review finding **P2** (2026-08-18). `_age_seconds` floored the age
# at zero and its comment claimed the floor was the fail-CLOSED choice — *"a
# server whose clock steps backwards would otherwise produce a negative age
# that reads as fresher than now"*. It is the fail-OPEN one: floored to `0.0`,
# a record stamped in the future reads as **maximally fresh** and is served
# from the cache without anybody being asked. The comment described the bug as
# the fix.
#
# `resolved_at` is the DATABASE's `now()` while the comparison happens against
# the APP process's clock, so the two are genuinely different clocks and a skew
# is a real state, not a hypothetical one.

class TestABackwardsClockDoesNotMakeARecordFRESH:
    async def test_a_record_stamped_in_the_future_is_not_treated_as_fresh(
        self, wired, db
    ):
        """Negative age = STALE, i.e. past-ceiling semantics.

        Console unreachable and the record unusable → refuse, which is the same
        answer this box gives for any record it cannot trust.
        """
        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))
        assert (await console_resolve.resolve_for_signin(email)).admit

        _backdate(db, email, -_future_seconds())          # an hour into the FUTURE
        console_resolve.invalidate()
        wired.goes_dark()

        decision = await console_resolve.resolve_for_signin(email)
        assert decision.admit is False, "a clock skew admitted a stale record"
        assert decision.code == console_resolve.CONSOLE_UNAVAILABLE

    async def test_a_record_stamped_in_the_future_is_RE_CONSULTED(
        self, wired, db
    ):
        """And with the Console up, nobody is punished for the skew.

        Treating it as stale means *ask again*, not *refuse* — the refusal
        above is only what happens when asking is impossible.
        """
        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))
        assert (await console_resolve.resolve_for_signin(email)).admit

        _backdate(db, email, -_future_seconds())
        console_resolve.invalidate()
        asked = len(wired.requests)

        decision = await console_resolve.resolve_for_signin(email)

        assert len(wired.requests) == asked + 1, "it answered from a future row"
        assert decision.admit is True
        assert decision.source == "console"

    async def test_ordinary_sub_minute_skew_does_not_disable_the_cache(
        self, wired, db
    ):
        """The other half of the bound, and it is why a TOLERANCE exists.

        ⚠️ **Measured, not hypothetical**: against the pgvector container this
        suite runs on, ``now()`` came back **0.40 s ahead** of
        ``datetime.now(UTC)``, reproducibly — app and database are different
        machines in the ordinary deployment. With a hard zero floor every
        freshly-written record is born "in the future", reads as stale, and the
        read-through cache silently stops working: one extra Console round trip
        per sign-in and a TTL nobody ever gets.
        """
        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))
        assert (await console_resolve.resolve_for_signin(email)).admit

        _backdate(db, email, -console_resolve._CLOCK_SKEW_TOLERANCE_SECONDS + 5)
        console_resolve.invalidate()
        wired.goes_dark()

        decision = await console_resolve.resolve_for_signin(email)
        assert decision.admit is True, "a sub-minute skew emptied the cache"
        assert decision.source == "cache-fresh"

    async def test_a_DEAD_record_stamped_in_the_future_still_refuses(
        self, wired, db
    ):
        """The skew may not relax a refusal either — in either direction.

        The dead-state short-circuit is bounded by the ceiling now, so a future
        stamp takes the record out of it; what must not happen is that the
        person is then *admitted*. They are not: the box re-consults, cannot,
        and refuses.
        """
        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))
        assert (await console_resolve.resolve_for_signin(email)).admit
        wired.refuses("deleted")
        _expire(db, email)
        console_resolve.invalidate()
        assert (await console_resolve.resolve_for_signin(email)).admit is False

        _backdate(db, email, -_future_seconds())
        console_resolve.invalidate()
        wired.goes_dark()

        assert (await console_resolve.resolve_for_signin(email)).admit is False


# ══ §6(j) row v — a TRANSPORT failure is not an authorization failure ════════
#
# Repair of review finding **P1-1** (2026-08-18). `_post_resolve` collapsed
# every non-200 to `(status, {})` and `resolve_for_signin` mapped everything
# that was not 200/403 to `AccessDenied`. So a Console 502 behind nginx, or a
# rotated `cc_depl_` key answering 401, showed EVERY user of EVERY tenant "your
# account isn't authorized" — the exact wrong-looking denial D33.1 forbids —
# while the same outage over a *closed port* degraded gracefully. Two spellings
# of one event, two opposite behaviours.
#
# The rule now: a status that says *the box could not get an answer* (5xx, 401,
# 408, 429) is an unreachable Console. A status that says *the Console decided*
# (403, 409) keeps its own meaning.

class TestATransportFailureIsNotAnAuthorizationFailure:
    async def test_a_console_5xx_degrades_to_the_cache_like_any_other_outage(
        self, wired, db
    ):
        """A 30-second 502 must not lock out the people this box already knows.

        Cached, past the TTL, inside the ceiling: exactly the window
        `cache-stale` exists for. Before the repair this arrived as
        `AccessDenied` and the cache was never consulted at all.
        """
        from acb_auth import console_resolve
        from acb_common.settings import get_settings

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))
        assert (await console_resolve.resolve_for_signin(email)).admit

        _backdate(db, email,
                  get_settings().customer_console_resolve_ttl_seconds + 60)
        console_resolve.invalidate()
        wired.answers_status(502)

        decision = await console_resolve.resolve_for_signin(email)
        assert decision.admit is True
        assert decision.source == "cache-stale"

    @pytest.mark.parametrize("status", [500, 502, 503, 504, 408, 429])
    async def test_an_unreadable_status_with_nothing_cached_says_unavailable(
        self, wired, db, status
    ):
        """Refuse — but with the copy that is TRUE.

        The person has done nothing wrong; a wrong-looking denial generates a
        support ticket and a password reset that fix nothing (D33.1).
        """
        from acb_auth import console_resolve

        wired.answers_status(status)

        decision = await console_resolve.resolve_for_signin(_email())

        assert decision.admit is False
        assert decision.code == console_resolve.CONSOLE_UNAVAILABLE
        assert decision.source == "unreachable"

    async def test_a_rotated_deployment_key_is_an_outage_not_a_denial(
        self, wired, db
    ):
        """401 is about the BOX's credential, never about the person.

        An operator rotating `cc_depl_…` without updating this deployment's env
        would otherwise tell every user of every tenant they are not
        authorized — a support queue full of password resets that cannot
        possibly help, for a one-line env fix.
        """
        from acb_auth import console_resolve

        wired.answers_status(401, detail="invalid deployment key")

        decision = await console_resolve.resolve_for_signin(_email())

        assert decision.admit is False
        assert decision.code == console_resolve.CONSOLE_UNAVAILABLE
        assert decision.code != console_resolve.ACCESS_DENIED

    async def test_a_403_and_a_409_still_mean_what_they_meant(self, wired, db):
        """The Console DECIDING is untouched by the rule above.

        Only statuses that prove the box could not read an answer are
        reclassified; a refusal and a seat cap are answers.
        """
        from acb_auth import console_resolve

        wired.refuses("deleted")
        refused = await console_resolve.resolve_for_signin(_email())
        assert refused.code == console_resolve.ACCESS_DENIED
        assert refused.source == "console-refused"

        wired.is_at_cap()
        capped = await console_resolve.resolve_for_signin(_email())
        assert capped.code == console_resolve.ACCESS_DENIED
        assert capped.source == "console-error"


# ══ Clause 7 — a lifecycle change is RECORDED within the stated bound ════════

class TestTheRecord:
    async def test_a_suspension_is_recorded_on_the_deployment_within_the_ttl(
        self, wired, db
    ):
        """Console reachable → the locks land no later than the TTL.

        Login still works throughout, which is the distinction the one state
        machine exists to keep: features locked, door open, data kept.
        """
        from acb_auth import console_resolve
        from acb_common.settings import get_settings

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug, status="active"))
        await console_resolve.resolve_for_signin(email)
        assert _org_row(db, slug)["registry_capabilities"]["write_seats"] is True

        wired.answers(_answer(slug, status="suspended", write_seats=False,
                              use_ai=False))
        _backdate(db, email, get_settings().customer_console_resolve_ttl_seconds + 1)
        console_resolve.invalidate()

        decision = await console_resolve.resolve_for_signin(email)
        assert decision.admit is True, "a suspension closed the door"
        stored = _org_row(db, slug)["registry_capabilities"]
        assert stored == {"sign_in": True, "write_seats": False, "use_ai": False}

    async def test_a_partitioned_deployment_refuses_a_cached_person_at_the_ceiling(
        self, wired, db
    ):
        """Console unreachable → the change lands no later than the ceiling.

        A box that cannot ask cannot learn, so the ceiling is the honest bound
        and it is enforced rather than assumed.
        """
        from acb_auth import console_resolve
        from acb_common.settings import get_settings

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))
        await console_resolve.resolve_for_signin(email)

        ceiling = get_settings().customer_console_resolve_max_staleness_seconds
        wired.goes_dark()

        _backdate(db, email, ceiling - 60)
        console_resolve.invalidate()
        assert (await console_resolve.resolve_for_signin(email)).admit is True

        _backdate(db, email, ceiling + 60)
        console_resolve.invalidate()
        assert (await console_resolve.resolve_for_signin(email)).admit is False

    async def test_a_recorded_suspension_changes_no_product_surface(
        self, wired, db
    ):
        """The deployment RECORDS the booleans; nothing else on the box reads them.

        Feature enforcement stays MT-2's ``intersect()`` seam and is a named
        non-goal. This fence states the boundary structurally: outside the
        module that writes them, no tenant-side code reads
        ``registry_capabilities`` at all — so "suspension disables the AI on
        the box" cannot be true by accident.
        """
        from pathlib import Path

        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug, status="suspended", write_seats=False,
                              use_ai=False))
        await console_resolve.resolve_for_signin(email)
        assert _org_row(db, slug)["registry_capabilities"]["use_ai"] is False

        repo = Path(__file__).resolve().parents[2]
        readers = []
        for base in ("apps", "packages"):
            for path in (repo / base).rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                if "registry_capabilities" not in path.read_text(
                    encoding="utf-8-sig"
                ):
                    continue
                readers.append(
                    str(path.relative_to(repo)).replace("\\", "/")
                )
        assert readers == ["packages/acb_auth/acb_auth/console_resolve.py"], (
            f"registry_capabilities gained a reader: {readers} — feature "
            "enforcement stays MT-2's intersect() seam (customer_console.md "
            "§2, and B5's answer in §6(d))"
        )

    async def test_the_record_is_keyed_on_the_slug_not_on_the_console_uuid(
        self, wired, db
    ):
        """The row that moves is the one whose SLUG matches the answer.

        Two local organizations exist; the answer names one. Only that one may
        carry a registry record, and the Console's ``organization_id`` — a UUID
        in a different database — must appear nowhere.
        """
        from acb_auth import console_resolve

        email = _email()
        named, other = _slug(), _slug()
        _provision_org(db, named)
        other_id = _provision_org(db, other)
        console_uuid = str(uuid.uuid4())
        wired.answers(_answer(named, organization_id=console_uuid))

        await console_resolve.resolve_for_signin(email)

        assert _org_row(db, named)["registry_status"] == "active"
        assert _org_row(db, other)["registry_status"] is None
        assert _org_row(db, named)["id"] != console_uuid
        assert other_id != console_uuid


# ══ Clause 8 — idempotent projection upsert ══════════════════════════════════

class TestProjectionUpsert:
    async def test_resolving_five_times_writes_one_projection_row_and_moves_resolved_at(
        self, wired, db
    ):
        """One identity row, one membership row, ``resolved_at`` moved.

        The address is deliberately re-presented in DIFFERENT CASE: 159's
        ``user_identity.email`` is plain ``TEXT`` with a UNIQUE INDEX on
        ``lower(email)`` — not the Console's ``CITEXT`` — so an upsert matching
        on the raw column would mint a second human on a UPN case change (R10).
        """
        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))

        stamps = []
        for i in range(5):
            console_resolve.invalidate()
            presented = email.upper() if i % 2 else email
            assert (await console_resolve.resolve_for_signin(presented)).admit
            stamps.append(_membership(db, email)[0]["resolved_at"])

        assert _identity_rows(db, email) == 1
        assert len(_membership(db, email)) == 1
        assert stamps == sorted(stamps), "resolved_at went backwards"
        assert stamps[-1] >= stamps[0]

    async def test_the_projection_upsert_joins_on_slug_not_on_the_console_uuid(
        self, wired, db
    ):
        """``org_membership.organization_id`` is the LOCAL id, always.

        It REFERENCES the tenant ``organization(id)``, a local
        ``gen_random_uuid()``. Writing the Console's id into it would either
        violate the FK or insert a second organization row and split the tenant
        in half.
        """
        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        local_id = _provision_org(db, slug)
        wired.answers(_answer(slug, organization_id=str(uuid.uuid4())))

        await console_resolve.resolve_for_signin(email)

        with db.begin() as c:
            rows = c.execute(
                text("SELECT m.organization_id::text AS org "
                     "  FROM user_identity ui "
                     "  JOIN org_membership m ON m.user_id = ui.id "
                     " WHERE lower(ui.email) = :e"),
                {"e": email},
            ).scalars().all()
        assert rows == [local_id]

    async def test_the_console_uuid_is_never_written_to_the_projection(
        self, wired, db
    ):
        """A third identifier, joinable by nothing local, is not persisted.

        ``organization`` already carries two (``id``, ``slug``). A third from
        another database is a foot-gun the next reader would mistake for a
        foreign key, so it rides the in-process answer and stops there.
        """
        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        console_uuid = str(uuid.uuid4())
        wired.answers(_answer(slug, organization_id=console_uuid))

        await console_resolve.resolve_for_signin(email)

        with db.begin() as c:
            hits = c.execute(
                text("SELECT count(*) FROM organization "
                     " WHERE id::text = :u "
                     "    OR settings::text LIKE '%' || :u || '%' "
                     "    OR COALESCE(registry_capabilities::text, '') "
                     "       LIKE '%' || :u || '%'"),
                {"u": console_uuid},
            ).scalar_one()
        assert hits == 0

    async def test_a_resolve_for_an_unprovisioned_org_signs_in_and_writes_nothing(
        self, wired, db, monkeypatch
    ):
        """No local row for the slug → sign in, write nothing, warn ONCE.

        Not an error, not a refusal, not an insert. Creating that row is
        provisioning's act (§6(k)), and the box simply has no fallback cache
        for that person until it exists — which degrades to the uncached case,
        i.e. fail-closed on the next outage, the safe direction.
        """
        from acb_auth import console_resolve

        recorder = Recorder()
        monkeypatch.setattr(console_resolve, "_log", recorder)

        email, slug = _email(), _slug()          # deliberately NOT provisioned
        wired.answers(_answer(slug))

        decision = await console_resolve.resolve_for_signin(email)

        assert decision.admit is True
        assert decision.slug == slug
        assert _membership(db, email) == []
        assert _identity_rows(db, email) == 0
        assert recorder.names().count("console_resolve.unprovisioned_org") == 1

    async def test_a_move_to_an_unprovisioned_org_clears_the_OLD_admission(
        self, wired, db
    ):
        """§6(c) trigger (a) fires even when there is nothing to write.

        Repair of review finding **P1-3** (2026-08-18). The unprovisioned-slug
        branch returned *before* ``_forget_others``, so a person the Console had
        MOVED to an organization placed here but not yet bootstrapped kept a
        live ``resolved_at`` on the org they left — and were admitted straight
        back into it, for up to MAX_STALENESS, on the next Console outage. The
        branch's own docstring claimed the opposite ("the box simply has no
        fallback cache for that person").

        "Nothing to cache" and "keep the last thing we cached" are different
        answers, and only one of them degrades to the fail-closed direction
        §6(k) argues for.
        """
        from acb_auth import console_resolve

        email, left, joined = _email(), _slug(), _slug()
        _provision_org(db, left)          # `joined` deliberately NOT provisioned
        wired.answers(_answer(left))
        assert (await console_resolve.resolve_for_signin(email)).admit
        assert _membership(db, email)[0]["resolved_at"] is not None

        wired.answers(_answer(joined))
        _expire(db, email)
        console_resolve.invalidate()

        moved = await console_resolve.resolve_for_signin(email)
        assert moved.admit is True, "the fresh answer stopped admitting them"
        assert moved.slug == joined

        rows = _membership(db, email)
        assert [r["slug"] for r in rows] == [left]
        assert rows[0]["resolved_at"] is None, (
            "the organization this person LEFT kept a live clock"
        )

        # The consequence, asserted rather than reasoned about: with no cache
        # the next outage fails closed instead of admitting them into an
        # organization the registry no longer places them in.
        console_resolve.invalidate()
        wired.goes_dark()
        outage = await console_resolve.resolve_for_signin(email)
        assert outage.admit is False
        assert outage.code == console_resolve.CONSOLE_UNAVAILABLE

    async def test_an_unresolved_row_reads_as_never_resolved_not_as_a_state(
        self, wired, db
    ):
        """R6's argued deviation, fenced: NULL is a state the code can SEE.

        Migration 159 seeds ``org_membership`` from ``app_user`` with a NULL
        clock, and 177 adds the column with no default precisely so those rows
        assert nothing. A person holding such a row must be refused when the
        Console is unreachable — a default would have made every pre-existing
        row claim a registry answer nobody ever received.
        """
        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        org_id = _provision_org(db, slug)
        with db.begin() as c:
            uid = c.execute(
                text("INSERT INTO user_identity (email, display_name) "
                     "VALUES (:e, '') RETURNING id"),
                {"e": email},
            ).scalar_one()
            c.execute(
                text("INSERT INTO org_membership (organization_id, user_id) "
                     "VALUES (:o, :u)"),
                {"o": org_id, "u": uid},
            )

        rows = _membership(db, email)
        assert rows and rows[0]["resolved_at"] is None

        wired.goes_dark()
        decision = await console_resolve.resolve_for_signin(email)

        assert decision.admit is False
        assert decision.code == console_resolve.CONSOLE_UNAVAILABLE


# ══ §6(c) — the invalidate escape hatch ══════════════════════════════════════

class TestInvalidate:
    async def test_invalidate_drops_a_cached_resolution(self, wired, db):
        """The per-process dict, in the shape ``access.invalidate`` already has.

        Proven by observing the projection read it forces: with the row gone
        the next call must fail closed, which it can only do if the dict was
        actually dropped.
        """
        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))
        await console_resolve.resolve_for_signin(email)

        with db.begin() as c:
            c.execute(
                text("UPDATE org_membership m SET resolved_at = NULL "
                     "  FROM user_identity ui "
                     " WHERE m.user_id = ui.id AND lower(ui.email) = :e"),
                {"e": email},
            )
        wired.goes_dark()

        console_resolve.invalidate(email)
        assert (await console_resolve.resolve_for_signin(email)).admit is False

    async def test_an_org_no_longer_placed_here_is_dropped_from_the_cache(
        self, wired, db
    ):
        """Moving an org off this deployment revokes on the next answer.

        §6(c)'s trigger (a). The old row's clock goes back to NULL — never
        observed — so a later outage cannot admit the person into an
        organization this box no longer serves.
        """
        from acb_auth import console_resolve

        email = _email()
        moved, kept = _slug(), _slug()
        _provision_org(db, moved)
        _provision_org(db, kept)

        wired.answers(_answer(moved))
        await console_resolve.resolve_for_signin(email)
        assert [r["slug"] for r in _membership(db, email)] == [moved]

        wired.answers(_answer(kept))
        _expire(db, email)
        console_resolve.invalidate()
        await console_resolve.resolve_for_signin(email)

        rows = {r["slug"]: r["resolved_at"] for r in _membership(db, email)}
        assert rows[moved] is None, "the moved org kept a live clock"
        assert rows[kept] is not None

    async def test_the_empty_answer_forgets_everything(self, wired, db):
        """Outcome iv fires ``invalidate()`` — row and dict alike.

        A per-process dict clear alone would survive nothing: a restart would
        resurrect the admission from the row, which is what makes the ceiling
        the real bound and this trigger worth having.
        """
        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))
        await console_resolve.resolve_for_signin(email)
        assert _membership(db, email)[0]["resolved_at"] is not None

        wired.answers_empty()
        _expire(db, email)
        console_resolve.invalidate()
        assert (await console_resolve.resolve_for_signin(email)).admit is False

        assert _membership(db, email)[0]["resolved_at"] is None


# ══ The wire, and the DSN discipline ═════════════════════════════════════════

class TestTheWireAndTheDsn:
    async def test_the_request_carries_the_email_and_the_key_and_no_org(
        self, wired, db
    ):
        """R11 at its strongest reading: the caller makes no tenant claim.

        Asserted against the request that actually crossed a real ``httpx``
        transport, not against the module's docstring about it.
        """
        from acb_auth import console_resolve

        email, slug = _email(), _slug()
        _provision_org(db, slug)
        wired.answers(_answer(slug))

        await console_resolve.resolve_for_signin(email, display_name="Ada L")

        assert len(wired.requests) == 1
        request = wired.requests[0]
        assert str(request.url) == f"{CONSOLE_URL}/registry/resolve"
        assert request.headers["authorization"] == f"Bearer {DEPLOYMENT_KEY}"
        body = json.loads(request.content)
        assert body == {"email": email, "display_name": "Ada L"}
        assert "org_slug" not in body

    async def test_an_unwired_deployment_admits_without_asking_anything(
        self, monkeypatch, db
    ):
        """Ships dark: unset means today's behaviour, byte for byte.

        No HTTP call, no query, no projection write. The settings are declared;
        wiring a live deployment is OWNER-GATE and this is what "declared but
        inert" has to mean for that split to be safe.
        """
        from acb_auth import console_resolve
        from acb_common.settings import get_settings

        console = FakeConsole()
        monkeypatch.setattr(console_resolve, "_new_http_client", console.client)
        monkeypatch.delenv("CUSTOMER_CONSOLE_URL", raising=False)
        monkeypatch.delenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", raising=False)
        get_settings.cache_clear()
        try:
            email = _email()
            decision = await console_resolve.resolve_for_signin(email)

            assert decision.admit is True
            assert decision.code is None
            assert decision.source == "unwired"
            assert console.requests == []
            assert _identity_rows(db, email) == 0
        finally:
            get_settings.cache_clear()

    async def test_half_configured_is_not_wired(self, monkeypatch):
        """An address with no key (or a key with no address) is not "partly on".

        It is a misconfiguration, and the fail-open reading of it — guess the
        rest — is the posture CP-0 existed to remove.

        ⚠️ **This proves the PYTHON half only, and reading it as the whole
        ship-dark guarantee was finding F1** (2026-08-18). Whether the browser
        tier even asks is decided in Next, by `CUSTOMER_CONSOLE_RESOLVE_ENABLED`
        (default unset = OFF); its fences are `signin.test.ts`'s *"is inert
        until CUSTOMER_CONSOLE_RESOLVE_ENABLED is exactly 'true'"* and *"does
        not arm itself off CUSTOMER_CONSOLE_URL (F1)"*. Two switches, two
        fences, because they are on opposite sides of a language boundary —
        `customer_console.md` §6(f)/§6(g).
        """
        from acb_auth import console_resolve
        from acb_common.settings import get_settings

        for present, absent in (
            ("CUSTOMER_CONSOLE_URL", "CUSTOMER_CONSOLE_DEPLOYMENT_KEY"),
            ("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", "CUSTOMER_CONSOLE_URL"),
        ):
            monkeypatch.setenv(present, "something")
            monkeypatch.delenv(absent, raising=False)
            get_settings.cache_clear()
            assert console_resolve.is_wired() is False
        get_settings.cache_clear()

    async def test_the_scope_disposes_the_engine_it_finds_ALREADY_built(self):
        """Setup drops the singletons — and must CLOSE what it drops.

        Review note 2 (2026-08-18). The setup half set ``_ENGINE = None``
        without disposing it, so an engine an earlier suite in this process had
        built (against any DSN) was orphaned with its pool still holding live
        sockets, for the length of the run. Teardown always disposed; setup
        only forgot, which is the harder leak to see and the one that
        accumulates once per suite.

        Exercised against the SAME context manager the fixture uses, nested, so
        this is a fence on the mechanism rather than a restatement of it: a
        disposed engine gets a fresh pool object, and the assertion is that the
        stranded one did.
        """
        from acb_common import db as shared
        from acb_common.db import get_session_factory

        async with tenant_engine_scope(_URL):
            factory = get_session_factory()
            async with factory() as session:
                await session.execute(text("SELECT 1"))
            stranded = shared._ENGINE
            assert stranded is not None
            pool_before = stranded.pool

            async with tenant_engine_scope(_URL):
                assert stranded.pool is not pool_before, (
                    "the engine the setup dropped was never disposed — its "
                    "pool still holds the connections it opened"
                )

    async def test_the_ladder_dsn_does_not_leak_out_of_this_suite(self):
        """§6(i): the in-process ``DATABASE_URL`` is scoped to the fixture.

        Exercises the SAME context manager the fixture uses, so this is a fence
        on the mechanism rather than a restatement of it. After teardown the
        engine singletons are back to ``None`` and ``DATABASE_URL`` is what it
        was — which is what keeps this suite's discipline and
        ``test_tenant_coverage.py``'s separable.
        """
        from acb_common import db as shared
        from acb_common.db import get_session_factory

        before = os.environ.get("DATABASE_URL")

        async with tenant_engine_scope(_URL):
            factory = get_session_factory()
            async with factory() as session:
                assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
            assert shared._ENGINE is not None

        assert shared._ENGINE is None
        assert shared._SESSION_FACTORY is None
        assert os.environ.get("DATABASE_URL") == before

    def test_the_launch_snapshot_is_what_gates_this_suite(self):
        """And it is a DIFFERENT variable from the tenant-coverage gate's.

        Collapsing the two would arm ``test_tenant_coverage.py``'s two DB-gated
        tests in a job where they fail by construction — they need
        ``04_policies.sql`` promoted and a non-superuser role, i.e. MT-1b/1c.
        Their gate must keep reading ``_ACB_DATABASE_URL_AT_LAUNCH`` and this
        one ``_ACB_TENANT_LADDER_URL_AT_LAUNCH``.
        """
        assert "_ACB_TENANT_LADDER_URL_AT_LAUNCH" in os.environ
        assert os.environ["_ACB_TENANT_LADDER_URL_AT_LAUNCH"] == _URL
        assert os.environ.get("_ACB_DATABASE_URL_AT_LAUNCH", "") != _URL
