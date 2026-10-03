"""WS-17 EM-T8b — the mailbox identity: label and colour slot.

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.4 and §11.7.2.
Decision D-EM-21. Defect MB-8.

R7 fences named here:

* ``email-mailbox-label-rule``: :func:`display_labels` applies the four rules
  of §11.4 in order: a chosen label, the domain, the local part, the address.
  A stored provider name ("Outlook") counts as no label.
* ``email-mailbox-slot-rule``: :func:`lowest_free_slot` takes the lowest slot
  of 1 to 12 that the other mailboxes of the member do not use, and repeats
  the least-used slot when all twelve are in use.
* ``email-mailbox-identity-api`` (R8): a new connect takes the slot that the
  SQL of ``NEXT_SLOT_SQL`` picks, and that slot is the one
  :func:`lowest_free_slot` picks. The account read returns ``color_slot`` and
  ``display_label``. The PATCH writes a label and a slot under the owner
  predicate, a blank label goes back to the default, a bad slot answers 400,
  and neither field restarts the sync loop.
* ``email-color-slot-migration`` (R8): the migration adds a nullable
  ``color_slot`` with a CHECK of 1 to 12 to a fresh ladder and to a promoted
  catalog. Its backfill numbers the mailboxes of each member from 1, oldest
  first, apart from the other members and organizations. A second run
  changes nothing.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_mailbox_identity.py -v -rs
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

import email_ingestion.scheduler as sched
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes.email.mailbox_identity import (
    chosen_label,
    default_labels,
    display_labels,
    lowest_free_slot,
    valid_slot,
)
from gateway.routes.email.transport import accounts, oauth
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._tenant_ladder import INIT_SCHEMA, _exec_file, ladder, tenant_engine_scope

# ``promoted`` is used by name for fixture injection, so the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    _URL,
    _apply_generated_phase,
    app_engine,
    promoted,
)

REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = REPO / "infra/postgres"


@pytest.mark.parametrize("stored", [None, "", "  ", "Outlook", "outlook", "Gmail", "Email"])
def test_a_provider_name_or_a_blank_is_no_choice(stored) -> None:
    assert chosen_label(stored) is None


def test_a_chosen_label_wins_and_is_trimmed() -> None:
    assert chosen_label("  Work ") == "Work"
    assert display_labels([("a", "dana@fracktal.in", "Work")]) == {"a": "Work"}


@pytest.mark.parametrize(("address", "want"), [
    ("vj@fracktal.in", "Fracktal"),
    ("Dana@Dewin.IN", "Dewin"),
    ("dana@constellationspace.io", "Constellationspace"),
    ("dana@outlook.com", "Personal"),
    ("dana@hotmail.com", "Personal"),
    ("dana@live.com", "Personal"),
    ("dana@msn.com", "Personal"),
    ("dana@gmail.com", "Personal"),
    ("dana@yahoo.com", "Personal"),
    ("dana@icloud.com", "Personal"),
])
def test_the_default_label_comes_from_the_domain(address, want) -> None:
    assert display_labels([("a", address, "Outlook")]) == {"a": want}


def test_two_mailboxes_of_one_domain_take_their_local_parts() -> None:
    got = display_labels([
        ("a", "vj@fracktal.in", "Outlook"),
        ("b", "sales@fracktal.in", None),
        ("c", "vj@outlook.com", "Outlook"),
    ])
    assert got == {"a": "Vj", "b": "Sales", "c": "Personal"}


def test_two_personal_mailboxes_take_their_local_parts() -> None:
    got = display_labels([
        ("a", "dana@outlook.com", None),
        ("b", "dana.v+news@gmail.com", None),
    ])
    assert got == {"a": "Dana", "b": "Dana.v"}


def test_the_same_local_part_twice_falls_back_to_the_address() -> None:
    got = display_labels([
        ("a", "dana@fracktal.in", None),
        ("b", "dana@fracktal.com", None),
    ])
    assert got == {"a": "dana@fracktal.in", "b": "dana@fracktal.com"}


def test_a_default_steps_aside_for_a_chosen_label() -> None:
    """A default label never repeats a label that the member chose."""
    got = display_labels([
        ("a", "vj@fracktal.in", "Fracktal"),
        ("b", "sales@fracktal.in", None),
    ])
    assert got == {"a": "Fracktal", "b": "Sales"}


def test_a_local_part_that_repeats_a_label_falls_back_to_the_address() -> None:
    got = display_labels([
        ("a", "x@example.org", "Sales"),
        ("b", "sales@fracktal.in", None),
        ("c", "vj@fracktal.in", None),
    ])
    assert got == {"a": "Sales", "b": "sales@fracktal.in", "c": "Vj"}


def test_two_chosen_labels_can_be_the_same() -> None:
    got = display_labels([("a", "a@x.org", "Work"), ("b", "b@y.org", "Work")])
    assert got == {"a": "Work", "b": "Work"}


def test_the_default_label_is_the_label_with_no_choice_of_its_own() -> None:
    rows = [
        ("a", "vj@fracktal.in", "Fracktal"),
        ("b", "sales@fracktal.in", None),
        ("c", "vj@outlook.com", "Home"),
    ]
    # Cleared, A would clash with the default of B, so both take local parts.
    # C keeps "Personal". The chosen labels of the OTHER mailboxes stay.
    assert default_labels(rows) == {"a": "Vj", "b": "Sales", "c": "Personal"}


def test_one_mailbox_gets_its_domain_label() -> None:
    assert display_labels([("a", "vj@fracktal.in", None)]) == {"a": "Fracktal"}


@pytest.mark.parametrize(("used", "want"), [
    ([], 1),
    ([1], 2),
    ([2, 3], 1),
    ([1, 2, 4], 3),
    ([None, 1], 2),
    ([0, 13, 1], 2),
    (list(range(1, 13)), 1),
    ([*range(1, 13), 1, 2], 3),
])
def test_the_lowest_free_slot(used, want) -> None:
    assert lowest_free_slot(used) == want


@pytest.mark.parametrize(("slot", "ok"), [
    (1, True), (12, True), (0, False), (13, False), (True, False), ("3", False), (None, False),
])
def test_a_valid_slot(slot, ok) -> None:
    assert valid_slot(slot) is ok


# ── R8: the migration ──────────────────────────────────────────────────────


def _migration() -> Path:
    """Found by CONTENT, never by number: R1 can renumber it at merge."""
    found = [
        p for p in _MIGRATIONS.glob("[0-9]*_*.sql")
        if "EM-T8b" in p.read_text(encoding="utf-8")
        and "ADD COLUMN IF NOT EXISTS color_slot" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, f"expected one EM-T8b migration, got {found}"
    return found[0]


def _shape(engine) -> tuple:
    with engine.connect() as c:
        col = c.execute(text(
            "SELECT data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'email_accounts' AND column_name = 'color_slot'"
        )).first()
        checks = sorted(r[0] for r in c.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'email_accounts'::regclass AND contype = 'c' "
            "AND conname = 'email_accounts_color_slot_range'")))
    return (tuple(col) if col else None, checks)


def _slots(engine) -> dict[str, int | None]:
    with engine.connect() as c:
        return {r.email_address: r.color_slot for r in c.execute(text(
            "SELECT email_address, color_slot FROM email_accounts"))}


@pytest.fixture(scope="module")
def upgraded():
    """A PRIVATE promoted catalog that meets the migration last.

    The ladder without the migration, then the four generated phases (the
    shape of production), then mailboxes of three members in two
    organizations, then the migration twice. Dropped at the end. It never
    touches the shared ladder database."""
    admin_url = make_url(_URL)
    name = f"{admin_url.database}_emt8b_{uuid.uuid4().hex[:8]}"
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    maint.dispose()

    eng = create_engine(admin_url.set(database=name), future=True)
    try:
        mig = _migration()
        files = [p for p in ladder() if os.path.basename(p) != mig.name]
        with eng.begin() as conn:
            _exec_file(conn, INIT_SCHEMA)
            for p in files:
                _exec_file(conn, p)
        for phase in ("01_add_columns.sql", "02_backfill.sql",
                      "03_constraints.sql", "04_policies.sql"):
            with eng.begin() as conn:
                _apply_generated_phase(conn, phase)
        before = _shape(eng)

        org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
        base = datetime(2026, 9, 1, tzinfo=UTC)
        # (org, member, address, minutes after base). The member of org A
        # has three mailboxes, made out of order and in mixed case. "Dana"
        # is a member of BOTH organizations.
        rows = [
            (org_a, "dana@t8b.test", "a-third@t8b.test", 30),
            (org_a, "dana@t8b.test", "a-first@t8b.test", 10),
            (org_a, "Dana@T8B.test", "a-second@t8b.test", 20),
            (org_a, "lee@t8b.test", "lee@t8b.test", 5),
            (org_b, "DANA@t8b.test", "b-first@t8b.test", 40),
        ]
        with eng.begin() as conn:
            for org in (org_a, org_b):
                conn.execute(text(
                    "INSERT INTO organization (id, slug, display_name) "
                    "VALUES (:id, :s, :s)"), {"id": org, "s": f"t8b-{org[:8]}"})
            for org, member, address, minutes in rows:
                conn.execute(text(
                    "INSERT INTO email_accounts (user_id, provider, email_address, "
                    "credentials_encrypted, organization_id, created_at) "
                    "VALUES (:u, 'microsoft', :m, 'x', CAST(:o AS uuid), :t)"),
                    {"u": member, "m": address, "o": org,
                     "t": base + timedelta(minutes=minutes)})

        with eng.begin() as conn:
            _exec_file(conn, str(mig))
        first = (_shape(eng), _slots(eng))
        with eng.begin() as conn:
            _exec_file(conn, str(mig))
        second = (_shape(eng), _slots(eng))
        # The member picks colour 7 for one mailbox. A third run must keep it.
        with eng.begin() as conn:
            conn.execute(text(
                "UPDATE email_accounts SET color_slot = 7 "
                "WHERE email_address = 'a-second@t8b.test'"))
            _exec_file(conn, str(mig))
        third = _slots(eng)

        yield SimpleNamespace(engine=eng, before=before, first=first,
                              second=second, third=third)
    finally:
        eng.dispose()
        maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with maint.connect() as c:
            c.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        maint.dispose()


_SHAPE = (
    ("smallint", "YES", None),
    ["CHECK (((color_slot IS NULL) OR ((color_slot >= 1) AND (color_slot <= 12))))"],
)


@_DB_GATE
class TestTheMigrationOnARealDatabase:

    def test_the_fresh_ladder_has_the_column(self, promoted) -> None:  # noqa: F811
        assert _shape(promoted.admin_engine) == _SHAPE

    def test_the_promoted_catalog_had_none_before(self, upgraded) -> None:
        assert upgraded.before == (None, [])

    def test_the_promoted_catalog_gets_a_nullable_column_and_its_check(
        self, upgraded,
    ) -> None:
        assert upgraded.first[0] == _SHAPE

    def test_the_backfill_numbers_each_member_oldest_first(self, upgraded) -> None:
        assert upgraded.first[1] == {
            "a-first@t8b.test": 1,
            "a-second@t8b.test": 2,
            "a-third@t8b.test": 3,
            "lee@t8b.test": 1,
            "b-first@t8b.test": 1,
        }

    def test_a_second_run_changes_nothing(self, upgraded) -> None:
        assert upgraded.second == upgraded.first

    def test_a_rerun_keeps_a_slot_that_the_member_chose(self, upgraded) -> None:
        assert upgraded.third == {**upgraded.first[1], "a-second@t8b.test": 7}

    def test_the_check_refuses_a_slot_outside_the_ramp(self, upgraded) -> None:
        from sqlalchemy.exc import IntegrityError

        for bad in (0, 13):
            with pytest.raises(IntegrityError), upgraded.engine.begin() as c:
                c.execute(text(
                    "UPDATE email_accounts SET color_slot = :s "
                    "WHERE email_address = 'lee@t8b.test'"), {"s": bad})


@pytest.mark.parametrize(("stored", "address", "want"), [
    ("box@x.test", "box@x.test", None),
    ("BOX@x.test ", "box@x.test", None),
    ("Work", "box@x.test", "Work"),
])
def test_a_label_equal_to_the_address_is_no_choice(stored, address, want) -> None:
    assert chosen_label(stored, address) == want
    assert display_labels([("a", address, stored)])["a"] == (want or "X")


# ── R8: the account routes on a real database ──────────────────────────────


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _seed(admin_engine, *, org: str, owner: str, address: str,
          slot: int | None, label: str | None = "Outlook") -> str:
    with admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, label, "
            "credentials_encrypted, organization_id, color_slot) "
            "VALUES (:u, 'microsoft', :m, :l, 'x', CAST(:o AS uuid), :s) "
            "RETURNING id"),
            {"u": owner, "m": address, "l": label, "o": org, "s": slot}).scalar_one())


def _purge(admin_engine, owner: str) -> None:
    with admin_engine.begin() as c:
        c.execute(text("DELETE FROM email_accounts WHERE lower(user_id) = lower(:u)"),
                  {"u": owner})


def _slot_of(admin_engine, account_id: str) -> tuple:
    with admin_engine.connect() as c:
        return tuple(c.execute(text(
            "SELECT color_slot, label FROM email_accounts WHERE id = CAST(:a AS uuid)"),
            {"a": account_id}).one())


@pytest.fixture()
def restarts(monkeypatch) -> list[str]:
    seen: list[str] = []

    async def _refresh(account_id, organization_id=None):
        seen.append(("refresh", account_id))

    async def _remove(account_id):
        seen.append(("remove", account_id))

    monkeypatch.setattr(sched, "refresh_account_sync", _refresh)
    monkeypatch.setattr(sched, "remove_account_sync", _remove)
    return seen


@_DB_GATE
class TestTheIdentityOnARealDatabase:

    @pytest.mark.parametrize("used", [
        [], [1], [2, 3], [1, 2, 4], list(range(1, 13)), [*range(1, 13), 1, 2],
    ])
    async def test_a_new_connect_takes_the_slot_of_the_rule(
        self, promoted, app_engine, used,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"Owner-{uuid.uuid4().hex[:8]}@em-t8b.test"
        for n, slot in enumerate(used):
            _seed(p.admin_engine, org=p.org_b, owner=owner,
                  address=f"old{n}@em-t8b.test", slot=slot)
        # A mailbox of ANOTHER member, and one of the same member in ANOTHER
        # organization, never count.
        _seed(p.admin_engine, org=p.org_b, owner=f"x-{owner}", address="x@em-t8b.test",
              slot=lowest_free_slot(used))
        _seed(p.admin_engine, org=p.org_a, owner=owner, address="a@em-t8b.test",
              slot=lowest_free_slot(used))
        app_dsn = p.app_url.render_as_string(hide_password=False)
        try:
            async with tenant_engine_scope(app_dsn):
                account_id = await oauth._save_account(
                    org=p.org_b, member=owner.lower(), owner=owner,
                    provider="microsoft", mailbox=f"new-{uuid.uuid4().hex[:6]}@em-t8b.test",
                    encrypted_creds="enc")
            assert _slot_of(p.admin_engine, account_id)[0] == lowest_free_slot(used)
        finally:
            _purge(p.admin_engine, owner)
            _purge(p.admin_engine, f"x-{owner}")

    async def test_the_read_and_the_patch(self, promoted, app_engine, restarts):  # noqa: F811
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"owner-{uuid.uuid4().hex[:8]}@em-t8b.test"
        work = _seed(p.admin_engine, org=p.org_b, owner=owner,
                     address="vj@fracktal.in", slot=1)
        sales = _seed(p.admin_engine, org=p.org_b, owner=owner,
                      address="sales@fracktal.in", slot=2, label=None)
        home = _seed(p.admin_engine, org=p.org_b, owner=owner,
                     address="vj@outlook.com", slot=None)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        other = UserContext(email="other@em-t8b.test", role=UserRole.EMPLOYEE,
                            organization_id=p.org_b)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                listed = {a.id: (a.display_label, a.color_slot)
                          for a in await accounts.list_accounts(user=me)}
                assert listed == {work: ("Vj", 1), sales: ("Sales", 2),
                                  home: ("Personal", None)}

                done = await accounts.update_account(
                    work, accounts.AccountUpdateModel(label=" Fracktal ", color_slot=7),
                    user=me)
                assert (done.display_label, done.color_slot, done.label) == (
                    "Fracktal", 7, "Fracktal")
                assert done.default_label == "Vj"
                assert _slot_of(p.admin_engine, work) == (7, "Fracktal")
                # The default of the other Fracktal mailbox steps aside.
                listed = {a.id: a.display_label for a in await accounts.list_accounts(user=me)}
                assert listed[sales] == "Sales"

                back = await accounts.update_account(
                    work, accounts.AccountUpdateModel(label="  "), user=me)
                assert back.label == "" and back.display_label == "Vj"
                assert _slot_of(p.admin_engine, work) == (7, None)

                for bad in (0, 13):
                    with pytest.raises(HTTPException) as exc:
                        await accounts.update_account(
                            work, accounts.AccountUpdateModel(color_slot=bad), user=me)
                    assert exc.value.status_code == 400
                with pytest.raises(HTTPException) as exc:
                    await accounts.update_account(
                        work, accounts.AccountUpdateModel(label="x" * 41), user=me)
                assert exc.value.status_code == 400
                for reserved in ("Outlook", " gmail ", "EMAIL"):
                    with pytest.raises(HTTPException) as exc:
                        await accounts.update_account(
                            work, accounts.AccountUpdateModel(label=reserved), user=me)
                    assert exc.value.status_code == 400

                with pytest.raises(HTTPException) as exc:
                    await accounts.update_account(
                        work, accounts.AccountUpdateModel(color_slot=3), user=other)
                assert exc.value.status_code == 404
                assert _slot_of(p.admin_engine, work)[0] == 7

                made = await accounts.set_default_account(home, user=me)
                assert (made.display_label, made.color_slot) == ("Personal", None)
            assert restarts == [], "a rename or a colour restarted the sync loop"

            # The sync toggle still stops and starts the loop.
            async with tenant_engine_scope(app_dsn):
                await accounts.update_account(
                    work, accounts.AccountUpdateModel(sync_enabled=False), user=me)
                assert restarts == [("remove", work)]
                await accounts.update_account(
                    work, accounts.AccountUpdateModel(sync_enabled=True), user=me)
                assert restarts == [("remove", work), ("refresh", work)]
        finally:
            release_tenant(token)
            _purge(p.admin_engine, owner)


def test_a_slot_must_be_a_real_number() -> None:
    from pydantic import ValidationError

    for bad in (True, "3"):
        with pytest.raises(ValidationError):
            accounts.AccountUpdateModel(color_slot=bad)
    assert accounts.AccountUpdateModel(color_slot=3).color_slot == 3
