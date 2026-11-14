"""A release slot ships green (CIV-30, D218 point 3).

``project-docs/specs/ci_velocity.md`` §8.1 point 3: "Each release slot ships
the newest commit that passed the full suite. A red tip of main holds no
release. The R8 gate still runs."

What this file fences, in ``.github/workflows/deploy.yml``:

1. MARK. A green push batch moves ``green/main`` to the commit that its
   ``test`` job tested. It moves forward only: never backwards, never to a
   commit that does not contain the current pointer, never off main. A
   folded, cancelled, failed or skipped ``test`` never moves it. It never
   pushes to ``main``.
2. SHIP. A slot run (a schedule run, or a dispatch with ``nightly=true``)
   resolves ONE release commit, the head of ``green/main``, in the job
   ``release-commit``. Every job that builds, tests, gates or deploys the
   release checks out that commit and names it. A run that tests one commit
   and deploys another is the worst defect this pipeline can have.
3. FALLBACK. With no ``green/main``, a slot ships GITHUB_SHA, as before, and
   the run summary says so. Any other failure to read ``green/main`` holds
   the release.
4. A red tip holds no release. The summary lists the commits that the
   release leaves out.
5. The R8 guard reads its lists from the release commit's own
   ``tests/fences.json`` (CIV-33c-2), not from the tip's copy that GitHub
   runs. A suite that lands after ``green/main`` does not hold the slot. A
   release commit whose own list names a missing suite still fails. A list
   that the guard cannot read fails closed. A release commit from before the
   move has no ``r8`` key. Then the guard reads the lists in that commit's
   own ``deploy.yml`` (the bootstrap).

The steps run for real in bash. A shell function stands in for ``git``, so
the step's own text runs unchanged, as in ``test_deploy_pipeline.py``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.test_daily_release import _eval
from tests.unit.test_deploy_pipeline import _gh_eval

yaml = pytest.importorskip("yaml")

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy.yml"
_SCRIPTS = WORKFLOW.parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ci_unit_guard as guard  # noqa: E402  scripts/ci_unit_guard.py

#: The one expression that names the release commit.
RESOLVED = "${{ needs.release-commit.outputs.sha }}"
#: `test` keeps the default checkout on a push, where the batch moves to the tip.
TEST_REF = "${{ github.event_name != 'push' && needs.release-commit.outputs.sha || '' }}"
#: Every job that builds, tests, gates or deploys the release, and checks out.
RELEASE_JOBS = {
    "lint", "test", "r8-suite", "r8-gate", "publish-release",
    "deploy", "pull-delivery", "chat-smoke",
}
#: The jobs of a push only. Neither builds nor ships a release.
PUSH_ONLY = {"publish-staging", "mark-green"}
#: Release jobs that check out nothing. `smoke` names the commit in its log.
NO_CHECKOUT = {"release-hold", "release-window", "smoke"}
RESOLVER = "release-commit"
MARK = "mark-green"
MARK_STEP = "Move green/main forward to the tested commit"
PICK_STEP = "Pick the release commit"
PUBLISH_STEP = "Fast-forward release to the release commit"


@pytest.fixture(scope="module")
def parsed() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _step(parsed: dict, job: str, name: str) -> dict:
    found = [s for s in parsed["jobs"][job]["steps"] if s.get("name") == name]
    assert len(found) == 1, f"`{job}` needs exactly one step named {name!r}"
    return found[0]


def _checkouts(job: dict) -> list[dict]:
    return [s for s in job.get("steps", []) if str(s.get("uses", "")).startswith("actions/checkout@")]


def _bash() -> str | None:
    if os.name == "nt":
        # Git's bash, not WSL's bash.exe, as in test_deploy_pipeline.py.
        git_bash = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/bin/bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


_BASH = _bash()
needs_bash = pytest.mark.skipif(_BASH is None, reason="no bash on this machine")

_MAIN, _NEWER, _TESTED, _OLD, _OFF, _GHSHA = (c * 40 for c in "fedcba")

# A fake `git`. Each call goes to $GIT_LOG. `ls-remote` answers from the
# next line of $GREEN_SEQ: a sha, ABSENT (exit 2) or ERR (exit 128). EMPTY
# gives exit 0 and no line. OTHERREF gives exit 0 and a line for another ref.
# `merge-base --is-ancestor A B` passes when A is B, or when "A:B" is in $ANC.
# `push` fails while $PUSH_FAIL is above 0. `log` prints $LEFT_COUNT lines.
_FAKE_GIT = r"""
sleep() { :; }
_ref() {
  case "$1" in
    refs/remotes/origin/main|origin/main) printf '%s' "$MAIN" ;;
    refs/remotes/origin/release|origin/release) printf '%s' "$RELEASE" ;;
    *) printf '%s' "$1" ;;
  esac
}
git() {
  printf '%s\n' "git $*" >> "$GIT_LOG"
  case "$1" in
    fetch) return 0 ;;
    rev-parse) _ref "$2"; echo ;;
    ls-remote)
      local n line
      n=$(( $(cat "$SEQ_POS" 2>/dev/null || echo 0) + 1 ))
      echo "$n" > "$SEQ_POS"
      line="$(sed -n "${n}p" "$GREEN_SEQ")"
      [ -n "$line" ] || line="$(tail -n 1 "$GREEN_SEQ")"
      case "$line" in
        ABSENT) return 2 ;;
        ERR) return 128 ;;
        EMPTY) return 0 ;;
        OTHERREF) printf '%s\t%s\n' "$MAIN" "${@: -1}-old" ;;
        *) printf '%s\t%s\n' "$line" "${@: -1}" ;;
      esac ;;
    merge-base)
      local a b
      a="$(_ref "$3")"; b="$(_ref "$4")"
      [ "$a" = "$b" ] && return 0
      case " $ANC " in *" $a:$b "*) return 0 ;; esac
      return 1 ;;
    push)
      if [ "${PUSH_FAIL:-0}" -gt 0 ]; then
        PUSH_FAIL=$(( PUSH_FAIL - 1 ))
        return 1
      fi
      return 0 ;;
    log)
      local i
      for i in $(seq 1 "${LEFT_COUNT:-0}"); do printf '%07d merge number %s (#%s)\n' "$i" "$i" "$i"; done ;;
    *) return 0 ;;
  esac
}
"""


def _run(script: str, tmp_path: Path, *, env: dict[str, str], green: list[str]) -> tuple[int, str, str, str, list[str]]:
    seq = tmp_path / "green_seq"
    seq.write_text("\n".join(green) + "\n", encoding="utf-8", newline="\n")
    log, out, summary = tmp_path / "git.log", tmp_path / "output", tmp_path / "summary"
    full = {
        **os.environ, "MAIN": _MAIN, "RELEASE": "", "ANC": "", "GITHUB_SHA": _GHSHA,
        "GITHUB_EVENT_NAME": "schedule", "GIT_LOG": log.as_posix(), "GREEN_SEQ": seq.as_posix(),
        "SEQ_POS": (tmp_path / "seq_pos").as_posix(), "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary), **env,
    }
    # The flags GitHub gives a `run:` step: bash --noprofile --norc -eo pipefail.
    # From a file, as GitHub runs it: Windows cuts a `-c` argument near 8 KiB,
    # and the pick step is longer than that since CIV-30c.
    step = tmp_path / "step.sh"
    step.write_text(_FAKE_GIT + script, encoding="utf-8", newline="\n")
    done = subprocess.run(
        [_BASH, "--noprofile", "--norc", "-eo", "pipefail", step.as_posix()],
        env=full, capture_output=True, text=True, timeout=120, cwd=tmp_path,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    output = out.read_text(encoding="utf-8") if out.exists() else ""
    text = summary.read_text(encoding="utf-8") if summary.exists() else ""
    return done.returncode, done.stdout + done.stderr, output, text, calls


def _pushes(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("git push")]


# ── 1. MARK: a green push batch moves green/main forward ─────────────────────


def test_the_mark_job_exists_with_the_least_permission(parsed: dict) -> None:
    jobs = parsed["jobs"]
    job = jobs[MARK]
    assert "test" in _needs(job) and "lint" in _needs(job)
    # CIV-30c: `statuses: write` for the status `green/main`, and nothing else.
    assert job.get("permissions") == {"contents": "write", "statuses": "write"}
    assert "concurrency" not in job, "a superseded mark must not cancel a newer one"
    writers = sorted(n for n, j in jobs.items() if (j.get("permissions") or {}).get("contents") == "write")
    assert writers == ["mark-green", "publish-release", "publish-staging"], writers
    assert parsed.get("permissions") == {"contents": "read"}


def test_the_mark_moves_the_commit_that_test_tested(parsed: dict) -> None:
    """A batch tests the tip, which can hold merges after its own push."""
    assert parsed["jobs"]["test"]["outputs"]["tested"] == (
        "${{ steps.tip.outputs.sha || needs.release-commit.outputs.sha }}"
    )
    step = _step(parsed, MARK, MARK_STEP)
    assert step["env"]["TESTED"] == "${{ needs.test.outputs.tested }}"


def test_the_mark_pushes_green_main_only_and_never_by_force(parsed: dict) -> None:
    run = _step(parsed, MARK, MARK_STEP)["run"]
    pushes = [ln.strip() for ln in run.splitlines() if re.search(r"\bgit\s+push\b", ln)]
    # CIV-30c: stderr goes to a file, so the step can read a refusal.
    assert pushes == ['if git push origin "${TESTED}:refs/heads/green/main" 2> "$tmp/push.err"; then'], pushes
    assert "--force" not in run and " -f " not in run and '"+' not in run
    assert "refs/heads/main\"" not in run.replace("+refs/heads/main:", ""), "never push main"


@pytest.mark.parametrize(("event", "ref", "lint", "test", "moves"), [
    ("push", "refs/heads/main", "success", "success", True),
    # A folded batch, a hung one (90 min), a red one and a skipped one.
    ("push", "refs/heads/main", "success", "cancelled", False),
    ("push", "refs/heads/main", "success", "failure", False),
    ("push", "refs/heads/main", "success", "skipped", False),
    ("push", "refs/heads/main", "failure", "success", False),
    ("push", "refs/heads/other", "success", "success", False),
    # A release tests green/main again. It never marks.
    ("schedule", "refs/heads/main", "success", "success", False),
    ("workflow_dispatch", "refs/heads/main", "success", "success", False),
])
def test_only_a_green_push_batch_marks(parsed: dict, event: str, ref: str, lint: str, test: str,
                                       moves: bool) -> None:
    ctx = {"github.event_name": event, "github.ref": ref,
           "needs.lint.result": lint, "needs.test.result": test}
    assert bool(_eval(" ".join(str(parsed["jobs"][MARK]["if"]).split()), ctx)) is moves


def _mark(parsed: dict, tmp_path: Path, *, green: list[str], tested: str = _TESTED,
          anc: str = "", push_fail: int = 0) -> tuple[int, str, str, list[str]]:
    code, out, _, text, calls = _run(
        _step(parsed, MARK, MARK_STEP)["run"], tmp_path, green=green,
        env={"TESTED": tested, "ANC": anc, "PUSH_FAIL": str(push_fail), "GITHUB_EVENT_NAME": "push"})
    return code, out, text, calls


_ON_MAIN = f"{_TESTED}:{_MAIN} {_OLD}:{_MAIN} {_NEWER}:{_MAIN}"


@needs_bash
def test_the_first_green_batch_makes_green_main(parsed: dict, tmp_path: Path) -> None:
    code, out, text, calls = _mark(parsed, tmp_path, green=["ABSENT"], anc=_ON_MAIN)
    assert code == 0, out
    assert _pushes(calls) == [f"git push origin {_TESTED}:refs/heads/green/main"]
    assert "green/main MOVED" in text


@needs_bash
def test_a_green_batch_moves_green_main_forward(parsed: dict, tmp_path: Path) -> None:
    code, out, _, calls = _mark(parsed, tmp_path, green=[_OLD], anc=f"{_ON_MAIN} {_OLD}:{_TESTED}")
    assert code == 0, out
    assert _pushes(calls) == [f"git push origin {_TESTED}:refs/heads/green/main"]
    assert f"git merge-base --is-ancestor {_OLD} {_TESTED}" in calls


@needs_bash
def test_green_main_never_moves_backwards(parsed: dict, tmp_path: Path) -> None:
    """An older batch or a rerun of an old push passed after a newer batch."""
    code, out, text, calls = _mark(parsed, tmp_path, green=[_NEWER], anc=f"{_ON_MAIN} {_TESTED}:{_NEWER}")
    assert code == 0, out
    assert _pushes(calls) == [], "the pointer moves forward only"
    assert "::notice title=green/main stays::" in out
    assert "STAYS" in text


@needs_bash
def test_green_main_never_moves_to_a_commit_that_does_not_contain_it(parsed: dict, tmp_path: Path) -> None:
    code, out, _, calls = _mark(parsed, tmp_path, green=[_OFF], anc=_ON_MAIN)
    assert code == 0, out
    assert _pushes(calls) == []


@needs_bash
def test_green_main_never_moves_off_main(parsed: dict, tmp_path: Path) -> None:
    code, out, _, calls = _mark(parsed, tmp_path, green=["ABSENT"], anc="")
    assert code == 0, out
    assert _pushes(calls) == [] and "::warning title=green/main stays::" in out
    assert not any("ls-remote" in c for c in calls), "it stops before it reads the pointer"


@needs_bash
def test_the_same_commit_twice_is_a_no_op(parsed: dict, tmp_path: Path) -> None:
    code, out, _, calls = _mark(parsed, tmp_path, green=[_TESTED], anc=_ON_MAIN)
    assert code == 0, out
    assert _pushes(calls) == []


@needs_bash
@pytest.mark.parametrize("tested", ["", "abc123", "Z" * 40])
def test_a_bad_tested_commit_fails_and_moves_nothing(parsed: dict, tmp_path: Path, tested: str) -> None:
    code, out, _, calls = _mark(parsed, tmp_path, green=["ABSENT"], tested=tested, anc=_ON_MAIN)
    assert code == 1, out
    assert calls == [], "no git call at all"


@needs_bash
def test_an_unreadable_pointer_fails_and_moves_nothing(parsed: dict, tmp_path: Path) -> None:
    code, out, _, calls = _mark(parsed, tmp_path, green=["ERR"], anc=_ON_MAIN)
    assert code == 1, out
    assert _pushes(calls) == []
    assert "::error title=green/main unknown::" in out


@needs_bash
def test_a_mark_that_loses_a_race_reads_the_pointer_again(parsed: dict, tmp_path: Path) -> None:
    """Another mark moved green/main past this commit between the read and
    the push. git refuses the push, and the second read stays put."""
    code, out, _, calls = _mark(
        parsed, tmp_path, green=["ABSENT", _NEWER], push_fail=1, anc=f"{_ON_MAIN} {_TESTED}:{_NEWER}")
    assert code == 0, out
    assert len(_pushes(calls)) == 1
    assert sum("ls-remote" in c for c in calls) == 2
    assert "::notice title=green/main stays::" in out


# ── 2. SHIP: one release commit for every job of the release ─────────────────


def test_the_resolver_runs_on_every_event_and_nothing_cancels_it(parsed: dict) -> None:
    job = parsed["jobs"][RESOLVER]
    assert "if" not in job, "lint and test need it on every event"
    assert "needs" not in job and "concurrency" not in job
    # CIV-30c: it reads the status `green/main`. Read only.
    assert job.get("permissions") == {"contents": "read", "statuses": "read"}
    assert job["outputs"] == {"sha": "${{ steps.pick.outputs.sha }}",
                              "picked_at": "${{ steps.pick.outputs.picked_at }}"}
    assert _step(parsed, RESOLVER, PICK_STEP).get("id") == "pick"


@pytest.mark.parametrize(("event", "nightly", "slot"), [
    ("schedule", None, "true"),
    ("workflow_dispatch", True, "true"),
    ("workflow_dispatch", False, "false"),
    ("push", None, "false"),
])
def test_only_a_slot_run_ships_green(parsed: dict, event: str, nightly: bool | None, slot: str) -> None:
    """A plain dispatch is a person's hotfix. It ships its own commit."""
    step = _step(parsed, RESOLVER, PICK_STEP)
    got = _eval(step["env"]["SLOT"], {"github.event_name": event, "inputs.nightly": nightly})
    assert got == slot


def test_every_release_job_checks_out_the_release_commit(parsed: dict) -> None:
    jobs = parsed["jobs"]
    assert set(jobs) == RELEASE_JOBS | PUSH_ONLY | NO_CHECKOUT | {RESOLVER}, (
        "a new job must say which commit it uses: add it to one of the sets in this file"
    )
    for name in RELEASE_JOBS:
        job = jobs[name]
        assert RESOLVER in _needs(job), f"{name} must need {RESOLVER} to read its output"
        cos = _checkouts(job)
        assert cos, f"{name} has no checkout"
        want = TEST_REF if name == "test" else RESOLVED
        for co in cos:
            assert (co.get("with") or {}).get("ref") == want, (
                f"{name} checks out {(co.get('with') or {}).get('ref')!r}, not the release commit"
            )
    for name in NO_CHECKOUT | PUSH_ONLY:
        if name in NO_CHECKOUT:
            assert not _checkouts(jobs[name]), f"{name} must check out nothing"
        else:
            assert "github.event_name == 'push'" in " ".join(str(jobs[name]["if"]).split())


@pytest.mark.parametrize(("event", "nightly", "ref"), [
    ("schedule", None, "SHA"),
    ("workflow_dispatch", True, "SHA"),
    ("workflow_dispatch", False, "SHA"),
    # A push keeps the default checkout: local `main`, which the batch moves.
    ("push", None, ""),
])
def test_the_test_job_tests_the_release_commit_on_a_release(event: str, nightly: bool | None, ref: str) -> None:
    ctx = {"github.event_name": event, "inputs.nightly": nightly,
           "needs.release-commit.outputs.sha": "SHA"}
    assert _eval(TEST_REF, ctx) == ref


def _release_step_text(step: dict) -> str:
    return yaml.safe_dump(step, sort_keys=True)


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_no_release_step_reads_the_run_commit(parsed: dict, event: str) -> None:
    """GITHUB_SHA is the tip of main when the run started. On a slot run the
    release commit is green/main. Only a step that runs on a push alone may
    read GITHUB_SHA (the tip and range steps of a push batch)."""
    jobs = parsed["jobs"]
    github = {"event_name": event, "run_attempt": "1", "run_id": "1"}
    for name in RELEASE_JOBS | NO_CHECKOUT:
        job = jobs[name]
        text = yaml.safe_dump({k: v for k, v in job.items() if k != "steps"})
        assert "GITHUB_SHA" not in text and "github.sha" not in text, name
        for step in job.get("steps", []):
            body = _release_step_text(step)
            if "GITHUB_SHA" not in body and "github.sha" not in body:
                continue
            assert "if" in step and not _gh_eval(step["if"], github, {"nightly": True}), (
                f"{name} / {step.get('name') or step.get('uses')} reads the run commit on a release"
            )


def test_the_deploy_path_names_the_release_commit(parsed: dict) -> None:
    jobs = parsed["jobs"]
    for name in ("deploy", "pull-delivery"):
        assert jobs[name]["env"]["RELEASE_SHA"] == RESOLVED, name
    assert _step(parsed, "smoke", "Health check")["env"]["RELEASE_SHA"] == RESOLVED
    publish = _step(parsed, "publish-release", PUBLISH_STEP)
    assert publish["env"]["RELEASE_SHA"] == RESOLVED
    assert 'git push origin "${RELEASE_SHA}:refs/heads/release"' in publish["run"]
    deploy = next(s for s in jobs["deploy"]["steps"] if s.get("id") == "deploy")["run"]
    assert '"DEPLOY_REF=${RELEASE_SHA} DEPLOY_FORCE=' in deploy
    assert 'if [ "$got_sha" = "$RELEASE_SHA" ]; then' in deploy
    stale = next(s for s in jobs["deploy"]["steps"] if s.get("id") == "stale")["run"]
    assert 'git merge-base --is-ancestor "$RELEASE_SHA" "$rel"' in stale
    pull = jobs["pull-delivery"]["steps"][1]["run"]
    assert 'await_pull_delivery "$RELEASE_SHA" || rc=$?' in pull


# ── 3. The resolver, run in bash ─────────────────────────────────────────────


def _pick(parsed: dict, tmp_path: Path, *, green: list[str], slot: str = "true", event: str = "schedule",
          anc: str = "", left: int = 0) -> tuple[int, str, str, str, list[str]]:
    step = _step(parsed, RESOLVER, PICK_STEP)
    # The step's own plain env (CIV-30c: WALK, WALK_SECONDS, CONTEXT). An
    # expression such as the token stays out. The fake `git` lists no
    # commit for the walk, so this file never reaches `gh`.
    plain = {k: str(v) for k, v in step["env"].items() if "${{" not in str(v)}
    return _run(step["run"], tmp_path, green=green,
                env={**plain, "SLOT": slot, "GITHUB_EVENT_NAME": event, "ANC": anc, "LEFT_COUNT": str(left)})


def _sha(output: str) -> str:
    """The `sha=` line of GITHUB_OUTPUT. A slot run also writes `picked_at=`."""
    found = [ln for ln in output.splitlines() if ln.startswith("sha=")]
    assert len(found) == 1, output
    return found[0]


@needs_bash
@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_a_run_that_is_not_a_slot_uses_its_own_commit(parsed: dict, tmp_path: Path, event: str) -> None:
    code, out, output, text, calls = _pick(parsed, tmp_path, green=[_TESTED], slot="false", event=event)
    assert code == 0, out
    assert output.strip() == f"sha={_GHSHA}"
    assert calls == [], "a push and a plain dispatch touch no network"
    if event == "push":
        assert text == "", "a push writes no release summary"
    else:
        assert "this run's own commit" in text


@needs_bash
def test_a_slot_ships_green_main_and_lists_what_it_leaves_out(parsed: dict, tmp_path: Path) -> None:
    code, out, output, text, calls = _pick(parsed, tmp_path, green=[_TESTED], anc=f"{_TESTED}:{_MAIN}", left=3)
    assert code == 0, out
    assert _sha(output) == f"sha={_TESTED}"
    assert re.search(r"^picked_at=\d+$", output, re.M), "a slot run gives its pick time (CIV-30c)"
    assert f"`{_TESTED[:12]}`, the head of `green/main`" in text
    assert "It leaves out 3 commit(s) on main after it" in text
    assert "merge number 3 (#3)" in text
    assert f"git log --oneline --no-decorate {_TESTED}..{_MAIN}" in calls


@needs_bash
def test_a_slot_with_a_green_tip_leaves_out_nothing(parsed: dict, tmp_path: Path) -> None:
    code, out, output, text, _ = _pick(parsed, tmp_path, green=[_MAIN])
    assert code == 0, out
    assert _sha(output) == f"sha={_MAIN}"
    assert "It leaves out no commit" in text


@needs_bash
def test_with_no_green_main_a_slot_falls_back_to_its_own_commit(parsed: dict, tmp_path: Path) -> None:
    """The FALLBACK: the behaviour before CIV-30, and it says so."""
    code, out, output, text, _ = _pick(parsed, tmp_path, green=["ABSENT"])
    assert code == 0, out
    assert _sha(output) == f"sha={_GHSHA}"
    assert "::warning title=No green/main yet::" in out
    assert "FALLBACK" in text and _GHSHA[:12] in text


@needs_bash
def test_an_unreadable_green_main_holds_the_release(parsed: dict, tmp_path: Path) -> None:
    """Only "no such ref" falls back. Any other failure ships nothing."""
    code, out, output, _, _ = _pick(parsed, tmp_path, green=["ERR"])
    assert code == 1, out
    assert output == "", "no release commit, so no job of the release runs"


@needs_bash
def test_a_green_main_off_main_holds_the_release(parsed: dict, tmp_path: Path) -> None:
    code, out, output, _, _ = _pick(parsed, tmp_path, green=[_OFF], anc="")
    assert code == 1, out
    assert "::error title=green/main is not on main::" in out
    assert output == ""


# ── 4. publish-release never rewinds release ─────────────────────────────────


def _publish(parsed: dict, tmp_path: Path, *, sha: str, release: str, anc: str,
             green: list[str] | None = None) -> tuple[int, str, list[str]]:
    run = _step(parsed, "publish-release", PUBLISH_STEP)["run"].replace("${{ github.event_name }}", "schedule")
    assert "${{" not in run
    code, out, _, _, calls = _run(run, tmp_path, green=green or [release or "ABSENT"],
                                  env={"RELEASE_SHA": sha, "RELEASE": release, "ANC": anc, "REASON": ""})
    return code, out, calls


@needs_bash
def test_publish_moves_release_forward_to_the_release_commit(parsed: dict, tmp_path: Path) -> None:
    code, out, calls = _publish(parsed, tmp_path, sha=_TESTED, release=_OLD, anc=f"{_OLD}:{_TESTED}")
    assert code == 0, out
    assert _pushes(calls) == [f"git push origin {_TESTED}:refs/heads/release"]


@needs_bash
def test_publish_holds_when_release_already_holds_a_newer_commit(parsed: dict, tmp_path: Path) -> None:
    """A plain dispatch shipped the tip. The next slot finds green/main older."""
    code, out, calls = _publish(parsed, tmp_path, sha=_TESTED, release=_NEWER, anc=f"{_TESTED}:{_NEWER}")
    assert code == 0, out
    assert _pushes(calls) == []
    assert "::notice title=Nothing newer to release::" in out


@needs_bash
@pytest.mark.parametrize("sha", ["", "abc", "G" * 40])
def test_publish_refuses_a_bad_release_commit(parsed: dict, tmp_path: Path, sha: str) -> None:
    code, out, calls = _publish(parsed, tmp_path, sha=sha, release=_OLD, anc="")
    assert code == 1, out
    assert calls == []


# ── 5. A bad read of green/main holds the release ────────────────────────────


@needs_bash
@pytest.mark.parametrize("answer", ["EMPTY", "OTHERREF"])
def test_a_read_with_no_exact_green_main_line_holds_the_release(
        parsed: dict, tmp_path: Path, answer: str) -> None:
    """`git ls-remote` exits 0, so the ref is there. A missing line for it
    is a bad read, not "no green/main yet". Only exit 2 falls back."""
    code, out, output, text, _ = _pick(parsed, tmp_path, green=[answer])
    assert code == 1, out
    assert output == "", "no release commit, so no job of the release runs"
    assert "::error title=green/main unreadable::" in out
    assert "FALLBACK" not in text


# ── 6. The R8 guard reads the lists of the release commit ────────────────────
#
# GitHub runs the workflow file of the tip of main on a schedule or a
# dispatch. The R8 reports and the tree are the release commit's. A suite
# that lands after green/main is in the tip's list and not in the release
# tree. The guard step reads the lists from the release commit's own
# tests/fences.json (`r8` key). Before CIV-33c-2 the lists sat in deploy.yml,
# so a release commit with no `r8` key is read the old way.

GUARD_JOB = "r8-gate"
GUARD_STEP = "The R8 suites ran, and no fence skipped"
_FENCES = WORKFLOW.parents[2] / "tests" / "fences.json"
_BS = chr(92)
#: A suite in the tip's list. The release commit lacks it: it landed later.
_LATE = "tests/unit/test_team_scope_isolation_r8.py"
#: A suite in the release commit's own list, and not in its tree.
_GONE = "tests/unit/test_gone_after_green_r8.py"

# A fake `git` that serves the release commit's tests/fences.json and its
# deploy.yml for the two exact `git show` calls, and fails every other call.
# `python3` is this interpreter.
_FAKE_SHOW = r"""
git() {
  printf '%s\n' "git $*" >> "$GIT_LOG"
  if [ "$1" = show ] && [ "$#" = 2 ] && [ "$2" = "${RELEASE_SHA}:tests/fences.json" ] \
     && [ -f "$RELEASE_FENCES" ]; then
    cat "$RELEASE_FENCES"
    return 0
  fi
  if [ "$1" = show ] && [ "$#" = 2 ] && [ "$2" = "${RELEASE_SHA}:.github/workflows/deploy.yml" ] \
     && [ -f "$RELEASE_WORKFLOW" ]; then
    cat "$RELEASE_WORKFLOW"
    return 0
  fi
  echo "fatal: bad object" >&2
  return 128
}
python3() { "$PYTHON" "$@"; }
"""


@pytest.fixture(scope="module")
def fences() -> dict:
    return json.loads(_FENCES.read_text(encoding="utf-8"))


def _named(fences: dict) -> list[str]:
    r8 = fences["r8"]
    return sorted(set(r8["must_run"]) | set(r8["never_skip"]))


def _edit(fences: dict, **changes: object) -> str:
    """tests/fences.json as another commit holds it, with `r8` changed.
    A value of None drops the key."""
    r8 = {**fences["r8"], **changes}
    r8 = {k: v for k, v in r8.items() if v is not None}
    return json.dumps({**fences, "r8": r8})


def _old_deploy(fences: dict, *, empty_must_run: bool = False) -> str:
    """deploy.yml as a release commit from before CIV-33c-2 holds it: the
    guard's three lists inline on the command."""
    r8 = fences["r8"]
    must = [] if empty_must_run else r8["must_run"]
    words = [f'--disarmed "{d}"' for d in r8["disarmed"]]
    words += ["--never-skip", *r8["never_skip"], "--must-run", *must]
    body = f" {_BS}\n".join(
        ("  " if w.startswith("--") else "    ") + w for w in words
    )
    return (
        "jobs:\n"
        f"  {GUARD_JOB}:\n"
        "    steps:\n"
        f"      - name: {GUARD_STEP}\n"
        "        run: |\n"
        f"          python3 scripts/ci_unit_guard.py check --junit junit {_BS}\n"
        + "\n".join("          " + ln for ln in body.splitlines())
        + "\n"
    )


def _release_tree(tmp_path: Path, files: list[str], *, ran: list[str] | None = None) -> None:
    """The checkout of the release commit: its files, its guard script, and
    one R8 report with a passing test for each file in `ran`."""
    for p in files:
        f = tmp_path / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("", encoding="utf-8")
    (tmp_path / "scripts").mkdir(exist_ok=True)
    shutil.copy(_SCRIPTS / "ci_unit_guard.py", tmp_path / "scripts" / "ci_unit_guard.py")
    cases = "".join(
        f'<testcase classname="{p[:-3].replace("/", ".")}" name="test_ok" file="{p}" time="0.1"/>'
        for p in (files if ran is None else ran)
    )
    (tmp_path / "junit").mkdir(exist_ok=True)
    (tmp_path / "junit" / "r8-shard-1.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        f"{cases}</testsuite></testsuites>", encoding="utf-8")


def _run_guard(parsed: dict, tmp_path: Path, *, release_fences: str | None, tree: list[str],
               release_workflow: str | None = None, ran: list[str] | None = None,
               sha: str = _TESTED) -> tuple[int, str, list[str]]:
    """Run the tip's guard step in bash, in the checkout of the release."""
    run = _step(parsed, GUARD_JOB, GUARD_STEP)["run"]
    assert "${{" not in run
    served = tmp_path / "served"
    served.mkdir()
    if release_fences is not None:
        (served / "fences.json").write_text(release_fences, encoding="utf-8", newline="\n")
    if release_workflow is not None:
        (served / "deploy.yml").write_text(release_workflow, encoding="utf-8", newline="\n")
    _release_tree(tmp_path, tree, ran=ran)
    (tmp_path / "runner_temp").mkdir()
    log = tmp_path / "git.log"
    env = {**os.environ, "RELEASE_SHA": sha, "RELEASE_FENCES": (served / "fences.json").as_posix(),
           "RELEASE_WORKFLOW": (served / "deploy.yml").as_posix(),
           "GIT_LOG": log.as_posix(), "PYTHON": Path(sys.executable).as_posix(),
           "RUNNER_TEMP": (tmp_path / "runner_temp").as_posix()}
    done = subprocess.run(
        [_BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", _FAKE_SHOW + run],
        env=env, capture_output=True, text=True, timeout=120, cwd=tmp_path,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done.returncode, done.stdout + done.stderr, calls


def test_the_guard_step_reads_the_release_commits_own_fences(parsed: dict) -> None:
    step = _step(parsed, GUARD_JOB, GUARD_STEP)
    assert step["env"]["RELEASE_SHA"] == RESOLVED
    run = step["run"]
    assert 'git show "${RELEASE_SHA}:tests/fences.json" > "$RUNNER_TEMP/release_fences.json"' in run
    assert '--lists-from-fences "$RUNNER_TEMP/release_fences.json"' in run
    # The bootstrap: a release commit from before the move has no `r8` key.
    assert 'git show "${RELEASE_SHA}:.github/workflows/deploy.yml" > "$RUNNER_TEMP/release_deploy.yml"' in run
    assert '--lists-from "$RUNNER_TEMP/release_deploy.yml"' in run
    assert f'--lists-step "{GUARD_STEP}"' in run
    # The tip's workflow carries no list of its own, and no selection (CIV-3).
    assert "--must-run" not in run and "--never-skip" not in run and "--disarmed" not in run
    assert "--only-selected" not in run and "--tree-root" not in run


def test_with_the_tip_as_the_release_the_lists_are_todays(fences: dict) -> None:
    """RELEASE_SHA equals the tip: the guard reads the lists of today's file."""
    got = guard.fence_lists(_FENCES.read_text(encoding="utf-8"))
    assert got["--must-run"] == fences["r8"]["must_run"]
    assert got["--never-skip"] == fences["r8"]["never_skip"]
    assert got["--disarmed"] == fences["r8"]["disarmed"]
    assert len(got["--must-run"]) > 50


@needs_bash
def test_a_suite_that_landed_after_green_does_not_hold_the_slot(
        parsed: dict, fences: dict, tmp_path: Path) -> None:
    """The tip's list names `_LATE`. The release commit has neither the
    file nor its line. The release commit's own lists apply."""
    release = _edit(fences, must_run=[p for p in fences["r8"]["must_run"] if p != _LATE])
    assert _LATE in _named(fences) and _LATE not in _named(json.loads(release))
    code, out, calls = _run_guard(
        parsed, tmp_path, release_fences=release, tree=_named(json.loads(release)))
    assert code == 0, out
    assert calls == [f"git show {_TESTED}:tests/fences.json"]


@needs_bash
def test_a_release_commit_whose_own_list_names_a_missing_suite_fails(
        parsed: dict, fences: dict, tmp_path: Path) -> None:
    """The release commit lists `_GONE`, and its tree lacks it. Its own gate
    is red, so the slot holds. The tip's list does not name it."""
    release = _edit(fences, must_run=[_GONE, *fences["r8"]["must_run"]])
    assert _GONE not in _named(fences)
    tree = [p for p in _named(json.loads(release)) if p != _GONE]
    code, out, _ = _run_guard(parsed, tmp_path, release_fences=release, tree=tree)
    assert code == 1, out
    assert f"a --must-run file is not in the tree: {_GONE}" in out


@needs_bash
@pytest.mark.parametrize("missed", [False, True])
def test_with_the_tip_as_the_release_the_guard_is_todays_guard(
        parsed: dict, fences: dict, tmp_path: Path, missed: bool) -> None:
    files = _named(fences)
    ran = [p for p in files if p != _LATE] if missed else None
    code, out, _ = _run_guard(
        parsed, tmp_path, release_fences=_FENCES.read_text(encoding="utf-8"), tree=files, ran=ran)
    if missed:
        assert code == 1 and f"no shard ran {_LATE}" in out, out
    else:
        assert code == 0, out


@needs_bash
@pytest.mark.parametrize(
    "case", ["show-fails", "not-json", "empty-must-run", "no-never-skip-list", "not-a-path", "bad-sha"])
def test_the_guard_fails_closed_when_it_cannot_read_the_release_lists(
        parsed: dict, fences: dict, tmp_path: Path, case: str) -> None:
    """Never an empty list, never the tip's list in its place, and never the
    old reader for a file that holds an `r8` key or that does not parse."""
    served: str | None = _FENCES.read_text(encoding="utf-8")
    sha = _TESTED
    if case == "show-fails":
        served = None
    elif case == "not-json":
        served = "{ not json"
    elif case == "empty-must-run":
        served = _edit(fences, must_run=[])
    elif case == "no-never-skip-list":
        served = _edit(fences, never_skip=None)
    elif case == "not-a-path":
        served = _edit(fences, must_run=["tests/unit/$(evil).py"])
    else:
        sha = ""
    # The old deploy.yml is served too: no case may reach it.
    code, out, calls = _run_guard(
        parsed, tmp_path, release_fences=served, release_workflow=_old_deploy(fences),
        tree=_named(fences), sha=sha)
    assert code == 1, out
    assert "checked " not in out, "the guard never runs on lists that it could not read"
    if case == "bad-sha":
        assert calls == [], "no read of a commit with no name"
    else:
        assert not any("deploy.yml" in c for c in calls), "no fallback on a fault"


@needs_bash
def test_a_release_commit_from_before_the_move_is_read_the_old_way(
        parsed: dict, fences: dict, tmp_path: Path) -> None:
    """The bootstrap: no `r8` key, so the lists come from its own deploy.yml."""
    old = {k: v for k, v in fences.items() if k != "r8"}
    code, out, calls = _run_guard(
        parsed, tmp_path, release_fences=json.dumps(old), release_workflow=_old_deploy(fences),
        tree=_named(fences))
    assert code == 0, out
    assert calls == [f"git show {_TESTED}:tests/fences.json", f"git show {_TESTED}:.github/workflows/deploy.yml"]


@needs_bash
@pytest.mark.parametrize("case", ["show-fails", "empty-must-run"])
def test_the_bootstrap_fails_closed_too(
        parsed: dict, fences: dict, tmp_path: Path, case: str) -> None:
    old = json.dumps({k: v for k, v in fences.items() if k != "r8"})
    workflow = None if case == "show-fails" else _old_deploy(fences, empty_must_run=True)
    code, out, _ = _run_guard(
        parsed, tmp_path, release_fences=old, release_workflow=workflow, tree=_named(fences))
    assert code == 1, out
    assert "checked " not in out


def test_the_fence_reader_fails_closed(fences: dict) -> None:
    for text, why in [
        ("", "no readable"),
        ("[]", "no readable"),
        (json.dumps({k: v for k, v in fences.items() if k != "r8"}), "no readable"),
        (_edit(fences, must_run=[]), "empty --must-run"),
        (_edit(fences, never_skip=[]), "empty --never-skip"),
        (_edit(fences, disarmed=None), "no list for --disarmed"),
        (_edit(fences, must_run="tests/unit/a.py"), "no list for --must-run"),
        (_edit(fences, must_run=["tests/unit/$(evil).py"]), "not a test path"),
    ]:
        with pytest.raises(guard.GuardListError, match=re.escape(why)):
            guard.fence_lists(text)


def test_the_old_list_reader_still_fails_closed(fences: dict) -> None:
    """The bootstrap reads an old deploy.yml with `guard_lists`."""
    old = _old_deploy(fences)
    got = guard.guard_lists(old, GUARD_STEP)
    assert got["--must-run"] == fences["r8"]["must_run"]
    assert got["--never-skip"] == fences["r8"]["never_skip"]
    for text, why in [
        ("", "steps named"),
        (old.replace(f"- name: {GUARD_STEP}", "- name: Other"), "steps named"),
        (_old_deploy(fences, empty_must_run=True), "--must-run"),
        (old.replace("--never-skip", "--never-skip-x"), "--never-skip"),
    ]:
        with pytest.raises(guard.GuardListError, match=re.escape(why)):
            guard.guard_lists(text, GUARD_STEP)
