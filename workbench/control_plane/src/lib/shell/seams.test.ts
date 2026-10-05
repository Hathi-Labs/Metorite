/**
 * An app plugs into the shell. It never builds one.
 *
 * Rule 10 of `workbench/control_plane/AGENTS.md`, from decision D89 (owner
 * directive, 2026-10-05): "Future applications … should also follow the same
 * UI/UX rules." The owning spec is `project-docs/specs/navigation_shell.md`
 * §5.2.
 *
 * **Why a test and not a paragraph.** Every gap in today's shell is a place
 * where one app solved a shell problem alone. Projects built a palette and a
 * bell. Email built a second palette. Three apps built three assistant rails.
 * None of them was wrong, because the shell offered no seam. Rule 10 says so
 * in prose, and prose binds nobody (R7). This file makes the fourth copy fail.
 *
 * **What it fences.** Four shell parts that an app must not build for itself:
 *
 *   1. a ⌘K listener
 *   2. a mount of `NotificationBell`
 *   3. an assistant rail, that is, a mount of the shared `<AgentChat>`
 *   4. a mount of a search or command palette
 *
 * **How.** The same ratchet as `lib/theme/conformance.test.ts`. Today's sites
 * sit in `SEAM_DEBT`, by file, with a count. A file with no budget must be
 * clean. A budgeted file may not get worse. A file that got BETTER fails until
 * somebody lowers its number, so the debt can never quietly become fiction.
 * NS-1 to NS-9 of the spec pay the debt down to zero.
 *
 * **Where the shell itself lives.** `src/lib/shell/` and `src/components/shell/`
 * are exempt. That is where NS-1 builds the one listener, the one palette, the
 * one bell and the one dock.
 *
 * ⚠️ **What it cannot see.** An app that draws its own top bar imports nothing
 * a regex can name, so "no own top bar" stays advisory, review-only. A
 * listener spelled a new way also escapes. The patterns below match the three
 * spellings the tree holds today.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("../..", import.meta.url));

function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) {
        walk(full);
      } else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
        out.push(relative(SRC, full).split(sep).join("/"));
      }
    }
  };
  walk(SRC);
  return out.sort();
}

const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");
const count = (text: string, re: RegExp) => (text.match(re) || []).length;

/** The shell's own home. NS-1 builds here, and nothing here is debt. */
const SHELL_HOME = ["lib/shell/", "components/shell/"];
const inShell = (rel: string) => SHELL_HOME.some((p) => rel.startsWith(p));

type Seam = "cmdk" | "bell" | "rail" | "palette";

const KEY_K = String.raw`\.key(?:\.toLowerCase\(\))?\s*===\s*["'][kK]["']`;

/**
 * One pattern per seam.
 *
 * `cmdk` needs a modifier within the same statement, so plain `j`/`k` list
 * navigation does not count. It also counts the shared predicate
 * `isOpenShortcut(`, its definition and each call.
 */
const PATTERNS: Record<Seam, RegExp> = {
  cmdk: new RegExp(
    String.raw`(?:metaKey|ctrlKey)[^;{}]{0,120}?` + KEY_K +
      "|" + KEY_K + String.raw`[^;{}]{0,120}?(?:metaKey|ctrlKey)` +
      String.raw`|\bisOpenShortcut\(`,
    "g",
  ),
  bell: /^import[^;]*\bNotificationBell\b/gm,
  rail: /^\s*<AgentChat\b/gm,
  palette: /<(?:SearchPalette|CommandPalette)\b/g,
};

/**
 * Sites that are not debt, each with its argument.
 *
 * `/chat` is the one full chat page. The spec keeps it (§7.1), and the dock
 * shares its sessions. It is a destination, not a rail inside another app.
 */
const SEAM_EXCEPTIONS: Partial<Record<Seam, string[]>> = {
  rail: ["app/chat/page.tsx"],
};

/**
 * Today's debt, measured 2026-10-05. Each number may only go DOWN.
 *
 * ⚠️ Do NOT add a file here to make a new app pass. A new app declares jobs,
 * a search provider, a needs provider and an agent in its manifest instead
 * (`navigation_shell.md` §5). If the shell part it needs does not exist yet,
 * that is NS-1's work. Build it in `src/lib/shell/`, once, for every app.
 */
const SEAM_DEBT: Record<Seam, Record<string, number>> = {
  cmdk: {
    "app/email/page.tsx": 1,
    "app/projects/lib/search.ts": 2,
    "app/projects/page.tsx": 1,
    "app/tasks/page.tsx": 1,
  },
  bell: {
    "app/projects/page.tsx": 1,
    "app/tasks/page.tsx": 1,
  },
  rail: {
    "app/build/apps/[slug]/edit/page.tsx": 1,
    "app/email/components/EmailAssistantChat.tsx": 1,
    "app/projects/components/AssistantRail.tsx": 1,
    "app/tasks/components/AssistantRail.tsx": 1,
  },
  palette: {
    "app/email/page.tsx": 1,
    "app/projects/page.tsx": 1,
    "app/tasks/page.tsx": 1,
  },
};

const SEAMS = Object.keys(PATTERNS) as Seam[];

// One scan per seam per run. Each `it` reads the same answer.
const measured = new Map<Seam, Map<string, number>>();

function actual(seam: Seam): Map<string, number> {
  const hit = measured.get(seam);
  if (hit) return hit;
  const out = new Map<string, number>();
  const skip = new Set(SEAM_EXCEPTIONS[seam] ?? []);
  for (const rel of sourceFiles()) {
    if (inShell(rel) || skip.has(rel)) continue;
    const n = count(read(rel), PATTERNS[seam]);
    if (n > 0) out.set(rel, n);
  }
  measured.set(seam, out);
  return out;
}

const WHY: Record<Seam, string> = {
  cmdk: "Do not add a ⌘K listener to an app. The shell owns the one listener. Declare jobs in the app's manifest instead.",
  bell: "Do not mount NotificationBell in an app. The shell owns the one bell. Declare a needs provider in the app's manifest instead.",
  rail: "Do not mount <AgentChat> as a rail in an app. The shell owns the one dock. Name the app's agent in its manifest instead.",
  palette: "Do not mount a search or command palette in an app. The shell owns the one command bar. Declare jobs and a search provider instead.",
};

describe("an app plugs into the shell and never builds one (D89, AGENTS.md rule 10)", () => {
  it("finds today's sites at all", () => {
    // A pattern that matches nothing makes every assertion below pass
    // vacuously. Each seam must still see at least one budgeted site.
    for (const seam of SEAMS) {
      expect(actual(seam).size, `${seam}: the pattern found nothing`).toBeGreaterThan(0);
    }
  });

  for (const seam of SEAMS) {
    describe(seam, () => {
      it("a file with no budget is clean", () => {
        const offenders = [...actual(seam)]
          .filter(([rel]) => !(rel in SEAM_DEBT[seam]))
          .map(([rel, n]) => `${rel}: ${n}`);
        expect(offenders, WHY[seam]).toEqual([]);
      });

      it("no budgeted file gets worse", () => {
        const found = actual(seam);
        const worse = Object.entries(SEAM_DEBT[seam])
          .filter(([rel, budget]) => (found.get(rel) ?? 0) > budget)
          .map(([rel, budget]) => `${rel}: ${found.get(rel)} > ${budget}`);
        expect(worse, WHY[seam]).toEqual([]);
      });

      it("no budget is stale", () => {
        // The rule that makes the two above mean something. Without it the
        // numbers drift above reality and the gate quietly loosens.
        const found = actual(seam);
        const better = Object.entries(SEAM_DEBT[seam])
          .filter(([rel, budget]) => (found.get(rel) ?? 0) < budget)
          .map(([rel, budget]) => `${rel}: ${found.get(rel) ?? 0} < ${budget}`);
        expect(better, "Thank you. Now lower these numbers in SEAM_DEBT.").toEqual([]);
      });
    });
  }

  it("refuses a new app that builds its own shell part", () => {
    // The fence against its own blind spot: prove each pattern catches the
    // shape a new app would write, so a regex that drifted cannot pass a
    // fourth palette silently.
    const sample: Record<Seam, string> = {
      cmdk: 'if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {',
      bell: 'import { NotificationBell } from "../projects/components/NotificationBell";',
      rail: "        <AgentChat agentName=\"finance-assistant\" />",
      palette: "      <SearchPalette open={open} />",
    };
    for (const seam of SEAMS) {
      expect(count(sample[seam], PATTERNS[seam]), seam).toBe(1);
    }
    // And list navigation with a plain `k` is not a ⌘K listener.
    expect(count('if (e.key === "k") moveUp();', PATTERNS.cmdk)).toBe(0);
  });
});
