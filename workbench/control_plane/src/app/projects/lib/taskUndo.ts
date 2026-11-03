/**
 * UNDO-1 (2026-11-03) — one helper that makes a task edit undoable.
 *
 * Owner ask: "when I make a mistake in the projects app, like reopening a
 * task, I should be able to undo what I did" (D109.5, DESIGN_SYSTEM M5). This
 * builds NO second undo system. It makes a command for `lib/undo.ts`'s stack,
 * the one `UndoProvider` drives with Ctrl+Z and the toast's Undo button.
 *
 * The inverse is `inversePatch(before, patch)`: the same keys, the values the
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
  setAssignees: (id: string, assignees: string[]) => Promise<unknown>;
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
 * Wrap two writes as an entry and return the undo for the toast button.
 * One `applied` flag serves the toast and Ctrl+Z, so pressing both reverts once.
 */
function register(hooks: EditHooks, step: Step): () => Promise<void> {
  let applied = true;
  let dead = false;
  const undo = async () => {
    if (!applied || dead) return;
    if (!(await step.revert())) {
      dead = true;
      return;
    }
    applied = false;
    await hooks.after();
  };
  const redo = async () => {
    if (applied || dead) return;
    if (!(await step.reapply())) {
      dead = true;
      return;
    }
    applied = true;
    await hooks.after();
  };
  hooks.record({ label: step.label, undo, redo });
  return undo;
}

export interface TaskEdit {
  /** The row as it was BEFORE the write. */
  before: object;
  patch: Record<string, unknown>;
  /** `updated_at` the forward write returned. It goes into the ledger. */
  version: string | null;
  label: string;
}

/** Record a PATCH that has already landed. Returns the undo for the toast. */
export function recordTaskEdit(io: EditIo, hooks: EditHooks, edit: TaskEdit): () => Promise<void> {
  const id = (edit.before as { id: string }).id;
  const inverse = inversePatch(edit.before, edit.patch);
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

const sameList = (a: readonly string[], b: readonly string[]) => {
  const x = [...a].sort();
  const y = [...b].sort();
  return x.length === y.length && x.every((v, i) => v === y[i]);
};

/**
 * Assignees go through a PUT that takes no If-Match, so the guard is a read:
 * put the prior list back only while the list is still the one this edit set.
 */
export function recordAssigneesEdit(
  io: EditIo,
  hooks: EditHooks,
  edit: AssigneesEdit,
): () => Promise<void> {
  const swap = async (from: readonly string[], to: readonly string[]) => {
    const now = await io.read(edit.id);
    if (!sameList(now.assignees ?? [], from)) {
      hooks.notify(STALE_MESSAGE);
      return false;
    }
    await io.setAssignees(edit.id, [...to]);
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

/**
 * The `moveTasks` calls that send moved tasks home: one per source project.
 * `nowStatus` is each task's status after the move, read from the server.
 */
export function moveBackPlan(
  before: readonly { id: string; project_id: string; status_id: string }[],
  nowStatus: Readonly<Record<string, string>>,
): MoveBack[] {
  const byProject = new Map<string, MoveBack>();
  for (const row of before) {
    const group = byProject.get(row.project_id) ?? {
      projectId: row.project_id,
      taskIds: [],
      statusMap: {},
    };
    group.taskIds.push(row.id);
    const now = nowStatus[row.id];
    if (now) group.statusMap[now] = row.status_id;
    byProject.set(row.project_id, group);
  }
  return [...byProject.values()];
}
