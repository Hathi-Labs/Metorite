"use client";

/**
 * Projects · the Archive view of My tasks (ARC-1, `one_projects_app.md` §9b).
 *
 * Reading this as: a chip of My tasks, drawn as a list. A member opens it to
 * find a task they filed and to put it back. It lists the member's archived
 * tasks across projects, newest filed first. One row can be restored from its
 * own button. A tick on many rows turns the bar above into "Restore N".
 *
 * ⚠️ **It decides nothing.** The rows are the server's `scope=mine` read with
 * `archived_only` (`myArchive.archiveQuery`). A row whose project is archived
 * keeps Restore off and says why, because a restored task would stay out of
 * every list until its project is restored.
 *
 * ⚠️ **Restore uses the two routes that exist.** One task: `POST
 * /projects/tasks/{id}/unarchive`. Many: `POST /projects/tasks/bulk` with
 * `action: "unarchive"`. Both give a toast with Undo, which files the same
 * tasks again.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import { SkeletonRows } from "@/components/ui/Skeleton";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import { useToast } from "@/components/ui/Toast";
import { useUndo } from "@/components/UndoProvider";
import { clickSelect } from "@/lib/selection";
import type { UndoEntry } from "@/lib/undo";

import { type TaskRow, projectsApi } from "../lib/api";
import { shortDate } from "../lib/outlook";
import {
  ARCHIVED_PROJECT_REASON,
  ARCHIVE_EMPTY_HINT,
  ARCHIVE_EMPTY_TEXT,
  ARCHIVE_READ_FAILED,
  type ArchiveRow,
  archiveQuery,
  archiveRows,
  pruneSelection,
  restoreBody,
  restorableIds,
  restorePlan,
  restoreReceipt,
  restoreShortfall,
  restoredIds,
} from "../lib/myArchive";
import { MY_TASKS_MAX_PAGES } from "../lib/myTasks";
import { appendTasks } from "../lib/paging";
import ProjectPathChip from "./ProjectPathChip";

interface Props {
  /** The path of a project, for the chip on each row. */
  projectPath: (projectId: string) => readonly string[];
  /** True when the project, or one above it, is archived. */
  projectArchived: (projectId: string) => boolean;
  /** Opens the ONE Projects task panel. */
  onOpenTask: (task: TaskRow) => void;
  /** Bumped by the page when the panel changed a task, so the rows read again. */
  version: number;
  /** After a restore: the page counts, the list and the sidebar read again. */
  onRestored: () => void;
}

async function readArchive(): Promise<TaskRow[]> {
  let rows: TaskRow[] = [];
  for (let page = 1; page <= MY_TASKS_MAX_PAGES; page++) {
    const res = await projectsApi.tasks(archiveQuery(page));
    rows = appendTasks(rows, res.rows ?? []);
    const total = res.total ?? rows.length;
    if (rows.length >= total || (res.rows ?? []).length === 0) break;
  }
  return rows;
}

/** "8 Nov 2026": the one short date of the Projects app (`lib/outlook.ts`),
 *  so this list and the archived-project bar (ARC-2a) print a date alike. */
function filedOn(task: TaskRow): string {
  return task.archived_at ? shortDate(task.archived_at).replace("—", "") : "";
}

export function MyArchiveView({ projectPath, projectArchived, onOpenTask, version, onRestored }: Props) {
  const toast = useToast();
  const undoApi = useUndo();
  const [tasks, setTasks] = useState<TaskRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [anchor, setAnchor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const live = useRef(true);

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  useEffect(() => {
    let current = true;
    readArchive().then(
      (rows) => {
        if (!current) return;
        setTasks(rows);
        setError(null);
      },
      (err: unknown) => {
        if (current) setError(String((err as Error).message || ARCHIVE_READ_FAILED));
      },
    );
    return () => {
      current = false;
    };
  }, [reload, version]);

  const rows = useMemo(
    () => archiveRows(tasks ?? [], projectPath, projectArchived),
    [tasks, projectPath, projectArchived],
  );
  const order = useMemo(() => rows.map((r) => r.task.id), [rows]);
  const restorable = useMemo(() => restorableIds(rows), [rows]);

  // A reload may remove rows. A tick on a row that left goes with it.
  useEffect(() => {
    setSelected((cur) => pruneSelection(cur, rows));
  }, [rows]);

  const again = useCallback(() => {
    setReload((n) => n + 1);
    onRestored();
  }, [onRestored]);

  async function file(ids: string[]) {
    for (const id of ids) await projectsApi.archiveTask(id);
  }

  async function restore(ids: string[]) {
    const plan = restorePlan(ids);
    if (!plan) return;
    setBusy(true);
    setNotice(null);
    try {
      let done: string[];
      if (plan.kind === "one") {
        await projectsApi.unarchiveTask(plan.id);
        done = [plan.id];
      } else {
        const reply = await projectsApi.bulkEdit(restoreBody(plan.ids));
        done = restoredIds(reply);
      }
      setSelected(new Set());
      const short = restoreShortfall(plan.kind === "one" ? 1 : plan.ids.length, done.length);
      if (short) setNotice(short);
      if (done.length > 0) {
        let back = true;
        const entry: UndoEntry = {
          label: `restored ${done.length === 1 ? "a task" : `${done.length} tasks`} from the archive`,
          undo: async () => {
            if (!back) return;
            back = false;
            await file(done);
            again();
          },
          redo: async () => {
            if (back) return;
            back = true;
            if (done.length === 1) await projectsApi.unarchiveTask(done[0]);
            else await projectsApi.bulkEdit(restoreBody(done));
            again();
          },
        };
        undoApi.record(entry);
        toast.show({
          key: "projects:archive:restore",
          variant: "success",
          title: restoreReceipt(done.length),
          action: {
            label: "Undo",
            onClick: () =>
              void undoApi
                .undoEntry(entry)
                .catch((err: Error) => toast.show({ variant: "error", title: String(err.message) })),
          },
        });
      }
      again();
    } catch (err) {
      setNotice(`The restore did not go through. ${String((err as Error).message)} Try again.`);
    } finally {
      if (live.current) setBusy(false);
    }
  }

  function pick(row: ArchiveRow, shift: boolean) {
    if (!row.canRestore) return;
    const next = clickSelect({ selected, anchor }, restorable, row.task.id, shift);
    setSelected(next.selected);
    setAnchor(next.anchor);
  }

  if (error && !tasks) {
    return (
      <div data-testid="my-archive" data-state="error">
        <EmptyState
          icon="TriangleAlert"
          message={ARCHIVE_READ_FAILED}
          hint="Your archived tasks are safe. Try the read again."
          action={{ label: "Retry", icon: "RotateCcw", onClick: () => setReload((n) => n + 1) }}
        />
      </div>
    );
  }
  if (!tasks) return <SkeletonRows count={6} />;
  if (rows.length === 0) {
    return (
      <div data-testid="my-archive" data-state="empty">
        <EmptyState icon="Archive" message={ARCHIVE_EMPTY_TEXT} hint={ARCHIVE_EMPTY_HINT} />
      </div>
    );
  }

  const count = selected.size;
  const everyone = restorable.length > 0 && count === restorable.length;

  return (
    <div className="flex min-h-0 flex-col" data-testid="my-archive" data-state="rows">
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted px-3 py-2" data-my-archive-bar="">
        <Checkbox
          checked={everyone}
          indeterminate={count > 0 && !everyone}
          disabled={restorable.length === 0 || busy}
          onChange={() => setSelected(everyone ? new Set() : new Set(restorable))}
          aria-label="Select every archived task that can be restored"
        />
        {count > 0 ? (
          <>
            <Badge tone="primary">{count} selected</Badge>
            <Button
              variant="secondary"
              size="sm"
              icon="Undo2"
              disabled={busy}
              data-archive-restore-many=""
              onClick={() => void restore([...selected])}
            >
              Restore {count === 1 ? "task" : `${count} tasks`}
            </Button>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => setSelected(new Set())}>
              Clear
            </Button>
          </>
        ) : (
          <span className="text-xs text-muted-foreground">
            {rows.length === 1 ? "1 archived task" : `${rows.length} archived tasks`}
          </span>
        )}
      </div>
      {notice ? (
        <div
          role="alert"
          className="border-b border-border bg-destructive/10 px-3 py-2 text-xs text-destructive"
          data-my-archive-notice=""
        >
          {notice}
        </div>
      ) : null}
      <ul className="min-h-0 flex-1 overflow-auto" aria-label="Archived tasks">
        {rows.map((row) => (
          <li
            key={row.task.id}
            className="flex min-h-11 flex-wrap items-center gap-x-2 gap-y-1 border-b border-border px-3 py-2 hover:bg-secondary/50"
            data-archive-row={row.task.id}
            data-project-archived={row.projectArchived ? "" : undefined}
          >
            {/* A 44 px target around the tick, as MOB-7 asks of each control. */}
            <span className="-ml-2 flex h-11 w-11 shrink-0 items-center justify-center">
              <Checkbox
                checked={selected.has(row.task.id)}
                disabled={!row.canRestore || busy}
                onChange={(event) => pick(row, Boolean((event.nativeEvent as MouseEvent).shiftKey))}
                aria-label={`Select ${row.task.title}`}
                title={row.canRestore ? undefined : ARCHIVED_PROJECT_REASON}
              />
            </span>
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <button
                type="button"
                className="min-w-0 truncate text-left text-sm text-foreground hover:underline"
                onClick={() => onOpenTask(row.task)}
              >
                {row.task.title}
              </button>
              <div className="flex min-w-0 flex-wrap items-center gap-x-2 text-[11px] text-muted-foreground">
                <ProjectPathChip path={row.path} />
                {filedOn(row.task) ? <span>Archived {filedOn(row.task)}</span> : null}
                {row.projectArchived ? (
                  <span data-archive-project-note="">{ARCHIVED_PROJECT_REASON}</span>
                ) : null}
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              icon="Undo2"
              disabled={!row.canRestore || busy}
              title={row.canRestore ? "Put this task back on its board" : ARCHIVED_PROJECT_REASON}
              data-archive-restore=""
              onClick={() => void restore([row.task.id])}
            >
              Restore
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default MyArchiveView;
