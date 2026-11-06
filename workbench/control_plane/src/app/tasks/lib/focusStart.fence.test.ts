import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * One start door (CC-4 fix round). Only focusStart.ts calls
 * enterFocusSession, so every start ends the running session, moves a
 * to-do task and hydrates the store. The store declares it, and tests may
 * mention it. Mutation, run by hand: call it from ItemDetail.tsx again and
 * this fails.
 */
function walk(dir: string, out: string[] = []): string[] {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walk(full, out);
    else if (/\.(ts|tsx)$/.test(e.name) && !/\.test\./.test(e.name)) out.push(full);
  }
  return out;
}

describe("the start door", () => {
  it("only focusStart.ts calls enterFocusSession", () => {
    const root = path.resolve(__dirname, "../../..");
    const callers = walk(root)
      .filter((f) => /\.enterFocusSession\(|[^.\w]enterFocusSession\(/.test(fs.readFileSync(f, "utf-8")))
      .map((f) => path.relative(root, f).replace(/\\/g, "/"))
      .filter((f) => f !== "app/tasks/lib/focusStart.ts");
    expect(callers).toEqual([]);
  });
});
