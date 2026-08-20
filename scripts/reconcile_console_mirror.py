"""CP-2e — the signup Console-mirror reconciler CLI (WS-31).

Spec: project-docs/specs/customer_console.md §6 CP-2e.

Runs ONE out-of-band pass of `acb_auth.console_resolve.reconcile`, which finds
tenant orgs whose Customer Console mirror is missing (a transient Console failure
during signup left them live on the tenant plane but un-metered) and re-drives
the mirror through the ONE existing seam `provision_org_on_console`
(idempotent-on-slug, create-only guarded), stamping `console_mirrored_at` on
success. Cadence is the operator's — this is a single idempotent pass, not a
scheduler; it is wired into nothing.

⚠️ **A DIFFERENT job from `scripts/reconciler.py`** — that is the CRM
source-of-truth diff / escalation reconciler and is unrelated. This is the signup
Console-mirror closer; the two are never merged.

⚠️ **Ships dark, inert without the owner's acts.** With the box unwired
(`CUSTOMER_CONSOLE_URL` / `CUSTOMER_CONSOLE_DEPLOYMENT_KEY` unset), `reconcile`
is a logged no-op that touches neither the Console nor the tenant DB and prints a
zero summary. It can only DO anything once §8 gate 2 (the Console is deployed)
and gate 8 (a `{provision}`-capable deployment key is wired) are the owner's acts
— running it against a live Console is transitively owner-gated by exactly those.

Run:  uv run python -m scripts.reconcile_console_mirror
"""
from __future__ import annotations

import asyncio
import dataclasses
import json

from acb_auth.console_resolve import reconcile


def main() -> None:
    """Run one reconcile pass and print its summary as JSON."""
    summary = asyncio.run(reconcile())
    print(json.dumps(dataclasses.asdict(summary), indent=2))


if __name__ == "__main__":
    main()
