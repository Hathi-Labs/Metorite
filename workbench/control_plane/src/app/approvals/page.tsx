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
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

import type { PendingAction } from "@/app/api/actions/pending/route";

import { actionIdFromSearch, approvalRowDomId, markedRow } from "./lib/approvalLink";

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
  const [rows, setRows] = useState<PendingAction[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetched, setFetched] = useState(false);
  const [busy, setBusy] = useState<Record<string, "approve" | "reject">>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  // The proposal a link names (MD-3a), and the row it marks.
  const [actionId, setActionId] = useState<string | null>(null);
  const { marked, gone } = markedRow(rows, actionId, fetched);
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
    try {
      const res = await fetch("/api/actions/pending");
      const all: PendingAction[] = res.ok ? await res.json() : [];
      setRows(all);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
      setFetched(true);
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 15_000);
    return () => clearInterval(t);
  }, [load]);

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
        setRows((rs) => rs.filter((r) => r.id !== id));
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
        subtitle={rows.length > 0 ? `${rows.length} waiting` : undefined}
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
      <p className="text-xs text-muted-foreground">
        Outward writes an agent proposed through the Action Broker. Approving
        runs the write; rejecting refuses it. Empty unless
        <code className="mx-1 rounded bg-secondary/50 px-1 py-0.5 font-mono text-[11px]">
          ACTION_BROKER_ENFORCE
        </code>
        is on.
      </p>

      <div className="mt-4 flex flex-col gap-2">
        {gone && (
          <p
            role="status"
            className="rounded-lg border border-border bg-card/50 px-3 py-2 text-xs text-muted-foreground"
          >
            That request is no longer waiting for you.
          </p>
        )}

        {fetched && rows.length === 0 && (
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
