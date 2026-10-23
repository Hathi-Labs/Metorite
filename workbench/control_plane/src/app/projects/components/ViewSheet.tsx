"use client";

/**
 * Projects · the View sheet (WS-64 OP-1, `one_projects_app.md` §5.2 item 3).
 *
 * It holds every DISPLAY control that the old `FilterBar` row carried: the
 * canvas (Board, List, Table, Calendar), Group by, Lanes, Subtasks, Fields,
 * Export, and the saved views with Save view. Its help text carries the note
 * that lane counts and grouping describe what is loaded (§5.2 item 6).
 *
 * `ViewSheetBody` is the controls alone, so the markup test renders it without
 * a Modal. `ViewSheet` is the button and its surface.
 */

import { useState } from "react";

import Icon from "@/components/Icon";
import { ModeSwitch, type ModeOption } from "@/components/ModeSwitch";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Input } from "@/components/ui/Input";
import SelectButton, { OFF_DEFAULT } from "@/components/ui/SelectButton";

import { type ViewMode, honoursGroupBy, honoursLanes } from "../lib/commands";
import {
  DEFAULT_GROUP_BY,
  GROUP_OPTIONS,
  type GroupBy,
  type SubtaskMode,
  describeDivergence,
  isFiltered,
  viewDivergence,
} from "../lib/grouping";
import {
  DEFAULT_SHOWN,
  FIELD_KEYS,
  FIELD_LABELS,
  customFieldKey,
  sameFieldSet,
  toggleField,
} from "../lib/shownFields";
import { SUBTASK_MODE_LABELS, defaultSubtaskMode, subtaskModesFor } from "../lib/subtaskView";
import {
  AT_DEFAULT,
  GROUP_OPTION_LABELS,
  LANE_OPTION_LABELS,
  type Props as FilterBarProps,
} from "./FilterBar";
import { SheetButton } from "./SheetButton";

/** The words under the controls: what the counts on the board describe. */
export const LOADED_NOTE = "Lane counts and grouping describe what is loaded.";

export type ViewSheetProps = Pick<
  FilterBarProps,
  | "filters"
  | "mode"
  | "spansProjects"
  | "groupBy"
  | "onGroupBy"
  | "lanes"
  | "onSubGroupBy"
  | "subtasks"
  | "onSubtasks"
  | "onWaitingOnMe"
  | "shownFields"
  | "onShownFields"
  | "fields"
  | "views"
  | "activeViewId"
  | "onApplyView"
  | "onSaveView"
  | "onDeleteView"
  | "onUpdateView"
  | "canSave"
  | "onExport"
> & {
  modes: readonly ModeOption<ViewMode>[];
  onMode: (next: ViewMode) => void;
};

export function ViewSheetBody({
  filters,
  mode,
  modes,
  onMode,
  spansProjects,
  groupBy,
  onGroupBy,
  lanes,
  onSubGroupBy,
  subtasks,
  onSubtasks,
  onWaitingOnMe,
  shownFields,
  onShownFields,
  fields,
  views,
  activeViewId,
  onApplyView,
  onSaveView,
  onDeleteView,
  onUpdateView,
  canSave,
  onExport,
}: ViewSheetProps) {
  const subGroupBy = lanes.subGroupBy;
  const [naming, setNaming] = useState(false);
  const [viewName, setViewName] = useState("");
  const [pickingFields, setPickingFields] = useState(false);
  const [exporting, setExporting] = useState(false);

  const axisOffered = (option: GroupBy, current: GroupBy): boolean =>
    (option !== "project" || spansProjects || current === "project") &&
    (option !== "agent" || Boolean(onWaitingOnMe) || current === "agent");

  const activeView = views.find((view) => view.id === activeViewId) ?? null;
  const drift = activeView
    ? viewDivergence({ filters, groupBy, lanes, shownFields }, activeView.config)
    : { dirty: false, changed: [] as never[] };
  const untouched =
    !isFiltered(filters) &&
    groupBy === "status" &&
    subGroupBy === "none" &&
    sameFieldSet(shownFields, DEFAULT_SHOWN);

  return (
    <div className="flex flex-col gap-3" data-view-sheet="">
      <ModeSwitch modes={modes} mode={mode} layout="toolbar" onPick={onMode} />

      {honoursGroupBy(mode) ? (
        <SelectButton
          label="Group by"
          widthClass="w-full"
          className={groupBy === DEFAULT_GROUP_BY ? AT_DEFAULT : OFF_DEFAULT}
          value={groupBy}
          defaultValue={DEFAULT_GROUP_BY}
          onChange={(next) => onGroupBy(next as GroupBy)}
          options={GROUP_OPTIONS.filter((option) => axisOffered(option, groupBy)).map(
            (option) => ({ value: option, label: GROUP_OPTION_LABELS[option] })
          )}
        />
      ) : null}

      {honoursLanes(mode) ? (
        <SelectButton
          label="Sub-group by (swimlanes)"
          widthClass="w-full"
          className={
            (subGroupBy === groupBy ? "none" : subGroupBy) === "none" ? AT_DEFAULT : OFF_DEFAULT
          }
          value={subGroupBy === groupBy ? "none" : subGroupBy}
          defaultValue="none"
          onChange={(next) => onSubGroupBy(next as GroupBy)}
          options={GROUP_OPTIONS.filter(
            (option) =>
              (option === "none" || option !== groupBy) && axisOffered(option, subGroupBy)
          ).map((option) => ({ value: option, label: LANE_OPTION_LABELS[option] }))}
        />
      ) : null}

      {subtaskModesFor(mode).length > 0 ? (
        <SelectButton
          label="Subtasks"
          widthClass="w-full"
          className={subtasks === defaultSubtaskMode(mode) ? AT_DEFAULT : OFF_DEFAULT}
          value={subtasks}
          defaultValue={defaultSubtaskMode(mode)}
          onChange={(next) => onSubtasks(next as SubtaskMode)}
          options={subtaskModesFor(mode).map((option) => ({
            value: option,
            label: SUBTASK_MODE_LABELS[option],
          }))}
        />
      ) : null}

      <div className="flex flex-col gap-1">
        <Button
          variant={sameFieldSet(shownFields, DEFAULT_SHOWN) ? "secondary" : "primary"}
          size="sm"
          icon="Columns3"
          aria-expanded={pickingFields}
          onClick={() => setPickingFields((now) => !now)}
        >
          Fields
        </Button>
        {pickingFields ? (
          <div className="rounded-md border border-border p-2">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[11px] font-medium text-foreground">Shown fields</span>
              <Button
                variant="text"
                size="sm"
                disabled={sameFieldSet(shownFields, DEFAULT_SHOWN)}
                onClick={() => onShownFields([...DEFAULT_SHOWN])}
              >
                Reset
              </Button>
            </div>
            <div className="max-h-48 space-y-0.5 overflow-y-auto">
              {[
                ...FIELD_KEYS.map((key) => ({ key, label: FIELD_LABELS[key] })),
                ...fields.map((def) => ({ key: customFieldKey(def), label: def.name })),
              ].map(({ key, label }) => (
                <label
                  key={key}
                  className="flex items-center gap-2 rounded px-1 py-0.5 text-xs text-foreground hover:bg-muted"
                >
                  <Checkbox
                    checked={shownFields.includes(key)}
                    onChange={() => onShownFields(toggleField(shownFields, key))}
                    aria-label={`Show ${label}`}
                  />
                  {label}
                </label>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <Button
        variant="secondary"
        size="sm"
        icon="Download"
        disabled={exporting}
        title="Export these filters and columns as a CSV"
        onClick={async () => {
          setExporting(true);
          try {
            await onExport();
          } finally {
            setExporting(false);
          }
        }}
      >
        {exporting ? "Exporting…" : "Export"}
      </Button>

      <div className="flex flex-col gap-1.5 border-t border-border pt-3">
        <span className="text-[11px] font-medium text-muted-foreground">Saved views</span>
        <div className="flex flex-wrap items-center gap-1">
          {views.map((view) => (
            <span key={view.id} className="inline-flex items-center">
              <Button
                variant="ghost"
                size="none"
                radius="keep"
                selected={view.id === activeViewId}
                onClick={() => onApplyView(view)}
                className="rounded-l-md px-2 py-1 text-xs"
              >
                {view.name}
                {view.id === activeViewId && drift.dirty ? (
                  <span
                    className="ml-1 align-middle text-[10px]"
                    aria-label="edited since it was saved"
                    title="Edited since it was saved"
                  >
                    ●
                  </span>
                ) : null}
              </Button>
              <button
                type="button"
                aria-label={`Delete view ${view.name}`}
                title={`Delete view ${view.name}`}
                onClick={() => onDeleteView(view)}
                className="rounded-r-md px-1 py-1 text-muted-foreground hover:bg-muted"
              >
                <Icon name="X" size={11} />
              </button>
            </span>
          ))}
        </div>

        {naming ? (
          <form
            className="flex items-center gap-1"
            onSubmit={(event) => {
              event.preventDefault();
              const name = viewName.trim();
              if (!name) return;
              onSaveView(name);
              setViewName("");
              setNaming(false);
            }}
          >
            <Input
              autoFocus
              inputSize="sm"
              value={viewName}
              onChange={(e) => setViewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setNaming(false);
              }}
              placeholder="View name"
              aria-label="View name"
            />
            <Button type="submit" size="sm">
              Save
            </Button>
          </form>
        ) : canSave ? (
          <Button
            variant="text"
            size="sm"
            icon="Bookmark"
            disabled={untouched}
            title={
              untouched
                ? "Filter, regroup or change the fields first — an untouched view is the board"
                : "Save these filters as a view"
            }
            onClick={() => setNaming(true)}
          >
            Save view
          </Button>
        ) : null}

        {activeView && drift.dirty ? (
          <div className="flex flex-col gap-1.5 rounded-md border border-border bg-muted px-2 py-1.5">
            <p className="text-[11px] text-foreground">
              <span className="font-medium">{activeView.name}</span> has unsaved changes to
              its {describeDivergence(drift.changed)}.
            </p>
            <div className="flex flex-wrap items-center gap-1.5">
              <Button size="sm" icon="Save" onClick={() => onUpdateView(activeView)}>
                Update view
              </Button>
              <Button
                variant="secondary"
                size="sm"
                icon="Plus"
                disabled={!canSave}
                onClick={() => {
                  setViewName(`${activeView.name} copy`);
                  setNaming(true);
                }}
              >
                Save as new
              </Button>
              <Button
                variant="ghost"
                size="sm"
                icon="RotateCcw"
                title={`Go back to ${activeView.name} as it was saved`}
                onClick={() => onApplyView(activeView)}
              >
                Reset
              </Button>
            </div>
          </div>
        ) : null}
      </div>

      <p className="text-[11px] text-muted-foreground">{LOADED_NOTE}</p>
    </div>
  );
}

export function ViewSheet(props: ViewSheetProps & { phone: boolean }) {
  const { phone, ...rest } = props;
  return (
    <SheetButton label="View" title="View" icon="SlidersHorizontal" phone={phone}>
      {() => <ViewSheetBody {...rest} />}
    </SheetButton>
  );
}

export default ViewSheet;
