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
 * in prose, and prose binds nobody (R7). This file makes the next copy fail.
 *
 * **What it fences.** Four shell parts that an app must not build or reuse
 * for itself:
 *
 *   1. a ⌘K listener
 *   2. a bell: an import of `NotificationBell`, or a read of the notification
 *      list, which only the bell does today
 *   3. an assistant rail: an import of `AgentChat` or of one of its rail
 *      wrappers (`AssistantRail`, `EmailAssistantChat`)
 *   4. an import of a search or command palette
 *
 * Rules 2 to 4 count IMPORTS, not JSX. An import cannot hide in a ternary, a
 * drawer argument or a renamed tag, and a JSDoc line never starts one. That
 * is also how this file sees a new app REUSE another app's rail, which is
 * the cheapest way to build a fourth one.
 *
 * **How.** The ratchet of `lib/theme/conformance.test.ts`, keyed by APP. D89's
 * unit is the app, so the debt sits in `SEAM_DEBT` per app folder
 * (`app/<name>/`), summed over its files. Moving a mount inside one app is
 * free. A new app is a new key, and a new key fails. A budgeted app may not
 * get worse. An app that got BETTER fails until somebody lowers its number, so
 * the debt can never quietly become fiction. NS-1 to NS-9 of the spec pay it
 * down to zero.
 *
 * **Where the shell lives.** `src/lib/shell/` only, which is where NS-1, NS-2
 * and NS-6 build. It is exempt from the debt, and it may hold ONE file per
 * seam: the one listener, the one bell, the one dock, the one palette. That
 * limit stops the folder from becoming a place to launder an app's debt.
 *
 * ⚠️ **What it cannot see.** An app that draws its own top bar imports nothing
 * a regex can name, so "no own top bar" stays advisory, review-only. A
 * hotkey library would also escape. The tree uses none today, and adding one
 * is a decision that names this file.
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

const FILES = sourceFiles();
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");
const count = (text: string, re: RegExp) => (text.match(re) || []).length;

/** The shell's own home. NS-1 builds here, and nothing here is debt. */
const SHELL_HOME = "lib/shell/";

/** The debt key of a file: its app folder, or the file itself outside `app/`. */
function debtKey(rel: string): string {
  const m = rel.match(/^app\/[^/]+\//);
  return m ? m[0] : rel;
}

type Seam = "cmdk" | "bell" | "rail" | "palette";

/** `k` or `K` on `.key`, any case helper, `==` or `===`, or `code === "KeyK"`. */
const KEY = String.raw`(?:(?:\.|\b)key(?:\.to(?:Lower|Upper)Case\(\))?\s*={2,3}\s*["'][kK]["']|\.code\s*={2,3}\s*["']KeyK["'])`;
/** A modifier that is NOT negated. `!e.metaKey && e.key === "k"` is j/k navigation. */
const MOD = String.raw`(?<!!\s*(?:[\w$]+\.)?)(?:metaKey|ctrlKey)`;

/** An import of one of `names`: static (not `import type`) or dynamic. */
const importOf = (names: string): string =>
  String.raw`^import\s+(?!type\b)[^;]*\b(?:${names})\b|\bimport\(\s*["'][^"']*\b(?:${names})["']`;

const PATTERNS: Record<Seam, RegExp> = {
  cmdk: new RegExp(
    MOD + String.raw`[^;{}]{0,120}?` + KEY + "|" + KEY + String.raw`[^;{}]{0,120}?` + MOD +
      String.raw`|\bisOpenShortcut\(`,
    "g",
  ),
  bell: new RegExp(importOf("NotificationBell") + String.raw`|\bnotificationsApi\.list\(`, "gm"),
  rail: new RegExp(importOf("AgentChat|AssistantRail|EmailAssistantChat"), "gm"),
  palette: new RegExp(importOf("SearchPalette|CommandPalette"), "gm"),
};

/**
 * Apps that are not debt, each with its argument.
 *
 * `/chat` is the one full chat page. The spec keeps it (§7.1), and the dock
 * shares its sessions. It is a destination, not a rail inside another app.
 */
const SEAM_EXCEPTIONS: Partial<Record<Seam, string[]>> = {
  rail: ["app/chat/"],
};

/**
 * Today's debt per app, measured 2026-10-05: 18 sites in 4 apps. Each number
 * may only go DOWN.
 *
 * ⚠️ Do NOT add a key here to make a new app pass. A new app declares jobs, a
 * search provider, a needs provider and an agent in its manifest instead
 * (`navigation_shell.md` §5). If the shell part it needs does not exist yet,
 * that is NS-1's work. Build it in `src/lib/shell/`, once, for every app.
 */
const SEAM_DEBT: Record<Seam, Record<string, number>> = {
  cmdk: {
    "app/email/": 1, // page.tsx, its own listener
    "app/projects/": 3, // lib/search.ts (the predicate, 2) and page.tsx (1)
    "app/tasks/": 1, // page.tsx
  },
  bell: {
    "app/projects/": 2, // components/NotificationBell.tsx reads the list, page.tsx mounts it
    "app/tasks/": 1, // page.tsx borrows the Projects bell
  },
  rail: {
    "app/build/": 1, // apps/[slug]/edit/page.tsx
    "app/email/": 2, // components/EmailAssistantChat.tsx and page.tsx
    "app/projects/": 2, // components/AssistantRail.tsx and page.tsx
    "app/tasks/": 2, // components/AssistantRail.tsx and page.tsx
  },
  palette: {
    "app/email/": 1,
    "app/projects/": 1,
    "app/tasks/": 1,
  },
};

const SEAMS = Object.keys(PATTERNS) as Seam[];

// One scan per seam per run. Each `it` reads the same answer.
const measured = new Map<Seam, Map<string, number>>();

/** Sites per debt key, outside the shell and the exceptions. */
function actual(seam: Seam): Map<string, number> {
  const hit = measured.get(seam);
  if (hit) return hit;
  const out = new Map<string, number>();
  const skip = SEAM_EXCEPTIONS[seam] ?? [];
  for (const rel of FILES) {
    if (rel.startsWith(SHELL_HOME)) continue;
    const key = debtKey(rel);
    if (skip.includes(key)) continue;
    const n = count(read(rel), PATTERNS[seam]);
    if (n > 0) out.set(key, (out.get(key) ?? 0) + n);
  }
  measured.set(seam, out);
  return out;
}

const WHY: Record<Seam, string> = {
  cmdk: "Do not add a ⌘K listener to an app. The shell owns the one listener. Declare jobs in the app's manifest instead.",
  bell: "Do not mount or rebuild a bell in an app. The shell owns the one bell. Declare a needs provider in the app's manifest instead.",
  rail: "Do not mount or reuse an assistant rail in an app. The shell owns the one dock. Name the app's agent in its manifest instead.",
  palette: "Do not mount a search or command palette in an app. The shell owns the one command bar. Declare jobs and a search provider instead.",
};

describe("an app plugs into the shell and never builds one (D89, AGENTS.md rule 10)", () => {
  for (const seam of SEAMS) {
    describe(seam, () => {
      it("an app with no budget is clean", () => {
        const offenders = [...actual(seam)]
          .filter(([key]) => !(key in SEAM_DEBT[seam]))
          .map(([key, n]) => `${key}: ${n}`);
        expect(offenders, WHY[seam]).toEqual([]);
      });

      it("no budgeted app gets worse", () => {
        const found = actual(seam);
        const worse = Object.entries(SEAM_DEBT[seam])
          .filter(([key, budget]) => (found.get(key) ?? 0) > budget)
          .map(([key, budget]) => `${key}: ${found.get(key)} > ${budget}`);
        expect(worse, WHY[seam]).toEqual([]);
      });

      it("no budget is stale", () => {
        // The rule that makes the two above mean something. Without it the
        // numbers drift above reality and the gate quietly loosens. It is
        // also the check that a pattern still matches: a pattern that
        // matched nothing would fail here for every budgeted app.
        const found = actual(seam);
        const better = Object.entries(SEAM_DEBT[seam])
          .filter(([key, budget]) => (found.get(key) ?? 0) < budget)
          .map(([key, budget]) => `${key}: ${found.get(key) ?? 0} < ${budget}`);
        expect(better, "Thank you. Now lower these numbers in SEAM_DEBT.").toEqual([]);
      });
    });
  }

  it("lets the shell hold at most ONE file per seam", () => {
    // "The one listener, the one bell, the one dock, the one palette." More
    // than one file here means somebody moved an app's copy in, instead of
    // building the shell's part.
    const crowded = SEAMS.map((seam) => {
      const files = FILES.filter(
        (rel) => rel.startsWith(SHELL_HOME) && count(read(rel), PATTERNS[seam]) > 0,
      );
      return [seam, files] as const;
    }).filter(([, files]) => files.length > 1);
    expect(crowded).toEqual([]);
  });

  it("keys the debt by app, so a move inside one app is free", () => {
    expect(debtKey("app/projects/page.tsx")).toBe("app/projects/");
    expect(debtKey("app/projects/components/X.tsx")).toBe("app/projects/");
    expect(debtKey("app/finance/page.tsx")).toBe("app/finance/");
    expect(debtKey("components/Thing.tsx")).toBe("components/Thing.tsx");
  });

  it("catches the shapes a new app would write, and nothing else", () => {
    // The fence against its own blind spot. Each pattern must catch every
    // realistic spelling below, so a regex that drifted cannot pass a new
    // copy silently.
    const caught: Record<Seam, string[]> = {
      cmdk: [
        'if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {',
        'if (e.key === "k" && (e.metaKey || e.ctrlKey)) {',
        'if (metaKey && key === "k") {',
        'if (e.ctrlKey && e.code === "KeyK") {',
        'if (e.metaKey && e.key.toUpperCase() == "K") {',
        "if (isOpenShortcut(event)) {",
      ],
      bell: [
        'import { NotificationBell } from "../projects/components/NotificationBell";',
        'import Bell, { NotificationBell as B } from "@/app/projects/components/NotificationBell";',
        'const Bell = dynamic(() => import("../projects/components/NotificationBell"));',
        "const rows = await notificationsApi.list();",
      ],
      rail: [
        'import AgentChat from "@/components/AgentChat";',
        'import { AssistantRail } from "../tasks/components/AssistantRail";',
        'import EmailAssistantChat from "../email/components/EmailAssistantChat";',
        'const Rail = lazy(() => import("@/components/AgentChat"));',
      ],
      palette: [
        'import SearchPalette from "../projects/components/SearchPalette";',
        'import { SearchPalette as Finder } from "../projects/components/SearchPalette";',
        'import CommandPalette from "./components/CommandPalette";',
      ],
    };
    for (const seam of SEAMS) {
      for (const line of caught[seam]) {
        expect(count(line, PATTERNS[seam]), `${seam}: ${line}`).toBe(1);
      }
    }

    const ignored: Array<[Seam, string]> = [
      // j/k list navigation in the house style, with negated modifiers.
      ["cmdk", 'if (!e.metaKey && !e.ctrlKey && !e.altKey && e.key === "k") moveUp();'],
      ["cmdk", 'if (e.key === "k") moveUp();'],
      // A type import mounts nothing.
      ["rail", 'import type { AgentChatProps } from "@/components/AgentChat";'],
      ["rail", "import { useAgentChat } from '@/lib/useAgentChat';"],
      // Prose about a part is not the part.
      ["palette", " * The page mounts <SearchPalette> once."],
      ["rail", " * A THIN wrapper around the shared <AgentChat>."],
    ];
    for (const [seam, line] of ignored) {
      expect(count(line, PATTERNS[seam]), `${seam}: ${line}`).toBe(0);
    }
  });
});
