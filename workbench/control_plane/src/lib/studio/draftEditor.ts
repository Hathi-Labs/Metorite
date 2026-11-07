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
import { stableJson } from "./dataDoor";
import type { PackageDraft } from "./packageDraft";
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

// The whole stable text, not a 32-bit hash: a collision would call a real edit "no edit".
const same = (a: unknown, b: unknown): boolean => stableJson(a ?? null) === stableJson(b ?? null);

/** True while an edit is open: a working copy, or Code text that does not parse. */
const isOpen = (s: EditState): boolean => s.working !== null || s.fault !== null;

/** The base of a new edit: the one the open edit began from, else the draft the member sees now. */
const baseFor = (s: EditState, draft: PackageDraft): string | null => (isOpen(s) ? (s.base ?? draft.manifestHash) : draft.manifestHash);

export function reduce(s: EditState, a: EditAction): EditState {
  switch (a.type) {
    case "edit": {
      // An edit from the Data or Views tab while the Code text is broken would drop that text. The tabs are locked then.
      // An edit while a save is in flight would be lost when the answer comes, so the tabs are locked then too.
      if (s.fault || s.saving) return s;
      if (same(a.manifest, a.draft.manifest)) return CLEAN;
      return { ...s, working: a.manifest, base: baseFor(s, a.draft), code: null, notice: null, server: [] };
    }
    case "code": {
      if (s.saving) return s;
      const parsed = parseManifestText(a.text);
      if (!parsed.ok) {
        return { ...s, code: a.text, fault: parsed.fault, base: baseFor(s, a.draft), notice: null, server: [] };
      }
      // The same manifest in other words is no edit. The text stays, so the cursor does not jump.
      if (same(parsed.value, a.draft.manifest)) return { ...CLEAN, code: a.text, base: a.draft.manifestHash };
      return { ...s, code: a.text, fault: null, working: parsed.value, base: baseFor(s, a.draft), notice: null, server: [] };
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
  return isOpen(s) && s.base !== null && !!draft && draft.manifestHash !== s.base;
}

export const NEWER_LINE =
  "A newer draft exists. Mira or another tab saved it after you began. Your edit is kept here, and a save is refused. Reload drops your edit and shows the newer draft, so copy your JSON first if you need it.";

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

/** True when the tabs take no edit: the Code text does not parse, or a save is in flight. */
export function editLocked(s: EditState): boolean {
  return s.fault !== null || s.saving;
}

/** The text that the Code tab shows: the member's own text while an edit is open or the draft is the one it began from. */
export function codeValue(s: EditState, draft: PackageDraft | null): string | null {
  if (s.code === null) return null;
  if (isOpen(s)) return s.code;
  return draft && s.base === draft.manifestHash ? s.code : null;
}

/**
 * The key of the Code editor. A new key mounts a new editor with an empty undo stack. While no edit
 * is open, the key follows the stored draft, so Undo cannot bring back text of an older draft. While
 * an edit is open, it holds the base of that edit.
 */
export function codeKey(s: EditState, draft: PackageDraft | null): string {
  return (isOpen(s) ? s.base : draft?.manifestHash) ?? "none";
}

/** The number of places where `b` differs from `a`: a key or an item that is only on one side counts as one. */
export function countChanges(a: unknown, b: unknown): number {
  if (a === undefined || b === undefined) return a === b ? 0 : 1;
  if (Array.isArray(a) && Array.isArray(b)) {
    let n = 0;
    for (let i = 0; i < Math.max(a.length, b.length); i++) n += countChanges(a[i], b[i]);
    return n;
  }
  if (typeof a === "object" && a && typeof b === "object" && b && !Array.isArray(a) && !Array.isArray(b)) {
    let n = 0;
    for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) n += countChanges((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]);
    return n;
  }
  return same(a, b) ? 0 : 1;
}
