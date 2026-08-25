"""Golden trajectory: per-run integration-credential scoping (MT-0a).

Locks the SECURITY invariant, not just the mechanics: a credential resolved for
run A must NOT be readable when run B (a different agent / different
integration) starts. This began as B6 Phase-5 Tier 0, which scoped the shared
``os.environ`` bridge with a restore token; MT-0a replaced that bridge with a
per-task ContextVar binding in ``acb_skills.integrations``, because the
process-global env could never close the concurrent-overlap window
(saas_multitenancy.md §6.1).

If a future edit reverts the binding to a process-global store (or drops the
release at any run-teardown site), the accumulation assertion here fails — and
the os.environ assertions catch the specific regression of bridging through
the process env again.

See specs/saas_multitenancy.md (§6.1, MT-0a) and
specs/permissions_sandbox_b6.md (Phase 5, Tier 0) for the history.
"""
from __future__ import annotations

import os

import acb_skills.integrations as integrations


def test_credentials_do_not_accumulate_across_runs(monkeypatch):
    """Simulate a sequence of runs and assert no secret outlives its own run."""
    for v in (
        "ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET",
        "APIFY_API_TOKEN", "INSTANTLY_API_KEY",
    ):
        monkeypatch.delenv(v, raising=False)

    runs = [
        ({"zoho-crm": {"client_id": "A-id", "client_secret": "A-sec"}},
         ["ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET"]),
        ({"apify": {"api_token": "B-apify"}}, ["APIFY_API_TOKEN"]),
        ({"instantly": {"api_key": "C-inst"}}, ["INSTANTLY_API_KEY"]),
    ]

    seen_before: set[str] = set()
    for creds, expected_vars in runs:
        # No prior run's secret is visible as this run begins.
        for var in seen_before:
            assert integrations.credential(var) == "", (
                f"{var} from a prior run leaked into a later run"
            )

        token = integrations.bind_run_credentials(creds)
        # This run's own creds are readable during the run...
        for var in expected_vars:
            assert integrations.credential(var)
            # ...without ever touching the process-global environment.
            assert var not in os.environ, (
                f"{var} was bridged through os.environ — the MT-0a regression"
            )
        seen_before.update(expected_vars)

        # Run teardown (the finally / AsyncExitStack callback in the executor).
        integrations.release_run_credentials(token)

        # Immediately after teardown, none of this run's creds remain.
        for var in expected_vars:
            assert integrations.credential(var) == ""


def test_operator_env_survives_the_run_lifecycle(monkeypatch):
    """A gateway-.env-provided secret wins over, and outlives, any run binding."""
    monkeypatch.setenv("ZOHO_CLIENT_ID", "operator-value")
    monkeypatch.delenv("ZOHO_CLIENT_SECRET", raising=False)

    token = integrations.bind_run_credentials(
        {"zoho-crm": {"client_id": "run-value", "client_secret": "sec"}}
    )
    # Operator's value wins throughout; the run-only var reads from the binding.
    assert integrations.credential("ZOHO_CLIENT_ID") == "operator-value"
    assert integrations.credential("ZOHO_CLIENT_SECRET") == "sec"

    integrations.release_run_credentials(token)
    # ...and still stands after teardown; only the run-scoped var is gone.
    assert integrations.credential("ZOHO_CLIENT_ID") == "operator-value"
    assert integrations.credential("ZOHO_CLIENT_SECRET") == ""
