# F6b

Tier: HIGH. A model call after each turn spends credits

Gate: AGENT-SAFE to build dark. **OWNER-GATE for the switch-on:** it spends credits, and it joins the open cap decision of F2c (task 1307). Make no second decision. After F6a. No rung yet (Later)

## What

**OPEN (task 3161).** SKL-1, part 2: **the cheap-tier review pass.** After a turn, a review decides skill, automation or none, and the card of F6a shows only on "skill". The input is the tool names and the catalog titles only. It holds no argument and no result, so a stored skill cannot carry customer data or an injected instruction (PZ-33 keeps tool answers out of its prompt for the same reason). The seam is a `studio.` prefix in `decide_features.PREFIXES` with an org list, through `acb_llm.decide` (`tier-decide`). The other choice is a job kind on `gateway/jobs/queue.py`, seeded in `routes/agent.py::_persist_on_complete` beside `_extract_run_memory`, as PZ-33 does. It never runs in `_tool_injection.py`, because that runs before the turn. This spec must still say how a verdict reaches the card after `RUN_FINISHED`. A decide call writes one `usage_event` row through the Console Router

## Done when

Tests use a fake decide and spend no credits. A turn of tool names only gives the verdict. No argument and no result reaches the model
