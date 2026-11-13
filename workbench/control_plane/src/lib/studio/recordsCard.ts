/**
 * The live records card of a Studio read in the chat (STU-F5a).
 *
 * Spec: `project-docs/specs/ai_studio.md` §14, row F5a. Mira reads the records
 * of a package with `studio_record_list`. Its result is text for the model.
 * The card draws the same read for the member as a live table, after the
 * other tool cards of the turn (`components/studio/StudioRecordsCard.tsx`).
 * This file holds every rule of that card, so the node-env vitest holds them:
 *
 * 1. **Which event draws a card.** A call of `studio_record_list` that
 *    finished, whose arguments name a package UUID and an object name, and
 *    whose result is not a refusal. A running call, a failed call and a
 *    refusal draw nothing. Two calls of the same read in one turn draw one
 *    card, because the second card would only repeat the first.
 * 2. **The read.** `object:<name>.list` with the `filter` and the `sort` of
 *    the call when they parse, and none when they do not. At most
 *    `MAX_RECORD_ROWS` rows, as the tool reads. The card never sends the
 *    `offset` of the call: it shows the first rows of the read, and its line
 *    under the table says when more exist.
 * 3. ⚠️ **THE READ-ONLY DOOR.** The BFF signs the direct proof for EVERY
 *    same-origin POST to the action route (`app/api/studio/[[...path]]`).
 *    A typed button on a door calls `door.run`. So a chat card with the full
 *    door would write with no approval card. The card builds its door on
 *    `readActions` for EVERY member, the owner too. A write then answers
 *    `NOT_DECLARED_SENTENCE` with no network call. A change from chat stays
 *    the write tools and their approval card (B2c-2c).
 * 4. **No edit.** The table block carries no `editable`, so no cell edits.
 *    The card draws no Board, no Form, no button and no delete.
 * 5. **No card.** A 404 (Studio is off for the org, or the package is not
 *    the member's), a 403 (the member may not use Studio), an object that the
 *    package does not hold, and an object with no declared list draw nothing.
 *    A 401 says to sign in, with no retry.
 * 6. **A write of Mira reads the card again.** A record write drops the reads
 *    of every live door (`announceAssistantWrite`, `isLiveDoorKey`), and the
 *    card reads its rows again.
 *
 * Fence: `components/studio/StudioRecordsCard.test.ts`.
 */

import { bareToolName } from "@/lib/toolSteps";

import { tableBlock } from "./blocks";
import { type Row, type StudioDataDoor, type StudioSource, stableJson, withMeta } from "./dataDoor";
import { fieldSpecs, tableFields } from "./packagePreview";
import {
  type DeclaredAction, type ObjectRow, type PackageDetail, PackageReadError, SIGN_IN_SENTENCE, StudioOff, createRealDoor,
  getPackage, readActions, verbMap,
} from "./realDoor";

/** The read tool whose call draws a card. */
export const RECORDS_CARD_TOOL = "studio_record_list";
/** The tools of this card, for the placement fence (`chatPlacement.test.ts`). */
export const RECORDS_CARD_TOOLS: readonly string[] = [RECORDS_CARD_TOOL];

/**
 * The most rows that one card reads and draws. It equals `MAX_RECORD_ROWS` of
 * `acb_skills/studio_tools.py`, the page of the tool, and the test holds the two equal.
 */
export const MAX_RECORD_ROWS = 25;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
/** An object name: the grammar of the manifest. */
const IDENT = /^[a-z][a-z0-9_]{0,47}$/;
/** The head of a refusal, in JSON or in a Python repr, when a cut result does not parse. */
const REFUSED_HEAD = /^\{\s*["']ok["']\s*:\s*(?:false|False)\b/;

const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

/** A tool event as the chat holds it. */
export interface RecordsEventLike {
  id: string;
  name: string;
  args?: Record<string, unknown>;
  result?: string;
  status: string;
}

/** One card: the read of one call, and the id of the first call that made it. */
export interface RecordsCall {
  /** The tool call id. It keeps the fold state of the card. */
  eventId: string;
  /** The read: one card for each distinct key in a turn. */
  key: string;
  packageId: string;
  object: string;
  source: StudioSource;
  /** The call named a filter that parsed. An empty answer then says that nothing matches. */
  filtered: boolean;
}

/** True when the result of a call is a refusal of the tool. A result that does not parse is no refusal. */
export function refusedResult(result: string | undefined): boolean {
  const text = (result ?? "").trim();
  if (!text) return false;
  try {
    const v: unknown = JSON.parse(text);
    return isObject(v) && v.ok === false;
  } catch {
    return REFUSED_HEAD.test(text);
  }
}

/**
 * A filter or a sort of the call, as the tool reads it (`_json_arg`): a JSON
 * text or a value that is already parsed. Undefined when it is empty, or when
 * it does not parse to the right shape.
 */
export function parsedArg(raw: unknown, kind: "list" | "object"): unknown[] | Record<string, unknown> | undefined {
  let v = raw;
  if (typeof v === "string") {
    if (!v.trim()) return undefined;
    try {
      v = JSON.parse(v);
    } catch {
      return undefined;
    }
  }
  if (kind === "list") return Array.isArray(v) && v.length > 0 ? v : undefined;
  return isObject(v) && Object.keys(v).length > 0 ? v : undefined;
}

/** The page size of the card: a positive limit of the call, at most `MAX_RECORD_ROWS`. */
export function rowLimit(raw: unknown): number {
  const n = typeof raw === "number" ? raw
    : typeof raw === "string" && /^\d+$/.test(raw.trim()) ? Number(raw.trim()) : Number.NaN;
  return Number.isInteger(n) && n > 0 ? Math.min(n, MAX_RECORD_ROWS) : MAX_RECORD_ROWS;
}

/** The read of one call, or null when the call draws no card (rule 1). */
export function recordsCall(e: RecordsEventLike): RecordsCall | null {
  if (e.status !== "done" || bareToolName(e.name) !== RECORDS_CARD_TOOL) return null;
  if (refusedResult(e.result)) return null;
  const args = isObject(e.args) ? e.args : {};
  const packageId = typeof args.package_id === "string" ? args.package_id.trim().toLowerCase() : "";
  const object = typeof args.object === "string" ? args.object.trim() : "";
  if (!UUID.test(packageId) || !IDENT.test(object)) return null;
  const filter = parsedArg(args.filter, "list");
  const sort = parsedArg(args.sort, "object");
  const source: StudioSource = {
    action: `object:${object}.list`,
    args: { limit: rowLimit(args.limit), ...(filter ? { filter } : {}), ...(sort ? { sort } : {}) },
  };
  return {
    eventId: e.id, key: `${packageId}:${stableJson(source)}`, packageId, object, source, filtered: !!filter,
  };
}

/** The reads of one turn, each once, in order. */
export function recordsCalls(events: readonly RecordsEventLike[] | undefined): RecordsCall[] {
  const out: RecordsCall[] = [];
  const seen = new Set<string>();
  for (const e of events ?? []) {
    const call = recordsCall(e);
    if (!call || seen.has(call.key)) continue;
    seen.add(call.key);
    out.push(call);
  }
  return out;
}

/** The cache key of the package read of a card. */
export const packageKey = (packageId: string): string => `studio:records-card:pkg:${packageId}`;

/**
 * The package of a card. Null on a 404 (Studio is off for the org, or the
 * package is not the member's) and on a 403 (the member may not use Studio),
 * so the card draws nothing (rule 5). A room sends the tool events of a run to
 * every member, so a guest of the room sees the call of another member. A 401
 * throws `SIGN_IN_SENTENCE`. Any other fault throws, and the card says that the
 * records did not load.
 */
export async function readCardPackage(packageId: string, fetchImpl?: typeof fetch): Promise<PackageDetail | null> {
  try {
    return await getPackage(packageId, fetchImpl);
  } catch (err) {
    if (err instanceof StudioOff) return null;
    if (err instanceof PackageReadError && err.status === 403) return null;
    if (err instanceof PackageReadError && err.status === 401) throw new Error(SIGN_IN_SENTENCE);
    throw err;
  }
}

/**
 * The words of a package read that failed, and whether a retry can help. A
 * 401 says to sign in, and a retry cannot pass it. Each other fault is a
 * connection fault, and a retry can help.
 */
export function packageFailure(error: string | null): { sentence: string; retry: boolean } {
  return error === SIGN_IN_SENTENCE ? { sentence: SIGN_IN_SENTENCE, retry: false } : { sentence: LOAD_FAILED, retry: true };
}

/**
 * Rows past `MAX_RECORD_ROWS` drop. The store honours the limit of the body,
 * so this cut is a second guard. The line under the table then says that more
 * records exist.
 */
export function capRows(rows: Row[]): Row[] {
  if (rows.length <= MAX_RECORD_ROWS) return rows;
  return withMeta(rows.slice(0, MAX_RECORD_ROWS), { atLeast: rows.length });
}

/**
 * ⚠️ The door of a card (rule 3). It holds the read actions of the package
 * only, for every member, the owner too. A create, an update and a delete
 * then answer `NOT_DECLARED_SENTENCE`, and a declared action that writes
 * answers `NOT_OPEN_SENTENCE`. Neither reaches the network.
 */
export function cardDoor(
  pkg: { id: string; actions: readonly DeclaredAction[] | undefined }, fetchImpl?: typeof fetch,
): StudioDataDoor {
  const inner = createRealDoor({ packageId: pkg.id, actions: readActions(pkg.actions), ...(fetchImpl ? { fetchImpl } : {}) });
  return {
    scope: inner.scope,
    read: async (source) => capRows(await inner.read(source)),
    run: (action, args) => inner.run(action, args),
  };
}

/**
 * The object that the card draws, or null when it draws nothing (rule 5): no
 * package, no such object, or no list of it on the read-only door.
 */
export function shownObject(pkg: PackageDetail | null | undefined, object: string): ObjectRow | null {
  if (!pkg) return null;
  const found = pkg.objects.find((o) => o?.name === object);
  if (!found) return null;
  return verbMap(readActions(pkg.actions))[`object:${object}.list`] ? found : null;
}

/** The title of a card: the app, then the object. */
export function recordsTitle(pkg: Pick<PackageDetail, "name">, object: Pick<ObjectRow, "name" | "label">): string {
  const label = (object.label ?? "").trim() || object.name;
  const app = pkg.name.trim();
  return app ? `${app} · ${label}` : label;
}

/** The table of a card (rule 4): the `dataGrid` template on the bind path, with no `editable`. */
export function recordsNode(object: Pick<ObjectRow, "fields">, source: StudioSource, title: string) {
  return tableBlock(tableFields(fieldSpecs(object)), source, title);
}

/** The words of a read that gave no row. */
export function emptyLine(call: Pick<RecordsCall, "filtered">): string {
  return call.filtered ? "No records match this filter." : "No records yet.";
}

/** The words of a package read that failed. Never the raw error. */
export const LOAD_FAILED = "The records did not load. Check the connection, then try again.";
