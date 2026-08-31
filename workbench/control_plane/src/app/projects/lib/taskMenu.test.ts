/**
 * Projects · the task context-menu registry (WS-27bd item 5).
 *
 * Three classes of defect, each of which ships green without a test here:
 * an entry offered on a surface that cannot perform it (one with no status
 * axis and no bulk selection); a separator opening or closing the menu once
 * `when` filtered a group away; and the drift this whole ticket exists to stop
 * — the card growing a second name for something the palette already does.
 *
 * The last one is the load-bearing case and it is a **cross-check against
 * `commands.ts`**, not a statement about this file alone.
 */

import { describe, expect, it } from "vitest";

import { isKnownIcon } from "@/lib/icons";

import type { StatusRow, TaskRow } from "./api";
import { COMMANDS } from "./commands";
import {
  TASK_MENU_ACTIONS,
  type TaskMenuActions,
  type TaskMenuContext,
  type TaskMenuEntry,
  taskMenuItems,
  taskMenuKind,
} from "./taskMenu";

const status = (id: string, name: string): StatusRow => ({
  id,
  project_id: "p1",
  name,
  color: "gray",
  position: 0,
  category: "open",
  is_default: false,
});

const STATUSES = [status("s1", "Todo"), status("s2", "Doing"), status("s3", "Done")];

const task = (over: Partial<TaskRow> = {}): TaskRow => ({
  id: "t1",
  project_id: "p1",
  root_project_id: "r1",
  status_id: "s2",
  title: "Write the fence",
  ...over,
});

/** The board's card: statuses to move along, and a checkbox beside it. */
const board = (over: Partial<TaskMenuContext> = {}): TaskMenuContext => ({
  task: task(),
  statuses: STATUSES,
  canSelect: true,
  selected: false,
  ...over,
});

/** A card on a surface with no status axis and no bulk selection. */
const bare = (over: Partial<TaskMenuContext> = {}): TaskMenuContext => ({
  task: task(),
  statuses: [],
  canSelect: false,
  selected: false,
  ...over,
});

/** Every action, recorded rather than performed (`commands.test.ts`'s shape). */
function spyActions(): TaskMenuActions & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    open: (t) => calls.push(`open:${t.id}`),
    copyLink: (t) => calls.push(`copyLink:${t.id}`),
    toggleSelect: (t) => calls.push(`toggleSelect:${t.id}`),
    setStatus: (t, statusId) => calls.push(`setStatus:${t.id}:${statusId}`),
  };
}

const ids = (entries: TaskMenuEntry[]) =>
  entries.filter((e) => e.kind === "item").map((e) => e.id);

describe("what a right-click offers", () => {
  it("the board's card offers open, copy link, select and every status", () => {
    expect(ids(taskMenuItems(board()))).toEqual([
      "task.open",
      "task.copyLink",
      "task.select",
      "task.status:s1",
      "task.status:s2",
      "task.status:s3",
    ]);
  });

  it("a statusless card offers neither a status block nor a selection", () => {
    // The defect this pins: a menu drawn from the board's assumptions on a
    // surface that has no status axis draws a "Change status" heading with
    // nothing under it and a Select that adds a row to a selection nothing
    // renders.
    const entries = taskMenuItems(bare());
    expect(ids(entries)).toEqual(["task.open", "task.copyLink"]);
    expect(entries.some((e) => e.kind === "label")).toBe(false);
    expect(entries.some((e) => e.kind === "sep")).toBe(false);
  });

  it("the select row says what the click will do, not what the row is", () => {
    const label = (selected: boolean) =>
      taskMenuItems(board({ selected })).find(
        (e) => e.kind === "item" && e.id === "task.select",
      );
    expect(label(false)).toMatchObject({ label: "Select" });
    expect(label(true)).toMatchObject({ label: "Remove from selection" });
  });

  it("exactly the task's own status is ticked", () => {
    const checked = taskMenuItems(board())
      .filter((e) => e.kind === "item" && e.checked)
      .map((e) => (e.kind === "item" ? e.id : ""));
    expect(checked).toEqual(["task.status:s2"]);
  });

  it("a project with no statuses draws no heading", () => {
    expect(ids(taskMenuItems(board({ statuses: [] })))).toEqual([
      "task.open",
      "task.copyLink",
      "task.select",
    ]);
    expect(
      taskMenuItems(board({ statuses: [] })).some((e) => e.kind === "label"),
    ).toBe(false);
  });

  it("an expand() that produced nothing takes its heading with it", () => {
    // The declared registry reaches this through `when` today, so the guard
    // inside `taskMenuItems` is only reachable with a registry of our own —
    // and it survived a mutation run until this case existed. A lone "Change
    // status" label over an empty block is exactly the shape a heading written
    // BESIDE its rows (rather than with them) always ends up in.
    const entries = taskMenuItems(board(), [
      {
        id: "task.nothing",
        label: () => "Nothing",
        group: 0,
        heading: "Nothing to choose",
        expand: () => [],
        run: () => {},
      },
    ]);
    expect(entries).toEqual([]);
  });

  it("no separator opens or closes the menu, and none doubles up", () => {
    for (const ctx of [board(), bare(), board({ statuses: [] }), board({ canSelect: false })]) {
      const entries = taskMenuItems(ctx);
      expect(entries.at(0)?.kind, JSON.stringify(ids(entries))).not.toBe("sep");
      expect(entries.at(-1)?.kind).not.toBe("sep");
      for (let i = 1; i < entries.length; i++) {
        expect(entries[i].kind === "sep" && entries[i - 1].kind === "sep").toBe(false);
      }
    }
  });

  it("dropping the middle group does not leave a hanging separator", () => {
    // `canSelect: false` removes group 1 outright; groups 0 and 2 must still
    // be separated by exactly one rule.
    const entries = taskMenuItems(board({ canSelect: false }));
    expect(entries.filter((e) => e.kind === "sep")).toHaveLength(1);
    expect(ids(entries)).toEqual([
      "task.open",
      "task.copyLink",
      "task.status:s1",
      "task.status:s2",
      "task.status:s3",
    ]);
  });
});

describe("running an entry", () => {
  it.each([
    ["task.open", "open:t1"],
    ["task.copyLink", "copyLink:t1"],
    ["task.select", "toggleSelect:t1"],
    ["task.status:s3", "setStatus:t1:s3"],
  ])("%s calls exactly %s", (id, expected) => {
    const ctx = board();
    const entry = taskMenuItems(ctx).find((e) => e.kind === "item" && e.id === id);
    expect(entry, `${id} is not offered`).toBeTruthy();
    const actions = spyActions();
    if (entry?.kind === "item") entry.run(actions, ctx);
    expect(actions.calls).toEqual([expected]);
  });

  it("each status row carries its OWN id", () => {
    // The closure-in-a-loop bug: every row ends up moving the task to the last
    // status because `run` read a variable that kept changing.
    const ctx = board();
    const actions = spyActions();
    for (const entry of taskMenuItems(ctx)) {
      if (entry.kind === "item" && taskMenuKind(entry.id) === "task.status") {
        entry.run(actions, ctx);
      }
    }
    expect(actions.calls).toEqual([
      "setStatus:t1:s1",
      "setStatus:t1:s2",
      "setStatus:t1:s3",
    ]);
  });
});

describe("the registry itself", () => {
  it("every emitted id traces back to a declared action", () => {
    // No entry invented at the call site, and no undeclared status action:
    // the `<kind>:<discriminator>` grammar is what keeps the status block ONE
    // declared thing instead of N anonymous ones.
    const declared = new Set(TASK_MENU_ACTIONS.map((a) => a.id));
    for (const ctx of [board(), bare(), board({ selected: true })]) {
      for (const id of ids(taskMenuItems(ctx))) {
        expect(declared, `${id} is not declared in TASK_MENU_ACTIONS`).toContain(
          taskMenuKind(id),
        );
      }
    }
  });

  it("every action is declared once, namespaced, and labelled", () => {
    expect(new Set(TASK_MENU_ACTIONS.map((a) => a.id)).size).toBe(
      TASK_MENU_ACTIONS.length,
    );
    for (const action of TASK_MENU_ACTIONS) {
      expect(action.id, action.id).toMatch(/^task\.[a-zA-Z]+$/);
      expect(action.label(board()).trim(), action.id).not.toBe("");
      expect(action.label(board({ selected: true })).trim(), action.id).not.toBe("");
    }
  });

  it("every glyph is a real Lucide icon", () => {
    // Same fence `commands.test.ts` carries: an unknown name resolves to the
    // `Zap` fallback, so a typo ships as a lightning bolt in the menu.
    const unknown = TASK_MENU_ACTIONS.map((a) => a.icon)
      .filter((name): name is string => Boolean(name))
      .filter((name) => !isKnownIcon(name));
    expect(unknown, "these are not Lucide icon names").toEqual([]);
  });
});

/**
 * ── One registry per scope, and no overlap between them ──────────────────
 *
 * The ticket's words are "one registry, two surfaces". `TaskBoard` reads
 * `TASK_MENU_ACTIONS` (`MyWork` was the second reader until it was removed
 * on 2026-08-31). The half that
 * needs a *test* is the other one: that this registry does not quietly become
 * a second spelling of the palette's.
 *
 * ⚠️ Read as a fence, this is deliberately weaker than "every menu entry
 * resolves to a `commands.ts` entry", which cannot hold and should not be
 * written: `commands.ts` contains no task-scoped action for Open / Copy link /
 * Select / Change status to resolve TO, and filling it with them is a change to
 * the palette, the `g`/`v` sequences and the printed shortcuts sheet at once.
 * What IS mechanical, and what this asserts, is that the two registries stay
 * disjoint in both directions — so the day somebody adds an `Open` command to
 * the palette or copies `Clear the filters` onto a card, this goes red.
 */
describe("the palette registry and the card registry stay disjoint", () => {
  const commandIds = new Set(COMMANDS.map((c) => c.id));
  const commandLabels = new Set(COMMANDS.map((c) => c.label.toLowerCase()));

  it("no id is claimed by both", () => {
    const clash = TASK_MENU_ACTIONS.filter((a) => commandIds.has(a.id)).map((a) => a.id);
    expect(
      clash,
      "One id, two registries — the palette and the card menu would run " +
        "different code for the same name. Move the action into one of them.",
    ).toEqual([]);
  });

  it("no label is claimed by both", () => {
    const clash = TASK_MENU_ACTIONS.flatMap((a) =>
      [a.label(board()), a.label(board({ selected: true }))]
        .filter((label) => commandLabels.has(label.toLowerCase()))
        .map((label) => `${a.id} → "${label}"`),
    );
    expect(
      clash,
      "The card menu and the palette say the same words for two different " +
        "actions (or the same action twice). One vocabulary per concept.",
    ).toEqual([]);
  });

  it("no palette command acts on a task, and no card action navigates", () => {
    // The direction that catches the drift BEFORE the id/label clash does:
    // a `task.*` id appearing in the palette registry.
    expect([...commandIds].filter((id) => id.startsWith("task."))).toEqual([]);
    expect(
      TASK_MENU_ACTIONS.filter((a) => !a.id.startsWith("task.")).map((a) => a.id),
    ).toEqual([]);
  });
});
