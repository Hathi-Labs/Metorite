/**
 * The arguments of a running tool step, for the chat trail (PR #654).
 *
 * `api/agent/chat/route.ts` forwards a `tool_args` event once a step's
 * streamed arguments form a whole JSON object, so a running row can show its
 * command or its path. A route file may export only route fields, so the
 * helpers live here.
 *
 * The second half is how a step's detail shows those arguments: each key as
 * a label, each value in full, and a long value clamped behind a toggle
 * (owner report, 2026-10-09). Fence: `toolSteps.test.ts`.
 */

/** The arguments once they are a whole, non-empty JSON object, else null.
 *  A fragment is not forwarded: the row keeps what it had. */
export function wholeToolArgs(raw: string | undefined): Record<string, unknown> | null {
  // A whole object ends in "}". Most fragments do not, so they cost no parse,
  // and a long argument stream stays linear rather than quadratic.
  if (!raw || !raw.trimEnd().endsWith("}")) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) &&
      Object.keys(parsed).length > 0
      ? capToolArgs(parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/** The most text one `tool_args` event carries for the running row. */
export const TOOL_ARGS_CAP = 8 * 1024;

/**
 * The running row needs a command or a path, not a 2 MB file body. Each
 * string value longer than the cap is cut, and says so. The full arguments
 * still arrive with `tool_end`, as before.
 */
export function capToolArgs(args: Record<string, unknown>): Record<string, unknown> {
  let budget = TOOL_ARGS_CAP;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(args)) {
    if (typeof v === "string") {
      const room = Math.max(0, budget);
      out[k] = v.length > room
        ? `${v.slice(0, room)}… [cut: ${v.length - room} more characters]`
        : v;
      budget -= Math.min(v.length, room);
    } else {
      const text = JSON.stringify(v) ?? "";
      if (text.length > Math.max(0, budget)) {
        out[k] = `[cut: ${text.length} characters]`;
      } else {
        out[k] = v;
        budget -= text.length;
      }
    }
  }
  return out;
}


// ── The arguments in a step's detail ─────────────────────────────────────────

/**
 * A value longer than this many lines opens clamped, behind "Show more". The
 * owner's report (2026-10-09): the detail cut every value at 80 characters,
 * with no ellipsis and no way to read the rest. So no value is cut now. A
 * long one is clamped, and the member can open it.
 */
export const CLAMP_LINES = 3;

/** About three lines of the trail at rail width. A value longer than this
 *  wraps past three lines, so it opens clamped too. */
export const CLAMP_CHARS = 240;

/** Does this text open clamped, behind a "Show more" toggle? */
export function needsClamp(text: string): boolean {
  return text.length > CLAMP_CHARS || text.split(/\r?\n/).length > CLAMP_LINES;
}

/**
 * A key whose value is a credential. The detail shows "Hidden" for it.
 *
 * The stored trail is the server's: `routes/chat.py` `_render_message` drops
 * every tool event of a row that the reader is not cleared for. That is the
 * one redaction, and this does not replace it. This only keeps a password or
 * a token that a model put in an argument off the screen, because the detail
 * now shows each value in full.
 */
export function isSecretKey(key: string): boolean {
  const k = key.toLowerCase().replace(/[^a-z]/g, "");
  return /password|passwd|secret|apikey|authorization|credential|privatekey|bearer|cookie/.test(k)
    || /token$/.test(k);
}

/** One argument as the detail draws it. */
export interface ArgField {
  /** The wire key, for React and for a test. */
  key: string;
  /** The key in product words: "Task", never "task:". */
  label: string;
  /** The value, in full. Never cut. */
  text: string;
  /** True when the value opens clamped behind "Show more". */
  clamp: boolean;
  /** True for a credential: the detail shows "Hidden", and `text` is empty. */
  hidden?: boolean;
}

/** A value as text. A list of plain values reads as a list, never as JSON. */
function valueText(v: unknown): string {
  if (typeof v === "string") return v.trim();
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v) && v.every((x) => ["string", "number", "boolean"].includes(typeof x))) {
    return v.map(String).join(", ");
  }
  return JSON.stringify(v, null, 2) ?? "";
}

/**
 * The arguments of a step, for its detail: each key as a label, and each
 * value in full. Pure, so a test reaches it without a DOM.
 *
 * - A key that the label or the description already shows (`skip`) is left
 *   out. A hand-off's agent and task do not show twice.
 * - An empty value is left out.
 * - A key takes its label from `labelOf`, which is `fieldSpec` in
 *   `cardFields.ts`: the ONE map of a key to its words. `_raw` is the text
 *   of arguments that did not parse (`api/agent/chat/route.ts`).
 */
export function argFields(
  args: Record<string, unknown> | undefined,
  labelOf: (key: string) => string,
  skip: readonly string[] = [],
): ArgField[] {
  const out: ArgField[] = [];
  for (const [key, v] of Object.entries(args ?? {})) {
    if (skip.includes(key) || v === null || v === undefined) continue;
    const label = key === "_raw" ? "Arguments" : labelOf(key);
    if (isSecretKey(key)) {
      out.push({ key, label, text: "", clamp: false, hidden: true });
      continue;
    }
    const text = valueText(v);
    if (!text) continue;
    out.push({ key, label, text, clamp: needsClamp(text) });
  }
  return out;
}
