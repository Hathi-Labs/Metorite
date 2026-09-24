"use client";

import AppIcon, { themedIcon, type ThemedIcon } from "@/components/Icon";
import { useMemo, useState } from "react";
import { useTaskStore, itemsForView } from "../lib/taskStore";
import { Energy } from "../lib/types";
import { priorityRank, type PriorityCell } from "../lib/priority";
import { CELL_ICON } from "../lib/priorityIcons";
import { groupItems } from "../lib/ordering";
import { TaskCard } from "./TaskCard";

// The GTD "Engage" surface — "what can I do right NOW?" — energy-first (the
// user's stated priority). You pick your current energy; it shows the do-able
// tasks at or below that energy, ranked by the founder matrix. Time-available
// and context are optional secondary narrowers (classic GTD engage criteria).
//
// This is the deliberate counterpart to the Priority view: Priority ranks ALL
// your work; Engage filters to what you can pick up given your current state.

const ENERGY_ORDER: Record<Energy, number> = { low: 0, medium: 1, high: 2 };

const ENERGY_OPTS: { value: Energy; label: string; icon: ThemedIcon }[] = [
  { value: "low", label: "Low / fried", icon: themedIcon("BatteryLow") },
  { value: "medium", label: "Medium", icon: themedIcon("BatteryMedium") },
  { value: "high", label: "Sharp / high", icon: themedIcon("Battery") },
];

const TIME_OPTS: { value: number; label: string }[] = [
  { value: 0, label: "Any time" },
  { value: 15, label: "≤ 15 min" },
  { value: 30, label: "≤ 30 min" },
  { value: 60, label: "≤ 1 hour" },
];

export function EngageView() {
  const items = useTaskStore((s) => s.items);
  const contexts = useTaskStore((s) => s.contexts);
  const urgentWindowHours = useTaskStore((s) => s.settings.urgentWindowHours);

  const [energy, setEnergy] = useState<Energy>("medium");
  const [maxMins, setMaxMins] = useState(0); // 0 = any
  const [context, setContext] = useState<string>(""); // "" = any

  const base = useMemo(
    () => itemsForView(items, "engage", null),
    [items],
  );

  const matched = useMemo(() => {
    const cap = ENERGY_ORDER[energy];
    const rows = base.filter((i) => {
      // Energy: a task with no energy set is always eligible (unknown ⇒ don't
      // hide it); otherwise it must be AT OR BELOW my current energy.
      if (i.energy && ENERGY_ORDER[i.energy] > cap) return false;
      // Time available (only excludes tasks that are known to be longer).
      if (maxMins > 0 && i.timeEstimateMins && i.timeEstimateMins > maxMins)
        return false;
      // Context (optional).
      if (context && i.context !== context) return false;
      return true;
    });
    // Rank by the matrix (Critical → …), then by due date within a tie.
    // FLOW MATCHING (Csikszentmihalyi): when the user says they're SHARP,
    // that scarce high-capacity state is exactly what deep/flow work needs —
    // so within the same priority level, deep-work tasks surface first. At
    // lower energy the deep tasks mostly filter out anyway (they're usually
    // high-energy), and no boost applies — you can't flow when fried.
    return rows.sort((a, b) => {
      const pr =
        priorityRank(a, urgentWindowHours) - priorityRank(b, urgentWindowHours);
      if (pr !== 0) return pr;
      if (energy === "high" && !!a.deepWork !== !!b.deepWork)
        return a.deepWork ? -1 : 1;
      return (a.dueAt ?? "￿").localeCompare(b.dueAt ?? "￿");
    });
  }, [base, energy, maxMins, context, urgentWindowHours]);

  // Grouped by priority level (Critical → Low Priority) so the pickable set
  // reads as ranked buckets, matching the Priority view. Sections come back in
  // rank order; the header carries the level, so cards don't repeat it.
  const groups = useMemo(
    () => groupItems(matched, "priority", urgentWindowHours),
    [matched, urgentWindowHours],
  );

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border bg-card px-4 py-3">
        <div className="flex items-center gap-2">
          <AppIcon name="Zap" className="h-4 w-4 text-primary" />
          <h1 className="text-base font-bold text-foreground">Engage · Now</h1>
          <span className="ml-auto text-xs text-muted-foreground">
            {matched.length} pickable
          </span>
        </div>
        <p className="mt-0.5 text-[11px] text-muted-foreground">
          What can you do right now? Set your energy — we&rsquo;ll surface the
          matches, most important first.
        </p>

        {/* Energy — the primary picker */}
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-[11px] font-medium text-muted-foreground">
            I&rsquo;m feeling
          </span>
          {ENERGY_OPTS.map((o) => {
            const Icon = o.icon;
            const on = energy === o.value;
            return (
              <button
                key={o.value}
                type="button"
                onClick={() => setEnergy(o.value)}
                aria-pressed={on}
                className={[
                  "tech-transition inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium",
                  on
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:border-primary/40",
                ].join(" ")}
              >
                <Icon className="h-3.5 w-3.5" />
                {o.label}
              </button>
            );
          })}
        </div>

        {/* Secondary narrowers — time + context */}
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <label className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <AppIcon name="Clock" className="h-3.5 w-3.5" />
            <select
              value={maxMins}
              onChange={(e) => setMaxMins(Number(e.target.value))}
              className="tech-transition h-7 rounded-md border border-border bg-background pl-2 pr-6 text-xs text-foreground focus:border-primary focus:outline-none"
            >
              {TIME_OPTS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          {contexts.length > 0 && (
            <label className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <AppIcon name="Tag" className="h-3.5 w-3.5" />
              <select
                value={context}
                onChange={(e) => setContext(e.target.value)}
                className="tech-transition h-7 rounded-md border border-border bg-background pl-2 pr-6 text-xs text-foreground focus:border-primary focus:outline-none"
              >
                <option value="">Any context</option>
                {contexts.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      </header>

      {matched.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
          <AppIcon name="Zap" className="h-6 w-6 text-muted-foreground/50" />
          <p className="text-sm text-foreground">Nothing matches right now</p>
          <p className="max-w-xs text-xs text-muted-foreground">
            Try a higher energy level or a longer time window — or enjoy the
            clear runway.
          </p>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto">
          {groups.map((g) => {
            const GroupIcon = CELL_ICON[g.key as PriorityCell];
            return (
            <section key={g.key}>
              <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-border bg-card/95 px-3 py-1.5 backdrop-blur">
                {GroupIcon && (
                  <GroupIcon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                )}
                <span className="truncate text-[11px] font-semibold uppercase tracking-wide text-foreground">
                  {g.label}
                </span>
                <span className="shrink-0 rounded-full bg-background/60 px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
                  {g.items.length}
                </span>
              </div>
              {g.items.map((item) => (
                <TaskCard key={item.id} item={item} variant="row" />
              ))}
            </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
