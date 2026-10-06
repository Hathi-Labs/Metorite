/**
 * The arguments of a running tool step, for the chat trail (PR #654).
 *
 * `api/agent/chat/route.ts` forwards a `tool_args` event once a step's
 * streamed arguments form a whole JSON object, so a running row can show its
 * command or its path. A route file may export only route fields, so the
 * helpers live here. Fence: `toolSteps.test.ts`.
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

