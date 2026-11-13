"use client";

/**
 * The app frame of `/studio/package-preview` (STU-AF1, `ai_studio.md` §5.5), behind
 * `NEXT_PUBLIC_STUDIO_APP_FRAME`. It draws the pages of the manifest through `StudioPage`, on
 * live data through the door of the page. The page id lives in the URL (`?page=<id>`).
 *
 * The frame composes parts that exist: `StudioPage`, the mapper `lib/studio/viewBlocks.ts`,
 * `GenerativeUINode`, `StudioForm` and `StudioDetail`. It adds no rail, no tab bar and no dialog.
 * The rail is AF-2. A viewer (J2a) reads only: the door holds the reads, and the form view says so.
 * This is the one file of the route that holds the page model. `page.tsx` imports none.
 */
import { useMemo } from "react";
import { useSearchParams } from "next/navigation";

import GenerativeUINode from "@/components/GenerativeUINode";
import { EmptyState } from "@/components/EmptyState";
import DeletePackageButton from "@/components/studio/DeletePackageButton";
import PackageLifetimeBar from "@/components/studio/PackageLifetimeBar";
import RecordsStatus from "@/components/studio/RecordsStatus";
import StudioDetail from "@/components/studio/StudioDetail";
import StudioFilterBar from "@/components/studio/StudioFilterBar";
import StudioForm from "@/components/studio/StudioForm";
import StudioPage, { PANEL } from "@/components/studio/StudioPage";
import Button from "@/components/ui/Button";
import SelectButton from "@/components/ui/SelectButton";
import { StudioDataContext } from "@/lib/studio/dataContext";
import { type Row, type StudioDataDoor, moreLine, sourceKey } from "@/lib/studio/dataDoor";
import { buildHref } from "@/lib/studio/packageDraft";
import {
  activeCount, barOf, barTarget, chipsOf, clearedBar, layoutsFor, searchKeyOf, toListArgs, withFilter,
  showNoMatch, withGroup, withLayout, withQ, withSort, type BarField,
} from "@/lib/studio/filterBarModel";
import { computedKeys } from "@/lib/studio/inlineEdit";
import { buildPage, viewObjectName } from "@/lib/studio/pageModel";
import { type PreviewControls, boardGroupKeys, fieldSpecs, recordTitle } from "@/lib/studio/packagePreview";
import { READ_ONLY_SENTENCE, type PackageDetail } from "@/lib/studio/realDoor";
import { type ViewDetail, type ViewMapContext, type ViewLens, mapView } from "@/lib/studio/viewBlocks";
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

export default function AppFrame({ pkg, detail, door, controls, onDeleted }: AppFrameProps) {
  const pageParam = useSearchParams().get("page");
  const manifest = useMemo(() => ({
    views: detail.views, pages: detail.pages, actions: detail.actions, objects: detail.objects,
  }), [detail.views, detail.pages, detail.actions, detail.objects]);
  // An unknown page id gives the first page, and never an error (`buildPage`).
  const built = useMemo(() => buildPage(manifest, pageParam), [manifest, pageParam]);
  const { state, setTab, setPage, setSelected, update } = usePageState(built.page);

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
  const ctx: ViewMapContext = useMemo(
    () => ({ actions: controls.actions, objects: detail.objects, owner: controls.owner, lens: bar?.lens ?? null }),
    [controls.actions, controls.owner, detail.objects, bar?.lens]);

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

  const pagePicks = built.pages.map((p) => ({ value: p.id, label: p.title || p.id }));
  const toolbar = (
    <>
      <PackageLifetimeBar pkg={pkg} />
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
      <StudioPage title={detail.name.trim() || "App"} back={{ href: "/studio", label: "Back to Studio" }}
        manifest={manifest} pageId={built.page.id} state={state} onTab={setTab}
        onCloseSide={() => setSelected(null)} renderView={renderView} toolbar={toolbar}
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
    </StudioDataContext.Provider>
  );
}
