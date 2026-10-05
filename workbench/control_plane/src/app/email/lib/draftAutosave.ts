// WS-17 EM-G3c-2 — the files and the autosave of the three email composers
// (`project-docs/specs/email_app_master_plan.md` §12.3.3b, items 10 to 14).
//
// ComposePanel, the inline reply of EmailDetail and the DraftCard of
// ConversationView share each rule here. Vitest runs with no DOM in this
// package, so a rule inside a component has no fence. Each rule lives here
// once, and `draftAutosave.test.ts` holds it.
import type { EmailAccount } from "./types";

// ── Item 10: the pick limit ─────────────────────────────────────────────

/**
 * The most bytes of files that one mail may carry from the composer.
 *
 * The Next proxy cuts a request body over 10,485,760 bytes
 * (`experimental.proxyClientMaxBodySize`), and the cut route answers 422.
 * The composer sends each file as base64, which is 4/3 of its bytes. So the
 * base64 of 7,500,000 bytes is 10,000,000 bytes, and the rest of the request
 * keeps 485,760 bytes. The limit holds for each provider, Outlook too.
 *
 * A workspace artifact is not in the count: the gateway reads its bytes, so
 * they never pass through the proxy.
 */
export const FILES_MAX_BYTES = 7_500_000;

/** The reason that a composer gives when it refuses a pick. */
export const FILES_TOO_LARGE = "Files can be 7.5 MB in all, at most.";

/** The bytes that a base64 text with no `data:` prefix decodes to. */
export function base64Bytes(b64: string): number {
  const n = b64.length;
  if (n === 0) return 0;
  const pad = b64.endsWith("==") ? 2 : b64.endsWith("=") ? 1 : 0;
  return Math.floor((n * 3) / 4) - pad;
}

/**
 * The reason to refuse a pick, or null to take it.
 *
 * `held` are the files that the mail holds already, as base64. `picked` are
 * the new files, with their size in bytes. A pick that makes the sum pass
 * {@link FILES_MAX_BYTES} is refused whole, as `projects/lib/importFlow.ts`
 * refuses an import that is too large.
 */
export function pickProblem(
  held: readonly { contentB64: string }[],
  picked: readonly { size: number }[],
): string | null {
  const heldBytes = held.reduce((sum, f) => sum + base64Bytes(f.contentB64), 0);
  const pickedBytes = picked.reduce((sum, f) => sum + f.size, 0);
  return heldBytes + pickedBytes > FILES_MAX_BYTES ? FILES_TOO_LARGE : null;
}

// ── Item 12: the wait ───────────────────────────────────────────────────

/** The wait after the last edit, for each draft but one. */
export const AUTOSAVE_WAIT_MS = 1_200;

/**
 * The wait after the last edit for a Gmail draft that holds a file.
 *
 * Each Gmail update reads each file of the draft and sends it again
 * (EM-G3a-f7). One autosave of a draft with a 5 MB file sent 9.44 MB up and
 * took 6.99 MB down, so an autosave after each pause of 1.2 seconds cost too
 * much. An Outlook update does not send the files again, so it keeps 1.2 s.
 */
export const GMAIL_FILES_WAIT_MS = 10_000;

/**
 * The wait of the next autosave.
 *
 * `draftHoldsFile` is `hasAttachments` of the draft row. ConversationView
 * reads it from the draft that it shows. ComposePanel and EmailDetail read it
 * from the row that the last save returned. The gateway keeps the flag true
 * after a save with files (`drafting.py`, sticky).
 */
export function autosaveWait(
  provider: EmailAccount["provider"] | undefined,
  draftHoldsFile: boolean,
): number {
  return provider === "gmail" && draftHoldsFile ? GMAIL_FILES_WAIT_MS : AUTOSAVE_WAIT_MS;
}

// ── Item 13: a failed save shows ───────────────────────────────────────

/** The state of the autosave, as each composer draws it. */
export type DraftStatus = "idle" | "saving" | "saved" | "not-saved" | "too-large";

/** The HTTP status that `gatewayFetch` puts on its error, or undefined. */
export function errorStatus(err: unknown): number | undefined {
  if (typeof err !== "object" || err === null) return undefined;
  const status = (err as { status?: unknown }).status;
  return typeof status === "number" ? status : undefined;
}

/**
 * The state after a save that failed. A 413 is "too-large", and each other
 * error is "not-saved". It reads the status code only, never the text.
 */
export function failedSaveStatus(err: unknown): "not-saved" | "too-large" {
  return errorStatus(err) === 413 ? "too-large" : "not-saved";
}

/** The words of a failed save, or null when the save did not fail. */
export function saveFailureText(status: DraftStatus): string | null {
  if (status === "too-large") return "Too large to save";
  if (status === "not-saved") return "Not saved";
  return null;
}

// ── Item 14: a failed send shows ───────────────────────────────────────

/**
 * The words of a failed send.
 *
 * `gatewayFetch` puts the `detail` of the gateway in the message. So a 413
 * shows "This mail is too large to send.", and the 502 of EM-T9 names the
 * file that it could not attach.
 */
export function sendFailureText(err: unknown): string {
  if (typeof err === "object" && err !== null) {
    const message = (err as { message?: unknown }).message;
    if (typeof message === "string" && message) return message;
  }
  return "Failed to send";
}

// ── Item 11: the flush ─────────────────────────────────────────────────

/** The timer calls of {@link createAutosave}. A test passes its own. */
export interface AutosaveTimers {
  set: (fire: () => void, ms: number) => unknown;
  clear: (handle: unknown) => void;
}

const BROWSER_TIMERS: AutosaveTimers = {
  set: (fire, ms) => setTimeout(fire, ms),
  clear: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

/**
 * One pending autosave of a composer.
 *
 * Before EM-G3c-2, the cleanup of each autosave effect cleared its timer. So
 * a close, a switch to another mail and an unmount each lost the last edit.
 * The cleanup now calls `hold`, which stops the timer and keeps the save.
 * A close, a switch and an unmount call `flush`, which runs it at once.
 */
export interface Autosave {
  /** Start the wait again for `run`. The pending save of before is dropped. */
  schedule(run: () => unknown, waitMs: number): void;
  /** Stop the timer, and keep the pending save for a flush. */
  hold(): void;
  /** Run the pending save at once. With no pending save, do nothing. */
  flush(): void;
  /** Drop the pending save: a discard, a send, or nothing to save. */
  cancel(): void;
  /** True while a save waits for its timer or for a flush. */
  readonly pending: boolean;
}

export function createAutosave(timers: AutosaveTimers = BROWSER_TIMERS): Autosave {
  let run: (() => unknown) | null = null;
  let handle: unknown = null;
  const stop = () => {
    if (handle !== null) timers.clear(handle);
    handle = null;
  };
  const fire = () => {
    const next = run;
    run = null;
    stop();
    if (next) void next();
  };
  return {
    schedule(next, waitMs) {
      stop();
      run = next;
      handle = timers.set(fire, waitMs);
    },
    hold: stop,
    flush: fire,
    cancel() {
      stop();
      run = null;
    },
    get pending() {
      return run !== null;
    },
  };
}
