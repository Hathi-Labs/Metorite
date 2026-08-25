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
 */

import Button from "@/components/ui/Button";
import { useCallback, useEffect, useState } from "react";

import type { PendingAction } from "@/app/api/actions/pending/route";

export default function ApprovalsPage() {
  const [rows, setRows] = useState<PendingAction[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetched, setFetched] = useState(false);
  const [busy, setBusy] = useState<Record<string, "approve" | "reject">>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

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
    <div className="mx-auto max-w-3xl px-4 py-6">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">Approvals</h1>
        {rows.length > 0 && (
          <span className="rounded-full bg-primary px-2 py-0.5 text-xs font-semibold text-white">
            {rows.length}
          </span>
        )}
        <Button variant="text" size="none" layout="" onClick={() => void load()} className="ml-auto text-xs">
          {loading ? "Refreshing…" : "Refresh"}
        </Button>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Outward writes an agent proposed through the Action Broker. Approving
        runs the write; rejecting refuses it. Empty unless
        <code className="mx-1 rounded bg-secondary/50 px-1 py-0.5 font-mono text-[11px]">
          ACTION_BROKER_ENFORCE
        </code>
        is on.
      </p>

      <div className="mt-4 flex flex-col gap-2">
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
          return (
            <div
              key={r.id}
              className="rounded-lg border border-primary/20 bg-primary/5 p-3 text-xs"
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
  );
}
