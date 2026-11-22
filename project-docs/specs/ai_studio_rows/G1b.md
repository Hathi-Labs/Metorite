# G1b

Tier: STD

Gate: AGENT-SAFE. Stage 2

## What

🔨 **BUILT, dark (2026-11-01).** `studio_describe(part)` in the `studio` pack: the live JSON Schema of the manifest, the field types of `studio_fields` and the block catalog, so Mira reads them before she drafts (§11.1). Stateless and read-only, like G1

## Done when

**Built:** `part` is `manifest`, `fields` or `blocks`. A bad part gives `ok: false` and the legal parts. The tool reads `Manifest.model_json_schema()`, `studio_fields` and `BLOCKS` live and copies none. It adds no per-block prop docs. The pack now holds eight tools. The schema it returns is the one A2 checks against, from the same source. A test fails when the two differ. Each result stays under G1's cap
