// ── The start-over dialog (WS-53 CRM-X0, spec crm_platform.md §19.11 item 5) ─
//
// Each change that needs an empty CRM opens this dialog: Switch to Metorite,
// Connect Zoho from Native mode, Start over, and a different Zoho account.
// Three steps, in this order:
//
//   1. counts   — the records of each entity, and that the change removes them.
//   2. download — "Download a copy" (GET /crm/export/all.zip). The dialog
//                 fetches the whole file, checks the answer, then saves it.
//                 The step is done when the file is saved, and a failure says
//                 so with a retry (review P2). The admin may also go on without it.
//   3. confirm  — the admin types DELETE. The button reads
//                 "Remove N records and continue".
//
// Pure: the reducer and the call have tests, and the component draws them.

import type { ResetThen } from "./setup";

export const CONFIRM_WORD = "DELETE";

export type StartOverStep = "counts" | "download" | "confirm";

export const STEPS: readonly StartOverStep[] = ["counts", "download", "confirm"];

/** The copy of step 2. `done` only after the file is whole and saved. */
export type DownloadState = "idle" | "busy" | "done" | "failed";

export type StartOverState = {
  step: StartOverStep;
  /** The copy (step 2). */
  download: DownloadState;
  /** What the admin typed (step 3). */
  typed: string;
  busy: boolean;
  error: string | null;
};

export type StartOverEvent =
  | { type: "next" }
  | { type: "back" }
  | { type: "download-start" }
  | { type: "downloaded" }
  | { type: "download-failed"; message: string }
  | { type: "type"; text: string }
  | { type: "submit" }
  | { type: "failed"; message: string };

export const INITIAL: StartOverState = {
  step: "counts",
  download: "idle",
  typed: "",
  busy: false,
  error: null,
};

/** The typed word matches. Case and spaces count, as the gateway checks. */
export function canConfirm(state: StartOverState): boolean {
  return state.step === "confirm" && state.typed === CONFIRM_WORD && !state.busy;
}

export function startOverReducer(state: StartOverState, event: StartOverEvent): StartOverState {
  const at = STEPS.indexOf(state.step);
  switch (event.type) {
    case "next":
      return at < STEPS.length - 1 ? { ...state, step: STEPS[at + 1], error: null } : state;
    case "back":
      return at > 0 && !state.busy ? { ...state, step: STEPS[at - 1], error: null } : state;
    case "download-start":
      return state.download === "busy" ? state : { ...state, download: "busy", error: null };
    case "downloaded":
      return { ...state, download: "done", error: null };
    case "download-failed":
      return { ...state, download: "failed", error: event.message };
    case "type":
      return { ...state, typed: event.text, error: null };
    case "submit":
      return canConfirm(state) ? { ...state, busy: true, error: null } : state;
    case "failed":
      return { ...state, busy: false, error: event.message };
  }
}

/** The words of the confirm button (§19.11 item 5). */
export function confirmLabel(total: number): string {
  return total === 1
    ? "Remove 1 record and continue"
    : `Remove ${total.toLocaleString("en-US")} records and continue`;
}

/** The sentence of step 1. */
export function countsSentence(total: number): string {
  return total === 0
    ? "This CRM holds no records. The change removes the setup only."
    : "This change removes every record below. You cannot undo it.";
}

/** What the call gives back (`POST /crm/reset`). */
export type ResetResult = {
  removed: Record<string, number>;
  mode: string | null;
  consent_url: string | null;
};

export const RESET_FAILED = "The CRM did not start over. Nothing was removed. Try again.";

export const DOWNLOAD_FAILED = "The copy did not download. Try again.";

/**
 * Step 2: get the whole ZIP, check the answer, then save it. Returns `null`
 * when the file is saved, or the words of the failure. It never throws, and a
 * refusal never becomes a saved file.
 */
export async function runDownload(download: () => Promise<void>): Promise<string | null> {
  try {
    await download();
    return null;
  } catch (err) {
    return err instanceof Error && err.message ? err.message : DOWNLOAD_FAILED;
  }
}

/**
 * The call, after the typed confirm. It sends the reset, then either follows
 * the Zoho consent (`then = "mirror"`) or wakes every CRM watcher. Returns
 * `null` on success, or the words of the failure. It never throws.
 */
export async function runStartOver(
  then: ResetThen,
  deps: {
    reset: (body: { confirm: string; then: ResetThen }) => Promise<ResetResult>;
    invalidate: () => void;
    go: (url: string) => void;
  },
): Promise<string | null> {
  let result: ResetResult;
  try {
    result = await deps.reset({ confirm: CONFIRM_WORD, then });
  } catch (err) {
    return err instanceof Error && err.message ? err.message : RESET_FAILED;
  }
  if (then === "mirror" && result.consent_url) {
    deps.go(result.consent_url);
    return null;
  }
  deps.invalidate();
  return null;
}
