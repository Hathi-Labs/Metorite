"""On-demand Metorite design system (generative_ui_2 §7 follow-up).

``design.md`` is ~5.5k tokens. Injecting it into every prompt was the first
mistake (fixed by making it a tool); returning ALL of it on every call was the
second. Roughly 60% of the file is three reference blocks — the report kit, the
data-viz blocks, and the React section — and a given artifact needs at most one
of them. An agent that only wants the palette was paying for the lot, and the
surface-routing rule pushes agents to render MORE often, so that waste compounds.

So this serves the doc in tiers, exactly like ``load_artifact_kit``:

* ``load_design_system()`` — the CORE: palette, typography, spacing, motion,
  do's and don'ts, the lint contract, and the surface-routing rule. ~1.5k tokens,
  and enough to decide what to build and to style it on-brand.
* ``load_design_system("blocks" | "charts" | "react")`` — one reference block,
  fetched only when actually writing that kind of artifact.
* ``load_design_system("all")`` — the whole document, unchanged.

The chunks are cut out of the real file at load time rather than duplicated, so
there is no second copy to drift.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path

# Heavy reference blocks, cut from the core by (start marker, end marker).
# End marker None means "to the closing guidance". Markers are verbatim strings
# from design.md; the loader falls back to the full doc if one stops matching, so
# an edit that renames a heading degrades to "too much" rather than "silently
# missing half the reference".
_CHUNKS: dict[str, tuple[str, str | None]] = {
    "react": ("**React artifacts.**", "**Report design kit"),
    "blocks": ("**Report design kit", "**Data-viz & decision blocks"),
    "charts": ("**Data-viz & decision blocks", "Compose these instead of hand-rolling"),
}

_ALIASES = {
    "components": "blocks", "report": "blocks", "kit": "blocks",
    "dataviz": "charts", "data-viz": "charts", "chart": "charts",
    "graphs": "charts", "jsx": "react", "tsx": "react",
}

_INDEX = (
    "\n---\n\n"
    "**More on demand.** This is the core. For the block references, call:\n"
    "- `load_design_system(\"blocks\")` — the `cc-report` document kit "
    "(eyebrow, callouts, cards, compare tables, steps, phases).\n"
    "- `load_design_system(\"charts\")` — KPI tiles, bar/donut/spark/trend "
    "charts, data tables, timelines, architecture diagrams, decision boxes.\n"
    "- `load_design_system(\"react\")` — writing a React artifact (also see "
    "`load_artifact_kit()`, which is usually the better route).\n"
    "- `load_design_system(\"all\")` — everything at once.\n"
)


@functools.lru_cache(maxsize=1)
def _design_doc() -> str:
    """The design.md body with any YAML front matter stripped (cached)."""
    try:
        import acb_skills  # noqa: PLC0415

        text = (Path(acb_skills.__file__).parent / "design.md").read_text(
            encoding="utf-8", errors="replace",
        )
    except Exception:  # noqa: BLE001 — never fail the tool on a missing doc
        return ""
    # Strip a leading ``---\n…\n---`` YAML front-matter block if present.
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    return text.strip()


def _slice(doc: str, name: str) -> str | None:
    """Extract one reference block, or None if its markers no longer match."""
    start_marker, end_marker = _CHUNKS[name]
    start = doc.find(start_marker)
    if start == -1:
        return None
    if end_marker is None:
        return doc[start:].strip()
    end = doc.find(end_marker, start)
    return doc[start : end if end != -1 else len(doc)].strip()


@functools.lru_cache(maxsize=1)
def _core() -> str:
    """The doc with the heavy reference blocks removed, plus an index of them."""
    doc = _design_doc()
    if not doc:
        return ""
    for name in _CHUNKS:
        block = _slice(doc, name)
        if block:
            doc = doc.replace(block, "", 1)
    # Collapse the holes the removals leave behind.
    doc = re.sub(r"\n{3,}", "\n\n", doc).strip()
    return doc + _INDEX


async def load_design_system(section: str = "") -> str:
    """Load the Metorite design system (palette, type, motion, block kits).

    Call this BEFORE you write a full-page HTML or Markdown **report**, or any
    bespoke **custom HTML/CSS** for an ``emit_generative_ui`` ``html`` node, so
    the result matches the Metorite look. You do NOT need it for named
    ``emit_generative_ui`` templates — those are already on-brand — nor for a
    quick plain-text reply.

    Args:
        section: Empty (default) for the CORE guide — palette, typography,
            spacing, motion, do's and don'ts, the lint contract, and the rule for
            choosing between an inline card, the side panel, and a saved file.
            Start here; it is a third the size of the full document. Then request
            just the reference you need:

            * ``"blocks"`` — the ``cc-report`` document kit.
            * ``"charts"`` — KPI tiles, charts, tables, timelines, decisions.
            * ``"react"`` — React artifacts (``load_artifact_kit()`` is usually
              the better route — the components replace this markup entirely).
            * ``"all"`` — the entire document.

    Returns:
        The requested guidance as Markdown.
    """
    doc = _design_doc()
    if not doc:
        return (
            "Design system unavailable (design.md not found). Use the --cc-* CSS "
            "variables named in the platform tools guidance and keep it clean and "
            "on-brand."
        )

    key = (section or "").strip().lower()
    key = _ALIASES.get(key, key)

    if not key:
        return _core() or doc
    if key == "all":
        return doc
    if key in _CHUNKS:
        block = _slice(doc, key)
        # Markers drifted (someone renamed a heading) — the full doc is the safe
        # answer: too many tokens beats silently returning nothing.
        return block or doc
    return (
        f"No design-system section named {section!r}. "
        f"Available: {', '.join(_CHUNKS)}, all — or omit for the core guide."
    )
