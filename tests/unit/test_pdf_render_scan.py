"""CIV-33d: the one source-tree scan of ``test_pdf_render.py``.

The scan walks every Python file of ``apps`` and ``packages``, so
``affected_tests`` runs this file on every Python change. It lives apart from
the large render file so that file runs only when its own imports change.
"""
from __future__ import annotations

from pathlib import Path


def test_no_product_code_imports_the_deprecated_fitz_name() -> None:
    """🔴 **From pymupdf 1.28.2, ``import fitz`` prints to STDOUT.**

    The shim calls ``message_warning`` on import, and pymupdf writes its
    messages to stdout. The render child sends the PDF on stdout, so every
    PDF began with a line of text and the parent refused it. Measured on
    2026-09-28 at the upgrade from 1.28.0. ``import pymupdf`` prints nothing.
    """
    root = Path(__file__).resolve().parents[2]
    offenders = []
    for base in ("apps", "packages"):
        for path in (root / base).rglob("*.py"):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip()
                if stripped.startswith(("import fitz", "from fitz ")):
                    offenders.append(f"{path.relative_to(root)}: {stripped}")
    assert offenders == []
