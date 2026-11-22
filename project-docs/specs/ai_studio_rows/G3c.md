# G3c

Tier: STD

Gate: AGENT-SAFE

## What

🔨 **BUILT, dark (2026-11-07, task 2208).** Direct edits in the Data, Views and Code tabs, saved through the draft route (`PUT /studio/packages/{id}/draft`, with `based_on`). REUSE MAP: the Workshop Monaco editor, theme and save key are now `components/CodeEditor.tsx`, shared by the Workshop page and the IDE. The unsaved mark is `components/UnsavedMark.tsx`, shared too. The Studio `useLeaveGuard` guards the leave. The draft route adds the record actions of each object, as `studio_save` does

## Done when

`draftEdit.test.ts`, `draftEditor.test.ts` and `build/page.test.ts` pass. `uv run pytest tests/unit/test_studio_package_routes.py -q -o timeout_method=thread` passes
