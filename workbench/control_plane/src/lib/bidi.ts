/**
 * The ONE rule of the bidi controls in text that somebody else wrote.
 *
 * A right-to-left override can make "invoice<RLO>fdp.exe" read as
 * "invoiceexe.pdf", and an isolate can move words on the screen. So text from
 * a sender or from an agent drops each bidi control before it draws: the
 * marks, the embeddings, the overrides and the isolates.
 *
 * Readers: `app/email/lib/forward.ts` (a file name of a forward) and
 * `lib/appRequest.ts` (each value of an app request card, MCP-P1b-1).
 * Fences: `app/email/lib/forward.test.ts` and
 * `components/AppRequestCard.test.ts`.
 */

/**
 * Each bidi control: the marks (U+061C, U+200E, U+200F), the embeddings and
 * the overrides (U+202A to U+202E), and the isolates (U+2066 to U+2069).
 * The class is written with escapes, never with the characters themselves
 * (the Trojan Source pattern). `test_app_requests_r8.py::
 * test_no_slice_file_holds_a_literal_bidi_control` holds it.
 *
 * ⚠️ The `/g` form is for `replace` only, which resets `lastIndex`. Never
 * call `.test()` or `.exec()` on it: a global regex keeps `lastIndex`
 * between calls and skips a match. Use {@link HAS_BIDI_CONTROL} to ask.
 */
export const BIDI_CONTROLS = /[\u061C\u200E\u200F\u202A-\u202E\u2066-\u2069]/g;

/** The same class with no `g` flag, for `.test()`. */
export const HAS_BIDI_CONTROL = /[\u061C\u200E\u200F\u202A-\u202E\u2066-\u2069]/;

/** *text* with each bidi control dropped. */
export function withoutBidi(text: string): string {
  return text.replace(BIDI_CONTROLS, "");
}
