/**
 * The one save of the Studio IDE (`ai_studio.md` §6.3, slice G3c).
 *
 * Every edit of a member, in the Data tab, the Views tab or the Code tab, goes out through this
 * call: `PUT /api/studio/packages/{id}/draft?based_on=<hash>`. The hash is the `manifest_hash`
 * of the draft that the member READ when the edit began, so a save that lands after a save of
 * Mira (or of another tab) gets 409 and writes nothing. It is the same route and the same
 * `based_on` that `studio_save` uses, so a member and Mira share one rule and one lock.
 *
 * The answer is one of five kinds. Each carries the words for the page. The words say what
 * happened and the next step. None of them says "error".
 */
import type { Problem } from "./draftProblems";
import { StudioOff } from "./realDoor";
import type { PackageDraft } from "./packageDraft";

export const SAVE_STALE =
  "The draft changed after you opened it. Mira or another tab saved it. Nothing was saved, and your edit is still here. Press Reload to see the newer draft.";
export const SAVE_FAILED =
  "The save did not reach Studio. Nothing was saved, and your edit is still here. Check your connection, then press Save again.";
export const SAVE_BUSY = "Studio is busy with another save. Nothing was saved, and your edit is still here. Press Save again in a few seconds.";
export const SAVE_BIG = "The draft is too large to save. Nothing was saved, and your edit is still here. Remove some of it, then press Save again.";
export const SAVE_NO_BASE =
  "The page does not know which draft you started from, so it cannot save safely. Nothing was saved. Press Reload, then make the edit again.";

export type SaveResult =
  | { kind: "saved"; draft: PackageDraft; added: string[]; skipped: string[] }
  | { kind: "stale"; message: string }
  | { kind: "invalid"; message: string; problems: Problem[] }
  | { kind: "failed"; message: string };

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

/** The refusals of a 422, as Problems at their JSON paths. At most 50. */
export function serverProblems(body: unknown): Problem[] {
  const detail = isObject(body) ? body.detail : null;
  const errors = isObject(detail) && Array.isArray(detail.errors) ? detail.errors : [];
  return errors.filter(isObject).slice(0, 50).map((e) => ({
    severity: "error" as const,
    path: typeof e.path === "string" && e.path ? e.path : "$",
    message: typeof e.message === "string" && e.message ? e.message : "Studio refused this part of the draft.",
  }));
}

/** Save `manifest` as the draft of `pkg`, on top of the draft with the hash `basedOn`. */
export async function saveDraft(
  pkg: string, manifest: unknown, basedOn: string | null, prev: PackageDraft, fetchImpl: typeof fetch = fetch,
): Promise<SaveResult> {
  if (!basedOn) return { kind: "failed", message: SAVE_NO_BASE };
  let res: Response;
  try {
    res = await fetchImpl(`/api/studio/packages/${pkg}/draft?based_on=${encodeURIComponent(basedOn)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(manifest),
    });
  } catch {
    return { kind: "failed", message: SAVE_FAILED };
  }
  if (res.status === 404) throw new StudioOff();
  const body: unknown = await res.json().catch(() => ({}));
  if (res.status === 409) return { kind: "stale", message: SAVE_STALE };
  if (res.status === 422) {
    const problems = serverProblems(body);
    const n = problems.length;
    const where = n === 0 ? "" : ` ${n} problem${n === 1 ? " is" : "s are"} in the Problems panel.`;
    return { kind: "invalid", problems, message: `Studio refused the draft.${where} Nothing was saved, and your edit is still here. Fix ${n === 1 ? "it" : "them"}, then press Save again.` };
  }
  if (res.status === 413) return { kind: "failed", message: SAVE_BIG };
  if (res.status === 503) return { kind: "failed", message: SAVE_BUSY };
  if (!res.ok) return { kind: "failed", message: SAVE_FAILED };
  const out = isObject(body) && isObject(body.package) ? body.package : {};
  const actions = isObject(body) && isObject(body.record_actions) ? body.record_actions : {};
  return {
    kind: "saved",
    added: strings(actions.added),
    skipped: strings(actions.skipped),
    draft: {
      ...prev,
      name: String(out.name ?? prev.name),
      manifest: out.manifest ?? manifest,
      liveVersion: typeof out.live_version === "number" ? out.live_version : prev.liveVersion,
      manifestHash: typeof out.manifest_hash === "string" ? out.manifest_hash : null,
    },
  };
}

/** Where a JSON text stops parsing: the 1-based line and a sentence for the member. */
export interface JsonFault {
  line: number;
  message: string;
}

/** Read the text of the Code tab. A text that is not one JSON object is refused with its line. */
export function parseManifestText(text: string): { ok: true; value: Record<string, unknown> } | { ok: false; fault: JsonFault } {
  try {
    const value: unknown = JSON.parse(text);
    if (!isObject(value)) {
      return { ok: false, fault: { line: 1, message: "The manifest is one JSON object, in curly braces. Line 1 starts with something else." } };
    }
    return { ok: true, value };
  } catch {
    // V8 names no line in its message any more, so a small scanner finds where the text stops being JSON.
    const f = findJsonFault(text);
    const line = text.slice(0, f.offset).split("\n").length;
    return { ok: false, fault: { line, message: `The JSON does not parse. Line ${line}: ${f.what}. Fix it, then press Save again.` } };
  }
}

/**
 * Where a text stops being JSON: the offset of the first fault and what is wrong there. Called after
 * `JSON.parse` threw, so a fault exists. If the scan finds none, it points at the end of the text.
 */
export function findJsonFault(text: string): { offset: number; what: string } {
  let i = 0;
  const fail = (what: string, at = i): never => { throw { offset: at, what }; };
  const ws = () => { while (i < text.length && " \t\r\n".includes(text[i])) i++; };
  const seen = () => (i >= text.length ? "the text ends too soon" : `unexpected ${JSON.stringify(text[i])}`);
  const str = () => {
    i++;
    while (i < text.length && text[i] !== '"') {
      if (text[i] === "\n") fail("a string is not closed on its line");
      i += text[i] === "\\" ? 2 : 1;
    }
    if (i >= text.length) fail("a string is not closed");
    i++;
  };
  const value = (): void => {
    ws();
    const c = text[i];
    if (c === "{") {
      i++; ws();
      if (text[i] === "}") { i++; return; }
      for (;;) {
        ws();
        if (text[i] !== '"') fail(`a key in quotes is expected, ${seen()}`);
        str(); ws();
        if (text[i] !== ":") fail(`a colon is expected, ${seen()}`);
        i++; value(); ws();
        if (text[i] === ",") { i++; continue; }
        if (text[i] === "}") { i++; return; }
        fail(`a comma or } is expected, ${seen()}`);
      }
    } else if (c === "[") {
      i++; ws();
      if (text[i] === "]") { i++; return; }
      for (;;) {
        value(); ws();
        if (text[i] === ",") { i++; continue; }
        if (text[i] === "]") { i++; return; }
        fail(`a comma or ] is expected, ${seen()}`);
      }
    } else if (c === '"') str();
    else {
      const m = /^(?:-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null)/.exec(text.slice(i));
      if (!m) fail(`a value is expected, ${seen()}`);
      else i += m[0].length;
    }
  };
  try {
    value(); ws();
    if (i < text.length) fail(`the text goes on after the object, ${seen()}`);
  } catch (e) {
    if (typeof e === "object" && e !== null && "offset" in e) return e as { offset: number; what: string };
    throw e;
  }
  return { offset: text.length, what: "the text is not valid JSON" };
}
