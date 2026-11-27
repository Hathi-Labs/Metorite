"""Fence for scripts/home_server/lanes/winddown.sh (LBB-4, task 4042).

engineering_practice.md section 7b.4, Done when 5.
"""

import re
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "home_server" / "lanes" / "winddown.sh"
NEW = (
    "For each open PR that you pushed, queue it with a live merger "
    "(hand_over.sh or a REQUEUE line), or hand it to a named landing lane "
    "in a NOTE and in the release_task handoff."
)


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_new_message_sentence_present():
    assert NEW in _text()


def test_old_ready_and_green_rule_absent():
    assert "only if it is ready and green" not in _text()


def test_no_fixed_session_id():
    assert not re.search(r"^\s*wake\s+[\"']?[0-9a-f]{8}", _text(), re.M)
    assert '"$@"' in _text()
    assert "set -u" in _text()
    assert "usage:" in _text()


def test_bash_syntax():
    bash = shutil.which("bash")
    if bash is None:
        import pytest

        pytest.skip("bash is not installed")
    r = subprocess.run([bash, "-n", str(SCRIPT)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
