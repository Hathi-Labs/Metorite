"""The INBOUND Console bootstrap CLI — provision what the registry places here.

Spec: project-docs/specs/customer_console.md §6 CP-2c · saas_multitenancy.md
§11 MT-1j.

Runs ONE out-of-band pass of `acb_auth.console_resolve.bootstrap_placed_orgs`,
which asks the Customer Console which organizations are PLACED on this
deployment and provisions, on the TENANT plane, every one this box has no local
`organization` row for.

⚠️ **The defect it repairs.** `POST /orgs/provision`'s OPERATOR arm writes the
Console plane only — org, placement, seats, owner membership, trial. Nothing
ever wrote the tenant plane for it (`provision_local_organization`'s one
production caller is the self-serve signup route). So a customer an operator
created through the Operator Console could not use the product: the owner signed
in, the registry admitted them, `/me/access` read the tenant plane and found no
organization, and they landed on *"No organization is linked to this email"* —
while the Operator Console's success panel told the operator they could sign in
with no invite needed.

⚠️ **A DIFFERENT job from `scripts/reconcile_console_mirror.py`, and its exact
mirror image.** That one PUSHES tenant-born organizations up to the Console
(a transient failure during self-serve signup left them un-metered). This one
PULLS Console-born organizations down to the tenant. Between them the two planes
converge from either direction. Neither is `scripts/reconciler.py`, which is the
CRM job and unrelated to both.

⚠️ **This CLI is the MANUAL path; the gateway runs the same pass on a timer.**
`CONSOLE_BOOTSTRAP_ENABLED=true` starts `start_console_bootstrap` from the
gateway lifespan, every `CONSOLE_BOOTSTRAP_INTERVAL_SECONDS` (default 60). Use
this to run one pass now — after creating a customer, or to see the counts.

⚠️ **Ships dark, inert without the owner's acts.** With the box unwired
(`CUSTOMER_CONSOLE_URL` / `CUSTOMER_CONSOLE_DEPLOYMENT_KEY` unset) the pass is a
logged no-op that touches neither plane and prints a zero summary. It writes
NOTHING to the Console in any case — it can only ever create the local half of a
customer the Console already placed here.

Run:  uv run python -m scripts.bootstrap_placed_orgs
"""
from __future__ import annotations

import asyncio
import dataclasses
import json

from acb_auth.console_resolve import bootstrap_placed_orgs


def main() -> None:
    """Run one bootstrap pass and print its summary as JSON."""
    summary = asyncio.run(bootstrap_placed_orgs())
    print(json.dumps(dataclasses.asdict(summary), indent=2))


if __name__ == "__main__":
    main()
