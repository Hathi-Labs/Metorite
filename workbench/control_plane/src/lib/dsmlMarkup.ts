/**
 * Hide DeepSeek's DSML tool-call markup in rendered assistant text.
 *
 * Owner report, 2026-10-11. DeepSeek can write its native tool-call format
 * into the answer text, for example `<｜DSML｜calls> <｜DSML｜invoke …>`. The
 * delimiter is the fullwidth bar U+FF5C.
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
 * place where a tool may run.
 */

const BAR = String.fromCharCode(0xff5c);
const TAG = `${BAR}DSML${BAR}`;

const BLOCK_NAMES = "(?:function_calls|tool_calls|calls)";

/** A whole block, from its opening tag to the matching closing tag. */
const BLOCK_RE = new RegExp(
  `<${TAG}(${BLOCK_NAMES})\\b[^>]*>[\\s\\S]*?</${TAG}\\1\\s*>`,
  "g",
);
/** A whole invoke with no block around it. */
const INVOKE_RE = new RegExp(
  `<${TAG}invoke\\b[^>]*>[\\s\\S]*?</${TAG}invoke\\s*>`,
  "g",
);
/** A block that opens and never closes, with everything after it. */
const UNCLOSED_BLOCK_RE = new RegExp(`<${TAG}${BLOCK_NAMES}\\b[^>]*>[\\s\\S]*$`);
/** An invoke that opens and never closes, with everything after it. */
const UNCLOSED_INVOKE_RE = new RegExp(`<${TAG}invoke\\b[^>]*>[\\s\\S]*$`);
/** Any single tag that is left over. */
const ANY_TAG_RE = new RegExp(`</?${TAG}[A-Za-z_][^<>]*>`, "g");
/** The head of a tag cut off before its `>`, at the very end of the text. */
const BARE_MARKER_RE = new RegExp(
  `</?${TAG}[A-Za-z_]+(?:\\s+[A-Za-z_][\\w-]*(?:\\s*=\\s*(?:"[^"]*"?)?)?)*\\s*$`,
);
/**
 * A fence line: any leading whitespace, then three or more ` or ~. Any indent
 * counts, so a fence inside a list item keeps its quote as written.
 */
const FENCE_LINE_RE = /^\s*(`{3,}|~{3,})(.*)$/;

/** True when the text holds any DSML markup. */
export function hasDsmlMarkup(text: string): boolean {
  return text.includes(TAG);
}

function stripPlain(text: string): string {
  if (!hasDsmlMarkup(text)) return text;
  return text
    .replace(BLOCK_RE, "")
    .replace(UNCLOSED_BLOCK_RE, "")
    .replace(INVOKE_RE, "")
    .replace(UNCLOSED_INVOKE_RE, "")
    .replace(ANY_TAG_RE, "")
    .replace(BARE_MARKER_RE, "");
}

/**
 * Split text into runs outside and inside code fences, in order.
 *
 * A fenced run keeps its fence lines. An unclosed fence runs to the end.
 */
function fenceRuns(text: string): { fenced: boolean; text: string }[] {
  const runs: { fenced: boolean; text: string }[] = [];
  let open: string | null = null;
  let current = "";
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    const piece = i < lines.length - 1 ? `${line}\n` : line;
    const m = FENCE_LINE_RE.exec(line);
    if (open === null && m && !(m[1][0] === "`" && m[2].includes("`"))) {
      if (current) runs.push({ fenced: false, text: current });
      current = piece;
      open = m[1];
      return;
    }
    current += piece;
    if (
      open !== null &&
      m &&
      m[1][0] === open[0] &&
      m[1].length >= open.length &&
      m[2].trim() === ""
    ) {
      runs.push({ fenced: true, text: current });
      current = "";
      open = null;
    }
  });
  if (current) runs.push({ fenced: open !== null, text: current });
  return runs;
}

/**
 * The text with every DSML block, invoke and stray tag removed, except inside
 * a code fence. A fenced block is a quote, and it stays as written.
 *
 * Text with no marker comes back as the SAME string, so the renderer costs
 * nothing on every ordinary message.
 */
export function stripDsmlMarkup(text: string): string {
  if (!hasDsmlMarkup(text)) return text;
  return fenceRuns(text)
    .map((run) => (run.fenced ? run.text : stripPlain(run.text)))
    .join("");
}
