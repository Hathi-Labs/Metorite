"""A provisioned member gets a DIRECTORY row — ``ensure_directory_row``.

Spec: ``people_center_app.md`` §2 (the two stores) and §5.3 (`/people/me`).

**The bug this closes.** §2 keeps ``app_user`` and ``gtd_people`` apart on
purpose, and that split is right. What was missing is that **nothing ever wrote
the second one for a member.** §2 names the writers as an import, a resume
upload, or a hand-added row, so every person who signed in opened *My Profile*
and read "An administrator can add you" — including the founder, in an
organization holding no other administrator. Measured on production
2026-09-19: zero rows in the directory, the org owner among the people with
none.

**R8** — every test runs against a REAL Postgres built by the tenant ladder.
The helper is one INSERT whose whole behaviour lives in an ``ON CONFLICT``
clause against migration 148's partial unique index on ``lower(email)``. A
hermetic fake agrees with whatever SQL it is handed, and an ``ON CONFLICT``
target that does not match a real index is an error only Postgres raises.

⚠️ **Writes here are COMMITTED.** ``tenant_session`` owns its transaction and
commits on clean exit, so a rolled-back fixture connection cannot contain it.
Every test seeds a unique address and deletes it in ``finally``.

⚠️ **The ladder does NOT build the tenancy layer** — ``_tenant_ladder`` skips
``infra/postgres/generated/``, so ``gtd_people`` here carries no
``organization_id`` column and no row-level security. That is exactly why the
helper's INSERT does not name the column: in a database that HAS it, the
generated default fills it from ``app.tenant_id``, and in one that does not,
the same statement still runs. H-104 is the open entry for that gap.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope

_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)


@pytest.fixture(scope="module")
def eng():
    engine = create_engine(_URL, future=True)
    with engine.begin() as conn:
        apply_ladder(conn)
    yield engine
    engine.dispose()


def _address() -> str:
    """A fresh address per test — these writes commit and outlive the test."""
    return f"member-{uuid.uuid4().hex[:10]}@directory.example"


def _rows(engine, email: str) -> list[dict]:
    with engine.begin() as conn:
        return [
            dict(r)
            for r in conn.execute(
                text(
                    "SELECT name, email, status, source FROM gtd_people "
                    " WHERE lower(email) = lower(:e)"
                ),
                {"e": email},
            ).mappings()
        ]


def _drop(engine, email: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM gtd_people WHERE lower(email) = lower(:e)"),
            {"e": email},
        )


async def _ensure(org: str, **kwargs) -> bool:
    """Call the helper through the seam a route would use."""
    from acb_common.db import tenant_session
    from gateway.routes.tasks.people import ensure_directory_row

    async with tenant_engine_scope(_URL), tenant_session(org) as db:
        return await ensure_directory_row(db, **kwargs)


class TestTheRowIsWritten:
    async def test_a_provisioned_member_gets_a_directory_row(self, eng):
        email = _address()
        try:
            created = await _ensure(str(uuid.uuid4()), email=email, display_name="Ada Lovelace")

            assert created is True, "the helper reported no insert"
            rows = _rows(eng, email)
            assert len(rows) == 1, "the member has no directory row"
            assert rows[0]["name"] == "Ada Lovelace"
            assert rows[0]["status"] == "active"
        finally:
            _drop(eng, email)

    async def test_an_invited_member_lands_invited_not_active(self, eng):
        """`invited` is not `active` anywhere else in this product, and the
        directory must not be the one place it silently becomes so."""
        email = _address()
        try:
            await _ensure(
                str(uuid.uuid4()),
                email=email,
                display_name="Grace",
                status="invited",
            )

            assert _rows(eng, email)[0]["status"] == "invited"
        finally:
            _drop(eng, email)

    async def test_the_source_records_who_minted_the_row(self, eng):
        """Provenance, so a later sweep can tell these from a hand-added row."""
        email = _address()
        try:
            await _ensure(str(uuid.uuid4()), email=email, display_name="Ada")

            assert _rows(eng, email)[0]["source"] == "member"
        finally:
            _drop(eng, email)

    async def test_a_blank_display_name_falls_back_to_the_local_part(self, eng):
        """An invite carries no name more often than not. A row named "" is
        unfindable in a directory whose whole job is finding people."""
        email = _address()
        try:
            await _ensure(str(uuid.uuid4()), email=email, display_name="")

            assert _rows(eng, email)[0]["name"] == email.split("@")[0]
        finally:
            _drop(eng, email)


class TestItIsSafeToCallTwice:
    async def test_a_second_call_inserts_nothing(self, eng):
        """Re-inviting somebody is a no-op, never a duplicate or a 500.

        The ON CONFLICT target has to match migration 148's partial index
        (`(lower(email)) WHERE email IS NOT NULL`) exactly. A near-miss raises
        `InvalidColumnReference` against a real database and nothing at all
        against a fake.
        """
        email = _address()
        org = str(uuid.uuid4())
        try:
            first = await _ensure(org, email=email, display_name="Ada")
            second = await _ensure(org, email=email, display_name="Ada")

            assert first is True
            assert second is False, "the second call claimed an insert"
            assert len(_rows(eng, email)) == 1
        finally:
            _drop(eng, email)

    async def test_it_does_not_overwrite_a_name_somebody_chose(self, eng):
        """The existing row wins. A member who fixed their own name must not
        have it reverted by an administrator re-sending an invite."""
        email = _address()
        org = str(uuid.uuid4())
        try:
            await _ensure(org, email=email, display_name="A. Lovelace")
            await _ensure(org, email=email, display_name="ada")

            assert _rows(eng, email)[0]["name"] == "A. Lovelace"
        finally:
            _drop(eng, email)

    async def test_the_address_is_matched_case_insensitively(self, eng):
        """148's index is on `lower(email)`, and the self predicate is too. A
        cased twin would be a second row that `/people/me` could never pick
        between."""
        email = _address()
        org = str(uuid.uuid4())
        try:
            await _ensure(org, email=email, display_name="Ada")
            again = await _ensure(org, email=email.upper(), display_name="Ada")

            assert again is False
            assert len(_rows(eng, email)) == 1
        finally:
            _drop(eng, email)


class TestTwoPeopleMayShareAName:
    """Migration 148 dropped ``UNIQUE(name)`` because two real people share a
    name. The directory must not put it back by the side door.

    ⚠️ **This suite found a live bug on its first real run.** The helper left
    ``source_key`` NULL, and 148's own backfill — replayed by the ladder, and
    by any future replay of that migration — computes it as
    ``<source>:<lower(name)>``. Two members called "Carol Co" produced
    ``member:carol co`` twice and the unique index aborted the whole replay.
    Nothing hermetic could see it: the INSERT itself was valid, and the
    collision happened in a DIFFERENT statement, in a later migration.
    """

    async def test_two_members_with_the_same_name_both_get_rows(self, eng):
        first = _address()
        second = _address()
        org = str(uuid.uuid4())
        try:
            await _ensure(org, email=first, display_name="Carol Co")
            await _ensure(org, email=second, display_name="Carol Co")

            assert len(_rows(eng, first)) == 1
            assert len(_rows(eng, second)) == 1
        finally:
            _drop(eng, first)
            _drop(eng, second)

    async def test_the_source_key_is_the_address_not_the_name(self, eng):
        """Keyed on what is actually unique for this source. Keyed on the name
        it would collide, and keyed on nothing 148's backfill would key it on
        the name later, which is the same collision one migration downstream.
        """
        email = _address()
        try:
            await _ensure(str(uuid.uuid4()), email=email, display_name="Carol Co")

            with eng.begin() as conn:
                key = conn.execute(
                    text("SELECT source_key FROM gtd_people  WHERE lower(email) = lower(:e)"),
                    {"e": email},
                ).scalar_one()

            assert key == f"member:{email}"
        finally:
            _drop(eng, email)


class TestItRefusesWhatItCannotKey:
    @pytest.mark.parametrize("bad", ["", "   ", None])
    async def test_an_unusable_address_writes_nothing(self, eng, bad):
        """The address IS the key — to the index, to the self predicate, and to
        the join with `app_user`. A row without one is unreachable by every
        path that would ever want it, so it is worse than no row."""
        created = await _ensure(str(uuid.uuid4()), email=bad, display_name="Nobody")

        assert created is False

    async def test_an_unknown_status_falls_back_to_active(self, eng):
        """Migration 148 put a CHECK on this column. An unrecognised word must
        become the safe default here rather than a 500 in front of an admin who
        was only inviting somebody."""
        email = _address()
        try:
            await _ensure(
                str(uuid.uuid4()),
                email=email,
                display_name="Ada",
                status="wizard",
            )

            assert _rows(eng, email)[0]["status"] == "active"
        finally:
            _drop(eng, email)


class TestTheInvitePathActuallyCallsIt:
    """The helper existing is not the fix — `provision_member` calling it is.

    ⚠️ **Through the real seam, never a mock.** The point of this ticket is a
    write that nobody had wired up. A test that asserts the helper was *called*
    would have passed just as happily on the day the directory was empty. This
    one provisions a real organization on the ladder, invites somebody through
    the one member-creation path, and then looks in the table.
    """

    async def test_inviting_a_colleague_writes_their_directory_row(self, eng):
        from acb_auth.roles import UserContext, UserRole
        from acb_common.db import tenant_session
        from acb_common.provisioning import provision_local_organization
        from gateway.routes.admin._common import provision_member

        slug = f"dirrow-{uuid.uuid4().hex[:8]}"
        owner = f"owner-{uuid.uuid4().hex[:8]}@directory.example"
        joiner = _address()
        try:
            async with tenant_engine_scope(_URL):
                org_id = await provision_local_organization(
                    slug, "Directory Row Org", owner_email=owner
                )
                admin = UserContext(email=owner, role=UserRole.EXECUTIVE)
                async with tenant_session(str(org_id)) as db:
                    await provision_member(
                        db,
                        str(org_id),
                        email=joiner,
                        display_name="Grace Hopper",
                        roles=["member"],
                        admin=admin,
                        status="invited",
                    )

            rows = _rows(eng, joiner)
            assert len(rows) == 1, (
                "the invited colleague has no directory row — `/people/me` "
                "would tell them to go find an administrator"
            )
            assert rows[0]["name"] == "Grace Hopper"
            assert rows[0]["status"] == "invited"
        finally:
            _drop(eng, joiner)
            _drop(eng, owner)
