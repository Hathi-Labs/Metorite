# J3b

Tier: HIGH

Gate: AGENT-SAFE. **The owner decided on 2026-11-11 (task 1201, the recommended defaults).** The cap is 500 credits for each run. That is the per-run ceiling of the Console (WS-31). A build is one chat session that edits one draft. The cap stays inert until `CUSTOMER_CONSOLE_SPEND_GATE` is on. A cap flip is gate `enforcement-flip`. **J3b-1 (metering fence) is built in this PR.** `test_studio_model_calls_metered.py` fails when a Studio model call skips the metered seam. **J3b-2 is not built.** It adds the run key, the build cap and the clear message. It waits for the spec edits

## What

**The credit caps.** A cap for each build and each run, and a `usage_event` row for each model call (§9 item 6)

## Done when

A build stops at its cap. Each model call writes one `usage_event` row
