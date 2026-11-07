/**
 * The edit state of the Studio IDE (`ai_studio.md` §6.3, slice G3c). Pure: a reducer and one save step.
 *
 * The page holds the STORED draft (from `useDraftWatch`) and, beside it, the member's working copy.
 * The working copy is null until the first edit. Then:
 *
 * - The tabs draw the working copy. The Mira facts keep the STORED draft, so she never reads text
 *   that was not saved.
 * - `base` is the `manifest_hash` of the stored draft at the first edit. A save sends it as
 *   `based_on`. The poll may bring a newer draft meanwhile. It never replaces the working copy: the
 *   page says "A newer draft exists", and a save gets 409 and keeps the edit.
 * - A text of the Code tab that does not parse is a `fault`. It never reaches the working copy, and
 *   a save is refused with its line.
 * - A working copy equal to the stored draft is no edit: the state returns to clean.
 */
import { draftHash, type PackageDraft } from "./packageDraft";
import type { Problem } from "./draftProblems";
import { parseManifestText, saveDraft, type JsonFault } from "./draftSave";
import { StudioOff } from "./realDoor";

export type Notice =
  | { kind: "saved"; text: string }
  | { kind: "stale" | "failed" | "invalid" | "json"; text: string };

export interface EditState {
  working: unknown | null;
  base: string | null;
  /** The text of the Code tab while the member types. Null follows the manifest. */
  code: string | null;
  fault: JsonFault | null;
  saving: boolean;
  notice: Notice | null;
  /** The refusals of the server check (422), at their JSON paths. */
  server: Problem[];
}

export const CLEAN: EditState = { working: null, base: null, code: null, fault: null, saving: false, notice: null, server: [] };

export type EditAction =
  | { type: "edit"; manifest: unknown; draft: PackageDraft }
  | { type: "code"; text: string; draft: PackageDraft }
  | { type: "saving" }
  | { type: "settled"; notice: Notice; server?: Problem[]; fault?: JsonFault | null }
  | { type: "saved"; notice: Notice }
  | { type: "revert" };

const same = (a: unknown, b: unknown): boolean => draftHash(a) === draftHash(b);

export function reduce(s: EditState, a: EditAction): EditState {
  switch (a.type) {
    case "edit": {
      // An edit from the Data or Views tab while the Code text is broken would drop that text. The tabs are locked then.
      if (s.fault) return s;
      if (same(a.manifest, a.draft.manifest)) return CLEAN;
      return { ...s, working: a.manifest, base: s.base ?? a.draft.manifestHash, code: null, notice: null, server: [] };
    }
    case "code": {
      const parsed = parseManifestText(a.text);
      if (!parsed.ok) {
        return { ...s, code: a.text, fault: parsed.fault, base: s.base ?? a.draft.manifestHash, notice: null, server: [] };
      }
      // The same manifest in other words is no edit. The text stays, so the cursor does not jump.
      if (same(parsed.value, a.draft.manifest)) return { ...CLEAN, code: a.text };
      return { ...s, code: a.text, fault: null, working: parsed.value, base: s.base ?? a.draft.manifestHash, notice: null, server: [] };
    }
    case "saving":
      return { ...s, saving: true, notice: null };
    case "settled":
      return { ...s, saving: false, notice: a.notice, server: a.server ?? s.server, fault: a.fault === undefined ? s.fault : a.fault };
    case "saved":
      return { ...CLEAN, notice: a.notice };
    case "revert":
      return CLEAN;
  }
}

/** True when the member holds an edit that the server does not hold. */
export function isDirty(s: EditState, draft: PackageDraft | null): boolean {
  if (s.fault) return true;
  return s.working !== null && !!draft && !same(s.working, draft.manifest);
}

/** The manifest the tabs draw. */
export function shownManifest(s: EditState, draft: PackageDraft | null): unknown {
  return s.working ?? draft?.manifest ?? null;
}

/** True when an edit is open and the stored draft is no longer the one that the edit began from. */
export function newerDraft(s: EditState, draft: PackageDraft | null): boolean {
  return s.working !== null && s.base !== null && !!draft && draft.manifestHash !== s.base;
}

export const NEWER_LINE =
  "A newer draft exists. Mira or another tab saved it after you began. Your edit is kept, and a save is refused until you press Reload.";

export const OFF_LINE = "This app is not available to you any more. Nothing was saved. Go back to Studio.";

function savedLine(added: readonly string[], skipped: readonly string[]): string {
  const base = "Saved. Mira reads this draft in her next turn.";
  if (added.length > 0) return `${base} Studio added ${added.length} record action${added.length === 1 ? "" : "s"} so the page can list, open and change records.`;
  if (skipped.length > 0) return `${base} The app is too big to add record actions for ${skipped.join(", ")}.`;
  return base;
}

/**
 * One save: the Code text is read first, a text that does not parse is refused with its line, and
 * the PUT goes out only when the text parses. Returns the action for the reducer and, after a
 * save, the new stored draft for the page to adopt.
 */
export async function commitSave(
  pkg: string, s: EditState, draft: PackageDraft, fetchImpl: typeof fetch = fetch,
): Promise<{ action: EditAction; adopt?: PackageDraft }> {
  let manifest = s.working;
  if (s.code !== null) {
    const parsed = parseManifestText(s.code);
    if (!parsed.ok) return { action: { type: "settled", notice: { kind: "json", text: parsed.fault.message }, fault: parsed.fault } };
    manifest = parsed.value;
  }
  if (manifest === null) return { action: { type: "revert" } };
  try {
    const r = await saveDraft(pkg, manifest, s.base, draft, fetchImpl);
    if (r.kind === "saved") return { action: { type: "saved", notice: { kind: "saved", text: savedLine(r.added, r.skipped) } }, adopt: r.draft };
    if (r.kind === "invalid") return { action: { type: "settled", notice: { kind: "invalid", text: r.message }, server: r.problems } };
    return { action: { type: "settled", notice: { kind: r.kind, text: r.message } } };
  } catch (e) {
    if (e instanceof StudioOff) return { action: { type: "settled", notice: { kind: "failed", text: OFF_LINE } } };
    throw e;
  }
}
