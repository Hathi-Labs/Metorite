/**
 * Projects · what the row menu on a PROJECT offers (WS-27bg slice 2).
 *
 * The third registry scope in this app, and the sibling of the two D-PM-23
 * already reconciled: `commands.ts` acts on the **page**, `taskMenu.ts` on the
 * **task under the pointer**, and this on the **project under the pointer**.
 * D-PM-23's condition applies, but note what it does and does NOT mean here.
 * The scopes stay disjoint by ACTING on different things — every entry below
 * receives the project under the pointer and can reach neither a task nor the
 * page, which `projectMenu.test.ts` asserts. It is deliberately NOT a
 * label-disjointness rule: archiving a task and archiving a project are
 * different actions that legitimately share a verb, on menus that open from
 * different rows, and a test forbidding that would be a fence against a
 * non-violation.
 *
 * Pure: no React, no DOM, no API client. The rules that are wrong-but-plausible
 * (offering "Pause" on a project that is already paused, offering Unarchive on
 * a project nobody archived, ticking the wrong state) are assertions rather
 * than clicks nobody can run in a node test runner.
 *
 * ## One menu, two ways in (owner directive 2026-09-06)
 *
 * The same list opens from a right-click AND from the row's own three-dot
 * button. A menu reachable only by right-click is a menu most people never
 * find, and the owner asked for both after using a product that offers both.
 * The button is the discovery path and the right-click is the fast one, so
 * they must not drift — which is why they share this one builder rather than
 * each assembling their own items.
 *
 * ## Why a vocabulary appears on a SPACE row and nowhere else
 *
 * Statuses, tags and custom fields are ROOT-scoped: one set per space,
 * inherited by every project and subproject under it. The tree row is a
 * precise thing to click, so offering the status editor on a leaf would say
 * the leaf has statuses of its own — the per-list override `StatusManager`
 * records that we deliberately do not have. `nodeLevel()` already calls a
 * root 'space', so the level test and the scope are the same test.
 *
 * ## What this deliberately does NOT offer, and why
 *
 * **Delete.** `DELETE /projects/nodes/{id}` exists and is an unrecoverable
 * cascade over the subtree, every task and every grant. It has never had a
 * control in the UI, and this ticket does not give it one — *"archive is the
 * default affordance and delete is deliberately harder to reach"* (spec §9.8.4)
 * is satisfied most strongly by archive being reachable and delete staying
 * exactly as unreachable as it was. Adding both in one slice would put a
 * one-click irreversible cascade next to its reversible twin, in a menu people
 * are still learning.
 *
 * **Copy link, Favourite, Duplicate, Sharing.** Four entries the owner's
 * reference product carries that we have no feature behind: selection lives in
 * page state with no `?project=` deep link to copy, nothing stores a favourite,
 * no endpoint duplicates a subtree, and Projects has no sharing surface — the
 * grants exist, the screen does not. A menu entry for a feature we do not have
 * is a bug report waiting to be filed.
 *
 * **The bulk close on Stop.** D-PM-26 says stopping a project should OFFER to
 * close its open tasks. That offer is a modal plus a bulk call plus a count the
 * user must be shown before agreeing, and it is deferred to its own slice
 * rather than smuggled in as a side effect of a menu item — which is precisely
 * the shape D-PM-26 forbids for the state change itself.
 */

import { PROJECT_STATES, PROJECT_STATE_ORDER } from "@/lib/statusAccent";

import type { ProjectRow } from "./api";
import { type ChildOption, hasRunState, type NodeLevel, ownState } from "./tree";

/**
 * One menu entry, in terms with no React in them.
 *
 * `icon` is a Lucide NAME, not a component — the same split `taskMenu.ts`
 * makes, so this module stays testable in a node runner and the surface does
 * the `themedIcon()` conversion at the one place it renders.
 */
export type ProjectMenuItem =
  | {
      kind: "item";
      label: string;
      icon?: string;
      checked?: boolean;
      onSelect: () => void;
    }
  | { kind: "label"; label: string }
  | { kind: "sep" };

/** What the menu can do. Supplied by the surface; none of it happens here. */
export interface ProjectMenuHandlers {
  onSetState: (project: ProjectRow, state: string) => void;
  onArchive: (project: ProjectRow) => void;
  onUnarchive: (project: ProjectRow) => void;
  /** Commit a new name. The surface owns the write, the toast and the refetch. */
  onRename: (project: ProjectRow, name: string) => void;
}

/**
 * View state the menu can raise, as opposed to writes it can perform.
 *
 * Kept as a SECOND parameter rather than folded into `ProjectMenuHandlers`
 * because the two have different owners: the handlers come from the page (they
 * PATCH, toast and refetch), while "put this row into its rename field" is the
 * tree's own local state and belongs nowhere near the page. Merging them would
 * force the page to hold a `beginRename` it cannot implement.
 *
 * Every field below is optional, and an absent one drops its entry rather than
 * greying it. A read-only tree therefore gets a menu of exactly what it can do,
 * and the same builder serves both.
 */
export interface ProjectMenuUi {
  /** Swap the row's label for its inline rename field. */
  onBeginRename: () => void;
  /**
   * WS-27bk §9.12.4 — open the "Move to…" picker for this row.
   *
   * ⚠️ **This is the KEYBOARD path, and it ships before the drag.** A tree
   * whose only re-parent gesture is a mouse drag excludes anybody who does not
   * use one. It also teaches the grammar: the picker greys what the server
   * would refuse and says why, so the rule arrives before the error.
   *
   * Optional, so a read-only tree omits it.
   */
  onMove?: () => void;
  /**
   * Open Space Settings — name, icon, icon colour (migration 194). Offered
   * on a SPACE only, and optional so a read-only tree can omit it.
   */
  onOpenSettings?: () => void;
  /**
   * Create a child of this row. Paired with `createOptions` — the grammar
   * decides WHICH children are legal (`childCreationOptions`), and passing the
   * options in rather than re-deriving them here keeps that one decision in
   * `tree.ts` where the `+` button already reads it.
   */
  onCreate?: (option: ChildOption) => void;
  createOptions?: readonly ChildOption[];
  /** The root-scoped vocabularies, and the archive policy. Space rows only. */
  onManageFields?: () => void;
  onManageStatuses?: () => void;
  onManageTags?: () => void;
  onManageLifecycle?: () => void;
}

/**
 * The items for one project row.
 *
 * The state list is built from `PROJECT_STATE_ORDER`, so a state added to the
 * vocabulary appears here without an edit — and one removed cannot linger as a
 * menu entry that PATCHes a value the server now refuses.
 *
 * **A project's OWN state is what gets ticked, never its effective one.** The
 * menu writes `pm_projects.status` on this row; ticking an inherited value
 * would tell the user their department's pause is this project's own setting,
 * and the next click would write it as one — turning a derived fact into a
 * stored one, which is exactly what D-PM-26 exists to prevent.
 */
export function projectMenuItems(
  project: ProjectRow,
  handlers: ProjectMenuHandlers,
  ui?: ProjectMenuUi,
  /**
   * Which level this row occupies. It decides what the menu may offer:
   * a run state on a project/subproject, the space-scoped screens on a space.
   * Defaults to 'project' so an older caller keeps today's menu.
   */
  level: NodeLevel = "project"
): ProjectMenuItem[] {
  const current = ownState(project);
  const archived = Boolean(project.archived_at);
  const isSpace = level === "space";

  /**
   * Groups, joined by separators at the end.
   *
   * Assembled this way rather than by pushing a `sep` after each block because
   * every block here is conditional, and an inline separator after an empty
   * block draws a rule against nothing — which is what a subproject's menu
   * (no create options, no space screens) would have shown.
   */
  const groups: ProjectMenuItem[][] = [];

  // Rename leads, because it is the only entry here that edits the project
  // rather than filing or pausing it — and because until WS-27bg slice 2's
  // remainder there was NO project-editing affordance in the product at all
  // (one `patchProject` call site, and it wrote `status`). Offered on an
  // archived project too: filing a project does not make its name wrong, and
  // the endpoint has never refused the write.
  if (ui) {
    const edit: ProjectMenuItem[] = [
      {
        kind: "item",
        label: "Rename",
        icon: "PenLine",
        onSelect: ui.onBeginRename,
      },
    ];
    // Beside Rename, because both edit what the node IS rather than filing or
    // pausing it. A move is the second such act the product has.
    if (ui.onMove) {
      edit.push({
        kind: "item",
        label: "Move to…",
        icon: "FolderInput",
        onSelect: ui.onMove,
      });
    }
    groups.push(edit);
  }

  // Create, from the same grammar the row's `+` button reads. A flat labelled
  // group rather than a flyout: `ContextMenu` has no submenus on purpose, and
  // "New folder" already names its own level, so a heading plus two items says
  // everything a flyout would.
  const options = ui?.onCreate ? (ui.createOptions ?? []) : [];
  if (ui?.onCreate && options.length > 0) {
    const create: ProjectMenuItem[] = [{ kind: "label", label: "Create" }];
    for (const option of options) {
      create.push({
        kind: "item",
        label: option.label,
        icon: option.kind === "folder" ? "Folder" : "Plus",
        onSelect: () => ui.onCreate?.(option),
      });
    }
    groups.push(create);
  }

  // The space-scoped screens. All four are root-scoped, and `nodeLevel()`
  // calls a root a space, so one test gates the lot.
  if (isSpace && ui) {
    const scoped: ProjectMenuItem[] = [];
    if (ui.onOpenSettings) {
      scoped.push({
        kind: "item",
        label: "Space settings",
        icon: "Settings",
        onSelect: ui.onOpenSettings,
      });
    }
    // Statuses lead the vocabularies, as they do in the header menu: theirs is
    // the one whose category half drives the roll-up, completion, and what
    // /tasks shows.
    if (ui.onManageStatuses) {
      scoped.push({
        kind: "item",
        label: "Statuses",
        icon: "Columns3",
        onSelect: ui.onManageStatuses,
      });
    }
    if (ui.onManageFields) {
      scoped.push({
        kind: "item",
        label: "Custom fields",
        icon: "SlidersHorizontal",
        onSelect: ui.onManageFields,
      });
    }
    if (ui.onManageTags) {
      scoped.push({
        kind: "item",
        label: "Tags",
        icon: "Tag",
        onSelect: ui.onManageTags,
      });
    }
    if (ui.onManageLifecycle) {
      scoped.push({
        kind: "item",
        label: "Lifecycle policy",
        icon: "History",
        onSelect: ui.onManageLifecycle,
      });
    }
    if (scoped.length > 0) groups.push(scoped);
  }

  // Only a project or a subproject owns a run state (owner directive
  // 2026-08-31). A space summarises and a folder groups; neither DOES work.
  // The server refuses the write — this is the courtesy in front of it.
  if (hasRunState(level)) {
    const states: ProjectMenuItem[] = [{ kind: "label", label: "Run state" }];
    for (const state of PROJECT_STATE_ORDER) {
      const visual = PROJECT_STATES[state];
      states.push({
        kind: "item",
        label: visual.label,
        icon: visual.icon,
        checked: state === current,
        // Re-selecting the current state is a no-op the caller can skip, but
        // the item stays offered: a menu that hides the state you are in makes
        // the list jump between openings and hides where you actually are.
        onSelect: () => handlers.onSetState(project, state),
      });
    }
    groups.push(states);
  }

  groups.push(archiveItems(project, handlers, archived));

  const items: ProjectMenuItem[] = [];
  for (const group of groups) {
    if (items.length > 0) items.push({ kind: "sep" });
    items.push(...group);
  }
  return items;
}

/**
 * The other axis (D-PM-25): filing is not a run state, so it is not in the
 * state list. Exactly one of the two is ever offered — and it is offered on
 * folders too, because filing a grouping node files its subtree, which is
 * exactly what grouping is for.
 */
function archiveItems(
  project: ProjectRow,
  handlers: ProjectMenuHandlers,
  archived: boolean
): ProjectMenuItem[] {
  return [
    archived
      ? {
          kind: "item",
          label: "Unarchive",
          icon: "ArchiveRestore",
          onSelect: () => handlers.onUnarchive(project),
        }
      : {
          kind: "item",
          label: "Archive",
          icon: "Archive",
          onSelect: () => handlers.onArchive(project),
        },
  ];
}
