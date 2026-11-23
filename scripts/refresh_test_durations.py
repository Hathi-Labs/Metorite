"""CIV-33b: refresh `.test_durations` from the newest green FULL run.

Only a FULL pr-check run uploads the `test-durations` artifact (deploy.yml's
push batch uploads none). The script reads GitHub through `gh` and writes
nothing there, except with ``--pr``, which opens a pull request when the file
changed. It never pushes to main. A box timer runs it nightly. The unit files
are in scripts/home_server/systemd/.

Fence: tests/unit/test_refresh_test_durations.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DURATIONS = ROOT / ".test_durations"
ARTIFACT = "test-durations"


def merge(
    old: dict[str, float], new: dict[str, float], exists=lambda path: True
) -> dict[str, float]:
    """The new timings win. A file in ``new`` replaces all its old entries.
    A file that no longer ``exists`` drops out. The result is sorted."""
    fresh_files = {k.split("::", 1)[0] for k in new}
    out = {
        k: v
        for k, v in old.items()
        if k.split("::", 1)[0] not in fresh_files and exists(k.split("::", 1)[0])
    }
    out.update({k: v for k, v in new.items() if exists(k.split("::", 1)[0])})
    return dict(sorted(out.items()))


def render(data: dict[str, float]) -> str:
    """The stable file text: one entry a line, sorted, a final newline."""
    return json.dumps(dict(sorted(data.items())), indent=0) + "\n"


def pick_artifact(artifacts: list[dict], conclusion_of) -> dict | None:
    """The newest live artifact whose run ended in success."""
    live = [a for a in artifacts if not a.get("expired")]
    for art in sorted(live, key=lambda a: a["created_at"], reverse=True):
        run_id = (art.get("workflow_run") or {}).get("id")
        if run_id and conclusion_of(run_id) == "success":
            return art
    return None


def _gh(*args: str, binary: bool = False):
    res = subprocess.run(["gh", *args], capture_output=True, check=True, cwd=ROOT)
    return res.stdout if binary else res.stdout.decode("utf-8")


def _conclusion(run_id: int) -> str:
    return _gh("api", f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}", "--jq", ".conclusion").strip()


def fetch_newest() -> tuple[dict, dict[str, float]]:
    raw = _gh("api", f"repos/{{owner}}/{{repo}}/actions/artifacts?name={ARTIFACT}&per_page=30")
    art = pick_artifact(json.loads(raw)["artifacts"], _conclusion)
    if art is None:
        raise SystemExit("no green run with a live test-durations artifact")
    blob = _gh("api", f"repos/{{owner}}/{{repo}}/actions/artifacts/{art['id']}/zip", binary=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        data = json.loads(z.read("test_durations.json").decode("utf-8"))
    return art, {str(k): float(v) for k, v in data.items()}


def _open_pr(stamp: str, note: str) -> None:
    branch = f"durations-refresh-{stamp}"

    def git(*a: str) -> None:
        subprocess.run(["git", *a], check=True, cwd=ROOT)

    git("switch", "-c", branch, "origin/main")
    git("add", ".test_durations")
    git("commit", "-m", f"CIV-33b: refresh .test_durations ({stamp})")
    git("push", "-u", "origin", branch)
    _gh(
        "pr", "create", "--base", "main", "--head", branch,
        "--title", f"CIV-33b: refresh .test_durations ({stamp})", "--body", note,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--pr", action="store_true", help="open a PR when the file changed")
    args = p.parse_args(argv)
    old = json.loads(DURATIONS.read_text(encoding="utf-8")) if DURATIONS.exists() else {}
    art, new = fetch_newest()
    merged = merge(old, new, lambda f: (ROOT / f).is_file())
    added, removed = len(merged.keys() - old.keys()), len(old.keys() - merged.keys())
    note = (
        f"run {art['workflow_run']['id']} ({art['created_at']}): "
        f"{added} entries added, {removed} removed, {len(merged)} in all"
    )
    text = render(merged)
    if DURATIONS.exists() and DURATIONS.read_text(encoding="utf-8") == text:
        print(f"unchanged. {note}")
        return 0
    DURATIONS.write_text(text, encoding="utf-8")
    print(note)
    if args.pr:
        _open_pr(dt.date.today().isoformat(), note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
