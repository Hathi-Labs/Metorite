/**
 * The Today title (WS-64 OP-2 fix round 1). `VIEW_META` falls back to Inbox,
 * so a view with no entry is labelled Inbox. Source fence, as the node
 * environment draws no component.
 */
import fs from "node:fs";
import path from "node:path";

import { expect, it } from "vitest";

it("ItemList names the Today view", () => {
  const src = fs.readFileSync(path.join(__dirname, "ItemList.tsx"), "utf8");
  expect(src).toMatch(/\btoday: \{ title: "Today"/);
});

it("the /tasks page maps a leaked Today view on mount", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "page.tsx"), "utf8");
  expect(src).toContain("tasksEntryView(");
});
