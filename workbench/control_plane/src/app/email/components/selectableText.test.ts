// Task 3392 (owner report 2026-11-15): "I cannot select text from an email."
// The email page root is `select-none` to keep stray selection out of the
// chrome. The reader must opt text back in with `select-text`. The runner has
// no DOM, so this reads the source. `e2e/email-select-text.spec.ts` selects
// text in a real browser.
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const read = (f: string) => readFileSync(join(__dirname, f), "utf-8");

describe("the reader keeps its text selectable", () => {
  it("the page root stays select-none", () => {
    expect(read("../page.tsx")).toContain("overflow-clip select-none");
  });

  it("the body wrapper of every mail carries select-text", () => {
    expect(read("MessageContent.tsx")).toMatch(/data-email-body=""\s+className="select-text"/);
  });

  it("the subject and the sender block carry select-text", () => {
    const src = read("EmailDetail.tsx");
    expect(src).toMatch(/<h2 className="flex-1 select-text /);
    expect(src).toMatch(/<div className="flex-1 min-w-0 select-text">/);
  });

  it("the recipients of a thread card carry select-text", () => {
    expect(read("ConversationView.tsx")).toContain("min-w-0 select-text space-y-0.5");
  });
});
