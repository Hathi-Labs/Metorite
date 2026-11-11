"use client";

/**
 * /approvals — Action Broker approval inbox (BO-1 / A2).
 *
 * The human-review queue for outward writes (email / CRM) an agent
 * proposed while ACTION_BROKER_ENFORCE is on. Approving runs the broker's
 * registered handler — it executes the real write. Rejecting refuses it; it is
 * never performed. Data:
 *   list     GET  /api/actions/pending
 *   approve  POST /api/actions/pending/{id}/approve
 *   reject   POST /api/actions/pending/{id}/reject
 *
 * When enforcement is off (the default) this queue is empty — every outward
 * write auto-applies through the broker (chokepointed + audited, no hold).
 *
 * `/approvals?action=<id>` (WS-58 MD-3a, `my_day_feed.md` §5.8 item 9) is the
 * My Day card's link to one proposal. The page scrolls to that row and marks
 * it. When the row is not in the queue, a line above the queue says so.
 * `lib/approvalLink.ts` reads the link. A bare `/approvals` opens as before.
 */

import { AppTopBar } from "@/components/AppTopBar";
import Button from "@/components/ui/Button";
import ElicitationCard from "@/components/ElicitationCard";
import { agentRunsApi } from "@/app/projects/lib/api";
import { answerPayload, cardQuestion } from "@/app/projects/lib/agentRuns";
import { relativeTime } from "@/lib/taskCard";
import type { ElicitationAnswers } from "@/lib/elicitationAnswer";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

import type { PendingAction } from "@/app/api/actions/pending/route";

import {
  actionIdFromSearch,
  afterLoad,
  approvalRowDomId,
  markedRow,
  type LoadResult,
  type QueueState,
} from "./lib/approvalLink";
import {
  answersInPlace,
  openQuestion,
  rowAgent,
  rowTitle,
  taskHref,
  waitingCount,
  waitingRuns,
  type WaitingRun,
} from "./lib/waitingForYou";

/**
 * The reader of `?action=<id>`. `useSearchParams` needs a Suspense boundary,
 * and this one holds only the reader, so the page still renders on the
 * server. The email app's `EmailDeepLink.tsx` is the same shape.
 */
function ActionParam({ onAction }: { onAction: (id: string | null) => void }) {
  const id = actionIdFromSearch(useSearchParams());
  useEffect(() => {
    onAction(id);
  }, [id, onAction]);
  return null;
}

export default function ApprovalsPage() {
  // The queue and how its last load ended. A failed load keeps the rows the
  // member sees (`afterLoad`), and never reads as an empty queue.
  const [queue, setQueue] = useState<QueueState<PendingAction>>({ rows: [], load: "pending" });
  const rows = queue.rows;
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<Record<string, "approve" | "reject">>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  // CIV-14k. The open agent questions of the member, oldest first. `null`
  // means the agent-run routes are off, and then no list draws.
  const [waiting, setWaiting] = useState<WaitingRun[] | null>(null);
  const [waitingBusy, setWaitingBusy] = useState<Record<string, boolean>>({});
  const [waitingErrors, setWaitingErrors] = useState<Record<string, string>>({});
  // The proposal a link names (MD-3a), and the row it marks.
  const [actionId, setActionId] = useState<string | null>(null);
  const { marked, gone } = markedRow(rows, actionId, queue.load);
  // Scroll to the marked row once, when it first shows. The queue reloads
  // every 15 seconds, and a reload must not pull the page back to the row.
  const scrolledTo = useRef<string | null>(null);
  useEffect(() => {
    if (!marked || scrolledTo.current === marked) return;
    scrolledTo.current = marked;
    document.getElementById(approvalRowDomId(marked))?.scrollIntoView({ block: "center" });
  }, [marked]);

  const load = useCallback(async () => {
    setLoading(true);
    let result: LoadResult<PendingAction> = { ok: false };
    try {
      const res = await fetch("/api/actions/pending");
      const body: unknown = res.ok ? await res.json() : null;
      if (Array.isArray(body)) result = { ok: true, rows: body as PendingAction[] };
      // Only an org admin reads the queue (AP-BUG-2). Another member sees
      // no queue section and no error.
      else if (res.status === 403) result = { ok: false, closed: true };
    } catch {
      // The request did not arrive. `result` stays a failed load.
    }
    setQueue((q) => afterLoad(q, result));
    setLoading(false);
  }, []);

  const loadWaiting = useCallback(async () => {
    try {
      const runs = await agentRunsApi.waiting();
      setWaiting(runs === null ? null : waitingRuns(runs));
    } catch {
      // A failed read keeps the list the member sees. The next poll retries.
    }
  }, []);

  useEffect(() => {
    void load();
    void loadWaiting();
    const t = setInterval(() => {
      void load();
      void loadWaiting();
    }, 15_000);
    return () => clearInterval(t);
  }, [load, loadWaiting]);

  const answerWaiting = async (run: WaitingRun, answers: ElicitationAnswers) => {
    const question = openQuestion(run);
    if (!question) return;
    setWaitingBusy((b) => ({ ...b, [run.id]: true }));
    setWaitingErrors((e) => {
      const n = { ...e };
      delete n[run.id];
      return n;
    });
    try {
      await agentRunsApi.post(run.id, answerPayload(question, answers));
      setWaiting((rows) => (rows ? rows.filter((r) => r.id !== run.id) : rows));
    } catch (err) {
      setWaitingErrors((e) => ({
        ...e,
        [run.id]: err instanceof Error ? err.message : "Could not send this answer.",
      }));
    } finally {
      setWaitingBusy((b) => {
        const n = { ...b };
        delete n[run.id];
        return n;
      });
    }
  };

  const act = async (id: string, action: "approve" | "reject") => {
    setBusy((b) => ({ ...b, [id]: action }));
    setErrors((e) => {
      const n = { ...e };
      delete n[id];
      return n;
    });
    try {
      const res = await fetch(
        `/api/actions/pending/${encodeURIComponent(id)}/${action}`,
        { method: "POST" }
      );
      const body = await res.json().catch(() => ({}));
      if (res.ok && body.ok !== false) {
        setQueue((q) => ({ ...q, rows: q.rows.filter((r) => r.id !== id) }));
      } else {
        setErrors((e) => ({
          ...e,
          [id]: body.error || `Could not ${action} this action.`,
        }));
      }
    } catch (err) {
      setErrors((e) => ({ ...e, [id]: String(err) }));
    } finally {
      setBusy((b) => {
        const n = { ...b };
        delete n[id];
        return n;
      });
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <Suspense fallback={null}>
        <ActionParam onAction={setActionId} />
      </Suspense>
      {/* The app's title bar (owner, 2026-10-10): the name, how many wait,
          and Refresh at the right end. */}
      <AppTopBar
        title="Approvals"
        subtitle={
          waitingCount(waiting?.length ?? 0, rows.length) > 0
            ? `${waitingCount(waiting?.length ?? 0, rows.length)} waiting`
            : undefined
        }
        tools={
          <Button
            variant="ghost"
            size="sm"
            icon="RefreshCw"
            onClick={() => void load()}
            disabled={loading}
          >
            {loading ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />
      <div className="min-h-0 flex-1 overflow-auto">
    <div className="mx-auto max-w-3xl px-4 py-6">
      {waiting !== null && waiting.length > 0 && (
        <section aria-label="Waiting for you" className="mb-8">
          <h2 className="text-sm font-semibold text-foreground">
            Waiting for you ({waiting.length})
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Questions that agents asked you, oldest first. Each agent waits for
            your answer, and the question stays here until you answer it.
          </p>
          <div className="mt-3 flex flex-col gap-3">
            {waiting.map((run) => {
              const question = openQuestion(run);
              if (!question) return null;
              return (
                <div
                  key={run.id}
                  className="rounded-lg border border-border bg-card/50 p-3 text-xs"
                >
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                    <a
                      href={taskHref(run)}
                      className="min-w-0 flex-1 basis-48 line-clamp-2 text-sm font-medium text-foreground hover:underline"
                    >
                      {rowTitle(run)}
                    </a>
                    <span className="text-muted-foreground">
                      {rowAgent(run)}
                      {question.created_at
                        ? ` \u00b7 ${relativeTime(question.created_at)}`
                        : ""}
                    </span>
                  </div>
                  {run.task?.project_name && (
                    <p className="mt-0.5 text-muted-foreground">{run.task.project_name}</p>
                  )}
                  <div className="mt-2">
                    {answersInPlace(run) ? (
                      <ElicitationCard
                        id={`waiting:${question.id}`}
                        questions={[cardQuestion(question)]}
                        onSubmit={(answers) => void answerWaiting(run, answers)}
                        disabled={!!waitingBusy[run.id]}
                      />
                    ) : (
                      <p className="whitespace-pre-wrap break-words text-foreground">
                        {question.content.body?.trim() || "The agent makes a request."}
                        <span className="ml-1 text-muted-foreground">
                          Open the task to answer it.
                        </span>
                      </p>
                    )}
                  </div>
                  {waitingErrors[run.id] && (
                    <p className="mt-1.5 text-destructive" role="alert">
                      {waitingErrors[run.id]} Try again, or open the task.
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

      {queue.load !== "closed" && waiting !== null && waiting.length > 0 && (
        <h2 className="text-sm font-semibold text-foreground">Proposed writes</h2>
      )}
      {queue.load !== "closed" && (
        <p className="mt-1 text-xs text-muted-foreground">
          Outward writes an agent proposed through the Action Broker. Approving
          runs the write; rejecting refuses it. Empty unless
          <code className="mx-1 rounded bg-secondary/50 px-1 py-0.5 font-mono text-[11px]">
            ACTION_BROKER_ENFORCE
          </code>
          is on.
        </p>
      )}

      {/* One live region, always mounted, so a screen reader announces the
          line when it arrives. It is empty when there is nothing to say. */}
      <div role="status">
        {queue.load === "failed" && (
          <p className="mt-4 text-xs text-destructive">
            Could not load the queue. Refresh to try again.
          </p>
        )}
        {queue.load === "closed" && (waiting?.length ?? 0) === 0 && (
          <p className="mt-4 rounded-lg border border-border bg-card/50 px-3 py-2 text-xs text-muted-foreground">
            An admin or a manager reviews proposed writes. Ask one of them to approve yours.
          </p>
        )}
        {gone && (
          <p className="mt-4 rounded-lg border border-border bg-card/50 px-3 py-2 text-xs text-muted-foreground">
            That request is no longer waiting for you.
          </p>
        )}
      </div>

      <div className="mt-4 flex flex-col gap-2">
        {queue.load === "ok" && rows.length === 0 && (waiting?.length ?? 0) === 0 && (
          <div className="rounded-lg border border-border bg-card/50 px-4 py-8 text-center">
            <p className="text-sm text-muted-foreground">
              Nothing awaiting approval.
            </p>
            <p className="mt-1 text-xs text-muted-foreground/70">
              Every outward write is auto-applying through the broker
              (chokepointed &amp; audited). Set the enforce flag to hold specific
              actions for review here.
            </p>
          </div>
        )}

        {rows.map((r) => {
          const args =
            (r.payload?.args as Record<string, unknown> | undefined) ?? {};
          // The row a link names wears the house selected look, the cursor
          // look of `InboxCard.tsx`. No look of this page's own (MD-3a).
          const isMarked = r.id === marked;
          return (
            <div
              key={r.id}
              id={approvalRowDomId(r.id)}
              aria-current={isMarked ? "true" : undefined}
              className={[
                "rounded-lg border bg-primary/5 p-3 text-xs",
                isMarked ? "border-primary ring-1 ring-primary/40" : "border-primary/20",
              ].join(" ")}
            >
              <div className="flex items-start gap-2">
                <span className="shrink-0 rounded border border-primary/20 bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                  Awaiting review
                </span>
                <span className="font-mono text-foreground line-clamp-1 flex-1">
                  {r.action}
                </span>
                {r.destructive && (
                  <span className="shrink-0 rounded border border-warning/20 bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning">
                    destructive
                  </span>
                )}
              </div>

              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-muted-foreground">
                <span className="font-mono">{r.actor}</span>
                <span>→</span>
                <span className="font-mono">{r.target}</span>
                {r.created_at && (
                  <span className="ml-auto">
                    {new Date(r.created_at).toLocaleString("en-IN")}
                  </span>
                )}
              </div>

              {Object.keys(args).length > 0 && (
                <pre className="mt-2 overflow-x-auto max-h-40 rounded bg-background p-2 text-[10px] text-muted-foreground border border-border whitespace-pre-wrap">
                  {JSON.stringify(args, null, 2)}
                </pre>
              )}

              {errors[r.id] && (
                <p className="mt-1.5 text-destructive">{errors[r.id]}</p>
              )}

              <div className="mt-2 flex gap-1.5">
                <button
                  disabled={!!busy[r.id]}
                  onClick={() => act(r.id, "approve")}
                  className="rounded bg-primary/10 px-2.5 py-1 text-primary hover:bg-primary/15 disabled:opacity-50 transition-colors"
                >
                  {busy[r.id] === "approve" ? "Applying…" : "Approve & run"}
                </button>
                <button
                  disabled={!!busy[r.id]}
                  onClick={() => act(r.id, "reject")}
                  className="rounded bg-secondary/50 px-2.5 py-1 text-muted-foreground hover:bg-secondary disabled:opacity-50 transition-colors"
                >
                  {busy[r.id] === "reject" ? "Rejecting…" : "Reject"}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
      </div>
    </div>
  );
}
