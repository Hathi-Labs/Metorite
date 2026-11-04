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

/** Each bidi control: the marks, the embeddings, the overrides, the isolates. */
export const BIDI_CONTROLS = /[؜‎‏‪-‮⁦-⁩]/g;

/** *text* with each bidi control dropped. */
export function withoutBidi(text: string): string {
  return text.replace(BIDI_CONTROLS, "");
}
