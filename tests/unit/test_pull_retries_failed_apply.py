"""A failed apply must be retried, not latched out by its own progress.

🔴 **The checkout is not the delivery, and confusing the two cost three days.**

`vps_pull.sh` decided whether to act by comparing `HEAD` to `origin/release`.
That answers "did the code ARRIVE", never "did the apply SUCCEED" — and
`vps_apply.sh` synchronises the checkout as one of its first acts, hundreds of
lines before it builds anything. So an apply that dies in the middle leaves
HEAD sitting on the target, and every following tick reads that as "already
current". The box never retries, and every signal it emits says it is fine.

Measured on the production box 2026-08-29:

    HEAD           16f5dccd   four merges past the last success
    last-pull-sha  24636e7c   written 14:10, and never again
    last-pull-ok   19:44      fresh, because a no-op touches it

⚠️ **Both markers already existed.** `last-pull-sha` is written only inside the
success branch, so it was the honest record all along. The gate simply consulted
the other one — and `last-pull-ok` cannot tell "up to date" apart from "failing
every five minutes", because a no-op refreshes it either way.

That is the shape of every delivery bug in this repo: a green signal that
describes something adjacent to the thing you care about.

⚠️ Idiom inherited from `test_operator_console_deploy_wiring` and
`test_deploy_venv_ownership`: **every assertion reads NON-COMMENT lines.** The
block under test carries a long comment naming every string here, and a guard
satisfied by prose certifies the documentation rather than the wiring.
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PULL = _ROOT / "scripts/vps_pull.sh"

_MARKER = "last-pull-sha"


def _executable_lines(path: pathlib.Path) -> list[str]:
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def test_the_skip_gate_consults_the_last_APPLIED_sha() -> None:
    """The whole fix. Skipping on HEAD alone is what latched the box out."""
    lines = _executable_lines(_PULL)
    gates = [ln for ln in lines if '"$LOCAL" = "$TARGET"' in ln]
    assert gates, "no skip gate found — has the comparison been renamed?"
    assert all("LAST_OK_SHA" in ln for ln in gates), (
        "the skip gate must also require that the last SUCCESSFUL apply was at "
        "this target. HEAD alone says the code arrived, not that it landed — "
        "and vps_apply.sh moves HEAD long before it builds anything"
    )


def test_the_gate_reads_that_sha_from_the_success_marker() -> None:
    """`LAST_OK_SHA` has to come from the file the success branch writes. Any
    other source re-derives the same wrong answer under a new name."""
    lines = _executable_lines(_PULL)
    reads = [ln for ln in lines if "LAST_OK_SHA=" in ln]
    assert reads, "LAST_OK_SHA is never assigned"
    assert all(_MARKER in ln for ln in reads), (
        f"read LAST_OK_SHA from $STATE_DIR/{_MARKER} — the only file on the "
        "box written exclusively on a successful apply"
    )


def test_the_marker_is_written_ONLY_inside_the_success_branch() -> None:
    """🔴 This is what makes the gate mean anything.

    Move this write outside the `if apply succeeded` branch — or into the
    no-op path beside `last-pull-ok` — and the marker starts recording
    "we tried", not "it worked". The gate above would then latch the box out
    exactly as before, while every test here still passed.
    """
    lines = _executable_lines(_PULL)
    apply_at = next(
        i for i, ln in enumerate(lines) if 'bash "$TMP_APPLY"' in ln
    )
    else_at = next(
        i for i, ln in enumerate(lines[apply_at:], apply_at) if ln.strip() == "else"
    )
    writes = [
        i
        for i, ln in enumerate(lines)
        if _MARKER in ln and ">" in ln and "LAST_OK_SHA" not in ln
    ]
    assert writes, f"nothing ever writes {_MARKER}"
    for i in writes:
        assert apply_at < i < else_at, (
            f"{_MARKER} must be written ONLY where the apply succeeded "
            f"(between lines {apply_at} and {else_at} of the executable body). "
            "Written anywhere else it records an attempt rather than a "
            "delivery, and the retry gate silently stops working"
        )
