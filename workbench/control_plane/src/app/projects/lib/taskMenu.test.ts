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
import { CLOSED } from "./relations";
import {
  TASK_MENU_ACTIONS,
  type TaskMenuActions,
  type TaskMenuContext,
  type TaskMenuEntry,
  laneFor,
  taskMenuItems,
  taskMenuKind,
  taskQuickActions,
} from "./taskMenu";

const status = (
  id: string,
  name: string,
  over: Partial<StatusRow> = {},
): StatusRow => ({
  id,
  project_id: "p1",
  name,
  color: "gray",
  position: 0,
  category: "todo",
  is_default: false,
  ...over,
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
    moveToProject: (t) => calls.push(`moveToProject:${t.id}`),
    addSubtask: (t) => calls.push(`addSubtask:${t.id}`),
    rename: (t) => calls.push(`rename:${t.id}`),
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

describe("Move to project… (WS-27bl §9.13.4)", () => {
  const labelsOf = (ctx: TaskMenuContext) =>
    taskMenuItems(ctx)
      .filter((i) => i.kind === "item")
      .map((i) => (i as { label: string }).label);

  it("is offered only when the surface can raise the card", () => {
    // The rule `canSelect` already follows: an entry the surface cannot
    // service is dropped, never greyed. A read-only board gets a menu of
    // exactly what it can do.
    expect(labelsOf(board())).not.toContain("Move to project…");
    expect(labelsOf(board({ canMoveToProject: true }))).toContain(
      "Move to project…"
    );
  });

  it("is offered on a surface with no status axis", () => {
    // Moving a task does not need the CURRENT project to have lanes drawn —
    // the destination's lanes are what the card resolves against.
    expect(labelsOf(bare({ canMoveToProject: true }))).toContain(
      "Move to project…"
    );
  });

  it("⚠️ OPENS the card and moves nothing", () => {
    // The whole point of D-PM-29: a move crosses two vocabularies, so the
    // member is shown the mapping and the losses before agreeing. A menu
    // entry that moved on click would skip that.
    const actions = spyActions();
    const ctx = board({ canMoveToProject: true });
    const entry = taskMenuItems(ctx).find(
      (i) => i.kind === "item" && (i as { label: string }).label === "Move to project…"
    );
    if (entry?.kind === "item") entry.run(actions, ctx);
    expect(actions.calls).toEqual(["moveToProject:t1"]);
  });

  it("hands the action the task under the pointer", () => {
    const seen: string[] = [];
    const ctx = board({ canMoveToProject: true, task: task({ id: "t9" }) });
    const entry = taskMenuItems(ctx).find(
      (i) => i.kind === "item" && (i as { label: string }).label === "Move to project…"
    );
    if (entry?.kind === "item") {
      entry.run({ ...spyActions(), moveToProject: (t) => seen.push(t.id) }, ctx);
    }
    expect(seen).toEqual(["t9"]);
  });
});

/**
 * ── The card's hover strip (owner direction, 2026-09-20) ────────────────
 *
 * The strip is a second READER of one registry, so what needs pinning is not
 * "does it draw four buttons" — it is every way the two readers could stop
 * agreeing, and every way a tick could move a task to the wrong lane.
 */

/**
 * Lanes with a real closing category, in a deliberately unhelpful order.
 *
 * ⚠️ **The array order DISCRIMINATES, and it was rewritten once because it
 * did not.** The first version listed Done at index 0 and To do at index 1,
 * so `.find(closed)` and `.find(closed)` after a sort both answered "Done" —
 * the test passed with the sort deleted. Now the first CLOSED lane in array
 * order (Cancelled) is not the lowest-position closed lane (Done), and the
 * first OPEN one (Doing) is not the lowest-position open one (To do). Keep it
 * that way: delete the sort in `laneFor` and two of these must go red.
 */
const LADDER = [
  status("s-cancelled", "Cancelled", { category: "cancelled", position: 40 }),
  status("s-doing", "Doing", { category: "in_progress", position: 20 }),
  status("s-done", "Done", { category: "done", position: 30 }),
  status("s-todo", "To do", { category: "todo", position: 10 }),
];

const ladder = (over: Partial<TaskMenuContext> = {}): TaskMenuContext => ({
  task: task({ status_id: "s-todo" }),
  statuses: LADDER,
  canSelect: true,
  canEditInline: true,
  selected: false,
  ...over,
});

const quickIds = (ctx: TaskMenuContext) => taskQuickActions(ctx).map((a) => a.id);

describe("which lane the tick sends a task to", () => {
  it("picks the first CLOSED lane by position, not the first in the array", () => {
    // The array here is deliberately out of order. Reading `statuses[0]` would
    // answer "Done" by luck, and so would a `.find(closed)` with no sort —
    // both break the day a space puts Cancelled before Done, and neither is
    // visible in review.
    expect(laneFor(LADDER, "closed")?.id).toBe("s-done");
  });

  it("picks the first OPEN lane by position to reopen into", () => {
    expect(laneFor(LADDER, "open")?.id).toBe("s-todo");
  });

  it("reopens into To do, not into a Backlog lane above it (D79)", () => {
    // The gateway's rule (`personal.reopen_if_closed`): the first `todo`
    // status. The first open lane by position was Backlog here, so the
    // Projects tick and a reopen from My Tasks sent one task two places.
    const withBacklog = [
      ...LADDER,
      status("s-backlog", "Backlog", { category: "backlog", position: 5 }),
    ];
    expect(laneFor(withBacklog, "open")?.id).toBe("s-todo");
  });

  it("answers nothing when the project has no lane of that kind", () => {
    const openOnly = [status("a", "A"), status("b", "B", { position: 1 })];
    expect(laneFor(openOnly, "closed")).toBeUndefined();
    expect(laneFor(openOnly, "open")?.id).toBe("a");
  });

  it("treats cancelled as closed, because the gateway does", () => {
    // `CLOSED` is `relations.ts`'s mirror of `CLOSING_CATEGORIES`. A second
    // list inside `taskMenu.ts` would pass this and disagree with the server.
    const cancelOnly = [
      status("open", "Open", { position: 0 }),
      status("x", "Cancelled", { category: "cancelled", position: 1 }),
    ];
    expect(laneFor(cancelOnly, "closed")?.id).toBe("x");
  });
});

describe("the tick is a toggle, and says which way it will go", () => {
  const tick = (ctx: TaskMenuContext) =>
    taskQuickActions(ctx).find((a) => a.id === "task.markDone");

  it("reads Mark done on an open task, and Reopen on a closed one", () => {
    expect(tick(ladder())).toMatchObject({ label: "Mark done", active: false });
    expect(tick(ladder({ task: task({ status_id: "s-done" }) }))).toMatchObject({
      label: "Reopen",
      active: true,
    });
  });

  it("an open task goes to Done, and a done task comes back to To do", () => {
    const open = ladder();
    const a = spyActions();
    tick(open)?.run(a, open);
    expect(a.calls).toEqual(["setStatus:t1:s-done"]);

    const closed = ladder({ task: task({ status_id: "s-done" }) });
    const b = spyActions();
    tick(closed)?.run(b, closed);
    expect(b.calls).toEqual(["setStatus:t1:s-todo"]);
  });

  it("a cancelled task reopens rather than being marked done again", () => {
    // The plausible bug: `isDone` written as `status_id === doneLane.id`
    // rather than as a category test. Cancelled is closed and is not the Done
    // lane, so that spelling offers "Mark done" on an abandoned task.
    const ctx = ladder({ task: task({ status_id: "s-cancelled" }) });
    expect(tick(ctx)).toMatchObject({ label: "Reopen", active: true });
  });

  it("is not offered at all where no lane closes work", () => {
    // A board whose lanes are all open has no notion of finished. A tick that
    // moved a task to "Backlog" would lie about what it did.
    const openOnly = ladder({ statuses: [status("a", "A"), status("b", "B")] });
    expect(quickIds(openOnly)).not.toContain("task.markDone");
  });

  it("is not offered on a surface with no status axis", () => {
    expect(quickIds(ladder({ statuses: [] }))).not.toContain("task.markDone");
  });
});

describe("the strip and the menu are one registry", () => {
  it("the strip is markDone, addSubtask, rename, select — in that order", () => {
    // `quick` ranks, not registry order: the registry is grouped for the
    // MENU's reading, and the strip's left-to-right is its own decision.
    // Select is last and `trailing`, so the pill draws it past the "more"
    // button and behind a rule.
    expect(quickIds(ladder())).toEqual([
      "task.markDone",
      "task.addSubtask",
      "task.rename",
      "task.select",
    ]);
  });

  it("markDone is on the strip and NOT in the menu", () => {
    // §5: the menu already lists every lane by name, so a "Mark done" command
    // beside a "Done" row is two names for one write. `inMenu: false` is the
    // whole mechanism, and this is what keeps it honest.
    expect(quickIds(ladder())).toContain("task.markDone");
    expect(ids(taskMenuItems(ladder()))).not.toContain("task.markDone");
  });

  it("dropping markDone leaves no empty group and no hanging separator", () => {
    // `inMenu` is applied BEFORE the block logic. Applied after, group 0 would
    // still be created for an action that emits nothing, and the menu would
    // open on a rule.
    const entries = taskMenuItems(ladder());
    expect(entries.at(0)?.kind).not.toBe("sep");
    expect(entries.at(-1)?.kind).not.toBe("sep");
    for (let i = 1; i < entries.length; i++) {
      expect(entries[i].kind === "sep" && entries[i - 1].kind === "sep").toBe(false);
    }
  });

  it("rename and add-subtask appear on BOTH, with one spelling each", () => {
    const menu = taskMenuItems(ladder()).filter((e) => e.kind === "item");
    const strip = taskQuickActions(ladder());
    for (const id of ["task.rename", "task.addSubtask"]) {
      const inMenu = menu.find((e) => e.id === id);
      const onStrip = strip.find((a) => a.id === id);
      expect(inMenu, id + " is missing from the menu").toBeTruthy();
      expect(onStrip, id + " is missing from the strip").toBeTruthy();
      // The drift this ticket guards against: the strip saying "Edit" where
      // the menu says "Rename".
      expect(onStrip?.label).toBe((inMenu as { label: string }).label);
      expect(onStrip?.icon).toBe((inMenu as { icon?: string }).icon);
    }
  });

  it("a surface that cannot edit in place is offered neither, on either", () => {
    const readOnly = ladder({ canEditInline: false });
    // Select survives: it does not edit the task, it selects it.
    expect(quickIds(readOnly)).toEqual(["task.markDone", "task.select"]);
    expect(ids(taskMenuItems(readOnly))).not.toContain("task.rename");
    expect(ids(taskMenuItems(readOnly))).not.toContain("task.addSubtask");
  });

  it("every strip button runs exactly its own action", () => {
    const ctx = ladder();
    const actions = spyActions();
    for (const item of taskQuickActions(ctx)) item.run(actions, ctx);
    expect(actions.calls).toEqual([
      "setStatus:t1:s-done",
      "addSubtask:t1",
      "rename:t1",
      "toggleSelect:t1",
    ]);
  });
});

describe("the strip's shape is enforced, not assumed", () => {
  it("no action carries both `quick` and `expand`", () => {
    // One button cannot be four lanes. If somebody ranks the status action,
    // `taskQuickActions` silently drops it and the strip loses a button with
    // no error — so the registry is checked, not the output.
    const both = TASK_MENU_ACTIONS.filter((a) => a.quick !== undefined && a.expand);
    expect(
      both.map((a) => a.id),
      "A quick action is ONE button. Give it rows or give it a rank.",
    ).toEqual([]);
  });

  it("every quick rank is unique, so the order is not the array's", () => {
    const ranks = TASK_MENU_ACTIONS.map((a) => a.quick).filter(
      (r): r is number => r !== undefined,
    );
    expect(new Set(ranks).size, "two buttons share a rank").toBe(ranks.length);
  });

  it("every quick action declares a glyph", () => {
    // The strip has no room for words. An action with no icon falls back to a
    // circle, which is a button that says nothing about what it does.
    const mute = TASK_MENU_ACTIONS.filter((a) => a.quick !== undefined && !a.icon);
    expect(mute.map((a) => a.id)).toEqual([]);
  });

  it("an action that is inMenu:false and has no rank is unreachable", () => {
    // The declaration mistake that ships silently: hiding an action from the
    // menu without putting it on the strip removes it from the product.
    const lost = TASK_MENU_ACTIONS.filter(
      (a) => a.inMenu === false && a.quick === undefined,
    );
    expect(lost.map((a) => a.id)).toEqual([]);
  });
});

/**
 * ── "Mark done" must not cancel the task (review finding, 2026-09-20) ──
 *
 * The first draft ranked closing lanes by POSITION alone, so a space that
 * orders Cancelled before Done got a tick labelled "Mark done" that wrote
 * Cancelled. Both the label and the glyph said the opposite of the write.
 */
describe("done outranks cancelled, and position only breaks the tie", () => {
  /** Backlog 10, To do 20, Cancelled 30, Done 40 — a legal, ordinary space. */
  const CANCEL_FIRST = [
    status("s-backlog", "Backlog", { position: 10 }),
    status("s-todo", "To do", { position: 20 }),
    status("s-cancel", "Cancelled", { category: "cancelled", position: 30 }),
    status("s-done", "Done", { category: "done", position: 40 }),
  ];

  it("picks Done even when Cancelled sits before it", () => {
    expect(laneFor(CANCEL_FIRST, "closed")?.id).toBe("s-done");
  });

  it("the tick on such a board says Mark done and writes the DONE lane", () => {
    const ctx: TaskMenuContext = {
      task: task({ status_id: "s-todo" }),
      statuses: CANCEL_FIRST,
      canSelect: true,
      canEditInline: true,
      selected: false,
    };
    const tick = taskQuickActions(ctx).find((a) => a.id === "task.markDone");
    expect(tick?.label).toBe("Mark done");
    const actions = spyActions();
    tick?.run(actions, ctx);
    expect(
      actions.calls,
      "a tick labelled Mark done wrote a lane that is not Done",
    ).toEqual(["setStatus:t1:s-done"]);
  });

  it("position still decides between two lanes of the SAME category", () => {
    const twoDone = [
      status("late", "Shipped", { category: "done", position: 90 }),
      status("early", "Done", { category: "done", position: 50 }),
    ];
    expect(laneFor(twoDone, "closed")?.id).toBe("early");
  });

  it("falls back to any closing lane when neither name is present", () => {
    // `CLOSED` in relations.ts is the authority on WHICH categories close.
    // A category added there must still find a lane here rather than
    // silently removing the tick from every card.
    const odd = [
      status("open", "Open", { position: 0 }),
      status("weird", "Filed", { category: "archived_x", position: 5 }),
    ];
    const reachable = laneFor(odd, "closed");
    // With today's CLOSED list this is undefined, which is the honest
    // answer. The assertion pins the SHAPE: never a lane whose category is
    // not closing.
    expect(reachable === undefined || CLOSED.includes(reachable.category)).toBe(
      true,
    );
  });

  it("cancelled is still chosen when it is the only closing lane", () => {
    const cancelOnly = [
      status("open", "Open", { position: 0 }),
      status("x", "Cancelled", { category: "cancelled", position: 1 }),
    ];
    expect(laneFor(cancelOnly, "closed")?.id).toBe("x");
  });

  it("a task sitting in Cancelled still offers Reopen, not Mark done", () => {
    const ctx: TaskMenuContext = {
      task: task({ status_id: "s-cancel" }),
      statuses: CANCEL_FIRST,
      canSelect: true,
      canEditInline: true,
      selected: false,
    };
    const tick = taskQuickActions(ctx).find((a) => a.id === "task.markDone");
    expect(tick).toMatchObject({ label: "Reopen", active: true });
    const actions = spyActions();
    tick?.run(actions, ctx);
    expect(actions.calls).toEqual(["setStatus:t1:s-backlog"]);
  });
});

/**
 * ── Select is on the strip, and it is the only way in (owner, 2026-09-20) ──
 *
 * The card's own checkbox no longer appears on hover. So if `task.select`
 * ever loses its `quick` rank, the ONLY door into a selection is the
 * right-click menu — and a member who does not right-click cannot select
 * anything at all. That is what these pin.
 */
describe("the strip carries Select, last and apart", () => {
  const strip = (over: Partial<TaskMenuContext> = {}) =>
    taskQuickActions(ladder(over));

  it("Select is on the strip", () => {
    expect(strip().map((a) => a.id)).toContain("task.select");
  });

  it("it is the LAST thing, and it is trailing", () => {
    const items = strip();
    expect(items.at(-1)?.id).toBe("task.select");
    expect(items.at(-1)?.trailing).toBe(true);
  });

  it("nothing else is trailing, so the rule is drawn once", () => {
    // Two trailing items would put two dividers in the pill.
    expect(strip().filter((a) => a.trailing).map((a) => a.id)).toEqual([
      "task.select",
    ]);
  });

  it("it says what the click will do, and reads as ON when selected", () => {
    const off = strip().find((a) => a.id === "task.select");
    expect(off).toMatchObject({ label: "Select", active: false });
    const on = strip({ selected: true }).find((a) => a.id === "task.select");
    expect(on).toMatchObject({ label: "Remove from selection", active: true });
  });

  it("it toggles the selection and nothing else", () => {
    const ctx = ladder();
    const actions = spyActions();
    strip().find((a) => a.id === "task.select")?.run(actions, ctx);
    expect(actions.calls).toEqual(["toggleSelect:t1"]);
  });

  it("a surface that cannot select is offered no tick box anywhere", () => {
    const readOnly = ladder({ canSelect: false });
    expect(taskQuickActions(readOnly).map((a) => a.id)).not.toContain(
      "task.select",
    );
    expect(ids(taskMenuItems(readOnly))).not.toContain("task.select");
  });

  it("⚠️ a surface that CAN select always has a door in", () => {
    // The load-bearing one. The card's checkbox is gone until a selection
    // exists, so the strip and the menu are the only ways to start one. If
    // both were ever filtered out on a selectable surface, selection would
    // be unreachable and nothing else here would notice.
    for (const ctx of [
      ladder(),
      ladder({ canEditInline: false }),
      ladder({ statuses: [] }),
      ladder({ canMoveToProject: true }),
    ]) {
      const onStrip = taskQuickActions(ctx).some((a) => a.id === "task.select");
      const inMenu = ids(taskMenuItems(ctx)).includes("task.select");
      expect(
        onStrip || inMenu,
        "no way to start a selection on a surface that supports one",
      ).toBe(true);
    }
  });
});

describe("which colour an ON toggle is", () => {
  it("Select is the ACCENT, because a checked box is a selection", () => {
    const sel = taskQuickActions(ladder({ selected: true })).find(
      (a) => a.id === "task.select",
    );
    expect(sel?.tone).toBe("primary");
  });

  it("Mark done is SUCCESS, because finished is a fact about the work", () => {
    // `Checkbox.tsx`'s header owns this rule. Painted in the accent, the tick
    // moves when a member changes their accent — the defect `underAccents`
    // exists to catch, and one this repo has shipped before.
    const tick = taskQuickActions(ladder()).find((a) => a.id === "task.markDone");
    expect(tick?.tone).toBe("success");
  });

  it("every quick action declares a tone that the strip can paint", () => {
    for (const item of taskQuickActions(ladder({ selected: true }))) {
      expect(["primary", "success"], item.id).toContain(item.tone);
    }
  });
});


describe("the lifecycle verbs (owner request, 2026-09-20)", () => {
  const filed = (over: Partial<TaskMenuContext> = {}) =>
    board({
      canArchive: true,
      canDelete: true,
      task: task({ archived_at: "2026-09-01T00:00:00Z" }),
      ...over,
    });
  const live = (over: Partial<TaskMenuContext> = {}) =>
    board({ canArchive: true, canDelete: true, ...over });

  it("offers Archive on a live task and Restore on a filed one, never both", () => {
    expect(ids(taskMenuItems(live()))).toContain("task.archive");
    expect(ids(taskMenuItems(live()))).not.toContain("task.unarchive");
    expect(ids(taskMenuItems(filed()))).toContain("task.unarchive");
    expect(ids(taskMenuItems(filed()))).not.toContain("task.archive");
  });

  it("offers Archive on an OPEN task", () => {
    /**
     * Archive is a shelf, not an outcome, so every lane can be shelved.
     *
     * ⚠️ The gateway refused this until 2026-09-21 and the entry was offered
     * anyway, so the refusal could explain itself. The refusal is gone; the
     * entry is unchanged. This test outlived the rule it was written for,
     * which is the point of asserting the OFFER rather than the outcome.
     */
    const open = live({ task: task({ status_id: "s-todo" }) });
    expect(ids(taskMenuItems(open))).toContain("task.archive");
  });

  it("drops all three where the surface cannot do them", () => {
    // The rule `canSelect`, `canMoveToProject` and `canEditInline` follow:
    // an entry with nowhere to send its result is dropped, never greyed.
    const shown = ids(taskMenuItems(bare()));
    for (const id of ["task.archive", "task.unarchive", "task.delete"]) {
      expect(shown).not.toContain(id);
    }
  });

  it("separates the power to file from the power to destroy", () => {
    // A surface may reasonably offer the reversible one and not the other.
    const fileOnly = board({ canArchive: true });
    expect(ids(taskMenuItems(fileOnly))).toContain("task.archive");
    expect(ids(taskMenuItems(fileOnly))).not.toContain("task.delete");
  });

  it("puts Delete last", () => {
    // A destructive entry sitting between two ordinary ones is one people
    // press by muscle memory.
    const shown = ids(taskMenuItems(live())).filter((id) => id !== "");
    expect(shown[shown.length - 1]).toBe("task.delete");
  });

  it("each verb calls its own action and nothing else", () => {
    const spy = spyActions();
    const actions = {
      ...spy,
      archive: (t: TaskRow) => spy.calls.push(`archive:${t.id}`),
      unarchive: (t: TaskRow) => spy.calls.push(`unarchive:${t.id}`),
      deleteTask: (t: TaskRow) => spy.calls.push(`delete:${t.id}`),
    };
    const run = (ctx: TaskMenuContext, id: string) => {
      const entry = taskMenuItems(ctx).find(
        (e) => e.kind === "item" && e.id === id,
      );
      if (entry && entry.kind === "item") entry.run(actions, ctx);
    };
    run(live(), "task.archive");
    run(filed(), "task.unarchive");
    run(live(), "task.delete");
    expect(spy.calls).toEqual(["archive:t1", "unarchive:t1", "delete:t1"]);
  });
});
