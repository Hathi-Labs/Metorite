// WS-17 EM-G3a-f7 PR-C2 — the saves and the send of a Gmail-web draft in the
// DraftCard (§12.3.12.5 of `project-docs/specs/email_app_master_plan.md`).
//
// The composer stays a text editor (D-G3a-f7-1). A save that leaves the text
// as it opened asks the gateway to keep the HTML of the draft (`keep_html`,
// D-G3a-f7-2). The gateway owns the HTML: it takes it from the row, or from
// Gmail. A text edit saves the text only, and the card tells the member first
// (D-G3a-f7-3). Each rule here is pure, so `draftSave.test.ts` fences it in
// the node environment of vitest (D-G3a-f7-9).
//
// ⚠️ Gmail only (D-G3a-f7-5). An Outlook or an IMAP draft sends the body of
// main, with no `keep_html` and no `allow_text_only`.

import type { DraftHtmlState, SaveDraftParams } from "./api";

/** The words of the notice (E-D3 table of §12.3.12.5, item PR-C2 4). */
export const FORMATTING_NOTICE =
  "This draft has formatting or images from Gmail. If you change the text here, " +
  "Metorite saves the text only, and the formatting and images go.";

/** The words after a stopped send (E-D3 table of §12.3.12.5, the stopped send). */
export const SEND_STOPPED_NOTICE =
  "Metorite could not keep the formatting and images of this draft. Press Send again to send the text only.";

/**
 * The words of the notice. After a stopped send (a 409 `html_unavailable`,
 * or a pre-send save that answered `lost`), the card tells the member that
 * the next press sends the text only. The next send and a text edit clear
 * the stop, and the general words come back.
 */
export function noticeWords(sendStopped: boolean): string {
  return sendStopped ? SEND_STOPPED_NOTICE : FORMATTING_NOTICE;
}

/** The fields of one DraftCard save, other than the text. */
export type DraftSaveFields = Omit<SaveDraftParams, "body" | "keepHtml">;

/** The answer of a save, as `saveDraft` returns it. */
export interface DraftSaveAnswer {
  html?: DraftHtmlState;
}

/**
 * The text of the card: the editable text, and the quoted chain under it.
 * The card builds its outgoing text and its text at open with this one rule,
 * so an unchanged card equals the text at open.
 */
export function draftText(main: string, quote: string): string {
  return quote ? `${main.replace(/\s+$/, "")}\n\n${quote}` : main;
}

/**
 * The params of `saveDraft` for one DraftCard save (D-G3a-f7-2, -5). It sets
 * `keepHtml` only for a Gmail draft whose text equals the text at open. A
 * null `openedText` means the card does not know the text at open yet.
 */
export function draftSavePayload(
  draft: DraftSaveFields,
  openedText: string | null,
  text: string,
  provider: string | undefined,
): SaveDraftParams {
  const payload: SaveDraftParams = { ...draft, body: text };
  if (provider === "gmail" && openedText !== null && text === openedText) {
    payload.keepHtml = true;
  }
  return payload;
}

/**
 * The notice shows before the first edit of a Gmail draft with HTML, and of a
 * Gmail draft whose HTML read failed (D-G3a-f7-3).
 *
 * Review round 1 (P3): it also shows while the HTML read of a Gmail draft
 * with no row HTML runs (`htmlReadPending`). So the member knows first, even
 * when the read is slow. A read that finds no HTML then removes it.
 */
export function showsFormattingNotice(
  provider: string | undefined,
  html: string | null | undefined,
  htmlReadFailed: boolean,
  htmlReadPending = false,
): boolean {
  if (provider !== "gmail") return false;
  return htmlReadFailed || htmlReadPending || !!html?.trim();
}

/** A save that answers `html: "lost"` sent the text only (D-G3a-f7-6). */
export function noticeAfterSave(answer: DraftSaveAnswer | null | undefined): boolean {
  return answer?.html === "lost";
}

/**
 * Whether the card sends after its pre-send save (D-G3a-f7-12). A `lost`
 * with no confirm stops the send, and the card shows the notice. The next
 * press of the member is the confirm.
 *
 * Review round 1 (P2): `lostBefore` is a `lost` of an earlier save of the
 * card, such as an autosave. That save already wrote the text only to Gmail,
 * so the pre-send save answers `none`. The send stops all the same.
 */
export function sendAfterPreSave(
  answer: DraftSaveAnswer | null | undefined,
  confirmed: boolean,
  lostBefore = false,
): "send" | "stop" {
  return (answer?.html === "lost" || lostBefore) && !confirmed ? "stop" : "send";
}

/** The reason of the 409 of the send (D-G3a-f7-11, `drafting.py`). */
const HTML_UNAVAILABLE = "html_unavailable";

/**
 * True for the 409 of a send that could not read the HTML of a Gmail draft
 * (D-G3a-f7-11). It reads the status as `statusOf` in `forward.ts` does. The
 * detail of this 409 is an object, so `gatewayFetch` keeps it in `detail`,
 * and the message reads "[object Object]".
 */
export function isHtmlUnavailable(err: unknown): boolean {
  if (typeof err !== "object" || err === null) return false;
  const { status, detail } = err as { status?: unknown; detail?: unknown };
  if (status !== 409 || typeof detail !== "object" || detail === null) return false;
  return (detail as { reason?: unknown }).reason === HTML_UNAVAILABLE;
}

/**
 * The card reads the HTML of a Gmail draft once, when the draft came with no
 * HTML (B5). The hydrate effect returns early for a draft with text, so this
 * read has an effect of its own.
 */
export function needsHtmlRead(
  provider: string | undefined,
  bodyHtml: string | null | undefined,
  alreadyRead: boolean,
): boolean {
  return provider === "gmail" && !bodyHtml && !alreadyRead;
}
