"""The shapes of a credential in a string, in one shared list (STU-A2 fix round).

This file is the ONE list of credential patterns (task 1272). The older copies
(``tool_refusals.py``, the gateway app conformance scan, the workflow graph
check, the upstream error scrub, the MCP card mask, the git clone check) import
from here. ``tests/unit/test_secret_shapes_one_list.py`` fails when a new list
appears elsewhere.

Three views of the one list:

* ``SECRET_SHAPES`` finds a whole credential in a string (strict lengths).
* ``KEY_SHAPES`` is the subset of vendor key shapes, with no bearer, no
  ``password=`` pair and no PEM block.
* ``REDACTION_SHAPES`` scrubs text where a vendor echoes part of a key back, so
  its minimum lengths are shorter. Use it to remove, never to decide.

**Every pattern runs in linear time.** Each repeat has an upper bound. The
long segments use possessive quantifiers, so the engine never backtracks into
them. A hostile string of 20,000 characters costs a bounded number of steps.
"""

from __future__ import annotations

import re

#: ``(label, pattern)``. The label is for a human log line. The scan needs the pattern only.
#: The vendor key shapes. ``KEY_SHAPES`` is this tuple, with the labels dropped.
_KEY_ROWS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OpenAI or Anthropic key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,512}+")),
    ("Stripe key", re.compile(r"\b[spr]k_(?:live|test)_[A-Za-z0-9]{8,256}+")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,512}+")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,512}+")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{6,512}+")),
    ("Metorite console key", re.compile(r"\bcc_(?:live|depl)_[A-Za-z0-9_-]{6,512}+")),
    (
        "Other AI vendor key",
        re.compile(r"\b(?:gsk|xai|pplx|csk)[-_][A-Za-z0-9_-]{16,512}+"),
    ),
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,256}+\.[A-Za-z0-9_-]{8,2048}+\.[A-Za-z0-9_-]{8,1024}+")
_PEM = re.compile(r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----")

SECRET_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    *_KEY_ROWS,
    ("JSON web token", _JWT),
    ("private key block", _PEM),
    ("bearer value", re.compile(r"(?i)\bbearer[ \t]{1,8}[A-Za-z0-9._~+/-]{16,512}+")),
    (
        "named secret with a value",
        re.compile(
            r"(?i)\b(?:password|passwd|secret|token|api[_-]?key)[ \t]{0,8}[:=][ \t]{0,8}\S{6,512}+"
        ),
    ),
)

#: The vendor key patterns alone. Use them to flag a key in code or config.
KEY_SHAPES: tuple[re.Pattern[str], ...] = tuple(rx for _, rx in _KEY_ROWS)
#: The PEM private key header.
PRIVATE_KEY_SHAPE: re.Pattern[str] = _PEM
#: The JWT shape.
JWT_SHAPE: re.Pattern[str] = _JWT

_NOT_WORD = r"(?<![A-Za-z0-9_])"
#: Shapes for scrubbing text that echoes part of a key. The minimum lengths are
#: short on purpose, so ``sk-abc123`` goes too. Every repeat is bounded and
#: possessive, so the scan is linear.
REDACTION_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(_NOT_WORD + r"sk-[A-Za-z0-9_-]{6,512}+"),
    re.compile(_NOT_WORD + r"[spr]k_(?:live|test)_[A-Za-z0-9]{8,256}+"),
    re.compile(_NOT_WORD + r"(?:gsk|xai|pplx|csk|fw)[-_][A-Za-z0-9_-]{6,512}+"),
    re.compile(_NOT_WORD + r"gh[pousr]_[A-Za-z0-9]{20,512}+"),
    re.compile(_NOT_WORD + r"github_pat_[A-Za-z0-9_]{20,512}+"),
    re.compile(_NOT_WORD + r"xox[abprs]-[A-Za-z0-9-]{6,512}+"),
    re.compile(_NOT_WORD + r"cc_(?:live|depl)_[A-Za-z0-9_-]{6,512}+"),
    re.compile(_NOT_WORD + r"(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])"),
    re.compile(_NOT_WORD + r"AIza[0-9A-Za-z_-]{6,512}+"),
    re.compile(
        r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,256}+\.[A-Za-z0-9_-]{5,2048}+\.[A-Za-z0-9_-]{1,1024}+"
    ),
)
#: A bearer value of any length, for scrubbing (the strict shape needs 16 characters).
REDACTION_BEARER: re.Pattern[str] = re.compile(r"(?i)\bbearer[ \t\r\n]{1,8}+\S{1,512}+")

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
