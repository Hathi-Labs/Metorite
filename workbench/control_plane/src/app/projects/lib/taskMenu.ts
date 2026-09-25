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

import { TASK_ACT_LABEL, doneLabel } from "@/lib/taskMenuVocabulary";

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
   * Absent or false where a merge cannot be offered: a read-only surface,
   * or a task that has already been merged away. A merged stub is on the
   * Archived shelf where Delete and Unarchive are the useful verbs; merging
   * it again is refused by the gateway, so offering it would be a click
   * that only ever produces an error.
   */
  canMerge?: boolean;
  /**
   * The surface can edit the card in place — rename it, and compose a subtask
   * under it. False on a read-only list, and the entries drop rather than
   * grey, which is the rule `canSelect` and `canMoveToProject` already follow.
   */
  canEditInline?: boolean;
  /**
   * The surface can file a task and bring it back.
   *
   * ⚠️ Absent on a read-only list, and the entries then DROP rather than
   * grey — the rule `canSelect`, `canMoveToProject` and `canEditInline`
   * already follow.
   */
  canArchive?: boolean;
  /** The surface can delete a task for good. Separate from `canArchive`
   * because they are different powers: filing is reversible and deleting is
   * not, and a surface may reasonably offer the first and not the second. */
  canDelete?: boolean;
}

/**
 * The lane a "Mark done" click sends a task to, and the one "Reopen" sends it
 * back to.
 *
 * ⚠️ **Derived from the lanes, never from a flag.** `StatusRow.is_default` is
 * on the wire and read by nothing: its own header says the first lane by
 * position is where work starts, and that on the dev database the flag sat on
 * `backlog` for every space. So position is the tie-break at both ends.
 *
 * ⚠️ **`done` OUTRANKS `cancelled`, and position only breaks the tie.**
 * Ranking by position alone was wrong and shipped in the first draft: a space
 * that orders Backlog 10, To do 20, Cancelled 30, Done 40 got a tick labelled
 * "Mark done" that CANCELLED the task. Both the label and the glyph said the
 * opposite of the write.
 *
 * ⚠️ This deliberately disagrees with the gateway's `_closing_status`
 * (`routes/projects/automation.py`), which prefers `cancelled`. That is not
 * drift — the two answer different questions. Auto-close sweeps work nobody
 * has touched for months, and its docstring says why: "a task nobody has
 * touched for months was abandoned, not finished, and calling it done would
 * inflate every completion report." A member pressing a tick is stating the
 * opposite. Same vocabulary, opposite intent, so the rankings are opposite on
 * purpose and each says so.
 *
 * `CLOSED` still comes from `relations.ts`, which mirrors the gateway's
 * `CLOSING_CATEGORIES` — WHICH categories close is one fact with one home.
 * Only the ranking within them is local.
 *
 * `undefined` when the project has no such lane — which is what drops the
 * button off the card instead of drawing one that cannot work.
 */
export function laneFor(
  statuses: readonly StatusRow[],
  end: "closed" | "open",
): StatusRow | undefined {
  const byPosition = [...statuses].sort((a, b) => a.position - b.position);
  if (end === "open") return byPosition.find((s) => !isResolved(s.category));
  for (const wanted of ["done", "cancelled"]) {
    const hit = byPosition.find((s) => s.category === wanted);
    if (hit) return hit;
  }
  // A closing category we do not rank by name: `CLOSED` is the authority on
  // WHICH categories close, so a new one added there still finds a lane here
  // rather than silently removing the tick.
  return byPosition.find((s) => isResolved(s.category));
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
  /**
   * Add to / remove from the bulk selection.
   *
   * `shift` extends a RANGE from the last anchor, exactly as shift-clicking
   * the card's old left checkbox did. That gutter is gone (owner,
   * 2026-09-20), so the strip's tick box is the only checkbox on a board card
   * and it carries the modifier. The right-click menu passes nothing: a
   * right-click has no anchor to extend from.
   */
  toggleSelect(task: TaskRow, shift?: boolean): void;
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
   * Open the merge card for this task (or for the whole selection).
   *
   * ⚠️ It OPENS a card, it does not merge — `moveToProject`'s rule, and
   * for a stronger reason. A merge moves comments, history and attachments
   * one way and re-running it does not undo it, so the member has to see
   * WHICH task survives before agreeing. A menu entry that merged on click
   * would be the most destructive single click in the product.
   */
  mergeInto?(task: TaskRow): void;
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
  /**
   * File the task out of every default list, board, calendar and search.
   *
   * **Offered on any status**, because archive is a shelf rather than an
   * outcome: hidden now, reversible, may come back. The gateway refused an
   * open task until 2026-09-21 and this menu deliberately still offered the
   * entry, so the refusal could teach the rule. The rule is gone and the
   * entry stays, which is the simpler product.
   */
  archive?(task: TaskRow): void;
  /** Bring one back from the archive. No guard in this direction. */
  unarchive?(task: TaskRow): void;
  /**
   * Delete for good. The surface confirms — this only asks for it.
   *
   * Named `deleteTask` rather than `delete`, which is a reserved word and
   * reads as an object operation at every call site.
   */
  deleteTask?(task: TaskRow): void;
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
   * Drawn at the END of the strip, past the "more" button and behind a rule.
   *
   * For a control that is a MODE rather than an act. `task.select` is the
   * only one: Mark done, Rename and Add subtask all do something to the task
   * and then they are over, while Select puts the board into a state. Owner
   * direction, 2026-09-20, pointing at the reference card where the tick box
   * sits apart from the action glyphs.
   */
  trailing?: boolean;
  /**
   * The strip draws this button as a toggle that is ON.
   * Only meaningful with `quick`.
   */
  activeWhen?(ctx: TaskMenuContext): boolean;
  /**
   * WHICH colour "on" is — and the two are not interchangeable.
   *
   * ⚠️ `Checkbox.tsx`'s header owns this rule. The member's accent means
   * SELECTION, so `task.select` is `primary`. A lane colour is a fact about
   * the work, so `task.markDone` is `success`. Painting done-ness in the
   * accent makes it change when a member changes theirs, which is the defect
   * the visual rig's `underAccents` exists to catch.
   */
  activeTone?: "primary" | "success";
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
/** Has somebody filed this task? */
export const isArchived = (ctx: TaskMenuContext): boolean =>
  Boolean(ctx.task.archived_at);

export const TASK_MENU_ACTIONS: readonly TaskMenuAction[] = [
  {
    id: "task.open",
    // Every shared label comes from `@/lib/taskMenuVocabulary`, the one list
    // both task apps read. This registry's ORDER must follow that list too.
    label: () => TASK_ACT_LABEL.open,
    icon: "PanelRight",
    group: 0,
    run: (actions, ctx) => actions.open(ctx.task),
  },
  {
    id: "task.copyLink",
    label: () => TASK_ACT_LABEL.copyLink,
    icon: "Link2",
    group: 0,
    run: (actions, ctx) => actions.copyLink(ctx.task),
  },
  {
    id: "task.markDone",
    // The button is a toggle, so the label has to say which way it will go.
    label: (ctx) => doneLabel(isDone(ctx)),
    icon: "Check",
    group: 0,
    quick: 0,
    // ⚠️ Menu-only omission on purpose: the right-click menu already lists
    // every lane by name, and a "Mark done" command beside a "Done" lane is
    // two names for one write. See the file header.
    inMenu: false,
    activeWhen: isDone,
    activeTone: "success",
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
    label: () => TASK_ACT_LABEL.rename,
    icon: "Pencil",
    group: 1,
    quick: 2,
    when: (ctx) => Boolean(ctx.canEditInline),
    run: (actions, ctx) => actions.rename?.(ctx.task),
  },
  {
    id: "task.addSubtask",
    label: () => TASK_ACT_LABEL.addSubtask,
    icon: "ListPlus",
    group: 1,
    quick: 1,
    when: (ctx) => Boolean(ctx.canEditInline),
    run: (actions, ctx) => actions.addSubtask?.(ctx.task),
  },
  {
    id: "task.select",
    // Says what the click will DO, not what the row currently is.
    label: (ctx) => (ctx.selected ? "Remove from selection" : TASK_ACT_LABEL.select),
    icon: "CheckSquare",
    group: 2,
    // On the strip too, and LAST (owner, 2026-09-20). It is the way INTO
    // selection now that the card's own checkbox no longer appears on hover
    // — without it the only door is the right-click menu, and a member who
    // does not right-click could not select anything at all.
    quick: 3,
    trailing: true,
    activeWhen: (ctx) => ctx.selected,
    // The accent, because a checked box is a selection. See `activeTone`.
    activeTone: "primary",
    // Offered only where the surface actually drew a checkbox: an entry that
    // adds a row to a selection nothing can act on is a dead click.
    when: (ctx) => ctx.canSelect,
    run: (actions, ctx) => actions.toggleSelect(ctx.task),
  },
  {
    id: "task.moveToProject",
    label: () => TASK_ACT_LABEL.moveToProject,
    icon: "FolderInput",
    // Beside Select rather than with Open: both act on WHERE the task lives
    // in the tree, and the status block below is about the task's state.
    group: 2,
    when: (ctx) => Boolean(ctx.canMoveToProject),
    run: (actions, ctx) => actions.moveToProject?.(ctx.task),
  },
  {
    id: "task.mergeInto",
    label: () => TASK_ACT_LABEL.mergeInto,
    icon: "Merge",
    // Beside Move: both are about a task's PLACE rather than its state, and
    // both open a card instead of acting. The status block below is the
    // task's own state and is a different question.
    group: 2,
    when: (ctx) => Boolean(ctx.canMerge),
    run: (actions, ctx) => actions.mergeInto?.(ctx.task),
  },
  {
    id: "task.status",
    label: () => TASK_ACT_LABEL.status,
    group: 3,
    heading: TASK_ACT_LABEL.status,
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
  {
    id: "task.archive",
    label: () => TASK_ACT_LABEL.archive,
    icon: "Archive",
    // Group 4: the lifecycle verbs sit below the status block, because they
    // are about whether the task is on the board at all rather than where on
    // it. Delete is last, where a destructive entry belongs.
    group: 4,
    when: (ctx) => Boolean(ctx.canArchive) && !isArchived(ctx),
    run: (actions, ctx) => actions.archive?.(ctx.task),
  },
  {
    id: "task.unarchive",
    label: () => "Restore from archive",
    icon: "ArchiveRestore",
    group: 4,
    when: (ctx) => Boolean(ctx.canArchive) && isArchived(ctx),
    run: (actions, ctx) => actions.unarchive?.(ctx.task),
  },
  {
    id: "task.delete",
    label: () => TASK_ACT_LABEL.delete,
    icon: "Trash2",
    group: 5,
    when: (ctx) => Boolean(ctx.canDelete),
    run: (actions, ctx) => actions.deleteTask?.(ctx.task),
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
  /** This toggle is ON. */
  active: boolean;
  /** Which colour "on" is. See `TaskMenuAction.activeTone`. */
  tone: "primary" | "success";
  /** Drawn past the "more" button, behind a rule. See `trailing`. */
  trailing: boolean;
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
      tone: action.activeTone ?? "primary",
      trailing: action.trailing === true,
      run: (actions, at) => action.run(actions, at, ""),
    }));
}
