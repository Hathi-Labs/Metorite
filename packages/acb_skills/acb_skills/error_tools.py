"""Code-error checking tool — auto-injected into every loaded agent.

Provides ``get_errors`` which mirrors VS Code Copilot's ``get_errors`` tool.
The agent calls this after editing files to check for lint / syntax / type
errors before concluding a task.  Particularly useful in the mutation sandbox
where agents generate code that must be verified.

Design (VS Code parity)
-----------------------
- Accepts optional ``filePaths`` (list of files to check) — if omitted,
  checks all recently changed Python files in the workspace.
- Runs ``python -m py_compile`` for syntax checks and, when available,
  ``mypy`` / ``ruff`` for type and lint checks.
- Returns structured error output (file, line, message) the agent can act on.
"""
from __future__ import annotations

import asyncio
import json as _json
from pathlib import Path


def _find_workspace_root() -> str:
    """The workspace root of the run on this frame, or ``""`` for none.

    H-201 (§21.16): a frame with no run context gets ``""``, and the caller
    checks nothing. The old fallback, the gateway's cwd, read files of no run.
    """
    try:
        from acb_skills.write_artifact import (  # noqa: PLC0415
            artifact_context,
        )
        return str(artifact_context().get("workspace_root") or "")
    except Exception:  # noqa: BLE001
        return ""


async def get_errors(filePaths: str = "[]") -> str:
    """Check Python files for syntax, type, and lint errors.

    Identical to ``run_diagnostics`` (both names are injected; see that tool
    for when to call it).

    Args:
        filePaths: JSON array of workspace-relative paths, e.g.
            ``'["executor.py"]'``; ``'[]'`` auto-discovers recently changed
            ``.py`` files.

    Returns:
        Structured error report, or ``"No errors found."`` if clean.
    """
    root = _find_workspace_root()
    if not root:
        # H-201 (§21.16): no run context, so no workspace. Check nothing.
        return "No workspace is configured for this run, so nothing was checked."
    root_path = Path(root)

    # Parse file paths.
    try:
        paths_raw = _json.loads(filePaths) if isinstance(filePaths, str) else filePaths
    except (_json.JSONDecodeError, TypeError):
        paths_raw = []

    if not isinstance(paths_raw, list):
        return "Error: filePaths must be a JSON array of strings"

    # Resolve to absolute paths.
    targets: list[Path] = []
    if paths_raw:
        for p in paths_raw:
            fp = (root_path / str(p)).resolve()
            if fp.is_file() and fp.suffix == ".py":
                targets.append(fp)
    else:
        # Auto-discover: all .py files modified in the last hour.
        import time
        cutoff = time.time() - 3600
        for fp in root_path.rglob("*.py"):
            if fp.is_file() and "__pycache__" not in str(fp):
                try:
                    if fp.stat().st_mtime > cutoff:
                        targets.append(fp)
                except OSError:
                    pass
        # Cap at 20 files to keep it fast.
        targets = sorted(targets)[:20]

    if not targets:
        return "No Python files to check."

    errors: list[dict] = []

    # Phase 1: Syntax check (py_compile — always available).
    for fp in targets:
        try:
            proc = await asyncio.create_subprocess_exec(
                "python", "-m", "py_compile", str(fp),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=10,
            )
            if proc.returncode != 0:
                err_text = stderr.decode(errors="replace").strip()
                for line in err_text.splitlines():
                    if line.strip():
                        errors.append({
                            "file": str(fp.relative_to(root_path)),
                            "severity": "error",
                            "message": line.strip()[:300],
                            "source": "py_compile",
                        })
        except Exception:  # noqa: BLE001
            pass

    # Phase 2: Ruff lint (if installed).
    try:
        ruff_targets = [str(t.relative_to(root_path)) for t in targets]
        proc = await asyncio.create_subprocess_exec(
            "ruff", "check", "--output-format", "text",
            *ruff_targets,
            cwd=str(root_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=30,
        )
        if proc.returncode != 0 and stdout:
            for line in stdout.decode(errors="replace").splitlines():
                if line.strip():
                    errors.append({
                        "file": "",
                        "severity": "warning",
                        "message": line.strip()[:300],
                        "source": "ruff",
                    })
    except Exception:  # noqa: BLE001
        pass  # ruff not installed or failed

    if not errors:
        return f"No errors found in {len(targets)} file(s)."

    # Format output.
    lines: list[str] = [f"Found {len(errors)} issue(s) in {len(targets)} file(s):"]
    for e in errors[:50]:  # cap output
        src = f"[{e['source']}]" if e.get("source") else ""
        loc = f"{e['file']}:" if e.get("file") else ""
        lines.append(f"  {loc}{e['severity'].upper()}{src} {e['message']}")

    if len(errors) > 50:
        lines.append(f"  ... and {len(errors) - 50} more issues")

    return "\n".join(lines)


async def run_diagnostics(filePaths: str = "[]") -> str:
    """Run code diagnostics on the workspace (syntax + lint check).

    Runs ``py_compile`` (syntax) and ``ruff`` (lint) over the target files.
    ``get_errors`` is the same tool under its other name.

    **Use this tool when:**
    - You have just edited or created Python files
    - You are about to commit changes / report success
    - A run failed and you need to diagnose syntax or lint errors

    Args:
        filePaths: JSON array of file paths relative to the workspace root
            (e.g. ``'["executor.py"]'``), or ``'[]'`` to auto-discover recently
            changed ``.py`` files.

    Returns:
        Structured error report, or a "No errors found" message if clean.
    """
    return await get_errors(filePaths)
