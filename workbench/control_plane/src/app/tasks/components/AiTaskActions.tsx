"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useState } from "react";
import { useTaskStore } from "../lib/taskStore";
import type { MyTask } from "../lib/types";
import type { EnrichFields } from "../lib/api";
import { durationLabel } from "../lib/utils";

// The per-task AI affordances, shown in the task detail header (so every task —
// local or ClickUp — can be re-clarified or auto-filled):
//   • Re-clarify — re-run the GTD Clarify wizard on this task (break it down,
//     add local context/energy, re-decide) via the ReclarifyModal.
//   • Fill missing details — the assistant proposes values for ONLY the empty
//     fields (context / energy / time / due / assignee); the user reviews the
//     pre-checked list and applies. Never overwrites a field that's already set.
export function AiTaskActions({ item }: { item: MyTask }) {
  const backend = useTaskStore((s) => s.backend);
  const openReclarify = useTaskStore((s) => s.openReclarify);
  const enrichItem = useTaskStore((s) => s.enrichItem);
  const updateItem = useTaskStore((s) => s.updateItem);

  const [fillOpen, setFillOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [fields, setFields] = useState<EnrichFields | null>(null);
  const [chosen, setChosen] = useState<Set<keyof EnrichFields>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const live = backend === "live";

  const openFill = async () => {
    setFillOpen(true);
    setLoading(true);
    setError(null);
    setFields(null);
    try {
      const f = await enrichItem(item.id);
      setFields(f);
      // Pre-check everything the assistant returned (review-then-apply).
      setChosen(new Set(Object.keys(f) as (keyof EnrichFields)[]));
    } catch {
      setError("Couldn't reach the assistant. Try again.");
    } finally {
      setLoading(false);
    }
  };

  const toggle = (k: keyof EnrichFields) =>
    setChosen((c) => {
      const next = new Set(c);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  const apply = () => {
    if (!fields) return;
    const patch: Parameters<typeof updateItem>[1] = {};
    if (chosen.has("context") && fields.context) patch.context = fields.context;
    if (chosen.has("energy") && fields.energy) patch.energy = fields.energy;
    if (chosen.has("timeEstimateMins") && fields.timeEstimateMins)
      patch.timeEstimateMins = fields.timeEstimateMins;
    if (chosen.has("dueAt") && fields.dueAt) patch.dueAt = fields.dueAt;
    if (chosen.has("assignee") && fields.assignee)
      patch.assignee = fields.assignee;
    if (Object.keys(patch).length) updateItem(item.id, patch);
    setFillOpen(false);
  };

  const proposedCount = fields ? Object.keys(fields).length : 0;

  return (
    <div className="relative flex items-center gap-1.5">
      <button
        type="button"
        onClick={() => openReclarify(item.id)}
        title="Re-run Clarify on this task — break it down or refine it"
        className="tech-transition inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-[11px] font-medium text-muted-foreground hover:border-primary/40 hover:text-foreground"
      >
        <Icon name="RefreshCw" className="h-3.5 w-3.5" />
        Re-clarify
      </button>
      <button
        type="button"
        onClick={openFill}
        disabled={!live}
        title={
          live
            ? "Let the assistant fill in the missing details"
            : "Connect the backend to use the assistant"
        }
        className="tech-transition inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-[11px] font-medium text-muted-foreground hover:border-primary/40 hover:text-foreground disabled:opacity-40"
      >
        <Icon name="Wand2" className="h-3.5 w-3.5" />
        Fill details
      </button>

      {fillOpen && (
        <>
          {/* click-away */}
          <button
            type="button"
            aria-label="Close"
            className="fixed inset-0 z-40 cursor-default"
            onClick={() => setFillOpen(false)}
          />
          <div className="absolute right-0 top-full z-50 mt-1.5 w-72 rounded-lg border border-border bg-card p-3 shadow-xl">
            <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-primary">
              <Icon name="Sparkles" className="h-3.5 w-3.5" />
              Fill missing details
            </div>
            {loading ? (
              <div className="flex items-center gap-2 py-3 text-xs text-muted-foreground">
                <Icon name="Loader2" className="h-4 w-4 animate-spin" />
                Reading the task…
              </div>
            ) : error ? (
              <p className="py-2 text-xs text-destructive">{error}</p>
            ) : proposedCount === 0 ? (
              <p className="py-2 text-xs text-muted-foreground">
                Nothing missing — this task already has its details.
              </p>
            ) : (
              <>
                <div className="flex flex-col gap-1">
                  {fields?.context !== undefined && (
                    <FillRow
                      label="Context"
                      value={fields.context}
                      checked={chosen.has("context")}
                      onToggle={() => toggle("context")}
                    />
                  )}
                  {fields?.energy !== undefined && (
                    <FillRow
                      label="Energy"
                      value={fields.energy}
                      checked={chosen.has("energy")}
                      onToggle={() => toggle("energy")}
                    />
                  )}
                  {fields?.timeEstimateMins !== undefined && (
                    <FillRow
                      label="Estimate"
                      value={durationLabel(fields.timeEstimateMins)}
                      checked={chosen.has("timeEstimateMins")}
                      onToggle={() => toggle("timeEstimateMins")}
                    />
                  )}
                  {fields?.dueAt !== undefined && (
                    <FillRow
                      label="Due"
                      value={fields.dueAt.slice(0, 10)}
                      checked={chosen.has("dueAt")}
                      onToggle={() => toggle("dueAt")}
                    />
                  )}
                  {fields?.assignee !== undefined && (
                    <FillRow
                      label="Assignee"
                      value={fields.assignee.name}
                      checked={chosen.has("assignee")}
                      onToggle={() => toggle("assignee")}
                    />
                  )}
                </div>
                <div className="mt-2.5 flex items-center gap-2">
                  <Button size="none" radius="keep" layout="inline-flex items-center" type="button" onClick={apply} disabled={chosen.size === 0} className="gap-1.5 rounded-md px-2.5 py-1.5 text-xs">
                    <Icon name="Check" className="h-3.5 w-3.5" />
                    Apply {chosen.size > 0 ? chosen.size : ""}
                  </Button>
                  <Button variant="ghost" size="none" radius="keep" layout="" type="button" onClick={() => setFillOpen(false)} className="rounded-md px-2 py-1.5 text-xs">
                    Cancel
                  </Button>
                </div>
                <p className="mt-1.5 text-[10px] text-muted-foreground">
                  Only empty fields are suggested — nothing you&apos;ve set is
                  touched.
                </p>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function FillRow({
  label,
  value,
  checked,
  onToggle,
}: {
  label: string;
  value: string;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={[
        "tech-transition flex items-center gap-2 rounded-md border px-2 py-1.5 text-left",
        checked
          ? "border-primary/40 bg-primary/5"
          : "border-border hover:bg-secondary",
      ].join(" ")}
    >
      <span
        className={[
          "flex h-4 w-4 shrink-0 items-center justify-center rounded border",
          checked
            ? "border-primary bg-primary text-primary-foreground"
            : "border-border",
        ].join(" ")}
      >
        {checked && <Icon name="Check" className="h-3 w-3" />}
      </span>
      <span className="w-16 shrink-0 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="min-w-0 flex-1 truncate text-xs text-foreground">
        {value}
      </span>
    </button>
  );
}
