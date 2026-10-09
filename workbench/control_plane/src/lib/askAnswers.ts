/**
 * The answer a blocking generative-UI card already got, by its `request_id`.
 *
 * Review round 1, P2-c. An option picker kept its chosen state in React
 * state only. A remount (a reload, a session switch and back, a move of the
 * card in the tree) drew an answered picker as new, and a second click sent
 * the answer again. So the card reads its answer from two places:
 *
 * 1. The live answer of this session: `AgentChat` keeps the `request_id` and
 *    the words of each answer it sent (`askAnswers`).
 * 2. The run's own record: `emit_generative_ui` returns its `request_id` and
 *    the `response` in its tool result (`acb_skills/write_artifact.py`), and
 *    the tool events of a turn are saved with the turn. That one survives a
 *    reload and another device.
 *
 * MAF hands the result over as `str(result)`, so it can be JSON or a Python
 * dict repr. The reader takes both, and reads nothing else.
 *
 * Fence: `askAnswers.test.ts`.
 */

const GENUI_TOOL = "emit_generative_ui";

/** The request id of a blocking card: 32 hex characters (`uuid4().hex`). */
const RID = /['"]request_id['"]\s*:\s*['"]([0-9a-f]{32})['"]/;
/** The response, in either quote style, up to the closing quote. */
const RESPONSE = /['"]response['"]\s*:\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')/;

function fromJson(text: string): [string, string] | null {
  try {
    const v = JSON.parse(text) as Record<string, unknown>;
    if (v && typeof v.request_id === "string" && typeof v.response === "string" && v.response) {
      return [v.request_id, v.response];
    }
  } catch {
    /* not JSON: try the repr below */
  }
  return null;
}

function fromRepr(text: string): [string, string] | null {
  const rid = RID.exec(text)?.[1];
  const m = RESPONSE.exec(text);
  const raw = m ? (m[1] ?? m[2] ?? "") : "";
  if (!rid || !raw) return null;
  return [rid, raw.replace(/\\(["'\\])/g, "$1")];
}

/** The answers that a turn's own `emit_generative_ui` results record. */
export function answersFromTools(
  toolEvents: readonly { name: string; result?: string }[] | undefined,
): Map<string, string> {
  const out = new Map<string, string>();
  for (const e of toolEvents ?? []) {
    if (e.name !== GENUI_TOOL || !e.result) continue;
    const hit = fromJson(e.result) ?? fromRepr(e.result);
    if (hit) out.set(hit[0], hit[1]);
  }
  return out;
}

/**
 * The option ids that an answer names. An answer is `Selected: A, B`, the
 * words the picker sends. A label with a comma in it is matched whole first.
 */
export function pickedFromAnswer(
  answer: string | null | undefined,
  options: readonly { id: string; label: string }[],
): string[] {
  if (!answer) return [];
  const body = answer.replace(/^\s*Selected:\s*/i, "").trim();
  const whole = options.find((o) => o.label === body);
  if (whole) return [whole.id];
  const parts = body.split(/\s*,\s*/);
  return options.filter((o) => parts.includes(o.label)).map((o) => o.id);
}
