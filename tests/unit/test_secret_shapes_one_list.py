"""Task 1272 - the credential patterns live in ONE list.

Fence (R7): ``test_no_second_credential_list`` fails when a regex literal that
holds a known credential prefix appears outside ``secret_shapes.py`` and
outside ``_ALLOWED`` below. Each allow-list row carries its reason.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from acb_common.secret_shapes import (
    KEY_SHAPES,
    REDACTION_BEARER,
    REDACTION_SHAPES,
    SECRET_SHAPES,
    looks_like_secret,
)

_REPO = Path(__file__).resolve().parents[2]
_ONE_LIST = "packages/acb_common/acb_common/secret_shapes.py"

#: Files that may keep a credential-prefix regex. One reason per file.
_ALLOWED: dict[str, str] = {
    "apps/services/customer_console/customer_console/main.py": (
        "The Customer Console is a separate deployable. Its pyproject lists no "
        "acb_common, and test_console_dependency_boundary keeps the two apart. "
        "Its _KEY_SHAPE scrubs a vendor error and needs 6-character fragments."
    ),
    "scripts/reaper.py": (
        "A stdlib-only operations script. It runs outside the workspace venv, so "
        "it cannot import acb_common."
    ),
}

#: A regex literal that names a credential prefix: the prefix, then a class or alternation.
_SHAPE_LITERAL = re.compile(
    r"sk-\\?\[|gh\\?\[pous|AKIA\\?[\[|]|AIza\\?\[|xox\\?\[|cc_(?:live|depl)[-_]?\\?\["
    r"|github_pat_\\?\[|\\?\[spr\\?\]k_|\b[spr]k_(?:live|test)_|\b(?:sk|rk|pk)\)?_(?:live|test)_|\(\?:[spr|]*k\)?_(?:live|test)|\(\?:[a-z|]*(?:gsk|xai|pplx)\b|eyJ\\?\["
)
_SCAN_ROOTS = ("apps", "packages", "scripts", "evals")


def test_no_second_credential_list() -> None:
    found: list[str] = []
    for root in _SCAN_ROOTS:
        for path in (_REPO / root).rglob("*.py"):
            rel = path.relative_to(_REPO).as_posix()
            parts = rel.split("/")
            if rel == _ONE_LIST or rel in _ALLOWED or "tests" in parts or "node_modules" in parts:
                continue
            if ".venv" in parts or "site-packages" in parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for n, line in enumerate(text.splitlines(), 1):
                if _SHAPE_LITERAL.search(line):
                    found.append(f"{rel}:{n}: {line.strip()[:90]}")
    assert not found, (
        "A credential pattern list appeared outside secret_shapes.py. Import it from "
        "acb_common.secret_shapes, or add the file to _ALLOWED with a reason:\n"
        + "\n".join(found)
    )


def test_allow_list_rows_still_hit() -> None:
    """A stale allow-list row hides a gap. Each row must still match a literal."""
    for rel, reason in _ALLOWED.items():
        text = (_REPO / rel).read_text(encoding="utf-8")
        assert reason.strip()
        assert _SHAPE_LITERAL.search(text), f"{rel} no longer needs its allow-list row"


@pytest.mark.parametrize(
    "sample",
    [
        "sk-" + "a" * 20,
        "sk_live_" + "A1b2C3d4E5",
        "rk_test_" + "A1b2C3d4E5",
        "AKIA" + "A" * 16,
        "ASIA" + "B" * 16,
        "AIza" + "x" * 35,
        "ghp_" + "a" * 24,
        "github_pat_" + "a" * 24,
        "xoxb-" + "1" * 12,
        "cc_live_abcdef_" + "z" * 10,
        "cc_depl_abcdef_" + "z" * 10,
        "gsk_" + "a" * 20,
    ],
)
def test_each_shape_is_found_and_scrubbed(sample: str) -> None:
    assert looks_like_secret(f"x {sample} y")
    scrubbed = f"x {sample} y"
    for rx in REDACTION_SHAPES:
        scrubbed = rx.sub("#", scrubbed)
    assert sample[6:] not in scrubbed


def test_cc_live_and_aiza_are_in_the_key_view() -> None:
    assert any(rx.search("cc_live_abcdef_secret") for rx in KEY_SHAPES)
    assert any(rx.search("AIza" + "q" * 35) for rx in KEY_SHAPES)
    assert not any(rx.search("task-123 and ask-me") for rx in KEY_SHAPES)


def test_short_fragments_scrub_but_do_not_flag() -> None:
    assert not looks_like_secret("sk-abc123")
    assert REDACTION_SHAPES[0].sub("#", "err sk-abc123 end") == "err # end"
    assert REDACTION_BEARER.sub("#", "Authorization: Bearer abc") == "Authorization: #"


def test_every_pattern_is_linear() -> None:
    """A 20,000 character hostile string costs well under a second per pattern."""
    hostile = [
        "sk-" + "a" * 20000 + "!",
        "eyJ" + "a" * 20000 + "." + "b" * 20000,
        "eyJaaaaa." + "a." * 10000,
        "bearer " + "a" * 20000,
        "password" + " " * 20000 + "x",
        "-----BEGIN " + "A " * 10000,
        "xox" + "b-" * 10000,
        "github_pat_" + "_" * 20000,
        "AKIA" + "A" * 20000,
        "AIza" + "-" * 20000,
        "cc_live_" + "_" * 20000,
    ]
    patterns = [rx for _, rx in SECRET_SHAPES] + list(REDACTION_SHAPES) + [REDACTION_BEARER]
    start = time.perf_counter()
    for rx in patterns:
        for text in hostile:
            rx.search(text)
            rx.sub("#", text)
    assert time.perf_counter() - start < 10.0


@pytest.mark.parametrize(
    "benign", ["fw-config-update", "gsk_summary", "csk_total_rows", "xai_grok_thing"]
)
def test_plain_words_are_not_scrubbed(benign: str) -> None:
    text = f"see {benign} here"
    for rx in REDACTION_SHAPES:
        assert rx.sub("#", text) == text


def test_real_shaped_vendor_keys_are_scrubbed() -> None:
    for key in ("gsk_" + "a1B2" * 6, "xai-" + "q" * 20, "pplx-" + "z9" * 10):
        out = f"k {key} e"
        for rx in REDACTION_SHAPES:
            out = rx.sub("#", out)
        assert out == "k # e"


@pytest.mark.parametrize(
    "old", ["pk-abcdef12", "rk-abcdef12", "sk_other_abc123", "eyJh.b.c", "eyJhb_c.d-e.f9"]
)
def test_old_tool_refusals_coverage_is_kept(old: str) -> None:
    out = f"x {old} y"
    for rx in REDACTION_SHAPES:
        out = rx.sub("#", out)
    assert out == "x # y"
