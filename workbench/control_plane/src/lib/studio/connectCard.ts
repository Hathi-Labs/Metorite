/**
 * The Connect card of a Studio save in the chat (STU-I8d).
 *
 * Spec: `project-docs/specs/ai_studio.md` §14, row I8d. Mira saves a draft with `studio_save`, or makes a
 * draft with `studio_create_from_template`. When the draft names a connection that is not met, the chat
 * draws the same Connect lines as the IDE (`ConnectLines` of I8a), after the other tool cards of the turn
 * (`components/studio/StudioConnectCard.tsx`). This file holds every rule of that card, so the node-env
 * vitest holds them:
 *
 * 1. **Which event draws a card.** A call of `studio_save` or `studio_create_from_template` that finished,
 *    and whose result is a success that names a `package_id` UUID. A running call, a failed call and a
 *    refusal draw nothing. Two saves of one package in a turn draw one card, because the second card would
 *    only repeat the first.
 * 2. **The read.** `GET /api/studio/packages/<id>/connections` (I8a), in the cache of the IDE. A 404 (a flag
 *    is off, or the member is not the owner) draws nothing.
 * 3. **The return stays the I8a one**, `/studio/build?pkg=<id>`. The chat is not under `/studio/`, so this
 *    file does not widen `STUDIO_PREFIX`.
 * 4. ⚠️ **A key is never typed in the card.** The card has no field. Connect opens the sheet of I8a, and
 *    the sheet leaves for the connect page of the owning app.
 *
 * Fence: `components/studio/StudioConnectCard.test.ts`.
 */

import { bareToolName } from "@/lib/toolSteps";

/** The tools whose save can name a connection. */
export const CONNECT_CARD_TOOLS: readonly string[] = ["studio_save", "studio_create_from_template"];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
/** The head of a success, in JSON or in a Python repr, when a cut result does not parse. */
const OK_HEAD = /^\{\s*["']ok["']\s*:\s*(?:true|True)\b/;
const ID_PAIR = /["']package_id["']\s*:\s*["']([0-9a-fA-F-]{36})["']/;

const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

/** A tool event as the chat holds it. */
export interface ConnectEventLike {
  id: string;
  name: string;
  result?: string;
  status: string;
}

/** One card: the package, and the id of the first call that made it. */
export interface ConnectCall {
  /** The tool call id. It keeps the fold state of the card. */
  eventId: string;
  packageId: string;
}

/** The package id of a successful save result, or null. */
export function savedPackageId(result: string | undefined): string | null {
  const text = (result ?? "").trim();
  if (!text) return null;
  let id: unknown = null;
  try {
    const v: unknown = JSON.parse(text);
    if (!isObject(v) || v.ok !== true) return null;
    id = v.package_id;
  } catch {
    if (!OK_HEAD.test(text)) return null;
    id = ID_PAIR.exec(text)?.[1];
  }
  const pid = typeof id === "string" ? id.trim().toLowerCase() : "";
  return UUID.test(pid) ? pid : null;
}

/** The card of one call, or null when it draws none (rule 1). */
export function connectCall(e: ConnectEventLike): ConnectCall | null {
  if (e.status !== "done" || !CONNECT_CARD_TOOLS.includes(bareToolName(e.name))) return null;
  const packageId = savedPackageId(e.result);
  return packageId ? { eventId: e.id, packageId } : null;
}

/** The cards of one turn, one for each package, in order. */
export function connectCalls(events: readonly ConnectEventLike[] | undefined): ConnectCall[] {
  const out: ConnectCall[] = [];
  const seen = new Set<string>();
  for (const e of events ?? []) {
    const call = connectCall(e);
    if (!call || seen.has(call.packageId)) continue;
    seen.add(call.packageId);
    out.push(call);
  }
  return out;
}
