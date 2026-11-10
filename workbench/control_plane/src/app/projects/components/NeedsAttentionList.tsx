"use client";

/**
 * Projects · Needs attention (WS-55 SW-11a).
 *
 * Spec: `project-docs/specs/external_agent_access.md` §12.7.
 *
 * Reading this as: a lens of the project board, not a canvas. It takes the
 * place of the board while it is on, under the same project header, with the
 * rows of the server in three sections. It matches the "Waiting on people"
 * panel of Reports in its row shape: the task as a link, then why and how
 * long. Its neighbour is the board it replaces.
 *
 * ⚠️ **Draws the server's rows and decides nothing.** The rows, the counts and
 * `can_act` are `needs_attention.py`'s. `lib/needsAttention.ts` holds the
 * words. A row whose run act the caller may not take shows no button, and it
 * says who can act.
 *
 * ⚠️ **Two writes, both on paths that exist.** Resume is
 * `POST /projects/agent-runs/{id}/resume`. Close the run is the release of
 * EA-14a, and it asks first. Neither changes the task. Each one reads the list
 * again through `onChanged`.
 */
import { displayProjectName, displayRowProject } from "../lib/personalTree";
import { useEffect, useReducer, useState } from "react";

import { ControlLink } from "@/components/ControlLink";
import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Skeleton } from "@/components/ui/Skeleton";
import { accentForHue } from "@/lib/statusAccent";

import { writeFailure } from "../lib/agentRuns";
import { ProjectsApiError, agentRunsApi } from "../lib/api";
import {
  ACTION_HINT,
  ACTION_ICON,
  ACTION_LABEL,
  type AttentionAction,
  type AttentionPage,
  type AttentionRow,
  CANNOT_ACT,
  CONNECTION_GONE,
  EMPTY_COPY,
  LENS_LABEL,
  attentionGroups,
  cappedLine,
  closeRunCopy,
  ownerText,
  rowActions,
  rowDetail,
  rowKey,
  rowTitle,
  runActBlocked,
} from "../lib/needsAttention";
import { NO_ROWS, errorFor, isPending, rowsReducer } from "../lib/rowState";

/** The deep link of a task: the shape the board and the bell read. */
function taskHref(taskId: string): string {
  return `/projects?task=${encodeURIComponent(taskId)}`;
}

export interface NeedsAttentionListProps {
  /** The server's answer, or null while the first read is in flight. */
  page: AttentionPage | null;
  /** A failed read, in words. The last page stays on screen beside it. */
  error: string | null;
  personLabels?: ReadonlyMap<string, string>;
  /** Open a task in the panel, as a card click does. */
  onOpenTask: (taskId: string) => void;
  /** Leave the lens for the board. */
  onBack: () => void;
  /** Read the list again, after an act. */
  onChanged: () => void;
}

export function NeedsAttentionList({
  page,
  error,
  personLabels,
  onOpenTask,
  onBack,
  onChanged,
}: NeedsAttentionListProps) {
  const [rows, dispatch] = useReducer(rowsReducer, NO_ROWS);
  const [closing, setClosing] = useState<AttentionRow | null>(null);
  const groups = page ? attentionGroups(page) : [];
  const capped = page ? cappedLine(page) : null;

  // A row that left the list takes its spinner and its message with it.
  const keys = page ? page.rows.map(rowKey).join("|") : "";
  useEffect(() => {
    dispatch({ kind: "prune", ids: keys ? keys.split("|") : [] });
  }, [keys]);

  async function act(row: AttentionRow, action: AttentionAction) {
    if (action === "open_blocker") {
      if (row.blocker_id) onOpenTask(row.blocker_id);
      return;
    }
    if (!row.run_id) return;
    const id = rowKey(row);
    dispatch({ kind: "start", id });
    try {
      if (action === "resume") await agentRunsApi.resume(row.run_id);
      else await agentRunsApi.release(row.run_id);
      dispatch({ kind: "settled", id });
      setClosing(null);
      onChanged();
    } catch (err) {
      const status = err instanceof ProjectsApiError ? err.status : undefined;
      const message = err instanceof Error ? err.message : undefined;
      const failure =
        action === "resume" ? "Could not resume the run." : "Could not close the run.";
      const said = writeFailure(status, message, failure);
      dispatch({ kind: "failed", id, message: said.text });
      setClosing(null);
      if (said.reload) onChanged();
    }
  }

  return (
    <section aria-label={LENS_LABEL} className="flex min-h-0 flex-1 flex-col" data-needs-attention="">
      <div className="flex min-w-0 flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <Icon name="TriangleAlert" className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <h3 className="text-sm font-medium text-foreground">{LENS_LABEL}</h3>
        {page ? (
          <span className="text-xs tabular-nums text-muted-foreground">{page.total}</span>
        ) : null}
        <Button
          className="ml-auto"
          variant="ghost"
          size="sm"
          icon="ArrowLeft"
          onClick={onBack}
        >
          Back to the board
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-3 py-3">
        {error ? (
          <p role="alert" className="mb-2 text-xs text-destructive">
            {error}
          </p>
        ) : null}

        {page === null && !error ? (
          <div className="space-y-2" aria-label="Loading the list">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : null}

        {page && groups.length === 0 ? (
          <EmptyState icon="CircleCheck" message={EMPTY_COPY.message} hint={EMPTY_COPY.hint} />
        ) : null}

        {groups.map((group) => (
          <div key={group.kind} className="mb-5" data-attention-group={group.kind}>
            <p className="flex items-center gap-1.5 text-xs text-foreground">
              <span
                className={`h-2 w-2 shrink-0 rounded-full ${accentForHue(group.hue).dot}`}
                aria-hidden
              />
              <span className="font-medium">{group.heading}</span>
              <span className="tabular-nums text-muted-foreground">{group.count}</span>
            </p>
            <p className="mb-2 mt-0.5 text-[11px] text-muted-foreground">{group.hint}</p>
            <ul className="divide-y divide-border rounded-md border border-border bg-card">
              {group.rows.map((row) => {
                const id = rowKey(row);
                const busy = isPending(rows, id);
                const failed = errorFor(rows, id);
                const acts = rowActions(row);
                return (
                  <li
                    key={id}
                    className="flex flex-wrap items-start gap-x-3 gap-y-1.5 px-3 py-2"
                    data-attention-row={row.kind}
                  >
                    {/* `basis-56`: on a phone the acts wrap under the
                        words, so the title of the task never clips to a few
                        letters (visual review, 390 px). */}
                    <div className="min-w-0 flex-1 basis-56">
                      <ControlLink
                        href={taskHref(row.task_id)}
                        onActivate={() => onOpenTask(row.task_id)}
                        className="block truncate text-xs font-medium text-foreground hover:underline"
                        title={`Open ${rowTitle(row)}`}
                      >
                        {rowTitle(row)}
                      </ControlLink>
                      <p className="text-[11px] text-muted-foreground">{rowDetail(row)}</p>
                      <p className="text-[10px] text-muted-foreground">
                        Owner: {ownerText(row, personLabels)}
                        {row.project_name ? ` · ${displayRowProject(row)}` : ""}
                      </p>
                      {row.kind === "stalled" && row.connection_gone ? (
                        <p className="text-[10px] text-muted-foreground">{CONNECTION_GONE}</p>
                      ) : null}
                      {runActBlocked(row) ? (
                        <p className="text-[10px] text-muted-foreground">{CANNOT_ACT}</p>
                      ) : null}
                      {failed ? (
                        <p role="alert" className="mt-0.5 text-[11px] text-destructive">
                          {failed}
                        </p>
                      ) : null}
                    </div>
                    {acts.length ? (
                      <div className="flex shrink-0 flex-wrap items-center gap-1">
                        {acts.map((action, index) =>
                          action === "open_blocker" && row.blocker_id ? (
                            <ControlLink
                              key={action}
                              href={taskHref(row.blocker_id)}
                              onActivate={() => void act(row, action)}
                              className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-xs text-primary hover:underline"
                              title={ACTION_HINT[action]}
                            >
                              <Icon name={ACTION_ICON[action]} className="h-3.5 w-3.5" aria-hidden />
                              {ACTION_LABEL[action]}
                            </ControlLink>
                          ) : (
                            <Button
                              key={action}
                              variant={index === 0 ? "secondary" : "ghost"}
                              size="sm"
                              icon={ACTION_ICON[action]}
                              loading={busy}
                              disabled={busy}
                              data-attention-action={action}
                              title={ACTION_HINT[action]}
                              onClick={() =>
                                action === "close_run" ? setClosing(row) : void act(row, action)
                              }
                            >
                              {ACTION_LABEL[action]}
                            </Button>
                          ),
                        )}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}

        {capped ? <p className="text-[11px] text-muted-foreground">{capped}</p> : null}
      </div>

      <ConfirmDialog
        open={closing !== null}
        title={closing ? closeRunCopy(closing).title : ""}
        body={closing ? closeRunCopy(closing).body : ""}
        confirmLabel={closing ? closeRunCopy(closing).confirm : ACTION_LABEL.close_run}
        icon="LogOut"
        busy={closing ? isPending(rows, rowKey(closing)) : false}
        onConfirm={() => {
          if (closing) void act(closing, "close_run");
        }}
        onCancel={() => setClosing(null)}
      />
    </section>
  );
}

export default NeedsAttentionList;
