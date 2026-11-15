"""scripts/sort_fence_lists.py re-sorts tests/fences.json and docs/flag_reload.md (CIV-HS, task 3284).

A lane that conflicts on either file takes main's side, adds its entry, and runs the script.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import sort_fence_lists as sfl  # noqa: E402

_FENCES = """{
  "_comment": ["x"],
  "python": {
    "safety": [
      "tests/unit/test_b.py",
      "tests/unit/test_a.py"
    ],
    "process": [
      "tests/unit/test_z.py"
    ]
  },
  "ts": {"safety": [], "process": []},
  "r8": {
    "must_run": [
      "tests/unit/test_y.py",
      "tests/unit/test_c.py",
      "tests/unit/test_c.py"
    ],
    "never_skip": [
      "tests/unit/test_q.py"
    ]
  }
}
"""


def test_the_lists_are_sorted_unique_and_the_text_around_them_stays():
    out = sfl.sort_fences_text(_FENCES)
    data = json.loads(out)
    assert data["python"]["safety"] == ["tests/unit/test_a.py", "tests/unit/test_b.py"]
    assert data["r8"]["must_run"] == ["tests/unit/test_c.py", "tests/unit/test_y.py"]
    assert '"_comment": ["x"]' in out and '"ts": {"safety": [], "process": []}' in out
    assert sfl.sort_fences_text(out) == out, "a second run changes nothing"


_DOC = """# T

intro

## The flags that reload

A reload applies only the fields.

`B_ORGS` second flag.

A paragraph that continues the second flag.

`A_ORGS` first flag.

A reload does NOT apply any other key.
"""


def test_the_flag_paragraphs_sort_and_a_continuation_stays_with_its_flag():
    out = sfl.sort_flag_doc_text(_DOC)
    assert out.index("`A_ORGS`") < out.index("`B_ORGS`")
    assert out.index("`B_ORGS`") < out.index("A paragraph that continues")
    assert out.index("A paragraph that continues") < out.index("A reload does NOT apply")
    assert sfl.sort_flag_doc_text(out) == out


def test_the_real_files_are_in_order():
    res = subprocess.run([sys.executable, str(ROOT / "scripts/sort_fence_lists.py"), "--check"],
                         capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stdout


def test_lane_rules_name_the_script():
    rules = (ROOT / "scripts/home_server/lanes/lane-rules.md").read_text(encoding="utf-8")
    assert "scripts/sort_fence_lists.py" in rules
