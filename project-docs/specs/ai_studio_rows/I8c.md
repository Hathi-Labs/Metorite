# I8c

Tier: STD

Gate: **BUILT, dark, frontend only (task 3216), behind the I8a gate `STUDIO_CONNECTION_GATE_ORGS`. Not merged yet.** AGENT-SAFE. After I8a

## What

**The app setup card** (task 3216). The owner's app page (`/studio/package-preview?pkg=<id>`) draws one card with the I8a lines for each unmet connection name of the draft. The rows come from the I8a route only, never from a claim of the package. The Ask an admin button, the bell row and a viewer of an install are out (I8b)

## Done when

In an org that both flags list, the owner's app page shows one card for an app whose draft names an unmet connection. Connect opens the I8a sheet. After an MCP add, the browser returns to `/studio/package-preview?pkg=<id>` and the line reads connected. A viewer and an org off either flag see no card. Visual review in four contexts
