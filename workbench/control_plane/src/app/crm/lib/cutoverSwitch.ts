// ── The switch to Metorite (WS-53 CRM-Z10b, spec crm_platform.md §3.4, D133) ──
//
// The dialog "Switch to Metorite" has five steps:
//
//   1. intro    — one sentence, and the counts behind a fold.
//   2. leave    — a "Leave out" tick for each module that is not complete.
//                 The step shows only when a module is not complete.
//   3. confirm  — the admin types SWITCH. The button reads "Switch to Metorite"
//                 (D134.1). It cannot be undone, so it asks first (§12 beat 4).
//   4. progress — POST start, then GET status every 3 seconds, then POST the
//                 switch.
//   5. done     — what Zoho said to the revoke. Close reloads the mode.
//
// A failure goes back to step 3 with the reason, and keeps the word and the
// ticks. The gateway is the boundary: it checks each of these again.
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
import { incompleteOf } from "./cutover";

/** The one word the admin types. The gateway checks it, case and spaces too. */
export const SWITCH_WORD = "SWITCH";

/** The words of the button that opens the dialog and of the one that switches (D134.1). */
export const SWITCH_LABEL = "Switch to Metorite";

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
  /** What the admin typed. */
  typed: string;
  busy: boolean;
  /** What runs now, in step 4. */
  phase: "sync" | "switch" | null;
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
  | { type: "failed"; message: string };

/** The first state for one preview. */
export function initialSwitch(preview: CutoverPreview): SwitchState {
  const gaps = incompleteOf(preview).length > 0;
  return {
    steps: gaps ? ["intro", "leave", "confirm"] : ["intro", "confirm"],
    step: "intro",
    leaveOut: [],
    typed: "",
    busy: false,
    phase: null,
    error: null,
    result: null,
  };
}

/** Every module that is not complete is ticked. The gateway refuses the rest. */
export function allLeftOut(state: SwitchState, gaps: readonly IncompleteRow[]): boolean {
  return gaps.every((row) => state.leaveOut.includes(row.key));
}

/** The typed word matches exactly, and no call runs. */
export function canSwitch(state: SwitchState): boolean {
  return state.step === "confirm" && state.typed === SWITCH_WORD && !state.busy;
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
      return { ...state, phase: event.phase };
    case "done":
      return { ...state, step: "done", busy: false, phase: null, result: event.result };
    case "failed":
      return { ...state, step: "confirm", busy: false, phase: null, error: event.message };
  }
}

export const SWITCH_FAILED = "The switch did not finish. Nothing changed. Try again.";

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

export const SYNC_TOO_LONG =
  "The final sync takes longer than 10 minutes. Nothing changed. Close this window, and try again later.";

export type SwitchDeps = {
  start: (body: SwitchBody) => Promise<StartResult>;
  status: () => Promise<CutoverStatus>;
  commit: (body: SwitchBody) => Promise<SwitchResult>;
  wait: (ms: number) => Promise<void>;
  onPhase?: (phase: "sync" | "switch") => void;
};

/**
 * Step 4: start the final sync, read the status every 3 seconds until it is
 * ready, then switch. Returns the result, or the words of the failure. It
 * never throws.
 */
export async function runSwitch(
  leaveOut: string[],
  deps: SwitchDeps,
): Promise<{ result: SwitchResult } | { error: string }> {
  const body: SwitchBody = { confirm: SWITCH_WORD, leave_out: [...leaveOut] };
  try {
    deps.onPhase?.("sync");
    const started = await deps.start(body);
    if (!started.already_fresh) {
      let ready = false;
      for (let read = 0; read < POLL_LIMIT && !ready; read++) {
        await deps.wait(POLL_MS);
        const status = await deps.status();
        if (status.state === "ready" || status.state === "done") ready = true;
        else if (status.state === "failed") return { error: syncStoppedWords(status.error) };
        else if (status.state === "idle") return { error: syncStoppedWords(null) };
      }
      if (!ready) return { error: SYNC_TOO_LONG };
    }
    deps.onPhase?.("switch");
    return { result: await deps.commit(body) };
  } catch (err) {
    return { error: err instanceof Error && err.message ? err.message : SWITCH_FAILED };
  }
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

/**
 * Close the dialog. After a switch, the mode is read again (`invalidate`), so
 * the CRM opens in Native mode. Not before: the dialog keeps its place until
 * the admin read the result. A call that runs keeps the dialog open.
 */
export function closeSwitch(
  state: SwitchState,
  deps: { reload: () => void; close: () => void },
): void {
  if (state.busy) return;
  if (state.step === "done") deps.reload();
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
