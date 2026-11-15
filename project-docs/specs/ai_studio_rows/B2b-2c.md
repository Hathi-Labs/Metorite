# B2b-2c

Tier: HIGH

Gate: AGENT-SAFE. After B2b-2b. The flag `STUDIO_DOOR_OPS_ORGS` stays the owner's (`enforcement-flip`)

## What

🔨 **BUILT, dark (2026-11-13, task 2968).** **The chat tool of a door op.** B2b-2b built the dark server half. The tool `studio_app_read(package_id, action, args)` in `acb_skills/studio_tools.py` is its consumer. It reads the package, finds the declared action, and checks that the `uses` is a class A GET row of `app_manifests.door_op`. Then it sends `POST /studio/apps/{id}/actions/{action}` as the member, with `args` as the body. The 200 body goes through `door_value` of `acb_common.door_values`: each string is untrusted text unless the row names its key in `plain`, and a key that names a secret leaves. The tool keeps no second copy of the rule. A 423 shows the sentence of `PAUSED_TEXT`. `acb_skills` cannot import the gateway, so the tool holds a copy, and a fence tests that the two texts are equal. The client limit is 35 s, above the 30 s of the door. The tool is a read tool of `STUDIO_RECORD_TOOLS`, so it rides the `studio` pack and needs no new line of `TOOL_PACKS`. An answer over 24,000 characters gives a refusal that says the read is too large to show. A 404 of the app, a 422 `bad_args` with its `field`, a 503 and a 403 `source_not_open` each give their own sentence. **Not in this slice:** a door op of class B or C, a write or an approval card, a change of the door, a new flag, a migration and any UI. The row `B2b-3` below owns the integration. ⚠️ The id B2b-3 names that row, so this consumer is B2b-2c

## Done when

`tests/unit/test_studio_app_read.py` (no database): an answer reaches the model wrapped, a `plain` id stays plain, a key with `token` or `secret` in its name leaves, and the tool holds no second copy of the wrap rule. The 423 text equals `invoke.PAUSED_TEXT`, and the client limit is above `door_op.TIMEOUT_S`. R8, not built here: the owner reads a Knowledge page through the tool, and a page in a space with no grant gives the 404 of Knowledge. **Verify:** `uv run pytest tests/unit/test_studio_app_read.py tests/unit/test_studio_record_tools.py tests/unit/test_studio_record_writes.py tests/unit/test_tool_packs_registry.py tests/unit/test_chat_parity.py tests/unit/test_pack_requests.py tests/unit/test_door_values_single_copy.py -q`
