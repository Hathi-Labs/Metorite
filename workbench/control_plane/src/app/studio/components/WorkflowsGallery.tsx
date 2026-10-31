"use client";

/**
 * The workflow gallery, moved out of `/workflows` into the Studio library
 * (`ai_studio.md` §14 E1). The Studio page owns the one `<h1>`. This section
 * heads itself with an `<h2>`. Module Studio now lives on a Studio tab.
 *
 * Data:  list    GET  /api/workflows
 *        create  POST /api/workflows
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import FilterPills from "@/components/FilterPills";
import {
  createWorkflow,
  deleteWorkflow,
  duplicateWorkflow,
  listWorkflows,
} from "@/app/workflows/lib/api";
import type { WorkflowSummary } from "@/app/workflows/lib/types";

const STATUS_BADGE: Record<string, string> = {
  published: "bg-success/10 text-success border-success/20",
  draft: "bg-warning/10 text-warning border-warning/20",
  disabled: "bg-muted text-muted-foreground border-border",
};

const RUN_DOT: Record<string, string> = {
  succeeded: "bg-success",
  failed: "bg-destructive",
  running: "bg-primary animate-pulse",
};

export default function WorkflowsGallery() {
  const router = useRouter();
  const [rows, setRows] = useState<WorkflowSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [actioning, setActioning] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  // Fetch-on-mount wiring: same pattern (and lint carve-out) as
  // build/apps/page.tsx.
  /* eslint-disable react-hooks/set-state-in-effect */
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await listWorkflows());
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const filtered = useMemo(() => {
    let out = rows;
    if (filter === "published") out = out.filter((r) => r.status === "published");
    if (filter === "drafts") out = out.filter((r) => r.status === "draft");
    if (filter === "disabled") out = out.filter((r) => r.status === "disabled");
    const q = query.trim().toLowerCase();
    if (q)
      out = out.filter(
        (r) =>
          r.name.toLowerCase().includes(q) ||
          (r.description ?? "").toLowerCase().includes(q),
      );
    return out;
  }, [rows, filter, query]);

  const onCreate = useCallback(async () => {
    setCreating(true);
    try {
      const wf = await createWorkflow("Untitled workflow");
      router.push(`/studio/workflows/${wf.id}`);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
      setCreating(false);
    }
  }, [router]);

  const onDuplicate = useCallback(
    async (e: React.MouseEvent, id: string) => {
      e.stopPropagation();
      setActioning(id);
      try {
        const copy = await duplicateWorkflow(id);
        router.push(`/studio/workflows/${copy.id}`);
      } catch (err) {
        setError(String(err instanceof Error ? err.message : err));
        setActioning(null);
      }
    },
    [router],
  );

  // Two-click delete: first click arms the button, second click deletes.
  const onDelete = useCallback(
    async (e: React.MouseEvent, id: string) => {
      e.stopPropagation();
      if (confirmDelete !== id) {
        setConfirmDelete(id);
        return;
      }
      setConfirmDelete(null);
      setActioning(id);
      try {
        await deleteWorkflow(id);
        await load();
      } catch (err) {
        setError(String(err instanceof Error ? err.message : err));
      } finally {
        setActioning(null);
      }
    },
    [confirmDelete, load],
  );

  return (
    <section className="flex flex-col" aria-label="Workflows">
      <div className="flex items-center justify-between px-4 sm:px-6 py-3 border-b border-border shrink-0">
        <div>
          <h2 className="text-sm font-bold text-foreground">Workflows</h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            Automate across your agents, tools and integrations
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            title="Refresh workflows"
            className="p-2 rounded-lg border border-border text-muted-foreground hover:bg-secondary tech-transition"
          >
            <Icon name="RefreshCw" className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          </button>
          <Button size="lg" layout="flex items-center" onClick={onCreate} disabled={creating}>
            {creating ? (
              <Icon name="Loader2" className="w-4 h-4 animate-spin" />
            ) : (
              <Icon name="Plus" className="w-4 h-4" />
            )}
            New workflow
          </Button>
        </div>
      </div>

      <>
          <div className="flex items-center border-b border-border shrink-0">
            <FilterPills
              className="border-b-0 flex-1 min-w-0"
              items={[
                { id: "all", label: "All", count: rows.length },
                {
                  id: "published",
                  label: "Published",
                  count: rows.filter((r) => r.status === "published").length,
                },
                {
                  id: "drafts",
                  label: "Drafts",
                  count: rows.filter((r) => r.status === "draft").length,
                },
                // Auto-disabled workflows are the ones that need a human
                // (spec R2) — they get their own pill so they are one click
                // away instead of buried in a grid of healthy cards.
                {
                  id: "disabled",
                  label: "Disabled",
                  count: rows.filter((r) => r.status === "disabled").length,
                },
              ]}
              activeId={filter}
              onChange={setFilter}
            />
            <div className="relative pr-3 sm:pr-4 shrink-0">
              <Icon name="Search" className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search workflows…"
                className="w-36 sm:w-56 rounded-lg border border-input bg-background pl-8 pr-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              />
            </div>
          </div>

          <div>
            <div className="p-4 sm:p-6">
              {loading && (
                <div className="flex flex-col items-center justify-center h-40 gap-3">
                  <Icon name="Loader2" className="w-5 h-5 animate-spin text-muted-foreground" />
                  <p className="text-sm text-muted-foreground">Loading workflows…</p>
                </div>
              )}
              {error && !loading && (
                <div className="flex flex-col items-center justify-center h-40 gap-3">
                  <p className="text-sm text-destructive">{error}</p>
                  <button
                    onClick={load}
                    className="text-xs text-muted-foreground hover:text-foreground underline"
                  >
                    Retry
                  </button>
                </div>
              )}
              {!loading && !error && filtered.length === 0 && rows.length > 0 && (
                <div className="flex flex-col items-center justify-center h-48 gap-3 text-center">
                  <Icon name="Search" className="w-8 h-8 text-muted-foreground/50" />
                  <p className="text-sm text-muted-foreground">
                    No workflows match. Try a different search or filter.
                  </p>
                </div>
              )}
              {!loading && !error && rows.length === 0 && (
                <div className="flex flex-col items-center justify-center h-48 gap-3 text-center">
                  <Icon name="Workflow" className="w-8 h-8 text-muted-foreground/50" />
                  <p className="text-sm text-muted-foreground">
                    No workflows yet. Build your first automation — trigger →
                    agents → tools → done.
                  </p>
                  <Button size="none" layout="" onClick={onCreate} className="px-4 py-2 text-sm">
                    Create a workflow
                  </Button>
                </div>
              )}
              {!loading && !error && filtered.length > 0 && (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
                  {filtered.map((wf) => (
                    <div
                      key={wf.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => router.push(`/studio/workflows/${wf.id}`)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") router.push(`/studio/workflows/${wf.id}`);
                      }}
                      onMouseLeave={() => {
                        if (confirmDelete === wf.id) setConfirmDelete(null);
                      }}
                      className="text-left rounded-xl border border-border bg-card p-4 hover:border-primary/40 tech-transition group cursor-pointer"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-primary/20 to-accent/15 flex items-center justify-center shrink-0">
                          <Icon name="Workflow" className="w-5 h-5 text-primary" />
                        </div>
                        <div className="flex items-center gap-1">
                          {actioning === wf.id ? (
                            <Icon name="Loader2" className="w-3.5 h-3.5 animate-spin text-muted-foreground" />
                          ) : (
                            <span className="hidden group-hover:flex items-center gap-1">
                              <Button variant="ghost" size="icon-xs" radius="keep" layout="" onClick={(e) => onDuplicate(e, wf.id)} title="Duplicate workflow" className="rounded-md">
                                <Icon name="Copy" className="w-3.5 h-3.5" />
                              </Button>
                              <button
                                onClick={(e) => onDelete(e, wf.id)}
                                title={
                                  confirmDelete === wf.id
                                    ? "Click again to delete permanently"
                                    : "Delete workflow"
                                }
                                className={`p-1 rounded-md tech-transition ${
                                  confirmDelete === wf.id
                                    ? "text-destructive bg-destructive/10 ring-1 ring-destructive/40"
                                    : "text-muted-foreground hover:text-destructive hover:bg-secondary"
                                }`}
                              >
                                <Icon name="Trash2" className="w-3.5 h-3.5" />
                              </button>
                            </span>
                          )}
                          <span
                            className={`text-[10px] px-2 py-0.5 rounded-full border ${STATUS_BADGE[wf.status] ?? STATUS_BADGE.draft}`}
                          >
                            {wf.status}
                          </span>
                        </div>
                      </div>
                      <div className="mt-3 font-medium text-sm text-foreground group-hover:text-primary tech-transition truncate">
                        {wf.name}
                      </div>
                      <div className="text-xs text-muted-foreground mt-0.5 line-clamp-2 min-h-[2rem]">
                        {wf.description || "No description"}
                      </div>
                      {wf.status === "disabled" && wf.disabled_reason && (
                        <div className="mt-2 flex items-start gap-1.5 rounded-lg border border-warning/20 bg-warning/10 px-2 py-1.5 text-[10px] leading-snug text-warning">
                          <Icon name="AlertTriangle" className="w-3 h-3 shrink-0 mt-px" />
                          <span className="line-clamp-2">{wf.disabled_reason}</span>
                        </div>
                      )}
                      <div className="mt-3 flex items-center gap-3 text-[10px] text-muted-foreground">
                        {wf.owner_email && (
                          <span className="truncate">by {wf.owner_email.split("@")[0]}</span>
                        )}
                        <span className="flex items-center gap-1">
                          <Icon name="Zap" className="w-3 h-3" />
                          {wf.trigger_count ?? 0} trigger
                          {(wf.trigger_count ?? 0) === 1 ? "" : "s"}
                        </span>
                        {wf.last_run_status && (
                          <span className="flex items-center gap-1.5">
                            <span
                              className={`w-1.5 h-1.5 rounded-full ${RUN_DOT[wf.last_run_status] ?? "bg-muted-foreground"}`}
                            />
                            {wf.last_run_status}
                          </span>
                        )}
                        {wf.updated_at && (
                          <span className="flex items-center gap-1 ml-auto">
                            <Icon name="Clock" className="w-3 h-3" />
                            {new Date(wf.updated_at).toLocaleDateString()}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
      </>
    </section>
  );
}
