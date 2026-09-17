"""WS-27bk §9.12.8 slice 3 — who a report goes to, and the two locks.

Spec: ``project-docs/specs/project_management_app.md`` §9.12.8.

The owner delegated H-111's two questions on 2026-09-17. The answers, and
what makes them safe:

⚠️ **A recipient is an address we ALREADY mail, never free text.** A
free-text field turns an internal analytics tool into an open mail relay:
anybody who can save a report could send company delivery figures to any
address on the internet, on a timer, from our one verified sender.

⚠️ **The send renders ONCE PER RECIPIENT, with that recipient's own
visibility.** `render_report` resolves visibility from the CALLER rather than
the author, and the job keeps that property by rendering per address. So
**any member may add any member**: it can never show them more than they could
already see by opening the app.

⚠️ **TWO LOCKS, and a member holds only one.** A member can arm a report's
schedule. Only the deployment can arm delivery, and §9.12.8 makes that the
owner's — *"build it dark, default off."*
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SOURCE = (
    REPO / "apps/services/gateway/gateway/routes/projects/reports.py"
).read_text(encoding="utf-8")
MIGRATION = (
    REPO / "infra/postgres/205_projects_report_recipients.sql"
).read_text(encoding="utf-8")


class TestARecipientIsNotFreeText:
    """⚠️ The ruling the table exists to carry."""

    def test_the_route_checks_the_directory(self):
        assert "_known_member" in SOURCE
        assert "FROM app_user WHERE lower(email)" in SOURCE

    def test_the_database_refuses_an_agent_and_a_mixed_case_address(self):
        # The same two CHECKs `pm_project_watchers` carries (R10). A second
        # grammar for "a person we mail" is how one of them starts leaking.
        assert "pm_report_recipients_is_human" in MIGRATION
        assert "pm_report_recipients_lowercased" in MIGRATION

    def test_adding_twice_is_adding(self):
        # Idempotent in the CONSTRAINT, not in a read-then-write. Two racing
        # requests would both pass a read.
        assert "UNIQUE (report_id, recipient)" in MIGRATION
        assert "ON CONFLICT (report_id, recipient) DO NOTHING" in SOURCE


class TestTheScheduleIsBuiltDark:
    """§9.12.8: *"build it dark, default off."*"""

    def test_the_column_defaults_to_off(self):
        # A report that existed before this migration keeps sending nothing,
        # which is the only safe meaning for a column added yesterday.
        assert (
            "ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT false"
            in MIGRATION
        )
        # ⚠️ The text above is the intent. `test_a_report_starts_disabled_
        # and_unsent` below is the proof, because it asks the DATABASE.

    def test_an_unknown_schedule_cannot_reach_the_table(self):
        assert "pm_reports_schedule_known" in MIGRATION
        assert "schedule IN ('weekly')" in MIGRATION

    def test_the_deployment_lock_is_read_not_hardcoded(self):
        from gateway.routes.projects.reports import delivery_armed

        # ⚠️ A literal False in the response would become a lie the moment
        # somebody armed the deployment, and nobody would think to change it.
        assert "delivery_armed()" in SOURCE
        assert '"delivery_armed_on_this_deployment": False' not in SOURCE

        was = os.environ.get("PROJECT_REPORT_EMAIL_ENABLED")
        try:
            for value, expected in (
                (None, False), ("", False), ("false", False),
                ("1", False), ("True", False), ("true", True),
            ):
                if value is None:
                    os.environ.pop("PROJECT_REPORT_EMAIL_ENABLED", None)
                else:
                    os.environ["PROJECT_REPORT_EMAIL_ENABLED"] = value
                assert delivery_armed() is expected, value
        finally:
            os.environ.pop("PROJECT_REPORT_EMAIL_ENABLED", None)
            if was is not None:
                os.environ["PROJECT_REPORT_EMAIL_ENABLED"] = was

    def test_it_is_OFF_right_now(self):
        """The state this repo ships in. A failure here means somebody armed
        report delivery, which is an owner decision (§3a rule 3)."""
        from gateway.routes.projects.reports import delivery_armed

        assert delivery_armed() is False

    def test_a_schedule_with_no_audience_is_refused(self):
        # Armed and delivering nothing looks armed on every screen. That is
        # the failure this whole feature is written to avoid.
        assert "Add at least one recipient before you turn this on" in SOURCE

    def test_last_sent_at_exists_so_the_job_can_be_idempotent(self):
        # A timer that fires twice must not send one report twice, and a send
        # is not a transaction that can be rolled back.
        assert "ADD COLUMN IF NOT EXISTS last_sent_at" in MIGRATION


# ── R8: the table behaves against a real Postgres ───────────────────────────

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from tests.unit._tenant_ladder import apply_ladder  # noqa: E402


@pytest.fixture(scope="module")
def db():
    if not _TENANT_URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset — R8 needs a real Postgres")
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
        conn.execute(text(
            (REPO / "infra/postgres/204_projects_reports.sql").read_text(
                encoding="utf-8")
        ))
        conn.execute(text(MIGRATION))
    yield eng
    eng.dispose()


@pytest.fixture
def report(db):
    made: dict[str, str] = {}
    with db.begin() as c:
        made["org"] = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["id"] = str(c.execute(
            text(
                "INSERT INTO pm_reports (project_id, organization_id, name,"
                " config, created_by) VALUES (NULL, CAST(:o AS uuid), :n,"
                " '{}'::jsonb, 'rep@example.test') RETURNING id"
            ),
            {"o": made["org"], "n": f"rec-{uuid.uuid4().hex[:6]}"},
        ).scalar_one())
    yield made
    with db.begin() as c:
        c.execute(
            text("DELETE FROM pm_reports WHERE id = CAST(:i AS uuid)"),
            {"i": made["id"]},
        )


def _add(conn, report, email):
    conn.execute(
        text(
            "INSERT INTO pm_report_recipients (report_id, recipient, created_by)"
            " VALUES (CAST(:r AS uuid), :e, 'rep@example.test')"
            " ON CONFLICT (report_id, recipient) DO NOTHING"
        ),
        {"r": report["id"], "e": email},
    )


class TestTheTableAgainstPostgres:
    def test_the_tenant_comes_from_the_REPORT_not_the_caller(self, db, report):
        """⚠️ The trigger reads it off the parent, so no insert site has to
        remember it — and a row cannot claim a tenant its report does not
        have."""
        with db.begin() as c:
            _add(c, report, "ana@example.test")
            got = c.execute(
                text(
                    "SELECT organization_id FROM pm_report_recipients"
                    " WHERE report_id = CAST(:r AS uuid)"
                ),
                {"r": report["id"]},
            ).scalar_one()
        assert str(got) == report["org"]

    def test_adding_twice_writes_one_row(self, db, report):
        with db.begin() as c:
            _add(c, report, "ana@example.test")
            _add(c, report, "ana@example.test")
            n = c.execute(
                text(
                    "SELECT count(*) FROM pm_report_recipients"
                    " WHERE report_id = CAST(:r AS uuid)"
                ),
                {"r": report["id"]},
            ).scalar_one()
        assert n == 1

    def test_an_agent_address_is_refused_by_the_DATABASE(self, db, report):
        """Not only by the route. A backfill writes here too."""
        with db.begin() as c, pytest.raises(IntegrityError):
            _add(c, report, "agent:summariser")

    def test_a_mixed_case_address_is_refused(self, db, report):
        # Every read compares folded, so a mixed-case row is an audience
        # member who never receives anything.
        with db.begin() as c, pytest.raises(IntegrityError):
            _add(c, report, "Ana@Example.test")

    def test_deleting_the_report_takes_its_audience(self, db):
        with db.begin() as c:
            org = str(c.execute(
                text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
            ).scalar_one())
            rid = str(c.execute(
                text(
                    "INSERT INTO pm_reports (organization_id, name, config,"
                    " created_by) VALUES (CAST(:o AS uuid), :n, '{}'::jsonb,"
                    " 'rep@example.test') RETURNING id"
                ),
                {"o": org, "n": f"gone-{uuid.uuid4().hex[:6]}"},
            ).scalar_one())
            c.execute(
                text(
                    "INSERT INTO pm_report_recipients (report_id, recipient)"
                    " VALUES (CAST(:r AS uuid), 'ana@example.test')"
                ),
                {"r": rid},
            )
            c.execute(
                text("DELETE FROM pm_reports WHERE id = CAST(:i AS uuid)"),
                {"i": rid},
            )
            left = c.execute(
                text(
                    "SELECT count(*) FROM pm_report_recipients"
                    " WHERE report_id = CAST(:r AS uuid)"
                ),
                {"r": rid},
            ).scalar_one()
        assert left == 0

    def test_an_unknown_schedule_is_refused(self, db, report):
        with db.begin() as c, pytest.raises(IntegrityError):
            c.execute(
                text(
                    "UPDATE pm_reports SET schedule = 'hourly'"
                    " WHERE id = CAST(:i AS uuid)"
                ),
                {"i": report["id"]},
            )

    def test_a_report_starts_disabled_and_unsent(self, db, report):
        with db.begin() as c:
            row = c.execute(
                text(
                    "SELECT enabled, schedule, last_sent_at FROM pm_reports"
                    " WHERE id = CAST(:i AS uuid)"
                ),
                {"i": report["id"]},
            ).one()
        assert row.enabled is False
        assert row.schedule is None
        assert row.last_sent_at is None
