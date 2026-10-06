// WS-17 EM-G3c-2 — the files and the autosave of the three email composers
// (`project-docs/specs/email_app_master_plan.md` §12.3.3b, items 10 to 14).
// WS-17 EM-G3c-3 closes the known limits f3, f5, f7, f8, f9 and f12 of it
// (§12.3.3c, items 1 to 6).
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

/** The bytes of the files of one pick. */
const pickBytes = (picked: readonly { size: number }[]) =>
  picked.reduce((sum, f) => sum + f.size, 0);

/**
 * The reason to refuse a pick, or null to take it.
 *
 * `held` are the files that the mail holds already, as base64. `picked` are
 * the new files, with their size in bytes. `readingBytes` are the bytes of
 * the picks that the composer still reads, which `held` does not show yet
 * (EM-G3c-3 item 5, EM-G3c-2-f3). A pick that makes the sum pass
 * {@link FILES_MAX_BYTES} is refused whole, as `projects/lib/importFlow.ts`
 * refuses an import that is too large.
 */
export function pickProblem(
  held: readonly { contentB64: string }[],
  picked: readonly { size: number }[],
  readingBytes = 0,
): string | null {
  const heldBytes = held.reduce((sum, f) => sum + base64Bytes(f.contentB64), 0);
  return heldBytes + readingBytes + pickBytes(picked) > FILES_MAX_BYTES ? FILES_TOO_LARGE : null;
}

/**
 * Count a pick as read, until its files join the mail (EM-G3c-3 item 5).
 *
 * It adds the bytes of `picked` to `counter`, and it gives the release. The
 * composer calls the release in a `finally`, after it adds the files, so a
 * read that fails frees its bytes too. A second call of the release does
 * nothing. No session resets the counter: a read of an old reply still ends.
 */
export function holdPick(
  counter: { current: number },
  picked: readonly { size: number }[],
): () => void {
  const bytes = pickBytes(picked);
  counter.current += bytes;
  let held = true;
  return () => {
    if (!held) return;
    held = false;
    counter.current -= bytes;
  };
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
 *
 * One save runs at a time (review round 1). A save that the timer or a flush
 * starts while the save before it runs waits until that save settles. So a
 * flush during the first save of a draft cannot make a second draft, and the
 * newer text lands last.
 *
 * A send, a discard and a pop-out end the draft of the composer, so they call
 * `drain` (review round 2). A close and a switch keep the draft, so they call
 * `flush`, and a save that waits still runs there.
 *
 * Each save carries the session of its composer (EM-G3c-3 item 1). A drain
 * drops only the saves of its own session. So the save of a reply that the
 * member closed still runs when a new reply sends. The DraftCard has one
 * draft for each card, so it passes session 0.
 */
export interface Autosave {
  /**
   * Start the wait again for `run`, a save of `session`. The pending save of
   * before is dropped.
   */
  schedule(run: () => unknown, waitMs: number, session?: number): void;
  /** Stop the timer, and keep the pending save for a flush. */
  hold(): void;
  /**
   * Start the pending save: at once when no save runs, else when the save
   * that runs settles. With no pending save, do nothing.
   */
  flush(): void;
  /**
   * Drop the pending save: nothing to save, or a switch after its flush. A
   * save that started, or that waits for the save before it, still runs.
   */
  cancel(): void;
  /**
   * Drop each save of `session` that did not start: the pending save, and
   * each save that waits for the save before it. A pending save of another
   * session starts, as on a flush, and a save of another session that waits
   * still runs (EM-G3c-3 item 1, EM-G3c-2-f7). The promise settles when the
   * end of the chain settles. It gives true when the drain dropped a save of
   * `session`.
   *
   * A send, a discard and a pop-out await it before they read the draft id.
   * So a save that runs gives its draft id first, and no older text lands
   * after the send (review round 2). A save scheduled after the drain runs.
   */
  drain(session?: number): Promise<boolean>;
  /**
   * Put `promise` at the head of the chain. Each save, and each drain, that
   * comes after waits until it settles (EM-G3c-3 item 6). A pop-out opens the
   * full composer at once and gives it the draft of the reply later.
   *
   * `apply` gets the value of `promise` in the order of the chain: after each
   * save before it settled, and before each save after it starts (review
   * round 1). A save of an older session cannot settle after it.
   */
  after<T>(promise: Promise<T>, apply?: (value: T) => void): void;
  /** True while a save waits for its timer or for a flush. */
  readonly pending: boolean;
}

const ignore = () => undefined;

export function createAutosave(timers: AutosaveTimers = BROWSER_TIMERS): Autosave {
  let run: (() => unknown) | null = null;
  let runSession = 0;
  let handle: unknown = null;
  // The last save that started or waits to start, until it settles.
  let last: Promise<void> | null = null;
  // One token for each save that waits for the save before it, with the
  // session of that save. A drain deletes the tokens of its session, and a
  // save whose token is gone does not start.
  const waiting = new Map<object, number>();
  const stop = () => {
    if (handle !== null) timers.clear(handle);
    handle = null;
  };
  /** Run one save. The save shows its own failure, so the chain ignores it. */
  const start = (next: () => unknown): Promise<void> => {
    try {
      return Promise.resolve(next()).then(ignore, ignore);
    } catch {
      return Promise.resolve();
    }
  };
  /** Make `mine` the end of the chain, until it settles. */
  const chain = (mine: Promise<void>) => {
    last = mine;
    void mine.then(() => {
      if (last === mine) last = null;
    });
  };
  const fire = () => {
    const next = run;
    const session = runSession;
    run = null;
    stop();
    if (!next) return;
    // With no save that runs, the save starts now and reads the state of now:
    // a switch flushes before it clears the draft id. Else it waits.
    if (last) {
      const token = {};
      waiting.set(token, session);
      chain(last.then(() => (waiting.delete(token) ? start(next) : undefined)));
    } else {
      chain(start(next));
    }
  };
  return {
    schedule(next, waitMs, session = 0) {
      stop();
      run = next;
      runSession = session;
      handle = timers.set(fire, waitMs);
    },
    hold: stop,
    flush: fire,
    cancel() {
      stop();
      run = null;
    },
    drain(session = 0) {
      let dropped = false;
      if (run !== null && runSession === session) {
        stop();
        run = null;
        dropped = true;
      } else {
        // A pending save of another session keeps its edit: it starts now.
        fire();
      }
      for (const [token, owner] of waiting) {
        if (owner !== session) continue;
        waiting.delete(token);
        dropped = true;
      }
      // A dropped save settles at once after the save before it, so the end
      // of the chain settles when each save that still runs has settled.
      return (last ?? Promise.resolve()).then(() => dropped);
    },
    after(promise, apply) {
      // The hand-over can fail, and each save after it still runs.
      chain((last ?? Promise.resolve()).then(() => promise).then(apply ?? ignore).then(ignore, ignore));
    },
    get pending() {
      return run !== null;
    },
  };
}

/**
 * What a pop-out gives the full composer when the drain of the reply settles
 * (EM-G3c-3 item 6): the draft of the reply, and its `hasAttachments`. With
 * no `draftId`, the composer makes its own draft.
 */
export interface DraftHandOver {
  draftId?: string;
  draftHasFile?: boolean;
}

/** The draft that the last autosave of a composer saved, and its session. */
export interface SavedDraft {
  session: number;
  from: string;
  id: string;
}

/**
 * The record of the last save, after a save that gave `next` (EM-G3c-3
 * review round 1).
 *
 * Sessions only go up, so a record of an older session never replaces the
 * record of a newer one. Else a slow save of an ended session wrote over the
 * draft that a pop-out handed over, and the next save of the new session made
 * a second draft. Each write of `lastSaveRef` goes through it.
 */
export function recordSave(prev: SavedDraft | null, next: SavedDraft): SavedDraft {
  return prev && prev.session > next.session ? prev : next;
}

/**
 * The draft that an autosave updates, or null to create one. A composer asks
 * it when the save runs, after the save before it settled (review round 1).
 *
 * A save of the live session updates the draft of the composer. A switch or
 * a new reply can end the session before a flushed save runs. That save
 * updates the draft that its own session saved last, in the same mailbox.
 * It never reads the draft of the new session.
 */
export function draftToUpdate(
  save: { session: number; from: string },
  live: { session: number; draftId: string | null },
  last: SavedDraft | null,
): string | null {
  if (save.session === live.session) return live.draftId;
  return last && last.session === save.session && last.from === save.from ? last.id : null;
}

/**
 * The drafts that a discard deletes: each draft of its session, in each
 * mailbox (EM-G3c-3 item 3, EM-G3c-2-f12).
 *
 * That is the draft of the composer while the session lives, the draft that
 * the session saved last, and each stale draft, with no id twice. The last
 * save counts in any mailbox. A change of From during the first save leaves
 * that draft in the old mailbox, and a discard that read the From of now did
 * not find it. The discard reads `session` and `stale` before its drain, and
 * it asks after the drain.
 */
export function draftsToDiscard(
  session: number,
  live: { session: number; draftId: string | null },
  last: SavedDraft | null,
  stale: readonly string[],
): string[] {
  const ids: string[] = [];
  const add = (id: string | null) => {
    if (id && !ids.includes(id)) ids.push(id);
  };
  if (live.session === session) add(live.draftId);
  if (last && last.session === session) add(last.id);
  for (const id of stale) add(id);
  return ids;
}

/**
 * The draft of the old mailbox that a save of an ended session makes stale,
 * or null (EM-G3c-3 item 4, EM-G3c-2-f5).
 *
 * The member changes the From while the first save runs, and then switches to
 * another mail. That save ends after its session ended, so no list holds its
 * draft. The next save of the session makes a draft in the new mailbox. Then
 * the draft that the session saved last is in another mailbox, and it goes.
 * The composer asks before it writes `lastSaveRef`.
 */
export function supersededDraft(
  save: { session: number; from: string },
  last: SavedDraft | null,
  savedId: string,
): string | null {
  return last && last.session === save.session && last.from !== save.from && last.id !== savedId
    ? last.id
    : null;
}
