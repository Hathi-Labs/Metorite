/**
 * STU-C4b fence: the Activity tab draws the model, reads through the door only, and builds no control.
 *
 * Mutation 3: read the record directly (a `fetch`, or the change table) instead of the door op.
 *   "reads through the door op only" turns red.
 * Mutation 2: drop the `next` cursor of Load more. "sends the cursor" turns red.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import StudioActivity, { ActivityEntry } from "@/components/studio/StudioActivity";
import type { StudioDataDoor } from "@/lib/studio/dataDoor";
import { parseHistory } from "@/lib/studio/historyFeed";
import type { ObjectRow } from "@/lib/studio/realDoor";

const read = (p: string) => readFileSync(resolve(__dirname, p), "utf-8").replace(/\r\n/g, "\n");
const src = read("./StudioActivity.tsx");
const record = read("./StudioRecord.tsx");

const OBJECT: ObjectRow = {
  id: "o1", name: "visit", label: "Visit", rules: [],
  fields: [{ key: "title", type: "text", label: "Title", required: false, many: false, options: null, to: null, hot: false }],
};
const door: StudioDataDoor = { scope: "p:live", read: async () => [], run: async () => ({ ok: true }) };
const draw = (declared = true) =>
  renderToStaticMarkup(createElement(StudioActivity, { object: OBJECT, recordId: "r1", door, declared }));

describe("StudioActivity", () => {
  it("reads through the door op only, never the record or the change table", () => {
    expect(src).toContain("door.read(");
    expect(src).toContain("historySource(");
    expect(src).not.toMatch(/fetch\(|XMLHttpRequest|studio_record_changes|\.get"|\.list"/);
    expect(src).not.toContain("door.run(");
  });
  it("sends the cursor of the page when it loads more", () => {
    expect(src).toContain("historySource(object.name, recordId, page.next)");
    expect(src).toContain("page.next !== null");
  });
  it("draws the loading state first, with no control", () => {
    const html = draw();
    expect(html).toContain('aria-label="Loading changes"');
    expect(html).not.toContain("Load more");
  });
  it("says that the app declares no history when it does not", () => {
    expect(draw(false)).toContain("does not declare the history of Visit");
  });
  it("draws a change as a list item with a time element, and no from for a boundary change", () => {
    const page = parseHistory({
      history: [
        { id: 1, op: "create", actor: "ann@acme.test", via_kind: null, via_id: null, changes: { title: { new: "A" } }, at: "2026-11-15T10:05:00Z" },
        { id: 2, op: "update", actor: null, via_kind: "workflow_run", via_id: "w", changes: { title: { old: "A", new: "B" } }, at: "2026-11-15T11:00:00Z" },
      ], next: 2,
    }, [{ key: "title", type: "text", label: "Title" }])!;
    const one = renderToStaticMarkup(createElement(ActivityEntry, { entry: page.entries[0] }));
    expect(one).toContain('<time dateTime="2026-11-15T10:05:00Z"');
    expect(one).toContain("Created");
    expect(one).toContain("ann@acme.test");
    expect(one).not.toContain("→");
    const two = renderToStaticMarkup(createElement(ActivityEntry, { entry: page.entries[1] }));
    expect(two).toContain("a workflow run");
    expect(two).toContain("→");
    expect(two).toContain('data-key="title"');
  });
  it("is a tab of the record page, only when the history is declared", () => {
    expect(record).toContain('from "@/components/studio/StudioActivity"');
    expect(record).toContain("recordTabs(object, objects, historyDeclared)");
    expect(record).toContain('active === "activity"');
  });
  it("uses the shared states and button, a list, and no hand-rolled control or colour", () => {
    expect(src).toContain('from "@/components/studio/StudioBlockState"');
    expect(src).toContain('from "@/components/ui/Button"');
    expect(src).toContain("<ol");
    expect(src).not.toMatch(/<(button|select|input|textarea)[\s>]/);
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b|bg-(sky|red|green|blue)-|text-white/);
  });
});
