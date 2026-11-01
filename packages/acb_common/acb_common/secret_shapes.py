"""The shapes of a credential in a string, in one shared list (STU-A2 fix round).

Six older copies of these patterns exist (``tool_refusals.py``, the gateway app
conformance scan and others). A follow-up task points them here. Until then,
this list is the union of those copies.

**Every pattern runs in linear time.** Each repeat has an upper bound. The
long segments use possessive quantifiers, so the engine never backtracks into
them. A hostile string of 20,000 characters costs a bounded number of steps.
"""

from __future__ import annotations

import re

#: ``(label, pattern)``. The label is for a human log line. The scan needs the pattern only.
SECRET_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OpenAI or Anthropic key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,512}+")),
    ("Stripe key", re.compile(r"\b[spr]k_(?:live|test)_[A-Za-z0-9]{8,256}+")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,512}+")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{6,512}+")),
    ("Metorite console key", re.compile(r"\bcc_(?:live|depl)_[A-Za-z0-9_-]{6,512}+")),
    (
        "JSON web token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{5,256}+\.[A-Za-z0-9_-]{8,2048}+\.[A-Za-z0-9_-]{8,1024}+"),
    ),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----")),
    ("bearer value", re.compile(r"(?i)\bbearer[ \t]{1,8}[A-Za-z0-9._~+/-]{16,512}+")),
    (
        "named secret with a value",
        re.compile(
            r"(?i)\b(?:password|passwd|secret|token|api[_-]?key)[ \t]{0,8}[:=][ \t]{0,8}\S{6,512}+"
        ),
    ),
)

#: A key whose name holds one of these words carries a secret value.
SECRET_KEY_WORDS: tuple[str, ...] = (
    "api_key", "apikey", "token", "secret", "password", "passwd", "credential", "private_key",
)  # fmt: skip


def looks_like_secret(text: str) -> bool:
    """True when ``text`` has the shape of a credential."""
    return any(rx.search(text) for _, rx in SECRET_SHAPES)


def is_secret_key(name: object) -> bool:
    """True when the key ``name`` says its value is a secret. ``api-key`` and ``apiKey`` count."""
    lowered = str(name).lower().replace("-", "_")
    return any(w in lowered for w in SECRET_KEY_WORDS)
