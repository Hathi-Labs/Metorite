/**
 * Toast — the message rules, the dedupe key and the promise state machine.
 *
 * The *rendering* lives in `src/components/ui/Toast.tsx`, which is the only
 * file allowed to name `@base-ui/react` (conformance rule 8). Everything a test
 * could get wrong lives here instead, because **`vitest.config.ts` in this
 * package is `environment: "node"` and collects only `src/**‍/*.test.ts`** — a
 * `.tsx` is not collected and there is no DOM runner, so a decision written
 * inside the component would have no fence at all. Same split as
 * `src/lib/controlLink.ts`, `src/lib/layoutBoundary.ts` and
 * `src/lib/outsideClick.ts`.
 *
 * ## The one rule this module exists to make unbreakable
 *
 * **A rejected promise never produces a success toast.** WS-27ak(3)'s whole
 * reason for existing is that `/projects` had *no confirmation channel at all*
 * (measured: zero `useToast`, zero `<Toaster>`, zero `ToastProvider` in the
 * tree — §11's parity table), so a mutation either reported inline or reported
 * nothing. The failure mode a toast layer then invites is worse than silence:
 * a "Saved" that appears because the call site wired the happy path and let the
 * rejection fall through. `settlePatch` is a total function over
 * `{ok:true} | {ok:false}` and the `ok:false` arm has no path to
 * `variant: "success"`, which is what `toast.test.ts` asserts over a table of
 * rejection shapes rather than over one.
 *
 * ## The three decisions, and why each is here and not in the component
 *
 * 1. **Which message.** A refusal carries the server's own sentence
 *    (`ProjectsApiError` puts the gateway's `detail` on `.message`), and the
 *    workbench contract is that a refusal must SURFACE — so the rejection wins
 *    where it is readable and a generic line covers the rest. "Readable" is a
 *    judgement with edges (`""`, `"[object Object]"`, a 40KB HTML error page),
 *    and every one of those edges is a case below.
 * 2. **Dedupe by key.** Base UI's store updates a toast in place when `add` is
 *    handed an `id` it already holds, so the whole of dedupe is *deriving a
 *    stable id from a caller's key*. Bulk operations are the reason: fifty rows
 *    saving under one key is one toast, not fifty.
 * 3. **The state machine.** `loading → success | error`, one toast, mutated in
 *    place. Expressed as patches so the transition is a value a test can read
 *    rather than a sequence of calls it would have to observe.
 */

/** What a toast currently is. Mirrors Base UI's `type`, which draws `data-type`. */
export type ToastVariant = "loading" | "success" | "error";

/**
 * Which live region announces it.
 *
 * `low` is the viewport's own `aria-live="polite"`; `high` additionally renders
 * a visually-hidden `role="alert"` copy, i.e. assertive. Errors are the only
 * thing that earns an interruption — a "Saved" that talks over a screen-reader
 * user mid-sentence is how the whole channel gets turned off.
 */
export type ToastPriority = "low" | "high";

/** One button on a toast — the "Undo" / "Retry" shape. At most one. */
export interface ToastAction {
  label: string;
  onClick: () => void;
}

/** A toast, as this app describes one. The component translates it. */
export interface ToastPatch {
  variant: ToastVariant;
  title: string;
  description?: string;
  priority: ToastPriority;
  /** ms until auto-dismiss. `0` means never. */
  timeout: number;
  action?: ToastAction;
}

/** A message that may be derived from whatever the promise settled with. */
export type ToastMessage<T> = string | ((value: T) => string);

/** An action that may be derived from the same. Return `null` for "no button". */
export type ToastActionSpec<T> =
  | ToastAction
  | ((value: T) => ToastAction | null | undefined);

/** What `useToast().promise()` is handed. */
export interface ToastPromiseSpec<T> {
  /**
   * Dedupe key. Two calls with the same key mutate ONE toast; without a key
   * every call gets its own. Namespace it with the thing it is about
   * (`"task-status:<id>"`) — a bare `"save"` makes two unrelated saves fight.
   */
  key?: string;
  loading: string;
  success: ToastMessage<T>;
  /**
   * The sentence naming what failed ("Couldn't change the status"). The
   * rejection's own words are shown *underneath* it, so the call site never has
   * to choose between context and the server's reason. Omit it and the
   * rejection's words become the whole message.
   */
  error?: ToastMessage<unknown>;
  /** One button on the success toast — the Undo shape. */
  successAction?: ToastActionSpec<T>;
  /** One button on the error toast — the Retry shape. */
  errorAction?: ToastActionSpec<unknown>;
}

/** The promise, settled. A discriminated union so neither arm can be forgotten. */
export type ToastOutcome<T> =
  | { ok: true; value: T }
  | { ok: false; reason: unknown };

/** How long a success stays up. Base UI's own default, named here so it is one number. */
export const SUCCESS_TIMEOUT = 5_000;

/**
 * How long a failure stays up: **forever, until dismissed.**
 *
 * A failure nobody saw is a failure that was never reported, and five seconds
 * is exactly long enough to miss while looking at the thing you were editing.
 * The toast carries a dismiss button and answers Escape, so "forever" costs one
 * key.
 */
export const ERROR_TIMEOUT = 0;

/** A loading toast is closed by its own settlement, never by a clock. */
export const LOADING_TIMEOUT = 0;

/** Said when the rejection carried nothing a person could read. */
export const GENERIC_FAILURE = "Something went wrong.";

/**
 * Namespace for caller keys.
 *
 * Ids are shared with any toast raised without a key (Base UI mints those as
 * `toast-<n>`), so a caller key of `"toast-3"` must not be able to collide with
 * one. The prefix is what stops that.
 */
export const KEY_PREFIX = "cc-toast:";

/**
 * How much of a rejection is worth reading aloud.
 *
 * Not a style preference: a proxy failure arrives as an HTML error page, and
 * `ProjectsApiError` puts whatever came back on `.message`. A toast is a line,
 * not a document — past this it is truncated with an ellipsis rather than
 * dropped, because a truncated reason still says more than a generic one.
 */
export const MAX_REASON = 180;

/** Strings that are technically present and tell a person nothing. */
const EMPTY_REASONS = new Set([
  "[object object]",
  "undefined",
  "null",
  "error",
  "{}",
  "nan",
]);

/**
 * The stable toast id for a caller key, or `undefined` for "mint a fresh one".
 *
 * This IS the dedupe mechanism: Base UI's store updates in place when `add`
 * receives an id it is already holding, and removes-then-re-adds when that
 * toast is mid-dismissal. So two fires of the same key while the first is up
 * mutate one toast, and a re-fire after it has gone raises a new one — both of
 * which are what a person watching the screen expects.
 */
export function toastIdFor(key: string | undefined): string | undefined {
  return key === undefined ? undefined : `${KEY_PREFIX}${key}`;
}

/** Errors interrupt; nothing else does. */
export function priorityFor(variant: ToastVariant): ToastPriority {
  return variant === "error" ? "high" : "low";
}

/** See the three timeout constants for the argument behind each. */
export function timeoutFor(variant: ToastVariant): number {
  if (variant === "success") return SUCCESS_TIMEOUT;
  if (variant === "error") return ERROR_TIMEOUT;
  return LOADING_TIMEOUT;
}

/**
 * A `show()` call's timeout: the caller's own window, for a SUCCESS only.
 *
 * My Tasks' undo toast needs this (continuity P3). Its window is
 * `UNDO_WINDOW_SECONDS`, which the delete dialog quotes, so the toast must
 * close on that number and not on `SUCCESS_TIMEOUT`. An error keeps
 * `ERROR_TIMEOUT` whatever the caller asks: a failure nobody saw was never
 * reported. A loading toast has no clock at all.
 */
export function showTimeout(variant: ToastVariant, override?: number): number {
  if (variant === "success" && override !== undefined && override > 0) return override;
  return timeoutFor(variant);
}

/** Trim, reject the useless, truncate the enormous. `null` = not readable. */
function usable(text: unknown): string | null {
  if (typeof text !== "string") return null;
  const trimmed = text.trim();
  if (!trimmed) return null;
  if (EMPTY_REASONS.has(trimmed.toLowerCase())) return null;
  return trimmed.length > MAX_REASON
    ? `${trimmed.slice(0, MAX_REASON).trimEnd()}…`
    : trimmed;
}

/**
 * The part of a rejection a person can read, or `null`.
 *
 * Deliberately shape-driven rather than `instanceof Error`: a `fetch` failure,
 * a `ProjectsApiError`, a hand-thrown string and a `{detail}` body parsed
 * straight off a 422 are all real rejections in this tree, and only one of them
 * is an `Error`. `detail` is checked before `message` because that is FastAPI's
 * field and it is the sentence a human wrote; `message` on the same object
 * would be the transport's.
 */
export function readableReason(reason: unknown): string | null {
  if (reason == null) return null;
  if (typeof reason === "string") return usable(reason);
  if (typeof reason !== "object") return null;
  const bag = reason as { detail?: unknown; message?: unknown };
  return usable(bag.detail) ?? usable(bag.message);
}

/** A message spec against the value it may be derived from. */
export function resolveMessage<T>(message: ToastMessage<T>, value: T): string {
  return typeof message === "function" ? message(value) : message;
}

/** An action spec against the same. `undefined` means "draw no button". */
export function resolveAction<T>(
  action: ToastActionSpec<T> | undefined,
  value: T,
): ToastAction | undefined {
  if (!action) return undefined;
  const resolved = typeof action === "function" ? action(value) : action;
  return resolved ?? undefined;
}

/**
 * The toast a promise starts as. Never auto-dismisses; never announces loudly.
 *
 * ⚠️ `description` and `action` are present-and-`undefined` rather than
 * omitted, and every patch below does the same. The substrate MERGES a patch
 * onto the toast it already holds (`{...prevToast, ...updates}`), so an omitted
 * key leaves the *previous* state's value on screen — a failure's detail line
 * sitting under a "Saved", or a "Retry" button on a toast that now says the
 * save worked. A key that is present overwrites; a key that is absent does not.
 */
export function loadingPatch(message: string): ToastPatch {
  return {
    variant: "loading",
    title: message,
    description: undefined,
    priority: priorityFor("loading"),
    timeout: timeoutFor("loading"),
    action: undefined,
  };
}

/**
 * The toast a promise BECOMES. The state machine, as one total function.
 *
 * ⚠️ Read the `ok: false` arm as the point of the file: there is no branch in
 * it that can produce `variant: "success"`, no matter what the rejection looks
 * like, and no `try` around it that could turn a thrown message-resolver into a
 * fallthrough. A call site cannot opt out either — it hands over a promise and
 * gets whichever arm the promise chose.
 */
export function settlePatch<T>(
  outcome: ToastOutcome<T>,
  spec: ToastPromiseSpec<T>,
): ToastPatch {
  if (outcome.ok) {
    return {
      variant: "success",
      title: resolveMessage(spec.success, outcome.value),
      // Present-and-`undefined`, never omitted — see `loadingPatch`. This one
      // was omitted in the first draft and `toast.test.ts` caught it: the
      // failure's detail line would have survived a retry that then succeeded.
      description: undefined,
      priority: priorityFor("success"),
      timeout: timeoutFor("success"),
      action: resolveAction(spec.successAction, outcome.value),
    };
  }

  const sentence = spec.error
    ? resolveMessage(spec.error, outcome.reason)
    : undefined;
  const detail = readableReason(outcome.reason);
  return {
    variant: "error",
    // With a sentence, the rejection's own words go underneath it, so context
    // and cause are both on screen. Without one, the rejection IS the message —
    // and when it is not readable, the generic line, because a toast that says
    // nothing is the silence this primitive exists to end.
    title: sentence ?? detail ?? GENERIC_FAILURE,
    description: sentence ? (detail ?? undefined) : undefined,
    priority: priorityFor("error"),
    timeout: timeoutFor("error"),
    action: resolveAction(spec.errorAction, outcome.reason),
  };
}
