"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import type { ThemedIcon } from "@/components/Icon";
import { useMemo, useRef, useState, useEffect } from "react";
import { GtdItem } from "../lib/types";
import { useTaskStore } from "../lib/taskStore";
import {
  filtersActive,
  activeFilterCount,
  NO_CONTEXT_FACET,
  NO_ENERGY_FACET,
  SORT_LABEL,
  type GroupBy,
  type SortField,
  type TaskFilters,
} from "../lib/ordering";
import {
  CELL_META,
  CELLS_IN_ORDER,
  priorityCell,
  type PriorityCell,
} from "../lib/priority";
import { CELL_ICON } from "../lib/priorityIcons";
import { contextAccent } from "../lib/contextColors";

// The unified filter + sort + group bar (Jira/Linear-style). One row:
//   [Search]  [Filter ▾ (N)] [chips…]        [Group by ▾]  [Sort ▾ ⇅]
// The Filter popover holds every facet (Context / Priority / Energy, + Assignee
// off My Next Actions) as MULTI-select checklists — a task matches ANY value
// within a facet (OR) and must pass EVERY active facet (AND). Active values show
// as removable chips inline, so the state is always visible without opening the
// popover. This scales to more facets without stacking pill rows (no cognitive
// overload — one control, progressive disclosure).
// Per-view exceptions: Assignee is hidden on My Next Actions (all mine), and
// Sort is hidden on Waiting For (that view derives its own order).

// SORT_LABEL moved to lib/ordering.ts — the board's drop-refusal overlay names
// the active sort from the same map this menu draws it from.

const SORT_FIELDS: SortField[] = [
  "manual", "priority", "due", "created", "title", "energy",
];

const GROUP_LABEL: Record<GroupBy | "", string> = {
  "": "Status", // the default grouping for Next Actions IS by status
  none: "No grouping",
  context: "Context",
  priority: "Priority",
  mode: "Suggestion",
  energy: "Energy",
  depth: "Work mode",
};

const GROUP_OPTIONS: (GroupBy | "")[] = [
  "", "context", "priority", "mode", "energy", "depth", "none",
];

const ENERGY_VALUES: { value: string; label: string }[] = [
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
  { value: NO_ENERGY_FACET, label: "No energy set" },
];

export function TaskToolbar({ items }: { items: GtdItem[] }) {
  const filters = useTaskStore((s) => s.filters);
  const setFilters = useTaskStore((s) => s.setFilters);
  const clearFilters = useTaskStore((s) => s.clearFilters);
  const sort = useTaskStore((s) => s.sort);
  const setSort = useTaskStore((s) => s.setSort);
  const groupBy = useTaskStore((s) => s.groupBy);
  const setGroupBy = useTaskStore((s) => s.setGroupBy);
  const view = useTaskStore((s) => s.selectedView);
  // My Next Actions is only ever tasks assigned to me, so an assignee facet
  // there is meaningless. It's offered on the other views (e.g. Waiting For).
  const showAssignee = view !== "next";
  // Waiting For orders itself: WaitingForView re-derives the whole order
  // (rows by days-waiting, groups by overdue count), so a Sort choice cannot
  // reach it. Hide the control there rather than leave a live-looking one that
  // silently does nothing. Search + filters still apply, so the rest of the
  // toolbar stays. Same per-view shape as showAssignee above.
  const showSort = view !== "waiting";

  // Facet option lists come from the items actually in view, so a facet never
  // offers a value that would return nothing. Each carries a live count.
  const contextOpts = useMemo(() => {
    const m = new Map<string, number>();
    for (const i of items) {
      const k = i.context || NO_CONTEXT_FACET;
      m.set(k, (m.get(k) ?? 0) + 1);
    }
    return [...m.entries()]
      .sort((a, b) =>
        a[0] === NO_CONTEXT_FACET ? 1 : b[0] === NO_CONTEXT_FACET ? -1 : a[0].localeCompare(b[0]),
      )
      .map(([value, count]) => ({
        value,
        label: value === NO_CONTEXT_FACET ? "No @context" : value,
        // The context's own colour dot — same vocabulary as the card chip.
        dot: value === NO_CONTEXT_FACET ? undefined : contextAccent(value).dot,
        count,
      }));
  }, [items]);

  const priorityOpts = useMemo(() => {
    const counts = new Map<PriorityCell, number>();
    for (const i of items) {
      const c = priorityCell(i);
      counts.set(c, (counts.get(c) ?? 0) + 1);
    }
    return CELLS_IN_ORDER.filter((c) => counts.has(c)).map((c) => ({
      value: c as string,
      label: CELL_META[c].label,
      icon: CELL_ICON[c],
      count: counts.get(c)!,
    }));
  }, [items]);

  const energyOpts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const i of items) counts.set(i.energy || NO_ENERGY_FACET, (counts.get(i.energy || NO_ENERGY_FACET) ?? 0) + 1);
    return ENERGY_VALUES.filter((e) => counts.has(e.value)).map((e) => ({
      ...e,
      count: counts.get(e.value)!,
    }));
  }, [items]);

  const assigneeOpts = useMemo(
    () =>
      Array.from(new Set(items.map((i) => i.assignee?.name).filter(Boolean)))
        .sort()
        .map((v) => v as string),
    [items],
  );

  const active = filtersActive(filters);
  const nFacets = activeFilterCount(filters);

  // Toggle a value in a multi-select facet.
  const toggle = (key: "contexts" | "priorities" | "energies", value: string) => {
    const cur = filters[key];
    const next = cur.includes(value)
      ? cur.filter((v) => v !== value)
      : [...cur, value];
    setFilters({ [key]: next } as Partial<TaskFilters>);
  };

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border bg-card px-4 py-2">
      {/* Search */}
      <div className="relative min-w-[160px] flex-1 sm:max-w-xs">
        <Icon name="Search" className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <input
          type="text"
          value={filters.query}
          onChange={(e) => setFilters({ query: e.target.value })}
          placeholder="Search tasks…"
          className="tech-transition h-7 w-full rounded-md border border-border bg-background pl-7 pr-6 text-xs text-foreground placeholder:text-muted-foreground/70 focus:border-primary focus:outline-none"
        />
        {filters.query && (
          <button
            type="button"
            onClick={() => setFilters({ query: "" })}
            title="Clear search"
            className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          >
            <Icon name="X" className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {/* Filter — one popover for every facet */}
      <FilterMenu
        count={filters.contexts.length + filters.priorities.length + filters.energies.length}
        sections={[
          { key: "contexts", label: "Context", options: contextOpts, selected: filters.contexts },
          { key: "priorities", label: "Priority", options: priorityOpts, selected: filters.priorities },
          { key: "energies", label: "Energy", options: energyOpts, selected: filters.energies },
        ]}
        onToggle={toggle}
      />

      {/* Assignee (single-select) — only on views where tasks aren't all mine. */}
      {showAssignee && assigneeOpts.length > 0 && (
        <Select
          label="Assignee"
          value={filters.assignee}
          onChange={(v) => setFilters({ assignee: v })}
          options={assigneeOpts}
          anyLabel="Anyone"
        />
      )}

      {/* Active-facet chips — the current filter state, always visible + removable. */}
      <FacetChips
        filters={filters}
        priorityMeta={priorityOpts}
        onRemove={(key, value) => toggle(key, value)}
      />

      {active && (
        <Button variant="ghost" size="none" radius="keep" layout="inline-flex items-center" type="button" onClick={clearFilters} className="h-7 gap-1 rounded-md px-2 text-[11px]">
          <Icon name="X" className="h-3 w-3" />
          Clear{nFacets > 1 ? " all" : ""}
        </Button>
      )}

      {/* Group-by + Sort — pushed right */}
      <div className="ml-auto flex items-center gap-1">
        <Icon name="Rows3" className="h-3.5 w-3.5 text-muted-foreground" />
        <select
          value={groupBy}
          onChange={(e) => setGroupBy(e.target.value as GroupBy | "")}
          aria-label="Group by"
          className={[
            "tech-transition h-7 rounded-md border bg-background pl-2 pr-6 text-xs focus:border-primary focus:outline-none",
            groupBy ? "border-primary/50 text-foreground" : "border-border text-muted-foreground",
          ].join(" ")}
        >
          {GROUP_OPTIONS.map((g) => (
            <option key={g || "default"} value={g}>
              {GROUP_LABEL[g]}
            </option>
          ))}
        </select>
      </div>
      {showSort && (
        <div className="flex items-center gap-1">
          <Icon name="ListFilter" className="h-3.5 w-3.5 text-muted-foreground" />
          <select
            value={sort.field}
            onChange={(e) => setSort({ field: e.target.value as SortField })}
            aria-label="Sort by"
            className="tech-transition h-7 rounded-md border border-border bg-background pl-2 pr-6 text-xs text-foreground focus:border-primary focus:outline-none"
          >
            {SORT_FIELDS.map((f) => (
              <option key={f} value={f}>
                {SORT_LABEL[f]}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setSort({ dir: sort.dir === "asc" ? "desc" : "asc" })}
            disabled={sort.field === "manual"}
            title={
              sort.field === "manual"
                ? "Manual order (drag cards to reorder)"
                : sort.dir === "asc"
                  ? "Ascending — click for descending"
                  : "Descending — click for ascending"
            }
            className={[
              "tech-transition inline-flex h-7 w-7 items-center justify-center rounded-md border border-border",
              sort.field === "manual"
                ? "cursor-not-allowed text-muted-foreground/40"
                : "text-muted-foreground hover:bg-secondary hover:text-foreground",
            ].join(" ")}
          >
            {sort.dir === "asc" ? (
              <Icon name="ArrowUpNarrowWide" className="h-3.5 w-3.5" />
            ) : (
              <Icon name="ArrowDownWideNarrow" className="h-3.5 w-3.5" />
            )}
          </button>
        </div>
      )}
    </div>
  );
}

// ── Filter popover ───────────────────────────────────────────────────────────

interface FacetOption {
  value: string;
  label: string;
  /** lucide icon (priority) — takes precedence over a dot/emoji marker. */
  icon?: ThemedIcon;
  /** a solid colour dot marker (context) from the themed ramp, e.g. "bg-cat-1". */
  dot?: string;
  emoji?: string;
  count: number;
}
interface FacetSection {
  key: "contexts" | "priorities" | "energies";
  label: string;
  options: FacetOption[];
  selected: string[];
}

function FilterMenu({
  count,
  sections,
  onToggle,
}: {
  count: number;
  sections: FacetSection[];
  onToggle: (key: FacetSection["key"], value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Only offer facets that actually have options in view.
  const usable = sections.filter((s) => s.options.length > 0);
  if (usable.length === 0) return null;

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={[
          "tech-transition inline-flex h-7 items-center gap-1 rounded-md border px-2 text-xs font-medium",
          count > 0
            ? "border-primary/50 bg-primary/5 text-foreground"
            : "border-border text-muted-foreground hover:text-foreground",
        ].join(" ")}
      >
        <Icon name="ListFilter" className="h-3.5 w-3.5" />
        Filter
        {count > 0 && (
          <span className="rounded-full bg-primary/15 px-1.5 text-[10px] font-semibold text-primary">
            {count}
          </span>
        )}
        <Icon name="ChevronDown" className="h-3 w-3 opacity-60" />
      </button>
      {open && (
        <div className="absolute left-0 top-8 z-40 max-h-[70vh] w-64 overflow-y-auto rounded-lg border border-border bg-card p-2 shadow-xl">
          {usable.map((s, si) => (
            <div key={s.key} className={si > 0 ? "mt-2 border-t border-border pt-2" : undefined}>
              <p className="px-1 pb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                {s.label}
              </p>
              {s.options.map((o) => {
                const on = s.selected.includes(o.value);
                return (
                  <button
                    key={o.value}
                    type="button"
                    onClick={() => onToggle(s.key, o.value)}
                    className="tech-transition flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-xs hover:bg-secondary"
                  >
                    <span
                      className={[
                        "flex h-4 w-4 shrink-0 items-center justify-center rounded border",
                        on ? "border-primary bg-primary text-primary-foreground" : "border-border",
                      ].join(" ")}
                    >
                      {on && <Icon name="Check" className="h-3 w-3" />}
                    </span>
                    {o.icon ? (
                      <o.icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                    ) : o.dot ? (
                      <span className={`h-2 w-2 shrink-0 rounded-full ${o.dot}`} aria-hidden />
                    ) : o.emoji ? (
                      <span aria-hidden>{o.emoji}</span>
                    ) : null}
                    <span className="min-w-0 flex-1 truncate text-foreground">{o.label}</span>
                    <span className="shrink-0 text-[10px] text-muted-foreground">{o.count}</span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Active-facet chips ───────────────────────────────────────────────────────

interface FacetChip {
  key: "contexts" | "priorities" | "energies";
  value: string;
  label: string;
  /** priority chips carry their lucide icon; context chips their colour. */
  icon?: ThemedIcon;
  dot?: string;
}

function FacetChips({
  filters,
  priorityMeta,
  onRemove,
}: {
  filters: TaskFilters;
  priorityMeta: { value: string; label: string; icon?: ThemedIcon }[];
  onRemove: (key: "contexts" | "priorities" | "energies", value: string) => void;
}) {
  const chips: FacetChip[] = [
    ...filters.contexts.map((v) => ({
      key: "contexts" as const,
      value: v,
      label: v === NO_CONTEXT_FACET ? "No @context" : v,
      dot: v === NO_CONTEXT_FACET ? undefined : contextAccent(v).dot,
    })),
    ...filters.priorities.map((v) => {
      const meta = priorityMeta.find((p) => p.value === v);
      return {
        key: "priorities" as const,
        value: v,
        label: meta ? meta.label : v,
        icon: meta?.icon,
      };
    }),
    ...filters.energies.map((v) => ({
      key: "energies" as const,
      value: v,
      label:
        v === NO_ENERGY_FACET
          ? "No energy"
          : `${v.charAt(0).toUpperCase()}${v.slice(1)} energy`,
    })),
  ];
  if (chips.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {chips.map((c) => (
        <span
          key={`${c.key}:${c.value}`}
          className="inline-flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 py-0.5 pl-2 pr-1 text-[11px] font-medium text-primary"
        >
          {c.icon ? (
            <c.icon className="h-3 w-3 shrink-0" aria-hidden />
          ) : c.dot ? (
            <span className={`h-2 w-2 shrink-0 rounded-full ${c.dot}`} aria-hidden />
          ) : null}
          <span className="truncate max-w-[140px]">{c.label}</span>
          <button
            type="button"
            onClick={() => onRemove(c.key, c.value)}
            aria-label={`Remove ${c.label} filter`}
            className="tech-transition rounded-full p-0.5 hover:bg-primary/20"
          >
            <Icon name="X" className="h-2.5 w-2.5" />
          </button>
        </span>
      ))}
    </div>
  );
}

function Select({
  label,
  value,
  onChange,
  options,
  anyLabel,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
  anyLabel: string;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label={label}
      className={[
        "tech-transition h-7 rounded-md border bg-background pl-2 pr-6 text-xs focus:border-primary focus:outline-none",
        value ? "border-primary/50 text-foreground" : "border-border text-muted-foreground",
      ].join(" ")}
    >
      <option value="">{anyLabel}</option>
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}
