import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

/**
 * Task 3587. The phone drawer keeps the node that `open` was given. So the
 * open Menu must take new content when the list it draws changes. Without
 * that, a tap while the first layout read was out kept the skeleton until the
 * drawer closed, and `e2e/account-switcher.spec.ts` failed on a slow runner.
 *
 * The browser fence is `e2e/shell-presets.spec.ts`, "a tap before the layout
 * read answers". This source fence runs in the Frontend check as well, which
 * blocks a merge.
 */
const shell = readFileSync(new URL("./AppShell.tsx", import.meta.url), { encoding: "utf-8" })
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/^\s*\/\/.*$/gm, "");

describe("the open Menu drawer takes new content (task 3587)", () => {
  it("re-injects the menu while it is open, when the skeleton or the list changes", () => {
    const key = shell.match(/const menuShape = `([^`]*)`;/);
    expect(key, "the key of what the open drawer draws").not.toBeNull();
    expect(key?.[1]).toContain("${navWaiting}");
    expect(key?.[1]).toContain("${drawerKey}");
    expect(shell).toMatch(
      /useEffect\(\(\) => \{\s*if \(menuOpen\) open\(menuRef\.current, "menu"\);\s*\}, \[menuShape, menuOpen, open\]\);/,
    );
  });

  it("the ref holds the content of the current render", () => {
    expect(shell).toMatch(/menuRef\.current = menuContent;/);
  });
});
