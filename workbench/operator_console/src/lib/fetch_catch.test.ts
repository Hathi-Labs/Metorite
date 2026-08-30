// Network failure is an answer — WS-31 console review, 2026-08-30.
//
// 🔴 A rejected fetch once left "Activating…" on screen forever: the busy
// flag was set before the await and cleared after it, and the throw skipped
// the clear. Some panels un-stuck their flag with `finally` but said
// NOTHING, which on a money write reads as "did it save?". Every client
// component that awaits fetch() must catch — the operator reads a message
// and the button comes back.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const APP = join(__dirname, "..", "app");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else out.push(p);
  }
  return out;
}

describe("no client fetch without a catch", () => {
  const clientFiles = walk(APP).filter(
    (f) => f.endsWith(".tsx") && !f.includes(".test."),
  );

  it("🔴 every client component that awaits fetch() handles rejection", () => {
    const offenders = clientFiles.filter((f) => {
      const s = readFileSync(f, "utf8");
      return (
        s.includes('"use client"') &&
        /await fetch\(/.test(s) &&
        !s.includes("} catch")
      );
    });
    expect(offenders).toEqual([]);
  });

  it("the last-resort error boundary exists for the server side", () => {
    // Server components re-throw network errors by design; without this
    // file a down Console rendered Next's anonymous 500 on every page.
    expect(statSync(join(APP, "error.tsx")).isFile()).toBe(true);
  });

  it("BFF gate refusals (`error`) are read wherever Console `detail` is", () => {
    // Two refusal shapes exist on purpose: the Console says {detail}, the
    // BFF gate says {error}. A component that reads only one swallows the
    // other's sentence — "sign in again" once degraded to "the Console
    // answered 401" with no way to tell what to do.
    for (const f of [
      join(APP, "operators", "OperatorAdmin.tsx"),
      join(APP, "activity", "ActivityFeed.tsx"),
      join(APP, "login", "InterimForm.tsx"),
      join(APP, "login", "callback", "page.tsx"),
    ]) {
      expect(readFileSync(f, "utf8")).toMatch(
        /body\.detail \?\? body\.error|body\.error \?\? body\.detail/,
      );
    }
  });
});
