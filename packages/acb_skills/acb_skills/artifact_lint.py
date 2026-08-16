"""Validate agent-generated artifact HTML before it reaches the sandbox.

Generated HTML runs in an opaque-origin iframe under a strict CSP
(``lib/theme/sandbox-frame.ts``). That sandbox is deliberately unforgiving and, crucially,
**silent**: a CDN ``<script src>`` is simply blocked, a typo'd ``cc-`` class name
just renders unstyled, and a ``cc-bar`` missing its ``--v`` custom property draws
an empty track. The agent gets no signal that any of it went wrong — it writes
the file, the tool returns success, and the user opens a broken report.

This module is the missing feedback loop. It parses the generated markup and
reports the mistakes the sandbox would otherwise swallow, so the agent can fix
them on the same turn. It is advisory only: linting NEVER blocks a write, and a
parse failure degrades to "no findings" rather than failing the tool.

The class registry below mirrors the stylesheet injected by
``workbench/control_plane/src/lib/theme/sandbox-frame.ts``.
``tests/unit/test_artifact_lint.py`` parses that file and fails if the two drift
apart, so a new ``cc-`` block cannot be added to the CSS without being
registered here (and vice versa).
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from html.parser import HTMLParser

# ── The cc-* class registry ────────────────────────────────────────────────
# Every class the sandbox stylesheet actually defines, grouped as an agent
# thinks about them. Kept in sync with lib/theme/sandbox-frame.ts by a drift test.

_LAYOUT = {
    "cc-report", "cc-card", "cc-grid", "cc-btn",
}
_DOCUMENT = {
    "cc-eyebrow", "cc-sec-num", "cc-lede", "cc-callout", "cc-callout-key",
    "cc-tag", "cc-chips", "cc-chip", "cc-dot", "cc-compare", "cc-yes", "cc-no",
    "cc-partial", "cc-pill", "cc-diagram", "cc-hl", "cc-steps", "cc-step",
    "cc-n", "cc-phase", "cc-badge",
}
_DATAVIZ = {
    "cc-stats", "cc-stat", "cc-k", "cc-v", "cc-d", "cc-up", "cc-down",
    "cc-feature", "cc-bars", "cc-bar", "cc-track", "cc-donuts", "cc-donut",
    "cc-ring", "cc-spark", "cc-fill", "cc-chart", "cc-plot", "cc-area",
    "cc-line", "cc-end", "cc-x", "cc-legend",
}
_TABLE = {
    "cc-table", "cc-num", "cc-dim", "cc-stripe", "cc-status", "cc-minibar",
    "cc-tag-pill",
}
_TIMELINE = {
    "cc-timeline", "cc-tl-row", "cc-tl-track", "cc-tl-bar", "cc-tl-axis",
    "cc-tl-ticks", "cc-ghost",
}
_ARCH = {
    "cc-arch", "cc-arch-row", "cc-node", "cc-node-t", "cc-node-s", "cc-arrow",
    "cc-bi",
}
_NOTES = {
    "cc-note", "cc-ico", "cc-info", "cc-decision", "cc-mark", "cc-verdict",
}
_MODIFIERS = {
    "cc-primary", "cc-accent", "cc-muted", "cc-success", "cc-warning",
    "cc-danger", "cc-t-success", "cc-t-warning", "cc-t-danger", "cc-t-accent",
}

CC_CLASSES: frozenset[str] = frozenset(
    _LAYOUT | _DOCUMENT | _DATAVIZ | _TABLE | _TIMELINE | _ARCH | _NOTES | _MODIFIERS
)

# Blocks that only draw correctly when the agent supplies a CSS custom property.
# class → the custom properties its inline style MUST set.
_REQUIRED_VARS: dict[str, tuple[str, ...]] = {
    "cc-bar": ("--v",),
    "cc-donut": ("--v",),
    "cc-minibar": ("--v",),
    "cc-tl-bar": ("--s", "--e"),
}

# Blocks whose visual depends on a required descendant. class → (child, why).
_REQUIRED_CHILD: dict[str, tuple[str, str]] = {
    "cc-bar": ("cc-track", "the bar has no track to fill"),
    "cc-donut": ("cc-ring", "the gauge ring will not render"),
    "cc-stat": ("cc-v", "the stat has no value to show"),
    "cc-tl-row": ("cc-tl-track", "the row has no timeline track"),
}

_MAX_FINDINGS = 14


@dataclass(frozen=True)
class Finding:
    """One lint result. ``severity`` is ``"error"`` or ``"warning"``."""

    severity: str
    message: str

    def render(self) -> str:
        prefix = "ERROR" if self.severity == "error" else "warning"
        return f"{prefix}: {self.message}"


# ── Minimal DOM ────────────────────────────────────────────────────────────

_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
})


@dataclass
class _El:
    tag: str
    classes: frozenset[str]
    style: str
    children: list[_El]
    attrs: dict[str, str]


class _Tree(HTMLParser):
    """Tolerant HTML → element tree, plus the raw text of every <style> block."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _El("#root", frozenset(), "", [], {})
        self._stack: list[_El] = [self.root]
        self.style_css: list[str] = []
        self._in_style = False

    def _build(self, tag: str, attrs: list[tuple[str, str | None]]) -> _El:
        by_name = {k.lower(): (v or "") for k, v in attrs}
        return _El(
            tag.lower(),
            frozenset(by_name.get("class", "").split()),
            by_name.get("style", ""),
            [],
            by_name,
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        el = self._build(tag, attrs)
        self._stack[-1].children.append(el)
        if el.tag == "style":
            self._in_style = True
        if el.tag not in _VOID_TAGS:
            self._stack.append(el)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].children.append(self._build(tag, attrs))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "style":
            self._in_style = False
        # Tolerant close: unwind to the nearest matching open tag, if any.
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.style_css.append(data)


def _walk(el: _El):
    for child in el.children:
        yield child
        yield from _walk(child)


def _descendant_classes(el: _El) -> frozenset[str]:
    out: set[str] = set()
    for child in _walk(el):
        out |= child.classes
    return frozenset(out)


# ── Individual checks ──────────────────────────────────────────────────────

_REMOTE = re.compile(r"""^\s*(?:https?:)?//""", re.I)
# Remote URLs referenced from CSS (a <style> block or a style attribute).
_CSS_REMOTE_RE = re.compile(
    r"""(?:url\(|@import\s+(?:url\()?)\s*["']?((?:https?:)?//[^\s"'<>)]+)""", re.I,
)

# Attributes that make the browser FETCH the URL (and are therefore CSP-blocked).
# `href` counts only on <link>: on <a> it is a link the user clicks, not a fetch.
_FETCHING_ATTRS = ("src", "srcset", "poster", "data")


def _check_external_resources(tree: _Tree) -> list[Finding]:
    """Remote fetches are blocked by the sandbox CSP — silently, so flag loudly.

    Driven off parsed attributes rather than a raw-text scan, so a report that
    *documents* a CDN tag inside an escaped <pre> block is not reported as a
    broken artifact.
    """
    urls: list[str] = []
    for el in _walk(tree.root):
        for attr in _FETCHING_ATTRS:
            value = el.attrs.get(attr, "")
            if value and _REMOTE.match(value):
                urls.append(value.strip())
        if el.tag == "link" and _REMOTE.match(el.attrs.get("href", "")):
            urls.append(el.attrs["href"].strip())
        urls.extend(_CSS_REMOTE_RE.findall(el.style))
    for css in tree.style_css:
        urls.extend(_CSS_REMOTE_RE.findall(css))

    return [
        Finding(
            "error",
            f"remote resource {url[:80]!r} is blocked by the sandbox CSP and will "
            f"silently fail to load — inline the asset or embed it as a data: URI",
        )
        for url in dict.fromkeys(urls)  # dedupe, preserve order
    ]


def _check_unknown_classes(tree: _Tree) -> list[Finding]:
    """A cc- class the stylesheet does not define renders completely unstyled."""
    used: set[str] = set()
    for el in _walk(tree.root):
        used |= {c for c in el.classes if c.startswith("cc-")}
    out: list[Finding] = []
    for cls in sorted(used - CC_CLASSES):
        near = difflib.get_close_matches(cls, CC_CLASSES, n=1, cutoff=0.7)
        hint = f" — did you mean {near[0]!r}?" if near else (
            " — it is not part of the report kit, so it will render unstyled"
        )
        out.append(Finding("warning", f"unknown class {cls!r}{hint}"))
    return out


def _check_block_structure(tree: _Tree) -> list[Finding]:
    """Kit blocks that need a custom property or a specific child to draw."""
    out: list[Finding] = []
    seen: set[str] = set()
    for el in _walk(tree.root):
        for cls in sorted(el.classes):
            for var in _REQUIRED_VARS.get(cls, ()):
                key = f"var:{cls}:{var}"
                if var not in el.style and key not in seen:
                    seen.add(key)
                    out.append(Finding(
                        "warning",
                        f"{cls!r} has no {var} in its style attribute — it will "
                        f'render empty (e.g. style="{var}:72")',
                    ))
            if cls in _REQUIRED_CHILD:
                child, why = _REQUIRED_CHILD[cls]
                key = f"child:{cls}"
                if child not in _descendant_classes(el) and key not in seen:
                    seen.add(key)
                    out.append(Finding(
                        "warning", f"{cls!r} is missing a {child!r} child — {why}",
                    ))
    return out


def _check_report_wrapper(tree: _Tree, full_page: bool) -> list[Finding]:
    if not full_page:
        return []
    for el in _walk(tree.root):
        if "cc-report" in el.classes:
            return []
    return [Finding(
        "warning",
        'full-page report is not wrapped in <div class="cc-report"> — it loses the '
        "document width, typography, and spacing of the report kit",
    )]


_HEX_RE = re.compile(r"#(?:[0-9a-f]{3}|[0-9a-f]{6})\b", re.I)


def _check_hardcoded_colors(tree: _Tree) -> list[Finding]:
    """Literal colors drift off-palette and break the light/dark theme switch."""
    blob = " ".join(tree.style_css) + " " + " ".join(
        el.style for el in _walk(tree.root)
    )
    hexes = {h.lower() for h in _HEX_RE.findall(blob)}
    if not hexes:
        return []
    sample = ", ".join(sorted(hexes)[:4])
    return [Finding(
        "warning",
        f"hard-coded color(s) {sample} — use the --cc-* tokens (--cc-primary, "
        f"--cc-accent, --cc-fg, --cc-muted, --cc-card, --cc-border) so the "
        f"artifact tracks the user's light/dark theme",
    )]


# Learned from Anthropic's web-artifacts-builder skill, which names these as the
# tells of generic LLM-generated design ("AI slop").
_SLOP_PURPLE = re.compile(
    r"gradient\([^)]*(?:purple|violet|indigo|#a855f7|#8b5cf6|#7c3aed|#6366f1)", re.I,
)
_SLOP_INTER = re.compile(r"font-family\s*:[^;}]*\bInter\b", re.I)


def _check_slop(tree: _Tree) -> list[Finding]:
    blob = " ".join(tree.style_css) + " " + " ".join(
        el.style for el in _walk(tree.root)
    )
    out: list[Finding] = []
    if _SLOP_PURPLE.search(blob):
        out.append(Finding(
            "warning",
            "purple/violet gradient detected — a hallmark of generic AI-generated "
            "design. Metorite is blue (--cc-primary) with one warm-orange "
            "accent (--cc-accent); drop the gradient",
        ))
    if _SLOP_INTER.search(blob):
        out.append(Finding(
            "warning",
            "explicit Inter font — omit font-family entirely; the sandbox already "
            "sets the on-brand system stack",
        ))
    centered = sum(
        1 for el in _walk(tree.root) if "text-align:center" in el.style.replace(" ", "")
    )
    if centered >= 4:
        out.append(Finding(
            "warning",
            f"{centered} centered blocks — Metorite reports are left-aligned; "
            "reserve centering for stat tiles and gauges",
        ))
    return out


# ── Entry point ────────────────────────────────────────────────────────────

_SOURCE_REMOTE_RE = re.compile(
    r"""["'`](?:https?:)?//[^\s"'`]+\.(?:png|jpe?g|gif|webp|svg|ico|avif|bmp)\b""",
    re.I,
)
_SOURCE_CDN_RE = re.compile(
    r"""["'`](?:https?:)?//(?:cdn|unpkg|jsdelivr|fonts\.googleapis|fonts\.gstatic|"""
    r"""logo\.clearbit|www\.google\.com/s2|icons\.duckduckgo)[^\s"'`]*""",
    re.I,
)


def lint_artifact_source(source: str) -> list[str]:
    """Lint a REACT artifact's source for the mistakes the sandbox swallows.

    The HTML linter cannot run on JSX — `className`, `style={{}}` and a bare
    component module look nothing like a cc-report document, so it would produce
    only noise. But the one failure that matters most is shared: the frame's CSP
    is ``img-src data:``, so a remote logo or image silently renders nothing.
    That is exactly the "logos didn't show up" report, and it produced no warning
    at all because linting was gated on the .html extension.

    Deliberately narrow — remote asset URLs and the icon/CDN hosts agents reach
    for by habit. Everything else about JSX is left to the compiler.
    """
    if not source or not source.strip():
        return []
    hits: list[str] = []
    for rx in (_SOURCE_REMOTE_RE, _SOURCE_CDN_RE):
        hits.extend(m.strip("\"'`") for m in rx.findall(source))
    out: list[str] = []
    for url in dict.fromkeys(hits):
        out.append(
            f"ERROR: remote asset {url[:80]!r} will not load — artifacts run "
            f"offline (CSP img-src data:), so remote logos and images render as "
            f"nothing. Use an <Icon name=\"…\"/> from @cc/ui, inline an SVG, or "
            f"embed the image as a data: URI."
        )
    return out[:_MAX_FINDINGS]


def lint_artifact_html(html: str, *, full_page: bool = True) -> list[str]:
    """Lint generated artifact HTML; return human-readable findings, worst first.

    ``full_page`` marks a saved ``.html`` report (side panel) as opposed to an
    inline ``emit_generative_ui`` card — only the former is expected to carry the
    ``cc-report`` wrapper. Returns ``[]`` for clean markup. Never raises.
    """
    if not html or not html.strip():
        return []
    try:
        tree = _Tree()
        tree.feed(html)
        tree.close()
        findings = [
            *_check_external_resources(tree),
            *_check_unknown_classes(tree),
            *_check_block_structure(tree),
            *_check_report_wrapper(tree, full_page),
            *_check_hardcoded_colors(tree),
            *_check_slop(tree),
        ]
    except Exception:  # noqa: BLE001 — advisory only; never break a write
        return []
    findings.sort(key=lambda f: 0 if f.severity == "error" else 1)
    rendered = [f.render() for f in findings[:_MAX_FINDINGS]]
    if len(findings) > _MAX_FINDINGS:
        rendered.append(f"… and {len(findings) - _MAX_FINDINGS} more")
    return rendered
