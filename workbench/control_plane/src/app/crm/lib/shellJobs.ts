// ── The CRM's jobs in the shell's command bar (WS-53 CRM-X2) ────────────────
//
// Spec: `project-docs/specs/crm_platform.md` section 19.6, with the notes of
// section 19.10. The bar names four CRM jobs (`src/lib/shell/registry.ts`):
// `new-lead`, `new-deal`, `log-call` and `find-record`. Each one opens a form
// or a list, and none saves (AGENTS rule 10: a job opens a form, a person
// saves it).
//
// Pure. The page mounts one `ShellJob` for each id and runs the plan here.
//
// ⚠️ The bar replaces the WHOLE query with `/crm?do=<job>`, so the open
// record is gone from the address when the job runs. The page keeps the view
// from before the job link and hands it here as `before`.

import {
  closeRecord,
  isEntity,
  parseView,
  selectLayout,
  selectRecordTab,
  selectTab,
  type CrmView,
} from "./urlState";
import type { EntitySlug } from "./types";

/** The four CRM jobs, in the order of `registry.ts`. */
export const CRM_JOB_IDS = ["new-lead", "new-deal", "log-call", "find-record"] as const;
export type CrmJobId = (typeof CRM_JOB_IDS)[number];

/** The line `log-call` shows when no record is open. */
export const LOG_CALL_HINT = "Open the record to log the call on";

/** What the page does for a job that opens a view. */
export type JobPlan = {
  /** The view to show. The page writes it in place of the job's address. */
  view: CrmView;
  /** Set the composer of the record's Timeline to Call. */
  composeCall: boolean;
  /** Focus the search box of the list (CRM-X1, `data-page-filter`). */
  focusSearch: boolean;
  /** A line for a toast, or null. */
  hint: string | null;
};

/** The list layout of one collection, with no record open. */
function listOf(base: CrmView, tab: EntitySlug): CrmView {
  return selectLayout(selectTab(closeRecord(base), tab), "list");
}

/**
 * `log-call`. With a record open, its Timeline, with the composer on Call.
 * With none, the Deals LIST and its search box (section 19.10: a board has
 * no search box), and a hint that says what to do next.
 */
export function logCallPlan(before: CrmView | null): JobPlan {
  if (before?.record) {
    return {
      view: selectRecordTab(before, "timeline"),
      composeCall: true,
      focusSearch: false,
      hint: null,
    };
  }
  return {
    view: listOf(before ?? parseView(""), "deals"),
    composeCall: false,
    focusSearch: true,
    hint: LOG_CALL_HINT,
  };
}

/** What `afterDialogsClose` needs from the browser. A test passes its own. */
export type FrameHost = {
  /** True while a dialog is in the document. */
  dialogOpen: () => boolean;
  nextFrame: (run: () => void) => number;
  cancelFrame: (id: number) => void;
};

/**
 * True while a dialog is open that does not hold `inside`. The `log-call`
 * composer sits in the record sheet, which is a dialog too, so only another
 * dialog, such as the closing command bar, is one to wait for.
 */
export function otherDialogOpen(
  dialogs: ArrayLike<{ contains: (node: never) => boolean }>,
  inside: unknown,
): boolean {
  return Array.from(dialogs).some((d) => !inside || !d.contains(inside as never));
}

/** The browser's host. With `inside`, the dialog that holds it does not count. */
export function browserHost(inside: Element | null = null): FrameHost {
  return {
    dialogOpen: () => otherDialogOpen(document.querySelectorAll('[role="dialog"]'), inside),
    nextFrame: (run) => requestAnimationFrame(run),
    cancelFrame: (id) => cancelAnimationFrame(id),
  };
}

/** Milliseconds a focus ask waits for its list. Then the page drops it. */
export const FOCUS_ASK_MS = 3000;

/** A job's ask to focus the search box of one list, and when it was made. */
export type FocusAsk = { entity: EntitySlug; at: number };

/**
 * What the page does with a focus ask, now that `listEntity` is on screen.
 * A list that is on screen in time gets the focus, and the ask is spent. An
 * ask older than `FOCUS_ASK_MS` is dropped, so a later visit to that list
 * never takes the focus by surprise.
 */
export function focusAskFor(
  ask: FocusAsk | null,
  listEntity: EntitySlug | null,
  now: number,
): { focus: boolean; keep: FocusAsk | null } {
  if (!ask || now - ask.at > FOCUS_ASK_MS) return { focus: false, keep: null };
  if (ask.entity === listEntity) return { focus: true, keep: null };
  return { focus: false, keep: ask };
}

/** Frames to wait after the last dialog leaves, for its focus cleanup. */
export const SETTLE_FRAMES = 2;
/** Frames to wait for a dialog to leave, about 1.5 s. Then the act runs anyway. */
export const MAX_WAIT_FRAMES = 90;

/**
 * Run `act` once no dialog is open, and return a cancel.
 *
 * ⚠️ A job runs while the command bar is still closing, and `find-record`
 * also closes a record sheet. Each `Modal` gives focus back when it has
 * finished closing (its `finalFocus`), so a focus set before then is lost.
 * Measured in the browser: the search box lost its focus to `<main>`.
 */
export function afterDialogsClose(act: () => void, host: FrameHost = browserHost()): () => void {
  let waited = 0;
  let settled = 0;
  let id = host.nextFrame(tick);
  function tick(): void {
    if (host.dialogOpen() && waited < MAX_WAIT_FRAMES) {
      waited += 1;
      settled = 0;
    } else if (settled < SETTLE_FRAMES) {
      settled += 1;
    } else {
      act();
      return;
    }
    id = host.nextFrame(tick);
  }
  return () => host.cancelFrame(id);
}

/**
 * `find-record`. The list of the collection on screen, or Deals when the view
 * is Reports or Settings, with its search box focused.
 */
export function findRecordPlan(before: CrmView | null): JobPlan {
  const base = before ?? parseView("");
  const tab: EntitySlug = isEntity(base.tab) ? base.tab : "deals";
  return { view: listOf(base, tab), composeCall: false, focusSearch: true, hint: null };
}
