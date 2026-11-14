"use client";

/**
 * The list bar of a Studio page (STU-AF4, `ai_studio.md` §5.5): search, one chip for each field
 * that the door can filter, a sort, a group and the layout switch. It sits over the table or the
 * board of the page and holds no state of its own beyond the text that a member is typing.
 *
 * It adds no third filter bar. The search box runs on `lib/filterModel.ts` (the state machine and
 * `searchCommitter` that the CRM bar uses), the chips are `SelectButton` and `Input`, and the
 * switch is `ModeSwitch`. `lib/studio/filterBarModel.ts` decides which chips exist and builds the
 * door input. The URL holds every choice (`usePageState`), so a reload shows the same list.
 */
import { useEffect, useRef, useState } from "react";

import { ModeSwitch } from "@/components/ModeSwitch";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import SelectButton, { OFF_DEFAULT } from "@/components/ui/SelectButton";
import {
  ANY, searchBoxStart, searchBoxSync, searchBoxWrote, searchCommitter,
} from "@/lib/filterModel";
import {
  type BarState, type Chip, type Layout, parseRange, rangeValue, sortOptions, type BarField,
} from "@/lib/studio/filterBarModel";

/** A text box that writes after a pause. It is the search box of the CRM, with another label. */
function PauseInput({ label, value, onCommit, icon, className = "w-44", type = "text", step, placeholder }: {
  label: string; value: string; onCommit: (text: string) => void;
  icon?: string; className?: string; type?: "text" | "number"; step?: string; placeholder?: string;
}) {
  const [state, setState] = useState(() => searchBoxStart(value));
  const synced = searchBoxSync(state, value);
  if (synced !== state) setState(synced);
  const commit = useRef(onCommit);
  useEffect(() => {
    commit.current = onCommit;
  });
  const box = useRef<ReturnType<typeof searchCommitter> | null>(null);
  useEffect(() => {
    const timer = box;
    return () => timer.current?.cancel();
  }, []);
  return (
    <Input icon={icon} inputSize="sm" className={className} type={type} step={step} value={synced.draft}
      placeholder={placeholder ?? label} aria-label={label}
      onChange={(e) => {
        const text = e.target.value;
        setState((s) => ({ ...s, draft: text }));
        box.current ??= searchCommitter((q) => {
          setState((s) => searchBoxWrote(s, q));
          commit.current(q);
        });
        box.current.type(text);
      }} />
  );
}

const YES_NO = [
  { value: ANY, label: "Any" },
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
];

function ChipControl({ chip, value, onChange }: { chip: Chip; value: string; onChange: (v: string) => void }) {
  switch (chip.kind) {
    case "text":
      return <PauseInput label={chip.label} value={value} onCommit={onChange} />;
    case "select":
      return (
        <SelectButton label={chip.label} value={value} defaultValue={ANY} widthClass="w-40"
          className={value ? OFF_DEFAULT : ""}
          options={[{ value: ANY, label: `${chip.label}: any` }, ...chip.options.map((o) => ({ value: o, label: `${chip.label}: ${o}` }))]}
          onChange={onChange} />
      );
    case "checkbox":
      return (
        <SelectButton label={chip.label} value={value} defaultValue={ANY} widthClass="w-36"
          className={value ? OFF_DEFAULT : ""}
          options={YES_NO.map((o) => (o.value === ANY ? { ...o, label: `${chip.label}: any` } : o))}
          onChange={onChange} />
      );
    case "number": {
      const r = parseRange(value);
      return (
        <span className="flex items-center gap-1" role="group" aria-label={chip.label}>
          <span className="text-xs text-muted-foreground">{chip.label}</span>
          <PauseInput label={`${chip.label} from`} placeholder="From" type="number" step="any" className="w-24" value={r.from}
            onCommit={(t) => onChange(rangeValue(t.trim(), r.to))} />
          <PauseInput label={`${chip.label} to`} placeholder="To" type="number" step="any" className="w-24" value={r.to}
            onCommit={(t) => onChange(rangeValue(r.from, t.trim()))} />
        </span>
      );
    }
    case "date": {
      const r = parseRange(value);
      return (
        <span className="flex items-center gap-1" role="group" aria-label={chip.label}>
          <span className="text-xs text-muted-foreground">{chip.label}</span>
 <span className="text-xs text-muted-foreground">from</span>
          <Input type="date" inputSize="sm" className="w-36" value={r.from} aria-label={`${chip.label} from`}
            onChange={(e) => onChange(rangeValue(e.target.value, r.to))} />
          <span className="text-xs text-muted-foreground">to</span>
          <Input type="date" inputSize="sm" className="w-36" value={r.to} aria-label={`${chip.label} to`}
            onChange={(e) => onChange(rangeValue(r.from, e.target.value))} />
        </span>
      );
    }
  }
}

export interface StudioFilterBarProps {
  fields: readonly BarField[];
  chips: readonly Chip[];
  /** The field that the search box filters, or null for no box. */
  searchKey: string | null;
  bar: BarState;
  layouts: readonly Layout[];
  /** The layout on screen. */
  layout: Layout;
  /** The select fields that can make lanes, with their labels. */
  lanes: readonly { key: string; label: string }[];
  /** The lane field on screen. */
  group: string | null;
  active: number;
  onQ: (q: string) => void;
  onFilter: (key: string, value: string) => void;
  onSort: (sort: string | null) => void;
  onLayout: (layout: Layout) => void;
  onGroup: (group: string | null) => void;
  onClear: () => void;
}

const LAYOUT_ICON: Record<Layout, { icon: string; label: string }> = {
  table: { icon: "Table", label: "Table" },
  board: { icon: "Kanban", label: "Board" },
};

export default function StudioFilterBar(p: StudioFilterBarProps) {
  const sorts = sortOptions(p.fields);
  const key = p.bar.sort?.replace(/^-/, "") ?? "";
  const desc = p.bar.sort?.startsWith("-") ?? false;
  const showGroup = p.layout === "board" && p.lanes.length > 1;
  const empty = !p.searchKey && p.chips.length === 0 && sorts.length === 0 && p.layouts.length < 2;
  if (empty) return null;
  return (
    <div className="flex flex-wrap items-center gap-2" role="search" aria-label="Filter this list" data-studio-filter-bar>
      {p.searchKey ? (
        <PauseInput icon="Search" label="Search" className="w-56" value={p.bar.q} onCommit={p.onQ} />
      ) : null}
      {p.chips.map((c) => (
        <ChipControl key={c.key} chip={c} value={p.bar.filters[c.key] ?? ANY} onChange={(v) => p.onFilter(c.key, v)} />
      ))}
      {sorts.length > 0 ? (
        <>
          <SelectButton label="Sort by" value={key} defaultValue={ANY} widthClass="w-40"
            className={key ? OFF_DEFAULT : ""}
            options={[{ value: ANY, label: "Sort: none" }, ...sorts.map((s) => ({ value: s.key, label: `Sort: ${s.label}` }))]}
            onChange={(v) => p.onSort(v ? (desc ? `-${v}` : v) : null)} />
          {key ? (
            <Button variant="ghost" size="icon-sm" icon={desc ? "ArrowDown" : "ArrowUp"}
              aria-label={desc ? "Sorted high to low. Sort low to high" : "Sorted low to high. Sort high to low"}
              onClick={() => p.onSort(desc ? key : `-${key}`)} />
          ) : null}
        </>
      ) : null}
      {showGroup ? (
        <SelectButton label="Group by" value={p.group ?? p.lanes[0].key} defaultValue={p.lanes[0].key} widthClass="w-40"
          options={p.lanes.map((l) => ({ value: l.key, label: `Group: ${l.label}` }))}
          onChange={(v) => p.onGroup(v === p.lanes[0].key ? null : v)} />
      ) : null}
      {p.active > 0 ? (
        <Button variant="ghost" size="sm" icon="X" onClick={p.onClear}>Clear</Button>
      ) : null}
      {p.layouts.length > 1 ? (
        <span className="ml-auto">
          <ModeSwitch<Layout> modes={p.layouts.map((l) => ({ id: l, ...LAYOUT_ICON[l] }))} mode={p.layout}
            onPick={p.onLayout} label="Layout" iconOnly />
        </span>
      ) : null}
    </div>
  );
}
