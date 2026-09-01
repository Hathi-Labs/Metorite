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
