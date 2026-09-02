"""A dead Console DSN must not take the whole deploy down in silence.

🔴 **The defect this pins cost hours and every signal stayed green.** Measured
2026-09-02 (H-100): the owner deleted the Console Supabase project during the
Mumbai migration, and `apps/services/customer_console/.env` still named it.

`vps_apply.sh` asked only whether the DSN was **present**. A DSN that is
present and dead fell into the "apply the ladder" branch, `psql` failed under
``set -e``, and the failure took the rest of the file with it. The workbench
rebuild is ~360 lines BELOW that call, so the box kept serving old code from a
stack where all four units read ``active``.

Nothing reported it. `vps-health.yml` probed the public URLs and the app
answered on the OLD build, so the probe passed. Only `acb-pull.service` knew,
and nothing read it.

Two properties keep it fixed, and they are NOT interchangeable:

* **the apply probes reachability BEFORE the ladder**, and treats
  "present but unreachable" as its own case — failing loudly when the Console
  is enabled, and skipping loudly when it is not, so a dead Console DSN cannot
  abort a tenant deploy
* **`vps-health.yml` compares the SERVING commit against `main`** — a delivery
  check. Every other job there asks whether the machinery answers, and an app
  running yesterday's code answers that perfectly.

⚠️ **Ship the apply fix without the health job and the next silent stall is
still invisible**, because the apply only speaks while a deploy is running.
The health job is what watches BETWEEN deploys. That gap is the reason this
shape has now cost time four separate times: H-89, the Operator Console drift
of 2026-08-28, the `.venv` ownership failure of 2026-08-26, and H-100.

⚠️ Idiom inherited from `test_deploy_next_build_swap`: **every assertion reads
NON-COMMENT lines.** The block under test carries a long comment naming every
string here, and a guard satisfied by prose certifies the documentation rather
than the wiring.
"""

from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_APPLY = _ROOT / "scripts/vps_apply.sh"
_HEALTH = _ROOT / ".github/workflows/vps-health.yml"


def _executable_lines(path: pathlib.Path) -> list[str]:
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


class TestTheApplyProbesTheConsoleDsn:
    def test_a_reachability_probe_exists(self) -> None:
        """Presence is not reachability, and the old code only asked presence."""
        body = "\n".join(_executable_lines(_APPLY))
        assert "cc_reachable" in body, (
            "vps_apply.sh must probe the Console DSN before running the ladder. "
            "Without it, a present-but-dead DSN kills the deploy under set -e."
        )

    def test_the_probe_bounds_its_connect_time(self) -> None:
        """An unbounded probe replaces a fast failure with a hung deploy."""
        body = "\n".join(_executable_lines(_APPLY))
        assert "PGCONNECT_TIMEOUT" in body, "the Console DSN probe must bound its connect time"

    def test_the_probe_never_leaks_the_dsn(self) -> None:
        """🔴 `psql` quotes the WHOLE connection string, password included, when
        it cannot connect. That is how a tenant credential reached a transcript
        on 2026-09-02 (H-97). The probe must read the exit code and nothing else.
        """
        lines = _executable_lines(_APPLY)
        probe = [ln for ln in lines if "psql" in ln and "PGCONNECT_TIMEOUT" in ln]
        assert probe, "expected a bounded psql probe line"
        for ln in probe:
            # Two spellings discard stderr, and both are correct:
            #   `2>/dev/null`        stderr straight to the bit bucket
            #   `>/dev/null 2>&1`    stdout to the bit bucket, stderr after it
            # ⚠️ `2>&1` ALONE is not a discard — it merges stderr into stdout,
            # which is the log. That is the spelling this must reject.
            stderr_discarded = "2>/dev/null" in ln or (">/dev/null" in ln and "2>&1" in ln)
            assert stderr_discarded, (
                f"the probe must discard stderr, or it prints the password: {ln!r}"
            )
            assert ">/dev/null" in ln, f"the probe must not print its output: {ln!r}"

    def test_an_unreachable_dsn_does_not_abort_a_box_without_the_console(
        self,
    ) -> None:
        """The heart of H-100.

        A box that does not serve the Console has no reason to fail its TENANT
        deploy because a Console database is gone. The branch must continue.
        """
        body = "\n".join(_executable_lines(_APPLY))
        assert "is-enabled --quiet acb-customer-console" in body, (
            "the unreachable case must branch on whether the Console is enabled"
        )


class TestTheHealthWorkflowChecksDelivery:
    def test_a_delivery_job_exists(self) -> None:
        """Reachability is not delivery. Nothing asked the second question."""
        body = "\n".join(_executable_lines(_HEALTH))
        assert "delivery:" in body, (
            "vps-health.yml must carry a job that compares the serving commit "
            "against main. Probing the URL passes on yesterday's code."
        )

    def test_the_delivery_job_reads_the_version_endpoint(self) -> None:
        """`GET /version` has reported the serving SHA since 2026-08-25, and
        nothing watched it between deploys."""
        body = "\n".join(_executable_lines(_HEALTH))
        assert "/version" in body, "the delivery check must read /version"

    def test_the_delivery_job_needs_full_history(self) -> None:
        """A shallow clone cannot answer `merge-base --is-ancestor`, and the
        job would then call every lagging deploy a force-push.
        """
        body = "\n".join(_executable_lines(_HEALTH))
        assert "fetch-depth: 0" in body, "the delivery job needs full history to compare commits"
