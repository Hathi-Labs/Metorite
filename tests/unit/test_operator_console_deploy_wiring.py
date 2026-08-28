"""H-75 — the Operator Console must ride the LIVE delivery path.

🔴 **Measured 2026-08-28, and it had been true for two days.**
`operator.metorite.com` served a login page, Caddy routed it, a process
answered — and both `/models` (merged 2026-08-27) and `/providers` (merged
2026-08-28) returned **404**. The site was up and the code was not.

The cause: `scripts/vps_apply.sh` rebuilt and restarted four services —
`acb-gateway`, `acb-workbench`, `acb-customer-console`, `acb-whatsapp-bridge`
— and `workbench/operator_console` was in none of them. Merging to `main`
deployed the customer product and never touched the operator console. Somebody
stood it up by hand once, and the code moved on without it.

This is `test_console_ladder_deploy_wiring`'s failure a second time, one
service over: **a thing that exists on the box, is reachable, looks healthy,
and is not on the delivery path.** A green deploy said nothing was wrong
because as far as the deploy was concerned nothing was.

Three properties:
  1. `vps_apply.sh` builds AND restarts the operator console;
  2. it does so AFTER the customer surfaces, so a staff-console failure never
     delays a customer request — which is what makes failing hard safe here;
  3. a box that runs it and cannot start it FAILS the deploy. A silent skip is
     how this drifted in the first place.

⚠️ Idiom inherited from `test_console_ladder_deploy_wiring` and
`test_backup_deploy_wiring`: **every assertion reads NON-COMMENT lines.** The
block being tested carries a long explanatory comment that names every string
here. A guard satisfied by prose certifies the documentation rather than the
wiring, which is exactly the defect BO-23 shipped — and which this repo has now
hit six times in source fences.
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_APPLY = _ROOT / "scripts/vps_apply.sh"

_OC_DIR = "workbench/operator_console"
_WORKBENCH_UNIT = "acb-workbench"


def _executable_lines(path: pathlib.Path) -> list[str]:
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def test_the_delivery_path_builds_the_operator_console() -> None:
    """The whole of H-75. Both automated paths — the workflow's SSH step and the
    box's `acb-pull` poller — run `vps_apply.sh`, so the build must live THERE.
    A rebuild somebody runs by hand is what we already had, and it drifted."""
    lines = _executable_lines(_APPLY)
    assert any(_OC_DIR in ln for ln in lines), (
        "vps_apply.sh must build workbench/operator_console — without it, every "
        "operator feature merged to main stays on main"
    )
    assert any("npm run build" in ln for ln in lines), (
        "the operator console is a Next.js app; a restart without a build "
        "serves the previous build forever"
    )


def test_it_restarts_the_unit_it_built() -> None:
    """A build with no restart is the same drift wearing a different hat: the
    new `.next` sits on disk while the running process holds the old one."""
    lines = _executable_lines(_APPLY)
    assert any(
        "systemctl restart" in ln and "OC_UNIT" in ln for ln in lines
    ), "vps_apply.sh must restart the operator console unit after building it"


def test_the_operator_console_comes_after_the_customer_surfaces() -> None:
    """Ordering is what makes failing hard SAFE here.

    The operator console is staff-only. Failing the deploy for it is correct —
    a dead console behind a green deploy is the defect this file exists for —
    but only once customers are already served. Move this block above the
    workbench and a broken staff page starts delaying customer requests.
    """
    lines = _executable_lines(_APPLY)
    workbench_at = max(
        i for i, ln in enumerate(lines)
        if "systemctl restart" in ln and _WORKBENCH_UNIT in ln
    )
    console_at = next(i for i, ln in enumerate(lines) if _OC_DIR in ln)
    assert workbench_at < console_at, (
        "the operator console must be built AFTER the workbench restarts, so a "
        "staff-only failure never delays a customer-facing one"
    )


def test_a_box_that_runs_it_and_cannot_start_it_FAILS_the_deploy() -> None:
    """🔴 The silent skip is the whole bug.

    A box with the unit enabled that then fails to start it must exit non-zero.
    Reporting success while shipping nothing is this repo's most expensive
    recurring failure — four deploys did it once, and the console did it for
    two days.
    """
    lines = _executable_lines(_APPLY)
    joined = "\n".join(lines)
    start = joined.index("OC_UNIT=")
    window = joined[start : start + 1600]
    assert "exit 1" in window, (
        "a failed operator-console start must fail the deploy, not log and "
        "continue"
    )
    assert "journalctl" in window, (
        "print the unit's log on failure — a bare 'FAILED TO START' sends "
        "somebody to SSH for what the deploy already knew"
    )


def test_a_box_that_does_NOT_run_it_skips_cleanly() -> None:
    """The other half. Not every box runs the operator console, and a deploy
    that failed on its absence would block the customer plane for a staff tool
    that was never meant to be there."""
    lines = _executable_lines(_APPLY)
    assert any(
        "is-enabled" in ln and "OC_UNIT" in ln for ln in lines
    ), "guard the block on the unit being enabled, as the Console block does"


def test_the_unit_name_is_overridable() -> None:
    """⚠️ The unit file is NOT in this repo — the console was stood up by hand,
    so unlike every other service here there is no `deploy/hostinger/*.service`
    to copy and no single name we can assert. Until that gap closes (H-75), the
    name must be settable rather than guessed."""
    lines = _executable_lines(_APPLY)
    assert any(
        "OPERATOR_CONSOLE_UNIT" in ln for ln in lines
    ), "allow the box to name its own unit while the unit file lives off-repo"
