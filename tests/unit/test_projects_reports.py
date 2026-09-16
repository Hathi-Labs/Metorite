"""WS-27bk §9.12.8 slice 1 — a report is a saved QUESTION.

Spec: ``project-docs/specs/project_management_app.md`` §9.12.8.

The owner drew the line: *"analytics is what you look at, a report is what
gets delivered."* §9.12.8 gives the reason a report may not carry its own
queries — one it states outright:

    A report with no analytics behind it would mint a second set of numbers,
    and two sets of numbers disagree.

So the claims here are mostly about what `reports.py` must NOT contain, and
about the one place a saved definition can quietly become wrong.

* **the row holds no ANSWER.** A `results` column would be that second set,
  cached and ageing while the dashboard beside it moved on.
* **the module re-runs §9.12.7's SQL rather than its own.** Pinned by source
  scan, because the failure is a new query appearing here — not a wrong value
  from an existing one.
* **an unknown section is REJECTED, never dropped.** A report is read by
  somebody who was not in the room when it was saved, and a section that
  vanishes leaves a document missing a part nobody can see is missing.
* **`skip_current_week` defaults ON here and OFF on the dashboard.** That
  difference is the whole of §9.12.8's shape, and a default that drifted to
  the dashboard's would make every weekly send report a different number for
  the same work.
* **NULL `project_id` is the portfolio**, and its tenant must still be set —
  the trigger reads the tenant off the parent, and there is no parent.
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
    REPO / "infra/postgres/204_projects_reports.sql"
).read_text(encoding="utf-8")


class TestTheRowHoldsNoAnswer:
    """⚠️ A cached number is the second set of numbers §9.12.8 forbids."""

    def test_the_table_has_no_results_column(self):
        for banned in ("results", "rendered", "snapshot", "totals"):
            assert f"    {banned}" not in MIGRATION, (
                f"pm_reports grew a `{banned}` column — a report stores the "
                f"question, never the answer."
            )

    def test_the_scope_is_nullable_because_NULL_is_the_portfolio(self):
        assert "project_id      UUID REFERENCES pm_projects" in MIGRATION
        assert "project_id      UUID NOT NULL" not in MIGRATION

    def test_the_tenant_is_forced_not_merely_enabled(self):
        # The owner role bypasses a policy that is only ENABLEd, and the
        # gateway connects as the owner on some deployments.
        assert "FORCE  ROW LEVEL SECURITY" in MIGRATION
        assert "pm_reports_tenant_isolation" in MIGRATION


class TestItReusesAnalyticsRatherThanRewritingIt:
    """⚠️ The fence for §9.12.8's stated reason for existing.

    A source scan, not a value check. The failure this guards against is a
    NEW query appearing in this module — at which point every value test
    still passes, against numbers that have quietly stopped matching the
    dashboard.
    """

    def test_it_imports_the_analytics_builders(self):
        for fn in (
            "finished_sql",
            "cycle_summary_sql",
            "weekly_sql",
            "load_sql",
            "total_open_sql",
            "finished_period_sql",
        ):
            assert fn in SOURCE, f"{fn} is no longer used — is there a copy?"

    def test_it_reimplements_none_of_their_machinery(self):
        # Each of these appears in `analytics.py` and must appear in NO other
        # query. Their presence here means somebody rebuilt a metric.
        for marker in (
            "date_trunc('week'",
            "percentile_cont",
            "pm_activities",
            "generate_series",
            "DISTINCT ON (task_id)",
        ):
            assert marker not in SOURCE, (
                f"reports.py contains {marker!r} — §9.12.8 forbids a second "
                f"set of numbers. Call the analytics builder instead."
            )

    def test_it_shares_the_status_vocabulary(self):
        # The same words `analytics.py` was made to import rather than spell.
        assert "COMPLETED_CATEGORY" in SOURCE
        assert "CLOSING_CATEGORIES" in SOURCE
        assert '"done"' not in SOURCE
        assert '"in_progress"' not in SOURCE


class TestTheConfigIsValidatedNotRepaired:
    """An unknown section must fail loudly, at save time."""

    def test_an_unknown_section_is_refused(self):
        from fastapi import HTTPException
        from gateway.routes.projects.reports import normalise_report_config

        with pytest.raises(HTTPException) as exc:
            normalise_report_config({"sections": ["finished", "velocity"]})
        assert exc.value.status_code == 422
        # The message must name the offender and the vocabulary, or the
        # author has to guess what they typed wrong.
        assert "velocity" in str(exc.value.detail)
        assert "finished" in str(exc.value.detail)

    def test_an_empty_section_list_is_refused(self):
        from fastapi import HTTPException
        from gateway.routes.projects.reports import normalise_report_config

        with pytest.raises(HTTPException):
            normalise_report_config({"sections": []})

    def test_sections_render_in_DECLARED_order_not_the_saved_one(self):
        from gateway.routes.projects.reports import (
            SECTIONS,
            normalise_report_config,
        )

        got = normalise_report_config(
            {"sections": ["stuck", "finished", "stuck"]}
        )
        # Declared order, and de-duplicated. Two people comparing last week's
        # copy to this week's must find the difference in the numbers.
        assert got["sections"] == [s for s in SECTIONS if s in {"stuck", "finished"}]
        assert got["sections"] == ["finished", "stuck"]

    def test_weeks_is_bounded_on_the_way_OUT_of_the_table(self):
        """A definition saved before a limit changed is still in the table."""
        from fastapi import HTTPException
        from gateway.routes.projects.reports import (
            MAX_WEEKS,
            normalise_report_config,
        )

        with pytest.raises(HTTPException):
            normalise_report_config({"weeks": MAX_WEEKS + 1})
        with pytest.raises(HTTPException):
            normalise_report_config({"weeks": 0})
        assert normalise_report_config({"weeks": MAX_WEEKS})["weeks"] == MAX_WEEKS

    def test_a_flag_must_be_a_BOOLEAN(self):
        """`"false"` is a true string, and would arm what it meant to disarm."""
        from fastapi import HTTPException
        from gateway.routes.projects.reports import normalise_report_config

        with pytest.raises(HTTPException):
            normalise_report_config({"skip_current_week": "false"})


class TestTheReportsPeriodIsNotTheDashboards:
    """⚠️ The whole of §9.12.8's shape, in one default."""

    def test_skip_current_week_defaults_ON(self):
        from gateway.routes.projects.reports import normalise_report_config

        # A weekly report describes a week that ENDED. Defaulted OFF, every
        # send would re-report the running week with a different number.
        assert normalise_report_config(None)["skip_current_week"] is True
        assert normalise_report_config({})["skip_current_week"] is True

    def test_the_DASHBOARD_still_defaults_it_OFF(self):
        import inspect

        from gateway.routes.projects.analytics import finished

        param = inspect.signature(finished).parameters["skip_current_week"]
        assert param.default is False, (
            "the dashboard must keep the running week — it is this week's work"
        )

    def test_the_default_period_is_ONE_week(self):
        from gateway.routes.projects.reports import normalise_report_config

        assert normalise_report_config(None)["weeks"] == 1


# ── R8: the definition round-trips through a real Postgres ──────────────────

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402

from tests.unit._tenant_ladder import apply_ladder  # noqa: E402


@pytest.fixture(scope="module")
def db():
    if not _TENANT_URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset — R8 needs a real Postgres")
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
        conn.execute(text(MIGRATION))
    yield eng
    eng.dispose()


class TestThePortfolioReportKeepsItsTenant:
    """⚠️ The trigger reads the tenant off the parent, and there is none."""

    def test_a_null_project_still_carries_an_organization(self, db):
        with db.begin() as c:
            org = str(c.execute(
                text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
            ).scalar_one())
            rid = str(c.execute(
                text(
                    "INSERT INTO pm_reports (project_id, organization_id, name,"
                    " config, created_by) VALUES (NULL, CAST(:o AS uuid), :n,"
                    " '{}'::jsonb, 'rep@example.test') RETURNING id"
                ),
                {"o": org, "n": f"weekly-{uuid.uuid4().hex[:6]}"},
            ).scalar_one())
            got = c.execute(
                text(
                    "SELECT project_id, organization_id FROM pm_reports"
                    " WHERE id = CAST(:i AS uuid)"
                ),
                {"i": rid},
            ).one()
            assert got.project_id is None
            assert str(got.organization_id) == org
            c.execute(
                text("DELETE FROM pm_reports WHERE id = CAST(:i AS uuid)"),
                {"i": rid},
            )

    def test_a_blank_name_is_refused_by_the_DATABASE(self, db):
        """Not only by the route. A backfill or a fixture writes here too."""
        from sqlalchemy.exc import IntegrityError

        with db.begin() as c:
            org = str(c.execute(
                text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
            ).scalar_one())
            with pytest.raises(IntegrityError):
                c.execute(
                    text(
                        "INSERT INTO pm_reports (organization_id, name, config,"
                        " created_by) VALUES (CAST(:o AS uuid), '   ',"
                        " '{}'::jsonb, 'rep@example.test')"
                    ),
                    {"o": org},
                )
