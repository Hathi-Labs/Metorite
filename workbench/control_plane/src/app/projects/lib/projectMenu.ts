/**
 * Projects · what a right-click on a PROJECT row offers (WS-27bg slice 2).
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
 * **The bulk close on Stop.** D-PM-26 says stopping a project should OFFER to
 * close its open tasks. That offer is a modal plus a bulk call plus a count the
 * user must be shown before agreeing, and it is deferred to its own slice
 * rather than smuggled in as a side effect of a menu item — which is precisely
 * the shape D-PM-26 forbids for the state change itself.
 */

import { PROJECT_STATES, PROJECT_STATE_ORDER } from "@/lib/statusAccent";

import type { ProjectRow } from "./api";
import { ownState } from "./tree";

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
 */
export interface ProjectMenuUi {
  /** Swap the row's label for its inline rename field. */
  onBeginRename: () => void;
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
  ui?: ProjectMenuUi
): ProjectMenuItem[] {
  const current = ownState(project);
  const archived = Boolean(project.archived_at);

  const items: ProjectMenuItem[] = [];

  // Rename leads, because it is the only entry here that edits the project
  // rather than filing or pausing it — and because until WS-27bg slice 2's
  // remainder there was NO project-editing affordance in the product at all
  // (one `patchProject` call site, and it wrote `status`). Offered on an
  // archived project too: filing a project does not make its name wrong, and
  // the endpoint has never refused the write.
  if (ui) {
    items.push({
      kind: "item",
      label: "Rename",
      icon: "PenLine",
      onSelect: ui.onBeginRename,
    });
    items.push({ kind: "sep" });
  }

  // A folder has no run state of its own (migration 193): it groups, and
  // its children inherit state from the nearest PROJECT ancestor. Offering
  // the list would write `status` onto a row nothing reads it from.
  if (project.kind === "folder") {
    items.push(...archiveItems(project, handlers, archived));
    return items;
  }

  items.push({ kind: "label", label: "Run state" });

  for (const state of PROJECT_STATE_ORDER) {
    const visual = PROJECT_STATES[state];
    items.push({
      kind: "item",
      label: visual.label,
      icon: visual.icon,
      checked: state === current,
      // Re-selecting the current state is a no-op the caller can skip, but the
      // item stays offered: a menu that hides the state you are in makes the
      // list jump between openings and hides where you actually are.
      onSelect: () => handlers.onSetState(project, state),
    });
  }

  items.push({ kind: "sep" });
  items.push(...archiveItems(project, handlers, archived));

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
