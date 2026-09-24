"""The report section vocabulary, held equal in Python and in TypeScript.

Spec: ``project-docs/specs/projects_ai_chat.md`` §10.3 item 6 and §13.3
rule 4 (WS-27bm S7a).

A report section is named in five places, in two languages:

1. ``gateway/routes/projects/reports.py`` ``SECTIONS`` — what a definition
   may ask for, and the render's switch.
2. ``skill_projects/reads.py`` ``_REPORT_SECTIONS`` — how the chat prints a
   rendered section.
3. ``skill_projects/writes.py`` ``REPORT_SECTIONS`` — what the chat may save.
4. ``ReportsView.tsx`` ``RenderedBody`` — how the Reports app draws it.
5. ``src/lib/reportEmail.ts`` — how the delivered email says it.

A section added to the route and left out of one of the others renders as
NOTHING there, with no error: a TypeScript interface is a claim about the
server, and the chat falls back to a generic printer. So one test reads all
five and fails on the first that lacks a name.

⚠️ **The TypeScript half is read as TEXT.** There is no shared runtime, and a
mirror constant in each file would be two more lists to keep in step. The
reads look for the section's own access (``sections.capacity``). A comment
that names the access counts too, so the scan is a floor against a FORGOTTEN
section, not proof that a section draws.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

from gateway.routes.projects import reports
from skill_projects import reads, writes

REPO = Path(__file__).resolve().parents[2]
CONTROL_PLANE = REPO / "workbench" / "control_plane" / "src"
REPORTS_VIEW = CONTROL_PLANE / "app" / "projects" / "components" / "ReportsView.tsx"
REPORT_EMAIL = CONTROL_PLANE / "lib" / "reportEmail.ts"
API_TS = CONTROL_PLANE / "app" / "projects" / "lib" / "api.ts"


def _drawn(path: Path) -> set[str]:
    """Every `sections.<name>` the file reads, or destructures by name."""
    source = path.read_text(encoding="utf-8")
    return set(re.findall(r"\bsections\.([a-z_]+)\b", source))


@pytest.mark.parametrize("name", reports.SECTIONS)
def test_the_chat_prints_every_section(name: str) -> None:
    assert name in reads._REPORT_SECTIONS, (
        f"{name} is a report section and skill_projects/reads.py cannot print it"
    )


@pytest.mark.parametrize("name", reports.SECTIONS)
def test_the_chat_may_save_every_section(name: str) -> None:
    assert name in writes.REPORT_SECTIONS, name


@pytest.mark.parametrize("name", reports.SECTIONS)
def test_the_reports_app_draws_every_section(name: str) -> None:
    assert name in _drawn(REPORTS_VIEW), (
        f"RenderedBody (ReportsView.tsx) never reads sections.{name}"
    )


@pytest.mark.parametrize("name", reports.SECTIONS)
def test_the_email_says_every_section(name: str) -> None:
    # `finished`, `throughput`, `stuck` and `load` are read as a local
    # (`const fin = sections.finished`). Any access by name counts.
    assert name in _drawn(REPORT_EMAIL), (
        f"reportEmail.ts never reads sections.{name}"
    )


@pytest.mark.parametrize("name", reports.SECTIONS)
def test_the_rendered_body_type_declares_every_section(name: str) -> None:
    source = API_TS.read_text(encoding="utf-8")
    body = source[source.index("export interface RenderedReportBody"):]
    body = body[: body.index("\n}\n")]
    assert re.search(rf"\b{name}\?:", body), f"RenderedReportBody lacks {name}"


def test_no_list_knows_a_section_the_route_does_not() -> None:
    """The other direction: a name the route refuses is a dead option."""
    assert set(reads._REPORT_SECTIONS) <= set(reports.SECTIONS)
    assert set(writes.REPORT_SECTIONS) == set(reports.SECTIONS)


def test_the_fence_would_fire_on_a_missing_section(tmp_path: Path) -> None:
    """The fence's own fence: a file that never reads a section is caught."""
    fake = tmp_path / "Body.tsx"
    fake.write_text("// sections.capacity is mentioned only in prose\n"
                    "const x = sections.load;\n", encoding="utf-8")
    drawn = _drawn(fake)
    assert "load" in drawn
    # ⚠️ A comment still matches the pattern, and that is accepted: the scan
    # is a floor against a FORGOTTEN section, not proof of a drawn one.
    assert "finished" not in drawn


# ── capacity is opt-in (§13.3 rule 4) ────────────────────────────────────────


def test_capacity_is_a_section() -> None:
    assert "capacity" in reports.SECTIONS


def test_capacity_is_not_a_default_section() -> None:
    assert "capacity" not in reports._DEFAULTS["sections"]


def test_the_defaults_are_an_explicit_list_not_the_vocabulary() -> None:
    """While `_DEFAULTS` was `list(SECTIONS)`, adding a section added it to
    every saved report that never asked for one."""
    assert reports._DEFAULTS["sections"] == ["finished", "throughput", "load", "stuck"]
    assert reports._DEFAULTS["sections"] != list(reports.SECTIONS)


def test_a_definition_with_no_sections_key_does_not_get_capacity() -> None:
    assert "capacity" not in reports.normalise_report_config({})["sections"]
    assert "capacity" not in reports.normalise_report_config(None)["sections"]


def test_a_definition_may_ask_for_capacity() -> None:
    got = reports.normalise_report_config({"sections": ["capacity", "load"]})
    # Declared order, not the caller's.
    assert got["sections"] == ["load", "capacity"]


# ── conflicts is opt-in too (§13.5 rule 12, §10.5 item 11) ───────────────────


def test_conflicts_is_a_section() -> None:
    assert "conflicts" in reports.SECTIONS


def test_conflicts_is_not_a_default_section() -> None:
    """Fails if `conflicts` enters the defaults. A saved weekly report that
    never asked for conflicts must not start to carry them."""
    assert "conflicts" not in reports.DEFAULT_SECTIONS
    assert "conflicts" not in reports._DEFAULTS["sections"]
    assert "conflicts" not in reports.normalise_report_config({})["sections"]
    assert "conflicts" not in reports.normalise_report_config(None)["sections"]


def test_a_definition_may_ask_for_conflicts() -> None:
    got = reports.normalise_report_config({"sections": ["conflicts", "capacity"]})
    # Declared order, not the caller's.
    assert got["sections"] == ["capacity", "conflicts"]


def test_the_report_section_reads_the_routes_own_body() -> None:
    """The panel and the report are one computation, as for `capacity`."""
    source = (REPO / "apps/services/gateway/gateway/routes/projects/reports.py").read_text(
        encoding="utf-8"
    )
    assert "from gateway.routes.projects.analytics_conflicts import conflicts_body" in source
    assert "await conflicts_body(" in source
