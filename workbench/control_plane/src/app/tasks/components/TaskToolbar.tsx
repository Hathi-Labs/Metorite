"use client";

import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Input } from "@/components/ui/Input";
import SelectButton, { OFF_DEFAULT } from "@/components/ui/SelectButton";
import Icon from "@/components/Icon";
import type { ThemedIcon } from "@/components/Icon";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";
import { searchOpen } from "@/app/projects/lib/grouping";
import { useMemo, useRef, useState, useEffect } from "react";
import { MyTask } from "../lib/types";
import { useTaskStore } from "../lib/taskStore";
import {
  filtersActive,
  activeFilterCount,
  NO_CONTEXT_FACET,
  NO_ENERGY_FACET,
  SORT_LABEL,
  DEFAULT_SORT,
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
//   [Search icon]  [Filter (N)] [chips…]        [Group by]  [Sort] [⇅]
// Built from the pieces Projects' `FilterBar` uses (2026-09-24): the search is
// an icon until asked for, then an `Input`; each axis is a `SelectButton`;
// every toggle is a `Button`. The look is Projects'. The options and what they
// do are My Tasks' own, unchanged.
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

// Each option says what it DOES, as Projects' "Group by …" options do, so the
// control needs no label beside it.
const GROUP_LABEL: Record<GroupBy | "", string> = {
  // "Stage", never "Status". The default groups Next Actions by the status
  // CATEGORY (`nextCategoryOf`), which Projects calls a stage ("Group by
  // stage"). A status is one lane's own name, and this is not that.
  "": "Group by stage",
  none: "No grouping",
  context: "Group by context",
  // D78: one priority system, the matrix, in both apps.
  priority: "Group by priority",
  mode: "Group by suggestion",
  energy: "Group by energy",
  depth: "Group by work mode",
};

/** A sort option's text. "Manual order" is not a sort BY anything. */
const sortOptionLabel = (f: SortField): string =>
  f === "manual" ? "Manual order" : `Sort by ${SORT_LABEL[f].toLowerCase()}`;

const GROUP_OPTIONS: (GroupBy | "")[] = [
  "", "context", "priority", "mode", "energy", "depth", "none",
];

const ENERGY_VALUES: { value: string; label: string }[] = [
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
  { value: NO_ENERGY_FACET, label: "No energy set" },
];

export function TaskToolbar({ items }: { items: MyTask[] }) {
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

  // The search is an icon until it is asked for (Projects' H-94 direction 3).
  // `opened` is the TOGGLE, never the answer: `searchOpen` keeps the field on
  // screen while it holds text, whatever this says.
  const [opened, setOpened] = useState(false);
  const searchField = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (opened) searchField.current?.focus();
  }, [opened]);
  const searching = searchOpen({ opened, draft: filters.query });

  // Toggle a value in a multi-select facet.
  const toggle = (key: FacetKey, value: string) => {
    const cur = filters[key] ?? [];
    const next = cur.includes(value)
      ? cur.filter((v) => v !== value)
      : [...cur, value];
    setFilters({ [key]: next } as Partial<TaskFilters>);
  };

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border bg-card px-4 py-2">
      {/* Search — collapsed to its icon, as in Projects' filter row. A
          fixed width, so nothing beside it moves when it opens. */}
      {searching ? (
        <div className="w-64">
          <Input
            ref={searchField}
            icon="Search"
            inputSize="sm"
            value={filters.query}
            onChange={(e) => setFilters({ query: e.target.value })}
            aria-label="Search tasks"
            placeholder="Search tasks…"
            onKeyDown={(e) => {
              if (e.key === "Escape") setOpened(false);
            }}
            onBlur={() => setOpened(false)}
          />
        </div>
      ) : (
        <Button
          variant="secondary"
          size="icon-sm"
          icon="Search"
          aria-label="Search tasks"
          aria-expanded={false}
          title="Search titles and notes"
          onClick={() => setOpened(true)}
        />
      )}

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
        <SelectButton
          label="Assignee"
          widthClass="w-40"
          className={filters.assignee ? OFF_DEFAULT : ""}
          value={filters.assignee}
          onChange={(v) => setFilters({ assignee: v })}
          options={[
            { value: "", label: "Anyone" },
            ...assigneeOpts.map((o) => ({ value: o, label: o })),
          ]}
        />
      )}

      {/* Active-facet chips — the current filter state, always visible + removable. */}
      <FacetChips
        filters={filters}
        priorityMeta={priorityOpts}
        onRemove={(key, value) => toggle(key, value)}
      />

      {active && (
        <Button variant="ghost" size="sm" icon="X" onClick={clearFilters}>
          Clear{nFacets > 1 ? " all" : ""}
        </Button>
      )}

      {/* Group-by + Sort — pushed right */}
      <div className="ml-auto flex items-center gap-2">
        <SelectButton
          label="Group by"
          widthClass="w-[11rem]"
          className={groupBy ? OFF_DEFAULT : ""}
          value={groupBy}
          defaultValue=""
          onChange={(v) => setGroupBy(v as GroupBy | "")}
          options={GROUP_OPTIONS.map((g) => ({ value: g, label: GROUP_LABEL[g] }))}
        />
        {showSort && (
          <div className="flex items-center gap-1">
            <SelectButton
              label="Sort by"
              widthClass="w-[10rem]"
              value={sort.field}
              defaultValue={DEFAULT_SORT.field}
              className={sort.field === DEFAULT_SORT.field ? "" : OFF_DEFAULT}
              onChange={(v) => setSort({ field: v as SortField })}
              options={SORT_FIELDS.map((f) => ({ value: f, label: sortOptionLabel(f) }))}
            />
            <Button
              variant="secondary"
              size="icon-sm"
              icon={sort.dir === "asc" ? "ArrowUpNarrowWide" : "ArrowDownWideNarrow"}
              onClick={() => setSort({ dir: sort.dir === "asc" ? "desc" : "asc" })}
              disabled={sort.field === "manual"}
              aria-label={sort.dir === "asc" ? "Ascending" : "Descending"}
              title={
                sort.field === "manual"
                  ? "Manual order (drag cards to reorder)"
                  : sort.dir === "asc"
                    ? "Ascending — click for descending"
                    : "Descending — click for ascending"
              }
            />
          </div>
        )}
      </div>
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
/** The multi-select facets. */
type FacetKey = "contexts" | "priorities" | "energies";

interface FacetSection {
  key: FacetKey;
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

  // Dismiss through the shared walker (`lib/outsideClick.ts`), as every
  // popover in the filter rows does.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (shouldDismiss(e.target as Element | null, domClickWalk(ref.current))) setOpen(false);
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
      {/* Projects' Fields button: `secondary` at rest, `primary` once it is
          doing something, and the count after the label. */}
      <Button
        variant={count > 0 ? "primary" : "secondary"}
        size="sm"
        icon="ListFilter"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        Filter
        {count > 0 ? <span className="ml-1 opacity-70">{count}</span> : null}
      </Button>
      {open && (
        <div className="absolute left-0 z-40 mt-1 max-h-[70vh] w-64 overflow-y-auto rounded-lg border border-border bg-popover p-2 shadow-md">
          {usable.map((s, si) => (
            <div key={s.key} className={si > 0 ? "mt-2 border-t border-border pt-2" : undefined}>
              <p className="px-1 pb-1 text-[11px] font-medium text-foreground">
                {s.label}
              </p>
              {s.options.map((o) => {
                const on = s.selected.includes(o.value);
                return (
                  <label
                    key={o.value}
                    className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-xs text-foreground hover:bg-muted"
                  >
                    <Checkbox
                      checked={on}
                      onChange={() => onToggle(s.key, o.value)}
                      aria-label={`${s.label}: ${o.label}`}
                    />
                    {o.icon ? (
                      <o.icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                    ) : o.dot ? (
                      <span className={`h-2 w-2 shrink-0 rounded-full ${o.dot}`} aria-hidden />
                    ) : o.emoji ? (
                      <span aria-hidden>{o.emoji}</span>
                    ) : null}
                    <span className="min-w-0 flex-1 truncate">{o.label}</span>
                    <span className="shrink-0 text-[10px] text-muted-foreground">{o.count}</span>
                  </label>
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
  key: FacetKey;
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
  onRemove: (key: FacetKey, value: string) => void;
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
