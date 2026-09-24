/**
 * The Inbox capture row at 768px and 1024px (2026-09-24).
 *
 * On one non-wrapping row, the title, the capture box, the attachments, Mind
 * sweep and the shortcuts button did not fit at 768px, and the box shrank to
 * zero width. The row now wraps, and below `xl` the capture box takes its own
 * full-width row. `vitest.config.ts` runs in `node`, with no DOM, so this
 * reads the classes. The screenshots in the PR are the check by eye.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const src = readFileSync(
  join(fileURLToPath(new URL(".", import.meta.url)), "InboxView.tsx"),
  "utf8",
).replace(/\r\n/g, "\n");

/** The desktop capture header, from its wrapper to the shortcut legend. */
const header = (() => {
  const start = src.indexOf('<div className="hidden shrink-0 border-b border-border bg-card sm:block">');
  const stop = src.indexOf("{showShortcuts && (", start);
  expect(start).toBeGreaterThan(-1);
  expect(stop).toBeGreaterThan(start);
  return src.slice(start, stop);
})();

const classesOf = (marker: string) => {
  const at = header.indexOf(marker);
  expect(at, marker).toBeGreaterThan(-1);
  const open = header.lastIndexOf('className="', at);
  return header.slice(open + 11, header.indexOf('"', open + 11)).split(/\s+/);
};

describe("the Inbox capture row", () => {
  it("wraps, as Projects' action row does", () => {
    expect(header).toMatch(/<div className="flex flex-wrap items-center gap-2\.5 px-4 py-2\.5">/);
  });

  it("gives the capture box a full row of its own below xl, and shares the row at xl", () => {
    const box = classesOf('<AppIcon name="Plus"');
    for (const c of ["order-last", "basis-full", "flex-1", "min-w-0", "xl:order-none", "xl:basis-0"]) {
      expect(box, c).toContain(c);
    }
  });

  it("keeps the tools on the title row, at its right end", () => {
    const tools = classesOf("<AttachmentComposer");
    expect(tools).toContain("ml-auto");
    expect(tools).toContain("xl:ml-0");
  });
});
