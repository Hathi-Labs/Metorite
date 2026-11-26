# AF-8e

Tier: STD

Gate: AGENT-SAFE. After AF-8a. **Status: BUILT, dark (2026-11-14, task 3323).** No UI, no migration and no flag

## What

**Mira does each tab and cell act of the Pages tab** (task 3323, follow-up of AF-8a). Rung 4. (1) A fix: the cap of cells is for each list (the flat `cells` or the `cells` of each tab), so the key is now `cells_per_list`, and it still reads `MAX_CELLS_PER_PAGE`. A rule sentence says so. (2) The `frame` part gets `steps`: six acts with one example `ops` list each. `studio_patch` has no `move`, so each act is more than one op, and a cell or a tab is copied by value. The acts are: tabs on a flat page, back to a flat page, move a tab, move a cell in its list, move a cell to another tab, and add a tab. Three rules name what a model can break: one cell cap for each list, a unique tab id, and a page never empty (a page that loses its last cell or tab needs the other list). **Non-goals:** a `move` op, a tab or cell form in chat

## Done when

`tests/unit/test_studio_frame_tools.py`: each step example is applied through `studio_patch` and `validate_manifest` returns `[]`, each example makes the shape it names, the cap of tabs and of cells refuses one more while two full tabs pass, a page with tabs and cells both, an empty page, a repeated tab id and a `move` op are refused, and the frame part stays under half of the describe cap. Verify: `uv run pytest tests/unit/test_studio_frame_tools.py tests/unit/test_studio_describe.py`
