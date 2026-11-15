"use client";

/**
 * The cells editor of one page card in the Pages tab (`ai_studio.md` §5.5, slice AF-8b-3b).
 *
 * Reading this as: a member who builds an app sets the tabs and the cells of a page with buttons and
 * selects. There is no drag here. Interaction: the member opens Layout on a page, then adds,
 * renames, orders or removes a tab, and adds, orders, widens, moves or removes a cell. Each act changes
 * the draft once. A refused act shows its reason as a sentence, and its button is off.
 *
 * This file never sends a change by itself. The tab passes `act`, and `act` is the one place
 * that sends. Each act calls a pure function of `tabEdit.ts` or `pageLayout.ts`.
 * A cell leaves only through `removeCellGuarded`, so the rail count guard binds it. It never
 * calls the network.
 */
import { useState } from "react";

import Button from "@/components/ui/Button";
import SelectButton from "@/components/ui/SelectButton";
import { FREE_GRID, GRID_BACK_WHY, presetOptions, setPageLayout, setRegionViews } from "@/lib/studio/layoutEdit";
import type { LayoutName } from "@/lib/studio/manifest";
import {
  BROKEN_NOTE, CELL_CAP_NOTE, PRESET_NOTE, TAB_CAP_NOTE, TAB_NAME_HINT, USE_TABS_HINT, moveCellArgs, tabTitleOrDefault,
  type LayoutList, type LayoutView,
} from "@/lib/studio/pagesTabModel";
import { addCell, moveCell, moveCellToTab, setCellSpan, type Loc } from "@/lib/studio/pageLayout";
import { addTab, layoutAct, moveTab, removeCellGuarded, renameTab } from "@/lib/studio/tabEdit";
import CommitInput from "./CommitInput";

export interface CellsEditorProps {
  page: LayoutView & { id: string; title: string };
  views: { id: string; label: string }[];
  /** The one sender of the Pages tab. A bad request sends nothing. */
  act: (fn: (m: unknown) => unknown) => void;
  locked: boolean;
  /** Ask the tab to confirm a tab removal. This file never removes a tab by itself. */
  onRemoveTab: (tabId: string) => void;
  open: boolean;
  onToggle: () => void;
}

const rowClass = "flex flex-wrap items-center gap-2 sm:flex-nowrap";
// A cell row is two lines on a phone: the label, then the controls. At sm and wider it is one line.
const cellRowClass = "flex flex-wrap items-center gap-x-2 gap-y-1 sm:flex-nowrap";
const cellLabelClass = "min-w-0 basis-full truncate text-xs sm:flex-1 sm:basis-0";
const noteClass = "break-words text-xs text-muted-foreground";

export default function CellsEditor({ page, views, act, locked, onRemoveTab, open, onToggle }: CellsEditorProps) {
  const [tabTitle, setTabTitle] = useState("");
  const [pick, setPick] = useState<Record<string, string>>({});
  const pid = page.id;

  const toggle = (
    <Button size="sm" variant="ghost" icon={open ? "ChevronDown" : "ChevronRight"} aria-expanded={open}
      aria-label={`Layout of the ${pid} page`} disabled={locked} onClick={onToggle}>Layout</Button>
  );
  // The toggle has one place in each state, so it does not move when the section opens.
  const toggleRow = <div className="flex justify-start">{toggle}</div>;
  if (!open) return toggleRow;
  if (page.broken) {
    return <div className="flex flex-col gap-2">{toggleRow}<p className={noteClass}>{BROKEN_NOTE}</p></div>;
  }

  // The preset picker (STU-LY-8b): a page is a free grid, or one of the presets of the closed list.
  const presetPicker = (
    <div className={rowClass}>
      <SelectButton label={`Layout preset of the ${pid} page`} value={page.preset?.layout ?? FREE_GRID} widthClass="w-48"
        disabled={locked} options={presetOptions()}
        onChange={(v) => act((m) => setPageLayout(m, pid, v === FREE_GRID ? null : (v as LayoutName)))} />
    </div>
  );
  if (page.preset) {
    const p = page.preset;
    const empty = p.regions.every((r) => r.views.length === 0);
    return (
      <div className="flex flex-col gap-2" data-preset={p.layout}>
        {toggle}
        {presetPicker}
        <p className={noteClass}>{PRESET_NOTE}</p>
        {p.regions.map((r) => {
          const pickKey = `region:${r.name}`;
          const choice = r.choices.some((c) => c.id === pick[pickKey]) ? pick[pickKey] : r.choices[0]?.id ?? "";
          const names = r.views.map((v) => v.id);
          return (
            <div key={r.name} data-region={r.name} className="flex flex-col gap-1 rounded-lg border border-border px-2 py-2">
              <span className="text-xs font-medium text-foreground">
                {`${r.name} region: ${r.required ? "needs" : "may hold"} ${r.cap === 1 ? "one" : `up to ${r.cap}`} ${r.block} view${r.cap === 1 ? "" : "s"}`}
              </span>
              <ul className="flex flex-col gap-1">
                {r.views.map((v) => (
                  <li key={v.id} className={cellRowClass}>
                    <span className={cellLabelClass} title={v.id}>{v.label}</span>
                    <Button size="icon-sm" variant="ghost" icon="X" aria-label={`Remove ${v.label} from the ${r.name} region`}
                      disabled={locked || (r.required && r.views.length === 1)}
                      onClick={() => act((m) => setRegionViews(m, pid, r.name, names.filter((n) => n !== v.id)))} />
                  </li>
                ))}
              </ul>
              {r.need ? <p className={noteClass} role="status">{r.need}</p> : null}
              <div className={rowClass}>
                <SelectButton label={`View to add to the ${r.name} region`} value={choice} widthClass="w-44" prompt={`No ${r.block} view`}
                  disabled={locked || !r.room || r.choices.length === 0} options={r.choices.map((c) => ({ value: c.id, label: c.label }))}
                  onChange={(v) => setPick((q) => ({ ...q, [pickKey]: v }))} />
                <Button size="sm" icon="Plus" disabled={locked || !r.room || !choice}
                  onClick={() => act((m) => setRegionViews(m, pid, r.name, [...names, choice]))}>Add view</Button>
              </div>
            </div>
          );
        })}
        {empty ? <p className={noteClass}>{GRID_BACK_WHY}</p> : null}
      </div>
    );
  }

  const spanOptions = Array.from({ length: page.cols }, (_, i) => ({ value: String(i + 1), label: String(i + 1) }));

  const listBlock = (l: LayoutList) => {
    const key = l.tabId ?? "";
    const where: (index: number) => Loc = (index) => ({ tabId: l.tabId, index });
    const choice = views.some((v) => v.id === pick[key]) ? pick[key] : views[0]?.id ?? "";
    const move = (index: number, delta: -1 | 1) => {
      const a = moveCellArgs(l.tabId, index, delta);
      act((m) => layoutAct(m, (t) => moveCell(t, pid, a.from, a.to)));
    };
    const others = page.lists.filter((o) => o.tabId !== l.tabId);
    return (
      <div className="flex flex-col gap-1">
        <ul className="flex flex-col gap-1">
          {l.cells.map((c) => (
            <li key={c.index} className="flex flex-col gap-1">
              <div className={cellRowClass}>
                <span className={cellLabelClass} title={c.view}>{c.viewLabel}</span>
                <SelectButton label={`Span of cell ${c.index + 1} in ${l.title}`} value={String(c.span ?? 1)} widthClass="w-20"
                  disabled={locked} options={spanOptions}
                  onChange={(v) => act((m) => layoutAct(m, (t) => setCellSpan(t, pid, where(c.index), Number(v))))} />
                <Button size="icon-sm" variant="ghost" icon="ChevronUp" aria-label={`Move cell ${c.index + 1} in ${l.title} up`}
                  disabled={locked || !c.canUp}
                  onClick={() => move(c.index, -1)} />
                <Button size="icon-sm" variant="ghost" icon="ChevronDown" aria-label={`Move cell ${c.index + 1} in ${l.title} down`}
                  disabled={locked || !c.canDown}
                  onClick={() => move(c.index, 1)} />
                {l.tabId !== null ? (
                  <div className="order-last basis-full sm:order-none sm:basis-auto">
                    <SelectButton label={`Move cell ${c.index + 1} in ${l.title} to a tab`} value="" widthClass="w-32" prompt="Move to tab"
                      disabled={locked || others.length === 0}
                      options={others.map((o) => ({
                        value: o.tabId ?? "", label: o.title, ...(o.room === 0 ? { disabled: true, hint: CELL_CAP_NOTE } : {}),
                      }))}
                      onChange={(v) => act((m) => layoutAct(m, (t) => moveCellToTab(t, pid, where(c.index), v)))} />
                  </div>
                ) : null}
                <Button size="icon-sm" variant="ghost" icon="X" aria-label={`Remove cell ${c.index + 1} from ${l.title}`}
                  disabled={locked || c.removeWhy !== null} onClick={() => act((m) => removeCellGuarded(m, pid, where(c.index)))} />
              </div>
              {c.removeWhy ? <p className={noteClass}>{c.removeWhy}</p> : null}
            </li>
          ))}
        </ul>
        <div className={rowClass}>
          <SelectButton label={`View of the new cell in ${l.title}`} value={choice} widthClass="w-44" prompt="No view yet"
            disabled={locked || views.length === 0} options={views.map((v) => ({ value: v.id, label: v.label }))}
            onChange={(v) => setPick((p) => ({ ...p, [key]: v }))} />
          <Button size="sm" icon="Plus" disabled={locked || l.room === 0 || !choice}
            onClick={() => act((m) => layoutAct(m, (t) => addCell(t, pid, where(l.cells.length), choice)))}>Add cell</Button>
        </div>
        {l.room === 0 ? <p className={noteClass}>{CELL_CAP_NOTE}</p> : null}
      </div>
    );
  };

  return (
    <div className="flex flex-col gap-2">
      {toggle}
      {presetPicker}
      {page.flat ? (
        <>
          {page.lists.map((l) => <div key="flat">{listBlock(l)}</div>)}
          <div className={rowClass}>
            <CommitInput className="w-48" label={`Title of the first tab of the ${pid} page`} value={tabTitle}
              placeholder="Title, such as Overview" disabled={locked} onCommit={setTabTitle} />
            <Button size="sm" icon="Plus" disabled={locked} onClick={() => {
              act((m) => addTab(m, pid, tabTitleOrDefault(tabTitle, page.tabCount)));
              setTabTitle("");
            }}>Use tabs</Button>
          </div>
          <p className={noteClass}>{USE_TABS_HINT}</p>
        </>
      ) : (
        <>
          {page.lists.map((l) => {
            const tabId = l.tabId ?? "";
            return (
              <div key={tabId} className="flex flex-col gap-1 rounded-lg border border-border px-2 py-2">
                <div className="flex flex-nowrap items-center gap-2">
                  <CommitInput className="min-w-0 flex-1" label={`Title of the ${tabId} tab`} value={l.title} disabled={locked}
                    onCommit={(t) => act((m) => renameTab(m, pid, tabId, t))} />
                  <Button size="icon-sm" variant="ghost" icon="ChevronUp" aria-label={`Move the ${l.title} tab up`}
                    disabled={locked || !l.tabCanUp} onClick={() => act((m) => moveTab(m, pid, tabId, -1))} />
                  <Button size="icon-sm" variant="ghost" icon="ChevronDown" aria-label={`Move the ${l.title} tab down`}
                    disabled={locked || !l.tabCanDown} onClick={() => act((m) => moveTab(m, pid, tabId, 1))} />
                  <Button size="icon-sm" variant="ghost" icon="Trash2" aria-label={`Remove the ${l.title} tab`}
                    disabled={locked || l.tabWhy !== null} onClick={() => onRemoveTab(tabId)} />
                </div>
                {l.tabWhy ? <p className={noteClass}>{l.tabWhy}</p> : null}
                {listBlock(l)}
              </div>
            );
          })}
          <div className={rowClass}>
            <CommitInput className="w-48" label="Title of the new tab" value={tabTitle} placeholder="Title, such as Details"
              disabled={locked || page.tabFull} onCommit={setTabTitle} />
            <Button size="sm" icon="Plus" disabled={locked || page.tabFull} onClick={() => {
              act((m) => addTab(m, pid, tabTitleOrDefault(tabTitle, page.tabCount)));
              setTabTitle("");
            }}>Add a tab</Button>
          </div>
          <p className={noteClass}>{page.tabFull ? TAB_CAP_NOTE : TAB_NAME_HINT}</p>
        </>
      )}
    </div>
  );
}
