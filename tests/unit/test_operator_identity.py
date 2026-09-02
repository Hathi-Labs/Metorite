"""The three checks that admit a platform operator — WS-31 **CP-12a**.

Spec: ``project-docs/specs/operator_identity_and_access.md`` §4 · §8.1 · **D64**.

Covers CP-12a done-whens 1-6. The subject is **auth**, so two things bind here
beyond ordinary testing:

* **R8** — the registry read is ``lower(email) = :param``, which is precisely
  the predicate a hermetic fake once matched against NULL and shipped green
  (``store.py``'s own docstring records it). This suite therefore skips
  **loudly** without a real Postgres, and a skip is not a pass.
* **Verified red first** — every refusal below was shown to fail before the
  check that produces it existed. A gate nobody watched fail is a gate nobody
  knows is wired.

Run::

    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_operator_identity.py
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import apply_ladder

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

TENANT = "11111111-2222-3333-4444-555555555555"
DOMAIN = "fracktal.in"

#: The configured, working environment. Each test that wants a BROKEN one
#: copies this and removes or changes exactly the key under test, so a typo in
#: an unrelated key cannot make a refusal look like the refusal being asserted.
ENV = {
    "OPERATOR_ENTRA_TENANT_ID": TENANT,
    "OPERATOR_STAFF_DOMAINS": f"{DOMAIN}, metorite.com",
}


@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
        # ⚠️ Replay. Migration 009 claims idempotency and the ladder helper
        # exists because that claim used to go unchecked (H-25 is the open
        # entry for the rest of the ladder). Two applications in one
        # transaction is the cheapest real proof.
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def conn():
    """A connection whose work is ROLLED BACK, so tests cannot see each other.

    The registry is a single global table with a UNIQUE email and a
    count-based bootstrap gate. Leaking one row from an earlier test would
    silently change what a later test is asserting.
    """
    eng = create_engine(_URL, future=True)
    with eng.connect() as c:
        tx = c.begin()
        try:
            yield c
        finally:
            tx.rollback()
    eng.dispose()


def _email(prefix: str = "op") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@{DOMAIN}"


def _seed(conn, email: str, *, role: str = "viewer", status: str = "active") -> str:
    row = conn.execute(
        text(
            "INSERT INTO operator (email, role, status) "
            "VALUES (:e, :r, :s) RETURNING id"
        ),
        {"e": email.lower(), "r": role, "s": status},
    ).first()
    return str(row[0])


# ── The table exists and says what the spec says ────────────────────────────


def test_the_three_tables_exist_and_are_not_tenant_scoped(conn):
    """⚠️ The absence of ``organization_id`` is the assertion, not an oversight.

    An operator is staff. Staff are not a tenant's members, so a tenant column
    here would be the defect. R5(a) is satisfied by the ladder this table is
    in, not by scoping it — see 009's header.
    """
    for table in ("operator", "operator_session", "operator_elevation"):
        cols = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :t"
                ),
                {"t": table},
            )
        }
        assert cols, f"{table} does not exist"
        assert "organization_id" not in cols, (
            f"{table} grew a tenant column — operators are cross-tenant staff"
        )


@pytest.mark.parametrize("bad_role", ["owner", "root", "superuser", ""])
def test_the_role_check_admits_only_the_three_roles(conn, bad_role):
    """D64.3. A fourth role invented at a call site must not reach the table."""
    with pytest.raises(Exception):
        conn.execute(
            text("INSERT INTO operator (email, role) VALUES (:e, :r)"),
            {"e": _email(), "r": bad_role},
        )


# ── Done-when 5: fails CLOSED when unconfigured ─────────────────────────────


def test_an_unset_tenant_id_refuses_everybody(conn):
    """Done-when 5. Unconfigured is 503, never 'allow'. (D33.1's lesson.)"""
    from customer_console import operators, store

    email = _email()
    _seed(conn, email, role="admin")
    row = store.operator_by_email(conn, email)

    broken = dict(ENV)
    broken.pop("OPERATOR_ENTRA_TENANT_ID")
    with pytest.raises(operators.OperatorUnconfigured):
        operators.admit(row, tid=TENANT, email=email, env=broken)


def test_an_unset_domain_list_refuses_everybody(conn):
    """The second half of done-when 5 — both values are load-bearing."""
    from customer_console import operators, store

    email = _email()
    _seed(conn, email)
    row = store.operator_by_email(conn, email)

    broken = dict(ENV)
    broken.pop("OPERATOR_STAFF_DOMAINS")
    with pytest.raises(operators.OperatorUnconfigured):
        operators.admit(row, tid=TENANT, email=email, env=broken)


def test_unconfigured_is_checked_before_the_registry_is_read(conn):
    """A box with no directory answers 503 without ever consulting the table.

    Different incidents. 503 says *the box is wrong*, 403 says *the person is
    wrong*, and an operator paged at 03:00 should not have to guess which.
    """
    from customer_console import operators

    broken = dict(ENV)
    broken.pop("OPERATOR_ENTRA_TENANT_ID")
    # `row=None` would be a 403 if the registry were consulted first.
    with pytest.raises(operators.OperatorUnconfigured):
        operators.admit(None, tid=TENANT, email=_email(), env=broken)


# ── Done-whens 1-4: the three checks ────────────────────────────────────────


def test_an_identity_from_another_directory_is_refused(conn):
    """Done-when 1. A real, active admin from the WRONG directory is refused.

    Seeded as an admin on purpose: the refusal must not depend on the row
    being weak, or the test would pass for the wrong reason.
    """
    from customer_console import operators, store

    email = _email()
    _seed(conn, email, role="admin")
    row = store.operator_by_email(conn, email)

    with pytest.raises(operators.OperatorForbidden):
        operators.admit(row, tid=str(uuid.uuid4()), email=email, env=ENV)


def test_a_missing_directory_claim_is_refused(conn):
    """An absent ``tid`` must not read as 'no directory configured, allow'."""
    from customer_console import operators, store

    email = _email()
    _seed(conn, email, role="admin")
    row = store.operator_by_email(conn, email)

    for absent in (None, "", "   "):
        with pytest.raises(operators.OperatorForbidden):
            operators.admit(row, tid=absent, email=email, env=ENV)


def test_an_email_outside_the_named_domains_is_refused(conn):
    """Done-when 2. Our directory, a real row, the wrong domain."""
    from customer_console import operators, store

    email = f"contractor-{uuid.uuid4().hex[:8]}@example.com"
    _seed(conn, email, role="admin")
    row = store.operator_by_email(conn, email)

    with pytest.raises(operators.OperatorForbidden):
        operators.admit(row, tid=TENANT, email=email, env=ENV)


def test_an_identity_with_no_registry_row_is_refused(conn):
    """Done-when 3 — **the check a future reader will call redundant**.

    Checks 1 and 2 both pass here. The person is genuinely in our directory,
    on a domain we named. They are still not an operator, and that is D34.4:
    the directory authenticates, the registry entitles.
    """
    from customer_console import operators, store

    email = _email()  # deliberately NOT seeded
    row = store.operator_by_email(conn, email)
    assert row is None

    with pytest.raises(operators.OperatorForbidden):
        operators.admit(row, tid=TENANT, email=email, env=ENV)


@pytest.mark.parametrize("status", ["suspended", "deactivated"])
def test_a_non_active_operator_is_refused(conn, status):
    """Done-when 4. Deactivation seals the row (D63) — it does not admit."""
    from customer_console import operators, store

    email = _email()
    _seed(conn, email, role="admin", status=status)
    row = store.operator_by_email(conn, email)

    with pytest.raises(operators.OperatorForbidden):
        operators.admit(row, tid=TENANT, email=email, env=ENV)


def test_an_active_operator_on_every_role_is_admitted(conn):
    """The positive case — all three roles pass, and the role survives."""
    from customer_console import operators, store

    for role in operators.ROLES:
        email = _email(role)
        _seed(conn, email, role=role)
        row = store.operator_by_email(conn, email)
        admitted = operators.admit(row, tid=TENANT, email=email, env=ENV)
        assert admitted.email == email.lower()
        assert admitted.role == role
        assert admitted.is_admin is (role == operators.ADMIN)


# ── The refusal must not be an oracle ───────────────────────────────────────


def test_every_refusal_reads_identically(conn):
    """⚠️ Four different causes, one message. This is a security property.

    If a wrong-directory refusal read differently from a not-an-operator
    refusal, the sign-in page would tell a stranger which checks they had
    already passed. The CAUSE is in the log, where staff can read it.
    """
    from customer_console import operators, store

    known = _email()
    _seed(conn, known, role="admin")
    known_row = store.operator_by_email(conn, known)

    suspended = _email()
    _seed(conn, suspended, status="suspended")
    suspended_row = store.operator_by_email(conn, suspended)

    outside = f"x-{uuid.uuid4().hex[:8]}@example.com"
    _seed(conn, outside)
    outside_row = store.operator_by_email(conn, outside)

    messages = set()
    for row, tid, email in (
        (known_row, str(uuid.uuid4()), known),      # wrong directory
        (outside_row, TENANT, outside),             # wrong domain
        (None, TENANT, _email()),                   # not in the registry
        (suspended_row, TENANT, suspended),         # suspended
    ):
        with pytest.raises(operators.OperatorForbidden) as caught:
            operators.admit(row, tid=tid, email=email, env=ENV)
        messages.add(str(caught.value))

    assert len(messages) == 1, f"refusals leak which check failed: {messages}"


# ── Email casing: a display choice must not lock somebody out ───────────────


def test_the_registry_read_is_case_insensitive(conn):
    """R8's reason for existing. ``lower(email) = :param`` against real SQL."""
    from customer_console import operators, store

    email = _email()
    _seed(conn, email)

    for presented in (email.upper(), email.title(), f"  {email}  "):
        row = store.operator_by_email(conn, presented)
        assert row is not None, f"{presented!r} did not find the row"
        admitted = operators.admit(row, tid=TENANT, email=presented, env=ENV)
        assert admitted.email == email.lower()


# ── Done-when 6: the bootstrap is ONE-TIME ──────────────────────────────────


def test_the_bootstrap_seeds_one_admin_into_an_empty_registry(conn):
    """Done-when 6, first half."""
    from customer_console import operators, store

    conn.execute(text("DELETE FROM operator_session"))
    conn.execute(text("DELETE FROM operator_elevation"))
    conn.execute(text("DELETE FROM operator"))

    email = _email("founder")
    env = dict(ENV, OPERATOR_BOOTSTRAP_EMAIL=email)

    new_id = operators.bootstrap(conn, env=env)
    assert new_id is not None

    row = store.operator_by_email(conn, email)
    assert row is not None
    assert row["role"] == operators.ADMIN, "the first operator must be able to add"
    assert row["status"] == "active"


def test_the_bootstrap_is_refused_when_any_operator_exists(conn):
    """Done-when 6, second half — the variable goes inert forever."""
    from customer_console import operators

    conn.execute(text("DELETE FROM operator_session"))
    conn.execute(text("DELETE FROM operator_elevation"))
    conn.execute(text("DELETE FROM operator"))
    _seed(conn, _email(), role="viewer")

    env = dict(ENV, OPERATOR_BOOTSTRAP_EMAIL=_email("intruder"))
    with pytest.raises(operators.BootstrapRefused):
        operators.bootstrap(conn, env=env)


def test_deactivating_the_only_admin_does_not_reopen_the_bootstrap(conn):
    """⚠️ The one-step escalation this guard exists to close.

    If the emptiness test counted only ACTIVE operators, then "deactivate the
    sole admin, then bootstrap yourself" would be a two-statement path from
    database reach to platform admin. The count is over every status.
    """
    from customer_console import operators

    conn.execute(text("DELETE FROM operator_session"))
    conn.execute(text("DELETE FROM operator_elevation"))
    conn.execute(text("DELETE FROM operator"))
    _seed(conn, _email(), role="admin", status="deactivated")

    env = dict(ENV, OPERATOR_BOOTSTRAP_EMAIL=_email("intruder"))
    with pytest.raises(operators.BootstrapRefused):
        operators.bootstrap(conn, env=env)


def test_the_bootstrap_refuses_an_email_off_the_named_domains(conn):
    """The bootstrap does not get to skip check 2."""
    from customer_console import operators

    conn.execute(text("DELETE FROM operator_session"))
    conn.execute(text("DELETE FROM operator_elevation"))
    conn.execute(text("DELETE FROM operator"))

    env = dict(ENV, OPERATOR_BOOTSTRAP_EMAIL="someone@example.com")
    with pytest.raises(operators.BootstrapRefused):
        operators.bootstrap(conn, env=env)


def test_an_unset_bootstrap_email_is_not_an_error(conn):
    """A configured box with a populated registry has no use for the value."""
    from customer_console import operators

    assert operators.bootstrap(conn, env=dict(ENV)) is None


# ── The store functions the guards will read ────────────────────────────────


def test_operator_count_counts_every_status(conn):
    from customer_console import store

    conn.execute(text("DELETE FROM operator_session"))
    conn.execute(text("DELETE FROM operator_elevation"))
    conn.execute(text("DELETE FROM operator"))
    _seed(conn, _email(), status="active")
    _seed(conn, _email(), status="suspended")
    _seed(conn, _email(), status="deactivated")

    assert store.operator_count(conn) == 3


def test_active_admin_count_ignores_viewers_and_inactive_admins(conn):
    """The last-admin guard's input (CP-12d). Wrong here, wrong there."""
    from customer_console import store

    conn.execute(text("DELETE FROM operator_session"))
    conn.execute(text("DELETE FROM operator_elevation"))
    conn.execute(text("DELETE FROM operator"))
    _seed(conn, _email(), role="admin", status="active")
    _seed(conn, _email(), role="admin", status="suspended")
    _seed(conn, _email(), role="admin", status="deactivated")
    _seed(conn, _email(), role="editor", status="active")
    _seed(conn, _email(), role="viewer", status="active")

    assert store.operator_active_admin_count(conn) == 1


def test_inserting_the_same_operator_twice_is_not_a_duplicate(conn):
    from customer_console import store

    email = _email()
    first = store.operator_insert(conn, email=email, role="viewer")
    second = store.operator_insert(conn, email=email.upper(), role="admin")

    assert first == second, "casing produced a second row"
    row = store.operator_by_email(conn, email)
    assert row["role"] == "viewer", "the second insert must not re-role anybody"


def test_the_directory_subject_is_written_once_and_never_overwritten(conn):
    """A subject that silently changed is a thing to notice, not to overwrite."""
    from customer_console import store

    email = _email()
    operator_id = store.operator_insert(conn, email=email, role="viewer")

    store.operator_set_directory_subject(
        conn, operator_id=operator_id, subject="entra-object-1"
    )
    assert store.operator_by_email(conn, email)["directory_subject"] == (
        "entra-object-1"
    )

    store.operator_set_directory_subject(
        conn, operator_id=operator_id, subject="entra-object-2"
    )
    assert store.operator_by_email(conn, email)["directory_subject"] == (
        "entra-object-1"
    ), "a second sign-in overwrote the recorded directory principal"


# ── D70: the directory moves to Google Workspace ────────────────────────────
#
# Spec §4.1 check 1 · §8.1 done-whens 1, 5, 30 and 32. The switch is
# ``OPERATOR_SIGNIN_PROVIDER`` and it DEFAULTS to ``azure``, so every case
# above still describes this box with the variable unset.

HD = "hathilabs.com"

#: A configured GOOGLE box. Same shape as ``ENV``, one directory further on.
GOOGLE_ENV = {
    "OPERATOR_SIGNIN_PROVIDER": "google",
    "OPERATOR_GOOGLE_HD": HD,
    "OPERATOR_STAFF_DOMAINS": f"{HD}, metorite.com",
}


def _google_email() -> str:
    return f"op-{uuid.uuid4().hex[:10]}@{HD}"


def test_an_unset_provider_variable_still_means_entra():
    """⚠️ **The ship-dark property, stated as a test.**

    D70 moves the directory. It must not move a box that has not been told
    to move. An unset variable is today's behaviour, unchanged.
    """
    from customer_console import operators

    assert operators.signin_provider({}) == operators.AZURE_PROVIDER
    assert operators.signin_provider({"OPERATOR_SIGNIN_PROVIDER": "  "}) == (
        operators.AZURE_PROVIDER
    )
    assert operators.staff_directory_id(ENV) == TENANT


def test_a_google_box_reads_the_hosted_domain_and_admits(conn):
    """Done-when 1 on the Google path — the positive half.

    Without it, every refusal below could pass because the whole path is
    broken rather than because the right thing was refused.
    """
    from customer_console import operators, store

    email = _google_email()
    _seed(conn, email, role="admin")
    row = store.operator_by_email(conn, email)

    admitted = operators.admit(row, tid=HD, email=email, env=GOOGLE_ENV)
    assert admitted.email == email
    assert admitted.role == "admin"


def test_a_google_identity_with_no_hosted_domain_is_refused(conn):
    """🔴 **Done-when 30, at the module.** The personal-account attack.

    The row is an ACTIVE admin and the domain is one we named, so checks 2
    and 3 both pass. Only the missing ``hd`` refuses, which is the whole
    point: anybody who receives mail at a staff domain can mint a verified
    Google account, and that account carries no ``hd``.
    """
    from customer_console import operators, store

    email = _google_email()
    _seed(conn, email, role="admin")
    row = store.operator_by_email(conn, email)

    for absent in (None, "", "   "):
        with pytest.raises(operators.OperatorForbidden):
            operators.admit(row, tid=absent, email=email, env=GOOGLE_ENV)


def test_another_workspace_domain_is_refused(conn):
    """Done-when 1. A real Google Workspace, and not ours."""
    from customer_console import operators, store

    email = _google_email()
    _seed(conn, email, role="admin")
    row = store.operator_by_email(conn, email)

    with pytest.raises(operators.OperatorForbidden):
        operators.admit(row, tid="other-company.com", email=email,
                        env=GOOGLE_ENV)


def test_the_entra_tenant_id_does_not_open_a_google_box(conn):
    """The two directories share no value. Neither may stand in for the other."""
    from customer_console import operators, store

    email = _google_email()
    _seed(conn, email, role="admin")
    row = store.operator_by_email(conn, email)

    with pytest.raises(operators.OperatorForbidden):
        operators.admit(row, tid=TENANT, email=email, env=GOOGLE_ENV)


def test_the_hosted_domain_comparison_folds_case(conn):
    """A DNS domain is case-insensitive, and ``_check_domain`` already folds
    the email half. A capital letter in an env line must not lock the team
    out of a live console."""
    from customer_console import operators, store

    email = _google_email()
    _seed(conn, email)
    row = store.operator_by_email(conn, email)

    shouty = dict(GOOGLE_ENV, OPERATOR_GOOGLE_HD="HathiLabs.COM")
    assert operators.admit(row, tid=HD, email=email, env=shouty).email == email
    assert operators.admit(
        row, tid="HATHILABS.com", email=email, env=GOOGLE_ENV
    ).email == email


def test_an_unset_hosted_domain_refuses_everybody(conn):
    """Done-when 5, rewritten by D70. ``OPERATOR_GOOGLE_HD`` fails CLOSED."""
    from customer_console import operators, store

    email = _google_email()
    _seed(conn, email, role="admin")
    row = store.operator_by_email(conn, email)

    broken = dict(GOOGLE_ENV)
    broken.pop("OPERATOR_GOOGLE_HD")
    with pytest.raises(operators.OperatorUnconfigured):
        operators.admit(row, tid=HD, email=email, env=broken)


def test_the_directory_getter_never_returns_none():
    """🔴 **Done-when 32's first guard.**

    ``main.py`` compares an identity's claim against this value. Two ``None``
    values compare equal in Python, so a getter that answered ``None`` for an
    unconfigured box would let an identity with no directory claim consume
    the one-time bootstrap. It must raise, on BOTH paths.
    """
    from customer_console import operators

    for env in ({}, {"OPERATOR_SIGNIN_PROVIDER": "google"}):
        with pytest.raises(operators.OperatorUnconfigured):
            operators.staff_directory_id(env)

    for env in (ENV, GOOGLE_ENV):
        value = operators.staff_directory_id(env)
        assert isinstance(value, str) and value.strip()


def test_a_missing_claim_never_matches_the_directory():
    """🔴 **Done-when 32's second guard**, and the one the gate reads.

    ``directory_matches`` is the ONE place that answers "did this sign-in
    come from our directory". A missing claim is ``False`` on every path.
    """
    from customer_console import operators

    for env in (ENV, GOOGLE_ENV):
        for absent in (None, "", "   "):
            assert operators.directory_matches(absent, env) is False

    assert operators.directory_matches(TENANT, ENV) is True
    assert operators.directory_matches(HD, GOOGLE_ENV) is True


def test_an_unconfigured_box_raises_even_for_a_missing_claim():
    """The order the built code already had, kept through the rewrite.

    A box with no directory pinned answers 503 rather than 403, and it does
    so before it looks at the claim. Different incidents.
    """
    from customer_console import operators

    with pytest.raises(operators.OperatorUnconfigured):
        operators.directory_matches(None, {"OPERATOR_STAFF_DOMAINS": HD})


def test_an_unknown_provider_refuses_rather_than_falling_back():
    """A typo must not quietly return the box to the Entra path.

    A silent fallback would admit every operator against a directory the
    reader believed they had left.
    """
    from customer_console import operators

    for bad in ("entra", "microsoft", "gmail", "google-workspace"):
        with pytest.raises(operators.OperatorUnconfigured):
            operators.signin_provider({"OPERATOR_SIGNIN_PROVIDER": bad})


def test_a_passwordless_provider_can_never_be_configured():
    """**D70.2** at the env door. See also done-when 33's constant fence in
    ``test_operator_signin.py``."""
    from customer_console import operators

    for bad in sorted(operators.PASSWORDLESS_PROVIDERS):
        with pytest.raises(operators.OperatorUnconfigured):
            operators.signin_provider({"OPERATOR_SIGNIN_PROVIDER": bad})


def test_the_claim_table_is_what_the_readers_read():
    """🔴 **R7 — the fence for ``operators.DIRECTORY_CLAIM``'s VALUES.**

    A reviewer measured this on 2026-09-01. Only the KEYS of that table were
    live, through ``ALLOWED_PROVIDERS = frozenset(DIRECTORY_CLAIM)``. Changing
    ``GOOGLE_PROVIDER: "hd"`` to ``"email"`` left the whole suite green,
    because ``_azure_tid`` and ``_google_hd`` wrote the names themselves.

    This test renames the claim in the table and asserts each reader follows.
    It fails the moment a reader goes back to a literal.
    """
    from customer_console import operator_signin, operators

    def google(key: str) -> dict:
        return {
            "identities": [
                {"provider": "google",
                 "identity_data": {"email_verified": True, key: HD}},
            ],
        }

    def azure(key: str) -> dict:
        return {
            "identities": [
                {"provider": "azure",
                 "identity_data": {"email_verified": True, key: TENANT}},
            ],
        }

    # The table as it stands. Both readers find the claim.
    assert operator_signin._google_hd(google("hd")) == HD
    assert operator_signin._azure_tid(azure("tid")) == TENANT

    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(operators.DIRECTORY_CLAIM, "google", "hosted_domain")
        patch.setitem(operators.DIRECTORY_CLAIM, "azure", "tenant_id")
        assert operator_signin._google_hd(google("hd")) is None, (
            "_google_hd still reads a literal 'hd' rather than the table"
        )
        assert operator_signin._azure_tid(azure("tid")) is None, (
            "_azure_tid still reads a literal 'tid' rather than the table"
        )
        assert operator_signin._google_hd(google("hosted_domain")) == HD
        assert operator_signin._azure_tid(azure("tenant_id")) == TENANT


def test_the_entra_tenant_id_still_compares_exactly():
    """🔴 **R7 — the fence for spec §4.1 check 1's "compares exactly" claim.**

    ``directory_matches`` folds case on the ``google`` path, because a DNS
    domain is case-insensitive. It must NOT fold on the ``azure`` path. A
    reviewer measured that making the azure path fold left the whole suite
    green on 2026-09-01, so the spec sentence had no fence at all.

    ⚠️ **This pins built behaviour, and it carries a cost worth naming.** An
    Entra directory that returned an upper-case GUID against a lower-case
    ``OPERATOR_ENTRA_TENANT_ID`` would refuse every operator. D70 records that
    we hold no Entra directory, so nobody is on this path. A reader who
    revives it must decide the fold deliberately, and change this test.
    """
    from customer_console import operators

    # ⚠️ A GUID with hex LETTERS in it. The module-level ``TENANT`` is all
    # digits, so ``.upper()`` returns the same string and would prove nothing.
    lower = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    shouty = lower.upper()
    assert shouty != lower

    quiet_env = dict(ENV, OPERATOR_ENTRA_TENANT_ID=lower)
    assert operators.directory_matches(lower, quiet_env) is True
    assert operators.directory_matches(shouty, quiet_env) is False, (
        "the azure path folded case, and a GUID is not a DNS domain"
    )

    loud_env = dict(ENV, OPERATOR_ENTRA_TENANT_ID=shouty)
    assert operators.directory_matches(lower, loud_env) is False
    assert operators.directory_matches(shouty, loud_env) is True


def test_the_bootstrap_binds_to_the_configured_directory(conn):
    """The one-time path fails closed on the Google box too.

    ``bootstrap`` reads the directory value before it counts rows, so a box
    that has not pinned ``OPERATOR_GOOGLE_HD`` cannot mint its first
    operator either.
    """
    from customer_console import operators

    conn.execute(text("DELETE FROM operator_session"))
    conn.execute(text("DELETE FROM operator_elevation"))
    conn.execute(text("DELETE FROM operator"))

    email = _google_email()
    broken = dict(GOOGLE_ENV, OPERATOR_BOOTSTRAP_EMAIL=email)
    broken.pop("OPERATOR_GOOGLE_HD")
    with pytest.raises(operators.OperatorUnconfigured):
        operators.bootstrap(conn, env=broken)

    good = dict(GOOGLE_ENV, OPERATOR_BOOTSTRAP_EMAIL=email)
    assert operators.bootstrap(conn, env=good) is not None


# ── The R8 gate cannot silently disarm ──────────────────────────────────────

_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """A hand-list that nothing checks is a hand-list that goes stale.

    Without the entry this suite still runs in the directory step and still
    SKIPS silently there when ``CUSTOMER_CONSOLE_DATABASE_URL`` is absent,
    while the job reports green. That is the CP-3 disarmed-gate failure, and
    this is the closest a hand-list gets to defending itself.
    """
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(
        encoding="utf-8"
    )
    assert "tests/unit/test_operator_identity.py" in workflow, (
        "this suite is not in pr-check.yml's R8 skip-guard list — without the "
        "entry it can skip in CI while the job reports green"
    )


def test_this_suite_is_named_in_the_spec_verification_block() -> None:
    """A suite absent from §11 is a suite nobody following the spec runs."""
    spec = (
        _ROOT / "project-docs" / "specs" / "operator_identity_and_access.md"
    ).read_text(encoding="utf-8")
    assert "tests/unit/test_operator_identity.py" in spec, (
        "operator_identity_and_access.md §11 does not name this suite — "
        "CP-12a's acceptance would then be verified by nobody"
    )
