/**
 * cardEmailText — the email inside a confirmation card, split from the
 * card's own words (owner report, 2026-10-24).
 *
 * The send cards wrote one block: "Subject: …", a blank line, "The text of
 * the draft:", then the mail. The owner could not tell whether the labels
 * would go out with the mail. Now the card shows each `Key: value` line as a
 * labelled row and the mail in its own framed box, under a caption that says
 * that only the box is sent.
 *
 * The split is exact or it is nothing: a text that does not have this shape
 * draws as before, whole. The body keeps every character after the marker
 * line, so the card never shows less than it sends (§21, §24.10 rule C7).
 *
 * The three writers: gateway `automation/held_card.py` (two shapes) and the
 * agent's `_with_draft_text`. Fence: `src/lib/cardEmailText.test.ts`.
 */

/** The marker lines that end the card's words and start the mail. */
export const EMAIL_TEXT_MARKERS: readonly string[] = ["The text of the draft:", "The text:"];

const HEAD_LINE = /^([^:«»]{1,48}):\s(.*)$/;

export interface CardEmailText {
  /** The `Key: value` lines above the mail, in order. */
  head: { key: string; value: string }[];
  /** The mail, every character after the marker line. */
  body: string;
}

/** The card text split into its head rows and the mail, or null when the
 *  text does not have exactly that shape. */
export function splitCardEmailText(text: string | undefined): CardEmailText | null {
  if (!text) return null;
  const lines = text.split("\n");
  const at = lines.findIndex((l) => EMAIL_TEXT_MARKERS.includes(l.trim()));
  if (at < 0) return null;
  const head: CardEmailText["head"] = [];
  for (const raw of lines.slice(0, at)) {
    const line = raw.trimEnd();
    if (!line.trim()) continue;
    const m = HEAD_LINE.exec(line.trim());
    if (!m) return null;
    head.push({ key: m[1].trim(), value: m[2].trim() });
  }
  return { head, body: lines.slice(at + 1).join("\n") };
}
