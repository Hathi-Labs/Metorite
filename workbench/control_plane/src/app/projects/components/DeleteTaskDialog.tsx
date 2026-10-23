"use client";

/**
 * Projects · delete a task that has subtasks (D-OP-10, OP-7).
 *
 * The plain delete confirm (`ConfirmDialog`) stays for a task with no
 * subtasks. This dialog is for a task that has some. It reads the subtasks the
 * member can see (`GET /projects/tasks/{id}/subtree`) and draws them as an
 * indented tree of check boxes. Every box is clear at first, so the default is
 * the old behaviour: each subtask moves up one level.
 *
 * The rules of the tree are in `lib/deleteSubtasks.ts`, with tests. This file
 * draws them. On a phone the dialog is a bottom sheet, and a whole row is the
 * tap target. Fence: `DeleteTaskDialog.test.ts`.
 */

import { useEffect, useMemo, useState } from "react";

import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import Modal from "@/components/ui/Modal";

import { projectsApi } from "../lib/api";
import {
  type TreeRow,
  buildRows,
  confirmLabel,
  countsOf,
  idsToDelete,
  introLine,
  movesUp,
  setAll,
  summaryLine,
  toggleRow,
} from "../lib/deleteSubtasks";

interface Props {
  taskId: string;
  title: string | null;
  isMobile: boolean;
  /** Called with the ids of the subtasks that go with the task. */
  onConfirm: (deleteSubtaskIds: string[]) => void;
  onCancel: () => void;
}

export default function DeleteTaskDialog({ taskId, title, isMobile, onConfirm, onCancel }: Props) {
  const [rows, setRows] = useState<TreeRow[] | null>(null);
  const [hidden, setHidden] = useState(0);
  const [failed, setFailed] = useState(false);
  const [ticked, setTicked] = useState<Set<string>>(new Set());

  useEffect(() => {
    let live = true;
    projectsApi
      .taskSubtree(taskId)
      .then((res) => {
        if (!live) return;
        setRows(buildRows(res.subtasks));
        setHidden(res.hidden);
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, [taskId]);

  const list = useMemo(() => rows ?? [], [rows]);
  const counts = countsOf(ticked, list);

  return (
    <Modal
      open
      onClose={onCancel}
      title="Delete this task?"
      icon="Trash2"
      size="md"
      layer="alert"
      placement={isMobile ? "sheet" : "center"}
    >
      <div className="space-y-2 px-3 py-3">
        {title ? (
          <p className="truncate text-xs text-muted-foreground">&ldquo;{title}&rdquo;</p>
        ) : null}
        <p className="text-xs text-foreground">It is deleted for good. This cannot be undone.</p>
        {rows === null && !failed ? (
          <p className="text-xs text-muted-foreground">Loading the subtasks…</p>
        ) : null}
        {failed ? (
          <p className="text-xs text-muted-foreground">
            The subtasks could not be read. If you delete now, each one moves up a level.
          </p>
        ) : null}
        {rows && rows.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            None of its subtasks is deleted. They move up a level.
          </p>
        ) : null}
        {rows && rows.length > 0 ? (
          <>
            <p className="text-xs text-foreground">{introLine(rows.length)}</p>
            <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm" onClick={() => setTicked(setAll(list, false))}>
                Keep all
              </Button>
              <Button variant="secondary" size="sm" onClick={() => setTicked(setAll(list, true))}>
                Delete all
              </Button>
            </div>
            <ul
              className="max-h-[50vh] overflow-y-auto rounded-md border border-border"
              aria-label="Subtasks"
            >
              {rows.map((row) => (
                <li key={row.id}>
                  <label
                    className="flex min-h-9 cursor-pointer items-center gap-2 py-1 pr-2 hover:bg-secondary/50"
                    style={{ paddingLeft: `${0.5 + (row.depth - 1) * 1.25}rem` }}
                  >
                    <Checkbox
                      checked={ticked.has(row.id)}
                      onChange={() => setTicked((cur) => toggleRow(cur, list, row.id))}
                      aria-label={`Delete ${row.title}`}
                    />
                    <span className="min-w-0 flex-1 truncate text-xs text-foreground">
                      {row.title}
                    </span>
                    {row.archived ? (
                      <span className="shrink-0 text-[11px] text-muted-foreground">archived</span>
                    ) : null}
                    {row.status ? (
                      <span className="shrink-0 text-[11px] text-muted-foreground">
                        {row.status}
                      </span>
                    ) : null}
                    {movesUp(ticked, list, row.id) ? (
                      <span className="shrink-0 text-[11px] text-muted-foreground">
                        → moves up
                      </span>
                    ) : row.descendants.length > 0 ? (
                      <span className="shrink-0 text-[11px] text-muted-foreground">
                        ▸ {row.descendants.length} subtask{row.descendants.length === 1 ? "" : "s"}
                      </span>
                    ) : null}
                  </label>
                </li>
              ))}
            </ul>
            <p className="text-xs font-medium text-foreground">{summaryLine(counts)}</p>
          </>
        ) : null}
        {hidden > 0 ? (
          <p className="text-[11px] text-muted-foreground">
            {hidden} subtask{hidden === 1 ? "" : "s"} you cannot see {hidden === 1 ? "stays" : "stay"}, and{" "}
            {hidden === 1 ? "moves" : "move"} up.
          </p>
        ) : null}
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-border px-3 py-2">
        <Button variant="secondary" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="destructive"
          size="sm"
          icon="Trash2"
          disabled={rows === null && !failed}
          onClick={() => onConfirm(idsToDelete(ticked, list))}
        >
          {confirmLabel(counts)}
        </Button>
      </div>
    </Modal>
  );
}
