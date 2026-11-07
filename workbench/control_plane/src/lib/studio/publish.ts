/**
 * Publish from the Studio IDE (`ai_studio.md` §14, slice G5).
 *
 * `POST /api/studio/packages/{id}/versions` with `{notes, based_on}` is the same route that
 * `studio_publish` uses, so a member and Mira share one rule and one lock. `based_on` is the
 * `manifest_hash` of the draft that the member READ, so a draft that changed meanwhile gets 409 and
 * nothing goes live. `getVersion` reads the live version with its manifest, so the dialog can show
 * what a publish changes (`GET /api/studio/packages/{id}/versions/{n}`).
 *
 * Every answer carries words that say what happened and the next step.
 */
import type { Problem } from "./draftProblems";
import { serverMessage, serverProblems } from "./draftSave";
import { StudioOff } from "./realDoor";

export const MAX_NOTES = 2000;

export const PUBLISH_STALE = "The draft changed after you opened this. Nothing was published. Press Reload to see the newer draft, then press Publish again.";
export const PUBLISH_BUSY = "Studio is busy with another change. Nothing was published. Press Publish again in a few seconds.";
export const PUBLISH_UNKNOWN =
  "The publish got no answer. It may have worked. Press Reload to check the live version before you press Publish again.";
export const PUBLISH_FAILED = "Studio could not publish. Nothing was published. Press Publish again, or check the Problems panel.";
export const LIVE_READ_FAILED = "The live version did not load, so the page cannot list what changes. Close this box, then press Publish again.";
export const HONEST_LINE =
  "Each new record type becomes a table in your organization. No member can remove a published record type or field yet.";

export type PublishResult =
  | { kind: "published"; version: number; created: string[]; fieldsAdded: number }
  | { kind: "stale"; message: string }
  | { kind: "problems"; message: string; problems: Problem[] }
  | { kind: "busy"; message: string }
  | { kind: "unknown"; message: string }
  | { kind: "failed"; message: string };

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

/** The refusals of a sync (409 `objects_out_of_sync`), as Problems at the record or field that they name. */
export function syncProblems(body: unknown): Problem[] {
  const detail = isObject(body) && isObject(body.detail) ? body.detail : {};
  const rows = Array.isArray(detail.problems) ? detail.problems.filter(isObject) : [];
  return rows.slice(0, 50).map((p) => {
    const obj = typeof p.object === "string" && p.object ? p.object : "";
    const field = typeof p.field === "string" && p.field ? p.field : "";
    const path = obj ? `$.objects[${obj}]${field ? `.fields[${field}]` : ""}` : "$.objects";
    const said = typeof p.message === "string" && p.message ? p.message : "This part does not fit the stored records.";
    return { severity: "error" as const, path, message: said };
  });
}

/** What the member sees when a block is the reason that Publish is off, or null. */
export function publishBlock(s: {
  dirty: boolean; saving: boolean; fault: boolean; errors: number; undoing: boolean; hash: string | null;
}): string | null {
  if (s.fault) return "The Code tab holds JSON that does not parse. Fix it or press Revert, then save.";
  if (s.saving || s.undoing) return "A save is running. Wait for it to end.";
  if (s.dirty) return "Save your edits first. Publish sends the saved draft.";
  if (s.errors > 0) return `The Problems panel holds ${s.errors === 1 ? "an error" : `${s.errors} errors`}. Fix ${s.errors === 1 ? "it" : "them"} first.`;
  if (!s.hash) return "The page does not know which draft is stored. Press Reload, then try again.";
  return null;
}

/** Publish the stored draft. `notes` is trimmed. Never throws, except `StudioOff` for a 404. */
export async function publishDraft(
  pkg: string, notes: string, basedOn: string, fetchImpl: typeof fetch = fetch,
): Promise<PublishResult> {
  let res: Response;
  try {
    res = await fetchImpl(`/api/studio/packages/${pkg}/versions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes: notes.trim(), based_on: basedOn }),
    });
  } catch {
    return { kind: "unknown", message: PUBLISH_UNKNOWN };
  }
  if (res.status === 404) throw new StudioOff();
  const body: unknown = await res.json().catch(() => ({}));
  const detail = isObject(body) && isObject(body.detail) ? body.detail : {};
  if (res.status === 409) {
    if (detail.code === "objects_out_of_sync") {
      const problems = syncProblems(body);
      return { kind: "problems", problems, message: `The records of the live app do not fit this draft. Nothing was published. ${problems.length === 1 ? "The problem is" : "The problems are"} in the Problems panel.` };
    }
    return { kind: "stale", message: PUBLISH_STALE };
  }
  if (res.status === 422) {
    const problems = serverProblems(body);
    const said = problems.length === 0 ? serverMessage(body) : null;
    const where = problems.length > 0 ? ` ${problems.length === 1 ? "The problem is" : "The problems are"} in the Problems panel.` : said ? ` ${said}` : "";
    return { kind: "problems", problems, message: `Studio refused to publish this draft.${where} Nothing was published. Fix ${problems.length === 1 ? "it" : "them"}, save, then press Publish again.` };
  }
  if (res.status === 503) return { kind: "busy", message: PUBLISH_BUSY };
  if (!res.ok) return { kind: "failed", message: PUBLISH_FAILED };
  const v = isObject(body) && isObject(body.version) ? body.version : {};
  const sync = isObject(body) && isObject(body.sync) ? body.sync : {};
  if (typeof v.version !== "number") return { kind: "unknown", message: PUBLISH_UNKNOWN };
  return {
    kind: "published", version: v.version, created: strings(sync.objects_created),
    fieldsAdded: Array.isArray(sync.fields_added) ? sync.fields_added.length : 0,
  };
}

/** The words for a good publish: the version and what the sync made. */
export function publishedWords(r: Extract<PublishResult, { kind: "published" }>): string {
  const made: string[] = [];
  if (r.created.length > 0) made.push(`${r.created.length} new table${r.created.length === 1 ? "" : "s"}`);
  if (r.fieldsAdded > 0) made.push(`${r.fieldsAdded} new field${r.fieldsAdded === 1 ? "" : "s"}`);
  return `Published version ${r.version}.${made.length > 0 ? ` Studio added ${made.join(" and ")} to your organization.` : " Studio added no tables or fields."}`;
}

/** Read the manifest of one version. Throws an `Error` with words, or `StudioOff`. */
export async function getVersionManifest(
  pkg: string, version: number, fetchImpl: typeof fetch = fetch,
): Promise<unknown> {
  let res: Response;
  try {
    res = await fetchImpl(`/api/studio/packages/${pkg}/versions/${version}`);
  } catch {
    throw new Error(LIVE_READ_FAILED);
  }
  if (res.status === 404) throw new StudioOff();
  if (!res.ok) throw new Error(LIVE_READ_FAILED);
  const body: unknown = await res.json().catch(() => ({}));
  const v = isObject(body) && isObject(body.version) ? body.version : null;
  if (!v || !isObject(v.manifest)) throw new Error(LIVE_READ_FAILED);
  return v.manifest;
}

/** Escape and a press outside close the box. While a publish runs they must not, or the member could press Publish twice. */
export function guardedClose(busy: boolean, close: () => void): void {
  if (!busy) close();
}
