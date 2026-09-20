/**
 * Projects · what a right-click on a task card offers (WS-27bd item 5).
 *
 * **A declared registry, not a chain of `if` branches inside a card.** This is
 * `lib/commands.ts`'s shape applied one scope down: every entry the menu can
 * draw is declared here with its label, its glyph, the context in which it is
 * offered, and what running it calls. Every card surface reads it, so two
 * surfaces cannot end up offering different words for one action —
 * which is the whole "one registry" point of the ticket.
 *
 * Everything is pure — no React, no DOM, no `navigator` — so the rules that are
 * wrong-but-plausible (an action offered on a surface that cannot perform it, a
 * separator opening the menu, the wrong status ticked) are assertions in
 * `taskMenu.test.ts` rather than clicks nobody can run in a node test runner.
 *
 * ## Its relationship to `lib/commands.ts` — read this before adding an entry
 *
 * `commands.ts` is the **page** registry: go somewhere, change the canvas,
 * widen the panel, manage the project. `taskMenu.ts` is the **task** registry:
 * act on the one row under the pointer. None of Open / Copy link / Select /
 * Change status exists in `commands.ts` and none of `commands.ts` acts on a
 * task, so the two are disjoint by construction — and `taskMenu.test.ts`
 * *enforces* the disjointness both ways. That test is what makes "no second
 * vocabulary" mechanical: the day somebody adds an `Open` command to the
 * palette, or copies `Clear the filters` onto a card, it goes red.
 *
 * ⚠️ The obvious-sounding alternative — have every menu entry BE a
 * `commands.ts` entry — was measured and is not buildable: the palette registry
 * has no task-scoped action to resolve to, and a card menu made of
 * "Widen the task panel / Custom fields / Import from ClickUp" is a junk drawer,
 * not a context menu. Extending `commands.ts` with task actions is a real
 * option; it is a change to the palette, the `g`/`v` sequences and the
 * shortcuts sheet all at once, so it is a ticket rather than a side effect.
 *
 * ## The card's hover strip reads this same registry
 *
 * Owner direction, 2026-09-20: hovering a board card offers Mark done, Add
 * subtask, Rename and a "more" button, the way ClickUp's card does. Those are
 * declared HERE, with a `quick` rank, rather than written into `TaskBoard`.
 *
 * ⚠️ **One registry, two presentations — not two registries.** A strip of
 * icon buttons and a right-click menu are the same list rendered differently,
 * so `taskQuickActions` is a second *reader* of `TASK_MENU_ACTIONS` and never
 * a second list. The day the strip and the menu disagree about what "Rename"
 * does, they disagree because somebody changed one declaration.
 *
 * `inMenu: false` is how an action stays out of the right-click menu.
 * `task.markDone` uses it: the menu already lists every lane by name, so
 * repeating one of them as a command would be the second-way-to-do-a-thing
 * §5 forbids. On the strip it is not a second way — it is the one gesture
 * that does not make you read four lane names to finish a task.
 */

import type { StatusRow, TaskRow } from "./api";
import { isResolved } from "./relations";

/**
 * Where the pointer is, so an entry can refuse to be offered when it would
 * no-op. The mirror of `commands.ts`'s `CommandContext`, one scope down.
 */
export interface TaskMenuContext {
  task: TaskRow;
  /**
   * The statuses this task's project offers. Empty on a surface that has no
   * status axis to move along, which is what hides the whole block rather
   * than drawing an empty heading.
   */
  statuses: readonly StatusRow[];
  /** The surface offers bulk selection — i.e. it drew a checkbox for this row. */
  canSelect: boolean;
  /** This task is already in that selection. */
  selected: boolean;
  /**
   * WS-27bl — the surface can raise the move card. Absent on a read-only
   * surface, and the entry is then dropped rather than greyed, which is the
   * rule `canSelect` above already follows.
   */
  canMoveToProject?: boolean;
  /**
   * The surface can edit the card in place — rename it, and compose a subtask
   * under it. False on a read-only list, and the entries drop rather than
   * grey, which is the rule `canSelect` and `canMoveToProject` already follow.
   */
  canEditInline?: boolean;
}

/**
 * The lane a "Mark done" click sends a task to, and the one "Reopen" sends it
 * back to.
 *
 * ⚠️ **Derived from the lanes, never from a flag.** `StatusRow.is_default` is
 * on the wire and read by nothing: its own header says the first lane by
 * position is where work starts, and that on the dev database the flag sat on
 * `backlog` for every space. So this applies that same rule at both ends —
 * the first CLOSED lane by position is where finished work goes, and the
 * first OPEN lane by position is where reopened work comes back to.
 *
 * `CLOSED` comes from `relations.ts`, which mirrors the gateway's
 * `CLOSING_CATEGORIES`. A second list of closing categories in this file is
 * exactly the mirror that goes stale and then lies.
 *
 * `undefined` when the project has no such lane — which is what drops the
 * button off the card instead of drawing one that cannot work.
 */
export function laneFor(
  statuses: readonly StatusRow[],
  end: "closed" | "open",
): StatusRow | undefined {
  const want = end === "closed";
  return [...statuses]
    .sort((a, b) => a.position - b.position)
    .find((status) => isResolved(status.category) === want);
}

/** This task's own lane category, or undefined on a surface with no axis. */
export function categoryOf(ctx: TaskMenuContext): string | undefined {
  return ctx.statuses.find((s) => s.id === ctx.task.status_id)?.category;
}

/** The task is sitting in a closed lane, so the tick is already on. */
export function isDone(ctx: TaskMenuContext): boolean {
  return isResolved(categoryOf(ctx));
}

/** Where the tick would send this task, or undefined if there is nowhere. */
export function doneTarget(ctx: TaskMenuContext): StatusRow | undefined {
  return laneFor(ctx.statuses, isDone(ctx) ? "open" : "closed");
}

/** What an entry is allowed to do. Every surface supplies all of these. */
export interface TaskMenuActions {
  /** Open the task — the same thing clicking the card does. */
  open(task: TaskRow): void;
  /** Put the task's deep link on the clipboard (`lib/card.taskDeepLink`). */
  copyLink(task: TaskRow): void;
  /** Add to / remove from the bulk selection. */
  toggleSelect(task: TaskRow): void;
  /** Move the task to another status. */
  setStatus(task: TaskRow, statusId: string): void;
  /**
   * WS-27bl §9.13.4 — open the move card for this task.
   *
   * ⚠️ It OPENS a card, it does not move. A move crosses two vocabularies, so
   * the member has to be shown the mapping and the losses before agreeing
   * (D-PM-29). Optional, so a read-only surface drops the entry rather than
   * offering a click that goes nowhere.
   */
  moveToProject?(task: TaskRow): void;
  /**
   * Open the inline subtask composer under this card.
   *
   * ⚠️ It OPENS a composer, it does not create. A subtask with no title is
   * a row somebody has to find and delete, so the click that adds one is the
   * one that has a title in it — the same reasoning `moveToProject` above
   * gives for opening a card rather than moving.
   */
  addSubtask?(task: TaskRow): void;
  /** Put this card's title into edit. Saving is the surface's business. */
  rename?(task: TaskRow): void;
}

/** One expanded row of a multi-row action (the status block). */
export interface TaskMenuRow {
  /** Appended to the action's id as `<action>:<suffix>`. */
  suffix: string;
  label: string;
  checked?: boolean;
}

export interface TaskMenuAction {
  /** Namespaced `task.*`, and disjoint from every `commands.ts` id. */
  id: string;
  /**
   * A function of the context rather than a string, because two of these flip:
   * Select ↔ Deselect. A label that lies about what the click will do is the
   * defect a toggle menu item exists to avoid.
   */
  label(ctx: TaskMenuContext): string;
  /** Lucide icon name; the theme picks the pack. Absent on expanded rows. */
  icon?: string;
  /**
   * Entries sharing a group are drawn together; a separator goes between two
   * groups that both produced rows. Group numbers, not literal separators, so
   * an action that is filtered out cannot leave a separator hanging.
   */
  group: number;
  /** A heading drawn above this action's rows. */
  heading?: string;
  /**
   * Drawn on the card's hover strip, at this rank. Absent = menu only.
   *
   * ⚠️ A quick action is ONE button, so an action that `expand`s cannot carry
   * a rank — `taskQuickActions` would have to pick one of its rows, and which
   * one would be a decision made in a renderer. `taskMenu.test.ts` fences it.
   */
  quick?: number;
  /** `false` keeps the action off the right-click menu. Default: on it. */
  inMenu?: boolean;
  /**
   * The strip draws this button in the accent, as a toggle that is ON.
   * Only meaningful with `quick`.
   */
  activeWhen?(ctx: TaskMenuContext): boolean;
  /** Offered only when this holds. Absent = always. */
  when?(ctx: TaskMenuContext): boolean;
  /**
   * One row (absent) or several. The status block is ONE action with N rows,
   * so "every emitted item traces back to a declared action" stays true — the
   * same `<kind>:<discriminator>` key grammar the card chips use.
   */
  expand?(ctx: TaskMenuContext): TaskMenuRow[];
  run(actions: TaskMenuActions, ctx: TaskMenuContext, suffix: string): void;
}

/**
 * THE registry. Everything a right-click on a task can offer.
 *
 * Deliberately short. A context menu is not a second toolbar: each entry here
 * is something you would otherwise have to open the panel to do.
 */
export const TASK_MENU_ACTIONS: readonly TaskMenuAction[] = [
  {
    id: "task.open",
    label: () => "Open",
    icon: "PanelRight",
    group: 0,
    run: (actions, ctx) => actions.open(ctx.task),
  },
  {
    id: "task.copyLink",
    label: () => "Copy link",
    icon: "Link2",
    group: 0,
    run: (actions, ctx) => actions.copyLink(ctx.task),
  },
  {
    id: "task.markDone",
    // The button is a toggle, so the label has to say which way it will go.
    label: (ctx) => (isDone(ctx) ? "Reopen" : "Mark done"),
    icon: "Check",
    group: 0,
    quick: 0,
    // ⚠️ Menu-only omission on purpose: the right-click menu already lists
    // every lane by name, and a "Mark done" command beside a "Done" lane is
    // two names for one write. See the file header.
    inMenu: false,
    activeWhen: isDone,
    // No closed lane means no tick — a project whose lanes are all open has
    // no notion of finished, and a button that moves a task to "Backlog"
    // under a checkmark would be lying about what it did.
    when: (ctx) => Boolean(doneTarget(ctx)),
    run: (actions, ctx) => {
      const target = doneTarget(ctx);
      if (target) actions.setStatus(ctx.task, target.id);
    },
  },
  // Group 1 acts on the task's own content. Group 2 on where it lives, and
  // on the selection. Group 3 is its state. The strip's order is `quick`,
  // which is independent — it follows the pointer, not the menu's reading.
  {
    id: "task.rename",
    label: () => "Rename",
    icon: "Pencil",
    group: 1,
    quick: 2,
    when: (ctx) => Boolean(ctx.canEditInline),
    run: (actions, ctx) => actions.rename?.(ctx.task),
  },
  {
    id: "task.addSubtask",
    label: () => "Add subtask",
    icon: "ListPlus",
    group: 1,
    quick: 1,
    when: (ctx) => Boolean(ctx.canEditInline),
    run: (actions, ctx) => actions.addSubtask?.(ctx.task),
  },
  {
    id: "task.select",
    // Says what the click will DO, not what the row currently is.
    label: (ctx) => (ctx.selected ? "Remove from selection" : "Select"),
    icon: "CheckSquare",
    group: 2,
    // Offered only where the surface actually drew a checkbox: an entry that
    // adds a row to a selection nothing can act on is a dead click.
    when: (ctx) => ctx.canSelect,
    run: (actions, ctx) => actions.toggleSelect(ctx.task),
  },
  {
    id: "task.moveToProject",
    label: () => "Move to project…",
    icon: "FolderInput",
    // Beside Select rather than with Open: both act on WHERE the task lives
    // in the tree, and the status block below is about the task's state.
    group: 2,
    when: (ctx) => Boolean(ctx.canMoveToProject),
    run: (actions, ctx) => actions.moveToProject?.(ctx.task),
  },
  {
    id: "task.status",
    label: () => "Change status",
    group: 3,
    heading: "Change status",
    when: (ctx) => ctx.statuses.length > 0,
    // The tick marks where the task IS, so the block reads as a choice rather
    // than a list of commands — /tasks' "Change stage" group, same grammar.
    expand: (ctx) =>
      ctx.statuses.map((status) => ({
        suffix: status.id,
        label: status.name,
        checked: status.id === ctx.task.status_id,
      })),
    run: (actions, ctx, suffix) => actions.setStatus(ctx.task, suffix),
  },
];

/** What a surface draws. Maps 1:1 onto `@/components/ContextMenu`'s `CtxItem`. */
export type TaskMenuEntry =
  | {
      kind: "item";
      /** `<action>` or `<action>:<discriminator>` — read with `taskMenuKind`. */
      id: string;
      label: string;
      icon?: string;
      checked?: boolean;
      run(actions: TaskMenuActions, ctx: TaskMenuContext): void;
    }
  | { kind: "label"; label: string }
  | { kind: "sep" };

/**
 * The action an emitted id belongs to.
 *
 * The same rule the card chips follow (`chipKind`): anything classifying an
 * entry reads the kind, never the whole key, so the status rows stay one
 * declared action rather than N undeclared ones.
 */
export function taskMenuKind(id: string): string {
  const at = id.indexOf(":");
  return at === -1 ? id : id.slice(0, at);
}

/**
 * The menu, for where the pointer is.
 *
 * Separators are derived from the group numbers at the end, so an action that
 * `when` filtered out cannot leave a separator opening or closing the menu —
 * which is the one visual defect a hand-written item list reliably ships.
 */
export function taskMenuItems(
  ctx: TaskMenuContext,
  registry: readonly TaskMenuAction[] = TASK_MENU_ACTIONS,
): TaskMenuEntry[] {
  const blocks: Array<{ group: number; entries: TaskMenuEntry[] }> = [];

  for (const action of registry) {
    // Skipped BEFORE the block logic, so a strip-only action cannot leave an
    // empty group behind and a separator with nothing on one side of it.
    if (action.inMenu === false) continue;
    if (action.when && !action.when(ctx)) continue;
    const rows = action.expand
      ? action.expand(ctx)
      : [{ suffix: "", label: action.label(ctx) }];
    // An `expand` that produced nothing draws nothing — heading included.
    if (rows.length === 0) continue;

    const entries: TaskMenuEntry[] = [];
    if (action.heading) entries.push({ kind: "label", label: action.heading });
    for (const row of rows) {
      entries.push({
        kind: "item",
        id: row.suffix ? `${action.id}:${row.suffix}` : action.id,
        label: row.label,
        icon: action.icon,
        checked: row.checked,
        run: (actions, at) => action.run(actions, at, row.suffix),
      });
    }

    const open = blocks.at(-1);
    if (open && open.group === action.group) open.entries.push(...entries);
    else blocks.push({ group: action.group, entries });
  }

  return blocks.flatMap((block, index) =>
    index === 0 ? block.entries : [{ kind: "sep" as const }, ...block.entries],
  );
}

/** One button on a card's hover strip. */
export interface TaskQuickAction {
  /** The declaring action's id. A quick action never expands, so never keyed. */
  id: string;
  label: string;
  icon: string;
  /** Drawn in the accent, as a toggle that is on (`task.markDone`). */
  active: boolean;
  run(actions: TaskMenuActions, ctx: TaskMenuContext): void;
}

/**
 * The card's hover strip, for where the pointer is.
 *
 * The second READER of `TASK_MENU_ACTIONS` — see the file header. It applies
 * the same `when` the menu does, so an action the surface cannot service is
 * absent from both, and it sorts by `quick` rather than by registry order so
 * the strip's left-to-right reading is a decision taken in this file.
 *
 * ⚠️ An action that `expand`s is skipped rather than drawn. One button cannot
 * be four lanes, and picking one of them here would be a product decision
 * made in a renderer.
 */
export function taskQuickActions(
  ctx: TaskMenuContext,
  registry: readonly TaskMenuAction[] = TASK_MENU_ACTIONS,
): TaskQuickAction[] {
  return registry
    .filter((action) => action.quick !== undefined && !action.expand)
    .filter((action) => !action.when || action.when(ctx))
    .sort((a, b) => (a.quick ?? 0) - (b.quick ?? 0))
    .map((action) => ({
      id: action.id,
      label: action.label(ctx),
      // A quick action with no glyph would draw an empty 24px hole. The
      // registry test requires one; this keeps the type honest.
      icon: action.icon ?? "Circle",
      active: action.activeWhen ? action.activeWhen(ctx) : false,
      run: (actions, at) => action.run(actions, at, ""),
    }));
}
