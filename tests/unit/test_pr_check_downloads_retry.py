"""Task 2328: no pr-check step downloads without a retry, and apt-get update retries.

A network blip (curl 56 on the pgdg key) failed a required check twice on PR 1930.
"""
from __future__ import annotations

import re
from pathlib import Path

PR_CHECK = Path(__file__).resolve().parents[2] / ".github/workflows/pr-check.yml"
# Local URLs (the app under test) need no retry. Add a reason here to allow one.
ALLOW = ("localhost", "127.0.0.1")


def _lines():
    for n, ln in enumerate(PR_CHECK.read_text(encoding="utf-8").splitlines(), 1):
        if not ln.lstrip().startswith("#"):
            yield n, ln


def test_every_download_in_pr_check_retries():
    bad = [n for n, ln in _lines()
           if re.search(r"\b(curl|wget)\b\s", ln) and "http" in ln
           and not any(a in ln for a in ALLOW) and "--retry" not in ln and "--tries" not in ln]
    assert not bad, f"pr-check.yml downloads without --retry on lines {bad}"


def test_apt_get_update_retries():
    bad = [n for n, ln in _lines() if re.search(r"apt-get\s+(-\S+\s+)*update", ln) and "Acquire::Retries" not in ln]
    assert not bad, f"apt-get update without Acquire::Retries on lines {bad}"
