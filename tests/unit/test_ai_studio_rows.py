"""Section 14 of ai_studio.md holds INDEX lines only (CIV-33 slice 2, task 3337).

The row text lives in project-docs/specs/ai_studio_rows/<id>.md. Two PRs then conflict
only when they edit the same row. A conflict resolution that brings a full row body
back into section 14 fails here. `scripts/ai_studio_rows.py check` is the one rule.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import ai_studio_rows as rows  # noqa: E402


def test_section_14_holds_index_lines_only():
    res = subprocess.run([sys.executable, str(ROOT / "scripts/ai_studio_rows.py"), "check"],
                         capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stdout


def test_a_full_row_body_in_section_14_is_refused(tmp_path, monkeypatch):
    spec = tmp_path / "ai_studio.md"
    spec.write_text(
        "## 14. Slices\n\n" + rows.INDEX_HEAD + "\n|---|---|---|---|---|\n"
        "| **X1** | 🔨 **BUILT.** A long what cell | and a done cell | STD | AGENT-SAFE |\n\n## 15. Next\n",
        encoding="utf-8")
    monkeypatch.setattr(rows, "SPEC", spec)
    monkeypatch.setattr(rows, "ROWS", tmp_path / "rows")
    assert rows.check() == 1


def test_the_split_cells_keep_a_pipe_inside_backticks():
    assert rows.split_cells("| **A** | uses `a | b` here | x | y |") == [" **A** ", " uses `a | b` here ", " x ", " y "]
    assert rows.split_cells("| **A** | what | done | STD | gate without a closing pipe")[-1] == " gate without a closing pipe"
