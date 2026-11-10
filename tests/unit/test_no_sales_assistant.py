"""No file names ``agent-sales-assistant`` (WS-53 CRM-C7 item 6, done-when 5).

Spec: ``project-docs/specs/crm_platform.md`` §20.10 item 6. The owner said on
2026-10-12 that the agent does not exist and is not needed. So no route, no
example, no test and no script names it, in any of its spellings:
``agent-sales-assistant``, ``agent_sales_assistant``, and the bare
``sales-assistant`` and ``sales_assistant``.

Four places may still hold the name, because each is a record and not a
caller: ``project-docs/``, ``docs/``, the migrations (``infra/postgres/``,
history under R6), and this fence. A migration deletes the agent's
``dynamic_agents`` row, and ``test_no_sales_assistant_r8.py`` proves that a
load of the agents does not bring it back.

The search runs through ``git grep``, so an untracked scratch file does not
fail the suite. Without git, the fence walks the tree.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: Every spelling, in any case. A space is English, not a name.
NAME_RE = re.compile(r"sales[-_]assistant", re.IGNORECASE)

#: The file names of this fence. A doc that cites the fence is not a caller.
FENCE_NAME_RE = re.compile(r"test_no_sales_assistant(?:_r8)?", re.IGNORECASE)

#: The places that may hold the name, as path prefixes.
EXEMPT_PREFIXES: tuple[str, ...] = (
    "project-docs/",
    "docs/",
    "infra/postgres/",
    "tests/unit/test_no_sales_assistant.py",
    "tests/unit/test_no_sales_assistant_r8.py",
)

_SKIP_DIRS = {".git", "node_modules", ".venv", ".next", "__pycache__", ".codegraph"}
_NEWLINE = chr(10)
_NUL = bytes([0])


def offenders(files: dict[str, str]) -> list[str]:
    """``path: text`` for each line outside the exempt places that names it."""
    out: list[str] = []
    for path, text in files.items():
        if path.startswith(EXEMPT_PREFIXES):
            continue
        for line in text.splitlines():
            if NAME_RE.search(FENCE_NAME_RE.sub("", line)):
                out.append(f"{path}: {line.strip()[:120]}")
    return out


def _git_grep() -> dict[str, str] | None:
    """The matching lines of the tracked text files, or None without git."""
    try:
        out = subprocess.run(
            ["git", "grep", "-I", "-n", "-i", "-E", NAME_RE.pattern],
            cwd=REPO, capture_output=True, check=False,
        )
    except OSError:
        return None
    if out.returncode not in (0, 1):  # 1 means no match
        return None
    texts: dict[str, list[str]] = {}
    for line in out.stdout.decode("utf-8", errors="replace").splitlines():
        path, _, rest = line.partition(":")
        _number, _, text = rest.partition(":")
        texts.setdefault(path, []).append(text)
    return {path: _NEWLINE.join(lines) for path, lines in texts.items()}


def _walk() -> dict[str, str]:
    texts: dict[str, str] = {}
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            path = Path(root, name)
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if _NUL not in raw[:8192]:  # skip a binary file
                texts[path.relative_to(REPO).as_posix()] = raw.decode(
                    "utf-8", errors="replace",
                )
    return texts


def test_no_file_outside_the_records_names_the_removed_agent() -> None:
    tree = _git_grep()
    found = offenders(tree if tree is not None else _walk())
    assert found == [], (
        "agent-sales-assistant was removed (owner, 2026-10-12). Remove the "
        "name, and do not point the caller at another agent unless it needs "
        f"CRM data: {found}"
    )


def test_the_fence_sees_each_spelling() -> None:
    """The fence on the fence: each spelling is red, the records are not."""
    for spelling in ("agent-sales-assistant", "agent_sales_assistant",
                     "Sales-Assistant", "SALES_ASSISTANT", "@sales-assistant hi"):
        assert offenders({"apps/x.py": f"name = '{spelling}'"}), spelling
    assert offenders({"apps/x.py": "You are a sales assistant."}) == []
    assert offenders({"infra/AGENTS.md": "Fence: test_no_sales_assistant_r8.py"}) == []
    assert offenders({"infra/AGENTS.md": "test_no_sales_assistant.py, agent-sales-assistant"})
    for exempt in ("project-docs/a.md", "docs/b.md", "infra/postgres/15_x.sql"):
        assert offenders({exempt: "agent-sales-assistant"}) == []
