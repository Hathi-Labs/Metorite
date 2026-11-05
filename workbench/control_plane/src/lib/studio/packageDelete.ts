/**
 * The delete of a Studio package, as the app page runs it (`ai_studio.md` §14
 * BETA-1, task 1809, over the routes of A5c-1).
 *
 * Two steps. `GET /api/studio/packages/<id>/delete-preview` gives the counts and
 * the token `confirm`. `DELETE /api/studio/packages/<id>` with `{confirm}` removes
 * the package. The gateway ends a call at 8 s, so a big package answers 202 with
 * what is left and a new token, and the caller sends the DELETE again. 409
 * `counts_changed` carries the new preview and removes nothing more. 503
 * `store_busy` carries `Retry-After`. 404 means the package is gone.
 *
 * Pure of React. Every sentence says what happened and the next step
 * (`failure-copy-explains`). Fence: `packageDelete.test.ts`.
 */
import { busySentence } from "./realDoor";

export interface PreviewObject {
  name: string;
  fields: number;
  records: number;
  capped: boolean;
}

export interface DeletePreview {
  name: string;
  objects: PreviewObject[];
  records: number;
  recordsCapped: boolean;
  versions: number;
  installs: number;
  otherMemberInstalls: number;
  confirm: string;
}

const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : 0);

/** The preview of the wire, or null when it holds no token. */
export function parsePreview(body: unknown): DeletePreview | null {
  if (!isObject(body) || typeof body.confirm !== "string" || !body.confirm) return null;
  const totals = isObject(body.totals) ? body.totals : {};
  const pkg = isObject(body.package) ? body.package : {};
  const objects = Array.isArray(body.objects)
    ? body.objects.filter(isObject).map((o) => ({
      name: String(o.name ?? ""), fields: num(o.fields), records: num(o.records), capped: o.capped === true,
    }))
    : [];
  return {
    name: typeof pkg.name === "string" && pkg.name.trim() ? pkg.name.trim() : "this app",
    objects,
    records: num(totals.records),
    recordsCapped: totals.records_capped === true,
    versions: num(totals.versions),
    installs: num(totals.installs),
    otherMemberInstalls: num(totals.other_member_installs),
    confirm: body.confirm,
  };
}

const plural = (n: number, one: string, many = `${one}s`): string => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

/** The words of the dialog. The body says what the delete removes and that it cannot be undone. */
export function previewCopy(p: DeletePreview): { heading: string; subject: string; body: string; note: string | null } {
  const records = `${plural(p.records, "record")}${p.recordsCapped ? " or more" : ""}`;
  const lines = [
    `This deletes the app and all its data for good: ${plural(p.objects.length, "object")}, ${records}, ${plural(p.versions, "version")}.`,
    "You cannot undo this.",
  ];
  let note: string | null = null;
  if (p.installs > 0) {
    note = p.otherMemberInstalls > 0
      ? `${plural(p.installs, "install")} of this app go too, and ${plural(p.otherMemberInstalls, "install")} reach other members. They lose the app.`
      : `${plural(p.installs, "install")} of this app go too.`;
  }
  return { heading: "Delete this app?", subject: p.name, body: lines.join("\n"), note };
}

export const DELETE_LABEL = "Delete app";
export const GONE_LINE = "This app is already gone. Go back to the library.";
export const OFFLINE_LINE = "The server did not answer, so nothing more was deleted. Check your connection and try again.";
export const PREVIEW_FAILED_LINE = "We could not read what the delete removes, so nothing was deleted. Try again in a moment.";
export const DELETE_FAILED_LINE = "The delete did not finish. Records that it removed stay removed. Try again, and the delete goes on.";

/** The line while a big delete goes on across several calls. */
export function progressLine(remaining: number, capped: boolean): string {
  const more = capped ? " or more" : "";
  return `Deleting. ${plural(remaining, "record")}${more} left.`;
}

export type StepResult =
  | { kind: "done"; removed: number }
  | { kind: "more"; confirm: string; remaining: number; capped: boolean; removed: number }
  | { kind: "changed"; preview: DeletePreview | null; removed: number; message: string }
  | { kind: "busy"; message: string }
  | { kind: "gone" }
  | { kind: "failed"; message: string };

const detailOf = (body: unknown): Record<string, unknown> => {
  const d = isObject(body) ? body.detail : undefined;
  return isObject(d) ? d : {};
};

/** One answer of the DELETE route, as one outcome. */
export function classifyStep(status: number, body: unknown, retryAfter?: string | null): StepResult {
  if (status === 200 && isObject(body) && body.deleted === true) return { kind: "done", removed: num(body.records_removed) };
  if (status === 202 && isObject(body) && typeof body.confirm === "string" && body.confirm) {
    return {
      kind: "more", confirm: body.confirm, remaining: num(body.remaining),
      capped: body.remaining_capped === true, removed: num(body.records_removed),
    };
  }
  if (status === 404) return { kind: "gone" };
  if (status === 409) {
    const d = detailOf(body);
    const removed = num(d.records_removed);
    const earlier = removed > 0 ? ` ${plural(removed, "record")} already went.` : "";
    return {
      kind: "changed", preview: parsePreview(d.preview), removed,
      message: `The app changed while you read this, so nothing more was deleted.${earlier} Check the new counts, then confirm again.`,
    };
  }
  if (status === 503) return { kind: "busy", message: busySentence(retryAfter) };
  return { kind: "failed", message: DELETE_FAILED_LINE };
}

export interface DeleteDeps {
  fetchImpl?: typeof fetch;
  /** Called after each 202, with what is left. */
  onProgress?: (remaining: number, capped: boolean) => void;
  /** The most calls of one run. A bound, so a loop that never ends cannot run. */
  maxCalls?: number;
}

export const MAX_DELETE_CALLS = 100;

/** The preview of a package: the counts, or the reason that it did not load. */
export async function readPreview(
  id: string, fetchImpl: typeof fetch = fetch,
): Promise<{ ok: true; preview: DeletePreview } | { ok: false; gone: boolean; message: string }> {
  try {
    const res = await fetchImpl(`/api/studio/packages/${encodeURIComponent(id)}/delete-preview`);
    if (res.status === 404) return { ok: false, gone: true, message: GONE_LINE };
    const body = await res.json().catch(() => null);
    const preview = res.ok ? parsePreview(body) : null;
    return preview ? { ok: true, preview } : { ok: false, gone: false, message: PREVIEW_FAILED_LINE };
  } catch {
    return { ok: false, gone: false, message: OFFLINE_LINE };
  }
}

/**
 * Send the DELETE with a token, and follow each 202 with its new token until the
 * package is gone or the answer is not a 202. `removed` adds up the records of all
 * the calls. A busy or failed end keeps `confirm`, the token of the last 202, so a
 * retry goes on from there.
 */
export async function runDelete(
  id: string, confirm: string, deps: DeleteDeps = {},
): Promise<Exclude<StepResult, { kind: "more" }> & { confirm: string; totalRemoved: number }> {
  const fetchImpl = deps.fetchImpl ?? fetch;
  const maxCalls = deps.maxCalls ?? MAX_DELETE_CALLS;
  let token = confirm;
  let total = 0;
  for (let call = 0; call < maxCalls; call++) {
    let step: StepResult;
    try {
      const res = await fetchImpl(`/api/studio/packages/${encodeURIComponent(id)}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: token }),
      });
      const body = await res.json().catch(() => null);
      step = classifyStep(res.status, body, res.headers.get("Retry-After"));
    } catch {
      step = { kind: "failed", message: OFFLINE_LINE };
    }
    if ("removed" in step) total += step.removed;
    if (step.kind !== "more") return { ...step, confirm: token, totalRemoved: total };
    token = step.confirm;
    deps.onProgress?.(step.remaining, step.capped);
  }
  return { kind: "failed", message: DELETE_FAILED_LINE, confirm: token, totalRemoved: total };
}
