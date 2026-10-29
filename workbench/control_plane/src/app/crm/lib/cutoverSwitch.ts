// ── The switch to Metorite that keeps the records (WS-53 CRM-Z10b) ─────────
//
// Spec crm_platform.md §3.4, D133 (as amended 2026-10-29). The owner wants
// two switches, and each name says what happens to the records. This file
// holds the steps of the one that keeps them. Its label lives in `setup.ts`
// (`KEEP_RECORDS_SWITCH`), with every other change of setup. The dialog
// has five steps:
//
//   1. intro    — one sentence, and the counts behind a fold.
//   2. leave    — a "Leave out" tick for each module that is not complete.
//                 The step shows only when a module is not complete. A 409
//                 `module_incomplete` adds the modules that it names.
//   3. confirm  — the admin types SWITCH. It cannot be undone, so it asks
//                 first (§12 beat 4).
//   4. progress — POST start, then GET status every 3 seconds, then POST the
//                 switch with the run that the start began.
//   5. done     — what Zoho said to the revoke. Close reloads the mode.
//
// A failure goes back to step 3 with the reason (step 2 for a module), and
// keeps the word and the ticks. The gateway is the boundary: it checks each
// of these again.
//
// Pure: the reducer, the order of the calls and the words have tests
// (`cutoverSwitch.test.ts`, `components/crmZ10b.test.ts`). The dialog draws them.

import type {
  CutoverPreview,
  CutoverStatus,
  IncompleteRow,
  RevokeOutcome,
  StartResult,
  SwitchBody,
  SwitchResult,
} from "./cutover";
import { STATE_WORDS, incompleteOf } from "./cutover";
import { KEEP_RECORDS_SWITCH } from "./setup";

/** The one word the admin types. The gateway checks it, case and spaces too. */
export const SWITCH_WORD = "SWITCH";

/** The words of the button that opens the dialog, of its title and of the one that switches. */
export const SWITCH_LABEL: string = KEEP_RECORDS_SWITCH.label;

/** The status read waits this long between two reads. */
export const POLL_MS = 3000;

/** 200 reads of 3 seconds: 10 minutes. */
export const POLL_LIMIT = 200;

export type SwitchStep = "intro" | "leave" | "confirm" | "progress" | "done";

export type SwitchState = {
  /** The steps of this dialog, in order. "leave" only with a module to leave out. */
  steps: SwitchStep[];
  step: SwitchStep;
  /** The count keys that the admin ticked in "Leave out". */
  leaveOut: string[];
  /** Modules that a 409 `module_incomplete` named and the preview did not. */
  extraGaps: IncompleteRow[];
  /** What the admin typed. */
  typed: string;
  busy: boolean;
  /** What runs now, in step 4. */
  phase: "sync" | "switch" | null;
  /**
   * The dialog sent the switch at least once. After that, nobody knows from
   * here whether the mode changed, so a close reloads it.
   */
  committed: boolean;
  /**
   * The final run of a start whose sync took longer than the dialog waits
   * (`SYNC_TOO_LONG`). The next press reads that run first, and starts no
   * new one while it runs (review P3).
   */
  runId: string | null;
  error: string | null;
  result: SwitchResult | null;
};

export type SwitchEvent =
  | { type: "next" }
  | { type: "back" }
  | { type: "tick"; key: string; on: boolean }
  | { type: "type"; text: string }
  | { type: "submit" }
  | { type: "phase"; phase: "sync" | "switch" }
  | { type: "done"; result: SwitchResult }
  | { type: "failed"; message: string; gaps?: IncompleteRow[]; runId?: string | null };

/** The first state for one preview. */
export function initialSwitch(preview: CutoverPreview): SwitchState {
  const gaps = incompleteOf(preview).length > 0;
  return {
    steps: gaps ? ["intro", "leave", "confirm"] : ["intro", "confirm"],
    step: "intro",
    leaveOut: [],
    extraGaps: [],
    typed: "",
    busy: false,
    phase: null,
    committed: false,
    runId: null,
    error: null,
    result: null,
  };
}

/** The modules of the "Leave out" step: those of the preview, then those of a 409. */
export function switchGaps(preview: CutoverPreview, state: SwitchState): IncompleteRow[] {
  const gaps = incompleteOf(preview);
  const known = new Set(gaps.map((row) => row.key));
  return [...gaps, ...state.extraGaps.filter((row) => !known.has(row.key))];
}

/** Every module that is not complete is ticked. The gateway refuses the rest. */
export function allLeftOut(state: SwitchState, gaps: readonly IncompleteRow[]): boolean {
  return gaps.every((row) => state.leaveOut.includes(row.key));
}

/** The typed word matches exactly, and no call runs. */
export function canSwitch(state: SwitchState): boolean {
  return state.step === "confirm" && state.typed === SWITCH_WORD && !state.busy;
}

/** The state after a failure that names modules: the "Leave out" step shows them. */
function withGaps(state: SwitchState, gaps: IncompleteRow[]): SwitchState {
  const known = new Set(state.extraGaps.map((row) => row.key));
  const extraGaps = [...state.extraGaps, ...gaps.filter((row) => !known.has(row.key))];
  const steps: SwitchStep[] = state.steps.includes("leave")
    ? state.steps
    : ["intro", "leave", "confirm"];
  return { ...state, steps, extraGaps, step: "leave" };
}

export function switchReducer(state: SwitchState, event: SwitchEvent): SwitchState {
  const at = state.steps.indexOf(state.step);
  switch (event.type) {
    case "next":
      return at >= 0 && at < state.steps.length - 1
        ? { ...state, step: state.steps[at + 1], error: null }
        : state;
    case "back":
      return at > 0 && !state.busy ? { ...state, step: state.steps[at - 1], error: null } : state;
    case "tick": {
      const rest = state.leaveOut.filter((key) => key !== event.key);
      return { ...state, leaveOut: event.on ? [...rest, event.key] : rest };
    }
    case "type":
      return { ...state, typed: event.text, error: null };
    case "submit":
      return canSwitch(state)
        ? { ...state, step: "progress", busy: true, phase: "sync", error: null }
        : state;
    case "phase":
      return {
        ...state,
        phase: event.phase,
        committed: state.committed || event.phase === "switch",
      };
    case "done":
      return { ...state, step: "done", busy: false, phase: null, result: event.result };
    case "failed": {
      const failed: SwitchState = {
        ...state,
        step: "confirm",
        busy: false,
        phase: null,
        error: event.message,
        runId: event.runId ?? null,
      };
      return event.gaps && event.gaps.length > 0 ? withGaps(failed, event.gaps) : failed;
    }
  }
}

/**
 * A switch call that failed with no reason from the gateway, such as a lost
 * answer. The switch may have happened, so these words never say "Nothing
 * changed". A second press finishes it, or says it is done.
 */
export const SWITCH_FAILED =
  "Metorite did not get an answer from the switch, so it may be done. Press the button again to finish it.";

/**
 * The start got no answer, such as a lost 202 (review P3). The final sync may
 * run now, so these words never say that it did not start.
 */
export const START_FAILED =
  "Metorite did not get an answer. The final sync may run now. Wait a minute, then try again.";

/** The newest run is not the final run any more, so it is not the final sync. */
export const OTHER_RUN =
  "Another sync started after the final sync. Nothing changed. Press the button again.";

/** The words of a final sync that stopped, by its safe code. */
const SYNC_STOPPED: Record<string, string> = {
  budget: "The final sync stopped, because Zoho's daily credit limit is used up. Try again tomorrow.",
  needs_reconnect: "The final sync stopped, because Zoho asks you to connect again. Reconnect Zoho, then try again.",
  mode_changed: "The CRM changed while the final sync ran. Load the page again.",
  shutdown: "The final sync stopped, because the server restarted. Try again.",
  stale: "The final sync stopped answering. Try again.",
};

export function syncStoppedWords(code: string | null): string {
  return (code && SYNC_STOPPED[code]) || "The final sync did not finish. Nothing changed. Try again.";
}

/** The dialog keeps the run, and the next press waits for the same sync. */
export const SYNC_TOO_LONG =
  "The final sync takes longer than 10 minutes. Nothing changed yet. Press the button again to wait for the same sync.";

export type SwitchDeps = {
  start: (body: SwitchBody) => Promise<StartResult>;
  status: () => Promise<CutoverStatus>;
  commit: (body: SwitchBody) => Promise<SwitchResult>;
  wait: (ms: number) => Promise<void>;
  onPhase?: (phase: "sync" | "switch") => void;
};

export type SwitchOutcome =
  | { result: SwitchResult }
  /** `runId`: the final run that still runs, for the next press. */
  | { error: string; gaps?: IncompleteRow[]; runId?: string };

/** The `code` of a gateway refusal (`CrmError`), or undefined. */
function codeOf(err: unknown): string | undefined {
  const code = (err as { code?: unknown } | null)?.code;
  return typeof code === "string" ? code : undefined;
}

/** The message of a gateway refusal. A failure with no code gets *fallback*. */
function wordsOf(err: unknown, fallback: string): string {
  if (codeOf(err) === undefined) return fallback;
  return err instanceof Error && err.message ? err.message : fallback;
}

/**
 * The modules of a 409 `module_incomplete` (`detail.modules` of `CrmError`).
 * Any other error, or a body of another shape, gives none.
 */
export function gapsOf(err: unknown): IncompleteRow[] {
  if (codeOf(err) !== "module_incomplete") return [];
  const modules = (err as { detail?: { modules?: unknown } }).detail?.modules;
  if (!Array.isArray(modules)) return [];
  const rows: IncompleteRow[] = [];
  for (const item of modules) {
    const row = item as
      | { key?: unknown; label?: unknown; reason?: unknown; count?: unknown }
      | null;
    if (!row || typeof row.key !== "string" || !row.key) continue;
    const reason = typeof row.reason === "string" && row.reason in STATE_WORDS
      ? (row.reason as IncompleteRow["reason"])
      : "failed";
    const count = typeof row.count === "number" && row.count > 0 ? row.count : undefined;
    rows.push({
      key: row.key,
      label: typeof row.label === "string" ? row.label : row.key,
      reason,
      ...(count !== undefined ? { count } : {}),
    });
  }
  return rows;
}

/**
 * Phases 2 and 3. A refusal that names modules carries them, and the run
 * (round 4, P3-c): after the ticks, the next press switches with the same
 * final run while phase 2 still takes it. A new run would have less budget.
 */
async function commitStep(body: SwitchBody, deps: SwitchDeps): Promise<SwitchOutcome> {
  deps.onPhase?.("switch");
  try {
    return { result: await deps.commit(body) };
  } catch (err) {
    const gaps = gapsOf(err);
    const error = wordsOf(err, SWITCH_FAILED);
    if (gaps.length === 0) return { error };
    return body.run_id ? { error, gaps, runId: body.run_id } : { error, gaps };
  }
}

/**
 * The run that a press may use: the *kept* run of an earlier press, while
 * the status still names it and it runs (`ready` false) or is ready (`ready`
 * true). Else `null`, and the press starts a new final sync. Phase 2 still
 * decides: a run that is no longer the newest, or not fresh, is 409
 * `final_sync_missing`, and that answer keeps no run.
 */
async function keptRun(
  kept: string | null,
  deps: SwitchDeps,
): Promise<{ runId: string; ready: boolean } | null> {
  if (!kept) return null;
  const now = await deps.status().catch(() => null);
  if (!now || now.run_id !== kept) return null;
  if (now.state === "ready") return { runId: kept, ready: true };
  return now.state === "running" ? { runId: kept, ready: false } : null;
}

/**
 * Step 4: start the final sync, read the status every 3 seconds until it is
 * ready, then switch with the run that the start began. Returns the result,
 * or the words of the failure. It never throws.
 *
 * *kept* is the run of an earlier press: one whose sync took too long
 * (review P3), or one that phase 2 answered with modules to leave out
 * (round 4, P3-c). While it runs, this press waits for it. While it is
 * ready, this press switches with it at once. Either way it starts no new
 * run. Such a press hands its run back in `runId`.
 *
 * A start that answers `not_mirror` can follow a switch whose answer got
 * lost. Then the status says `done`, and the switch is sent again: the
 * gateway answers `already_done` and closes Zoho.
 */
export async function runSwitch(
  leaveOut: string[],
  deps: SwitchDeps,
  kept: string | null = null,
): Promise<SwitchOutcome> {
  const body: SwitchBody = { confirm: SWITCH_WORD, leave_out: [...leaveOut] };
  deps.onPhase?.("sync");
  const held = await keptRun(kept, deps);
  if (held?.ready) return commitStep({ ...body, run_id: held.runId }, deps);
  let runId = held?.runId ?? null;
  if (runId === null) {
    try {
      runId = (await deps.start(body)).run_id;
    } catch (err) {
      if (codeOf(err) === "not_mirror") {
        const now = await deps.status().catch(() => null);
        if (now?.state === "done") return commitStep(body, deps);
      }
      return { error: wordsOf(err, START_FAILED) };
    }
  }
  try {
    let ready = false;
    for (let read = 0; read < POLL_LIMIT && !ready; read++) {
      await deps.wait(POLL_MS);
      const status = await deps.status();
      if (status.state === "done") ready = true;
      else if (status.run_id !== runId) return { error: OTHER_RUN };
      else if (status.state === "ready") ready = true;
      else if (status.state === "failed") return { error: syncStoppedWords(status.error) };
      else if (status.state === "idle") return { error: syncStoppedWords(null) };
    }
    if (!ready) return { error: SYNC_TOO_LONG, runId };
  } catch (err) {
    return { error: wordsOf(err, syncStoppedWords(null)), runId };
  }
  return commitStep({ ...body, run_id: runId }, deps);
}

/** What Zoho said, in a member's words (§3.4 phase 3). */
const REVOKE_WORDS: Record<RevokeOutcome, string> = {
  confirmed: "Zoho confirmed that Metorite has no access to it now.",
  not_confirmed:
    "Zoho did not confirm that Metorite has no access to it now. In Zoho, remove Metorite from the connected apps.",
  not_configured:
    "Metorite could not ask Zoho to remove its access. In Zoho, remove Metorite from the connected apps.",
};

/** The lines of step 5. The first says the switch happened. */
export function resultLines(result: SwitchResult): string[] {
  const lines = ["Metorite is your CRM now. You can edit every record, and Zoho no longer syncs."];
  if (result.revoked) {
    lines.push(`Zoho is disconnected. ${REVOKE_WORDS[result.revoked]}`);
  } else if (result.zoho_connection === "disconnected" || result.zoho_connection === "none") {
    lines.push("Zoho was already disconnected.");
  } else {
    lines.push(
      "Zoho is still connected, because the disconnect did not finish. Press Finish closing Zoho in CRM settings.",
    );
  }
  return lines;
}

export const FINISH_FAILED = "Zoho is still connected. The disconnect did not finish. Try again.";

export const FINISH_NOT_SWITCHED =
  "This CRM did not switch from Zoho, so there is nothing to close. Load the page again.";

/**
 * "Finish closing Zoho" (§3.4 phase 3). It reads the status first, and posts
 * the switch again only when the status says `done` (verifier finding F6).
 * A tab that is old must not send the switch word for the admin. It never
 * throws.
 */
export async function finishClosing(deps: {
  status: () => Promise<CutoverStatus>;
  commit: (body: SwitchBody) => Promise<SwitchResult>;
}): Promise<{ lines: string[] } | { error: string }> {
  try {
    const now = await deps.status();
    if (now.state !== "done") return { error: FINISH_NOT_SWITCHED };
    const result = await deps.commit({ confirm: SWITCH_WORD, leave_out: [] });
    if (result.zoho_connection === "disconnected" || result.zoho_connection === "none") {
      return { lines: resultLines(result).slice(1) };
    }
    return { error: FINISH_FAILED };
  } catch (err) {
    return { error: err instanceof Error && err.message ? err.message : FINISH_FAILED };
  }
}

/**
 * Close the dialog. After a switch, the mode is read again (`invalidate`), so
 * the CRM opens in Native mode. Also after a switch call that failed: it may
 * have changed the mode. Not before: the dialog keeps its place until the
 * admin read the result. A call that runs keeps the dialog open.
 */
export function closeSwitch(
  state: SwitchState,
  deps: { reload: () => void; close: () => void },
): void {
  if (state.busy) return;
  if (state.step === "done" || state.committed) deps.reload();
  deps.close();
}

/** The one sentence of each step. */
export const STEP_WORDS: Record<Exclude<SwitchStep, "progress" | "done">, string> = {
  intro: "Stop using Zoho now. Metorite becomes your CRM and keeps every record from the last sync.",
  leave: "These modules are not complete. Tick each one to switch without its missing records.",
  confirm: "You cannot undo this. Metorite runs one final sync, then never syncs with this Zoho again.",
};

export const STEP_TITLES: Record<Exclude<SwitchStep, "progress" | "done">, string> = {
  intro: "Stop Zoho",
  leave: "Leave out",
  confirm: "Confirm",
};

/** What step 4 says while it runs (§10: what the app does now). */
export function progressWords(phase: SwitchState["phase"]): string {
  return phase === "switch" ? "Switching to Metorite…" : "Running the final sync from Zoho…";
}
