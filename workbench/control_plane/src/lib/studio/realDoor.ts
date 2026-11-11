/**
 * The real data door of a Studio package (`ai_studio.md` §14, slice STU-D2b-1).
 *
 * It is the first `StudioDataDoor` that talks to the gateway. It posts to
 * `/api/studio/apps/<package id>/actions/<action id>`. The package declares
 * each action in its manifest as `{id, uses}`, and the route takes the `id`.
 * A block names the verb, as `object:visit.list`, so the door maps the verb
 * to the declared id.
 *
 * ⚠️ THE READ GUARD. The BFF (`api/studio/[[...path]]`) signs the direct proof for
 * EVERY same-origin POST to `apps/<id>/actions/<id>`. It cannot tell a read from a
 * write. So a read that reached a write action would run as the member's own click.
 * Two guards hold, and each one alone is not enough.
 *
 * 1. The client guard: `door.read` accepts only `object:<name>.list` and
 *    `object:<name>.get`, and refuses every other source with no network call. It
 *    holds for the manifest that the page read at open. The door maps a verb to the
 *    action id of that manifest. A later draft can re-point an id at a write, and the
 *    page does not know until it reads the package again.
 * 2. The body marker: each `.get` body is `{id, read: true}`. The door ignores the
 *    marker for a get, and it refuses a body with that key for a create, an update and
 *    a delete (`_write_input` takes exactly the keys of the write). So a re-pointed id
 *    still cannot delete a record. A list body cannot name a delete either, because
 *    the list takes only filter, sort, limit and offset.
 *
 * A page reads on mount, on focus and on refresh, and none of those may write.
 * Fence: `realDoor.test.ts`.
 *
 * ⚠️ A refusal never shows the raw text of an error. The member reads one of
 * the sentences below.
 *
 * Pure of React, so a test drives it in the node environment.
 */
import { type ActionResult, type Row, type StudioDataDoor, type StudioSource, withMeta } from "./dataDoor";
import { ACTION_FAILED } from "./TypedActionButton";

/** The one shape of a source that a read may name. */
const READ_SOURCE = /^object:[a-z][a-z0-9_]{0,47}\.(?:list|get)$/;
/** A write that a click may name. */
const WRITE_ACTION = /^object:[a-z][a-z0-9_]{0,47}\.(?:create|update|delete)$/;
/** A declared action id (A2). */
const ACTION_ID = /^[a-z][a-z0-9_]{0,47}$/;
const PACKAGE_ID = /^[0-9a-fA-F-]{36}$/;

export const NOT_DECLARED_SENTENCE = "This package does not declare that action, so it cannot run here.";
export const NOT_OPEN_SENTENCE = "This action is not open on this page.";
export const APPROVAL_SENTENCE =
  "Direct edits are not on for your workspace yet, so this change was not saved.";
/**
 * A 202 of the door: the change waits for an approval card, and nothing is
 * written yet (STU-B2c-2a). It is never a save.
 */
export const WAITING_SENTENCE = "This change waits for an approval, so it is not saved yet.";
export const GONE_SENTENCE = "That record is gone. Reload the list.";
export const UNPUBLISHED_SENTENCE = "This package has no such object yet. Publish the package first.";
export const SIGN_IN_SENTENCE = "Your session ended. Sign in again.";

/** Does `source` name a read? The only test the guard makes. */
export function isReadSource(action: unknown): action is string {
  return typeof action === "string" && READ_SOURCE.test(action);
}

/** One action that the manifest of the package declares. */
export interface DeclaredAction {
  id: string;
  uses: string;
  /** An action that fills values by itself. The door never uses it for a verb. */
  set?: unknown;
}

/**
 * The declared action id for each verb. An action with a `set` is left out,
 * because it writes values that the block did not send. The first plain
 * action for a verb wins.
 */
export function verbMap(actions: readonly DeclaredAction[] | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const a of actions ?? []) {
    if (!a || typeof a.id !== "string" || typeof a.uses !== "string") continue;
    if (a.set !== undefined && a.set !== null) continue;
    if (!ACTION_ID.test(a.id)) continue;
    if (!READ_SOURCE.test(a.uses) && !WRITE_ACTION.test(a.uses)) continue;
    if (!(a.uses in out)) out[a.uses] = a.id;
  }
  return out;
}

const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

/**
 * The rows of a list answer. A record is `{id, data:{...}}` on the wire. A
 * row is `{id, ...data}`, and `id` is the record id even when `data` holds a key `id`.
 * An answer with no `records` list gives `null`, so the caller can fail.
 */
export function flattenRecords(answer: unknown): Row[] | null {
  if (!isObject(answer) || !Array.isArray(answer.records)) return null;
  const rows: Row[] = [];
  for (const rec of answer.records) {
    if (!isObject(rec) || typeof rec.id !== "string") continue;
    rows.push({ ...(isObject(rec.data) ? rec.data : {}), id: rec.id });
  }
  return rows;
}

/** The row of a get answer, `{record:{id,data}}`, or null. */
export function flattenRecord(answer: unknown): Row | null {
  if (!isObject(answer) || !isObject(answer.record)) return null;
  return flattenRecords({ records: [answer.record] })?.[0] ?? null;
}

const text = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);

/** `fieldErrors` of the gateway is a list of `{field, message}`. The Form reads a map. */
function fieldMap(raw: unknown, single?: { field: unknown; message: unknown }): Record<string, string> {
  const out: Record<string, string> = {};
  if (Array.isArray(raw)) {
    for (const e of raw) {
      if (isObject(e) && typeof e.field === "string" && text(e.message) && !(e.field in out)) {
        out[e.field] = e.message as string;
      }
    }
  } else if (isObject(raw)) {
    for (const [k, v] of Object.entries(raw)) if (text(v)) out[k] = v as string;
  }
  if (single && typeof single.field === "string" && text(single.message) && !(single.field in out)) {
    out[single.field] = single.message as string;
  }
  return out;
}

/** The detail of an error body: an object, or a string wrapped as a message. */
function detailOf(body: unknown): Record<string, unknown> {
  const d = isObject(body) ? body.detail : undefined;
  if (isObject(d)) return d;
  return typeof d === "string" ? { message: d } : {};
}

/**
 * The store words a rule refusal as "Rule `name` of object `obj`: <message>".
 * The Form and the Board already say "Refused by <rule>:", so keep only the
 * message of the rule.
 */
export function ownMessage(message: string | null): string | null {
  if (message === null) return null;
  const m = /^Rule `[^`]+` of object `[^`]+`: ([\s\S]+)$/.exec(message);
  return m ? m[1] : message;
}

/** The sentence of a 503 that carries a wait. */
export function busySentence(retryAfter: string | null | undefined): string {
  const n = retryAfter ? Number.parseInt(retryAfter, 10) : NaN;
  const wait = Number.isFinite(n) && n > 0 && n <= 60 ? ` in ${n} second${n === 1 ? "" : "s"}` : " in a moment";
  return `This app is busy now. Try again${wait}.`;
}

/**
 * The answer of a write, as the blocks read it (`formModel.ts`, THE REFUSAL
 * CONTRACT). `status` is the HTTP status. `body` is the parsed JSON, or `{}`.
 */
export function mapAnswer(status: number, body: unknown, retryAfter?: string | null): ActionResult {
  // A 202 means a card waits and nothing is written, so it is not a save.
  if (status === 202) return { ok: false, message: WAITING_SENTENCE };
  if (status >= 200 && status < 300) return { ok: true, data: body };
  const d = detailOf(body);
  if (status === 422) {
    const rule = text(d.rule);
    const message = rule ? ownMessage(text(d.message)) : text(d.message);
    const fieldErrors = fieldMap(d.fieldErrors, { field: d.field, message: d.message });
    return {
      ok: false,
      message: message ?? ACTION_FAILED,
      data: { fieldErrors, ...(rule ? { rule } : {}), ...(message ? { message } : {}) },
    };
  }
  if (status === 503) return { ok: false, message: busySentence(retryAfter) };
  if (status === 403) {
    if (d.code === "approval_required") return { ok: false, message: APPROVAL_SENTENCE };
    return { ok: false, message: d.code === "not_declared" ? NOT_DECLARED_SENTENCE : NOT_OPEN_SENTENCE };
  }
  if (status === 401) return { ok: false, message: SIGN_IN_SENTENCE };
  if (status === 404) return { ok: false, message: d.code === "object_missing" ? UNPUBLISHED_SENTENCE : GONE_SENTENCE };
  return { ok: false, message: ACTION_FAILED };
}

export interface RealDoorOptions {
  /** The package uuid. */
  packageId: string;
  /** The `actions` of the package manifest. */
  actions: readonly DeclaredAction[] | undefined;
  /** For a test. Default: the global `fetch`. */
  fetchImpl?: typeof fetch;
}

/** The URL of one declared action of a package, through the BFF. */
export function actionUrl(packageId: string, actionId: string): string {
  return `/api/studio/apps/${packageId}/actions/${actionId}`;
}

/** A read that failed. The message is a plain sentence, and `status` is the HTTP status. */
export class DoorReadError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "DoorReadError";
  }
}

export function createRealDoor(opts: RealDoorOptions): StudioDataDoor {
  if (!PACKAGE_ID.test(opts.packageId)) throw new Error("A package id is a uuid.");
  const verbs = verbMap(opts.actions);
  const declared = new Set((opts.actions ?? []).map((a) => a?.id).filter((x): x is string => typeof x === "string"));
  const fetchImpl: typeof fetch = opts.fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a));

  async function post(actionId: string, body: Record<string, unknown>): Promise<{ status: number; body: unknown; wait: string | null }> {
    const res = await fetchImpl(actionUrl(opts.packageId, actionId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const parsed: unknown = await res.json().catch(() => ({}));
    return { status: res.status, body: parsed, wait: res.headers?.get?.("Retry-After") ?? null };
  }

  return {
    scope: `${opts.packageId}:live`,

    async read(source: StudioSource): Promise<Row[]> {
      // THE READ GUARD. No other source reaches the network (see the file header).
      if (!isReadSource(source?.action)) throw new DoorReadError(NOT_OPEN_SENTENCE, 0);
      const id = verbs[source.action];
      if (!id) throw new DoorReadError(NOT_DECLARED_SENTENCE, 403);
      // The marker is the second guard (see the file header). Only a get carries it.
      const isGet = source.action.endsWith(".get");
      const out = await post(id, isGet ? { ...(source.args ?? {}), read: true } : (source.args ?? {}));
      if (out.status < 200 || out.status >= 300) {
        const failed = mapAnswer(out.status, out.body, out.wait);
        throw new DoorReadError(failed.message ?? ACTION_FAILED, out.status);
      }
      if (source.action.endsWith(".get")) {
        const one = flattenRecord(out.body);
        if (!one) throw new DoorReadError(GONE_SENTENCE, 404);
        return [one];
      }
      const rows = flattenRecords(out.body);
      if (!rows) throw new DoorReadError(ACTION_FAILED, 502);
      const b = isObject(out.body) ? out.body : {};
      return withMeta(rows, {
        ...(typeof b.total === "number" ? { total: b.total } : {}),
        ...(typeof b.total_at_least === "number" ? { atLeast: b.total_at_least } : {}),
      });
    },

    async run(action: string, args?: Record<string, unknown>): Promise<ActionResult> {
      // A write is a verb of an object, or an action that the package declares.
      let id: string | undefined;
      if (WRITE_ACTION.test(action)) id = verbs[action];
      else if (declared.has(action)) id = action;
      if (!id) return { ok: false, message: WRITE_ACTION.test(action) ? NOT_DECLARED_SENTENCE : NOT_OPEN_SENTENCE };
      try {
        const out = await post(id, args ?? {});
        return mapAnswer(out.status, out.body, out.wait);
      } catch {
        return { ok: false, message: ACTION_FAILED };
      }
    },
  };
}

/**
 * Keep one door while the actions stay the same (STU-D2b fix round 1, P2-B). A page
 * reads the package again on focus, and the read gives a new object each time. A new
 * door would reset the edit store and the delete flow, so the door is keyed on the
 * package id and the text of the actions.
 */
export function createDoorKeeper(
  make: (opts: RealDoorOptions) => StudioDataDoor = createRealDoor,
): (packageId: string, actions: readonly DeclaredAction[] | undefined) => StudioDataDoor {
  let key = "";
  let door: StudioDataDoor | null = null;
  return (packageId, actions) => {
    const next = `${packageId}\u0000${JSON.stringify(actions ?? [])}`;
    if (door === null || next !== key) {
      door = make({ packageId, actions });
      key = next;
    }
    return door;
  };
}

// --- the package routes (GET only) -----------------------------------------

export interface PackageSummary {
  id: string;
  name: string;
  status: string;
  live_version: number | null;
  /** The last change, as the gateway writes it. Null when the answer has none. */
  updated_at: string | null;
}

export interface ObjectRow {
  id: string;
  name: string;
  label: string | null;
  fields: Array<{
    key: string; type: string; label: string | null; required: boolean; many: boolean;
    options: string[] | null; to: string | null; hot: boolean;
    /** C13. A lines relation names the field of its line object that points back. */
    lines?: string | null;
  }>;
  rules: Array<Record<string, unknown>>;
}

export interface PackageDetail {
  id: string;
  name: string;
  actions: DeclaredAction[];
  objects: ObjectRow[];
}

/** The studio is off for this org (404 on each route), or the member is out. */
export const STUDIO_OFF = "studio_off";
export class StudioOff extends Error {
  constructor() { super(STUDIO_OFF); }
}

async function getJson(url: string, fetchImpl: typeof fetch = fetch): Promise<unknown> {
  const res = await fetchImpl(url);
  if (res.status === 404) throw new StudioOff();
  if (!res.ok) throw new Error(ACTION_FAILED);
  return res.json().catch(() => ({}));
}

export async function listPackages(fetchImpl: typeof fetch = fetch): Promise<PackageSummary[]> {
  const body = await getJson("/api/studio/packages", fetchImpl);
  const raw = isObject(body) && Array.isArray(body.packages) ? body.packages : [];
  return raw.filter(isObject).map((p) => ({
    id: String(p.id), name: String(p.name ?? ""), status: String(p.status ?? ""),
    live_version: typeof p.live_version === "number" ? p.live_version : null,
    updated_at: typeof p.updated_at === "string" ? p.updated_at : null,
  }));
}

export async function getPackage(id: string, fetchImpl: typeof fetch = fetch): Promise<PackageDetail> {
  if (!PACKAGE_ID.test(id)) throw new StudioOff();
  const body = await getJson(`/api/studio/packages/${id}`, fetchImpl);
  const pkg = isObject(body) && isObject(body.package) ? body.package : {};
  const manifest = isObject(pkg.manifest) ? pkg.manifest : {};
  const objects = isObject(body) && Array.isArray(body.objects) ? (body.objects.filter(isObject) as unknown as ObjectRow[]) : [];
  const actions = Array.isArray(manifest.actions) ? (manifest.actions.filter(isObject) as unknown as DeclaredAction[]) : [];
  return { id, name: String(pkg.name ?? ""), actions, objects };
}
