/**
 * Email plugs into the shell's command bar and builds no palette of its own
 * (D87 to D89, task #914). The shell takes ⌘K in the capture phase, so an
 * app listener never ran. Source and pure-function checks, no DOM.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { NAV_SECTIONS } from "@/lib/nav";
import { JOBS, buildItems, heldPanes } from "@/lib/shell/registry";

const DIR = __dirname;

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) return sources(p);
    return /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n) ? [p] : [];
  });
}

describe("Email and the shell command bar", () => {
  it("declares its compose job for the shell, on the /email pane", () => {
    const pane = NAV_SECTIONS.flatMap((s) => s.items).find((p) => p.href === "/email");
    expect(pane?.launch).toBe("live");
    const mine = JOBS.filter((j) => j.app === "/email");
    expect(mine.map((j) => j.id)).toContain("compose");
    const items = buildItems(heldPanes(NAV_SECTIONS.filter((s) => s.items.length)), false, []);
    expect(items.some((i) => i.key === "do:compose" && i.href.startsWith("/email"))).toBe(true);
  });

  it("holds no CommandPalette and no own ⌘K listener", () => {
    const files = sources(DIR);
    expect(files.some((f) => /CommandPalette/.test(f))).toBe(false);
    const offenders = files.filter((f) => {
      const src = readFileSync(f, "utf-8");
      return /CommandPalette|Command palette|\(e\.metaKey \|\| e\.ctrlKey\) && e\.key\.toLowerCase\(\) === "k"/.test(src);
    });
    expect(offenders).toEqual([]);
  });

  it("uses the shared isTypingTarget in page.tsx", () => {
    const src = readFileSync(join(DIR, "page.tsx"), "utf-8");
    expect(src).toContain('from "@/lib/keyboard"');
    expect(src).not.toMatch(/tagName === "INPUT"/);
  });
});
