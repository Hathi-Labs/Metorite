"use client";

/**
 * The Pages tab of the Studio IDE (`ai_studio.md` §5.5, slice AF-8b-2). It shows only under
 * `NEXT_PUBLIC_STUDIO_APP_FRAME`. The page decides that, and this file knows no flag.
 *
 * Reading this as: a member who builds an app sees its pages and its rail, and changes them
 * without JSON. Interaction: the member adds, renames, orders or deletes a page, and does the same
 * for the sections and the rows of the rail. Each act changes the draft once, and the bar then shows
 * "Unsaved changes". A delete asks first. Tabs and cells show as read-only text (AF-8b-3 edits them).
 *
 * Every act calls one function of `frameEdit.ts` through `applyFrame`. This file edits nothing
 * by itself, so a bad request sends nothing. It never calls the network. The toolbar of the page saves.
 */
import { useEffect, useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import SelectButton from "@/components/ui/SelectButton";
import {
  addPage, addRow, addSection, materializePages, moveRow, movePage, moveSection, removePage, removeRow,
  removeSection, renamePage, renameSection, setPageCols, setRowIcon, setRowLabel,
} from "@/lib/studio/frameEdit";
import { NAV_ICONS } from "@/lib/studio/manifest";
import {
  CAP_NOTE, MATERIALIZE_NOTE, PAGES_TAB_LINE, applyFrame, colsOptions, pagesView, railView,
} from "@/lib/studio/pagesTabModel";
import CommitInput from "./CommitInput";

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === "string" ? v : "");

export interface PagesTabProps {
  manifest: unknown;
  onChange: (manifest: unknown) => void;
  /** The Code tab holds JSON that does not parse, so an edit here would drop it. */
  locked: boolean;
  /** The page that an Explorer click put in focus. Local state of the page, never the URL. */
  focusPage?: string | null;
}

type Ask = { kind: "page"; id: string } | { kind: "section"; index: number } | null;

const cardClass = "flex flex-col gap-2 rounded-xl border border-border bg-card px-3 py-2";

export default function PagesTab({ manifest, onChange, locked, focusPage = null }: PagesTabProps) {
  const pages = pagesView(manifest);
  const rail = railView(manifest);
  const views = isObject(manifest) && Array.isArray(manifest.views)
    ? manifest.views.filter(isObject).map((v) => str(v.id)).filter(Boolean)
    : [];
  const [newTitle, setNewTitle] = useState("");
  const [newView, setNewView] = useState("");
  const [sectionTitle, setSectionTitle] = useState("");
  const [rowPick, setRowPick] = useState<Record<number, string>>({});
  const [ask, setAsk] = useState<Ask>(null);

  const act = (fn: (m: unknown) => unknown) => applyFrame(manifest, onChange, fn);
  const viewChoice = views.includes(newView) ? newView : views[0] ?? "";
  const pageChoices = pages.pages.map((p) => ({ value: p.id, label: p.title || p.id, hint: p.title ? p.id : undefined }));
  const canMaterialize = materializePages(manifest) !== manifest;

  // An Explorer click on a page scrolls the card of that page into view.
  useEffect(() => {
    if (focusPage) document.getElementById(`pages-tab-${focusPage}`)?.scrollIntoView?.({ block: "nearest" });
  }, [focusPage]);

  const pagesBlock = !pages.declared ? (
    <>
      <EmptyState icon="Layers" message="This app uses the built-in pages."
        hint={MATERIALIZE_NOTE}
        action={{
          label: "Edit the pages", icon: "Pencil", onClick: () => act(materializePages),
          disabled: locked || !canMaterialize, disabledReason: locked || canMaterialize ? undefined : "Add a view first.",
        }} />
      <ul className="flex flex-col gap-2">
        {pages.pages.map((p) => (
          <li key={p.id} className={cardClass}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm">{p.title || p.id}</span>
              <span className="text-xs text-muted-foreground">{p.id}</span>
            </div>
            {p.parts.length ? <p className="break-words text-xs text-muted-foreground">{p.parts.join(" · ")}</p> : null}
          </li>
        ))}
      </ul>
    </>
  ) : (
    <>
      <ul className="flex flex-col gap-2">
        {pages.pages.map((p, i) => (
          <li key={p.id} id={`pages-tab-${p.id}`} aria-current={focusPage === p.id ? "true" : undefined}
            className={`${cardClass} ${focusPage === p.id ? "border-primary" : ""}`}>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <CommitInput className="min-w-0 flex-1" label={`Title of the ${p.id} page`} value={p.title} placeholder={p.id}
                  disabled={locked} onCommit={(t) => act((m) => renamePage(m, p.id, t))} />
                <span className="shrink-0 text-xs text-muted-foreground">{p.id}</span>
              </div>
              <div className="flex flex-nowrap items-center gap-2">
                <SelectButton label={`Columns of the ${p.id} page`} value={String(p.cols)} widthClass="w-20" disabled={locked}
                  options={colsOptions(p.floor)} onChange={(v) => act((m) => setPageCols(m, p.id, Number(v)))} />
                <Button size="icon-sm" variant="ghost" icon="ChevronUp" aria-label={`Move the ${p.id} page up`}
                  disabled={locked || i === 0} onClick={() => act((m) => movePage(m, p.id, -1))} />
                <Button size="icon-sm" variant="ghost" icon="ChevronDown" aria-label={`Move the ${p.id} page down`}
                  disabled={locked || i === pages.pages.length - 1} onClick={() => act((m) => movePage(m, p.id, 1))} />
                <Button size="icon-sm" variant="ghost" icon="Trash2" aria-label={`Delete the ${p.id} page`} disabled={locked}
                  onClick={() => setAsk({ kind: "page", id: p.id })} />
              </div>
            </div>
            {p.parts.length ? <p className="break-words text-xs text-muted-foreground">{p.parts.join(" · ")}</p> : null}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        <CommitInput className="w-48" label="Title of the new page" value={newTitle} placeholder="Title, such as Orders"
          disabled={locked || pages.full} onCommit={setNewTitle} />
        <SelectButton label="View of the new page" value={viewChoice} widthClass="w-44" prompt="No view yet"
          disabled={locked || pages.full || views.length === 0} options={views.map((v) => ({ value: v, label: v }))} onChange={setNewView} />
        <Button size="sm" icon="Plus" disabled={locked || pages.full || !viewChoice} onClick={() => {
          if (act((m) => addPage(m, newTitle, viewChoice))) setNewTitle("");
        }}>Add page</Button>
        {pages.full ? <span className="text-xs text-muted-foreground">{CAP_NOTE}</span> : null}
      </div>
    </>
  );

  const addSectionRow = (
    <div className="flex flex-wrap items-center gap-2">
      <CommitInput className="w-48" label="Title of the new section" value={sectionTitle} placeholder="Title, such as Work"
        disabled={locked || rail.full} onCommit={setSectionTitle} />
      <Button size="sm" icon="Plus" disabled={locked || rail.full || !sectionTitle.trim()} onClick={() => {
        if (act((m) => addSection(m, sectionTitle))) setSectionTitle("");
      }}>Add a section</Button>
      {rail.full ? <span className="text-xs text-muted-foreground">The rail holds at most 4 sections.</span> : null}
    </div>
  );

  const railBlock = !rail.declared ? (
    <>
      <EmptyState icon="PanelLeft" message="The rail follows the default."
        hint="It lists each page in order. Add a section to set your own rail." />
      {rail.sections.map((s, i) => (
        <div key={i} className={cardClass}>
          <p className="text-xs text-muted-foreground">{s.rows.map((r) => r.label || r.pageName).join(" · ") || "No page yet"}</p>
        </div>
      ))}
      {addSectionRow}
    </>
  ) : (
    <>
      <ul className="flex flex-col gap-2">
        {rail.sections.map((s, si) => {
          const pick = pageChoices.some((c) => c.value === rowPick[si]) ? rowPick[si] : pageChoices[0]?.value ?? "";
          return (
            <li key={si} className={cardClass}>
              <div className="flex flex-wrap items-center gap-2">
                <CommitInput className="w-48" label={`Title of section ${si + 1}`} value={s.title}
                  disabled={locked} onCommit={(t) => act((m) => renameSection(m, si, t))} />
                <span className="flex-1" />
                <Button size="icon-sm" variant="ghost" icon="ChevronUp" aria-label={`Move section ${s.title} up`}
                  disabled={locked || si === 0} onClick={() => act((m) => moveSection(m, si, -1))} />
                <Button size="icon-sm" variant="ghost" icon="ChevronDown" aria-label={`Move section ${s.title} down`}
                  disabled={locked || si === rail.sections.length - 1} onClick={() => act((m) => moveSection(m, si, 1))} />
                <Button size="icon-sm" variant="ghost" icon="Trash2" aria-label={`Delete section ${s.title}`} disabled={locked}
                  onClick={() => setAsk({ kind: "section", index: si })} />
              </div>
              {s.rows.length === 0 ? <p className="text-xs text-muted-foreground">No row yet. Add a page below.</p> : null}
              <ul className="flex flex-col gap-1">
                {s.rows.map((r, ri) => (
                  <li key={`${r.page}-${ri}`} className="flex flex-wrap items-center gap-2 sm:flex-nowrap">
                    <span className="w-24 shrink-0 truncate text-xs text-muted-foreground" title={r.pageName}>{r.pageName}</span>
                    <CommitInput className="w-40 shrink-0" label={`Label of row ${ri + 1} in ${s.title}`} value={r.label} placeholder={r.pageName}
                      disabled={locked} onCommit={(t) => act((m) => setRowLabel(m, si, ri, t || null))} />
                    <SelectButton label={`Icon of row ${ri + 1} in ${s.title}`} value={r.icon} widthClass="w-36" disabled={locked}
                      options={NAV_ICONS.map((n) => ({ value: n, label: n }))} onChange={(v) => act((m) => setRowIcon(m, si, ri, v))} />
                    <span className="hidden min-w-0 flex-1 sm:block" />
                    <Button size="icon-sm" variant="ghost" icon="ChevronUp" aria-label={`Move row ${ri + 1} in ${s.title} up`}
                      disabled={locked || ri === 0} onClick={() => act((m) => moveRow(m, si, ri, -1))} />
                    <Button size="icon-sm" variant="ghost" icon="ChevronDown" aria-label={`Move row ${ri + 1} in ${s.title} down`}
                      disabled={locked || ri === s.rows.length - 1} onClick={() => act((m) => moveRow(m, si, ri, 1))} />
                    <Button size="icon-sm" variant="ghost" icon="X" aria-label={`Remove row ${ri + 1} from ${s.title}`} disabled={locked}
                      onClick={() => act((m) => removeRow(m, si, ri))} />
                  </li>
                ))}
              </ul>
              <div className="flex flex-wrap items-center gap-2">
                <SelectButton label={`Page of the new row in ${s.title}`} value={pick} widthClass="w-44" prompt="No page yet"
                  disabled={locked || s.full || pageChoices.length === 0} options={pageChoices}
                  onChange={(v) => setRowPick((p) => ({ ...p, [si]: v }))} />
                <Button size="sm" icon="Plus" disabled={locked || s.full || !pick} onClick={() => act((m) => addRow(m, si, pick))}>
                  Add row
                </Button>
                {s.full ? <span className="text-xs text-muted-foreground">A section holds at most 12 rows.</span> : null}
              </div>
            </li>
          );
        })}
      </ul>
      {addSectionRow}
    </>
  );

  return (
    <div className="flex flex-col gap-4 p-3">
      <p className="text-xs text-muted-foreground">{PAGES_TAB_LINE}</p>
      <section aria-label="Pages" className="flex flex-col gap-3">
        <h3 className="text-sm font-medium">Pages</h3>
        {pagesBlock}
      </section>
      <section aria-label="Rail" className="flex flex-col gap-3">
        <h3 className="text-sm font-medium">Rail</h3>
        {railBlock}
      </section>
      <p className="text-xs text-muted-foreground">A change is a draft until you press Save. A page holds no records, so a delete never touches data.</p>
      <ConfirmDialog open={ask !== null} title={ask?.kind === "section" ? "Delete this section?" : "Delete this page?"}
        subject={ask?.kind === "page" ? ask.id : ask?.kind === "section" ? rail.sections[ask.index]?.title ?? null : null}
        body={ask?.kind === "section"
          ? "The section leaves this draft with its rows. The pages stay. The live app keeps the section until you publish."
          : "The page leaves this draft, and so do the rail rows that name it. The views stay. The live app keeps the page until you publish."}
        confirmLabel="Delete" cancelLabel="Keep it" icon="Trash2"
        onConfirm={() => {
          if (ask?.kind === "page") act((m) => removePage(m, ask.id));
          else if (ask?.kind === "section") act((m) => removeSection(m, ask.index));
          setAsk(null);
        }} onCancel={() => setAsk(null)} />
    </div>
  );
}
