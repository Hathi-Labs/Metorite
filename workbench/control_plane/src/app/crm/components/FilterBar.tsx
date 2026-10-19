"use client";

/**
 * The CRM filter row — row 2 of the two-row toolbar (WS-53 CRM-U8).
 *
 * Row 1 says WHICH view (`ViewChips`). This row says WHAT is filtered:
 *
 *   Search · Stage · Owner · one chip for each filter in force · + Filter ·
 *   Clear · "23 deals"
 *
 * The search box is a list only (CRM-X1). The Stage control is a list only,
 * because the lanes of a board are the stages. A chip reopens the editor of
 * its field and has a remove button. "+ Filter" opens one panel that lists
 * every field the member can filter by, in groups, with a search box.
 * Picking a field opens its editor in the same panel. The editors are the
 * controls of the old second row: a choice list for a picklist, two days for
 * a range, a text box for one number or one day (`EqualsFilter`).
 *
 * On a phone the row is Search and one "Filters (n)" button. The button
 * opens a sheet with the same Stage, Owner, chips and field list.
 *
 * The choices live in `lib/filterBar.ts` and `lib/filterFields.ts`. This file
 * is markup and local state only. It adds no second look: the house `Button`,
 * `SelectButton` and `Input`, and the `AnchoredPanel` surface.
 */

import { useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import AnchoredPanel from "@/components/ui/AnchoredPanel";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import SelectButton, { OFF_DEFAULT } from "@/components/ui/SelectButton";
import { useViewMode } from "@/components/ViewModeProvider";
import { useDismiss } from "@/hooks/useDismiss";
import { filterWord } from "@/lib/shell/registry";

import type { FieldDef } from "../lib/fields";
import {
  ANY,
  SOURCE_OPTIONS,
  booleanFilterOptions,
  customFilters,
  dateFilterValue,
  numberFilterValue,
  optionsOf,
  ownerOptions,
  picklistDefs,
  searchBoxStart,
  searchBoxSync,
  searchBoxWrote,
  searchChanges,
  searchCommitter,
  setCustom,
  setRange,
  type CrmOwner,
  type CustomFilter,
} from "../lib/filterBar";
import {
  activeFilterChips,
  anyFilter,
  fieldFillText,
  filterFields,
  filtersButtonLabel,
  filtersInForce,
  groupFields,
  removeFilter,
  stageOptions,
  type FilterField,
} from "../lib/filterFields";
import type { Chip } from "../lib/filters";
import { clearFilters } from "../lib/savedViews";
import type { EntitySlug } from "../lib/types";
import type { CrmView } from "../lib/urlState";
import { useFieldLayout } from "../lib/useCrmMode";
import { viewLabel, type SourceLabels } from "../lib/views";

export type FilterBarProps = {
  /** The list's entity. `null` for a generic module list, which has no field filter. */
  entity: EntitySlug | null;
  /**
   * The source's names of the core entities (CRM-Q17a-2 item 7). The search
   * box names the list as the rail does, so "Existing Sites" searches as
   * "Filter existing sites", never as "contacts".
   */
  labels?: SourceLabels | null;
  /** The name of the list for the search box and the count, when `entity` is null. */
  noun?: string;
  view: CrmView;
  onChange: (next: CrmView) => void;
  /**
   * The owners of `GET /crm/owners` (CRM-X1): each active member, and each
   * record owner who is not a member. `null` while unread or on a failure.
   */
  owners?: ReadonlyArray<CrmOwner> | null;
  /** Draw the Owner control. A module list has no owner filter. */
  showOwner?: boolean;
  /**
   * Draw the search box (CRM-X1). A list only: the board read takes no `q`,
   * so a box on a board would change nothing.
   */
  search?: boolean;
  /** The stage chips of the list (`chipsFor`). With none, no Stage control. */
  stages?: ReadonlyArray<Chip>;
  fieldDefs: FieldDef[] | null;
  /** Mirror mode: a synced record keeps no created date, so the range is hidden. */
  mirror?: boolean;
  /** Filters dropped before the request, by name. */
  removed?: string[];
  /** The custom filters could not be checked, so the list was not read. */
  unchecked?: boolean;
  /** The records the list holds under the filters. Omit it on a board. */
  count?: number | null;
};

export default function FilterBar({
  entity,
  labels = null,
  noun,
  view,
  onChange,
  owners = null,
  showOwner = true,
  search = false,
  stages = [],
  fieldDefs,
  mirror = false,
  removed = [],
  unchecked = false,
  count = null,
}: FilterBarProps) {
  const { isMobile } = useViewMode();
  // CRM-C4b: with the studio on, the bar reads the layout and offers more
  // types. With it off, it offers the picklists of today.
  const { layout } = useFieldLayout(entity);
  const custom: CustomFilter[] = !entity
    ? []
    : layout?.studio === true
      ? customFilters(layout.fields, true)
      : picklistDefs(fieldDefs).map((def) => ({ def, kind: "options" }));
  const fields = entity ? filterFields(entity, custom, mirror) : [];
  const chips = activeFilterChips(view, fields);
  const listNoun = entity ? viewLabel(entity, labels) : (noun ?? "records");
  const stageName = entity === "leads" ? "Status" : "Stage";
  const stageChoices = stageOptions(stages, stageName);
  const showStage = stageChoices.length > 0;

  // The panel: where it hangs, and what it shows. `list` is the field list.
  const [panel, setPanel] = useState<{ anchor: HTMLElement; step: string } | null>(null);
  const [sheet, setSheet] = useState(false);
  const [sheetStep, setSheetStep] = useState("list");
  const root = useRef<HTMLDivElement | null>(null);
  useDismiss(panel !== null, () => setPanel(null), root);

  const stage = showStage ? (
    <SelectButton
      label={stageName}
      widthClass="w-36"
      className={view.statusId ? OFF_DEFAULT : ""}
      value={view.statusId ?? ANY}
      defaultValue={ANY}
      onChange={(next) => onChange({ ...view, statusId: next || null })}
      options={stageChoices}
    />
  ) : null;

  const owner = showOwner ? (
    <SelectButton
      label="Owner"
      widthClass="w-40"
      className={view.owner ? OFF_DEFAULT : ""}
      value={view.owner ?? ANY}
      defaultValue={ANY}
      onChange={(next) => onChange({ ...view, owner: next || null })}
      options={ownerOptions(owners, view.owner)}
    />
  ) : null;

  const searchBox = search ? (
    <SearchBox
      label={listNoun.toLowerCase()}
      value={view.q}
      onCommit={(q) => {
        if (searchChanges(q, view.q)) onChange({ ...view, q });
      }}
    />
  ) : null;

  const notes = (
    <>
      {removed.length > 0 ? (
        <p role="status" className="mt-1 text-[11px] text-muted-foreground">
          A filter was removed: {removed.join(", ")} is not available here.
        </p>
      ) : null}
      {unchecked ? (
        <p role="status" className="mt-1 text-[11px] text-destructive">
          Could not check the custom filters. Reload to try again.
        </p>
      ) : null}
    </>
  );

  const countText =
    count !== null && count !== undefined ? (
      <span className="ml-auto shrink-0 text-xs text-muted-foreground" data-testid="crm-count">
        {count.toLocaleString("en-US")} {listNoun.toLowerCase()}
      </span>
    ) : null;

  // ── The phone: Search and one "Filters (n)" button ────────────────────────
  if (isMobile) {
    return (
      <div className="shrink-0 border-b border-border px-3 py-2" data-testid="crm-filter-bar">
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1 [&_input]:w-full">{searchBox}</div>
          {entity || showStage ? (
            <Button
              variant="ghost"
              size="sm"
              icon="SlidersHorizontal"
              selected={filtersInForce(view, chips) > 0}
              onClick={() => {
                setSheetStep("list");
                setSheet(true);
              }}
            >
              {filtersButtonLabel(filtersInForce(view, chips))}
            </Button>
          ) : null}
        </div>
        {notes}
        <Modal
          open={sheet}
          onClose={() => setSheet(false)}
          title="Filters"
          icon="SlidersHorizontal"
          placement="sheet"
        >
          <div className="flex flex-col gap-3 overflow-y-auto p-3">
            <div className="flex flex-wrap items-center gap-2">
              {stage}
              {owner}
            </div>
            <div className="flex flex-wrap items-center gap-1">
              <ChipRow
                chips={chips}
                onOpen={(id) => setSheetStep(id)}
                onRemove={(id) => onChange(removeFilter(view, id))}
              />
            </div>
            <FieldPanel
              fields={fields}
              step={sheetStep}
              setStep={setSheetStep}
              view={view}
              onChange={onChange}
              onDone={() => setSheetStep("list")}
            />
            {anyFilter(view) ? (
              <Button
                variant="ghost"
                size="sm"
                icon="X"
                onClick={() => onChange(clearFilters(view))}
              >
                Clear
              </Button>
            ) : null}
          </div>
        </Modal>
      </div>
    );
  }

  // ── The desktop row ───────────────────────────────────────────────────────
  return (
    <div className="shrink-0 border-b border-border px-3 py-2 sm:px-4" data-testid="crm-filter-bar">
      <div ref={root} className="flex flex-wrap items-center gap-2">
        {searchBox}
        {stage}
        {owner}
        <ChipRow
          chips={chips}
          onOpen={(id, el) => setPanel({ anchor: el, step: id })}
          onRemove={(id) => {
            setPanel(null);
            onChange(removeFilter(view, id));
          }}
        />
        {fields.length > 0 ? (
          <Button
            variant="ghost"
            size="sm"
            icon="Plus"
            aria-haspopup="dialog"
            aria-expanded={panel?.step === "list"}
            onClick={(e) => {
              const el = e.currentTarget;
              setPanel((p) => (p?.step === "list" ? null : { anchor: el, step: "list" }));
            }}
          >
            Filter
          </Button>
        ) : null}
        {anyFilter(view) ? (
          <Button variant="ghost" size="sm" icon="X" onClick={() => onChange(clearFilters(view))}>
            Clear
          </Button>
        ) : null}
        {countText}
        <AnchoredPanel
          anchor={panel?.anchor ?? null}
          open={panel !== null}
          maxHeight={360}
          className="w-72"
          panelProps={{ role: "dialog", "aria-label": "Filter" }}
        >
          {panel ? (
            <FieldPanel
              fields={fields}
              step={panel.step}
              setStep={(step) => setPanel((p) => (p ? { ...p, step } : p))}
              view={view}
              onChange={onChange}
              onDone={() => setPanel(null)}
            />
          ) : null}
        </AnchoredPanel>
      </div>
      {notes}
    </div>
  );
}

/** The chips of the filters in force. A chip reopens its editor. The cross removes it. */
function ChipRow({
  chips,
  onOpen,
  onRemove,
}: {
  chips: ReadonlyArray<{ id: string; label: string }>;
  onOpen: (id: string, el: HTMLElement) => void;
  onRemove: (id: string) => void;
}) {
  return (
    <>
      {chips.map((chip) => (
        <span
          key={chip.id}
          className="inline-flex items-center rounded-md bg-secondary"
          data-testid="crm-filter-chip"
        >
          <Button
            variant="ghost"
            size="sm"
            title="Change this filter"
            onClick={(e) => onOpen(chip.id, e.currentTarget)}
          >
            {chip.label}
          </Button>
          <Button
            variant="ghost"
            size="icon-xs"
            icon="X"
            aria-label={`Remove ${chip.label}`}
            onClick={() => onRemove(chip.id)}
          />
        </span>
      ))}
    </>
  );
}

/**
 * What the "+ Filter" panel shows. Step `list` is the field list. Any other
 * step is the id of a field, and shows its editor.
 */
function FieldPanel({
  fields,
  step,
  setStep,
  view,
  onChange,
  onDone,
}: {
  fields: FilterField[];
  step: string;
  setStep: (step: string) => void;
  view: CrmView;
  onChange: (next: CrmView) => void;
  onDone: () => void;
}) {
  const [words, setWords] = useState("");
  const field = step === "list" ? null : (fields.find((f) => f.id === step) ?? null);

  if (!field) {
    const groups = groupFields(fields, words);
    return (
      <div className="flex flex-col gap-1 p-2">
        <Input
          autoFocus
          icon="Search"
          inputSize="sm"
          value={words}
          onChange={(e) => setWords(e.target.value)}
          placeholder="Find a field…"
          aria-label="Find a field"
        />
        {groups.length === 0 ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">No field matches.</p>
        ) : null}
        {groups.map(({ group, items }) => (
          <div key={group} role="group" aria-label={group} className="flex flex-col">
            <p className="px-2 pb-0.5 pt-2 text-[11px] font-medium uppercase text-muted-foreground">
              {group}
            </p>
            {items.map((item) => {
              const fill = fieldFillText(item);
              return (
                <Button
                  key={item.id}
                  variant="ghost"
                  size="sm"
                  className="w-full justify-between"
                  onClick={() => {
                    if (item.kind === "converted") {
                      onChange({ ...view, includeConverted: !view.includeConverted });
                      onDone();
                    } else {
                      setStep(item.id);
                    }
                  }}
                >
                  <span className="truncate">{item.label}</span>
                  {fill ? <span className="text-[11px] text-muted-foreground">{fill}</span> : null}
                </Button>
              );
            })}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 p-2">
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon-xs"
          icon="ArrowLeft"
          aria-label="Back to the field list"
          onClick={() => setStep("list")}
        />
        <p className="text-xs font-medium text-foreground">{field.label}</p>
      </div>
      <FieldEditor field={field} view={view} onChange={onChange} onDone={onDone} />
    </div>
  );
}

/** One field's control. A choice closes the panel. A range and a number stay open. */
function FieldEditor({
  field,
  view,
  onChange,
  onDone,
}: {
  field: FilterField;
  view: CrmView;
  onChange: (next: CrmView) => void;
  onDone: () => void;
}) {
  if (field.kind === "range" && field.range) {
    const { range } = field;
    return (
      <div className="flex flex-col gap-2" role="group" aria-label={`${range.label} dates`}>
        <label className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
          From
          <Input
            type="date"
            inputSize="sm"
            aria-label={`${range.label} from`}
            value={view.ranges[range.from] ?? ""}
            onChange={(e) => onChange(setRange(view, range.from, e.target.value))}
          />
        </label>
        <label className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
          To
          <Input
            type="date"
            inputSize="sm"
            aria-label={`${range.label} to`}
            value={view.ranges[range.to] ?? ""}
            onChange={(e) => onChange(setRange(view, range.to, e.target.value))}
          />
        </label>
        <Button size="sm" onClick={onDone}>
          Done
        </Button>
      </div>
    );
  }
  if (field.kind === "source") {
    return (
      <Choices
        label="Source"
        value={view.source ?? ANY}
        options={[{ value: ANY, label: "Any source" }, ...SOURCE_OPTIONS]}
        onPick={(next) => {
          onChange({ ...view, source: next || null });
          onDone();
        }}
      />
    );
  }
  const def = field.def;
  if (!def) return null;
  const chosen = view.custom[def.key] ?? [];
  if (field.kind === "boolean") {
    return (
      <Choices
        label={def.label}
        value={chosen[0] ?? ANY}
        options={booleanFilterOptions(def)}
        onPick={(next) => {
          onChange(setCustom(view, def.key, next));
          onDone();
        }}
      />
    );
  }
  if (field.kind === "number" || field.kind === "date") {
    return (
      <>
        <EqualsFilter
          def={def}
          kind={field.kind}
          value={chosen[0] ?? ANY}
          onCommit={(next) => onChange(setCustom(view, def.key, next))}
        />
        <Button size="sm" onClick={onDone}>
          Done
        </Button>
      </>
    );
  }
  const options = optionsOf(def);
  const known = chosen.every((c) => options.some((o) => o.value === c));
  return (
    <Choices
      label={def.label}
      value={chosen[0] ?? ANY}
      options={[
        { value: ANY, label: `Any ${def.label.toLowerCase()}` },
        ...options,
        // A saved view can name an option that the field no longer lists.
        ...(known || chosen.length === 0 ? [] : [{ value: chosen[0], label: chosen.join(", ") }]),
      ]}
      onPick={(next) => {
        onChange(setCustom(view, def.key, next));
        onDone();
      }}
    />
  );
}

/** A short list of choices, one `Button` each. The chosen one wears the selected look. */
function Choices({
  label,
  value,
  options,
  onPick,
}: {
  label: string;
  value: string;
  options: ReadonlyArray<{ value: string; label: string; description?: string }>;
  onPick: (value: string) => void;
}) {
  return (
    <div role="group" aria-label={label} className="flex max-h-60 flex-col overflow-y-auto">
      {options.map((option) => (
        <Button
          key={option.value || "__any"}
          variant="ghost"
          size="sm"
          className="w-full justify-start"
          selected={option.value === value}
          title={option.description}
          onClick={() => onPick(option.value)}
        >
          <span className="truncate">{option.label}</span>
          {option.value === value ? <Icon name="Check" size={12} /> : null}
        </Button>
      ))}
    </div>
  );
}

/**
 * The filter "equal to one number" or "on one day" of a custom field
 * (CRM-C4b). The text is local while the member types. It writes the view on
 * Enter and when the input loses the focus, and only a good value. A text
 * that is not a number or a day marks the input invalid, and the view stays.
 */
function EqualsFilter({
  def,
  kind,
  value,
  onCommit,
}: {
  def: FieldDef;
  kind: "number" | "date";
  value: string;
  onCommit: (next: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  const [seen, setSeen] = useState(value);
  if (seen !== value) {
    setSeen(value);
    setDraft(value);
  }
  const parsed = kind === "number" ? numberFilterValue(def, draft) : dateFilterValue(draft);
  const commit = (next: string | null) => {
    if (next !== null && next !== value) onCommit(next);
  };
  return (
    <div className="flex items-center gap-1" role="group" aria-label={`${def.label} filter`}>
      <Input
        inputSize="sm"
        className="w-full"
        aria-label={kind === "number" ? `${def.label} equals` : `${def.label} on`}
        aria-invalid={parsed === null || undefined}
        {...(kind === "number" ? { inputMode: "text" as const, placeholder: "=" } : { type: "date" })}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          // A date input gives a whole day or nothing, so it writes at once.
          if (kind === "date") commit(dateFilterValue(e.target.value));
        }}
        onBlur={() => commit(parsed)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit(parsed);
        }}
      />
    </div>
  );
}

/**
 * The list's search box (CRM-X1). It writes `?q=` once, 300 ms after the last
 * key, through the page's `go`, so each search is one history entry and Back
 * gives the last query back.
 *
 * The text is local while the member types. The rules of when a `?q=` from
 * the URL replaces it are pure, in `lib/filterBar.ts` (`searchBoxSync`), so
 * the vitest setup tests them.
 *
 * `data-page-filter` lets the shell reach it: `/` focuses it, and "Show all in
 * …" fills it (`src/lib/shell/pageFilter.ts`).
 *
 * The list of a generic Zoho module uses it too (CRM-Q6b, `ModuleList.tsx`),
 * so the CRM has one search box.
 */
export function SearchBox({
  label,
  value,
  onCommit,
}: {
  label: string;
  value: string;
  onCommit: (q: string) => void;
}) {
  const [state, setState] = useState(() => searchBoxStart(value));
  const synced = searchBoxSync(state, value);
  if (synced !== state) setState(synced);
  const commit = useRef(onCommit);
  useEffect(() => {
    commit.current = onCommit;
  });
  // Made on the first key, in the handler, so no render reads a ref.
  const box = useRef<ReturnType<typeof searchCommitter> | null>(null);
  useEffect(() => {
    const timer = box;
    return () => timer.current?.cancel();
  }, []);
  return (
    <Input
      icon="Search"
      inputSize="sm"
      className="w-56"
      value={synced.draft}
      onChange={(e) => {
        const text = e.target.value;
        setState((s) => ({ ...s, draft: text }));
        box.current ??= searchCommitter((q) => {
          setState((s) => searchBoxWrote(s, q));
          commit.current(q);
        });
        box.current.type(e.target.value);
      }}
      placeholder={`${filterWord()} ${label}…`}
      aria-label={`${filterWord()} ${label}`}
      data-page-filter={label}
    />
  );
}
