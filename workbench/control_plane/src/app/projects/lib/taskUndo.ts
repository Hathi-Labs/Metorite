/**
 * UNDO-1 (2026-11-03) — one helper that makes a task edit undoable.
 *
 * Owner ask: "when I make a mistake in the projects app, like reopening a
 * task, I should be able to undo what I did" (D109.5, DESIGN_SYSTEM M5). This
 * builds NO second undo system. It makes a command for `lib/undo.ts`'s stack,
 * the one `UndoProvider` drives with Ctrl+Z and the toast's Undo button.
 *
 * The inverse is `taskInverse(before, patch)`: the same keys, the values the
 * row held BEFORE (an absent value becomes null). Each inverse is a PATCH that
 * carries If-Match. A 412 means somebody changed the task since. Then Undo
 * says so once and writes nothing, and the step is marked dead so redo cannot
 * replay on top of the change.
 *
 * ⚠️ **The If-Match is the task's version NOW, never the version of the step's
 * own write** (fix round 1, finding 1). Each write moves the stamp, and the
 * server passes a token only when it equals `updated_at` or lies from
 * `fields_at` up (`core.precondition_holds`). So a second undo on one task that
 * sent its own old version always got a 412. `VersionLedger` keeps the newest
 * version that THIS session wrote, per task. Each forward write, undo and redo
 * notes its answer there. A read never does, so an edit by somebody else in
 * between still answers 412.
 *
 * Pure: the network is `EditIo`, the stack and the toast are `EditHooks`, so
 * these rules are tests and not clicks. `useTaskUndo` binds them.
 */

import { type UndoEntry, inversePatch } from "@/lib/undo";

export const STALE_MESSAGE = "It changed since — open it";

/**
 * A server stamp as microseconds, or null. `Date.parse` alone keeps only
 * milliseconds, and the server writes microseconds (and omits a zero part).
 */
function instant(stamp: string): number | null {
  const m = /^(.*T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(.*)$/.exec(stamp);
  if (!m) return null;
  const ms = Date.parse(`${m[1]}${m[3]}`);
  if (Number.isNaN(ms)) return null;
  return ms * 1000 + Number((m[2] ?? "").padEnd(6, "0").slice(0, 6));
}

/** The newest version of each task that this session wrote. */
export interface VersionLedger {
  get(id: string): string | null;
  /** Keep `version` unless the ledger holds a later one. */
  note(id: string, version: string | null | undefined): void;
}

export function createVersionLedger(): VersionLedger {
  const latest = new Map<string, string>();
  return {
    get: (id) => latest.get(id) ?? null,
    note(id, version) {
      if (!version) return;
      const held = latest.get(id);
      // Two writes in flight can answer out of order. The later stamp wins.
      const a = held ? instant(held) : null;
      const b = instant(version);
      if (a !== null && b !== null && a > b) return;
      latest.set(id, version);
    },
  };
}

/**
 * The one ledger of the app. `projectsApi.patchTask` notes each answer here,
 * so every door of Projects and My tasks chains on the same versions.
 */
export const taskVersions: VersionLedger = createVersionLedger();

export interface EditIo {
  patch: (
    id: string,
    payload: Record<string, unknown>,
    ifMatch: string | null,
  ) => Promise<{ updated_at?: string | null } | unknown>;
  setAssignees: (id: string, assignees: string[], ifMatch: string | null) => Promise<unknown>;
  read: (id: string) => Promise<{ assignees?: string[] | null; updated_at?: string | null }>;
  /** Where each write notes the version it got, and each undo reads it. */
  versions: VersionLedger;
}

export interface EditHooks {
  record: (entry: UndoEntry) => void;
  /** One short line to the member. */
  notify: (message: string) => void;
  /** Refresh the surface after a revert or a redo. */
  after: () => Promise<void> | void;
}

const versionOf = (row: unknown): string | null =>
  (row as { updated_at?: string | null } | null)?.updated_at ?? null;

/** A 412 from the gateway: the task changed since the version we hold. */
export function isStale(err: unknown): boolean {
  return (err as { status?: number } | null)?.status === 412;
}

interface Step {
  label: string;
  /** Run the write. Returns false when a refusal was shown and nothing changed. */
  revert: () => Promise<boolean>;
  reapply: () => Promise<boolean>;
}

/**
 * Wrap two writes as an entry, record it, and return it for the toast.
 *
 * The toast's Undo runs the entry through the provider's one-at-a-time gate
 * (`undoApi.undoEntry`), the same gate as Ctrl+Z (fix round 1, finding 6).
 * The entry also guards itself: `applied` changes only after the write
 * answers, so a call that comes while a write is in flight gets that same
 * write and sends no second one.
 */
function register(hooks: EditHooks, step: Step): UndoEntry {
  let applied = true;
  let dead = false;
  let inFlight: Promise<void> | null = null;
  const once = (run: () => Promise<void>) => {
    if (!inFlight) inFlight = run().finally(() => (inFlight = null));
    return inFlight;
  };
  const undo = () =>
    once(async () => {
      if (!applied || dead) return;
      if (!(await step.revert())) {
        dead = true;
        return;
      }
      applied = false;
      await hooks.after();
    });
  const redo = () =>
    once(async () => {
      if (applied || dead) return;
      if (!(await step.reapply())) {
        dead = true;
        return;
      }
      applied = true;
      await hooks.after();
    });
  const entry: UndoEntry = { label: step.label, undo, redo };
  hooks.record(entry);
  return entry;
}

export interface TaskEdit {
  /** The row as it was BEFORE the write. */
  before: object;
  patch: Record<string, unknown>;
  /** `updated_at` the forward write returned. It goes into the ledger. */
  version: string | null;
  label: string;
}

/**
 * The PATCH that puts `before` back after `patch`.
 *
 * ⚠️ `custom_fields` MERGES on the server (`custom_fields.apply_values`), so
 * the whole prior object is not its inverse: a key that the edit filled and
 * the prior object never held would stay. The inverse names each key that
 * the edit sent, with its prior value, or null to clear it (fix round 1,
 * finding 2). Every other key is `inversePatch`.
 */
export function taskInverse(
  before: object,
  patch: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = inversePatch(before, patch);
  const sent = patch.custom_fields;
  if (sent && typeof sent === "object" && !Array.isArray(sent)) {
    const held = ((before as { custom_fields?: Record<string, unknown> | null })
      .custom_fields ?? {}) as Record<string, unknown>;
    out.custom_fields = Object.fromEntries(
      Object.keys(sent).map((key) => [key, held[key] ?? null]),
    );
  }
  // A status change out of a closed lane (Reopen) puts the task back to its
  // lane on Undo. The server stamps "now" on entering a closing lane, so the
  // inverse carries the held done date (UNDO-1 round 2, fix B). The server
  // honours it only when the same write enters a closing lane.
  const held = (before as { completed_at?: string | null }).completed_at;
  if ("status_id" in patch && typeof held === "string" && held) {
    out.completed_at = held;
  }
  return out;
}

/** Record a PATCH that has already landed. Returns its entry, for the toast. */
export function recordTaskEdit(io: EditIo, hooks: EditHooks, edit: TaskEdit): UndoEntry {
  const id = (edit.before as { id: string }).id;
  const inverse = taskInverse(edit.before, edit.patch);
  io.versions.note(id, edit.version);
  const write = async (payload: Record<string, unknown>): Promise<boolean> => {
    try {
      // The task's version NOW: a later step on this task moved it since.
      const got = await io.patch(id, payload, io.versions.get(id));
      io.versions.note(id, versionOf(got));
      return true;
    } catch (err) {
      if (isStale(err)) {
        hooks.notify(STALE_MESSAGE);
        return false;
      }
      throw err;
    }
  };
  return register(hooks, {
    label: edit.label,
    revert: () => write(inverse),
    reapply: () => write(edit.patch),
  });
}

export interface AssigneesEdit {
  id: string;
  before: readonly string[];
  next: readonly string[];
  label: string;
}

/**
 * An assignee set as the server keeps it: each non-blank entry, trimmed, in
 * lower case (`tasks._clean_assignees`). The client's list keeps what the
 * member typed, so a raw compare called "Ana@x.io" a change (finding 7).
 */
const assigneeSet = (list: readonly string[]) =>
  [...new Set(list.map((a) => (a ?? "").trim().toLowerCase()).filter(Boolean))].sort();

const sameList = (a: readonly string[], b: readonly string[]) => {
  const x = assigneeSet(a);
  const y = assigneeSet(b);
  return x.length === y.length && x.every((v, i) => v === y[i]);
};

/**
 * Assignees go through their own PUT, so the guard is a read: put the prior
 * list back only while the list is still the one this edit set. The PUT then
 * sends the read's version as If-Match (`tasks._check_version`), so a field
 * change between the read and the write answers 412 (finding 7).
 *
 * ⚠️ An assignee write moves `updated_at` and not `fields_at`, so the server
 * passes a token across ANOTHER assignee change. The read guard is what sees
 * that one, and only up to the read.
 */
export function recordAssigneesEdit(
  io: EditIo,
  hooks: EditHooks,
  edit: AssigneesEdit,
): UndoEntry {
  const swap = async (from: readonly string[], to: readonly string[]) => {
    const now = await io.read(edit.id);
    if (!sameList(now.assignees ?? [], from)) {
      hooks.notify(STALE_MESSAGE);
      return false;
    }
    try {
      await io.setAssignees(edit.id, [...to], now.updated_at ?? null);
    } catch (err) {
      if (isStale(err)) {
        hooks.notify(STALE_MESSAGE);
        return false;
      }
      throw err;
    }
    return true;
  };
  return register(hooks, {
    label: edit.label,
    revert: () => swap(edit.next, edit.before),
    reapply: () => swap(edit.before, edit.next),
  });
}

export interface MoveBack {
  projectId: string;
  taskIds: string[];
  /** Status in the destination → the status the task held at the source. */
  statusMap: Record<string, string>;
}

/** A moved task as Undo reads it, before the move or after it. */
export interface MovedRow {
  id: string;
  project_id: string;
  root_project_id: string;
  status_id: string;
  type_id?: string | null;
  task_number?: number | null;
  completed_at?: string | null;
}

/** What the server answered to the move, as far as Undo reads it. */
export interface MoveAnswer {
  task_ids?: readonly string[];
  dropped_fields: readonly string[];
  subtasks_moved?: number;
  subtask_ids?: readonly string[];
}

/** Why a move offers no Undo. Each line says what happened and what to do. */
export const MOVE_NO_UNDO = {
  subtasks:
    "Undo is off for this move: the subtasks moved too, and Undo cannot send each one back to its own project. Use Move to project to send them back.",
  drops: (fields: readonly string[]) =>
    `Undo is off for this move: it cleared ${fields.join(", ")}. Use Move to project to send the tasks back, then fill those fields again.`,
  space:
    "Undo is off for this move: the tasks went to another space, and took new numbers there. Use Move to project to send them back.",
  done:
    "Undo is off for this move: it changed when a task was done. Use Move to project to send the tasks back.",
  unread:
    "Undo is off for this move: the page could not read where each task was. Use Move to project to send them back.",
} as const;

export type MoveUndo =
  | { ok: true; calls: MoveBack[] }
  | { ok: false; reason: string };

/**
 * The `moveTasks` calls that send moved tasks home, or why there are none
 * (fix round 1, finding 3).
 *
 * Undo is offered only when going home restores each task EXACTLY. So the
 * move took no subtasks along (a subtask may come from a project other than
 * its parent's), dropped no field value, and kept each task in its space:
 * the same root, the same number, the same type and the same done date.
 *
 * Each task keeps its OWN prior status. One `moveTasks` call holds one
 * status map, so two tasks that share a status now but held two before go
 * home in two calls.
 */
export function moveUndoPlan(
  movedIds: readonly string[],
  before: readonly MovedRow[],
  after: Readonly<Record<string, MovedRow | null | undefined>>,
  answer: MoveAnswer,
): MoveUndo {
  if ((answer.subtask_ids?.length ?? answer.subtasks_moved ?? 0) > 0) {
    return { ok: false, reason: MOVE_NO_UNDO.subtasks };
  }
  if (answer.dropped_fields.length > 0) {
    return { ok: false, reason: MOVE_NO_UNDO.drops(answer.dropped_fields) };
  }
  const ids = answer.task_ids ?? movedIds;
  const byId = new Map(before.map((row) => [row.id, row]));
  const pairs: [MovedRow, MovedRow][] = [];
  for (const id of ids) {
    const was = byId.get(id);
    const now = after[id];
    if (!was || !now) return { ok: false, reason: MOVE_NO_UNDO.unread };
    if (
      now.root_project_id !== was.root_project_id ||
      (now.task_number ?? null) !== (was.task_number ?? null) ||
      (now.type_id ?? null) !== (was.type_id ?? null)
    ) {
      return { ok: false, reason: MOVE_NO_UNDO.space };
    }
    if ((now.completed_at ?? null) !== (was.completed_at ?? null)) {
      return { ok: false, reason: MOVE_NO_UNDO.done };
    }
    if (now.project_id !== was.project_id) pairs.push([was, now]);
  }
  const calls: MoveBack[] = [];
  for (const [was, now] of pairs) {
    // The first call to the same home whose map has room for this status.
    const call = calls.find(
      (c) =>
        c.projectId === was.project_id &&
        (c.statusMap[now.status_id] ?? was.status_id) === was.status_id,
    );
    if (call) {
      call.taskIds.push(was.id);
      call.statusMap[now.status_id] = was.status_id;
    } else {
      calls.push({
        projectId: was.project_id,
        taskIds: [was.id],
        statusMap: { [now.status_id]: was.status_id },
      });
    }
  }
  return { ok: true, calls };
}
