/**
 * WT-6a (one_assistant_context.md §9.4) — the Undo of an archive that a chat
 * card approved.
 *
 * In a held chat, `archive_task` and `archive_project` run behind their card
 * only while `HELD_CLASS_C_ORGS` holds the org. After the write, the tool
 * sends one `confirmation_undo` event (`ask_tools.publish_card_undo`). It
 * names the card's `request_id`, the row and the row's version after the
 * archive. The receipt line of that card then offers Undo.
 *
 * Undo is a command for `lib/undo.ts`, in the style of `taskUndo.ts`. The
 * BROWSER calls the existing unarchive route under the member's own session
 * and RLS, with the version as `If-Match`. A 412 means somebody changed the
 * row since. Then Undo says so once, restores nothing, and the entry is dead.
 * The event carries no token, and the model names no part of it.
 *
 * Pure: the network is `ArchiveIo`, so these rules are tests and not clicks.
 * Fence: `src/lib/archiveUndo.test.ts`.
 */

import { STALE_MESSAGE, isStale } from "@/app/projects/lib/taskUndo";
import type { UndoEntry } from "@/lib/undo";

export { STALE_MESSAGE };

/** The event name. The server owns it (`CONFIRMATION_UNDO` in `ask_tools.py`). */
export const CONFIRMATION_UNDO = "confirmation_undo";

export type ArchiveKind = "task" | "project";

/** What an archive receipt can restore. */
export interface ArchiveUndo {
  kind: ArchiveKind;
  id: string;
  /** The row's `updated_at` after the archive: the `If-Match` of the Undo. */
  version: string;
}

/** Where a receipt's Undo is. "ready" and "failed" offer the button. */
export type ArchiveUndoState = "ready" | "busy" | "done" | "stale" | "failed" | "refused";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const STAMP = /^\d{4}-\d{2}-\d{2}T[0-9:.]+(Z|[+-]\d{2}:?\d{2})$/;

/**
 * The event's request id and its row, or null for a value the receipt cannot
 * use. The id must be a UUID, because it goes into a path. The version must
 * carry a UTC offset, because the gateway refuses any other `If-Match`.
 */
export function archiveUndoFromEvent(
  value: unknown,
): { requestId: string; undo: ArchiveUndo } | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  const requestId = typeof v.request_id === "string" ? v.request_id : "";
  const kind = v.kind;
  const id = typeof v.id === "string" ? v.id.toLowerCase() : "";
  const version = typeof v.version === "string" ? v.version : "";
  if (!requestId || (kind !== "task" && kind !== "project")) return null;
  if (!UUID.test(id) || !STAMP.test(version)) return null;
  return { requestId, undo: { kind, id, version } };
}

export interface ArchiveIo {
  /** POST the unarchive route of the row, with `If-Match`. Throws `{status}`. */
  unarchive: (kind: ArchiveKind, id: string, ifMatch: string) => Promise<unknown>;
}

/** The status of a failed call, or null for a network fault. */
const statusOf = (err: unknown): number | null => {
  const s = (err as { status?: unknown } | null)?.status;
  return typeof s === "number" ? s : null;
};

/**
 * Run the unarchive once. "done" restored the row. "stale" is a 412: the row
 * changed since, and nothing was restored. "refused" is any other 4xx (the
 * row is gone, or the member cannot see it now), and Undo stops. "failed" is
 * a network fault or a 5xx, and the member may try again.
 */
export async function runArchiveUndo(io: ArchiveIo, undo: ArchiveUndo): Promise<ArchiveUndoState> {
  try {
    await io.unarchive(undo.kind, undo.id, undo.version);
    return "done";
  } catch (err) {
    if (isStale(err)) return "stale";
    const status = statusOf(err);
    if (status !== null && status >= 400 && status < 500) return "refused";
    return "failed";
  }
}

/**
 * The command of one receipt, for `lib/undo.ts`. `undo` runs the unarchive,
 * once at a time: a second call while one runs gets the same promise. After
 * "done", "stale" or "refused" the entry is spent, and a later call does
 * nothing. `redo` does nothing: an archive again needs a new card.
 */
export function archiveUndoEntry(
  io: ArchiveIo,
  undo: ArchiveUndo,
  label: string,
  onState: (state: ArchiveUndoState) => void,
): UndoEntry {
  let spent = false;
  let inFlight: Promise<void> | null = null;
  const run = async () => {
    if (spent) return;
    onState("busy");
    const state = await runArchiveUndo(io, undo);
    if (state !== "failed") spent = true;
    onState(state);
  };
  return {
    label,
    undo: () => {
      if (!inFlight) inFlight = run().finally(() => (inFlight = null));
      return inFlight;
    },
    redo: () => undefined,
  };
}

/** The unarchive route of each kind, under `/api/projects` (the session proxy). */
export function unarchivePath(kind: ArchiveKind, id: string): string {
  return kind === "task"
    ? `/api/projects/tasks/${id}/unarchive`
    : `/api/projects/nodes/${id}/unarchive`;
}

/**
 * The browser's io: the same-origin proxy, which sends the member's own
 * session to the gateway and passes `If-Match` on. No token of the server.
 */
export function browserArchiveIo(fetchFn: typeof fetch = fetch): ArchiveIo {
  return {
    async unarchive(kind, id, ifMatch) {
      const res = await fetchFn(unarchivePath(kind, id), {
        method: "POST",
        credentials: "same-origin",
        headers: { "If-Match": ifMatch },
      });
      if (!res.ok) throw Object.assign(new Error(`unarchive ${res.status}`), { status: res.status });
      return res.json().catch(() => null);
    },
  };
}

/** The words of an archive receipt after its Undo, or null to keep the line. */
export function archiveUndoText(state: ArchiveUndoState | undefined, label: string): string | null {
  switch (state) {
    case "busy":
      return `Restoring… ${label}`;
    case "done":
      return `Restored · ${label}`;
    case "stale":
      return `Approved · ${label}. ${STALE_MESSAGE}`;
    case "refused":
      return `Approved · ${label}. Undo could not restore it.`;
    case "failed":
      return `Approved · ${label}. Undo did not go through. Try again.`;
    default:
      return null;
  }
}

/** True when the line offers the Undo button. */
export function archiveUndoOffered(state: ArchiveUndoState | undefined): boolean {
  return state === "ready" || state === "failed";
}
