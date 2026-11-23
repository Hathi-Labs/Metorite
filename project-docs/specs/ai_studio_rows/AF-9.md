# AF-9

Tier: LOW

Gate: AGENT-SAFE. After AF-3. **AF-9a BUILT, dark (2026-11-14, task 3157):** Field visits, Support desk and Purchase requests declare two `pages` (All, Board) and a one-section `nav` (a List row and a Kanban row). Fence: `test_studio_templates.py` (`FRAMED`). **AF-9b BUILT, dark (2026-11-14, task 3325):** Each of the three templates now declares five pages and five rail rows. The order is Dashboard, All, My, Open and Board. My and Open carry `count: true`. My filters the person field (`engineer`, `assignee`, `requester`) with `eq` and the `me` token. Open filters `status` with `ne` the closed value (`done`, `solved`), or with `in` the open stages for Purchase requests. The Dashboard holds one `stat` (open count) and one `donut` `chart` by status. Both read an `object:<name>.aggregate` action, and `status` is now `hot` for the group. Fences: `test_studio_templates.py` (`test_af9b_*`) and `readyAppRails.test.ts`. **Left:** the `record` key (record tabs) waits on PR 2299 and AF-5b. The tabs are the defaults of §5.5 rule 3 until then. The visual review of one template beside the CRM is not done.

## What

**The templates get a frame** (task 3157). Rung 4. The K1 templates declare a rail, record tabs and views. Field visits gets Dashboard, All visits, My visits, Open visits with a count, and Board. The other two templates get the same shape

## Done when

Each template installs and opens with its rail. The K1 template tests pass. Visual review of one template beside the CRM
