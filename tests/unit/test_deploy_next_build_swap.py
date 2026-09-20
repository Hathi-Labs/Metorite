"""A deploy must not delete the build it is still serving from.

🔴 **The defect this pins cost a full outage and nothing alarmed.** Measured
2026-09-01: `app.metorite.com` answered **HTTP 500 on every route**, including
`/`, while `acb-workbench` restart-looped every five seconds with::

    Error: Could not find a production build in the '.next' directory

The build had not failed. `vps_apply.sh` ran ``rm -rf .next`` and *then* built,
so the directory the live server reads from was deleted to make room for its
replacement — and the server served 500s for the whole build. Two Next.js
builds run per deploy, so the window is minutes rather than seconds. A build
that genuinely FAILED left the app that way until somebody noticed.

Three properties keep it fixed, and each is one line that a future tidy-up
would happily remove:

* **the build writes to a staging directory**, never over `.next`
* **the swap is a rename**, so it is atomic — a copy can be read half-done
* **`vps-health.yml` treats 5xx as an outage**, because it did not: the probe
  logged that morning's total outage as ``OK (HTTP 500)`` under the rule "any
  HTTP response means the stack is serving"

⚠️ **The last two are coupled and must not be separated.** Alarming on 5xx is
only correct while the deploy no longer serves 500s routinely. Ship the health
change without the swap and every release pages; ship the swap without the
health change and the next outage is invisible again.

⚠️ Idiom inherited from `test_deploy_venv_ownership` and
`test_operator_console_deploy_wiring`: **every assertion reads NON-COMMENT
lines.** The block under test carries a long comment naming every string here,
and a guard satisfied by prose certifies the documentation rather than the
wiring.
"""

from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_APPLY = _ROOT / "scripts/vps_apply.sh"
_HEALTH = _ROOT / ".github/workflows/vps-health.yml"
_CONFIGS = (
    _ROOT / "workbench/control_plane/next.config.ts",
    _ROOT / "workbench/operator_console/next.config.mjs",
)


def _executable_lines(path: pathlib.Path) -> list[str]:
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def _executable_js(path: pathlib.Path) -> str:
    """The same rule for the TS/JS configs, whose comments open with ``//``.

    ⚠️ Not cosmetic. `control_plane/next.config.ts` NAMES ``NEXT_DIST_DIR`` in
    its comment, so a whole-file search would pass on a config that had lost
    the setting and kept the paragraph explaining it.
    """
    return "\n".join(
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("//")
    )


class TestTheApplyNeverDeletesTheLiveBuild:
    def test_nothing_removes_dot_next_itself(self) -> None:
        """``rm -rf .next`` is the defect, verbatim.

        Removing the STAGING directory is required and safe — it is not being
        served. Removing `.next` is the outage.
        """
        offenders = [
            ln
            for ln in _executable_lines(_APPLY)
            if "rm -rf" in ln
            and any(
                target == ".next"
                for target in ln.replace("rm -rf", "").split()
            )
        ]
        assert offenders == [], (
            "vps_apply.sh deletes the build the running server is serving "
            f"from: {offenders}"
        )

    def test_the_build_targets_a_staging_directory(self) -> None:
        lines = _executable_lines(_APPLY)
        assert any("NEXT_DIST_DIR" in ln and ".next.staging" in ln for ln in lines)

    def test_the_swap_is_a_rename_not_a_copy(self) -> None:
        # A copy is not atomic. A server that reloads mid-copy reads a
        # half-written build, which is the same 500 wearing a different hat.
        lines = _executable_lines(_APPLY)
        assert any(ln.strip() == "mv .next.staging .next" for ln in lines)
        assert not any(
            "cp " in ln and ".next.staging" in ln for ln in lines
        ), "the swap must be a rename — a copy can be read half-done"

    def test_the_swap_is_gated_on_a_real_build_artifact(self) -> None:
        """Exit code 0 is not the same claim as "there is a build here".

        BUILD_ID is the file `next start` looks for and fails on, so it is the
        artifact worth checking. This repo has shipped three separate defects
        where a green command produced nothing.
        """
        lines = _executable_lines(_APPLY)
        assert any(".next.staging/BUILD_ID" in ln for ln in lines)

    def test_both_next_apps_are_built_through_the_one_helper(self) -> None:
        # A second inlined build is a second copy of this bug. The workbench
        # and the operator console must both call it.
        lines = _executable_lines(_APPLY)
        calls = [ln for ln in lines if "build_next_staged" in ln and "(" not in ln]
        assert len(calls) >= 2, (
            "both Next.js apps must build through build_next_staged; "
            f"found {calls}"
        )


class TestBothNextConfigsHonourTheStagingDirectory:
    def test_dist_dir_reads_the_environment(self) -> None:
        """Without this the staging build silently writes to `.next` anyway.

        That failure is invisible: the deploy still passes, the swap still
        renames, and the outage comes back with the fence green.
        """
        for config in _CONFIGS:
            body = _executable_js(config)
            assert "NEXT_DIST_DIR" in body, (
                f"{config.name} ignores NEXT_DIST_DIR"
            )
            assert "distDir" in body, f"{config.name} sets no distDir"


class TestTheHealthProbeCallsA5xxAnOutage:
    def test_a_5xx_is_not_reported_as_alive(self) -> None:
        """The probe recorded a total outage as ``OK (HTTP 500)``.

        The old rule was "any HTTP response means the stack is serving". A
        Next.js server with no build directory serves 500s perfectly.

        ⚠️ **This reads the ALARMING branch, not the file.** The first version
        of this test asserted ``"5[0-9][0-9]" in body`` and a mutation that
        narrowed the outage arm to a single code (``599)``) passed it — the
        pattern was still present up in the retry loop. A guard that matches
        somewhere else in the file is not a guard.
        """
        lines = _executable_lines(_HEALTH)
        report = next(
            i for i, ln in enumerate(lines) if "SERVING ERRORS" in ln
        )
        # Walk back to the case label that selects this branch.
        label = next(
            lines[i].strip()
            for i in range(report, -1, -1)
            if lines[i].strip().endswith(")")
            and not lines[i].strip().startswith("echo")
        )
        assert label == "5[0-9][0-9])", (
            "the outage branch must match the whole 5xx range, not "
            f"{label!r} — a narrowed arm lets most broken states report OK"
        )
        assert any("HEALTHY=0" in ln for ln in lines[report - 2 : report + 1])

    def test_a_4xx_still_counts_as_alive(self) -> None:
        # A redirect to sign-in and a 401 both prove the stack compiled. If
        # this ever fails, the probe has started paging on a healthy box.
        lines = _executable_lines(_HEALTH)
        assert not any("4[0-9][0-9]" in ln for ln in lines)


_PULL = _ROOT / "scripts/vps_pull.sh"
_TSCONFIG = _ROOT / "workbench/control_plane/tsconfig.json"


class TestAStaleGeneratedTypeCannotDeadlockTheBuild:
    """🔴 Eleven hours of production outage, from one renamed directory.

    `tsconfig.json` includes the generated route types of BOTH dist dirs,
    because either can be live. So a staged build isolates its output and
    still type-checks the PREVIOUS build's `validator.ts`. That file names
    every route by path, so renaming a route directory makes it reference a
    module that is gone:

        Cannot find module '../../src/app/api/people/[...path]/route.js'

    Nothing recovers on its own. The build cannot pass until `.next` is
    replaced, and the swap only replaces `.next` after a build passes.

    Measured 2026-09-20 on production. PR #306 renamed that route, and every
    five-minute apply for the next eleven hours died here — each one having
    already restarted the live gateway.
    """

    def test_the_staged_build_clears_the_previous_generated_types(self) -> None:
        lines = _executable_lines(_APPLY)
        start = next(
            i for i, ln in enumerate(lines) if "build_next_staged()" in ln
        )
        # Only the body, and only up to the build command — clearing them
        # AFTER the build would be pointless.
        build = next(
            i for i in range(start, len(lines)) if "npm run build" in lines[i]
        )
        body = lines[start:build]
        removers = ("rm ", "drop_dir ")
        assert any(
            ".next/types" in ln and ln.strip().startswith(removers) for ln in body
        ), (
            "build_next_staged must remove the previous build's .next/types "
            "BEFORE it builds. Without it a renamed route directory wedges "
            "every future deploy, and each retry restarts the gateway first."
        )

    def test_tsconfig_still_scopes_both_dist_dirs(self) -> None:
        """The tripwire for the test above.

        If somebody removes `.next/types` from `include`, the deadlock is
        gone by another route and the `rm` stops being load-bearing — but so
        does this whole class, and it would keep passing while guarding
        nothing. Fail here instead, so the reason gets re-read.
        """
        text = _TSCONFIG.read_text(encoding="utf-8")
        assert ".next/types/**/*.ts" in text
        assert ".next.staging/types/**/*.ts" in text

    def test_the_gateway_still_restarts_before_the_workbench_builds(self) -> None:
        """⚠️ This pins the ORDER that made the fault user-visible.

        The gateway restart runs long before the workbench build, so a build
        that always fails still bounces production on every tick. Reordering
        is not obviously right — the API is meant to be new before the UI
        that calls it — so this is NOT a demand to change it. It records the
        ordering the circuit breaker in `vps_pull.sh` exists to compensate
        for. Change the order and the breaker's rationale needs re-reading.
        """
        lines = _executable_lines(_APPLY)
        restart = next(
            i for i, ln in enumerate(lines)
            if "systemctl restart acb-gateway" in ln
        )
        build = next(i for i, ln in enumerate(lines) if "npm run build" in ln)
        assert restart < build


class TestARetryThatCannotSucceedStopsRetrying:
    """One failed apply is a deploy that did not land. 130 is an outage.

    The retry in `vps_pull.sh` gates on the last SUCCESSFUL sha, which is
    right — a half-finished apply must be tried again. What it missed is an
    apply that fails the same way every time. Because the apply restarts the
    gateway before it builds, an unwinnable retry is not a no-op: it is a
    gateway restart every five minutes, forever.
    """

    def test_there_is_a_failure_counter_and_a_ceiling(self) -> None:
        lines = _executable_lines(_PULL)
        text = "\n".join(lines)
        assert "last-fail-count" in text, "nothing counts repeated failures"
        assert "MAX_FAILS" in text, "nothing caps them"

    def test_the_breaker_refuses_BEFORE_running_the_apply(self) -> None:
        """A breaker that trips after the apply has already run is decoration.

        The whole cost of the loop was paid inside `vps_apply.sh` — the
        gateway restart. Exiting after it has run saves nothing.
        """
        lines = _executable_lines(_PULL)
        trip = next(
            i for i, ln in enumerate(lines) if "MAX_FAILS" in ln and "-ge" in ln
        )
        apply_at = next(
            i for i, ln in enumerate(lines) if 'bash "$TMP_APPLY"' in ln
        )
        assert trip < apply_at, (
            "the failure ceiling is checked after the apply already ran — "
            "the restart it is meant to prevent has already happened"
        )

    def test_the_count_is_keyed_to_the_target_sha(self) -> None:
        """A new commit must get its own attempts, with nothing cleared by
        hand. A global counter would latch the box out after three unrelated
        failures and need an operator to reset it."""
        lines = _executable_lines(_PULL)
        text = "\n".join(lines)
        assert 'last-fail-sha' in text
        assert '"$FAIL_SHA" = "$TARGET"' in text

    def test_a_success_clears_the_breaker(self) -> None:
        """Otherwise a box that recovers stays latched out by its history."""
        lines = _executable_lines(_PULL)
        # ⚠️ The WRITE of the success marker, not the read of it. Anchoring on
        # `">" in ln` matched `$(cat ... 2>/dev/null)` first, three hundred
        # lines earlier, and the assertion then read a window with no writes
        # in it at all.
        ok = next(
            i for i, ln in enumerate(lines)
            if 'echo "$TARGET" >' in ln and "last-pull-sha" in ln
        )
        window = "\n".join(lines[ok : ok + 4])
        assert "rm -f" in window and "last-fail" in window

    def test_giving_up_still_exits_NON_zero(self) -> None:
        """A box that has stopped trying must not look healthy.

        `systemctl --failed` is the only thing watching. Exiting 0 here would
        turn a stuck box into a silent one, which is the exact failure WS-25
        was built to end.
        """
        lines = _executable_lines(_PULL)
        trip = next(
            i for i, ln in enumerate(lines) if "MAX_FAILS" in ln and "-ge" in ln
        )
        window = "\n".join(lines[trip : trip + 8])
        assert "exit 0" not in window
        assert "exit 11" in window


class TestHousekeepingCannotAbortTheDeploy:
    """🔴 Two production deploys reported SUCCESS and shipped no UI, in one hour.

    Measured 2026-09-20. `.next` on the box carries root-owned files (H-89),
    the apply runs as the deploy user, and so ``rm -rf .next.previous`` exits
    "Permission denied". Under ``set -e`` that ended the script.

    The post-swap call is the one that hurts. By then `.next` has ALREADY been
    replaced, so the box holds a build its running server never loaded, and
    the ``systemctl restart acb-workbench`` eleven lines below never runs. The
    gateway answers its own ``/version`` with the new SHA, the workbench
    answers 307, and `verify()` blesses the release. The owner opens the app
    and sees the previous build.

    The rule: **deleting the last build's leftovers is never worth a failed
    release.** Every removal in the build path goes through ``drop_dir``,
    which tries as the deploy user, then with sudo, then gives up and says so.

    ⚠️ This does NOT weaken `TestTheApplyNeverDeletesTheLiveBuild`. A removal
    that must not fail is a different claim from a removal that must not
    happen, and `.next` itself is still never a target.
    """

    def test_every_removal_in_the_build_path_goes_through_the_helper(self) -> None:
        lines = _executable_lines(_APPLY)
        start = next(i for i, ln in enumerate(lines) if "build_next_staged()" in ln)
        end = next(
            i for i in range(start + 1, len(lines))
            if lines[i].strip() == "}"
        )
        raw = [ln for ln in lines[start:end] if ln.strip().startswith("rm ")]
        assert raw == [], (
            "a bare `rm` inside build_next_staged can abort the deploy after "
            f"the swap and before the restart — use drop_dir: {raw}"
        )

    def test_the_helper_never_returns_non_zero(self) -> None:
        lines = _executable_lines(_APPLY)
        start = next(i for i, ln in enumerate(lines) if ln.strip().startswith("drop_dir()"))
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "}")
        body = lines[start:end]
        assert any(ln.strip() == "return 0" for ln in body), (
            "drop_dir exists so that a failed cleanup cannot end the apply; "
            "it must end in `return 0`"
        )
        assert any("sudo rm -rf" in ln for ln in body), (
            "the files it must remove are root-owned (H-89) — try sudo before "
            "giving up"
        )

    def test_the_restart_still_follows_the_swap(self) -> None:
        """The property the cleanup was destroying.

        A new `.next` that the running server never loaded is worse than no
        deploy: the served chunks are gone, replaced by a build nothing has
        read. The restart is what makes the swap real.
        """
        lines = _executable_lines(_APPLY)
        swap = next(i for i, ln in enumerate(lines) if ln.strip() == "mv .next.staging .next")
        restart = next(
            i for i in range(swap, len(lines))
            if "systemctl restart acb-workbench" in lines[i]
        )
        assert swap < restart

    def test_a_failed_npm_install_does_not_end_the_apply(self) -> None:
        """It did, this morning, before the build was even attempted.

        `npm ci` and `npm install` both hit EACCES on a root-owned
        `node_modules`. The build is the gate — it either passes, or
        `build_next_staged` keeps the running build and fails loudly. An
        install that cannot abort costs nothing and saves the release.
        """
        lines = _executable_lines(_APPLY)
        i = next(i for i, ln in enumerate(lines) if "npm ci --prefer-offline" in ln)
        # The statement, not the neighbourhood: a backslash continues it.
        stmt = [lines[i]]
        while stmt[-1].rstrip().endswith(chr(92)):
            i += 1
            stmt.append(lines[i])
        statement = " ".join(ln.strip() for ln in stmt)
        assert statement.count("||") >= 2 and "echo" in statement, (
            "the workbench npm install must fall through to a warning rather "
            f"than abort the apply under set -e: {statement!r}"
        )
