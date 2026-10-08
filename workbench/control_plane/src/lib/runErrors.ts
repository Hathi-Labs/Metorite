/**
 * What a failed chat turn says to the member: one stable code, and our words.
 *
 * Owner report, 2026-10-08. A Projects chat showed the member the raw Python
 * repr of an `APIConnectionError`, with a class name that ran past the edge
 * of the box, and then told them to run `sudo journalctl` on our server.
 *
 * ## The rule
 *
 * The SERVER names the failure, because it holds the exception
 * (`packages/acb_llm/acb_llm/run_errors.py`). This file maps the name to
 * words. **Nothing here parses a Python repr.** The raw text travels beside
 * the code, and the card shows it only inside the "Show full error" fold.
 *
 * `RUN_ERROR_WORDS` must hold every code in the Python `RUN_ERROR_CODES`.
 * `tests/unit/test_run_errors.py` reads this file and fails when they differ.
 * A code this file does not know draws the `unknown` words.
 *
 * Fences: `runErrors.test.ts` (the words, the fold, the operator hint, the
 * retry target) and `tests/unit/test_run_errors.py` (the vocabulary).
 */

export type RunErrorCode =
  | "connection"
  | "timeout"
  | "rate_limited"
  | "credits"
  | "model_refused"
  | "permission"
  | "cancelled"
  | "run_in_progress"
  | "signed_out"
  | "unknown";

export interface RunErrorWords {
  title: string;
  body: string;
  /** False where pressing Retry cannot help, so the card offers none. */
  retry: boolean;
}

/** The member's words for each code. Product words, no class names. */
export const RUN_ERROR_WORDS: Record<RunErrorCode, RunErrorWords> = {
  connection: {
    title: "Connection lost",
    body:
      "Metorite lost its connection while answering. This usually happens when the app updates. " +
      "Your message is saved. Press Retry.",
    retry: true,
  },
  timeout: {
    title: "The answer took too long",
    body:
      "The AI did not answer in time. Your message is saved. Press Retry. " +
      "If it happens again, ask for a smaller piece of work.",
    retry: true,
  },
  rate_limited: {
    title: "The AI is busy",
    body: "The AI service has too many requests right now. Wait a minute, then press Retry.",
    retry: true,
  },
  credits: {
    title: "Out of AI credits",
    body:
      "Your organization is out of AI credits. An admin can add credits in Settings, Billing. " +
      "Your message is saved.",
    retry: false,
  },
  model_refused: {
    title: "The AI could not take this request",
    body:
      "The AI model refused this request. Try a shorter message, or start a new chat " +
      "if this conversation is long.",
    retry: true,
  },
  permission: {
    title: "Not allowed",
    body:
      "Metorite refused this request. You may not have access to it, or your organization " +
      "reached a spending limit. Ask an admin of your organization.",
    retry: false,
  },
  cancelled: {
    title: "Stopped",
    body: "This answer stopped before it finished. Press Retry to ask again.",
    retry: true,
  },
  run_in_progress: {
    title: "Another answer is in progress",
    body:
      "Someone else in this conversation has an answer in progress. Nothing was lost. " +
      "Wait for it to finish, then press Retry.",
    retry: true,
  },
  signed_out: {
    title: "Signed out",
    body: "Your session ended. Sign in again, then send your message again.",
    retry: false,
  },
  unknown: {
    title: "Something went wrong",
    body: "Metorite could not finish this answer. Press Retry. If it happens again, tell your admin.",
    retry: true,
  },
};

/** What the error card draws. Stored in the thread as `__ERROR__<json>`. */
export interface RunErrorView {
  code: RunErrorCode;
  /** A short id the server also logged, or null when no server named one. */
  ref: string | null;
  /** The raw failure text. Shown inside the fold, and copied, only. */
  raw: string;
}

export function isRunErrorCode(v: unknown): v is RunErrorCode {
  return typeof v === "string" && Object.prototype.hasOwnProperty.call(RUN_ERROR_WORDS, v);
}

/**
 * The code for an HTTP status the chat route received.
 *
 * Mirrors `classify_status` in `run_errors.py`, for the failures that never
 * reached the executor (the gateway refused the request, or the route could
 * not reach it). `runErrors.test.ts` pins the same table.
 *
 * Two statuses differ on purpose, because here they come from the GATEWAY,
 * not from the model. A 401 is the member's session (`signed_out`). A 404 is
 * a missing session or route, not a model that refused (`unknown`).
 */
export function codeForStatus(status: number): RunErrorCode {
  if (status === 402) return "credits";
  if (status === 403) return "permission";
  if (status === 429) return "rate_limited";
  if (status === 409) return "run_in_progress";
  if (status === 401) return "signed_out";
  if (status === 408 || status === 504) return "timeout";
  if (status === 400 || status === 413 || status === 422) return "model_refused";
  if (status === 502 || status === 503) return "connection";
  return "unknown";
}

/**
 * A stream error frame, thrown out of the SSE loop with its code intact.
 * The message stays the raw text, for the fold.
 */
export class ChatRunError extends Error {
  readonly code: RunErrorCode;
  readonly ref: string | null;
  constructor(raw: string, code: unknown, ref: unknown) {
    super(raw);
    this.name = "ChatRunError";
    this.code = isRunErrorCode(code) ? code : "unknown";
    this.ref = typeof ref === "string" && ref ? ref : null;
  }
}

/**
 * The view for a failed turn.
 *
 * A failed REQUEST (`status` set) carries the route's SSE error frame as its
 * body, so the frame's own code wins, then the status. A stream error
 * carries its code on the `ChatRunError`.
 */
export function runErrorView(a: {
  raw: string;
  code?: unknown;
  ref?: unknown;
  status?: number | null;
}): RunErrorView {
  let raw = a.raw;
  let code: unknown = a.code;
  let ref: unknown = a.ref;
  const frame = /^\s*data:\s*(\{[\s\S]*\})\s*$/.exec(raw);
  if (frame) {
    try {
      const evt = JSON.parse(frame[1]) as { type?: string; content?: unknown; code?: unknown; ref?: unknown };
      if (evt.type === "error") {
        raw = String(evt.content ?? raw);
        code = code ?? evt.code;
        ref = ref ?? evt.ref;
      }
    } catch {
      /* not a frame; keep the text as it is */
    }
  }
  const named: RunErrorCode = isRunErrorCode(code)
    ? code
    : typeof a.status === "number"
      ? codeForStatus(a.status)
      : "unknown";
  return { code: named, ref: typeof ref === "string" && ref ? ref : null, raw };
}

/** Read a stored `__ERROR__` payload back. Anything malformed is `unknown`. */
export function parseStoredRunError(json: string): RunErrorView {
  try {
    const v = JSON.parse(json) as Partial<RunErrorView>;
    return {
      code: isRunErrorCode(v.code) ? v.code : "unknown",
      ref: typeof v.ref === "string" && v.ref ? v.ref : null,
      raw: typeof v.raw === "string" ? v.raw : "",
    };
  } catch {
    return { code: "unknown", ref: null, raw: json };
  }
}

/**
 * The operator's next step, or null for a member who cannot act on it.
 *
 * ⚠️ Only for a member the server resolved as an admin (`access.is_admin`).
 * There is no separate platform-operator flag in the access answer, so the
 * org admin flag is the narrowest one the browser has. A member never sees a
 * shell command.
 *
 * ⚠️ OPEN (review round 1): `is_admin` is true for the admin of EVERY org,
 * so a customer's admin also sees a command for a server they cannot reach.
 * The brief asked for this gate. A staff-only signal from the server is the
 * fix, and it does not exist yet.
 */
export function operatorHint(view: RunErrorView, isAdmin: boolean): string | null {
  if (!isAdmin) return null;
  const grep = view.ref ? ` | grep ${view.ref}` : " -n 50";
  return `Operator: read the gateway log for this run with \`sudo journalctl -u acb-gateway${grep}\`.`;
}
