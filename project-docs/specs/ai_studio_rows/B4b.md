# B4b

Tier: HIGH

Gate: AGENT-SAFE. After the decision

## What

**The card for a logic batch** (task to open). A logic action is class B or C, so a call that is not direct needs the member's card. B4a refuses such a call with `approval_required` and builds no card. **Needs a decision first.** (1) One card for the whole batch, or one card for each write. (2) What the store keeps, so that a replay after the approval cannot give other writes. **Proposal: store the proposed writes, and not the input.** The module runs once, at the ask. The card shows the writes (ids and the changed keys), and the spend applies the stored writes in one batch. A module that reads live data would give other writes at a second run. The spend must also check each write again as the member of the card (C8a).

## Done when

One approval applies one stored batch, all or none. A second run of the module gives no other write. A decline writes nothing. The card names each object and op
