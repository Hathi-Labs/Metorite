"""Custom Apps · publish-time conformance scan (RFC §4.0, layer 3).

Static checks that a bundle honours the platform contract — Metorite is
the app's only backbone. The sandbox already makes deviations non-functional
at runtime (layer 2); this scan catches them at publish so the author hears
"use the platform way" from the builder instead of shipping a broken app.

Errors block the publish (422 ``conformance_failed``); warnings ride along in
the success payload for the Workshop UI to surface.
"""

from __future__ import annotations

import re

# External resource loads / network calls — non-functional in the sandbox and
# off-architecture by contract. Matched case-insensitively.
_ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "external script/style/media reference (bundle everything inline)",
        re.compile(
            r"""(?:src|href)\s*=\s*["'](?:https?:)?//""", re.IGNORECASE,
        ),
    ),
    (
        "external URL in CSS url() (inline or use data: URIs)",
        re.compile(r"""url\(\s*["']?(?:https?:)?//""", re.IGNORECASE),
    ),
    (
        "direct network call (use window.cc — the platform brokers all I/O)",
        re.compile(
            r"""(?:fetch|importScripts)\s*\(\s*["'`](?:https?:)?//"""
            r"""|new\s+(?:WebSocket|EventSource|XMLHttpRequest)\s*\(""",
            re.IGNORECASE,
        ),
    ),
    (
        "service worker registration (not supported in the sandbox)",
        re.compile(r"navigator\.serviceWorker", re.IGNORECASE),
    ),
    (
        "@import of an external stylesheet",
        re.compile(r"""@import\s+(?:url\()?\s*["']?(?:https?:)?//""", re.IGNORECASE),
    ),
)

# Credential-shaped strings — nothing key-like belongs in an app, ever.
_KEY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OpenAI/Anthropic-style secret key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
)

# Likely-broken-in-sandbox usage: worth a warning, not a block (an app may
# feature-detect and fall back to cc.storage).
_WARNING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "browser storage is unavailable in the sandbox — use cc.storage",
        re.compile(r"\b(?:localStorage|sessionStorage|indexedDB)\b"),
    ),
    (
        "external URL in code/markup — external resources do not load in the sandbox",
        re.compile(r"""https?://(?!www\.w3\.org/)""", re.IGNORECASE),
    ),
)


def scan_bundle(html: str) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for a bundle against the platform contract.

    Heuristic by design: it cannot prove conformance (the sandbox is the real
    enforcement), only catch the common deviations early with a message that
    names the platform-native alternative.
    """
    errors: list[str] = []
    warnings: list[str] = []
    for message, pattern in _ERROR_PATTERNS:
        if pattern.search(html):
            errors.append(message)
    for message, pattern in _KEY_PATTERNS:
        if pattern.search(html):
            errors.append(f"credential-shaped string found: {message}")
    error_hit = bool(errors)
    for message, pattern in _WARNING_PATTERNS:
        match = pattern.search(html)
        if match is None:
            continue
        # A bare-URL warning is redundant when a stricter error already fired.
        if error_hit and message.startswith("external URL"):
            continue
        warnings.append(message)
    return errors, warnings
