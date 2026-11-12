"use client";

/**
 * One task, read off its id, with what the shared task body needs beside it:
 * its lanes, its project's custom fields and its tags (PK-1, task 2469).
 *
 * The ONE loader of a task panel that starts from an id and no board. My
 * Tasks' detail (`ItemDetail.tsx`, `LensBody`) and the panel on My Day
 * (`lib/shell/MyDayPeekPanel.tsx`) both read through it. Do not copy the
 * fetch into a host. The Projects page keeps its own read, because it holds
 * the board's lanes, fields and tags already.
 *
 * The rules, each one learned the hard way:
 *
 * - **The lanes come off the LENS read first** (`/my/tasks/{id}/lanes`, S6e
 *   repair). The route under `/nodes` is behind a grant that a member reached
 *   by assignment alone may not hold, and a 404 there was a dead Status
 *   select. A task that is not in the member's lens has no lens lanes. Then
 *   the project's own lanes are tried, and then none: a panel with no status
 *   options is degraded but usable, as Projects' `openWithStatuses` says.
 * - **Fields and tags are best effort.** They are behind the project grant
 *   too. A body with no tag suggestions beats no body.
 * - **A merged task opens the task it went into, once** (`followMerge`), as
 *   Projects' `openTaskById` does. My Tasks passes no option: its row is a
 *   live row of the store, and the strip above the body reads that row.
 *
 * Fence: `useTaskById.test.ts`.
 */

import { useCallback, useEffect, useState } from "react";

import { lensMyTaskLanes } from "@/app/tasks/lib/lens";

import { type FieldRow, type StatusRow, type TagRow, type TaskRow, projectsApi } from "./api";

/** The reads the loader makes. A test passes its own. */
export interface TaskByIdIo {
  task: (id: string) => Promise<TaskRow>;
  lensLanes: (id: string) => Promise<StatusRow[]>;
  projectLanes: (projectId: string) => Promise<StatusRow[]>;
  fields: (projectId: string) => Promise<FieldRow[]>;
  tags: (projectId: string) => Promise<TagRow[]>;
}

export const taskByIdIo: TaskByIdIo = {
  task: (id) => projectsApi.task(id),
  // A lane as the lens carries it is the shape `StatusRow` reads.
  lensLanes: async (id) => (await lensMyTaskLanes(id)) as StatusRow[],
  projectLanes: async (projectId) => (await projectsApi.statuses(projectId)).rows,
  fields: async (projectId) => (await projectsApi.fields(projectId)).rows,
  tags: async (projectId) => (await projectsApi.tags(projectId)).rows,
};

/** The first phase: the task and its lanes. The body can draw after it. */
export interface TaskWithLanes {
  task: TaskRow;
  statuses: StatusRow[];
}

/** The project that owns a task's lanes, fields and tags. */
export function rootOf(task: Pick<TaskRow, "root_project_id" | "project_id">): string {
  return task.root_project_id || task.project_id;
}

/**
 * The task and its lanes. The task read and the lens lanes read go out
 * together. A failed task read throws. A failed lanes read never does.
 */
export async function loadTaskWithLanes(
  id: string,
  io: TaskByIdIo = taskByIdIo,
  followMerge = false,
): Promise<TaskWithLanes> {
  // Settled at once, so a refusal here is never an unhandled rejection
  // while the task read is still out.
  let lanes = io.lensLanes(id).then(
    (rows) => rows,
    () => null,
  );
  let task = await io.task(id);
  if (followMerge && task.merged_into_task_id) {
    task = await io.task(task.merged_into_task_id);
    lanes = io.lensLanes(task.id).then(
      (rows) => rows,
      () => null,
    );
  }
  const fromLens = await lanes;
  if (fromLens) return { task, statuses: fromLens };
  const fromProject = await io.projectLanes(rootOf(task)).catch(() => [] as StatusRow[]);
  return { task, statuses: fromProject };
}

/** The second phase: the project's fields and tags, each one best effort. */
export async function loadTaskExtras(
  task: Pick<TaskRow, "root_project_id" | "project_id">,
  io: TaskByIdIo = taskByIdIo,
): Promise<{ fields: FieldRow[]; tags: TagRow[] }> {
  const root = rootOf(task);
  const [fields, tags] = await Promise.all([
    io.fields(root).catch(() => [] as FieldRow[]),
    io.tags(root).catch(() => [] as TagRow[]),
  ]);
  return { fields, tags };
}

export interface TaskById {
  /** Null until the first phase lands, and after a failed task read. */
  task: TaskRow | null;
  statuses: StatusRow[];
  fields: FieldRow[];
  tags: TagRow[];
  /** The message of a failed task read, else null. */
  error: string | null;
  /** Put the row a write answered with in place. */
  setTask: (task: TaskRow) => void;
  /** Read it all again, after a failure. */
  retry: () => void;
}

/** One task off its id. `id` null reads nothing. */
export function useTaskById(id: string | null, options: { followMerge?: boolean } = {}): TaskById {
  const followMerge = options.followMerge ?? false;
  const [task, setTask] = useState<TaskRow | null>(null);
  const [statuses, setStatuses] = useState<StatusRow[]>([]);
  const [fields, setFields] = useState<FieldRow[]>([]);
  const [tags, setTags] = useState<TagRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [round, setRound] = useState(0);

  useEffect(() => {
    if (!id) return;
    let live = true;
    // A new id starts clean: the old task never shows under the new id.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the read writes them too
    setTask(null);
    setError(null);
    setFields([]);
    setTags([]);
    loadTaskWithLanes(id, taskByIdIo, followMerge)
      .then(async (first) => {
        if (!live) return;
        setTask(first.task);
        setStatuses(first.statuses);
        const extras = await loadTaskExtras(first.task);
        if (!live) return;
        setFields(extras.fields);
        setTags(extras.tags);
      })
      .catch((err: Error) => {
        if (live) setError(String(err?.message ?? err));
      });
    return () => {
      live = false;
    };
  }, [id, followMerge, round]);

  const retry = useCallback(() => setRound((n) => n + 1), []);
  return { task, statuses, fields, tags, error, setTask, retry };
}
