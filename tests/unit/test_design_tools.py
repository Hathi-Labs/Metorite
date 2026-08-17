"""On-demand design system tool (generative_ui_2 §7).

design.md is no longer injected into every agent prompt; agents call
load_design_system() when they need the full design language (a heavy report
or bespoke custom HTML). These tests lock in that the tool returns the real
doc, strips the YAML front matter, and is wired into the injection floor.
"""
from __future__ import annotations

import asyncio

import acb_skills.design_tools as dt


def test_load_design_system_returns_the_real_doc():
    out = asyncio.run(dt.load_design_system())
    # Substantive content from the actual design.md body.
    assert "Metorite" in out
    assert "Visual theme & atmosphere" in out
    assert len(out) > 2000  # the real ~16KB doc, not the fallback string


def test_front_matter_is_stripped():
    out = asyncio.run(dt.load_design_system())
    # The YAML front-matter keys must NOT leak into the returned body…
    assert "when_to_use:" not in out
    assert "summary:" not in out
    # …and the body should start at the H1, not a stray "---" fence.
    assert out.lstrip().startswith("# Metorite")


def test_doc_reader_is_cached_and_stable():
    dt._design_doc.cache_clear()
    first = dt._design_doc()
    second = dt._design_doc()
    assert first == second and first != ""


def test_load_design_system_in_core_floor():
    from orchestrator._tool_injection import (
        _CORE_STANDARD_TOOL_NAMES,
        _resolve_injected_scope,
    )
    assert "load_design_system" in _CORE_STANDARD_TOOL_NAMES
    # Rides the floor even under a narrow scope.
    resolved = _resolve_injected_scope(["web_search"])
    assert resolved is not None and "load_design_system" in resolved


def test_annotation_is_read_only():
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS
    hints = TOOL_ANNOTATIONS["load_design_system"]
    assert hints["read_only"] is True
    assert hints["destructive"] is False
    assert hints["open_world"] is False


def test_design_md_has_front_matter():
    """The front matter documents when to load the doc (agent-facing hint)."""
    from pathlib import Path

    import acb_skills
    raw = (Path(acb_skills.__file__).parent / "design.md").read_text(
        encoding="utf-8",
    )
    assert raw.startswith("---")
    assert "when_to_use:" in raw and "summary:" in raw


# ── Tiered loading ─────────────────────────────────────────────────────────
# Returning the whole ~5.5k-token doc on every call was most of its cost: ~60%
# of it is three reference blocks and an artifact needs at most one. These lock
# in that the default is materially cheaper AND that nothing was lost.

def _tokens(text: str) -> int:
    return len(text) // 4


def test_core_is_materially_cheaper_than_the_whole_document():
    core = asyncio.run(dt.load_design_system())
    full = asyncio.run(dt.load_design_system("all"))
    assert _tokens(core) < _tokens(full) * 0.7, (
        "the core response has grown back toward the full document — the point "
        "of tiering is that the common path stays cheap"
    )


def test_core_keeps_what_every_artifact_needs():
    core = asyncio.run(dt.load_design_system())
    # Palette, the anti-slop rules, the lint contract, and the surface-routing
    # rule are needed on EVERY artifact, so they must not be behind a section.
    assert "--cc-primary" in core
    assert "Avoid the generic-AI look" in core
    assert "Which surface" in core
    assert "linted" in core
    # And it must advertise what else can be fetched, or the detail is orphaned.
    assert 'load_design_system("blocks")' in core


def test_core_omits_the_heavy_reference_blocks():
    core = asyncio.run(dt.load_design_system())
    assert "cc-donut" not in core       # charts
    assert "cc-tl-bar" not in core      # charts (timeline)


def test_each_section_returns_only_its_own_block():
    charts = asyncio.run(dt.load_design_system("charts"))
    blocks = asyncio.run(dt.load_design_system("blocks"))
    react = asyncio.run(dt.load_design_system("react"))
    assert "cc-donut" in charts and "cc-stat" in charts
    assert "cc-eyebrow" in blocks
    assert "@cc/ui" in react
    # Sections are disjoint — asking for one must not drag in another.
    assert "cc-donut" not in blocks
    assert "@cc/ui" not in charts


def test_section_aliases_and_case_insensitivity():
    for alias in ("charts", "CHARTS", "dataviz", "data-viz", "chart"):
        assert "cc-donut" in asyncio.run(dt.load_design_system(alias))
    for alias in ("blocks", "report", "kit"):
        assert "cc-eyebrow" in asyncio.run(dt.load_design_system(alias))


def test_all_still_returns_the_complete_document():
    full = asyncio.run(dt.load_design_system("all"))
    assert "cc-donut" in full and "cc-eyebrow" in full and "@cc/ui" in full
    assert "Visual theme & atmosphere" in full


def test_nothing_is_lost_across_the_tiers():
    """Every heavy block must be reachable — core hides them, sections serve them."""
    full = asyncio.run(dt.load_design_system("all"))
    reachable = "".join(
        asyncio.run(dt.load_design_system(s)) for s in ("", "blocks", "charts", "react")
    )
    for marker in ("cc-eyebrow", "cc-donut", "cc-tl-bar", "cc-decision", "@cc/ui"):
        assert marker in full and marker in reachable, f"{marker} unreachable"


def test_unknown_section_says_what_is_available():
    out = asyncio.run(dt.load_design_system("nonsense"))
    assert "nonsense" in out
    assert "blocks" in out and "charts" in out


def test_marker_drift_falls_back_to_the_full_document(monkeypatch):
    """If someone renames a heading, serve too much rather than silently nothing.

    The chunk boundaries are verbatim strings from design.md, so an innocent
    edit could stop them matching. Failing open (whole doc) costs tokens;
    failing closed would hand the agent an empty reference.
    """
    monkeypatch.setitem(dt._CHUNKS, "charts", ("NO SUCH MARKER", None))
    out = asyncio.run(dt.load_design_system("charts"))
    assert "Visual theme & atmosphere" in out  # i.e. the whole document
