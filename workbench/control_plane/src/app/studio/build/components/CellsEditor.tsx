"use client";

/**
 * The cells editor of one page card in the Pages tab (`ai_studio.md` §5.5, slice AF-8b-3b).
 *
 * Reading this as: a member who builds an app sets the tabs and the cells of a page with buttons and
 * selects, or drags a cell row or a view chip (slice AF-11b-2). Interaction: the member opens Layout
 * on a page, then adds, renames, orders or removes a tab, and adds, orders, widens, moves or removes
 * a cell. Each act changes the draft once. A refused act shows its reason as a sentence, and its
 * button is off.
 *
 * A drag is the same act as the buttons. A drop calls `act` once, with `drop` of `cellDrag.ts`, so
 * it is one edit, one Save and one Undo. The buttons stay the keyboard and phone path: a touch
 * screen starts no drag, and the handles and the strip are hidden there. A drag stays inside one
 * page card, because each card holds its own drag state.
 *
 * This file never sends a change by itself. The tab passes `act`, and `act` is the one place
 * that sends. Each act calls a pure function of `tabEdit.ts` or `pageLayout.ts`.
 * A cell leaves only through `removeCellGuarded`, so the rail count guard binds it. It never
 * calls the network.
 */
import { useState, type DragEvent } from "react";

import Button from "@/components/ui/Button";
import SelectButton from "@/components/ui/SelectButton";
import { FREE_GRID, GRID_BACK_WHY, presetOptions, setPageLayout, setRegionViews } from "@/lib/studio/layoutEdit";
import type { LayoutName } from "@/lib/studio/manifest";
import { IDLE, begin, drop, end, hover, isOver, leave, listEnd, type DragState } from "@/lib/studio/cellDrag";
import type { DragItem } from "@/lib/studio/dropModel";
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
  /** The draft. A drag reads it to decide where a drop is allowed. It never sends it. */
  manifest: unknown;
  /** The one sender of the Pages tab. A bad request sends nothing. */
  act: (fn: (m: unknown) => unknown) => unknown;
  locked: boolean;
  /** Ask the tab to confirm a tab removal. This file never removes a tab by itself. */
  onRemoveTab: (tabId: string) => void;
  open: boolean;
  onToggle: () => void;
}

const rowClass = "flex flex-wrap items-center gap-2 sm:flex-nowrap";
// A cell row is two lines on a phone: the label, then the controls. At sm and wider it is one line.
const cellRowClass = "flex flex-wrap items-center gap-x-2 gap-y-1 sm:flex-nowrap";
// The handle and the label are one group, so the handle never stands alone on the first line of a phone row.
const cellLabelClass = "flex min-w-0 basis-full items-center gap-1 sm:flex-1 sm:basis-0";
const cellNameClass = "min-w-0 flex-1 truncate text-xs";
const noteClass = "break-words text-xs text-muted-foreground";
const alertClass = "break-words text-xs text-destructive";
// The line of a drag has one place, so the section does not jump when a sentence appears.
const lineSlotClass = "min-h-5";
// A drop target keeps its border space at rest, so the highlight moves nothing. The border is the
// line that says before or after, and the ring is the look of a board column that takes a drop.
const rowTargetClass = "rounded-md border-y-2 border-transparent";
const rowOverClass = "ring-2 ring-primary/40";
const emptyZoneClass = "rounded-md border border-dashed border-border px-2 py-2 text-xs text-muted-foreground";
// Only a pointer drags. A touch screen has no drag, so the handles and the strip are not drawn there.
const pointerOnly = "pointer-coarse:hidden";

export default function CellsEditor({ page, views, manifest, act, locked, onRemoveTab, open, onToggle }: CellsEditorProps) {
  const [tabTitle, setTabTitle] = useState("");
  const [pick, setPick] = useState<Record<string, string>>({});
  const [ds, setDs] = useState<DragState>(IDLE);
  const pid = page.id;

  const start = (e: DragEvent<HTMLElement>, item: DragItem, label: string) => {
    if (locked) {
      e.preventDefault();
      return;
    }
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", label);
    setDs(begin(item));
  };
  // The pointer is over a place that names a row. `offsetY` and `height` come from the row itself.
  // The state moves by a function of the latest state, because the leave of the old row arrives
  // after the over of the new one, and a stale copy would wipe the new highlight.
  const over = (e: DragEvent<HTMLElement>, loc: Loc, at?: { offsetY: number; height: number }) => {
    if (locked || !ds.drag) return;
    const r = e.currentTarget.getBoundingClientRect();
    const y = at ? at.offsetY : e.clientY - r.top;
    const h = at ? at.height : r.height;
    setDs((cur) => hover(cur, manifest, pid, loc, y, h).state);
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "move";
  };
  const out = (e: DragEvent<HTMLElement>, loc: Loc) => {
    // A move between the children of one target is not a leave.
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
    setDs((cur) => (isOver(cur, loc) ? leave(cur) : cur));
  };
  // The one send of a drag. `act` runs the function at once with the draft, and the line is read after it.
  const land = (e: DragEvent<HTMLElement>, loc: Loc, at?: { offsetY: number; height: number }) => {
    e.preventDefault();
    e.stopPropagation();
    if (locked) return;
    const r = e.currentTarget.getBoundingClientRect();
    const box: { next: DragState | null } = { next: null };
    act((m) => {
      const d = drop(ds, m, pid, loc, at ? at.offsetY : e.clientY - r.top, at ? at.height : r.height);
      box.next = d.state;
      return d.next;
    });
    setDs(box.next ?? end(ds));
  };
  // A click anywhere in the section ends the sentence of the last drop, so it never describes an old draft.
  const clearLine = () => {
    if (ds.line) setDs({ ...ds, line: null });
  };

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
      <div className="flex flex-col gap-1"
        onDragOver={(e) => { const z = listEnd(l.cells.length, l.tabId); over(e, z.loc, z); }}
        onDragLeave={(e) => out(e, listEnd(l.cells.length, l.tabId).loc)}
        onDrop={(e) => { const z = listEnd(l.cells.length, l.tabId); land(e, z.loc, z); }}>
        {l.cells.length === 0 && ds.drag ? (
          <div className={`${emptyZoneClass} ${isOver(ds, where(0)) ? rowOverClass : ""}`}>Drop here to start this list.</div>
        ) : null}
        <ul className="flex flex-col gap-1">
          {l.cells.map((c) => (
            <li key={c.index}
              className={`flex flex-col gap-1 ${rowTargetClass} ${isOver(ds, where(c.index)) ? `${rowOverClass} ${ds.position === "before" ? "border-t-primary" : "border-b-primary"}` : ""}`}
              onDragOver={(e) => over(e, where(c.index))} onDragLeave={(e) => out(e, where(c.index))} onDrop={(e) => land(e, where(c.index))}>
              <div className={cellRowClass}>
                <div className={cellLabelClass}>
                  <Button size="icon-sm" variant="ghost" icon="GripVertical" aria-label={`Drag cell ${c.index + 1} in ${l.title}`}
                    disabled={locked} draggable className={`cursor-grab ${pointerOnly}`}
                    onDragStart={(e) => start(e, { kind: "cell", from: where(c.index) }, c.viewLabel)}
                    onDragEnd={(e) => { e.currentTarget.blur(); setDs((cur) => end(cur)); }} />
                  <span className={cellNameClass} title={c.view}>{c.viewLabel}</span>
                </div>
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
    <div className="flex flex-col gap-2" onClickCapture={clearLine}>
      {toggle}
      {presetPicker}
      {views.length > 0 ? (
        <div className={`flex flex-col gap-1 ${pointerOnly}`}>
          <p className={noteClass}>Drag a view onto a list, or use Add cell.</p>
          <div className="flex flex-wrap gap-1">
            {views.map((v) => (
              <Button key={v.id} size="sm" variant="secondary" icon="GripVertical" disabled={locked} draggable
                className="cursor-grab" onDragStart={(e) => start(e, { kind: "view", viewId: v.id }, v.label)}
                onDragEnd={(e) => { e.currentTarget.blur(); setDs((cur) => end(cur)); }}>{v.label}</Button>
            ))}
          </div>
        </div>
      ) : null}
      <div className={`${lineSlotClass} ${pointerOnly}`}>
        <p role="status" className={noteClass}>{ds.line?.kind === "status" ? ds.line.text : ""}</p>
        {ds.line?.kind === "alert" ? <p role="alert" className={alertClass}>{ds.line.text}</p> : null}
      </div>
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
