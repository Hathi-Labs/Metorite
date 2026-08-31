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
 */

import type { StatusRow, TaskRow } from "./api";

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
    id: "task.select",
    // Says what the click will DO, not what the row currently is.
    label: (ctx) => (ctx.selected ? "Remove from selection" : "Select"),
    icon: "CheckSquare",
    group: 1,
    // Offered only where the surface actually drew a checkbox: an entry that
    // adds a row to a selection nothing can act on is a dead click.
    when: (ctx) => ctx.canSelect,
    run: (actions, ctx) => actions.toggleSelect(ctx.task),
  },
  {
    id: "task.status",
    label: () => "Change status",
    group: 2,
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
