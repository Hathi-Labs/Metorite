/**
 * Hide DeepSeek's tool-call markup in rendered assistant text.
 *
 * Owner report, 2026-10-11. DeepSeek can write its native tool-call format
 * into the answer text, for example `<｜DSML｜calls> <｜DSML｜invoke …>`. The
 * canonical delimiter is the fullwidth bar U+FF5C.
 *
 * Owner report, 2026-10-17. A second answer showed `<｜｜DSML｜｜tool_calls>`,
 * with TWO bars on each side, and this file knew only one. So the marker is
 * now tolerant, as the Router's is: one to three bars on each side of `DSML`,
 * from U+FF5C, `|`, U+2502, U+2223 and U+FFE8, with up to two spaces or tabs
 * after the `<` and around each bar. No space may come between the last bar
 * and the tag name.
 *
 * DeepSeek V3 can also write `<｜tool▁calls▁begin｜> … <｜tool▁calls▁end｜>`
 * (▁ is U+2581). That region is hidden too.
 *
 * The real fix is in the Router (`customer_console/dsml.py`). It turns the
 * markup into structured tool calls before any client sees the answer. This
 * file is the second line, for two paths the Router cannot reach:
 *
 *  1. A message STORED before the fix. History keeps the text it received.
 *  2. A box that serves with `ROUTER_SERVING_ENABLED` off, where completions
 *     go to the vendor through the gateway's local path.
 *
 * It only HIDES text. It never runs a call, because the renderer is not a
 * place where a tool may run. It reads the text the way the Router's machine
 * does: left to right, a region at a time, and a code fence is tracked on the
 * SHOWN text only. So a fence inside a hidden region (V3 writes one) never
 * turns the rest of the answer into a quote.
 */

const ch = (code: number) => String.fromCharCode(code);

/** Every bar a delimiter may use: U+FF5C, `|`, U+2502, U+2223 and U+FFE8. */
const BARS = `[${ch(0xff5c)}|${ch(0x2502)}${ch(0x2223)}${ch(0xffe8)}]`;
/** DeepSeek V3's token separator, U+2581. */
const LOW = ch(0x2581);
const SP = "[ \\t]{0,2}";
/** Before `DSML`: spaces, then one to three bars, each with spaces after it. */
const LEFT = `${SP}(?:${BARS}${SP}){1,3}`;
/** After `DSML`: one to three bars, each with spaces before it. */
const RIGHT = `(?:${SP}${BARS}){1,3}`;
const CORE = `${LEFT}DSML${RIGHT}`;

const BLOCK_NAME_RE = /^(?:function_calls|tool_calls|calls)$/;

/** Where a region may start: a DSML tag, a stray closing one, or a V3 token. */
const MARK_SOURCE = `</?${CORE}|<${LEFT}tool${LOW}`;
const MARK_RE = new RegExp(MARK_SOURCE, "g");
const HAS_MARK_RE = new RegExp(MARK_SOURCE);
/** The first tag of a region. Group 3 is set when it is a V3 token. */
const FIRST_TAG_RE = new RegExp(
  `<(/?)${CORE}([A-Za-z_]+)[^<>]*>` +
    `|<${LEFT}tool${LOW}([a-z${LOW}]+)${RIGHT}[ \\t]{0,2}>`,
  "y",
);
/** The head of a first tag cut off before its `>`, at the very end of the text. */
const TAG_HEAD_RE = new RegExp(
  `(?:</?${CORE}[A-Za-z_]+(?:\\s+[A-Za-z_][\\w-]*(?:\\s*=\\s*(?:"[^"]*"?)?)?)*\\s*` +
    `|<${LEFT}tool${LOW}[a-z${LOW}]*(?:${SP}${BARS}){0,3}[ \\t]{0,2})$`,
  "y",
);
/** A closing block tag. Any block name closes any block, as in the Router. */
const BLOCK_CLOSE_RE = new RegExp(
  `</${CORE}(?:function_calls|tool_calls|calls)\\s*>`,
  "g",
);
const INVOKE_CLOSE_RE = new RegExp(`</${CORE}invoke\\s*>`, "g");
/** The token that ends the region a V3 first token opens. */
const V3_CLOSERS: Record<string, RegExp> = {
  [`calls${LOW}begin`]: new RegExp(
    `<${LEFT}tool${LOW}calls${LOW}end${RIGHT}[ \\t]{0,2}>`,
    "g",
  ),
  [`call${LOW}begin`]: new RegExp(
    `<${LEFT}tool${LOW}call${LOW}end${RIGHT}[ \\t]{0,2}>`,
    "g",
  ),
};
/** A real tag name starts with one of these. */
const TAG_START_RE = /[A-Za-z_]/;
/**
 * A fence line: any leading whitespace, then three or more ` or ~. Any indent
 * counts, so a fence inside a list item keeps its quote as written.
 */
const FENCE_LINE_RE = /^[ \t]*(`{3,}|~{3,})(.*)$/;

/**
 * True when the text holds any markup this file hides. A substring test
 * comes first, so ordinary text never reaches a regex.
 */
export function hasDsmlMarkup(text: string): boolean {
  if (!text.includes("DSML") && !text.includes(`tool${LOW}`)) return false;
  return HAS_MARK_RE.test(text);
}

/** Where the first match of `re` at or after `from` ends, or null. */
function endOfMatch(re: RegExp, text: string, from: number): number | null {
  re.lastIndex = from;
  const m = re.exec(text);
  return m ? m.index + m[0].length : null;
}

/**
 * Where the region that starts at a marker ends, or null when no tag forms
 * there. A block or an invoke runs to its closing tag, a V3 `calls▁begin` or
 * `call▁begin` to its end token, and any other tag is one tag long. A region
 * that never closes, or a head cut off at the end, runs to the end.
 */
function regionEnd(text: string, start: number): number | null {
  FIRST_TAG_RE.lastIndex = start;
  const first = FIRST_TAG_RE.exec(text);
  if (!first) {
    TAG_HEAD_RE.lastIndex = start;
    return TAG_HEAD_RE.test(text) ? text.length : null;
  }
  const end = first.index + first[0].length;
  let closer: RegExp | undefined;
  if (first[3] !== undefined) closer = V3_CLOSERS[first[3]];
  else if (!first[1] && BLOCK_NAME_RE.test(first[2])) closer = BLOCK_CLOSE_RE;
  else if (!first[1] && first[2] === "invoke") closer = INVOKE_CLOSE_RE;
  if (!closer) return end;
  return endOfMatch(closer, text, end) ?? text.length;
}

/** Tracks whether the text shown so far sits inside a code fence. */
function fenceTracker() {
  let open: string | null = null;
  let line = "";
  const endLine = () => {
    const m = FENCE_LINE_RE.exec(line);
    line = "";
    if (!m) return;
    if (open === null) {
      // A backtick fence may not hold a backtick in its info string.
      if (!(m[1][0] === "`" && m[2].includes("`"))) open = m[1];
    } else if (m[1][0] === open[0] && m[1].length >= open.length && m[2].trim() === "") {
      open = null;
    }
  };
  return {
    push(text: string) {
      text.split("\n").forEach((part, i) => {
        if (i) endLine();
        if (line.length < 200) line += part.slice(0, 200 - line.length);
      });
    },
    inside: () => open !== null,
  };
}

/**
 * The text with every DSML block, invoke, stray tag and V3 region removed,
 * except inside a code fence. A fenced one is a quote, and it stays as
 * written.
 *
 * Text with no marker comes back as the SAME string, so the renderer costs
 * nothing on every ordinary message.
 */
export function stripDsmlMarkup(text: string): string {
  if (!hasDsmlMarkup(text)) return text;
  const fence = fenceTracker();
  let out = "";
  const show = (piece: string) => {
    if (!piece) return;
    out += piece;
    fence.push(piece);
  };
  let pos = 0;
  while (pos < text.length) {
    MARK_RE.lastIndex = pos;
    const mark = MARK_RE.exec(text);
    if (!mark) {
      show(text.slice(pos));
      break;
    }
    show(text.slice(pos, mark.index));
    const after = mark.index + mark[0].length;
    // A quote inside a fence, or a marker with no tag name, is text.
    const end =
      fence.inside() || !TAG_START_RE.test(text.charAt(after))
        ? null
        : regionEnd(text, mark.index);
    if (end === null) {
      show(mark[0]);
      pos = after;
      continue;
    }
    pos = end;
  }
  return out;
}
