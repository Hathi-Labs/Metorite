/**
 * Projects · a file pasted or dropped into a task's text (WS-57 TR-4).
 *
 * Pure: no React, no DOM, no fetch. `components/useInlineUploads.ts` wires
 * these rules to the description field, the comment composer and the card.
 *
 * ## The flow
 *
 * 1. A paste or a drop brings files. `pastePlan` decides whether the browser
 *    keeps the paste (text) or the files win.
 * 2. Each image that `previewKind` calls an image gets a placeholder at the
 *    caret, `![Uploading name… (n)]()`, and goes up through the attachment
 *    route. `n` keeps two uploads of one name apart.
 * 3. When the upload ends, `replaceFirst` swaps the placeholder for
 *    `![name](<served url>)`. A failure removes the placeholder.
 * 4. Any other file uploads as an attachment and inserts nothing.
 *
 * ## Why the served URL is rebuilt here
 *
 * `lib/markdownMedia.ts` lets exactly one attachment path load at once:
 * `/api/projects/attachments/<uuid>/<segment>`, where the segment holds no
 * `/`, `?`, `#`, `%` or `\`. The gateway's own `url` field carries the stored
 * name as it is, so a name with a space would need `%20` and fall to the
 * click-to-load placeholder. The gateway ignores the name segment (it serves
 * the stored name), so `attachmentImageUrl` writes a segment that always
 * matches. Fence: `inlineUploads.test.ts`, which runs the built URL through
 * `remoteHost`.
 */

import { previewKind } from "./preview";

/** The two facts of a `File` these rules read. A test passes plain objects. */
export interface FileLike {
  name: string;
  type: string;
}

/**
 * An image that may draw inline. The same safelist the Files list previews
 * with (`preview.ts`), so SVG is NOT one: it attaches and inserts nothing.
 */
export function isInlineImage(file: FileLike): boolean {
  return previewKind(file.type) === "image";
}

/** Split files into the images that draw inline and every other file. */
export function partitionFiles<T extends FileLike>(
  files: readonly T[],
): { images: T[]; others: T[] } {
  const images: T[] = [];
  const others: T[] = [];
  for (const file of files) (isInlineImage(file) ? images : others).push(file);
  return { images, others };
}

/**
 * What a paste does. `null` means the browser keeps the paste as it is.
 *
 * ⚠️ **Text wins over files.** A copy from Word or Excel puts the text AND a
 * picture of it on the clipboard. A member who copies three cells wants the
 * cells, so a paste that holds any text stays a text paste. A screenshot has
 * no text, so its files win.
 */
export function pastePlan<T extends FileLike>(
  text: string,
  files: readonly T[],
): { images: T[]; others: T[] } | null {
  if (files.length === 0) return null;
  if (text.trim() !== "") return null;
  return partitionFiles(files);
}

/** A file name as alt text: no bracket, backslash or line break. */
export function altText(name: string): string {
  const clean = name.replace(/[[\]\\\r\n]/g, "").trim();
  return clean || "image";
}

/**
 * The text the field holds while ONE upload runs: `![Uploading name… (n)]()`.
 *
 * ⚠️ `n` makes each placeholder unique. Chrome names every pasted screenshot
 * `image.png`, so two pastes without it hold the same string, and the first
 * upload to end would take the place of the other.
 */
export function uploadPlaceholder(name: string, seq: number): string {
  return `![Uploading ${altText(name)}… (${seq})]()`;
}

/** An upload placeholder, wherever it sits in the text. For the view only. */
const ANY_PLACEHOLDER = /!\[Uploading ([^\]\n]*)…(?: \(\d+\))?\]\(\)/g;

/**
 * True when the text holds one of `tokens`, the placeholders THIS client
 * still waits on. A string that only looks like a placeholder (an agent can
 * write one) never blocks a save.
 */
export function holdsPending(text: string, tokens: Iterable<string>): boolean {
  for (const token of tokens) if (text.includes(token)) return true;
  return false;
}

/**
 * The text after one upload ends. `markdown` is the image, or `null` for a
 * failure, which removes the placeholder and the line break `onOwnLine` put
 * after it. Only `token` changes, so a same-name upload stays where it is.
 */
export function settleUpload(text: string, token: string, markdown: string | null): string {
  if (markdown !== null) return replaceFirst(text, token, markdown);
  return text.includes(`${token}\n`)
    ? replaceFirst(text, `${token}\n`, "")
    : replaceFirst(text, token, "");
}

/**
 * Text for a one-line excerpt: each image becomes `[alt]`, or `[image]` when
 * it has no alt text, so raw Markdown never shows in "Replying to …".
 */
export function withoutImages(text: string): string {
  return text.replace(/!\[([^\]\n]*)\]\([^)\n]*\)/g, (_m, alt: string) =>
    alt.trim() ? `[${alt.trim()}]` : "[image]",
  );
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * The URL an inline image points at: the attachment route, with a name
 * segment that never needs an escape. See the module note.
 */
export function attachmentImageUrl(attachmentId: string, name: string): string {
  let segment = name.replace(/[^A-Za-z0-9._-]/g, "_");
  if (/^\.*$/.test(segment)) segment = "image";
  const id = UUID.test(attachmentId) ? attachmentId.toLowerCase() : attachmentId;
  return `/api/projects/attachments/${id}/${segment}`;
}

/** The Markdown for one uploaded image. */
export function imageMarkdown(
  name: string,
  uploaded: { attachment_id: string; name: string },
): string {
  return `![${altText(name)}](${attachmentImageUrl(uploaded.attachment_id, uploaded.name)})`;
}

/**
 * Put `insertion` in place of the selection `[start, end)`. Answers the new
 * text and the caret after the insertion. A selection outside the text is
 * clamped, so a stale caret cannot cut the text.
 */
export function insertAtCaret(
  value: string,
  start: number,
  end: number,
  insertion: string,
): { value: string; caret: number } {
  const from = Math.max(0, Math.min(start, value.length));
  const to = Math.max(from, Math.min(end, value.length));
  return {
    value: value.slice(0, from) + insertion + value.slice(to),
    caret: from + insertion.length,
  };
}

/**
 * `insertion` on lines of its own: a newline before it when the caret is in
 * the middle of a line, and one after it when text follows on that line. An
 * image pasted into a sentence then draws under the words, not inside them.
 */
export function onOwnLine(value: string, start: number, end: number, insertion: string): string {
  const from = Math.max(0, Math.min(start, value.length));
  const to = Math.max(from, Math.min(end, value.length));
  const before = from > 0 && value[from - 1] !== "\n" ? "\n" : "";
  const after = to < value.length && value[to] !== "\n" ? "\n" : "";
  return `${before}${insertion}${after}`;
}

/**
 * Replace the FIRST `token` in `value`. Answers `value` unchanged when the
 * token is gone: the member deleted the placeholder, so the image does not
 * come back. The file stays in the Files list either way.
 *
 * ⚠️ A plain search, never `String.replace` with a string: a `$` in the
 * replacement would paste parts of the text.
 */
export function replaceFirst(value: string, token: string, replacement: string): string {
  const at = value.indexOf(token);
  if (at < 0) return value;
  return value.slice(0, at) + replacement + value.slice(at + token.length);
}

/**
 * The text that the rendered view draws. A placeholder becomes the words
 * "Uploading name…" in italics, because an image with no source draws as a
 * broken frame.
 */
export function forView(text: string): string {
  return text.replace(ANY_PLACEHOLDER, (_m, name: string) => `*Uploading ${name}…*`);
}

/**
 * Keep each line break that the member typed, outside a fenced code block.
 *
 * The description and the comments drew as `whitespace-pre-wrap` text before
 * TR-4, so one newline was one line. Markdown joins two lines into one
 * paragraph. A hard break (two spaces before the newline) keeps the old look.
 * A blank line, a list and a fence keep their Markdown meaning.
 */
export function keepLineBreaks(text: string): string {
  const lines = text.split("\n");
  let fence: string | null = null;
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const opener = line.match(/^\s{0,3}(`{3,}|~{3,})/);
    if (fence === null && opener) {
      fence = opener[1][0];
      out.push(line);
      continue;
    }
    if (fence !== null) {
      if (opener && opener[1][0] === fence) fence = null;
      out.push(line);
      continue;
    }
    const next = lines[i + 1];
    const breaks = next !== undefined && line.trim() !== "" && next.trim() !== "";
    out.push(breaks && !/ {2}$|\\$/.test(line) ? `${line}  ` : line);
  }
  return out.join("\n");
}
