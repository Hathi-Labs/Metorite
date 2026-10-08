/**
 * A read tool's result, as blocks a card can draw.
 *
 * The Projects reads write for the MODEL: `Header (n):` lines, `  key: value`
 * facts, `- «name» [category] · … · id <uuid>` rows. The model needs every id
 * and every category key, so the text keeps them. A member needs none of
 * them (owner report, 2026-10-07: the Vocabulary card showed
 * "Backlog [backlog] · default · id 75e0ad79-…"). This module turns the text
 * into blocks, and `components/Readout.tsx` draws them. The ids stay
 * in the tool result, so the model still reads them.
 *
 * Rules:
 *
 * - **No UUID reaches a block.** A `· id <uuid>` part, a `full_id:` line and
 *   a `*_id` fact are removed. A bare UUID anywhere else is removed too.
 * - **No `[key]` reaches the text.** A trailing `[word]` becomes the block's
 *   `tag`, and the card draws it as a word or a chip.
 * - The fence marks stay in the text, and `FencedText` draws them.
 *
 * The email and CRM reads use the same blocks (follow-up of #716 and #735).
 * They print `•` rows, `id=<uuid>` and `(id=<uuid>)`, and `• key: value`
 * facts. So a `•` row is an item, an `id=` is removed, and a `• key: value`
 * row is a labelled fact. A `•` row also splits its facts at ` — `. A `-`
 * row (the Projects reads) splits at ` · ` only, as before.
 *
 * Pure. Fence: `src/lib/readout.test.ts`.
 */

const UUID_SRC = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const UUID_ANY = new RegExp(UUID_SRC, "gi");

export type ReadoutBlock =
  | { kind: "heading"; text: string; count?: number }
  | { kind: "field"; key: string; value: string }
  | { kind: "item"; text: string; tag?: string; parts: string[]; section: string }
  | { kind: "line"; text: string; tag?: string };

/** A machine line: an id the cards parse, a link the page opens. */
const MACHINE = /^\s*(?:full_id|link|done|[a-z_]+_id):/i;
/** `Statuses (4):`, `Children (2):`: a line at the margin that ends in a colon. */
const HEADING = /^(\S.*?)(?:\s*\((\d+)\))?:$/;
/** `  status: «Done»`. The key holds no mark. */
const FIELD = /^\s+([^:«»]{1,40}):\s(.*)$/;

/**
 * A line the read writes for the MODEL only: an instruction about when to
 * call a tool. A member reads the data, not the agent's rules. The line
 * stays in the tool result. Fence: `readout.test.ts`.
 */
const MODEL_ONLY = [/\bwhen you call the tool\b/i, /\bCall it when the member asks\b/i];

/** Remove every id from one line of text. */
export function withoutIds(line: string): string {
  return line
    .replace(new RegExp(String.raw`\s*·\s*(?:[a-z_]+[\s_])?id:?\s+${UUID_SRC}`, "gi"), "")
    .replace(new RegExp(String.raw`\s*\((?:[a-z_]+[\s_])?id:?\s+${UUID_SRC}\)`, "gi"), "")
    // The email and CRM forms: `(id=<uuid>)`, and `— id=<uuid>, 3 unread`.
    .replace(/\s*\((?:[a-z_]+_)?id=[^)\s]+\)/gi, "")
    .replace(new RegExp(String.raw`(?:[a-z_]+_)?id=${UUID_SRC},?\s*`, "gi"), "")
    .replace(UUID_ANY, "")
    .replace(/\s+([.,])(?=\s|$)/g, "$1")
    .replace(/\s*(?:·|—)\s*$/, "")
    .trimEnd();
}

/** Take a `[word]` key out of a row: `«Backlog» [backlog] · default`. */
function takeTag(text: string): { text: string; tag?: string } {
  // A leading `[pending] Newsletter rule: …` (the email rule history) too.
  const lead = /^\[([a-z_ ]{1,30})\]\s+/.exec(text);
  if (lead) return { text: text.slice(lead[0].length).trim(), tag: lead[1] };
  const m = /\s\[([a-z_ ]{1,30})\](?=\s*(?:·|—|$))/.exec(text);
  if (!m) return { text };
  return { text: (text.slice(0, m.index) + text.slice(m.index + m[0].length)).trim(), tag: m[1] };
}

/** `• lead_name: Ravi`, a fact of an email or CRM read. The key holds no mark. */
const DOT_FIELD = /^([a-z][a-z_ ]{0,39}):\s+(.+)$/;

/** The blocks of a result. `legend` is the data legend line to drop. */
export function parseReadout(result: string, legend = ""): ReadoutBlock[] {
  const out: ReadoutBlock[] = [];
  let section = "";
  for (const raw of (result || "").split(/\r?\n/)) {
    if (!raw.trim() || (legend && raw.startsWith(legend))) continue;
    if (MACHINE.test(raw) || MODEL_ONLY.some((re) => re.test(raw))) continue;
    // `• status_id: <uuid>` is an id fact. It goes before its value does.
    const dotKey = /^\s*•\s+([a-z][a-z_ ]{0,39}):/.exec(raw);
    if (dotKey && /(^|_)ids?$/i.test(dotKey[1].trim())) continue;
    const line = withoutIds(raw);
    if (!line.trim()) continue;
    const item = /^\s*([-•])\s+(.*)$/.exec(line);
    if (item) {
      const dot = item[1] === "•";
      const fact = dot ? DOT_FIELD.exec(item[2].trim()) : null;
      if (fact) {
        out.push({ kind: "field", key: fact[1].trim(), value: fact[2].trim() });
        continue;
      }
      const { text, tag } = takeTag(item[2]);
      const parts = text.split(dot ? / · | — / : " · ").map((p) => p.trim()).filter(Boolean);
      out.push({ kind: "item", text, tag, parts, section });
      continue;
    }
    const field = FIELD.exec(line);
    if (field && !/(^|_)id$/i.test(field[1].trim())) {
      out.push({ kind: "field", key: field[1].trim(), value: field[2].trim() });
      continue;
    }
    const heading = /^\s/.test(line) ? null : HEADING.exec(line.trim());
    if (heading) {
      section = heading[1].trim();
      out.push(
        heading[2] !== undefined
          ? { kind: "heading", text: takeTag(section).text, count: Number(heading[2]) }
          : { kind: "heading", text: takeTag(section).text },
      );
      continue;
    }
    const plain = takeTag(line.trim());
    out.push(plain.tag ? { kind: "line", text: plain.text, tag: plain.tag } : { kind: "line", text: plain.text });
  }
  return out;
}

/** A vocabulary status row: `«Backlog»` + tag `backlog` + `default`. */
export function statusRow(block: Extract<ReadoutBlock, { kind: "item" }>): {
  name: string;
  category?: string;
  isDefault: boolean;
} {
  return {
    name: block.parts[0] ?? block.text,
    category: block.tag,
    isDefault: block.parts.slice(1).includes("default"),
  };
}
