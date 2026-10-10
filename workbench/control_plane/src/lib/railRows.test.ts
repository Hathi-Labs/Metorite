/**
 * A rail of named items draws its rows with `RailRow` (owner, 2026-10-10).
 *
 * The owner asked for every app with "multiple list spaces" to read as
 * cleanly as ClickUp's sidebar. One primitive does that, and this fence
 * keeps each converted rail on it. A rail that goes back to a hand-rolled
 * row loses the hover reveal, the count and the whole-name tip together,
 * and nothing else in CI would notice.
 *
 * ⚠️ This is a SOURCE fence. It proves a rail renders `RailRow`, not that
 * every row in it does. `e2e/rail-rows.spec.ts` measures the real rows.
 * A new rail of named items joins `RAILS` in the PR that builds it. That
 * half is review, and `DESIGN_SYSTEM.md` §6 says so.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("..", import.meta.url));

/** Each converted rail, and the rows in it that must be `RailRow`s. */
const RAILS: Record<string, string> = {
  "app/projects/components/ProjectTree.tsx": "every space, folder and project row, and the draft row",
  "app/tasks/components/ListsSidebar.tsx": "the views, the Areas and the projects I lead",
  "app/email/components/AccountSidebar.tsx": "the folders and the automation entries",
  "app/whatsapp/page.tsx": "the triage streams and the labels",
  "app/crm/components/CrmRail.tsx": "the views, from Pipeline to Reports, and Pipeline settings",
};

const strip = (text: string) =>
  text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

describe("a rail of named items draws RailRow", () => {
  for (const [file, rows] of Object.entries(RAILS)) {
    it(`${file} — ${rows}`, () => {
      const code = strip(readFileSync(SRC + file, "utf8"));
      expect(code).toMatch(/import RailRow from "@\/components\/ui\/RailRow"/);
      expect(code).toContain("<RailRow");
    });
  }

  it("the Projects tree keeps no row of its own beside it", () => {
    const code = strip(readFileSync(SRC + "app/projects/components/ProjectTree.tsx", "utf8"));
    // The hand-rolled row padded itself by depth. RailRow owns the indent now.
    expect(code).not.toMatch(/paddingLeft:\s*`\$\{depth/);
    expect(code.match(/<RailRow/g)).toHaveLength(2);
  });

  it("the My Tasks rail keeps no count pill of its own", () => {
    const code = strip(readFileSync(SRC + "app/tasks/components/ListsSidebar.tsx", "utf8"));
    expect(code).not.toContain("rounded-full px-1.5 py-0.5 text-center text-[10px] font-semibold");
  });
});
