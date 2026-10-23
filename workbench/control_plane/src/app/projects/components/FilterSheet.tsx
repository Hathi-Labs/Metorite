"use client";

/**
 * Projects · the Filters sheet (WS-64 OP-1, `one_projects_app.md` §5.2 item 2).
 *
 * It holds every FILTER control that the old `FilterBar` row carried: stage,
 * assignee, Overdue, Watching, Waiting on me, Agent work, Archived, and the
 * tags with their counts. It has Clear and "Show N tasks". The display controls
 * (grouping, lanes, fields, export, saved views) are in `ViewSheet.tsx`.
 *
 * `FilterSheetBody` is the controls alone, so the markup test renders it
 * without a Modal. `FilterSheet` is the button and its surface.
 *
 * The controls are the ones the old bar used: `SelectButton`, `Button` with
 * `aria-pressed`, and the tag button. Nothing here is a second look.
 */

import Button from "@/components/ui/Button";
import SelectButton, { OFF_DEFAULT } from "@/components/ui/SelectButton";

import type { TagRow } from "../lib/api";
import { filtersButtonLabel } from "../lib/compactChrome";
import { type Filters, UNSET, labelWith } from "../lib/grouping";
import { byUsage, chipClass } from "../lib/tags";
import { AT_DEFAULT, CATEGORIES } from "./FilterBar";
import { SheetButton } from "./SheetButton";

export interface FilterSheetProps {
  filters: Filters;
  onFilters: (next: Filters) => void;
  me: string;
  people: readonly string[];
  personLabels?: ReadonlyMap<string, string>;
  tags: TagRow[];
  archivedCount?: number | null;
  waitingOnMe?: boolean;
  onWaitingOnMe?: (next: boolean) => void;
  agentWork?: boolean;
  onAgentWork?: (next: boolean) => void;
  /** How many tasks the filters now match, for "Show N tasks". */
  resultCount: number | null;
  /** How many filters are on, for the button and for Clear. */
  activeCount: number;
  /** Clears the filters AND the two page toggles. */
  onClearAll: () => void;
}

export function FilterSheetBody({
  filters,
  onFilters,
  me,
  people,
  personLabels,
  tags,
  archivedCount,
  waitingOnMe = false,
  onWaitingOnMe,
  agentWork = false,
  onAgentWork,
  resultCount,
  activeCount,
  onClearAll,
  onDone,
}: FilterSheetProps & { onDone: () => void }) {
  const set = (patch: Partial<Filters>) => onFilters({ ...filters, ...patch });

  // A saved view can name somebody who holds nothing now. Without an option the
  // control would read "Anyone" while the filter still applies.
  const chosen = filters.assignee.trim();
  const orphanAssignee =
    chosen &&
    chosen.toLowerCase() !== me.toLowerCase() &&
    !people.some((who) => who.toLowerCase() === chosen.toLowerCase())
      ? chosen
      : null;

  return (
    <div className="flex flex-col gap-3" data-filter-sheet="">
      <SelectButton
        label="Stage"
        widthClass="w-full"
        className={filters.statusCategory ? OFF_DEFAULT : AT_DEFAULT}
        value={filters.statusCategory}
        onChange={(next) => set({ statusCategory: next })}
        options={CATEGORIES.map(([value, label]) => ({ value, label }))}
      />
      <SelectButton
        label="Assignee"
        widthClass="w-full"
        className={filters.unassigned || filters.assignee ? OFF_DEFAULT : AT_DEFAULT}
        value={filters.unassigned ? UNSET : filters.assignee}
        onChange={(picked) =>
          set(
            picked === UNSET
              ? { assignee: "", unassigned: true }
              : { assignee: picked, unassigned: false }
          )
        }
        options={[
          { value: "", label: "Anyone" },
          ...(me ? [{ value: me, label: "Me" }] : []),
          { value: UNSET, label: "Unassigned" },
          ...people.map((who) => ({ value: who, label: labelWith(personLabels)(who) })),
          ...(orphanAssignee
            ? [{ value: orphanAssignee, label: labelWith(personLabels)(orphanAssignee) }]
            : []),
        ]}
      />

      <div className="flex flex-wrap gap-2" role="group" aria-label="Quick filters">
        <Button
          variant={filters.overdue ? "primary" : "secondary"}
          size="sm"
          aria-pressed={filters.overdue}
          onClick={() => set({ overdue: !filters.overdue })}
        >
          Overdue
        </Button>
        <Button
          variant={filters.watching ? "primary" : "secondary"}
          size="sm"
          icon="Eye"
          aria-pressed={filters.watching}
          onClick={() => set({ watching: !filters.watching })}
        >
          Watching
        </Button>
        {onWaitingOnMe ? (
          <Button
            variant={waitingOnMe ? "primary" : "secondary"}
            size="sm"
            icon="MessageCircleQuestion"
            aria-pressed={waitingOnMe}
            title="Tasks where an agent waits for your answer"
            onClick={() => onWaitingOnMe(!waitingOnMe)}
          >
            Waiting on me
          </Button>
        ) : null}
        {onAgentWork ? (
          <Button
            variant={agentWork ? "primary" : "secondary"}
            size="sm"
            icon="Bot"
            aria-pressed={agentWork}
            title="Tasks that an agent worked on: a run, or an act through an agent"
            onClick={() => onAgentWork(!agentWork)}
          >
            Agent work
          </Button>
        ) : null}
        <Button
          variant={filters.archived ? "primary" : "secondary"}
          size="sm"
          icon="Archive"
          aria-pressed={filters.archived}
          aria-label={
            typeof archivedCount === "number"
              ? `Archived, ${archivedCount} task${archivedCount === 1 ? "" : "s"}`
              : "Archived"
          }
          title={
            filters.archived
              ? "Showing only archived tasks"
              : "Show the archive — filed tasks, which every other view hides"
          }
          onClick={() => set({ archived: !filters.archived })}
        >
          Archived
          {typeof archivedCount === "number" && archivedCount > 0 ? (
            <span className="ml-1 opacity-70">{archivedCount}</span>
          ) : null}
        </Button>
      </div>

      {tags.length ? (
        <div className="flex flex-col gap-1.5">
          <span className="text-[11px] font-medium text-muted-foreground">Tags</span>
          <div className="flex flex-wrap items-center gap-1">
            {byUsage(tags)
              .slice(0, 12)
              .map((tag) => {
                const on = filters.tags.includes(tag.name);
                return (
                  <button
                    key={tag.id}
                    type="button"
                    aria-pressed={on}
                    onClick={() =>
                      set({
                        tags: on
                          ? filters.tags.filter((name) => name !== tag.name)
                          : [...filters.tags, tag.name],
                      })
                    }
                    className={`rounded-md px-1.5 py-0.5 text-[11px] ${
                      on ? "bg-primary/10 text-primary" : chipClass(tag.color)
                    }`}
                  >
                    {tag.name}
                    <span className="ml-1 opacity-70">{tag.task_count ?? 0}</span>
                  </button>
                );
              })}
          </div>
        </div>
      ) : null}

      <div className="flex items-center justify-between gap-2 border-t border-border pt-3">
        <Button
          variant="ghost"
          size="sm"
          icon="X"
          disabled={activeCount === 0}
          onClick={onClearAll}
        >
          Clear
        </Button>
        <Button size="sm" onClick={onDone}>
          {resultCount === null
            ? "Show tasks"
            : `Show ${resultCount} ${resultCount === 1 ? "task" : "tasks"}`}
        </Button>
      </div>
    </div>
  );
}

export function FilterSheet(props: FilterSheetProps & { phone: boolean }) {
  const { phone, ...rest } = props;
  return (
    <SheetButton
      label={filtersButtonLabel(props.activeCount)}
      title="Filters"
      icon="Filter"
      phone={phone}
      highlighted={props.activeCount > 0}
    >
      {(close) => <FilterSheetBody {...rest} onDone={close} />}
    </SheetButton>
  );
}
export default FilterSheet;
