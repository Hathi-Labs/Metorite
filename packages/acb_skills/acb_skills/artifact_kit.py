"""On-demand API reference for the ``@cc/ui`` artifact design kit.

The kit itself (``workbench/control_plane/src/lib/artifactUi.tsx``) is compiled
server-side and never enters an agent's context. What *would* cost context is
documenting it: ~25 components, and a hand-written reference would both bloat
``design.md`` and drift from the source the moment anyone edits a prop.

So the reference is derived from the source and served in two tiers:

* ``load_artifact_kit()`` — a compact INDEX: one line per component. Enough to
  choose, a few hundred tokens for the lot.
* ``load_artifact_kit("Stat,Table")`` — full signatures for ONLY the components
  actually being used.

``artifact_kit.json`` is generated from the .tsx by :func:`parse_kit_source` and
committed, so this package has no runtime dependency on the frontend tree. A
drift test re-parses the real source and fails if the two disagree, which is what
keeps the reference honest without anyone maintaining it by hand.
"""
from __future__ import annotations

import functools
import json
import re
from pathlib import Path

# ── Parsing the kit source ─────────────────────────────────────────────────

_SECTION_RE = re.compile(r"^//\s*──\s*(.+?)\s*─+", re.M)
_EXPORT_RE = re.compile(
    r"export function (?P<name>\w+)\("
    r"\s*\{(?P<destructure>.*?)\}\s*:\s*\{(?P<types>.*?)\}\s*\)\s*\{",
    re.S,
)


def _doc_before(source: str, pos: int) -> str:
    """The JSDoc immediately preceding *pos*, or "".

    Deliberately not part of the export regex: an optional ``/** … */`` prefix
    there matches greedily across intervening code, so a component would inherit
    the docstring of whatever unexported helper happened to sit above it.
    """
    head = source[:pos].rstrip()
    if not head.endswith("*/"):
        return ""
    start = head.rfind("/**")
    if start == -1:
        return ""
    # Reject a "preceding" block that has code between it and the export.
    if "*/" in head[start + 3 : -2]:
        return ""
    return _clean_doc(head[start + 3 : -2])


def _summarise(text: str, limit: int = 62) -> str:
    """One short line for the index. Length-capped rather than sentence-split —
    abbreviations like "e.g." make sentence splitting cut mid-thought."""
    text = text.strip().rstrip(".")
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _clean_doc(raw: str | None) -> str:
    """Collapse a JSDoc block into one line of prose."""
    if not raw:
        return ""
    lines = [re.sub(r"^\s*\*?\s?", "", ln).strip() for ln in raw.splitlines()]
    return " ".join(ln for ln in lines if ln).strip()


_FIELD_RE = re.compile(r"^(\w+)(\??)\s*:\s*(.+?);?\s*$")


def _parse_props(types: str) -> list[dict]:
    """Turn a props type body into [{name, type, optional, doc}].

    Line-based on purpose. Splitting the block on ';' looks tidier but silently
    drops fields whose JSDoc contains a semicolon ("Signed change; sign picks the
    colour" cost us Stat.delta), and every field in this file is one per line.
    """
    props: list[dict] = []
    doc_lines: list[str] = []
    in_block = False

    for raw in types.splitlines():
        line = raw.strip()
        if not line:
            continue
        if in_block:
            if line.endswith("*/"):
                doc_lines.append(line[:-2])
                in_block = False
            else:
                doc_lines.append(line)
            continue
        if line.startswith("/**"):
            if line.endswith("*/") and len(line) > 4:
                doc_lines = [line[3:-2]]
            else:
                doc_lines = [line[3:]]
                in_block = True
            continue
        if line.startswith("//"):
            continue
        match = _FIELD_RE.match(line)
        if match:
            props.append({
                "name": match.group(1),
                "optional": match.group(2) == "?",
                "type": " ".join(match.group(3).split()),
                "doc": _clean_doc("\n".join(doc_lines)),
            })
        doc_lines = []
    return props


def parse_kit_source(source: str) -> list[dict]:
    """Extract the component API from artifactUi.tsx.

    Returns ``[{name, section, summary, props: [...]}, …]`` in source order.
    """
    # Map a character offset to the section heading it falls under.
    sections = [(m.start(), m.group(1)) for m in _SECTION_RE.finditer(source)]

    def section_for(pos: int) -> str:
        name = ""
        for start, title in sections:
            if start < pos:
                name = title
            else:
                break
        return name

    out: list[dict] = []
    for m in _EXPORT_RE.finditer(source):
        out.append({
            "name": m.group("name"),
            "section": section_for(m.start()),
            "summary": _doc_before(source, m.start()),
            "props": _parse_props(m.group("types")),
        })
    return out


# ── Serving the reference ──────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _kit() -> list[dict]:
    try:
        import acb_skills  # noqa: PLC0415

        path = Path(acb_skills.__file__).parent / "artifact_kit.json"
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — never fail the tool on a missing file
        return []


def _render_index(kit: list[dict]) -> str:
    lines = [
        '@cc/ui — prebuilt React components for artifacts. import { X } from "@cc/ui";',
        "",
        "Use these instead of hand-writing divs: they are on-brand by construction,",
        "tree-shaken (you pay only for what you import), and cost a fraction of the",
        "tokens. Call load_artifact_kit(\"Stat,Table\") for the exact props of the ones",
        "you pick.",
        "",
    ]
    current = ""
    for comp in kit:
        if comp["section"] != current:
            current = comp["section"]
            lines.append(f"{current.upper()}")
        summary = _summarise(comp["summary"])
        lines.append(f"  {comp['name']:<12} {summary}")
    return "\n".join(lines)


def _render_component(comp: dict) -> str:
    lines = [f"{comp['name']} — {comp['summary']}" if comp["summary"] else comp["name"]]
    if not comp["props"]:
        lines.append("  (no props)")
    for p in comp["props"]:
        flag = "?" if p["optional"] else " "
        line = f"  {p['name']}{flag}: {p['type']}"
        if p["doc"]:
            line += f"   — {p['doc']}"
        lines.append(line)
    return "\n".join(lines)


async def load_artifact_kit(components: str = "") -> str:
    """Look up the ``@cc/ui`` React components available inside artifacts.

    Call this BEFORE writing a React artifact (an ``emit_generative_ui``
    ``react`` node, or an ``outputs/*.jsx`` file). ``@cc/ui`` gives you the whole
    Metorite design kit — stat tiles, bar/donut/trend charts, data tables,
    timelines, decision boxes, and buttons that talk back to you — as components,
    so you write ``<Stat label="Revenue" value={18} unit="%" delta={12} />``
    instead of six nested divs of exact class names.

    REACH FOR THIS WITHOUT BEING ASKED whenever you are about to present real
    data — more than a handful of rows or metrics, anything with a trend, a
    breakdown, a status per row, or a comparison. A rendered artifact beats a
    markdown table at that size, and composing these components costs a fraction
    of the tokens of hand-writing the markup (and cannot typo a class name).
    Volume also picks the surface: a few rows belong inline in the chat, a large
    or explorable set belongs in the side panel (``"surface": "panel"``, or a
    saved ``outputs/*.jsx``).

    Args:
        components: Empty for the INDEX (every component, one line each — start
            here). Or a comma-separated list of names
            (``"Stat,Bars,Table"``) for the full props of just those.

    Returns:
        The reference as plain text.
    """
    kit = _kit()
    if not kit:
        return (
            "The @cc/ui component reference is unavailable. You can still write "
            "React artifacts using plain JSX with the cc-* CSS classes from "
            "load_design_system()."
        )
    wanted = [c.strip().lower() for c in components.split(",") if c.strip()]
    if not wanted:
        return _render_index(kit)

    by_name = {c["name"].lower(): c for c in kit}
    found = [by_name[w] for w in wanted if w in by_name]
    missing = [w for w in wanted if w not in by_name]
    if not found:
        return (
            f"No such component(s): {', '.join(missing)}. "
            f"Available: {', '.join(c['name'] for c in kit)}"
        )
    out = "\n\n".join(_render_component(c) for c in found)
    if missing:
        out += f"\n\n(no such component: {', '.join(missing)})"
    return out
