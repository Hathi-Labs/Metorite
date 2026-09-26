"""The report section vocabulary, held equal in Python and in TypeScript.

Spec: ``project-docs/specs/projects_ai_chat.md`` §10.3 item 6 and §13.3
rule 4 (WS-27bm S7a).

A report section is named in seven places, in two languages, plus the chat
card map:

1. ``gateway/routes/projects/reports.py`` ``SECTIONS`` — what a definition
   may ask for, and the render's switch.
2. ``skill_projects/reads.py`` ``_REPORT_SECTIONS`` — how the chat prints a
   rendered section.
3. ``skill_projects/writes.py`` ``REPORT_SECTIONS`` — what the chat may save.
4. ``ReportsView.tsx`` ``RenderedBody`` — how the Reports app draws it.
5. ``src/lib/reportEmail.ts`` — how the delivered email says it.
6. ``app/projects/lib/reportBuilder.ts`` ``REPORT_SECTIONS`` — what the
   builder offers (WS-27bn R1). Held equal in name AND order, below.
7. ``app/projects/lib/api.ts`` ``RenderedReportBody`` — the type of the body.

The chat card map, ``skill_projects/views.py`` ``REPORT_CARD_SECTIONS``, names
the card's words for a section. ``test_projects_agent.py`` holds its titles to
``ReportsView.tsx``.

A section added to the route and left out of one of the others renders as
NOTHING there, with no error: a TypeScript interface is a claim about the
server, and the chat falls back to a generic printer. So one test reads all
six and fails on the first that lacks a name.

⚠️ **The TypeScript half is read as TEXT.** There is no shared runtime, and a
mirror constant in each file would be two more lists to keep in step. The
reads look for the section's own access (``sections.capacity``). A comment
that names the access counts too, so the scan is a floor against a FORGOTTEN
section, not proof that a section draws.

WS-27bn R2 adds the template catalogue, ``reports.py`` ``TEMPLATES``. A live
template may name only sections in ``SECTIONS``, in that order. A coming-soon
template names none. The 13 keys are append-only.
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


# ── WS-27bn R1: the builder's section list (a sixth place) ───────────────────

REPORT_BUILDER = CONTROL_PLANE / "app" / "projects" / "lib" / "reportBuilder.ts"


def _ts_list(name: str) -> list[str]:
    """The string keys of one exported TypeScript array in reportBuilder.ts."""
    source = REPORT_BUILDER.read_text(encoding="utf-8")
    start = source.index(f"export const {name}")
    body = source[start: source.index("];", start)]
    if "key:" in body:
        return re.findall(r'key: "([a-z_]+)"', body)
    return re.findall(r'"([a-z_]+)"', body.split("= [", 1)[1])


def test_the_builder_offers_every_section_in_the_routes_order() -> None:
    """The builder draws its checkboxes from this list. A section left out is
    a section a member cannot choose, and a wrong order reorders the chips
    against the render."""
    assert _ts_list("REPORT_SECTIONS") == list(reports.SECTIONS)


def test_the_builder_starts_from_the_routes_defaults() -> None:
    assert _ts_list("DEFAULT_REPORT_SECTIONS") == list(reports.DEFAULT_SECTIONS)


# ── WS-27bn R2: the template catalogue ───────────────────────────────────────

#: The 13 keys of `projects_reports.md` §4. APPEND-ONLY: a saved report keeps
#: its key in `config.template`, and the config is normalised on every read.
#: A key that leaves `TEMPLATES` makes each list and render of it answer 422.
PINNED_TEMPLATE_KEYS: tuple[str, ...] = (
    "team_pulse", "my_day", "what_changed", "weekly_delivery",
    "project_status", "one_on_one", "exceptions", "capacity_outlook",
    "stakeholder_update", "portfolio_health", "focus_switching",
    "retrospective", "data_hygiene",
)


def template_faults(
    templates: dict[str, dict], sections: tuple[str, ...],
) -> list[str]:
    """Each way a catalogue breaks the section vocabulary, as one line."""
    faults: list[str] = []
    for key, t in templates.items():
        if t.get("key") != key:
            faults.append(f"{key}: the entry names the key {t.get('key')!r}")
        if not t.get("available"):
            if "sections" in t:
                faults.append(f"{key}: a coming-soon template carries sections")
            if not t.get("waits_for"):
                faults.append(f"{key}: a coming-soon template names no waits_for")
            continue
        asked = list(t.get("sections") or [])
        unknown = [s for s in asked if s not in sections]
        if unknown:
            faults.append(f"{key}: sections outside SECTIONS: {unknown}")
        elif asked != [s for s in sections if s in asked]:
            faults.append(f"{key}: sections out of SECTIONS order: {asked}")
        if not asked:
            faults.append(f"{key}: a live template names no sections")
    return faults


def test_the_catalogue_names_only_sections_the_route_knows() -> None:
    assert template_faults(reports.TEMPLATES, reports.SECTIONS) == []


def test_no_template_key_disappears() -> None:
    """Append-only. A removed key 422s every saved report that carries it."""
    missing = [k for k in PINNED_TEMPLATE_KEYS if k not in reports.TEMPLATES]
    assert missing == []
    assert list(reports.TEMPLATES)[: len(PINNED_TEMPLATE_KEYS)] == list(
        PINNED_TEMPLATE_KEYS
    )


def test_project_status_is_live_in_sections_order() -> None:
    """WS-27bn R3a. T5: "this week" is one week with the running week kept."""
    t5 = reports.TEMPLATES["project_status"]
    assert t5["available"] is True
    assert t5["sections"] == ["finished", "outlook", "stuck", "conflicts"]
    assert (t5["weeks"], t5["skip_current_week"]) == (1, False)
    assert t5["scope_kinds"] == ["project"]


def test_the_live_templates_are_exactly_the_pinned_four() -> None:
    """WS-27bn R3d makes T1 `team_pulse` live."""
    live = [k for k, t in reports.TEMPLATES.items() if t["available"]]
    assert live == ["team_pulse", "weekly_delivery", "project_status", "data_hygiene"]


def test_weekly_delivery_is_exactly_the_default_report() -> None:
    t4 = reports.TEMPLATES["weekly_delivery"]
    assert t4["available"] is True
    assert t4["sections"] == list(reports.DEFAULT_SECTIONS)
    assert (t4["weeks"], t4["skip_current_week"]) == (1, True)


def test_the_template_fence_fires_on_a_missing_section() -> None:
    """The fence's own fence: a name that is not a section fires it.

    WS-27bn R3d. `pulse` IS a section now, so the fake uses a name that
    no slice will ever add.
    """
    fake = {
        "team_pulse": {"key": "team_pulse", "available": True,
                       "sections": ["not_a_section", "conflicts"]},
    }
    assert any(
        "not_a_section" in f for f in template_faults(fake, reports.SECTIONS)
    )


def test_the_template_fence_fires_on_order_and_on_coming_soon_sections() -> None:
    fake = {
        "a": {"key": "a", "available": True, "sections": ["stuck", "finished"]},
        "b": {"key": "b", "available": False, "waits_for": "R3",
              "sections": ["load"]},
    }
    faults = template_faults(fake, reports.SECTIONS)
    assert any("order" in f for f in faults)
    assert any("coming-soon template carries sections" in f for f in faults)
