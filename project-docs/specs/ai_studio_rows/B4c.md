# B4c

Tier: HIGH

Gate: AGENT-SAFE. After B4b and OPM-6

## What

**The MRP run as a logic module** (K4, OPM-6, task 2834). The first real module. It reads the demand, the BOMs and the stock as the caller, and proposes the work orders and the suggestions to buy. It registers in `MODULES`, and its org list stays under `STUDIO_LOGIC_ORGS`. It needs B4b for a run that no person starts, and it needs a decision on the cap of 50 writes per batch (B4a), because a run can propose more.

## Done when

An MRP run on sample data proposes the writes of the spec of K4. It applies all or none. A caller who cannot read a BOM gets no line from it
