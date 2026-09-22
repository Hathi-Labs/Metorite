/**
 * What to tell a member when an app's API call failed and the server said
 * nothing useful.
 *
 * ## Why this is shared rather than written per app
 *
 * Four apps each had their own fetch wrapper and their own last-resort
 * string: "Request failed (502)", "Gateway error 502". Every one of them
 * names the machinery and none of them answers the two questions a person
 * actually has — **is this me or them**, and **should I try again**. A member
 * reading "Gateway error 502" has no way to know the answer is "wait ten
 * seconds".
 *
 * The wrappers stay per app: they carry different error types, different
 * paths and different cache rules, and merging them would be a refactor in
 * search of a reason. The MESSAGE vocabulary is the part that must not fork,
 * because it is the part a person reads (CLAUDE.md §5).
 *
 * ## The defect that produced it
 *
 * On 2026-09-20 a stuck deploy loop restarted the production gateway every
 * five minutes. Requests in flight got Starlette's unhandled-exception reply,
 * which is the plain text `Internal Server Error` — not JSON. Two of the four
 * wrappers ran `JSON.parse(text)` BEFORE checking `res.ok`, so they threw a
 * raw `SyntaxError` and the member saw:
 *
 *     Unexpected token 'I', "Internal S"... is not valid JSON
 *
 * That message names the parser, points at no server, and suggests nothing.
 * The fix has two halves: parse after the status and never let the parse
 * throw (each wrapper), and say something useful when there is no `detail`
 * (here).
 */

/**
 * A member-facing sentence for a failed response with no server `detail`.
 *
 * ⚠️ **The raw body is deliberately not shown.** It is either boilerplate
 * ("Internal Server Error") or an HTML error page. Pasting either into a
 * toast tells a person nothing and looks alarming.
 *
 * @param status  The HTTP status. Quoted in the message because it is the one
 *                token worth putting in a report.
 * @param body    The raw text, used ONLY to tell "no reply at all" from "a
 *                reply we could not read". Never rendered.
 */
export function describeFailure(status: number, body?: string): string {
  // ⚠️ A restart is the common case, not an exotic one: every deploy bounces
  // the gateway, so anyone using the app during a release sees this. It is
  // the one failure where "try again" is the whole correct answer.
  if (status === 502 || status === 503 || status === 504) {
    return "The server is restarting. Wait a moment and try again.";
  }
  if (status >= 500) {
    // "Nothing was saved" is the question a person asks after a failed write,
    // and it is true: these routes commit at the end of one transaction, so a
    // 500 rolled back.
    return `The server had an error (${status}). Nothing was saved. Try again.`;
  }
  if (status === 413) return "That file is too large.";
  if (status === 429) return "Too many requests just now. Wait a moment.";
  if (status === 401 || status === 403) {
    return "You do not have access to this. Sign in again if you just did.";
  }
  // ⚠️ Not "not found". The API answers 404 for "not yours" as well as "no
  // such thing" (R5), and the UI must not invent a distinction the server
  // refuses to make.
  if (status === 404) return "That is not here, or is not yours.";
  return body?.trim()
    ? `Request failed (${status}).`
    : `Request failed (${status}) with no reply.`;
}

/**
 * Read a response body that SHOULD be JSON but may be anything.
 *
 * Returns the parsed object, or `null` when the body is empty or unparseable.
 * `ok` says which of those two it was, so a caller can tell an empty 204 from
 * an HTML error page — they need different messages.
 */
export function readJsonBody(
  text: string,
): { value: { detail?: unknown } | null; ok: boolean } {
  if (!text) return { value: null, ok: true };
  try {
    return { value: JSON.parse(text) as { detail?: unknown }, ok: true };
  } catch {
    return { value: null, ok: false };
  }
}

/**
 * FastAPI's `detail` as a string.
 *
 * ⚠️ It is a string for our own `HTTPException` and an ARRAY OF OBJECTS for a
 * Pydantic validation failure. Rendering the array gives "[object Object]",
 * which has reached a member's screen in this repo before.
 *
 * A third shape since S6c: an OBJECT with a `message`. The required-fields
 * refusal on a move (`assert_required_fields_present`, migration 192) answers
 * `{error, message, fields}` so a client can draw the fields as inputs. Read
 * as a string it was "", and the member saw a generic 422 instead of the
 * names of the fields to fill in.
 */
export function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail.trim();
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const message = (detail as { message?: unknown }).message;
    return typeof message === "string" ? message.trim() : "";
  }
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) =>
        typeof d === "string"
          ? d
          : typeof (d as { msg?: unknown })?.msg === "string"
            ? (d as { msg: string }).msg
            : "",
      )
      .filter(Boolean);
    return parts.join(". ");
  }
  return "";
}
