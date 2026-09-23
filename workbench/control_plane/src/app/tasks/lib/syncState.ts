/**
 * The sync-state decision table for a /tasks item (BO-1b).
 *
 * A GTD row's `syncState` answers three questions the UI keeps asking, and
 * before this module each of them was a `=== "pending"` comparison written out
 * at the call site. That was survivable while there were three states; the
 * Action Broker's `awaiting_approval` is the fourth, and every un-migrated
 * comparison silently answers it wrong — a queued task would offer Push again
 * (enqueuing a second proposal) and its stage picker would ask ClickUp about a
 * task that exists in no workspace.
 *
 * So the questions live here, as pure functions over the state, and the call
 * sites ask instead of comparing:
 *
 *   * `canPush`         — may the member push this to the PM tool?
 *   * `hasUpstreamTask` — does a task exist upstream to ask about?
 *   * `syncBadge`       — which chip says so?
 *
 * `syncBadge` returns a `MetaChip` DESCRIPTOR, never JSX and never a class:
 * `lib/taskCard.ts` owns the chip vocabulary and `components/TaskMeta.tsx` is
 * the only file that turns a `MetaTone` into a colour, per `DESIGN_SYSTEM.md`'s
 * "never write a colour" rule. Extending that seam is the point — a second tone
 * table here would be a second opinion about what "warning" looks like.
 */

import type { MetaChip } from "@/lib/taskCard";

/**
 * Every value the old task store's `sync_state` column could hold.
 *
 * `pending` and `awaiting_approval` are NOT synonyms and the difference is who
 * is being waited on: `pending` is staged for the MEMBER's own push (nothing
 * has been asked of anyone), `awaiting_approval` means the push already
 * happened and `ACTION_BROKER_ENFORCE` queued the outward write for a human
 * approver — the row is waiting on the `/actions` inbox, not on its owner.
 */
export type SyncState = "local" | "pending" | "synced" | "awaiting_approval";

/**
 * May this item be pushed to its PM tool?
 *
 * Only a locally-staged item. `awaiting_approval` is the case this function
 * exists for: the write is already queued in the Action Broker, so offering
 * Push again would enqueue a SECOND proposal for the same task and an approver
 * would create it twice.
 */
export function canPush(syncState: string | undefined): boolean {
  return syncState === "pending";
}

/**
 * Does a task exist in the PM tool for this row yet?
 *
 * False for both waiting states — a staged item was never written, and a queued
 * one is held at the broker. Callers use it to skip provider round trips
 * (task-specific stage options, detail fetches) that would 404 or, worse,
 * resolve against nothing and look empty.
 */
export function hasUpstreamTask(syncState: string | undefined): boolean {
  return !(syncState === "pending" || syncState === "awaiting_approval");
}

/**
 * The chip that explains a waiting state, or `null` when there is nothing to
 * say — `local` has no upstream to be out of step with, and `synced` is the
 * quiet case (the surface already shows the provider link).
 *
 * ⚠️ **The `pending` branch has no live render site today and is deliberately
 * DEFENSIVE, not dead-by-accident.** `ItemDetail.tsx`'s only call renders it at
 * `sync && !pushable`, and a `pending` item is by definition `canPush` — the
 * staged case is drawn instead by the Push affordance section right below,
 * which carries its own label and button. The branch exists so the table is
 * TOTAL over `SyncState`: any second caller that asks "which chip?" for a
 * waiting row gets the right answer for both waiting states rather than a
 * `null` it would have to special-case. Wiring it into the affordance section
 * is a UI change, not a correctness one, and is not this ticket's.
 */
export function syncBadge(syncState: string | undefined): MetaChip | null {
  if (syncState === "pending") {
    return {
      key: "sync:pending",
      icon: "Clock",
      label: "Not yet pushed",
      tone: "warning",
      title: "Staged locally — push it to create the task in the tool.",
    };
  }
  if (syncState === "awaiting_approval") {
    return {
      key: "sync:awaiting_approval",
      icon: "ShieldCheck",
      label: "Waiting for approval",
      tone: "accent",
      title:
        "The write to the tool is queued in the Action Broker and needs an " +
        "approval before it happens. Nothing exists in the tool yet.",
    };
  }
  return null;
}
