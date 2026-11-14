"use client";

/**
 * The app frame of `/studio/package-preview` (STU-AF1, `ai_studio.md` §5.5), behind
 * `NEXT_PUBLIC_STUDIO_APP_FRAME`. It draws the pages of the manifest through `StudioPage`, on
 * live data through the door of the page. The page id lives in the URL (`?page=<id>`).
 *
 * The frame composes parts that exist: `StudioPage`, the mapper `lib/studio/viewBlocks.ts`,
 * `GenerativeUINode`, `StudioForm` and `StudioDetail`. It adds no rail and no tab bar. The dialogs of AF-6 are `StudioNewButton` and `StudioFormDialog`.
 * The rail is AF-2. A viewer (J2a) reads only: the door holds the reads, and the form view says so.
 * This is the one file of the route that holds the page model. `page.tsx` imports none.
 */
import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

import GenerativeUINode from "@/components/GenerativeUINode";
import { EmptyState } from "@/components/EmptyState";
import DeletePackageButton from "@/components/studio/DeletePackageButton";
import PackageLifetimeBar from "@/components/studio/PackageLifetimeBar";
import RecordsStatus from "@/components/studio/RecordsStatus";
import StudioDetail from "@/components/studio/StudioDetail";
import StudioFilterBar from "@/components/studio/StudioFilterBar";
import StudioNewButton from "@/components/studio/StudioNewButton";
import StudioForm from "@/components/studio/StudioForm";
import StudioRecord from "@/components/studio/StudioRecord";
import StudioPage, { PANEL } from "@/components/studio/StudioPage";
import Button from "@/components/ui/Button";
import SelectButton from "@/components/ui/SelectButton";
import { StudioDataContext } from "@/lib/studio/dataContext";
import { type Row, type StudioDataDoor, moreLine, rowsTotal, sourceKey } from "@/lib/studio/dataDoor";
import { buildHref } from "@/lib/studio/packageDraft";
import {
  activeCount, barOf, barTarget, chipsOf, clearedBar, layoutsFor, searchKeyOf, toListArgs, withFilter,
  showNoMatch, withGroup, withLayout, withQ, withSort, type BarField,
} from "@/lib/studio/filterBarModel";
import { newButtons } from "@/lib/studio/dialogModel";
import { computedKeys } from "@/lib/studio/inlineEdit";
import { buildPage, viewObjectName } from "@/lib/studio/pageModel";
import { type PreviewControls, boardGroupKeys, fieldSpecs, recordTitle } from "@/lib/studio/packagePreview";
import { READ_ONLY_SENTENCE, type PackageDetail } from "@/lib/studio/realDoor";
import { type ViewDetail, type ViewMapContext, type ViewLens, mapView } from "@/lib/studio/viewBlocks";
import {
  closeRecord, openRecord, openedRecord, parseRecordLink, recordLinkBase, withFullPage, withRecordTab,
} from "@/lib/studio/recordPageModel";
import { usePageState } from "@/lib/studio/usePageState";
import { useCachedResource } from "@/lib/useCachedResource";

export interface AppFrameProps {
  pkg: string;
  detail: PackageDetail;
  door: StudioDataDoor;
  controls: PreviewControls;
  /** Called after the owner deleted the app. */
  onDeleted: () => void;
}

/** A detail view: the sheet of the picked record, or of the first one. */
function DetailCell({ model, door, selected, onPick }: {
  model: ViewDetail; door: StudioDataDoor; selected: string | null; onPick: (id: string) => void;
}) {
  const rows = useCachedResource<Row[]>(sourceKey(door, model.source), () => door.read(model.source));
  const all = rows.data ?? [];
  const record = all.find((r) => String(r.id) === selected) ?? all[0];
  return (
    <div className={PANEL}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-foreground">Detail</h2>
        <SelectButton label="Record" value={record ? String(record.id) : ""} widthClass="w-48"
          options={all.map((r) => ({ value: String(r.id), label: recordTitle(model.fields, r) }))} onChange={onPick} />
      </div>
      {record ? <StudioDetail fields={model.fields} record={record} />
        : rows.error && rows.data === undefined
          ? <RecordsStatus loading={false} error={rows.error} onRetry={rows.refresh} />
          : <EmptyState icon="Inbox" message="No record to show yet" />}
      {moreLine(rows.data) ? <p role="status" className="mt-2 text-xs text-muted-foreground">{moreLine(rows.data)}</p> : null}
    </div>
  );
}

/** The list of the bar's view. A filter that leaves no row says "No match", with the way out (AF-4). */
function FilteredList({ source, door, active, onClear, children }: {
  source: { action: string; args?: Record<string, unknown> }; door: StudioDataDoor;
  active: number; onClear: () => void; children: React.ReactNode;
}) {
  // The same key as the block's own read, so this adds no second read.
  const rows = useCachedResource<Row[]>(sourceKey(door, source), () => door.read(source));
  if (!showNoMatch(rows.data, active)) return <>{children}</>;
  return (
    <div className={PANEL}>
      <EmptyState icon="SearchX" message="No match" hint="No record fits the search and the filters."
        action={{ label: "Clear filters", icon: "X", onClick: onClear }} />
    </div>
  );
}

/** The count of a page header (STU-AF7): the records of one object, as the door counts them. Nothing while it loads or fails. */
function HeaderCount({ object, label, door }: { object: string; label: string; door: StudioDataDoor }) {
  const source = useMemo(() => ({ action: `object:${object}.list`, args: { limit: 1 } }), [object]);
  const rows = useCachedResource<Row[]>(sourceKey(door, source), () => door.read(source));
  const total = rowsTotal(rows.data);
  return total === null ? null : <span data-header-count>{`${total} ${label}`}</span>;
}

export default function AppFrame({ pkg, detail, door, controls, onDeleted }: AppFrameProps) {
  const params = useSearchParams();
  const pageParam = params.get("page");
  const search = params.toString();
  const manifest = useMemo(() => ({
    views: detail.views, pages: detail.pages, actions: detail.actions, objects: detail.objects,
  }), [detail.views, detail.pages, detail.actions, detail.objects]);
  // An unknown page id gives the first page, and never an error (`buildPage`).
  const built = useMemo(() => buildPage(manifest, pageParam), [manifest, pageParam]);
  const { state, setTab, setPage, setSelected, update, go } = usePageState(built.page);

  // The list bar (STU-AF4) acts on the first table or board of the page. Its choices reach the door
  // as the `filter` and `sort` of the `list` read, so the rows are the store's and not a client cut.
  const cells = useMemo(() => buildPage(manifest, built.page.id, state.tab).cells, [manifest, built.page.id, state.tab]);
  const target = useMemo(
    () => barTarget(cells, detail.views, (v) => viewObjectName(v, controls.actions)),
    [cells, detail.views, controls.actions]);
  const bar = useMemo(() => {
    const object = target ? detail.objects.find((o) => o.name === target.object) : undefined;
    if (!target || !object) return null;
    const fields = object.fields as unknown as BarField[];
    const lanes = boardGroupKeys(fieldSpecs(object), computedKeys(object.rules))
      .map((k) => ({ key: k, label: fields.find((f) => f.key === k)?.label || k }));
    const layouts = layoutsFor(lanes.map((l) => l.key));
    const own = target.view.block === "board" ? "board" : "table";
    const layout = state.layout && layouts.includes(state.layout) ? state.layout : layouts.includes(own) ? own : "table";
    const value = barOf(state);
    const lens: ViewLens = {
      view: target.view.id, layout, group: state.group,
      args: toListArgs(fields, value),
    };
    return { fields, lanes, layouts, layout, value, lens };
  }, [target, detail.objects, state]);
  // The record page (STU-AF5). A row or a card links to its record only when the app declares the get.
  const getters = useMemo(() => new Set(detail.objects
    .filter((o) => controls.actions.some((a) => a.uses === `object:${o.name}.get`)).map((o) => o.name)),
  [detail.objects, controls.actions]);
  const recordBase = useCallback((object: string) => (getters.has(object) ? recordLinkBase(search, object) : null), [getters, search]);
  const ctx: ViewMapContext = useMemo(
    () => ({ actions: controls.actions, objects: detail.objects, owner: controls.owner, lens: bar?.lens ?? null, recordBase }),
    [controls.actions, controls.owner, detail.objects, bar?.lens, recordBase]);

  const opened = openedRecord(state);
  const recordObject = opened ? detail.objects.find((o) => o.name === opened.object) : undefined;
  const [titled, setTitled] = useState<{ id: string; text: string } | null>(null);
  const onTitle = useCallback((text: string) => setTitled((t) => (t && t.id === opened?.id && t.text === text ? t : { id: opened?.id ?? "", text })), [opened?.id]);
  // A link of a row or a card opens its record in the panel at once, with no page load.
  const onLinkClick = (e: React.MouseEvent) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target instanceof Element ? e.target.closest("a[href]") : null;
    const link = parseRecordLink(a?.getAttribute("href"));
    if (!link) return;
    e.preventDefault();
    go((s) => openRecord(s, link.object, link.id));
  };

  const renderView = (id: string) => {
    const view = detail.views.find((v) => v.id === id);
    const model = view ? mapView(view, ctx) : null;
    if (!model) return null;
    switch (model.kind) {
      case "node": {
        const node = <GenerativeUINode spec={model.node} />;
        const src = model.node.props.source as { action: string; args?: Record<string, unknown> } | undefined;
        if (!bar || id !== bar.lens.view || !src) return node;
        return (
          <FilteredList source={src} door={door} active={activeCount(bar.fields, bar.value)}
            onClear={() => update(clearedBar)}>{node}</FilteredList>
        );
      }
      case "problem":
        return <p role="status" className={`${PANEL} text-sm text-muted-foreground`}>{model.reason}</p>;
      case "form":
        return (
          <div className={PANEL}>
            <StudioForm fields={model.fields} door={door} action={model.action} title={model.title}
              submitLabel="Add record" savedMessage="Record added" />
          </div>
        );
      case "detail":
        return <DetailCell model={model} door={door} selected={state.selected} onPick={setSelected} />;
    }
  };

  // "New <Object>" (STU-AF6): one button for each object that the page lists and the app can create.
  const adds = useMemo(() => newButtons(
    cells.flatMap((c) => {
      const v = detail.views.find((x) => x.id === c.view);
      return v && (v.block === "table" || v.block === "board") ? [viewObjectName(v, controls.actions)] : [];
    }), detail.objects, controls.actions, controls.owner),
  [cells, detail.views, detail.objects, controls.actions, controls.owner]);
  const pagePicks = built.pages.map((p) => ({ value: p.id, label: p.title || p.id }));
  const toolbar = (
    <>
      <PackageLifetimeBar pkg={pkg} />
      {adds.map((b) => {
        const o = detail.objects.find((x) => x.name === b.object);
        return o ? <StudioNewButton key={b.object} button={b} object={o} door={door} /> : null;
      })}
      {pagePicks.length > 1 ? (
        <SelectButton label="Page" value={built.page.id} widthClass="w-48" options={pagePicks} onChange={setPage} />
      ) : null}
      {controls.owner ? (
        <>
          <Button variant="secondary" size="sm" icon="Code" href={buildHref(pkg)}>Open in Studio IDE</Button>
          <DeletePackageButton id={pkg} onDeleted={onDeleted} />
        </>
      ) : <span role="status" className="text-xs text-muted-foreground">{READ_ONLY_SENTENCE}</span>}
    </>
  );

  return (
    <StudioDataContext.Provider value={door}>
      <div className="h-full" onClickCapture={onLinkClick}>
      <StudioPage title={detail.name.trim() || "App"} back={{ href: "/studio", label: "Back to Studio" }}
        manifest={manifest} pageId={built.page.id} state={state} onTab={setTab} foldScope={pkg}
        renderCount={(c) => (
          <HeaderCount object={c.object} door={door}
            label={c.label || detail.objects.find((o) => o.name === c.object)?.label || c.object} />
        )}
        onCloseSide={() => setSelected(null)} renderView={renderView} toolbar={toolbar}
        record={opened && recordObject ? {
          title: titled && titled.id === opened.id ? titled.text : recordObject.label || recordObject.name,
          full: opened.full,
          onClose: () => go(closeRecord),
          onFull: (full) => go((s) => withFullPage(s, full)),
          backLabel: built.page.title || "the page",
          body: (
            <StudioRecord object={recordObject} objects={detail.objects} recordId={opened.id} door={door}
              actions={controls.actions} owner={controls.owner} tab={opened.tab}
              onTab={(t) => go((s) => withRecordTab(s, t))} recordBase={recordBase} onTitle={onTitle}
              onDeleted={() => go(closeRecord)} />
          ),
        } : null}
        filterBar={bar ? (
          <StudioFilterBar fields={bar.fields} chips={chipsOf(bar.fields)} searchKey={searchKeyOf(bar.fields)}
            bar={bar.value} layouts={bar.layouts} layout={bar.layout} lanes={bar.lanes}
            group={state.group ?? null} active={activeCount(bar.fields, bar.value)}
            onQ={(q) => update((s) => withQ(s, q))}
            onFilter={(k, v) => update((s) => withFilter(s, k, v))}
            onSort={(v) => update((s) => withSort(s, v))}
            onLayout={(l) => update((s) => withLayout(s, l))}
            onGroup={(g) => update((s) => withGroup(s, g))}
            onClear={() => update(clearedBar)} />
        ) : undefined} />
      </div>
    </StudioDataContext.Provider>
  );
}
