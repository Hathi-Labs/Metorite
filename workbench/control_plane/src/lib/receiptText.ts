/**
 * receiptText — a receipt line as the member reads it.
 *
 * A tool reply is written for the model. The guillemets fence the member's
 * words as data, a leading `[next·LOCAL]` tag names a list and a source, and
 * `id=` or `(id: …)` names a row for the next call. A card that shows the
 * line to the member keeps none of these (tasks 1002 and 1003). Pure.
 */

/** Show the guillemets as curly quotes: « as “ and » as ”. */
export function curlyQuotes(text: string): string {
  return text.replaceAll("«", "“").replaceAll("»", "”");
}

/**
 * One reply line for a folded card: curly quotes, no leading `[tag]`, and no
 * `· id=…` or `(id: …)` part.
 */
export function memberLine(line: string): string {
  return curlyQuotes(line)
    .replace(/^\s*\[[^\]]*\]\s*/, "")
    .replace(/\s*·\s*id=\S+/g, "")
    .replace(/\s*\(id:\s*[^)]*\)/g, "")
    .trim();
}
