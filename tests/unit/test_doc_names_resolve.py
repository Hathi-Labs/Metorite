"""Every markdown file that a live doc names exists. R7's fence for #956.

The cleanup PR #1386 (2026-10-29) deleted research records and specs that live
docs still cited by name. `test_index_completeness.py` saw none of it, because
it checks only the `specs/` rows of `INDEX.md`. A builder who followed such a
citation found nothing, and the owner had asked that research stays reachable
when the work starts.

Two rules, over every `.md` name that a live doc mentions:

1. **The name exists.** Some tracked `.md` file has that file name. Most docs
   cite by bare name (`project_management_app.md`), so this is the rule that
   catches a deletion.
2. **A rooted path resolves.** A mention that starts with `project-docs/`,
   `specs/` or `docs/` (after any `../`) points at a real file. It may be
   relative to the repo root, to `project-docs/`, or to the citing doc. This
   catches a file that moved while its citations kept the old path.

⚠️ **Live docs only.** `project-docs/research/` and `project-docs/specs/archive/`
are records. They keep their text as written, and their old citations are
part of the record. They still count as targets.

⚠️ **The baseline only shrinks.** `BASELINE` freezes the citations that were
already missing when this fence landed. They are findings, not permissions
(CLAUDE.md §5: do not refactor the tree to conform). A new missing name fails.
A baseline entry that starts to resolve also fails, so the list cannot rot.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]

_SKIP_DIRS = {".git", "node_modules", ".venv", ".next", "__pycache__", "dist", "build"}

#: Record folders: they are targets, never citing docs.
_RECORDS = ("project-docs/research/", "project-docs/specs/archive/")

#: Names used as examples, or files that live outside this repo. They never
#: resolve, and that is correct.
EXAMPLE_NAMES: frozenset[str] = frozenset(
    {
        # Files that an agent writes in its own workspace at run time.
        "NOTES.md",
        "SCRIPTS.md",
        "PLAYBOOK.md",
        # Example file names inside a spec's worked examples and mockups.
        "notes.md",
        "q3_summary.md",
        "report.md",
        "summary.md",
        "executive.md",
        # The docs of other repos (SPACE, Paca) that a research section quotes.
        "ecw.md",
        "execution-environments.md",
        "installation.md",
        "automation-workflows.md",
        "repository-structure.md",
        # A file-name rule in docs/style_ste.md, and a sketched file tree in the
        # stale-bannered project-docs/system_architecture.md.
        "CONTRIBUTING.md",
        "CHANGELOG.md",
    }
)

#: Citations that were already missing on 2026-10-29. Fix one, then delete it
#: here. Never add to this set: restore the file or fix the citation instead.
BASELINE: frozenset[str] = frozenset(
    {
        # Deleted by the cleanup PR #1386 as stale. Fix each citation.
        "FOUNDATION_AUDIT_REPORT.md",
        "FOUNDATION_BUILDOUT_CHECKLIST.md",
        "FOUNDATION_CONTINUATION.md",
        "project_plan.md",
        "13-ag-ui-and-generative-ui.md",
        # Deleted before #1386.
        "control_plane_infrastructure.md",
        "platform_control_plane.md",
        "drawio_integration_plan.md",
        "drawio_integration_review.md",
        # A suffix shorthand for saas_multitenancy_implementation.md. Name it in full.
        "_implementation.md",
        # A planned output that nobody has written yet (a framework-uplift done-when).
        "framework-uplift-resolution.md",
    }
)

#: Rooted paths that were already missing on 2026-10-29. Same rule.
BASELINE_PATHS: frozenset[str] = frozenset(
    {
        # The planned member guide of specs/agent_sandbox.md:782.
        "docs/agent_sandbox.md",
    }
)

_MENTION = re.compile(r"(?<![\w/.:@-])((?:\.\./|\./)*[A-Za-z0-9_][\w./-]*\.md)(?![\w])")
_URL_BEFORE = re.compile(r"https?://\S*$")
_PLACEHOLDER = re.compile(r"[<>{}*]")
_ROOTED = re.compile(r"^(?:\.\./|\./)*(?:project-docs|specs|docs)/")


def _tracked_md() -> list[str]:
    """Every tracked `.md` path, POSIX style. A tree with no git falls back to a
    walk that skips the derived dirs."""
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "*.md"],
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout.decode("utf-8")
        paths = [p for p in out.split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        paths = []
        for root, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for f in files:
                if f.endswith(".md"):
                    paths.append((Path(root) / f).relative_to(ROOT).as_posix())
    return sorted(p for p in paths if (ROOT / p).is_file())


def _is_live_doc(path: str) -> bool:
    if path.startswith(_RECORDS):
        return False
    if path.startswith(("project-docs/", "docs/")):
        return True
    return path == "CLAUDE.md" or path.endswith("AGENTS.md")


def _normalise(path: str) -> str:
    parts: list[str] = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts)


def _mentions(text: str) -> list[tuple[int, str]]:
    found = []
    for ln, line in enumerate(text.splitlines(), 1):
        for m in _MENTION.finditer(line):
            token = m.group(1)
            if _URL_BEFORE.search(line[: m.start(1)]) or _PLACEHOLDER.search(token):
                continue
            found.append((ln, token))
    return found


def _scan() -> tuple[int, int, dict[str, list[str]], dict[str, list[str]]]:
    """Return (docs read, mentions read, missing names, missing rooted paths).

    Each missing entry maps to the `path:line` places that cite it."""
    tracked = _tracked_md()
    tracked_set = set(tracked)
    names = {PurePosixPath(p).name for p in tracked}
    docs = [p for p in tracked if _is_live_doc(p)]
    missing_names: dict[str, list[str]] = {}
    missing_paths: dict[str, list[str]] = {}
    count = 0
    for doc in docs:
        text = (ROOT / doc).read_text(encoding="utf-8", errors="replace")
        for ln, token in _mentions(text):
            count += 1
            name = PurePosixPath(token).name
            where = f"{doc}:{ln}"
            if name not in names:
                if name not in EXAMPLE_NAMES:
                    missing_names.setdefault(name, []).append(where)
                continue
            if _ROOTED.match(token):
                here = str(PurePosixPath(doc).parent)
                candidates = {
                    _normalise(token),
                    _normalise("project-docs/" + token),
                    _normalise(here + "/" + token),
                }
                if not candidates & tracked_set:
                    missing_paths.setdefault(token, []).append(where)
    return len(docs), count, missing_names, missing_paths


def test_the_scan_reads_real_docs() -> None:
    """Guards every assertion below from passing because nothing was read."""
    docs, mentions, _, _ = _scan()
    assert docs >= 100, f"only {docs} live docs found; the file list is wrong"
    assert mentions >= 1000, f"only {mentions} .md mentions found; the pattern is wrong"


def test_every_named_doc_exists() -> None:
    """🔴 A live doc names a `.md` file that no tracked file is called."""
    _, _, missing, _ = _scan()
    new = {n: w for n, w in missing.items() if n not in BASELINE}
    assert not new, (
        "These .md names have no file in the repo:\n"
        + "\n".join(f"  {n}  <- {', '.join(w[:4])}" for n, w in sorted(new.items()))
        + "\nRestore the file (`git show <commit>^:<path>`), or fix the citation. "
        "A research record goes to project-docs/research/, a superseded spec to "
        "project-docs/specs/archive/. An example name goes to EXAMPLE_NAMES."
    )


def test_every_rooted_path_resolves() -> None:
    """🔴 A `project-docs/`, `specs/` or `docs/` path points at a file that is not there."""
    _, _, _, missing = _scan()
    new = {p: w for p, w in missing.items() if p not in BASELINE_PATHS}
    assert not new, (
        "These paths do not resolve from the repo root, from project-docs/, or "
        "from the citing doc:\n"
        + "\n".join(f"  {p}  <- {', '.join(w[:4])}" for p, w in sorted(new.items()))
        + "\nThe file probably moved. Change the citation to its new path."
    )


def test_the_baselines_only_shrink() -> None:
    """A baseline entry that resolves now must leave the baseline."""
    _, _, missing_names, missing_paths = _scan()
    fixed = sorted((BASELINE - set(missing_names)) | (BASELINE_PATHS - set(missing_paths)))
    assert not fixed, (
        f"These entries resolve now: {fixed}. Delete them from the baseline in "
        "this file, so that the fence guards them again."
    )
    examples_found = sorted(EXAMPLE_NAMES & {PurePosixPath(p).name for p in _tracked_md()})
    assert not examples_found, (
        f"These EXAMPLE_NAMES are real files now: {examples_found}. Delete them "
        "from EXAMPLE_NAMES, so that the fence checks them."
    )
