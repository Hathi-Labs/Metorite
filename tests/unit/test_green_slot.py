"""A slot ships the newest green commit (CIV-30c, task 3121).

``project-docs/specs/ci_velocity.md`` §8.7. On 2026-11-13 the 13:30 slot
shipped ``151fb81a5``. It left out 11 commits, and a newer batch was green.
Two faults caused it:

1. The mark of the batch on ``e9e89b8ce`` failed (run 43422421248). GitHub
   refused to let the GITHUB_TOKEN move the ref ``green/main``, because the
   move crossed a change of a workflow file.
2. GitHub started the 05:30 cron at 13:17 (run 43432451895). It picked its
   commit before the 13:30 slot, published first, and the step ``once`` then
   held the box's run (43434161129).

The runs that a push cancelled were the fold of CIV-4: each one never
started its ``test`` job, and the next batch tested the tip that held it.

What this file fences, in ``.github/workflows/deploy.yml``:

1. MARK. A green batch puts the commit status ``green/main`` on the tested
   commit before it moves the ref. A refusal of the ref move for a workflow
   file is a warning, and the step passes. A status that the API refuses 3
   times fails the step, and the ref step still runs.
2. PICK. A slot run takes the newest commit of main with the status, from
   the tip down to the ref. With none, it takes the ref. A status read that
   fails falls back to the ref and warns. Only a ``success`` status of
   ``github-actions[bot]`` counts.
3. WAIT. A slot run that starts in the 30 min before a slot waits until 2
   min after it, and then picks. A person's rerun does not wait.
4. HOLD. ``release-window`` holds a run that picked before the slot of its
   window, or that gives no pick time.
5. The replay of 2026-11-13, and a day with one merge each 10 min: a slot
   ships the commit of the last green batch that ended before the slot.

The steps run for real in bash, from a file, as GitHub runs them. Shell
functions stand in for ``git``, ``gh``, ``date`` and ``sleep``. The world of
one test is a folder: the first parents of main, the ref, and the statuses.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy.yml"
CONTEXT = "green/main"
REFUSAL = ("refusing to allow a GitHub App to create or update workflow "
           "`.github/workflows/deploy.yml` without `workflows` permission")
SLOT_IF = "${{ github.event_name == 'schedule' || inputs.nightly }}"
WAIT_STEP = "Wait for the slot"
PICK_STEP = "Pick the release commit"
STATUS_STEP = "Put the status green/main on the tested commit"
REF_STEP = "Move green/main forward to the tested commit"
CLOCK_STEP = "Read the clock"
#: The three slots of the crons, in minutes of the UTC day (CIV-21, D194).
SLOT_MINUTES = [5 * 60 + 30, 13 * 60 + 30, 21 * 60 + 30]


@pytest.fixture(scope="module")
def parsed() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step(parsed: dict, job: str, name: str) -> dict:
    found = [s for s in parsed["jobs"][job]["steps"] if s.get("name") == name]
    assert len(found) == 1, f"`{job}` needs exactly one step named {name!r}"
    return found[0]


def _names(job: dict) -> list[str]:
    return [s.get("name") or s.get("uses") for s in job["steps"]]


def _bash() -> str | None:
    if os.name == "nt":
        # Git's bash, not WSL's bash.exe, as in test_deploy_pipeline.py.
        git_bash = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/bin/bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


_BASH = _bash()
needs_bash = pytest.mark.skipif(_BASH is None, reason="no bash on this machine")


def _utc(text: str) -> int:
    return int(dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.UTC).timestamp())


def _sha(seed: str) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


# ── 1. The static wiring ─────────────────────────────────────────────────────


def test_the_mark_puts_the_status_first_and_the_ref_second(parsed: dict) -> None:
    job = parsed["jobs"]["mark-green"]
    assert _names(job) == [STATUS_STEP, "actions/checkout@v5", REF_STEP], (
        "the status needs no checkout, so it lands seconds after the batch"
    )
    status = job["steps"][0]
    assert "if" not in status, "the status step runs whenever the job runs"
    assert status["env"] == {"GH_TOKEN": "${{ github.token }}",
                             "TESTED": "${{ needs.test.outputs.tested }}", "CONTEXT": CONTEXT}
    run = status["run"]
    assert 'gh api -X POST "repos/${GITHUB_REPOSITORY}/statuses/${TESTED}"' in run
    assert "-f state=success" in run and '-f context="$CONTEXT"' in run
    assert "git " not in run and "DELETE" not in run
    # The ref is the second record. It runs when the status step failed.
    for step in job["steps"][1:]:
        assert step["if"] == "${{ !cancelled() }}", step.get("name") or step.get("uses")


def test_only_the_mark_writes_a_status(parsed: dict) -> None:
    jobs = parsed["jobs"]
    writers = sorted(n for n, j in jobs.items() if (j.get("permissions") or {}).get("statuses") == "write")
    assert writers == ["mark-green"], writers
    assert jobs["release-commit"]["permissions"] == {"contents": "read", "statuses": "read"}
    assert parsed["permissions"] == {"contents": "read"}, "no new right for the other jobs"


def test_the_pick_reads_the_context_that_the_mark_writes(parsed: dict) -> None:
    pick = _step(parsed, "release-commit", PICK_STEP)
    assert pick["env"]["CONTEXT"] == _step(parsed, "mark-green", STATUS_STEP)["env"]["CONTEXT"] == CONTEXT
    assert pick["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert int(pick["env"]["WALK"]) >= 50, "a day of merges fits in the walk"
    assert 0 < int(pick["env"]["WALK_SECONDS"]) <= 300
    run = pick["run"]
    assert 'gh api "repos/${GITHUB_REPOSITORY}/commits/${sha}/statuses?per_page=100"' in run
    assert 'git rev-list --first-parent --max-count="$WALK" "$tip"' in run
    assert 'echo "picked_at=${picked_at}" >> "$GITHUB_OUTPUT"' in run


def test_the_wait_matches_the_slots_and_the_windows(parsed: dict) -> None:
    job = parsed["jobs"]["release-commit"]
    assert _names(job)[0] == WAIT_STEP, "it waits before the checkout and the pick"
    wait = job["steps"][0]
    assert wait["if"] == SLOT_IF, "a push and a plain dispatch never wait"
    crons = [c["cron"] for c in (parsed.get("on") or parsed[True])["schedule"]]
    from_crons = sorted(int(c.split()[1]) * 60 + int(c.split()[0]) for c in crons)
    assert [int(m) for m in wait["env"]["SLOTS"].split()] == from_crons == SLOT_MINUTES
    lead = int(wait["env"]["LEAD_SECONDS"])
    windows = parsed["jobs"]["release-window"]["steps"][0]["env"]["WINDOWS"].split()
    opens = sorted(int(w.split("-")[0]) for w in windows)
    assert opens == sorted((m - lead // 60) % 1440 for m in SLOT_MINUTES), (
        "the wait covers the part of each window before its slot"
    )
    grace = int(wait["env"]["GRACE_SECONDS"])
    assert 0 < grace <= 300
    assert int(job["timeout-minutes"]) >= (lead + grace) // 60 + 5, "the job must outlive its wait"


def test_the_window_reads_the_pick_time(parsed: dict) -> None:
    job = parsed["jobs"]["release-window"]
    assert "release-commit" in job["needs"]
    assert job["steps"][0]["env"]["PICKED_AT"] == "${{ needs.release-commit.outputs.picked_at }}"


# ── The fake world ───────────────────────────────────────────────────────────
#
# $W/main     the first parents of main, newest first
# $W/wf       the commits that change a workflow file
# $W/ref      the head of the ref green/main (no file: no ref)
# $W/st/SHA   the statuses of SHA, one JSON object a line, newest first
# $W/calls    each git and gh call
# $W/sleeps   each sleep, in seconds
# REFUSE=workflow refuses a ref move across a commit of $W/wf, as GitHub
# refused the GITHUB_TOKEN on 2026-11-13. GH_FAIL=post|read|all fails gh.

_FAKE = r"""
_idx() { [ -n "$1" ] && grep -n -x -F "$1" "$W/main" | head -n 1 | cut -d: -f1; }
_tip() { head -n 1 "$W/main"; }
git() {
  printf 'git %s\n' "$*" >> "$W/calls"
  case "$1" in
    fetch) return 0 ;;
    rev-parse)
      case "$2" in refs/remotes/origin/main) _tip ;; *) printf '%s\n' "$2" ;; esac ;;
    ls-remote)
      if [ -s "$W/ref" ]; then printf '%s\trefs/heads/green/main\n' "$(cat "$W/ref")"; return 0; fi
      return 2 ;;
    merge-base)
      local a b ia ib
      a="$3"; b="$4"
      [ "$a" = refs/remotes/origin/main ] && a="$(_tip)"
      [ "$b" = refs/remotes/origin/main ] && b="$(_tip)"
      ia="$(_idx "$a")"; ib="$(_idx "$b")"
      if [ -n "$ia" ] && [ -n "$ib" ] && [ "$ia" -ge "$ib" ]; then return 0; fi
      return 1 ;;
    rev-list)
      local n i
      n="${3#--max-count=}"; i="$(_idx "$4")"
      [ -n "$i" ] || return 128
      tail -n "+$i" "$W/main" | head -n "$n" ;;
    log)
      local r ia ib
      r="${*: -1}"; ia="$(_idx "${r%%..*}")"; ib="$(_idx "${r##*..}")"
      if [ "$ia" -gt "$ib" ]; then sed -n "${ib},$(( ia - 1 ))p" "$W/main" | sed 's/$/ a merge (#1)/'; fi
      return 0 ;;
    push)
      local sha cur ic it
      sha="${3%%:*}"
      cur="$(cat "$W/ref" 2>/dev/null || true)"
      if [ "${REFUSE:-}" = workflow ] && [ -s "$W/wf" ]; then
        it="$(_idx "$sha")"; ic="$(_idx "$cur")"
        [ -n "$ic" ] || ic=$(( $(wc -l < "$W/main") + 1 ))
        if sed -n "${it},$(( ic - 1 ))p" "$W/main" | grep -q -x -F -f "$W/wf"; then
          echo "To https://github.com/Hathi-Labs/Metorite" >&2
          echo " ! [remote rejected]     $sha -> green/main ($REFUSAL)" >&2
          return 1
        fi
      fi
      printf '%s\n' "$sha" > "$W/ref" ;;
    *) return 0 ;;
  esac
}
gh() {
  printf 'gh %s\n' "$*" >> "$W/calls"
  if [ "${GH_FAIL:-}" = all ]; then echo '{"message":"Server Error"}'; return 1; fi
  if [ "$2" = -X ] && [ "$3" = POST ]; then
    if [ "${GH_FAIL:-}" = post ]; then echo '{"message":"Server Error"}'; return 1; fi
    local sha state="" ctx=""
    sha="${4##*/}"
    shift 4
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -f)
          case "$2" in state=*) state="${2#state=}" ;; context=*) ctx="${2#context=}" ;; esac
          shift 2 ;;
        *) shift ;;
      esac
    done
    mkdir -p "$W/st"
    { printf '{"context": "%s", "state": "%s", "creator": {"login": "github-actions[bot]"}}\n' "$ctx" "$state"
      cat "$W/st/$sha" 2>/dev/null || true; } > "$W/st/$sha.new"
    mv "$W/st/$sha.new" "$W/st/$sha"
    echo '{}'
    return 0
  fi
  if [ "${GH_FAIL:-}" = read ]; then echo '{"message":"Server Error"}'; return 1; fi
  local sha="${2#*/commits/}"
  sha="${sha%%/*}"
  printf '['
  if [ -f "$W/st/$sha" ]; then paste -s -d, "$W/st/$sha"; fi
  printf ']\n'
}
date() { command date -u -d "@${NOW_S}" "${@: -1}"; }
sleep() { printf '%s\n' "$1" >> "$W/sleeps"; }
python3() { "$PYTHON" "$@"; }
"""


@dataclass
class Result:
    code: int
    out: str
    outputs: dict[str, str]
    summary: str


@dataclass
class World:
    """Main, the ref and the statuses, as GitHub holds them at one moment."""

    root: Path
    commits: list[tuple[int, str]] = field(default_factory=list)  # (merge time, sha), oldest first
    wf: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        (self.root / "st").mkdir(parents=True, exist_ok=True)

    def at(self, now: int) -> None:
        """Write main as it stood at `now`: the merges up to that moment."""
        shas = [s for t, s in self.commits if t <= now]
        (self.root / "main").write_text("".join(f"{s}\n" for s in reversed(shas)), encoding="utf-8", newline="\n")
        (self.root / "wf").write_text("".join(f"{s}\n" for s in sorted(self.wf)), encoding="utf-8", newline="\n")

    @property
    def ref(self) -> str:
        path = self.root / "ref"
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    @ref.setter
    def ref(self, sha: str) -> None:
        (self.root / "ref").write_text(f"{sha}\n" if sha else "", encoding="utf-8", newline="\n")

    def status(self, sha: str, state: str = "success", creator: str = "github-actions[bot]",
               context: str = CONTEXT) -> None:
        path = self.root / "st" / sha
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        row = json.dumps({"context": context, "state": state, "creator": {"login": creator}})
        path.write_text(row + "\n" + old, encoding="utf-8", newline="\n")

    def marked(self, sha: str) -> bool:
        path = self.root / "st" / sha
        return path.exists() and f'"context": "{CONTEXT}", "state": "success"' in path.read_text(encoding="utf-8")

    def calls(self) -> list[str]:
        path = self.root / "calls"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def sleeps(self) -> list[int]:
        path = self.root / "sleeps"
        return [int(x) for x in path.read_text(encoding="utf-8").split()] if path.exists() else []

    def reset_logs(self) -> None:
        for name in ("calls", "sleeps"):
            (self.root / name).unlink(missing_ok=True)


def _plain(step: dict) -> dict[str, str]:
    return {k: str(v) for k, v in (step.get("env") or {}).items() if "${{" not in str(v)}


def _run(world: World, step: dict, now: int, env: dict[str, str] | None = None) -> Result:
    world.at(now)
    tmp = world.root / "run"
    tmp.mkdir(exist_ok=True)
    out, summary, script = tmp / "output", tmp / "summary", tmp / "step.sh"
    for path in (out, summary):
        path.unlink(missing_ok=True)
    script.write_text(_FAKE + step["run"], encoding="utf-8", newline="\n")
    full = {
        **os.environ, **_plain(step),
        "W": world.root.as_posix(), "NOW_S": str(now), "PYTHON": Path(sys.executable).as_posix(),
        "REFUSAL": REFUSAL, "GITHUB_REPOSITORY": "Hathi-Labs/Metorite",
        "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": "7", "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_EVENT_NAME": "schedule", "GITHUB_SHA": world.commits[-1][1] if world.commits else "",
        "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary), **(env or {}),
    }
    # The flags GitHub gives a `run:` step: bash --noprofile --norc -eo pipefail.
    done = subprocess.run([_BASH, "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()],
                          env=full, capture_output=True, text=True, timeout=120, cwd=tmp)
    outputs: dict[str, str] = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            outputs[key] = value
    text = summary.read_text(encoding="utf-8") if summary.exists() else ""
    return Result(done.returncode, done.stdout + done.stderr, outputs, text)


def _mark(parsed: dict, world: World, tested: str, now: int, **env: str) -> tuple[Result, Result]:
    """The two steps of `mark-green`. The ref step runs after a failed
    status step too (`!cancelled()`)."""
    base = {"TESTED": tested, "GITHUB_EVENT_NAME": "push", **env}
    status = _run(world, _step(parsed, "mark-green", STATUS_STEP), now, base)
    ref = _run(world, _step(parsed, "mark-green", REF_STEP), now, base)
    return status, ref


def _wait(parsed: dict, world: World, now: int, attempt: str = "1") -> int:
    """Run the wait step. Return the seconds it slept (0: no sleep)."""
    world.reset_logs()
    got = _run(world, _step(parsed, "release-commit", WAIT_STEP), now, {"GITHUB_RUN_ATTEMPT": attempt})
    assert got.code == 0, got.out
    sleeps = world.sleeps()
    assert len(sleeps) <= 1, sleeps
    return sleeps[0] if sleeps else 0


def _pick(parsed: dict, world: World, now: int, **env: str) -> Result:
    world.reset_logs()
    return _run(world, _step(parsed, "release-commit", PICK_STEP), now, {"SLOT": "true", **env})


def _slot_run(parsed: dict, world: World, start: int) -> tuple[Result, int]:
    """A slot run from its start: the wait, then the pick at the new time."""
    slept = _wait(parsed, world, start)
    picked = start + slept
    return _pick(parsed, world, picked), picked


def _clock(parsed: dict, world: World, now: int, picked: str, attempt: str = "1") -> Result:
    return _run(world, _step(parsed, "release-window", CLOCK_STEP), now,
                {"PICKED_AT": picked, "GITHUB_RUN_ATTEMPT": attempt})


# ── 2. MARK ──────────────────────────────────────────────────────────────────

_A, _B, _C, _D = (_sha(x) for x in "abcd")  # main, oldest first


def _line(tmp_path: Path) -> World:
    world = World(tmp_path / "w")
    world.commits = [(100 + i, s) for i, s in enumerate([_A, _B, _C, _D])]
    return world


@needs_bash
def test_a_green_batch_puts_the_status_on_its_commit(parsed: dict, tmp_path: Path) -> None:
    world = _line(tmp_path)
    world.ref = _A
    status, ref = _mark(parsed, world, _C, 1000)
    assert status.code == 0 and ref.code == 0, status.out + ref.out
    assert world.marked(_C)
    assert f"STATUS `{CONTEXT}` is on `{_C[:12]}`" in status.summary
    assert world.ref == _C, "with no refusal, the ref moves as before"


@needs_bash
def test_a_refused_ref_move_warns_and_passes(parsed: dict, tmp_path: Path) -> None:
    """Done-when 2. GitHub refuses the ref move across a workflow change.
    The step passes, says why, and the status holds the mark."""
    world = _line(tmp_path)
    world.ref = _A
    world.wf = {_B}
    status, ref = _mark(parsed, world, _C, 1000, REFUSE="workflow")
    assert status.code == 0, status.out
    assert ref.code == 0, ref.out
    assert "::warning title=green/main ref stays::" in ref.out
    assert REFUSAL in ref.out, "the log keeps GitHub's own words"
    assert world.ref == _A and world.marked(_C)
    assert "green/main ref STAYS" in ref.summary
    pushes = [c for c in world.calls() if c.startswith("git push")]
    assert len(pushes) == 1, "a refusal for a workflow file is not a race, so no retry"


@needs_bash
def test_a_status_that_the_api_refuses_fails_the_step_and_the_ref_still_moves(
        parsed: dict, tmp_path: Path) -> None:
    world = _line(tmp_path)
    world.ref = _A
    status, ref = _mark(parsed, world, _C, 1000, GH_FAIL="post")
    assert status.code == 1, status.out
    assert "::error title=No green/main status::" in status.out
    assert sum(c.startswith("gh api -X POST") for c in world.calls()) == 3
    assert ref.code == 0 and world.ref == _C, "the second record still lands"


@needs_bash
@pytest.mark.parametrize("tested", ["", "abc123", "Z" * 40])
def test_a_bad_tested_commit_gets_no_status(parsed: dict, tmp_path: Path, tested: str) -> None:
    world = _line(tmp_path)
    status = _run(world, _step(parsed, "mark-green", STATUS_STEP), 1000, {"TESTED": tested})
    assert status.code == 1, status.out
    assert not any(c.startswith("gh") for c in world.calls())


# ── 3. PICK ──────────────────────────────────────────────────────────────────


@needs_bash
def test_a_status_newer_than_the_ref_wins(parsed: dict, tmp_path: Path) -> None:
    world = _line(tmp_path)
    world.ref = _A
    world.status(_C)
    got = _pick(parsed, world, 5000)
    assert got.code == 0, got.out
    assert got.outputs["sha"] == _C
    assert got.outputs["picked_at"] == "5000"
    assert "with the status `green/main`" in got.summary
    assert f"The ref `green/main` is at `{_A[:12]}`, which is older" in got.summary
    assert "It leaves out 1 commit(s)" in got.summary
    reads = [c for c in world.calls() if c.startswith("gh api repos/")]
    assert [r.split("/commits/")[1][:40] for r in reads] == [_D, _C], "it stops at the first status"


@needs_bash
def test_with_no_status_the_ref_is_the_pick_and_the_walk_stops_at_it(parsed: dict, tmp_path: Path) -> None:
    world = _line(tmp_path)
    world.ref = _B
    got = _pick(parsed, world, 5000)
    assert got.code == 0, got.out
    assert got.outputs["sha"] == _B
    assert "the head of `green/main`" in got.summary
    reads = [c for c in world.calls() if c.startswith("gh api repos/")]
    assert len(reads) == 2, "it reads the tip and the next commit, and stops at the ref"


@needs_bash
@pytest.mark.parametrize("fault", ["read", "all"])
def test_a_status_read_that_fails_falls_back_to_the_ref(parsed: dict, tmp_path: Path, fault: str) -> None:
    world = _line(tmp_path)
    world.ref = _B
    world.status(_D)
    got = _pick(parsed, world, 5000, GH_FAIL=fault)
    assert got.code == 0, got.out
    assert got.outputs["sha"] == _B, "a green commit, maybe not the newest"
    assert "::warning title=green/main status unread::" in got.out


@needs_bash
def test_with_no_ref_a_status_is_the_pick(parsed: dict, tmp_path: Path) -> None:
    world = _line(tmp_path)
    world.status(_B)
    got = _pick(parsed, world, 5000)
    assert got.code == 0, got.out
    assert got.outputs["sha"] == _B
    assert "The ref `green/main` does not exist" in got.summary


@needs_bash
def test_with_no_ref_and_no_status_a_slot_falls_back_as_before(parsed: dict, tmp_path: Path) -> None:
    world = _line(tmp_path)
    got = _pick(parsed, world, 5000)
    assert got.code == 0, got.out
    assert got.outputs["sha"] == _D, "GITHUB_SHA, as before CIV-30"
    assert "::warning title=No green/main yet::" in got.out


@needs_bash
def test_with_no_ref_a_failed_status_read_holds_the_release(parsed: dict, tmp_path: Path) -> None:
    world = _line(tmp_path)
    got = _pick(parsed, world, 5000, GH_FAIL="read")
    assert got.code == 1, got.out
    assert "sha" not in got.outputs, "no release commit, so no job of the release runs"
    assert "::error title=green/main unknown::" in got.out


@needs_bash
def test_a_failure_status_and_a_foreign_writer_do_not_count(parsed: dict, tmp_path: Path) -> None:
    world = _line(tmp_path)
    world.ref = _A
    world.status(_D)
    world.status(_D, state="failure")  # the newest state of the context wins
    world.status(_C, creator="someone")
    world.status(_B, context="other/check")
    got = _pick(parsed, world, 5000)
    assert got.code == 0, got.out
    assert got.outputs["sha"] == _A
    assert "::warning title=green/main status not counted::" in got.out and "someone" in got.out


# ── 4. WAIT ──────────────────────────────────────────────────────────────────


@needs_bash
@pytest.mark.parametrize(("clock", "attempt", "slept"), [
    # 2026-11-13: the 05:30 cron started at 13:17:51. It waits for 13:32:00.
    ("2026-11-13T13:17:51", "1", 849),
    # The box's run starts a few seconds after the slot.
    ("2026-11-13T13:30:06", "1", 114),
    ("2026-11-13T13:31:59", "1", 1),
    ("2026-11-13T13:32:00", "1", 0),
    ("2026-11-13T13:00:00", "1", 1920),
    ("2026-11-13T12:59:59", "1", 0),
    ("2026-11-13T05:29:00", "1", 180),
    ("2026-11-13T21:10:00", "1", 1320),
    ("2026-11-13T21:31:00", "1", 60),
    ("2026-11-14T00:30:00", "1", 0),
    ("2026-11-13T09:00:00", "1", 0),
    # A person's rerun picks at once.
    ("2026-11-13T13:17:51", "2", 0),
])
def test_a_slot_run_waits_for_its_slot(parsed: dict, tmp_path: Path, clock: str, attempt: str,
                                       slept: int) -> None:
    world = _line(tmp_path)
    assert _wait(parsed, world, _utc(clock), attempt) == slept


# ── 5. HOLD ──────────────────────────────────────────────────────────────────


@needs_bash
@pytest.mark.parametrize(("now", "picked", "attempt", "is_open"), [
    ("2026-11-13T14:22:00", "2026-11-13T13:32:00", "1", True),
    ("2026-11-13T14:22:00", "2026-11-13T13:30:00", "1", True),
    ("2026-11-13T14:22:00", "2026-11-13T13:29:59", "1", False),
    # A cron that GitHub started in the gap, and whose tests ended in the window.
    ("2026-11-13T13:10:00", "2026-11-13T12:20:00", "1", False),
    ("2026-11-13T21:20:00", "2026-11-13T20:40:00", "1", False),
    # The window of 21:30 spans midnight.
    ("2026-11-14T00:30:00", "2026-11-13T21:32:00", "1", True),
    ("2026-11-14T00:30:00", "2026-11-13T20:59:00", "1", False),
    ("2026-11-13T06:20:00", "2026-11-13T05:32:00", "1", True),
    # A person's rerun is not held.
    ("2026-11-13T13:10:00", "2026-11-13T12:20:00", "2", True),
])
def test_a_run_that_picked_before_its_slot_does_not_publish(
        parsed: dict, tmp_path: Path, now: str, picked: str, attempt: str, is_open: bool) -> None:
    world = _line(tmp_path)
    got = _clock(parsed, world, _utc(now), str(_utc(picked)), attempt)
    assert got.code == 0, got.out
    assert got.outputs["open"] == ("true" if is_open else "false")
    if not is_open:
        assert "::warning title=Release waits for the slot run::" in got.out
        assert f"before the {_slot_label(_utc(now))} UTC slot" in got.out
        assert "Release WAITS" in got.summary


def _slot_label(now: int) -> str:
    minute = now % 86400 // 60
    for m in SLOT_MINUTES:
        if -30 <= (minute - m + 720) % 1440 - 720 < 270:
            return f"{m // 60:02d}:{m % 60:02d}"
    raise AssertionError("no window")


@needs_bash
@pytest.mark.parametrize("picked", ["", "soon", "-5"])
def test_a_run_with_no_pick_time_does_not_publish(parsed: dict, tmp_path: Path, picked: str) -> None:
    world = _line(tmp_path)
    got = _clock(parsed, world, _utc("2026-11-13T14:22:00"), picked)
    assert got.code == 0, got.out
    assert got.outputs["open"] == "false"
    assert "no pick time" in got.out


@needs_bash
def test_outside_a_window_the_clock_waits_as_before(parsed: dict, tmp_path: Path) -> None:
    world = _line(tmp_path)
    got = _clock(parsed, world, _utc("2026-11-13T18:30:00"), str(_utc("2026-11-13T17:40:00")))
    assert got.outputs["open"] == "false"
    assert "::warning title=Release waits for the next slot::" in got.out


# ── 6. The replay of 2026-11-13 ──────────────────────────────────────────────
#
# The first parents of main from 11:23 to 13:23 UTC, as `git log` gives
# them. The commits marked True changed .github/workflows/deploy.yml.
_NOV13 = [
    ("151fb81a52aab62e7bad4cadf55068d6373d3f12", "11:23:51", False),
    ("1790c45be291a6b1d8d3853d98cf1f4ba8eab8ea", "11:32:57", False),
    ("35dafcedb5c33ebb9e1ba3a907c537cae2d3850f", "11:41:43", False),
    ("a4e82aecc65ba4dab280a5ede5b6cb1b6df199a5", "11:49:59", False),
    ("ac39b7f896e4f61dddf1a2288e21244c7976c0ef", "11:58:25", False),
    ("e9e89b8cef78dfa19b534feb3be30aba7c0b3fe5", "12:06:40", True),
    ("81986e866e6c5ec669bc6dda0796086bc856b1be", "12:15:53", False),
    ("f499e3ddfd71f8d67923019843cd6a4281cd327e", "12:24:42", True),
    ("bba5ebaca41499806f745ecce7b79650ab54cb4f", "12:32:51", False),
    ("00e1f41f5f715526a8cec882e8360a72ebde213c", "12:41:35", False),
    ("a6529bf83b081262efafc41c7921e0d61e100250", "12:49:59", False),
    ("9954ee094587183ce20431f13813782ca87b4f53", "13:08:07", True),
    ("c07b862774d235618db9dad82709430e78bf7397", "13:23:01", False),
]
_E9E = "e9e89b8cef78dfa19b534feb3be30aba7c0b3fe5"
_151 = "151fb81a52aab62e7bad4cadf55068d6373d3f12"


def _nov13(tmp_path: Path) -> World:
    world = World(tmp_path / "w")
    world.commits = [(_utc(f"2026-11-13T{t}"), s) for s, t, _ in _NOV13]
    world.wf = {s for s, _, wf in _NOV13 if wf}
    # The batch on 151fb81a5 moved the ref at 12:08:58 (before this change,
    # so no status).
    world.ref = _151
    return world


@needs_bash
def test_the_replay_of_2026_11_13_ships_the_batch_that_ended_at_12_52(parsed: dict, tmp_path: Path) -> None:
    """The batch on e9e89b8ce ended at 12:52:42. Its ref move was refused
    (run 43422421248). The cron of 13:17:51 and the box's run of 13:30:02
    both shipped 151fb81a5 and left out 11 and 12 commits. Now both ship
    e9e89b8ce, the last batch that ended before the slot."""
    world = _nov13(tmp_path)
    status, ref = _mark(parsed, world, _E9E, _utc("2026-11-13T12:52:44"), REFUSE="workflow")
    assert status.code == 0 and ref.code == 0, status.out + ref.out
    assert world.ref == _151, "GitHub refused the ref, as in run 43422421248"
    assert world.marked(_E9E), "the status landed"
    for start in ("2026-11-13T13:17:51", "2026-11-13T13:30:06"):
        got, picked = _slot_run(parsed, world, _utc(start))
        assert got.code == 0, got.out
        assert picked == _utc("2026-11-13T13:32:00"), "no pick before the slot of the box"
        assert got.outputs["sha"] == _E9E, start
        assert "It leaves out 7 commit(s)" in got.summary
        # Its release-window, after about 50 min of tests.
        window = _clock(parsed, world, picked + 50 * 60, got.outputs["picked_at"])
        assert window.outputs["open"] == "true", window.out


@needs_bash
def test_a_cron_from_the_gap_does_not_ship_its_early_pick(parsed: dict, tmp_path: Path) -> None:
    """A cron that GitHub starts at 12:20, in the gap, picks at once. Its
    tests end at 13:10, inside the window. It no longer publishes, so the
    step `once` cannot hold the box's run for it."""
    world = _nov13(tmp_path)
    got, picked = _slot_run(parsed, world, _utc("2026-11-13T12:20:00"))
    assert picked == _utc("2026-11-13T12:20:00"), "no wait: the window is not open"
    assert got.outputs["sha"] == _151
    window = _clock(parsed, world, _utc("2026-11-13T13:10:00"), got.outputs["picked_at"])
    assert window.outputs["open"] == "false", window.out


# ── 7. A day with one merge each 10 min ──────────────────────────────────────


@dataclass
class Batch:
    start: int
    end: int
    tested: str
    green: bool


def _batches(world: World, until: int) -> list[Batch]:
    """CIV-4: one batch at a time. A batch tests the tip of main when it
    starts. The next starts when it ends, if main moved. The lengths repeat
    from the batches of 2026-11-13 (42 to 46 min)."""
    lengths = [42 * 60 + 15, 44 * 60 + 5, 45 * 60 + 8, 44 * 60 + 45, 46 * 60]
    out: list[Batch] = []
    t = world.commits[0][0] + 60
    last = ""
    while t < until:
        tip = [s for c, s in world.commits if c <= t][-1]
        if tip == last:
            later = [c for c, _ in world.commits if c > t]
            if not later:
                break
            t = later[0] + 30
            continue
        end = t + lengths[len(out) % len(lengths)]
        # One red batch in the morning: a red tip holds no mark.
        out.append(Batch(t, end, tip, green=len(out) != 6))
        last = tip
        t = end + 2
    return out


@needs_bash
def test_a_day_of_merges_each_10_min_ships_the_last_green_batch(parsed: dict, tmp_path: Path) -> None:
    """Done-when 1 and 3. Merges each 10 min from 03:00 to 22:00. One in
    three changes a workflow file, so GitHub refuses most ref moves. At each
    slot, the box's run (3 s after the slot) and a late cron (13 min before)
    each ship the tested commit of the last green batch that ended before
    the slot, or a newer green one."""
    day = _utc("2026-11-13T00:00:00")
    world = World(tmp_path / "w")
    world.commits = [(day + 3 * 3600 + 600 * i, _sha(f"merge {i}")) for i in range(19 * 6 + 1)]
    world.wf = {s for i, (_, s) in enumerate(world.commits) if i % 3 == 1}
    batches = _batches(world, day + 22 * 3600)
    slots = [day + m * 60 for m in SLOT_MINUTES]
    events: list[tuple[int, int, object]] = [(b.end + 2, 0, b) for b in batches if b.green]
    # A slot run picks 2 min after its slot (the grace), so a mark that
    # lands before then counts.
    events += [(s + 120, 1, s) for s in slots]
    refused = 0
    order = [s for _, s in world.commits]
    for when, kind, what in sorted(events, key=lambda e: (e[0], e[1])):
        if kind == 0:
            assert isinstance(what, Batch)
            status, ref = _mark(parsed, world, what.tested, when, REFUSE="workflow")
            assert status.code == 0 and ref.code == 0, status.out + ref.out
            refused += "::warning title=green/main ref stays::" in ref.out
            continue
        assert isinstance(what, int)
        slot = what
        before = [b for b in batches if b.green and b.end < slot]
        want = before[-1].tested
        for start in (slot + 3, slot - 13 * 60):
            got, picked = _slot_run(parsed, world, start)
            assert got.code == 0, got.out
            assert picked >= slot, "a slot run never picks before its slot"
            sha = got.outputs["sha"]
            assert sha in {b.tested for b in batches if b.green}, "only a green batch ships"
            assert order.index(sha) >= order.index(want), (
                f"slot {slot}: shipped {sha[:12]}, older than {want[:12]}"
            )
            window = _clock(parsed, world, picked + 50 * 60, got.outputs["picked_at"])
            assert window.outputs["open"] == "true", window.out
    assert refused >= 3, "the day must test the refusal, or it proves nothing about done-when 2"
