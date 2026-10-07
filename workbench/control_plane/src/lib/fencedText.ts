/**
 * The data fence, read for a person.
 *
 * The skills fence every value that a member wrote in «guillemets»
 * (`skill_projects/client.py::data`). The fence is for the MODEL: an
 * instruction inside the marks is data, never an order. It also keeps the
 * card parsers safe, because a fenced value cannot forge a `key: value` line.
 * So the server text keeps its marks, and only the UI removes them.
 *
 * This module is the one parser of that fence for display. It splits a text
 * into plain runs and names. `FencedText.tsx` draws a name as a pill or as a
 * quiet emphasis, and `remarkEntityPills.ts` builds its pills from the same
 * pattern. Owner report, 2026-10-07: the marks reached the member raw, on a
 * confirmation card and in a generative-UI list.
 *
 * Three rules:
 *
 * 1. **A name is one balanced pair on one line**, 1 to 200 characters, with no
 *    mark inside. `#5 «X»` is one task name that carries its number.
 * 2. **A stray mark never shows.** An unclosed «, a lone », the outer pair of a
 *    nested «a «b» c» and an empty «» are all dropped.
 * 3. **A quotation keeps its marks.** French typography pads the marks with
 *    a space (`« Bonjour »`), and `data()` never does, so a padded pair is a
 *    quotation and stays text as written (review round 1).
 * 4. **The output is data, never markup.** The parts are strings, and React
 *    escapes them. Nothing here builds HTML.
 *
 * Pure and framework-free, so the node-env vitest holds it.
 * Fence: `src/lib/fencedText.test.ts`.
 */

/** The pattern of one fenced name, with an optional `#n ` before it. */
export const FENCE_SOURCE = String.raw`(?:(#\d+)\s+)?«([^«»\n]{1,200})»`;

/** A quotation: a pair padded with white space inside. Kept as written. */
export const QUOTE_SOURCE = String.raw`«\s[^«»\n]{0,200}?\s»`;

/** One part of a fenced text. */
export type FencedPart =
  | { kind: "text"; text: string }
  | { kind: "name"; text: string; number?: string };

const STRAY = /[«»]/g;

/** Split a text into plain runs and fenced names. */
export function splitFenced(value: string): FencedPart[] {
  const source = String(value ?? "");
  const out: FencedPart[] = [];
  const push = (text: string, keep = false) => {
    const clean = keep ? text : text.replace(STRAY, "");
    if (!clean) return;
    const last = out[out.length - 1];
    if (last?.kind === "text") last.text += clean;
    else out.push({ kind: "text", text: clean });
  };
  let last = 0;
  for (const m of source.matchAll(new RegExp(`(${QUOTE_SOURCE})|${FENCE_SOURCE}`, "g"))) {
    const start = m.index ?? 0;
    push(source.slice(last, start));
    last = start + m[0].length;
    if (m[1] !== undefined) {
      push(m[1], true);
      continue;
    }
    const name = m[3].trim();
    if (name) out.push(m[2] ? { kind: "name", text: name, number: m[2] } : { kind: "name", text: name });
    else if (m[2]) push(`${m[2]} `);
  }
  push(source.slice(last));
  return out;
}

/** The text with every mark removed: for an `aria-label`, a `title` or a sort. */
export function unfenced(value: string): string {
  return splitFenced(value)
    .map((p) => (p.kind === "name" && p.number ? `${p.number} ${p.text}` : p.text))
    .join("");
}

/** True when the text holds a fence mark at all. */
export function hasFence(value: string): boolean {
  return /[«»]/.test(String(value ?? ""));
}

/**
 * The value when it is exactly one fenced name (`«Done»`, `#7 «Fix it»`),
 * else null. A card draws such a value as one typed element.
 */
export function soleName(value: string): { text: string; number?: string } | null {
  const parts = splitFenced(String(value ?? "").trim());
  if (parts.length !== 1 || parts[0].kind !== "name") return null;
  const p = parts[0];
  return p.number ? { text: p.text, number: p.number } : { text: p.text };
}
