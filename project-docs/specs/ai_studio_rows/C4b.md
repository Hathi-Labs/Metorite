# C4b

Tier: STD

Gate: AGENT-SAFE. After C4a-2

## What

🔨 **BUILT, dark (2026-11-15, task 3086).** **This is ONE slice with D22a (task 2366): the changes-only Activity tab of a record, and its chat twin.** It adds no migration, no flag and no gateway route. It uses the app frame flag that the record page already has (`NEXT_PUBLIC_STUDIO_APP_FRAME`).

**The door.** `realDoor.ts` admits `object:<name>.history` in `READ_SOURCE`. So `door.read` and `readActions` (a viewer) carry it. `historyBody` builds the body from `id`, `after` and `limit` only, and it always sets a `limit`.

The door refuses a `read` marker for a history, so the body cannot carry that guard. A delete takes exactly `{id}`. So a body with a `limit` never matches the keys of a write.

The answer is one row, `{history, next}`.

**The model.** `lib/studio/historyFeed.ts` is pure. It turns the answer into lines. It draws only the keys that are in `changes`, and it never fills a key from the record. So a field that C8 hides shows no line.

**The values.** A boundary change with no `old` key shows the new value and no "from". Each value goes through `displayValue`, the one formatter of the Table and the Detail block. So money, dates and selects read as they read there. A value never shows as raw JSON, and a long value is cut at 160 characters.

**The actor.** The line shows the address that the door gives. With no address, it says "a workflow run" or "an agent" by `via_kind`, and else "Someone". A viewer of an install reads no writer. `joinPages` keeps the pages in order and shows no id twice.

**The order.** The tab keeps the order of the door: **oldest first**. "Load more" brings the later changes, and `next` is the `after` of the next page.

The first draft of this row said newest first. The door pages by `id > after`, so it cannot start at the end. The section says "Oldest first". A newest-first list needs a direction in the door, and nobody built that.

**The tab.** `components/studio/StudioActivity.tsx` draws the model with `StudioBlockState`: loading, empty ("No changes yet.") and error with Retry. It also draws an ordered list, one `time` element for each change, and a "Load more" `Button` when `next` exists. A failed later page keeps the pages that loaded and shows a line of its own.

**The tab list.** `recordTabs` adds Activity as the last tab when the app declares `object:<name>.history`. It drops the tab when the app does not, also from a `record.tabs` list. The tab reads through `door.read` only. It never reads the record, the change table or a run.

**The Detail block.** It has no history section. The Details tab of the record page IS the Detail block, and Activity is its own tab beside it. So one component lives in one place, and a copy in the Detail block would be a second implementation.

**The chat twin.** It is a MODE of `studio_record_list`: send `history_of` (a record id) and, for the next page, `next`. The mode posts the same door op to the declared action, as the member. It returns the same kind of list, oldest first, in an untrusted frame, with `next`. It sits under the key `history`, and not `records`. The door applies the redaction. So the mode adds none, and it never fills a key.

**Why a mode.** A Studio turn with email words holds exactly 110 tools, the cap of `MAX_REQUEST_TOOLS`. One more tool in `studio-more` takes that turn to 111, and the loader then drops the email pack in four cases of the golden matrix. E4b made the same choice for the workflow read. So `studio-more` keeps its 13 tools, `studio_record_list` keeps its parity row, and the studio debt ceiling stays 3.

**The limits.** A page over 12 000 characters is cut, and `next` then names the last change that is shown. `studio_describe` now lists `history` in `declare_yourself`. So a builder declares the action. ⚠️ Open for the owner: a separate tool named `studio_record_history` needs a place in a pack. That needs a decision on the cap.

**Verify.** In `workbench/control_plane`, run `npx vitest run src/lib/studio/historyFeed.test.ts src/components/studio/StudioActivity.test.ts src/lib/studio/realDoor.test.ts src/lib/studio/recordPageModel.test.ts src/components/studio/StudioRecord.test.ts`, then `npx vitest run src/lib/theme/`. Then run `uv run pytest tests/unit/test_studio_describe.py tests/unit/test_studio_record_history_tool.py tests/unit/test_studio_record_tools.py tests/unit/test_chat_parity.py tests/unit/test_studio_split_matrix.py`.

**Not seen yet.** Nobody saw the tab in a browser. Lane-ui takes the captures in light mode, at compact density, under a changed accent, and at 390 px.

## Done when

A member opens a record and sees each change in order, with the actor, the time and the old and new value. A field that C8 hides shows no line. The same list comes back from the chat tool
