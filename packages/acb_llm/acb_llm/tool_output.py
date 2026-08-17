"""Deterministic compression of runtime tool OUTPUT before it re-enters context.

The RTK ("Rust Token Killer") idea applied to Metorite's own runtime
agents: a coding/mutation agent runs ``pytest`` / ``git log`` / a build via the
Copilot SDK's built-in shell tool, and the full multi-thousand-line output folds
straight back into the conversation history that re-enters the model next turn.
That is pure token waste — the agent needs the failures and the tail, not 200
lines of passing-test detail or a progress-bar animation.

This module provides ``compress_tool_output`` — a PURE, deterministic function
(no LLM call, cache-friendly, fast) that:
  * passes small output through byte-identical (threshold-gated);
  * collapses runs of identical lines to ``[... xN ...]``;
  * for recognizable test/pytest output, keeps the failure/error lines and the
    summary tail even when it has to drop the passing middle;
  * otherwise keeps a head + tail around a marker (the same shape as the existing
    sub-agent trimmer), on a newline boundary.

It is applied at the Copilot event-translator seam (``copilot_agent.py``), gated
on the tool NAME so it only touches shell/test/build output — never the
structured JSON that our injected custom tools return and the agent parses.

The full, uncompressed output always remains in the run trace / logs, so nothing
is truly lost (unlike a raw truncation) — the marker says where to find it.

See specs/runtime_agent_effectiveness_2026-07.md (Item ②).
"""
from __future__ import annotations

import os
import re

# Tools whose output is free-form shell/test/build text worth compressing.
# Matched case-insensitively as a substring of the tool name, so "run_in_terminal",
# "shell", "bash", "pwsh", "execute_command" all qualify.
_SHELL_TOOL_HINTS = ("shell", "terminal", "bash", "pwsh", "powershell",
                     "command", "execute", "run_")

# Lines that carry the signal in test/build output — always kept. This is
# deliberately FAILURE/ERROR-biased: a per-line "PASSED" must NOT count as
# signal (else every passing line is kept and nothing compresses). Aggregate
# summary lines ("N failed, M passed") ARE kept via the count pattern.
_SIGNAL_RE = re.compile(
    r"(\bFAIL(ED|URE)?\b|\bERRORS?\b|Traceback|\bassert\b|\bException\b|"
    r"\bpanic\b|error\[|warning:|\bWARN(ING)?\b)",
    re.IGNORECASE,
)
# Aggregate summary lines like "1 failed, 500 passed in 12s" — anchored to the
# START of the (stripped) line so a per-test line "test_5 PASSED" (which contains
# "5 PASSED") does NOT match. Kept alongside signal lines.
_SUMMARY_RE = re.compile(
    r"^=*\s*[0-9]+\s+(passed|failed|error|warning|skipped)",
    re.IGNORECASE,
)

_MARKER = "\n[… output compressed — full text in run trace …]\n"


def _threshold() -> int:
    """Compress only when the output exceeds this many chars (env-tunable)."""
    try:
        return int(os.environ.get("RUNTIME_TOOL_OUTPUT_MAX_CHARS", "4000"))
    except ValueError:
        return 4000


def is_compressible_tool(tool_name: str) -> bool:
    """True for shell/terminal/test-runner tools; False for structured custom tools."""
    name = (tool_name or "").lower()
    return any(h in name for h in _SHELL_TOOL_HINTS)


def _collapse_repeats(lines: list[str]) -> list[str]:
    """Collapse runs of ≥3 identical consecutive lines to one + a count marker."""
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        j = i
        while j < n and lines[j] == lines[i]:
            j += 1
        run = j - i
        out.append(lines[i])
        if run >= 3:
            out.append(f"[... x{run - 1} more identical lines ...]")
        elif run == 2:
            out.append(lines[i])  # keep both when it's just a pair
        i = j
    return out


def _keep_signal_lines(lines: list[str], budget_chars: int) -> str | None:
    """If the text looks like test/build output, keep signal lines + tail.

    Returns the compressed string, or None if this strategy doesn't apply
    (caller falls back to head+tail)."""
    signal_idx = [
        i for i, ln in enumerate(lines)
        if _SIGNAL_RE.search(ln) or _SUMMARY_RE.search(ln.strip())
    ]
    if not signal_idx:
        return None  # not test/build-shaped — use head+tail instead

    keep: set[int] = set()
    # Keep each signal line plus one line of context on each side.
    for i in signal_idx:
        keep.update((i - 1, i, i + 1))
    # Always keep the last few lines (the summary tail).
    keep.update(range(max(0, len(lines) - 6), len(lines)))
    keep = {i for i in keep if 0 <= i < len(lines)}

    ordered = sorted(keep)
    pieces: list[str] = []
    prev = -2
    for i in ordered:
        if i != prev + 1 and pieces:
            pieces.append("[… …]")
        pieces.append(lines[i])
        prev = i
    result = "\n".join(pieces)
    # If keeping signal lines still overflows badly, fall back to head+tail.
    if len(result) > budget_chars * 2:
        return None
    return result


def _head_tail(text: str, budget_chars: int) -> str:
    """Keep head + tail around the marker, cut on a newline boundary."""
    head = (budget_chars * 3) // 4
    tail = budget_chars - head
    head_str = text[:head]
    nl = head_str.rfind("\n")
    if nl > head // 2:
        head_str = head_str[:nl]
    tail_str = text[-tail:] if tail else ""
    nl = tail_str.find("\n")
    if 0 <= nl < tail // 2:
        tail_str = tail_str[nl + 1:]
    return head_str + _MARKER + tail_str


def compress_tool_output(tool_name: str, text: str) -> str:
    """Compress free-form shell/test/build output; pass everything else through.

    Deterministic and lossless-of-signal: small output and non-shell tools are
    returned byte-identical. Large shell output is collapsed (repeat runs) and,
    if it fits, kept whole; otherwise reduced to signal-lines+tail (test/build
    shape) or head+tail (arbitrary output).
    """
    if not text:
        return text
    threshold = _threshold()
    if len(text) <= threshold:
        return text
    if not is_compressible_tool(tool_name):
        return text  # structured custom-tool result — never touch it

    # 1. Collapse repeated lines first — cheap, and often enough on its own.
    lines = text.split("\n")
    collapsed = "\n".join(_collapse_repeats(lines))
    if len(collapsed) <= threshold:
        return collapsed

    # 2. Test/build-shaped: keep signal lines + tail.
    signal = _keep_signal_lines(collapsed.split("\n"), threshold)
    if signal is not None:
        return signal

    # 3. Arbitrary large output: head + tail around the marker.
    return _head_tail(collapsed, threshold)
