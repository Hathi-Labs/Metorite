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
const ANY_TAG_RE = new RegExp(`</?${TAG}[^>]*>`, "g");
/** A marker cut off before its `>`, at the very end of the text. */
const BARE_MARKER_RE = new RegExp(`</?${TAG}[^>]*$`);

/** True when the text holds any DSML markup. */
export function hasDsmlMarkup(text: string): boolean {
  return text.includes(TAG);
}

/**
 * The text with every DSML block, invoke and stray tag removed.
 *
 * Text with no marker comes back as the SAME string, so the renderer costs
 * nothing on every ordinary message.
 */
export function stripDsmlMarkup(text: string): string {
  if (!hasDsmlMarkup(text)) return text;
  return text
    .replace(BLOCK_RE, "")
    .replace(UNCLOSED_BLOCK_RE, "")
    .replace(INVOKE_RE, "")
    .replace(UNCLOSED_INVOKE_RE, "")
    .replace(ANY_TAG_RE, "")
    .replace(BARE_MARKER_RE, "");
}
