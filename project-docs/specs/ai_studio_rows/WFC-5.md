# WFC-5

Tier: STD, and HIGH for the run-time part

Gate: AGENT-SAFE. After WFC-1 and WFC-3. The run-time part waits for the owner

## What

**Retire the legacy agent step (task to file).** Two parts. The STD part: an "Upgrade to Ask Mira" action rewrites the DRAFT node only, and the author then publishes a new version. Publish refuses a NEW legacy node. A published version is never rewritten. The mapping: `crm-assistant` to the `crm-records` pack, `projects-assistant` and `task-manager` to the Projects packs, `email-assistant` to the email pack, `orchestrator` to no pack. `apis-config`, `app-builder` and `whatsapp-assistant` have no match. The HIGH part: the run fails closed on a retired agent, with the words "This step used an assistant that Metorite retired. Replace it with Ask Mira." The run-time retirement needs the owner's OK

## Done when

A draft upgrade keeps the other nodes and edges. A published run of a mapped agent is unchanged until the owner confirms the run-time part
