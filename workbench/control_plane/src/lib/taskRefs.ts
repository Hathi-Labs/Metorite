/**
 * The bare task references in a chat answer: `#171`.
 *
 * Owner report, 2026-10-13: "in some cases, the # reference number does not
 * reference a clickable link". The chat drew a task pill only for the fenced
 * form `#172 «one surface»` (`remarkEntityPills.ts`). A bare `#171` in the same
 * paragraph stayed text. This module finds the bare form, and
 * `remarkEntityPills` turns each one into a `taskRef` node. `ChatTaskRef.tsx`
 * then asks the server whether the number is a real task the member can see.
 *
 * The parser only finds CANDIDATES. A candidate links only when the lookup
 * finds exactly one visible task with that number, so the guards here keep
 * the lookup small and the obvious non-references out. Five rules:
 *
 * 1. **A reference is `#` and a number that does not start with 0**, 1 to 9
 *    digits. `#0` and `#012` are not task numbers.
 * 2. **No letter, digit, `_` or URL character before the `#`.** That rules out
 *    `abc#12`, `page/#12`, `?a=#12`, `&#12;` and a second `##12`.
 * 3. **No letter or `_` after the number, and no decimal part.** That rules
 *    out `#12a`, `#12_x` and `#1.5`. A hashtag word like `#launch` has no digit,
 *    so it never matches.
 * 4. **A colour is not a reference.** A hex run of 6 or 8 characters (`#123456`,
 *    `#11223344`) is a colour, even with digits only. A run with a hex letter
 *    (`#fff`, `#a1b2c3`, `#1a2b3c`) fails rule 1 or rule 3 already.
 * 5. **A URL is not a reference.** A word that holds `://` or starts with
 *    `www.` keeps its `#12`. The Markdown parser makes most URLs a link, and
 *    the plugin never looks inside a link. This guard covers the rest.
 *
 * Code, inline code and a heading's own `#` marker never reach this module:
 * the Markdown parser makes them `code`, `inlineCode` and `heading` nodes, and
 * the plugin reads text nodes only.
 *
 * Pure and framework-free, so the node-env vitest holds it.
 * Fence: `src/lib/taskRefs.test.ts`.
 */

/** One candidate: where it is in the text, and the reference as written. */
export interface TaskRefMatch {
  index: number;
  length: number;
  /** `#171`. */
  ref: string;
  /** 171. */
  number: number;
}

/**
 * Rules 1 to 3. The lookbehind is rule 2, the lookahead rule 3.
 * `u` for `\p{L}`, `g` for `matchAll`.
 */
const CANDIDATE = /(?<![\p{L}\p{N}_/\\#&=?@])#([1-9][0-9]{0,8})(?![\p{L}\p{N}_]|[.,][0-9])/gu;

/** Rule 4: the hex run that starts at `#`. */
const HEX_RUN = /^#[0-9a-fA-F]+/;

/** True when the hex run at the start of `rest` reads as a colour. */
export function isColourAt(rest: string): boolean {
  const run = rest.match(HEX_RUN)?.[0] ?? "";
  const digits = run.length - 1;
  return digits === 6 || digits === 8;
}

/** Rule 5: the white-space-free word around `index` is a URL. */
export function isInsideUrl(text: string, index: number): boolean {
  let start = index;
  while (start > 0 && !/\s/.test(text[start - 1])) start -= 1;
  let end = index;
  while (end < text.length && !/\s/.test(text[end])) end += 1;
  const word = text.slice(start, end);
  return word.includes("://") || /^\(?www\./i.test(word);
}

/** Every bare task reference in one run of plain text, in order. */
export function findTaskRefs(text: string): TaskRefMatch[] {
  if (!text.includes("#")) return [];
  const out: TaskRefMatch[] = [];
  for (const m of text.matchAll(CANDIDATE)) {
    const index = m.index ?? 0;
    if (isColourAt(text.slice(index))) continue;
    if (isInsideUrl(text, index)) continue;
    out.push({ index, length: m[0].length, ref: m[0], number: Number(m[1]) });
  }
  return out;
}

/** One part of a text run: plain words, or a reference. */
export type TaskRefPart = { kind: "text"; text: string } | { kind: "ref"; ref: string; number: number };

/** Split one run of plain text into words and references. */
export function splitTaskRefs(text: string): TaskRefPart[] {
  const refs = findTaskRefs(text);
  if (refs.length === 0) return text ? [{ kind: "text", text }] : [];
  const out: TaskRefPart[] = [];
  let last = 0;
  for (const r of refs) {
    if (r.index > last) out.push({ kind: "text", text: text.slice(last, r.index) });
    out.push({ kind: "ref", ref: r.ref, number: r.number });
    last = r.index + r.length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}
