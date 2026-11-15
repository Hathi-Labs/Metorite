# J2b-4b

Tier: STD

Gate: UI, D145 preview first. After J2b-4a

## What

🔨 **BUILT, dark (2026-11-15, task 3377). Owner preview and four captures are left.** **A real approvals screen** at `/studio/approvals`. It is a sub-page of Studio under the `studio` gate. `?pkg=<uuid>&v=<n>` opens one pending version.

The holder reads the app name, the version, who asked and when. A plain summary lists objects and fields, actions, connections and pages. The raw manifest sits in a folded block, as plain text. The block keeps the first 24,000 characters (the cap of the chat tools). It says "This shows the start of the manifest" when it cuts. No manifest text is HTML or markdown.

Approve and Reject ask through `ConfirmDialog`. They call the J2b-1 routes through the `/api/studio` proxy (`lib/studio/approvalsApi.ts`). Neither route takes a body, so a reject has no reason. The owner and the publisher see no button and read one sentence. A member without `studio:publish` reads the sentence of the 403.

The Needs-you row of J2b-4a now opens `/studio/approvals?pkg=<id>&v=<n>`. No migration, no flag, no nav entry. Fences: `approvalsModel.test.ts`, `approvalsApi.test.ts`, `test_shell_needs.py` and `test_shell_needs_r8.py`

## Done when

A holder opens the row and lands on the pending version
