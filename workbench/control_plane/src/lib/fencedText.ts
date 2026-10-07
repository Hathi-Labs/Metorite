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
 * 3. **The output is data, never markup.** The parts are strings, and React
 *    escapes them. Nothing here builds HTML.
 *
 * Pure and framework-free, so the node-env vitest holds it.
 * Fence: `src/lib/fencedText.test.ts`.
 */

/** The pattern of one fenced name, with an optional `#n ` before it. */
export const FENCE_SOURCE = String.raw`(?:(#\d+)\s+)?«([^«»\n]{1,200})»`;

/** One part of a fenced text. */
export type FencedPart =
  | { kind: "text"; text: string }
  | { kind: "name"; text: string; number?: string };

const STRAY = /[«»]/g;

/** Split a text into plain runs and fenced names. */
export function splitFenced(value: string): FencedPart[] {
  const source = String(value ?? "");
  const out: FencedPart[] = [];
  const push = (text: string) => {
    const clean = text.replace(STRAY, "");
    if (!clean) return;
    const last = out[out.length - 1];
    if (last?.kind === "text") last.text += clean;
    else out.push({ kind: "text", text: clean });
  };
  let last = 0;
  for (const m of source.matchAll(new RegExp(FENCE_SOURCE, "g"))) {
    const start = m.index ?? 0;
    push(source.slice(last, start));
    const name = m[2].trim();
    if (name) out.push(m[1] ? { kind: "name", text: name, number: m[1] } : { kind: "name", text: name });
    else if (m[1]) push(`${m[1]} `);
    last = start + m[0].length;
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
