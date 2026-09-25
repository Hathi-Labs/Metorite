/**
 * My Tasks · `?`, the go-keys, and the sheet printed from the real key map
 * (continuity P3, item 7).
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { GO_COMMANDS, sequenceLabel } from "@/app/projects/lib/commands";

import {
  INBOX_KEYS,
  TASKS_GLOBAL_KEYS,
  keyPassesOpenSheet,
  offersKey,
  stepTasksKey,
  tasksShortcutSections,
} from "./shortcuts";

const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

const BODY = { tagName: "BODY" };

describe("the keys My Tasks answers", () => {
  it("? opens the sheet", () => {
    const step = stepTasksKey([], "?");
    expect(step.claimed).toBe(true);
    expect(step.outcome).toEqual({ kind: "help" });
  });

  it("g then a letter jumps to that app, through the SAME commands Projects binds", () => {
    const first = stepTasksKey([], "g");
    expect(first).toMatchObject({ claimed: true, pending: ["g"], outcome: { kind: "none" } });
    expect(stepTasksKey(first.pending, "p").outcome).toEqual({ kind: "go", href: "/projects" });
    for (const command of GO_COMMANDS) {
      const [g, letter] = command.sequence!;
      expect(stepTasksKey([g], letter).outcome).toEqual({ kind: "go", href: command.href });
    }
  });

  it("a bare Inbox key is NOT claimed, so the triage switch still gets it", () => {
    for (const key of ["t", "e", "x", "s", "r", "2", "m", "o", "c", "u"]) {
      expect(stepTasksKey([], key).claimed, key).toBe(false);
    }
  });

  it("? never fires inside a text field, a textarea, a select or an editor", () => {
    expect(offersKey({ key: "?", target: BODY })).toBe(true);
    for (const tagName of ["INPUT", "TEXTAREA", "SELECT"]) {
      expect(offersKey({ key: "?", target: { tagName } }), tagName).toBe(false);
    }
    expect(offersKey({ key: "?", target: { tagName: "DIV", isContentEditable: true } })).toBe(false);
    expect(offersKey({ key: "g", ctrlKey: true, target: BODY })).toBe(false);
  });

  it("the listener runs in the capture phase, so `g t` never reaches the Inbox's `t`", () => {
    const src = read("../components/TasksShortcuts.tsx");
    expect(src).toMatch(/addEventListener\("keydown", onKey, true\)/);
    expect(src).toMatch(/if \(!step\.claimed\) return;[\s\S]*event\.stopPropagation\(\)/);
  });

  it("over the open sheet only Escape and Tab reach the page", () => {
    expect(keyPassesOpenSheet("Escape")).toBe(true);
    expect(keyPassesOpenSheet("Tab")).toBe(true);
    for (const key of ["t", "Enter", "ArrowDown", "x", "?"]) {
      expect(keyPassesOpenSheet(key), key).toBe(false);
    }
  });
});

describe("the sheet is printed from the real key map", () => {
  it("the Go section is Projects' go-keys, row for row", () => {
    const go = tasksShortcutSections().find((s) => s.section === "Go")!;
    expect(go.rows).toEqual(
      GO_COMMANDS.map((c) => ({ keys: sequenceLabel(c), label: c.label, icon: c.icon })),
    );
  });

  it("every key the Inbox triage switch binds is listed, and nothing else", () => {
    const src = read("../components/InboxView.tsx");
    const at = src.indexOf("switch (e.key) {");
    expect(at, "the Inbox triage switch moved").toBeGreaterThan(-1);
    const block = src.slice(at, src.indexOf("\n      }\n", at));
    const bound = [...block.matchAll(/case "([^"]+)":/g)].map((m) => m[1]).sort();
    expect(bound).toEqual(INBOX_KEYS.map((k) => k.bound).sort());
  });

  it("the global keys are the ones the page and the undo toast bind", () => {
    const keys = TASKS_GLOBAL_KEYS.map((k) => k.keys);
    expect(keys).toEqual(["c", "⌘ K", "u", "?"]);
    expect(read("../page.tsx")).toMatch(/e\.key === "c" \|\| e\.key === "C"/);
    expect(read("../page.tsx")).toMatch(/isOpenShortcut\(e\)/);
    expect(read("../components/UndoToast.tsx")).toMatch(/e\.key === "u"/);
  });

  it("both page branches mount the shortcuts, and the Inbox legend is a pointer", () => {
    const page = read("../page.tsx");
    expect(page.match(/\{shortcuts\}/g)).toHaveLength(2);
    const inbox = read("../components/InboxView.tsx");
    expect(inbox).toMatch(/onClick=\{openShortcutsSheet\}/);
    // The hand-kept inline legend is gone.
    expect(inbox).not.toMatch(/function Sc\b|<Sc /);
  });
});
