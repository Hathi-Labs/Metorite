"""Unit tests for the generated-artifact linter.

Two jobs here:

1. The individual checks catch the mistakes the sandbox swallows silently
   (CSP-blocked CDN fetches, unknown ``cc-`` classes, kit blocks missing the
   custom property that makes them draw).
2. A drift test parses ``sandbox-frame.ts`` and asserts the Python class
   registry matches the stylesheet exactly — so a new ``cc-`` block cannot be
   added to the CSS (or removed from it) without updating the linter.
"""
from __future__ import annotations

import asyncio
import json
import re
from importlib import import_module
from pathlib import Path

from acb_skills.artifact_lint import CC_CLASSES, lint_artifact_html


async def _noop_notify(**_kwargs) -> None:
    return None


async def _noop_mirror(*_args, **_kwargs) -> None:
    return None


def _prepare(wa, monkeypatch, tmp_path) -> None:
    """Point write_artifact at a temp workspace with its I/O side effects stubbed.

    Both side effects are fire-and-forget tasks spawned on the loop that
    ``asyncio.run`` tears down. ``mirror_to_blob_store`` in particular reaches for
    Postgres, which hangs this test file — and, because these tests sort ahead of
    most of the suite, everything after it. A lint unit test has no business
    touching the blob store, so stub both.
    """
    monkeypatch.setattr(wa, "_notify", _noop_notify)
    monkeypatch.setattr(wa, "mirror_to_blob_store", _noop_mirror)
    wa.bind_artifact_context(**
        {"session_id": "sess-lint", "workspace_root": str(tmp_path)}
    )


REPO_ROOT = Path(__file__).resolve().parents[2]
#: The frame's stylesheet, which is the source of truth for the ``cc-`` class
#: vocabulary. It moved out of ``components/SandboxedHtml.tsx`` when the frame
#: builder was split from the React component (2026-08-07) — this path is the
#: coupling, and pointing it at the wrong file makes the drift test vacuous
#: rather than failing, so it is worth a comment.
SANDBOX_TSX = (
    REPO_ROOT / "workbench" / "control_plane" / "src" / "lib" / "theme" / "sandbox-frame.ts"
)


def _messages(html: str, **kw) -> str:
    return "\n".join(lint_artifact_html(html, **kw))


# ── External resources (the highest-value check) ───────────────────────────

def test_flags_cdn_script_as_error() -> None:
    out = lint_artifact_html(
        '<div class="cc-report"><script src="https://cdn.tailwindcss.com"></script></div>'
    )
    assert any(m.startswith("ERROR") and "cdn.tailwindcss.com" in m for m in out)


def test_flags_remote_stylesheet_and_font_import() -> None:
    html = (
        '<div class="cc-report">'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=X">'
        "<style>@import url(https://example.com/a.css);</style></div>"
    )
    out = _messages(html)
    assert "fonts.googleapis.com" in out
    assert "example.com" in out


def test_documented_cdn_tag_in_a_code_block_is_not_an_error() -> None:
    """A report ABOUT a CDN tag is not a broken artifact.

    The check reads parsed attributes, so escaped markup inside <pre>/<code> is
    text — not a fetch — and must not be reported.
    """
    html = (
        '<div class="cc-report"><h2>Why CDNs fail here</h2>'
        "<pre><code>&lt;script src=\"https://cdn.example.com/x.js\"&gt;&lt;/script&gt;"
        "</code></pre></div>"
    )
    assert lint_artifact_html(html) == []


def test_protocol_relative_url_is_flagged() -> None:
    out = lint_artifact_html('<div class="cc-report"><img src="//cdn.example.com/a.png"></div>')
    assert any(m.startswith("ERROR") for m in out)


def test_flags_remote_image_but_allows_data_uri_and_anchor_links() -> None:
    remote = '<div class="cc-report"><img src="https://img.example/a.png"></div>'
    assert "ERROR" in _messages(remote)

    # A data: URI is inlined, and <a href> is a link the user clicks, not a
    # fetch the CSP blocks — neither should be reported.
    ok = (
        '<div class="cc-report">'
        '<img src="data:image/png;base64,iVBORw0KGgo=">'
        '<a href="https://example.com">docs</a></div>'
    )
    assert "ERROR" not in _messages(ok)


# ── Unknown classes ────────────────────────────────────────────────────────

def test_unknown_cc_class_suggests_nearest_match() -> None:
    out = _messages('<div class="cc-report"><div class="cc-stats"><div class="cc-statt">'
                    '<div class="cc-v">1</div></div></div></div>')
    assert "cc-statt" in out
    assert "cc-stat" in out  # suggestion


def test_known_classes_are_silent() -> None:
    html = (
        '<div class="cc-report">'
        '<p class="cc-eyebrow">Report</p><h1>Title</h1>'
        '<div class="cc-stats"><div class="cc-stat"><p class="cc-k">REVENUE</p>'
        '<div class="cc-v">42<small>%</small></div></div></div></div>'
    )
    assert lint_artifact_html(html) == []


# ── Block structure ────────────────────────────────────────────────────────

def test_bar_without_value_var_is_flagged() -> None:
    out = _messages(
        '<div class="cc-report"><div class="cc-bars">'
        '<div class="cc-bar"><b>A</b><div class="cc-track"></div><span>72%</span>'
        "</div></div></div>"
    )
    assert "--v" in out and "cc-bar" in out


def test_bar_with_value_var_is_clean() -> None:
    html = (
        '<div class="cc-report"><div class="cc-bars">'
        '<div class="cc-bar" style="--v:72"><b>A</b><div class="cc-track"></div>'
        "<span>72%</span></div></div></div>"
    )
    assert lint_artifact_html(html) == []


def test_timeline_bar_needs_start_and_span() -> None:
    out = _messages(
        '<div class="cc-report"><div class="cc-timeline" style="--cols:12">'
        '<div class="cc-tl-row"><b>Design</b><div class="cc-tl-track">'
        '<span class="cc-tl-bar">Design</span></div></div></div></div>'
    )
    assert "--s" in out and "--e" in out


def test_missing_required_child_is_flagged() -> None:
    out = _messages('<div class="cc-report"><div class="cc-stats">'
                    '<div class="cc-stat"><p class="cc-k">X</p></div></div></div>')
    assert "cc-v" in out


# ── Report wrapper ─────────────────────────────────────────────────────────

def test_full_page_without_report_wrapper_is_flagged() -> None:
    assert "cc-report" in _messages("<h1>Quarterly review</h1><p>Body</p>")


def test_inline_card_does_not_need_report_wrapper() -> None:
    assert lint_artifact_html('<div class="cc-card">hi</div>', full_page=False) == []


# ── Palette / anti-slop (learned from web-artifacts-builder) ───────────────

def test_hardcoded_hex_colors_flagged() -> None:
    out = _messages('<div class="cc-report" style="color:#ff0055">x</div>')
    assert "#ff0055" in out and "--cc-" in out


def test_purple_gradient_and_inter_font_flagged() -> None:
    html = (
        '<div class="cc-report"><style>'
        ".h{background:linear-gradient(90deg,#8b5cf6,purple);font-family:Inter,sans-serif}"
        "</style></div>"
    )
    out = _messages(html)
    assert "gradient" in out
    assert "Inter" in out


# ── Robustness ─────────────────────────────────────────────────────────────

def test_empty_and_malformed_input_never_raises() -> None:
    assert lint_artifact_html("") == []
    assert lint_artifact_html("   ") == []
    # Unbalanced/garbage markup must degrade gracefully, not explode.
    assert isinstance(lint_artifact_html('<div class="cc-report"><p><span></div>'), list)


def test_errors_sort_before_warnings() -> None:
    out = lint_artifact_html('<img src="https://x.example/a.png"><div class="cc-nope">')
    assert out[0].startswith("ERROR")


# ── Wiring: the lint must actually reach the agent ─────────────────────────

CLEAN_REPORT = (
    '<div class="cc-report"><p class="cc-eyebrow">Q3</p><h1>Review</h1></div>'
)
BROKEN_REPORT = '<div class="cc-report"><img src="https://cdn.example.com/a.png"></div>'


def test_write_artifact_returns_warnings_for_broken_html(tmp_path, monkeypatch) -> None:
    wa = import_module("acb_skills.write_artifact")
    _prepare(wa, monkeypatch, tmp_path)

    res = asyncio.run(wa.write_artifact("report.html", BROKEN_REPORT))
    # The file is still written — linting is advisory, never blocking.
    assert (tmp_path / "outputs" / "report.html").exists()
    assert any(w.startswith("ERROR") for w in res["warnings"])
    assert "overwrite=True" in res["warning_note"]


def test_write_artifact_clean_html_has_no_warnings_key(tmp_path, monkeypatch) -> None:
    wa = import_module("acb_skills.write_artifact")
    _prepare(wa, monkeypatch, tmp_path)

    res = asyncio.run(wa.write_artifact("report.html", CLEAN_REPORT))
    assert "warnings" not in res


def test_write_artifact_does_not_lint_non_html(tmp_path, monkeypatch) -> None:
    """Markdown and other formats must not be run through an HTML linter."""
    wa = import_module("acb_skills.write_artifact")
    _prepare(wa, monkeypatch, tmp_path)

    res = asyncio.run(wa.write_artifact("notes.md", "# Title\n<img src='https://x.io/a.png'>"))
    assert "warnings" not in res


def test_emit_generative_ui_returns_warnings_for_broken_html(monkeypatch) -> None:
    import asyncio as _asyncio

    wa = import_module("acb_skills.write_artifact")
    executor = import_module("orchestrator.executor")
    queue: _asyncio.Queue = _asyncio.Queue()
    monkeypatch.setattr(executor, "resolve_run_queue", lambda _sid: queue)

    ui = json.dumps({
        "type": "html",
        "props": {"code": '<div class="cc-card"><img src="https://cdn.example.com/a.png"></div>'},
    })
    res = asyncio.run(wa.emit_generative_ui(ui))
    assert res["ok"] is True
    assert any(w.startswith("ERROR") for w in res["warnings"])


def test_react_node_is_not_run_through_the_html_linter(monkeypatch) -> None:
    """JSX is not HTML — linting it would produce nonsense findings.

    A React artifact's `className`/`style={{}}` and its bare component source
    look nothing like a cc-report document, so the HTML linter must stay out of
    the way entirely.
    """
    import asyncio as _asyncio

    wa = import_module("acb_skills.write_artifact")
    executor = import_module("orchestrator.executor")
    queue: _asyncio.Queue = _asyncio.Queue()
    monkeypatch.setattr(executor, "resolve_run_queue", lambda _sid: queue)

    ui = json.dumps({
        "type": "react",
        "props": {"code": 'export default function A(){ return <div className="x"/>; }'},
    })
    res = asyncio.run(wa.emit_generative_ui(ui))
    assert res == {"ok": True}


def test_react_source_is_not_run_through_the_html_linter(tmp_path, monkeypatch) -> None:
    """A .jsx artifact is React source — the HTML DOCUMENT linter must not touch it.

    This test used to assert .jsx produced no warnings at all, using a remote
    <img> as the example. That encoded the bug: the sandbox blocks remote images
    (CSP img-src data:), so the logo silently vanished and the agent was told
    nothing. The narrow remote-asset check now runs on JSX; what must NOT run is
    the document linter, which would flag the missing cc-report wrapper and every
    className as nonsense.
    """
    wa = import_module("acb_skills.write_artifact")
    _prepare(wa, monkeypatch, tmp_path)

    res = asyncio.run(wa.write_artifact(
        "dashboard.jsx",
        'export default function A(){ return <img src="https://x.io/a.png"/>; }',
    ))
    assert res["path"] == "outputs/dashboard.jsx"
    # The remote asset IS reported now...
    assert any("x.io" in w for w in res["warnings"])
    # ...but none of the HTML-document findings are.
    joined = " ".join(res["warnings"])
    assert "cc-report" not in joined
    assert "unknown class" not in joined


def test_emit_generative_ui_inline_card_needs_no_report_wrapper(monkeypatch) -> None:
    import asyncio as _asyncio

    wa = import_module("acb_skills.write_artifact")
    executor = import_module("orchestrator.executor")
    queue: _asyncio.Queue = _asyncio.Queue()
    monkeypatch.setattr(executor, "resolve_run_queue", lambda _sid: queue)

    ui = json.dumps({"type": "html", "props": {"code": '<div class="cc-card">hi</div>'}})
    res = asyncio.run(wa.emit_generative_ui(ui))
    assert res == {"ok": True}


# ── Drift guard: registry vs. the real stylesheet ──────────────────────────

def test_registry_matches_sandbox_stylesheet() -> None:
    """CC_CLASSES must equal the cc-* selectors the sandbox frame actually defines."""
    assert SANDBOX_TSX.exists(), f"missing {SANDBOX_TSX}"
    source = SANDBOX_TSX.read_text(encoding="utf-8")

    # Selectors look like `.cc-foo`. Names ending in "-" come from prose in the
    # comments (e.g. ".cc-t-{success,warning}") and are not real classes.
    found = {
        m for m in re.findall(r"\.(cc-[a-z0-9-]+)", source) if not m.endswith("-")
    }

    missing_from_registry = found - set(CC_CLASSES)
    assert not missing_from_registry, (
        "the sandbox frame defines cc- classes the linter does not know about "
        f"(they would be reported as unknown): {sorted(missing_from_registry)}"
    )

    stale_in_registry = set(CC_CLASSES) - found
    assert not stale_in_registry, (
        "the linter registers cc- classes the stylesheet no longer defines "
        f"(agents would be told they are valid): {sorted(stale_in_registry)}"
    )


# ── React artifacts: the remote-asset check ────────────────────────────────
# Reported symptom: "logos and images did not show up". The frame's CSP is
# img-src data:, so a remote logo renders nothing — and React artifacts got NO
# warning at all, because linting was gated on the .html extension.

def test_remote_logo_in_react_source_is_an_error() -> None:
    from acb_skills.artifact_lint import lint_artifact_source

    out = lint_artifact_source(
        'export default function A(){ return <img src="https://logo.clearbit.com/x.com"/>; }'
    )
    assert out and out[0].startswith("ERROR")
    assert "logo.clearbit.com" in out[0]
    # The message must say what to do instead, not just what failed.
    assert "@cc/ui" in out[0] or "data:" in out[0]


def test_remote_image_and_cdn_hosts_are_caught() -> None:
    from acb_skills.artifact_lint import lint_artifact_source

    for url in (
        "https://example.com/logo.png",
        "//cdn.jsdelivr.net/npm/x/y.svg",
        "https://fonts.googleapis.com/css2?family=Inter",
        "https://www.google.com/s2/favicons?domain=acme.com",
    ):
        out = lint_artifact_source(f'const L = "{url}";')
        assert out, f"missed {url}"


def test_offline_react_source_is_clean() -> None:
    from acb_skills.artifact_lint import lint_artifact_source

    assert lint_artifact_source(
        'import { Report, Icon } from "@cc/ui";\n'
        'export default function A(){ return <Report><Icon name="rocket"/>'
        '<img src="data:image/png;base64,iVBOR"/></Report>; }'
    ) == []
    assert lint_artifact_source("") == []


def test_jsx_artifact_is_linted_on_write(tmp_path, monkeypatch) -> None:
    """The .jsx path must reach the source linter, not the HTML one."""
    wa = import_module("acb_skills.write_artifact")
    _prepare(wa, monkeypatch, tmp_path)

    res = asyncio.run(wa.write_artifact(
        "dash.jsx",
        'export default function A(){ return <img src="https://logo.clearbit.com/a.com"/>; }',
    ))
    assert any("clearbit" in w for w in res["warnings"])


def test_jsx_without_remote_assets_still_has_no_warnings(tmp_path, monkeypatch) -> None:
    """JSX must not be run through the HTML document linter — it would flag the
    missing cc-report wrapper and every className as nonsense."""
    wa = import_module("acb_skills.write_artifact")
    _prepare(wa, monkeypatch, tmp_path)

    res = asyncio.run(wa.write_artifact(
        "dash.jsx",
        'export default function A(){ return <div className="cc-report">hi</div>; }',
    ))
    assert "warnings" not in res
