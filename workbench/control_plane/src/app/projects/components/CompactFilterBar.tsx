"use client";

/**
 * Projects · the compact filter chrome (WS-64 OP-1, `one_projects_app.md` §5).
 *
 * ONE tool row: a search field, the Filters button with the number of filters
 * that are on, and the View button. Below it, ONLY while a filter is on, one
 * line of removable chips that scrolls sideways and never wraps. Every other
 * control that `FilterBar` drew lives in `FilterSheet` and `ViewSheet`.
 *
 * It takes the props of `FilterBar` and adds a few, so the page swaps one
 * component for the other behind `compactChromeEnabled()`. With the flag off
 * the page draws `FilterBar`, unchanged.
 *
 * The search text is held locally and pushed up on a delay, as `FilterBar`
 * does: five keystrokes must not be five round trips.
 */

import { useEffect, useState } from "react";

import type { ModeOption } from "@/components/ModeSwitch";
import Icon from "@/components/Icon";
import ScrollStrip from "@/components/ScrollStrip";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

import { activeChips, foldedAgentText } from "../lib/compactChrome";
import { AGENT_STRIP, type AgentFocus, type AgentSummary } from "../lib/agentRuns";
import type { ViewMode } from "../lib/commands";
import { EMPTY_FILTERS, labelWith } from "../lib/grouping";
import { FilterBar, type Props as FilterBarProps } from "./FilterBar";
import { FilterSheet } from "./FilterSheet";
import { ViewSheet } from "./ViewSheet";

export type CompactFilterBarProps = FilterBarProps & {
  phone: boolean;
  modes: readonly ModeOption<ViewMode>[];
  onMode: (next: ViewMode) => void;
  /** How many tasks the filters match (the server total), for "Show N tasks". */
  resultCount: number | null;
  /** "100 of 219" when the list is not whole, else null. */
  loadedLabel: string | null;
  /** The agent counts, folded to one chip in the chip row on a phone. */
  agentSummary: AgentSummary | null;
  agentFocus: AgentFocus | null;
  onAgentFocus: (next: AgentFocus | null) => void;
};

export function CompactFilterBar(props: CompactFilterBarProps) {
  const {
    filters,
    onFilters,
    phone,
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
    loadedLabel,
    agentSummary,
    agentFocus,
    onAgentFocus,
  } = props;

  const [draft, setDraft] = useState(filters.q);
  useEffect(() => setDraft(filters.q), [filters.q]);
  useEffect(() => {
    if (draft === filters.q) return;
    const timer = setTimeout(() => onFilters({ ...filters, q: draft }), 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  const chips = activeChips({
    filters,
    waitingOnMe,
    agentWork,
    me,
    labelOf: labelWith(personLabels),
  });
  const folded = phone ? foldedAgentText(agentSummary) : "";
  const clearAll = () => {
    onFilters({ ...EMPTY_FILTERS, q: filters.q });
    onWaitingOnMe?.(false);
    onAgentWork?.(false);
  };

  return (
    <div className="@container border-b border-border px-3 py-2" data-compact-chrome="">
      <div className="flex items-center gap-2" data-tool-row="">
        {/* The search keeps a usable width in a narrow pane (768 px with
            the rail open), so the field never folds to a bare icon. */}
        <div className={phone ? "min-w-0 flex-1" : "min-w-[9rem] max-w-64 flex-1"}>
          <Input
            icon="Search"
            inputSize="sm"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-label="Search tasks"
            placeholder="Search tasks…"
          />
        </div>
        <FilterSheet
          filters={filters}
          onFilters={onFilters}
          me={me}
          people={people}
          personLabels={personLabels}
          tags={tags}
          archivedCount={archivedCount}
          waitingOnMe={waitingOnMe}
          onWaitingOnMe={onWaitingOnMe}
          agentWork={agentWork}
          onAgentWork={onAgentWork}
          resultCount={resultCount}
          activeCount={chips.length}
          onClearAll={clearAll}
          phone={phone}
        />
        <ViewSheet
          filters={filters}
          mode={props.mode}
          modes={props.modes}
          onMode={props.onMode}
          spansProjects={props.spansProjects}
          groupBy={props.groupBy}
          onGroupBy={props.onGroupBy}
          lanes={props.lanes}
          onSubGroupBy={props.onSubGroupBy}
          subtasks={props.subtasks}
          onSubtasks={props.onSubtasks}
          onWaitingOnMe={onWaitingOnMe}
          shownFields={props.shownFields}
          onShownFields={props.onShownFields}
          fields={props.fields}
          views={props.views}
          activeViewId={props.activeViewId}
          onApplyView={props.onApplyView}
          onSaveView={props.onSaveView}
          onDeleteView={props.onDeleteView}
          onUpdateView={props.onUpdateView}
          canSave={props.canSave}
          onExport={props.onExport}
          phone={phone}
        />
        {loadedLabel ? (
          <span
            // Below 22rem of pane (a phone is 24rem) the count yields first: it is a hint, and
            // the end-of-list row repeats it.
            className="hidden shrink-0 text-[11px] tabular-nums text-muted-foreground @[22rem]:inline"
            title="Tasks loaded of all tasks under these filters"
            data-loaded-of-total=""
          >
            {loadedLabel}
          </span>
        ) : null}
      </div>

      {chips.length > 0 || folded ? (
        <div className="mt-2" data-active-row="">
          <ScrollStrip label="Active filters" className="gap-1.5">
            {chips.map((chip) => (
              <Button
                key={chip.key}
                variant="secondary"
                size="sm"
                aria-label={`Remove filter ${chip.label}`}
                onClick={() => {
                  if (chip.clear.kind === "filters") {
                    onFilters({ ...filters, ...chip.clear.patch });
                  } else if (chip.clear.kind === "waitingOnMe") {
                    onWaitingOnMe?.(false);
                  } else {
                    onAgentWork?.(false);
                  }
                }}
              >
                {chip.label}
                <Icon name="X" size={11} />
              </Button>
            ))}
            {folded ? (
              <Button
                variant={agentFocus ? "primary" : "secondary"}
                size="sm"
                icon="Bot"
                aria-pressed={Boolean(agentFocus)}
                title="Agent work in this project. Press to show the first group."
                onClick={() => onAgentFocus(agentFocus ? null : firstFocus(agentSummary))}
              >
                {folded}
              </Button>
            ) : null}
            {chips.length > 0 ? (
              <Button variant="ghost" size="sm" onClick={clearAll}>
                Clear
              </Button>
            ) : null}
          </ScrollStrip>
        </div>
      ) : null}
    </div>
  );
}

/** The first count above zero, in the strip's own order. */
function firstFocus(summary: AgentSummary | null): AgentFocus | null {
  return AGENT_STRIP.find((item) => (summary?.[item.field] ?? 0) > 0)?.focus ?? null;
}

export default CompactFilterBar;

/**
 * The page mounts this one. With `compact` off it draws the old `FilterBar`
 * with the props it always had, so the flag-off page is unchanged.
 */
export function FilterChrome({
  compact,
  ...props
}: CompactFilterBarProps & { compact: boolean }) {
  if (compact) return <CompactFilterBar {...props} />;
  const {
    phone: _phone,
    modes: _modes,
    onMode: _onMode,
    resultCount: _resultCount,
    loadedLabel: _loadedLabel,
    agentSummary: _agentSummary,
    agentFocus: _agentFocus,
    onAgentFocus: _onAgentFocus,
    ...legacy
  } = props;
  return <FilterBar {...legacy} />;
}
